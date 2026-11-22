from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ftp_lab.client import FTPClient, execute
from support import running_server

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "server-data" / "samples"
SERVER_DATA = Path(__file__).resolve().parent.parent / "server-data"


class PermutationsTestCase(unittest.TestCase):
    def test_samples_exist_and_manifest_matches(self):
        manifest_path = SAMPLES_DIR / "manifest.json"
        self.assertTrue(manifest_path.exists(), "samples/manifest.json must exist")
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(len(manifest), 27, "Must have exactly 27 RFC 959 permutations")
        for item in manifest:
            file_path = SAMPLES_DIR / item["filename"]
            self.assertTrue(file_path.exists(), f"Sample file {item['filename']} must exist")
            self.assertEqual(file_path.stat().st_size, item["size_bytes"])

    def test_client_type_stru_mode_execution(self):
        with running_server() as (server, root):
            host, port = server.server_address
            with FTPClient(host, port) as client:
                client.login('student', 'lab-test')

                # Test TYPE A, STRU F, MODE S
                execute(client, "type A")
                execute(client, "stru F")
                execute(client, "mode S")
                stat_reply = client.stat()
                status_text = " ".join(stat_reply.lines)
                self.assertIn("TYPE: A", status_text)
                self.assertIn("STRU: F", status_text)
                self.assertIn("MODE: S", status_text)

                # Test TYPE E, STRU R, MODE B
                execute(client, "type E")
                execute(client, "stru R")
                execute(client, "mode B")
                stat_reply = client.stat()
                status_text = " ".join(stat_reply.lines)
                self.assertIn("TYPE: E", status_text)
                self.assertIn("STRU: R", status_text)
                self.assertIn("MODE: B", status_text)

                # Test TYPE I, STRU P, MODE C
                execute(client, "type I")
                execute(client, "stru P")
                execute(client, "mode C")
                stat_reply = client.stat()
                status_text = " ".join(stat_reply.lines)
                self.assertIn("TYPE: I", status_text)
                self.assertIn("STRU: P", status_text)
                self.assertIn("MODE: C", status_text)

                # Test quote command
                execute(client, "quote NOOP")
                execute(client, "quote SYST")

    def test_download_samples(self):
        manifest_path = SAMPLES_DIR / "manifest.json"
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)

        with running_server() as (server, root):
            # Copy samples into running_server's root so server has them
            shutil.copytree(SAMPLES_DIR, root / "samples", dirs_exist_ok=True)
            shutil.copy2(SERVER_DATA / "welcome.txt", root / "welcome.txt")

            host, port = server.server_address
            with FTPClient(host, port) as client, tempfile.TemporaryDirectory() as tmpdir:
                client.login('student', 'lab-test')

                # Verify welcome.txt in root
                local_welcome = Path(tmpdir) / "welcome.txt"
                client.download("welcome.txt", str(local_welcome))
                self.assertTrue(local_welcome.exists())
                self.assertEqual(local_welcome.stat().st_size, (SERVER_DATA / "welcome.txt").stat().st_size)

                # Switch to samples directory
                execute(client, "cd samples")

                for item in manifest:
                    remote_name = item["filename"]
                    local_dest = Path(tmpdir) / f"test_{remote_name}"
                    execute(client, f"type {item['type']}")
                    execute(client, f"stru {item['stru']}")
                    execute(client, f"mode {item['mode']}")
                    client.download(remote_name, str(local_dest))
                    self.assertTrue(local_dest.exists(), f"Downloaded file {local_dest} must exist")
                    self.assertEqual(local_dest.stat().st_size, item["size_bytes"])


if __name__ == "__main__":
    unittest.main()
