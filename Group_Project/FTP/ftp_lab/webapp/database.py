"""SQLite persistence. Passwords/session cookies are represented only by hashes."""

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now() -> str:
    """Return the current UTC timestamp formatted as an ISO 8601 string.

    What it does:
        Provides standard timestamps for database creation and audit columns.

    How it does:
        Calls datetime.now(UTC).isoformat().
    """
    return datetime.now(UTC).isoformat()


def token_hash(token: str) -> str:
    """Compute the SHA-256 hexadecimal digest of an opaque session token.

    What it does:
        Hashes session tokens so plaintext cookies are never stored in the database.

    How it does:
        Encodes token to UTF-8 and returns hashlib.sha256(wire).hexdigest().
    """
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str) -> str:
    """Derive a CSRF token deterministically from a session token.

    What it does:
        Generates an origin-bound CSRF token tied cryptographically to the session cookie.

    How it does:
        Prepends 'ftp-app-csrf:' to token and hashes it using SHA-256.
    """
    return hashlib.sha256(('ftp-app-csrf:' + token).encode()).hexdigest()


def password_hash(password: str, salt: bytes) -> bytes:
    """Derive a cryptographically strong scrypt password verifier.

    What it does:
        Hashes user passwords with salt using the scrypt key derivation function.

    How it does:
        Invokes hashlib.scrypt with N=16384, r=8, p=1, and maxmem=64 MiB.
    """
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, maxmem=64 * 1024 * 1024)


# SQLite files are created per workspace; no IDE data source is required.
# noinspection SqlNoDataSourceInspection
class Database:
    """SQLite persistence layer for users, sessions, saved devices, and transfer jobs."""

    def __init__(self, path: Path):
        """Initialize the SQLite database with write-ahead logging and schemas.

        What it does:
            Creates or connects to the application database, sets permissions, and creates tables.

        How it does:
            1. Ensures the parent directory exists with 0700 permissions.
            2. Enables WAL mode (PRAGMA journal_mode=WAL).
            3. Creates users, sessions, devices, and jobs tables if they do not exist.
            4. Restricts file permissions on the database file to 0600.
        """
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    salt BLOB NOT NULL, verifier BLOB NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL,
                    username TEXT NOT NULL, local INTEGER NOT NULL DEFAULT 0,
                    last_connected TEXT);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    data TEXT NOT NULL);
            ''')
        path.chmod(0o600)

    @contextmanager
    def connection(self):
        """Context manager providing an auto-committing SQLite connection.

        What it does:
            Opens a database connection with foreign key constraints, committing on success
            and rolling back on exceptions.

        How it does:
            Connects via sqlite3.connect(timeout=10), sets sqlite3.Row row_factory, enables
            foreign keys, yields db, commits transactions, rolls back on error, and closes in
                finally.
        """
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def setup_required(self) -> bool:
        """Check whether initial owner account registration is required.

        What it does:
            Determines if the application lacks an owner account.

        How it does:
            Executes SELECT COUNT(*) FROM users and returns True if count == 0.
        """
        with self.connection() as db:
            return db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0

    def setup(self, username: str, password: str) -> dict[str, Any]:
        """Register the primary owner account during initial setup.

        What it does:
            Validates and registers the sole administrative owner account with scrypt salt and
                verifier.

        How it does:
            1. Validates username (2-64 chars) and password (8-256 chars).
            2. Generates a random 16-byte salt (secrets.token_bytes).
            3. Derives the scrypt password verifier.
            4. In an immediate transaction, ensures no user exists, inserts user record,
               and returns a dictionary with id and username.
        """
        username = username.strip()
        if not 2 <= len(username) <= 64 or any(ord(c) < 32 for c in username):
            raise ValueError('Choose a username between 2 and 64 characters')
        if not 8 <= len(password) <= 256:
            raise ValueError('Use a password between 8 and 256 characters')
        salt = secrets.token_bytes(16)
        verifier = password_hash(password, salt)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
                raise ValueError('This app already has an owner. Please sign in.')
            cursor = db.execute(
                'INSERT INTO users(username,salt,verifier,created) VALUES(?,?,?,?)',
                (username, salt, verifier, now()),
            )
            return {'id': cursor.lastrowid, 'username': username}

    def login(self, username: str, password: str) -> dict[str, Any] | None:
        """Authenticate user credentials against the database.

        What it does:
            Verifies user credentials using salted scrypt hashing and constant-time comparison.

        How it does:
            1. Queries the user record by trimmed username.
            2. Uses the user's salt (or dummy 16 zero-bytes if user not found to prevent timing
                clues).
            3. Derives scrypt hash of the candidate password.
            4. Verifies hash using hmac.compare_digest against the stored verifier.
            5. Returns user dict on success or None on failure.
        """
        with self.connection() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username.strip(),)).fetchone()
        salt = row['salt'] if row else bytes(16)
        calculated = password_hash(password, salt)
        if row is None or not hmac.compare_digest(calculated, row['verifier']):
            return None
        return {'id': row['id'], 'username': row['username']}

    def create_session(self, user_id: int, *, ttl: float = 8 * 3600) -> str:
        """Create a new session cookie and store its hash in the database.

        What it does:
            Generates an unguessable session token, prunes expired records, and saves the session.

        How it does:
            1. Generates 32 URL-safe random bytes via secrets.token_urlsafe(32).
            2. Prunes old expired sessions (DELETE FROM sessions WHERE expires <= now).
            3. Inserts (token_hash(token), user_id, time.time() + ttl).
            4. Returns the plaintext token for cookie delivery.
        """
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
            db.execute(
                'INSERT INTO sessions VALUES(?,?,?)',
                (token_hash(token), user_id, time.time() + ttl),
            )
        return token

    def session(self, token: str) -> dict[str, Any] | None:
        """Validate a session token and fetch the associated user.

        What it does:
            Validates token expiration and returns user information.

        How it does:
            Hashes token with token_hash(), queries active sessions table joining users,
            and returns dict of user attributes or None if expired/not found.
        """
        if not token:
            return None
        with self.connection() as db:
            row = db.execute(
                '''SELECT users.id, users.username FROM sessions JOIN users
                ON users.id=sessions.user_id WHERE digest=? AND expires>?''',
                (token_hash(token), time.time()),
            ).fetchone()
            return dict(row) if row else None

    def delete_session(self, token: str):
        """Invalidate and remove a session from the database.

        What it does:
            Deletes an active session on user logout.

        How it does:
            Executes DELETE FROM sessions WHERE digest = token_hash(token).
        """
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (token_hash(token),))

    def devices(self, owner: int) -> list[dict[str, Any]]:
        """Retrieve all saved FTP device endpoints belonging to a user.

        What it does:
            Returns a list of device configurations configured by the owner.

        How it does:
            Executes SELECT * FROM devices WHERE user_id=? ORDER BY id, returning dictionaries.
        """
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute('SELECT * FROM devices WHERE user_id=? ORDER BY id', (owner,))
            ]

    def device(self, owner: int, device_id: int) -> dict[str, Any] | None:
        """Retrieve a specific device configuration by ID.

        What it does:
            Looks up a single device owned by the user.

        How it does:
            Queries devices table by user_id and device_id; returns dict or None.
        """
        with self.connection() as db:
            row = db.execute(
                'SELECT * FROM devices WHERE user_id=? AND id=?', (owner, device_id)
            ).fetchone()
            return dict(row) if row else None

    def add_device(self, owner: int, device: dict[str, Any]) -> dict[str, Any]:
        """Save a new FTP device endpoint.

        What it does:
            Persists a remote FTP server target configuration.

        How it does:
            Inserts name, host, port, username, and local flag into devices, then retrieves
            and returns the saved record.
        """
        with self.connection() as db:
            result = db.execute(
                'INSERT INTO devices(user_id,name,host,port,username,local) VALUES(?,?,?,?,?,?)',
                (
                    owner,
                    device['name'],
                    device['host'],
                    device['port'],
                    device['username'],
                    device.get('local', 0),
                ),
            )
            device_id = result.lastrowid
            if device_id is None:
                raise RuntimeError('Device insert did not return an ID')
        saved = self.device(owner, device_id)
        if saved is None:
            raise RuntimeError('Device could not be saved')
        return saved

    def touch_device(self, owner: int, device_id: int):
        """Update the last_connected timestamp of a device.

        What it does:
            Records the timestamp of the latest successful connection to a device.

        How it does:
            Executes UPDATE devices SET last_connected = now() WHERE user_id=? AND id=?.
        """
        with self.connection() as db:
            db.execute(
                'UPDATE devices SET last_connected=? WHERE user_id=? AND id=?',
                (now(), owner, device_id),
            )

    def local_device(self, owner: int, address: tuple[str, int]) -> dict[str, Any]:
        """Register or update the local host computer's FTP service endpoint.

        What it does:
            Finds or creates a 'Host computer' device entry pointing to the local loopback server.

        How it does:
            Searches user's devices for local == 1. If found, updates host and port;
            if not found, creates and saves a new device marked local=1.
        """
        existing = next((d for d in self.devices(owner) if d['local']), None)
        if existing:
            with self.connection() as db:
                db.execute(
                    '''UPDATE devices SET host=?,port=?,
                    name=CASE WHEN name='This computer' THEN 'Host computer' ELSE name END
                    WHERE id=? AND user_id=?''',
                    (address[0], address[1], existing['id'], owner),
                )
            saved = self.device(owner, existing['id'])
            if saved is None:
                raise RuntimeError('Local device could not be saved')
            return saved
        return self.add_device(
            owner,
            {
                'name': 'Host computer',
                'host': address[0],
                'port': address[1],
                'username': 'local',
                'local': 1,
            },
        )

    def delete_device(self, owner: int, device_id: int):
        """Remove a saved device configuration.

        What it does:
            Deletes a device from the database by ID.

        How it does:
            Executes DELETE FROM devices WHERE user_id=? AND id=?.
        """
        with self.connection() as db:
            db.execute('DELETE FROM devices WHERE user_id=? AND id=?', (owner, device_id))

    def save_job(self, owner: int, job: dict[str, Any]):
        """Persist or update a transfer job record.

        What it does:
            Saves job state JSON into the database using upsert semantics.

        How it does:
            Serializes job dict using json.dumps(allow_nan=False) and executes INSERT INTO jobs
            ... ON CONFLICT(id) DO UPDATE SET data=excluded.data.
        """
        with self.connection() as db:
            db.execute(
                'INSERT INTO jobs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                (job['id'], owner, json.dumps(job, allow_nan=False)),
            )

    def job(self, owner: int, job_id: str) -> dict[str, Any] | None:
        """Fetch a specific transfer job by ID.

        What it does:
            Retrieves and deserializes a single job record.

        How it does:
            Queries jobs table by user_id and id, deserializing the JSON string if found.
        """
        with self.connection() as db:
            row = db.execute(
                'SELECT data FROM jobs WHERE user_id=? AND id=?', (owner, job_id)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def jobs(self, owner: int) -> list[dict[str, Any]]:
        """Fetch up to 100 recent transfer jobs for an owner.

        What it does:
            Returns a list of recent jobs sorted by creation order descending.

        How it does:
            Queries jobs table ordered by rowid DESC LIMIT 100, deserializing each JSON payload.
        """
        with self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    'SELECT data FROM jobs WHERE user_id=? ORDER BY rowid DESC LIMIT 100', (owner,)
                )
            ]

    def all_jobs(self) -> list[tuple[int, dict[str, Any]]]:
        """Fetch all transfer jobs across all users for background worker restoration.

        What it does:
            Returns (user_id, job_dict) pairs for queue resumption on server startup.

        How it does:
            Executes SELECT user_id, data FROM jobs and deserializes each job's JSON payload.
        """
        with self.connection() as db:
            return [
                (row['user_id'], json.loads(row['data']))
                for row in db.execute('SELECT user_id,data FROM jobs')
            ]

    def delete_job(self, owner: int, job_id: str):
        """Delete a transfer job record.

        What it does:
            Removes a completed or canceled job from the database.

        How it does:
            Executes DELETE FROM jobs WHERE user_id=? AND id=?.
        """
        with self.connection() as db:
            db.execute('DELETE FROM jobs WHERE user_id=? AND id=?', (owner, job_id))
