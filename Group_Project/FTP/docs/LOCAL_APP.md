# Relay: the local FTP app, explained

Relay adds a browser workspace to the socket-based Python FTP client.
It provides local account authentication, saved FTP devices, upload and
download progress, persistent history, and measured compression comparisons.
The browser does not implement FTP: Python still sends the actual FTP
commands over separate TCP control and data connections.

For the underlying protocol and four core modules, read
[the FTP walkthrough](explanation.md). For recorded audio/video results,
read [the benchmark report](../benchmarks/README.md).

## 1. Start and use the app

From the repository root:

```bash
cd Group_Project/FTP
python3 -m ftp_lab.webapp --demo
```

`make app` runs the same command. On the host, open `http://127.0.0.1:8080`.
The app binds to all IPv4 interfaces by default. Other devices on the same
trusted LAN use `http://HOST_LAN_IP:8080`; see [the LAN guide](LAN_ACCESS.md).
Python 3.11+ on the Linux/POSIX environment described in the README is enough;
the app requires no package installation, frontend build, cloud account,
internet connection, or external JavaScript CDN.

On first use, create a local account with a username of 2–64 characters and
a password of 8–256 characters. Subsequent visits show **Sign in**. There is
one initial workspace owner; there is no registration list or password reset
interface. Keep your password available. Do not remove the database as a
routine way to resolve a sign-in problem: it holds profiles and history too.

The default `--demo` mode starts the project's FTP server on a dynamically
assigned loopback port. **Host computer** appears as a connected device
after sign-in. Its FTP account uses an internally generated password, so
you do not need to enter a second password for this device. It serves
`app-data/shared/`, initially containing `Welcome.txt`.
This folder belongs to the Python host, even when Relay is opened on a
phone. The browser-to-host leg uses HTTP; the internal FTP leg can remain
on loopback while multiple browsers access the shared workspace.

The three views separate common actions from additional detail:

| View | Purpose |
| --- | --- |
| Transfers | Choose a device, send a file, browse/download files, see active progress |
| History | Revisit completed, failed, or cancelled jobs; retry or remove local records |
| Performance | Compare original and gzip payloads, timings, verification and graphs |

The interface uses a light canvas, restrained green accents, labeled
controls, keyboard focus states, and reduced-motion support. Transfer
information stays visible without exposing protocol commands to the user.

### 1.1 Add another FTP device

1. Start an FTP server on the other computer or another local port.
2. Select **Add a device**. Enter a friendly name, hostname or IPv4 address,
   FTP control port, and FTP username. Enter an address such as
   `192.168.1.20`, not a URL containing `ftp://` or a port suffix.
3. Select **Save device**, then enter that FTP server's password and connect.
4. Select its card to use it as the destination or source.

Relay saves the profile in SQLite. It checks credentials with an actual
FTP login and PWD command before marking the profile connected. The
connection indicator means session credentials are available; each listing
or transfer opens its own FTP connection, so a device can still become
unreachable after connection was checked.

Relay's local password and the device's FTP password serve different
purposes. Signing in to Relay does not grant access to an external FTP
server. Device passwords are retained in process memory for that app
session, for at most eight hours, and cleared on sign-out or restart.
The saved profile remains, so reconnecting only requires its password.

For the project's server on a trusted LAN, start it with its LAN IPv4
address using the CLI instructions in the [README](../README.md). The
browser service itself stays on the computer running Relay. Passive FTP
requires additional incoming data ports at the FTP server, beyond its
control port. NAT configuration and fixed passive port ranges are not
implemented here.

### 1.2 Upload a file

Choose the destination device and folder, then choose or drop a file.
Select **Send file**. One selected file creates one job.

There are two separate legs:

```text
browser File -- local HTTP PUT --> Python staging file
Python file -- real FTP STOR --> chosen device
```

The first phase prepares a local disk copy. The second phase sends that
copy over FTP. A preparation progress bar reaching 100% therefore does
not mean the device has received the file; follow the phase label until
the job says **Completed**. The FTP phase has its own bytes and progress.

**Compress before sending** creates an explicit gzip file named
`original-name.gz` on the device. It does not change the original browser
file or silently decompress on the other computer. Without compression,
the selected filename is used directly. Uploading an existing name can
replace that remote file under the FTP server's rules. The shipped server
publishes uploads with an atomic staged replacement after successful EOF.

### 1.3 Download a file

Select **Download** beside a remote file. Python first retrieves it over
FTP into the app's transfer directory. On completion, select **Save file**
to download the prepared copy through the browser.

**Unpack .gz downloads** decompresses a gzip file locally before offering
the saved copy. For example, `lecture.wav.gz` becomes `lecture.wav`.
Enable it before selecting **Download**; changing the checkbox does not
modify a file already prepared by an earlier job. Only one gzip layer is
removed: `archive.tar.gz` becomes `archive.tar`, not a folder of extracted
files. The completed card shows the output filename, **Unpacked from** the
source name, its output size, and **Save unpacked file**. Its transfer byte
counter still measures the compressed FTP payload, not the expanded size.
Ordinary downloads retain their filename and bytes. A failed FTP retrieval
does not offer a partial local file. Gzip output is bounded to 8 GiB and
invalid output basenames are rejected before work starts.
A damaged header, truncated stream, or invalid compressed block fails with
a clear gzip error and removes partial local output. Turn unpacking off and
start a new download if you need to save the original compressed file.

### 1.4 Cancel, retry, and history

**Cancel** requests cooperative cancellation. Running work may briefly
show **Cancelling** until a worker reaches a cancellation check or a socket
operation returns. A queued job can be cancelled before it starts. For an
upload, retry may require choosing the source file again because browsers
do not persist file access across sessions. Reconnect the device if its
session password has expired.

Removing a terminal history item removes its database record and local
prepared download. It does not delete the remote FTP file. Active jobs
must finish or be cancelled before removal. Completed uploads and
benchmarks discard their temporary local source copies; completed
downloads remain available until their history entry is removed.

## 2. Where data lives and how restart works

All devices signed in with the workspace owner see the same database-backed
profiles and history. Their session cookies, CSRF tokens and external FTP
passwords remain independent. The first-owner creation step is performed
on the host's loopback URL; later LAN sign-ins use the host's assigned IP.

The default data directory is relative to the shell's working directory.
Run from `Group_Project/FTP` to use the ignored project-local `app-data/`.

```text
app-data/
|-- app.db              SQLite owner, session digests, devices, job history
|-- app.db-wal/shm      SQLite runtime journal files, when present
|-- shared/             Built-in demo FTP root (only with --demo)
`-- transfers/
    `-- <job-id>/       Source staging or completed local download
```

The directory is created with owner-only permissions when newly created;
the database is set to mode 0600. These permissions are not disk encryption.
Do not commit the directory. `.gitignore` covers its contents, including
database journals and transferred personal files.

Stop the app with Ctrl+C and restart the same command to reuse the database.
Saved profiles and terminal history persist. On startup, an unfinished job
from a previous process is marked **Interrupted** and its staging directory
is removed. Files are not automatically resumed. Graceful shutdown requests
cancellation, waits for workers, closes sockets, and stops the demo server.

Custom settings are available without modifying source:

```bash
python3 -m ftp_lab.webapp --demo --port 8085 --data-dir app-data
```

Without `--demo`, Relay manages external devices only. If a database already
contains the demo profile, it is not a substitute for a running demo server.
Keep the launch mode consistent with the workspace you intend to use.

For VS Code, open [FTP.code-workspace](../FTP.code-workspace), start the
Python service in its terminal, and run **FTP: Open in VS Code browser**
from Run and Debug. This launch entry opens the page; it does not start a
second server.

## 3. Architecture and source reading order

```text
index.html + styles.css + app.js
        | same-origin HTTP, cookie and CSRF token
        v
AppServer / AppHandler              server.py
        | route, validate, authenticate
        v
AppService                          service.py
        |                         |
        v                         v
Database                       two background workers
database.py                       |
        |                         +--> FTPClient --> FTP device
        v                         +--> run_benchmark
SQLite app.db                     +--> disk transfer staging
```

Read the modules in this order:

| Source | Main responsibilities |
| --- | --- |
| [database.py](../ftp_lab/webapp/database.py) | Schema, transactions, password verification, sessions, profile/history persistence |
| [service.py](../ftp_lab/webapp/service.py) | Validation, memory-only credentials, job lifecycle, progress, cancellation, file cleanup |
| [server.py](../ftp_lab/webapp/server.py) | HTTP routes, request protections, cookies, streaming request/response bodies |
| [app.js](../ftp_lab/webapp/static/app.js) | API calls, views, source selection, polling, formatting and SVG graphs |
| [index.html](../ftp_lab/webapp/static/index.html) | Semantic page structure, forms, dialogs and accessible labels |
| [styles.css](../ftp_lab/webapp/static/styles.css) | Layout, colors, focus/disabled states and responsive rules |
| [benchmark.py](../ftp_lab/benchmark.py) | Measured original/gzip transfers, content verification and report export |

### 3.1 SQLite and local authentication

SQLite is an embedded database file: no database daemon is required.
`Database.connection()` opens a connection for an operation, enables
foreign keys, commits successful changes, rolls back failures, and closes
the connection. WAL mode supports short concurrent reads and writes.
Parameterized SQL binds user input as data instead of constructing SQL
statements from form values.

The schema has four tables:

| Table | Stored fields and role |
| --- | --- |
| users | Username, random salt, scrypt verifier, creation time |
| sessions | SHA-256 digest of a random session token, owner, expiry |
| devices | Owner, name, host, port, FTP username, demo flag, last connection time |
| jobs | Job ID, owner, serialized job state/result |

`setup()` uses an immediate transaction to ensure only the first owner can
be created. `password_hash()` uses scrypt with N=16384, r=8, p=1 and a
random 16-byte salt. A verifier can check a password without storing the
original password. `login()` compares the derived verifier with
`hmac.compare_digest`; invalid users still incur the scrypt calculation.

Successful authentication creates a random token. The browser receives
the token as an HttpOnly, SameSite=Strict cookie with an eight-hour expiry;
SQLite receives only its hash. `/api/session` exposes the signed-in user
and a derived CSRF token to the frontend. A session token is a bearer
credential even though its database representation is hashed.

### 3.2 HTTP request protections

The HTTP server defaults to `0.0.0.0` for LAN access. `--host 127.0.0.1`
restricts it to this computer. The allowed Host authority is the actual
destination IP/port of the accepted TCP socket, with `localhost` additionally
accepted on loopback. Arbitrary hostnames, forged addresses, duplicate Host
headers, and forwarded-header claims do not grant access. Mutations require
an Origin matching the exact requested authority and `X-FTP-App: 1`.
Authenticated mutations also require the session's `X-CSRF-Token`.
Cross-origin pages cannot simply submit forms to control a signed-in app.

Static routes explicitly allow only the HTML, CSS, and JavaScript files.
Transfer attachments require authentication and owner-scoped job lookup.
The server sends no-store caching, no-referrer, nosniff, and a restrictive
same-origin Content Security Policy. Authentication attempts are rate
limited. The frontend inserts untrusted filenames with `textContent`.

Initial owner setup is allowed only when both the peer and destination
are loopback addresses. Other devices can sign in after setup; they cannot
claim a fresh workspace. A fresh CLI launch bound only to a specific LAN
IP stops with instructions to bootstrap the owner on loopback first.

This is a trusted-LAN teaching application. The HTTP channel is plaintext,
and external FTP credentials/file contents travel in plaintext FTP.
Keep FTP servers and lab credentials on a trusted network. Local account
authentication does not add TLS to FTP or protect against another process
running as the same OS user. Public hosting, HTTPS termination, multi-user
administration, and password recovery are outside this implementation.

### 3.3 Job state and bounded background work

`ThreadPoolExecutor(max_workers=2)` limits simultaneous transfers. A workspace
can have at most eight nonterminal jobs. Upload/benchmark sources must have
an exact declared size no larger than 8 GiB. Raw PUT bodies are streamed
in bounded chunks to disk; files are not encoded into JSON or loaded whole
into memory. Requests have timeouts and metadata JSON has a 16 KiB limit.

```text
upload / benchmark: awaiting_upload -> preparing source -> queued
download:                                                  queued
                                                             |
                                                          running
                                                       /     |     \
                                               completed   failed  cancelled
```

The `status` field is machine-readable; `phase` explains current work, such
as preparing, connecting, compressing, receiving, or unpacking. The source
request atomically claims a waiting job, preventing two requests from
writing the same source. Queue admission and cancellation are coordinated
with an RLock, worker futures, and per-job cancellation events.

The FTP client accepts a progress callback receiving each block's byte
count. It calls the download callback before writing an incoming block,
so the service can reject a server that exceeds its reported size. After
retrieval, the staged file must also match that size before publication;
a file that shrank between SIZE and RETR fails with a retry message. The
benchmark also caps retrieval to the known uploaded payload and bounds
decompressed verification output to the original source size.

For cancelled uploads, the client resets the data connection instead of
presenting an ordinary clean EOF. The shipped server then discards its
staged upload. Ordinary FTP has no universal rollback guarantee for every
external server; cancellation may leave remote effects there.

This design bounds individual work, not total historical disk use.
Completed downloads can accumulate, and simultaneous compression/retrieval
uses additional disk copies. Keep enough free space and remove unwanted
local history/downloads through the app. Directory listings display at
most 500 entries and the history query returns the latest 100 jobs.

### 3.4 Frontend state and progress

The browser sends same-origin requests with session credentials. Upload
source staging uses XMLHttpRequest to receive upload progress events.
The app polls job state to show FTP progress, average payload speed, and an
estimate of remaining time. The estimate is not available before enough
bytes have moved and can vary as throughput changes.

Progress is phase-specific. Compressed transfers report compressed FTP
payload bytes rather than pretending to have transmitted original bytes.
A completion reply is required before the terminal success state is shown.
Graph samples describe payload throughput; they do not count Ethernet,
IP, TCP, or FTP control overhead.

Each sign-in/sign-out advances a frontend session generation. Async
responses from an older generation are discarded, including responses
whose JSON parsing finishes after sign-out. Poll loops stop and selected
File references are cleared so stale data cannot restore the prior UI.

## 4. Compression comparisons

Choose a file in Transfers and select **Compare compression**. Relay runs
the same source twice: once unchanged and once gzip-compressed at level 6.
Each version is uploaded under a unique benchmark name, downloaded again,
and checked against the original size and SHA-256 digest after optional
decompression. Temporary remote benchmark files are removed after use;
a cleanup failure is reported by the engine rather than silently claimed
as successful cleanup.

Performance shows payload size reduction, extra or saved preparation-plus-
upload time, verified content, a throughput graph, a size comparison, and
a measured data table. **Download results .csv** exports the underlying
run data. See the [benchmark report](../benchmarks/README.md) for precise
timing boundaries and large-file measurements.

Gzip may save very little on MP3/MP4 because these formats already compress
their contents. Compression can also take longer than the transfer time
it saves, especially on loopback. Relay displays the measured result in
either direction.

## 5. API map

JSON errors use `{ "error": "message" }`. Mutations use the Origin,
application header, and authenticated CSRF header described above.

| Method and path | Purpose |
| --- | --- |
| GET /api/session | Setup requirement/permission (`setup_allowed`), current user, CSRF token |
| POST /api/setup, /api/login | Authenticate with username/password; set session cookie |
| POST /api/logout | Revoke session and clear its device credentials |
| GET, POST /api/devices | List profiles or save name/host/port/username |
| POST /api/devices/{id}/connect | Check FTP login and retain password in session memory |
| DELETE /api/devices/{id} | Remove an external device profile |
| GET /api/devices/{id}/files?path=/ | List remote folder names, sizes, and directory flags |
| GET, POST /api/jobs | List recent jobs or create upload/download/benchmark work |
| PUT /api/jobs/{id}/source | Stream exact-size raw upload/benchmark source |
| GET /api/jobs/{id} | Current job state |
| POST /api/jobs/{id}/cancel | Request cancellation |
| DELETE /api/jobs/{id} | Remove terminal record/local artifacts |
| GET /api/jobs/{id}/download | Save completed download attachment |
| GET /api/jobs/{id}/report.csv | Export completed compression comparison |

The full JSON field contract is recorded in
[LOCAL_APP_PLAN.md](LOCAL_APP_PLAN.md#backendfrontend-contract).

## 6. Verification and troubleshooting

On 2026-11-16, the LAN extension passed **69 Python tests and 6 Node.js tests**,
with ResourceWarning treated as an error for Python. Ruff and Pyright
reported no errors. This includes auth/session persistence, Origin/CSRF,
raw streaming, job ownership, queued cancellation, duplicate source claims,
gzip round trips, invalid unpacked names, oversized payload rejection,
benchmark verification, and stale frontend response protection.

The internal browser was also exercised against an isolated local test
workspace: sign-in, saving and connecting an additional FTP device,
128 MiB WAV upload with visible progress/completion, download/Save file,
compression comparison with verified graphs, and sign-out/sign-in. The
saved profile and history remained, while its external FTP password had
to be entered again. No browser console errors/warnings were observed.
The user's normal workspace was left at first-run setup.

The subsequent LAN check exercised the real host IPv4 interface by setting
`FTP_TEST_LAN_HOST` for the test suite. Independent HTTP sessions shared
profiles and a byte-exact upload/download, retained separate device
credentials, and signed out independently. Seven network tests cover
Host/Origin checks, concurrent initial device listing, setup protection,
and the specific-address bootstrap requirement. The real-interface test
is opt-in and skips if its environment variable is absent. A physical
second computer/phone is a separate verification step.

The later gzip-display regression update passed **70 Python and 8 frontend
tests**. Browser verification confirmed the completed card uses the saved
output filename and **Save unpacked file**, and the saved bytes match the
original source. Corrupt gzip inputs produce a terminal error instead of
leaving a job stuck while unpacking.

| Symptom | Action |
| --- | --- |
| Browser cannot connect | Start the app and use the printed URL/port |
| Address already in use | Stop the old app or choose another `--port` |
| Missing Python module | Start from `Group_Project/FTP` using `python3 -m ftp_lab.webapp` |
| Profile is saved but not connected | Enter that FTP server's password again |
| File upload preparation finished but FTP is still running | Follow the phase label; the two legs are separate |
| FTP operation times out | Check device reachability, login, and passive data-port access |
| Transfer interrupted after restart | Reconnect and retry; partial work is not resumed |
| Comparison fails on a read-only server | Use a folder allowing STOR, RETR, and DELE |
| Compression makes a transfer slower | Use the uncompressed option for that file/network |
| Download no longer available | Its local output was removed; download it again from the device |

The automated tests use dynamically assigned loopback ports and temporary
directories under the FTP component's `build/tmp/`. Generated stress media
is placed in `benchmarks/generated-media/`; only small benchmark reports
are committed.
