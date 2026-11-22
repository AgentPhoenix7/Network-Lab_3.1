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
                        """Track incremental upload bytes and sample transfer throughput over time.

                        What it does:
                            Accumulates bytes uploaded, updates the live progress callback, and
                            records periodic throughput samples at 100ms intervals into the result.

                        How it does:
                            1. Verifies non-cancellation via _check(cancel).
                            2. Increments `done` by delta.
                            3. Calculates elapsed seconds and records a snapshot dict (bytes, speed_bps)
                               if >= 0.1s has elapsed or transfer has completed.
                            4. Invokes user-supplied progress callback if provided.
                        """
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
                        """Track incremental download bytes and guard against stream size overrun.

                        What it does:
                            Validates chunk length, increments total received bytes, and verifies that
                            the downloaded payload does not exceed the expected size.

                        How it does:
                            1. Verifies non-cancellation via _check(cancel).
                            2. Ensures delta is non-negative and received + delta does not exceed payload_size.
                            3. Increments nonlocal received accumulator.
                        """
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
        and cumulative upload time-series that express clear, meaningful insights.

    How it does:
        1. compression: Compares original vs. gzip payload sizes with percentage savings,
           using separate panels for large raw media and compressed control files.
        2. throughput: Visualizes wire transfer rate vs. end-to-end effective rate, highlighting
           the loopback CPU compression overhead.
        3. durations: Stacked breakdown across preparation, upload, download, and verification phases.
        4. time-series: Multi-panel upload progress curves, independently scaled per input media.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)

    c_plain = "#2b5c8f"      # Steel blue
    c_gzip = "#2a9d8f"       # Teal
    c_effective = "#e76f51"  # Coral
    c_prep = "#e76f51"       # Gzip CPU Prep
    c_upload = "#2b5c8f"     # FTP Upload
    c_download = "#457b9d"   # Verification Download
    c_verif = "#6a4c93"      # Verification

    def save_fig(fig, name):
        """Render and save a Matplotlib figure as dual PNG and SVG image files.

        What it does:
            Renders the provided matplotlib Figure instance and writes both PNG (160 DPI)
            and vector SVG graphics to the benchmark output directory.

        How it does:
            1. Binds FigureCanvasAgg to the figure.
            2. Invokes fig.savefig() for '{name}.png' with dpi=160 and bbox_inches='tight'.
            3. Invokes fig.savefig() for '{name}.svg' with bbox_inches='tight'.
            4. Clears the figure with fig.clear() to release memory.
        """
        FigureCanvasAgg(fig)
        fig.savefig(output / f"{name}.png", dpi=160, bbox_inches="tight")
        fig.savefig(output / f"{name}.svg", bbox_inches="tight")
        fig.clear()

    # Extract source metadata and runs
    sources = []
    data_by_src = {}
    for r in results:
        src = r.get("source_name", "unknown")
        if src not in data_by_src:
            sources.append(src)
            data_by_src[src] = {
                "plain_payload": [], "gzip_payload": [],
                "plain_net": [], "plain_eff": [], "gzip_net": [], "gzip_eff": [],
                "plain_phases": {"prep": [], "xfer": [], "down": [], "verif": []},
                "gzip_phases": {"prep": [], "xfer": [], "down": [], "verif": []},
                "samples": []
            }
        for run in r.get("runs", []):
            m = run.get("mode", "plain")
            p_mib = run.get("payload_bytes", 0) / MIB
            data_by_src[src][f"{m}_payload"].append(p_mib)
            data_by_src[src][f"{m}_net"].append(run.get("throughput_mib_s", 0))
            data_by_src[src][f"{m}_eff"].append(run.get("effective_mib_s", 0))
            phases = data_by_src[src][f"{m}_phases"]
            phases["prep"].append(run.get("preparation_seconds", 0))
            phases["xfer"].append(run.get("transfer_seconds", 0))
            phases["down"].append(run.get("download_seconds", 0))
            phases["verif"].append(run.get("verification_seconds", 0))
        if "samples" in r:
            data_by_src[src]["samples"].append((r.get("repeat", 1), r["samples"]))

    def mean(lst, default=0.0):
        """Calculate the arithmetic mean of a sequence of numeric values.

        What it does:
            Computes the average value of elements in a list, returning a default if empty.

        How it does:
            Sums all elements in lst and divides by len(lst) if non-empty, otherwise returns default.
        """
        return sum(lst) / len(lst) if lst else default

    # 1. COMPRESSION PLOT
    large_srcs = [s for s in sources if mean(data_by_src[s]["plain_payload"]) > 10]
    small_srcs = [s for s in sources if mean(data_by_src[s]["plain_payload"]) <= 10]

    if large_srcs and small_srcs:
        fig_c = Figure(figsize=(10.5, 4.6))
        gs = fig_c.add_gridspec(1, 2, width_ratios=[len(large_srcs) * 1.3, len(small_srcs) * 1.0], wspace=0.28)
        ax1 = fig_c.add_subplot(gs[0, 0])
        ax2 = fig_c.add_subplot(gs[0, 1])

        x1 = list(range(len(large_srcs)))
        w = 0.35
        p1 = [mean(data_by_src[s]["plain_payload"]) for s in large_srcs]
        g1 = [mean(data_by_src[s]["gzip_payload"]) for s in large_srcs]
        b1 = ax1.bar([i - w/2 for i in x1], p1, w, label="Original / Plain", color=c_plain, edgecolor="black", linewidth=0.5)
        b2 = ax1.bar([i + w/2 for i in x1], g1, w, label="Gzip Level 6", color=c_gzip, edgecolor="black", linewidth=0.5)
        ax1.set_ylabel("Payload Size (MiB)", fontweight="bold")
        ax1.set_title("(a) Large Raw Media (PCM Audio & Raw Video)", fontsize=10, fontweight="bold", pad=6)
        ax1.set_xticks(x1)
        ax1.set_xticklabels(large_srcs, fontweight="bold")
        ax1.set_ylim(0, max(p1) * 1.18 if p1 else 10)
        ax1.grid(axis="y", linestyle="--", alpha=0.5)
        for bar in b1:
            h = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2, h + 2, f"{h:.1f} MiB", ha="center", va="bottom", fontsize=8.5, fontweight="bold")
        for i, bar in enumerate(b2):
            h = bar.get_height()
            saved = (1.0 - h / p1[i]) * 100 if p1[i] > 0 else 0
            ax1.text(bar.get_x() + bar.get_width()/2, h + 2, f"{h:.2f} MiB\n(-{saved:.1f}%)", ha="center", va="bottom", fontsize=8, fontweight="bold", color="#1b4332")
        ax1.legend(loc="upper right", framealpha=0.9)

        x2 = list(range(len(small_srcs)))
        p2 = [mean(data_by_src[s]["plain_payload"]) for s in small_srcs]
        g2 = [mean(data_by_src[s]["gzip_payload"]) for s in small_srcs]
        b3 = ax2.bar([i - w/2 for i in x2], p2, w, label="Original / Plain", color=c_plain, edgecolor="black", linewidth=0.5)
        b4 = ax2.bar([i + w/2 for i in x2], g2, w, label="Gzip Level 6", color=c_gzip, edgecolor="black", linewidth=0.5)
        ax2.set_ylabel("Payload Size (MiB)", fontweight="bold")
        ax2.set_title("(b) Compressed Control (H.264 MP4)", fontsize=10, fontweight="bold", pad=6)
        ax2.set_xticks(x2)
        ax2.set_xticklabels(small_srcs, fontweight="bold")
        ax2.set_ylim(0, max(p2) * 1.35 if p2 else 5)
        ax2.grid(axis="y", linestyle="--", alpha=0.5)
        for bar in b3:
            h = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2, h + 0.05, f"{h:.3f} MiB", ha="center", va="bottom", fontsize=8.5, fontweight="bold")
        for i, bar in enumerate(b4):
            h = bar.get_height()
            saved = (1.0 - h / p2[i]) * 100 if p2[i] > 0 else 0
            ax2.text(bar.get_x() + bar.get_width()/2, h + 0.05, f"{h:.3f} MiB\n(-{saved:.2f}%)", ha="center", va="bottom", fontsize=8, fontweight="bold", color="#1b4332")
        fig_c.suptitle("FTP Payload Size: Plain vs. Gzip Level 6 Preprocessing", fontsize=11.5, fontweight="bold")
    else:
        fig_c = Figure(figsize=(max(7, len(sources) * 2.2), 4.5))
        ax = fig_c.subplots()
        x = list(range(len(sources)))
        w = 0.35
        p = [mean(data_by_src[s]["plain_payload"]) for s in sources]
        g = [mean(data_by_src[s]["gzip_payload"]) for s in sources]
        b1 = ax.bar([i - w/2 for i in x], p, w, label="Original / Plain", color=c_plain, edgecolor="black", linewidth=0.5)
        b2 = ax.bar([i + w/2 for i in x], g, w, label="Gzip Level 6", color=c_gzip, edgecolor="black", linewidth=0.5)
        ax.set_ylabel("Payload (MiB)", fontweight="bold")
        ax.set_title("FTP Payload Size: Plain vs. Gzip Level 6 Preprocessing", fontsize=11, fontweight="bold", pad=8)
        ax.set_xticks(x)
        ax.set_xticklabels(sources, fontweight="bold")
        ax.legend()
        ax.grid(axis="y", linestyle="--", alpha=0.5)
        for bar in b1:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.02 * max(p or [1]), f"{h:.1f} MiB", ha="center", va="bottom", fontsize=8, fontweight="bold")
        for i, bar in enumerate(b2):
            h = bar.get_height()
            orig = p[i]
            saved = (1.0 - h / orig) * 100 if orig > 0 else 0
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.02 * max(p or [1]), f"{h:.2f} MiB (-{saved:.1f}%)", ha="center", va="bottom", fontsize=7.5, fontweight="bold", color="#1b4332")
    save_fig(fig_c, "compression")

    # 2. THROUGHPUT PLOT
    fig_t = Figure(figsize=(max(8.5, len(sources) * 2.6), 4.6))
    ax_t = fig_t.subplots()
    x_t = list(range(len(sources)))
    w_t = 0.26
    p_eff_m = [mean(data_by_src[s]["plain_eff"]) for s in sources]
    g_net_m = [mean(data_by_src[s]["gzip_net"]) for s in sources]
    g_eff_m = [mean(data_by_src[s]["gzip_eff"]) for s in sources]

    b1_t = ax_t.bar([i - w_t for i in x_t], p_eff_m, w_t, label="Plain (Network = Effective)", color=c_plain, edgecolor="black", linewidth=0.5)
    b2_t = ax_t.bar(x_t, g_net_m, w_t, label="Gzip Wire Rate (Payload / transfer s)", color=c_gzip, edgecolor="black", linewidth=0.5)
    b3_t = ax_t.bar([i + w_t for i in x_t], g_eff_m, w_t, label="Gzip Effective Rate (Source / total s)", color=c_effective, edgecolor="black", linewidth=0.5, hatch="//")

    ax_t.set_ylabel("Throughput (MiB/s)", fontweight="bold")
    ax_t.set_title("FTP Transfer Throughput: Network Wire Rate vs. End-to-End Effective Rate", fontsize=11, fontweight="bold", pad=8)
    ax_t.set_xticks(x_t)
    ax_t.set_xticklabels(sources, fontweight="bold")
    max_tp = max(max(p_eff_m or [1]), max(g_net_m or [1]), max(g_eff_m or [1]))
    ax_t.set_ylim(0, max_tp * 1.25)
    ax_t.grid(axis="y", linestyle="--", alpha=0.5)

    for bar in b1_t:
        h = bar.get_height()
        ax_t.text(bar.get_x() + bar.get_width()/2, h + 0.02 * max_tp, f"{h:.0f}\nMiB/s", ha="center", va="bottom", fontsize=7.5, fontweight="bold")
    for bar in b2_t:
        h = bar.get_height()
        ax_t.text(bar.get_x() + bar.get_width()/2, h + 0.02 * max_tp, f"{h:.0f}\nMiB/s", ha="center", va="bottom", fontsize=7.5, fontweight="bold", color="#1b4332")
    for bar in b3_t:
        h = bar.get_height()
        ax_t.text(bar.get_x() + bar.get_width()/2, h + 0.02 * max_tp, f"{h:.0f}\nMiB/s", ha="center", va="bottom", fontsize=7.5, fontweight="bold", color="#9e2a2b")

    ax_t.legend(loc="upper right", framealpha=0.92, fontsize=8.5)
    save_fig(fig_t, "throughput")

    # 3. DURATIONS PLOT
    fig_d = Figure(figsize=(max(9.0, len(sources) * 2.8), 4.8))
    ax_d = fig_d.subplots()
    labels_d = []
    positions_d = []
    bar_x_d = []
    totals_d = []
    idx_d = 0
    w_d = 0.38
    gap_d = 0.45

    phases = [
        ("prep", "Preparation / Gzip CPU Compression", c_prep),
        ("xfer", "FTP Upload (Socket Transfer)", c_upload),
        ("down", "FTP Verification Download", c_download),
        ("verif", "Hash & Decompress Verification", c_verif)
    ]

    for s in sources:
        for m in ("plain", "gzip"):
            labels_d.append(f"{s}\n({m})")
            pos = idx_d * (w_d + 0.06) + (idx_d // 2) * gap_d
            positions_d.append(pos)
            bar_x_d.append(pos)

            bottom = 0.0
            for key, name, col in phases:
                val = mean(data_by_src[s][f"{m}_phases"][key])
                lbl = name if idx_d == 0 else ""
                ax_d.bar(pos, val, w_d, bottom=bottom, color=col, label=lbl, edgecolor="black", linewidth=0.5)
                bottom += val
            totals_d.append(bottom)
            idx_d += 1

    ax_d.set_ylabel("Total Duration (Seconds)", fontweight="bold")
    ax_d.set_title("Phase Duration Breakdown: Gzip Preprocessing Overhead vs. Network Transfer", fontsize=11, fontweight="bold", pad=8)
    ax_d.set_xticks(positions_d)
    ax_d.set_xticklabels(labels_d, fontsize=8, fontweight="bold")
    max_dur = max(totals_d or [1])
    ax_d.set_ylim(0, max_dur * 1.22)
    ax_d.grid(axis="y", linestyle="--", alpha=0.5)

    for x_pos, tot in zip(bar_x_d, totals_d):
        ax_d.text(x_pos, tot + 0.02 * max_dur, f"{tot:.2f} s", ha="center", va="bottom", fontsize=8, fontweight="bold")

    ax_d.legend(loc="upper right", framealpha=0.92, fontsize=8)
    save_fig(fig_d, "durations")

    # 4. TIME-SERIES PLOT
    num_s = max(1, len(sources))
    fig_ts = Figure(figsize=(max(8.5, num_s * 4.0), 4.2))
    axes_ts = fig_ts.subplots(1, num_s, squeeze=False)[0]

    for ax, src in zip(axes_ts, sources):
        max_bytes = 0
        samples_list = data_by_src[src]["samples"]
        for rep, samples in samples_list:
            for mode, color, ls in [("plain", c_plain, "-"), ("gzip", c_gzip, "--")]:
                m_samples = [s for s in samples if s.get("mode") == mode]
                if not m_samples:
                    continue
                times = [s.get("elapsed", 0) for s in m_samples]
                bytes_m = [s.get("bytes", 0) / MIB for s in m_samples]
                max_bytes = max(max_bytes, max(bytes_m or [0]))
                lbl = f"{mode.capitalize()} (Rep {rep})"
                ax.plot(times, bytes_m, color=color, linestyle=ls, linewidth=1.8,
                        marker="o" if mode == "plain" else "s", markersize=4, label=lbl, alpha=0.85)

        ax.set_title(f"Source: {src}", fontsize=10, fontweight="bold", pad=6)
        ax.set_xlabel("Elapsed Time (s)", fontweight="bold")
        ax.set_ylabel("Uploaded Payload (MiB)", fontweight="bold")
        ax.set_ylim(0, max_bytes * 1.2 if max_bytes > 0 else 1)
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.legend(fontsize=7.5, loc="lower right" if max_bytes > 10 else "center right")

    fig_ts.suptitle("Cumulative FTP Upload Progress Samples Over Time", fontsize=11.5, fontweight="bold")
    save_fig(fig_ts, "time-series")


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
                    """Create, connect, and authenticate an FTPClient instance for benchmarking.

                    What it does:
                        Instantiates a new FTPClient targeting the benchmark server address and logs in.

                    How it does:
                        1. Instantiates FTPClient with host/port address and 120s timeout.
                        2. Calls client.login() with configured username and password.
                        3. Closes socket and re-raises if any exception occurs during initialization.
                        4. Returns the authenticated client instance.
                    """
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
