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
    def __init__(self, reply):
        self.reply = reply
        super().__init__(f'{reply.code} {reply.message}')


class FTPClient:
    def __init__(self, host='127.0.0.1', port=2121, *, timeout=30, passive=True, trace=None):
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
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def read_response(self):
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
        if reply.code not in codes:
            raise ProtocolError(f'Unexpected FTP reply: {reply.code} {reply.message}')
        return reply

    def command(self, verb, arg=''):
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
        reply = self.command('USER', username)
        if reply.code == 331:
            reply = self.command('PASS', password)
        return self.expect(reply, (230,))

    def pwd(self):
        reply = self.expect(self.command('PWD'), (257,))
        match = re.match(r'^"((?:""|[^"])*)"', reply.message)
        if match is None:
            raise ProtocolError('Invalid PWD reply')
        return match[1].replace('""', '"')

    def size(self, remote):
        self.expect(self.command('TYPE', 'I'), (200,))
        reply = self.expect(self.command('SIZE', remote), (213,))
        if not reply.message.isascii() or not reply.message.isdigit():
            raise ProtocolError('Invalid SIZE reply')
        return int(reply.message)

    def passive_port(self):
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
        self.expect(self.command('TYPE', 'A'), (200,))
        content = bytearray()

        def receive(data):
            for chunk in socket_chunks(data):
                content.extend(chunk)

        self.transfer('NLST' if names_only else 'LIST', remote, receive)
        return content.decode('utf-8').splitlines()

    def upload(self, local, remote, *, progress: Callable[[int], None] | None = None):
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

    def quit(self):
        try:
            return self.expect(self.command('QUIT'), (221,))
        finally:
            self.close()


HELP = '''Commands (quote paths containing spaces):
  pwd | ls [remote] | nlst [remote] | cd remote | cdup
  mkdir remote | rmdir remote | delete remote | size remote
  get remote [local] | put local [remote]
  ascii | binary | passive | active | noop | help | quit'''


def execute(client, line):
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
