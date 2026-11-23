"""Exercise database persistence and authenticated HTTP transfer workflows."""

import gzip
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

from support import running_server

from ftp_lab.client import FTPClient
from ftp_lab.webapp.database import Database
from ftp_lab.webapp.server import AppServer
from ftp_lab.webapp.service import AppService


class DatabaseTests(unittest.TestCase):
    """Account, session, and device persistence tests."""

    def test_password_session_and_device_persistence_without_plaintext_secrets(self):
        """Verify password session and device persistence without plaintext secrets."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'app.db'
            db = Database(path)
            user = db.setup('owner', 'a-long-app-password')
            logged_in = db.login('owner', 'a-long-app-password')
            if logged_in is None:
                self.fail('Owner could not sign in')
            self.assertEqual(logged_in['id'], user['id'])
            self.assertIsNone(db.login('owner', 'wrong password'))
            token = db.create_session(user['id'])
            session = db.session(token)
            if session is None:
                self.fail('Session was not persisted')
            self.assertEqual(session['username'], 'owner')
            self.assertIsNone(db.session(db.create_session(user['id'], ttl=-1)))
            saved = db.add_device(
                user['id'],
                {'name': 'Lab PC', 'host': '127.0.0.1', 'port': 2121, 'username': 'student'},
            )
            self.assertEqual(Database(path).devices(user['id'])[0]['name'], 'Lab PC')
            self.assertIsNone(db.device(999, saved['id']))
            db.delete_session(token)
            self.assertIsNone(db.session(token))
            self.assertNotIn(b'a-long-app-password', path.read_bytes())
            self.assertNotIn(token.encode(), path.read_bytes())
            with self.assertRaises(ValueError):
                db.setup('second', 'another-password')


class AppTests(unittest.TestCase):
    """Authenticated browser API and background transfer integration tests."""

    def setUp(self):
        # unittest exits this context after the per-test server and socket cleanups.
        # pylint: disable-next=consider-using-with
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.service = AppService(Path(self.temp))
        self.server = AppServer(('127.0.0.1', 0), self.service)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={'poll_interval': 0.01}
        )
        self.thread.start()
        self.addCleanup(self.close_server)
        self.cookie = ''
        self.csrf = ''

    def close_server(self):
        """Stop HTTP and FTP workers and release their sockets."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.service.close()

    # Plain HTTP is intentional for this trusted LAN lab.
    # noinspection HttpUrlsUsage
    def request(
        self, method, path, data=None, *, raw=None, headers=None
    ) -> tuple[int, Any, dict[str, str]]:
        """Issue an HTTP request while retaining cookies for the next request."""
        host, port = self.server.address
        common = {'Origin': f'http://{host}:{port}', 'X-FTP-App': '1', 'X-CSRF-Token': self.csrf}
        if self.cookie:
            common['Cookie'] = self.cookie
        if raw is None and data is not None:
            raw = json.dumps(data).encode()
            common['Content-Type'] = 'application/json'
        common.update(headers or {})
        with closing(http.client.HTTPConnection(host, port, timeout=10)) as conn:
            conn.request(method, path, body=raw, headers=common)
            response = conn.getresponse()
            payload = response.read()
            cookie = response.getheader('Set-Cookie')
            if cookie:
                self.cookie = cookie.split(';', 1)[0]
            result = (
                json.loads(payload)
                if response.getheader('Content-Type', '').startswith('application/json')
                else payload
            )
            return response.status, result, dict(response.getheaders())

    def login(self):
        """Create the workspace owner and retain its authenticated session."""
        status, result, headers = self.request(
            'POST', '/api/setup', {'username': 'owner', 'password': 'app-password'}
        )
        self.assertEqual(status, 200, result)
        self.csrf = result['csrf']
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])

    def device(self, ftp_server):
        """Register a loopback FTP server as a saved device."""
        host, port = ftp_server.address
        status, body, _ = self.request(
            'POST',
            '/api/devices',
            {'name': 'Test PC', 'host': host, 'port': port, 'username': 'student'},
        )
        self.assertEqual(status, 201, body)
        device = body['device']
        status, body, _ = self.request(
            'POST', f'/api/devices/{device["id"]}/connect', {'password': 'lab-test'}
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body['device']['ready'])
        return device

    def wait_job(self, job):
        """Poll a transfer job until it finishes or the test deadline expires."""
        deadline = time.monotonic() + 10
        while True:
            status, body, _ = self.request('GET', f'/api/jobs/{job["id"]}')
            self.assertEqual(status, 200, body)
            current = body['job']
            if current['status'] in ('completed', 'failed', 'cancelled'):
                return current
            self.assertLess(time.monotonic(), deadline, current)
            time.sleep(0.02)

    def test_authentication_origin_csrf_logout_and_static_assets(self):
        """Verify authentication origin csrf logout and static assets."""
        self.assertEqual(self.request('GET', '/api/jobs')[0], 401)
        self.assertTrue(self.request('GET', '/api/session')[1]['setup_required'])
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertEqual(self.request('GET', '/static/app.js')[0], 200)
        self.assertEqual(self.request('GET', '/static/../../database.py')[0], 404)
        self.login()
        self.assertEqual(
            self.request('POST', '/api/devices', {}, headers={'X-CSRF-Token': 'wrong'})[0], 403
        )
        self.assertEqual(
            self.request('POST', '/api/logout', {}, headers={'Origin': 'https://evil.example'})[0],
            403,
        )
        self.assertEqual(
            self.request('GET', '/api/session', headers={'Host': 'evil.example'})[0], 403
        )
        self.assertEqual(self.request('POST', '/api/logout', {})[0], 200)
        self.assertEqual(self.request('GET', '/api/jobs')[0], 401)
        self.assertEqual(
            self.request('POST', '/api/login', {'username': 'owner', 'password': 'wrong'})[0], 401
        )
        status, result, _ = self.request(
            'POST', '/api/login', {'username': 'owner', 'password': 'app-password'}
        )
        self.assertEqual(status, 200, result)

    # Independent evidence and plot panels intentionally repeat these examples.
    # noinspection DuplicatedCode
    def test_streamed_upload_download_compression_and_persistent_history(self):
        """Verify streamed upload download compression and persistent history."""
        # Verify the complete upload, download, compression, and restart workflow.
        # pylint: disable=too-many-locals
        self.login()
        with running_server(timeout=10) as (ftp, root):
            device = self.device(ftp)
            payload = bytes(range(256)) * 1025
            for compressed in (False, True):
                status, body, _ = self.request(
                    'POST',
                    '/api/jobs',
                    {
                        'device_id': device['id'],
                        'kind': 'upload',
                        'name': 'sample.wav',
                        'size': len(payload),
                        'remote_path': '/',
                        'compress': compressed,
                    },
                )
                self.assertEqual(status, 201, body)
                job = body['job']
                self.assertEqual(
                    self.request('PUT', f'/api/jobs/{job["id"]}/source', raw=payload)[0], 200
                )
                done = self.wait_job(job)
                self.assertEqual(done['status'], 'completed', done)
                self.assertEqual(done['percent'], 100)
                self.assertGreater(done['speed_bps'], 0)
                name = 'sample.wav.gz' if compressed else 'sample.wav'
                received = (root / name).read_bytes()
                self.assertEqual(gzip.decompress(received) if compressed else received, payload)
                status, body, _ = self.request(
                    'POST',
                    '/api/jobs',
                    {
                        'device_id': device['id'],
                        'kind': 'download',
                        'name': name,
                        'remote_path': '/' + name,
                        'size': len(received),
                        'unpack': compressed,
                    },
                )
                self.assertEqual(status, 201, body)
                result = self.wait_job(body['job'])
                self.assertEqual(result['status'], 'completed', result)
                status, attachment, headers = self.request(
                    'GET', f'/api/jobs/{result["id"]}/download'
                )
                self.assertEqual(status, 200)
                self.assertEqual(attachment, payload)
                self.assertIn('attachment', headers['Content-Disposition'])
                self.assertEqual(result['result']['filename'], 'sample.wav')
                self.assertEqual(
                    headers['Content-Disposition'], "attachment; filename*=UTF-8''sample.wav"
                )
            self.assertEqual(
                self.request('GET', f'/api/devices/{device["id"]}/files?path=/')[0], 200
            )
            jobs = self.request('GET', '/api/jobs')[1]['jobs']
            self.assertEqual(len(jobs), 4)
            reopened = Database(self.service.data_dir / 'app.db')
            self.assertEqual(len(reopened.jobs(1)), 4)
            for path in self.service.data_dir.glob('app.db*'):
                self.assertNotIn(b'lab-test', path.read_bytes())

    def test_invalid_source_length_name_and_job_access_are_rejected(self):
        """Verify invalid source length name and job access are rejected."""
        self.login()
        with running_server(timeout=10) as (ftp, _root):
            device = self.device(ftp)
            data = {
                'device_id': device['id'],
                'kind': 'upload',
                'name': '../escape',
                'size': 3,
                'remote_path': '/',
            }
            self.assertEqual(self.request('POST', '/api/jobs', data)[0], 400)
            data['name'] = 'valid'
            job = self.request('POST', '/api/jobs', data)[1]['job']
            self.assertEqual(
                self.request('PUT', f'/api/jobs/{job["id"]}/source', raw=b'too much')[0], 400
            )
            self.assertEqual(self.request('GET', '/api/jobs/not-a-job')[0], 404)
            self.assertEqual(self.request('POST', f'/api/jobs/{job["id"]}/cancel', {})[0], 200)
            self.assertEqual(self.wait_job(job)['status'], 'cancelled')

    def test_invalid_gzip_downloads_fail_and_remove_partial_artifacts(self):
        """Verify invalid gzip downloads fail and remove partial artifacts."""
        self.login()
        valid = gzip.compress(b'original contents')
        invalid_deflate = bytes.fromhex('1f8b08000000000000ff07') + bytes(8)
        with running_server(timeout=10) as (ftp, root):
            device = self.device(ftp)
            for content in (b'not gzip', valid[:-4], invalid_deflate):
                with self.subTest(content=content):
                    (root / 'broken.txt.gz').write_bytes(content)
                    status, body, _ = self.request(
                        'POST',
                        '/api/jobs',
                        {
                            'device_id': device['id'],
                            'kind': 'download',
                            'name': 'broken.txt.gz',
                            'remote_path': '/broken.txt.gz',
                            'size': len(content),
                            'unpack': True,
                        },
                    )
                    self.assertEqual(status, 201, body)
                    job = self.wait_job(body['job'])
                    self.assertEqual(job['status'], 'failed', job)
                    self.assertIn('gzip', job['error'].lower())
                    self.assertFalse((self.service.spool / job['id']).exists())
                    self.assertNotEqual(
                        self.request('GET', f'/api/jobs/{job["id"]}/download')[0], 200
                    )

    # Independent evidence and plot panels intentionally repeat these examples.
    # noinspection DuplicatedCode
    def test_benchmark_api_verifies_both_modes_and_exports_real_metrics(self):
        """Verify benchmark api verifies both modes and exports real metrics."""
        self.login()
        with running_server(timeout=10) as (ftp, root):
            device = self.device(ftp)
            payload = b'media test\x00\xff' * 8192
            status, body, _ = self.request(
                'POST',
                '/api/jobs',
                {
                    'device_id': device['id'],
                    'kind': 'benchmark',
                    'name': 'clip.wav',
                    'size': len(payload),
                    'remote_path': '/',
                },
            )
            self.assertEqual(status, 201, body)
            job = body['job']
            self.assertEqual(
                self.request('PUT', f'/api/jobs/{job["id"]}/source', raw=payload)[0], 200
            )
            completed = self.wait_job(job)
            self.assertEqual(completed['status'], 'completed', completed)
            self.assertEqual([r['mode'] for r in completed['result']['runs']], ['plain', 'gzip'])
            self.assertTrue(all(r['verified'] for r in completed['result']['runs']))
            self.assertEqual(completed['result']['source_name'], 'clip.wav')
            self.assertLess(completed['result']['runs'][1]['payload_bytes'], len(payload))
            self.assertEqual(completed['total'], completed['result']['runs'][1]['payload_bytes'])
            self.assertEqual(completed['done'], completed['total'])
            self.assertNotIn('payload_total', completed)
            csv_status, report, _ = self.request('GET', f'/api/jobs/{job["id"]}/report.csv')
            self.assertEqual(csv_status, 200)
            self.assertIn(b'throughput_mib_s', report)
            self.assertEqual(list(root.iterdir()), [])

    def test_cancelled_queued_job_cannot_be_deleted_while_worker_still_runs(self):
        """Verify canceled queued job cannot be deleted while worker still runs."""
        self.login()
        with running_server(timeout=10) as (ftp, _root):
            device = self.device(ftp)
            # Occupy both worker slots without mocking FTP behavior.
            release = threading.Event()
            entered = [threading.Event(), threading.Event()]

            def block(index):
                entered[index].set()
                release.wait(3)

            for i in range(2):
                self.service.pool.submit(block, i)
            self.assertTrue(all(event.wait(1) for event in entered))
            try:
                body = self.request(
                    'POST',
                    '/api/jobs',
                    {
                        'device_id': device['id'],
                        'kind': 'download',
                        'name': 'absent',
                        'size': 0,
                        'remote_path': '/absent',
                    },
                )[1]
                job_id = body['job']['id']
                self.request('POST', f'/api/jobs/{job_id}/cancel', {})
                self.assertEqual(self.request('DELETE', f'/api/jobs/{job_id}')[0], 200)
            finally:
                release.set()
            self.assertEqual(self.request('GET', f'/api/jobs/{job_id}')[0], 404)

    def test_only_one_source_request_can_claim_a_job(self):
        """Verify only one source request can claim a job."""
        self.login()
        with running_server(timeout=10) as (ftp, root):
            device = self.device(ftp)
            job = self.request(
                'POST',
                '/api/jobs',
                {
                    'device_id': device['id'],
                    'kind': 'upload',
                    'name': 'once',
                    'size': 3,
                    'remote_path': '/',
                },
            )[1]['job']
            token = self.cookie.partition('=')[2]
            entered, release = threading.Event(), threading.Event()

            class BlockingSource:
                """Hold a source read until the concurrent request check finishes."""

                # This source fixture implements only the read method needed by the worker.
                # pylint: disable=too-few-public-methods

                @staticmethod
                def read(_amount):
                    """Block until the concurrent claim attempt has completed."""
                    entered.set()
                    release.wait(3)
                    return b'one'

            result = []

            def receive():
                result.append(self.service.receive_source(1, token, job['id'], BlockingSource(), 3))

            worker = threading.Thread(target=receive)
            worker.start()
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaises(ValueError):
                    self.service.receive_source(1, token, job['id'], io.BytesIO(b'two'), 3)
            finally:
                release.set()
                worker.join(3)
            done = self.wait_job(job)
            self.assertEqual(done['status'], 'completed', done)
            self.assertEqual((root / 'once').read_bytes(), b'one')

    def test_download_meter_rejects_bytes_beyond_device_size(self):
        """Verify download meter rejects bytes beyond device size."""
        self.login()
        with running_server(timeout=10) as (ftp, _root):
            device = self.device(ftp)
            job = self.request(
                'POST',
                '/api/jobs',
                {
                    'device_id': device['id'],
                    'kind': 'upload',
                    'name': 'file',
                    'size': 8,
                    'remote_path': '/',
                },
            )[1]['job']
            # Test the internal guard before a chunk can be written.
            meter = self.service._meter(1, job['id'], 8)  # pylint: disable=protected-access
            meter(8)
            with self.assertRaisesRegex(ValueError, 'size'):
                meter(1)

    def test_short_download_is_not_published_as_complete(self):
        """Verify short download is not published as complete."""
        self.login()
        with running_server(timeout=10) as (ftp, root):
            (root / 'changing.txt').write_bytes(b'short')
            device = self.device(ftp)
            # The remote file can shrink between SIZE and RETR, while RETR
            # still finishes with a normal EOF and a successful FTP reply.
            with patch.object(FTPClient, 'size', return_value=8):
                status, body, _ = self.request(
                    'POST',
                    '/api/jobs',
                    {
                        'device_id': device['id'],
                        'kind': 'download',
                        'name': 'changing.txt',
                        'size': 8,
                        'remote_path': '/changing.txt',
                    },
                )
                self.assertEqual(status, 201, body)
                job = self.wait_job(body['job'])
            self.assertEqual(job['status'], 'failed', job)
            self.assertIn('size', job['error'].lower())
            self.assertNotEqual(self.request('GET', f'/api/jobs/{job["id"]}/download')[0], 200)
            self.assertFalse((self.service.spool / job['id']).exists())

    def test_invalid_unpacked_basename_is_rejected_before_download(self):
        """Verify invalid unpacked basename is rejected before download."""
        self.login()
        with running_server(timeout=10) as (ftp, _root):
            device = self.device(ftp)
            for name in ('.gz', '..gz', '...gz'):
                with self.subTest(name=name):
                    status, body, _ = self.request(
                        'POST',
                        '/api/jobs',
                        {
                            'device_id': device['id'],
                            'kind': 'download',
                            'name': name,
                            'size': 1,
                            'remote_path': '/' + name,
                            'unpack': True,
                        },
                    )
                    self.assertEqual(status, 400, body)


if __name__ == '__main__':
    unittest.main()
