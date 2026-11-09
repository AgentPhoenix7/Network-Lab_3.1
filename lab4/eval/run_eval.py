#!/usr/bin/env python3
"""Evaluation driver for CDMA: EXPERIMENTS.md E1 (correctness) and E3 (efficiency).

Python only generates input files, launches the built binaries, parses their
key=value metrics and writes the CSVs (CLAUDE.md R1). Every subprocess is
polled against a deadline, and every child is killed in a finally block.

  python3 eval/run_eval.py --quick [--seed S]   small sweep, finishes < 60 s
  python3 eval/run_eval.py [--seed S]           full E1 + E3b sweep

Exits non-zero if any run fails a check.
"""
import argparse
import csv
import math
import os
import random
import shutil
import subprocess
import sys
import time

EVAL = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(EVAL)
DATA = os.path.join(EVAL, "data")
BIN_CHANNEL = os.path.join(ROOT, "channel")
BIN_SENDER = os.path.join(ROOT, "sender")
BIN_RECEIVER = os.path.join(ROOT, "receiver")
BIN_WALSH = os.path.join(ROOT, "tools", "walsh_dump")

PORT_BASE = 9400          # run i uses 9400 + (i mod 500)
PORT_SPAN = 500
RUN_TIMEOUT = 200         # seconds for one whole run (channel + all stations)
FRAME_PAYLOAD = 44        # bytes of user data per frame (lab1 frame_t)
FRAME_BITS = 65 * 8       # 520 bits per spread frame (FRAME_SIZE * 8)
TRIALS = 3

E1_HEADER = ["n", "L", "trial", "seed", "station", "file_bytes", "rounds",
             "frames_ok", "fcs_fail", "seq_errors", "error_frames", "silent_rounds",
             "bits_one", "bits_zero", "bits_silent", "bits_invalid",
             "max_abs_sum", "match"]
E3_HEADER = ["n", "L", "trial", "seed", "file_bytes", "rounds", "elapsed_s", "sum_s",
             "round_us", "goodput_station_bps", "goodput_total_bps", "chip_rate_cps",
             "code_util"]

E1_NS = [1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32]
E1_NS_QUICK = [1, 2, 3, 5, 8]
E3_NS = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64]
E3_NS_QUICK = [1, 2, 4, 8]
E3_BYTES = 16 * 1024
E3_BYTES_QUICK = 2048


def check_binaries():
    missing = [p for p in (BIN_CHANNEL, BIN_SENDER, BIN_RECEIVER, BIN_WALSH)
               if not os.path.isfile(p)]
    if missing:
        sys.exit("run_eval: missing binaries (run `make eval` or `make all`): "
                 + ", ".join(missing))


def read_metrics(path):
    """Parse a key=value file into a dict of ints (empty dict if missing)."""
    if not os.path.isfile(path):
        return {}
    out = {}
    with open(path) as f:
        for line in f:
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                out[k] = int(v)
    return out


def run_system(workdir, n, port, in_paths, out_paths, late_id=-1, late_rounds=0):
    """Run one channel, n senders and n receivers.

    Returns (ok, chan_metrics, recv_metrics_list, matches, notes). ok is False
    if any process exited non-zero or timed out; metrics may then be partial.
    """
    os.makedirs(workdir, exist_ok=True)
    chan_m = os.path.join(workdir, "channel.metrics")
    recv_m = [os.path.join(workdir, f"r{i}.metrics") for i in range(n)]
    procs, logs, named = [], [], []

    def launch(args, name):
        log = open(os.path.join(workdir, name + ".log"), "w")
        logs.append(log)
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT)
        procs.append(p)
        named.append((name, p))

    notes = []
    try:
        # Channel first; station_join retries its connect for ~5 s, so no sleep.
        launch([BIN_CHANNEL, "--port", str(port), "--n", str(n),
                "--metrics-file", chan_m], "channel")
        for i in range(n):
            s_args = [BIN_SENDER, "--port", str(port), "--id", str(i),
                      "--in", in_paths[i]]
            if i == late_id:
                s_args += ["--start-round", str(late_rounds)]
            launch(s_args, f"sender{i}")
            launch([BIN_RECEIVER, "--port", str(port), "--id", str(i),
                    "--out", out_paths[i], "--metrics-file", recv_m[i]],
                   f"receiver{i}")

        deadline = time.monotonic() + RUN_TIMEOUT
        while True:
            codes = {name: p.poll() for name, p in named}
            if any(c not in (None, 0) for c in codes.values()):
                break                       # a failure: stop waiting, kill the rest
            if all(c == 0 for c in codes.values()):
                break
            if time.monotonic() > deadline:
                notes.append(f"timeout after {RUN_TIMEOUT} s")
                break
            time.sleep(0.02)
        for name, p in named:
            c = p.poll()
            if c is None or c != 0:
                notes.append(f"{name} exit {c}")
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                notes.append(f"pid {p.pid} did not die after SIGKILL")
        for f in logs:
            f.close()

    chan = read_metrics(chan_m)
    recs = [read_metrics(p) for p in recv_m]
    matches = []
    for i in range(n):
        same = os.path.isfile(out_paths[i]) and _same_bytes(in_paths[i], out_paths[i])
        matches.append(int(same))
    ok = not notes and bool(chan) and all(recs)
    return ok, chan, recs, matches, notes


def _same_bytes(a, b):
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def make_e1_inputs(n, seed, quick, folder):
    """Seeded random inputs: sizes 1000 + 997*i + rand(0..43) (quick: smaller)."""
    rng = random.Random(seed)
    os.makedirs(folder, exist_ok=True)
    in_paths, sizes = [], []
    for i in range(n):
        base = 120 + 97 * i if quick else 1000 + 997 * i
        size = base + rng.randint(0, 43)
        path = os.path.join(folder, f"in{i}.bin")
        with open(path, "wb") as f:
            f.write(rng.randbytes(size))
        in_paths.append(path)
        sizes.append(size)
    return in_paths, sizes


def make_e3_inputs(n, seed, size, folder):
    rng = random.Random(seed)
    os.makedirs(folder, exist_ok=True)
    in_paths = []
    for i in range(n):
        path = os.path.join(folder, f"in{i}.bin")
        with open(path, "wb") as f:
            f.write(rng.randbytes(size))
        in_paths.append(path)
    return in_paths


def e1_checks(n, sizes, chan, recs, matches):
    fails = []
    if chan.get("max_abs_sum", n + 1) > n:
        fails.append(f"max_abs_sum={chan.get('max_abs_sum')} > n")
    for i in range(n):
        r, size = recs[i], sizes[i]
        if matches[i] != 1:
            fails.append(f"station {i} output differs")
        for key in ("fcs_fail", "seq_errors", "error_frames", "bits_invalid"):
            if r.get(key) != 0:
                fails.append(f"station {i} {key}={r.get(key)}")
        expect = -(-size // FRAME_PAYLOAD)
        if r.get("frames_ok") != expect:
            fails.append(f"station {i} frames_ok={r.get('frames_ok')} != {expect}")
        if r.get("silent_rounds") != r.get("rounds", -1) - r.get("frames_ok", -1):
            fails.append(f"station {i} silent_rounds accounting")
    return fails


def sweep_e1(ns, trials, seed0, quick, run_idx):
    rows, fails = [], []
    for n in ns:
        for t in trials:
            seed = seed0 + 1000 * n + t
            tag = f"e1_n{n}_t{t}"
            folder = os.path.join(DATA, tag)
            in_paths, sizes = make_e1_inputs(n, seed, quick, folder)
            out_paths = [os.path.join(folder, f"out{i}.bin") for i in range(n)]
            late = n - 1 if n >= 2 else -1
            port = PORT_BASE + (run_idx % PORT_SPAN)
            run_idx += 1
            ok, chan, recs, matches, notes = run_system(
                os.path.join(folder, "run"), n, port, in_paths, out_paths,
                late_id=late, late_rounds=3)
            if not chan or not all(recs):
                fails.append(f"{tag}: no metrics ({'; '.join(notes) or 'missing'})")
                continue
            checks = e1_checks(n, sizes, chan, recs, matches)
            checks += notes
            if checks:
                fails.append(f"{tag}: " + "; ".join(checks))
            for i in range(n):
                r = recs[i]
                rows.append([n, chan["L"], t, seed, i, sizes[i], r["rounds"],
                             r["frames_ok"], r["fcs_fail"], r["seq_errors"],
                             r["error_frames"], r["silent_rounds"], r["bits_one"],
                             r["bits_zero"], r["bits_silent"], r["bits_invalid"],
                             chan["max_abs_sum"], matches[i]])
            print(f"  E1 n={n:<3} trial={t} {'PASS' if not checks else 'FAIL'}"
                  f"  L={chan['L']} rounds={chan['rounds']}", flush=True)
    return rows, fails, run_idx


def sweep_e3(ns, trials, seed0, size, run_idx):
    rows, fails = [], []
    for n in ns:
        for t in trials:
            seed = seed0 + 1000 * n + t
            tag = f"e3_n{n}_t{t}"
            folder = os.path.join(DATA, tag)
            in_paths = make_e3_inputs(n, seed, size, folder)
            out_paths = [os.path.join(folder, f"out{i}.bin") for i in range(n)]
            port = PORT_BASE + (run_idx % PORT_SPAN)
            run_idx += 1
            ok, chan, recs, matches, notes = run_system(
                os.path.join(folder, "run"), n, port, in_paths, out_paths)
            if not chan or not all(recs):
                fails.append(f"{tag}: no metrics ({'; '.join(notes) or 'missing'})")
                continue
            checks = list(notes)
            if not all(m == 1 for m in matches):
                checks.append("output differs")
            for i, r in enumerate(recs):
                for key in ("fcs_fail", "seq_errors", "error_frames", "bits_invalid"):
                    if r.get(key) != 0:
                        checks.append(f"station {i} {key}={r.get(key)}")
                if r.get("frames_ok") != -(-size // FRAME_PAYLOAD):
                    checks.append(f"station {i} frames_ok={r.get('frames_ok')}")
            if chan.get("elapsed_ns", 0) <= 0 or chan.get("rounds", 0) <= 0:
                checks.append("non-positive elapsed or rounds")
            if checks:
                fails.append(f"{tag}: " + "; ".join(checks))
                continue
            n_, L = chan["n"], chan["L"]
            el_s = chan["elapsed_ns"] / 1e9
            rounds = chan["rounds"]
            goodput_station = size * 8 / el_s
            rows.append([n_, L, t, seed, size, rounds,
                         f"{el_s:.6f}", f"{chan['sum_ns'] / 1e9:.6f}",
                         f"{chan['elapsed_ns'] / rounds / 1000:.3f}",
                         f"{goodput_station:.3f}", f"{n_ * goodput_station:.3f}",
                         f"{rounds * FRAME_BITS * L / el_s:.3f}",
                         f"{n_ / L:.6f}"])
            print(f"  E3 n={n:<3} trial={t} PASS  L={L} rounds={rounds}"
                  f" elapsed={el_s:.3f}s", flush=True)
    return rows, fails, run_idx


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true", help="small sweep (< 60 s)")
    ap.add_argument("--seed", type=int, default=42, help="master seed (default 42)")
    args = ap.parse_args()
    check_binaries()

    shutil.rmtree(DATA, ignore_errors=True)   # eval/data is regenerated, never kept
    os.makedirs(DATA)

    t0 = time.monotonic()
    trials = [0] if args.quick else list(range(TRIALS))
    e1_ns = E1_NS_QUICK if args.quick else E1_NS
    e3_ns = E3_NS_QUICK if args.quick else E3_NS
    e3_size = E3_BYTES_QUICK if args.quick else E3_BYTES

    print("E1 correctness sweep")
    e1_rows, e1_fails, idx = sweep_e1(e1_ns, trials, args.seed, args.quick, 0)
    print("E3b throughput sweep")
    e3_rows, e3_fails, idx = sweep_e3(e3_ns, trials, args.seed, e3_size, idx)

    write_csv(os.path.join(EVAL, "EVAL_E1.csv"), E1_HEADER, e1_rows)
    write_csv(os.path.join(EVAL, "EVAL_E3.csv"), E3_HEADER, e3_rows)

    print("\nSummary")
    for n in e1_ns:
        rs = [r for r in e1_rows if r[0] == n]
        if not rs:
            continue
        match = 100.0 * sum(r[17] for r in rs) / len(rs)
        inval = sum(r[15] for r in rs)
        frames = sum(r[7] for r in rs)
        print(f"  E1 n={n:<3} L={rs[0][1]:<3} stations={len(rs):<4} frames={frames:<6}"
              f" match={match:5.1f}% invalid_bits={inval}")
    print(f"  E3 rows={len(e3_rows)} (n in {e3_ns}, {e3_size} B per station)")
    print(f"  elapsed {time.monotonic() - t0:.1f} s, seed={args.seed}")

    fails = e1_fails + e3_fails
    if fails:
        print(f"\nFAILED ({len(fails)}):")
        for f in fails:
            print("  " + f)
        return 1
    print("\nALL CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
