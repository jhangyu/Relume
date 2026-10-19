import json

from . import db


def get(key, default=None):
    r = db.conn().execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(r['value']) if r else default


def put(key, value):
    with db.tx() as c:
        c.execute('INSERT INTO settings(key, value) VALUES (?, ?) '
                  'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))


ROUTES = []
