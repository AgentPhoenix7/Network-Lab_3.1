import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def running_server(*, timeout=0.5, **options):
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
