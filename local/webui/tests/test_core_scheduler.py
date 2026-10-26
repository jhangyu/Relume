import contextlib, datetime as dt, io, json, os, unittest
from unittest import mock
from zoneinfo import ZoneInfo
from webui import db, jobs, scheduler
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
    def update_row(self):
        return db.conn().execute("SELECT * FROM schedules WHERE kind='update'").fetchone()

    def jobs(self):
        return [tuple(r) for r in db.conn().execute('SELECT kind, trigger, schedule_id, target, options FROM jobs')]

    def test_ensure_update_creates_missing_row(self):
        db.conn().execute("DELETE FROM schedules")
        scheduler.ensure_update()
        scheduler.ensure_update()
        rows = db.conn().execute("SELECT name, target, spec, options, enabled, next_run_at FROM schedules").fetchall()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r['name'], json.loads(r['target']), json.loads(r['spec']), json.loads(r['options']),
                          r['enabled']), ('Update', {}, {'every': 'day', 'at': '09:00'}, {'notify': True}, 1))
        self.assertIsNotNone(r['next_run_at'])

    def test_update_schedule_fires_one_all_run(self):
        scheduler.ensure_update()
        row = self.update_row()
        db.conn().execute("UPDATE schedules SET options='{\"notify\":false}' WHERE id=?", (row['id'],))
        scheduler.fire(self.update_row(), NOW)
        self.assertEqual(self.jobs(), [('upstream', 'schedule', row['id'], '{"all":true}', '{"notify":false}')])

    def test_update_target_and_options(self):
        from webui.server import HttpError
        for target, options in (({'all': True}, {}), ({}, {'build': True}), ({}, {'notify': 1})):
            with self.assertRaises(HttpError, msg=(target, options)):
                scheduler._validate({'name': 'x', 'kind': 'update', 'target': target, 'spec': SPEC, 'options': options})

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
        scheduler.ensure_update()
        sid = self.update_row()['id']
        db.conn().execute("UPDATE schedules SET spec=?, next_run_at='2026-10-10T04:00:00Z'", (jobs.canon(SPEC),))
        scheduler.tick(NOW)
        scheduler.tick(NOW)
        self.assertEqual(db.conn().execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
        row = db.conn().execute('SELECT last_run_at, next_run_at FROM schedules WHERE id=?', (sid,)).fetchone()
        self.assertEqual(tuple(row), ('2026-10-19T12:00:00Z', '2026-10-20T04:00:00Z'))


class RoutesTest(DataTestCase):
    def setUp(self):
        super().setUp()
        db.conn().execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (1,'cli','a/b','stable')")
        self.base = serve(self)
        self.c = login(self.base)

    def test_crud_preview_run(self):
        body = {'name': 'Nightly', 'kind': 'ota', 'target': {'all': True}, 'spec': {'every': 'day', 'at': '04:00'},
                'options': {'retries': 1}}
        st, _, b = http('POST', self.base + '/api/schedules', body, cookie=self.c)
        self.assertEqual(st, 201)
        sid = json.loads(b)['id']
        st, _, b = http('POST', self.base + '/api/schedules/preview', {'spec': {'every': 'hours', 'hours': 3}}, cookie=self.c)
        self.assertEqual(len(json.loads(b)['runs']), 5)
        with contextlib.redirect_stdout(io.StringIO()):   # no devices: "target expands to nothing"
            st, _, b = http('POST', self.base + f'/api/schedules/{sid}/run', {}, cookie=self.c)
        self.assertEqual(json.loads(b)['job_ids'], [])
        s = json.loads(http('GET', self.base + '/api/schedules', cookie=self.c)[2])['schedules'][0]
        self.assertEqual(len(s['next_runs']), 5)
        self.assertEqual(s['last_counts'], {})
        self.assertEqual(http('PUT', self.base + f'/api/schedules/{sid}', {'enabled': False}, cookie=self.c)[0], 200)
        s = json.loads(http('GET', self.base + '/api/schedules', cookie=self.c)[2])['schedules'][0]
        self.assertEqual((s['enabled'], s['next_runs'], s['next_run_at']), (False, [], None))
        self.assertEqual(http('PUT', self.base + f'/api/schedules/{sid}', {'kind': 'update'}, cookie=self.c)[0], 400)
        st, _, b = http('PUT', self.base + f'/api/schedules/{sid}', {'options': {'notify': False}}, cookie=self.c)
        self.assertEqual((st, json.loads(b)['options']), (200, {'notify': False}))
        self.assertEqual(http('DELETE', self.base + f'/api/schedules/{sid}', {}, cookie=self.c)[0], 200)
        self.assertEqual(http('DELETE', self.base + f'/api/schedules/{sid}', {}, cookie=self.c)[0], 404)

    def test_update_schedule_cannot_be_created_or_deleted(self):
        scheduler.ensure_update()
        sid = db.conn().execute("SELECT id FROM schedules WHERE kind='update'").fetchone()[0]
        body = {'name': 'Mine', 'kind': 'update', 'target': {}, 'spec': {'every': 'day', 'at': '04:00'}, 'options': {}}
        self.assertEqual(http('POST', self.base + '/api/schedules', body, cookie=self.c)[0], 400)
        for method, path in (('DELETE', ''), ('PUT', ''), ('POST', '/run')):
            self.assertEqual(http(method, self.base + f'/api/schedules/{sid}{path}', {}, cookie=self.c)[0], 409, method)
        self.assertEqual(json.loads(http('GET', self.base + '/api/schedules', cookie=self.c)[2])['schedules'], [])
        self.assertEqual(db.conn().execute("SELECT count(*) FROM schedules WHERE kind='update'").fetchone()[0], 1)
