import datetime
import os
import pathlib
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA_DIR = pathlib.Path(__file__).with_name('schema')
SUBDIRS = ('home', 'logs', 'work', 'artifacts', 'apk-cache', 'upstream', 'icons')
DATA = pathlib.Path(os.environ.get('RELUME_DATA', '/data'))
FMT = '%Y-%m-%dT%H:%M:%SZ'
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_local = threading.local()
_gen = 0


def iso(d):
    return d.astimezone(datetime.timezone.utc).strftime(FMT) if d else None


def parse_iso(s):
    return datetime.datetime.strptime(s, FMT).replace(tzinfo=datetime.timezone.utc) if s else None


def utcnow():
    return iso(datetime.datetime.now(datetime.timezone.utc))


def init(data_dir=None):
    """Set DATA, create its subdirectories, drop cached connections."""
    global DATA, _gen
    DATA = pathlib.Path(data_dir or os.environ.get('RELUME_DATA', '/data'))
    for d in SUBDIRS:
        (DATA / d).mkdir(parents=True, exist_ok=True)
    _gen += 1


def conn():
    c = getattr(_local, 'c', None)
    if c is None or _local.gen != _gen:
        c = sqlite3.connect(DATA / 'webui.db', timeout=5, isolation_level=None,
                            check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=5000')
        _local.c, _local.gen = c, _gen
    return c


@contextmanager
def tx():
    c = conn()
    c.execute('BEGIN IMMEDIATE')
    try:
        yield c
    except BaseException:
        c.execute('ROLLBACK')
        raise
    c.execute('COMMIT')


def migrate(schema_dir=None):
    d = pathlib.Path(schema_dir or SCHEMA_DIR)
    c = conn()
    have = c.execute('PRAGMA user_version').fetchone()[0]
    for f in sorted(d.glob('[0-9][0-9][0-9]_*.sql')):
        n = int(f.name[:3])
        if n > have:
            c.executescript(f.read_text())
            c.execute(f'PRAGMA user_version={n}')
