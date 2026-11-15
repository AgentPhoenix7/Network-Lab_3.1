import os
import socket
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from ftp_lab.client import FTPClient, FTPError
from ftp_lab.protocol import ProtocolError
from support import running_server


@contextmanager
def independent_peer(handler):
    """A small independent wire oracle; no production FTP parser is reused."""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(3)

        def run():
            with listener.accept()[0] as control:
                control.settimeout(3)
                with control.makefile('rb') as reader:
                    handler(control, reader)

        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(run)
            try:
                yield listener.getsockname()
            finally:
                worker.result(timeout=4)


class ClientTests(unittest.TestCase):
    def test_own_client_upload_download_and_directory_commands_in_both_modes(self):
        with running_server() as (server, _root), tempfile.TemporaryDirectory() as folder:
            local = Path(folder) / 'local'
            payload = bytes(range(256)) * 257
            local.write_bytes(payload)
            for passive in (True, False):
                with self.subTest(passive=passive), FTPClient(*server.address, passive=passive, timeout=3) as client:
                    client.login('student', 'lab-test')
                    client.command('MKD', 'new')
                    client.command('CWD', 'new')
                    self.assertEqual(client.pwd(), '/new')
                    client.upload(local, 'remote name')
                    self.assertEqual(client.size('remote name'), len(payload))
                    self.assertEqual(client.list(names_only=True), ['remote name'])
                    target = Path(folder) / 'download'
                    client.download('remote name', target)
                    self.assertEqual(target.read_bytes(), payload)
                    client.command('DELE', 'remote name')
                    client.command('CDUP')
                    client.command('RMD', 'new')
                    client.quit()

    def test_ascii_and_binary_mode_survive_directory_listing(self):
        with running_server() as (server, root), tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'text'
            source.write_bytes(b'a\nb\rc\r\n')
            with FTPClient(*server.address) as client:
                client.login('student', 'lab-test')
                client.binary = False
                client.upload(source, 'text')
                self.assertEqual((root / 'text').read_bytes(), source.read_bytes())
                client.list()
                target = Path(folder) / 'retrieved'
                client.download('text', target)
                self.assertEqual(target.read_bytes(), source.read_bytes())
                client.binary = True
                client.upload(source, 'binary')
                client.list()
                client.download('binary', target)
                self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_password_is_redacted_and_command_injection_is_blocked(self):
        transcript = []
        with running_server() as (server, _root), FTPClient(*server.address, trace=transcript.append) as client:
            client.login('student', 'lab-test')
            self.assertNotIn('lab-test', '\n'.join(transcript))
            self.assertIn('C> PASS <redacted>', transcript)
            with self.assertRaises(ProtocolError):
                client.command('CWD', '/\r\nDELE file')

    def test_missing_remote_preserves_local_file(self):
        with running_server() as (server, _root), tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'download'
            target.write_bytes(b'original')
            with FTPClient(*server.address) as client:
                client.login('student', 'lab-test')
                with self.assertRaises(FTPError):
                    client.download('missing', target)
                self.assertEqual(client.pwd(), '/')
            self.assertEqual(target.read_bytes(), b'original')
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def peer_transfer(self, control, reader, *, fail=False):
        control.sendall(b'220-Welcome\r\n Independent FTP test peer\r\n220 Ready\r\n')
        for expected, reply in ((b'USER student\r\n', b'331 Password\r\n'),
                                (b'PASS lab-test\r\n', b'230 OK\r\n')):
            self.assertEqual(reader.readline(), expected)
            control.sendall(reply)
        self.assertEqual(reader.readline(), b'TYPE I\r\n' if fail else b'TYPE A\r\n')
        control.sendall(b'200 OK\r\n')
        self.assertEqual(reader.readline(), b'EPSV\r\n')
        control.sendall(b'502 EPSV unavailable\r\n')
        self.assertEqual(reader.readline(), b'PASV\r\n')
        with socket.socket() as data_listener:
            data_listener.bind(('127.0.0.1', 0))
            data_listener.listen()
            data_listener.settimeout(3)
            port = data_listener.getsockname()[1]
            # The client must use the control peer, ignoring this advertised IP.
            control.sendall(f'227 Passive (8,8,8,8,{port // 256},{port % 256})\r\n'.encode())
            self.assertEqual(reader.readline(), b'RETR file\r\n' if fail else b'NLST\r\n')
            control.sendall(b'150 Opening\r\n')
            with data_listener.accept()[0] as data:
                data.sendall(b'partial' if fail else b'independent file\r\n')
        control.sendall(b'426 Transfer failed\r\n' if fail else b'226 Complete\r\n')

    def test_multiline_greeting_epsv_fallback_and_pasv_peer_pinning(self):
        with independent_peer(self.peer_transfer) as address, FTPClient(*address, timeout=2) as client:
            client.login('student', 'lab-test')
            self.assertEqual(client.list(names_only=True), ['independent file'])

    def test_failed_completion_from_independent_peer_preserves_local_file(self):
        def peer(control, reader):
            self.peer_transfer(control, reader, fail=True)

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'file'
            target.write_bytes(b'original')
            with independent_peer(peer) as address, FTPClient(*address, timeout=2) as client:
                client.login('student', 'lab-test')
                with self.assertRaises(FTPError):
                    client.download('file', target)
            self.assertEqual(target.read_bytes(), b'original')
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_batch_cli_roundtrip_and_failure_exit_status(self):
        with running_server() as (server, _root), tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source'
            target = Path(folder) / 'target'
            source.write_bytes(b'CLI\x00\xff\n')
            env = dict(os.environ, FTP_PASSWORD='lab-test')
            command = [sys.executable, '-m', 'ftp_lab.client', '--host', '127.0.0.1',
                       '--port', str(server.address[1]), '--script', '-']
            result = subprocess.run(command, input=f'put {source} remote\nls\nget remote {target}\nquit\n',
                                    text=True, capture_output=True, env=env, timeout=5, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            failure = subprocess.run(command, input='get missing\n', text=True,
                                     capture_output=True, env=env, timeout=5, check=False)
            self.assertEqual(failure.returncode, 1, failure.stderr)
            self.assertIn('550', failure.stderr)


if __name__ == '__main__':
    unittest.main()
