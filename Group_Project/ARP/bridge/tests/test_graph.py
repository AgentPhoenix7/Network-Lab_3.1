"""B2: layout is deterministic for a seed and places hosts on their LAN's shape. B3: static routes on three_lans."""
import json
import math
from pathlib import Path

from bridge.graph import layout, static_routes

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def preset(name):
    return json.loads((SCENARIOS / f"{name}.json").read_text())


def test_layout_is_deterministic_for_a_seed():
    assert layout(preset("proxy_arp"), seed=7) == layout(preset("proxy_arp"), seed=7)


def test_layout_sets_positions_for_all_nodes_and_lans():
    out = layout(preset("three_lans"), seed=1)
    assert all("x" in lan and "y" in lan for lan in out["lans"])
    assert all("x" in node and "y" in node for node in out["nodes"])


def test_bus_hosts_share_a_horizontal_line_under_their_lan():
    out = layout(preset("proxy_arp"), seed=1)
    lan_a = next(lan for lan in out["lans"] if lan["id"] == 0)
    bus_hosts = [n for n in out["nodes"] if n["id"] in ("h1", "h2")]
    assert len({n["y"] for n in bus_hosts}) == 1
    assert bus_hosts[0]["y"] > lan_a["y"]


def test_star_hosts_sit_on_a_circle_around_their_lan():
    out = layout(preset("proxy_arp"), seed=1)
    lan_b = next(lan for lan in out["lans"] if lan["id"] == 1)
    radii = [math.dist((n["x"], n["y"]), (lan_b["x"], lan_b["y"]))
             for n in out["nodes"] if n["id"] in ("h3", "h4")]
    assert all(abs(r - 120) <= 2 for r in radii)


def test_static_routes_three_lans():
    routes = static_routes(preset("three_lans"))
    assert routes["r1"] == ["route 141.23.58.0/24 via 141.23.57.3"]
    assert routes["r2"] == ["route 141.23.56.0/24 via 141.23.57.2"]


def test_static_routes_none_for_direct_lans_only():
    routes = static_routes(preset("proxy_arp"))
    assert routes["r1"] == []
