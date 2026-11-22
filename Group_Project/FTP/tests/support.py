import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def running_server(*, timeout=0.5, **options):
    """Context manager spinning up an ephemeral loopback FTPServer instance for testing.

    What it does:
        Creates a temporary directory sandbox, starts a background FTPServer thread,
        and yields (server, root_path) for isolated test execution.

    How it does:
        1. Allocates tempfile.TemporaryDirectory() and creates an inner 'root' subdirectory.
        2. Instantiates FTPServer on 127.0.0.1:0 with test credentials ('student', 'lab-test').
        3. Spawns and starts a daemon server thread running serve_forever().
        4. Yields server and root Path to caller.
        5. In finally block: shuts down server, closes sockets, and joins thread within 3s timeout.
    """
    from ftp_lab.server import FTPServer

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / 'root'
        root.mkdir()
        server = FTPServer(('127.0.0.1', 0), root, 'student', 'lab-test', timeout=timeout, **options)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
        thread.start()
        try:
            yield server, root
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
            if thread.is_alive():
                raise AssertionError('Server thread did not stop')


def connect(server, *, login=True):
    """Establish a standard standard-library ftplib.FTP connection to a test server.

    What it does:
        Connects an ftplib.FTP client to the target server address and optionally authenticates.

    How it does:
        1. Instantiates ftplib.FTP().
        2. Calls ftp.connect() with the server address and 3s timeout.
        3. If login is True, calls ftp.login('student', 'lab-test').
        4. Closes the socket and re-raises on any connection/login failure.
        5. Returns the connected FTP instance.
    """
    import ftplib

    ftp = ftplib.FTP()
    try:
        ftp.connect(*server.address, timeout=3)
        if login:
            ftp.login('student', 'lab-test')
    except BaseException:
        ftp.close()
        raise
    return ftp
