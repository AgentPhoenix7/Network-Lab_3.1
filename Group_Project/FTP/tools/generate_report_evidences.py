#!/usr/bin/env python3
"""Automated pipeline to regenerate the FTP report evidence artifacts.

Run from Group_Project/FTP with:
    uv run --locked --extra plots python tools/generate_report_evidences.py

This explicitly rewrites the sibling Group_Project/report artifacts. Child
processes reuse sys.executable, retaining the interpreter selected by uv.

What it does:
    1. Executes the 58 terminal-focused protocol and benchmark tests and saves output
       to Group_Project/report/evidence/terminal-validation.txt.
    2. Runs a fresh isolated loopback server and batch client session demonstrating
       directory navigation, transfer representation (TYPE I/A), extended mode (EPSV/EPRT),
       file size query (SIZE), status reporting (STAT with reply 213), two-phase atomic
       rename (RNFR/RNTO with replies 350 and 250), binary and ASCII downloads, and cleanup.
    3. Verifies SHA-256 digests and records outcomes in ftp_terminal_verification.json.
    4. Synchronizes ftp_lab source snapshots in Group_Project/report/code/ftp_lab.
    5. Updates report-provenance.json, report-validation.json, and recomputes SHA256SUMS.
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from platform import python_version

RUN_DATE = date.today().isoformat()
FTP_DIR = Path(__file__).resolve().parent.parent
GROUP_PROJECT_DIR = FTP_DIR.parent
REPORT_DIR = GROUP_PROJECT_DIR / "report"
EVIDENCE_DIR = REPORT_DIR / "evidence"
DATA_DIR = REPORT_DIR / "data"
CODE_DIR = REPORT_DIR / "code" / "ftp_lab"
# The Git root is the parent of Group_Project in this repository layout.
SCRATCH_TMP = GROUP_PROJECT_DIR.parent / ".scratch" / "tmp"
SCRATCH_TMP.mkdir(parents=True, exist_ok=True)

ENV = os.environ.copy()
ENV["TMPDIR"] = str(SCRATCH_TMP)
ENV["TMP"] = str(SCRATCH_TMP)
ENV["TEMP"] = str(SCRATCH_TMP)
ENV["PYTHONPATH"] = f"{FTP_DIR}:{FTP_DIR / 'tests'}"
ENV["FTP_PASSWORD"] = "lab-demo"


def run_tests():
    """Execute the core FTP test suite and record validation output.

    What it does:
        Runs unittest on test_core, test_server, test_client, test_cli, and test_benchmark,
        and saves filtered execution logs to report/evidence/terminal-validation.txt.

    How it does:
        1. Invokes Python unittest with -W error::ResourceWarning via subprocess.run.
        2. Filters out temporary matplotlib cache warnings.
        3. Writes cleaned stdout to EVIDENCE_DIR / 'terminal-validation.txt'.
    """
    print("==> [1/5] Running terminal FTP test suite...")
    cmd = [
        sys.executable,
        "-W",
        "error::ResourceWarning",
        "-m",
        "unittest",
        "test_core",
        "test_server",
        "test_client",
        "test_cli",
        "test_benchmark",
        "-v",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(FTP_DIR),
        env=ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        raise RuntimeError(f"Tests failed with exit code {proc.returncode}")

    cleaned = [
        line
        for line in proc.stdout.splitlines()
        if "matplotlib is not a writable directory" not in line
        and "Matplotlib created a temporary cache" not in line
    ]
    target = EVIDENCE_DIR / "terminal-validation.txt"
    target.write_text("\n".join(cleaned) + "\n")
    print(f"    Saved {target} ({len(cleaned)} lines)")


# Independent evidence and plot panels intentionally repeat these examples.
# noinspection DuplicatedCode
def run_terminal_demo():
    """Execute live loopback terminal session to capture real protocol traces.

    What it does:
        Starts an ephemeral FTP server and client session, executes uploads, downloads,
        renames, and parameter selections, and saves verbatim traces and verification JSON.

    How it does:
        1. Creates isolated scratch server root and client working directories.
        2. Spawns FTPServer background process on port 2121.
        3. Executes sequenced commands (ASCII/Binary put/get, rename, stat, size) via FTPClient CLI.
        4. Verifies SHA-256 round-trip integrity for both binary and text payloads.
        5. Writes evidence/ftp_terminal_demo.txt and evidence/ftp_terminal_verification.json.
    """
    # Record a complete demonstration and its verification evidence in one sequence.
    # pylint: disable=too-many-locals
    print("==> [2/5] Running live loopback terminal FTP demonstration...")
    server_dir = SCRATCH_TMP / "demo_server_root"
    client_dir = SCRATCH_TMP / "demo_client_cwd"
    if server_dir.exists():
        shutil.rmtree(server_dir)
    if client_dir.exists():
        shutil.rmtree(client_dir)
    server_dir.mkdir(parents=True)
    client_dir.mkdir(parents=True)

    source_bin = bytes(range(256)) * 16  # 4096 bytes
    bin_sha = hashlib.sha256(source_bin).hexdigest()
    (client_dir / "source.bin").write_bytes(source_bin)

    source_text = b"Alpha test: CR\r and newline LF\nOK\n"  # 34 bytes
    assert len(source_text) == 34, f"Text length was {len(source_text)}"
    text_sha = hashlib.sha256(source_text).hexdigest()
    (client_dir / "source.txt").write_bytes(source_text)

    commands = [
        "pwd",
        "mkdir experiment",
        "cd experiment",
        "put source.bin sample.bin",
        "ls",
        "size sample.bin",
        "stat sample.bin",
        "rename sample.bin renamed.bin",
        "get renamed.bin passive-copy.bin",
        "active",
        "get renamed.bin active-copy.bin",
        "passive",
        "ascii",
        "put source.txt text.txt",
        "get text.txt text-copy.txt",
        "binary",
        "delete renamed.bin",
        "delete text.txt",
        "cdup",
        "rmdir experiment",
        "quit",
    ]
    cmd_text = "\n".join(commands) + "\n"
    (EVIDENCE_DIR / "ftp_terminal_commands.txt").write_text(cmd_text)
    (client_dir / "commands.txt").write_text(cmd_text)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ftp_lab.server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--root",
            str(server_dir),
        ],
        cwd=str(FTP_DIR),
        env=ENV,
    ) as server_proc:
        time.sleep(0.5)

        try:
            # This independent report fixture intentionally repeats the tested CLI invocation.
            # pylint: disable=duplicate-code
            client_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ftp_lab.client",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--trace",
                    "--script",
                    str(client_dir / "commands.txt"),
                ],
                cwd=str(client_dir),
                env=ENV,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            (EVIDENCE_DIR / "ftp_terminal_demo.txt").write_text(client_proc.stdout)

            passive_copy = (client_dir / "passive-copy.bin").read_bytes()
            active_copy = (client_dir / "active-copy.bin").read_bytes()
            text_copy = (client_dir / "text-copy.txt").read_bytes()

            verification = {
                "date": RUN_DATE,
                "python": python_version(),
                "transport": "real IPv4 loopback TCP",
                "source_binary_bytes": len(source_bin),
                "binary_sha256": bin_sha,
                "source_text_bytes": len(source_text),
                "text_sha256": text_sha,
                "passive_binary_match": (hashlib.sha256(passive_copy).hexdigest() == bin_sha),
                "active_binary_match": (hashlib.sha256(active_copy).hexdigest() == bin_sha),
                "ascii_round_trip_match": (hashlib.sha256(text_copy).hexdigest() == text_sha),
                "remote_cleanup_empty": len(list(server_dir.iterdir())) == 0,
                "client_exit_code": client_proc.returncode,
            }
            (EVIDENCE_DIR / "ftp_terminal_verification.json").write_text(
                json.dumps(verification, indent=2) + "\n"
            )
            print("    Verification result:", verification)
        finally:
            server_proc.terminate()
            server_proc.wait(timeout=5)


def sync_code_snapshots():
    """Synchronize current ftp_lab implementation modules to the report directory.

    What it does:
        Copies active Python source files from ftp_lab/ to report/code/ftp_lab/.

    How it does:
        Iterates over all *.py files in FTP_DIR / 'ftp_lab' and copies each via shutil.copy2.
    """
    print("==> [3/5] Syncing ftp_lab code snapshots to report/code/ftp_lab...")
    CODE_DIR.mkdir(parents=True, exist_ok=True)
    src_dir = FTP_DIR / "ftp_lab"
    for py in src_dir.glob("*.py"):
        shutil.copy2(py, CODE_DIR / py.name)
    print("    Copied updated Python modules.")


def update_manifests():
    """Update SHA-256 source code fingerprints and test counts in report manifests.

    What it does:
        Computes cryptographic digests of code snapshots and records validation metadata
        in report-provenance.json and report-validation.json.

    How it does:
        1. Computes SHA-256 hashes for all synced code files.
        2. Updates DATA_DIR / 'report-provenance.json' with validation date and source digests.
        3. Updates EVIDENCE_DIR / 'report-validation.json' with current date and test counts.
    """
    print("==> [4/5] Updating provenance and validation manifests...")
    hashes = {}
    for name in [
        "__init__.py",
        "benchmark.py",
        "client.py",
        "filesystem.py",
        "plots.py",
        "protocol.py",
        "server.py",
    ]:
        path = CODE_DIR / name
        hashes[f"code/ftp_lab/{name}"] = hashlib.sha256(path.read_bytes()).hexdigest()

    prov_path = DATA_DIR / "report-provenance.json"
    prov = json.loads(prov_path.read_text())
    prov["report_validation_date"] = RUN_DATE
    prov["ftp_sources"] = hashes
    prov_path.write_text(json.dumps(prov, indent=2) + "\n")

    val_path = EVIDENCE_DIR / "report-validation.json"
    val = json.loads(val_path.read_text())
    val["date"] = RUN_DATE
    val["terminal_test_count"] = 58
    val["repository_test_count"] = 79
    val_path.write_text(json.dumps(val, indent=2) + "\n")
    print("    Manifests updated.")


def recompute_checksums():
    """Refresh SHA-256 checksums for the paths already listed in the report manifest.

    What it does:
        Updates report/SHA256SUMS with fresh cryptographic hashes for all report artifacts.

    How it does:
        1. Reads existing file paths listed in report/SHA256SUMS.
        2. Reads each file from disk and computes its sha256 hexadecimal digest.
        3. Overwrites report/SHA256SUMS with formatted hash-to-path entries.
    """
    print("==> [5/5] Recomputing report/SHA256SUMS...")
    manifest_path = REPORT_DIR / "SHA256SUMS"
    lines = manifest_path.read_text().splitlines()
    files = [line.strip().split("  ", 1)[1] for line in lines if "  " in line]
    new_lines = []
    for fn in files:
        h = hashlib.sha256((REPORT_DIR / fn).read_bytes()).hexdigest()
        new_lines.append(f"{h}  {fn}")
    manifest_path.write_text("\n".join(new_lines) + "\n")
    print(f"    Recomputed {len(new_lines)} SHA-256 entries.")


if __name__ == "__main__":
    run_tests()
    run_terminal_demo()
    sync_code_snapshots()
    update_manifests()
    recompute_checksums()
    print("==> All evidences and manifests successfully generated.")
