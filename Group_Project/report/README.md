# Combined ARP and terminal FTP report

This directory extends the supplied `report.zip` with the group's FTP
component. The original ARP sections, authors and illustrations are retained.
The appended FTP part covers the terminal client/server, protocol concepts,
pseudocode and actual Python excerpts, an executed terminal demonstration,
verification, and large-media performance measurements. It does not describe
the FTP browser application.

## Deliverables

- `report.tex` / `report.pdf`: primary combined report with flexible figures.
- `report_H.tex` / `report_H.pdf`: the supplied fixed-placement variant, also
  extended with the same FTP content.
- `sections/ftp_*.tex` and `sections_H/ftp_*.tex`: FTP report sections.
- `figures/ftp_lecture_figures.pdf`: four cropped native PDF figures extracted
  from the supplied application-layer slides, original PDF pages 75, 76, 77
  and 80. The bibliography and captions identify their source.
- `figures/ftp_benchmark_*.png`: the four saved benchmark graphs.
- `data/`: original run/sample CSV, structured measurements, media manifest
  and report provenance. Measurements were observed on 2026-11-15.
- `code/ftp_lab/*.py`: actual terminal/benchmark source snapshots used by the
  numbered excerpts, arranged as their original importable package, with hashes in `data/report-provenance.json`.
- `evidence/`: fresh terminal command input, trace, byte/hash checks and the
  52-test terminal/benchmark validation output from 2026-11-16.

`sections/` and `sections_H/` from the archive are retained. The latter's
fixed-placement preference also applies to the appended FTP sections.
The archive's optional browser-capture tools and duplicate intermediate
screenshots are omitted from this report-only bundle. The original archive
remains unchanged, and all figures used by the ARP sections are retained.

## Build

From the repository root:

```bash
make -C Group_Project/report
```

The build requires a TeX installation with the packages named in
`preamble.tex`, including TikZ, listings, algorithm and algpseudocode. It
uses `pdflatex` with shell escape disabled and runs three passes for each
variant. Auxiliary files and temporary storage go to this directory's
`build/` folder; completed PDFs are copied back into this directory.

The provided graphs are ready to include; rebuilding this report does not
rerun the media experiment or require Python/FFmpeg/Matplotlib. The report
contains separate terminal commands for reproducing that experiment.

## Evidence boundaries

The 12 saved plain/gzip media runs all verified their downloaded source size
and SHA-256 digest. Graphs measure cached loopback transfers, not LAN link
capacity. Gzip saved substantial bytes for the generated raw media but took
longer after preparation was counted; MP4 saved very little. The fresh
terminal run separately verified passive/active binary copies and streamed
ASCII conversion. No physical second-device throughput claim is made.

`SHA256SUMS` covers the deliverable sources, PDFs and assets. The original
input archive remains untouched in Downloads. The ARP/FTP protocol
implementations are unchanged, and the normal FTP service remains stopped.
