import pathlib
import tempfile
import unittest

from webui import db, events, jobs


class DataTestCase(unittest.TestCase):
    """Fresh RV_DATA dir + all schema files for every test."""
    schema_dir = None

    def setUp(self):
        jobs.stop()                     # no worker from an earlier test may claim this test's jobs
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(jobs.stop)      # runs before tmp.cleanup (LIFO): no worker touches a removed dir
        self.data = pathlib.Path(tmp.name)
        db.init(self.data)
        db.migrate(self.schema_dir)
        events.reset()
        jobs.reset()
