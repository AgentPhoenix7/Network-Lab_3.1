"""Virtual FTP root using POSIX directory descriptors; never follow symlinks."""

import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

STAGING_PREFIX = '.ftp-upload-'


def valid_name(name: str) -> bool:
    return not name.startswith(STAGING_PREFIX) and not any(ord(c) < 32 or ord(c) == 127 for c in name)


class Sandbox:
    def __init__(self, root: Path):
        self.root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.cwd: tuple[str, ...] = ()

    def close(self):
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    def normalize(self, path: str) -> tuple[str, ...]:
        if not valid_name(path) or len(path.encode('utf-8')) > 4096:
            raise ValueError('Invalid path')
        parts = [] if path.startswith('/') else list(self.cwd)
        for part in path.split('/'):
            if not valid_name(part):
                raise ValueError('Reserved or invalid name')
            if part in ('', '.'):
                continue
            if part == '..':
                if not parts:
                    raise PermissionError('Path escapes FTP root')
                parts.pop()
            else:
                parts.append(part)
        if len(('/' + '/'.join(parts)).encode('utf-8')) > 4096:
            raise ValueError('Virtual path too long')
        return tuple(parts)

    @contextmanager
    def directory(self, parts: tuple[str, ...]):
        # A fresh open description avoids sharing a directory stream position.
        fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root_fd)
        try:
            for part in parts:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def parent(self, path: str):
        parts = self.normalize(path)
        if not parts:
            raise PermissionError('Operation on root is not allowed')
        with self.directory(parts[:-1]) as fd:
            yield fd, parts[-1]

    def pwd(self) -> str:
        return '/' + '/'.join(self.cwd)

    def chdir(self, path: str):
        parts = self.normalize(path)
        with self.directory(parts):
            self.cwd = parts

    @contextmanager
    def open_read(self, path: str):
        with self.parent(path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise PermissionError('Only regular files can be transferred')
                yield source

    @contextmanager
    def upload(self, path: str):
        """Publish only after clean EOF; a reset/timeout leaves the old file intact."""
        with self.parent(path) as (parent, name):
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISREG(info.st_mode):
                    raise PermissionError('Only regular files can be replaced')
            staging = STAGING_PREFIX + uuid.uuid4().hex
            fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, 'wb') as target:
                    yield target
                    target.flush()
                os.replace(staging, name, src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(staging, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def size(self, path: str) -> int:
        with self.open_read(path) as source:
            return os.fstat(source.fileno()).st_size

    def mkdir(self, path: str):
        with self.parent(path) as (fd, name):
            os.mkdir(name, 0o755, dir_fd=fd)

    def rmdir(self, path: str):
        with self.parent(path) as (fd, name):
            os.rmdir(name, dir_fd=fd)

    def delete(self, path: str):
        with self.parent(path) as (fd, name):
            if not stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                raise PermissionError('Only regular files can be deleted')
            os.unlink(name, dir_fd=fd)

    def entries(self, path: str = '') -> list[tuple[str, os.stat_result]]:
        parts = self.normalize(path)
        if parts:
            with self.directory(parts[:-1]) as fd:
                info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode):
                    return [(parts[-1], info)]
                if not stat.S_ISDIR(info.st_mode):
                    raise PermissionError('Unsupported listing target')
        entries = []
        with self.directory(parts) as fd:
            for name in sorted(os.listdir(fd)):
                if not valid_name(name):
                    continue
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode):
                    entries.append((name, info))
        return entries
