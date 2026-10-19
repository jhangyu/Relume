import socket
import struct
import time
import unittest
from unittest import mock

from webui import devices


def qname(name):
    return b''.join(bytes([len(p)]) + p.encode() for p in name.split('.')) + b'\0'


def ptr(off):
    return struct.pack('>H', 0xC000 | off)


def build_reply():
    """PTR + SRV + A for one connect service, every repeated name compressed."""
    b = bytearray(struct.pack('>HHHHHH', 0, 0x8400, 0, 1, 0, 2))
    svc_off = len(b)
    b += qname('_adb-tls-connect._tcp.local')
    local_off = svc_off + bytes(b[svc_off:]).index(b'\x05local')
    inst_rdata = bytes([15]) + b'adb-ABC123-x1y2' + ptr(svc_off)
    b += struct.pack('>HHIH', 12, 1, 120, len(inst_rdata))
    inst_off = len(b)
    b += inst_rdata
    srv_rdata = struct.pack('>HHH', 0, 0, 37011) + bytes([8]) + b'Pixel-7a' + ptr(local_off)
    b += ptr(inst_off) + struct.pack('>HHIH', 33, 1, 120, len(srv_rdata))
    tgt_off = len(b) + 6
    b += srv_rdata
    b += ptr(tgt_off) + struct.pack('>HHIH', 1, 1, 120, 4) + bytes([192, 168, 1, 20])
    return bytes(b)


class FakeSock:
    def __init__(self, packets=(), send_error=None):
        self.packets, self.send_error, self.sent = list(packets), send_error, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def bind(self, addr):
        pass

    def settimeout(self, t):
        pass

    def sendto(self, data, addr):
        if self.send_error:
            raise self.send_error
        self.sent.append((data, addr))

    def recvfrom(self, n):
        if not self.packets:
            raise socket.timeout()
        return self.packets.pop(0), ('192.168.1.20', 5353)

    def close(self):
        pass


EXPECTED = {'adb-ABC123-x1y2': {'service': 'connect', 'host': '192.168.1.20', 'port': 37011}}


class MdnsTest(unittest.TestCase):
    def test_query_has_two_ptr_questions(self):
        q = devices.build_query()
        self.assertEqual(struct.unpack_from('>HHHHHH', q), (0, 0, 2, 0, 0, 0))
        self.assertIn(b'\x10_adb-tls-pairing\x04_tcp\x05local\x00\x00\x0c\x00\x01', q)

    def test_parse_compressed_reply(self):
        recs = devices.parse_packet(build_reply())
        self.assertEqual([r[1] for r in recs], [12, 33, 1])
        self.assertEqual(devices._merge(recs), EXPECTED)

    def test_pointer_loop_returns(self):
        pkt = struct.pack('>HHHHHH', 0, 0x8400, 0, 1, 0, 0) + ptr(12) + b'\x00' * 10
        t = time.monotonic()
        self.assertEqual(devices.parse_packet(pkt), [])
        self.assertLess(time.monotonic() - t, 1)

    def test_forward_pointer_rejected(self):
        pkt = struct.pack('>HHHHHH', 0, 0x8400, 0, 1, 0, 0) + ptr(40) + b'\x00' * 40
        self.assertEqual(devices.parse_packet(pkt), [])

    def test_truncated_packet(self):
        recs = devices.parse_packet(build_reply()[:-3])
        self.assertEqual([r[1] for r in recs], [12, 33])

    def test_sendto_oserror_empty(self):
        with mock.patch.object(devices, '_udp_socket', return_value=FakeSock(send_error=OSError('unreachable'))):
            self.assertEqual(devices.browse(timeout=0.2), {})

    def test_browse_collects_replies(self):
        sock = FakeSock(packets=[build_reply(), b'\x00\x01'])
        with mock.patch.object(devices, '_udp_socket', return_value=sock):
            self.assertEqual(devices.browse(timeout=0.2), EXPECTED)
        self.assertEqual(sock.sent[0][1], ('224.0.0.251', 5353))
        self.assertIn('last_seen', devices.DISCOVERED['adb-ABC123-x1y2'])
