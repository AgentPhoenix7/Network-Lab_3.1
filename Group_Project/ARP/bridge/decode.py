"""Decode one lab1 frame_t (130 hex chars) into the contract §3.1 `decoded` object. Pure: struct and checksums only."""
import ipaddress
import struct

FRAME = struct.Struct(">B6s6sHH44sI")  # lab1 frame_t, big-endian: type, SRC, DST, len, seq, payload, fcs
ARP = struct.Struct(">HHBBH6s4s6s4s")  # RFC 826 payload, 28 bytes
IPV4 = struct.Struct(">BBHHHBBH4s4s")  # 20-byte header (IHL 5)
ICMP = struct.Struct(">BBHHH")         # 8-byte header; 16 bytes of data follow
FRAME_TYPES = {0x06: "ARP", 0x08: "IPv4"}
ARP_OPS = {1: "request", 2: "reply"}
ICMP_TYPES = {8: "echo_req", 0: "echo_rep"}


def inet_sum(data: bytes) -> int:
    """Ones-complement sum of 16-bit words (RFC 1071). A header with a valid checksum sums to 0xFFFF."""
    total = sum(struct.unpack(f">{len(data) // 2}H", data))
    while total >> 16:  # end-around carry
        total = (total & 0xFFFF) + (total >> 16)
    return total


def _mac(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def _ip(raw: bytes) -> str:
    return str(ipaddress.IPv4Address(raw))


def _arp(body: bytes) -> dict:
    htype, ptype, hlen, plen, oper, sha, spa, tha, tpa = ARP.unpack(body[:ARP.size])
    return {"htype": htype, "ptype": f"0x{ptype:04x}", "hlen": hlen, "plen": plen,
            "oper": ARP_OPS.get(oper, oper), "sha": _mac(sha), "spa": _ip(spa),
            "tha": _mac(tha), "tpa": _ip(tpa)}


def _icmp(seg: bytes) -> dict:
    typ, _code, _csum, ident, seq = ICMP.unpack(seg[:ICMP.size])
    # the ICMP checksum covers the whole segment (header + data), so a valid one sums to 0xFFFF
    return {"type": ICMP_TYPES.get(typ, typ), "id": ident, "seq": seq,
            "checksum_ok": inet_sum(seg) == 0xFFFF}


def _ipv4(body: bytes) -> dict:
    hdr = body[:IPV4.size]
    _vihl, _tos, _total, _id, _frag, ttl, proto, csum, src, dst = IPV4.unpack(hdr)
    out = {"ip": {"ttl": ttl, "proto": proto, "src": _ip(src), "dst": _ip(dst),
                  "checksum": f"0x{csum:04x}", "checksum_ok": inet_sum(hdr) == 0xFFFF}}
    if proto == 1:  # ICMP
        out["icmp"] = _icmp(body[IPV4.size:])
    return out


def decode(frame_hex: str) -> dict:
    """Return {"eth": ..., "arp": ...} or {"eth": ..., "ip": ..., "icmp": ...} (icmp only for proto 1)."""
    raw = bytes.fromhex(frame_hex)
    if len(raw) != FRAME.size:
        raise ValueError(f"frame must be {FRAME.size} bytes, got {len(raw)}")
    ftype, src, dst, length, seq, payload, fcs = FRAME.unpack(raw)
    if ftype not in FRAME_TYPES:
        raise ValueError(f"unknown frame_type 0x{ftype:02x}")
    eth = {"type": FRAME_TYPES[ftype], "src": _mac(src), "dst": _mac(dst), "len": length,
           "seq": seq, "fcs": f"0x{fcs:08x}"}
    body = payload[:length]  # only the bytes the frame says are used
    if ftype == 0x06:
        return {"eth": eth, "arp": _arp(body)}
    return {"eth": eth, **_ipv4(body)}
