"""Real HTTP/FTP tests for one workspace accessed by independent browsers."""

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

from ftp_lab.webapp.server import AppServer
from ftp_lab.webapp.service import AppService
from support import running_server


class BrowserSession:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.cookie = ''
        self.csrf = ''

    def request(self, method, path, data=None, *, raw=None, headers=None) -> tuple[int, Any]:
        values = {'Origin': f'http://{self.host}:{self.port}', 'X-FTP-App': '1',
                  'X-CSRF-Token': self.csrf, 'Cookie': self.cookie}
        if data is not None:
            raw = json.dumps(data).encode()
            values['Content-Type'] = 'application/json'
        values.update(headers or {})
        with closing(http.client.HTTPConnection(self.host, self.port, timeout=10)) as connection:
            connection.request(method, path, body=raw, headers=values)
            response = connection.getresponse()
            body = response.read()
            cookie = response.getheader('Set-Cookie')
            if cookie is not None:
                self.cookie = cookie.split(';', 1)[0]
            result = json.loads(body) if response.getheader('Content-Type', '').startswith('application/json') else body
            if isinstance(result, dict) and 'csrf' in result:
                self.csrf = result['csrf']
            return response.status, result


class NetworkAppTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.service = AppService(Path(self.folder.name), demo=True)
        self.addCleanup(self.service.close)
        try:
            self.server = AppServer(('0.0.0.0', 0), self.service)
        except ValueError as exc:
            self.fail(f'The browser app must support binding to network interfaces: {exc}')
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.port = self.server.address[1]
        self.first = BrowserSession('127.0.0.1', self.port)
        self.second = BrowserSession('127.0.0.2', self.port)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def setup_owner(self):
        status, value = self.first.request('POST', '/api/setup', {'username': 'owner', 'password': 'network-test-password'})
        self.assertEqual(status, 200, value)

    def sign_in(self, browser):
        status, value = browser.request('POST', '/api/login', {'username': 'owner', 'password': 'network-test-password'})
        self.assertEqual(status, 200, value)

    def wait_job(self, browser, job):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, value = browser.request('GET', f'/api/jobs/{job["id"]}')
            self.assertEqual(status, 200, value)
            job = value['job']
            if job['status'] in ('completed', 'failed', 'cancelled'):
                return job
            time.sleep(0.01)
        self.fail(f'Transfer did not finish: {job}')

    def test_network_binding_accepts_destination_ip_but_rejects_forged_hosts(self):
        for browser in (self.first, self.second):
            self.assertEqual(browser.request('GET', '/')[0], 200)
            self.assertEqual(browser.request('GET', '/api/jobs')[0], 401)
            for host in (f'attacker.test:{self.port}', f'192.0.2.99:{self.port}', '127.0.0.1:1'):
                self.assertEqual(browser.request('GET', '/api/session', headers={'Host': host})[0], 403)
        self.assertEqual(self.first.request('GET', '/', headers={'Host': f'localhost:{self.port}'})[0], 200)

    def test_mutations_require_exact_request_origin_and_session_csrf(self):
        self.setup_owner()
        for headers in ({'Origin': f'http://127.0.0.2:{self.port}'},
                        {'Origin': 'http://attacker.test'}, {'X-CSRF-Token': ''}, {'X-FTP-App': ''}):
            self.assertEqual(self.first.request('POST', '/api/logout', {}, headers=headers)[0], 403)
        self.assertEqual(self.first.request('GET', '/api/jobs')[0], 200)

    def test_independent_browsers_share_profiles_and_files_but_keep_sessions_separate(self):
        self.setup_owner()
        self.sign_in(self.second)
        self.assertNotEqual(self.first.cookie, self.second.cookie)
        self.assertNotEqual(self.first.csrf, self.second.csrf)
        status, value = self.first.request('POST', '/api/devices',
            {'name': 'Saved LAN PC', 'host': '192.0.2.10', 'port': 2121, 'username': 'student'})
        self.assertEqual(status, 201, value)
        status, value = self.second.request('GET', '/api/devices')
        self.assertEqual(status, 200, value)
        self.assertIn('Saved LAN PC', [device['name'] for device in value['devices']])
        device = next(device for device in value['devices'] if device['local'])
        self.assertEqual(self.first.request('GET', '/api/devices')[0], 200)
        payload = bytes(range(256)) * 4097
        status, value = self.first.request('POST', '/api/jobs', {'device_id': device['id'],
            'kind': 'upload', 'name': 'shared-video.dat', 'size': len(payload), 'remote_path': '/'})
        self.assertEqual(status, 201, value)
        job = value['job']
        self.assertEqual(self.first.request('PUT', f'/api/jobs/{job["id"]}/source', raw=payload)[0], 200)
        self.assertEqual(self.wait_job(self.second, job)['status'], 'completed')
        status, value = self.second.request('POST', '/api/jobs', {'device_id': device['id'],
            'kind': 'download', 'name': 'shared-video.dat', 'remote_path': '/shared-video.dat'})
        self.assertEqual(status, 201, value)
        job = self.wait_job(self.second, value['job'])
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(self.second.request('GET', f'/api/jobs/{job["id"]}/download'), (200, payload))
        self.assertEqual(self.first.request('POST', '/api/logout', {})[0], 200)
        self.assertEqual(self.first.request('GET', '/api/jobs')[0], 401)
        self.assertEqual(self.second.request('GET', '/api/jobs')[0], 200)

    def test_external_device_password_is_not_shared_between_browsers(self):
        self.setup_owner()
        self.sign_in(self.second)
        with running_server() as (ftp, _root):
            status, value = self.first.request('POST', '/api/devices', {'name': 'Private credentials',
                'host': ftp.address[0], 'port': ftp.address[1], 'username': 'student'})
            self.assertEqual(status, 201, value)
            device_id = value['device']['id']
            self.assertEqual(self.first.request('POST', f'/api/devices/{device_id}/connect', {'password': 'lab-test'})[0], 200)
            first_devices = self.first.request('GET', '/api/devices')[1]['devices']
            second_devices = self.second.request('GET', '/api/devices')[1]['devices']
            self.assertTrue(next(d for d in first_devices if d['id'] == device_id)['ready'])
            self.assertFalse(next(d for d in second_devices if d['id'] == device_id)['ready'])

    def test_concurrent_first_device_listing_creates_one_shared_host_profile(self):
        self.setup_owner()
        browsers = [self.first] + [BrowserSession('127.0.0.2', self.port) for _ in range(3)]
        for browser in browsers[1:]:
            self.sign_in(browser)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda browser: browser.request('GET', '/api/devices'), browsers))
        for status, value in results:
            self.assertEqual(status, 200, value)
            self.assertEqual(len([d for d in value['devices'] if d['local']]), 1, value)
        self.assertEqual(len([d for d in self.service.db.devices(1) if d['local']]), 1)

    def test_specific_lan_bind_requires_owner_bootstrap_before_listening(self):
        command = [sys.executable, '-m', 'ftp_lab.webapp', '--host', '192.0.2.10',
                   '--port', '0', '--data-dir', str(Path(self.folder.name) / 'fresh-bind')]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Create the owner account', result.stderr)
        self.assertIn('--host 127.0.0.1', result.stderr)

    @unittest.skipUnless(os.environ.get('FTP_TEST_LAN_HOST'), 'Set FTP_TEST_LAN_HOST to exercise a real LAN interface')
    def test_lan_interface_blocks_owner_claim_but_allows_authenticated_access(self):
        remote = BrowserSession(os.environ['FTP_TEST_LAN_HOST'], self.port)
        status, session = remote.request('GET', '/api/session')
        self.assertEqual(status, 200, session)
        self.assertTrue(session['setup_required'])
        self.assertFalse(session['setup_allowed'])
        self.assertEqual(remote.request('POST', '/api/setup', {'username': 'remote', 'password': 'uninvited-claim'})[0], 403)
        self.setup_owner()
        self.sign_in(remote)
        self.assertEqual(remote.request('GET', '/api/devices')[0], 200)
        self.assertEqual(remote.request('GET', '/api/jobs')[0], 200)


if __name__ == '__main__':
    unittest.main()
