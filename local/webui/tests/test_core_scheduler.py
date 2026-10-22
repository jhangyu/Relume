import contextlib, datetime as dt, io, json, os, unittest
from unittest import mock
from zoneinfo import ZoneInfo
from webui import build, db, jobs, scheduler
from webui.tests.helpers import DataTestCase, serve, http, login

U = dt.timezone.utc
def T(s): return dt.datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=U)
def S(d): return d.strftime('%Y-%m-%dT%H:%M:%SZ')


class NextRunTest(unittest.TestCase):
    def test_day_utc(self):
        z = ZoneInfo('UTC')
        self.assertEqual(S(scheduler.next_run({'every': 'day', 'at': '04:00'}, T('2026-10-19T03:59:00Z'), z=z)), '2026-10-19T04:00:00Z')
        self.assertEqual(S(scheduler.next_run({'every': 'day', 'at': '04:00'}, T('2026-10-19T04:00:00Z'), z=z)), '2026-10-20T04:00:00Z')

    def test_week(self):  # 2026-10-19 is a Monday
        z = ZoneInfo('UTC')
        r = scheduler.next_run({'every': 'week', 'days': [3], 'at': '09:30'}, T('2026-10-19T10:00:00Z'), z=z)
        self.assertEqual(S(r), '2026-10-22T09:30:00Z')

    def test_hours(self):
        spec = {'every': 'hours', 'hours': 6}
        self.assertEqual(S(scheduler.next_run(spec, T('2026-10-19T00:00:00Z'))), '2026-10-19T06:00:00Z')
        self.assertEqual(S(scheduler.next_run(spec, T('2026-10-19T09:00:00Z'), last_run=T('2026-10-19T01:00:00Z'))), '2026-10-19T07:00:00Z')

    def test_dst_berlin(self):
        z = ZoneInfo('Europe/Berlin')
        spec = {'every': 'day', 'at': '02:30'}
        # spring forward 2026-03-29: 02:30 does not exist -> 03:00 CEST = 01:00Z
        self.assertEqual(S(scheduler.next_run(spec, T('2026-03-28T12:00:00Z'), z=z)), '2026-03-29T01:00:00Z')
        self.assertEqual(S(scheduler.next_run(spec, T('2026-03-29T01:00:00Z'), z=z)), '2026-03-30T00:30:00Z')
        # fall back 2026-10-25: 02:30 happens twice -> first (CEST) = 00:30Z
        self.assertEqual(S(scheduler.next_run(spec, T('2026-10-24T12:00:00Z'), z=z)), '2026-10-25T00:30:00Z')

    def test_next_runs_five(self):
        runs = scheduler.next_runs({'every': 'hours', 'hours': 1}, T('2026-10-19T00:00:00Z'))
        self.assertEqual([S(r) for r in runs], [f'2026-10-19T0{h}:00:00Z' for h in range(1, 6)])


class FireTest(DataTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(scheduler.EXPAND, {
            'build': lambda t: [{'item_id': i} for i in (1, 2, 3)],
            'upstream': lambda t: [],
            'ota': lambda t: [{'device_id': 1}]})
        patcher.start(); self.addCleanup(patcher.stop)

    def test_missed_run_fires_once(self):
        sid = scheduler.create('n', 'build', {'all': True}, {'every': 'day', 'at': '04:00'})
        db.conn().execute("UPDATE schedules SET next_run_at='2026-10-10T04:00:00Z'")
        now = T('2026-10-19T12:00:00Z')
        scheduler.tick(now)
        scheduler.tick(now)
        self.assertEqual(db.conn().execute("SELECT count(*) FROM jobs").fetchone()[0], 3)
        row = db.conn().execute('SELECT last_run_at, next_run_at FROM schedules WHERE id=?', (sid,)).fetchone()
        self.assertEqual(tuple(row), ('2026-10-19T12:00:00Z', '2026-10-20T04:00:00Z'))

    def test_only_changed(self):
        sid = scheduler.create('n', 'build', {'all': True}, {'every': 'day', 'at': '04:00'}, {'only_changed': True})
        with mock.patch.object(build, 'needs_build', lambda i: i == 2):
            scheduler.fire(db.conn().execute('SELECT * FROM schedules WHERE id=?', (sid,)).fetchone(), T('2026-10-19T12:00:00Z'))
        rows = [json.loads(r[0]) for r in db.conn().execute('SELECT target FROM jobs')]
        self.assertEqual(rows, [{'item_id': 2}])
        opts = json.loads(db.conn().execute('SELECT options FROM jobs').fetchone()[0])
        self.assertNotIn('only_changed', opts)

    def test_empty_target_warns(self):
        sid = scheduler.create('pull', 'upstream', {'all': True}, {'every': 'day', 'at': '09:00'})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            scheduler.fire(db.conn().execute('SELECT * FROM schedules WHERE id=?', (sid,)).fetchone(), T('2026-10-19T12:00:00Z'))
        self.assertIn('target expands to nothing', out.getvalue())
        self.assertEqual(db.conn().execute('SELECT count(*) FROM jobs').fetchone()[0], 0)

    def test_upstream_defaults_and_rejects(self):
        sid = scheduler.create('pull', 'upstream', {'all': True}, {'every': 'day', 'at': '09:00'})
        opts = json.loads(db.conn().execute('SELECT options FROM schedules WHERE id=?', (sid,)).fetchone()[0])
        self.assertEqual(opts, {'pull': True, 'rebuild': True})
        from webui.server import HttpError
        for kind, target, spec, options in (
                ('ota', {'device_ids': [1]}, {'every': 'day', 'at': '04:00'}, {'mode': 'force_reinstall'}),
                ('ota', {'device_ids': [1]}, {'every': 'day', 'at': '04:00'}, {'retries': 6}),
                ('build', {'item_ids': []}, {'every': 'day', 'at': '04:00'}, {}),
                ('build', {'all': True}, {'every': 'day', 'at': '24:00'}, {}),
                ('build', {'all': True}, {'every': 'hours', 'hours': 0}, {}),
                ('build', {'all': True}, {'every': 'week', 'days': [], 'at': '04:00'}, {}),
                ('build', {'all': True}, {'every': 'cron', 'expr': '* * * * *'}, {})):
            with self.assertRaises(HttpError):
                scheduler.create('x', kind, target, spec, options)


class RoutesTest(DataTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(scheduler.EXPAND, {'build': lambda t: [{'item_id': 7}]})
        patcher.start(); self.addCleanup(patcher.stop)
        self.base = serve(self)
        self.c = login(self.base)

    def test_crud_preview_run(self):
        body = {'name': 'Nightly', 'kind': 'build', 'target': {'all': True}, 'spec': {'every': 'day', 'at': '04:00'}}
        st, _, b = http('POST', self.base + '/api/schedules', body, cookie=self.c)
        self.assertEqual(st, 201)
        sid = json.loads(b)['id']
        st, _, b = http('POST', self.base + '/api/schedules/preview', {'spec': {'every': 'hours', 'hours': 3}}, cookie=self.c)
        self.assertEqual(len(json.loads(b)['runs']), 5)
        st, _, b = http('POST', self.base + f'/api/schedules/{sid}/run', {}, cookie=self.c)
        self.assertEqual(len(json.loads(b)['job_ids']), 1)
        s = json.loads(http('GET', self.base + '/api/schedules', cookie=self.c)[2])['schedules'][0]
        self.assertEqual(len(s['next_runs']), 5)
        self.assertEqual(s['last_counts'], {'queued': 1})
        self.assertEqual(http('PUT', self.base + f'/api/schedules/{sid}', {'enabled': False}, cookie=self.c)[0], 200)
        s = json.loads(http('GET', self.base + '/api/schedules', cookie=self.c)[2])['schedules'][0]
        self.assertEqual((s['enabled'], s['next_runs'], s['next_run_at']), (False, [], None))
        self.assertEqual(http('PUT', self.base + f'/api/schedules/{sid}', {'kind': 'ota'}, cookie=self.c)[0], 400)
        self.assertEqual(http('DELETE', self.base + f'/api/schedules/{sid}', {}, cookie=self.c)[0], 200)
        self.assertEqual(http('DELETE', self.base + f'/api/schedules/{sid}', {}, cookie=self.c)[0], 404)
