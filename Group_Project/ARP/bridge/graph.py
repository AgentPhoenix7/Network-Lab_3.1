"""NetworkX view of a topology: LAN/station bipartite graph, layout (B2) and static routes (B3).

Only this module (and the layout it returns) uses networkx.
"""
import copy
import ipaddress
import math
from collections import defaultdict

import networkx as nx


def build_graph(topo) -> nx.Graph:
    """Bipartite graph: ("lan", id) nodes and ("node", id) nodes, one edge per interface."""
    g = nx.Graph()
    for lan in topo["lans"]:
        g.add_node(("lan", lan["id"]), bipartite=0)
    for node in topo["nodes"]:
        g.add_node(("node", node["id"]), bipartite=1, kind=node["kind"])
        for iface in node["ifaces"]:
            g.add_edge(("node", node["id"]), ("lan", iface["lan"]))
    return g


def _skeleton(g) -> nx.Graph:
    # Hosts only hang off their LAN; routing and layout use LANs and routers.
    keep = [n for n, d in g.nodes(data=True) if n[0] == "lan" or d["kind"] == "router"]
    return g.subgraph(keep)


def layout(topo, seed=0, scale=400) -> dict:
    """Return a copy of topo with x/y set: routers and LANs from Kamada-Kawai on the skeleton,
    bus hosts on a horizontal line under their LAN, star hosts on a circle around it."""
    topo = copy.deepcopy(topo)
    skel = _skeleton(build_graph(topo))
    pos = nx.kamada_kawai_layout(skel, pos=nx.random_layout(skel, seed=seed), scale=scale)
    xy = {n: (round(p[0]), round(p[1])) for n, p in pos.items()}
    for lan in topo["lans"]:
        lan["x"], lan["y"] = xy[("lan", lan["id"])]
    for node in topo["nodes"]:
        if node["kind"] == "router":
            node["x"], node["y"] = xy[("node", node["id"])]
    homes = defaultdict(list)  # a host is placed around its first LAN
    for node in topo["nodes"]:
        if node["kind"] == "host" and node["ifaces"]:
            homes[node["ifaces"][0]["lan"]].append(node)
    for lan in topo["lans"]:
        hosts = homes[lan["id"]]
        for k, host in enumerate(hosts):
            if lan["mode"] == "bus":
                host["x"] = lan["x"] + round((k - (len(hosts) - 1) / 2) * 110)
                host["y"] = lan["y"] + 80
            else:
                a = 2 * math.pi * k / len(hosts)
                host["x"] = lan["x"] + round(120 * math.cos(a))
                host["y"] = lan["y"] + round(120 * math.sin(a))
    return topo


def static_routes(topo) -> dict:
    """Router id -> ["route <net>/<prefix> via <ip>", ...] for every LAN it is not attached to.
    The next hop is the first router on the BFS shortest path over LANs and routers."""
    skel = _skeleton(build_graph(topo))
    lans = {lan["id"]: lan for lan in topo["lans"]}
    nodes = {n["id"]: n for n in topo["nodes"]}
    out = {}
    for router in (n for n in topo["nodes"] if n["kind"] == "router"):
        own = {i["lan"] for i in router["ifaces"]}
        src = ("node", router["id"])
        lines = []
        for lid, lan in lans.items():
            if lid in own or not nx.has_path(skel, src, ("lan", lid)):
                continue
            path = nx.shortest_path(skel, src, ("lan", lid))
            via_lan, next_id = path[1][1], path[2][1]
            via_ip = next(i["ip"] for i in nodes[next_id]["ifaces"] if i["lan"] == via_lan)
            net = ipaddress.ip_network(lan["cidr"], strict=False)
            lines.append(f"route {net.network_address}/{net.prefixlen} via {via_ip}")
        out[router["id"]] = lines
    return out
