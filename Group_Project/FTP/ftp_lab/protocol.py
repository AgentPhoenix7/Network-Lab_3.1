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
        """Initialize the control stream with a socket and framing limits.

        What it does:
            Prepares a buffered stream for reading and sending bounded, CRLF-delimited
            FTP control lines over a raw TCP socket.

        How it does:
            Stores the underlying connected socket, configures maximum permissible line
            lengths for incoming (max_line) and outgoing (max_send_line) messages to prevent
            memory exhaustion attacks, and allocates an internal bytearray buffer for
            accumulating stream chunks.
        """
        self.sock = sock
        self.max_line = max_line
        self.max_send_line = max_send_line
        self.buffer = bytearray()

    def readline(self) -> str:
        """Read the next CRLF-terminated line from the stream.

        What it does:
            Extracts, validates, and decodes the next complete control line from the TCP
            stream, handling chunk fragmentation and pipelined commands.

        How it does:
            1. Searches for a newline (b'\\n') in the internal buffer.
            2. If found, ensures it is preceded by carriage return (b'\\r') and does not exceed
               max_line; raises ProtocolError on bare newlines, empty lines, or length violations.
            3. Strips the trailing CRLF and removes the consumed slice from self.buffer.
            4. Validates that no illegal control characters (< 32 except TAB, or DEL 127) exist.
            5. Decodes the wire bytes into a UTF-8 string.
            6. If no newline is buffered yet, verifies the buffer has not exceeded max_line,
               then fetches up to 4096 bytes via sock.recv(). If EOF occurs (chunk == b''),
               raises ProtocolError if incomplete bytes remain or EOFError if cleanly closed.
        """
        while True:
            end = self.buffer.find(b'\n')
            if end >= 0:
                if end + 1 > self.max_line or end == 0 or self.buffer[end - 1] != 13:
                    raise ProtocolError('Expected a bounded CRLF-terminated line')
                wire = bytes(self.buffer[: end - 1])
                del self.buffer[: end + 1]
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
        """Send a single framed control line over the socket.

        What it does:
            Encodes and transmits an FTP command or reply line terminated by CRLF.

        How it does:
            1. Scans line for forbidden control characters (\\r, \\n, \\x00) to prevent
               command-injection or reply-splitting attacks.
            2. Encodes the text to UTF-8 and appends CRLF (b'\\r\\n').
            3. Verifies that the wire byte length does not exceed max_send_line.
            4. Writes all bytes to the socket using sock.sendall().
        """
        if any(c in line for c in '\r\n\x00'):
            raise ProtocolError('Control-line injection')
        wire = line.encode('utf-8') + b'\r\n'
        if len(wire) > self.max_send_line:
            raise ProtocolError('Control line too long')
        self.sock.sendall(wire)


@dataclass(frozen=True)
class Reply:
    """Numeric FTP status and the complete control lines making up its reply."""

    code: int
    lines: tuple[str, ...]

    @property
    def message(self) -> str:
        """Extract the human-readable text from the final reply line.

        What it does:
            Returns the textual description following the 3-digit status code and delimiter.

        How it does:
            Slices the last line in lines from character index 4 onward (skipping the code
            and following space/hyphen).
        """
        return self.lines[-1][4:]


def read_reply(stream: ControlStream) -> Reply:
    """Parse a single-line or multiline FTP response from the control stream.

    What it does:
        Reads and parses an RFC 959 reply from the server, returning a structured Reply object.

    How it does:
        1. Reads the first line using stream.readline().
        2. Validates that the line starts with a 3-digit ASCII code followed by ' ' or '-'.
        3. Parses the integer status code.
        4. If the 4th character is '-', it indicates a multiline reply; loops reading lines
           (up to a safety threshold of 128 lines) until a line begins with '{code} '.
        5. Raises ProtocolError if the reply is malformed or multiline is unterminated.
        6. Constructs and returns a Reply dataclass.
    """
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
    """Yield data chunks from a connected TCP stream until EOF.

    What it does:
        Provides a generator interface for streaming raw bytes from a data socket.

    How it does:
        Repeatedly invokes sock.recv(BLOCK_SIZE) (64 KiB) in a loop until recv returns b''
        (indicating the peer closed their write end), yielding each non-empty byte chunk.
    """
    while chunk := sock.recv(BLOCK_SIZE):
        yield chunk


def encode_ascii(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Convert POSIX LF newlines to NVT ASCII CRLF format.

    What it does:
        Encodes a stream of binary chunks containing POSIX text into RFC 959 Network Virtual
        Terminal (NVT) ASCII format for transmission.

    How it does:
        Iterates over input byte chunks, first escaping standalone carriage returns
        (b'\\r' -> b'\\r\\x00') so they are not mistaken for line delimiters, and then
        converting POSIX newlines (b'\\n' -> b'\\r\\n').
    """
    for chunk in chunks:
        yield chunk.replace(b'\r', b'\r\x00').replace(b'\n', b'\r\n')


def decode_ascii(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Convert NVT ASCII CRLF stream to POSIX LF format across chunk boundaries.

    What it does:
        Decodes an incoming RFC 959 NVT ASCII stream into POSIX text format, converting
        CRLF to LF and literal CR NUL to CR.

    How it does:
        Maintains a state variable (pending_cr) across chunk boundaries. For every byte:
        1. If pending_cr is True:
           - If current byte is LF (10), emits POSIX LF (10) and clears pending_cr.
           - If current byte is NUL (0), emits literal CR (13) and clears pending_cr.
           - Otherwise, emits CR (13) followed by the current byte, clearing pending_cr.
        2. If current byte is CR (13), sets pending_cr = True without emitting.
        3. Otherwise, appends the byte directly to the output buffer.
        4. Flushes any remaining trailing CR as b'\\r' upon stream completion.
    """
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
