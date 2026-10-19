import json, threading, time, urllib.request
from webui import db, jobs
from webui.tests.helpers import DataTestCase, serve, http, login


def read_sse(url, cookie, last_id=None):
    h = {'Cookie': cookie}
    if last_id is not None:
        h['Last-Event-ID'] = str(last_id)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
        return r.read().decode()


class JobsApiTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.base = serve(self)
        self.c = login(self.base)

    def test_finished_job_and_resume(self):
        jobs.register('build', lambda job, log: (log.line('one'), log.line('two')))
        jid = jobs.run_inline('build', {'item_id': 1}, 'manual')['id']
        text = read_sse(f'{self.base}/api/jobs/{jid}/log', self.c)
        self.assertRegex(text, r'id: \d+\ndata: \d\d:\d\d:\d\d one\ndata: \d\d:\d\d:\d\d two\n\n')
        self.assertTrue(text.endswith('event: end\ndata: success\n\n'))
        first_line = len((db.DATA / 'logs' / f'{jid}.log').read_text().splitlines()[0]) + 1
        text = read_sse(f'{self.base}/api/jobs/{jid}/log', self.c, last_id=first_line)
        self.assertNotIn(' one', text)
        self.assertIn(' two', text)

    def test_running_job_streams(self):
        go = threading.Event()
        def h(job, log):
            log.line('start')
            go.wait(5)
            log.line('later')
        jobs.register('build', h)
        jobs.start()
        jid = jobs.enqueue('build', {'item_id': 1}, 'manual')
        threading.Timer(1.0, go.set).start()
        text = read_sse(f'{self.base}/api/jobs/{jid}/log', self.c)
        self.assertIn(' start', text)
        self.assertIn(' later', text)
        self.assertTrue(text.endswith('event: end\ndata: success\n\n'))

    def test_list_filters_and_detail(self):
        jobs.register('build', lambda job, log: None, detail=lambda i: {'d': i})
        jobs.register('ota', lambda job, log: None)
        a = jobs.run_inline('build', {'item_id': 1}, 'manual')['id']
        b = jobs.run_inline('build', {'item_id': 2}, 'manual')['id']
        c = jobs.run_inline('ota', {'device_id': 9}, 'manual')['id']
        get = lambda q: [j['id'] for j in json.loads(http('GET', f'{self.base}/api/jobs?{q}', cookie=self.c)[2])['jobs']]
        self.assertEqual(get('kind=build'), [b, a])
        self.assertEqual(get('item_id=1'), [a])
        self.assertEqual(get('device_id=9'), [c])
        self.assertEqual(get(f'before_id={b}'), [a])
        self.assertEqual(get('limit=1'), [c])
        self.assertEqual(get('limit=-1'), [c])
        self.assertEqual(get('status=failed'), [])
        rows = json.loads(http('GET', f'{self.base}/api/jobs?kind=build', cookie=self.c)[2])['jobs']
        self.assertEqual(rows[0]['detail'], {'d': b})
        self.assertEqual(http('GET', f'{self.base}/api/jobs?item_id=x', cookie=self.c)[0], 400)

    def test_cancel_errors(self):
        jobs.register('build', lambda job, log: None)
        jid = jobs.run_inline('build', {'item_id': 1}, 'manual')['id']
        self.assertEqual(http('POST', f'{self.base}/api/jobs/{jid}/cancel', {}, cookie=self.c)[0], 409)
        self.assertEqual(http('POST', f'{self.base}/api/jobs/999/cancel', {}, cookie=self.c)[0], 404)
        self.assertEqual(http('GET', f'{self.base}/api/jobs/999', cookie=self.c)[0], 404)
