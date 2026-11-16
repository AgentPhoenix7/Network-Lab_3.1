"""Bounded background transfers and session-memory FTP credentials."""

import copy
import gzip
import os
import posixpath
import secrets
import shutil
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..client import FTPClient, FTPError
from ..server import FTPServer
from .database import Database, now, token_hash

MAX_BYTES = 8 * 1024 ** 3
CHUNK = 1024 * 1024
TERMINAL = {'completed', 'failed', 'cancelled'}


class Cancelled(Exception):
    pass


class NotFound(Exception):
    pass


def filename(value):
    if not isinstance(value, str) or not value or value in ('.', '..') or '/' in value or '\\' in value:
        raise ValueError('Choose a valid filename without directory components')
    if len(value.encode()) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Filename is too long or contains control characters')
    return value


def remote_path(value):
    if not isinstance(value, str) or not value.startswith('/') or len(value.encode()) > 4096:
        raise ValueError('Choose an absolute remote folder or file path')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid remote path')
    return value


class AppService:
    def __init__(self, data_dir: Path, *, demo=False):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = Database(self.data_dir / 'app.db')
        self.spool = self.data_dir / 'transfers'
        self.spool.mkdir(exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.credentials: dict[tuple[str, int], tuple[str, float]] = {}
        self.live: dict[str, dict[str, Any]] = {}
        self.events: dict[str, threading.Event] = {}
        self.futures: dict[str, Future[None]] = {}
        self.owners: dict[str, tuple[int, str]] = {}
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ftp-app')
        self.demo_server = None
        self.demo_thread = None
        self.demo_password = secrets.token_urlsafe(24)
        self.closed = False
        for owner, job in self.db.all_jobs():
            if job['status'] not in TERMINAL:
                job.update(status='failed', phase='Interrupted', error='The app restarted before this transfer finished.')
                self.db.save_job(owner, job)
                shutil.rmtree(self.spool / job['id'], ignore_errors=True)
        if demo:
            shared = self.data_dir / 'shared'
            shared.mkdir(exist_ok=True)
            welcome = shared / 'Welcome.txt'
            if not welcome.exists():
                welcome.write_text('Your local FTP device is ready. Upload a file or download this one.\n')
            self.demo_server = FTPServer(('127.0.0.1', 0), shared, 'local', self.demo_password, timeout=60)
            self.demo_thread = threading.Thread(target=self.demo_server.serve_forever, kwargs={'poll_interval': 0.05})
            self.demo_thread.start()

    def close(self):
        self.closed = True
        with self.lock:
            for event in self.events.values():
                event.set()
            self.credentials.clear()
        self.pool.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            for job_id, job in self.live.items():
                if job['status'] not in TERMINAL:
                    self._update(self.owners[job_id][0], job_id, status='cancelled', phase='Cancelled', error='App closed')
                    shutil.rmtree(self.spool / job_id, ignore_errors=True)
        if self.demo_server is not None:
            self.demo_server.shutdown()
            self.demo_server.server_close()
        if self.demo_thread is not None:
            self.demo_thread.join(5)

    def logout(self, token):
        digest = token_hash(token)
        with self.lock:
            self.credentials = {key: value for key, value in self.credentials.items() if key[0] != digest}
            for job_id, (_owner, session) in self.owners.items():
                if session == digest:
                    self.events[job_id].set()

    def _device(self, owner, device_id) -> dict[str, Any]:
        if isinstance(device_id, bool) or not isinstance(device_id, int):
            raise TypeError('Choose a saved device')
        device = self.db.device(owner, device_id)
        if device is None:
            raise NotFound('Device not found')
        return device

    def devices(self, owner, token):
        if self.demo_server is not None:
            with self.lock:
                device = self.db.local_device(owner, self.demo_server.address)
                self._remember(token, device['id'], self.demo_password)
        result = []
        for device in self.db.devices(owner):
            device.pop('user_id', None)
            device['ready'] = self._password(token, device['id']) is not None
            device['local'] = bool(device['local'])
            result.append(device)
        return result

    def add_device(self, owner, data):
        for key in ('name', 'host', 'username'):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 253 or any(ord(c) < 32 for c in value):
                raise ValueError(f'Enter a valid device {key}')
        if any(c in data['host'] for c in '/\\:@ '):
            raise ValueError('Enter an IPv4 address or hostname without a URL or port')
        port = data.get('port')
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        clean = {key: data[key].strip() for key in ('name', 'host', 'username')}
        clean['port'] = port
        device = self.db.add_device(owner, clean)
        device.pop('user_id', None)
        device.update(ready=False, local=False)
        return device

    def _remember(self, token, device_id, password):
        with self.lock:
            self.credentials[(token_hash(token), device_id)] = (password, time.time() + 8 * 3600)

    def _password(self, token, device_id):
        with self.lock:
            self.credentials = {key: value for key, value in self.credentials.items() if value[1] > time.time()}
            entry = self.credentials.get((token_hash(token), device_id))
            return entry[0] if entry else None

    @contextmanager
    def client(self, owner, token, device_id, *, password=None):
        device = self._device(owner, device_id)
        password = password if password is not None else self._password(token, device_id)
        if password is None:
            raise ValueError('Connect this device again to enter its password')
        with FTPClient(device['host'], device['port'], timeout=15) as client:
            client.login(device['username'], password)
            yield client

    def connect_device(self, owner, token, device_id, password):
        device = self._device(owner, device_id)
        if device['local'] and self.demo_server is not None:
            password = self.demo_password
        if not isinstance(password, str) or not password or len(password) > 256:
            raise ValueError('Enter the FTP device password')
        with self.client(owner, token, device_id, password=password) as client:
            client.pwd()
        self._remember(token, device_id, password)
        self.db.touch_device(owner, device_id)
        return next(d for d in self.devices(owner, token) if d['id'] == device_id)

    def delete_device(self, owner, device_id):
        device = self._device(owner, device_id)
        if device['local']:
            raise ValueError('The built-in local device is managed by the app')
        if any(j['device_id'] == device_id and j['status'] not in TERMINAL for j in self.jobs(owner)):
            raise ValueError('Finish or cancel this device\'s transfers first')
        self.db.delete_device(owner, device_id)
        with self.lock:
            self.credentials = {key: value for key, value in self.credentials.items() if key[1] != device_id}

    def files(self, owner, token, device_id, path):
        path = remote_path(path)
        with self.client(owner, token, device_id) as client:
            client.command('CWD', path)
            actual = client.pwd()
            names = client.list(names_only=True)
            entries = []
            for name in names[:500]:
                try:
                    filename(name)
                    client.command('CWD', posixpath.join(actual, name))
                except ValueError:
                    continue
                except FTPError:
                    size = client.size(posixpath.join(actual, name))
                    entries.append({'name': name, 'directory': False, 'size': size})
                else:
                    entries.append({'name': name, 'directory': True, 'size': 0})
                    client.command('CWD', actual)
            return {'path': actual, 'entries': sorted(entries, key=lambda e: (not e['directory'], e['name'].lower()))}

    def jobs(self, owner):
        result = self.db.jobs(owner)
        with self.lock:
            for index, job in enumerate(result):
                current = self.live.get(job['id'])
                if current is not None:
                    result[index] = copy.deepcopy(current)
        return result

    def job(self, owner, job_id) -> dict[str, Any]:
        if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
            raise NotFound('Transfer not found')
        stored = self.db.job(owner, job_id)
        if stored is None:
            raise NotFound('Transfer not found')
        with self.lock:
            current = self.live.get(job_id)
            if current is not None:
                return copy.deepcopy(current)
            return stored

    def create_job(self, owner, token, data):
        if self.closed:
            raise ValueError('The app is shutting down')
        device_id = data.get('device_id')
        device = self._device(owner, device_id)
        if self._password(token, device_id) is None:
            raise ValueError('Connect this device before starting a transfer')
        if sum(j['status'] not in TERMINAL for j in self.jobs(owner)) >= 8:
            raise ValueError('There are already 8 pending transfers. Let one finish first.')
        kind = data.get('kind')
        if kind not in ('upload', 'download', 'benchmark'):
            raise ValueError('Unknown transfer type')
        name = filename(data.get('name'))
        size = data.get('size', 0)
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_BYTES:
            raise ValueError('Choose a file no larger than 8 GiB')
        path = remote_path(data.get('remote_path', '/'))
        if kind == 'download' and data.get('unpack'):
            if not name.lower().endswith('.gz'):
                raise ValueError('Only gzip files can be unpacked')
            filename(name[:-3])
        if data.get('compress') and len((name + '.gz').encode()) > 255:
            raise ValueError('Compressed filename is too long')
        job_id = uuid.uuid4().hex
        job = {'id': job_id, 'device_id': device_id, 'device_name': device['name'], 'kind': kind, 'name': name,
                   'size': size, 'remote_path': path, 'compress': bool(data.get('compress')), 'unpack': bool(data.get('unpack')),
                   'status': 'queued' if kind == 'download' else 'awaiting_upload', 'phase': 'Waiting for file',
                   'done': 0, 'total': size, 'percent': 0, 'speed_bps': 0, 'eta_seconds': None, 'elapsed_seconds': 0,
                   'created': now(), 'error': '', 'result': {}, 'samples': []}
        with self.lock:
            if self.closed or sum(j['status'] not in TERMINAL for j in self.jobs(owner)) >= 8:
                raise ValueError('There are already 8 pending transfers, or the app is closing')
            (self.spool / job_id).mkdir(mode=0o700)
            self.live[job_id] = job
            self.events[job_id] = threading.Event()
            self.owners[job_id] = (owner, token_hash(token))
            self.db.save_job(owner, job)
            if kind == 'download':
                self._queue(owner, token, job_id)
        return self.job(owner, job_id)

    def _update(self, owner, job_id, *, persist=True, **values):
        with self.lock:
            self.live[job_id].update(values)
            if persist:
                self.db.save_job(owner, self.live[job_id])

    def check_cancel(self, job_id):
        if self.closed or self.events[job_id].is_set():
            raise Cancelled('Transfer cancelled')

    def receive_source(self, owner, token, job_id, stream, length):
        with self.lock:
            job = self.job(owner, job_id)
            if job['status'] != 'awaiting_upload' or job['kind'] == 'download':
                raise ValueError('This transfer is not waiting for a file')
            if length != job['size']:
                raise ValueError('Uploaded file size does not match the selected file')
            self._update(owner, job_id, status='running', phase='Preparing file')
        done = 0
        last = time.monotonic()
        try:
            with (self.spool / job_id / 'source').open('wb') as out:
                while done < length:
                    self.check_cancel(job_id)
                    block = stream.read(min(CHUNK, length - done))
                    if not block:
                        raise ValueError('File upload ended early. Choose the file again.')
                    out.write(block)
                    done += len(block)
                    if time.monotonic() - last >= 0.2:
                        self._update(owner, job_id, persist=False, done=done, percent=100 * done / max(1, length))
                        last = time.monotonic()
            self.check_cancel(job_id)
            self._queue(owner, token, job_id)
        except (OSError, ValueError, Cancelled) as exc:
            status = 'cancelled' if isinstance(exc, Cancelled) else 'failed'
            self._update(owner, job_id, status=status, phase=status.title(), error=str(exc))
            shutil.rmtree(self.spool / job_id, ignore_errors=True)
            raise
        return self.job(owner, job_id)

    def _queue(self, owner, token, job_id):
        with self.lock:
            self.check_cancel(job_id)
            self._update(owner, job_id, status='queued', phase='Queued', done=0, percent=0)
            self.futures[job_id] = self.pool.submit(self._work, owner, token, job_id)

    def _meter(self, owner, job_id, total, phase='Transferring', mode='plain'):
        started = time.monotonic()
        previous, previous_time, saved = 0, started, started
        done = 0

        def advance(delta):
            nonlocal done, previous, previous_time, saved
            self.check_cancel(job_id)
            done += delta
            if done > total or done > MAX_BYTES:
                raise ValueError('Device sent more data than its reported file size')
            elapsed = time.monotonic() - started
            speed = done / max(elapsed, 1e-9)
            timestamp = time.monotonic()
            if timestamp - saved >= 0.2 or done >= total:
                interval_speed = (done - previous) / max(timestamp - previous_time, 1e-9)
                with self.lock:
                    samples = self.live[job_id]['samples']
                    samples.append({'elapsed': elapsed, 'bytes': done, 'speed_bps': interval_speed, 'mode': mode})
                    del samples[:-2000]
                self._update(owner, job_id, phase=phase, done=done, total=total,
                             percent=min(99, 100 * done / max(total, 1)), speed_bps=speed,
                             eta_seconds=max(0, total - done) / speed if speed else None,
                             elapsed_seconds=elapsed)
                previous, previous_time, saved = done, timestamp, timestamp

        self._update(owner, job_id, phase=phase, done=0, total=total, percent=0,
                     samples=[{'elapsed': 0, 'bytes': 0, 'speed_bps': 0, 'mode': mode}])
        return advance

    def _work(self, owner, token, job_id):
        folder = self.spool / job_id
        job = self.job(owner, job_id)
        started = time.monotonic()
        password = self._password(token, job['device_id']) or ''
        success = False
        try:
            self.check_cancel(job_id)
            self._update(owner, job_id, status='running', phase='Connecting')
            if job['kind'] == 'benchmark':
                from ..benchmark import run_benchmark

                def factory():
                    return self.client(owner, token, job['device_id'])

                def measured(mode, done, total, elapsed):
                    self.check_cancel(job_id)
                    with self.lock:
                        samples = self.live[job_id]['samples']
                        samples.append({'elapsed': elapsed, 'bytes': done, 'speed_bps': done / max(elapsed, 1e-9), 'mode': mode})
                        del samples[:-2000]
                    self._update(owner, job_id, persist=False, phase='Comparing compression', done=done, total=total,
                                 percent=min(99, (50 if mode == 'gzip' else 0) + 45 * done / max(total, 1)),
                                 speed_bps=done / max(elapsed, 1e-9), elapsed_seconds=time.monotonic() - started)

                self._update(owner, job_id, phase='Comparing compression')
                result = run_benchmark(folder / 'source', factory, folder / 'benchmark',
                                       remote_dir=job['remote_path'], progress=measured,
                                       cancel=lambda: self.check_cancel(job_id))
                result['source_name'] = job['name']
                self._update(owner, job_id, result=result, samples=result.get('samples', self.live[job_id]['samples']))
            elif job['kind'] == 'upload':
                source = folder / 'source'
                remote_name = job['name']
                if job['compress']:
                    self._update(owner, job_id, phase='Compressing file', done=0, percent=0)
                    packed = folder / 'compressed.gz'
                    processed = 0
                    with source.open('rb') as inp, gzip.open(packed, 'wb', compresslevel=6) as out:
                        while block := inp.read(CHUNK):
                            self.check_cancel(job_id)
                            out.write(block)
                            processed += len(block)
                            self._update(owner, job_id, persist=False, done=processed, percent=100 * processed / max(1, job['size']))
                    source = packed
                    remote_name += '.gz'
                total = source.stat().st_size
                callback = self._meter(owner, job_id, total, mode='gzip' if job['compress'] else 'plain')
                with self.client(owner, token, job['device_id']) as client:
                    client.command('CWD', job['remote_path'])
                    client.upload(source, remote_name, progress=callback)
                self._update(owner, job_id, result={'remote_name': remote_name, 'source_bytes': job['size'], 'payload_bytes': total})
            else:
                received = folder / 'received'
                with self.client(owner, token, job['device_id']) as client:
                    total = client.size(job['remote_path'])
                    if total > MAX_BYTES:
                        raise ValueError('This file exceeds the 8 GiB download limit')
                    callback = self._meter(owner, job_id, total, 'Receiving file')
                    client.download(job['remote_path'], received, progress=callback)
                    if received.stat().st_size != total:
                        raise ValueError('Downloaded file size changed. Refresh the device files and try again.')
                output = folder / 'output'
                if job['unpack']:
                    self._update(owner, job_id, phase='Unpacking file')
                    written = 0
                    with gzip.open(received, 'rb') as inp, output.open('wb') as out:
                        while block := inp.read(CHUNK):
                            self.check_cancel(job_id)
                            written += len(block)
                            if written > MAX_BYTES:
                                raise ValueError('Unpacked file exceeds the 8 GiB limit')
                            out.write(block)
                else:
                    os.replace(received, output)
                received.unlink(missing_ok=True)
                self._update(owner, job_id, result={'filename': job['name'][:-3] if job['unpack'] else job['name'],
                                                   'bytes': output.stat().st_size})
            self.check_cancel(job_id)
            success = True
            self._update(owner, job_id, status='completed', phase='Complete', percent=100, eta_seconds=0,
                         elapsed_seconds=time.monotonic() - started)
        except (OSError, ValueError, RuntimeError, FTPError, EOFError, Cancelled) as exc:
            status = 'cancelled' if isinstance(exc, Cancelled) else 'failed'
            error = str(exc).replace(password, '<redacted>') if password else str(exc)
            self._update(owner, job_id, status=status, phase=status.title(), error=error,
                         elapsed_seconds=time.monotonic() - started)
        finally:
            if job['kind'] != 'download' or not success:
                shutil.rmtree(folder, ignore_errors=True)

    def cancel_job(self, owner, job_id):
        with self.lock:
            job = self.job(owner, job_id)
            if job['status'] in TERMINAL:
                return job
            self.events[job_id].set()
            pending = self.futures.get(job_id)
            idle = job['status'] == 'awaiting_upload'
            cancelled_queue = job['status'] == 'queued' and pending is not None and pending.cancel()
            if idle or cancelled_queue:
                self._update(owner, job_id, status='cancelled', phase='Cancelled', error='Transfer cancelled')
                shutil.rmtree(self.spool / job_id, ignore_errors=True)
            else:
                self._update(owner, job_id, phase='Cancelling')
        return self.job(owner, job_id)

    def delete_job(self, owner, job_id):
        job = self.job(owner, job_id)
        if job['status'] not in TERMINAL:
            raise ValueError('Cancel this transfer before removing it')
        self.db.delete_job(owner, job_id)
        with self.lock:
            self.live.pop(job_id, None)
            self.events.pop(job_id, None)
            self.owners.pop(job_id, None)
            self.futures.pop(job_id, None)
        shutil.rmtree(self.spool / job_id, ignore_errors=True)

    def attachment(self, owner, job_id):
        job = self.job(owner, job_id)
        if job['kind'] != 'download' or job['status'] != 'completed':
            raise ValueError('This download is not ready')
        path = self.spool / job_id / 'output'
        if not path.is_file():
            raise NotFound('The local download is no longer available')
        return path, filename(job['result']['filename'])

    def report(self, owner, job_id):
        from ..benchmark import csv_report
        job = self.job(owner, job_id)
        if job['kind'] != 'benchmark' or job['status'] != 'completed':
            raise ValueError('This comparison is not ready')
        return csv_report(job['result'])
