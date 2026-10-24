import datetime, json, pathlib, shutil, sqlite3, tempfile, threading, unittest
from webui import db, events
from webui.tests.helpers import DataTestCase


class DbTest(DataTestCase):
    def test_iso_roundtrip(self):
        d = datetime.datetime(2026, 10, 23, 1, 2, 3, tzinfo=datetime.timezone.utc)
        self.assertEqual(db.iso(d), '2026-10-23T01:02:03Z')
        self.assertEqual(db.parse_iso(db.iso(d)), d)
        self.assertIsNone(db.iso(None))
        self.assertIsNone(db.parse_iso(''))
        plus8 = datetime.timezone(datetime.timedelta(hours=8))
        self.assertEqual(db.iso(datetime.datetime(2026, 10, 23, 9, 2, 3, tzinfo=plus8)), '2026-10-23T01:02:03Z')

    def test_init_creates_subdirs(self):
        for d in db.SUBDIRS:
            self.assertTrue((self.data / d).is_dir(), d)

    def test_pragmas(self):
        c = db.conn()
        self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(c.execute('PRAGMA busy_timeout').fetchone()[0], 5000)

    def test_core_tables_and_user_version(self):
        names = {r[0] for r in db.conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'settings', 'sessions', 'jobs', 'schedules'} <= names)
        self.assertGreaterEqual(db.conn().execute('PRAGMA user_version').fetchone()[0], 1)

    def test_migrate_order_and_skip(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / '002_b.sql').write_text('CREATE TABLE b (x INTEGER REFERENCES a(x));')
            (d / '001_a.sql').write_text('CREATE TABLE a (x INTEGER PRIMARY KEY);')
            with tempfile.TemporaryDirectory() as data:
                db.init(data)
                db.migrate(d)
                self.assertEqual(db.conn().execute('PRAGMA user_version').fetchone()[0], 2)
                db.migrate(d)  # second run applies nothing, raises nothing
                (d / '003_c.sql').write_text('CREATE TABLE c (y);')
                db.migrate(d)
                self.assertEqual(db.conn().execute('PRAGMA user_version').fetchone()[0], 3)

    def test_tx_rollback(self):
        with self.assertRaises(RuntimeError):
            with db.tx() as c:
                c.execute("INSERT INTO settings VALUES ('k','1')")
                raise RuntimeError('x')
        self.assertIsNone(db.conn().execute("SELECT 1 FROM settings WHERE key='k'").fetchone())

    def test_connection_per_thread(self):
        main = db.conn()
        other = []
        t = threading.Thread(target=lambda: other.append(db.conn()))
        t.start(); t.join()
        self.assertIsNot(main, other[0])

    def test_utcnow_format(self):
        self.assertRegex(db.utcnow(), r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')


class AppScheduleMigrationTest(unittest.TestCase):
    def test_old_schedule_rows_become_app_rows(self):
        schema = pathlib.Path(db.SCHEMA_DIR)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as data:
            old = pathlib.Path(d)
            for f in schema.glob('00[1-6]_*.sql'):
                shutil.copy(f, old)
            db.init(data)
            db.migrate(old)
            c = db.conn()
            c.executescript("""
                INSERT INTO upstream(id, kind, repo, channel) VALUES (1,'cli','a/c','stable'),(2,'patches','a/p','stable');
                INSERT INTO source(id, name, upstream_id, cli_upstream_id) VALUES ('s1','S1',2,1),('s2','S2',2,1);
                INSERT INTO app(package) VALUES ('p1'),('p2'),('p3');
                INSERT INTO grp(id, name, position) VALUES (1,'G',0);
                INSERT INTO item(id, source_id, package, key, group_id, variants) VALUES
                  (1,'s1','p1','a',1,'[]'),(2,'s1','p2','b',NULL,'[]'),(3,'s2','p3','c',1,'[]');""")
            sp = '{"every":"day","at":"04:00"}'
            rows = [('up-all', 'upstream', '{"all":true}', '{"pull":true,"rebuild":false,"notify":false}'),
                    ('up-src', 'upstream', '{"source_ids":["s1"]}', '{"pull":false,"rebuild":true}'),
                    ('up-none', 'upstream', '{"source_ids":["nope"]}', '{}'),
                    ('b-all', 'build', '{"all":true}', '{"only_changed":true}'),
                    ('b-grp', 'build', '{"group_ids":[1]}', '{}'),
                    ('b-ungrouped', 'build', '{"group_ids":[0]}', '{"notify":false}'),
                    ('b-items', 'build', '{"item_ids":[2,3]}', '{"only_changed":false}'),
                    ('b-empty', 'build', '{"group_ids":[9]}', '{}'),
                    ('ota', 'ota', '{"device_ids":[1]}', '{"retries":1}')]
            for i, (n, k, t, o) in enumerate(rows, 1):
                c.execute("INSERT INTO schedules(id,name,kind,target,spec,options,enabled,next_run_at) VALUES (?,?,?,?,?,?,1,'2026-10-20T04:00:00Z')",
                          (i, n, k, t, sp, o))
            shutil.copy(schema / '007_app_schedule.sql', old)
            db.migrate(old)
            got = {r['name']: (r['kind'], r['target'], r['options'], r['enabled'], r['next_run_at'])
                   for r in db.conn().execute('SELECT * FROM schedules')}
        def app(update, build, force, notify=True):
            return json.dumps({'build': build, 'force': force, 'notify': notify, 'update': update}, sort_keys=True, separators=(',', ':'))
        T = '2026-10-20T04:00:00Z'
        self.assertEqual(got, {
            'up-all': ('app', '{"all":true}', app(True, False, False, False), 1, T),
            'up-src': ('app', '{"item_ids":[1,2]}', app(True, True, False), 1, T),
            'up-none': ('app', '{"all":true}', app(True, True, False), 0, None),
            'b-all': ('app', '{"all":true}', app(False, True, False), 1, T),
            'b-grp': ('app', '{"item_ids":[1,3]}', app(False, True, True), 1, T),
            'b-ungrouped': ('app', '{"item_ids":[2]}', app(False, True, True, False), 1, T),
            'b-items': ('app', '{"item_ids":[2,3]}', app(False, True, True), 1, T),
            'b-empty': ('app', '{"all":true}', app(False, True, True), 0, None),
            'ota': ('ota', '{"device_ids":[1]}', '{"retries":1}', 1, T)})


class EventsTest(unittest.TestCase):
    def setUp(self):
        events.reset()

    def test_emit_runs_all_and_swallows(self):
        got = []
        events.subscribe('build.failed', lambda p: 1 / 0)
        events.subscribe('build.failed', got.append)
        events.emit('build.failed', {'item_id': 1})
        self.assertEqual(got, [{'item_id': 1}])

    def test_other_type_not_called(self):
        got = []
        events.subscribe('ota.done', got.append)
        events.emit('ota.failed', {})
        self.assertEqual(got, [])
