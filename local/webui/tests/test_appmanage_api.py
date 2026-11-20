"""App Manage routes (plan 2026-11-20 §1.3, §1.4): token-scoped authorization, matrix shape, SSE topic."""
import base64
import hashlib
import json
from unittest import mock

from webui import appmanage_play, appmanage_store as store, auth, db, events
from webui.tests import device_helpers as dh
from webui.tests.helpers import DataTestCase, http, login, serve

PNG = b'\x89PNG\r\n\x1a\n' + b'icon'
PNG_SHA = hashlib.sha256(PNG).hexdigest()
WEB_MUTATIONS = (('PUT', '/api/appmanage/primary', {'device_id': None}),
                 ('POST', '/api/appmanage/actions', {'package': 'com.x', 'action': 'remove', 'device_ids': [1]}),
                 ('DELETE', '/api/appmanage/actions/1', {}), ('POST', '/api/appmanage/refresh', {}))


def phone(name, tok, uid: str | None = 'set'):
    """App row with device token tok; uid=None makes it a legacy row (companion up to v1.2.0)."""
    did = dh.add_app_device(name, token_hash=auth.token_hash(tok))
    if uid is None:
        with db.tx() as c:
            c.execute('UPDATE devices SET device_uid=NULL WHERE id=?', (did,))
    return did


class AppManageApiTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(store, 'ICON_DIR', self.data / 'icons' / 'appmanage'))
        self.base = serve(self, ['appmanage'])
        self.cookie = login(self.base)
        self.a, self.b = phone('Alpha', 'tok-a'), phone('Beta', 'tok-b')

    def web(self, method, path, body=None):
        st, _, raw = http(method, self.base + path, body, cookie=self.cookie)
        return st, json.loads(raw)

    def me(self, tok, method, path, body=None):
        st, _, raw = http(method, self.base + path, body, headers={'Authorization': f'Bearer {tok}'})
        return st, json.loads(raw)

    def delivered(self, did, source='play_page'):
        (a,) = store.create_actions('com.x', 'install', [did], source)
        store.pickup(did)
        return a['id']

    def test_device_token_gets_403_on_every_web_route(self):
        self.delivered(self.a)
        for method, path, body in WEB_MUTATIONS + (('GET', '/api/appmanage', None),):
            self.assertEqual(self.me('tok-a', method, path, body), (403, {'error': 'session required'}), path)
        self.assertEqual(store.actions(self.a)[0]['state'], 'delivered')
        self.assertIsNone(store.get_primary())

    def test_other_devices_action_is_404_and_own_works(self):
        aid = self.delivered(self.b)
        want = (404, {'error': f'action {aid} not found for this device'})
        self.assertEqual(self.me('tok-a', 'POST', f'/api/me/actions/{aid}/start', {}), want)
        self.assertEqual(self.me('tok-a', 'POST', f'/api/me/actions/{aid}/result', {'result': 'done'}), want)
        self.assertEqual(self.me('tok-a', 'POST', '/api/me/actions/999/start', {}), (404, {'error': 'action 999 not found for this device'}))
        self.assertEqual(store.actions(self.b)[0]['state'], 'delivered')
        self.assertEqual(self.me('tok-b', 'POST', f'/api/me/actions/{aid}/start', {}), (200, {'id': aid}))
        self.assertEqual(self.me('tok-b', 'POST', f'/api/me/actions/{aid}/result', {'result': 'done'}), (200, {'ok': True}))
        self.assertEqual(store.actions(self.b)[0]['state'], 'done')
        st, body = self.me('tok-b', 'POST', f'/api/me/actions/{aid}/result', {'result': 'done'})
        self.assertEqual(st, 409, body)

    def test_play_direct_start_returns_urls_and_lookup_failure_fails_action(self):
        apks = [{'name': 'base', 'url': 'https://play.googleapis.com/b'}]
        aid = self.delivered(self.a, 'play_direct')
        with mock.patch.object(appmanage_play, 'resolve', return_value={'version_code': 5, 'apks': apks}) as r:
            self.assertEqual(self.me('tok-a', 'POST', f'/api/me/actions/{aid}/start', {}),
                             (200, {'id': aid, 'version_code': 5, 'apks': apks}))
        self.assertEqual((r.call_args.args[0], r.call_args.args[1]['id']), ('com.x', self.a))
        aid = self.delivered(self.b, 'play_direct')
        err = appmanage_play.PlayLookupError('PLAY_LOOKUP_FAILED', 'Play lookup of com.x failed: rate limit')
        with mock.patch.object(appmanage_play, 'resolve', side_effect=err):
            self.assertEqual(self.me('tok-b', 'POST', f'/api/me/actions/{aid}/start', {}),
                             (502, {'error': str(err), 'code': 'PLAY_LOOKUP_FAILED'}))
        row = store.actions(self.b)[0]
        self.assertEqual((row['id'], row['state'], row['error_code']), (aid, 'failed', 'PLAY_LOOKUP_FAILED'))

    def test_icons_legacy_403_upload_and_serve(self):
        phone('Old', 'tok-old', uid=None)
        body = {'sha256': PNG_SHA, 'png_base64': base64.b64encode(PNG).decode()}
        self.assertEqual(self.me('tok-old', 'POST', '/api/me/icons', body)[0], 403)
        self.assertIsNone(store.icon_path(PNG_SHA))
        self.assertEqual(self.me('tok-a', 'POST', '/api/me/icons', dict(body, png_base64='%%'))[0], 400)
        self.assertEqual(self.me('tok-a', 'POST', '/api/me/icons', body), (200, {'stored': True}))
        st, h, raw = http('GET', f'{self.base}/api/appmanage/icons/{PNG_SHA}', cookie=self.cookie)
        self.assertEqual((st, raw, h['Cache-Control']), (200, PNG, 'max-age=86400'))
        self.assertEqual(http('GET', f'{self.base}/api/appmanage/icons/{"0" * 64}', cookie=self.cookie)[0], 404)

    def test_matrix_keys_and_primary_errors(self):
        legacy = phone('Old', 'tok-old', uid=None)
        self.assertEqual(self.web('PUT', '/api/appmanage/primary', {'device_id': 999})[0], 404)
        self.assertEqual(self.web('PUT', '/api/appmanage/primary', {'device_id': legacy})[0], 409)
        self.assertEqual(self.web('PUT', '/api/appmanage/primary', {'device_id': 'x'})[0], 400)
        self.assertEqual(self.web('PUT', '/api/appmanage/primary', {})[0], 400)
        self.assertEqual(self.web('PUT', '/api/appmanage/primary', {'device_id': self.b}), (200, {'primary_id': self.b}))
        store.ingest(self.a, store.parse_inventory({'full': True, 'base_rev': None, 'removed': [], 'apps': [
            {'package': 'com.x', 'label': 'X', 'version_code': 2, 'version_name': '2', 'app_bytes': 1,
             'data_bytes': 1, 'cache_bytes': 1, 'size_reason': None, 'icon_sha256': None}]}))
        st, m = self.web('GET', '/api/appmanage')
        self.assertEqual((st, set(m)), (200, {'primary_id', 'devices', 'apps'}))
        self.assertEqual([d['id'] for d in m['devices']], [self.b, self.a])   # primary first, legacy excluded
        self.assertEqual(set(m['devices'][1]), {'id', 'name', 'kind', 'online', 'reported_at'})
        self.assertIsNotNone(m['devices'][1]['reported_at'])
        (app,) = m['apps']
        self.assertEqual(set(app), {'package', 'label', 'icon', 'group', 'source', 'ref', 'highest', 'cells'})
        self.assertEqual(set(app['cells']), {str(self.a), str(self.b)})

    def test_create_and_cancel_publish_appmanage(self):
        before = dict(events._last)
        st, body = self.web('POST', '/api/appmanage/actions',
                            {'package': 'com.x', 'action': 'install', 'device_ids': [self.a, self.b], 'source': 'play_page'})
        self.assertEqual(st, 200, body)
        self.assertEqual([(a['device_id'], a['state']) for a in body['actions']], [(self.a, 'queued'), (self.b, 'queued')])
        self.assertEqual({t for t in events.TOPICS if events._last[t] > before[t]}, {'appmanage'})
        aid = body['actions'][0]['id']
        self.assertEqual(self.web('DELETE', f'/api/appmanage/actions/{aid}'), (200, {'id': aid, 'state': 'cancelled'}))
        self.assertEqual(self.web('DELETE', f'/api/appmanage/actions/{aid}')[0], 409)
