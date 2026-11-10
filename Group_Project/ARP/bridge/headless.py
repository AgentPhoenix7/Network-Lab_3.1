"""Headless end-to-end run (plan §6): start a preset with the real binaries, ping once, wait for the expected
`ip deliver` event, stop everything. Exit 0 when the event arrives in time, 1 otherwise.

python -m bridge.headless --preset proxy_arp --ping h1 141.23.56.21 --expect echo_rep --timeout 10
"""
import argparse
import asyncio
import sys

from . import confgen
from .server import Bridge


def parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a preset headless and check one ping.")
    p.add_argument("--preset", required=True)
    p.add_argument("--ping", nargs=2, metavar=("NODE", "DST"), required=True)
    p.add_argument("--expect", default="echo_rep", choices=["echo_req", "echo_rep"])
    p.add_argument("--base-port", type=int, default=confgen.BASE_PORT, help="channel for LAN k listens on base+k")
    p.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for the expected event")
    return p.parse_args(argv)


async def run(args: argparse.Namespace) -> bool:
    confgen.BASE_PORT = args.base_port
    bridge = Bridge(tel_port=0)  # port 0: the OS picks a free telemetry port
    hit = asyncio.Event()
    replies: list[dict] = []

    def watch(msg: dict) -> None:
        if msg.get("type") == "error":
            print(f"error: {msg['msg']}")
        ev = msg.get("event", {}) if msg.get("type") == "event" else {}
        if ev.get("ev") == "ip" and ev.get("op") == "deliver" and ev.get("icmp") == args.expect:
            hit.set()

    bridge.clients.append(watch)
    await bridge.tel.start()
    try:
        await bridge.handle({"cmd": "load_preset", "name": args.preset}, replies.append)
        await bridge.handle({"cmd": "start", "topology": bridge.topology}, replies.append)
        if not bridge.running:
            print("start failed:", *replies, sep="\n  ")
            return False
        node, dst = args.ping
        await bridge.handle({"cmd": "ping", "node": node, "dst": dst, "count": 1}, replies.append)
        try:
            await asyncio.wait_for(hit.wait(), args.timeout)
            ok = True
        except TimeoutError:
            ok = False
        print(f"run {bridge.rec.run_id}: {len(bridge.rec.events)} events; ping {node} -> {dst}; "
              f"expect ip deliver {args.expect}: {'seen' if ok else 'not seen'}")
        for msg in replies:
            if msg.get("type") == "error":
                print(f"error: {msg['msg']}")
        return ok
    finally:
        await bridge.shutdown()


def main(argv: list[str] | None = None) -> int:
    ok = asyncio.run(run(parse(argv)))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
