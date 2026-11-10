"""Telemetry TCP server (contract §2): reads JSON lines from C processes, registers them by hello, writes command lines back.

Registry keys are "lan:<id>" for channels and "node:<id>" for stations. Events are passed on unchanged.
"""
import asyncio
import json
from collections.abc import Callable


def client_key(msg: dict) -> str:
    return f"lan:{msg['lan']}" if "lan" in msg else f"node:{msg['node']}"


class Telemetry:
    def __init__(self, on_event: Callable[[dict], None], host: str = "127.0.0.1", port: int = 9700) -> None:
        self.on_event = on_event
        self.host = host
        self.port = port
        self.writers: dict[str, asyncio.StreamWriter] = {}
        self._conns: set[asyncio.StreamWriter] = set()
        self._ready: dict[str, asyncio.Event] = {}
        self._server: asyncio.Server | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]  # port 0 in tests picks a free one
        return self.port

    async def stop(self) -> None:
        for w in list(self._conns):
            w.close()  # closing the sockets first, so wait_closed() cannot hang on them
        self._server.close()
        await self._server.wait_closed()

    def send(self, target: str, line: str) -> bool:
        """Write one command line to a registered process; False if it is not connected."""
        writer = self.writers.get(target)
        if writer is None:
            return False
        writer.write(f"{line}\n".encode())
        return True

    async def wait_ready(self, key: str, timeout: float) -> None:
        """Return when the process sent its `ready` event; raises TimeoutError otherwise."""
        await asyncio.wait_for(self._ready_event(key).wait(), timeout)

    def _ready_event(self, key: str) -> asyncio.Event:
        return self._ready.setdefault(key, asyncio.Event())

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._conns.add(writer)
        key = None
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:  # a stray non-JSON line must not kill the link
                    continue
                if msg.get("ev") == "hello":
                    key = client_key(msg)
                    self.writers[key] = writer
                elif msg.get("ev") == "ready":
                    self._ready_event(client_key(msg)).set()
                self.on_event(msg)
        finally:
            if key is not None and self.writers.get(key) is writer:
                del self.writers[key]
                self._ready.pop(key, None)  # a restarted process must report ready again
            self._conns.discard(writer)
            writer.close()
