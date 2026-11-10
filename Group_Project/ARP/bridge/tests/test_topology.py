"""B1: validate() catches each rule; auto_fill() fills MACs and gateways. Presets must validate."""
import copy
import json
from pathlib import Path

import pytest

from bridge.topology import auto_fill, validate

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def base():
    """Valid: h1, h2 on bus LAN A; r1 joins A and star LAN B; h3 on B with gateway r1."""
    return {
        "lans": [{"id": 0, "name": "A", "mode": "bus", "cidr": "10.0.1.0/24"},
                 {"id": 1, "name": "B", "mode": "star", "cidr": "10.0.2.0/24"}],
        "nodes": [
            {"id": "h1", "kind": "host", "gateway": None,
             "ifaces": [{"idx": 0, "lan": 0, "ip": "10.0.1.10", "prefix": 24, "mac": "02:00:00:00:01:00"}]},
            {"id": "h2", "kind": "host", "gateway": None,
             "ifaces": [{"idx": 0, "lan": 0, "ip": "10.0.1.11", "prefix": 24, "mac": "02:00:00:00:02:00"}]},
            {"id": "r1", "kind": "router", "routes": [],
             "ifaces": [{"idx": 0, "lan": 0, "ip": "10.0.1.1", "prefix": 24, "mac": "02:00:00:00:03:00"},
                        {"idx": 1, "lan": 1, "ip": "10.0.2.1", "prefix": 24, "mac": "02:00:00:00:03:01"}]},
            {"id": "h3", "kind": "host", "gateway": "10.0.2.1",
             "ifaces": [{"idx": 0, "lan": 1, "ip": "10.0.2.10", "prefix": 24, "mac": "02:00:00:00:04:00"}]},
        ],
    }


def rules(errors):
    return [e["msg"] for e in errors]


def test_base_topology_is_valid():
    assert validate(base()) == []


def test_duplicate_node_id():
    t = base()
    t["nodes"][1]["id"] = "h1"
    assert any("duplicate node id" in m for m in rules(validate(t)))


def test_node_id_longer_than_seven_chars():
    t = base()
    t["nodes"][0]["id"] = "host-one"
    errs = validate(t)
    assert any(e["path"] == "nodes.host-one.id" and "longer" in e["msg"] for e in errs)


def test_duplicate_ip():
    t = base()
    t["nodes"][1]["ifaces"][0]["ip"] = "10.0.1.10"
    errs = validate(t)
    assert any(e["path"] == "nodes.h2.ifaces[0].ip" and "duplicate IP" in e["msg"] for e in errs)


def test_duplicate_mac():
    t = base()
    t["nodes"][1]["ifaces"][0]["mac"] = "02:00:00:00:01:00"
    assert any("duplicate MAC" in m for m in rules(validate(t)))


def test_ip_outside_lan_cidr():
    t = base()
    t["nodes"][1]["ifaces"][0]["ip"] = "10.0.9.11"
    errs = validate(t)
    assert any(e["path"] == "nodes.h2.ifaces[0].ip" and "outside" in e["msg"] for e in errs)


def test_router_ifaces_on_same_lan():
    t = base()
    t["nodes"][2]["ifaces"][1]["lan"] = 0
    t["nodes"][2]["ifaces"][1]["ip"] = "10.0.1.2"
    assert any("two interfaces on the same LAN" in m for m in rules(validate(t)))


def test_gateway_not_on_link():
    t = base()
    t["nodes"][3]["gateway"] = "10.0.7.1"
    errs = validate(t)
    assert any(e["path"] == "nodes.h3.gateway" and "not on a directly connected LAN" in e["msg"] for e in errs)


def test_graph_not_connected():
    t = base()
    t["lans"].append({"id": 2, "name": "C", "mode": "bus", "cidr": "10.0.3.0/24"})
    assert any("not connected" in m for m in rules(validate(t)))


def test_unknown_lan_reference():
    t = base()
    t["nodes"][0]["ifaces"][0]["lan"] = 5
    assert any(e["path"] == "nodes.h1.ifaces[0].lan" for e in validate(t))


def test_auto_fill_assigns_macs_and_host_gateway():
    t = base()
    for node in t["nodes"]:
        for iface in node["ifaces"]:
            iface.pop("mac")
    t["nodes"][0]["gateway"] = None
    filled = auto_fill(t)
    assert filled["nodes"][0]["ifaces"][0]["mac"] == "02:00:00:00:01:00"
    assert filled["nodes"][2]["ifaces"][1]["mac"] == "02:00:00:00:03:01"
    assert filled["nodes"][0]["gateway"] == "10.0.1.1"
    assert filled["nodes"][1]["gateway"] == "10.0.1.1"
    assert validate(filled) == []
    assert "mac" not in t["nodes"][0]["ifaces"][0]  # the input is not modified


@pytest.mark.parametrize("name", ["single_bus", "single_star", "two_lans", "proxy_arp", "three_lans"])
def test_presets_validate(name):
    topo = json.loads((SCENARIOS / f"{name}.json").read_text())
    assert validate(topo) == []
    assert validate(auto_fill(copy.deepcopy(topo))) == []
