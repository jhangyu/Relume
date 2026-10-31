"""Read package, versionCode and versionName of an APK through a byte-range reader.

read(offset, n) must return exactly n bytes (callers use explicit ranges: the Play CDN answers 416 to suffix ranges).
Path: zip end record -> central directory -> AndroidManifest.xml entry -> inflate -> Android binary XML.
"""
import struct
import zlib
from typing import Callable, NamedTuple

TAIL_BYTES = 65536
MAX_CENTRAL_DIR = 4 << 20
MAX_MANIFEST_COMPRESSED = 2 << 20
MAX_MANIFEST_BYTES = 8 << 20
MANIFEST_NAME = b'AndroidManifest.xml'
EOCD_SIG = b'PK\x05\x06'
CD_SIG = b'PK\x01\x02'
LOCAL_SIG = b'PK\x03\x04'
EOCD_LEN, CD_LEN, LOCAL_LEN = 22, 46, 30
ZIP64_MARK = 0xFFFFFFFF
STORED, DEFLATED = 0, 8
RES_XML, RES_STRING_POOL, RES_XML_START_ELEMENT = 3, 1, 0x102
UTF8_FLAG = 0x100
TYPE_STRING, TYPE_INT_DEC = 3, 0x10
NO_STRING = 0xFFFFFFFF


class ManifestError(Exception):
    pass


class Manifest(NamedTuple):
    package: str
    version_code: int
    version_name: str


def _need(cond: bool, field: str, expected: str, actual) -> None:
    if not cond:
        raise ManifestError(f'APK {field}: expected {expected}, got {actual}')


def _read(read: Callable[[int, int], bytes], offset: int, n: int, size: int, field: str) -> bytes:
    _need(0 <= offset and n >= 0 and offset + n <= size, field, f'range inside {size} bytes', f'{offset}+{n}')
    data = read(offset, n)
    _need(len(data) == n, field, f'{n} bytes at {offset}', f'{len(data)} bytes')
    return data


def _find_entry(read, size):
    tail_len = min(TAIL_BYTES, size)
    tail = _read(read, size - tail_len, tail_len, size, 'tail')
    i = tail.rfind(EOCD_SIG)
    _need(i >= 0, 'end record', f'{EOCD_SIG!r} in last {tail_len} bytes', 'not found')
    _need(len(tail) - i >= EOCD_LEN, 'end record', f'{EOCD_LEN} bytes', len(tail) - i)
    cd_size, cd_off = struct.unpack('<II', tail[i + 12:i + 20])
    _need(ZIP64_MARK not in (cd_size, cd_off), 'central directory', 'non-zip64 offsets', 'zip64 marker')
    _need(cd_size <= MAX_CENTRAL_DIR, 'central directory size', f'<= {MAX_CENTRAL_DIR}', cd_size)
    cd = _read(read, cd_off, cd_size, size, 'central directory')
    p = 0
    while cd[p:p + 4] == CD_SIG:
        method, csize, usize, nlen, elen, clen = struct.unpack('<H8xIIHHH', cd[p + 10:p + 34])
        lho, = struct.unpack('<I', cd[p + 42:p + 46])
        if cd[p + CD_LEN:p + CD_LEN + nlen] == MANIFEST_NAME:
            return method, csize, usize, lho
        p += CD_LEN + nlen + elen + clen
    raise ManifestError(f'APK central directory: expected an AndroidManifest.xml entry, got none in {cd_size} bytes')


def _manifest_bytes(read, size) -> bytes:
    method, csize, usize, lho = _find_entry(read, size)
    _need(csize <= MAX_MANIFEST_COMPRESSED, 'manifest compressed size', f'<= {MAX_MANIFEST_COMPRESSED}', csize)
    _need(usize <= MAX_MANIFEST_BYTES, 'manifest size', f'<= {MAX_MANIFEST_BYTES}', usize)
    hdr = _read(read, lho, LOCAL_LEN, size, 'local header')
    _need(hdr[:4] == LOCAL_SIG, 'local header', repr(LOCAL_SIG), hdr[:4])
    nlen, elen = struct.unpack('<HH', hdr[26:30])
    body = _read(read, lho + LOCAL_LEN + nlen + elen, csize, size, 'manifest data')
    if method == STORED:
        return body
    _need(method == DEFLATED, 'manifest compression method', f'{STORED} or {DEFLATED}', method)
    d = zlib.decompressobj(-15)
    out = d.decompress(body, MAX_MANIFEST_BYTES)
    _need(not d.unconsumed_tail, 'manifest inflate', f'<= {MAX_MANIFEST_BYTES} bytes', 'more data')
    return out


def _pool_string(b: bytes, base: int, off: int, utf8: bool) -> str:
    q = base + off
    if utf8:
        q += 1 if b[q] & 0x80 == 0 else 2  # skip the utf16 char count
        n = b[q]
        if n & 0x80:
            n = ((n & 0x7F) << 8) | b[q + 1]
            q += 2
        else:
            q += 1
        return b[q:q + n].decode('utf-8')
    n, = struct.unpack('<H', b[q:q + 2])
    return b[q + 2:q + 2 + 2 * n].decode('utf-16-le')


def _manifest_attrs(b: bytes) -> dict:
    _need(struct.unpack('<H', b[:2])[0] == RES_XML, 'binary XML header', str(RES_XML), b[:2])
    p, strings = 8, []
    while p < len(b):
        ctype, _, csize = struct.unpack('<HHI', b[p:p + 8])
        _need(csize >= 8, 'chunk size', '>= 8', csize)
        if ctype == RES_STRING_POOL:
            cnt, _, flags, sstart = struct.unpack('<IIII', b[p + 8:p + 24])
            offs = struct.unpack(f'<{cnt}I', b[p + 28:p + 28 + 4 * cnt])
            strings = [_pool_string(b, p + sstart, o, bool(flags & UTF8_FLAG)) for o in offs]
        elif ctype == RES_XML_START_ELEMENT:
            _, name, astart, asize, acount = struct.unpack('<IIHHH', b[p + 16:p + 30])
            _need(strings[name] == 'manifest', 'first element', 'manifest', strings[name])
            out, a = {}, p + 16 + astart
            for _ in range(acount):
                _, an, raw, _, _, dtype, data = struct.unpack('<IIIHBBI', b[a:a + 20])
                if dtype == TYPE_STRING:
                    out[strings[an]] = strings[data]
                elif dtype == TYPE_INT_DEC:
                    out[strings[an]] = data
                elif raw != NO_STRING:
                    out[strings[an]] = strings[raw]
                a += asize
            return out
        p += csize
    raise ManifestError('APK binary XML: expected a start element, got none')


def read_manifest(read: Callable[[int, int], bytes], size: int) -> Manifest:
    try:
        attrs = _manifest_attrs(_manifest_bytes(read, size))
    except (struct.error, IndexError, zlib.error, UnicodeDecodeError) as e:
        raise ManifestError(f'APK of {size} bytes is malformed: expected a valid zip and binary XML, got {e!r}') from e
    for key in ('package', 'versionCode', 'versionName'):
        _need(key in attrs, f'manifest attribute {key}', 'present', 'missing')
    _need(isinstance(attrs['versionCode'], int), 'versionCode', 'integer', repr(attrs['versionCode']))
    return Manifest(attrs['package'], attrs['versionCode'], attrs['versionName'])
