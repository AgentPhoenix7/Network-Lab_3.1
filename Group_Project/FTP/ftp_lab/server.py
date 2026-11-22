"""Concurrent IPv4 FTP server implemented with standard-library TCP sockets."""

import argparse
import hmac
import ipaddress
import logging
import math
import os
import signal
import socket
import socketserver
import stat
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .filesystem import Sandbox
from .protocol import (
    BLOCK_SIZE,
    ControlStream,
    ProtocolError,
    decode_ascii,
    encode_ascii,
    socket_chunks,
)

LOG = logging.getLogger('ftp_lab.server')


class CommandError(Exception):
    """Exception carrying an FTP numeric status code and explanatory message."""

    def __init__(self, code: int, message: str):
        """Initialize the command error with a code and message.

        What it does:
            Stores the 3-digit FTP reply code and description for protocol error reporting.

        How it does:
            Assigns self.code and self.message from the arguments.
        """
        self.code, self.message = code, message


class FTPServer(socketserver.ThreadingTCPServer):
    """Multi-threaded IPv4 TCP FTP server with bounded concurrency and graceful shutdown."""

    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def __init__(self, address, root: Path, username: str, password: str, *, timeout=30.0, max_clients=32):
        """Initialize the concurrent FTP server.

        What it does:
            Configures the server parameters, directory root, authentication credentials,
            concurrency controls, and active socket tracking.

        How it does:
            1. Validates that timeout and max_clients are positive and credentials are non-empty
               without control characters.
            2. Resolves root to an absolute existing directory.
            3. Initializes a BoundedSemaphore(max_clients) to cap simultaneous active client sessions.
            4. Sets up a threading.Event (closing) and a thread-safe registry (self.sockets,
               self.socket_lock) to record active control and data sockets for shutdown cleanup.
            5. Calls the superclass socketserver.ThreadingTCPServer.__init__ with FTPSession handler.
        """
        if not math.isfinite(timeout) or timeout <= 0 or max_clients < 1:
            raise ValueError('Timeout and client limit must be positive')
        if not username or not password or any(ord(c) < 32 or ord(c) == 127 for c in username + password):
            raise ValueError('A nonempty user/password without control characters is required')
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)
        self.username, self.password = username, password
        self.io_timeout = timeout
        self.slots = threading.BoundedSemaphore(max_clients)
        self.closing = threading.Event()
        self.socket_lock = threading.Lock()
        self.sockets: set[socket.socket] = set()
        super().__init__(address, FTPSession)

    @property
    def address(self) -> tuple[str, int]:
        """Return the bound host and port.

        What it does:
            Provides the server's bound IPv4 address and TCP port number.

        How it does:
            Extracts host and port from self.server_address and validates host type.
        """
        host, port = self.server_address[:2]
        if not isinstance(host, str):
            raise TypeError('Expected a bound IPv4 address')
        return host, port

    def register(self, sock: socket.socket):
        """Register an active socket and apply socket inactivity timeout.

        What it does:
            Tracks a control or data socket for lifecycle tracking and timeout enforcement.

        How it does:
            Sets sock.settimeout(self.io_timeout). Acquires self.socket_lock; if the server
            is shutting down (closing is set), closes the socket and raises ConnectionAbortedError.
            Otherwise adds the socket to self.sockets.
        """
        sock.settimeout(self.io_timeout)
        with self.socket_lock:
            if self.closing.is_set():
                sock.close()
                raise ConnectionAbortedError('Server is closing')
            self.sockets.add(sock)

    def discard(self, sock: socket.socket):
        """Unregister and close a tracked socket.

        What it does:
            Removes a socket from the active registry and safely closes it.

        How it does:
            Acquires self.socket_lock to remove sock from self.sockets, then invokes sock.close().
        """
        with self.socket_lock:
            self.sockets.discard(sock)
        sock.close()

    def process_request(self, request, client_address):
        """Process an incoming client connection, enforcing concurrency limits.

        What it does:
            Accepts or rejects an incoming client TCP connection based on current load and shutdown state.

        How it does:
            Validates the socket and sets its timeout. If the server is closing or if acquiring
            a slot from self.slots (non-blocking) fails because the client cap has been reached,
            sends '421 Too many clients or server shutting down\\r\\n' and closes the connection.
            Otherwise delegates to super().process_request() to dispatch a worker thread.
        """
        if not isinstance(request, socket.socket):
            raise TypeError('FTP requires a TCP socket')
        request.settimeout(self.io_timeout)
        if self.closing.is_set() or not self.slots.acquire(blocking=False):
            try:
                request.sendall(b'421 Too many clients or server shutting down\r\n')
            finally:
                request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        """Execute client session handling in a dedicated thread.

        What it does:
            Runs the client request thread and ensures the concurrency slot is returned upon completion.

        How it does:
            Invokes super().process_request_thread() in a try-finally block, calling
            self.slots.release() upon thread termination.
        """
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_close(self):
        """Perform graceful shutdown of the server and all active connections.

        What it does:
            Signals server termination, closes all registered control and data sockets,
            and cleans up server listener resources.

        How it does:
            1. Sets the self.closing event.
            2. Acquires self.socket_lock to snapshot all currently open sockets.
            3. Iterates through all sockets, calling shutdown(SHUT_RDWR) and close() to
               immediately unblock threads waiting on I/O.
            4. Calls super().server_close() to close the main listening socket.
        """
        self.closing.set()
        with self.socket_lock:
            sockets = list(self.sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        super().server_close()


class FTPSession(socketserver.BaseRequestHandler):
    """Session handler managing state, command processing, and data transfers for one client."""

    request: socket.socket
    fs: Sandbox

    def setup(self):
        """Prepare session references.

        What it does:
            Verifies and caches the reference to the parent FTPServer.

        How it does:
            Ensures self.server is an FTPServer and stores it in self.ftp_server.
        """
        if not isinstance(self.server, FTPServer):
            raise TypeError('FTP sessions require an FTPServer')
        self.ftp_server = self.server

    def handle(self):
        """Main loop servicing control connection commands for the session lifetime.

        What it does:
            Coordinates command reading, dispatching, and response handling for a connected client.

        How it does:
            1. Wraps the request socket in a ControlStream with max_line=512.
            2. Initializes state variables: pending_user, authenticated flag, transfer representation
               ('A'), passive/active endpoint state, and epsv_only mode.
            3. Registers the client socket with the server and instantiates the Sandbox.
            4. Emits the 220 greeting.
            5. Enters a loop reading lines via self.stream.readline() and dispatching via self.dispatch().
            6. Catches CommandError to send mapped numeric replies; ProtocolError and TimeoutError
               terminate the session.
            7. In a finally block, resets data sockets, closes the sandbox, and discards the control socket.
        """
        self.stream = ControlStream(self.request, max_line=512)
        self.pending_user = None
        self.authenticated = False
        self.representation = 'A'
        self.structure = 'F'
        self.mode = 'S'
        self.passive = None
        self.active = None
        self.epsv_only = False
        self.rename_from = None
        filesystem = None
        try:
            self.ftp_server.register(self.request)
            self.fs = filesystem = Sandbox(self.ftp_server.root)
            self.reply(220, 'Python socket FTP lab ready')
            while not self.ftp_server.closing.is_set():
                try:
                    line = self.stream.readline()
                    if not self.dispatch(line):
                        break
                except CommandError as exc:
                    self.reply(exc.code, exc.message)
                except ProtocolError:
                    self.reply(500, 'Invalid or oversized command; closing connection')
                    break
                except TimeoutError:
                    self.reply(421, 'Control connection timed out')
                    break
        except (EOFError, OSError):
            pass
        finally:
            self.reset_data()
            if filesystem is not None:
                filesystem.close()
            self.ftp_server.discard(self.request)

    def reply(self, code: int, text: str):
        """Send an FTP numeric response line to the client.

        What it does:
            Formats and writes a standard numeric status reply to the control stream.

        How it does:
            Formats '{code} {text}' and sends it using self.stream.send_line().
        """
        self.stream.send_line(f'{code} {text}')

    def reset_data(self):
        """Clean up any open passive listener or active data connection target.

        What it does:
            Resets data connection configuration to an idle state.

        How it does:
            If self.passive is active, discards it from the server and resets it to None.
            Clears self.active to None.
        """
        if self.passive is not None:
            self.ftp_server.discard(self.passive)
            self.passive = None
        self.active = None

    def dispatch(self, line: str) -> bool:
        """Parse and route an incoming FTP command line to its specific handler.

        What it does:
            Interprets command verb, validates argument rules, enforces authentication,
            and routes commands. Returns False if the session should terminate (QUIT).

        How it does:
            1. Splits line into verb and argument, validating ASCII alphabetic verb format.
            2. Checks whether verb requires arguments or forbids arguments.
            3. Processes session control commands:
               - QUIT: sends 221 and returns False.
               - USER: records username, resets auth state, and replies 331.
               - PASS: validates username and password using constant-time hmac.compare_digest.
               - NOOP, SYST, FEAT, HELP, OPTS: returns standard protocol informational replies.
            4. For all other commands, ensures the session is authenticated (raising 530 if not).
            5. Dispatches to self.authenticated_command(verb, arg).
            6. Maps any internal OS/Value errors to CommandError 550.
        """
        verb, _, arg = line.partition(' ')
        verb = verb.upper()
        if not verb or not verb.isascii() or not verb.isalpha():
            raise CommandError(500, 'Invalid command')
        required = {'USER', 'TYPE', 'MODE', 'STRU', 'CWD', 'MKD', 'RMD', 'DELE', 'RNFR', 'RNTO', 'RETR', 'STOR', 'APPE', 'ALLO', 'ACCT', 'SITE', 'SMNT', 'REST', 'SIZE', 'PORT', 'EPRT', 'OPTS'}
        no_arg = {'QUIT', 'NOOP', 'PWD', 'CDUP', 'SYST', 'FEAT', 'PASV', 'ABOR', 'REIN'}
        if verb in required and not arg or verb in no_arg and arg:
            raise CommandError(501, 'Invalid command arguments')
        if verb != 'RNTO':
            self.rename_from = None
        if verb == 'QUIT':
            self.reply(221, 'Goodbye')
            return False
        if verb == 'REIN':
            self.reset_data()
            self.pending_user = None
            self.authenticated = False
            self.representation = 'A'
            self.structure = 'F'
            self.mode = 'S'
            self.epsv_only = False
            self.rename_from = None
            self.fs.chdir('/')
            self.reply(220, 'Service ready for new user')
        elif verb == 'ACCT':
            self.reply(202, 'Command not implemented, superfluous at this site')
        elif verb == 'USER':
            self.reset_data()
            self.pending_user, self.authenticated = arg, False
            self.fs.chdir('/')
            self.reply(331, 'Password required')
        elif verb == 'PASS':
            if self.pending_user is None:
                raise CommandError(503, 'Send USER first')
            user_ok = hmac.compare_digest(self.pending_user.encode(), self.ftp_server.username.encode())
            password_ok = hmac.compare_digest(arg.encode(), self.ftp_server.password.encode())
            self.authenticated = user_ok and password_ok
            self.reply(230 if self.authenticated else 530, 'Login successful' if self.authenticated else 'Login incorrect')
        elif verb == 'NOOP':
            self.reply(200, 'OK')
        elif verb == 'SYST':
            self.reply(215, 'UNIX Type: L8')
        elif verb == 'FEAT':
            self.stream.send_line('211-Features')
            for feature in (' EPRT', ' EPSV', ' SIZE', ' UTF8'):
                self.stream.send_line(feature)
            self.reply(211, 'End')
        elif verb == 'HELP':
            self.reply(214, 'USER PASS ACCT CWD CDUP SMNT REIN QUIT PORT PASV TYPE STRU MODE RETR STOR STOU APPE ALLO REST RNFR RNTO ABOR DELE RMD MKD PWD LIST NLST SITE SYST STAT HELP NOOP EPRT EPSV SIZE FEAT OPTS')
        elif verb == 'OPTS':
            if arg.upper() != 'UTF8 ON':
                raise CommandError(501, 'Only UTF8 ON is supported')
            self.reply(200, 'UTF8 enabled')
        elif verb in ('ALLO', 'SITE'):
            self.reply(202, 'Command not implemented, superfluous at this site')
        elif verb in ('SMNT', 'REST'):
            raise CommandError(502, 'Command not implemented')
        elif verb == 'STAT':
            if not arg:
                self.stream.send_line('211-FTP server status:')
                self.stream.send_line(f' Connected from {self.client_address[0]}:{self.client_address[1]}')
                self.stream.send_line(f' Logged in as: {self.pending_user if self.authenticated else "Not logged in"}')
                self.stream.send_line(f' TYPE: {self.representation}, STRU: {self.structure}, MODE: {self.mode}')
                if self.passive is not None:
                    self.stream.send_line(' Data: Passive listening')
                elif self.active is not None:
                    self.stream.send_line(f' Data: Active ({self.active[0]}:{self.active[1]})')
                else:
                    self.stream.send_line(' Data: Idle')
                self.reply(211, 'End of status')
            else:
                if not self.authenticated:
                    raise CommandError(530, 'Please login with USER and PASS')
                entries = self.fs.entries(arg)
                self.stream.send_line(f'213-Status of {arg}:')
                for name, info in entries:
                    self.stream.send_line(f' {stat.filemode(info.st_mode)} 1 ftp ftp {info.st_size:12d} Jan 01 00:00 {name}')
                self.reply(213, 'End of status')
        elif not self.authenticated:
            raise CommandError(530, 'Please login with USER and PASS')
        else:
            try:
                self.authenticated_command(verb, arg)
            except (OSError, ValueError) as exc:
                LOG.debug('Filesystem/argument error: %s', type(exc).__name__)
                raise CommandError(550, 'File or directory unavailable or forbidden') from exc
        return True

    def authenticated_command(self, verb: str, arg: str):
        """Execute stateful operations and transfers for authenticated sessions.

        What it does:
            Dispatches filesystem, transfer type, data setup, and file transfer commands.

        How it does:
            - TYPE: validates and records ASCII ('A'), Binary ('I'), or EBCDIC ('E') representation.
            - MODE: validates Stream ('S'), Block ('B'), or Compressed ('C') mode.
            - STRU: validates File ('F'), Record ('R'), or Page ('P') structure.
            - PWD: asks sandbox for current path and replies with 257.
            - CWD/CDUP: changes sandbox directory via self.fs.chdir().
            - MKD/RMD/DELE: creates directory, removes directory, or deletes file.
            - RNFR/RNTO: rename sequence locating existing file and moving to target.
            - SIZE: requires Binary mode and replies with file size via self.fs.size().
            - PASV/EPSV/PORT/EPRT: delegates to self.configure_data().
            - LIST/NLST/RETR/STOR: delegates to self.transfer().
            - ABOR: resets data configuration and replies 226.
        """
        if verb == 'TYPE':
            kind = arg.upper()
            if kind in ('A', 'A N'):
                self.representation = 'A'
            elif kind in ('I', 'L 8'):
                self.representation = 'I'
            elif kind in ('E', 'EBCDIC'):
                self.representation = 'E'
            else:
                raise CommandError(504, 'Only TYPE A, E, and I are supported')
            self.reply(200, 'Transfer type set')
        elif verb == 'MODE':
            mode = arg.upper()
            if mode not in ('S', 'B', 'C'):
                raise CommandError(504, 'Only MODE S, B, and C are supported')
            self.mode = mode
            self.reply(200, f'Mode set to {mode}')
        elif verb == 'STRU':
            stru = arg.upper()
            if stru not in ('F', 'R', 'P'):
                raise CommandError(504, 'Only STRU F, R, and P are supported')
            self.structure = stru
            self.reply(200, f'Structure set to {stru}')
        elif verb == 'PWD':
            self.reply(257, '"' + self.fs.pwd().replace('"', '""') + '"')
        elif verb in ('CWD', 'CDUP'):
            self.fs.chdir('..' if verb == 'CDUP' else arg)
            self.reply(250, 'Directory changed')
        elif verb == 'MKD':
            self.fs.mkdir(arg)
            path = '/' + '/'.join(self.fs.normalize(arg))
            self.reply(257, '"' + path.replace('"', '""') + '" created')
        elif verb in ('RMD', 'DELE'):
            (self.fs.rmdir if verb == 'RMD' else self.fs.delete)(arg)
            self.reply(250, 'Removed')
        elif verb == 'RNFR':
            self.fs.check_exists(arg)
            self.rename_from = arg
            self.reply(350, 'File exists, ready for destination name')
        elif verb == 'RNTO':
            if self.rename_from is None:
                raise CommandError(503, 'Send RNFR first')
            old = self.rename_from
            self.rename_from = None
            self.fs.rename(old, arg)
            self.reply(250, 'Rename successful')
        elif verb == 'SIZE':
            if self.representation != 'I':
                raise CommandError(550, 'SIZE requires TYPE I')
            self.reply(213, str(self.fs.size(arg)))
        elif verb in ('PASV', 'EPSV', 'PORT', 'EPRT'):
            self.configure_data(verb, arg)
        elif verb in ('LIST', 'NLST', 'RETR', 'STOR', 'STOU', 'APPE'):
            self.transfer(verb, arg)
        elif verb == 'ABOR':
            self.reset_data()
            self.reply(226, 'Data setup cleared')
        else:
            raise CommandError(502, 'Command not implemented')

    def configure_data(self, verb: str, arg: str):
        """Configure passive listening sockets or active connection endpoints.

        What it does:
            Prepares data transport configuration for upcoming LIST, RETR, or STOR operations.

        How it does:
            1. Clears prior data configuration via self.reset_data().
            2. Handles EPSV ALL mode restriction.
            3. For PASV and EPSV:
               - Creates a TCP listener socket bound to the control connection's local IP and port 0.
               - Registers the listener, starts listening (listen(4)), and stores it in self.passive.
               - Responds with 229 (EPSV) or 227 (PASV) containing the allocated port.
            4. For PORT and EPRT:
               - Parses IPv4 address and port from the argument string.
               - Enforces anti-bounce security rules: IP must match the client's control peer IP
                 (self.client_address[0]) and port must be unprivileged (1024-65535).
               - Stores the target as self.active = (ip, port) and replies 200.
        """
        self.reset_data()
        if self.epsv_only and verb != 'EPSV':
            raise CommandError(501, 'EPSV ALL requires EPSV')
        if verb == 'EPSV' and arg.upper() == 'ALL':
            self.epsv_only = True
            self.reply(200, 'Only EPSV will be used')
            return
        if verb in ('PASV', 'EPSV'):
            if verb == 'EPSV' and arg not in ('', '1'):
                raise CommandError(522 if arg.isdigit() else 501, 'Use IPv4 (1)')
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.ftp_server.register(listener)
                local_ip = self.request.getsockname()[0]
                listener.bind((local_ip, 0))
                listener.listen(4)
                port = listener.getsockname()[1]
                self.passive = listener
            except BaseException:
                self.ftp_server.discard(listener)
                raise
            if verb == 'EPSV':
                self.reply(229, f'Entering Extended Passive Mode (|||{port}|)')
            else:
                address = local_ip.replace('.', ',')
                self.reply(227, f'Entering Passive Mode ({address},{port // 256},{port % 256})')
            return
        try:
            if verb == 'PORT':
                fields = arg.split(',')
                if len(fields) == 6 and all(f.isascii() and f.isdigit() and 0 <= int(f) <= 255 for f in fields):
                    ip = '.'.join(str(int(f)) for f in fields[:4])
                    port = int(fields[4]) * 256 + int(fields[5])
                elif arg.isascii() and arg.isdigit() and 1024 <= int(arg) <= 65535:
                    ip = self.client_address[0]
                    port = int(arg)
                else:
                    raise ValueError('Invalid PORT')
            else:
                delimiter = arg[0]
                fields = arg.split(delimiter)
                if not 33 <= ord(delimiter) <= 126 or len(fields) != 5 or fields[0] or fields[-1]:
                    raise ValueError('Invalid EPRT')
                if fields[1] != '1':
                    raise CommandError(522, 'Use IPv4 (1)')
                ip = str(ipaddress.IPv4Address(fields[2]))
                if not fields[3].isascii() or not fields[3].isdigit():
                    raise ValueError('Invalid port')
                port = int(fields[3])
            if ip != self.client_address[0] or not 1024 <= port <= 65535:
                raise ValueError('Active target must be the control peer on an unprivileged port')
        except (ValueError, IndexError) as exc:
            raise CommandError(501, 'Invalid or forbidden data endpoint') from exc
        self.active = (ip, port)
        self.reply(200, 'Active data endpoint set')

    @contextmanager
    def data_connection(self):
        """Establish and yield a connected TCP socket for a data transfer.

        What it does:
            Context manager that yields the connected data socket for passive or active mode.

        How it does:
            1. In passive mode: accepts an incoming connection on self.passive within the configured
               timeout deadline, verifying that the connecting peer matches the client control IP.
            2. In active mode: creates a new TCP socket, registers it with the server, and initiates
               a connection to self.active.
            3. Registers the established socket and yields it.
            4. In a finally block, ensures the data socket is discarded and closed.
        """
        data = None
        try:
            try:
                if self.passive is not None:
                    deadline = time.monotonic() + self.ftp_server.io_timeout
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('No matching data peer')
                        self.passive.settimeout(remaining)
                        data, peer = self.passive.accept()
                        if peer[0] == self.client_address[0]:
                            break
                        data.close()
                        data = None
                else:
                    active = self.active
                    if active is None:
                        raise CommandError(425, 'No active data endpoint')
                    data = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.ftp_server.register(data)
                    data.connect(active)
                self.ftp_server.register(data)
            except OSError as exc:
                raise CommandError(425, 'Cannot open data connection') from exc
            yield data
        finally:
            if data is not None:
                self.ftp_server.discard(data)

    def transfer(self, verb: str, arg: str):
        """Execute a data transfer for directory listings, uploads, or downloads.

        What it does:
            Coordinates the complete data exchange cycle for LIST, NLST, RETR, and STOR.

        How it does:
            1. Validates files and targets using ExitStack before acknowledging the transfer:
               - RETR opens file for reading via self.fs.open_read().
               - STOR opens atomic staging file via self.fs.upload().
               - LIST/NLST retrieves directory entries from self.fs.entries().
            2. Verifies that a data connection mode (passive/active) was pre-configured.
            3. Sends preliminary response 150 'Opening data connection'.
            4. Connects via self.data_connection() context manager:
               - For LIST/NLST: writes formatted listing text lines over the data connection.
               - For RETR: reads blocks from file and transmits over data socket (converting
                 newlines if representation is 'A').
               - For STOR: reads chunks from socket and writes to staging file (converting if 'A').
            5. Checks if server shutdown occurred during transfer (raising ConnectionAbortedError
               to prevent committing a partial upload).
            6. Closes the data connection and sends completion reply 226.
            7. Resets data configuration in a finally block.
        """
        started = False
        file = None
        entries = []
        unique_name = None
        try:
            with ExitStack() as stack:
                # Validate paths before acknowledging a transfer or consuming data.
                if verb == 'RETR':
                    file = stack.enter_context(self.fs.open_read(arg))
                elif verb == 'STOR':
                    file = stack.enter_context(self.fs.upload(arg))
                elif verb == 'APPE':
                    file = stack.enter_context(self.fs.append_upload(arg))
                elif verb == 'STOU':
                    unique_name = self.fs.unique_name()
                    file = stack.enter_context(self.fs.upload(unique_name))
                else:
                    entries = self.fs.entries(arg)
                if self.passive is None and self.active is None:
                    raise CommandError(425, 'Use PASV, EPSV, PORT or EPRT first')
                if verb == 'STOU' and unique_name is not None:
                    self.reply(150, f'FILE: {unique_name}')
                else:
                    self.reply(150, 'Opening data connection')
                started = True
                with self.data_connection() as data:
                    if verb in ('LIST', 'NLST'):
                        for name, info in entries:
                            line = name if verb == 'NLST' else f'{stat.filemode(info.st_mode)} 1 ftp ftp {info.st_size:12d} Jan 01 00:00 {name}'
                            data.sendall(line.encode('utf-8') + b'\r\n')
                    elif verb == 'RETR' and file is not None:
                        chunks = iter(lambda: file.read(BLOCK_SIZE), b'')
                        for chunk in encode_ascii(chunks) if self.representation == 'A' else chunks:
                            data.sendall(chunk)
                    elif file is not None:
                        chunks = socket_chunks(data)
                        for chunk in decode_ascii(chunks) if self.representation == 'A' else chunks:
                            file.write(chunk)
                # Local SHUT_RDWR can look like clean data EOF. Reject it
                # before ExitStack commits the staged upload replacement.
                if self.ftp_server.closing.is_set():
                    raise ConnectionAbortedError('Server closed during transfer')
            if verb == 'STOU' and unique_name is not None:
                self.reply(250, f'Transfer complete (unique file: {unique_name})')
            else:
                self.reply(226, 'Transfer complete')
            LOG.info('%s %s completed', self.client_address[0], verb)
        except OSError as exc:
            if started:
                raise CommandError(426, 'Transfer failed; data connection closed') from exc
            raise
        finally:
            self.reset_data()


def main(argv=None):
    """Run the FTP server command-line interface.

    What it does:
        Parses CLI flags, reads the lab password from the environment, configures logging,
        and starts the concurrent FTP server.

    How it does:
        1. Defines command-line arguments: --host, --port, --root, --user, --timeout, --max-clients.
        2. Retrieves the password from the FTP_PASSWORD environment variable.
        3. Creates the server root directory if it does not exist.
        4. Instantiates FTPServer and registers a SIGTERM signal handler.
        5. Calls server.serve_forever() until interrupted by KeyboardInterrupt (Ctrl+C or SIGTERM).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1', help='IPv4 bind address (default: loopback)')
    parser.add_argument('--port', type=int, default=2121)
    parser.add_argument('--root', type=Path, default=Path('server-data'))
    parser.add_argument('--user', default='student')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--max-clients', type=int, default=32)
    args = parser.parse_args(argv)
    password = os.environ.get('FTP_PASSWORD')
    if not password:
        parser.error('Set FTP_PASSWORD to the lab account password')
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:
        args.root.mkdir(parents=True, exist_ok=True)
        server = FTPServer((args.host, args.port), args.root, args.user, password,
                           timeout=args.timeout, max_clients=args.max_clients)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    def interrupt(signum, frame):
        """Signal handler translating SIGTERM into KeyboardInterrupt for graceful shutdown."""
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    with server:
        host, port = server.address
        print(f'Listening on {host}:{port} root={server.root}', flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
