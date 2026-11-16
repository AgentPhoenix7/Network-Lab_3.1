# Access from multiple devices

## Start the shared browser service

On the computer hosting the project:

```bash
cd Group_Project/FTP
python3 -m ftp_lab.webapp --demo
```

The default bind address is `0.0.0.0` on TCP port 8080, which accepts IPv4
connections through the host's network interfaces. `0.0.0.0` is a listen
address, not the address to type into another device's browser.

1. On the host, open `http://127.0.0.1:8080` and create the workspace owner.
2. Find the host's Wi-Fi/Ethernet IPv4 address in network settings or with
   `ip -4 -brief address show`. Use the active LAN interface, not `lo`.
3. Connect a phone or another computer to the same trusted Wi-Fi/LAN.
4. Open `http://HOST_LAN_IP:8080` on that device. For example, a host at
   `192.168.1.20` would use `http://192.168.1.20:8080`.
5. Sign in with the workspace account. **Host computer** is the shared
   `app-data/shared/` folder on the Python host, not the phone's filesystem.

Browsers have separate cookies, CSRF tokens, and remembered external FTP
passwords. Saved device profiles, the shared FTP folder, and history belong
to the same workspace owner and are visible to every signed-in session.
Signing out cancels that session's jobs and revokes its credentials without
signing out the other browsers. This is one shared account, not a separate
account per device/person. Share its credentials only with intended users.

Upload in one browser, then refresh the host folder in another and download
the file there. Each browser offers its own file chooser and saves downloads
on the device running that browser. The host Python process performs the
actual FTP transfer; selecting a browser device does not automatically make
that device an FTP server. To connect an additional FTP server, use
**Add a device** with that server's address, port, and username.

## Bind options and account setup

```bash
# Trusted LAN access (the default)
python3 -m ftp_lab.webapp --demo --host 0.0.0.0 --port 8080

# Host-only access
python3 -m ftp_lab.webapp --demo --host 127.0.0.1 --port 8080
```

You can bind only the host's assigned LAN IPv4 address using `--host` after
the owner account exists. For a fresh workspace, first start on loopback
with the same `--data-dir` and port, create its account there, then restart
bound to the LAN address. The CLI reports this prerequisite if that specific
bind mode has no owner yet. Default wildcard mode already includes loopback
and needs no separate bootstrap restart.

Remote initial account creation is rejected. An unconfigured workspace
shows other devices **The host goes first**, with an instruction to create
the owner on the host and then select **Try again**. Use the host's IPv4
address to access the service. Arbitrary DNS hostnames and reverse proxies
are not configured by this version.

## Network requirements and troubleshooting

For the browser workflow, other devices need to reach **TCP 8080** on the
host (or the chosen `--port`). The internal demo FTP server remains on
loopback behind the browser service. It needs no incoming FTP ports from
the phone. If the host connects onward to an external FTP server, it still
needs the FTP control and negotiated data connections described in the
core README.

| Symptom | Check |
| --- | --- |
| Works on host but not phone | Both devices are on the same LAN, correct host IPv4/port, server binds 0.0.0.0 or LAN IP, host firewall allows that port from the LAN |
| Guest Wi-Fi cannot connect | Router client/AP isolation may block device-to-device traffic; use an appropriate shared lab network |
| Connection refused | Python service is running and the URL uses its printed port |
| Connection times out | Address changed, firewall rules, or Wi-Fi isolation; do not disable the whole firewall |
| Host/address error | Use the assigned destination IPv4 and correct port, without a proxy or guessed hostname |
| Setup says host goes first | Create the account at the loopback URL on the host, then retry from the device |
| A profile is saved but disconnected | Enter its FTP password in this browser; passwords are intentionally session-specific |

Host addresses can change after reconnecting to Wi-Fi. A DHCP reservation
can make an address stable if the network administrator wants that setup.
No firewall, router, port-forwarding, or public-sharing settings are changed
by the application. HTTP and FTP carry credentials and data in plaintext:
use a trusted lab LAN. Internet exposure needs a separate HTTPS/TLS and
deployment design.

## Direct FTP access without the browser

The standalone FTP server already supports IPv4 binding and concurrent
clients. From `Group_Project/FTP`, supply your lab password through
`FTP_PASSWORD` and bind the desired interface:

```bash
FTP_PASSWORD=choose-a-lab-password python3 -m ftp_lab.server \
  --host 0.0.0.0 --port 2121 --root server-data
```

On another computer, run the project client with `--host HOST_LAN_IP
--port 2121`, or connect a compatible FTP client using username `student`
and that lab password. This is a separately configured FTP account/root;
it does not reuse the browser workspace password or the demo's internal
random credential. Passive mode needs incoming FTP data connections at
the server in addition to TCP 2121. The current FTP implementation uses
ephemeral data ports; configuring a fixed passive range is outside scope.

## Verification record

The new regressions first failed because the old HTTP server refused
wildcard binding. The new suite exercises independent browser sessions,
shared profile/file upload-download, isolated external FTP passwords,
concurrent initial demo-profile creation, strict Host/Origin/CSRF handling,
remote owner-claim prevention, and specific-address owner bootstrap.

Run the normal suite with `make test`. To include the real LAN-interface
case, from the repository root set an IPv4 address actually assigned to
this host:

```bash
FTP_TEST_LAN_HOST=HOST_LAN_IP make -C Group_Project/FTP test
```

That case is skipped when the variable is absent. All other socket tests
remain self-contained and use dynamic ports. On 2026-11-16, all **69 Python
and 6 frontend tests** passed with the real-interface case enabled. Ruff
and Pyright were also checked. A separate internal-browser session opened
the actual host LAN URL, observed the remote setup guard, signed in after
local bootstrap, and completed a 128 MiB WAV upload without console warnings
or errors.

Connecting through the host's assigned LAN IP proves the service accepts
that destination and validates it correctly. It does not replace testing
a physical second computer or phone: external Wi-Fi isolation and firewall
behavior still depend on the user's network.

The full regression run also exposed a shutdown race in the FTP server:
locally closing a data socket could look like clean EOF and allow a staged
upload to replace the original file. A deterministic regression reproduced
that ordering. The server now checks its closing event before the staged
upload can commit, and the original file remains intact.

## Design and implementation plan

The user requested access beyond localhost on 2026-11-16. Extend the
existing Python browser app for a trusted IPv4 Wi-Fi/LAN. Retain one shared
workspace owner account with independent browser sessions, saved devices,
and shared transfer history. Keep ARP untouched and require no new packages.

The browser app is the entry point for phones and computers. Its internal
demo FTP server can stay on loopback: browser files travel over HTTP to
Python, which performs real FTP transfers to the shared host folder or
another saved FTP server. Raw FTP clients can separately use the existing
server's `--host` option.

The considered choices are a localhost-only browser behind an additional
proxy, or binding the existing HTTP server directly to the LAN. Direct
binding is the chosen implementation because it needs no additional
service and suits this trusted-network coursework. Public internet hosting,
TLS certificate provisioning, and separate accounts per person are outside
this change. The app and FTP traffic remain plaintext on a trusted LAN.

- [x] Add `--host`, defaulting the browser app to `0.0.0.0`, with
  `--host 127.0.0.1` available for local-only operation.
- [x] Validate Host against the accepted socket's actual destination IPv4
  address and port; permit localhost as a loopback alias. Require mutations'
  Origin to match the exact requested authority, preserving application and
  CSRF headers. Do not trust forwarded headers or arbitrary hostnames.
- [x] Allow initial account setup only through a loopback connection, and
  show other devices an instruction to complete setup on the host first.
- [x] Test wildcard binding, independent sessions sharing profiles/files,
  scoped sign-out, Host/Origin rejection, and remote setup prevention before
  implementing. Verify concurrent first access creates one demo profile.
- [x] Explain the host/shared-folder wording, LAN URL, firewall/network
  requirements, browser-versus-FTP ports, and remaining limitations.
- [x] Run the full tests, lint/type checks, and a real host LAN-interface
  transfer; inspect the page in the internal browser. Distinguish that check
  from a physical phone or second-computer test.
- [x] Commit and push the verified change to the existing app PR.

Implementation uses `AppServer`/`AppHandler` in `webapp/server.py`, the
existing SQLite/session service, and the current frontend. The regression
suite is extended in `tests/test_network_app.py`. Defaults and usage are
synchronized with the README and detailed app guide. No firewall rules or
router settings are changed automatically.
