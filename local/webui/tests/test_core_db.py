import datetime, pathlib, shutil, sqlite3, tempfile, threading, unittest
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


class MarketplaceMigrationTest(unittest.TestCase):
    def test_migration_010_keeps_builtin_items_and_drops_the_rest(self):
        schema = pathlib.Path(db.SCHEMA_DIR)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as data:
            old = pathlib.Path(d)
            for f in schema.glob('00[1-9]_*.sql'):
                shutil.copy(f, old)
            db.init(data)
            db.migrate(old)
            c = db.conn()
            c.executescript("""
                INSERT INTO upstream(id, kind, repo, channel, downloaded_tag) VALUES
                  (11,'cli','MorpheApp/morphe-desktop','stable','c1'),(12,'patches','MorpheApp/morphe-patches','stable','v2'),
                  (13,'patches','o/other','stable','w1'),(14,'patches','o/orphan','stable',NULL),
                  (15,'project','o/gms','stable','g1');
                INSERT INTO bundle_info(upstream_id, tag, package, patches) VALUES
                  (12,'v1','a.one','[]'),(12,'v2','a.one','[]'),(13,'w1','a.four','[]');
                INSERT INTO project(id, upstream_id, package) VALUES (1, 15, 'o.gms');
                INSERT INTO source(id, name, upstream_id, cli_upstream_id) VALUES ('morphe','Morphe',12,11),('other','Other',13,11);
                INSERT INTO app(package, display_name, apkmirror_list_url) VALUES
                  ('a.one','One','https://www.apkmirror.com/uploads/?appcategory=one'),('a.two',NULL,NULL),
                  ('a.three',NULL,NULL),('a.four',NULL,NULL);
                INSERT INTO grp(id, name, position) VALUES (1,'G',0);
                INSERT INTO item(id, source_id, package, key, group_id, enabled, version_mode, version_pin, variants,
                                 patch_overrides) VALUES
                  (1,'morphe','a.one','one',1,1,'pinned','1.2','["v"]','{"P":{"enabled":false}}'),
                  (2,'morphe','a.two','two',NULL,0,'auto',NULL,'[]','{}'),
                  (3,'morphe','a.three','three',NULL,1,'auto',NULL,'[]','{}'),
                  (4,'other','a.four','four',1,1,'auto',NULL,'[]','{}');
                INSERT INTO item_dependency(item_id, project_id) VALUES (3,1),(4,1);
                INSERT INTO devices(id, name, serial, host, connect_port) VALUES (1,'Pixel','s:5555','s',5555);
                INSERT INTO device_assignments VALUES (1,3),(1,4);
                INSERT INTO schedules(id, name, kind, target, spec, options, enabled, next_run_at) VALUES
                  (1,'ota','ota','{"device_ids":[1]}','{"every":"day","at":"03:00"}','{"retries":1}',1,'2026-10-27T03:00:00Z'),
                  (2,'build only','app','{"all":true}','{"every":"day","at":"05:00"}',
                   '{"build":true,"force":false,"notify":true,"update":false}',1,'2026-10-27T05:00:00Z'),
                  (3,'Daily','app','{"all":true}','{"every":"day","at":"09:00"}',
                   '{"build":true,"force":false,"notify":false,"update":true}',1,'2026-10-27T09:00:00Z'),
                  (4,'later','app','{"all":true}','{"every":"day","at":"10:00"}',
                   '{"build":true,"force":false,"notify":true,"update":true}',1,'2026-10-27T10:00:00Z');
                INSERT INTO jobs(id, kind, status, trigger, schedule_id, target, created_at) VALUES
                  (1,'build','success','manual',NULL,'{"item_id":4}','t'),(2,'ota','success','manual',NULL,'{}','t'),
                  (3,'appcheck','success','schedule',2,'{"package":"a.one"}','t'),
                  (4,'build','success','schedule',2,'{"item_id":1}','t');
                INSERT INTO build_jobs(job_id, item_id) VALUES (1,4),(4,1);
                INSERT INTO artifacts(job_id, item_id, variant, abi, path, created_at) VALUES (1,4,'v','universal','p','t');
                INSERT INTO ota_installs(job_id, device_id, kind, ref_id, package, result) VALUES
                  (2,1,'item',4,'a.four','installed'),(2,1,'item',3,'a.three','installed');""")
            shutil.copy(schema / '010_marketplace.sql', old)
            db.migrate(old)
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 10)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
            builtin = c.execute("SELECT id, upstream_id, name FROM bundle WHERE origin = 'builtin'").fetchall()
            self.assertEqual([tuple(r) for r in builtin], [(builtin[0]['id'], 12, 'Morphe Patches')])
            items = [tuple(r) for r in c.execute(
                'SELECT id, package, bundle_id, enabled, version_mode, version_pin, variants, patch_overrides FROM item ORDER BY id')]
            b = builtin[0]['id']
            self.assertEqual(items, [(1, 'a.one', b, 1, 'pinned', '1.2', '["v"]', '{"P":{"enabled":false}}'),
                                     (2, 'a.two', b, 0, 'auto', None, '[]', '{}'),
                                     (3, 'a.three', b, 1, 'auto', None, '[]', '{}')])
            def rows(sql):
                return [tuple(r) for r in c.execute(sql)]
            self.assertEqual(rows('SELECT item_id, project_id FROM item_dependency'), [(3, 1)])
            self.assertEqual(rows('SELECT device_id, item_id FROM device_assignments'), [(1, 3)])
            self.assertEqual(rows("SELECT ref_id FROM ota_installs WHERE kind = 'item'"), [(3,)])
            self.assertEqual(rows('SELECT item_id FROM artifacts'), [])
            self.assertEqual(rows('SELECT job_id, item_id, inputs_sha FROM build_jobs'), [(4, 1, None)])
            self.assertEqual(rows('SELECT id, kind, schedule_id FROM jobs ORDER BY id'), [(2, 'ota', None), (4, 'build', None)])
            self.assertEqual(rows('SELECT id, name, kind, target, spec, options, enabled, next_run_at FROM schedules ORDER BY id'), [
                (1, 'ota', 'ota', '{"device_ids":[1]}', '{"every":"day","at":"03:00"}', '{"retries":1}', 1, '2026-10-27T03:00:00Z'),
                (3, 'Daily', 'update', '{}', '{"every":"day","at":"09:00"}', '{"notify":false}', 1, '2026-10-27T09:00:00Z')])
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO schedules(name, kind, target, spec) VALUES ('x','update','{}','{}')")
            self.assertEqual(rows('SELECT id FROM upstream ORDER BY id'), [(11,), (12,), (15,)])
            self.assertEqual(rows('SELECT upstream_id, tag FROM bundle_info'), [(12, 'v2')])
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertFalse({'source', 'grp'} & names)
            self.assertEqual(rows("SELECT package, apkm_state FROM app WHERE package LIKE 'a.%' ORDER BY package"),
                             [('a.four', 'pending'), ('a.one', 'manual'), ('a.three', 'pending'), ('a.two', 'pending')])
            self.assertEqual(rows("SELECT apkmirror_list_url, apkm_state FROM app WHERE package = 'com.reddit.frontpage'"),
                             [('https://www.apkmirror.com/uploads/?appcategory=reddit', 'manual')])

    def test_migration_010_fresh_db_has_builtin_bundle(self):
        with tempfile.TemporaryDirectory() as data:
            db.init(data)
            db.migrate()
            c = db.conn()
            got = [tuple(r) for r in c.execute(
                'SELECT b.origin, b.engine, u.kind, u.repo, u.channel, u.tag FROM bundle b JOIN upstream u ON u.id = b.upstream_id')]
            self.assertEqual(got, [('builtin', 'morphe', 'patches', 'MorpheApp/morphe-patches', 'stable', None)])
            self.assertEqual(c.execute('SELECT count(*) FROM app').fetchone()[0], 41)
            self.assertEqual(c.execute('SELECT count(*) FROM schedules').fetchone()[0], 0)


class AndroidMigrationTest(unittest.TestCase):
    def test_migration_008_devices_and_history(self):
        schema = pathlib.Path(db.SCHEMA_DIR)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as data:
            old = pathlib.Path(d)
            for f in schema.glob('00[1-7]_*.sql'):
                shutil.copy(f, old)
            db.init(data)
            db.migrate(old)
            c = db.conn()
            c.executescript("""
                INSERT INTO upstream(id, kind, repo, channel, downloaded_tag) VALUES
                  (1,'project','a/p','stable','v1'),(2,'cli','a/c','stable','v2');
                INSERT INTO project(id, upstream_id, package) VALUES (1, 1, 'com.p');
                INSERT INTO source(id, name, upstream_id, cli_upstream_id) VALUES ('s1','S1',2,2);
                INSERT INTO app(package) VALUES ('com.a');
                INSERT INTO item(id, source_id, package, key, variants) VALUES (1,'s1','com.a','a','[]');
                INSERT INTO devices(id, name, serial, host, connect_port, ota_after_build) VALUES
                  (1,'Pixel','192.168.1.5:5555','192.168.1.5',5555,1);
                INSERT INTO device_assignments VALUES (1, 1);
                INSERT INTO device_projects VALUES (1, 1);
                INSERT INTO jobs(id, kind, status, trigger, target, created_at, finished_at) VALUES
                  (1,'ota','success','manual','{}','2026-10-01T00:00:00Z','2026-10-01T00:05:00Z'),
                  (2,'ota','running','manual','{}','2026-10-02T00:00:00Z',NULL);
                INSERT INTO ota_installs(job_id, device_id, kind, ref_id, package, result) VALUES
                  (1,1,'item',1,'com.a','installed'),(2,1,'project',1,'com.p','failed');""")
            shutil.copy(schema / '008_android.sql', old)
            db.migrate(old)
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 8)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
            dev = c.execute('SELECT * FROM devices').fetchone()
            self.assertEqual((dev['kind'], dev['serial'], dev['ota_after_build'], dev['token_hash']),
                             ('adb', '192.168.1.5:5555', 1, None))
            self.assertRegex(dev['created_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
            self.assertEqual(c.execute('SELECT count(*) FROM device_assignments').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT count(*) FROM device_projects').fetchone()[0], 1)
            self.assertEqual([r[0] for r in c.execute('SELECT at FROM ota_installs ORDER BY id')],
                             ['2026-10-01T00:05:00Z', '2026-10-02T00:00:00Z'])
            self.assertEqual(dict(c.execute('SELECT kind, downloaded_tag FROM upstream').fetchall()),
                             {'project': None, 'cli': 'v2'})
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO devices(name, kind, host) VALUES ('phone', 'app', '10.0.0.2')")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO devices(name, kind) VALUES ('bad', 'adb')")
            c.execute("INSERT INTO devices(name, kind, token_hash) VALUES ('phone', 'app', 'h')")
            # devices.pair INSERT shape (no kind, no created_at) and ota._record shape (no at) stay valid
            did = c.execute('INSERT INTO devices(name,serial,mdns_name,host,connect_port,model,abi_list,sdk,'
                            'android_release,ota_after_build,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,0,?)',
                            ('P2', '10.0.0.3:5555', None, '10.0.0.3', 5555, 'm', 'arm64-v8a', 34, '14', db.utcnow())).lastrowid
            self.assertEqual(c.execute('SELECT kind FROM devices WHERE id=?', (did,)).fetchone()[0], 'adb')
            c.execute("INSERT INTO ota_installs(job_id, device_id, kind, ref_id, package, result) VALUES (NULL, ?, 'item', 1, 'com.a', 'installed')", (did,))
            self.assertIsNotNone(c.execute('SELECT at FROM ota_installs WHERE job_id IS NULL').fetchone()[0])


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
