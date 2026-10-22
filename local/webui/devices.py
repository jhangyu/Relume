"""Device part: adb runner, mDNS browser, device manager and routes (spec device §2-§5)."""
import importlib
import os
import re
import signal
import socket
import struct
import subprocess
import threading
import time

from . import db
from .server import HttpError, validate_host, validate_port

ERROR_CODES = {
    'INSTALL_FAILED_UPDATE_INCOMPATIBLE': 'SIGNATURE_MISMATCH',
    'INSTALL_FAILED_VERSION_DOWNGRADE': 'VERSION_DOWNGRADE',
    'INSTALL_FAILED_INSUFFICIENT_STORAGE': 'INSUFFICIENT_STORAGE',
    'INSTALL_FAILED_NO_MATCHING_ABIS': 'NO_MATCHING_ABI',
    'INSTALL_FAILED_USER_RESTRICTED': 'USER_REJECTED',
    'INSTALL_FAILED_ABORTED': 'USER_REJECTED',
}
_PROP = re.compile(r'^\[([^\]]+)\]: \[(.*)\]$')


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
    return {line[8:].strip() for line in text.splitlines() if line.startswith('package:')}


def install_error(text):
    if any(line.strip() == 'Success' for line in text.splitlines()):
        return None
    for name, code in ERROR_CODES.items():
        if name in text:
            return code
    return 'ADB_ERROR'


ADB = ['adb']
_CRASH = ('cannot connect to daemon', 'daemon not running')
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
            out, _ = p.communicate(timeout=1)
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
DISCOVERED = {}
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
    """One D3 query; {} when nothing answers or the network refuses (macOS bridge, spec D4)."""
    global DISCOVERED
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
    except OSError:
        pass
    found = _merge(records)
    now = time.time()
    DISCOVERED = {k: dict(v, last_seen=now) for k, v in found.items()}
    return found


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


def _dev_or_404(device_id):
    dev = get(device_id)
    if dev is None:
        raise HttpError(404, 'device not found')
    return dev


def device_json(dev, states):
    return dict(dev, ota_after_build=bool(dev['ota_after_build']), online=states.get(address(dev)) == 'device')


def _last(text):
    lines = [x for x in text.splitlines() if x.strip()]
    return lines[-1].strip() if lines else 'no output'


def online_states():
    try:
        rc, out = adb(['devices'], timeout=10)
    except AdbTimeout:
        return {}
    return parse_devices(out) if rc == 0 else {}


def _state(addr):
    return online_states().get(addr)


def _connect(addr, log=None):
    try:
        return adb(['connect', addr], timeout=20, log=log)[1]
    except AdbTimeout:
        return 'timeout'


def _save_addr(dev, host, port):
    with db.tx() as c:
        c.execute('UPDATE devices SET host=?, connect_port=? WHERE id=?', (host, port, dev['id']))
    dev.update(host=host, connect_port=port)


def reconnect(dev, *, cancel_event=None, log=None):
    """Spec device 4.2 steps 1-3; returns 'online' | 'offline' | 'unauthorized'."""
    st = _state(address(dev))
    if st not in ('device', 'unauthorized'):
        _connect(address(dev), log)
        st = _state(address(dev))
    if st not in ('device', 'unauthorized') and dev.get('mdns_name') and not (cancel_event and cancel_event.is_set()):
        e = browse().get(dev['mdns_name'])
        if e and e['service'] == 'connect' and (e['host'], e['port']) != (dev['host'], dev['connect_port']):
            new = f"{e['host']}:{e['port']}"
            _connect(new, log)
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
        raise HttpError(400, 'PAIR_FAILED: ' + _last(out))
    addr = f'{host}:{connect_port}'
    out = _connect(addr)
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
    dev = _dev_or_404(req.params['id'])
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
    dev = _dev_or_404(req.params['id'])
    b = req.json
    if not isinstance(b, dict) or not b or set(b) - {'name', 'ota_after_build'}:
        raise HttpError(400, 'only name and ota_after_build can change')
    sets = {}
    if 'name' in b:
        name = b['name'].strip() if isinstance(b['name'], str) else ''
        if not 1 <= len(name) <= 64:
            raise HttpError(400, 'name must be 1-64 characters')
        sets['name'] = name
    if 'ota_after_build' in b:
        if not isinstance(b['ota_after_build'], bool):
            raise HttpError(400, 'ota_after_build must be true or false')
        sets['ota_after_build'] = int(b['ota_after_build'])
    with db.tx() as c:  # keys come from the fixed whitelist above
        c.execute(f"UPDATE devices SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), dev['id']))
    return 200, {'device': device_json(get(dev['id']), online_states())}


def delete_device(req):
    dev = _dev_or_404(req.params['id'])
    try:
        adb(['disconnect', address(dev)], timeout=10)
    except AdbTimeout:
        pass
    with db.tx() as c:
        c.execute('DELETE FROM devices WHERE id=?', (dev['id'],))
    return 200, {'deleted': dev['id']}


def _check_assignment(item_ids, project_ids):
    importlib.import_module('webui.projects').check_assignment(item_ids, project_ids)


def put_assignments(req):
    """Items replace device_assignments; project_ids, when present, replace device_projects (spec follow-project 5.5)."""
    dev = _dev_or_404(req.params['id'])
    body = req.json or {}
    ids, pids = body.get('item_ids'), body.get('project_ids')
    for key, v in (('item_ids', ids), ('project_ids', [] if pids is None else pids)):
        if not isinstance(v, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in v):
            raise HttpError(400, f'{key} must be a list of integers')
    ids = list(dict.fromkeys(ids))
    keep = pids is None
    pids = [r[0] for r in db.conn().execute('SELECT project_id FROM device_projects WHERE device_id=? ORDER BY project_id',
                                             (dev['id'],))] if keep else list(dict.fromkeys(pids))
    _check_assignment(ids, pids)
    with db.tx() as c:
        c.execute('DELETE FROM device_assignments WHERE device_id=?', (dev['id'],))
        c.executemany('INSERT INTO device_assignments(device_id,item_id) VALUES (?,?)', [(dev['id'], i) for i in ids])
        if not keep:
            c.execute('DELETE FROM device_projects WHERE device_id=?', (dev['id'],))
            c.executemany('INSERT INTO device_projects(device_id,project_id) VALUES (?,?)',
                          [(dev['id'], p) for p in pids])
    return 200, {'device': device_json(get(dev['id']), online_states())}


def resolve_devices(target):
    c = db.conn()
    if target.get('all') is True:
        return [r['id'] for r in c.execute('SELECT id FROM devices ORDER BY id')]
    ids = target.get('device_ids')
    if isinstance(ids, list):
        have = {r['id'] for r in c.execute('SELECT id FROM devices')}
        return [i for i in ids if i in have]
    return []


def discovered(req):
    browse()
    found = DISCOVERED
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
]
