"""Telemetry server, driven by an in-process asyncio client that speaks the §2 line protocol."""
import asyncio
import json

from bridge.telemetry import Telemetry


async def connect(port: int, *lines: dict):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    for obj in lines:
        writer.write((json.dumps(obj) + "\n").encode())
    await writer.drain()
    return reader, writer


async def until(cond, timeout: float = 2.0) -> None:
    async def poll():
        while not cond():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(poll(), timeout)


def test_hello_registers_and_events_reach_callback():
    async def run():
        events = []
        tel = Telemetry(on_event=events.append, port=0)
        port = await tel.start()
        _, writer = await connect(port, {"ev": "hello", "role": "channel", "lan": 1},
                                  {"ev": "tx", "node": "h1", "iface": 0, "uid": "h1:0:0"})
        try:
            await until(lambda: len(events) == 2)
            assert "lan:1" in tel.writers
            assert events[0] == {"ev": "hello", "role": "channel", "lan": 1}
            assert events[1]["uid"] == "h1:0:0"
        finally:
            writer.close()
            await tel.stop()
    asyncio.run(run())


def test_send_writes_command_line_to_the_right_process():
    async def run():
        tel = Telemetry(on_event=lambda ev: None, port=0)
        port = await tel.start()
        reader_a, writer_a = await connect(port, {"ev": "hello", "role": "station", "node": "h1"})
        reader_b, writer_b = await connect(port, {"ev": "hello", "role": "station", "node": "h2"})
        try:
            await until(lambda: {"node:h1", "node:h2"} <= tel.writers.keys())
            assert tel.send("node:h2", "PING 141.23.56.21 1") is True
            assert await asyncio.wait_for(reader_b.readline(), 2) == b"PING 141.23.56.21 1\n"
            assert tel.send("node:nobody", "QUIT") is False
        finally:
            writer_a.close()
            writer_b.close()
            await tel.stop()
    asyncio.run(run())


def test_wait_ready_returns_on_ready_and_times_out_otherwise():
    async def run():
        tel = Telemetry(on_event=lambda ev: None, port=0)
        port = await tel.start()
        try:
            _, writer = await connect(port, {"ev": "hello", "role": "channel", "lan": 0})
            pending = asyncio.create_task(tel.wait_ready("lan:0", timeout=2))
            writer.write(b'{"ev": "ready", "lan": 0}\n')
            await writer.drain()
            await asyncio.wait_for(pending, 2)
            try:
                await tel.wait_ready("lan:9", timeout=0.05)
                raise AssertionError("expected TimeoutError")
            except TimeoutError:
                pass
            writer.close()
        finally:
            await tel.stop()
    asyncio.run(run())


def test_non_json_line_is_skipped():
    async def run():
        events = []
        tel = Telemetry(on_event=events.append, port=0)
        port = await tel.start()
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"garbage from a stray printf\n" + b'{"ev": "log", "level": "info", "msg": "ok"}\n')
            await writer.drain()
            await until(lambda: len(events) == 1)
            assert events == [{"ev": "log", "level": "info", "msg": "ok"}]
        finally:
            writer.close()
            await tel.stop()
    asyncio.run(run())
