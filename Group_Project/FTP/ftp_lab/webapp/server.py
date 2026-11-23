"""Authenticated LAN HTTP app with destination-bound Host/Origin checks."""

import argparse
import hmac
import json
import signal
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from ..client import FTPError
from .database import csrf_token
from .service import AppService, Cancelled, NotFound

STATIC = Path(__file__).parent / 'static'


class AppServer(ThreadingHTTPServer):
    """Multi-threaded HTTP server providing web UI and JSON APIs for FTP operations."""

    daemon_threads = True

    def __init__(self, address, service: AppService):
        """Initialize the web application HTTP server.

        What it does:
            Prepares the HTTP server listening on the specified IPv4 endpoint and binds the
                AppService.

        How it does:
            Validates and normalizes address, initializes rate-limiting state (attempts dictionary)
            and authentication lock, and invokes ThreadingHTTPServer.__init__.
        """
        address = (str(IPv4Address(address[0])), address[1])
        self.service = service
        self.attempts: dict[str, list[float]] = {}
        self.auth_lock = threading.Lock()
        super().__init__(address, AppHandler)

    @property
    def address(self) -> tuple[str, int]:
        """Return the bound host and port.

        What it does:
            Provides the HTTP server's bound host IP string and port number.

        How it does:
            Extracts host and port from self.server_address.
        """
        host, port = self.server_address[:2]
        if not isinstance(port, int):
            raise TypeError('Expected a bound IPv4 port')
        return str(host), port


class AppHandler(BaseHTTPRequestHandler):
    """HTTP request handler implementing security headers, CSRF checks, and API routing."""

    app: AppServer

    server_version = 'LocalFTP/1.0'

    def setup(self):
        """Prepare handler references and set connection timeout.

        What it does:
            Stores a typed reference to the AppServer and sets socket timeout.

        How it does:
            Verifies self.server is an AppServer, sets self.app, and sets connection timeout to 30s.
        """
        super().setup()
        if not isinstance(self.server, AppServer):
            raise TypeError('App HTTP handler requires AppServer')
        self.app = self.server
        self.connection.settimeout(30)

    # Required override signature; logging remains silent to avoid leaking credentials.
    # noinspection PyShadowingBuiltins
    def log_message(self, format, *args):
        """Suppress default HTTP request logging to prevent credential exposure.

        What it does:
            Silences HTTP access logging.

        How it does:
            Returns immediately without writing logs, keeping filenames, tokens, and cookies
                private.
        """
        return

    def end_headers(self):
        """Inject strict security headers into every HTTP response.

        What it does:
            Adds defensive browser headers (CSP, nosniff, no-store, no-referrer) to responses.

        How it does:
            Emits Content-Security-Policy, X-Content-Type-Options, Cache-Control, and
                Referrer-Policy
            headers before invoking BaseHTTPRequestHandler.end_headers().
        """
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self'; style-src "
            "'self'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; object-src 'none'; base-uri "
            "'none'",
        )
        super().end_headers()

    def reply(self, status: int, value: dict[str, Any], *, cookie: str | None = None):
        """Send a JSON HTTP response.

        What it does:
            Encodes and transmits a JSON object with appropriate HTTP status and headers.

        How it does:
            Serializes value with json.dumps(allow_nan=False), sends headers (Content-Type,
                Content-Length,
            optional Set-Cookie), finishes headers, and writes encoded body to self.wfile.
        """
        body = json.dumps(value, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def session(self) -> tuple[str, dict[str, Any] | None]:
        """Extract and validate session cookie from request headers.

        What it does:
            Reads the 'ftp_session' cookie and queries the database for active session data.

        How it does:
            Parses Cookie header using SimpleCookie, extracts token, and calls
            self.app.service.db.session(token). Returns (token, user_or_none).
        """
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
        except ValueError:
            return '', None
        entry = cookies.get('ftp_session')
        token = entry.value if entry is not None else ''
        return token, self.app.service.db.session(token)

    def payload(self) -> dict[str, Any]:
        """Parse incoming JSON request body.

        What it does:
            Validates Content-Type and size, reading and decoding the JSON request body.

        How it does:
            Ensures Content-Type is 'application/json', reads up to 16 KiB based on Content-Length,
            parses JSON via json.loads(), and verifies it is a dictionary.
        """
        if self.headers.get_content_type() != 'application/json':
            raise ValueError('Expected a JSON request')
        length = self.content_length()
        if length > 16384:
            raise ValueError('Request is too large')
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise TypeError('Expected an object')
        return data

    def content_length(self) -> int:
        """Parse and validate the Content-Length header.

        What it does:
            Extracts the integer request body length.

        How it does:
            Rejects chunked Transfer-Encoding, reads Content-Length string, validates digits,
            and returns integer value.
        """
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Chunked request bodies are not supported')
        value = self.headers.get('Content-Length', '0')
        if not value.isascii() or not value.isdigit():
            raise ValueError('Invalid content length')
        return int(value)

    # BaseHTTPRequestHandler dispatches to this exact method name.
    # noinspection PyPep8Naming
    def do_GET(self):  # pylint: disable=invalid-name
        """Handle incoming HTTP GET requests.

        What it does:
            Dispatches GET requests to the centralized application routing handler.

        How it does:
            Invokes self.route() to authenticate, parse query parameters, and serve static assets or
                API endpoints.
        """
        self.route()

    # BaseHTTPRequestHandler dispatches to this exact method name.
    # noinspection PyPep8Naming
    def do_POST(self):  # pylint: disable=invalid-name
        """Handle incoming HTTP POST requests.

        What it does:
            Dispatches POST requests to the centralized application routing handler.

        How it does:
            Invokes self.route() to validate CSRF tokens, parse payloads, and execute state-mutating
                actions.
        """
        self.route()

    # BaseHTTPRequestHandler dispatches to this exact method name.
    # noinspection PyPep8Naming
    def do_PUT(self):  # pylint: disable=invalid-name
        """Handle incoming HTTP PUT requests.

        What it does:
            Dispatches PUT requests to the centralized application routing handler.

        How it does:
            Invokes self.route() to validate CSRF tokens, verify payload streams, and update
                resources.
        """
        self.route()

    # BaseHTTPRequestHandler dispatches to this exact method name.
    # noinspection PyPep8Naming
    def do_DELETE(self):  # pylint: disable=invalid-name
        """Handle incoming HTTP DELETE requests.

        What it does:
            Dispatches DELETE requests to the centralized application routing handler.

        How it does:
            Invokes self.route() to authenticate requests, validate CSRF protection, and delete
                resources.
        """
        self.route()

    def route(self):
        """Execute request routing with structured error handling.

        What it does:
            Executes self.dispatch() and translates exceptions into standard HTTP error responses.

        How it does:
            Catches NotFound (404), Cancelled (409), ValueError/KeyError/FTPError (400),
            and network timeout/disconnect errors (502).
        """
        try:
            self.dispatch()
        except NotFound as exc:
            self.reply(404, {'error': str(exc)})
        except Cancelled:
            self.reply(409, {'error': 'Transfer cancelled'})
        except (ValueError, KeyError, TypeError, FTPError) as exc:
            self.reply(400, {'error': str(exc)[:500]})
        except (OSError, EOFError):
            try:
                self.reply(
                    502,
                    {'error': 'The connection ended or timed out. Please reconnect and try again.'},
                )
            except OSError:
                pass

    # Plain HTTP is intentional for this trusted LAN lab.
    # noinspection HttpUrlsUsage
    def dispatch(self):
        """Validate request headers and dispatch to specific API endpoint handlers.

        What it does:
            Validates Host, Origin, and CSRF headers, routing requests to authentication,
            device management, file browsing, and transfer job endpoints.

        How it does:
            1. Validates Host header against destination socket IP and port to prevent DNS
                rebinding.
            2. For mutating methods (POST/PUT/DELETE): validates X-FTP-App header, Origin header,
               and X-CSRF-Token matching csrf_token(token).
            3. Routes GET /api/session and static files (index.html, styles.css, app.js).
            4. Routes POST /api/setup and /api/login, enforcing 10 attempts/min rate limit.
            5. Dispatches authenticated device and job endpoints to AppService methods.
        """
        # Keep HTTP method, authentication, and ownership checks beside each route.
        # pylint: disable=too-many-locals
        # pylint: disable=too-many-return-statements
        # pylint: disable=too-many-branches
        # pylint: disable=too-many-statements
        host = self.headers.get('Host', '')
        port = self.app.address[1]
        destination = IPv4Address(self.connection.getsockname()[0])
        allowed = {f'{destination}:{port}'}
        if port == 80:
            allowed.add(str(destination))
        if destination.is_loopback:
            allowed.add(f'localhost:{port}')
            if port == 80:
                allowed.add('localhost')
        if host not in allowed or len(self.headers.get_all('Host', [])) != 1:
            self.reply(403, {'error': 'Use the host computer\'s IP address and the app port'})
            return
        setup_allowed = destination.is_loopback and IPv4Address(self.client_address[0]).is_loopback
        method = self.command
        route = urlsplit(self.path)
        path = route.path
        token, user = self.session()
        if method != 'GET':
            if (
                self.headers.get('X-FTP-App') != '1'
                or self.headers.get('Origin') != f'http://{host}'
            ):
                self.reply(403, {'error': 'Invalid request origin'})
                return
            if user is not None and not hmac.compare_digest(
                self.headers.get('X-CSRF-Token', ''), csrf_token(token)
            ):
                self.reply(403, {'error': 'Your session changed. Refresh the page and try again.'})
                return
        service = self.app.service
        if method == 'GET' and path == '/api/session':
            self.reply(
                200,
                {
                    'setup_required': service.db.setup_required(),
                    'user': user,
                    'setup_allowed': setup_allowed,
                    'csrf': csrf_token(token) if user else '',
                },
            )
            return
        if method == 'GET' and path in ('/', '/static/styles.css', '/static/app.js'):
            name = {
                '/': 'index.html',
                '/static/styles.css': 'styles.css',
                '/static/app.js': 'app.js',
            }[path]
            file = STATIC / name
            if not file.is_file():
                raise NotFound('Page not found')
            body = file.read_bytes()
            self.send_response(200)
            self.send_header(
                'Content-Type',
                {'html': 'text/html; charset=utf-8', 'css': 'text/css', 'js': 'text/javascript'}[
                    name.rsplit('.', 1)[1]
                ],
            )
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if method == 'POST' and path in ('/api/setup', '/api/login'):
            if path == '/api/setup' and not setup_allowed:
                self.reply(
                    403,
                    {
                        'error': 'Create the workspace account on the host computer first using '
                        f'http://127.0.0.1:{port}'
                    },
                )
                return
            data = self.payload()
            username, password = data.get('username'), data.get('password')
            if (
                not isinstance(username, str)
                or not isinstance(password, str)
                or len(password) > 256
            ):
                raise ValueError('Enter a username and password')
            with self.app.auth_lock:
                attempts = [
                    t
                    for t in self.app.attempts.get(self.client_address[0], [])
                    if t > time.monotonic() - 60
                ]
                if len(attempts) >= 10:
                    self.reply(429, {'error': 'Too many attempts. Wait a minute and try again.'})
                    return
                attempts.append(time.monotonic())
                self.app.attempts[self.client_address[0]] = attempts
            user = (
                service.db.setup(username, password)
                if path == '/api/setup'
                else service.db.login(username, password)
            )
            if user is None:
                self.reply(401, {'error': 'Username or password did not match'})
                return
            new_token = service.db.create_session(user['id'])
            self.reply(
                200,
                {'setup_required': False, 'user': user, 'csrf': csrf_token(new_token)},
                cookie=f'ftp_session={new_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800',
            )
            return
        if user is None:
            self.reply(
                401 if path.startswith('/api/') else 404,
                {'error': 'Please sign in' if path.startswith('/api/') else 'Not found'},
            )
            return
        owner = user['id']
        if method == 'POST' and path == '/api/logout':
            service.logout(token)
            service.db.delete_session(token)
            self.reply(
                200,
                {'ok': True},
                cookie='ftp_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0',
            )
        elif path == '/api/devices' and method == 'GET':
            self.reply(200, {'devices': service.devices(owner, token)})
        elif path == '/api/devices' and method == 'POST':
            self.reply(201, {'device': service.add_device(owner, self.payload())})
        elif path == '/api/jobs' and method == 'GET':
            self.reply(200, {'jobs': service.jobs(owner)})
        elif path == '/api/jobs' and method == 'POST':
            self.reply(201, {'job': service.create_job(owner, token, self.payload())})
        elif path.startswith('/api/devices/'):
            parts = path.split('/')
            try:
                device_id = int(parts[3])
            except (ValueError, IndexError) as exc:
                raise NotFound('Device not found') from exc
            if len(parts) == 5 and parts[4] == 'connect' and method == 'POST':
                self.reply(
                    200,
                    {
                        'device': service.connect_device(
                            owner, token, device_id, self.payload().get('password', '')
                        )
                    },
                )
            elif len(parts) == 5 and parts[4] == 'files' and method == 'GET':
                folder = parse_qs(route.query).get('path', ['/'])[0]
                self.reply(200, service.files(owner, token, device_id, folder))
            elif len(parts) == 4 and method == 'DELETE':
                service.delete_device(owner, device_id)
                self.reply(200, {'ok': True})
            else:
                raise NotFound('Not found')
        elif path.startswith('/api/jobs/'):
            parts = path.split('/')
            job_id = parts[3]
            if len(parts) == 4 and method == 'GET':
                self.reply(200, {'job': service.job(owner, job_id)})
            elif len(parts) == 4 and method == 'DELETE':
                service.delete_job(owner, job_id)
                self.reply(200, {'ok': True})
            elif len(parts) == 5 and parts[4] == 'source' and method == 'PUT':
                self.reply(
                    200,
                    {
                        'job': service.receive_source(
                            owner, token, job_id, self.rfile, self.content_length()
                        )
                    },
                )
            elif len(parts) == 5 and parts[4] == 'cancel' and method == 'POST':
                self.reply(200, {'job': service.cancel_job(owner, job_id)})
            elif len(parts) == 5 and parts[4] == 'download' and method == 'GET':
                file, name = service.attachment(owner, job_id)
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(file.stat().st_size))
                self.send_header(
                    'Content-Disposition', "attachment; filename*=UTF-8''" + quote(name, safe='')
                )
                self.end_headers()
                with file.open('rb') as source:
                    while block := source.read(1024 * 1024):
                        self.wfile.write(block)
            elif len(parts) == 5 and parts[4] == 'report.csv' and method == 'GET':
                data = service.report(owner, job_id).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Disposition', 'attachment; filename="ftp-comparison.csv"')
                self.end_headers()
                self.wfile.write(data)
            else:
                raise NotFound('Not found')
        else:
            raise NotFound('Not found')


# Plain HTTP is intentional for this trusted LAN lab.
# noinspection HttpUrlsUsage
def main(argv=None):
    """Run the web application server CLI.

    What it does:
        Configures data storage, initializes AppService, binds HTTP server, and serves clients.

    How it does:
        1. Parses options: --host, --port, --data-dir, --demo.
        2. Ensures bootstrap setup constraints (LAN requires loopback bootstrap first).
        3. Instantiates AppServer and runs server.serve_forever() until interrupted.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='IPv4 bind address (default: all interfaces; use 127.0.0.1 for local only)',
    )
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--data-dir', type=Path, default=Path('app-data'))
    parser.add_argument(
        '--demo', action='store_true', help='Start an automatically connected local FTP device'
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535')
    try:
        host = str(IPv4Address(args.host))
    except ValueError:
        parser.error('--host must be an IPv4 bind address')
    service = AppService(args.data_dir, demo=args.demo)
    if host != '0.0.0.0' and not IPv4Address(host).is_loopback and service.db.setup_required():
        service.close()
        parser.error(
            'Create the owner account first: start with --host 127.0.0.1 using the same '
            '--data-dir and --port, complete setup there, then restart with the LAN address'
        )
    try:
        server = AppServer((host, args.port), service)
    except OSError:
        service.close()
        raise

    def interrupt(_signum, _frame):
        """Translate SIGTERM signal to KeyboardInterrupt for graceful shutdown.

        What it does:
            Translates OS termination signals into a standard Python KeyboardInterrupt exception.

        How it does:
            Raises KeyboardInterrupt to exit the main serving loop and trigger resource cleanup.
        """
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        with server:
            port = server.address[1]
            if host == '0.0.0.0':
                print(f'On this computer: http://127.0.0.1:{port}', flush=True)
                print(
                    f'Other devices: http://<this computer\'s LAN IPv4 address>:{port}', flush=True
                )
                print(
                    'Listening on all IPv4 interfaces. Use a trusted LAN; '
                    'HTTP and FTP are plaintext.',
                    flush=True,
                )
            else:
                print(f'Open http://{host}:{port}', flush=True)
            server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
