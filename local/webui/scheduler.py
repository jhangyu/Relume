import datetime as dt
import json
import os
import re
import threading
import traceback
from zoneinfo import ZoneInfo

from . import build, catalog, db, devices, jobs
from .server import HttpError, is_int, validate_slug

UTC = dt.timezone.utc
AT = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
OPTION_KEYS = {
    'upstream': {'pull': bool, 'rebuild': bool, 'notify': bool},
    'build': {'only_changed': bool, 'notify': bool},
    'ota': {'retries': int, 'notify': bool},
}
TARGET_KEYS = {'upstream': ('source_ids',), 'build': ('group_ids', 'item_ids'), 'ota': ('device_ids',)}
EXPAND = {
    'upstream': lambda t: [{'upstream_id': i} for i in catalog.resolve_upstreams(t)],
    'build': lambda t: [{'item_id': i} for i in catalog.resolve_items(t)],
    'ota': lambda t: [{'device_id': i} for i in devices.resolve_devices(t)],
}
_wake = threading.Event()


def _tz():
    return ZoneInfo(os.environ.get('TZ') or 'UTC')


def _local_to_utc(naive, z):
    for _ in range(24 * 60):
        u = naive.replace(tzinfo=z).astimezone(UTC)
        if u.astimezone(z).replace(tzinfo=None) == naive:
            return u
        naive += dt.timedelta(minutes=1)
    raise ValueError('no valid local time')


def next_run(spec, after, last_run=None, z=None):
    if spec['every'] == 'hours':
        return (last_run or after) + dt.timedelta(hours=spec['hours'])
    z = z or _tz()
    hh, mm = map(int, spec['at'].split(':'))
    day0 = after.astimezone(z).date()
    for i in range(9):
        day = day0 + dt.timedelta(days=i)
        if spec['every'] == 'week' and day.weekday() not in spec['days']:
            continue
        u = _local_to_utc(dt.datetime(day.year, day.month, day.day, hh, mm), z)
        if u > after:
            return u
    raise ValueError('no next run')


def next_runs(spec, after, n=5, last_run=None):
    out, t, lr = [], after, last_run
    for _ in range(n):
        t = next_run(spec, t, lr)
        out.append(t)
        lr = t
    return out


def _check_spec(spec):
    ok = isinstance(spec, dict) and (
        (spec.get('every') == 'day' and set(spec) == {'every', 'at'} and isinstance(spec['at'], str) and AT.match(spec['at']))
        or (spec.get('every') == 'week' and set(spec) == {'every', 'days', 'at'} and isinstance(spec['at'], str)
            and AT.match(spec['at']) and isinstance(spec['days'], list) and spec['days']
            and len(set(spec['days'])) == len(spec['days'])
            and all(is_int(d) and 0 <= d <= 6 for d in spec['days']))
        or (spec.get('every') == 'hours' and set(spec) == {'every', 'hours'} and is_int(spec['hours'])
            and 1 <= spec['hours'] <= 168))
    if not ok:
        raise HttpError(400, 'invalid spec')
    return spec


def _check_target(kind, t):
    if t == {'all': True}:
        return t
    if not isinstance(t, dict) or len(t) != 1 or next(iter(t)) not in TARGET_KEYS[kind]:
        raise HttpError(400, 'invalid target')
    key, ids = next(iter(t.items()))
    if not isinstance(ids, list) or not ids:
        raise HttpError(400, 'invalid target')
    for v in ids:
        if key == 'source_ids':
            validate_slug(v, 'source id')
        elif not is_int(v) or v < (0 if key == 'group_ids' else 1):
            raise HttpError(400, 'invalid target')
    return t


def _check_options(kind, o):
    if not isinstance(o, dict):
        raise HttpError(400, 'invalid options')
    for k, v in o.items():
        typ = OPTION_KEYS[kind].get(k)
        if typ is None or isinstance(v, bool) != (typ is bool) or not isinstance(v, typ):
            raise HttpError(400, f'invalid option {k}')
        if k == 'retries' and not 0 <= v <= 5:
            raise HttpError(400, 'invalid option retries')
    if kind == 'upstream':
        o = {'pull': True, 'rebuild': True, **o}
    return o


def _validate(body, cur=None):
    cur = cur or {}
    kind = body.get('kind', cur.get('kind'))
    if kind not in OPTION_KEYS or (cur and kind != cur['kind']):
        raise HttpError(400, 'invalid kind')
    name = body.get('name', cur.get('name'))
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise HttpError(400, 'invalid name')
    enabled = body.get('enabled', cur.get('enabled', True))
    if not isinstance(enabled, bool):
        raise HttpError(400, 'invalid enabled')
    return {'name': name.strip(), 'kind': kind,
            'target': _check_target(kind, body.get('target', cur.get('target'))),
            'spec': _check_spec(body.get('spec', cur.get('spec'))),
            'options': _check_options(kind, body.get('options', cur.get('options', {}))),
            'enabled': enabled}


def _now():
    return dt.datetime.now(UTC).replace(microsecond=0)


def create(name, kind, target, spec, options=None, enabled=True):
    v = _validate({'name': name, 'kind': kind, 'target': target, 'spec': spec,
                   'options': options or {}, 'enabled': enabled})
    nxt = db.iso(next_run(v['spec'], _now())) if v['enabled'] else None
    with db.tx() as c:
        sid = c.execute('INSERT INTO schedules(name,kind,target,spec,options,enabled,next_run_at) VALUES (?,?,?,?,?,?,?)',
                        (v['name'], kind, jobs.canon(v['target']), jobs.canon(v['spec']),
                         jobs.canon(v['options']), int(v['enabled']), nxt)).lastrowid
    wake()
    return sid


def fire(row, now):
    kind, opts = row['kind'], json.loads(row['options'])
    targets = EXPAND[kind](json.loads(row['target']))
    if kind == 'build' and opts.get('only_changed'):
        targets = [t for t in targets if build.needs_build(t['item_id'])]
    if not targets:
        print(f"schedule {row['id']} ({row['name']}): target expands to nothing", flush=True)
    job_opts = {k: v for k, v in opts.items() if k != 'only_changed'}
    ids = [jobs.enqueue(kind, t, 'schedule', row['id'], job_opts) for t in targets]
    with db.tx() as c:
        c.execute('UPDATE schedules SET last_run_at=?, next_run_at=? WHERE id=?',
                  (db.iso(now), db.iso(next_run(json.loads(row['spec']), now, last_run=now)), row['id']))
    return ids


def tick(now=None):
    now = now or _now()
    for row in db.conn().execute('SELECT * FROM schedules WHERE enabled=1 AND next_run_at IS NOT NULL '
                                 'AND next_run_at <= ? ORDER BY id', (db.iso(now),)).fetchall():
        try:
            fire(row, now)
        except Exception:
            traceback.print_exc()


def wake():
    _wake.set()


def _loop():
    while True:
        try:
            tick()
        except Exception:
            traceback.print_exc()
        _wake.wait(30)
        _wake.clear()


def start():
    threading.Thread(target=_loop, daemon=True, name='scheduler').start()


def _row(sid):
    r = db.conn().execute('SELECT * FROM schedules WHERE id=?', (sid,)).fetchone()
    if r is None:
        raise HttpError(404, 'schedule not found')
    return r


def _to_json(r, now):
    d = dict(r)
    for k in ('target', 'spec', 'options'):
        d[k] = json.loads(d[k])
    d['enabled'] = bool(d['enabled'])
    d['next_runs'] = [db.iso(x) for x in next_runs(d['spec'], now, 5, db.parse_iso(d['last_run_at']))] if d['enabled'] else []
    d['last_counts'] = {s: n for s, n in db.conn().execute(
        'SELECT status, count(*) FROM jobs WHERE schedule_id=? AND created_at >= ? GROUP BY status',
        (d['id'], d['last_run_at']))} if d['last_run_at'] else {}
    return d


def list_route(req):
    now = _now()
    return 200, {'schedules': [_to_json(r, now) for r in db.conn().execute('SELECT * FROM schedules ORDER BY id')]}


def create_route(req):
    b = req.json
    return 201, {'id': create(b.get('name'), b.get('kind'), b.get('target'), b.get('spec'),
                               b.get('options'), b.get('enabled', True))}


def update_route(req):
    r = _row(req.params['id'])
    cur = {k: json.loads(r[k]) for k in ('target', 'spec', 'options')}
    cur.update(name=r['name'], kind=r['kind'], enabled=bool(r['enabled']))
    v = _validate(req.json, cur)
    nxt = db.iso(next_run(v['spec'], _now(), db.parse_iso(r['last_run_at']))) if v['enabled'] else None
    with db.tx() as c:
        c.execute('UPDATE schedules SET name=?, target=?, spec=?, options=?, enabled=?, next_run_at=? WHERE id=?',
                  (v['name'], jobs.canon(v['target']), jobs.canon(v['spec']), jobs.canon(v['options']),
                   int(v['enabled']), nxt, r['id']))
    wake()
    return 200, _to_json(_row(r['id']), _now())


def delete_route(req):
    _row(req.params['id'])
    with db.tx() as c:
        c.execute('DELETE FROM schedules WHERE id=?', (req.params['id'],))
    return 200, {'ok': True}


def preview_route(req):
    return 200, {'runs': [db.iso(x) for x in next_runs(_check_spec(req.json.get('spec')), _now())]}


def run_route(req):
    return 200, {'job_ids': fire(_row(req.params['id']), _now())}


ROUTES = [
    ('GET', '/api/schedules', list_route),
    ('POST', '/api/schedules', create_route),
    ('POST', '/api/schedules/preview', preview_route),
    ('PUT', '/api/schedules/{id}', update_route),
    ('DELETE', '/api/schedules/{id}', delete_route),
    ('POST', '/api/schedules/{id}/run', run_route),
]
