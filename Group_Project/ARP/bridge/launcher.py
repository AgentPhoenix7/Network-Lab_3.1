"""Asyncio launcher for the C processes: channels first, then stations, each one waited for on its `ready` event.

A process that exits on its own is reported through on_error and the rest of the run is stopped.
"""
import asyncio
from collections.abc import Callable

from .telemetry import Telemetry

KILL_AFTER_S = 2.0  # QUIT gets this long before SIGKILL


class Launcher:
    def __init__(self, tel: Telemetry, on_error: Callable[[str], None]) -> None:
        self.tel = tel
        self.on_error = on_error
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self._watchers: set[asyncio.Task] = set()  # strong refs so the tasks are not garbage-collected

    async def start(self, channels: list[tuple[str, list[str]]], stations: list[tuple[str, list[str]]],
                    timeout: float = 5.0) -> None:
        """Spawn each (telemetry key, argv); a failure stops everything started so far and raises."""
        try:
            for name, argv in [*channels, *stations]:
                await self._spawn(name, argv)
                try:
                    await self.tel.wait_ready(name, timeout)
                except TimeoutError:
                    raise RuntimeError(f"{name} not ready after {timeout} s") from None
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Send QUIT to every process, then SIGKILL whatever is still alive after KILL_AFTER_S."""
        for name in self.procs:
            self.tel.send(name, "QUIT")
        procs = list(self.procs.values())
        self.procs.clear()  # cleared before any await: the watchers then see a clean stop, not an error
        if procs:
            await asyncio.wait([asyncio.ensure_future(p.wait()) for p in procs], timeout=KILL_AFTER_S)
        for p in procs:
            if p.returncode is None:
                p.kill()
            await p.wait()

    async def _spawn(self, name: str, argv: list[str]) -> None:
        proc = await asyncio.create_subprocess_exec(*argv)
        self.procs[name] = proc
        task = asyncio.create_task(self._watch(name, proc))
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)

    async def _watch(self, name: str, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        if self.procs.get(name) is not proc:  # already stopped on purpose
            return
        self.on_error(f"{name} exited (code {code})")
        await self.stop()
