#!/usr/bin/env python3
"""Figures for the CDMA report (EV6).

Reads only eval/EVAL_E1.csv, eval/EVAL_E3.csv and tools/walsh_dump output.
Writes eval/fig_*.png. Colours: Okabe-Ito-style blue/orange plus neutrals,
checked with tools/validate_palette (dataviz skill); ink is never series colour.
"""
import csv
import os
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

EVAL = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(EVAL)
BIN_WALSH = os.path.join(ROOT, "tools", "walsh_dump")
E1_CSV = os.path.join(EVAL, "EVAL_E1.csv")
E3_CSV = os.path.join(EVAL, "EVAL_E3.csv")

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GREY, LIGHT, INK, MUTED = "#8c8b86", "#d4d3cd", "#0b0b0b", "#52514e"
plt.rcParams.update({
    "font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlecolor": INK,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": LIGHT, "grid.linewidth": 0.6,
    "figure.dpi": 110, "savefig.bbox": "tight",
})


def walsh(*args):
    """Run tools/walsh_dump and return its CSV rows as lists of strings."""
    out = subprocess.run([BIN_WALSH, *args], capture_output=True, text=True,
                         timeout=30, check=True).stdout
    return [line.split(",") for line in out.strip().splitlines()]


def read_csv(path):
    if not os.path.isfile(path):
        sys.exit(f"plot: missing {path} (run eval/run_eval.py first)")
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def save(fig, name):
    path = os.path.join(EVAL, name)
    fig.savefig(path)
    plt.close(fig)
    print("wrote", path)


def fig_walsh():
    W = np.array([[int(v) for v in r] for r in walsh("8")])
    G = np.array([[int(v) for v in r] for r in walsh("--gram", "8")])
    fig, axes = plt.subplots(1, 2, figsize=(9, 4.2))
    panels = [(axes[0], W, "$W_8$ (entries +1 / -1)", "RdBu_r", -1, 1),
              (axes[1], G, "$W_8 W_8^T = 8I$ (dot products)", "viridis", 0, 8)]
    for ax, M, title, cmap, lo, hi in panels:
        im = ax.imshow(M, cmap=cmap, vmin=lo, vmax=hi)
        ax.set_title(title, loc="left")
        ax.set_xlabel("chip index k")
        if ax is axes[0]:   # shared row axis; a second label collides with the colorbar
            ax.set_ylabel("code index i")
        ax.set_xticks(range(M.shape[1]))
        ax.set_yticks(range(M.shape[0]))
        ax.grid(False)
        for i in range(M.shape[0]):
            for j in range(M.shape[1]):
                saturated = im.norm(M[i, j]) > 0.6 or im.norm(M[i, j]) < 0.2
                ax.text(j, i, f"{M[i, j]:+d}" if lo < 0 else f"{M[i, j]}",
                        ha="center", va="center", fontsize=7,
                        color="white" if saturated else INK)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.suptitle("Walsh codes: rows are orthogonal (Sylvester construction, L=8)",
                 x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    save(fig, "fig_walsh.png")


def fig_e1_decisions(rows):
    ns = sorted({int(r["n"]) for r in rows})
    keys = [("bits_one", "ONE", BLUE), ("bits_zero", "ZERO", ORANGE),
            ("bits_silent", "SILENT", LIGHT), ("bits_invalid", "INVALID", INK)]
    totals = {k: [] for k, _, _ in keys}
    grand = []
    for n in ns:
        rs = [r for r in rows if int(r["n"]) == n]
        sums = {k: sum(int(r[k]) for r in rs) for k, _, _ in keys}
        tot = sum(sums.values())
        grand.append(tot)
        for k, _, _ in keys:
            totals[k].append(100.0 * sums[k] / tot if tot else 0.0)
    fig, ax = plt.subplots(figsize=(8, 4.2))
    x = np.arange(len(ns))
    bottom = np.zeros(len(ns))
    for k, label, colour in keys:
        vals = np.array(totals[k])
        ax.bar(x, vals, bottom=bottom, color=colour, label=label, width=0.7,
               edgecolor="white", linewidth=0.8)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels([str(n) for n in ns])
    ax.set_xlabel("number of stations n")
    ax.set_ylabel("share of per-bit decisions (%)")
    ax.set_ylim(0, 100)
    ax.grid(axis="x", visible=False)
    inv = sum(int(r["bits_invalid"]) for r in rows)
    ax.set_title(f"E1 decisions per n (all receivers): INVALID total = {inv}",
                 loc="left")
    ax.legend(ncols=4, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    save(fig, "fig_e1_decisions.png")


def fig_e3_analytic():
    rows = walsh("--lengths", "64")[1:]
    ns = np.array([int(r[0]) for r in rows])
    L = np.array([int(r[1]) for r in rows])
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.step(ns, 1.0 / L, where="post", color=BLUE, label="per-station rate 1/L")
    ax.step(ns, ns / L, where="post", color=ORANGE, label="code utilisation n/L")
    for k in range(1, 7):
        m = 2 ** k + 1
        if m <= 64:
            ax.axvline(m, color=MUTED, linestyle=":", linewidth=0.8)
            ax.text(m + 0.3, 1.02, f"{m}", color=MUTED, fontsize=8, va="bottom")
    ax.set_xlabel("number of stations n")
    ax.set_ylabel("fraction")
    ax.set_ylim(0, 1.1)
    ax.set_xlim(1, 64)
    ax.set_title("E2a analytic: L = next power of 2 >= n (dotted: n = 2^k + 1)",
                 loc="left")
    ax.legend(frameon=False, loc="lower right", bbox_to_anchor=(1.0, 0.12))
    save(fig, "fig_e3_analytic.png")


def fig_e3_goodput(rows):
    by_n = {}
    for r in rows:
        by_n.setdefault(int(r["n"]), []).append(r)
    ns = sorted(by_n)
    def stats(key):
        vals = [[float(r[key]) for r in by_n[n]] for n in ns]
        return (np.array([np.mean(v) for v in vals]),
                np.array([np.min(v) for v in vals]),
                np.array([np.max(v) for v in vals]))
    st_mean, st_min, st_max = stats("goodput_station_bps")
    tot_mean, tot_min, tot_max = stats("goodput_total_bps")
    Ls = np.array([int(by_n[n][0]["L"]) for n in ns])
    ns_arr = np.array(ns, dtype=float)
    shape = 1.0 / (ns_arr * Ls)
    shape = shape / shape[0] * st_mean[0]          # normalised at n = 1
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharex=True)
    for ax, mean, lo, hi, title, pred in [
        (axes[0], st_mean, st_min, st_max, "per-station goodput", shape),
        (axes[1], tot_mean, tot_min, tot_max, "total goodput", shape * ns_arr),
    ]:
        ax.errorbar(ns_arr, mean, yerr=[mean - lo, hi - mean], fmt="o-",
                    color=BLUE, ecolor=BLUE, capsize=3, label="measured (mean, min-max)")
        ax.plot(ns_arr, pred, "--", color=ORANGE,
                label="predicted shape 1/(n·L), normalised at n=1" if ax is axes[0]
                else "n × predicted per-station shape")
        ax.set_xscale("log", base=2)
        ax.set_xticks(ns_arr)
        ax.set_xticklabels([str(n) for n in ns])
        ax.set_xlabel("number of stations n (log scale)")
        ax.set_ylabel("bits per second (loopback)")
        ax.set_title(title, loc="left")
        ax.set_ylim(bottom=0)
        ax.legend(frameon=False, fontsize=8)
    fig.suptitle("E2b measured goodput: simulation throughput on loopback, shape is the result",
                 x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))   # keep the suptitle clear of panel titles
    save(fig, "fig_e3_goodput.png")


def fig_e3_round_cost(rows):
    x = np.array([int(r["n"]) * int(r["L"]) for r in rows], dtype=float)
    y = np.array([float(r["round_us"]) for r in rows])
    a, b = np.polyfit(x, y, 1)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.scatter(x, y, color=BLUE, s=28, label="runs (3 trials per n)")
    xs = np.linspace(0, x.max() * 1.05, 100)
    ax.plot(xs, a * xs + b, color=ORANGE, linewidth=1.6,
            label=f"linear fit: {a:.3f}·(n·L) + {b:.1f} µs")
    ax.set_xlabel("n · L (chips per round per sender, scaled by n)")
    ax.set_ylabel("channel time per round (µs)")
    ax.set_ylim(bottom=0)
    ax.set_xlim(left=0)
    ax.set_title("E2b channel cost per round is roughly linear in n·L", loc="left")
    ax.legend(frameon=False)
    save(fig, "fig_e3_round_cost.png")


def main():
    e1 = read_csv(E1_CSV)
    e3 = read_csv(E3_CSV)
    fig_walsh()
    fig_e1_decisions(e1)
    fig_e3_analytic()
    fig_e3_goodput(e3)
    fig_e3_round_cost(e3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
