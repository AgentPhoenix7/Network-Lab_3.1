# FTP group project

A Python implementation of an FTP client and concurrent server over real
TCP sockets. Both sides implement the FTP commands and data connections
directly; neither delegates FTP to a library. `ftplib` is used only as an
independent client in tests.

For a foundations-first explanation of the protocol, code, execution flow,
tests, and a guided demonstration, read
[the complete walkthrough](docs/explanation.md).
For the browser interface, account/device storage, progress, and compression
comparisons, read the [local app guide](docs/LOCAL_APP.md). Saved large-media
measurements and graphs are in the [benchmark report](benchmarks/README.md).

This is the group's **FTP component**. It runs independently of
`Group_Project/ARP` and does not import or modify the ARP implementation.
The assignment asks for suitable TCP/UDP sockets; this component uses TCP
for both FTP connections. The command subset and implementation choices
below are this project's design, beyond that brief assignment requirement.

## Requirements

- Python **3.11+** on Linux/POSIX with `dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW`.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) manages the project's `.venv` and locked Python dependencies.
- The FTP client/server and browser app use only the Python standard library.
- Optional: Node.js for frontend helper tests, FFmpeg/ffprobe for generated
  media, and Matplotlib for exported benchmark PNG/SVG plots.
- `make` is optional; equivalent `uv run --locked ...` commands are shown below.

## Set up the environment

From the repository root:

```bash
cd Group_Project/FTP
uv sync --locked --extra plots
```

`pyproject.toml` declares Python 3.11+ compatibility, the default `dev` group
(Pylint), and the optional `plots` extra (Matplotlib). `uv.lock` fixes direct
and transitive dependency versions, including Python-version-specific choices.
Both files belong in version control. The project runs from its source tree;
it does not need a build or editable package installation.

`uv sync --locked --extra plots` (also `make sync`) prepares linting and all
Python tests, including PNG/SVG chart exports. For only the standard-library
app and FTP tools, use `uv sync --locked --no-dev`, then the `--no-dev` launch
commands below. No environment activation is required. In PyCharm, select
this project's `.venv/bin/python` as the interpreter.

Initial setup may download Python or dependencies. After they are available
locally, add `--offline` to `uv run` or `uv sync` to prevent network access.
Node.js and FFmpeg/ffprobe are separate system tools; uv does not install them.

To change dependencies, use `uv add --dev NAME` for development tools or
`uv add --optional plots NAME` for plot dependencies. To update locked
versions, run `uv lock --upgrade`, then `uv sync --locked --extra plots` and
`make lint test`. `uv lock --check` verifies that the lock matches the manifest.
Use `--locked` for normal work so a stale lock is reported instead of rewritten.

## Quick start: browser app

```bash
cd Group_Project/FTP
make app
# Equivalent: uv run --locked --no-dev python -m ftp_lab.webapp --demo
```

On the host computer, open **http://127.0.0.1:8080** and create your workspace account.
The built-in **Host computer** FTP device is ready immediately. Choose a
file and select **Send file**, or select **Download** beside a remote file.
**Add a device** saves another FTP server's name, address, port, and username;
its password is requested on connection and kept only in session memory.

Progress shows the current phase, bytes, speed, and estimated time. Select
**Compare compression** for verified original/gzip transfers and graphs in
**Performance**. **History** keeps completed and failed transfers.

Account data, history, and files remain in ignored `app-data/`. Stop with
Ctrl+C; restarting the same command keeps that workspace. To open the app
inside VS Code, open `FTP.code-workspace`, start the server, and run the
**FTP: Open in VS Code browser** launch configuration. See the
[detailed guide](docs/LOCAL_APP.md) for limits and troubleshooting.

### Open it from phones and other computers

The browser app now listens on all IPv4 interfaces by default. Connect
devices to the same trusted Wi-Fi/LAN and open **http://HOST_LAN_IP:8080**,
replacing `HOST_LAN_IP` with the server computer's LAN address. Sign in
with the workspace account created on the host. Each browser gets its
own session; profiles, host-folder files, and history are shared.

The first account can only be created through loopback on the host.
Other devices show a setup instruction until it exists. **Host computer**
means the computer running Python, including when viewed from a phone.
For local-only access, run `uv run --locked --no-dev python -m ftp_lab.webapp --demo --host 127.0.0.1`.

This is plaintext HTTP/FTP for a trusted lab LAN. The browser workflow
needs TCP port 8080 at the host; direct FTP clients additionally need the
FTP control/data ports. See [LAN access and troubleshooting](docs/LAN_ACCESS.md)
for setup, host binding, firewall checks, and direct FTP commands.

## Quick start: two terminals

Run commands from `Group_Project/FTP` in both terminals.

Terminal 1 starts a server on loopback TCP port 2121, serving a private
`server-data/` directory. This is an example lab password; choose your own
for use on a network.

```bash
cd Group_Project/FTP
FTP_PASSWORD=lab-demo uv run --locked --no-dev python -m ftp_lab.server
```

Terminal 2 creates an example file and connects. Enter the same password
when prompted; it will not be echoed.

```bash
cd Group_Project/FTP
mkdir -p downloads
printf 'Hello from our FTP project!\n' > downloads/hello.txt
uv run --locked --no-dev python -m ftp_lab.client --trace
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

## Connect from another PC through the terminal

Connect both PCs to the same trusted Wi-Fi or LAN. Install uv and copy or
clone this project on both PCs, then run `uv sync --locked --no-dev` from
`Group_Project/FTP` on each. The terminal client/server use Python's standard
library and need no third-party runtime packages.

### 1. Start the server on the PC that will share files

From the repository root, run:

```bash
cd Group_Project/FTP
FTP_PASSWORD='choose-a-password' uv run --locked --no-dev python -m ftp_lab.server \
  --host 0.0.0.0 --port 2121 --root server-data --user student
```

Replace `choose-a-password` with your lab password and keep this terminal
running. The server stores shared files in `Group_Project/FTP/server-data/`.
`0.0.0.0` makes it listen on all IPv4 interfaces; stop it with Ctrl+C.

Find the server PC's LAN IP using another terminal on that PC:

```bash
ip -4 -brief address
```

Use the IPv4 address beside the connected Wi-Fi or Ethernet interface,
for example `192.168.1.10`. Do not use `127.0.0.1`, `0.0.0.0`, or an address
belonging to a disconnected, VPN or container interface. On Windows, use
`ipconfig` and find the connected adapter's IPv4 address instead.

### 2. Connect from the other PC

From that PC's copy of the repository:

```bash
cd Group_Project/FTP
mkdir -p downloads
printf 'Hello from the client PC!\n' > downloads/hello.txt
uv run --locked --no-dev python -m ftp_lab.client \
  --host 192.168.1.10 --port 2121 --user student --trace
```

Replace `192.168.1.10` with the server PC's actual LAN IP. Enter the same
password you chose when starting the server. This example uses a Bash
terminal; uv selects the project interpreter on Windows too. The server
requires the Linux/POSIX filesystem APIs listed above.

### 3. Upload and download files

At the `ftp>` prompt:

```text
pwd
ls
put downloads/hello.txt hello.txt
get hello.txt downloads/roundtrip.txt
quit
```

`put` reads a file on the client PC and writes it under the server's shared
root. `get` saves a server file on the client PC. Relative local paths are
resolved from the directory where you launched the client. Binary and
passive modes are already the defaults.

If the connection fails, check the server is running, the IP is correct,
and its firewall allows incoming TCP **2121** from the client. If login
succeeds but `ls` or transfers time out, passive FTP also needs incoming
connections to the server's dynamically selected data ports. Guest Wi-Fi
or access-point client isolation can prevent PCs from reaching each other.

These instructions cover a trusted LAN. FTP credentials and files are
unencrypted. Separate networks require suitable routing or a VPN with
reachable control/data ports. See [the LAN guide](docs/LAN_ACCESS.md#direct-ftp-access-without-the-browser)
for the distinction between direct FTP and browser-app access.

## Terminal commands

Quote paths containing spaces, using shell-style quoting.

| Command | Effect |
| --- | --- |
| `pwd`, `cd path`, `cdup` | Inspect/change the virtual working directory |
| `ls [path]`, `nlst [path]` | Detailed listing or names only |
| `mkdir path`, `rmdir path` | Create/remove an empty directory |
| `delete path`, `rename old new` | Remove a file or rename a file/directory |
| `size path` | Print the binary file size |
| `put local [remote]` | Upload, using the local basename by default |
| `get remote [local]` | Download, using the remote basename by default |
| `append local [remote]` | Append data from a local file to a remote file (`APPE`) |
| `type [A\|I\|E]` | Set representation: ASCII (`A`), Image/Binary (`I`), EBCDIC (`E`) |
| `stru [F\|R\|P]` | Set structure: File (`F`), Record (`R`), Page (`P`) |
| `mode [S\|B\|C]` | Set transmission mode: Stream (`S`), Block (`B`), Compressed (`C`) |
| `ascii`, `binary` | Convenience shortcuts for `type A` and `type I` |
| `active`, `passive` | Select who initiates the next data connection |
| `stat [path]` | Query session status (displays TYPE, STRU, MODE) or file metadata |
| `rein` | Reinitialize session state (`REIN`) |
| `quote verb [arg]` | Send raw RFC 959 wire command directly to server |
| `noop`, `help`, `quit` | Check the connection, show help, disconnect |

Binary and passive mode are the client defaults. A listing or SIZE command
does not change the client's chosen file-transfer type.

Batch commands work via `--script file` or `--script -` for stdin:

```bash
FTP_PASSWORD=lab-demo uv run --locked --no-dev python -m ftp_lab.client --script - <<'EOF'
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

For the two-PC setup, see [Connect from another PC through the terminal](#connect-from-another-pc-through-the-terminal).
You can bind the server to its specific LAN IPv4 address instead of
`0.0.0.0`. Active transfers need an ephemeral incoming port on the client.
NAT forwarding and fixed passive port ranges are outside this
implementation's scope.

## RFC 959 TYPE / STRU / MODE Permutations Demo

The FTP server's sandbox root (`server-data/`) and its tracked sub-directory (`server-data/samples/`) contain authentic, tracked demonstration files covering all **27 valid permutations** ($3 \times 3 \times 3$) of the RFC 959 transmission parameters:

- **Representation Type (`TYPE`)**:
  - `TYPE A`: **ASCII** non-print format. Text lines use NVT CRLF across the wire and POSIX LF in local files.
  - `TYPE I`: **Image (Binary)** format. 8-bit byte-exact transparent transfer for binary executables, archives, and media.
  - `TYPE E`: **EBCDIC** character format. Decoded using IBM CP037 for mainframe system interoperability.
- **File Structure (`STRU`)**:
  - `STRU F`: **File** structure. Continuous sequence of bytes with no internal record boundaries.
  - `STRU R`: **Record** structure. Sequential records terminating in an End-of-Record (EOR) condition.
  - `STRU P`: **Page** structure. Indexed independent pages with page headers (type, page number, length).
- **Transmission Mode (`MODE`)**:
  - `MODE S`: **Stream** mode. Continuous stream of bytes terminated by closing the TCP data connection.
  - `MODE B`: **Block** mode. Transmission framed into blocks with 3-byte headers (1-byte descriptor flags + 2-byte byte count).
  - `MODE C`: **Compressed** mode. Run-length compression encoding byte repetitions and blank runs.

### Tracked Demonstration Files

All demonstration files are tracked in Git (whitelisted in `.gitignore`) so they are ready out-of-the-box:

- **Root Sandbox File**: [`server-data/welcome.txt`](server-data/welcome.txt) (`TYPE A`, `STRU F`, `MODE S`) introduces the sandbox environment.
- **Tracked Permutations Directory**: [`server-data/samples/`](server-data/samples/) contains all 27 permutations and a machine-readable manifest:

| # | Permutation | Parameter Commands | Example Tracked File | Size | Description & Format |
| -: | :---: | :--- | :--- | ---: | :--- |
| 1 | **A-F-S** | `type A` / `stru F` / `mode S` | `type-A_stru-F_mode-S_ascii-file-stream.txt` | 463 B | Standard ASCII text stream with CRLF line delimiters. |
| 2 | **A-F-B** | `type A` / `stru F` / `mode B` | `type-A_stru-F_mode-B_ascii-file-block.dat` | 469 B | ASCII text framed into RFC 959 Mode B transmission blocks. |
| 3 | **A-F-C** | `type A` / `stru F` / `mode C` | `type-A_stru-F_mode-C_ascii-file-compressed.dat` | 409 B | ASCII text compressed with Mode C run-length encoding. |
| 4 | **A-R-S** | `type A` / `stru R` / `mode S` | `type-A_stru-R_mode-S_ascii-record-stream.csv` | 375 B | Tabular CSV records with EOR delimited lines. |
| 5 | **A-R-B** | `type A` / `stru R` / `mode B` | `type-A_stru-R_mode-B_ascii-record-block.dat` | 393 B | ASCII records encapsulated in Mode B blocks with EOR descriptor bits. |
| 6 | **A-R-C** | `type A` / `stru R` / `mode C` | `type-A_stru-R_mode-C_ascii-record-compressed.dat` | 381 B | Sequential ASCII records compressed using Mode C run-length scheme. |
| 7 | **A-P-S** | `type A` / `stru P` / `mode S` | `type-A_stru-P_mode-S_ascii-page-stream.txt` | 373 B | Paged ASCII document with RFC 959 page headers (type, number, length). |
| 8 | **A-P-B** | `type A` / `stru P` / `mode B` | `type-A_stru-P_mode-B_ascii-page-block.dat` | 376 B | Paged ASCII data framed inside Mode B transmission blocks. |
| 9 | **A-P-C** | `type A` / `stru P` / `mode C` | `type-A_stru-P_mode-C_ascii-page-compressed.dat` | 377 B | Paged ASCII text with Mode C compressed page payloads. |
| 10 | **I-F-S** | `type I` / `stru F` / `mode S` | `type-I_stru-F_mode-S_binary-file-stream.bin` | 303 B | Byte-exact raw binary data containing all 256 byte values and binary structs. |
| 11 | **I-F-B** | `type I` / `stru F` / `mode B` | `type-I_stru-F_mode-B_binary-file-block.dat` | 309 B | Binary payload packed into RFC 959 Mode B transmission blocks. |
| 12 | **I-F-C** | `type I` / `stru F` / `mode C` | `type-I_stru-F_mode-C_binary-file-compressed.gz` | 326 B | Gzip compressed binary stream per RFC 959 compressed binary mode. |
| 13 | **I-R-S** | `type I` / `stru R` / `mode S` | `type-I_stru-R_mode-S_binary-record-stream.dat` | 256 B | Binary record stream containing fixed 32-byte C-struct sensor records. |
| 14 | **I-R-B** | `type I` / `stru R` / `mode B` | `type-I_stru-R_mode-B_binary-record-block.dat` | 280 B | Binary records framed in Mode B blocks with EOR descriptor flags. |
| 15 | **I-R-C** | `type I` / `stru R` / `mode C` | `type-I_stru-R_mode-C_binary-record-compressed.dat` | 226 B | Binary structured records compressed with Mode C run-length encoding. |
| 16 | **I-P-S** | `type I` / `stru P` / `mode S` | `type-I_stru-P_mode-S_binary-page-stream.dat` | 303 B | Binary paged file format per RFC 959 with binary page headers. |
| 17 | **I-P-B** | `type I` / `stru P` / `mode B` | `type-I_stru-P_mode-B_binary-page-block.dat` | 306 B | Binary pages encapsulated in Mode B transmission blocks. |
| 18 | **I-P-C** | `type I` / `stru P` / `mode C` | `type-I_stru-P_mode-C_binary-page-compressed.dat` | 307 B | Binary page structure with Mode C compressed page bodies. |
| 19 | **E-F-S** | `type E` / `stru F` / `mode S` | `type-E_stru-F_mode-S_ebcdic-file-stream.ebcdic` | 158 B | Authentic IBM CP037 EBCDIC mainframe text stream file. |
| 20 | **E-F-B** | `type E` / `stru F` / `mode B` | `type-E_stru-F_mode-B_ebcdic-file-block.dat` | 161 B | CP037 EBCDIC text stream partitioned into Mode B transmission blocks. |
| 21 | **E-F-C** | `type E` / `stru F` / `mode C` | `type-E_stru-F_mode-C_ebcdic-file-compressed.dat` | 161 B | CP037 EBCDIC text stream compressed with run-length byte compression. |
| 22 | **E-R-S** | `type E` / `stru R` / `mode S` | `type-E_stru-R_mode-S_ebcdic-record-stream.ebcdic` | 444 B | Classic 80-column IBM mainframe punched card records in CP037 EBCDIC. |
| 23 | **E-R-B** | `type E` / `stru R` / `mode B` | `type-E_stru-R_mode-B_ebcdic-record-block.dat` | 462 B | 80-column EBCDIC punched card records packed in Mode B blocks with EOR. |
| 24 | **E-R-C** | `type E` / `stru R` / `mode C` | `type-E_stru-R_mode-C_ebcdic-record-compressed.dat` | 209 B | EBCDIC card records compressed with repeated byte/blank compression. |
| 25 | **E-P-S** | `type E` / `stru P` / `mode S` | `type-E_stru-P_mode-S_ebcdic-page-stream.ebcdic` | 197 B | EBCDIC mainframe report pages with RFC 959 page headers and CP037 text. |
| 26 | **E-P-B** | `type E` / `stru P` / `mode B` | `type-E_stru-P_mode-B_ebcdic-page-block.dat` | 200 B | EBCDIC report pages inside Mode B transmission blocks. |
| 27 | **E-P-C** | `type E` / `stru P` / `mode C` | `type-E_stru-P_mode-C_ebcdic-page-compressed.dat` | 201 B | EBCDIC report pages with Mode C compressed payloads. |

The complete metadata and SHA-256 hashes are cataloged in [`server-data/samples/manifest.json`](server-data/samples/manifest.json).

---

### How to Demonstrate Each Permutation

#### Step 1: Start the FTP Server

In Terminal 1, start the server using the default sandbox root (`server-data`):

```bash
cd Group_Project/FTP
FTP_PASSWORD=lab-demo uv run --locked --no-dev python -m ftp_lab.server
```

The server binds to `127.0.0.1:2121`, serving `Group_Project/FTP/server-data/`.

#### Step 2: Connect with the Terminal Client

In Terminal 2, create a clean local download directory and start the client with protocol tracing:

```bash
cd Group_Project/FTP
mkdir -p downloads
uv run --locked --no-dev python -m ftp_lab.client --trace
```

Enter password `lab-demo` when prompted.

#### Step 3: Interactive Demo of Selected Permutations

At the `ftp>` prompt:

```text
# 1. Inspect sandbox root (displays welcome.txt and samples/ directory)
pwd
ls

# 2. Navigate into the permutations samples directory
cd samples
ls

# --- Demo 1: ASCII File Stream (A-F-S) ---
type A
stru F
mode S
stat
get type-A_stru-F_mode-S_ascii-file-stream.txt downloads/demo_A_F_S.txt

# --- Demo 2: Binary Image Record Block (I-R-B) ---
type I
stru R
mode B
stat
get type-I_stru-R_mode-B_binary-record-block.dat downloads/demo_I_R_B.dat

# --- Demo 3: Mainframe EBCDIC Punched Card Records (E-R-S) ---
type E
stru R
mode S
stat
get type-E_stru-R_mode-S_ebcdic-record-stream.ebcdic downloads/demo_E_R_S.ebcdic

# --- Demo 4: Compressed Binary Archive (I-F-C) ---
type I
stru F
mode C
stat
get type-I_stru-F_mode-C_binary-file-compressed.gz downloads/demo_I_F_C.gz

# --- Demo 5: Raw Wire Command Execution via 'quote' ---
quote NOOP
quote SYST
quote FEAT
quote PWD

quit
```

#### Step 4: Verify Downloaded Files Locally

In Terminal 2, inspect the retrieved demonstration files:

```bash
# Verify ASCII text transfer:
cat downloads/demo_A_F_S.txt

# Verify EBCDIC CP037 decoding (IBM mainframe character translation):
uv run --locked --no-dev python -c "print(open('downloads/demo_E_R_S.ebcdic', 'rb').read().decode('cp037'))"

# Verify binary block headers with hexdump (reveals descriptor byte 0x40 and length):
xxd downloads/demo_I_R_B.dat

# Verify gzip decompression of compressed mode binary archive:
gzip -dc downloads/demo_I_F_C.gz | file -
```

#### Step 5: Batch Negotiation and Automated Permutation Tests

You can negotiate selected parameter combinations in one batch session:

```bash
FTP_PASSWORD=lab-demo uv run --locked --no-dev python -m ftp_lab.client --script - <<'EOF'
cd samples
type A
stru F
mode S
stat
type E
stru R
mode B
stat
type I
stru P
mode C
stat
quit
EOF
```

All 27 permutations are also validated by the automated test suite:
```bash
uv run --locked --extra plots python -m unittest discover -s tests -p "test_permutations.py"
```

#### Step 6: Browser UI Demonstration

Start the local web application:
```bash
uv run --locked --no-dev python -m ftp_lab.webapp --demo
```
Open **http://127.0.0.1:8080** and navigate to the **RFC 959 Command Explorer** section. Click on the **TYPE**, **STRU**, or **MODE** cards to view interactive syntax, parameter options, and animated protocol message exchanges in the command stream.

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

Supported wire commands (covering RFC 959 and Forouzan Table 10.4):

```text
USER PASS ACCT CWD CDUP SMNT REIN QUIT NOOP SYST FEAT HELP OPTS UTF8 ON
PWD CWD CDUP MKD RMD DELE RNFR RNTO SIZE STAT
TYPE A | TYPE A N | TYPE E | TYPE EBCDIC | TYPE I | TYPE L 8
MODE S | MODE B | MODE C | STRU F | STRU R | STRU P
PASV EPSV [1|ALL] PORT EPRT (IPv4)
LIST [path] NLST [path] RETR path STOR path STOU APPE ALLO REST ABOR SITE
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
quotas, and Telnet urgent/OOB abort handling are not implemented.
ABOR clears idle data setup; commands are handled serially within a session,
so an in-progress transfer is interrupted by closing its data connection.
In stream mode, clean EOF is the end of a file: without an external expected
size/checksum, a deliberately shortened upload cannot be distinguished from
a complete smaller file. The server root must be managed by a trusted local
operator; this is not an OS-level isolation boundary against local users.

## Tests

From `Group_Project/FTP`:

```bash
uv sync --locked --extra plots
make lint
make test
```

`make test` selects the `plots` extra, so chart-export tests run when its
locked dependencies are available. Node.js enables the frontend helper tests;
FFmpeg and ffprobe enable generated-media checks. Those system tools remain
optional. The normal test run skips the real LAN-interface case unless
`FTP_TEST_LAN_HOST` is set.

Without make (from the repository root):

```bash
mkdir -p Group_Project/FTP/build/tmp
TMPDIR="$PWD/Group_Project/FTP/build/tmp" TMP="$PWD/Group_Project/FTP/build/tmp" TEMP="$PWD/Group_Project/FTP/build/tmp" \
  PYTHONPATH="$PWD/Group_Project/FTP" \
  uv run --project Group_Project/FTP --locked --extra plots python \
  -W error::ResourceWarning -m unittest discover -s Group_Project/FTP/tests -v
node Group_Project/FTP/tests/test_frontend.cjs
```

Tests use dynamically assigned loopback ports and isolated directories under
the FTP component's `build/tmp`, and clean up their sockets and temporary
files. They need local TCP socket access; a sandbox that denies local
network traffic must allow it for this test run.

The suite exercises the server with the independent standard-library
`ftplib`, the shipped client with the server and independent wire peers,
and subprocesses for terminal behavior. Cases include empty/all-byte/large
files, active/passive mode, ASCII conversion, UTF-8/spaces/quotes, concurrent
sessions, authentication, malformed commands, root confinement, reset
uploads, timeout recovery, and failed-download preservation.

The current suite contains **79 Python tests** and **10 frontend helper tests**
(the latter run when Node.js is available). Additional coverage includes
local authentication, persistent devices/history, request protections,
streamed sources, cancellation, gzip, benchmark verification, oversized
remote payloads, and stale responses after sign-out. Large-media stress
measurements are a separate [reproducible report](benchmarks/README.md).
One LAN-interface test is opt-in: set `FTP_TEST_LAN_HOST` to a real local
LAN IPv4 address when running `make test`. It is otherwise reported skipped.

| Path | Responsibility |
| --- | --- |
| `ftp_lab/protocol.py` | CRLF framing, replies, streaming ASCII conversion |
| `ftp_lab/filesystem.py` | Virtual root and atomic uploads |
| `ftp_lab/server.py` | Commands, session state, concurrency and server CLI |
| `ftp_lab/client.py` | Socket client, active/passive transfers and terminal UI |
| `ftp_lab/webapp/` | Local HTTP API, SQLite, background jobs, offline browser UI |
| `pyproject.toml`, `uv.lock` | Dependency declarations and reproducible Python environment |
| `ftp_lab/benchmark.py` | Original/gzip comparisons, verification and CSV/JSON report exports |
| `ftp_lab/plots.py` | Optional Matplotlib PNG/SVG report rendering |
| `tools/media_fixtures.py` | Offline playable audio/video fixture generation |
| `tests/` | Core, interoperability, error and CLI tests |
| `docs/LOCAL_APP.md` | Browser workflow and detailed app architecture |
| `docs/LAN_ACCESS.md` | Multi-device setup, bind options, ports and verification |
| `docs/LOCAL_APP_PLAN.md` | Browser design and HTTP/JSON contract |
| `benchmarks/` | Saved measurements, plots and reproduction instructions |
| `docs/IMPLEMENTATION.md` | Scope, design and implementation checklist |

Protocol sources: [RFC 959](https://datatracker.ietf.org/doc/html/rfc959)
and [RFC 2428](https://www.rfc-editor.org/rfc/rfc2428). Python API references:
[sockets](https://docs.python.org/3/library/socket.html),
[socketserver](https://docs.python.org/3/library/socketserver.html),
[descriptor-relative filesystem operations](https://docs.python.org/3/library/os.html),
and the [ftplib test oracle](https://docs.python.org/3/library/ftplib.html).
