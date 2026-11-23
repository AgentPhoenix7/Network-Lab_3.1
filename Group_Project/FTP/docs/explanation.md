# How FTP Works — A Complete, No-Assumptions Walkthrough

This document explains the Python FTP project from “what is a socket?” to
“how does a failed upload leave the old file intact?” It connects the
networking ideas to the actual functions in this checkout, using small
examples, diagrams, and step-by-step traces. No prior FTP knowledge is
assumed; the Python features used by the implementation are explained too.

Start at the beginning for a guided lesson. For a particular source file,
use the [file-by-file reference](#22-file-by-file-reference-and-reading-order).
For a demonstration, jump to [running it yourself](#21-running-it-yourself).
The shorter [README](../README.md) remains the quick-start reference.

This walkthrough focuses on the socket protocol and terminal tools. The
project now also has Relay, a browser interface accessible across a trusted
IPv4 LAN, with authentication,
SQLite device/history storage, progress, and compression graphs. Continue
with [the detailed app walkthrough](LOCAL_APP.md) for those modules and
[the large-media report](../benchmarks/README.md) for measured results.
The [multi-device guide](LAN_ACCESS.md) distinguishes host-only account
setup, LAN browser access, and direct remote FTP clients.

---

## Table of Contents

1. [The 30,000-foot view](#1-the-30000-foot-view)
2. [Background concepts you need first](#2-background-concepts-you-need-first)
3. [What this project implements](#3-what-this-project-implements)
4. [The four main modules](#4-the-four-main-modules)
5. [The Python features that make the code work](#5-the-python-features-that-make-the-code-work)
6. [Sockets: listening, connecting, and moving bytes](#6-sockets-listening-connecting-and-moving-bytes)
7. [Control messages: how TCP bytes become FTP commands](#7-control-messages-how-tcp-bytes-become-ftp-commands)
8. [Replies: understanding the three-digit numbers](#8-replies-understanding-the-three-digit-numbers)
9. [Logging in: a small state machine](#9-logging-in-a-small-state-machine)
10. [Passive mode: the client opens the data connection](#10-passive-mode-the-client-opens-the-data-connection)
11. [Active mode: the server opens the data connection](#11-active-mode-the-server-opens-the-data-connection)
12. [One upload, from terminal command to server file](#12-one-upload-from-terminal-command-to-server-file)
13. [One download, from server file to local destination](#13-one-download-from-server-file-to-local-destination)
14. [Binary and ASCII: why transfer type matters](#14-binary-and-ascii-why-transfer-type-matters)
15. [The virtual filesystem and root confinement](#15-the-virtual-filesystem-and-root-confinement)
16. [Staging files: publishing a transfer safely](#16-staging-files-publishing-a-transfer-safely)
17. [Inside the concurrent server](#17-inside-the-concurrent-server)
18. [Inside the client and terminal interface](#18-inside-the-client-and-terminal-interface)
19. [Errors, timeouts, and shutdown](#19-errors-timeouts-and-shutdown)
20. [How the tests check the implementation](#20-how-the-tests-check-the-implementation)
21. [Running it yourself](#21-running-it-yourself)
22. [File-by-file reference and reading order](#22-file-by-file-reference-and-reading-order)
23. [Questions you should be able to answer](#23-questions-you-should-be-able-to-answer)
24. [Glossary and further reading](#24-glossary-and-further-reading)

---

## 1. The 30,000-foot view

Imagine a file-storage office. You speak to a clerk to log in, ask which
files exist, and request an upload or download. The actual file travels
through a separate delivery opening. Finishing a delivery does not end
your conversation with the clerk.

FTP uses the same separation:

- The **control connection** carries instructions and replies.
- A **data connection** carries a file or directory listing.

```text
                   CONTROL: commands and replies
             USER, PASS, LIST, RETR, STOR, 150, 226 ...
        +-----------------------------------------------+
        |                                               |
        v                                               v
  +-------------+                                +-------------+
  | FTP client  |                                | FTP server  |
  | client.py   |                                | server.py   |
  +-------------+                                +-------------+
        ^                                               ^
        |                                               |
        +-----------------------------------------------+
                    DATA: file/listing bytes
                 a separate TCP connection
```

The control connection usually stays open for the entire login session.
In this implementation, each transfer gets a new, single-use data
connection. After that connection closes, the control connection can
carry the next command.

The client is not permanently the sender, and the server is not
permanently the receiver:

| Operation | File/listing bytes travel from | To |
| --- | --- | --- |
| Upload: `put`, wire command `STOR` | Client | Server |
| Download: `get`, wire command `RETR` | Server | Client |
| Directory listing: `ls`, wire command `LIST` | Server | Client |

Both programs run on the same computer for the simplest demonstration.
They still communicate through real TCP sockets. Files are not copied
directly between the client and server by a shared-file shortcut.

---

## 2. Background concepts you need first

### 2.1 Bytes, strings, and encoding

A **byte** holds eight bits, giving 256 possible values: 0 through 255.
A binary file is a sequence of these byte values. A photograph, executable,
or compressed archive may contain any of them.

Python distinguishes text from bytes:

```python
text = 'USER student'             # str: characters
wire = text.encode('utf-8')       # bytes: ready for a socket
text_again = wire.decode('utf-8') # str: ready for command parsing
```

The `b` prefix marks a bytes literal, such as `b'hello'`. Escapes can name
bytes that are awkward to print: `b'\x00'` is a zero byte, and `b'\xff'`
is byte value 255. `b''` is an empty byte string, not a byte with value zero.

This project decodes control messages and directory names using UTF-8.
Binary file contents stay as bytes; they are not decoded as text. That
distinction is why a file containing `b'\x00\xff'` can be transferred safely.

### 2.2 IP addresses and ports

An IP address identifies a network endpoint's host. A port distinguishes
services on that host. In `127.0.0.1:2121`:

- `127.0.0.1` is the loopback address: this computer.
- `2121` is the server's control-listening port.

Traditional FTP uses control port 21; this lab defaults to 2121 so it can
run as an ordinary user on Linux. The client must connect to the port the
server actually chose. See the [FTP connection model](https://datatracker.ietf.org/doc/html/rfc959#section-3.2).

Port numbers occupy 16 bits. When the server binds to port **0**, it asks
the operating system to allocate an available port. Tests use this to
avoid hardcoding a port that another application might already occupy.
The client then connects to the resulting nonzero port.

### 2.3 What is a socket?

A **socket** is a program's handle to a network endpoint. A connected TCP
socket lets one program send bytes and another receive them.

```text
        program A                         program B
      socket.sendall(...)  ----------->  socket.recv(...)
      socket.recv(...)     <-----------  socket.sendall(...)
```

The operating system handles network packets underneath those calls.
The Python code works with byte streams rather than constructing IP
or TCP packet headers itself.

### 2.4 What TCP provides, and what the application must still do

TCP provides an ordered byte stream with transport-level reliability.
It can retransmit packets internally. A connection can still fail, time
out, or close before the application receives the file it expected.

TCP does **not** preserve the boundaries between calls to `sendall()`.
For example:

```text
Sender's calls:    [USER student\r\n] [PASS example\r\n]

Possible reads:   [US] [ER student\r\nPASS ex] [ample\r\n]
       or:        [USER student\r\nPASS example\r\n]
```

Both are valid. The receiver must find FTP's message boundaries itself.
That is the purpose of `ControlStream`, not a flaw in TCP.

### 2.5 What EOF means

**EOF**, or end of file, also has a socket meaning. If `recv()` returns
`b''`, the peer has ended its sending side and no more bytes remain to
be read. A reset or a timeout generally raises an exception instead.

For a data connection in this project's stream mode, clean EOF marks
the end of a transfer's bytes. For a control connection, EOF means the
session has ended. The code reacts differently to these two situations.

### 2.6 Local and remote paths

**Local** means “on the machine running the client.” **Remote** means
“in the server's FTP filesystem.” These meanings remain distinct even
when both processes happen to run on the same computer.

```text
put downloads/report.txt reports/final.txt
    +------------------+ +---------------+
        local source       remote destination

get reports/final.txt downloads/copy.txt
    +---------------+ +----------------+
       remote source     local destination
```

Changing the remote directory with `cd` does not change the client's
local shell directory.

---

## 3. What this project implements

The group assignment supplies the requirement to implement protocols
using suitable sockets. This directory implements the FTP component in
Python, independently of ARP. Its modules do not import the ARP project.

FTP logic is written here: command interpretation, reply parsing, login
state, data negotiation, text conversion, and file transfer. Python's
`socketserver` supplies generic connection/thread management; it does not
implement FTP for us. Python's FTP library, `ftplib`, is used only by tests
as an independent implementation to check against.

The supported feature set is a documented subset, not every feature
described by the FTP standards:

| Area | What is implemented |
| --- | --- |
| Login | Configured username/password, `USER` followed by `PASS`, reinitialization (`REIN`), and account handling (`ACCT`) |
| File transfer | Upload (`STOR`), store unique (`STOU`), append (`APPE`), download (`RETR`), allocation acknowledgement (`ALLO`), binary and NVT ASCII types |
| Data setup | IPv4 PASV/EPSV and PORT/EPRT |
| Directories | Working directory, navigation (`CWD`, `CDUP`), creation (`MKD`), empty-directory removal (`RMD`) |
| File operations | Listings (`LIST`, `NLST`), status over control connection (`STAT`), binary size (`SIZE`), regular-file deletion (`DELE`), and renaming (`RNFR`/`RNTO`) |
| RFC 959 Commands | Complete coverage of all 33 RFC 959 commands (including `ABOR`, `NOOP`, `SYST`, `HELP`, `SITE`, `SMNT`, `REST`) |
| Concurrent use | Independent sessions, bounded by a client limit |
| File publication | Staging files followed by atomic replacement |
| Errors | Numeric FTP errors, connection timeouts, cleanup |

Some defaults belong to the server; others belong to the client:

| Setting | Default or bound | Where it comes from |
| --- | --- | --- |
| Control address | `127.0.0.1:2121` | Both command-line entry points |
| Server root | `server-data/` | Server CLI |
| Username | `student` | Both CLIs |
| Password | `FTP_PASSWORD`; interactive client may prompt | Both CLIs |
| Socket timeout | 30 seconds | Server and client constructors/CLIs |
| Concurrent client limit | 32 | Server |
| Client file preference | Binary | `FTPClient.binary = True` |
| Client data preference | Passive | `FTPClient.passive` |
| Initial server session type | ASCII (`A`) | `FTPSession.handle()` |
| File-data read block | 65,536 bytes | `protocol.BLOCK_SIZE` |
| Control-buffer receive call | Up to 4,096 bytes | `ControlStream.readline()` |
| Command line | Up to 512 encoded bytes, including CRLF | Server parser and client command validation |
| Reply line | Up to 16 KiB, including CRLF | `ControlStream` defaults |
| Normalized virtual path | Up to 4,096 UTF-8 bytes | `Sandbox.normalize()` |

The initial ASCII server state and binary client preference are consistent:
the client explicitly sends `TYPE I` before a binary file operation.
These bounds are implementation choices; they should not be presented as
universal limits for every FTP server.

The browser layer adds a separate set of defaults. Do not confuse its HTTP
port with the FTP control or data ports:

| Browser setting | Default or behavior |
| --- | --- |
| HTTP listener | `0.0.0.0:8080`, accepting the host's IPv4 interfaces |
| Host-only alternative | `uv run --locked --no-dev python -m ftp_lab.webapp --demo --host 127.0.0.1` |
| First account | Created through loopback on the host computer |
| Other browsers | Sign in at `http://HOST_LAN_IP:8080` after owner setup |
| Shared data | Owner's saved profiles, transfer history, and host FTP folder |
| Session-private data | Browser cookie/CSRF token and remembered external FTP passwords |
| Internal demo FTP | Loopback, dynamically assigned port, generated credentials |
| Transfer workers | Two, with at most eight nonterminal jobs |
| File and unpacked bound | 8 GiB; this is a bound, not an 8 GiB test result |

One workspace owner can sign in from multiple devices. This is different
from creating a separate account for each person or turning every browser
into a remotely accessible FTP server. The browser's file chooser refers
to that browser device; **Host computer** refers to the Python server's
shared FTP directory.

---

## 4. The four main modules

```text
                       YOUR TERMINAL
                             |
                             v
                  ftp_lab/client.py
               commands + transfer client
                    |               |
                    | TCP sockets   | protocol helpers
                    |               v
                    |       ftp_lab/protocol.py
                    |       CRLF lines, FTP replies,
                    |       ASCII conversion, chunks
                    |               ^
                    v               |
                  ftp_lab/server.py
              sessions + FTP command handling
                             |
                             v
                  ftp_lab/filesystem.py
               virtual root + file operations
                             |
                             v
                    SERVER'S DISK FILES
```

- [protocol.py](../ftp_lab/protocol.py) solves byte/message representation.
- [filesystem.py](../ftp_lab/filesystem.py) solves file access under a root.
- [server.py](../ftp_lab/server.py) decides what each FTP command means.
- [client.py](../ftp_lab/client.py) turns user actions into FTP exchanges.

Keeping these responsibilities apart lets a framing test use a socket pair
without starting the entire server, and lets a filesystem test check a
failed upload without needing a network connection.

### 4.1 How browsers on other devices reach FTP

```text
phone browser -------- HTTP to HOST_LAN_IP:8080 --------+
                                                     |
second PC browser ----- independent HTTP session ------+
                                                     v
                                         webapp/server.py
                                       auth + Host/Origin/CSRF
                                                     |
                                                     v
                                         webapp/service.py
                                        jobs + progress + bounds
                                            |             |
                                            v             v
                                      database.py      FTPClient
                                        SQLite            |
                                                         FTP
                                                          |
                                               host shared folder or
                                               saved external FTP server
```

The HTTP listener binds to all IPv4 interfaces by default. For each request,
`getsockname()` on the accepted connection identifies the actual destination
IP. The request Host must match that address and the app port; a loopback
connection also accepts `localhost`. Mutating requests must carry the exact
same Origin, the application header, and an authenticated CSRF token.
Allowing network clients does not require accepting arbitrary Host headers.

Only a loopback peer connecting to a loopback destination can create the
first owner. A LAN browser can sign in afterward, but cannot take over a
new workspace. Sessions have independent cookies and device passwords,
while SQLite stores the shared account's device profiles and job history.
The built-in FTP server can stay on loopback because the Python host,
rather than the phone's browser, opens its FTP connections.

Continue with [LOCAL_APP.md](LOCAL_APP.md#3-architecture-and-source-reading-order)
for the complete service/database/frontend flow and
[LAN_ACCESS.md](LAN_ACCESS.md) for network setup and its limits.

---

## 5. The Python features that make the code work

### 5.1 A module, a package, and `python -m`

A `.py` file is a module. The `ftp_lab/` directory, containing
`__init__.py`, is a package. Running:

```bash
uv run --locked --no-dev python -m ftp_lab.server
```

uses uv to select the project environment, then asks Python to run that
module in its package context. Imports such as
`from .filesystem import Sandbox` then refer to another module in the
same package. Run the command from `Group_Project/FTP`, the directory
containing `ftp_lab`. Running `python ftp_lab/server.py` directly does
not establish the same package context and can break those relative imports.

The familiar block at the bottom of each entry-point module:

```python
if __name__ == '__main__':
    main()
```

means “start the command-line program when this module is executed.”
Importing the classes in a test does not automatically start a server.
The client uses `raise SystemExit(main())` so its return value becomes
the process exit status.

### 5.2 Objects carry session state

An object groups data with functions that act on that data. `self` names
the current object. Every `FTPSession` has its own directory, login state,
transfer type, and negotiated data endpoint. `self.authenticated` in one
session does not log in another session.

An annotation such as `request: socket.socket` describes an expected
type to readers and tools. It does not itself create a socket or test
the runtime value. The `isinstance()` check in `FTPSession.setup()` is
an actual runtime check.

### 5.3 `with`: cleanup even when an operation fails

Opening a file or socket allocates an operating-system resource. A
`with` statement arranges to release it when the block ends:

```python
with Path(local).open('rb') as source:
    # use source here
    ...
# source is closed, including if an exception escaped the block
```

This is why much of the transfer code is organized around context
managers. A network failure should not leave open files or listening ports.

### 5.4 `@contextmanager`: pause at `yield`, then clean up

Several project functions create custom context managers. Conceptually:

```python
@contextmanager
def resource():
    acquire_resource()
    try:
        yield usable_value
    finally:
        release_resource()
```

When used in `with resource() as value`, execution pauses at `yield`
while the caller runs its block. When the block ends, the generator
continues. If the block raises an exception, it is raised at the `yield`
point inside the generator.

That detail explains `Sandbox.upload()`: a successful caller reaches
the code that publishes the file; an exceptional caller skips publication
and reaches the cleanup in `finally`.

### 5.5 `ExitStack`: collect resources dynamically

Some operations open different resources depending on the command.
`ExitStack` lets code enter the chosen context managers and release
them in reverse order later:

```text
enter file/staging context
    enter data-socket context
        move bytes
    leave data socket
leave file/staging context
```

The server's transfer function can therefore select a read context for
`RETR`, or an upload context for `STOR`, without duplicating all the
connection-handling code.

### 5.6 Generators stream data instead of loading a whole file

A generator produces values as needed. `socket_chunks()` yields one
received bytes block at a time. The file-reading expression:

```python
iter(lambda: source.read(BLOCK_SIZE), b'')
```

means “keep calling `read(BLOCK_SIZE)` until it returns `b''`.” The
`lambda` is a small unnamed function; the second argument to `iter`
is the stopping value.

The ASCII encoder/decoder wrap these iterators. File transfer memory
use is based on chunks, rather than the entire file size. Directory
listings are a separate case: the client collects a listing in memory
before decoding it into lines.

### 5.7 Exceptions separate normal results from failures

`raise` transfers control to a matching `except` block. `finally` runs
whether the operation succeeded, returned early, or raised an exception.
The code uses three named exception types for different boundaries:

| Exception | Meaning |
| --- | --- |
| `ProtocolError` | Malformed framing/reply or an invalid protocol expectation |
| `CommandError` | Server should send a particular FTP error code/message |
| `FTPError` | Client received a negative FTP reply from its peer |

Operating-system failures use exceptions such as `OSError`,
`ConnectionResetError`, and `TimeoutError`. A “file missing” result is
handled differently from an oversized control line because the former
usually allows a session to continue, while the latter closes it here.

---

## 6. Sockets: listening, connecting, and moving bytes

### 6.1 The listening socket and the accepted socket are different

The server begins with a **listening socket**. When a client connects,
`accept()` produces another socket for that particular conversation.
The listening socket remains available to accept other clients.

```text
                        server listening socket
                           127.0.0.1:2121
                                  |
                              accept()
                       +----------+----------+
                       |                     |
                       v                     v
               client A control      client B control
                   socket                socket
                       |                     |
                  session A             session B
```

`socketserver.ThreadingTCPServer` performs the control-listener setup
and acceptance. The project's data-connection code calls `socket()`,
`bind()`, `listen()`, `accept()`, and `connect()` directly.

### 6.2 The core operations

| Operation | Plain meaning | Where it matters here |
| --- | --- | --- |
| `socket(AF_INET, SOCK_STREAM)` | Create an IPv4 TCP endpoint | Client control/data sockets and server data sockets |
| `bind((host, port))` | Choose a local address/port | Data listeners; control listener via `socketserver` |
| `listen(...)` | Accept incoming connection attempts | Server passive data or client active data |
| `accept()` | Obtain a socket for an incoming peer | Passive server or active client |
| `connect((host, port))` | Initiate a TCP connection | Control client and whichever side initiates data |
| `sendall(bytes)` | Keep sending until all supplied bytes are accepted locally, or an error occurs | Control lines and outgoing file blocks |
| `recv(n)` | Receive up to `n` bytes | Control framing and incoming file blocks |
| `settimeout(seconds)` | Bound blocking socket operations | Avoid waiting indefinitely |
| `close()` | Release this socket handle | Normal completion and cleanup |

`sendall()` returning does not prove that the remote application saved
the data. FTP's later completion reply provides the application-level
result. Even that reply is not a cryptographic checksum or a promise
that storage survives a power failure.

### 6.3 A block size is not a network packet size

`BLOCK_SIZE = 65536` controls how much file data the Python loop asks
for at once. It does not force TCP packets to have 65,536-byte payloads,
nor require `recv()` to return exactly that many bytes. Any shorter
nonempty block is processed normally.

---

## 7. Control messages: how TCP bytes become FTP commands

This is the main job of [protocol.py](../ftp_lab/protocol.py).

### 7.1 CRLF is the message delimiter

FTP commands end with **CRLF**: carriage return, byte 13 (`\r`), followed
by line feed, byte 10 (`\n`). For example:

```text
Characters:  N  O  O  P  CR LF
Hex bytes:  4e 4f 4f 50 0d 0a
```

`ControlStream.send_line('NOOP')` encodes the text as UTF-8, appends
`b'\r\n'`, validates the send bound, and calls `sendall()`.
It rejects embedded CR, LF, or NUL so an argument cannot inject a
second command.

### 7.2 Why `readline()` keeps a buffer

Suppose a read supplies:

```text
USER student\r\nNOOP\r\n
```

Returning all of it as a single command would be wrong. Returning the
first command and discarding the rest would lose `NOOP`. The buffer
lets the parser remove only the line it has consumed:

```text
buffer before:  [USER student\r\n][NOOP\r\n]
                 +---------------+
                 consume one line

return value:   "USER student"
buffer after:   [NOOP\r\n]
```

If no LF exists yet, the function receives more bytes and tries again.
That handles fragmentation. If another complete line is already present,
the next call returns it without needing another network read. That
handles multiple queued commands, sometimes called pipelining.

### 7.3 The parser, step by step

The following is explanatory pseudocode for `ControlStream.readline()`:

```text
repeat:
    look for LF in the buffered bytes
    if found:
        require the preceding byte to be CR
        require this line to fit the receive limit
        remove exactly that CRLF-terminated line from the buffer
        reject forbidden control bytes
        decode the line as UTF-8 and return it

    if the unfinished buffered line has reached its limit:
        fail instead of allocating indefinitely

    receive more bytes
    if the peer ended the stream:
        unfinished line -> ProtocolError
        no unfinished line -> EOFError
    otherwise append bytes and repeat
```

At this framing layer, horizontal tab is allowed; other low control
bytes and DEL are rejected. Filename validation is stricter and rejects
control characters, including tab. An invalid UTF-8 line is also rejected.

### 7.4 Why commands and replies have separate bounds

The server creates `ControlStream(request, max_line=512)`, limiting
incoming commands. Outgoing replies use the separate `max_send_line`
bound, whose default is 16 KiB. The client reads replies with the
16 KiB default and separately caps the commands it sends at 512 bytes.

A reply may need more space than its command. For example, `PWD` is
three letters, but its reply can contain a long current directory.
Inside quoted FTP paths, `"` is represented as `""`, which expands
the text further. The virtual-path bound and larger reply bound allow
these responses without increasing the server's command limit.

---

## 8. Replies: understanding the three-digit numbers

FTP replies contain a numeric code followed by human-readable text:

```text
230 Login successful
^^^ +--------------+
code    explanation
```

Programs should act on the code rather than depending on the exact
wording of a sentence. The first digit gives a useful broad category:

| First digit | Meaning | Example in this project |
| --- | --- | --- |
| `1` | Preliminary: an operation is starting | `150 Opening data connection` |
| `2` | Successful completion | `226 Transfer complete` |
| `3` | Another step is required | `331 Password required` |
| `4` | Negative result classified as transient by FTP | `425 Cannot open data connection` |
| `5` | Negative completion for the current request | `550 File or directory unavailable or forbidden` |

A `5xx` result does not mean a file can never become available. It means
this request was not completed under the current conditions.

### 8.1 A transfer has a beginning reply and an ending reply

```text
RETR report.txt
       |
       +--> 150  "starting"
               [file bytes on the data socket]
       +--> 226  "finished"
```

The client must read both. If it reads only `150`, the pending `226`
might later be mistaken for the reply to `PWD` or another command.

### 8.2 Multiline replies

`FEAT` advertises supported extensions with several lines:

```text
211-Features
 EPRT
 EPSV
 SIZE
 UTF8
211 End
```

The hyphen after the first `211` starts a multiline reply. The matching
`211` followed by a space ends it. An unrelated `200` line inside that
reply must not end it.

`read_reply()` validates the initial three ASCII digits and separator,
collects the lines, and stops at the matching terminator. It bounds
multiline replies to 128 total lines. The resulting immutable `Reply`
object contains `code` and `lines`; its `message` property takes the
text after the code and separator on the final line.

### 8.3 Error codes you will actually encounter

| Code | Meaning here |
| --- | --- |
| `212` | Directory status returned for `STAT <directory>` (distinguished from file status `213`) |
| `421` | Session timeout, capacity rejection, or shutdown rejection |
| `425` | Missing/unusable data setup or failure to establish the data connection |
| `426` | Transfer failed after its preliminary reply (connection broken or aborted) |
| `450` | File unavailable or locked due to transient busy state (e.g. `EBUSY`, `EAGAIN`) |
| `451` | Local filesystem or I/O error during data stream processing |
| `452` | Insufficient storage space on host system (`ENOSPC`) |
| `500` | Invalid command syntax or malformed/oversized control framing |
| `501` | Invalid command arguments or forbidden data endpoint |
| `502` | Command not implemented |
| `503` | `PASS` arrived before `USER`, or `RNTO` arrived before `RNFR` |
| `504` | Unsupported transfer parameter, such as an unsupported TYPE |
| `522` | An unsupported network family was requested by an extended data command |
| `530` | Login required or credentials rejected |
| `550` | File/directory unavailable or forbidden; also SIZE requested outside binary type |
| `552` | Exceeded storage allocation or user disk quota (`EDQUOT`) |
| `553` | File name not allowed (invalid destination name, reserved name, or syntax error) |

These meanings follow the supported command paths in
[server.py](../ftp_lab/server.py). General FTP reply categories are
described in [RFC 959, section 4.2](https://datatracker.ietf.org/doc/html/rfc959#section-4.2).

---

## 9. Logging in: a small state machine

A **state machine** is a system whose response depends on both its
current state and its input. Sending `PASS` before `USER` is different
from sending the same password after selecting a username.

```text
          connection accepted
                  |
                  v
          not authenticated
                  |
             USER student
                  |
                  v
       username selected, reply 331
                  |
             PASS password
            /             \
       valid               invalid
         |                    |
         v                    v
  authenticated, 230    unauthenticated, 530
```

`FTPSession.handle()` initially sets `pending_user = None` and
`authenticated = False`. `dispatch()` then implements the transitions:

1. `USER` clears existing data setup, records the username, revokes
   previous authentication, resets the virtual directory to `/`, and
   replies `331`.
2. `PASS` without a selected username produces `503`.
3. Otherwise both username and password are compared with the configured
   credentials using `hmac.compare_digest()` on encoded bytes.
4. The session becomes authenticated only if both comparisons match.

Sending a new `USER` after logging in revokes access until another
successful password exchange. Authentication is not a permanent flag
that can survive switching to a different username.

The server permits connection-management/information commands such as
`QUIT`, `NOOP`, `SYST`, `FEAT`, `HELP`, and the supported `OPTS` request
before login. File and data-setup operations require authentication.

### 9.1 Where the password comes from

The server CLI reads `FTP_PASSWORD` from its environment and requires a
nonempty value. The client reads the same variable if present; otherwise
an interactive client uses `getpass.getpass()` to prompt without echoing
the typed password. A noninteractive client without the variable reports
an argument error instead of waiting for a hidden prompt.

`compare_digest()` is a comparison helper, not password encryption or a
password database. This lab has one account whose configured password is
held in memory. FTP sends the `PASS` bytes over the network in plaintext.
The trace prints `PASS <redacted>`, but redaction affects terminal output,
not the bytes transmitted to the server.

---

## 10. Passive mode: the client opens the data connection

Passive mode answers one question: **who initiates the data connection?**
The client does. The server listens on an additional port and tells the
client which port to use.

### 10.1 EPSV, step by step

```text
CLIENT                                          SERVER
  |                                                |
  |---------- EPSV on control connection ---------->|
  |                                   create a data listener
  |                                   bind to local IP, port 0
  |                                   OS chooses, e.g. 50000
  |<--------- 229 (|||50000|) -----------------------|
  |                                                |
  |=========== connect to server:50000 ============>|
  |           separate data TCP connection          |
  |                                                |
  |---------- RETR report.txt --------------------->|
  |<--------- 150 opening --------------------------|
  |<========= file bytes, then data EOF ============|
  |<--------- 226 complete -------------------------|
```

The real port is dynamically assigned; 50000 is an explanatory example.
The client connects its passive data socket before sending `RETR`/`STOR`/
`LIST`. On the server, `accept()` is performed when handling that
transfer command, after the preliminary reply. The listening socket's
queue allows the TCP connection to be pending until then.

### 10.2 How the server and client divide the work

In `FTPSession.configure_data()`:

1. Old data setup is discarded.
2. A new TCP listener is created and registered for cleanup.
3. It binds to the control socket's local IP and an available port.
4. The listener is saved in `self.passive`.
5. The server returns `229` with the port for EPSV.

In `FTPClient.passive_port()`, the client extracts and validates the
port. `FTPClient.data_setup()` then connects to the control peer's IP
at that port. Neither side needs the file contents to negotiate this.

### 10.3 PASV and the two-number port calculation

Older PASV replies include an IPv4 address and two port components:

```text
227 Entering Passive Mode (127,0,0,1,195,80)
                            IP      p1 p2
```

The port is:

```text
port = p1 * 256 + p2
     = 195 * 256 + 80
     = 50000
```

The server uses integer division and remainder to perform the inverse:
`p1 = port // 256`, `p2 = port % 256`.

The client first tries EPSV. It falls back to PASV only for selected
unsupported/invalid-extension replies: `500`, `501`, `502`, `504`, or
`522`. It does not treat every network or login error as permission to
ignore the error and try a different command.

### 10.4 Why the client ignores the PASV address

The PASV reply can advertise an IP address. This client deliberately
uses the control connection's peer IP instead, while retaining the
advertised port. That prevents the reply from redirecting the data
connection to a different host. The server also accepts passive data
connections only from the control peer's IP.

IP matching is a useful endpoint restriction; it does not cryptographically
authenticate another process sharing that IP. This is a trusted-network
lab implementation.

---

## 11. Active mode: the server opens the data connection

In active mode the client listens for data, announces its endpoint,
and the server connects to it. The control connection is still initiated
by the client; active mode changes only the data connection's initiator.

```text
CLIENT                                          SERVER
  |                                                |
  | create listener on client IP, port 0            |
  | OS chooses, e.g. 50001                          |
  |                                                |
  |------- EPRT |1|127.0.0.1|50001| --------------->|
  |<------ 200 endpoint accepted ------------------|
  |------- STOR report.txt ------------------------>|
  |<------ 150 opening ----------------------------|
  |<========== server connects to client:50001 =====|
  |=========== uploaded file bytes ===============>|
  | close data socket                              |
  |<------ 226 complete ----------------------------|
```

The `1` in EPRT identifies IPv4. `|` is the delimiter separating the
family, address, and port. The server parses and validates the fields
before saving the endpoint in `self.active`.

The client tries EPRT first. For the supported fallback error codes
`500`, `501`, `502`, and `504`, it tries PORT, whose address and port
use the same six-number representation as PASV.

### 11.1 Endpoint restrictions

The server accepts an active endpoint only when:

- Its IP matches the client on the control connection.
- Its destination port is between 1024 and 65535.

This restricts FTP bounce behavior: using the server to make arbitrary
connections to other hosts or privileged destination ports. The active
client also checks the incoming data connection's peer IP against its
control peer before accepting the stream for the transfer.

This implementation does **not** bind the active server data socket to
source port 20. It creates a TCP socket and lets the OS choose the local
source endpoint when `connect()` runs. Describing every data connection
in this project as “port 20” would therefore be incorrect.

### 11.2 How passive and active compare

| Question | Passive | Active |
| --- | --- | --- |
| Who listens for data? | Server | Client |
| Who calls `connect()` for data? | Client | Server |
| Setup commands | EPSV, fallback PASV | EPRT, fallback PORT |
| Incoming data port needed on | Server | Client |
| File direction | Depends on upload/download | Depends on upload/download |

Passive does not mean “download,” and active does not mean “upload.”
Either mode can carry either operation.

The server also supports `EPSV 1` and `EPSV ALL`. `EPSV ALL` commits that
session to EPSV-only data setup, rejecting PASV/PORT/EPRT afterward. The
normal client does not send `EPSV ALL`, so its active/passive switch
remains usable.

The extended command syntax comes from
[RFC 2428, sections 2 and 3](https://www.rfc-editor.org/rfc/rfc2428).

---

## 12. One upload, from terminal command to server file

Suppose the client is logged in, its remote directory is `/demo`, and
you enter:

```text
put downloads/notes.txt "remote notes.txt"
```

The source is a local file. The destination is
`server-data/demo/remote notes.txt` when the default server root is used.

### 12.1 The call chain

```text
client.main()
  -> execute(client, terminal_line)
       -> shlex.split(line)
       -> client.upload(local_path, remote_name)
            -> open local source as binary bytes
            -> command(TYPE, I)             [binary preference]
            -> transfer(STOR, remote_name, send_callback)
                 -> data_setup()            [EPSV/PASV or EPRT/PORT]
                 -> command(STOR, remote_name)
                 -> send_callback(data_socket)
                 -> read final completion reply
```

`shlex.split()` turns the quoted filename into one argument. The quotes
used for grouping are not sent as part of the name. The actual wire
command is `STOR remote notes.txt`, followed by CRLF.

The server separates the verb from its argument at the **first space**.
It does not split a path at every space, so the remainder stays one path.

### 12.2 The exchange on the two connections

Here is a passive binary upload; the data port is illustrative:

```text
CLIENT                                            SERVER
  |---- TYPE I ------------------------------------->|
  |<--- 200 -----------------------------------------|
  |---- EPSV --------------------------------------->|
  |<--- 229 (|||50000|) ------------------------------|
  |==== connect data socket ========================>|
  |---- STOR remote notes.txt ---------------------->|
  |                                  validate remote path
  |                                  create hidden staging file
  |<--- 150 -----------------------------------------|
  |==== source bytes ===============================>|
  |==== more source bytes ==========================>|
  | close data socket                        receive clean EOF
  |                                  close/flush staging file
  |                                  replace destination
  |<--- 226 -----------------------------------------|
```

The arrows marked `====` belong to the data connection. The other arrows
belong to the persistent control connection.

### 12.3 What the server does with STOR

`FTPSession.dispatch()` checks authentication and delegates to
`authenticated_command()`, which selects `transfer('STOR', path)`.
That transfer function:

1. Enters `Sandbox.upload(path)`, validating the destination and opening
   a hidden staging file in its parent directory.
2. Requires an active or passive data endpoint. If none exists, it
   reports `425`; unwinding the context removes the staging file.
3. Sends `150`, then establishes/accepts the data connection.
4. Receives blocks from `socket_chunks(data)`.
5. Writes them directly for binary type, or through `decode_ascii()`
   for ASCII type.
6. Leaves the data context, closing that socket.
7. Leaves the upload context, which publishes the completed file.
8. Sends `226`, then clears the single-use data setup in `finally`.

The final filename is not gradually overwritten while the upload runs.
The [staging-file section](#16-staging-files-publishing-a-transfer-safely)
explains how that property is achieved.

### 12.4 What if the file is empty?

An empty file still has a real FTP operation: TYPE, data negotiation,
STOR, `150`, data EOF, and `226`. The data loop simply has no nonempty
blocks to send. The server publishes a zero-length regular file.

---

## 13. One download, from server file to local destination

For:

```text
get "remote notes.txt" downloads/copy.txt
```

the wire command is `RETR remote notes.txt`. The file bytes travel in
the reverse direction from an upload. The same active/passive mechanism
chooses who opens the data connection.

### 13.1 The client prepares a temporary destination

`FTPClient.download()` creates a `NamedTemporaryFile` in the destination's
parent directory. It does not open the final destination for truncation.
The temporary filename begins with `.ftp-download-`.

```text
Before download:       copy.txt = old complete contents
During download:      .ftp-download-... = incoming bytes
After success:        copy.txt = new complete contents
After failure:        copy.txt = old complete contents
```

The destination's parent directory must already exist. The client does
not silently create a whole local directory tree for a misspelled path.

### 13.2 The server reads a regular file

On `RETR`, the server enters `Sandbox.open_read(path)` before sending
`150`. This checks the path and opens a permitted regular file. A missing
file can therefore produce `550` immediately, rather than first announcing
that a transfer is starting.

The server reads at most `BLOCK_SIZE` bytes per file-read call and sends
each resulting block. In ASCII type it applies `encode_ascii()` first.
It closes the data socket when the source is exhausted and then sends
the control completion reply.

### 13.3 The client requires the completion reply

The client's receive callback reads data until EOF. In binary mode it
writes the blocks unchanged; in ASCII mode it decodes NVT newlines.
`FTPClient.transfer()` then reads the final reply and requires `226`
or `250` before reporting success to `download()`.

Only after the temporary output file has closed successfully does
`os.replace(staging, target)` publish it as the requested local filename.
A `finally` block removes any remaining staging file on failure.

Receiving some bytes, or even receiving clean data EOF, is not enough
for this client to replace a local file if the server subsequently
reports a failed transfer.

---

## 14. Binary and ASCII: why transfer type matters

### 14.1 Binary type means the bytes are the file

With `TYPE I`, bytes pass through unchanged. This is appropriate for
arbitrary file formats and is the shipped client's default preference.

```text
local bytes:    00 ff 0a 0d 41
wire bytes:     00 ff 0a 0d 41
remote bytes:   00 ff 0a 0d 41
```

There is no text decoding, newline normalization, application-level CRC,
or custom file header inserted into that stream.

### 14.2 ASCII type uses an agreed newline representation

POSIX text files commonly separate lines using LF alone. FTP's NVT
representation uses CRLF for line endings and CR NUL for a literal CR.
The project implements these conversions explicitly:

| Local POSIX bytes | NVT wire bytes | Meaning |
| --- | --- | --- |
| `b'\n'` | `b'\r\n'` | A newline |
| `b'\r'` | `b'\r\x00'` | A literal carriage return |
| Other bytes | Unchanged by these helpers | No general file-encoding conversion |

For example:

```text
local:   b'a\nb\rc\r\n'
wire:    b'a\r\nb\r\x00c\r\x00\r\n'
decoded: b'a\nb\rc\r\n'
```

`encode_ascii()` first replaces literal CR with CR NUL, then LF with
CRLF. The order matters: replacing LF first would introduce CR bytes
that the later replacement could incorrectly escape.

The helpers operate on bytes. Choosing `ascii` does not validate that
every file byte is a seven-bit ASCII character, nor transcode an entire
file between UTF-8 and some other text encoding.

### 14.3 Why decoding remembers a pending CR

TCP may split an encoded newline between blocks:

```text
block 1: b'first\r'
block 2: b'\nsecond\r'
block 3: b'\x00end'
```

`decode_ascii()` retains `pending_cr` across generator iterations. On
seeing the next byte:

- LF completes a newline and produces one local LF.
- NUL completes a literal-CR escape and produces one CR.
- Another byte causes the retained CR to be emitted, then that new
  byte is processed normally.

If the stream ends with a pending CR, it is emitted as a literal CR.
Without the retained state, a CRLF divided across two receives could
be decoded differently from an identical CRLF received in one block.

### 14.4 Listings and SIZE do not erase the user's preference

`FTPClient.list()` sends `TYPE A`. `FTPClient.size()` sends `TYPE I`,
because this server implements SIZE for the binary representation.
Neither method modifies the client's `binary` preference attribute.

Each file upload/download sends TYPE again from that preference. Thus:

```text
binary -> ls -> get photo.jpg
          |          |
       TYPE A      TYPE I, then RETR
```

The photo is still downloaded in binary mode. In the opposite direction,
choosing `ascii`, calling `size`, and then downloading a text file restores
TYPE A for that download.

The server accepts the standard Table 10.4 configurations: `TYPE A` (ASCII),
`TYPE E` / `TYPE EBCDIC` (EBCDIC), and `TYPE I` / `TYPE L 8` (Image/binary).
For structure and mode, it accepts `STRU F` (file), `STRU R` (record), and
`STRU P` (page), plus `MODE S` (stream), `MODE B` (block), and `MODE C`
(compressed). Unsupported parameter values receive `504` or `501`.

---

## 15. The virtual filesystem and root confinement

[filesystem.py](../ftp_lab/filesystem.py) makes the server's configured
directory appear to be an independent filesystem whose root is `/`.

### 15.1 The FTP root is not the computer's root

With the default configuration:

```text
FTP-visible path                Actual server path
-----------------------------   ---------------------------------
/                               Group_Project/FTP/server-data/
/demo                           Group_Project/FTP/server-data/demo/
/demo/notes.txt                 .../server-data/demo/notes.txt
```

An FTP absolute path starts at the FTP root. `/etc/passwd` would refer
to `server-data/etc/passwd`, if that permitted file existed; it does
not request the host's `/etc/passwd`.

`Sandbox` holds the virtual current directory as a tuple of components,
such as `('demo', 'reports')`. It does not call `os.chdir()`. Changing
the process working directory would be unsuitable for independent
threads, because it would affect every session in the process.

### 15.2 `normalize()` turns a request into components

For a session currently at `/demo/reports`:

| Input | Resulting virtual path |
| --- | --- |
| `file.txt` | `/demo/reports/file.txt` |
| `../file.txt` | `/demo/file.txt` |
| `/shared/file.txt` | `/shared/file.txt` |
| `./file.txt` | `/demo/reports/file.txt` |
| `../../../outside` | Rejected: it would go above `/` |

The function handles empty components, `.`, and `..`; rejects forbidden
characters and reserved staging-name components; and bounds the final
normalized path to 4096 UTF-8 bytes. The final bound includes the current
directory, so a short relative request cannot bypass it in a deep tree.

The operating system can impose additional restrictions, such as the
maximum length of an individual filename component.

### 15.3 Why string normalization alone is insufficient

Suppose a directory under the root contains a symbolic link pointing
outside it. A path can look harmless as a string while following that
link reaches another directory.

The code therefore combines normalization with descriptor-relative
operations. A **file descriptor** is an OS handle to an already opened
resource. `Sandbox.__init__()` opens a descriptor for the root directory.
`directory(parts)` walks from that descriptor, opening one component
at a time with directory and no-symlink-following flags.

```text
opened root descriptor
        |
        +-- open "demo" relative to root descriptor
                  |
                  +-- open "reports" relative to demo descriptor
                            |
                            +-- operate on "file.txt" in that parent
```

`parent(path)` provides the parent directory descriptor and final name
to operations such as `mkdir`, `delete`, and `upload`. Subsequent OS
calls use `dir_fd=...` rather than trusting a reconstructed host path.
See Python's [descriptor-relative OS APIs](https://docs.python.org/3/library/os.html#dir-fd).

### 15.4 The important open flags

| Flag or check | Purpose |
| --- | --- |
| `O_DIRECTORY` | Require an opened path component to be a directory |
| `O_NOFOLLOW` | Do not follow a final-component symbolic link |
| `O_NONBLOCK` on a read open | Avoid blocking on a FIFO before the file-kind check |
| `fstat()` plus `S_ISREG` | Require the opened transfer source to be a regular file |
| `O_CREAT | O_EXCL` for staging | Create a new staging file, failing if that name already exists |
| `follow_symlinks=False` in metadata checks | Inspect a link itself instead of its target |

`directory()` opens a fresh description for `.` relative to the root,
rather than simply duplicating a directory descriptor. This avoids
sharing a directory-stream position while listing/walking directories.

These operations are POSIX-specific, which is why this implementation
targets Linux/POSIX rather than claiming unchanged Windows compatibility.

### 15.5 Listings do not execute `ls`

`entries()` enumerates permitted entries, sorts names, and reads their
metadata. It omits symlinks, special files, reserved staging names, and
names containing control characters. It can list one permitted regular
file as well as a directory.

The server formats LIST lines itself with permissions, size, and name.
Its `Jan 01 00:00` field is placeholder display text, not the file's
actual modification time. NLST sends names only. Neither command invokes
a shell, expands wildcards, nor interprets `ls` flags.

The root must be managed by a trusted local operator. These checks
restrict remote FTP operations; they are not an OS sandbox against a
hostile local user who can alter the served directory tree or its files.

---

## 16. Staging files: publishing a transfer safely

### 16.1 Why writing directly to the destination is risky

Opening an existing destination with `'wb'` truncates it immediately.
If the connection fails after the first few blocks, both the original
and the complete replacement are lost.

`Sandbox.upload()` instead creates a hidden name such as:

```text
.ftp-upload-<random UUID>
```

The UUID supplies a candidate name, while exclusive creation enforces
that the newly created staging file did not already exist. Clients may
not access these reserved names through normal FTP paths.

### 16.2 The context manager's success and failure paths

```text
create staging file in destination's parent
                  |
              yield file
                  |
          caller receives/writes bytes
             /                  \
     clean completion           exception
           |                        |
     flush and close            close file
           |                        |
     os.replace(staging, final)     |
           |                        |
           +----------+-------------+
                      |
          finally: remove staging if it remains
```

`os.replace()` changes which file occupies the final name. Putting the
staging file in the same directory keeps the replacement on the same
filesystem, where the rename operation can publish the new name atomically.
Readers using the name see an old or new complete version rather than
an in-place rewrite gradually growing block by block.

The client uses the same pattern for downloads. The server waits for
clean data EOF; the client additionally waits for a successful final FTP
reply before it publishes its local destination.

### 16.3 The limits of this guarantee

**Atomic replacement is not a power-loss durability guarantee.** The
code closes/flushed buffered output but does not implement a full
file-and-directory `fsync()` durability sequence.

If two uploads target the same destination concurrently, the last one
to complete its replacement wins. This is not a version-control or
locking system.

Also, FTP stream mode here supplies no expected final file length or
digest. A cleanly ended shorter upload looks like a valid smaller file.
A reset or timeout is detectable and triggers cleanup; an intentionally
early clean EOF cannot be distinguished from legitimate completion
without additional information. The code does not claim otherwise.

---

## 17. Inside the concurrent server

The server has a shared connection manager, `FTPServer`, and one
`FTPSession` object per control connection. Both are defined in
[server.py](../ftp_lab/server.py).

### 17.1 Startup through `main()`

```text
uv run --locked --no-dev python -m ftp_lab.server
    |
    +-> argparse reads host, port, root, user, timeout, client limit
    +-> read FTP_PASSWORD
    +-> create root directory if needed
    +-> construct FTPServer(...)
    +-> install SIGTERM handler
    +-> enter server context
    +-> print actual bound address
    +-> serve_forever()
```

The constructor checks a finite positive timeout, positive capacity,
valid account strings, and an existing directory root. It then delegates
control-socket construction/binding to `ThreadingTCPServer`.
`FTPServer.address` returns the bound IPv4 host/port pair, which matters
when port 0 was requested.

### 17.2 How a control connection becomes a session

```text
control connection accepted
          |
FTPServer.process_request()
          |
    acquire capacity slot
          |
    start request thread
          |
FTPSession.setup()
          |
FTPSession.handle()
    -> initialize session state and root descriptor
    -> send 220
    -> read line, dispatch command, repeat
    -> finally: clear data setup, close root and control socket
          |
request thread finishes
          |
release capacity slot
```

`BaseRequestHandler` invokes `setup()` before `handle()`. Here `setup()`
checks that the generic framework's server is an `FTPServer` and stores
the narrowed reference as `self.ftp_server`. This makes the dependency
explicit to both runtime code and static type checking.

The networking framework's handler lifecycle and thread behavior are
documented in Python's [socketserver reference](https://docs.python.org/3/library/socketserver.html).

### 17.3 Shared state versus per-session state

| Shared by the server | Owned separately by each session |
| --- | --- |
| Configured root/account | `pending_user`, `authenticated` |
| Socket inactivity timeout | `representation` (`A` or `I`) |
| Capacity semaphore | `passive` listener or `active` endpoint |
| Registry of open session/data sockets | `epsv_only` flag |
| Registry lock and shutdown event | `Sandbox` and its virtual `cwd` |

Different sessions may transfer simultaneously. Within one session,
commands run serially: its command loop handles one operation before
reading the next command.

### 17.4 A semaphore, a lock, and an event have different jobs

- A **bounded semaphore** represents the available client slots.
  `acquire(blocking=False)` either obtains a slot immediately or refuses
  the additional client with `421`. A `finally` block releases the slot
  when a worker ends; failed worker startup also releases it.
- A **lock** protects short modifications/snapshots of the shared socket
  registry. It does not lock the entire server for every transfer.
- An **event** records that shutdown has started. Registration can then
  reject new sockets, while workers can observe the closing state.

`register()` also sets the socket timeout. `discard()` removes the
socket from the registry and closes it. The registry covers session
control sockets, passive listeners, and established data sockets so
shutdown can release blocking I/O resources.

Threading lets independent blocking network conversations overlap. It
does not imply that this program parallelizes arbitrary CPU-heavy work
or supports unlimited clients.

### 17.5 Command dispatch is layered

`dispatch()` extracts the verb, checks its syntax/argument shape,
handles login and allowed pre-login commands, and checks authentication.
`authenticated_command()` then selects the permitted operation:

| Command group | Implementation route |
| --- | --- |
| TYPE/MODE/STRU | Validate supported transfer parameters |
| PWD/CWD/CDUP/MKD/RMD/DELE/RNFR/RNTO/SIZE | Call `Sandbox` methods and reply |
| PASV/EPSV/PORT/EPRT | `configure_data()` |
| LIST/NLST/RETR/STOR | `transfer()` |
| ABOR | Clear idle data setup, reply 226 |
| Unknown authenticated command | Reply 502 through `CommandError` |

`SYST` identifies the lab's UNIX-style environment. `FEAT` advertises
EPRT, EPSV, SIZE, and UTF8. `OPTS UTF8 ON` is the supported option
request. `HELP` returns a command summary.

The server's `transfer()` always clears its data setup in `finally`,
including when file validation or transfer establishment fails. A new
operation must negotiate a new endpoint rather than accidentally reuse
a stale listener or active destination.

---

## 18. Inside the client and terminal interface

### 18.1 Connection construction and reply checks

`FTPClient.__init__()` creates and connects the control socket, wraps
it in `ControlStream`, and requires the server's `220` greeting. It
also handles an initial `120` preliminary greeting before the ready
reply. A failed constructor closes the allocated socket before raising.

`command()` validates an outgoing verb/argument, emits an optional
redacted trace, sends the line, and obtains one complete reply.
`read_response()` raises `FTPError` for `4xx`/`5xx`; it closes the client
on `421`. `expect()` checks whether a nonnegative reply has the specific
code the current operation expects. A syntactically valid but unexpected
reply is a `ProtocolError`.

For example, a successful login requires `230`; accepting an unrelated
`200` merely because it is a `2xx` reply would hide a protocol mistake.

### 18.2 Transfer orchestration and callbacks

`data_setup()` prepares the passive connection or active listener.
`transfer()` sends the operation, requires `125` or `150`, obtains
the appropriate data socket, invokes the operation's callback, closes
the data resources, and requires the final `226` or `250`.

The callback supplies the part that changes between operations:

| Caller | Callback's job |
| --- | --- |
| `upload()` | Read local file blocks and send them |
| `download()` | Receive blocks and write the local staging file |
| `list()` | Receive listing bytes into a bytearray |

These small nested functions capture the already-open source/destination
or listing buffer. This is a Python **closure**: the function uses
variables from the scope in which it was defined.

After a failure following a preliminary transfer reply, `transfer()`
closes the control session. Unread completion replies could otherwise
become confused with replies to later commands. A negative reply before
the transfer starts, such as a missing-file `550`, can leave the control
session reusable.

The optional `progress(delta_bytes)` argument to `upload()` and `download()`
lets the browser service measure actual payload movement without replacing
the FTP implementation. It receives a block's byte count, not the cumulative
total and not TCP/IP overhead. The service accumulates those deltas into
progress, speed, estimates, and graph samples. Download callbacks run before
writing the incoming block so an oversized payload can be rejected before
the crossing chunk is written.

`consume_data()` wraps the operation callback. If it raises, including
cooperative cancellation, the client requests a data-socket reset with
SO_LINGER before closing. This prevents the shipped server from treating
that interrupted upload as a clean, complete smaller file. The app also
compares a staged download's final length with the earlier SIZE reply;
a source that shrank between SIZE and RETR is rejected before its browser
attachment is published. External FTP servers may have different rollback
behavior, so cancellation is not a universal remote undo operation.

### 18.3 The other client methods

`pwd()` requires a `257` reply and extracts its quoted path, converting
doubled quotes back to literal quotes. `size()` selects TYPE I, requires
`213`, and validates the size as ASCII decimal digits. `list()` decodes
the received listing as UTF-8 and splits it into text lines.

`close()` releases the control socket and sets `self.sock` to `None`.
`quit()` first requests a `221` goodbye reply, then closes even if the
exchange fails. The context manager's `__exit__()` calls `close()`;
leaving a `with FTPClient(...)` block alone does not promise to send QUIT.
The terminal interface sends QUIT explicitly on normal user exit or EOF.

### 18.4 Terminal words are not always FTP wire commands

`execute(client, line)` parses shell-style quoting and maps convenient
user commands onto the protocol:

| Terminal input | Result |
| --- | --- |
| `cd path`, `cdup` | CWD, CDUP |
| `mkdir path`, `rmdir path`, `delete path` | MKD, RMD, DELE |
| `rename old new` | RNFR, RNTO |
| `ls`, `nlst` | TYPE A, data negotiation, LIST or NLST |
| `get`, `put` | File-transfer method, including TYPE and data setup |
| `size` | TYPE I, then SIZE |
| `ascii`, `binary` | Change the local file-type preference |
| `passive`, `active` | Change the local data-mode preference |
| `noop`, `help`, `quit`/`bye` | Connection check, local help, or clean exit |

Selecting `binary` does not itself transfer a file. A subsequent file
operation sends the appropriate TYPE command. Similarly, `active`
changes how the next data connection will be negotiated.

### 18.5 Interactive versus batch mode

`main()` chooses interactive mode only when no script was requested
and stdin is a terminal. Interactive mode prints help and a prompt,
reports recoverable command errors, and allows the next command.

`--script file` reads a UTF-8 command file. `--script -` reads stdin.
Piped stdin without `--script` is also noninteractive. Batch mode
stops at the first command error and returns status 1. Argument parsing
errors use status 2; Ctrl+C in the client uses 130; normal completion
returns 0.

---

## 19. Errors, timeouts, and shutdown

### 19.1 Errors cross several boundaries

```text
OS/file failure
     |
     v
server maps permitted failures to CommandError(code, message)
     |
     v
numeric FTP reply on control connection
     |
     v
client read_response() raises FTPError for a negative reply
     |
     v
terminal prints the error, or batch exits with failure
```

For instance, a forbidden filesystem path becomes `550`; failure to
open the negotiated data connection becomes `425`. An `OSError` in
the transfer body after `150` becomes `426`. This implementation does
not classify every disk and network error into all the specialized
reply codes that a larger FTP server might provide.

Malformed framing has its own path: the server attempts a `500` reply
and ends the control session. A partial command at control EOF is not
silently executed as though its missing CRLF had arrived.

### 19.2 A timeout is not the same as a whole-file deadline

Registered sockets use an inactivity/blocking-operation timeout, 30
seconds by default. A large transfer can take longer than 30 seconds
overall if its individual operations keep making progress.

When accepting a data peer, the code also uses `time.monotonic()` to
compute a deadline and the remaining wait. Rejecting a connection from
the wrong IP does not restart an unlimited fresh accept timeout.
Monotonic time is appropriate for elapsed waits because wall-clock
adjustments do not change its elapsed-time direction.

The server's `serve_forever(poll_interval=...)` is a different setting:
it controls how often its service loop checks for shutdown, not how
long a client may pause between commands.

### 19.3 Why QUIT, data EOF, and shutdown are different

| Event | Effect |
| --- | --- |
| Data EOF | Ends the current stream-mode data transfer |
| QUIT | Requests an orderly end to this control session |
| Server shutdown | Stops the service and releases all its session/data resources |

In the server CLI, Ctrl+C interrupts `serve_forever()`. SIGTERM is
mapped to the same `KeyboardInterrupt` path. Leaving `with server`
calls `server_close()`, which sets the closing event, shuts down/closes
registered sockets, and delegates to the threaded server's close logic.
Workers are non-daemon and `block_on_close` is true, so close waits for
their completion.

Shutting down an already accepted data socket can cause `recv()` to return
`b''`, which otherwise means clean EOF. `FTPSession.transfer()` therefore
checks the server's closing event before leaving the ExitStack that owns
the upload staging context. If shutdown caused that EOF, it raises instead
of allowing the partial staging file to replace the old destination. The
deterministic shutdown-EOF test checks this ordering directly, alongside
the real concurrent shutdown test.

In tests, `serve_forever()` runs in a background thread. The controlling
thread calls `shutdown()`, then `server_close()`, then joins the service
thread. Python's `shutdown()` waits for the service loop and must not
be called from that same running service thread; doing so can deadlock.

### 19.4 Features deliberately outside this implementation

The project does not implement TLS/FTPS, SFTP, IPv6, anonymous/multiple
accounts, resume, rename, quotas, or a fixed passive port range. SFTP
is a different SSH-based protocol, not the name for FTP's active mode
or its ASCII type.

ABOR clears idle data setup. Because one session handles commands
serially, it does not parse a new ABOR command in parallel with a blocked
file transfer or implement Telnet urgent/OOB abort handling. Closing
the data connection can interrupt a transfer; whether that produces a
detected failure or clean EOF matters as explained earlier.

These limits belong in a project explanation: knowing which guarantees
the code actually supplies is part of understanding the implementation.

---

## 20. How the tests check the implementation

The current Python test files define **79 test methods**, including the
browser service and benchmark additions. Node.js also runs **10 frontend
helper tests** when available. Some methods exercise
several cases or modes, so that is not a count of individual transfers.
The Python tests use `unittest` from the standard library. The original
terminal-only milestone had 37 tests; its dated observation remains in
[IMPLEMENTATION.md](IMPLEMENTATION.md).

### 20.1 Why “our client works with our server” is insufficient

If both sides make the same protocol mistake, they may still understand
each other. The suite therefore checks several independent boundaries:

```text
Python ftplib  ---------- real sockets ---------->  our server

our client     ---------- real sockets ---------->  our server

our client     ---------- real sockets ---------->  independent wire peer

server CLI process <----- real sockets -----> client CLI process

protocol helpers <------ socket pairs ------> controlled byte sequences
filesystem helpers <--- temporary files ---> checked file outcomes
```

The independent wire peer is a small test implementation that sends
specific reply bytes without using the production reply parser. It can
test a multiline greeting, reject EPSV to force PASV fallback, advertise
an untrusted PASV IP, or send a failed completion after some data bytes.

### 20.2 The test files

| File | Methods | What it checks |
| --- | --- | --- |
| [test_core.py](../tests/test_core.py) | 13 | Framing, truncated/invalid input, multiline replies, injection, ASCII boundaries, root/filesystem operations, symlinks, rollback, special files and path bounds |
| [test_server.py](../tests/test_server.py) | 22 | `ftplib` interoperability, login state, both data modes, binary/ASCII content, directory names, errors, timeouts, reset uploads, concurrency, capacity, shutdown and long replies |
| [test_client.py](../tests/test_client.py) | 9 | Own client in both modes, type preferences, redaction, local preservation, independent peers, progress/cancellation and batch CLI behavior |
| [test_cli.py](../tests/test_cli.py) | 2 | Separate server/client subprocesses, script files, SIGTERM cleanup and clean missing-password errors |
| [test_benchmark.py](../tests/test_benchmark.py) | 12 | Original/gzip verification, samples and exports, cancellation/cleanup, invalid input and hostile oversized retrieval |
| [test_webapp.py](../tests/test_webapp.py) | 11 | SQLite/auth persistence, request protections, jobs, streaming, gzip, ownership, cancellation, source claims and size/name bounds |
| [test_permutations.py](../tests/test_permutations.py) | 3 | Sample manifest integrity, representation/structure/mode negotiation, and downloads of all 27 fixtures |
| [test_network_app.py](../tests/test_network_app.py) | 7 | LAN binding, shared files/profiles, independent sessions/passwords, Host/Origin checks, concurrent demo-profile creation, owner bootstrap; real-interface case opt-in |

[test_frontend.cjs](../tests/test_frontend.cjs) adds ten Node.js tests for
readable sizes/times, folder paths, graph samples, compression comparisons,
complete preparation timing, ignoring a stale response after logout, and
the completed-download filename/source presentation without changing the
names shown for pending, original or upload jobs. The command catalog and
guided-scenario tests also validate the RFC explorer data.

The binary server test includes an empty file, all 256 byte values,
and a larger payload built as `bytes(range(256)) * 8193`. It checks
returned bytes and the server-side file contents in both active and
passive modes. A printed “success” message alone would not prove that.

The reset-upload test uses Linux socket linger behavior to force a reset
and checks that the old destination survives. The shutdown-during-upload
test waits until staging bytes have reached the file before requesting
close, rather than racing a shutdown against a transfer that might not
have begun.

### 20.3 `support.py`: reusable test setup

[support.py](../tests/support.py) has two helpers:

- `running_server()` creates a temporary root, starts an FTPServer on
  loopback port 0, runs `serve_forever()` in a background thread, yields
  the server/root to the test, then shuts down and joins on exit.
- `connect()` constructs a standard-library `ftplib.FTP` client and
  optionally logs in with the test account. If connection/login fails,
  it closes the partially created client before propagating the error.

The helper's default server timeout is 0.5 seconds to make timeout
tests quick; that is not the normal CLI's 30-second default.

The independent-peer helper uses a `ThreadPoolExecutor`. Calling the
worker future's `result(timeout=4)` brings exceptions from the peer
thread back to the test thread. Otherwise an assertion failing in a
background thread could be missed by the main test.

### 20.4 What the Makefile actually does

`make test` runs `uv run --locked --extra plots python` so its interpreter
and dependencies come from the project lock. It creates `build/tmp` under
the FTP directory, sets `TMPDIR`, `TMP`, and `TEMP` to that path, disables
bytecode generation for the run, and invokes unittest discovery.
`ResourceWarning` is treated as an error to help expose resource leaks.

Tests using `tempfile` inherit that temporary location from the command's
environment. Running unittest manually without the variables would not
automatically place its temporary data in `build`.

`make lint` selects the default development group and the `plots` extra so
Pylint can resolve the optional renderer's imports. `make sync`
synchronizes `.venv` with that group and the `plots` extra. `make server`,
`make client`, `make app`, and `make clean-db` run through
`uv run --locked --no-dev python` because their runtime uses only the
standard library. The server still requires its
password environment variable; Make does not supply a hidden account.

These checks establish particular behaviors under test. They do not
establish production hardening, performance numbers, full RFC compliance,
or that a network/firewall configuration on another machine will work.

---

## 21. Running it yourself

### 21.1 Start from the repository root

Requirements are Python 3.11 or newer and a Linux/POSIX environment with
the descriptor-relative APIs used above, plus uv for environment management.
Run `uv sync --locked --extra plots` inside `Group_Project/FTP` first; see
[the environment setup](../README.md#set-up-the-environment) for dependency
groups, offline operation, and upgrades. `make` is optional. Use an unused server root/directory name for the
first walkthrough so preexisting demonstration files do not conflict.

In **terminal 1**:

```bash
cd Group_Project/FTP
FTP_PASSWORD=lab-demo uv run --locked --no-dev python -m ftp_lab.server
```

The server should announce an address like:

```text
Listening on 127.0.0.1:2121 root=<absolute path to server-data>
```

`lab-demo` is an example lab password. The source does not contain a
built-in password. The environment assignment in this command supplies
it to that server process.

### 21.2 Prepare a local file and connect

In **terminal 2**, independently starting from the repository root:

```bash
cd Group_Project/FTP
mkdir -p downloads
printf 'Hello from FTP.\n' > downloads/hello.txt
uv run --locked --no-dev python -m ftp_lab.client --trace
```

Enter the same password when prompted. Trace lines beginning with `C>`
are client control commands; lines beginning with `S>` are server replies.
The password trace is redacted. File bytes do not appear as control
trace lines because they use a different connection.

### 21.3 Exercise both connection modes

At the `ftp>` prompt, enter:

```text
pwd
mkdir lesson
cd lesson
put downloads/hello.txt "hello world.txt"
ls
size "hello world.txt"
get "hello world.txt" downloads/passive-copy.txt
active
get "hello world.txt" downloads/active-copy.txt
passive
ascii
get "hello world.txt" downloads/ascii-copy.txt
binary
delete "hello world.txt"
cdup
rmdir lesson
quit
```

What to look for:

1. `pwd` initially prints `/`.
2. Remote paths are under `/lesson` after `cd lesson`.
3. File transfers show TYPE, data setup, `150`, then `226`.
4. Passive operations normally show EPSV; the active download shows EPRT.
5. The local terminal commands `ascii` and `binary` affect later file
   transfers; they do not themselves send a file.
6. `delete`/`rmdir` remove only the remote sample created in this session.
   Local copies remain available for verification.

Back in the local shell:

```bash
cmp downloads/hello.txt downloads/passive-copy.txt
cmp downloads/hello.txt downloads/active-copy.txt
cmp downloads/hello.txt downloads/ascii-copy.txt
```

For this sample, each comparison should succeed without output. The
ASCII round trip reconstructs the POSIX newline representation.
Stop the server in terminal 1 with Ctrl+C when finished.

### 21.4 A batch example

With the server running, this reads terminal commands from stdin:

```bash
FTP_PASSWORD=lab-demo uv run --locked --no-dev python -m ftp_lab.client --script - <<'EOF'
pwd
nlst
quit
EOF
```

The shell's here-document supplies the input lines. `EOF` is the shell
delimiter, not an FTP command sent to the server.

For a controlled failure example, request a filename you know is absent:

```bash
FTP_PASSWORD=lab-demo uv run --locked --no-dev python -m ftp_lab.client --script - <<'EOF'
get definitely-missing-lesson-file.txt downloads/should-not-exist.txt
EOF
echo "$?"
```

For an absent remote file, the error includes `550` and the exit status
is 1. A preexisting local destination would be preserved. Check the
status immediately: running another shell command changes `$?`.

### 21.5 Run the full test suite

From the FTP directory:

```bash
make test
```

Or, from the repository root without make:

```bash
mkdir -p Group_Project/FTP/build/tmp
TMPDIR="$PWD/Group_Project/FTP/build/tmp" TMP="$PWD/Group_Project/FTP/build/tmp" TEMP="$PWD/Group_Project/FTP/build/tmp" \
  PYTHONPATH="$PWD/Group_Project/FTP" \
  uv run --project Group_Project/FTP --locked --extra plots python \
  -W error::ResourceWarning -m unittest discover -s Group_Project/FTP/tests -v
node Group_Project/FTP/tests/test_frontend.cjs
```

The current Python suite discovers 79 tests. With all optional tools
available but no `FTP_TEST_LAN_HOST`, 78 pass and the real-interface case
is skipped. `make test` also runs ten frontend tests if Node.js is installed.
The suite requires local socket access; a sandbox rejecting even loopback traffic must
allow that access for these tests.
Set `FTP_TEST_LAN_HOST` to an assigned local LAN IPv4 address to run the
real-interface case too; that case otherwise reports a skip.

### 21.6 Common startup and usage problems

| Symptom | What to check |
| --- | --- |
| No module named `ftp_lab` | Run from `Group_Project/FTP`, or use the documented PYTHONPATH for a repository-root test run |
| Relative-import error when running a source file directly | Use `uv run --locked --no-dev python -m ftp_lab.server`/`client` |
| Server requests `FTP_PASSWORD` | Supply the environment variable to the server command |
| Connection refused | Start the server first; check host and control port |
| Address already in use | Choose another server `--port` and use the same client `--port` |
| `530` | Check account/password and login state |
| `425` on another machine | Check the additional incoming data port, not only the control port |
| `550` | Check remote path, current remote directory, permissions, supported file kind or TYPE for SIZE |
| A local output path fails | Create its parent directory and check local permissions |
| Listing a wildcard or `-l` fails | LIST takes a literal path here; shell expansion/flags are not implemented |

For a trusted LAN demonstration, bind the server with `--host` set to
its LAN IPv4 address and use that address on the client. Passive mode
needs incoming ephemeral data connections on the server; active mode
needs them on the client. Fixed passive ranges, NAT setup, and public
deployment are not provided by these programs.

### 21.7 Use the browser workspace from another device

On the host computer, from the repository root:

```bash
cd Group_Project/FTP
uv run --locked --no-dev python -m ftp_lab.webapp --demo
```

Create the first workspace account at `http://127.0.0.1:8080` on the host.
Find its active LAN IPv4 address in network settings. On another computer
or phone on the same trusted network, open `http://HOST_LAN_IP:8080`, using
that address, and sign in with the workspace account.

Upload a sample in one browser. In the other, refresh the **Host computer**
folder, choose the file's **Download**, wait for completion, and select
**Save file**. The source chooser and final saved copy belong to the browser
device; the intermediate shared folder belongs to the Python host. The
automated independent-session regression verifies the copied bytes exactly.

The app's port 8080 carries HTTP. It is separate from the standalone FTP
server's default control port 2121 and its per-transfer data ports. The
browser service defaults to all IPv4 interfaces; the terminal server keeps
its documented loopback default unless its `--host` is changed. See
[LAN_ACCESS.md](LAN_ACCESS.md) for that direct-FTP workflow and troubleshooting.

For host-only browser operation, add `--host 127.0.0.1`. Plaintext HTTP/FTP
is intended for a trusted lab LAN. A host-interface test does not prove
that a particular phone's Wi-Fi isolation or firewall path will permit it.

---

## 22. File-by-file reference and reading order

### 22.1 Directory map

```text
Group_Project/FTP/
|-- .gitignore
|-- Makefile
|-- pyproject.toml             <- runtime/extras/development dependency declarations
|-- uv.lock                    <- locked direct and transitive dependencies
|-- README.md
|-- FTP.code-workspace
|-- benchmarks/
|   |-- README.md
|   `-- results/               <- small measured CSV/JSON and PNG/SVG reports
|-- docs/
|   |-- IMPLEMENTATION.md
|   |-- LOCAL_APP_PLAN.md
|   |-- LOCAL_APP.md           <- browser app and architecture walkthrough
|   |-- LAN_ACCESS.md          <- multi-device access and validation
|   `-- explanation.md          <- this walkthrough
|-- ftp_lab/
|   |-- __init__.py
|   |-- protocol.py
|   |-- filesystem.py
|   |-- server.py
|   |-- client.py
|   |-- benchmark.py
|   |-- plots.py               <- optional PNG/SVG rendering
|   `-- webapp/
|       |-- __init__.py
|       |-- __main__.py
|       |-- database.py
|       |-- service.py
|       |-- server.py
|       `-- static/            <- index.html, styles.css, app.js
|-- tools/
|   |-- clean_db.py
|   |-- generate_report_evidences.py
|   |-- generate_sandbox_samples.py
|   `-- media_fixtures.py
`-- tests/
    |-- support.py
    |-- test_core.py
    |-- test_server.py
    |-- test_client.py
    |-- test_cli.py
    |-- test_benchmark.py
    |-- test_webapp.py
    |-- test_network_app.py
    |-- test_permutations.py
    `-- test_frontend.cjs
```

Suggested reading order is `protocol.py` → `filesystem.py` → `server.py`
→ `client.py` → the tests. That introduces helpers before the functions
that combine them. Read `main()` in each entry point to see how the
objects become a running program.

### 22.2 `ftp_lab/protocol.py`

| Symbol | Responsibility |
| --- | --- |
| `BLOCK_SIZE` | File-data read size used by streaming loops |
| `ProtocolError` | Distinguish malformed protocol data/expectations from a normal negative FTP result |
| `ControlStream.__init__()` | Store socket, receive/send bounds and the unconsumed byte buffer |
| `ControlStream.readline()` | Reassemble exactly one bounded UTF-8 CRLF line |
| `ControlStream.send_line()` | Validate, encode, delimit and send one line |
| `Reply`, `Reply.message` | Immutable parsed reply plus final-line message text |
| `read_reply()` | Parse a single-line or matching-code multiline reply |
| `socket_chunks()` | Yield incoming data blocks until clean EOF |
| `encode_ascii()` | Convert POSIX newline/literal CR to NVT bytes |
| `decode_ascii()` | Reverse those conversions while retaining CR state across blocks |

The file contains representation helpers, not login policy or disk paths.

### 22.3 `ftp_lab/filesystem.py`

| Symbol | Responsibility |
| --- | --- |
| `STAGING_PREFIX`, `valid_name()` | Reserve internal upload names and reject control-character names |
| `Sandbox.__init__()`, `close()` | Acquire/release the root directory descriptor |
| `normalize()` | Convert an absolute/relative virtual path into bounded root-relative components |
| `directory()` | Walk/open a directory through descriptors without following symlinks |
| `parent()` | Yield a permitted parent descriptor and final component |
| `pwd()`, `chdir()` | Read/update this session's virtual directory |
| `open_read()` | Open and validate a regular transfer source |
| `upload()` | Provide a staging output stream, publish on success, clean up on failure |
| `size()` | Return a regular file's byte size |
| `mkdir()`, `rmdir()`, `delete()` | Perform the allowed directory/file mutations |
| `entries()` | Return sorted permitted names and metadata for a listing |

This module has no knowledge of FTP reply codes. It raises normal
Python/OS exceptions; the server decides which wire reply to send.

### 22.4 `ftp_lab/server.py`

| Symbol | Responsibility |
| --- | --- |
| `CommandError` | Carry the numeric reply and message for an unsuccessful command |
| `FTPServer.__init__()` | Validate configuration and initialize the threaded listener/registry/capacity controls |
| `FTPServer.address` | Expose the bound IPv4 host and port |
| `register()`, `discard()` | Track sockets, apply timeouts, and close resources |
| `process_request()` | Admit/reject a control connection before starting its worker |
| `process_request_thread()` | Guarantee capacity release when the worker exits |
| `server_close()` | Mark shutdown, interrupt tracked sockets, and wait for workers |
| `FTPSession.setup()` | Validate/narrow the server reference used by this handler |
| `FTPSession.handle()` | Initialize state, greet, read/dispatch commands, and clean up the session |
| `reply()` | Write a numeric single-line response |
| `reset_data()` | Close passive setup and discard the active endpoint |
| `dispatch()` | Parse the verb/argument and handle syntax, login and authentication gating |
| `authenticated_command()` | Route the supported authenticated commands |
| `configure_data()` | Validate/negotiate passive listeners or active endpoints |
| `data_connection()` | Establish/yield/close the transfer's actual data socket |
| `transfer()` | Combine file/listing work, data movement, replies and data-setup cleanup |
| `main()`, its `interrupt()` callback | Parse CLI settings, start the service, and map SIGTERM to orderly exit |

### 22.5 `ftp_lab/client.py`

| Symbol | Responsibility |
| --- | --- |
| `FTPError` | Preserve a negative peer reply as an exception |
| `FTPClient.__init__()` | Connect the control socket and validate the greeting |
| `__enter__()`, `__exit__()` | Allow `with FTPClient(...)`, closing on block exit |
| `close()`, `quit()` | Release immediately, or request goodbye then release |
| `read_response()`, `expect()` | Parse/classify replies and require operation-specific success codes |
| `command()` | Validate, trace, send and receive one command exchange |
| `login()` | Perform USER/PASS sequencing |
| `pwd()`, `size()` | Interpret a quoted remote path or binary file size |
| `passive_port()` | Parse EPSV, or selected-error fallback PASV, into a valid data port |
| `data_setup()` | Prepare the chosen passive connection or active listener |
| `transfer()` | Run a data callback between preliminary and final replies |
| `consume_data()` | Reset an interrupted data socket before close so cancellation does not look like ordinary EOF |
| `list()` | Receive and decode LIST/NLST output |
| `upload()` | Stream a local source into STOR |
| `download()` | Receive RETR into a local staging file and publish on success |
| `HELP`, `execute()` | Define and interpret the user-facing terminal command set |
| `main()` | Select credentials/input mode, run the command loop and return an exit status |

`ftp_lab/__init__.py` identifies the package and contains a descriptive
docstring; it is not an alternative server or a hidden FTP implementation.

### 22.6 Supporting files

- [README.md](../README.md): concise setup, feature and limitation reference.
- [pyproject.toml](../pyproject.toml) and [uv.lock](../uv.lock): dependency
  declarations and the reproducible environment selected by uv.
- [Makefile](../Makefile): locked uv test/lint environments and launch shortcuts.
- [IMPLEMENTATION.md](IMPLEMENTATION.md): scope, design, implementation
  checklist and recorded validation context.
- [.gitignore](../.gitignore): omit sample server/download data, Python
  caches/environments, local environment settings and logs. Source, tests,
  this explanation, and the other documentation remain trackable.
- [tests/support.py](../tests/support.py), eight Python test modules and
  the Node.js helper tests: setup and the checks explained in section 20.
- [LOCAL_APP.md](LOCAL_APP.md): browser operation and module-by-module
  database/auth/service/frontend explanation.
- [LAN_ACCESS.md](LAN_ACCESS.md): multi-device URLs, host-only bootstrap,
  ports, shared versus session-private state, and network troubleshooting.
- [LOCAL_APP_PLAN.md](LOCAL_APP_PLAN.md): current design and JSON contract.
- [benchmarks/README.md](../benchmarks/README.md): measured audio/video
  observations, graphs, timing definitions and reproduction commands.

### 22.7 Browser and benchmark modules

| Module | Main symbols and responsibility |
| --- | --- |
| `webapp/database.py` | `Database`, `password_hash`, `token_hash`, `csrf_token`: schema, verifiers, session digests and persistent owner data |
| `webapp/service.py` | `AppService`, `filename`, `remote_path`: job validation, disk streaming, session passwords, workers, cancellation and attachments |
| `webapp/server.py` | `AppServer`, `AppHandler`, `main`: bind options, request destination/Origin checks, owner bootstrap, routes and HTTP streaming |
| `webapp/static/app.js` | Session generation guard, API wrapper, source staging, job polling, `downloadPresentation()` for output/source names, dialogs and SVG rendering |
| `benchmark.py` | `run_benchmark`, `csv_report`, `export_reports`: measured plain/gzip transfers, bounded SHA-256 verification and raw reports; `render_graphs` forwards to the optional renderer |
| `plots.py` | `render_graphs`: standalone Matplotlib PNG/SVG exports; imported plotting dependencies are needed only when charts are requested |
| `tools/media_fixtures.py` | `generate`, `main`: offline playable WAV/raw AVI/MP4 sources and provenance manifest |

The detailed [local app guide](LOCAL_APP.md) follows an upload and download
through these layers. The [benchmark report](../benchmarks/README.md) explains
why source bytes, compressed payload bytes, FTP throughput and effective
throughput are separate measurements.

---

## 23. Questions you should be able to answer

### 23.1 Why use two connections?

That is FTP's control/data model. Commands and completion replies remain
on a persistent control stream while a file or listing uses a separate
data stream. The implementation follows that separation rather than
inventing a one-socket file-copy protocol.

### 23.2 Why not just call `recv()` once for a command?

TCP is a stream. One receive can contain half a line or several lines.
`ControlStream` retains bytes until CRLF completes one command and keeps
the remainder for the next call.

### 23.3 What does passive mean?

The server listens for the data connection and the client initiates it.
It does not describe which side supplies the file bytes.

### 23.4 What proves a download succeeded?

The client needs data EOF and an accepted final completion reply, then
successful closure/publication of its local output. Tests additionally
compare the actual bytes. There is no automatic cryptographic file hash
exchange in this protocol subset.

### 23.5 Why send TYPE again before a file transfer?

Other operations can change the server's current representation: a
listing selects A and SIZE selects I. Sending the client's chosen TYPE
again ensures the next file operation uses the intended representation.

### 23.6 Why not use the process working directory for remote `cd`?

It is process-wide. One session would change the directory used by
another. A separate `Sandbox.cwd` tuple keeps each session independent.

### 23.7 Is blocking `..` enough to confine file access?

No. Symlinks can redirect apparently ordinary paths. The implementation
also uses descriptor-relative walking and no-follow flags, rejects
symlinks/special files for transfers, and assumes trusted local root
management.

### 23.8 Why use temporary files instead of directly opening the target?

Direct `'wb'` access destroys the old target immediately. A staging file
lets a detected failed transfer be discarded while preserving the old
destination, and publishes a completed replacement with a rename.

### 23.9 Why close the client session after some transfer errors?

After `150`, a later completion/error reply may still be queued on the
control connection. Reusing the session without knowing exactly which
replies remain could associate the wrong reply with the next command.
This client closes rather than attempting complex resynchronization.

### 23.10 Is socketserver doing the FTP work?

It manages generic TCP acceptance and worker threads. This project's
code implements the FTP commands, numeric replies, state, data
negotiation, filesystem rules and transfer behavior.

### 23.11 Why test with ftplib if we wrote our own client?

Interoperability with an independent implementation catches mistakes
that an identically mistaken in-house client/server pair could share.
Independent wire peers similarly test branches in the shipped client.

### 23.12 Does this implementation need extra retransmission/CRC code?

It relies on TCP for ordered transport rather than implementing a
second link-layer reliability simulator. Application-level correctness
still requires parsing, login/state checks, data errors, completion
replies and safe file handling. Adding a custom CRC trailer would also
change the FTP file stream unless a separate extension were defined.

### 23.13 What should you claim when presenting the project?

Claim a Python, socket-based FTP subset with independent client/server,
active/passive data modes, file/directory operations and the verified
test cases. Explain the plaintext/trusted-lab scope and missing features.
You can also present Relay's authenticated browser workflow, shared LAN
workspace, independent browser sessions, and verified compression graphs.
Keep the preserved loopback performance observations separate from a claim
about real Wi-Fi speed or physical second-device verification.
Do not describe it as SFTP, encrypted FTP, an ARP simulation, a production
file service, or a complete implementation of every FTP extension.

---

## 24. Glossary and further reading

| Term | Meaning in this project |
| --- | --- |
| Byte | An eight-bit value; file contents and socket streams are sequences of bytes |
| UTF-8 | The encoding used for control text and listing filenames |
| Socket | An OS-backed handle to a network endpoint |
| TCP | Ordered, reliable byte-stream transport; it does not supply application message boundaries |
| Loopback | Same-machine networking, here using `127.0.0.1` |
| Port | A number selecting a transport endpoint on a host |
| Ephemeral port | A dynamically selected local port, used for data listeners and outgoing connections |
| Listening socket | A socket waiting for incoming TCP connections |
| Accepted socket | A new socket representing one accepted peer connection |
| Control connection | The session's persistent command/reply TCP connection |
| Data connection | A separate TCP connection for file or listing bytes |
| Passive mode | Client initiates data; server listens |
| Active mode | Server initiates data; client listens |
| CRLF | Carriage return followed by line feed; the FTP control-line terminator |
| NVT ASCII | The network text representation used for ASCII-mode CR/LF conversion |
| EOF | End of the incoming stream; a clean socket EOF is represented by `recv()` returning `b''` |
| Preliminary reply | A reply such as 150 announcing that an operation is starting |
| Completion reply | A reply such as 226 reporting that the operation finished |
| State machine | Behavior defined by current state plus the next input |
| Session | One connected client's login, working directory, type and data setup |
| Virtual root | The configured server directory presented to FTP clients as `/` |
| File descriptor | An OS handle to an opened file, directory, socket or similar resource |
| Symbolic link | A filesystem entry that redirects path lookup to another path |
| Regular file | An ordinary byte-storage file, rather than a directory, FIFO, device or link |
| Staging file | A temporary file holding incoming data before publication |
| Atomic replacement | Publishing a new file under a final name in one rename operation |
| Context manager | A `with`-compatible object that controls entry/exit and cleanup |
| Generator | A function/iterator producing values over time through `yield` |
| Callback | A function passed to other code to perform an operation-specific step |
| Thread | An execution path sharing the process's resources with other threads |
| Lock | A guard allowing one thread at a time to manipulate protected shared state |
| Semaphore | A counter limiting how many operations/clients may proceed concurrently |
| Event | A shared flag used here to communicate shutdown |
| Timeout | A bound on a wait; it is distinct from a successful EOF |
| Monotonic clock | A clock suitable for elapsed-time deadlines unaffected by wall-clock changes |
| Interoperability test | Checking protocol behavior against an independent implementation |
| LAN | A local network shared by the host and other devices, such as trusted Wi-Fi |
| Wildcard bind | Listening on all host IPv4 interfaces with `0.0.0.0`, rather than using that value as a browser destination |
| Browser session | An app owner's authenticated cookie/CSRF context, distinct from a short-lived FTP connection |
| Host header | HTTP request authority that this app checks against the accepted socket destination |
| Origin | Browser request source that must exactly match the requested app authority for mutations |
| CSRF token | Session-derived value required to authorize app mutations from the frontend |
| Payload throughput | FTP file bytes divided by measured upload time, excluding network headers |
| Effective throughput | Original source bytes divided by preparation-plus-upload time |

The repository source links throughout this document define the behavior
of this particular implementation. For the wider protocol and Python
APIs, consult these primary references:

- [RFC 959: File Transfer Protocol](https://datatracker.ietf.org/doc/html/rfc959):
  control/data connections, commands, representations and replies.
- [RFC 2428: FTP Extensions for IPv6 and NATs](https://www.rfc-editor.org/rfc/rfc2428):
  EPRT/EPSV syntax; this project implements the IPv4 portion of those commands.
- [Python socket documentation](https://docs.python.org/3/library/socket.html):
  socket creation, connection operations, timeouts and byte transfer.
- [Python socketserver documentation](https://docs.python.org/3/library/socketserver.html):
  request-handler lifecycle, threaded servers and shutdown requirements.
- [Python contextlib documentation](https://docs.python.org/3/library/contextlib.html):
  `contextmanager` and `ExitStack` cleanup behavior.
- [Python os documentation](https://docs.python.org/3/library/os.html):
  descriptor-relative operations, file flags and replacement.
- [Python ftplib documentation](https://docs.python.org/3/library/ftplib.html):
  the independent FTP client used by this project's server tests.
