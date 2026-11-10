"""E2E scenarios S1-S10 (plan §7, M9): the real ./channel and ./station binaries, driven through bridge.server.Bridge.

Each test runs one scenario on a fresh Bridge (the same code path as the browser and headless.py), collects every
event the bridge broadcasts, and asserts on the trace. Bridge.shutdown() sends QUIT and kills whatever is left,
and it runs in `finally`, so no process outlives a test. Channel ports come from a free block picked per test
(confgen.BASE_PORT is patched for the duration), so runs never collide with a running project.
"""
import asyncio
import random
import socket
import time
from contextlib import contextmanager

from bridge import confgen
from bridge.decode import decode
from bridge.server import Bridge

DEADLINE_S = 20.0  # wall-clock limit for one whole scenario


@contextmanager
def port_block():
    """Patch confgen.BASE_PORT to a block where 3 consecutive ports are free (channel for LAN k = base + k)."""
    old = confgen.BASE_PORT
    for _ in range(100):
        base = random.randint(20000, 60000)
        socks = []
        try:
            for k in range(3):
                s = socket.socket()
                socks.append(s)
                s.bind(("127.0.0.1", base + k))
            break
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    else:
        raise RuntimeError("no free port block for the channels")
    confgen.BASE_PORT = base
    try:
        yield
    finally:
        confgen.BASE_PORT = old


def ev(name, **want):
    """Predicate: an event of kind `name` whose fields equal every keyword."""
    return lambda e: e.get("ev") == name and all(e.get(k) == v for k, v in want.items())


def arp_tx(e, oper=None):
    """A frame an ARP engine put on the wire (optionally only requests or only replies)."""
    arp = e.get("decoded", {}).get("arp") if e.get("ev") == "tx" else None
    return arp is not None and (oper is None or arp.get("oper") == oper)


def icmp_tx(kind):
    return lambda e: e.get("ev") == "tx" and e.get("decoded", {}).get("icmp", {}).get("type") == kind


def at(node, pred):
    return lambda e: e.get("node") == node and pred(e)


class Net:
    """One Bridge run: `events` holds every event broadcast so far, in order."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.errors: list[str] = []
        self.bridge = Bridge(tel_port=0)  # OS picks the telemetry port
        self.bridge.clients.append(self._on_msg)

    def _on_msg(self, msg: dict) -> None:
        if msg.get("type") == "event":
            self.events.append(msg["event"])
        elif msg.get("type") == "error":
            self.errors.append(msg["msg"])

    async def send(self, cmd_name: str, /, **kw) -> None:
        replies: list[dict] = []
        await self.bridge.handle({"cmd": cmd_name, **kw}, replies.append)
        bad = [r["msg"] for r in replies if r.get("type") == "error"]
        assert not bad, f"{cmd_name} failed: {bad}"

    async def start(self, preset: str, speed: float = 10, arp: dict | None = None,
                    loss: float = 0.0, corrupt: float = 0.0) -> None:
        await self.send("load_preset", name=preset)
        await self.send("set_speed", speed=speed)
        if arp:
            await self.send("set_arp", **arp)
        await self.send("set_impair", lan=None, loss=loss, corrupt=corrupt, delay_ms=0)
        await self.send("start", topology=self.bridge.topology)
        assert self.bridge.running

    async def ping(self, node: str, dst: str) -> None:
        await self.send("ping", node=node, dst=dst, count=1)

    async def wait_for(self, pred, what: str, n: int = 1, start: int = 0, timeout: float = 5.0) -> list[dict]:
        """Poll until at least n events after index `start` match pred; return them."""
        end = time.monotonic() + timeout
        while True:
            hits = [e for e in self.events[start:] if pred(e)]
            if len(hits) >= n:
                return hits
            if time.monotonic() > end:
                raise AssertionError(f"timed out waiting for {what} (have {len(hits)}/{n}, "
                                     f"{len(self.events)} events)")
            await asyncio.sleep(0.02)


def run(body) -> None:
    """Run body(net) on a fresh Bridge under DEADLINE_S; the bridge is shut down whatever happens."""
    async def main():
        net = Net()
        await net.bridge.tel.start()
        try:
            await asyncio.wait_for(body(net), DEADLINE_S)
        finally:
            await net.bridge.shutdown()
        assert not net.errors, net.errors

    with port_block():
        asyncio.run(main())


def test_s1_bus_request_reaches_all_and_reply_is_filtered():
    async def body(net):
        await net.start("single_bus")
        await net.ping("h1", "141.23.56.12")  # h3
        req = (await net.wait_for(at("h1", arp_tx), "ARP request from h1", timeout=5))[0]
        assert req["decoded"]["arp"]["oper"] == "request"
        for node in ("h2", "h3", "h4"):
            await net.wait_for(ev("rx", node=node, uid=req["uid"], verdict="accepted"),
                               f"request accepted at {node}")
        rep = (await net.wait_for(lambda e: arp_tx(e, "reply") and e["node"] == "h3", "ARP reply from h3"))[0]
        await net.wait_for(ev("rx", node="h1", uid=rep["uid"], verdict="accepted"), "reply accepted at h1")
        for node in ("h2", "h4"):
            await net.wait_for(ev("rx", node=node, uid=rep["uid"], verdict="ignored"),
                               f"reply ignored at {node} (bus)")
        await net.wait_for(ev("ip", node="h3", op="deliver", icmp="echo_req"), "echo request delivered to h3")
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "echo reply delivered to h1")

    run(body)


def test_s2_star_reply_goes_only_to_h1_after_learning():
    async def body(net):
        await net.start("single_star")
        await net.ping("h1", "141.23.56.12")
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "echo reply delivered to h1")
        rep = (await net.wait_for(lambda e: icmp_tx("echo_rep")(e) and e["node"] == "h3", "echo reply from h3"))[0]
        med = (await net.wait_for(lambda e: e["ev"] == "medium" and e["uid"] == rep["uid"],
                                  "medium event for the echo reply"))[0]
        assert med["to"] == [{"node": "h1", "iface": 0}], med["to"]

    run(body)


def test_s3_second_ping_needs_no_arp():
    async def body(net):
        await net.start("single_bus")
        await net.ping("h1", "141.23.56.11")
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "first echo reply")
        # telemetry arrives per process, so the reply's tx line may trail the delivery: wait for it explicitly
        await net.wait_for(lambda e: arp_tx(e, "reply"), "ARP reply tx")
        arp_before = len([e for e in net.events if arp_tx(e)])
        assert arp_before == 2, "expected one ARP request and one reply before the second ping"
        await net.ping("h1", "141.23.56.11")
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "second echo reply", n=2)
        assert len([e for e in net.events if arp_tx(e)]) == arp_before, "second ping sent an ARP frame"

    run(body)


def test_s4_entry_expires_and_next_ping_re_arps():
    async def body(net):
        await net.start("single_bus", speed=5, arp=dict(ttl_ms=2000, retry_ms=1000, retries=3))
        await net.ping("h1", "141.23.56.11")
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "first echo reply")
        await net.wait_for(ev("arp", node="h1", op="expire", ip="141.23.56.11"), "expire on h1", timeout=10)
        await net.ping("h1", "141.23.56.11")
        await net.wait_for(ev("arp", node="h1", op="pending", ip="141.23.56.11"), "re-ARP on h1", n=2)
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "second echo reply", n=2)

    run(body)


def test_s5_total_loss_retries_then_arp_fail_drop():
    async def body(net):
        await net.start("single_bus", arp=dict(ttl_ms=30000, retry_ms=1000, retries=3), loss=1.0)
        await net.ping("h1", "141.23.56.11")
        await net.wait_for(ev("ip", node="h1", op="drop", reason="arp_fail"), "ip drop arp_fail on h1")
        ops = [e["op"] for e in net.events
               if e["ev"] == "arp" and e["node"] == "h1" and e["ip"] == "141.23.56.11" and e["op"] in ("retry", "fail")]
        assert ops == ["retry", "retry", "fail"], ops

    run(body)


def test_s6_corruption_is_always_fcs_fail_and_never_learned():
    async def body(net):
        await net.start("single_bus", arp=dict(ttl_ms=30000, retry_ms=1000, retries=3), corrupt=1.0)
        await net.ping("h1", "141.23.56.11")
        await net.wait_for(ev("arp", node="h1", op="fail"), "ARP fail on h1")
        rx = [e for e in net.events if e["ev"] == "rx"]
        assert rx, "no rx event at all"
        assert all(e["verdict"] == "fcs_fail" for e in rx), [e["verdict"] for e in rx]
        assert not [e for e in net.events if e["ev"] == "arp" and e["op"] == "learn"]

    run(body)


def test_s7_two_lans_host_arps_gateway_and_router_arps_target():
    async def body(net):
        await net.start("two_lans")
        await net.ping("h1", "141.23.57.10")  # h3, on LAN B
        gw = (await net.wait_for(ev("arp", node="h1", op="pending"), "ARP from h1"))[0]
        assert gw["ip"] == "141.23.56.1", "h1 must ARP its gateway, not the far host"
        await net.wait_for(ev("ip", node="r1", op="forward", dst="141.23.57.10"), "r1 forwards")
        await net.wait_for(ev("arp", node="r1", iface=1, op="pending", ip="141.23.57.10"), "r1 ARPs h3 on LAN B")
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "echo reply delivered to h1")
        assert not [e for e in net.events if e["ev"] == "arp" and e["node"] == "h1" and e["ip"] == "141.23.57.10"]

    run(body)


def test_s8_proxy_arp_answers_for_h3_and_echo_round_trips():
    async def body(net):
        await net.start("proxy_arp")
        await net.ping("h1", "141.23.56.21")  # h3, behind r1
        await net.wait_for(ev("arp", node="h1", op="pending", ip="141.23.56.21"), "h1 ARPs .21 directly")
        await net.wait_for(ev("arp", node="r1", op="proxy_reply", ip="141.23.56.21", mac="02:00:00:00:03:00"),
                           "r1 proxy reply for .21")
        await net.wait_for(ev("arp", node="h1", op="resolved", ip="141.23.56.21", mac="02:00:00:00:03:00"),
                           "h1 caches .21 -> r1's MAC")
        await net.wait_for(ev("ip", node="h3", op="deliver", icmp="echo_req"), "echo request delivered to h3")
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "echo reply delivered to h1")
        lan_a_requests = [e for e in net.events
                          if arp_tx(e, "request") and e["node"] in ("h1", "h2", "r1") and e["iface"] == 0]
        assert len(lan_a_requests) == 1 and lan_a_requests[0]["node"] == "h1", \
            "only h1's own request may appear on LAN A"

    run(body)


def test_s9_three_lans_static_routes_and_ttl_62_on_delivery():
    async def body(net):
        await net.start("three_lans")
        await net.ping("h1", "141.23.58.10")  # h3, on LAN C
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "echo reply delivered to h1",
                           timeout=8)
        await net.wait_for(ev("ip", node="r1", op="forward", icmp="echo_req"), "r1 forwards")
        await net.wait_for(ev("ip", node="r2", op="forward", icmp="echo_req"), "r2 forwards")
        last_hop = (await net.wait_for(lambda e: icmp_tx("echo_req")(e) and e["node"] == "r2"
                                       and e["iface"] == 1, "r2 sends echo request on LAN C"))[0]
        ttl = decode(last_hop["hex"])["ip"]["ttl"]
        assert ttl == 62, f"delivered request has TTL {ttl}, expected 62"
        await net.wait_for(ev("rx", node="h3", uid=last_hop["uid"], verdict="accepted"), "h3 accepts the request")

    run(body)


def test_s10_speed_zero_freezes_traffic_and_speed_one_resumes():
    async def body(net):
        await net.start("single_bus", speed=10)
        await net.send("ping", node="h1", dst="141.23.56.11", count=20)  # 20 sim-seconds of pings
        await net.wait_for(ev("ip", node="h1", op="deliver", icmp="echo_rep"), "first echo reply")
        await net.send("pause")
        await asyncio.sleep(0.2)  # let frames already in flight settle
        mark = len(net.events)
        await asyncio.sleep(1.0)
        assert not [e for e in net.events[mark:] if e["ev"] == "rx"], "rx events while paused"
        await net.send("set_speed", speed=1)
        await net.send("resume")
        await net.wait_for(ev("rx"), "traffic after resume", start=mark)

    run(body)
