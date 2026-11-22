"""FTP terminal client: protocol and transfers use sockets, not ftplib."""

import argparse
import getpass
import math
import os
import re
import shlex
import socket
import struct
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .protocol import (
    BLOCK_SIZE,
    ControlStream,
    ProtocolError,
    decode_ascii,
    encode_ascii,
    read_reply,
    socket_chunks,
)


class FTPError(Exception):
    """Exception raised when the FTP server returns a failure status reply (>= 400)."""

    def __init__(self, reply):
        """Initialize the FTP error from a server Reply object.

        What it does:
            Stores the failure reply object and formats a standard error string.

        How it does:
            Stores self.reply and initializes Exception with '{code} {message}'.
        """
        self.reply = reply
        super().__init__(f'{reply.code} {reply.message}')


class FTPClient:
    """Socket-based FTP client implementing RFC 959 control and data connections."""

    def __init__(self, host='127.0.0.1', port=2121, *, timeout=30, passive=True, trace=None):
        """Initialize the FTP client and connect to the remote server.

        What it does:
            Establishes a TCP control connection to the target server and verifies the initial greeting.

        How it does:
            1. Validates that timeout is positive/finite and port is in 1-65535.
            2. Records timeout, passive mode flag, trace callback, and sets default binary mode (True).
            3. Creates a TCP streaming socket, sets the inactivity timeout, and connects to (host, port).
            4. Wraps the socket in a ControlStream for line-oriented CRLF I/O.
            5. Reads the server welcome reply (handling optional 120 preliminary delay) and expects code 220.
            6. Closes the socket and re-raises if any connection or greeting error occurs.
        """
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Timeout must be positive and finite')
        if not 1 <= port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        self.timeout, self.passive, self.trace = timeout, passive, trace
        self.binary = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.settimeout(timeout)
            self.sock.connect((host, port))
            self.stream = ControlStream(self.sock)
            welcome = self.read_response()
            if welcome.code == 120:
                welcome = self.read_response()
            self.expect(welcome, (220,))
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        """Context manager entry point returning the client instance."""
        return self

    def __exit__(self, *exc):
        """Context manager exit point closing the underlying connection."""
        self.close()

    def close(self):
        """Close the client control connection socket.

        What it does:
            Terminates the control connection and cleans up socket descriptors.

        How it does:
            Checks if self.sock is not None, invokes self.sock.close(), and resets self.sock to None.
        """
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def read_response(self):
        """Read and parse an FTP reply from the server.

        What it does:
            Reads a complete single-line or multiline reply, logs traces, and checks for error codes.

        How it does:
            1. Calls read_reply(self.stream) to parse the wire reply.
            2. If self.trace is set, logs each received line prefixed with 'S> '.
            3. If reply.code >= 400: closes the socket if code is 421 (service shutting down/timeout),
               and raises FTPError(reply).
            4. Returns the parsed Reply object.
        """
        reply = read_reply(self.stream)
        if self.trace:
            for line in reply.lines:
                self.trace('S> ' + line)
        if reply.code >= 400:
            if reply.code == 421:
                self.close()
            raise FTPError(reply)
        return reply

    @staticmethod
    def expect(reply, codes):
        """Assert that an FTP reply has an expected numeric status code.

        What it does:
            Verifies that the reply matches one of the acceptable status codes.

        How it does:
            Checks if reply.code is present in codes tuple. If not, raises ProtocolError;
            otherwise returns the reply.
        """
        if reply.code not in codes:
            raise ProtocolError(f'Unexpected FTP reply: {reply.code} {reply.message}')
        return reply

    def command(self, verb, arg=''):
        """Send a formatted command to the server and read its response.

        What it does:
            Constructs and transmits an FTP command line, and awaits the server's reply.

        How it does:
            1. Capitalizes verb and ensures ASCII alphabetic format.
            2. Formats line as 'VERB arg', checks <= 512 byte limit, and verifies no injection chars.
            3. Ensures the socket connection is open.
            4. If self.trace is enabled, logs 'C> line' (masking PASS argument as '<redacted>').
            5. Transmits the line via self.stream.send_line(line) and returns self.read_response().
            6. On network or framing errors, closes the connection before re-raising.
        """
        verb = verb.upper()
        if not verb.isascii() or not verb.isalpha():
            raise ProtocolError('Invalid command name')
        line = verb + (' ' + arg if arg else '')
        if len(line.encode('utf-8')) + 2 > 512 or any(c in line for c in '\r\n\x00'):
            raise ProtocolError('Invalid or oversized command')
        if self.sock is None:
            raise EOFError('Client connection is closed')
        if self.trace:
            self.trace('C> PASS <redacted>' if verb == 'PASS' else 'C> ' + line)
        try:
            self.stream.send_line(line)
            return self.read_response()
        except (OSError, EOFError, ProtocolError):
            self.close()
            raise

    def login(self, username, password):
        """Authenticate with the server using USER and PASS.

        What it does:
            Performs the standard FTP login sequence.

        How it does:
            Sends 'USER username'. If the server responds with 331 (password required),
            sends 'PASS password'. Verifies that the final reply is 230 (login successful).
        """
        reply = self.command('USER', username)
        if reply.code == 331:
            reply = self.command('PASS', password)
        return self.expect(reply, (230,))

    def pwd(self):
        """Query the remote current working directory path.

        What it does:
            Executes the PWD command and returns the unquoted remote directory path.

        How it does:
            Sends 'PWD', expects status 257, extracts the quoted directory using regex
            r'^"((?:""|[^"])*)"', unescapes doubled quotes ('""' -> '"'), and returns the path string.
        """
        reply = self.expect(self.command('PWD'), (257,))
        match = re.match(r'^"((?:""|[^"])*)"', reply.message)
        if match is None:
            raise ProtocolError('Invalid PWD reply')
        return match[1].replace('""', '"')

    def size(self, remote):
        """Retrieve the size in bytes of a remote file.

        What it does:
            Queries the byte size of a file using binary mode.

        How it does:
            Sends 'TYPE I' to ensure binary representation, sends 'SIZE remote', expects reply 213,
            validates that the reply message contains digits, and returns it as an integer.
        """
        self.expect(self.command('TYPE', 'I'), (200,))
        reply = self.expect(self.command('SIZE', remote), (213,))
        if not reply.message.isascii() or not reply.message.isdigit():
            raise ProtocolError('Invalid SIZE reply')
        return int(reply.message)

    def passive_port(self):
        """Negotiate and extract an ephemeral passive data port from the server.

        What it does:
            Requests a passive data port using EPSV, falling back to PASV if unsupported.

        How it does:
            1. Sends 'EPSV' expecting reply 229. If successful, parses delimiter and port from (|||port|).
            2. If server rejects EPSV with 500, 501, 502, 504, or 522, sends 'PASV' expecting reply 227.
            3. For PASV, parses the 6-tuple (h1,h2,h3,h4,p1,p2) and computes port = p1 * 256 + p2.
            4. Validates that the extracted port is between 1 and 65535 and returns it.
        """
        try:
            reply = self.expect(self.command('EPSV'), (229,))
        except FTPError as exc:
            if exc.reply.code not in (500, 501, 502, 504, 522):
                raise
            reply = self.expect(self.command('PASV'), (227,))
            match = re.search(r'\((\d+,\d+,\d+,\d+,\d+,\d+)\)', reply.message)
            if not match:
                raise ProtocolError('Invalid PASV reply')
            fields = [int(part) for part in match[1].split(',')]
            if any(not 0 <= part <= 255 for part in fields):
                raise ProtocolError('Invalid PASV endpoint')
            port = fields[4] * 256 + fields[5]
        else:
            match = re.search(r'\(([^()]*)\)', reply.message)
            fields = match[1].split(match[1][0]) if match and match[1] else []
            if len(fields) != 5 or fields[:3] != ['', '', ''] or fields[4] or not fields[3].isascii() or not fields[3].isdigit():
                raise ProtocolError('Invalid EPSV reply')
            port = int(fields[3])
        if not 1 <= port <= 65535:
            raise ProtocolError('Invalid data port')
        return port

    @contextmanager
    def data_setup(self):
        """Prepare and yield a configured data socket for passive or active mode.

        What it does:
            Context manager establishing the data transport endpoint before issuing a transfer command.

        How it does:
            1. Verifies the control socket is connected.
            2. Creates a TCP streaming socket with timeout inside an ExitStack.
            3. In passive mode: obtains server data port via self.passive_port(), and connects
               directly to the control peer's IP (ignoring any untrusted IP in PASV reply for security).
            4. In active mode: binds to the control socket's local IP on ephemeral port 0, starts listening,
               and informs the server via EPRT (falling back to PORT on error).
            5. Yields the socket to the caller.
        """
        control = self.sock
        if control is None:
            raise EOFError('Client connection is closed')
        with ExitStack() as stack:
            endpoint = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            endpoint.settimeout(self.timeout)
            if self.passive:
                port = self.passive_port()
                # Pin data to the authenticated control peer, never the PASV IP.
                endpoint.connect((control.getpeername()[0], port))
            else:
                local_ip = control.getsockname()[0]
                endpoint.bind((local_ip, 0))
                endpoint.listen(4)
                port = endpoint.getsockname()[1]
                try:
                    self.expect(self.command('EPRT', f'|1|{local_ip}|{port}|'), (200,))
                except FTPError as exc:
                    if exc.reply.code not in (500, 501, 502, 504):
                        raise
                    address = local_ip.replace('.', ',')
                    self.expect(self.command('PORT', f'{address},{port // 256},{port % 256}'), (200,))
            yield endpoint

    def transfer(self, verb, remote, consume):
        """Coordinate an entire file or listing data transfer with the server.

        What it does:
            Executes a data transfer operation, streams data through the consumer, and awaits completion.

        How it does:
            1. Prepares the data socket via self.data_setup().
            2. Sends the transfer command ('VERB remote') expecting preliminary reply 125 or 150.
            3. In passive mode: passes the connected socket to self.consume_data(endpoint, consume).
            4. In active mode: accepts the server's incoming connection within timeout deadline,
               verifies the connecting peer matches the control peer IP, and invokes consume_data.
            5. Awaits and validates the final completion reply (226 or 250).
            6. Closes the client connection if aborted mid-transfer to prevent control line desync.
        """
        control = self.sock
        if control is None:
            raise EOFError('Client connection is closed')
        expected_peer = control.getpeername()[0]
        started = False
        try:
            with self.data_setup() as endpoint:
                self.expect(self.command(verb, remote), (125, 150))
                started = True
                if self.passive:
                    self.consume_data(endpoint, consume)
                else:
                    deadline = time.monotonic() + self.timeout
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('No matching server data connection')
                        endpoint.settimeout(remaining)
                        data, peer = endpoint.accept()
                        with data:
                            if peer[0] != expected_peer:
                                continue
                            data.settimeout(self.timeout)
                            self.consume_data(data, consume)
                            break
            return self.expect(self.read_response(), (226, 250))
        except BaseException:
            # After a preliminary reply, unread completion replies make reuse unsafe.
            if started:
                self.close()
            raise

    @staticmethod
    def consume_data(data, consume):
        """Execute the transfer consumer, resetting the socket on errors.

        What it does:
            Runs the data processing callback and ensures an aborted transfer forces a TCP RST.

        How it does:
            Calls consume(data). If an exception is raised, sets SO_LINGER to (1, 0) on the socket
            before re-raising so that closing sends a TCP RST rather than a clean FIN, preventing
            the peer from interpreting an incomplete transfer as a valid short file.
        """
        try:
            consume(data)
        except BaseException:
            # An interrupted upload must not look like a valid shorter file.
            # Reset the data stream before its context closes it with clean EOF.
            try:
                data.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            except OSError:
                pass
            raise

    def list(self, remote='', *, names_only=False):
        """Fetch remote directory entries or file metadata.

        What it does:
            Executes LIST or NLST to retrieve directory listings as a list of strings.

        How it does:
            1. Sends 'TYPE A' expecting 200.
            2. Defines a receive callback accumulating incoming chunks into a bytearray.
            3. Invokes self.transfer('NLST' if names_only else 'LIST', remote, receive).
            4. Decodes accumulated bytes as UTF-8 and returns split lines.
        """
        self.expect(self.command('TYPE', 'A'), (200,))
        content = bytearray()

        def receive(data):
            for chunk in socket_chunks(data):
                content.extend(chunk)

        self.transfer('NLST' if names_only else 'LIST', remote, receive)
        return content.decode('utf-8').splitlines()

    def upload(self, local, remote, *, progress: Callable[[int], None] | None = None):
        """Upload a local file to the remote server.

        What it does:
            Sends a local file's content to the server using the configured transfer type.

        How it does:
            1. Opens local file in binary read mode.
            2. Sets transfer type ('TYPE I' for binary, 'TYPE A' for ASCII).
            3. Defines a send callback reading BLOCK_SIZE chunks, converting newlines if ASCII,
               transmitting via data.sendall(), and reporting chunk byte length to progress callback.
            4. Executes self.transfer('STOR', remote, send).
        """
        with Path(local).open('rb') as source:
            self.expect(self.command('TYPE', 'I' if self.binary else 'A'), (200,))

            def send(data):
                chunks = iter(lambda: source.read(BLOCK_SIZE), b'')
                for chunk in chunks if self.binary else encode_ascii(chunks):
                    data.sendall(chunk)
                    if progress is not None:
                        progress(len(chunk))

            return self.transfer('STOR', remote, send)

    def download(self, remote, local, *, progress: Callable[[int], None] | None = None):
        """Download a remote file to a local path using atomic staging.

        What it does:
            Retrieves a file from the server and writes it to disk, replacing the destination
            only upon complete, verified transfer.

        How it does:
            1. Creates a temporary staging file (.ftp-download-*) in the destination directory.
            2. Sets transfer representation ('TYPE I' or 'TYPE A').
            3. Streams chunks from data socket, decoding ASCII if needed and notifying progress.
            4. Executes self.transfer('RETR', remote, receive).
            5. Upon receiving successful completion reply, atomically renames staging file to
               target using os.replace().
            6. In finally block, unlinks staging file if an error occurred before atomic replacement.
        """
        target = Path(local)
        staging = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.ftp-download-', dir=target.parent, delete=False) as out:
                staging = Path(out.name)
                self.expect(self.command('TYPE', 'I' if self.binary else 'A'), (200,))

                def receive(data):
                    def incoming():
                        for chunk in socket_chunks(data):
                            if progress is not None:
                                progress(len(chunk))
                            yield chunk

                    chunks = incoming()
                    for chunk in chunks if self.binary else decode_ascii(chunks):
                        out.write(chunk)

                reply = self.transfer('RETR', remote, receive)
            os.replace(staging, target)
            return reply
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)

    def rename(self, remote_from: str, remote_to: str):
        """Rename a remote file or directory using RNFR and RNTO.

        What it does:
            Sends RNFR followed by RNTO to rename a file or directory on the server.

        How it does:
            1. Transmits 'RNFR remote_from', expecting intermediate status 350.
            2. Transmits 'RNTO remote_to', expecting completion status 250.
            3. Returns the final 250 Reply object.
        """
        self.expect(self.command('RNFR', remote_from), (350,))
        return self.expect(self.command('RNTO', remote_to), (250,))

    def append(self, local, remote='', *, progress: Callable[[int], None] | None = None):
        """Append data from a local file to a remote file using APPE."""
        local_path = Path(local)
        remote_name = remote or local_path.name
        with local_path.open('rb') as file:
            def send(data):
                chunks = iter(lambda: file.read(BLOCK_SIZE), b'')
                for chunk in chunks if self.binary else encode_ascii(chunks):
                    data.sendall(chunk)
                    if progress:
                        progress(len(chunk))
            return self.transfer('APPE', remote_name, send)

    def stat(self, remote=''):
        """Query server or file status over control connection using STAT."""
        return self.command('STAT', remote)

    def rein(self):
        """Reinitialize the FTP session using REIN."""
        return self.expect(self.command('REIN'), (220,))

    def quit(self):
        """Disconnect gracefully from the FTP server.

        What it does:
            Sends the QUIT command, validates response 221, and closes the connection.

        How it does:
            Sends 'QUIT', expects 221, and invokes self.close() in a finally block.
        """
        try:
            return self.expect(self.command('QUIT'), (221,))
        finally:
            self.close()


HELP = '''Commands (quote paths containing spaces):
  pwd | ls [remote] | nlst [remote] | cd remote | cdup
  mkdir remote | rmdir remote | delete remote | rename old new | size remote
  get remote [local] | put local [remote] | append local [remote]
  stat [remote] | rein | ascii | binary | passive | active | noop | help | quit'''


def execute(client: FTPClient, line: str) -> bool:
    """Parse and execute a single user command string in the client shell.

    What it does:
        Tokenizes user input with shell quoting, maps commands to FTPClient methods,
        and outputs results. Returns False if the session should exit.

    How it does:
        1. Splits command line using shlex.split() to preserve quoted strings and spaces.
        2. Dispatches based on command keyword:
           - quit / bye: calls client.quit() and returns False.
           - help: displays HELP text.
           - pwd, cd, cdup, mkdir, rmdir, delete, rename, size: invokes corresponding FTP commands.
           - ls, nlst: fetches and prints directory entries.
           - get, put: downloads/uploads files (deriving basename if omitted).
           - ascii, binary: toggles client.binary mode.
           - passive, active: toggles client.passive mode.
           - noop: verifies connection liveness.
        3. Raises ValueError on unrecognized syntax.
        4. Returns True to continue the interactive session.
    """
    words = shlex.split(line)
    if not words:
        return True
    command, *args = words
    command = command.lower()
    if command in ('quit', 'bye') and not args:
        client.quit()
        return False
    if command == 'help' and not args:
        print(HELP)
    elif command == 'pwd' and not args:
        print(client.pwd())
    elif command in ('ls', 'nlst') and len(args) <= 1:
        for entry in client.list(args[0] if args else '', names_only=command == 'nlst'):
            print(entry)
    elif command in ('cd', 'mkdir', 'rmdir', 'delete') and len(args) == 1:
        verb = {'cd': 'CWD', 'mkdir': 'MKD', 'rmdir': 'RMD', 'delete': 'DELE'}[command]
        print(client.command(verb, args[0]).message)
    elif command in ('rename', 'rn') and len(args) == 2:
        print(client.rename(args[0], args[1]).message)
    elif command in ('cdup', 'noop') and not args:
        print(client.command('CDUP' if command == 'cdup' else 'NOOP').message)
    elif command == 'size' and len(args) == 1:
        print(client.size(args[0]))
    elif command == 'get' and 1 <= len(args) <= 2:
        target = args[1] if len(args) == 2 else Path(args[0]).name
        if target in ('', '.', '..'):
            raise ValueError('Choose a local output filename')
        client.download(args[0], target)
        print(f'Downloaded {args[0]} to {target}')
    elif command == 'put' and 1 <= len(args) <= 2:
        remote = args[1] if len(args) == 2 else Path(args[0]).name
        client.upload(args[0], remote)
        print(f'Uploaded {args[0]} to {remote}')
    elif command == 'append' and 1 <= len(args) <= 2:
        remote = args[1] if len(args) == 2 else Path(args[0]).name
        client.append(args[0], remote)
        print(f'Appended {args[0]} to {remote}')
    elif command == 'stat' and len(args) <= 1:
        reply = client.stat(args[0] if args else '')
        for line in reply.lines:
            print(line)
    elif command == 'rein' and not args:
        print(client.rein().message)
    elif command in ('ascii', 'binary') and not args:
        client.binary = command == 'binary'
        print(f'Transfer type: {command}')
    elif command in ('passive', 'active') and not args:
        client.passive = command == 'passive'
        print(f'Data mode: {command}')
    else:
        raise ValueError('Unknown command or incorrect arguments; use help')
    return True


def main(argv=None):
    """Run the FTP terminal client interface.

    What it does:
        Parses CLI options, retrieves credentials, establishes session, and runs the
        interactive REPL or non-interactive script.

    How it does:
        1. Parses options: --host, --port, --user, --timeout, --active, --trace, --script.
        2. Retrieves password from FTP_PASSWORD or prompts via getpass.getpass() if interactive.
        3. Opens client connection within an ExitStack, performs login, and initializes prompt.
        4. In interactive mode: prints help and presents 'ftp> ' prompt, reading lines from stdin.
        5. In script mode: executes commands line-by-line, exiting with status 1 on error.
        6. Traps KeyboardInterrupt to exit with status 130.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=2121)
    parser.add_argument('--user', default='student')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--active', action='store_true', help='Use active data connections')
    parser.add_argument('--trace', action='store_true', help='Print control commands/replies (password redacted)')
    parser.add_argument('--script', type=Path, help='Read batch commands from a file, or - for stdin')
    args = parser.parse_args(argv)
    password = os.environ.get('FTP_PASSWORD')
    if password is None:
        if not sys.stdin.isatty():
            parser.error('Set FTP_PASSWORD for noninteractive use')
        password = getpass.getpass('FTP password: ')
    interactive = args.script is None and sys.stdin.isatty()
    try:
        with ExitStack() as stack:
            source = sys.stdin
            if args.script is not None and str(args.script) != '-':
                source = stack.enter_context(args.script.open(encoding='utf-8'))
            client = stack.enter_context(FTPClient(args.host, args.port, timeout=args.timeout,
                                                 passive=not args.active, trace=print if args.trace else None))
            client.login(args.user, password)
            if interactive:
                print(HELP)
            while True:
                if interactive:
                    print('ftp> ', end='', flush=True)
                line = source.readline()
                if not line:
                    client.quit()
                    break
                try:
                    if not execute(client, line):
                        break
                except (FTPError, OSError, ValueError) as exc:
                    print(f'Error: {exc}', file=sys.stderr)
                    if not interactive or client.sock is None:
                        return 1
    except (FTPError, OSError, EOFError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
