"""Virtual FTP root using POSIX directory descriptors; never follow symlinks."""

import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

STAGING_PREFIX = '.ftp-upload-'


def valid_name(name: str) -> bool:
    """Validate whether a file or directory name is permitted.

    What it does:
        Checks whether a given path component complies with name safety rules.

    How it does:
        Verifies that the name does not start with the internal upload staging prefix
        ('.ftp-upload-') and contains no unprintable ASCII control characters (ASCII < 32
        or DEL 127). Returns True if valid, False otherwise.
    """
    return not name.startswith(STAGING_PREFIX) and not any(ord(c) < 32 or ord(c) == 127 for c in name)


class Sandbox:
    """Descriptor-relative POSIX filesystem sandbox confining sessions to an FTP root."""

    def __init__(self, root: Path):
        """Initialize the virtual sandbox anchored to a root directory.

        What it does:
            Prepares an isolated filesystem sandbox rooted at the specified directory.

        How it does:
            Opens the root directory path with os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            to obtain a persistent file descriptor (self.root_fd), and initializes the session's
            virtual current working directory (self.cwd) as an empty tuple representing '/'.
        """
        self.root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.cwd: tuple[str, ...] = ()

    def close(self):
        """Release the root directory file descriptor.

        What it does:
            Closes the underlying directory descriptor associated with this sandbox.

        How it does:
            Checks if root_fd is open (>= 0), calls os.close(self.root_fd), and resets
            the descriptor to -1 to prevent descriptor leaks.
        """
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    def normalize(self, path: str) -> tuple[str, ...]:
        """Normalize an FTP path into a canonical tuple of directory parts.

        What it does:
            Validates and resolves a user-supplied virtual path into a canonical tuple
            of path segments, preventing root traversal escapes.

        How it does:
            1. Validates string length (<= 4096 UTF-8 bytes) and verifies no illegal control chars.
            2. If path starts with '/', begins resolution from root (); otherwise begins from self.cwd.
            3. Splits the path by '/' and processes segments: ignores empty parts and '.', and pops
               the last segment on '..'. If '..' is encountered at root, raises PermissionError.
            4. Validates that the reconstructed virtual path length does not exceed 4096 bytes.
            5. Returns a tuple of validated path component strings.
        """
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
        """Open a directory descriptor by walking path parts relative to root.

        What it does:
            Context manager that yields an open directory file descriptor corresponding
            to the given virtual path parts.

        How it does:
            1. Opens an independent descriptor for '.' relative to self.root_fd using
               os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW.
            2. Iteratively steps down each component in parts using os.open() with dir_fd set
               to the current level's descriptor, closing the previous descriptor immediately.
            3. Yields the final directory descriptor to the caller.
            4. Closes the yielded descriptor in a finally block when exiting the context.
        """
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
        """Locate the parent directory descriptor and basename for a given path.

        What it does:
            Context manager that yields a tuple of (parent_dir_fd, target_name) for any
            file or directory operation.

        How it does:
            1. Normalizes the target path into component parts.
            2. Forbids operations directly on the root directory itself (empty parts).
            3. Opens the parent directory using self.directory(parts[:-1]).
            4. Yields the parent directory descriptor and the final component string (parts[-1]).
        """
        parts = self.normalize(path)
        if not parts:
            raise PermissionError('Operation on root is not allowed')
        with self.directory(parts[:-1]) as fd:
            yield fd, parts[-1]

    def pwd(self) -> str:
        """Return the current virtual working directory.

        What it does:
            Generates the absolute FTP path string for the session's current directory.

        How it does:
            Joins the strings in self.cwd with '/' and prepends a leading '/'.
        """
        return '/' + '/'.join(self.cwd)

    def chdir(self, path: str):
        """Change the current virtual working directory.

        What it does:
            Updates the session's virtual working directory if the target path exists and is a directory.

        How it does:
            Normalizes path to component parts and verifies existence/traversability by opening
            it with self.directory(parts). If successful, updates self.cwd = parts.
        """
        parts = self.normalize(path)
        with self.directory(parts):
            self.cwd = parts

    @contextmanager
    def open_read(self, path: str):
        """Open an existing regular file for reading.

        What it does:
            Context manager that yields a binary file object opened for reading.

        How it does:
            1. Resolves parent directory descriptor and filename via self.parent(path).
            2. Opens the file with os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK via dir_fd.
            3. Verifies that the file is a regular file (stat.S_ISREG), rejecting symlinks,
               directories, and device nodes.
            4. Wraps the file descriptor in a binary file reader (os.fdopen) and yields it.
        """
        with self.parent(path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise PermissionError('Only regular files can be transferred')
                yield source

    @contextmanager
    def upload(self, path: str):
        """Publish an upload atomically via temporary staging file.

        What it does:
            Context manager providing an atomic file write target that replaces the destination
            only upon complete, successful transfer.

        How it does:
            1. Resolves parent directory descriptor and target filename via self.parent(path).
            2. If the destination already exists, verifies it is a regular file.
            3. Generates a unique staging filename (.ftp-upload-<uuid>) in the parent directory.
            4. Opens staging file with os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW and 0o600 mode.
            5. Yields the binary file object for incoming chunks.
            6. On clean exit, flushes the stream and atomically moves staging to destination using
               os.replace(src_dir_fd=parent, dst_dir_fd=parent).
            7. In the finally block, unlinks the staging file if still present (e.g., on socket error,
               abort, or timeout), keeping any pre-existing destination file unmodified.
        """
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

    @contextmanager
    def append_upload(self, path: str):
        """Publish an append upload atomically via temporary staging file.

        What it does:
            Context manager providing an atomic append target for the APPE command.
            Preserves pre-existing file content if the transfer fails or aborts.

        How it does:
            1. Resolves parent directory descriptor and target filename via self.parent(path).
            2. If the destination exists, verifies it is a regular file.
            3. Generates a unique staging filename (.ftp-upload-<uuid>) in parent directory.
            4. Copies existing file content (if any) into the staging file.
            5. Yields the binary file object for incoming chunks to append.
            6. On clean exit, flushes and atomically moves staging to destination using os.replace().
            7. In finally block, unlinks the staging file if still present.
        """
        with self.parent(path) as (parent, name):
            exists = False
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise PermissionError('Only regular files can be appended to')
                exists = True
            except FileNotFoundError:
                pass
            staging = STAGING_PREFIX + uuid.uuid4().hex
            fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, 'wb') as target:
                    if exists:
                        src_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                        with os.fdopen(src_fd, 'rb') as src:
                            while chunk := src.read(65536):
                                target.write(chunk)
                    yield target
                    target.flush()
                os.replace(staging, name, src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(staging, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def unique_name(self) -> str:
        """Generate an unused unique filename in the current directory for STOU.

        What it does:
            Generates a unique filename in the current working directory.

        How it does:
            Generates names of the form 'file_<8-char-hex>' and checks existence until an
            unused name is found.
        """
        while True:
            candidate = f'file_{uuid.uuid4().hex[:8]}'
            try:
                self.check_exists(candidate)
            except (FileNotFoundError, OSError, ValueError):
                return candidate

    def size(self, path: str) -> int:
        """Get the byte size of a regular file.

        What it does:
            Returns the exact file size in bytes for the FTP SIZE command.

        How it does:
            Opens the file using self.open_read(path) and queries os.fstat(fileno).st_size.
        """
        with self.open_read(path) as source:
            return os.fstat(source.fileno()).st_size

    def mkdir(self, path: str):
        """Create a new directory in the sandbox.

        What it does:
            Creates a directory at the specified path with standard 0755 permissions.

        How it does:
            Resolves the parent directory descriptor and invokes os.mkdir(name, 0o755, dir_fd=fd).
        """
        with self.parent(path) as (fd, name):
            os.mkdir(name, 0o755, dir_fd=fd)

    def rmdir(self, path: str):
        """Remove an empty directory from the sandbox.

        What it does:
            Deletes an empty directory at the specified virtual path.

        How it does:
            Resolves the parent directory descriptor and invokes os.rmdir(name, dir_fd=fd).
        """
        with self.parent(path) as (fd, name):
            os.rmdir(name, dir_fd=fd)

    def delete(self, path: str):
        """Delete an existing regular file from the sandbox.

        What it does:
            Removes a regular file at the specified virtual path.

        How it does:
            Resolves the parent directory descriptor, verifies via os.stat(follow_symlinks=False)
            that the target is a regular file, and unlinks it with os.unlink(name, dir_fd=fd).
        """
        with self.parent(path) as (fd, name):
            if not stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                raise PermissionError('Only regular files can be deleted')
            os.unlink(name, dir_fd=fd)

    def check_exists(self, path: str):
        """Verify that a path exists in the sandbox as a regular file or directory.

        What it does:
            Checks existence and file kind of a path for RNFR command.

        How it does:
            Resolves parent directory descriptor and inspects os.stat(follow_symlinks=False).
            Raises FileNotFoundError if missing or PermissionError if special/symlink.
        """
        with self.parent(path) as (fd, name):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise PermissionError('Target is not a regular file or directory')

    def rename(self, old_path: str, new_path: str):
        """Rename an existing regular file or directory.

        What it does:
            Atomically moves or renames a file or directory within the virtual sandbox.

        How it does:
            1. Resolves parent directory descriptors and names for old_path and new_path.
            2. Verifies old_name exists and is a regular file or directory without following symlinks.
            3. Verifies new_name complies with valid_name rules.
            4. Uses os.replace() with src_dir_fd and dst_dir_fd to rename the entry safely within the root.
        """
        with self.parent(old_path) as (old_dir_fd, old_name), self.parent(new_path) as (new_dir_fd, new_name):
            old_info = os.stat(old_name, dir_fd=old_dir_fd, follow_symlinks=False)
            if not (stat.S_ISREG(old_info.st_mode) or stat.S_ISDIR(old_info.st_mode)):
                raise PermissionError('Only regular files or directories can be renamed')
            if not valid_name(new_name):
                raise ValueError('Invalid destination name')
            os.replace(old_name, new_name, src_dir_fd=old_dir_fd, dst_dir_fd=new_dir_fd)

    def entries(self, path: str = '') -> list[tuple[str, os.stat_result]]:
        """List file and directory entries for LIST and NLST commands.

        What it does:
            Retrieves directory entries and metadata, or a single file's metadata if path
            targets a file.

        How it does:
            1. Normalizes path. If path specifies an existing regular file, returns [(name, stat)].
            2. Otherwise, opens the directory using self.directory(parts).
            3. Iterates through os.listdir(fd), filtering out invalid/staging names.
            4. Collects stat results for regular files and directories without following symlinks.
            5. Returns a sorted list of (entry_name, stat_result) tuples.
        """
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
