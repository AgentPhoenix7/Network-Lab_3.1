"""Conf files and channel argument lists follow plan §5.5 / §5.6 (proxy_arp and three_lans presets)."""
import json
from pathlib import Path

from bridge.confgen import channel_args, write_run

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def preset(name):
    return json.loads((SCENARIOS / f"{name}.json").read_text())


def test_router_conf_has_node_iface_and_proxy_lines(tmp_path):
    write_run(preset("proxy_arp"), tmp_path)
    lines = (tmp_path / "r1.conf").read_text().splitlines()
    assert lines[0] == "node r1 router"
    assert "iface 0 lan 0 port 9600 mac 02:00:00:00:03:00 ip 141.23.56.1/24" in lines
    assert "iface 1 lan 1 port 9601 mac 02:00:00:00:03:01 ip 141.23.56.17/29" in lines
    assert "proxy 0 141.23.56.21/32" in lines
    assert "proxy 0 141.23.56.23/32" in lines
    assert not any(line.startswith("gateway") for line in lines)
    assert lines[-1] == "arp ttl_ms 30000 retry_ms 1000 retries 0"


def test_host_conf_has_gateway_line(tmp_path):
    write_run(preset("proxy_arp"), tmp_path)
    assert "gateway 141.23.56.17" in (tmp_path / "h3.conf").read_text().splitlines()


def test_router_conf_has_static_route_line(tmp_path):
    write_run(preset("three_lans"), tmp_path)
    assert "route 141.23.58.0/24 via 141.23.57.3" in (tmp_path / "r1.conf").read_text().splitlines()


def test_write_run_returns_one_channel_arg_list_per_lan(tmp_path):
    args = write_run(preset("proxy_arp"), tmp_path, seed=5)
    assert args[1] == ["--lan", "1", "--port", "9601", "--mode", "star", "--tel-port", "9700", "--seed", "5"]
    assert args[0][:6] == ["--lan", "0", "--port", "9600", "--mode", "bus"]
    assert len(list(tmp_path.glob("*.conf"))) == 5


def test_channel_args_without_seed():
    assert channel_args(preset("single_bus"), 0, tel_port=9800) == [
        "--lan", "0", "--port", "9600", "--mode", "bus", "--tel-port", "9800"]
