"""Topology -> one station conf per node (plan §5.6) and one channel argument list per LAN (§5.5).

Routes come from graph.static_routes(); the conf lines are written in the exact format the C parser reads.
"""
from pathlib import Path

from .graph import static_routes

BASE_PORT = 9600
DEFAULT_ARP = {"ttl_ms": 30000, "retry_ms": 1000, "retries": 0}  # 0 = retry ARP forever


def channel_args(topo, lan_id, tel_port=9700, seed=None) -> list[str]:
    lan = next(x for x in topo["lans"] if x["id"] == lan_id)
    args = ["--lan", str(lan_id), "--port", str(BASE_PORT + lan_id),
            "--mode", lan["mode"], "--tel-port", str(tel_port)]
    if seed is not None:
        args += ["--seed", str(seed)]
    return args


def station_conf(node, routes, arp=DEFAULT_ARP) -> str:
    lines = [f"node {node['id']} {node['kind']}"]
    for i in node["ifaces"]:
        lines.append(f"iface {i['idx']} lan {i['lan']} port {BASE_PORT + i['lan']} "
                     f"mac {i['mac']} ip {i['ip']}/{i['prefix']}")
        lines += [f"proxy {i['idx']} {p}" for p in i.get("proxy", [])]
    lines += routes
    if node.get("gateway"):
        lines.append(f"gateway {node['gateway']}")
    lines.append(f"arp ttl_ms {arp['ttl_ms']} retry_ms {arp['retry_ms']} retries {arp['retries']}")
    return "\n".join(lines) + "\n"


def write_run(topo, run_dir, arp=DEFAULT_ARP, tel_port=9700, seed=None) -> dict:
    """Write run_dir/<node>.conf for every node; return {lan_id: channel argv tail}."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    routes = static_routes(topo)
    for node in topo["nodes"]:
        conf = station_conf(node, routes.get(node["id"], []), arp)
        (run_dir / f"{node['id']}.conf").write_text(conf)
    return {lan["id"]: channel_args(topo, lan["id"], tel_port, seed) for lan in topo["lans"]}
