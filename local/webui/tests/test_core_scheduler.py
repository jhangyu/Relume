import contextlib, datetime as dt, io, json, os, unittest
from unittest import mock
from zoneinfo import ZoneInfo
from webui import build, db, jobs, scheduler
from webui.tests.catalog_rows import add_basic
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


SPEC = {'every': 'day', 'at': '04:00'}
NOW = T('2026-10-19T12:00:00Z')


class FireTest(DataTestCase):
    def setUp(self):
        super().setUp()
        c = db.conn()
        add_basic(c)  # items 1,2 source morphe (upstreams 2,1), item 3 source morphe-beta (3,1)
        c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (4,'project','a/dep','stable')")
        c.execute('INSERT INTO project(id, upstream_id) VALUES (1,4)')
        c.execute('INSERT INTO item_dependency(item_id, project_id) VALUES (1,1)')
        c.execute("UPDATE item SET version_mode='latest' WHERE id=1")
        c.execute('UPDATE item SET auto_update=1 WHERE id IN (1,2)')  # item 3: Update off

    def fire(self, target, **options):
        sid = scheduler.create('n', 'app', target, SPEC, options)
        scheduler.fire(db.conn().execute('SELECT * FROM schedules WHERE id=?', (sid,)).fetchone(), NOW)
        return sorted((r['kind'], r['target'], r['options']) for r in db.conn().execute('SELECT * FROM jobs'))

    def test_update_only(self):
        got = self.fire({'all': True}, update=True)
        opts = '{"notify":true,"rebuild":false}'
        self.assertEqual(got, [('appcheck', '{"package":"com.google.android.youtube"}', opts)] +
                         [('upstream', f'{{"upstream_id":{u}}}', opts) for u in (1, 2, 4)])

    def test_update_and_build_rebuilds_through_jobs(self):
        got = self.fire({'all': True}, update=True, build=True)
        self.assertEqual({k for k, _, _ in got}, {'upstream', 'appcheck'})
        self.assertEqual({o for _, _, o in got}, {'{"notify":true,"rebuild":true}'})
        self.assertEqual(len(got), 4)

    def test_build_only_needs_build_and_force(self):
        db.conn().execute('UPDATE item SET enabled=0 WHERE id=3')
        with mock.patch.object(build, 'needs_build', lambda i: i == 2):
            got = self.fire({'all': True}, build=True)
        self.assertEqual(got, [('build', '{"item_id":2}', '{"notify":true}')])
        db.conn().execute('DELETE FROM jobs')
        got = self.fire({'all': True}, build=True, force=True)
        self.assertEqual([t for _, t, _ in got], ['{"item_id":1}', '{"item_id":2}'])

    def test_item_switches_filter_work(self):
        got = self.fire({'item_ids': [2, 3]}, update=True)  # item 3 has Update off
        self.assertEqual(got, [('upstream', f'{{"upstream_id":{u}}}', '{"notify":true,"rebuild":false}') for u in (1, 2)])

    def test_project_ids_pull_project_upstream(self):
        got = self.fire({'project_ids': [1]}, update=True)
        self.assertEqual([t for _, t, _ in got], ['{"upstream_id":4}'])

    def test_empty_target_warns(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.fire({'item_ids': [3]}, update=True), [])
        self.assertIn('target expands to nothing', out.getvalue())

    def test_option_and_target_validation(self):
        from webui.server import HttpError
        for target, options in (({'all': True}, {}), ({'all': True}, {'notify': True}),
                                ({'all': True}, {'update': True, 'build': True, 'force': True}),
                                ({'all': True}, {'update': True, 'force': True}),
                                ({'all': True}, {'build': 1}), ({'all': True}, {'build': True, 'pull': True}),
                                ({'item_ids': []}, {'update': True}), ({'source_ids': ['x']}, {'update': True}),
                                ({'item_ids': [0]}, {'update': True}), ({}, {'update': True})):
            with self.assertRaises(HttpError, msg=(target, options)):
                scheduler.create('x', 'app', target, SPEC, options)
        scheduler.create('x', 'app', {'item_ids': [1], 'project_ids': [1]}, SPEC, {'build': True, 'force': True})

    def test_ota_all_devices_target(self):
        self.assertTrue(scheduler.create('x', 'ota', {'all': True}, SPEC, {}))

    def test_ota_validation(self):
        from webui.server import HttpError
        for options in ({'mode': 'force_reinstall'}, {'retries': 6}):
            with self.assertRaises(HttpError):
                scheduler.create('x', 'ota', {'device_ids': [1]}, SPEC, options)
        for spec in ({'every': 'day', 'at': '24:00'}, {'every': 'hours', 'hours': 0},
                     {'every': 'week', 'days': [], 'at': '04:00'}, {'every': 'cron', 'expr': '* * * * *'}):
            with self.assertRaises(HttpError):
                scheduler.create('x', 'ota', {'device_ids': [1]}, spec, {})

    def test_missed_run_fires_once(self):
        sid = scheduler.create('n', 'app', {'all': True}, SPEC, {'update': True})
        db.conn().execute("UPDATE schedules SET next_run_at='2026-10-10T04:00:00Z'")
        scheduler.tick(NOW)
        scheduler.tick(NOW)
        self.assertEqual(db.conn().execute('SELECT count(*) FROM jobs').fetchone()[0], 4)
        row = db.conn().execute('SELECT last_run_at, next_run_at FROM schedules WHERE id=?', (sid,)).fetchone()
        self.assertEqual(tuple(row), ('2026-10-19T12:00:00Z', '2026-10-20T04:00:00Z'))


class RoutesTest(DataTestCase):
    def setUp(self):
        super().setUp()
        db.conn().execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (1,'cli','a/b','stable')")
        self.base = serve(self)
        self.c = login(self.base)

    def test_crud_preview_run(self):
        body = {'name': 'Nightly', 'kind': 'app', 'target': {'all': True}, 'spec': {'every': 'day', 'at': '04:00'},
                'options': {'build': True, 'force': True}}
        st, _, b = http('POST', self.base + '/api/schedules', body, cookie=self.c)
        self.assertEqual(st, 201)
        sid = json.loads(b)['id']
        st, _, b = http('POST', self.base + '/api/schedules/preview', {'spec': {'every': 'hours', 'hours': 3}}, cookie=self.c)
        self.assertEqual(len(json.loads(b)['runs']), 5)
        st, _, b = http('POST', self.base + f'/api/schedules/{sid}/run', {}, cookie=self.c)
        self.assertEqual(json.loads(b)['job_ids'], [])
        s = json.loads(http('GET', self.base + '/api/schedules', cookie=self.c)[2])['schedules'][0]
        self.assertEqual(len(s['next_runs']), 5)
        self.assertEqual(s['last_counts'], {})
        self.assertEqual(http('PUT', self.base + f'/api/schedules/{sid}', {'enabled': False}, cookie=self.c)[0], 200)
        s = json.loads(http('GET', self.base + '/api/schedules', cookie=self.c)[2])['schedules'][0]
        self.assertEqual((s['enabled'], s['next_runs'], s['next_run_at']), (False, [], None))
        self.assertEqual(http('PUT', self.base + f'/api/schedules/{sid}', {'kind': 'ota'}, cookie=self.c)[0], 400)
        st, _, b = http('PUT', self.base + f'/api/schedules/{sid}', {'kind': 'app', 'options': {'update': True}}, cookie=self.c)
        self.assertEqual((st, json.loads(b)['options']), (200, {'build': False, 'force': False, 'notify': True, 'update': True}))
        self.assertEqual(http('DELETE', self.base + f'/api/schedules/{sid}', {}, cookie=self.c)[0], 200)
        self.assertEqual(http('DELETE', self.base + f'/api/schedules/{sid}', {}, cookie=self.c)[0], 404)
