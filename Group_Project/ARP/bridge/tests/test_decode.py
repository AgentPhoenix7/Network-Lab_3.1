"""B4: decode() matches the golden ARP payload, and hand-built IPv4/ICMP frames decode with checksums checked."""
import struct
from pathlib import Path

import pytest

from bridge.decode import decode

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "golden" / "arp_request.hex"
SRC = bytes.fromhex("020000000100")
DST_BCAST = bytes.fromhex("ffffffffffff")
DST_R1 = bytes.fromhex("020000000300")


def checksum(data: bytes) -> int:
    total = sum(struct.unpack(f">{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def frame(ftype: int, src: bytes, dst: bytes, payload: bytes, seq: int, fcs: int) -> str:
    """Pack a lab1 frame_t (src before dst) and return its hex, padding the payload to 44 bytes."""
    padded = payload.ljust(44, b"\x00")
    return struct.pack(">B6s6sHH44sI", ftype, src, dst, len(payload), seq, padded, fcs).hex()


def test_golden_arp_request_decodes():
    golden = bytes.fromhex(GOLDEN.read_text().strip())
    hex_frame = frame(0x06, SRC, DST_BCAST, golden, seq=3, fcs=0x1C2D3E4F)
    assert decode(hex_frame) == {
        "eth": {"type": "ARP", "src": "02:00:00:00:01:00", "dst": "ff:ff:ff:ff:ff:ff",
                "len": 28, "seq": 3, "fcs": "0x1c2d3e4f"},
        "arp": {"htype": 1, "ptype": "0x0800", "hlen": 6, "plen": 4, "oper": "request",
                "sha": "b2:34:55:10:22:10", "spa": "130.23.43.20",
                "tha": "00:00:00:00:00:00", "tpa": "130.23.43.25"},
    }


def _ipv4_echo_frame(icmp_type: int, flip_ip_byte: bool = False, flip_icmp_byte: bool = False) -> str:
    icmp = struct.pack(">BBHHH", icmp_type, 0, 0, 1, 0) + b"ARPLAB-PING-0000"
    icmp = icmp[:2] + struct.pack(">H", checksum(icmp)) + icmp[4:]
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 44, 1, 0, 64, 1, 0,
                     bytes([141, 23, 56, 10]), bytes([141, 23, 56, 21]))
    ip = ip[:10] + struct.pack(">H", checksum(ip)) + ip[12:]
    if flip_ip_byte:
        ip = ip[:8] + bytes([ip[8] ^ 0x01]) + ip[9:]
    if flip_icmp_byte:
        icmp = icmp[:-1] + bytes([icmp[-1] ^ 0x01])
    return frame(0x08, SRC, DST_R1, ip + icmp, seq=4, fcs=0xAABBCCDD)


def test_ipv4_echo_request_decodes():
    decoded = decode(_ipv4_echo_frame(icmp_type=8))
    ip_csum = decoded["ip"]["checksum"]
    assert decoded == {
        "eth": {"type": "IPv4", "src": "02:00:00:00:01:00", "dst": "02:00:00:00:03:00",
                "len": 44, "seq": 4, "fcs": "0xaabbccdd"},
        "ip": {"ttl": 64, "proto": 1, "src": "141.23.56.10", "dst": "141.23.56.21",
               "checksum": ip_csum, "checksum_ok": True},
        "icmp": {"type": "echo_req", "id": 1, "seq": 0, "checksum_ok": True},
    }


def test_ipv4_echo_reply_type_and_bad_checksums_are_flagged():
    assert decode(_ipv4_echo_frame(icmp_type=0))["icmp"]["type"] == "echo_rep"
    bad_ip = decode(_ipv4_echo_frame(8, flip_ip_byte=True))
    assert bad_ip["ip"]["checksum_ok"] is False and bad_ip["icmp"]["checksum_ok"] is True
    bad_icmp = decode(_ipv4_echo_frame(8, flip_icmp_byte=True))
    assert bad_icmp["ip"]["checksum_ok"] is True and bad_icmp["icmp"]["checksum_ok"] is False


@pytest.mark.parametrize("hex_frame", ["06" * 10, "00" * 66])
def test_rejects_wrong_length(hex_frame):
    with pytest.raises(ValueError, match="frame must be 65 bytes"):
        decode(hex_frame)


def test_rejects_unknown_frame_type():
    with pytest.raises(ValueError, match="unknown frame_type"):
        decode(frame(0x09, SRC, DST_BCAST, b"", seq=0, fcs=0))
