# ARP Project

ARP (RFC 826) and proxy ARP in C, on a simulated multi-LAN network. Each LAN is a `channel` process (bus or star), each host or router is a `station` process. A Python bridge launches them and a web page shows every frame and ARP decision live.

## Requirements

- gcc and make
- Python 3.11 or newer
- The lab sources `lab1`, `lab2`, `lab4` and `utils` two folders above this one (`../../`). The Makefile links them; it never copies them.

## Build and test

```bash
make channel station           # the two C programs
make test                      # all C unit tests
python3 -m venv .venv && . .venv/bin/activate
pip install -r bridge/requirements.txt
pytest bridge/tests            # bridge, station smoke and end-to-end tests
```

## Run

```bash
python -m bridge.server        # then open http://localhost:8000
```

Choose a preset (18 to pick from; `proxy_arp` is the classic proxy ARP case), press Start, then Ping from `h1` to `141.23.56.21`. Add `?mock=1` to the URL to try the page with no backend.

Without the web page:

```bash
python -m bridge.headless --preset proxy_arp --ping h1 141.23.56.21 --expect echo_rep --timeout 10
```

By hand, in three terminals (stdio mode prints JSON events and reads commands on stdin):

```bash
./channel --lan 0 --port 9600 --mode bus --tel-port 0
./station --conf scenarios/manual/h1.conf --tel-port 0
./station --conf scenarios/manual/h2.conf --tel-port 0     # then type: PING 10.0.0.1 1  in h2
```

## Layout

| path | what |
|---|---|
| `src/` | C sources. Core ARP is `arp_pkt.c`, `arp_cache.c`, `arp.c` |
| `tests/` | C unit tests and golden frames |
| `bridge/` | Python bridge (FastAPI, NetworkX) and its tests |
| `scenarios/` | 18 preset topologies and a two-host manual setup |
| `web/` | the web page (plain HTML and JS, Cytoscape from a CDN) |
