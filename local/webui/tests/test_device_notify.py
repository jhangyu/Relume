import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from webui import db, events, notify, settings
from webui.server import HttpError
from webui.tests import helpers
from webui.tests.device_helpers import add_catalog_item, add_job, req



class Stub:
    """Local HTTP endpoint: records requests, answers from `responses` (the last one repeats)."""

    def __init__(self, tc, responses=((200, {}, b'{"ok":true}'),)):
        self.requests, self.responses, self.gate = [], list(responses), None
        self.arrived = threading.Event()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                outer.requests.append((self.path, dict(self.headers), self.rfile.read(n)))
                outer.arrived.set()
                if outer.gate is not None:
                    outer.gate.wait(30)
                st, hd, body = outer.responses.pop(0) if len(outer.responses) > 1 else outer.responses[0]
                self.send_response(st)
                for k, v in hd.items():
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST  # a 302 after POST arrives as GET

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.srv.daemon_threads = True
        helpers.run_server(tc, self.srv)
        tc.addCleanup(lambda: self.gate and self.gate.set())
        self.url = f'http://127.0.0.1:{self.srv.server_address[1]}'


def discord(url, events_=None):
    return {'type': 'discord', 'enabled': True, 'config': {'webhook_url': url + '/api/webhooks/1/x'},
            'events': events_ or list(notify.DEFAULT_EVENTS), 'last_test': None}


class NotifyTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        self.pauses = []
        self.enterContext(mock.patch.object(notify, '_pause', side_effect=self.pauses.append))

    def test_telegram_shape(self):
        stub = Stub(self)
        self.addCleanup(setattr, notify, 'TELEGRAM_API', notify.TELEGRAM_API)
        notify.TELEGRAM_API = stub.url
        cfg = {'type': 'telegram', 'enabled': True, 'config': {'bot_token': '123:ABC', 'chat_id': '42'}}
        self.assertEqual(notify.send(cfg, 'hi')[:2], (True, 200))
        path, headers, body = stub.requests[0]
        self.assertEqual(path, '/bot123:ABC/sendMessage')
        self.assertEqual(json.loads(body), {'chat_id': '42', 'text': 'hi'})
        self.assertEqual(headers['Content-Type'], 'application/json')

    def test_discord_shape(self):
        stub = Stub(self, [(204, {}, b'')])
        self.assertEqual(notify.send(discord(stub.url), 'hi')[:2], (True, 204))
        self.assertEqual((stub.requests[0][0], json.loads(stub.requests[0][2])), ('/api/webhooks/1/x', {'content': 'hi'}))

    def test_ntfy_shape_and_auth(self):
        stub = Stub(self)
        cfg = {'type': 'ntfy', 'enabled': True, 'config': {'server_url': stub.url, 'topic': 'rv', 'token': 'tk_secret1'}}
        self.assertTrue(notify.send(cfg, 'hi')[0])
        path, headers, body = stub.requests[0]
        self.assertEqual((path, body, headers['Title'], headers['Authorization']), ('/rv', b'hi', 'Relume', 'Bearer tk_secret1'))

    def test_ntfy_cross_host_redirect_drops_authorization(self):
        b = Stub(self)
        a = Stub(self, [(302, {'Location': b.url + '/x'}, b'')])
        cfg = {'type': 'ntfy', 'enabled': True, 'config': {'server_url': a.url, 'topic': 'rv', 'token': 'tk_secret1'}}
        self.assertEqual(notify.send(cfg, 'hi')[:2], (True, 200))
        self.assertEqual(b.requests[0][0], '/x')
        self.assertNotIn('Authorization', b.requests[0][1])

    def test_truncate_2000_keeps_link(self):
        add_catalog_item(1, 'com.google.android.youtube')
        settings.put('base_url', 'http://nas:47000/')
        text = notify.format_message('build.failed', {'item_id': 1, 'job_id': 5, 'fail_code': 'patch_failed',
                                                      'fail_detail': 'x' * 5000})
        self.assertEqual(len(text), 2000)
        self.assertTrue(text.endswith('\nhttp://nas:47000/builds.html?job=5'))
        self.assertTrue(text.startswith('Build failed: com.google.android.youtube · src: patch_failed: x'))

    def _project(self, display_name):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (23,'project','MorpheApp/MicroG-RE','stable')")
            c.execute('INSERT INTO project(id, upstream_id, display_name) VALUES (1,23,?)', (display_name,))
        settings.put('base_url', 'http://nas:47000')

    def test_format_signer_changed(self):
        self._project('MicroG RE')
        text = notify.format_message('project.signer_changed', {'project_id': 1, 'upstream_id': 23, 'tag': '7.2.2',
                                                                'old_sha256': 'ab', 'new_sha256': 'cd', 'job_id': 4})
        self.assertEqual(text, 'Signer changed: MicroG RE 7.2.2. Installs are blocked until you trust the new signer.'
                               '\nhttp://nas:47000/projects.html')
        self.assertIn('project.signer_changed', notify.EVENTS)

    def test_format_project_new_version(self):
        self._project(None)
        text = notify.format_message('upstream.new_version', {'upstream_id': 23, 'tag': '7.2.2', 'item_ids': [], 'job_id': None})
        self.assertEqual(text, 'New version: MicroG-RE 7.2.2\nhttp://nas:47000/projects.html')
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (2,'patches','MorpheApp/morphe-patches','stable')")
        text = notify.format_message('upstream.new_version', {'upstream_id': 2, 'tag': 'v1', 'item_ids': [1], 'job_id': None})
        self.assertEqual(text, 'New upstream version: MorpheApp/morphe-patches v1 (1 items)\nhttp://nas:47000/')

    def test_retry_after_honoured(self):
        stub = Stub(self, [(429, {}, b'{"ok":false,"parameters":{"retry_after":3}}'), (200, {}, b'{"ok":true}')])
        self.addCleanup(setattr, notify, 'TELEGRAM_API', notify.TELEGRAM_API)
        notify.TELEGRAM_API = stub.url
        cfg = {'type': 'telegram', 'enabled': True, 'config': {'bot_token': 't', 'chat_id': '1'}}
        self.assertTrue(notify.send(cfg, 'hi')[0])
        self.assertEqual(self.pauses, [3.0])
        self.assertEqual(len(stub.requests), 2)

    def test_emit_not_blocked_by_stalled_provider(self):
        stub = Stub(self)
        stub.gate = threading.Event()
        settings.put('notify', discord(stub.url))
        notify.register()
        threading.Thread(target=notify.worker, daemon=True).start()
        t = time.monotonic()
        events.emit('ota.done', {'job_id': None, 'device_id': 1, 'outcome': 'done'})
        self.assertLess(time.monotonic() - t, 0.1)
        self.assertTrue(stub.arrived.wait(5))   # fail-fast bound, the worker posts at once
        self.assertEqual(len(stub.requests), 1)

    def test_skip_notify_false_job(self):
        stub = Stub(self, [(204, {}, b'')])
        settings.put('notify', discord(stub.url))
        add_catalog_item(1, 'com.google.android.youtube')
        quiet = add_job(kind='build', target={'item_id': 1}, options={'notify': False}, status='failed')
        loud = add_job(kind='build', target={'item_id': 1}, options={'notify': True}, status='failed')
        p = {'item_id': 1, 'fail_code': 'patch_failed', 'fail_detail': 'x'}
        self.assertIsNone(notify.deliver('build.failed', dict(p, job_id=quiet)))
        self.assertEqual(stub.requests, [])
        self.assertTrue(notify.deliver('build.failed', dict(p, job_id=loud)))
        self.assertIsNone(notify.deliver('device.offline', {'job_id': loud, 'device_id': 1}))

    def test_upstream_new_version_follows_job_notify(self):
        stub = Stub(self, [(204, {}, b'')])
        settings.put('notify', discord(stub.url))
        quiet = add_job(kind='upstream', target={'upstream_id': 2}, options={'notify': False}, trigger='schedule', status='success')
        manual = add_job(kind='upstream', target={'upstream_id': 2}, options={'notify': True}, trigger='manual', status='success')
        p = {'upstream_id': 2, 'tag': 'v1.48.0', 'item_ids': [1]}
        self.assertIsNone(notify.deliver('upstream.new_version', dict(p, job_id=quiet)))
        self.assertEqual(stub.requests, [])
        self.assertTrue(notify.deliver('upstream.new_version', dict(p, job_id=manual)))
        self.assertTrue(notify.deliver('upstream.new_version', dict(p, job_id=None)))
        self.assertEqual(len(stub.requests), 2)

    def test_mask_and_keep_secret_on_put(self):
        body = {'type': 'telegram', 'enabled': True, 'config': {'bot_token': '123456:ABCDEFGH1234', 'chat_id': '42'}}
        notify.put_notify(req(body=body))
        st, got = notify.get_notify(req(method='GET'))
        self.assertEqual(got['config']['bot_token'], '****1234')
        notify.put_notify(req(body={'type': 'telegram', 'enabled': True,
                                    'config': {'bot_token': '****1234', 'chat_id': '43'}}))
        self.assertEqual(settings.get('notify')['config'], {'bot_token': '123456:ABCDEFGH1234', 'chat_id': '43'})
        notify.put_notify(req(body={'type': 'telegram', 'enabled': True, 'config': {'chat_id': '44'}}))
        self.assertEqual(settings.get('notify')['config']['bot_token'], '123456:ABCDEFGH1234')

    def test_default_events_include_signer_changed(self):
        st, got = notify.get_notify(req(method='GET'))
        self.assertEqual(got['events'], ['upstream.new_version', 'build.succeeded', 'build.failed', 'ota.done', 'ota.failed',
                                         'project.signer_changed'])
        notify.put_notify(req(body={'type': 'discord', 'enabled': True, 'config': {'webhook_url': 'https://d.example/x'}}))
        self.assertNotIn('device.offline', settings.get('notify')['events'])

    def test_put_validation(self):
        bad = [{'type': 'slack', 'config': {}},
               {'type': 'discord', 'config': {'webhook_url': 'https://d.example/x', 'x': 1}},
               {'type': 'discord', 'config': {}},
               {'type': 'ntfy', 'config': {'server_url': 'ftp://n.example', 'topic': 't'}},
               {'type': 'discord', 'enabled': 'yes', 'config': {'webhook_url': 'https://d.example/x'}},
               {'type': 'discord', 'config': {'webhook_url': 'https://d.example/x'}, 'events': ['nope']}]
        for body in bad:
            with self.assertRaises(HttpError, msg=str(body)) as cm:
                notify.put_notify(req(body=body))
            self.assertEqual(cm.exception.status, 400)

    def test_test_route_stores_last_test(self):
        stub = Stub(self, [(500, {}, b'boom')])
        settings.put('notify', discord(stub.url))
        st, body = notify.test_notify(req())
        self.assertEqual((body['ok'], body['status'], body['detail']), (False, 500, 'boom'))
        self.assertEqual(len(stub.requests), 3)
        last = settings.get('notify')['last_test']
        self.assertEqual((last['ok'], last['detail']), (False, '500: boom'))
        self.assertTrue(last['at'].endswith('Z'))

    def test_register_starts_no_thread(self):
        n = threading.active_count()
        notify.register()
        self.assertEqual(threading.active_count(), n)
