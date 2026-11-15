import ftplib
import os
import re
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def test_server_and_client_subprocesses_and_sigterm_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'root'
            env = dict(os.environ, FTP_PASSWORD='lab-test')
            process = subprocess.Popen(
                [sys.executable, '-m', 'ftp_lab.server', '--port', '0', '--root', str(root)],
                cwd=PROJECT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                if process.stdout is None:
                    self.fail('Server stdout pipe was not created')
                self.assertTrue(select.select([process.stdout], [], [], 3)[0], 'Server never announced startup')
                startup = process.stdout.readline()
                match = re.search(r'Listening on 127\.0\.0\.1:(\d+)', startup)
                if match is None:
                    self.fail(startup)
                port = int(match[1])
                source, target = Path(folder) / 'source', Path(folder) / 'target'
                source.write_bytes(bytes(range(256)) * 1024)
                script = Path(folder) / 'session.txt'
                script.write_text(f'put {source} wire-file\nget wire-file {target}\nquit\n')
                result = subprocess.run(
                    [sys.executable, '-m', 'ftp_lab.client', '--port', str(port), '--trace', '--script', str(script)],
                    cwd=PROJECT, env=env, capture_output=True, text=True, timeout=5, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertIn('226', result.stdout)
                self.assertNotIn('lab-test', result.stdout + result.stderr)
                # Keep a session open: shutdown must close it without waiting 30 seconds.
                with ftplib.FTP() as ftp:
                    ftp.connect('127.0.0.1', port, timeout=3)
                    ftp.login('student', 'lab-test')
                    process.terminate()
                    _stdout, stderr = process.communicate(timeout=3)
                    self.assertEqual(process.returncode, 0, stderr)
                    self.assertNotIn('Traceback', stderr)
                    self.assertNotIn('lab-test', stderr)
                    ftp.close()
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)

    def test_missing_password_and_invalid_options_fail_cleanly(self):
        env = dict(os.environ)
        env.pop('FTP_PASSWORD', None)
        for module in ('server', 'client'):
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, '-m', 'ftp_lab.' + module], cwd=PROJECT,
                    env=env, input='', text=True, capture_output=True, timeout=3, check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn('FTP_PASSWORD', result.stderr)
                self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
