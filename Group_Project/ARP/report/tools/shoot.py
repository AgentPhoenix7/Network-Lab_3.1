"""Take the report's UI screenshots: proxy_arp, h1 pings 141.23.56.21 (the worked example).

Run from the project root:  .venv/bin/python report/tools/shoot.py
It starts its own bridge (own HTTP, telemetry and channel ports), drives headless Chromium through one run,
then replays the recorded run with the timeline scrubber so each stage is captured at an exact moment.
It stops only the processes it started.
"""
import json
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "report" / "tools" / "_out"
FIG = ROOT / "report" / "figures"
HTTP_PORT, TEL_PORT, CHAN_BASE = 8770, 9771, 9810  # channel for LAN k listens on CHAN_BASE + k
WIDE = {"width": 1120, "height": 580}   # stage shots: panels hidden, graph as large as possible
TALL = {"width": 1440, "height": 1000}  # log and inspector shots
PRESET, SRC, DST = "proxy_arp", "h1", "141.23.56.21"
DELAY_MS = 100  # same line delay as the worked example; the bridge default of 1000 ms makes h1 retry its ARP request first
SPEED = 0.5  # low, so a live run is easy to follow; the shots come from the replay scrubber anyway
URL = f"http://127.0.0.1:{HTTP_PORT}/"
LAUNCH = (
    "import sys, uvicorn\n"
    "from bridge import confgen\n"
    "from bridge.server import create_app\n"
    "confgen.BASE_PORT = int(sys.argv[1])\n"
    "uvicorn.run(create_app(tel_port=int(sys.argv[2])), host='127.0.0.1', port=int(sys.argv[3]), log_level='warning')\n"
)


def port_free(p: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", p)) != 0


def start_bridge() -> subprocess.Popen:
    for p in (HTTP_PORT, TEL_PORT, CHAN_BASE, CHAN_BASE + 1):
        if not port_free(p):
            sys.exit(f"port {p} is busy; change the constants at the top of shoot.py")
    proc = subprocess.Popen([sys.executable, "-c", LAUNCH, str(CHAN_BASE), str(TEL_PORT), str(HTTP_PORT)], cwd=ROOT)
    end = time.monotonic() + 15
    while True:
        try:
            urllib.request.urlopen(URL, timeout=1).read()
            return proc
        except OSError:
            if time.monotonic() > end:
                proc.kill()
                sys.exit("bridge did not start")
            time.sleep(0.2)


def stop_bridge(proc: subprocess.Popen) -> None:
    proc.terminate()  # uvicorn shutdown makes the bridge send QUIT to every C process
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    left = subprocess.run(["pgrep", "-f", f"{ROOT}/(channel|station) .*--tel-port {TEL_PORT}"], capture_output=True)
    print("C processes left:", "none" if left.returncode else left.stdout.decode().split())


def scrub_to(page, t_ms: float, end_ms: float) -> None:
    """Move the replay cursor to t_ms (the slider has 1000 steps over the whole run)."""
    page.evaluate("""([v]) => { const s = document.getElementById('scrub'); s.value = v;
        s.dispatchEvent(new Event('input', { bubbles: true })); }""", [round(t_ms / end_ms * 1000)])
    page.wait_for_timeout(250)


def shot(page, name: str, clip=None) -> None:
    page.screenshot(path=str(OUT / name), clip=clip)
    print("saved", name)


def find_stages(ev: list) -> dict:
    """Pick event times for each stage from the recorded run (tx + medium share a uid)."""
    tx = {e["uid"]: e for e in ev if e["ev"] == "tx"}
    med = [e for e in ev if e["ev"] == "medium"]
    kind = lambda m: (tx[m["uid"]].get("decoded") or {}).get("arp", {}).get("oper") if m["uid"] in tx else None
    arp_media = [m for m in med if kind(m)]
    req_a = next(m for m in arp_media if m["lan"] == 0 and kind(m) == "request")
    rep_a = next(m for m in arp_media if m["lan"] == 0 and kind(m) == "reply" and m["from"] == "r1")
    req_b = next(m for m in arp_media if m["lan"] == 1 and kind(m) == "request")
    echo_b = next(m for m in med if m["lan"] == 1 and m["uid"] in tx and "icmp" in tx[m["uid"]]["decoded"]
                  and tx[m["uid"]]["decoded"]["icmp"]["type"] == "echo_req")
    mid = lambda m: m["t"] + m["delay_ms"] * 0.5
    return {"req_a": mid(req_a), "rep_a": mid(rep_a), "req_b": mid(req_b), "echo_b": mid(echo_b), "rep_uid": rep_a["uid"]}


# The bridge lays this preset out as a tall column, which prints small. The browser gets the same topology with
# new x/y only (a left-to-right row); nothing else about the page or the run changes.
ROW = {"LAN A": (0, 0), "LAN B": (560, 0), "r1": (320, 0), "h1": (-55, 80), "h2": (55, 80), "h3": (500, 100), "h4": (620, 100)}


def relayout(raw):
    msg = json.loads(raw)
    if msg.get("type") != "topology":
        return raw
    for item in msg["topology"]["lans"] + msg["topology"]["nodes"]:
        item["x"], item["y"] = ROW[item.get("name", item.get("id"))]
    return json.dumps(msg)


def route_ws(ws) -> None:
    server = ws.connect_to_server()
    ws.on_message(lambda m: server.send(m))
    server.on_message(lambda m: ws.send(relayout(m)))


def main() -> None:
    from playwright.sync_api import sync_playwright  # imported here so --finalize runs without Playwright

    OUT.mkdir(parents=True, exist_ok=True)
    proc = start_bridge()
    events: list = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport=WIDE, device_scale_factor=2)
            ctx.add_init_script("localStorage.setItem('arp.panels', "
                                "JSON.stringify({logHidden: true, inspHidden: true, inspW: 420, logW: 300}))")
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route_web_socket(re.compile(r".*/ws$"), route_ws)

            def on_ws(ws):
                ws.on("framereceived", lambda payload: events.append(json.loads(payload)["event"])
                      if json.loads(payload).get("type") == "event" else None)

            page.on("websocket", on_ws)
            page.goto(URL)
            page.wait_for_function("window.__cy !== undefined")
            page.select_option("#preset", PRESET)
            page.wait_for_function("window.__cy.nodes().length > 4")
            page.wait_for_timeout(600)
            wrap = page.locator(".canvas-wrap").bounding_box()
            band = {"x": 0, "y": 0, "width": WIDE["width"], "height": wrap["y"] + wrap["height"]}
            shot(page, "01_topology.png", band)

            page.click("#btn-start")
            page.wait_for_function("document.getElementById('st-text').textContent === 'Running'")
            page.evaluate("""([sp, d]) => {
                const s = document.getElementById('speed');
                s.value = Math.round(50 + 50 * Math.log10(sp)); s.dispatchEvent(new Event('input', { bubbles: true }));
                const l = document.getElementById('delay');
                l.value = d; l.dispatchEvent(new Event('input', { bubbles: true })); }""", [SPEED, DELAY_MS])
            page.wait_for_timeout(600)
            n_before = len(events)
            # same ping as the worked example, from the node panel's Ping button
            page.click("#btn-insp")
            page.evaluate(f"window.__cy.getElementById('{SRC}').emit('tap')")
            page.select_option("#ping-dst", DST)
            page.click("[data-act=ping]")
            for _ in range(240):  # up to 60 s: wait for the echo reply to reach h1
                if any(e["ev"] == "ip" and e.get("op") == "deliver" and e.get("icmp") == "echo_rep" for e in events):
                    break
                page.wait_for_timeout(250)
            else:
                raise RuntimeError("ping never completed")
            page.wait_for_timeout(1500)
            page.click("#btn-insp")  # hide the inspector again for the wide shots
            assert len(events) > n_before, "no events seen"

            stages = find_stages(events)
            end_ms = events[-1]["t"]
            print("stages", stages, "end", end_ms, "events", len(events))
            page.click("#tl-replay")  # replay mode: markers come from the cursor, so each stage is exact
            page.wait_for_timeout(300)
            for _ in range(2):  # refit: the dock grew a row in replay mode, so the canvas shrank
                page.evaluate("window.dispatchEvent(new Event('resize'))")
                page.wait_for_timeout(500)
            wrap = page.locator(".canvas-wrap").bounding_box()
            canvas = {"x": 0, "y": wrap["y"], "width": WIDE["width"], "height": wrap["height"]}
            for key, name in [("req_a", "02_req_lanA.png"), ("rep_a", "03_proxy_reply.png"),
                              ("req_b", "04_req_lanB.png"), ("echo_b", "04b_echo_lanB.png")]:
                scrub_to(page, stages[key], end_ms)
                shot(page, name, canvas)

            page.set_viewport_size(TALL)
            page.click("#btn-log")
            page.click("#btn-insp")
            page.wait_for_timeout(500)
            scrub_to(page, stages["rep_a"] + 140, end_ms)
            page.locator("#log").screenshot(path=str(OUT / "05a_log.png"))
            scrub_to(page, end_ms, end_ms)
            page.evaluate(f"window.__cy.getElementById('{SRC}').emit('tap')")
            page.wait_for_timeout(300)
            page.locator("#insp").screenshot(path=str(OUT / "05b_h1_cache.png"))
            page.evaluate("window.__cy.getElementById('r1').emit('tap')")
            page.wait_for_timeout(300)
            page.locator("#insp").screenshot(path=str(OUT / "05c_r1_proxy.png"))
            page.evaluate(f"window.__cy.getElementById('{SRC}:0').emit('tap')")
            page.wait_for_timeout(300)
            page.click(f"[data-act=packet][data-uid='{stages['rep_uid']}']")
            page.wait_for_timeout(300)
            page.locator("#insp").screenshot(path=str(OUT / "06_packet.png"))
            shot(page, "07_full.png")
            if errors:
                print("page errors:", errors)
            browser.close()
    finally:
        stop_bridge(proc)

FULL = {"width": 1500, "height": 860}  # the whole window: log, graph, inspector, controls and timeline


def main_full() -> None:
    """One full-window shot of the finished run, for the interface section: python report/tools/shoot.py --full"""
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    proc = start_bridge()
    events: list = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport=FULL, device_scale_factor=1.5)
            ctx.add_init_script("localStorage.setItem('arp.panels', "
                                "JSON.stringify({logHidden: false, inspHidden: false, inspW: 420, logW: 300}))")
            page = ctx.new_page()
            page.route_web_socket(re.compile(r".*/ws$"), route_ws)
            page.on("websocket", lambda ws: ws.on("framereceived", lambda payload: events.append(json.loads(payload)["event"])
                    if json.loads(payload).get("type") == "event" else None))
            page.goto(URL)
            page.wait_for_function("window.__cy !== undefined")
            page.select_option("#preset", PRESET)
            page.wait_for_function("window.__cy.nodes().length > 4")
            page.click("#btn-start")
            page.wait_for_function("document.getElementById('st-text').textContent === 'Running'")
            page.evaluate("""([d]) => { const l = document.getElementById('delay');
                l.value = d; l.dispatchEvent(new Event('input', { bubbles: true })); }""", [DELAY_MS])
            page.wait_for_timeout(600)
            page.evaluate(f"window.__cy.getElementById('{SRC}').emit('tap')")
            page.select_option("#ping-dst", DST)
            page.click("[data-act=ping]")
            for _ in range(240):
                if any(e["ev"] == "ip" and e.get("op") == "deliver" and e.get("icmp") == "echo_rep" for e in events):
                    break
                page.wait_for_timeout(250)
            else:
                raise RuntimeError("ping never completed")
            page.wait_for_timeout(1500)
            page.evaluate(f"window.__cy.getElementById('{SRC}').emit('tap')")  # node panel: h1's ARP cache
            for _ in range(2):
                page.evaluate("window.dispatchEvent(new Event('resize'))")
                page.wait_for_timeout(400)
            page.evaluate("window.__cy.fit(undefined, 30)")
            page.wait_for_timeout(500)
            shot(page, "08_full_window.png")
            browser.close()
    finally:
        stop_bridge(proc)


def finalize() -> None:
    """Crop the raw shots in _out/ to the report figures. Needs Pillow, which the venv lacks: run this with a
    Python that has it, e.g. `python3 report/tools/shoot.py --finalize`. Row numbers are device pixels (scale 2)."""
    from PIL import Image

    def rows(name, *spans, right=None):
        im = Image.open(OUT / name).convert("RGB")
        w = right or im.width  # keep only the left `right` pixels: LAN A, r1 and the legend
        parts = [im.crop((0, a, w, b)) for a, b in spans]
        out = Image.new("RGB", (w, sum(p.height for p in parts)), "white")
        y = 0
        for part in parts:
            out.paste(part, (0, y))
            y += part.height
        return out

    def save(img, name):
        img.save(FIG / name, optimize=True)
        print("figure", name, img.size, (FIG / name).stat().st_size // 1024, "KB")

    save(rows("01_topology.png", (0, 105), (400, 830)), "ui_01_topology.png")
    save(rows("02_req_lanA.png", (40, 420), right=1560), "ui_02_request_lanA.png")
    save(rows("03_proxy_reply.png", (40, 420), (760, 840), right=1560), "ui_03_proxy_reply.png")
    save(rows("05a_log.png", (90, 1000)), "ui_05_log.png")
    save(rows("05b_h1_cache.png", (110, 195), (415, 680)), "ui_05_cache.png")
    save(rows("06_packet.png", (550, 1145)), "ui_06_arp_reply.png")
    if (OUT / "08_full_window.png").exists():
        full = Image.open(OUT / "08_full_window.png").convert("RGB")
        save(full.resize((1800, round(full.height * 1800 / full.width)), Image.LANCZOS), "ui_full.png")


if __name__ == "__main__":
    finalize() if "--finalize" in sys.argv else main_full() if "--full" in sys.argv else main()
