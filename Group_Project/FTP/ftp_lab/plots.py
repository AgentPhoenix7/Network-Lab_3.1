"""Optional standalone PNG/SVG exports for measured FTP benchmarks."""

from pathlib import Path
from typing import Any

MIB = 1024 * 1024


# Independent evidence and plot panels intentionally repeat these examples.
# noinspection DuplicatedCode
def render_graphs(results: list[dict[str, Any]], output: Path | str):
    """Save standalone PNG and SVG figures summarizing benchmark performance.

    What it does:
        Renders Matplotlib charts for throughput, payload compression, phase durations,
        and cumulative upload time-series that express clear, meaningful insights.

    How it does:
        1. compression: Compares original vs. gzip payload sizes with percentage savings,
           using separate panels for large raw media and compressed control files.
        2. throughput: Visualizes wire transfer rate vs. end-to-end effective rate, highlighting
           the loopback CPU compression overhead.
        3. durations: Stacked breakdown across preparation, upload, download, and verification
            phases.
        4. time-series: Multi-panel upload progress curves, independently scaled per input media.
    """
    # Keep chart construction, annotations, and export in the same rendering scope.
    # pylint: disable=too-many-statements
    # Keep the app and transfer CLIs usable without the optional plotting dependency.
    # pylint: disable=import-outside-toplevel
    from matplotlib.backends.backend_agg import (
        FigureCanvasAgg,
    )
    from matplotlib.figure import Figure

    # pylint: enable=import-outside-toplevel

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    colors = {
        'plain': '#2b5c8f',
        'gzip': '#2a9d8f',
        'effective': '#e76f51',
        'prep': '#e76f51',
        'upload': '#2b5c8f',
        'download': '#457b9d',
        'verif': '#6a4c93',
    }

    def save_fig(fig, figure_name):
        """Render and save a Matplotlib figure as dual PNG and SVG image files.

        What it does:
            Renders the provided matplotlib Figure instance and writes both PNG (160 DPI)
            and vector SVG graphics to the benchmark output directory.

        How it does:
            1. Binds FigureCanvasAgg to the figure.
            2. Invokes fig.savefig() for '{figure_name}.png' with dpi=160 and bbox_inches='tight'.
            3. Invokes fig.savefig() for '{figure_name}.svg' with bbox_inches='tight'.
            4. Clears the figure with fig.clear() to release memory.
        """
        FigureCanvasAgg(fig)
        fig.savefig(output_dir / f"{figure_name}.png", dpi=160, bbox_inches="tight")
        fig.savefig(output_dir / f"{figure_name}.svg", bbox_inches="tight")
        fig.clear()

    # Plot panels and archived report snapshots share this rendering structure.
    # noinspection DuplicatedCode
    def summarize():
        """Group repeated measurements and progress samples by source file."""
        # Extract source metadata and runs
        source_names = []
        grouped: dict[str, Any] = {}
        for r in results:
            src = r.get("source_name", "unknown")
            if src not in grouped:
                source_names.append(src)
                grouped[src] = {
                    "plain_payload": [],
                    "gzip_payload": [],
                    "plain_net": [],
                    "plain_eff": [],
                    "gzip_net": [],
                    "gzip_eff": [],
                    "plain_phases": {"prep": [], "xfer": [], "down": [], "verif": []},
                    "gzip_phases": {"prep": [], "xfer": [], "down": [], "verif": []},
                    "samples": [],
                }
            for run in r.get("runs", []):
                m = run.get("mode", "plain")
                p_mib = run.get("payload_bytes", 0) / MIB
                grouped[src][f"{m}_payload"].append(p_mib)
                grouped[src][f"{m}_net"].append(run.get("throughput_mib_s", 0))
                grouped[src][f"{m}_eff"].append(run.get("effective_mib_s", 0))
                phases = grouped[src][f"{m}_phases"]
                phases["prep"].append(run.get("preparation_seconds", 0))
                phases["xfer"].append(run.get("transfer_seconds", 0))
                phases["down"].append(run.get("download_seconds", 0))
                phases["verif"].append(run.get("verification_seconds", 0))
            if "samples" in r:
                grouped[src]["samples"].append((r.get("repeat", 1), r["samples"]))

        return source_names, grouped

    sources, data_by_src = summarize()

    def mean(lst: list[float], default: float = 0.0) -> float:
        """Calculate the arithmetic mean of a sequence of numeric values.

        What it does:
            Computes the average value of elements in a list, returning a default if empty.

        How it does:
            Sums all elements in lst and divides by len(lst) if non-empty, otherwise returns
                default.
        """
        return sum(lst) / len(lst) if lst else default

    # Plot panels and archived report snapshots share this rendering structure.
    # noinspection DuplicatedCode
    def compression():
        """Render the payload-size comparison panels."""
        # Keep chart construction, annotations, and export in the same rendering scope.
        # pylint: disable=too-many-locals
        # pylint: disable=too-many-statements
        large_srcs = [s for s in sources if mean(data_by_src[s]["plain_payload"]) > 10]
        small_srcs = [s for s in sources if mean(data_by_src[s]["plain_payload"]) <= 10]
        if large_srcs and small_srcs:
            fig_c = Figure(figsize=(10.5, 4.6))
            gs = fig_c.add_gridspec(
                1, 2, width_ratios=[len(large_srcs) * 1.3, len(small_srcs) * 1.0], wspace=0.28
            )
            ax1 = fig_c.add_subplot(gs[0, 0])
            ax2 = fig_c.add_subplot(gs[0, 1])

            x1 = list(range(len(large_srcs)))
            w = 0.35
            p1 = [mean(data_by_src[s]["plain_payload"]) for s in large_srcs]
            g1 = [mean(data_by_src[s]["gzip_payload"]) for s in large_srcs]
            b1 = ax1.bar(
                [i - w / 2 for i in x1],
                p1,
                w,
                label="Original / Plain",
                color=colors['plain'],
                edgecolor="black",
                linewidth=0.5,
            )
            b2 = ax1.bar(
                [i + w / 2 for i in x1],
                g1,
                w,
                label="Gzip Level 6",
                color=colors['gzip'],
                edgecolor="black",
                linewidth=0.5,
            )
            ax1.set_ylabel("Payload Size (MiB)", fontweight="bold")
            ax1.set_title(
                "(a) Large Raw Media (PCM Audio & Raw Video)", fontsize=10, fontweight="bold", pad=6
            )
            ax1.set_xticks(x1)
            ax1.set_xticklabels(large_srcs, fontweight="bold")
            ax1.set_ylim(0, max(p1) * 1.18 if p1 else 10)
            ax1.grid(axis="y", linestyle="--", alpha=0.5)
            for rectangle in b1:
                h = float(rectangle.get_height())
                ax1.text(
                    rectangle.get_x() + rectangle.get_width() / 2,
                    h + 2,
                    f"{h:.1f} MiB",
                    ha="center",
                    va="bottom",
                    fontsize=8.5,
                    fontweight="bold",
                )
            for i, rectangle in enumerate(b2):
                h = float(rectangle.get_height())
                saved = (1.0 - h / p1[i]) * 100 if p1[i] > 0 else 0
                ax1.text(
                    rectangle.get_x() + rectangle.get_width() / 2,
                    h + 2,
                    f"{h:.2f} MiB\n(-{saved:.1f}%)",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    fontweight="bold",
                    color="#1b4332",
                )
            ax1.legend(loc="upper right", framealpha=0.9)

            x2 = list(range(len(small_srcs)))
            p2 = [mean(data_by_src[s]["plain_payload"]) for s in small_srcs]
            g2 = [mean(data_by_src[s]["gzip_payload"]) for s in small_srcs]
            b3 = ax2.bar(
                [i - w / 2 for i in x2],
                p2,
                w,
                label="Original / Plain",
                color=colors['plain'],
                edgecolor="black",
                linewidth=0.5,
            )
            b4 = ax2.bar(
                [i + w / 2 for i in x2],
                g2,
                w,
                label="Gzip Level 6",
                color=colors['gzip'],
                edgecolor="black",
                linewidth=0.5,
            )
            ax2.set_ylabel("Payload Size (MiB)", fontweight="bold")
            ax2.set_title(
                "(b) Compressed Control (H.264 MP4)", fontsize=10, fontweight="bold", pad=6
            )
            ax2.set_xticks(x2)
            ax2.set_xticklabels(small_srcs, fontweight="bold")
            ax2.set_ylim(0, max(p2) * 1.35 if p2 else 5)
            ax2.grid(axis="y", linestyle="--", alpha=0.5)
            for rectangle in b3:
                h = float(rectangle.get_height())
                ax2.text(
                    rectangle.get_x() + rectangle.get_width() / 2,
                    h + 0.05,
                    f"{h:.3f} MiB",
                    ha="center",
                    va="bottom",
                    fontsize=8.5,
                    fontweight="bold",
                )
            for i, rectangle in enumerate(b4):
                h = float(rectangle.get_height())
                saved = (1.0 - h / p2[i]) * 100 if p2[i] > 0 else 0
                ax2.text(
                    rectangle.get_x() + rectangle.get_width() / 2,
                    h + 0.05,
                    f"{h:.3f} MiB\n(-{saved:.2f}%)",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    fontweight="bold",
                    color="#1b4332",
                )
            fig_c.suptitle(
                "FTP Payload Size: Plain vs. Gzip Level 6 Preprocessing",
                fontsize=11.5,
                fontweight="bold",
            )
        else:
            fig_c = Figure(figsize=(max(7, len(sources) * 2.2), 4.5))
            ax = fig_c.add_subplot()
            x = list(range(len(sources)))
            w = 0.35
            p = [mean(data_by_src[s]["plain_payload"]) for s in sources]
            g = [mean(data_by_src[s]["gzip_payload"]) for s in sources]
            b1 = ax.bar(
                [i - w / 2 for i in x],
                p,
                w,
                label="Original / Plain",
                color=colors['plain'],
                edgecolor="black",
                linewidth=0.5,
            )
            b2 = ax.bar(
                [i + w / 2 for i in x],
                g,
                w,
                label="Gzip Level 6",
                color=colors['gzip'],
                edgecolor="black",
                linewidth=0.5,
            )
            ax.set_ylabel("Payload (MiB)", fontweight="bold")
            ax.set_title(
                "FTP Payload Size: Plain vs. Gzip Level 6 Preprocessing",
                fontsize=11,
                fontweight="bold",
                pad=8,
            )
            ax.set_xticks(x)
            ax.set_xticklabels(sources, fontweight="bold")
            ax.legend()
            ax.grid(axis="y", linestyle="--", alpha=0.5)
            for rectangle in b1:
                h = float(rectangle.get_height())
                ax.text(
                    rectangle.get_x() + rectangle.get_width() / 2,
                    h + 0.02 * max(p or [1]),
                    f"{h:.1f} MiB",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    fontweight="bold",
                )
            for i, rectangle in enumerate(b2):
                h = float(rectangle.get_height())
                orig = p[i]
                saved = (1.0 - h / orig) * 100 if orig > 0 else 0
                ax.text(
                    rectangle.get_x() + rectangle.get_width() / 2,
                    h + 0.02 * max(p or [1]),
                    f"{h:.2f} MiB (-{saved:.1f}%)",
                    ha="center",
                    va="bottom",
                    fontsize=7.5,
                    fontweight="bold",
                    color="#1b4332",
                )
        save_fig(fig_c, "compression")

    # Plot panels and archived report snapshots share this rendering structure.
    # noinspection DuplicatedCode
    def throughput():
        """Render wire and effective upload throughput."""
        fig_t = Figure(figsize=(max(8.5, len(sources) * 2.6), 4.6))
        ax_t = fig_t.add_subplot()
        x_t = list(range(len(sources)))
        w_t = 0.26
        p_eff_m = [mean(data_by_src[s]["plain_eff"]) for s in sources]
        g_net_m = [mean(data_by_src[s]["gzip_net"]) for s in sources]
        g_eff_m = [mean(data_by_src[s]["gzip_eff"]) for s in sources]
        b1_t = ax_t.bar(
            [i - w_t for i in x_t],
            p_eff_m,
            w_t,
            label="Plain (Network = Effective)",
            color=colors['plain'],
            edgecolor="black",
            linewidth=0.5,
        )
        b2_t = ax_t.bar(
            x_t,
            g_net_m,
            w_t,
            label="Gzip Wire Rate (Payload / transfer s)",
            color=colors['gzip'],
            edgecolor="black",
            linewidth=0.5,
        )
        b3_t = ax_t.bar(
            [i + w_t for i in x_t],
            g_eff_m,
            w_t,
            label="Gzip Effective Rate (Source / total s)",
            color=colors['effective'],
            edgecolor="black",
            linewidth=0.5,
            hatch="//",
        )
        ax_t.set_ylabel("Throughput (MiB/s)", fontweight="bold")
        ax_t.set_title(
            "FTP Transfer Throughput: Network Wire Rate vs. End-to-End Effective Rate",
            fontsize=11,
            fontweight="bold",
            pad=8,
        )
        ax_t.set_xticks(x_t)
        ax_t.set_xticklabels(sources, fontweight="bold")
        max_tp = max(p_eff_m + g_net_m + g_eff_m or [1])
        ax_t.set_ylim(0, max_tp * 1.25)
        ax_t.grid(axis="y", linestyle="--", alpha=0.5)
        for rectangle in b1_t:
            h = float(rectangle.get_height())
            ax_t.text(
                rectangle.get_x() + rectangle.get_width() / 2,
                h + 0.02 * max_tp,
                f"{h:.0f}\nMiB/s",
                ha="center",
                va="bottom",
                fontsize=7.5,
                fontweight="bold",
            )
        for rectangle in b2_t:
            h = float(rectangle.get_height())
            ax_t.text(
                rectangle.get_x() + rectangle.get_width() / 2,
                h + 0.02 * max_tp,
                f"{h:.0f}\nMiB/s",
                ha="center",
                va="bottom",
                fontsize=7.5,
                fontweight="bold",
                color="#1b4332",
            )
        for rectangle in b3_t:
            h = float(rectangle.get_height())
            ax_t.text(
                rectangle.get_x() + rectangle.get_width() / 2,
                h + 0.02 * max_tp,
                f"{h:.0f}\nMiB/s",
                ha="center",
                va="bottom",
                fontsize=7.5,
                fontweight="bold",
                color="#9e2a2b",
            )
        ax_t.legend(loc="upper right", framealpha=0.92, fontsize=8.5)
        save_fig(fig_t, "throughput")

    # Plot panels and archived report snapshots share this rendering structure.
    # noinspection DuplicatedCode
    def durations():
        """Render stacked preparation, transfer, and verification durations."""
        # Keep chart construction, annotations, and export in the same rendering scope.
        # pylint: disable=too-many-locals
        fig_d = Figure(figsize=(max(9.0, len(sources) * 2.8), 4.8))
        ax_d = fig_d.add_subplot()
        labels_d = []
        positions_d = []
        bar_x_d = []
        totals_d = []
        idx_d = 0
        w_d = 0.38
        gap_d = 0.45
        phases = [
            ("prep", "Preparation / Gzip CPU Compression", colors['prep']),
            ("xfer", "FTP Upload (Socket Transfer)", colors['upload']),
            ("down", "FTP Verification Download", colors['download']),
            ("verif", "Hash & Decompress Verification", colors['verif']),
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
                    ax_d.bar(
                        pos,
                        val,
                        w_d,
                        bottom=bottom,
                        color=col,
                        label=lbl,
                        edgecolor="black",
                        linewidth=0.5,
                    )
                    bottom += val
                totals_d.append(bottom)
                idx_d += 1
        ax_d.set_ylabel("Total Duration (Seconds)", fontweight="bold")
        ax_d.set_title(
            "Phase Duration Breakdown: Gzip Preprocessing Overhead vs. Network Transfer",
            fontsize=11,
            fontweight="bold",
            pad=8,
        )
        ax_d.set_xticks(positions_d)
        ax_d.set_xticklabels(labels_d, fontsize=8, fontweight="bold")
        max_dur = max(totals_d or [1])
        ax_d.set_ylim(0, max_dur * 1.22)
        ax_d.grid(axis="y", linestyle="--", alpha=0.5)
        for x_pos, tot in zip(bar_x_d, totals_d):
            ax_d.text(
                x_pos,
                tot + 0.02 * max_dur,
                f"{tot:.2f} s",
                ha="center",
                va="bottom",
                fontsize=8,
                fontweight="bold",
            )
        ax_d.legend(loc="upper right", framealpha=0.92, fontsize=8)
        save_fig(fig_d, "durations")

    # Plot panels and archived report snapshots share this rendering structure.
    # noinspection DuplicatedCode
    def time_series():
        """Render cumulative upload progress for each input file."""
        # Keep chart construction, annotations, and export in the same rendering scope.
        # pylint: disable=too-many-locals
        num_s = max(1, len(sources))
        fig_ts = Figure(figsize=(max(8.5, num_s * 4.0), 4.2))
        axes_ts = fig_ts.subplots(1, num_s, squeeze=False)[0]
        for ax, src in zip(axes_ts, sources):
            max_bytes = 0
            samples_list = data_by_src[src]["samples"]
            for rep, samples in samples_list:
                for mode, color, ls in [
                    ("plain", colors['plain'], "-"),
                    ("gzip", colors['gzip'], "--"),
                ]:
                    m_samples = [s for s in samples if s.get("mode") == mode]
                    if not m_samples:
                        continue
                    times = [s.get("elapsed", 0) for s in m_samples]
                    bytes_m = [s.get("bytes", 0) / MIB for s in m_samples]
                    max_bytes = max([max_bytes, *bytes_m])
                    lbl = f"{mode.capitalize()} (Rep {rep})"
                    ax.plot(
                        times,
                        bytes_m,
                        color=color,
                        linestyle=ls,
                        linewidth=1.8,
                        marker="o" if mode == "plain" else "s",
                        markersize=4,
                        label=lbl,
                        alpha=0.85,
                    )

            ax.set_title(f"Source: {src}", fontsize=10, fontweight="bold", pad=6)
            ax.set_xlabel("Elapsed Time (s)", fontweight="bold")
            ax.set_ylabel("Uploaded Payload (MiB)", fontweight="bold")
            ax.set_ylim(0, max_bytes * 1.2 if max_bytes > 0 else 1)
            ax.grid(True, linestyle="--", alpha=0.5)
            ax.legend(fontsize=7.5, loc="lower right" if max_bytes > 10 else "center right")
        fig_ts.suptitle(
            "Cumulative FTP Upload Progress Samples Over Time", fontsize=11.5, fontweight="bold"
        )
        save_fig(fig_ts, "time-series")

    compression()
    throughput()
    durations()
    time_series()
