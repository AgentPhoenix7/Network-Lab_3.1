import ftplib
import io
import socket
import struct
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from ftp_lab.protocol import ControlStream, read_reply
from support import connect, running_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.context = running_server()
        self.server, self.root = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def ftp(self, **options):
        ftp = connect(self.server, **options)
        self.addCleanup(ftp.close)
        return ftp

    def test_login_sequence_and_reauthentication_revoke_access(self):
        ftp = self.ftp(login=False)
        for command in ("PASS lab-test", "PWD", "RETR file", "PASV"):
            with self.subTest(command=command), self.assertRaises(ftplib.error_perm):
                ftp.sendcmd(command)
        with self.assertRaisesRegex(ftplib.error_perm, "^530"):
            ftp.login("student", "incorrect")
        ftp.login("student", "lab-test")
        self.assertEqual(ftp.pwd(), "/")
        self.assertTrue(ftp.sendcmd("USER another").startswith("331"))
        with self.assertRaisesRegex(ftplib.error_perm, "^530"):
            ftp.pwd()

    def test_binary_roundtrip_empty_all_bytes_and_large_active_and_passive(self):
        ftp = self.ftp()
        for passive in (True, False):
            ftp.set_pasv(passive)
            for index, payload in enumerate(
                (b"", bytes(range(256)), bytes(range(256)) * 8193)
            ):
                with self.subTest(passive=passive, size=len(payload)):
                    name = f"file-{index}"
                    self.assertTrue(
                        ftp.storbinary(f"STOR {name}", io.BytesIO(payload)).startswith(
                            "226"
                        )
                    )
                    received = bytearray()
                    ftp.retrbinary(f"RETR {name}", received.extend)
                    self.assertEqual(bytes(received), payload)
                    self.assertEqual((self.root / name).read_bytes(), payload)
                    self.assertEqual(ftp.size(name), len(payload))

    def test_ascii_transfers_convert_posix_newlines(self):
        ftp = self.ftp()
        ftp.storlines("STOR text", io.BytesIO(b"first\nsecond\n"))
        self.assertEqual((self.root / "text").read_bytes(), b"first\nsecond\n")
        lines = []
        ftp.retrlines("RETR text", lines.append)
        self.assertEqual(lines, ["first", "second"])

    def test_directories_listings_spaces_quotes_and_utf8(self):
        ftp = self.ftp()
        name = 'folder "quoted" café'
        ftp.mkd(name)
        ftp.cwd(name)
        self.assertEqual(ftp.pwd(), "/" + name)
        ftp.storbinary("STOR file with spaces", io.BytesIO(b"content"))
        self.assertEqual(ftp.nlst(), ["file with spaces"])
        listing = []
        ftp.retrlines("LIST", listing.append)
        self.assertEqual(len(listing), 1)
        self.assertIn("file with spaces", listing[0])
        ftp.delete("file with spaces")
        ftp.cwd("..")
        ftp.rmd(name)
        self.assertEqual(ftp.nlst(), [])

    def test_long_quoted_directory_creation_returns_success(self):
        ftp = self.ftp()
        name = '"' * 255
        self.assertEqual(ftp.mkd(name), "/" + name)
        ftp.cwd(name)
        self.assertEqual(ftp.pwd(), "/" + name)
        self.assertTrue((self.root / name).is_dir())

    def test_deep_working_directory_can_be_reported(self):
        ftp = self.ftp()
        parts = []
        for index in range(4):
            name = str(index) + "x" * 179
            ftp.mkd(name)
            ftp.cwd(name)
            parts.append(name)
        self.assertEqual(ftp.pwd(), "/" + "/".join(parts))

    def test_missing_file_does_not_break_control_session(self):
        ftp = self.ftp()
        with self.assertRaisesRegex(ftplib.error_perm, "^550"):
            ftp.retrbinary("RETR absent", lambda block: None)
        self.assertTrue(ftp.voidcmd("NOOP").startswith("200"))
        ftp.storbinary("STOR valid", io.BytesIO(b"ok"))

    def test_path_escape_symlinks_and_listing_injection_are_denied(self):
        ftp = self.ftp()
        outside = self.root.parent / "secret"
        outside.write_bytes(b"private")
        (self.root / "link").symlink_to(outside)
        (self.root / "dirlink").symlink_to(self.root.parent, target_is_directory=True)
        (self.root / "bad\nname").write_bytes(b"bad")
        for command in (
            "RETR ../secret",
            "RETR link",
            "STOR link",
            "CWD dirlink",
            "DELE link",
            "RMD /",
        ):
            with (
                self.subTest(command=command),
                self.assertRaisesRegex(ftplib.error_perm, "^550"),
            ):
                ftp.sendcmd(command)
        self.assertEqual(ftp.nlst(), [])
        self.assertEqual(outside.read_bytes(), b"private")

    def test_data_setup_required_and_consumed_on_failed_transfer(self):
        ftp = self.ftp()
        with self.assertRaisesRegex(ftplib.error_temp, "^425"):
            ftp.sendcmd("LIST")
        ftp.sendcmd("PASV")
        with self.assertRaisesRegex(ftplib.error_perm, "^550"):
            ftp.sendcmd("RETR missing")
        with self.assertRaisesRegex(ftplib.error_temp, "^425"):
            ftp.sendcmd("LIST")

    def test_active_bounce_bad_ports_and_extended_commands(self):
        ftp = self.ftp()
        for command in (
            "PORT 8,8,8,8,8,8",
            "PORT 127,0,0,1,0,21",
            "PORT 127,0,0,1,256,1",
            "PORT 127,0,0,1,8,8junk",
            "EPRT |1|8.8.8.8|5000|",
            "EPRT |1|127.0.0.1|21|",
        ):
            with (
                self.subTest(command=command),
                self.assertRaisesRegex(ftplib.error_perm, "^501"),
            ):
                ftp.sendcmd(command)
        self.assertTrue(ftp.sendcmd("EPRT |1|127.0.0.1|5000|").startswith("200"))
        # Simplified textbook PORT notation (Figure 10.18: PORT 1267)
        self.assertTrue(ftp.sendcmd("PORT 5001").startswith("200"))
        self.assertTrue(ftp.sendcmd("EPSV 1").startswith("229"))
        with self.assertRaisesRegex(ftplib.error_perm, "^522"):
            ftp.sendcmd("EPSV 2")
        self.assertTrue(ftp.sendcmd("EPSV ALL").startswith("200"))
        with self.assertRaisesRegex(ftplib.error_perm, "^501"):
            ftp.sendcmd("PASV")

    def test_rnfr_rnto_rename_sequence(self):
        ftp = self.ftp()
        (self.root / "report.old").write_bytes(b"content to rename")
        with self.assertRaisesRegex(ftplib.error_perm, "^503"):
            ftp.sendcmd("RNTO report.new")
        with self.assertRaisesRegex(ftplib.error_perm, "^550"):
            ftp.sendcmd("RNFR nonexistent.txt")
        self.assertTrue(ftp.sendcmd("RNFR report.old").startswith("350"))
        self.assertTrue(ftp.sendcmd("RNTO report.new").startswith("250"))
        self.assertFalse((self.root / "report.old").exists())
        self.assertEqual((self.root / "report.new").read_bytes(), b"content to rename")

    def test_protocol_pdf_table_commands(self):
        ftp = self.ftp()
        # Table 10.4 TYPE options: A, E, I (and EBCDIC)
        self.assertTrue(ftp.sendcmd("TYPE A").startswith("200"))
        self.assertTrue(ftp.sendcmd("TYPE E").startswith("200"))
        self.assertTrue(ftp.sendcmd("TYPE EBCDIC").startswith("200"))
        self.assertTrue(ftp.sendcmd("TYPE I").startswith("200"))
        # Table 10.4 STRU options: F, R, P
        self.assertTrue(ftp.sendcmd("STRU F").startswith("200"))
        self.assertTrue(ftp.sendcmd("STRU R").startswith("200"))
        self.assertTrue(ftp.sendcmd("STRU P").startswith("200"))
        # Table 10.4 MODE options: S, B, C
        self.assertTrue(ftp.sendcmd("MODE S").startswith("200"))
        self.assertTrue(ftp.sendcmd("MODE B").startswith("200"))
        self.assertTrue(ftp.sendcmd("MODE C").startswith("200"))
        # HELP lists Table 10.4 commands including RNFR and RNTO
        help_reply = ftp.sendcmd("HELP")
        self.assertTrue(help_reply.startswith("214"))
        for cmd in ("USER", "PASS", "QUIT", "ABOR", "PWD", "CWD", "CDUP", "LIST",
                    "MKD", "RMD", "DELE", "RNFR", "RNTO", "RETR", "STOR",
                    "STRU", "TYPE", "PORT", "PASV", "MODE"):
            self.assertIn(cmd, help_reply)

    def test_rfc959_commands(self):
        ftp = self.ftp()
        # STOU stores a unique file
        self.assertTrue(ftp.storbinary("STOU", io.BytesIO(b"unique payload")).startswith("250"))
        files = ftp.nlst()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith("file_"))
        self.assertEqual((self.root / files[0]).read_bytes(), b"unique payload")

        # APPE creates new file if absent, and appends if present
        ftp.storbinary("APPE append_file.txt", io.BytesIO(b"hello "))
        self.assertEqual((self.root / "append_file.txt").read_bytes(), b"hello ")
        ftp.storbinary("APPE append_file.txt", io.BytesIO(b"world"))
        self.assertEqual((self.root / "append_file.txt").read_bytes(), b"hello world")

        # STAT without args returns 211 status
        stat_reply = ftp.sendcmd("STAT")
        self.assertTrue(stat_reply.startswith("211"))
        self.assertIn("FTP server status", stat_reply)
        self.assertIn("TYPE: I", stat_reply)

        # STAT with args returns 213 status
        stat_file = ftp.sendcmd("STAT append_file.txt")
        self.assertTrue(stat_file.startswith("213"))
        self.assertIn("append_file.txt", stat_file)

        # ALLO, ACCT, SITE return 202
        self.assertTrue(ftp.sendcmd("ALLO 1024").startswith("202"))
        self.assertTrue(ftp.sendcmd("ACCT lab-account").startswith("202"))
        self.assertTrue(ftp.sendcmd("SITE CHMOD 644").startswith("202"))

        # SMNT, REST return 502
        with self.assertRaisesRegex(ftplib.error_perm, "^502"):
            ftp.sendcmd("SMNT /")
        with self.assertRaisesRegex(ftplib.error_perm, "^502"):
            ftp.sendcmd("REST 100")

        # REIN reinitializes session
        rein_reply = ftp.sendcmd("REIN")
        self.assertTrue(rein_reply.startswith("220"))
        # Unauthenticated after REIN
        with self.assertRaisesRegex(ftplib.error_perm, "^530"):
            ftp.pwd()
        ftp.login("student", "lab-test")
        self.assertEqual(ftp.pwd(), "/")

    def test_additional_rfc959_response_codes(self):
        ftp = self.ftp()
        (self.root / "sample.txt").write_bytes(b"content")
        (self.root / "subfolder").mkdir(exist_ok=True)

        # 212: STAT on directory
        dir_stat = ftp.sendcmd("STAT subfolder")
        self.assertTrue(dir_stat.startswith("212"), f"Expected 212 for directory status, got: {dir_stat}")

        # 213: STAT on regular file
        file_stat = ftp.sendcmd("STAT sample.txt")
        self.assertTrue(file_stat.startswith("213"), f"Expected 213 for file status, got: {file_stat}")

        # 553: RNTO with invalid destination name
        ftp.sendcmd("RNFR sample.txt")
        with self.assertRaisesRegex(ftplib.error_perm, "^553"):
            ftp.sendcmd("RNTO .ftp-upload-target")

        # 553: STOR with invalid/reserved file name
        with self.assertRaisesRegex(ftplib.error_perm, "^553"):
            ftp.sendcmd("STOR .ftp-upload-forbidden")

        # Simulated errno mappings
        import errno
        from ftp_lab.server import FTPSession

        # 450: File busy (EBUSY)
        with patch.object(FTPSession, 'authenticated_command', side_effect=OSError(errno.EBUSY, "Device or resource busy")):
            with self.assertRaisesRegex(ftplib.error_temp, "^450"):
                ftp.sendcmd("DELE sample.txt")

        # 452: Insufficient storage space (ENOSPC)
        with patch.object(FTPSession, 'authenticated_command', side_effect=OSError(errno.ENOSPC, "No space left on device")):
            with self.assertRaisesRegex(ftplib.error_temp, "^452"):
                ftp.sendcmd("STOR sample.txt")

        # 552: Exceeded storage allocation (EDQUOT)
        edquot = getattr(errno, 'EDQUOT', 122)
        with patch.object(FTPSession, 'authenticated_command', side_effect=OSError(edquot, "Disk quota exceeded")):
            with self.assertRaisesRegex(ftplib.error_perm, "^552"):
                ftp.sendcmd("STOR sample.txt")

        # 451: Local error during transfer (non-network OSError during streaming)
        with patch("ftp_lab.server.socket_chunks", side_effect=OSError(errno.EIO, "I/O error")):
            data = ftp.transfercmd("STOR iotest.dat")
            with self.assertRaisesRegex(ftplib.error_temp, "^451"):
                ftp.voidresp()
            data.close()

    def test_timeout_waiting_for_passive_data_connection_and_recovery(self):
        ftp = self.ftp()
        ftp.sendcmd("PASV")
        ftp.putcmd("LIST")
        self.assertTrue(ftp.getresp().startswith("150"))
        with self.assertRaisesRegex(ftplib.error_temp, "^425"):
            ftp.getresp()
        self.assertTrue(ftp.sendcmd("NOOP").startswith("200"))

    def test_reset_upload_preserves_original_and_cleans_staging_file(self):
        ftp = self.ftp()
        (self.root / "file").write_bytes(b"original")
        data = ftp.transfercmd("STOR file")
        data.sendall(b"partial")
        data.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        data.close()
        with self.assertRaisesRegex(ftplib.error_temp, "^426"):
            ftp.voidresp()
        self.assertEqual((self.root / "file").read_bytes(), b"original")
        self.assertEqual(list(self.root.iterdir()), [self.root / "file"])

    def test_fragmented_pipelined_and_oversized_commands(self):
        with socket.create_connection(self.server.address, timeout=3) as sock:
            stream = ControlStream(sock)
            self.assertEqual(read_reply(stream).code, 220)
            sock.sendall(b"US")
            sock.sendall(b"ER student\r\nPASS lab-test\r\nPWD\r\n")
            self.assertEqual(
                [read_reply(stream).code for _ in range(3)], [331, 230, 257]
            )
            sock.sendall(b"x" * 513)
            self.assertEqual(read_reply(stream).code, 500)
            self.assertEqual(sock.recv(1), b"")

    def test_independent_concurrent_working_directories(self):
        for name in ("one", "two", "three", "four"):
            (self.root / name).mkdir()

        def transfer(name):
            with connect(self.server) as ftp:
                ftp.cwd(name)
                ftp.storbinary("STOR file", io.BytesIO(name.encode()))
                return ftp.pwd()

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(
                list(pool.map(transfer, ("one", "two", "three", "four"))),
                ["/one", "/two", "/three", "/four"],
            )
        for name in ("one", "two", "three", "four"):
            self.assertEqual((self.root / name / "file").read_text(), name)

    def test_shutdown_closes_idle_sessions_promptly(self):
        ftp = self.ftp()
        finished = threading.Event()
        closer = threading.Thread(
            target=lambda: (self.server.server_close(), finished.set())
        )
        closer.start()
        self.assertTrue(finished.wait(2))
        closer.join(2)
        with self.assertRaises((EOFError, OSError)):
            ftp.sendcmd("NOOP")


class CapacityTests(unittest.TestCase):
    def test_shutdown_eof_does_not_publish_a_staged_upload(self):
        from ftp_lab.protocol import socket_chunks

        with running_server() as (server, root), connect(server) as ftp:
            (root / 'waiting').write_bytes(b'original')

            def closing_chunks(sock):
                yield from socket_chunks(sock)
                # SHUT_RDWR can make recv return clean EOF during shutdown.
                # Keep that ordering deterministic instead of timing a thread.
                server.closing.set()

            with patch('ftp_lab.server.socket_chunks', closing_chunks), self.assertRaisesRegex(ftplib.error_temp, '^426'):
                ftp.storbinary('STOR waiting', io.BytesIO(b'replacement'))
            self.assertEqual((root / 'waiting').read_bytes(), b'original')
            self.assertEqual(list(root.iterdir()), [root / 'waiting'])

    def test_capacity_limit_rejects_additional_clients(self):
        with running_server(max_clients=1) as (server, _root), connect(server) as first:
            with self.assertRaisesRegex(ftplib.error_temp, "^421"):
                connect(server, login=False)
            self.assertEqual(first.pwd(), "/")

    def test_shutdown_interrupts_data_receive_before_long_timeout(self):
        with running_server(timeout=10) as (server, root):
            (root / "waiting").write_bytes(b"original")
            with connect(server) as ftp:
                ftp.voidcmd("TYPE I")
                data = ftp.transfercmd("STOR waiting")
                try:
                    payload = b"x" * (1024 * 1024)
                    data.sendall(payload)
                    deadline = time.monotonic() + 2
                    while not any(
                        p.stat().st_size > 0 for p in root.glob(".ftp-upload-*")
                    ):
                        snapshot = [(p.name, p.stat().st_size) for p in root.iterdir()]
                        self.assertLess(
                            time.monotonic(),
                            deadline,
                            f"Server never staged upload bytes: {snapshot}",
                        )
                        time.sleep(0.005)
                    finished = threading.Event()
                    closer = threading.Thread(
                        target=lambda: (server.server_close(), finished.set())
                    )
                    closer.start()
                    self.assertTrue(
                        finished.wait(2),
                        "Shutdown waited for the 10-second data timeout",
                    )
                    closer.join(2)
                    self.assertEqual((root / "waiting").read_bytes(), b"original")
                    self.assertEqual(list(root.iterdir()), [root / "waiting"])
                finally:
                    data.close()
                    ftp.close()


if __name__ == "__main__":
    unittest.main()
