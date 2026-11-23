"""Verify benchmark byte bounds, integrity, metrics, and offline artifact exports."""

import csv
import gzip
import hashlib
import importlib.util
import io
import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from support import running_server

from ftp_lab.benchmark import csv_report, render_graphs, run_benchmark, write_csv
from ftp_lab.client import FTPClient


class BenchmarkTests(unittest.TestCase):
    """Plain and gzip transfer measurements and verification tests."""

    @staticmethod
    def factory(server, client_class=FTPClient):
        """Return a factory for authenticated loopback FTP clients."""

        def create():
            client = client_class(*server.address, timeout=3)
            client.login('student', 'lab-test')
            return client

        return create

    def test_binary_modes_verify_and_report_actual_payload_measurements(self):
        """Verify binary modes verify and report actual payload measurements."""
        payload = bytes(range(256)) * 2049 + b'\x00\xff\r\n'
        with tempfile.TemporaryDirectory() as folder, running_server() as (server, root):
            source = Path(folder) / 'source.bin'
            source.write_bytes(payload)
            work = Path(folder) / 'work'
            work.mkdir()
            (work / 'keep').write_text('keep')
            (root / 'keep').write_text('existing remote file')
            events = []
            result = run_benchmark(
                source, self.factory(server), work, progress=lambda *event: events.append(event)
            )
            self.assertEqual([r['mode'] for r in result['runs']], ['plain', 'gzip'])
            for run in result['runs']:
                self.assertTrue(run['verified'])
                self.assertEqual(run['sha256'], hashlib.sha256(payload).hexdigest())
                self.assertEqual(run['source_bytes'], len(payload))
                self.assertGreater(run['transfer_seconds'], 0)
                self.assertGreaterEqual(run['total_seconds'], run['transfer_seconds'])
                self.assertGreaterEqual(run['verification_seconds'], 0)
                self.assertAlmostEqual(
                    run['throughput_mib_s'],
                    run['payload_bytes'] / 1048576 / run['transfer_seconds'],
                )
                self.assertAlmostEqual(
                    run['effective_mib_s'], len(payload) / 1048576 / run['total_seconds']
                )
                own_events = [event for event in events if event[0] == run['mode']]
                self.assertEqual(own_events[0][1], 0)
                self.assertEqual(own_events[-1][1:3], (run['payload_bytes'], run['payload_bytes']))
                self.assertEqual(
                    [event[1] for event in own_events], sorted(event[1] for event in own_events)
                )
            self.assertEqual(result['runs'][0]['payload_bytes'], len(payload))
            self.assertLess(result['runs'][1]['payload_bytes'], len(payload) // 10)
            self.assertGreater(result['runs'][1]['compression_seconds'], 0)
            self.assertEqual(source.read_bytes(), payload)
            self.assertEqual([p.name for p in work.iterdir()], ['keep'])
            self.assertEqual([p.name for p in root.iterdir()], ['keep'])

    def test_rejects_oversized_plain_download_before_excess_bytes_are_written(self):
        """Verify rejects oversized plain download before excess bytes are written."""
        self.assert_receive_cap('plain')

    def test_rejects_oversized_gzip_download_before_excess_bytes_are_written(self):
        """Verify rejects oversized gzip download before excess bytes are written."""
        self.assert_receive_cap('gzip')

    def assert_receive_cap(self, malicious_mode):
        # Independent storage/client fixture: inject one excess wire byte only
        # after the legitimate uploaded payload has been returned to the engine.
        """Assert that one excess wire byte is rejected before it is written."""
        remote_files = {}
        writes = []
        exceeded = []

        class AdversarialClient:
            """Inject an excess download byte to verify receive bounds."""

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            @staticmethod
            def upload(local, remote, *, progress):
                """Store a fake remote payload and report its exact byte count."""
                payload = Path(local).read_bytes()
                remote_files[remote] = payload
                progress(len(payload))

            @staticmethod
            def download(remote, local, *, progress):
                """Inject one excess byte after returning the legitimate payload."""
                mode = 'gzip' if remote.endswith('.gz') else 'plain'
                payload = remote_files[remote]
                with Path(local).open('wb') as output:
                    progress(len(payload))
                    writes.append((mode, output.write(payload), len(payload)))
                    if mode == malicious_mode:
                        exceeded.append(mode)
                        progress(1)
                        writes.append((mode, output.write(b'!'), len(payload)))

            @staticmethod
            def command(verb, remote):
                """Delete only artifacts owned by the benchmark fixture."""
                if verb != 'DELE':
                    raise AssertionError('Unexpected FTP command')
                del remote_files[remote]

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source'
            source.write_bytes(b'bounded receive test' * 17)
            work = Path(folder) / 'work'
            with self.assertRaisesRegex(ValueError, 'payload'):
                run_benchmark(source, AdversarialClient, work)
            self.assertEqual(exceeded, [malicious_mode])
            self.assertTrue(all(actual == expected for _mode, actual, expected in writes))
            self.assertEqual(remote_files, {})
            self.assertEqual(list(work.iterdir()), [])
            self.assertEqual(source.read_bytes(), b'bounded receive test' * 17)

    def test_gzip_verification_bounds_decompressed_bytes(self):
        """Verify gzip verification bounds decompressed bytes."""

        class ExpandedClient(FTPClient):
            """Replace a gzip response with content larger than the source."""

            def download(self, remote, local, **kwargs):
                result = super().download(remote, local, **kwargs)
                if remote.endswith('.gz'):
                    Path(local).write_bytes(gzip.compress(b'x' * (2 * 1048576)))
                return result

        with tempfile.TemporaryDirectory() as folder, running_server() as (server, root):
            source = Path(folder) / 'source'
            source.write_bytes(b'original' * 100)
            work = Path(folder) / 'work'
            with self.assertRaisesRegex(ValueError, 'exceeds source size'):
                run_benchmark(source, self.factory(server, ExpandedClient), work)
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(list(work.iterdir()), [])

    def test_gzip_can_expand_incompressible_content_and_still_verify(self):
        """Verify gzip can expand incompressible content and still verify."""
        payload = random.Random(3041).randbytes(200000)
        with tempfile.TemporaryDirectory() as folder, running_server() as (server, root):
            source = Path(folder) / 'random.bin'
            source.write_bytes(payload)
            result = run_benchmark(source, self.factory(server), Path(folder) / 'work')
            self.assertGreater(result['runs'][1]['payload_bytes'], len(payload))
            self.assertTrue(result['runs'][1]['verified'])
            self.assertEqual(result['runs'][1]['sha256'], hashlib.sha256(payload).hexdigest())
            self.assertEqual(list(root.iterdir()), [])

    def test_empty_source_and_remote_subdirectory(self):
        """Verify empty source and remote subdirectory."""
        with tempfile.TemporaryDirectory() as folder, running_server() as (server, root):
            source = Path(folder) / 'empty'
            source.touch()
            (root / 'benchmarks').mkdir()
            result = run_benchmark(
                source, self.factory(server), Path(folder) / 'work', remote_dir='/benchmarks'
            )
            self.assertEqual(result['runs'][0]['payload_bytes'], 0)
            self.assertTrue(all(run['verified'] for run in result['runs']))
            self.assertEqual(list((root / 'benchmarks').iterdir()), [])

    def test_detects_corrupt_download_and_cleans_own_files(self):
        """Verify detects corrupt download and cleans own files."""

        class CorruptClient(FTPClient):
            """Append corruption to an otherwise successful download."""

            def download(self, remote, local, **kwargs):
                result = super().download(remote, local, **kwargs)
                with Path(local).open('ab') as output:
                    output.write(b'corruption')
                return result

        with tempfile.TemporaryDirectory() as folder, running_server() as (server, root):
            source = Path(folder) / 'source'
            source.write_bytes(b'expected bytes')
            work = Path(folder) / 'work'
            with self.assertRaisesRegex(ValueError, 'SHA-256|verification'):
                run_benchmark(source, self.factory(server, CorruptClient), work)
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(list(work.iterdir()), [])
            self.assertEqual(source.read_bytes(), b'expected bytes')

    def test_cancellation_during_upload_cleans_partial_remote_and_local_files(self):
        """Verify cancellation during upload cleans partial remote and local files."""

        class Cancelled(Exception):
            """Signal cancellation from the benchmark callback fixture."""

        cancelled = False

        def progress(_mode, done, _total, _elapsed):
            nonlocal cancelled
            if done:
                cancelled = True

        def check():
            if cancelled:
                raise Cancelled('stop requested')

        with tempfile.TemporaryDirectory() as folder, running_server() as (server, root):
            source = Path(folder) / 'source'
            source.write_bytes(bytes(range(256)) * 8192)
            work = Path(folder) / 'work'
            with self.assertRaises(Cancelled):
                run_benchmark(source, self.factory(server), work, progress=progress, cancel=check)
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(list(work.iterdir()), [])

    def test_csv_has_explicit_measurements_and_escaped_names(self):
        """Verify csv has explicit measurements and escaped names."""
        with tempfile.TemporaryDirectory() as folder, running_server() as (server, _root):
            source = Path(folder) / 'comma,name.bin'
            source.write_bytes(b'data' * 100)
            result = run_benchmark(source, self.factory(server), Path(folder) / 'work')
            rows = list(csv.DictReader(io.StringIO(csv_report(result))))
            self.assertEqual([r['mode'] for r in rows], ['plain', 'gzip'])
            self.assertEqual(rows[0]['source_name'], source.name)
            self.assertEqual(int(rows[0]['payload_bytes']), 400)
            self.assertEqual(rows[0]['verified'], 'True')
            self.assertIn('verification_seconds', rows[0])
            target = Path(folder) / 'report.csv'
            write_csv(result, target)
            self.assertEqual(target.read_text(), csv_report(result))

    def test_local_cli_repeats_actual_transfers_and_exports_samples(self):
        """Verify local cli repeats actual transfers and exports samples."""
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source'
            source.write_bytes(b'CLI benchmark\x00\xff' * 2000)
            output = Path(folder) / 'report'
            command = [
                sys.executable,
                '-m',
                'ftp_lab.benchmark',
                '--local',
                str(source),
                '--repeat',
                '2',
                '--output',
                str(output),
            ]
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=20, check=False
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with (output / 'runs.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row['verified'] == 'True' for row in rows))
            self.assertTrue((output / 'samples.csv').is_file())
            self.assertTrue((output / 'results.json').is_file())


class OfflineArtifactTests(unittest.TestCase):
    """Offline media and standalone benchmark chart export tests."""

    def test_media_generator_creates_playable_files_and_manifest_without_padding(self):
        """Verify media generator creates playable files and manifest without padding."""
        if shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None:
            self.skipTest('ffmpeg and ffprobe are optional media fixture tools')
        tool = Path(__file__).resolve().parents[1] / 'tools' / 'media_fixtures.py'
        with tempfile.TemporaryDirectory() as folder:
            command = [sys.executable, str(tool), '--output', folder, '--mib', '1', '--mp4']
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=30, check=False
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = Path(folder)
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(len(manifest['files']), 3)
            with wave.open(str(output / 'audio.wav')) as audio:
                self.assertEqual(audio.getnchannels(), 2)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertEqual(audio.getframerate(), 48000)
                self.assertGreaterEqual(audio.getnframes() * 4, 1048576)
            for name in ('video.avi', 'video.mp4'):
                inspected = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output / name)],
                    text=True,
                    capture_output=True,
                    check=True,
                )
                streams = json.loads(inspected.stdout)['streams']
                self.assertEqual(streams[0]['codec_type'], 'video')
                self.assertGreater(int(streams[0]['width']), 0)
            self.assertGreaterEqual((output / 'video.avi').stat().st_size, 1048576)
            for entry in manifest['files']:
                self.assertEqual(
                    entry['sha256'],
                    hashlib.sha256((output / entry['name']).read_bytes()).hexdigest(),
                )
            second = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('exists', second.stderr)

    def test_report_graph_exports_are_standalone_images(self):
        """Verify report graph exports are standalone images."""
        if importlib.util.find_spec('matplotlib') is None:
            self.skipTest('Matplotlib is an optional plotting dependency')
        # Independent report fixture: explicit values make all plotted quantities inspectable.
        run = {
            'source_bytes': 1048576,
            'payload_bytes': 1048576,
            'throughput_mib_s': 2,
            'effective_mib_s': 1,
            'preparation_seconds': 0.5,
            'transfer_seconds': 0.5,
            'download_seconds': 0.25,
            'verification_seconds': 0.1,
        }
        result = {
            'source_name': 'example.wav',
            'runs': [dict(run, mode='plain'), dict(run, mode='gzip')],
            'samples': [{'mode': 'plain', 'elapsed': 0.5, 'bytes': 1048576}],
        }
        with tempfile.TemporaryDirectory() as folder:
            render_graphs([result], folder)
            for name in ('throughput', 'compression', 'durations', 'time-series'):
                self.assertTrue(
                    (Path(folder) / (name + '.png')).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
                )
                self.assertIn('<svg', (Path(folder) / (name + '.svg')).read_text())


if __name__ == '__main__':
    unittest.main()
