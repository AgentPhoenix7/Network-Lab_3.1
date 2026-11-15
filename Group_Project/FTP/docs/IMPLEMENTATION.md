# FTP implementation plan

## Scope and design

Implement FTP independently of ARP in Python 3.11+ on Linux/POSIX, using
standard-library TCP sockets. The user selected Python on 2026-11-15.
The supplied assignment establishes the socket requirement; it does not
specify a language, UI, or a complete RFC conformance profile.

The terminal client and concurrent server implement the FTP control/data
connection model from RFC 959, with IPv4 PASV/PORT and EPSV/EPRT from RFC
2428. Include login, binary and ASCII transfers, listings, navigation,
directory creation/removal, file deletion, and SIZE. No ARP integration,
web UI, TLS, resume, or full RFC conformance is implied.

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
  interactive/batch terminal entry point.
- `tests/`: standard-library unittest tests using real loopback sockets,
  temporary roots, ftplib and subprocess execution of both CLIs.
- `README.md`: reproducible demo, commands, protocol flow and limitations.

## Verification focus

Binary/empty/large files; ASCII boundaries; fragmented/pipelined and malformed
commands; login state and errors; path traversal and symlinks; refused and
timed-out data connections; failed transfers preserving existing files;
concurrent session independence; shutdown releasing sockets and threads;
the shipped client handling independent server replies and data errors.
Tests put temporary data under the repository's `.scratch/tmp` via TMPDIR.

## Tasks

- [x] 1. Write failing framing/codec and filesystem tests, then implement
  `ControlStream`, ASCII conversion and `Sandbox`.
- [x] 2. Write failing real-socket ftplib interoperability and error tests,
  then implement `FTPServer` and `python3 -m ftp_lab.server`.
- [x] 3. Write failing socket-client and CLI tests, then implement
  `FTPClient` and `python3 -m ftp_lab.client`.
- [x] 4. Document the demo and supported subset; run all tests, inspect
  ignore rules and the FTP-only diff, and obtain a fresh code review.

Each implementation task starts with a failing behavioral test. Native
execution in this session is used on branch `feat/ftp`; the existing
checkout is retained as requested. Commit/push/PR creation remain separate
from implementation unless explicitly requested.

## Validation

On 2026-11-15, all 37 unittest cases passed on Python 3.14.10, including
real client/server subprocesses, binary and ASCII transfers, independent
ftplib/wire peers, and shutdown during an upload. ResourceWarning is treated
as an error. A fresh independent review found a quoted/deep-path reply
length defect; failing regressions reproduced it, separate command/reply
limits and a 4096-byte normalized-path bound fixed it, and the reviewer
independently reran the full suite successfully. Python 3.11 is the source
compatibility floor; this environment's runtime validation used 3.14.10.

## References

- https://datatracker.ietf.org/doc/html/rfc959
- https://www.rfc-editor.org/rfc/rfc2428
- https://docs.python.org/3/library/socket.html
- https://docs.python.org/3/library/socketserver.html
- https://docs.python.org/3/library/ftplib.html
- https://docs.python.org/3/library/os.html
