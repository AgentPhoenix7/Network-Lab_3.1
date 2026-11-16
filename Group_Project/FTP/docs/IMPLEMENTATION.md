# FTP implementation and validation record

This covers the socket client/server, Relay browser app, and multi-device
LAN extension. The detailed browser design is in
[LOCAL_APP_PLAN.md](LOCAL_APP_PLAN.md), its walkthrough in
[LOCAL_APP.md](LOCAL_APP.md), and network setup in [LAN_ACCESS.md](LAN_ACCESS.md).
Dated validation milestones are retained below, separately from the current
70-test suite.

## Scope and design

Implement FTP independently of ARP in Python 3.11+ on Linux/POSIX, using
standard-library TCP sockets. The user selected Python on 2026-11-15.
The supplied assignment establishes the socket requirement; it does not
specify a language, UI, or a complete RFC conformance profile.

The terminal client and concurrent server implement the FTP control/data
connection model from RFC 959, with IPv4 PASV/PORT and EPSV/EPRT from RFC
2428. Include login, binary and ASCII transfers, listings, navigation,
directory creation/removal, file deletion, and SIZE. Relay adds an offline
browser UI, SQLite owner/session/device/history storage, bounded background
transfers, progress, and verified compression graphs. Its HTTP listener
defaults to `0.0.0.0:8080` for a trusted IPv4 LAN; `--host 127.0.0.1`
retains host-only operation. First-owner setup requires loopback, while
existing-account sign-in supports independent device sessions. ARP
integration, TLS, resume, and full RFC conformance remain outside scope.

The implementation choice is Python sockets rather than a C implementation
(the rest of this repository uses C) or an FTP server library. This keeps
the protocol implementation visible and needs no installed dependencies.
`ftplib` is an independent interoperability oracle in tests, not the client
implementation.

## Files and boundaries

- `ftp_lab/protocol.py`: bounded CRLF control framing, replies, ASCII codec.
- `ftp_lab/filesystem.py`: descriptor-relative root confinement, no symlinks,
  regular files only, atomic upload replacement.
- `ftp_lab/server.py`: per-session state, FTP commands, active/passive data
  sockets, bounded concurrent sessions, CLI and graceful shutdown.
- `ftp_lab/client.py`: socket-based client, reply parsing, transfers and
  interactive/batch terminal entry point, byte progress and cancellation.
- `ftp_lab/webapp/`: SQLite persistence, authenticated HTTP API, background
  work, offline browser UI, and destination-bound Host/Origin checks.
- `ftp_lab/benchmark.py`: original/gzip comparisons, bounded round-trip
  verification, timing/samples, CSV/JSON and optional graph exports.
- `tools/media_fixtures.py`: real offline WAV/AVI/MP4 fixture generation.
- `benchmarks/`: preserved original measurements, provenance and graphs.
- `tests/`: unittest and Node checks using sockets, isolated roots,
  independent wire/ftplib peers, subprocesses and an opt-in LAN interface.
- `README.md`: reproducible demo, commands, protocol flow and limitations.

## Verification focus

Binary/empty/large files; ASCII boundaries; fragmented/pipelined and malformed
commands; login state and errors; path traversal and symlinks; refused and
timed-out data connections; failed transfers preserving existing files;
concurrent session independence; shutdown releasing sockets and threads;
the shipped client handling independent server replies and data errors.
Also verify independent browser sessions, host-only account bootstrap,
shared profiles/files with private session credentials, exact Host/Origin/
CSRF checks, concurrent initial profile creation, bounded raw sources,
size-consistent downloads, gzip limits, comparison verification, and
shutdown EOF preserving a staged upload's original destination.
Tests put temporary data under the FTP component's `build/tmp` via TMPDIR.

## Tasks

- [x] 1. Write failing framing/codec and filesystem tests, then implement
  `ControlStream`, ASCII conversion and `Sandbox`.
- [x] 2. Write failing real-socket ftplib interoperability and error tests,
  then implement `FTPServer` and `python3 -m ftp_lab.server`.
- [x] 3. Write failing socket-client and CLI tests, then implement
  `FTPClient` and `python3 -m ftp_lab.client`.
- [x] 4. Document the demo and supported subset; run all tests, inspect
  ignore rules and the FTP-only diff, and obtain a fresh code review.
- [x] 5. Add the authenticated browser workspace, saved devices/history,
  streamed transfers, progress, cancellation and compression comparisons.
- [x] 6. Record real large-media measurements with verification and plots.
- [x] 7. Enable trusted-LAN access, retain independent browser sessions and
  host-only first-owner setup, and verify network/security regressions.

Behavior changes use failing regression tests before fixes. The original
CLI milestone used branch `feat/ftp`; the browser/LAN follow-up is on
`codex/ftp-local-app`. Git history records the delivered milestones.

## Validation

On 2026-11-15, all 37 unittest cases passed on Python 3.14.10, including
real client/server subprocesses, binary and ASCII transfers, independent
ftplib/wire peers, and shutdown during an upload. ResourceWarning is treated
as an error. A fresh independent review found a quoted/deep-path reply
length defect; failing regressions reproduced it, separate command/reply
limits and a 4096-byte normalized-path bound fixed it, and the reviewer
independently reran the full suite successfully. Python 3.11 is the source
compatibility floor; this environment's runtime validation used 3.14.10.

Earlier on 2026-11-16, the browser extension passed 61 Python tests and 6 frontend
tests. Ruff and Pyright checks were clean. Internal-browser verification
covered sign-in, device saving/connection, 128 MiB audio upload with progress,
download/save, verified compression graphs, and persistence across sign-out.
The separate [benchmark report](../benchmarks/README.md) retains 12 verified
large-media run measurements from 2026-11-15, including their limitations.

The user's subsequent multi-device request extends the browser app to a
trusted LAN. [LAN_ACCESS.md](LAN_ACCESS.md) records binding, request
protection, independent browser sessions, and host-only owner setup.
After that extension and a reproduced shutdown-EOF fix, all **69 Python
tests and 6 frontend tests passed**, including the real-interface case with
`FTP_TEST_LAN_HOST` set. Ruff, JavaScript syntax and Pyright checks passed.
The internal browser reached the host LAN URL, enforced the remote setup
guard, signed in after local bootstrap, and completed a 128 MiB WAV upload.
That real-interface check is distinct from a physical second-device test;
Wi-Fi isolation and external firewall reachability remain network-specific.

The gzip-download follow-up corrects completed transfer cards to show the
saved filename rather than the compressed source name, labels unpacked
outputs explicitly, and catches invalid gzip deflate data as a failed job.
Regression tests first reproduced the misleading display and stuck worker;
the updated suite passes **70 Python and 8 frontend tests**. A browser
download verified both the real filename and byte-exact unpacked content.

## References

- https://datatracker.ietf.org/doc/html/rfc959
- https://www.rfc-editor.org/rfc/rfc2428
- https://docs.python.org/3/library/socket.html
- https://docs.python.org/3/library/socketserver.html
- https://docs.python.org/3/library/ftplib.html
- https://docs.python.org/3/library/os.html
