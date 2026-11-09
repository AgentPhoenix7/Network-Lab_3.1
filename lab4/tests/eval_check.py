#!/usr/bin/env python3
"""Validate eval/EVAL_E1.csv and eval/EVAL_E3.csv (tests.md EV2, EV3, EV4).

Headers are read from the code blocks in EXPERIMENTS.md, so the CSVs must match
the documented schema exactly. L is compared against tools/walsh_dump --lengths,
never recomputed here (CLAUDE.md R1). Exits non-zero on any failure.
"""
import csv
import math
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(ROOT, "eval")
BIN_WALSH = os.path.join(ROOT, "tools", "walsh_dump")
E1_CSV = os.path.join(EVAL, "EVAL_E1.csv")
E3_CSV = os.path.join(EVAL, "EVAL_E3.csv")
EXPERIMENTS = os.path.join(ROOT, "EXPERIMENTS.md")


def documented_header(csv_name):
    """Header from the fenced block after the `eval/<csv_name>` heading."""
    with open(EXPERIMENTS) as f:
        text = f.read()
    i = text.find(f"`eval/{csv_name}`")
    if i < 0:
        raise SystemExit(f"eval_check: EXPERIMENTS.md has no schema for {csv_name}")
    start = text.index("```", i) + 3
    end = text.index("```", start)
    return "".join(text[start:end].split()).split(",")


def walsh_lengths(N):
    out = subprocess.run([BIN_WALSH, "--lengths", str(N)], capture_output=True,
                         text=True, timeout=30, check=True).stdout
    rows = [line.split(",") for line in out.strip().splitlines()[1:]]
    return {int(n): int(L) for n, L in rows}


def read_rows(path, header):
    if not os.path.isfile(path):
        raise SystemExit(f"eval_check: missing {path}")
    with open(path, newline="") as f:
        reader = csv.reader(f)
        got = next(reader, None)
        if got != header:
            raise SystemExit(f"eval_check: {os.path.basename(path)} header\n"
                             f"  got      {got}\n  expected {header}")
        return [dict(zip(header, r)) for r in reader]


def check_e1(rows, lengths):
    fails = []
    if not rows:
        return ["EVAL_E1.csv has no rows"]
    groups = {}
    for r in rows:
        n, L = int(r["n"]), int(r["L"])
        tag = f"E1 n={n} trial={r['trial']} station={r['station']}"
        if L != lengths.get(n):
            fails.append(f"{tag}: L={L} != walsh_dump {lengths.get(n)}")
        if int(r["match"]) != 1:
            fails.append(f"{tag}: match={r['match']}")
        for k in ("fcs_fail", "seq_errors", "error_frames", "bits_invalid"):
            if int(r[k]) != 0:
                fails.append(f"{tag}: {k}={r[k]}")
        if int(r["max_abs_sum"]) > n:
            fails.append(f"{tag}: max_abs_sum={r['max_abs_sum']} > n")
        frames, size = int(r["frames_ok"]), int(r["file_bytes"])
        if frames != math.ceil(size / 44):
            fails.append(f"{tag}: frames_ok={frames} != ceil({size}/44)")
        if int(r["silent_rounds"]) != int(r["rounds"]) - frames:
            fails.append(f"{tag}: silent_rounds != rounds - frames_ok")
        groups.setdefault((n, r["trial"]), set()).add(int(r["station"]))
    for (n, t), stations in groups.items():
        if stations != set(range(n)):
            fails.append(f"E1 n={n} trial={t}: stations {sorted(stations)} != 0..{n-1}")
    return fails


def check_e3(rows, lengths):
    fails = []
    if not rows:
        return ["EVAL_E3.csv has no rows"]
    for r in rows:
        n, L = int(r["n"]), int(r["L"])
        tag = f"E3 n={n} trial={r['trial']}"
        if L != lengths.get(n):
            fails.append(f"{tag}: L={L} != walsh_dump {lengths.get(n)}")
        if abs(float(r["code_util"]) - n / L) > 1e-6:
            fails.append(f"{tag}: code_util={r['code_util']} != n/L")
        for k in ("elapsed_s", "round_us", "rounds", "file_bytes"):
            if float(r[k]) <= 0:
                fails.append(f"{tag}: {k}={r[k]} must be > 0")
    return fails


def main():
    lengths = walsh_lengths(64)
    e1_rows = read_rows(E1_CSV, documented_header("EVAL_E1.csv"))
    e3_rows = read_rows(E3_CSV, documented_header("EVAL_E3.csv"))
    fails = check_e1(e1_rows, lengths) + check_e3(e3_rows, lengths)
    print(f"EVAL_E1.csv: {len(e1_rows)} rows, EVAL_E3.csv: {len(e3_rows)} rows")
    if fails:
        print(f"eval_check: {len(fails)} failure(s)")
        for f in fails[:50]:
            print("  " + f)
        return 1
    print("eval_check: PASS (EV2, EV3, EV4)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
