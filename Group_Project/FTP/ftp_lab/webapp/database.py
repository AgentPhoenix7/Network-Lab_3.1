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


def now():
    return datetime.now(UTC).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str) -> str:
    return hashlib.sha256(('ftp-app-csrf:' + token).encode()).hexdigest()


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, maxmem=64 * 1024 * 1024)


class Database:
    def __init__(self, path: Path):
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

    def setup_required(self):
        with self.connection() as db:
            return db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0

    def setup(self, username: str, password: str):
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
            cursor = db.execute('INSERT INTO users(username,salt,verifier,created) VALUES(?,?,?,?)',
                                (username, salt, verifier, now()))
            return {'id': cursor.lastrowid, 'username': username}

    def login(self, username: str, password: str):
        with self.connection() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username.strip(),)).fetchone()
        salt = row['salt'] if row else bytes(16)
        calculated = password_hash(password, salt)
        if row is None or not hmac.compare_digest(calculated, row['verifier']):
            return None
        return {'id': row['id'], 'username': row['username']}

    def create_session(self, user_id, *, ttl=8 * 3600):
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (token_hash(token), user_id, time.time() + ttl))
        return token

    def session(self, token):
        if not token:
            return None
        with self.connection() as db:
            row = db.execute('''SELECT users.id, users.username FROM sessions JOIN users
                ON users.id=sessions.user_id WHERE digest=? AND expires>?''',
                             (token_hash(token), time.time())).fetchone()
            return dict(row) if row else None

    def delete_session(self, token):
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (token_hash(token),))

    def devices(self, owner):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM devices WHERE user_id=? ORDER BY id', (owner,))]

    def device(self, owner, device_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM devices WHERE user_id=? AND id=?', (owner, device_id)).fetchone()
            return dict(row) if row else None

    def add_device(self, owner, device):
        with self.connection() as db:
            result = db.execute('INSERT INTO devices(user_id,name,host,port,username,local) VALUES(?,?,?,?,?,?)',
                                (owner, device['name'], device['host'], device['port'], device['username'], device.get('local', 0)))
            device_id = result.lastrowid
        saved = self.device(owner, device_id)
        if saved is None:
            raise RuntimeError('Device could not be saved')
        return saved

    def touch_device(self, owner, device_id):
        with self.connection() as db:
            db.execute('UPDATE devices SET last_connected=? WHERE user_id=? AND id=?', (now(), owner, device_id))

    def local_device(self, owner, address):
        existing = next((d for d in self.devices(owner) if d['local']), None)
        if existing:
            with self.connection() as db:
                db.execute('''UPDATE devices SET host=?,port=?,
                    name=CASE WHEN name='This computer' THEN 'Host computer' ELSE name END
                    WHERE id=? AND user_id=?''',
                           (address[0], address[1], existing['id'], owner))
            saved = self.device(owner, existing['id'])
            if saved is None:
                raise RuntimeError('Local device could not be saved')
            return saved
        return self.add_device(owner, {'name': 'Host computer', 'host': address[0], 'port': address[1],
                                       'username': 'local', 'local': 1})

    def delete_device(self, owner, device_id):
        with self.connection() as db:
            db.execute('DELETE FROM devices WHERE user_id=? AND id=?', (owner, device_id))

    def save_job(self, owner, job: dict[str, Any]):
        with self.connection() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (job['id'], owner, json.dumps(job, allow_nan=False)))

    def job(self, owner, job_id):
        with self.connection() as db:
            row = db.execute('SELECT data FROM jobs WHERE user_id=? AND id=?', (owner, job_id)).fetchone()
            return json.loads(row[0]) if row else None

    def jobs(self, owner):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM jobs WHERE user_id=? ORDER BY rowid DESC LIMIT 100', (owner,))]

    def all_jobs(self):
        with self.connection() as db:
            return [(row['user_id'], json.loads(row['data'])) for row in db.execute('SELECT user_id,data FROM jobs')]

    def delete_job(self, owner, job_id):
        with self.connection() as db:
            db.execute('DELETE FROM jobs WHERE user_id=? AND id=?', (owner, job_id))
