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
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message


class FTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def __init__(self, address, root: Path, username: str, password: str, *, timeout=30.0, max_clients=32):
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
        host, port = self.server_address[:2]
        if not isinstance(host, str):
            raise TypeError('Expected a bound IPv4 address')
        return host, port

    def register(self, sock):
        sock.settimeout(self.io_timeout)
        with self.socket_lock:
            if self.closing.is_set():
                sock.close()
                raise ConnectionAbortedError('Server is closing')
            self.sockets.add(sock)

    def discard(self, sock):
        with self.socket_lock:
            self.sockets.discard(sock)
        sock.close()

    def process_request(self, request, client_address):
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
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_close(self):
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
    request: socket.socket
    fs: Sandbox

    def setup(self):
        if not isinstance(self.server, FTPServer):
            raise TypeError('FTP sessions require an FTPServer')
        self.ftp_server = self.server

    def handle(self):
        self.stream = ControlStream(self.request, max_line=512)
        self.pending_user = None
        self.authenticated = False
        self.representation = 'A'
        self.passive = None
        self.active = None
        self.epsv_only = False
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

    def reply(self, code, text):
        self.stream.send_line(f'{code} {text}')

    def reset_data(self):
        if self.passive is not None:
            self.ftp_server.discard(self.passive)
            self.passive = None
        self.active = None

    def dispatch(self, line):
        verb, _, arg = line.partition(' ')
        verb = verb.upper()
        if not verb or not verb.isascii() or not verb.isalpha():
            raise CommandError(500, 'Invalid command')
        required = {'USER', 'TYPE', 'MODE', 'STRU', 'CWD', 'MKD', 'RMD', 'DELE', 'RETR', 'STOR', 'SIZE', 'PORT', 'EPRT', 'OPTS'}
        no_arg = {'QUIT', 'NOOP', 'PWD', 'CDUP', 'SYST', 'FEAT', 'PASV', 'ABOR'}
        if verb in required and not arg or verb in no_arg and arg:
            raise CommandError(501, 'Invalid command arguments')
        if verb == 'QUIT':
            self.reply(221, 'Goodbye')
            return False
        if verb == 'USER':
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
            self.reply(214, 'USER PASS QUIT PWD CWD CDUP MKD RMD DELE TYPE MODE STRU PASV EPSV PORT EPRT LIST NLST RETR STOR SIZE NOOP ABOR')
        elif verb == 'OPTS':
            if arg.upper() != 'UTF8 ON':
                raise CommandError(501, 'Only UTF8 ON is supported')
            self.reply(200, 'UTF8 enabled')
        elif not self.authenticated:
            raise CommandError(530, 'Please login with USER and PASS')
        else:
            try:
                self.authenticated_command(verb, arg)
            except (OSError, ValueError) as exc:
                LOG.debug('Filesystem/argument error: %s', type(exc).__name__)
                raise CommandError(550, 'File or directory unavailable or forbidden') from exc
        return True

    def authenticated_command(self, verb, arg):
        if verb == 'TYPE':
            kind = arg.upper()
            if kind not in ('A', 'A N', 'I', 'L 8'):
                raise CommandError(504, 'Only TYPE A and TYPE I are supported')
            self.representation = 'A' if kind.startswith('A') else 'I'
            self.reply(200, 'Transfer type set')
        elif verb in ('MODE', 'STRU'):
            if arg.upper() != ('S' if verb == 'MODE' else 'F'):
                raise CommandError(504, 'Only stream mode and file structure are supported')
            self.reply(200, 'OK')
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
        elif verb == 'SIZE':
            if self.representation != 'I':
                raise CommandError(550, 'SIZE requires TYPE I')
            self.reply(213, str(self.fs.size(arg)))
        elif verb in ('PASV', 'EPSV', 'PORT', 'EPRT'):
            self.configure_data(verb, arg)
        elif verb in ('LIST', 'NLST', 'RETR', 'STOR'):
            self.transfer(verb, arg)
        elif verb == 'ABOR':
            self.reset_data()
            self.reply(226, 'Data setup cleared')
        else:
            raise CommandError(502, 'Command not implemented')

    def configure_data(self, verb, arg):
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
                if len(fields) != 6 or any(not f.isascii() or not f.isdigit() or not 0 <= int(f) <= 255 for f in fields):
                    raise ValueError('Invalid PORT')
                ip = '.'.join(str(int(f)) for f in fields[:4])
                port = int(fields[4]) * 256 + int(fields[5])
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

    def transfer(self, verb, arg):
        started = False
        file = None
        entries = []
        try:
            with ExitStack() as stack:
                # Validate paths before acknowledging a transfer or consuming data.
                if verb == 'RETR':
                    file = stack.enter_context(self.fs.open_read(arg))
                elif verb == 'STOR':
                    file = stack.enter_context(self.fs.upload(arg))
                else:
                    entries = self.fs.entries(arg)
                if self.passive is None and self.active is None:
                    raise CommandError(425, 'Use PASV, EPSV, PORT or EPRT first')
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
            self.reply(226, 'Transfer complete')
            LOG.info('%s %s completed', self.client_address[0], verb)
        except OSError as exc:
            if started:
                raise CommandError(426, 'Transfer failed; data connection closed') from exc
            raise
        finally:
            self.reset_data()


def main(argv=None):
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
