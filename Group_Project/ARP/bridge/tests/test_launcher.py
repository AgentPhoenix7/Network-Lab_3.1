"""Launcher tests: a fake C program (plain Python, written to tmp_path) connects to the telemetry server,
sends hello + ready, and then obeys or ignores QUIT depending on its mode."""
import asyncio
import sys
import time

from bridge.launcher import Launcher
from bridge.telemetry import Telemetry

FAKE = '''
import asyncio, json, sys

port, role, ident, mode = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
key = "lan" if role == "channel" else "node"
ident = int(ident) if role == "channel" else ident

def say(writer, obj):
    writer.write((json.dumps(obj) + "\\n").encode())

async def main():
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    say(writer, {"ev": "hello", "role": role, key: ident})
    say(writer, {"ev": "ready", key: ident})
    await writer.drain()
    if mode == "crash":
        sys.exit(3)
    if mode == "deaf":
        await asyncio.sleep(3600)
    while (line := await reader.readline()) and line.strip() != b"QUIT":
        pass

asyncio.run(main())
'''


def argv_for(fake, port: int, role: str, ident: str, mode: str = "ok") -> list[str]:
    return [sys.executable, str(fake), str(port), role, ident, mode]


def test_channels_start_before_stations_and_quit_is_clean(tmp_path):
    async def run():
        fake = tmp_path / "fake_c.py"
        fake.write_text(FAKE)
        events, errors = [], []
        tel = Telemetry(on_event=events.append, port=0)
        launcher = Launcher(tel, on_error=errors.append)
        port = await tel.start()
        try:
            await launcher.start(
                channels=[("lan:1", argv_for(fake, port, "channel", "1"))],
                stations=[("node:h1", argv_for(fake, port, "station", "h1"))],
            )
            hellos = [e["role"] for e in events if e["ev"] == "hello"]
            assert hellos == ["channel", "station"]
            procs = dict(launcher.procs)
            await launcher.stop()
            assert [p.returncode for p in procs.values()] == [0, 0]
            assert errors == []
            assert launcher.procs == {}
        finally:
            await launcher.stop()
            await tel.stop()
    asyncio.run(run())


def test_unexpected_exit_is_reported_and_the_rest_is_stopped(tmp_path):
    async def run():
        fake = tmp_path / "fake_c.py"
        fake.write_text(FAKE)
        errors = []
        tel = Telemetry(on_event=lambda ev: None, port=0)
        launcher = Launcher(tel, on_error=errors.append)
        port = await tel.start()
        try:
            await launcher.start(
                channels=[("lan:1", argv_for(fake, port, "channel", "1"))],
                stations=[("node:h1", argv_for(fake, port, "station", "h1", mode="crash"))],
            )
            for _ in range(200):  # the watcher needs a few loop turns to see the exit
                if errors:
                    break
                await asyncio.sleep(0.01)
            assert errors == ["node:h1 exited (code 3)"]
            assert launcher.procs == {}  # the channel was stopped too
        finally:
            await launcher.stop()
            await tel.stop()
    asyncio.run(run())


def test_stop_kills_a_process_that_ignores_quit(tmp_path):
    async def run():
        fake = tmp_path / "fake_c.py"
        fake.write_text(FAKE)
        tel = Telemetry(on_event=lambda ev: None, port=0)
        launcher = Launcher(tel, on_error=lambda msg: None)
        port = await tel.start()
        try:
            await launcher.start(
                channels=[("lan:1", argv_for(fake, port, "channel", "1", mode="deaf"))],
                stations=[],
            )
            proc = launcher.procs["lan:1"]
            began = time.monotonic()
            await launcher.stop()
            elapsed = time.monotonic() - began
            assert proc.returncode == -9  # SIGKILL
            assert 1.5 < elapsed < 4.0    # about the 2 s grace period, not a hang
        finally:
            await tel.stop()
    asyncio.run(run())
