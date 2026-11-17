"""Measured, streaming plain/gzip FTP comparisons and offline report exports.

Upload timings include FTP TYPE, data-connection setup, STOR, and completion.
``total_seconds`` is preparation plus upload; round-trip verification is timed
separately and is never counted as upload throughput. MiB means 1,048,576 bytes.
Run ``python -m ftp_lab.benchmark --help`` for the repeatable stress CLI.
"""

import argparse
import csv
import getpass
import gzip
import hashlib
import io
import json
import os
import posixpath
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
MIB = 1024 * 1024
CSV_FIELDS = (
    'source_name', 'repeat', 'mode', 'source_bytes', 'payload_bytes',
    'compression_ratio', 'compression_seconds', 'preparation_seconds',
    'transfer_seconds', 'total_seconds', 'download_seconds',
    'verification_seconds', 'round_trip_seconds', 'throughput_mib_s',
    'effective_mib_s', 'sha256', 'verified',
)


def _check(cancel):
    """Check for benchmark cancellation.

    What it does:
        Executes the cancellation callback if one was provided.

    How it does:
        If cancel is not None, invokes cancel() to allow the caller to raise a cancellation exception.
    """
    if cancel is not None:
        cancel()


def _digest(stream, cancel=None, max_bytes=None):
    """Compute the SHA-256 hash and byte length of a binary stream.

    What it does:
        Calculates the SHA-256 checksum and verifies stream byte bounds.

    How it does:
        Reads the stream in 1 MiB chunks (CHUNK_SIZE). Periodically verifies cancellation.
        Accumulates total size, raises ValueError if max_bytes is exceeded, updates
        hashlib.sha256(), and returns (hexdigest, size).
    """
    digest = hashlib.sha256()
    size = 0
    while True:
        _check(cancel)
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            return digest.hexdigest(), size
        size += len(chunk)
        if max_bytes is not None and size > max_bytes:
            raise ValueError('SHA-256 verification failed: downloaded content exceeds source size')
        digest.update(chunk)


def run_benchmark(source, client_factory, work_dir, remote_dir='/', progress=None, cancel=None):
    """Upload and download both modes, SHA-256 verify, and clean owned artifacts.

    What it does:
        Runs side-by-side plain and gzip FTP benchmarks for a file, recording throughput,
        durations, time-series progress, and validating cryptographic hash round-trip integrity.

    How it does:
        1. Validates that source is an existing regular file and remote_dir is clean.
        2. Computes the baseline SHA-256 digest and size of the original source file.
        3. For both modes ('plain' and 'gzip'):
           - Gzip mode compresses the source to a staging file with compresslevel=6 and mtime=0.
           - Connects to the server via client_factory().
           - Uploads the payload to a unique remote filename (.ftp-benchmark-<uuid>...), sampling
             instantaneous speed and notifying progress callback.
           - Downloads the payload back to local staging, enforcing expected byte bounds.
           - Verifies SHA-256 digest against original source (decompressing if gzip).
           - Records detailed metrics in a result dictionary.
        4. Cleans up the remote benchmark file using FTP DELE command and removes local staging files.
        5. Returns the structured result dictionary.
    """
    source, work_dir = Path(source), Path(work_dir)
    if not source.is_file():
        raise ValueError('Benchmark source must be a regular file')
    if not isinstance(remote_dir, str) or any(c in remote_dir for c in '\r\n\x00'):
        raise ValueError('Invalid remote directory')
    _check(cancel)
    source_size = source.stat().st_size
    hash_start = time.perf_counter()
    with source.open('rb') as original:
        expected_hash, expected_size = _digest(original, cancel)
    if expected_size != source_size:
        raise ValueError('Source changed while preparing benchmark')
    source_hash_seconds = time.perf_counter() - hash_start
    work_dir.mkdir(parents=True, exist_ok=True)
    result = {'source_name': source.name, 'source_bytes': source_size,
              'source_sha256': expected_hash, 'source_hash_seconds': source_hash_seconds,
              'timing_scope': 'total_seconds = preparation + FTP upload; verification is separate',
              'runs': [], 'samples': []}
    with tempfile.TemporaryDirectory(prefix='benchmark-', dir=work_dir) as temporary:
        staging = Path(temporary)
        for mode in ('plain', 'gzip'):
            _check(cancel)
            run_start = time.perf_counter()
            payload = source
            compression_seconds = 0.0
            if mode == 'gzip':
                payload = staging / 'payload.gz'
                compression_start = time.perf_counter()
                with (source.open('rb') as original, payload.open('wb') as raw,
                      gzip.GzipFile(filename='', fileobj=raw, mode='wb', compresslevel=6, mtime=0) as output):
                    while True:
                        _check(cancel)
                        chunk = original.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        output.write(chunk)
                compression_seconds = time.perf_counter() - compression_start
            payload_size = payload.stat().st_size
            remote = posixpath.join(remote_dir, '.ftp-benchmark-' + uuid.uuid4().hex + ('.gz' if mode == 'gzip' else '.bin'))
            retrieved = staging / ('retrieved-' + mode)
            attempted = False
            failure = None
            try:
                with client_factory() as client:
                    client.binary = True
                    _check(cancel)
                    preparation_seconds = time.perf_counter() - run_start
                    upload_start = time.perf_counter()
                    done = 0
                    last_sample = -1.0

                    def report(delta, *, upload_start=upload_start, payload_size=payload_size, mode=mode):
                        nonlocal done, last_sample
                        _check(cancel)
                        done += delta
                        elapsed = time.perf_counter() - upload_start
                        if elapsed - last_sample >= 0.1 or done == payload_size:
                            result['samples'].append({'mode': mode, 'elapsed': elapsed, 'bytes': done,
                                                      'speed_bps': done / elapsed if elapsed > 0 else 0})
                            last_sample = elapsed
                        if progress is not None:
                            progress(mode, done, payload_size, elapsed)
                        _check(cancel)

                    report(0)
                    attempted = True
                    client.upload(payload, remote, progress=report)
                    transfer_seconds = time.perf_counter() - upload_start
                    total_seconds = time.perf_counter() - run_start
                    # Keep the final server completion latency visible in time-series exports.
                    report(0)
                    _check(cancel)
                    if done != payload_size:
                        raise ValueError('Upload progress did not match payload size')
                    download_start = time.perf_counter()
                    received = 0

                    def receive_progress(delta, *, payload_size=payload_size):
                        nonlocal received
                        _check(cancel)
                        if delta < 0 or received + delta > payload_size:
                            raise ValueError('Downloaded FTP payload exceeds expected size')
                        received += delta

                    # FTPClient checks progress before writing each incoming chunk.
                    client.download(remote, retrieved, progress=receive_progress)
                    download_seconds = time.perf_counter() - download_start
                    if received != payload_size:
                        raise ValueError('Downloaded FTP payload size does not match uploaded payload')
                verification_start = time.perf_counter()
                opener = gzip.open if mode == 'gzip' else open
                with opener(retrieved, 'rb') as downloaded:
                    actual_hash, actual_size = _digest(downloaded, cancel, max_bytes=source_size)
                if actual_hash != expected_hash or actual_size != source_size:
                    raise ValueError('SHA-256 verification failed for ' + mode)
                verification_seconds = time.perf_counter() - verification_start
                result['runs'].append({
                    'mode': mode, 'source_bytes': source_size, 'payload_bytes': payload_size,
                    'compression_ratio': payload_size / source_size if source_size else None,
                    'compression_seconds': compression_seconds, 'preparation_seconds': preparation_seconds,
                    'transfer_seconds': transfer_seconds, 'total_seconds': total_seconds,
                    'download_seconds': download_seconds, 'verification_seconds': verification_seconds,
                    'round_trip_seconds': time.perf_counter() - run_start,
                    'throughput_mib_s': payload_size / MIB / transfer_seconds if transfer_seconds else 0,
                    'effective_mib_s': source_size / MIB / total_seconds if total_seconds else 0,
                    'sha256': actual_hash, 'verified': True,
                })
            except BaseException as exc:
                failure = exc
                raise
            finally:
                if attempted:
                    try:
                        with client_factory() as cleanup:
                            cleanup.command('DELE', remote)
                    except Exception as cleanup_error:
                        if failure is None:
                            raise RuntimeError('Benchmark remote cleanup failed: ' + remote) from cleanup_error
                        failure.add_note('Remote cleanup could not be confirmed for ' + remote + ': ' + str(cleanup_error))
                retrieved.unlink(missing_ok=True)
    return result


def csv_report(result):
    """Return interoperable CSV formatted metrics.

    What it does:
        Formats benchmark run results into a CSV string adhering to CSV_FIELDS.

    How it does:
        Initializes an in-memory io.StringIO buffer with csv.DictWriter, writes header,
        and writes rows for each benchmark run.
    """
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, CSV_FIELDS, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    for run in result['runs']:
        writer.writerow({'source_name': result.get('source_name', ''), 'repeat': result.get('repeat', 1), **run})
    return output.getvalue()


def write_csv(result, path):
    """Write benchmark metrics to a CSV file.

    What it does:
        Saves CSV report output to a destination file on disk.

    How it does:
        Generates CSV text via csv_report(result) and writes it to path using Path.write_text().
    """
    Path(path).write_text(csv_report(result), encoding='utf-8')


def export_reports(results, output, *, plots=False):
    """Export benchmark runs, sampled progress time-series, and optional plots.

    What it does:
        Saves benchmark run metrics to runs.csv, time-series data to samples.csv,
        raw results to results.json, and optionally generates visualization plots.

    How it does:
        1. Ensures the output directory exists.
        2. Exports aggregate runs to runs.csv via write_csv().
        3. Formats and writes fine-grained upload time-series records to samples.csv.
        4. Serializes all results into formatted results.json.
        5. If plots is True, calls render_graphs(results, output).
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    flat = {'runs': [{**run, 'source_name': result['source_name'], 'repeat': result.get('repeat', 1)}
                     for result in results for run in result['runs']]}
    write_csv(flat, output / 'runs.csv')
    with (output / 'samples.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, ('source_name', 'repeat', 'mode', 'elapsed_seconds', 'payload_bytes', 'average_payload_mib_s'))
        writer.writeheader()
        for result in results:
            for sample in result['samples']:
                writer.writerow({'source_name': result['source_name'], 'repeat': result.get('repeat', 1),
                                 'mode': sample['mode'], 'elapsed_seconds': sample['elapsed'],
                                 'payload_bytes': sample['bytes'], 'average_payload_mib_s': sample['speed_bps'] / MIB})
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    if plots:
        render_graphs(results, output)


def render_graphs(results, output):
    """Save standalone PNG and SVG figures summarizing benchmark performance.

    What it does:
        Renders Matplotlib charts for throughput, payload compression, phase durations,
        and cumulative upload time-series.

    How it does:
        1. Imports Matplotlib's Figure and FigureCanvasAgg.
        2. Renders four distinct figures:
           - throughput: Bar chart comparing application payload throughput and effective throughput.
           - compression: Bar chart illustrating raw vs. gzip compressed payload sizes.
           - durations: Stacked bar chart showing preparation, upload, download, and verification times.
           - time-series: Line graph plotting cumulative upload progress over elapsed seconds.
        3. Saves each figure to output directory in both .png and .svg formats.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    colors = {'plain': '#457b9d', 'gzip': '#2a9d8f'}
    runs = [(f"{result['source_name']} #{result.get('repeat', 1)} {run['mode']}", run)
            for result in results for run in result['runs']]

    def save(figure, name):
        """Save a Matplotlib figure as high-resolution PNG and vector SVG."""
        FigureCanvasAgg(figure)
        figure.savefig(output / (name + '.png'), dpi=160, bbox_inches='tight')
        figure.savefig(output / (name + '.svg'), bbox_inches='tight')
        figure.clear()

    figure = Figure(figsize=(max(8, min(24, len(runs) * 0.7)), 5), layout='constrained')
    axis = figure.subplots()
    axis.bar(range(len(runs)), [run['throughput_mib_s'] for _, run in runs],
             color=[colors[run['mode']] for _, run in runs], label='FTP upload payload')
    axis.plot(range(len(runs)), [run['effective_mib_s'] for _, run in runs], 'ko', label='Source bytes / (preparation + upload)')
    axis.set(ylabel='MiB/s', title='Measured FTP throughput (application payload)',
             xticks=range(len(runs)), xticklabels=[label for label, _ in runs])
    axis.tick_params(axis='x', labelrotation=40)
    axis.legend()
    save(figure, 'throughput')

    figure = Figure(figsize=(max(8, min(24, len(runs) * 0.7)), 5), layout='constrained')
    axis = figure.subplots()
    axis.bar(range(len(runs)), [run['payload_bytes'] / MIB for _, run in runs],
             color=[colors[run['mode']] for _, run in runs])
    axis.set(ylabel='FTP payload (MiB)', title='Plain and gzip level 6 payload sizes',
             xticks=range(len(runs)), xticklabels=[label for label, _ in runs])
    axis.tick_params(axis='x', labelrotation=40)
    save(figure, 'compression')

    figure = Figure(figsize=(max(8, min(24, len(runs) * 0.7)), 5), layout='constrained')
    axis = figure.subplots()
    bottom = [0.0] * len(runs)
    for field, title in (('preparation_seconds', 'Preparation (includes gzip)'),
                         ('transfer_seconds', 'FTP upload'), ('download_seconds', 'Verification download'),
                         ('verification_seconds', 'Hash/decompress verification')):
        values = [run[field] for _, run in runs]
        axis.bar(range(len(runs)), values, bottom=bottom, label=title)
        bottom = [a + b for a, b in zip(bottom, values)]
    axis.set(ylabel='Seconds', title='Measured duration breakdown (verification separate)',
             xticks=range(len(runs)), xticklabels=[label for label, _ in runs])
    axis.tick_params(axis='x', labelrotation=40)
    axis.legend()
    save(figure, 'durations')

    figure = Figure(figsize=(9, 5), layout='constrained')
    axis = figure.subplots()
    for result in results:
        for mode in ('plain', 'gzip'):
            samples = [sample for sample in result['samples'] if sample['mode'] == mode]
            axis.plot([sample['elapsed'] for sample in samples], [sample['bytes'] / MIB for sample in samples],
                      label=f"{result['source_name']} #{result.get('repeat', 1)} {mode}",
                      linestyle='-' if mode == 'plain' else '--')
    axis.set(xlabel='Seconds since FTP upload began', ylabel='Cumulative FTP payload (MiB)',
             title='Actual upload progress samples')
    axis.legend(fontsize=8)
    save(figure, 'time-series')


@contextmanager
def _local_server(folder):
    """Start an ephemeral loopback FTPServer in a background thread.

    What it does:
        Context manager that launches a test FTP server on loopback port 0 and yields its bound address.

    How it does:
        Creates a temporary server root, initializes FTPServer on ('127.0.0.1', 0), runs it in
        a daemon thread, yields server.address, and calls shutdown()/server_close() upon exit.
    """
    from .server import FTPServer

    root = folder / 'server'
    root.mkdir()
    server = FTPServer(('127.0.0.1', 0), root, 'benchmark', 'local-benchmark')
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02})
    worker.start()
    try:
        yield server.address
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def main(argv=None):
    """Run the benchmark CLI entry point.

    What it does:
        Parses CLI parameters, prepares a local or remote FTP connection, executes repeatable
        plain vs. gzip benchmarks, and exports reports and charts.

    How it does:
        1. Parses command arguments: files, --local, --host, --port, --user, --repeat, --output, --plots.
        2. Validates source files and reads/prompts for the password.
        3. Sets up an isolated loopback server (if --local) or targets the specified host:port.
        4. Runs run_benchmark() for each file across the specified repeat iterations.
        5. Exports reports and optionally renders charts via render_graphs().
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', type=Path, nargs='+', help='Existing source files; never modified')
    parser.add_argument('--local', action='store_true', help='Start an isolated loopback FTP server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=2121)
    parser.add_argument('--user', default='student')
    parser.add_argument('--remote-dir', default='/')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plots', action='store_true', help='Export Matplotlib PNG and SVG graphs')
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error('--repeat must be positive')
    if any(not source.is_file() for source in args.files):
        parser.error('Every source must be an existing regular file')
    password = 'local-benchmark' if args.local else os.environ.get('FTP_PASSWORD')
    if password is None:
        if not sys.stdin.isatty():
            parser.error('Set FTP_PASSWORD for noninteractive remote benchmarks')
        password = getpass.getpass('FTP password: ')
    from contextlib import nullcontext

    from .client import FTPClient, FTPError
    from .protocol import ProtocolError

    args.output.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='benchmark-cli-', dir=args.output) as folder:
            work = Path(folder)
            server_context = _local_server(work) if args.local else nullcontext((args.host, args.port))
            with server_context as address:
                def factory():
                    client = FTPClient(*address, timeout=120)
                    try:
                        client.login('benchmark' if args.local else args.user, password)
                    except BaseException:
                        client.close()
                        raise
                    return client
                results = []
                for repeat in range(1, args.repeat + 1):
                    for source in args.files:
                        result = run_benchmark(source, factory, work / 'transfers', remote_dir=args.remote_dir)
                        result['repeat'] = repeat
                        result['transport'] = 'loopback' if args.local else args.host
                        results.append(result)
                        export_reports(results, args.output)
                        for run in result['runs']:
                            print(f"{source.name} #{repeat} {run['mode']}: {run['payload_bytes']} payload bytes, "
                                  f"{run['transfer_seconds']:.4f}s upload, {run['throughput_mib_s']:.2f} MiB/s, "
                                  f"{run['total_seconds']:.4f}s preparation+upload, SHA-256 verified")
                if args.plots:
                    render_graphs(results, args.output)
    except (OSError, ValueError, RuntimeError, ImportError, EOFError, FTPError, ProtocolError) as error:
        print('Benchmark failed: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
