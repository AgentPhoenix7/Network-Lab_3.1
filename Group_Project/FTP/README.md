# FTP group project

A Python implementation of an FTP client and concurrent server over real
TCP sockets. Both sides implement the FTP commands and data connections
directly; neither delegates FTP to a library. `ftplib` is used only as an
independent client in tests.

For a foundations-first explanation of the protocol, code, execution flow,
tests, and a guided demonstration, read
[the complete walkthrough](docs/explanation.md).

This is the group's **FTP component**. It runs independently of
`Group_Project/ARP` and does not import or modify the ARP implementation.
The assignment asks for suitable TCP/UDP sockets; this component uses TCP
for both FTP connections. The command subset and implementation choices
below are this project's design, beyond that brief assignment requirement.

## Requirements

- Python **3.11+** on Linux/POSIX with `dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW`.
- No pip packages or virtual environment required.
- `make` is optional; every entry point also works with `python3`.

## Quick start: two terminals

Run commands from `Group_Project/FTP` in both terminals.

Terminal 1 starts a server on loopback TCP port 2121, serving a private
`server-data/` directory. This is an example lab password; choose your own
for use on a network.

```bash
cd Group_Project/FTP
FTP_PASSWORD=lab-demo python3 -m ftp_lab.server
```

Terminal 2 creates an example file and connects. Enter the same password
when prompted; it will not be echoed.

```bash
cd Group_Project/FTP
mkdir -p downloads
printf 'Hello from our FTP project!\n' > downloads/hello.txt
python3 -m ftp_lab.client --trace
```

At the `ftp>` prompt:

```text
pwd
mkdir demo
cd demo
put downloads/hello.txt "hello world.txt"
ls
size "hello world.txt"
get "hello world.txt" downloads/roundtrip.txt
active
get "hello world.txt" downloads/active-copy.txt
passive
delete "hello world.txt"
cdup
rmdir demo
quit
```

Verify the copies locally:

```bash
cmp downloads/hello.txt downloads/roundtrip.txt
cmp downloads/hello.txt downloads/active-copy.txt
```

Control traces show commands and numeric replies; `PASS` is redacted.
Files and example download directories are ignored by Git.

## Terminal commands

Quote paths containing spaces, using shell-style quoting.

| Command | Effect |
| --- | --- |
| `pwd`, `cd path`, `cdup` | Inspect/change the virtual working directory |
| `ls [path]`, `nlst [path]` | Detailed listing or names only |
| `mkdir path`, `rmdir path` | Create/remove an empty directory |
| `delete path`, `size path` | Remove a file or print its binary size |
| `put local [remote]` | Upload, using the local basename by default |
| `get remote [local]` | Download, using the remote basename by default |
| `ascii`, `binary` | Select text conversion or byte-exact transfers |
| `active`, `passive` | Select who initiates the next data connection |
| `noop`, `help`, `quit` | Check the connection, show help, disconnect |

Binary and passive mode are the client defaults. A listing or SIZE command
does not change the client's chosen file-transfer type.

Batch commands work via `--script file` or `--script -` for stdin:

```bash
FTP_PASSWORD=lab-demo python3 -m ftp_lab.client --script - <<'EOF'
pwd
nlst
quit
EOF
```

Batch mode stops at the first failed command and returns exit status 1.
Interactive mode reports recoverable command errors and keeps the session
open. Connection loss or an interrupted transfer closes the client session;
reconnect before continuing. Ctrl+C returns status 130 in the client.

Both programs support `--help`. The server supports `--host`, `--port`,
`--root`, `--user`, `--timeout`, and `--max-clients`; the client supports
`--host`, `--port`, `--user`, `--timeout`, `--active`, `--trace`, and `--script`.
The password comes from `FTP_PASSWORD`; an interactive client can prompt
instead. It is never accepted on the command line or stored in source.
The server creates its root directory if necessary. Stop it with Ctrl+C
or SIGTERM; active sockets are closed and worker threads are joined.

For two machines on a trusted LAN, bind the server to its LAN IPv4 address
and pass that address as the client's `--host`. Passive transfers need an
ephemeral incoming TCP data port on the server; active transfers need an
ephemeral incoming port on the client. NAT forwarding and fixed passive
port ranges are outside this implementation's scope.

## How FTP is implemented

One persistent TCP **control connection** carries UTF-8 commands terminated
by CRLF and numeric replies. Each listing or transfer gets a separate,
single-use TCP **data connection**. TCP packet boundaries are never treated
as FTP message boundaries.

Command lines are limited to 512 bytes, virtual paths to 4096 UTF-8 bytes,
and reply lines to 16 KiB. Reply bounds allow quote escaping and deep PWD
responses without weakening the incoming command limit.

For a passive binary download, for example:

```text
client                         server
  ---- USER student ---------->   331 password required
  ---- PASS (redacted) -------->   230 logged in
  ---- TYPE I ---------------->   200 binary mode
  ---- EPSV ------------------>   229 (|||data-port|)
  ==== connect data-port =====>   separate TCP connection
  ---- RETR filename --------->   150 opening data connection
  <=== bytes, then data EOF ====   data socket closes
  <--- 226 transfer complete ---   control socket stays open
```

In active mode the client listens, sends EPRT/PORT, and the server connects
to that endpoint after the transfer command. The client tries EPSV/EPRT
first and falls back to PASV/PORT for servers that reject the extensions.

Supported wire commands:

```text
USER PASS QUIT NOOP SYST FEAT HELP OPTS UTF8 ON
PWD CWD CDUP MKD RMD DELE SIZE
TYPE A | TYPE A N | TYPE I | TYPE L 8
MODE S | STRU F
PASV EPSV [1|ALL] PORT EPRT (IPv4)
LIST [path] NLST [path] RETR path STOR path ABOR
```

SIZE requires binary type. LIST/NLST accept a literal file or directory
path; shell wildcards and `ls` flags are not interpreted. ASCII transfers
convert POSIX LF to NVT CRLF and literal CR to CR NUL, with the reverse
conversion on receipt, including across chunk boundaries.

Common replies are 220 (ready), 331/230/530 (login), 150/226 (transfer),
425 (no data connection), 426 (data failure), 501/502/504 (unsupported or
invalid request), 550 (unavailable/forbidden path), and 221 (goodbye).

## Filesystem and connection behavior

- Each session has its own login, directory, transfer type and data endpoint.
  The default limit is 32 simultaneous clients, with a 30-second socket
  inactivity timeout. Extra connections receive 421.
- `/` is the configured FTP root. Paths that go above it, symlinks, special
  files and control characters in filenames are rejected. Directory
  traversal uses descriptor-relative POSIX operations rather than changing
  the process working directory or invoking shell commands.
- Uploads are written to hidden staging files, then atomically replace the
  destination after clean data EOF. A detected reset or timeout removes the
  staging file and preserves an existing destination. Downloads similarly
  replace a local destination only after a successful completion reply.
  Concurrent uploads to the same name use the last completed replacement.
- Active endpoints must match the control peer and use ports 1024–65535.
  Passive data peers must match the control peer. The client ignores the IP
  advertised in PASV replies and connects to its control peer instead.

This is a teaching implementation of an FTP subset, not a complete FTP
service. FTP sends credentials and data in plaintext; use lab credentials
on a trusted network. TLS/SFTP, IPv6, anonymous/multi-user accounts, resume,
rename, quotas, and Telnet urgent/OOB abort handling are not implemented.
ABOR clears idle data setup; commands are handled serially within a session,
so an in-progress transfer is interrupted by closing its data connection.
In stream mode, clean EOF is the end of a file: without an external expected
size/checksum, a deliberately shortened upload cannot be distinguished from
a complete smaller file. The server root must be managed by a trusted local
operator; this is not an OS-level isolation boundary against local users.

## Tests

From this directory:

```bash
make test
```

Without make (from the repository root):

```bash
mkdir -p .scratch/tmp
TMPDIR="$PWD/.scratch/tmp" TMP="$PWD/.scratch/tmp" TEMP="$PWD/.scratch/tmp" \
  PYTHONPATH="$PWD/Group_Project/FTP" \
  python3 -W error::ResourceWarning -m unittest discover -s Group_Project/FTP/tests -v
```

Tests use dynamically assigned loopback ports and isolated directories under
the repository's `.scratch/tmp`, and clean up their sockets and temporary
files. They need local TCP socket access; a sandbox that denies local
network traffic must allow it for this test run.

The suite exercises the server with the independent standard-library
`ftplib`, the shipped client with the server and independent wire peers,
and subprocesses for terminal behavior. Cases include empty/all-byte/large
files, active/passive mode, ASCII conversion, UTF-8/spaces/quotes, concurrent
sessions, authentication, malformed commands, root confinement, reset
uploads, timeout recovery, and failed-download preservation.

| Path | Responsibility |
| --- | --- |
| `ftp_lab/protocol.py` | CRLF framing, replies, streaming ASCII conversion |
| `ftp_lab/filesystem.py` | Virtual root and atomic uploads |
| `ftp_lab/server.py` | Commands, session state, concurrency and server CLI |
| `ftp_lab/client.py` | Socket client, active/passive transfers and terminal UI |
| `tests/` | Core, interoperability, error and CLI tests |
| `docs/IMPLEMENTATION.md` | Scope, design and implementation checklist |

Protocol sources: [RFC 959](https://datatracker.ietf.org/doc/html/rfc959)
and [RFC 2428](https://www.rfc-editor.org/rfc/rfc2428). Python API references:
[sockets](https://docs.python.org/3/library/socket.html),
[socketserver](https://docs.python.org/3/library/socketserver.html),
[descriptor-relative filesystem operations](https://docs.python.org/3/library/os.html),
and the [ftplib test oracle](https://docs.python.org/3/library/ftplib.html).
