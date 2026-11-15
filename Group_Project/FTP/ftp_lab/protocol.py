"""FTP control framing and streaming NVT ASCII conversion (RFC 959)."""

import socket
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

BLOCK_SIZE = 65536


class ProtocolError(ValueError):
    """Malformed, truncated, or oversized control message."""


class ControlStream:
    """A TCP recv is not a message: retain incomplete and pipelined lines."""

    def __init__(self, sock: socket.socket, max_line: int = 16384, max_send_line: int = 16384):
        self.sock = sock
        self.max_line = max_line
        self.max_send_line = max_send_line
        self.buffer = bytearray()

    def readline(self) -> str:
        while True:
            end = self.buffer.find(b'\n')
            if end >= 0:
                if end + 1 > self.max_line or end == 0 or self.buffer[end - 1] != 13:
                    raise ProtocolError('Expected a bounded CRLF-terminated line')
                wire = bytes(self.buffer[:end - 1])
                del self.buffer[:end + 1]
                if any(c < 32 and c != 9 or c == 127 for c in wire):
                    raise ProtocolError('Control character in line')
                try:
                    return wire.decode('utf-8')
                except UnicodeDecodeError as exc:
                    raise ProtocolError('Invalid UTF-8') from exc
            if len(self.buffer) >= self.max_line:
                raise ProtocolError('Control line too long')
            chunk = self.sock.recv(4096)
            if not chunk:
                if self.buffer:
                    raise ProtocolError('Truncated control line')
                raise EOFError('Control connection closed')
            self.buffer.extend(chunk)

    def send_line(self, line: str) -> None:
        if any(c in line for c in '\r\n\x00'):
            raise ProtocolError('Control-line injection')
        wire = line.encode('utf-8') + b'\r\n'
        if len(wire) > self.max_send_line:
            raise ProtocolError('Control line too long')
        self.sock.sendall(wire)


@dataclass(frozen=True)
class Reply:
    code: int
    lines: tuple[str, ...]

    @property
    def message(self) -> str:
        return self.lines[-1][4:]


def read_reply(stream: ControlStream) -> Reply:
    first = stream.readline()
    if len(first) < 4 or not first[:3].isascii() or not first[:3].isdigit() or first[3] not in ' -':
        raise ProtocolError('Invalid FTP reply')
    code = int(first[:3])
    lines = [first]
    if first[3] == '-':
        while len(lines) < 128:
            line = stream.readline()
            lines.append(line)
            if line.startswith(f'{code:03d} '):
                break
        else:
            raise ProtocolError('Unterminated multiline reply')
    return Reply(code, tuple(lines))


def socket_chunks(sock: socket.socket) -> Iterator[bytes]:
    while chunk := sock.recv(BLOCK_SIZE):
        yield chunk


def encode_ascii(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """POSIX LF -> NVT CRLF; literal CR -> CR NUL, including at boundaries."""
    for chunk in chunks:
        yield chunk.replace(b'\r', b'\r\x00').replace(b'\n', b'\r\n')


def decode_ascii(chunks: Iterable[bytes]) -> Iterator[bytes]:
    pending_cr = False
    for chunk in chunks:
        out = bytearray()
        for byte in chunk:
            if pending_cr:
                if byte == 10:
                    out.append(10)
                    pending_cr = False
                    continue
                out.append(13)
                pending_cr = False
                if byte == 0:
                    continue
            if byte == 13:
                pending_cr = True
            else:
                out.append(byte)
        if out:
            yield bytes(out)
    if pending_cr:
        yield b'\r'
