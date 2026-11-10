"""Frontend checks F1-F4 (plan §7, M11): the web/ page in a real browser (Playwright, headless Chromium).

F1 runs the page on ?mock=1 with no backend. F2-F4 start the real bridge (uvicorn + create_app) in a subprocess
on HTTP port 8010 with telemetry port 9710. Channel ports come from a free block (confgen.BASE_PORT is patched in
that subprocess, the same as test_e2e does in-process), so the run never collides with a project already running.
Every test closes its page, and the module fixture stops the bridge, which sends QUIT to every C process.
"""
import json
import random
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
HTTP_PORT, TEL_PORT = 8010, 9710
BASE = f"http://127.0.0.1:{HTTP_PORT}/"
SHOT = ROOT / "runs" / "ui_proxy_arp.png"
# Start the real bridge on the given channel base, telemetry and HTTP ports (argv: base tel http).
LAUNCH = (
    "import sys, uvicorn\n"
    "from bridge import confgen\n"
    "from bridge.server import create_app\n"
    "confgen.BASE_PORT = int(sys.argv[1])\n"
    "uvicorn.run(create_app(tel_port=int(sys.argv[2])), host='127.0.0.1', port=int(sys.argv[3]), log_level='warning')\n"
)


def free_block(n: int = 3) -> int:
    """First base port of a block of n free consecutive ports (channel for LAN k = base + k)."""
    for _ in range(100):
        base = random.randint(20000, 60000)
        socks = []
        try:
            for k in range(n):
                s = socket.socket()
                socks.append(s)
                s.bind(("127.0.0.1", base + k))
            return base
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("no free port block for the channels")


def wait_http(url: str, timeout: float) -> None:
    end = time.monotonic() + timeout
    while True:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return
        except OSError:
            if time.monotonic() > end:
                raise
            time.sleep(0.1)


def no_c_processes() -> bool:
    """True when no channel or station started by this test (telemetry port 9710) is still running."""
    out = subprocess.run(["pgrep", "-f", str(ROOT) + "/(channel|station) .*--tel-port 9710"], capture_output=True, text=True)
    return out.returncode != 0


@pytest.fixture(scope="module")
def live_bridge():
    """The real bridge in a subprocess, stopped (SIGTERM, then the lifespan sends QUIT) whatever happens."""
    base = free_block()
    proc = subprocess.Popen([sys.executable, "-c", LAUNCH, str(base), str(TEL_PORT), str(HTTP_PORT)], cwd=ROOT)
    try:
        wait_http(BASE, 15)
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        assert no_c_processes(), "a channel or station outlived the bridge"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: pg.errors.append(f"console: {m.text}") if m.type == "error" else None)
    yield pg
    ctx.close()


def node_click(page, node_id: str) -> None:
    """Click a Cytoscape node at its rendered position, as a user would."""
    box = page.locator("#cy").bounding_box()
    x, y = page.evaluate("id => { const p = window.__cy.getElementById(id).renderedPosition(); return [p.x, p.y]; }", node_id)
    page.mouse.click(box["x"] + x, box["y"] + y)


def open_page(page, path: str = "") -> None:
    """Load the page and record what it draws: the full log text and every proxy marker added to the overlay.

    The log drawer keeps only the newest 40 rows, so a proxy reply can scroll out of view before the test looks.
    """
    page.goto(BASE + path)
    page.evaluate("""() => {
        window.__seen = { proxy: 0, log: '' };
        new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
            if (n.classList && n.classList.contains('proxy')) window.__seen.proxy++; })))
            .observe(document.getElementById('overlay'), { childList: true });
        new MutationObserver(() => { window.__seen.log += document.getElementById('log-list').textContent; })
            .observe(document.getElementById('log-list'), { childList: true, subtree: true, characterData: true });
    }""")


def proxy_ping(page) -> None:
    """Choose proxy_arp, Start, and ping h1 -> 141.23.56.21 from the inspector."""
    open_page(page)
    expect(page.locator("#banner")).to_be_hidden()
    expect(page.locator("#st-text")).to_have_text("Stopped")  # no run left over from an earlier test
    page.select_option("#preset", "single_bus")
    page.select_option("#preset", "proxy_arp")
    page.wait_for_function("window.__cy && window.__cy.nodes().length === 7")
    page.click("#btn-start")
    node_click(page, "h1")
    page.select_option("#ping-dst", "141.23.56.21")
    page.click('[data-act="ping"][data-node="h1"]')


def stop_run(page) -> None:
    page.click("#btn-stop")
    expect(page.locator("#st-text")).to_have_text("Stopped")


def test_f1_mock_page_renders_and_plays_run(page, live_bridge):
    open_page(page, "?mock=1")
    page.wait_for_function("window.__cy && window.__cy.nodes().length === 7")
    page.click("#btn-start")
    page.wait_for_function("window.__seen.log.includes('proxy_reply') && window.__seen.log.includes('h1 deliver reply')",
                           timeout=10000)
    assert page.evaluate("window.__seen.proxy") >= 1
    assert page.errors == []


def test_f2_live_proxy_arp_ping(page, live_bridge):
    try:
        proxy_ping(page)
        page.wait_for_function("window.__seen.log.includes('proxy_reply')", timeout=15000)
        page.wait_for_function("window.__seen.log.includes('h1 deliver reply')", timeout=15000)
        # h1's ARP cache row for 141.23.56.21 must show r1's MAC (the proxy reply).
        expect(page.locator("#insp-body tr", has_text="141.23.56.21")).to_contain_text("02:00:00:00:03:00", timeout=5000)
        # The marker comes with the medium event, which can trail the log rows by a few ms.
        page.wait_for_function("window.__seen.proxy >= 1", timeout=5000)
        page.screenshot(path=str(SHOT))
        assert page.errors == []
    finally:
        if page.locator("#btn-stop").is_visible():
            stop_run(page)


def test_f3_slider_sends_debounced_set_impair(page, live_bridge):
    sent = []
    page.on("websocket", lambda ws: ws.on("framesent", lambda p: sent.append(json.loads(p)) if isinstance(p, str) else None))
    open_page(page)
    expect(page.locator("#banner")).to_be_hidden()
    expect(page.locator("#st-text")).to_have_text("Stopped")
    sent.clear()
    # 21 input events in one tick, the way a fast drag produces them.
    page.evaluate("""() => { const el = document.getElementById('loss');
        for (let v = 0; v <= 20; v++) { el.value = v; el.dispatchEvent(new Event('input')); } }""")
    expect(page.locator("#loss-txt")).to_have_text("20%", timeout=3000)
    page.wait_for_timeout(400)
    impairs = [m for m in sent if m.get("cmd") == "set_impair"]
    # Throttle (contract §6, at most one command per 150 ms): the first value goes at once, the last one trails.
    assert 1 <= len(impairs) <= 2, impairs
    assert impairs[-1]["loss"] == pytest.approx(0.2), impairs
    expect(page.locator("#loss")).to_have_value("20")  # the bridge's state echo agrees with the slider
    assert page.errors == []


def test_f4_export_then_open_replays_same_events(page, live_bridge, tmp_path):
    proxy_ping(page)
    page.wait_for_function("window.__seen.log.includes('h1 deliver reply')", timeout=15000)
    stop_run(page)
    with page.expect_download() as dl:
        page.click("#btn-export")
    path = tmp_path / "run.jsonl"
    dl.value.save_as(str(path))
    n = len([line for line in path.read_text().splitlines() if line.strip()])

    open_page(page)  # a fresh page gets the bridge's copy of the same run through get_log
    expect(page.locator("#log-count")).to_have_text(f"{n} events")
    page.set_input_files("#file-open", str(path))
    expect(page.locator("#mode-replay")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#log-count")).to_have_text(f"{n} events")
    page.click("#tl-play")
    expect(page.locator("#scrub")).to_have_value("1000", timeout=15000)
    assert page.errors == []
