"""WebSocket server (contract §3) over a fake telemetry registry and a fake launcher: no C binaries run.
The fake records every line the bridge sends, keyed by the same "lan:<id>" / "node:<id>" targets."""
import json

from starlette.testclient import TestClient

from bridge.server import create_app


class FakeTel:
    def __init__(self, on_event, port=0):
        self.on_event, self.port = on_event, 9700
        self.sent: list[tuple[str, str]] = []

    async def start(self):
        return self.port

    async def stop(self):
        pass

    def send(self, target, line):
        self.sent.append((target, line))
        return True


class FakeLauncher:
    """Records the argv it was given; `flood` emits that many events, as if the C processes ran."""

    def __init__(self, tel, on_error, flood=0):
        self.tel, self.on_error, self.flood, self.argv = tel, on_error, flood, {}

    async def start(self, channels, stations):
        self.argv = dict([*channels, *stations])
        for i in range(self.flood):
            self.tel.on_event({"ev": "arp", "node": "h1", "op": "pending", "ip": f"10.0.0.{i % 250}"})

    async def stop(self):
        self.argv = {}


def make_app(tmp_path, flood=0):
    def launcher_factory(tel, on_error):
        return FakeLauncher(tel, on_error, flood)
    return create_app(root=tmp_path, tel_factory=FakeTel, launcher_factory=launcher_factory)


def drain(ws, kind, skip=("event",)):
    """Read messages until one of the wanted type arrives; events in between are skipped."""
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
        assert msg["type"] in skip, msg


def load(ws, name="proxy_arp"):
    ws.send_json({"cmd": "load_preset", "name": name})
    return drain(ws, "topology")["topology"]


def test_connect_sends_hello_then_state(tmp_path):
    with TestClient(make_app(tmp_path)) as c, c.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "hello", "version": 1}
        state = ws.receive_json()
        assert state["type"] == "state" and state["running"] is False and state["speed"] == 1.0


def test_w1_every_command_is_answered_as_the_contract_says(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json(); ws.receive_json()
        tel = app.state.bridge.tel
        topo = load(ws)
        assert topo["lans"][0]["id"] == 0 and "x" in topo["nodes"][0]

        ws.send_json({"cmd": "validate", "topology": topo})
        assert drain(ws, "validation")["ok"] is True
        bad = json.loads(json.dumps(topo))
        bad["nodes"][1]["ifaces"][0]["ip"] = bad["nodes"][0]["ifaces"][0]["ip"]
        ws.send_json({"cmd": "validate", "topology": bad})
        v = drain(ws, "validation")
        assert v["ok"] is False and v["errors"][0]["msg"].startswith("duplicate IP")

        ws.send_json({"cmd": "start", "topology": topo})
        assert drain(ws, "topology")["topology"]["nodes"][2]["routes"] == []  # r1 sits on both LANs
        assert drain(ws, "state")["running"] is True
        tel.sent.clear()

        ws.send_json({"cmd": "pause"})
        assert drain(ws, "state")["paused"] is True
        ws.send_json({"cmd": "resume"})
        assert drain(ws, "state")["paused"] is False
        ws.send_json({"cmd": "set_speed", "speed": 2.0})
        assert drain(ws, "state")["speed"] == 2.0
        ws.send_json({"cmd": "set_speed", "speed": 0})
        assert "0.1..10" in drain(ws, "error")["msg"]
        ws.send_json({"cmd": "set_impair", "lan": None, "loss": 0.05, "corrupt": 0.0, "delay_ms": 300})
        assert drain(ws, "state")["impair"]["global"]["loss"] == 0.05
        ws.send_json({"cmd": "set_impair", "lan": 1, "clear": True})
        assert drain(ws, "state")["impair"]["per_lan"] == {}
        ws.send_json({"cmd": "ping", "node": "h1", "dst": "141.23.56.21", "count": 1})
        ws.send_json({"cmd": "flush", "node": "h1"})
        ws.send_json({"cmd": "get_log"})
        assert drain(ws, "log")["events"] == []
        ws.send_json({"cmd": "set_arp", "ttl_ms": 1, "retry_ms": 1, "retries": 1})
        assert "stop the run" in drain(ws, "error")["msg"]
        ws.send_json({"cmd": "load_preset", "name": "../etc"})
        assert "bad preset name" in drain(ws, "error")["msg"]
        ws.send_json({"cmd": "frobnicate"})
        assert "unknown command" in drain(ws, "error")["msg"]
        ws.send_json({"cmd": "stop"})
        assert drain(ws, "state")["running"] is False
        ws.send_json({"cmd": "set_arp", "ttl_ms": 5000, "retry_ms": 200, "retries": 2})
        assert drain(ws, "state")["arp"] == {"ttl_ms": 5000, "retry_ms": 200, "retries": 2}

    assert ("lan:0", "SPEED 0") in tel.sent and ("node:r1", "SPEED 0") in tel.sent
    assert ("lan:0", "SPEED 2") in tel.sent
    assert ("lan:0", "IMPAIR 0.05 0 300") in tel.sent
    assert ("node:h1", "PING 141.23.56.21 1") in tel.sent
    assert ("node:h1", "FLUSH") in tel.sent


def test_w2_seq_has_no_gaps_across_1000_events(tmp_path):
    app = make_app(tmp_path, flood=1000)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json(); ws.receive_json()
        load(ws)
        ws.send_json({"cmd": "start", "topology": app.state.bridge.topology})
        seqs = []
        while len(seqs) < 1000:
            msg = ws.receive_json()
            if msg["type"] == "event":
                seqs.append(msg["event"]["seq"])
        assert seqs == list(range(1000))


def test_w3_get_log_equals_the_recorded_jsonl(tmp_path):
    with TestClient(make_app(tmp_path, flood=50)) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json(); ws.receive_json()
        topo = load(ws)
        ws.send_json({"cmd": "start", "topology": topo})
        drain(ws, "topology")
        drain(ws, "state")
        ws.send_json({"cmd": "get_log"})
        events = drain(ws, "log")["events"]
    run = events[0]["run"]
    lines = (tmp_path / "runs" / f"{run}.jsonl").read_text().splitlines()
    assert len(events) == 50 and events == [json.loads(line) for line in lines]


def test_w4_set_impair_for_lan1_reaches_only_channel_1(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json(); ws.receive_json()
        topo = load(ws)
        ws.send_json({"cmd": "start", "topology": topo})
        drain(ws, "topology")
        drain(ws, "state")
        tel = app.state.bridge.tel
        tel.sent.clear()
        ws.send_json({"cmd": "set_impair", "lan": 1, "loss": 0.2, "corrupt": 0.0, "delay_ms": 300})
        assert drain(ws, "state")["impair"]["per_lan"]["1"]["loss"] == 0.2
        assert tel.sent == [("lan:1", "IMPAIR 0.2 0 300")]


def test_rx_uid_from_station_is_rewritten_to_node_iface_seq(tmp_path):
    from bridge.recorder import Recorder
    from bridge.server import Bridge
    b = Bridge(root=tmp_path, tel_factory=FakeTel, launcher_factory=lambda tel, on_error: FakeLauncher(tel, on_error))
    got = []
    b.running, b.rec, b.macs = True, Recorder("t", tmp_path), {"02:00:00:00:01:00": "h1:0"}
    b.clients.append(got.append)
    b.on_c_event({"ev": "rx", "node": "h2", "iface": 0, "uid": "02:00:00:00:01:00:7", "verdict": "accepted"})
    assert got[0]["event"]["uid"] == "h1:0:7"
