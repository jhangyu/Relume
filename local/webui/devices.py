"""Device part: adb runner, mDNS browser, device manager and routes (spec device §2-§5)."""
import os
import re
import signal
import socket
import struct
import subprocess
import threading
import time

from . import db, projects, server
from .server import HttpError, is_int, validate_host, validate_port

ERROR_CODES = {
    'INSTALL_FAILED_UPDATE_INCOMPATIBLE': 'SIGNATURE_MISMATCH',
    'INSTALL_FAILED_VERSION_DOWNGRADE': 'VERSION_DOWNGRADE',
    'INSTALL_FAILED_INSUFFICIENT_STORAGE': 'INSUFFICIENT_STORAGE',
    'INSTALL_FAILED_NO_MATCHING_ABIS': 'NO_MATCHING_ABI',
    'INSTALL_FAILED_USER_RESTRICTED': 'USER_REJECTED',
    'INSTALL_FAILED_ABORTED': 'USER_REJECTED',
}
# install receipt codes the Relume app may send (spec 5.1): adb codes plus the app's own pipeline failures
APP_ERROR_CODES = frozenset(ERROR_CODES.values()) | {'HASH_MISMATCH', 'SIGNER_MISMATCH_FILE', 'INSTALLER_ERROR',
                                                    'USER_REJECTED', 'DEPENDENCY_FAILED'}
APP_ONLY = 'device installs through the Relume app'
_PROP = re.compile(r'^\[([^\]]+)\]: \[(.*)\]$')
_PKG_LINE = re.compile(r'^package:(\S+) versionCode:(\d+)$')


def parse_devices(text):
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith(('*', 'List of devices')):
            out[parts[0]] = parts[1]
    return out


def parse_getprop(text):
    return {m.group(1): m.group(2) for m in map(_PROP.match, text.splitlines()) if m}


def parse_packages(text):
    """`pm list packages --show-versioncode` -> {package: {version_code, version_name: None, signer_sha256: None,
    apk_sha256: None}}."""
    out = {}
    for line in text.splitlines():
        if not line.startswith('package:'):
            continue
        m = _PKG_LINE.match(line.strip())
        if m is None:
            raise ValueError(f'pm list packages: expected "package:<name> versionCode:<n>", got {line!r}')
        out[m[1]] = {'version_code': int(m[2]), 'version_name': None, 'signer_sha256': None, 'apk_sha256': None}
    return out


APK_PATH = re.compile(r'package:(/[A-Za-z0-9._~=+/-]+\.apk)\Z')   # also the guard: the path goes back to a device shell
SHA256SUM = re.compile(r'([0-9a-f]{64})\s')


def parse_pm_path(text):
    """`pm path <pkg>` -> the base APK path (the first package: line, the file Android reports as sourceDir)."""
    line = next((x.strip() for x in text.splitlines() if x.startswith('package:')), '')
    m = APK_PATH.match(line)
    if m is None:
        raise ValueError(f'expected "package:/<path>.apk", got {line!r}')
    return m[1]


def parse_sha256sum(text):
    """`sha256sum <path>` (toybox, Android 8+) -> the lowercase hex digest."""
    m = SHA256SUM.match(text)
    if m is None:
        raise ValueError(f'expected "<64 hex> <path>", got {text[:200]!r}')
    return m[1]


def install_error(text):
    if any(line.strip() == 'Success' for line in text.splitlines()):
        return None
    for name, code in ERROR_CODES.items():
        if name in text:
            return code
    return 'ADB_ERROR'


ADB = ['adb']
_CRASH = ('cannot connect to daemon', 'daemon not running')
CANCEL_CHECK_S = 1   # a running adb command looks at its cancel_event this often
_locks = {}
_locks_guard = threading.Lock()


class AdbTimeout(Exception):
    pass


def _lock(serial):
    with _locks_guard:
        return _locks.setdefault(serial, threading.Lock())


def _exec(argv, timeout, cancel_event):
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors='replace', start_new_session=True)
    except FileNotFoundError:
        return 127, 'adb not found'
    deadline = time.monotonic() + timeout
    while True:
        try:
            out, _ = p.communicate(timeout=max(0, min(CANCEL_CHECK_S, deadline - time.monotonic())))
            return p.returncode, out
        except subprocess.TimeoutExpired:
            if time.monotonic() > deadline or (cancel_event is not None and cancel_event.is_set()):
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.communicate()
                raise AdbTimeout(' '.join(argv[len(ADB):]))


def adb(args, *, serial=None, timeout=60, cancel_event=None, log=None):
    """Only entry point that runs adb. One lock per serial (None = global)."""
    argv = list(ADB) + (['-s', serial] if serial else []) + list(args)
    with _lock(serial):
        rc, out = _exec(argv, timeout, cancel_event)
        if any(s in out for s in _CRASH):
            _exec(list(ADB) + ['start-server'], 60, None)
            rc, out = _exec(argv, timeout, cancel_event)
    if log is not None:
        for line in out.splitlines():
            log.line('adb: ' + line)
    return rc, out


def start():
    try:
        rc, out = adb(['start-server'])
    except AdbTimeout as e:
        rc, out = 1, f'timeout: {e}'
    if rc != 0:
        print(f'warning: adb start-server failed: {out.strip()}', flush=True)


DISCOVERY_TIMEOUT = 2.0
SERVICES = {'_adb-tls-pairing._tcp.local': 'pairing', '_adb-tls-connect._tcp.local': 'connect'}
_MAX_JUMPS = 32


def _qname(name):
    return b''.join(bytes([len(p)]) + p.encode() for p in name.split('.')) + b'\0'


def build_query():
    """DNS-SD legacy unicast query (RFC 6762 6.7): id 0, two PTR questions, sent from an ephemeral port."""
    body = b''.join(_qname(n) + struct.pack('>HH', 12, 1) for n in SERVICES)
    return struct.pack('>HHHHHH', 0, 0, len(SERVICES), 0, 0, 0) + body


def _name(data, off):
    """Decode a (compressed) name; returns (name, offset after it). Pointers must go strictly backwards."""
    labels, end, start, jumps = [], None, off, 0
    while True:
        n = data[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            target = ((n & 0x3F) << 8) | data[off + 1]
            if target >= start or jumps >= _MAX_JUMPS:
                raise ValueError('bad compression pointer')
            if end is None:
                end = off + 2
            jumps += 1
            off = start = target
            continue
        if n & 0xC0 or off + 1 + n > len(data):
            raise ValueError('bad label')
        labels.append(data[off + 1:off + 1 + n].decode('utf-8', 'replace'))
        off += 1 + n
    return '.'.join(labels), (off if end is None else end)


def parse_packet(data):
    """Records (name, type, rdata) for PTR/SRV/A; stops at the first malformed part, never raises."""
    out = []
    try:
        qd, an, ns, ar = struct.unpack_from('>HHHH', data, 4)
        off = 12
        for _ in range(qd):
            off = _name(data, off)[1] + 4
        for _ in range(an + ns + ar):
            name, off = _name(data, off)
            rtype, _cls, _ttl, rdlen = struct.unpack_from('>HHIH', data, off)
            rd, off = off + 10, off + 10 + rdlen
            if off > len(data):
                raise ValueError('truncated record')
            if rtype == 12:
                out.append((name, 12, _name(data, rd)[0]))
            elif rtype == 33:
                port = struct.unpack_from('>H', data, rd + 4)[0]
                out.append((name, 33, (port, _name(data, rd + 6)[0])))
            elif rtype == 1 and rdlen == 4:
                out.append((name, 1, socket.inet_ntoa(data[rd:rd + 4])))
    except (IndexError, ValueError, struct.error):
        pass
    return out


def _merge(records):
    srv = {n.lower(): v for n, t, v in records if t == 33}
    addr = {n.lower(): v for n, t, v in records if t == 1}
    out = {}
    for owner, t, inst in records:
        kind = SERVICES.get(owner.lower()) if t == 12 else None
        s = srv.get(inst.lower()) if kind else None
        if s and s[1].lower() in addr:
            out[inst.split('.')[0]] = {'service': kind, 'host': addr[s[1].lower()], 'port': s[0]}
    return out


def _udp_socket():
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def browse(timeout=None):
    """One mDNS query -> {name: {service, host, port, last_seen}}; {} when nothing answers or the network refuses
    (macOS bridge)."""
    timeout = DISCOVERY_TIMEOUT if timeout is None else timeout
    records = []
    try:
        with _udp_socket() as s:
            s.bind(('', 0))
            s.sendto(build_query(), ('224.0.0.251', 5353))
            end = time.monotonic() + timeout
            while (left := end - time.monotonic()) > 0:
                s.settimeout(left)
                try:
                    data, _ = s.recvfrom(9000)
                except socket.timeout:
                    break
                records += parse_packet(data)
    except OSError as e:
        print(f'mdns browse failed: {e}', flush=True)
    now = time.time()
    return {k: dict(v, last_seen=now) for k, v in _merge(records).items()}


_CODE = re.compile(r'^[0-9]{6}$')


def validate_code(v):
    if not isinstance(v, str) or not _CODE.match(v):
        raise HttpError(400, 'code must be 6 digits')
    return v


def address(dev):
    return f"{dev['host']}:{dev['connect_port']}"


def get(device_id):
    r = db.conn().execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
    return dict(r) if r else None


def dev_or_404(device_id):
    dev = get(device_id)
    if dev is None or is_legacy(dev):   # a legacy row (companion up to v1.2.0) is not a device the server manages
        raise HttpError(404, 'device not found')
    return dev


LEGACY_SQL = "(kind = 'app' AND device_uid IS NULL)"   # companion up to v1.2.0: no ANDROID_ID (decision 1)


def is_legacy(dev):
    return dev['kind'] == 'app' and dev['device_uid'] is None


def is_app(dev):
    return dev['kind'] == 'app'


def device_json(dev, states=None):
    """online is None for app rows: they are never reached over adb. states = `adb devices` map the caller already
    holds; None = read it now (ADB rows only). has_token tells an active phone from a revoked one; the token hash
    itself never leaves the server."""
    out = {k: v for k, v in dev.items() if k != 'token_hash'}
    online = None if is_app(dev) else (online_states() if states is None else states).get(address(dev)) == 'device'
    return dict(out, ota_after_build=bool(dev['ota_after_build']), has_token=dev['token_hash'] is not None, online=online)


def last_line(text):
    lines = [x for x in text.splitlines() if x.strip()]
    return lines[-1].strip() if lines else 'no output'


def _warn(log, text):
    """One failure line: the job log when the caller runs in a job, else the server log."""
    if log is None:
        print(text, flush=True)
    else:
        log.line(text)


_list_failing = False                 # owned by online_states via _note_list_result, guarded by _list_failing_lock
_list_failing_lock = threading.Lock()


def _note_list_result(error):
    """One server-log line per change of `adb devices` health: error = the failure text, None = adb answered."""
    global _list_failing
    with _list_failing_lock:
        if _list_failing == (error is not None):
            return
        _list_failing = error is not None
    print(f'{error}: every ADB device shows offline (logged once until adb answers again)' if error
          else 'adb devices answers again', flush=True)


def online_states():
    """`adb devices` -> {address: state}. adb itself failing shows every ADB device offline and says so once in the log."""
    try:
        rc, out = adb(['devices'], timeout=10)
    except AdbTimeout:
        _note_list_result('adb devices timed out')
        return {}
    if rc != 0:
        _note_list_result(f'adb devices failed (exit {rc}): {last_line(out)}')
        return {}
    _note_list_result(None)
    return parse_devices(out)


def _state(addr):
    return online_states().get(addr)


def _connect(addr, log=None):
    """adb connect output; raises AdbTimeout."""
    return adb(['connect', addr], timeout=20, log=log)[1]


def _try_connect(addr, log):
    """reconnect step: a timed-out connect is logged, the state check after it decides."""
    try:
        _connect(addr, log)
    except AdbTimeout:
        _warn(log, f'adb connect {addr} timed out')


def _save_addr(dev, host, port):
    with db.tx() as c:
        c.execute('UPDATE devices SET host=?, connect_port=? WHERE id=?', (host, port, dev['id']))
    dev.update(host=host, connect_port=port)


def reconnect(dev, *, cancel_event=None, log=None):
    """Spec device 4.2 steps 1-3; returns 'online' | 'offline' | 'unauthorized'."""
    st = _state(address(dev))
    if st not in ('device', 'unauthorized'):
        _try_connect(address(dev), log)
        st = _state(address(dev))
    if st not in ('device', 'unauthorized') and dev.get('mdns_name') and not (cancel_event and cancel_event.is_set()):
        e = browse().get(dev['mdns_name'])
        if e and e['service'] == 'connect' and (e['host'], e['port']) != (dev['host'], dev['connect_port']):
            new = f"{e['host']}:{e['port']}"
            _try_connect(new, log)
            st = _state(new)
            if st == 'device':
                _save_addr(dev, e['host'], e['port'])
    if st == 'device':
        with db.tx() as c:
            c.execute('UPDATE devices SET last_seen_at=? WHERE id=?', (db.utcnow(), dev['id']))
        return 'online'
    return 'unauthorized' if st == 'unauthorized' else 'offline'


def _discovered_connect(host):
    for name, e in sorted(browse().items()):
        if e['service'] == 'connect' and e['host'] == host:
            return name, e['port']
    return None, None


def pair(req):
    b = req.json or {}
    host = validate_host(b.get('host'))
    pair_port = validate_port(b.get('pair_port'), 'pair_port')
    code = validate_code(b.get('code'))
    connect_port = None if b.get('connect_port') is None else validate_port(b['connect_port'], 'connect_port')
    mdns_name, found_port = _discovered_connect(host)
    if connect_port is None:
        if found_port is None:
            raise HttpError(400, 'connect_port required: not discovered')
        connect_port = found_port
    try:
        out = adb(['pair', f'{host}:{pair_port}', code], timeout=30)[1]
    except AdbTimeout:
        raise HttpError(400, 'PAIR_FAILED: timeout')
    if 'Successfully paired' not in out:
        raise HttpError(400, 'PAIR_FAILED: ' + last_line(out))
    addr = f'{host}:{connect_port}'
    try:
        out = _connect(addr)
    except AdbTimeout:
        raise HttpError(409, 'DEVICE_OFFLINE')
    if 'unauthorized' in out or 'failed to authenticate' in out:
        raise HttpError(409, 'UNAUTHORIZED')
    if 'connected to' not in out:
        raise HttpError(409, 'DEVICE_OFFLINE')
    try:
        props = parse_getprop(adb(['shell', 'getprop'], serial=addr, timeout=20)[1])
    except AdbTimeout:
        raise HttpError(409, 'DEVICE_OFFLINE')
    serial = props.get('ro.serialno') or props.get('ro.boot.serialno')
    if not serial:
        raise HttpError(409, 'DEVICE_OFFLINE')
    sdk = props.get('ro.build.version.sdk', '')
    facts = (props.get('ro.product.model'), props.get('ro.product.cpu.abilist'),
             int(sdk) if sdk.isdigit() else None, props.get('ro.build.version.release'))
    now = db.utcnow()
    with db.tx() as c:
        row = c.execute('SELECT id FROM devices WHERE serial=?', (serial,)).fetchone()
        if row:
            did = row['id']
            c.execute('UPDATE devices SET host=?, connect_port=?, mdns_name=COALESCE(?, mdns_name), model=?, '
                      'abi_list=?, sdk=?, android_release=?, last_seen_at=? WHERE id=?',
                      (host, connect_port, mdns_name, *facts, now, did))
        else:
            did = c.execute('INSERT INTO devices(name,serial,mdns_name,host,connect_port,model,abi_list,sdk,'
                            'android_release,ota_after_build,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,0,?)',
                            (facts[0] or serial, serial, mdns_name, host, connect_port, *facts, now)).lastrowid
    return 200, {'device': device_json(get(did), {addr: 'device'})}


def connect(req):
    dev = dev_or_404(req.params['id'])
    if is_app(dev):
        raise HttpError(409, f"device {dev['id']} is kind 'app': it reports through the Relume app and has no adb connection")
    b = req.json or {}
    host = dev['host'] if b.get('host') is None else validate_host(b['host'])
    port = dev['connect_port'] if b.get('connect_port') is None else validate_port(b['connect_port'], 'connect_port')
    if (host, port) != (dev['host'], dev['connect_port']):
        _save_addr(dev, host, port)
    st = reconnect(dev)
    if st != 'online':
        raise HttpError(409, 'UNAUTHORIZED' if st == 'unauthorized' else 'DEVICE_OFFLINE')
    return 200, {'device': device_json(get(dev['id']), {address(dev): 'device'})}


def patch_device(req):
    dev = dev_or_404(req.params['id'])
    b = req.json
    if not b or set(b) - {'name', 'ota_after_build'}:
        raise HttpError(400, 'only name and ota_after_build can change')
    sets = {}
    if 'name' in b:
        name = b['name'].strip() if isinstance(b['name'], str) else ''
        if not 1 <= len(name) <= 64:
            raise HttpError(400, 'name must be 1-64 characters')
        sets['name'] = name
    if 'ota_after_build' in b:
        if is_app(dev):
            raise HttpError(400, 'ota_after_build is for ADB devices: this device installs through the Relume app')
        if not isinstance(b['ota_after_build'], bool):
            raise HttpError(400, 'ota_after_build must be true or false')
        sets['ota_after_build'] = int(b['ota_after_build'])
    with db.tx() as c:  # keys come from the fixed whitelist above
        c.execute(f"UPDATE devices SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), dev['id']))
    return 200, {'device': device_json(get(dev['id']))}


def delete_device(req):
    dev = dev_or_404(req.params['id'])
    if not is_app(dev):
        try:
            adb(['disconnect', address(dev)], timeout=10)
        except AdbTimeout:
            print(f"adb disconnect {address(dev)} timed out: device {dev['id']} deleted anyway", flush=True)
    with db.tx() as c:
        c.execute('DELETE FROM ota_installs WHERE device_id=?', (dev['id'],))
        c.execute('DELETE FROM devices WHERE id=?', (dev['id'],))
    return 200, {'deleted': dev['id']}


def put_assignments(req):
    """Items replace device_assignments; project_ids, when present, replace device_projects (spec follow-project 5.5)."""
    me = get(req.device_id) if req.device_id is not None else None
    if me and is_legacy(me):
        # Companion up to v1.2.0 assigns the app to its own row before every install and gives up on any error:
        # answer as if done, change nothing (the explicit-id plan that follows needs no assignment).
        if req.params['id'] != me['id']:
            raise HttpError(403, 'a legacy token can only name its own device')
        return 200, {'device': device_json(me)}
    dev = dev_or_404(req.params['id'])
    body = req.json or {}
    ids, pids = body.get('item_ids'), body.get('project_ids')
    for key, v in (('item_ids', ids), ('project_ids', [] if pids is None else pids)):
        server.ints(v, key)
    ids = list(dict.fromkeys(ids))
    _write_assignments(dev['id'], ids, None if pids is None else list(dict.fromkeys(pids)))
    return 200, {'device': device_json(get(dev['id']))}


def _write_assignments(device_id, item_ids, project_ids):
    """Full-list write: item_ids replace device_assignments, project_ids (None = keep) replace device_projects.
    The conflict check runs inside the write transaction (BEGIN IMMEDIATE): no other writer can commit between the
    check and the write."""
    with db.tx() as c:
        projects.check_assignment(item_ids, projects.assigned_projects(device_id) if project_ids is None else project_ids)
        # a removed (disabled) app is hidden from every client, so a full-list PUT cannot mean to drop it
        c.execute('DELETE FROM device_assignments WHERE device_id=? AND item_id NOT IN (SELECT id FROM item WHERE enabled=0)',
                  (device_id,))
        c.executemany('INSERT OR IGNORE INTO device_assignments(device_id,item_id) VALUES (?,?)',
                      [(device_id, i) for i in item_ids])
        if project_ids is not None:
            c.execute('DELETE FROM device_projects WHERE device_id=?', (device_id,))
            c.executemany('INSERT INTO device_projects(device_id,project_id) VALUES (?,?)',
                          [(device_id, p) for p in project_ids])


KINDS = {'items': ('device_assignments', 'item_id'), 'projects': ('device_projects', 'project_id')}


def assign_one(device_id, kind, ref_id):
    """Add one item or project ('items'/'projects') to a phone (decision 17): idempotent, same conflict rule as the
    full-list PUT."""
    table, col = KINDS[kind]
    with db.tx() as c:
        ids = [r[0] for r in c.execute('SELECT item_id FROM device_assignments WHERE device_id=?', (device_id,))]
        pids = projects.assigned_projects(device_id)
        if kind == 'items' and ref_id not in ids:
            ids.append(ref_id)
        if kind == 'projects' and ref_id not in pids:
            pids.append(ref_id)
        projects.check_assignment(ids, pids)
        c.execute(f'INSERT OR IGNORE INTO {table}(device_id,{col}) VALUES (?,?)', (device_id, ref_id))


def unassign_one(device_id, kind, ref_id):
    """Remove one item or project ('items'/'projects') from a phone (decision 17); an absent row is a no-op."""
    table, col = KINDS[kind]
    with db.tx() as c:
        c.execute(f'DELETE FROM {table} WHERE device_id=? AND {col}=?', (device_id, ref_id))


def _me_target(req):
    """-> (device id, kind, ref id) of /api/me/assignments/<kind>/<id>; the device is the token's own row."""
    kind, ref = req.params['kind'], req.params['id']
    if kind not in KINDS:
        raise HttpError(404, f"unknown assignment kind {kind!r}: expected 'items' or 'projects'")
    if not is_int(ref):
        raise HttpError(400, f'id must be an integer, got {ref!r}')
    return req.device_id, kind, ref


def me_assign(req):
    device_id, kind, ref = _me_target(req)
    assign_one(device_id, kind, ref)
    return 200, {'device': device_json(get(device_id))}


def me_unassign(req):
    device_id, kind, ref = _me_target(req)
    unassign_one(device_id, kind, ref)
    return 200, {'device': device_json(get(device_id))}


def resolve_devices(target):
    """ADB fan-out targets: app rows never take part."""
    c = db.conn()
    if target.get('all') is True:
        return [r['id'] for r in c.execute("SELECT id FROM devices WHERE kind='adb' ORDER BY id")]
    ids = target.get('device_ids')
    if isinstance(ids, list):
        have = {r['id'] for r in c.execute("SELECT id FROM devices WHERE kind='adb'")}
        return [i for i in ids if i in have]
    return []


def discovered(req):
    found = browse()
    known = {r['mdns_name']: r['id'] for r in
             db.conn().execute('SELECT id, mdns_name FROM devices WHERE mdns_name IS NOT NULL')}
    pairing = [{'name': n, 'host': e['host'], 'port': e['port'], 'last_seen': e['last_seen']}
               for n, e in sorted(found.items()) if e['service'] == 'pairing']
    connect_ = [{'name': n, 'host': e['host'], 'port': e['port'], 'device_id': known.get(n), 'last_seen': e['last_seen']}
                for n, e in sorted(found.items()) if e['service'] == 'connect']
    return 200, {'pairing': pairing, 'connect': connect_}


ROUTES = [
    ('GET', '/api/devices/discovered', discovered),
    ('POST', '/api/devices/pair', pair),
    ('POST', '/api/devices/{id}/connect', connect),
    ('PATCH', '/api/devices/{id}', patch_device),
    ('DELETE', '/api/devices/{id}', delete_device),
    ('PUT', '/api/devices/{id}/assignments', put_assignments),
    ('PUT', '/api/me/assignments/{kind}/{id}', me_assign),
    ('DELETE', '/api/me/assignments/{kind}/{id}', me_unassign),
]
