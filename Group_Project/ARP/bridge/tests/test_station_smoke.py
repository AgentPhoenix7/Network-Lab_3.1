"""ST2 (plan 5.6, M8): smoke test of the station in stdio mode.

One bus channel and two hosts. PING 10.0.0.2 from h1 must show ARP pending, ARP resolved,
and an echo reply delivered to h1, all within 5 s. Ports are moved to 19650 so the test does not
collide with a running project."""
import json
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORT = 19650
TIMEOUT_S = 5.0


def read_events(path):
    """All complete JSON lines written so far. A line still being written is skipped."""
    events = []
    for line in path.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events


def wait_for(path, done, timeout=TIMEOUT_S):
    """Polls the output file until done(events) holds or the timeout passes. Returns the last events."""
    end = time.time() + timeout
    while True:
        events = read_events(path)
        if done(events) or time.time() > end:
            return events
        time.sleep(0.05)


def has(events, **want):
    return any(all(e.get(k) == v for k, v in want.items()) for e in events)


def scenario_conf(tmp_path, name):
    text = (ROOT / "scenarios" / "manual" / f"{name}.conf").read_text()
    path = tmp_path / f"{name}.conf"
    path.write_text(text.replace("port 9600", f"port {PORT}"))
    return path


def test_ping_resolves_and_delivers(tmp_path):
    subprocess.run(["make", "channel", "station"], cwd=ROOT, check=True, capture_output=True)
    procs = []

    def start(argv, out, stdin=subprocess.DEVNULL):
        handle = open(out, "w")
        proc = subprocess.Popen(argv, cwd=ROOT, stdin=stdin, stdout=handle, stderr=subprocess.STDOUT)
        procs.append(proc)
        return proc

    try:
        ch_out = tmp_path / "channel.out"
        start(["./channel", "--lan", "0", "--port", str(PORT), "--mode", "bus", "--tel-port", "0"], ch_out)
        assert wait_for(ch_out, lambda ev: has(ev, ev="ready")), "channel never sent ready"

        h1_out, h2_out = tmp_path / "h1.out", tmp_path / "h2.out"
        h1 = start(["./station", "--conf", str(scenario_conf(tmp_path, "h1")), "--tel-port", "0"],
                   h1_out, stdin=subprocess.PIPE)
        start(["./station", "--conf", str(scenario_conf(tmp_path, "h2")), "--tel-port", "0"], h2_out)
        assert wait_for(h1_out, lambda ev: has(ev, ev="ready", node="h1")), "h1 never sent ready"
        assert wait_for(h2_out, lambda ev: has(ev, ev="ready", node="h2")), "h2 never sent ready"

        h1.stdin.write(b"PING 10.0.0.2 1\n")
        h1.stdin.flush()
        events = wait_for(h1_out, lambda ev: has(ev, ev="ip", op="deliver", icmp="echo_rep"))

        assert has(events, ev="arp", node="h1", op="pending", ip="10.0.0.2"), "no ARP request on h1"
        assert has(events, ev="arp", node="h1", op="resolved", ip="10.0.0.2"), "ARP never resolved on h1"
        assert has(events, ev="ip", node="h1", op="deliver", icmp="echo_rep"), "no echo reply delivered to h1"
    finally:
        for proc in procs:
            proc.kill()
            proc.wait()
