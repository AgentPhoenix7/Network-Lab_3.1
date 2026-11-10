"""Topology model (contract §3.3): plain JSON-shaped dicts, validation (B1) and auto-fill.

validate() never raises on bad input: it returns a list of {path, msg}, empty when usable.
"""
import copy
import ipaddress

MAX_NODE_ID = 7


def _in_cidr(ip, cidr) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
    except (TypeError, ValueError):
        return False


def _connected(topo) -> bool:
    # Walk the LAN/station bipartite graph; every node and every LAN must be reached.
    nodes = {n.get("id"): [i.get("lan") for i in n.get("ifaces", [])] for n in topo.get("nodes", [])}
    attached = {}
    for nid, lans in nodes.items():
        for lid in lans:
            attached.setdefault(lid, []).append(nid)
    start = ("node", next(iter(nodes)))
    seen, todo = {start}, [start]
    while todo:
        kind, key = todo.pop()
        if kind == "node":
            nbrs = [("lan", lid) for lid in nodes[key]]
        else:
            nbrs = [("node", nid) for nid in attached.get(key, [])]
        for nb in nbrs:
            if nb not in seen:
                seen.add(nb)
                todo.append(nb)
    expected = {("node", n) for n in nodes} | {("lan", lan["id"]) for lan in topo.get("lans", [])}
    return seen.issuperset(expected)


def validate(topo) -> list[dict]:
    """Return {path, msg} errors; an empty list means the topology is valid."""
    errors = []

    def err(path, msg):
        errors.append({"path": path, "msg": msg})

    lans = {lan["id"]: lan for lan in topo.get("lans", [])}
    nodes = topo.get("nodes", [])
    if not nodes:
        err("nodes", "no nodes")
    ids, ips, macs = set(), set(), set()
    for node in nodes:
        nid = node.get("id", "")
        base = f"nodes.{nid}"
        if nid in ids:
            err(f"{base}.id", f"duplicate node id {nid}")
        ids.add(nid)
        if len(nid) > MAX_NODE_ID:
            err(f"{base}.id", f"node id longer than {MAX_NODE_ID} characters")
        on_lans = []
        for i, iface in enumerate(node.get("ifaces", [])):
            path, lid, ip, mac = f"{base}.ifaces[{i}]", iface.get("lan"), iface.get("ip"), iface.get("mac")
            if lid not in lans:
                err(f"{path}.lan", f"unknown LAN {lid}")
            elif not _in_cidr(ip, lans[lid]["cidr"]):
                err(f"{path}.ip", f"{ip} is outside {lans[lid]['cidr']}")
            if node.get("kind") == "router" and lid in on_lans:
                err(f"{path}.lan", f"router has two interfaces on the same LAN {lid}")
            on_lans.append(lid)
            if ip in ips:
                err(f"{path}.ip", f"duplicate IP {ip}")
            ips.add(ip)
            if mac and mac in macs:
                err(f"{path}.mac", f"duplicate MAC {mac}")
            if mac:
                macs.add(mac)
        gw = node.get("gateway")
        if gw and not any(_in_cidr(gw, lans[i["lan"]]["cidr"]) for i in node.get("ifaces", []) if i.get("lan") in lans):
            err(f"{base}.gateway", f"gateway {gw} is not on a directly connected LAN")
    if nodes and not _connected(topo):
        err("nodes", "topology graph is not connected")
    return errors


def auto_fill(topo) -> dict:
    """Return a copy with missing MACs (02:00:00:00:NN:II, NN = 1-based node position) and
    missing host gateways (the first router interface on one of the host's LANs) filled in."""
    topo = copy.deepcopy(topo)
    for n_idx, node in enumerate(topo.get("nodes", []), start=1):
        for iface in node.get("ifaces", []):
            if not iface.get("mac"):
                iface["mac"] = f"02:00:00:00:{n_idx:02x}:{iface['idx']:02x}"
    routers = [n for n in topo.get("nodes", []) if n.get("kind") == "router"]
    for node in topo.get("nodes", []):
        if node.get("kind") != "host" or node.get("gateway"):
            continue
        for iface in node.get("ifaces", []):
            hop = next((ri["ip"] for r in routers for ri in r["ifaces"] if ri["lan"] == iface["lan"]), None)
            if hop:
                node["gateway"] = hop
                break
    return topo
