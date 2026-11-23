"""Verify control framing, ASCII conversion, and filesystem confinement."""

import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from ftp_lab.filesystem import Sandbox
from ftp_lab.protocol import (
    ControlStream,
    ProtocolError,
    decode_ascii,
    encode_ascii,
    read_reply,
)


class ProtocolTests(unittest.TestCase):
    """Control framing and streaming NVT ASCII regression tests."""

    def setUp(self):
        self.receiver, self.sender = socket.socketpair()
        self.receiver.settimeout(1)
        self.addCleanup(self.receiver.close)
        self.addCleanup(self.sender.close)
        self.stream = ControlStream(self.receiver, max_line=512)

    def test_fragmented_and_pipelined_commands(self):
        """Verify fragmented and pipelined commands."""
        result = []
        started = threading.Event()

        def read():
            started.set()
            result.append(self.stream.readline())

        self.sender.sendall(b'US')
        worker = threading.Thread(target=read)
        worker.start()
        self.assertTrue(started.wait(1))
        self.sender.sendall(b'ER student\r\nNOOP\r\n')
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ['USER student'])
        self.assertEqual(self.stream.readline(), 'NOOP')

    def test_overlong_or_invalid_line_is_rejected(self):
        """Verify overlong or invalid line is rejected."""
        for wire in (b'x' * 513, b'NOOP\n', b'NO\x00OP\r\n', b'\xff\r\n'):
            with self.subTest(wire=wire[:20]):
                a, b = socket.socketpair()
                try:
                    a.settimeout(1)
                    b.sendall(wire)
                    with self.assertRaises(ProtocolError):
                        ControlStream(a, max_line=512).readline()
                finally:
                    a.close()
                    b.close()

    def test_partial_command_at_eof_is_rejected(self):
        """Verify partial command at eof is rejected."""
        self.sender.sendall(b'USER student')
        self.sender.shutdown(socket.SHUT_WR)
        with self.assertRaises(ProtocolError):
            self.stream.readline()

    def test_multiline_reply_ends_only_at_matching_code(self):
        """Verify multiline reply ends only at matching code."""
        self.sender.sendall(b'211-Features\r\n UTF8\r\n200 still text\r\n211 End\r\n')
        reply = read_reply(self.stream)
        self.assertEqual(reply.code, 211)
        self.assertIn(' UTF8', reply.lines)

    def test_command_injection_is_rejected_before_send(self):
        """Verify command injection is rejected before send."""
        with self.assertRaises(ProtocolError):
            self.stream.send_line('USER x\r\nDELE file')

    def test_ascii_codec_preserves_newlines_and_literal_carriage_returns(self):
        """Verify ascii codec preserves newlines and literal carriage returns."""
        local = b'a\nb\rc\r\n'
        wire = b'a\r\nb\r\x00c\r\x00\r\n'
        self.assertEqual(b''.join(encode_ascii([local[:3], local[3:]])), wire)
        self.assertEqual(b''.join(decode_ascii(bytes([c]) for c in wire)), local)
        self.assertEqual(b''.join(decode_ascii([b'end\r'])), b'end\r')


class FilesystemTests(unittest.TestCase):
    """Descriptor-relative path confinement and atomic file operation tests."""

    def setUp(self):
        # unittest exits this context after the per-test server and socket cleanups.
        # pylint: disable-next=consider-using-with
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp) / 'root'
        self.root.mkdir()
        self.outside = Path(self.temp) / 'secret'
        self.outside.write_bytes(b'private')
        self.fs = Sandbox(self.root)
        self.addCleanup(self.fs.close)

    def test_navigation_and_absolute_paths_are_virtual(self):
        """Verify navigation and absolute paths are virtual."""
        self.fs.mkdir('nested')
        self.fs.chdir('nested')
        self.assertEqual(self.fs.pwd(), '/nested')
        with self.fs.upload('/hello world') as out:
            out.write(b'hello')
        self.assertEqual((self.root / 'hello world').read_bytes(), b'hello')
        self.fs.chdir('..')
        self.assertEqual(self.fs.pwd(), '/')
        self.assertEqual(self.fs.size('hello world'), 5)
        self.assertEqual([name for name, _ in self.fs.entries()], ['hello world', 'nested'])
        self.fs.delete('hello world')
        self.fs.rmdir('nested')
        self.assertEqual(self.fs.entries(), [])

    def test_traversal_and_symlinks_cannot_read_or_write_outside(self):
        """Verify traversal and symlinks cannot read or write outside."""
        (self.root / 'link').symlink_to(self.outside)
        (self.root / 'dirlink').symlink_to(self.root.parent, target_is_directory=True)
        for name in ('../secret', 'link', 'dirlink/secret'):
            with self.subTest(name=name):
                with self.assertRaises((OSError, ValueError)), self.fs.open_read(name):
                    pass
                with self.assertRaises((OSError, ValueError)), self.fs.upload(name) as out:
                    out.write(b'overwritten')
        self.assertEqual(self.outside.read_bytes(), b'private')
        self.assertEqual(self.fs.entries(), [])

    def test_failed_upload_preserves_existing_file_and_removes_staging_file(self):
        """Verify failed upload preserves existing file and removes staging file."""
        (self.root / 'file').write_bytes(b'original')
        with self.assertRaises(ConnectionResetError), self.fs.upload('file') as out:
            out.write(b'partial')
            raise ConnectionResetError('injected disconnect')
        self.assertEqual((self.root / 'file').read_bytes(), b'original')
        self.assertEqual(os.listdir(self.root), ['file'])

    def test_special_files_and_reserved_names_are_rejected(self):
        """Verify special files and reserved names are rejected."""
        os.mkfifo(self.root / 'fifo')
        for name in ('fifo', '.ftp-upload-hidden', 'bad\nname', '/'):
            with self.subTest(name=name), self.assertRaises(
                (OSError, ValueError)
            ), self.fs.open_read(name):
                self.fail('opened a special file')

    def test_resulting_virtual_path_length_is_bounded(self):
        """Verify resulting virtual path length is bounded."""
        self.fs.cwd = ('x' * 250,) * 16
        with self.assertRaises(ValueError):
            self.fs.normalize('y' * 100)

    def test_rename_regular_file_and_directory(self):
        """Verify rename regular file and directory."""
        (self.root / 'old.txt').write_bytes(b'rename payload')
        self.fs.check_exists('old.txt')
        self.fs.rename('old.txt', 'new.txt')
        self.assertFalse((self.root / 'old.txt').exists())
        self.assertEqual((self.root / 'new.txt').read_bytes(), b'rename payload')
        self.fs.mkdir('old_dir')
        self.fs.rename('old_dir', 'new_dir')
        self.assertFalse((self.root / 'old_dir').exists())
        self.assertTrue((self.root / 'new_dir').is_dir())
        with self.assertRaises((OSError, ValueError)):
            self.fs.check_exists('nonexistent.txt')
        with self.assertRaises((OSError, ValueError)):
            self.fs.rename('nonexistent.txt', 'dest.txt')

    def test_append_upload_and_unique_name(self):
        # Appending to nonexistent creates file
        """Verify append upload and unique name."""
        with self.fs.append_upload('append_test.txt') as target:
            target.write(b'first ')
        self.assertEqual((self.root / 'append_test.txt').read_bytes(), b'first ')
        # Appending to existing file appends data
        with self.fs.append_upload('append_test.txt') as target:
            target.write(b'second')
        self.assertEqual((self.root / 'append_test.txt').read_bytes(), b'first second')
        # Unique name generates an unused name
        unique = self.fs.unique_name()
        self.assertTrue(unique.startswith('file_'))
        self.assertFalse((self.root / unique).exists())


if __name__ == '__main__':
    unittest.main()
