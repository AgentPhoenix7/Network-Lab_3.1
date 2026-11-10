"""FastAPI bridge (contract §3): WebSocket /ws for the browser, static web/ at /.

Bridge owns one run at a time: it validates and lays out the topology, writes the confs, launches the C
processes, stamps and records every telemetry event, and relays commands. The logic lives in Bridge so
headless.py and the tests can drive it without a browser. It never interprets ARP itself.
"""
import asyncio
import ipaddress
import json
import re
import struct
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .confgen import DEFAULT_ARP, write_run
from .decode import decode
from .graph import layout, static_routes
from .launcher import Launcher
from .recorder import Recorder
from .telemetry import Telemetry
from .topology import auto_fill, validate

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"


class SimClock:
    """Bridge-side sim time for `t`: advances at the speed multiplier, frozen while paused (plan §6)."""

    def __init__(self) -> None:
        self.ms, self.speed, self.last = 0.0, 1.0, None  # last None = not advancing

    def _fold(self) -> None:
        if self.last is not None:
            now = time.monotonic()
            self.ms, self.last = self.ms + (now - self.last) * 1000 * self.speed, now

    def reset(self) -> None:
        self.ms, self.last = 0.0, None

    def run(self, on: bool) -> None:
        self._fold()
        self.last = time.monotonic() if on else None

    def set_speed(self, speed: float) -> None:
        self._fold()
        self.speed = speed

    def now(self) -> int:
        self._fold()
        return round(self.ms)


def _prepare(topo: dict) -> dict:
    """auto_fill, NetworkX layout (positions) and static routes for every router (contract §3.3)."""
    topo = layout(auto_fill(topo))
    routes = static_routes(topo)
    for node in topo["nodes"]:
        if node["kind"] == "router":
            node["routes"] = routes[node["id"]]
    return topo


class Bridge:
    def __init__(self, root=ROOT, tel_factory=Telemetry, launcher_factory=Launcher, tel_port=9700) -> None:
        self.root = Path(root)
        self.tel = tel_factory(on_event=self.on_c_event, port=tel_port)
        self.launcher = launcher_factory(self.tel, on_error=self.on_error)
        self.clients: list = []  # callables that receive every broadcast message
        self.clock = SimClock()
        self.rec: Recorder | None = None
        self.topology: dict | None = None
        self.macs: dict = {}  # sender MAC -> "node:iface", to rewrite station rx uids (contract 2.1)
        self.running = self.paused = False
        self.speed = 1.0
        self.impair = {"global": {"loss": 0.0, "corrupt": 0.0, "delay_ms": 1000.0}, "per_lan": {}}
        self.arp = dict(DEFAULT_ARP)

    def state(self) -> dict:
        return {"type": "state", "running": self.running, "paused": self.paused, "speed": self.speed,
                "impair": self.impair, "arp": self.arp}

    def broadcast(self, msg: dict) -> None:
        for send in list(self.clients):
            send(msg)

    def on_c_event(self, msg: dict) -> None:
        """Telemetry callback: stamp, record, decode tx frames (contract §3.1), broadcast."""
        if not self.running:
            return
        if msg.get("ev") == "rx" and msg.get("uid", "").count(":") == 6:  # station sends "<mac>:<seq>"
            mac, seq = msg["uid"].rsplit(":", 1)
            if mac in self.macs:
                msg = {**msg, "uid": f"{self.macs[mac]}:{seq}"}
        if msg.get("ev") == "tx":
            try:
                msg = {**msg, "decoded": decode(msg["hex"])}
            except (KeyError, TypeError, ValueError, struct.error):
                pass  # a malformed frame is still recorded, just without `decoded`
        self.broadcast({"type": "event", "event": self.rec.add(msg, self.clock.now())})

    def on_error(self, msg: str) -> None:
        """Launcher callback: a C process died, so the launcher has already stopped the rest."""
        self.running = self.paused = False
        self.clock.run(False)
        self.broadcast({"type": "error", "msg": msg})
        self.broadcast(self.state())

    async def handle(self, cmd, reply) -> None:
        """Run one command (contract §3.2). Errors and validation go to reply; state goes to everyone."""
        fn = getattr(self, f"cmd_{cmd.get('cmd')}", None) if isinstance(cmd, dict) else None
        if fn is None:
            return reply({"type": "error", "msg": f"unknown command {cmd!r:.80}"})
        try:
            await fn(cmd, reply)
        except (AttributeError, KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
            reply({"type": "error", "msg": f"bad command: {exc}"})

    async def shutdown(self) -> None:
        await self._halt()
        await self.tel.stop()

    async def cmd_validate(self, cmd, reply) -> None:
        reply(self._check(cmd["topology"]))

    async def cmd_start(self, cmd, reply) -> None:
        check = self._check(cmd["topology"])
        if not check["ok"]:
            return reply(check)
        if self.running:
            raise ValueError("a run is already active; stop it first")
        topo = _prepare(cmd["topology"])
        self.macs = {i["mac"].lower(): f"{n['id']}:{i['idx']}" for n in topo["nodes"] for i in n["ifaces"]}
        run_id = time.strftime("%Y-%m-%dT%H-%M-%S")
        run_dir = self.root / "runs" / run_id
        tails = write_run(topo, run_dir, self.arp, self.tel.port)
        chans = [(f"lan:{lid}", [str(self.root / "channel"), *tail]) for lid, tail in tails.items()]
        stations = [(f"node:{n['id']}", [str(self.root / "station"), "--conf", str(run_dir / f"{n['id']}.conf"),
                                         "--tel-port", str(self.tel.port)]) for n in topo["nodes"]]
        self.rec = Recorder(run_id, self.root / "runs")
        self.running = True  # on_c_event records from the first `hello` on
        self.clock.reset()
        self.clock.set_speed(self.speed)
        self.clock.run(True)
        try:
            await self.launcher.start(chans, stations)
        except (OSError, RuntimeError) as exc:
            self.running = False
            self.clock.run(False)
            return reply({"type": "error", "msg": f"start failed: {exc}"})
        self.topology = topo
        self._send_all(f"SPEED {self.speed:g}")
        self._push_impair(self._lan_ids())
        self.broadcast({"type": "topology", "topology": topo})
        self.broadcast(self.state())

    async def cmd_stop(self, cmd, reply) -> None:
        await self._halt()

    async def cmd_pause(self, cmd, reply) -> None:
        if not self.running:
            raise ValueError("no run to pause")
        self.paused = True
        self.clock.run(False)
        self._send_all("SPEED 0")
        self.broadcast(self.state())

    async def cmd_resume(self, cmd, reply) -> None:
        if not self.running:
            raise ValueError("no run to resume")
        self.paused = False
        self.clock.run(True)
        self._send_all(f"SPEED {self.speed:g}")
        self.broadcast(self.state())

    async def cmd_set_speed(self, cmd, reply) -> None:
        speed = float(cmd["speed"])
        if not 0.1 <= speed <= 10:
            raise ValueError("speed must be 0.1..10")
        self.speed = speed
        self.clock.set_speed(speed)
        if self.running and not self.paused:
            self._send_all(f"SPEED {speed:g}")
        self.broadcast(self.state())

    async def cmd_set_impair(self, cmd, reply) -> None:
        lan = None if cmd.get("lan") is None else int(cmd["lan"])
        if lan is not None and self.topology and lan not in self._lan_ids():
            raise ValueError(f"unknown LAN {lan}")
        if cmd.get("clear"):
            if lan is None:
                raise ValueError("clear needs a LAN")
            self.impair["per_lan"].pop(lan, None)
            self._push_impair([lan])
        else:
            vals = {"loss": float(cmd["loss"]), "corrupt": float(cmd["corrupt"]), "delay_ms": float(cmd["delay_ms"])}
            if not (0 <= vals["loss"] <= 1 and 0 <= vals["corrupt"] <= 1 and vals["delay_ms"] >= 0):
                raise ValueError("loss and corrupt must be 0..1, delay_ms >= 0")
            if lan is None:  # global: channels with their own override keep it
                self.impair["global"] = vals
                self._push_impair([i for i in self._lan_ids() if i not in self.impair["per_lan"]])
            else:
                self.impair["per_lan"][lan] = vals
                self._push_impair([lan])
        self.broadcast(self.state())

    async def cmd_set_arp(self, cmd, reply) -> None:
        if self.running:
            raise ValueError("stop the run before changing the ARP timers")
        arp = {"ttl_ms": int(cmd["ttl_ms"]), "retry_ms": int(cmd["retry_ms"]), "retries": int(cmd["retries"])}
        if min(arp.values()) < 1:
            raise ValueError("ARP timers must be at least 1")
        self.arp = arp
        self.broadcast(self.state())

    async def cmd_ping(self, cmd, reply) -> None:
        dst = str(ipaddress.IPv4Address(cmd["dst"]))  # also keeps newlines out of the C command line
        count = int(cmd.get("count", 1))
        if count < 1:
            raise ValueError("count must be at least 1")
        self._to_node(cmd["node"], f"PING {dst} {count}")

    async def cmd_flush(self, cmd, reply) -> None:
        self._to_node(cmd["node"], "FLUSH")

    async def cmd_get_log(self, cmd, reply) -> None:
        reply({"type": "log", "events": list(self.rec.events) if self.rec else []})

    async def cmd_load_preset(self, cmd, reply) -> None:
        name = cmd["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z_0-9]+", name):
            raise ValueError("bad preset name")
        if self.running:
            raise ValueError("stop the run before loading a preset")
        self.topology = _prepare(json.loads((SCENARIOS / f"{name}.json").read_text()))
        self.broadcast({"type": "topology", "topology": self.topology})

    def _check(self, topo) -> dict:
        if not isinstance(topo, dict):
            raise ValueError("topology must be an object")
        errors = validate(topo)
        return {"type": "validation", "ok": not errors, "errors": errors}

    def _lan_ids(self) -> list:
        return [lan["id"] for lan in self.topology["lans"]] if self.topology else []

    def _send_all(self, line: str) -> None:
        if self.topology:
            for key in [f"lan:{i}" for i in self._lan_ids()] + [f"node:{n['id']}" for n in self.topology["nodes"]]:
                self.tel.send(key, line)

    def _push_impair(self, lan_ids: list) -> None:
        """Send IMPAIR to each channel: its own override if it has one, else the global setting."""
        for lid in lan_ids if self.running else []:
            imp = self.impair["per_lan"].get(lid, self.impair["global"])
            self.tel.send(f"lan:{lid}", f"IMPAIR {imp['loss']:g} {imp['corrupt']:g} {imp['delay_ms']:g}")

    def _to_node(self, node, line: str) -> None:
        if not self.running:
            raise ValueError("no run is active")
        if not self.tel.send(f"node:{node}", line):
            raise ValueError(f"node {node} is not connected")

    async def _halt(self) -> None:
        await self.launcher.stop()
        self.running = self.paused = False
        self.clock.run(False)
        self.broadcast(self.state())


def create_app(root=ROOT, tel_factory=Telemetry, launcher_factory=Launcher, tel_port=9700) -> FastAPI:
    bridge = Bridge(root, tel_factory, launcher_factory, tel_port)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await bridge.tel.start()
        yield
        await bridge.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.bridge = bridge

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        queue: asyncio.Queue = asyncio.Queue()  # one sender per client keeps events and replies in order
        send = queue.put_nowait
        bridge.clients.append(send)
        sender = asyncio.create_task(_pump(ws, queue))
        send({"type": "hello", "version": 1})
        if bridge.topology:
            send({"type": "topology", "topology": bridge.topology})
        send(bridge.state())
        try:
            while True:
                try:
                    cmd = json.loads(await ws.receive_text())
                except ValueError:
                    cmd = None
                await bridge.handle(cmd, send)
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            bridge.clients.remove(send)

    if (root / "web").is_dir():
        app.mount("/", StaticFiles(directory=root / "web", html=True), name="web")
    return app


async def _pump(ws: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        await ws.send_json(await queue.get())


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
