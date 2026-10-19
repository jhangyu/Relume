import pathlib
import tempfile
import unittest

from webui import db, events


class DataTestCase(unittest.TestCase):
    """Fresh RV_DATA dir + all schema files for every test."""
    schema_dir = None

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = pathlib.Path(tmp.name)
        db.init(self.data)
        db.migrate(self.schema_dir)
        events.reset()
