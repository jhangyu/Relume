import datetime as dt, json, threading, time, urllib.request
from webui import db, events, jobs, scheduler
from webui.tests.helpers import DataTestCase, serve, http, login, run_inline


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
        jid = run_inline('build', {'item_id': 1}, 'manual')['id']
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
        req = urllib.request.Request(f'{self.base}/api/jobs/{jid}/log', headers={'Cookie': self.c})
        with urllib.request.urlopen(req, timeout=20) as r:
            seen = ''
            while ' start' not in seen:   # 'start' arrives while the job is still blocked on go
                line = r.readline().decode()
                self.assertTrue(line, f"stream ended before ' start' arrived, got {seen!r}")
                seen += line
            go.set()
            text = seen + r.read().decode()
        self.assertIn(' start', text)
        self.assertIn(' later', text)
        self.assertTrue(text.endswith('event: end\ndata: success\n\n'))

    def test_list_filters_and_detail(self):
        jobs.register('build', lambda job, log: None, detail=lambda i: {'d': i})
        jobs.register('ota', lambda job, log: None)
        a = run_inline('build', {'item_id': 1}, 'manual')['id']
        b = run_inline('build', {'item_id': 2}, 'manual')['id']
        c = run_inline('ota', {'device_id': 9}, 'manual')['id']
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
        jid = run_inline('build', {'item_id': 1}, 'manual')['id']
        self.assertEqual(http('POST', f'{self.base}/api/jobs/{jid}/cancel', {}, cookie=self.c)[0], 409)
        self.assertEqual(http('POST', f'{self.base}/api/jobs/999/cancel', {}, cookie=self.c)[0], 404)
        self.assertEqual(http('GET', f'{self.base}/api/jobs/999', cookie=self.c)[0], 404)


class ScheduleIdFilterTest(DataTestCase):
    def test_schedule_id_filter(self):
        base = serve(self)
        cookie = login(base)
        scheduler.ensure_update()
        sid = db.conn().execute("SELECT id FROM schedules WHERE kind='update'").fetchone()[0]
        s1 = jobs.enqueue('upstream', {'all': True}, 'manual', sid)
        jobs.enqueue('upstream', {'upstream_id': 5}, 'manual')   # newer, no schedule: must be filtered out
        get = lambda q: [j['id'] for j in json.loads(http('GET', f'{base}/api/jobs?{q}', cookie=cookie)[2])['jobs']]
        self.assertEqual(get(f'schedule_id={sid}'), [s1])
        self.assertEqual(get(f'kind=upstream&schedule_id={sid}&limit=1'), [s1])
        self.assertEqual(get(f'kind=build&schedule_id={sid}'), [])
        r = http('GET', f'{base}/api/jobs?schedule_id=x', cookie=cookie)
        self.assertEqual((r[0], json.loads(r[2])['error']), (400, "schedule_id expected an integer, got 'x'"))


class EventsTest(DataTestCase):
    def test_change_stream_coalesces_topics(self):
        s = events.stream(lambda: True)
        events.changed('jobs')
        events.changed('apps', 'jobs')
        self.assertEqual(next(s), b'event: change\ndata: ["jobs","apps"]\n\n')
        with self.assertRaises(ValueError):
            events.changed('nope')
        with self.assertRaises(ValueError):
            events.changed()

    def test_stream_requires_auth_and_ends_on_logout(self):
        base = serve(self)
        self.assertEqual(http('GET', base + '/api/events')[0], 401)
        cookie = login(base)
        got, opened = {}, threading.Event()

        def read():
            req = urllib.request.Request(base + '/api/events', headers={'Cookie': cookie})
            with urllib.request.urlopen(req, timeout=10) as r:
                got['status'] = r.status
                opened.set()                    # headers sent: the stream's mark is already taken
                got['body'] = r.read()          # returns when the server closes the stream
        t = threading.Thread(target=read)
        t.start()
        self.assertTrue(opened.wait(10), 'stream did not open')
        # the logout's devices publish wakes the stream, which then sees the dead session and ends
        self.assertEqual(http('POST', base + '/api/auth/logout', {}, cookie=cookie)[0], 200)
        t.join(10)
        self.assertFalse(t.is_alive(), 'stream still open after logout')
        self.assertEqual(got['status'], 200)
        self.assertEqual(http('GET', base + '/api/events', cookie=cookie)[0], 401)

    def test_mutations_and_job_lifecycle_publish(self):
        base = serve(self, ['market'])
        cookie = login(base)
        scheduler.ensure_update()
        before = dict(events._last)
        st, _, body = http('PUT', base + '/api/schedule', {'enabled': False}, cookie=cookie)
        self.assertEqual(st, 200, body)
        moved = {t for t in events.TOPICS if events._last[t] > before[t]}
        self.assertEqual(moved, {'apps', 'schedules'})
        before = dict(events._last)
        self.assertNotEqual(http('POST', base + '/api/auth/login', {'username': 'admin', 'password': 'wrong-pass-1'})[0], 200)
        self.assertEqual(events._last, before)  # PUBLIC routes never publish
        before = dict(events._last)
        jobs.register('build', lambda job, log: None)
        jid = jobs.enqueue('build', {'item_id': 1}, 'manual')
        self.assertGreater(events._last['jobs'], before['jobs'])
        mark = events._last['jobs']
        self.assertEqual(jobs.enqueue('build', {'item_id': 1}, 'manual'), jid)  # dedup: nothing new
        self.assertEqual(events._last['jobs'], mark)
        self.assertEqual(jobs.cancel(jid), 'cancelled')
        self.assertGreater(events._last['jobs'], mark)
        before = dict(events._last)
        scheduler.fire(db.conn().execute("SELECT * FROM schedules WHERE kind='update'").fetchone(),
                       dt.datetime(2026, 10, 19, 12, tzinfo=dt.timezone.utc))  # fixed clock
        self.assertGreater(events._last['schedules'], before['schedules'])
