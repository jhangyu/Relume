import datetime, pathlib, sqlite3, tempfile, threading, unittest
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
