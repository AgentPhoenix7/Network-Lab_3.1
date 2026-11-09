# Assignment 5: Answers

Setup: Fedora 44, Wireshark 4.6.9 from dnf, Wi-Fi card `wlp0s20f3`. Two captures live in `captures/`:
`ping.pcapng` (Q1) and `web.pcapng` (Q2-Q10). Screenshots live in `report/figures/`.

`web.pcapng` holds only my test traffic: the lookup of `info.cern.ch` and the visits to it. I dropped other programs' packets so the file shows nothing private.

## Terms used below

- **Packet**: one small chunk of data sent over a network. Wireshark lists one row per packet.
- **MAC address**: a 6-byte name burned into a network card, like `f4:1e:57:c7:11:a4`. It works only on the local link (my Wi-Fi to my router).
- **OUI**: the first 3 bytes of a MAC address. It names the company that made the card.
- **IP address**: the name that works across the whole Internet, like `188.184.67.127`.
- **ARP**: a question sent to everyone on the local link: "who owns this IP address? Tell me your MAC address."
- **ICMP / ping**: a test message. `ping` sends an echo request and waits for an echo reply.
- **TCP**: a way to send data in order, with a handshake first (SYN, SYN-ACK, ACK). Web pages use it.
- **UDP**: a way to send data with no handshake. Fast and light. Name lookups use it.
- **DNS**: the phone book that turns a name (`info.cern.ch`) into an IP address.
- **HTTP**: the language of web pages. The browser sends `GET`; the server answers `200 OK` and the page.
- **Flow**: all the packets that belong to one conversation.

## Install proof

![Wireshark welcome screen](report/figures/q0_welcome.png)
![dnf and rpm output](report/figures/q0_dnf_info.png)

dnf installed Wireshark 4.6.9 (`wireshark` and `wireshark-cli`). The welcome screen lists my Wi-Fi card `wlp0s20f3` first. Double-clicking it starts a capture.

## Q1. Ping and ARP

I cleared my computer's ARP memory, then ran `ping -c 4 192.168.1.1` (my router). All four pings got an answer, 0% lost.

![ARP and ICMP packets](report/figures/q1_arp_icmp_list.png)

**What Wireshark shows, in order:**

1. **Packet 2, ARP request.** My computer shouts to everyone: "Who has 192.168.1.1? Tell 192.168.1.55." It goes to the broadcast address `ff:ff:ff:ff:ff:ff`, so every device on the Wi-Fi hears it. It needs the router's MAC address before it can send a ping.
2. **Packet 3, ARP reply.** 11 milliseconds later the router answers me alone: "192.168.1.1 is at `f4:1e:57:c7:11:a4`."
3. **Packets 4 to 11, ICMP.** Four echo requests from me, each followed by an echo reply from the router. Wireshark pairs them: "reply in 5", "request in 4".
4. **Packets 12 and 13, ARP again.** The router asks me "Who has 192.168.1.55?" and I answer. It does this to check that I am still here.

Packet 1 is another device on the network (192.168.1.141) asking the same question. It is not mine.

![ARP request details](report/figures/q1_arp_request.png)

The details show the ARP request: sender MAC and IP are mine, the target MAC is all zeros (the unknown), the target IP is the router.

**Check against `ping`.** Wire times from the capture: 9.6, 3.0, 61.4 and 32.0 ms. `ping` printed 21.0, 3.04, 61.4 and 32.1 ms. Only the first differs. `ping` started its clock before the ARP lookup. The ARP wait of about 11 ms makes up the gap (3.2314 s to 3.2524 s is 20.9 ms).

## Q2. Web traffic

I opened `http://info.cern.ch` (the first website ever made) with `curl`. The site uses plain HTTP, so Wireshark can read the GET line. Sites on HTTPS hide it.

**a. Protocols in the Protocol column.** DNS, TCP, HTTP and TLSv1.3. TLS is the lock on HTTPS: it hides the page. My HTTPS test visit shows up as TLS.

![Unfiltered packet list](report/figures/q2a_unfiltered.png)

**b. Time from GET to 200 OK.** 0.2217 seconds for the first page (packet 6 to packet 8). The second page took 0.2608 seconds (packet 15 to packet 18). The screenshot uses time-of-day format: 16:00:03.5823 for the GET and 16:00:03.8040 for the reply. The difference matches. Wireshark's `http.time` field gives the same number.

**c. Addresses.** The website is `188.184.67.127` (DNS answer: `info.cern.ch` is a nickname for `webafs902.cern.ch`). My computer is `192.168.1.55`.

**d. The GET packet.** Packet 6 holds `GET / HTTP/1.1`. The screenshot shows the HTTP layer expanded in the Packet Details panel.

**e. Host value.** `info.cern.ch`.

![HTTP layer expanded](report/figures/q2_http_get_details.png)

## Q3. Hex and ASCII highlight

Clicking the HTTP layer in the details panel colours its bytes in the Packet Bytes panel. The left side is hex; the right side shows the same bytes as text. The text starts `GET / HTTP/1.1`.

(Same screenshot as Q2: `report/figures/q2_http_get_details.png`.)

## Q4. First 4 bytes of the Host parameter

![Host line highlighted](report/figures/q4_host_hex.png)

- The header line starts `48 6f 73 74` = `Host`.
- The value starts `69 6e 66 6f` = `info`.

I read the question as asking about the value. Both are shown, so the answer holds either way.

## Q5. Filters and Follow Stream

I typed a filter into the bar above the packet list. Wireshark hides every packet that does not match.

- `dns` shows 2 packets: the question and the answer.
- `tcp.port==80` shows the web conversations (handshakes, GETs, replies, closes).
- `http` shows 4 packets: two GETs and two `200 OK`.

![dns filter](report/figures/q5_dns.png)
![tcp filter](report/figures/q5_tcp.png)

**Follow a conversation.** Right-click a packet, then Follow, then TCP Stream. Wireshark joins the packets into one text. Red is what my computer sent (the GET). Blue is the reply: headers, then the page. The page text reads "home of the first website". The whole conversation is 954 bytes: 1 packet from me, 1 from the server.

![Follow TCP stream](report/figures/q5_follow_tcp.png)

## Q6. Packet coming back from the server

Filter: `tcp.srcport==80 && http`. Packet 8 is the first reply. In the Ethernet layer the source is the router (`f4:1e:57:c7:11:a4`) and the destination is my computer.

![Ethernet layer of a server reply](report/figures/q6_server_ethernet.png)

## Q7. NIC makers

- **My computer: Intel.** The card's built-in address is `ac:45:ef:b3:5b:e6` (`ethtool -P`). Wireshark's list maps `ac:45:ef` to Intel Corporate. `lspci` agrees: Intel Arrow Lake WiFi.
  On the air, though, Linux uses a random address, `42:b6:e6:52:57:8e`. Its "locally administered" bit is set, so no maker owns it. That is why the screenshot shows no maker name for my side.
- **The server: not visible.** The frame that reached me came from my router, made by **Routerboard.com** (MikroTik). A MAC address works on one link only. Each router builds a new frame, so the web server's own card never appears in my capture.

## Q8. OUI bytes in hex

| Device | Address in capture | OUI (hex) | Maker |
|---|---|---|---|
| My PC, built-in | `ac:45:ef:b3:5b:e6` | `ac 45 ef` | Intel Corporate |
| My PC, in use (random) | `42:b6:e6:52:57:8e` | `42 b6 e6` | none |
| Router (server side) | `f4:1e:57:c7:11:a4` | `f4 1e 57` | Routerboard.com |

In packet 8 the raw bytes begin `42 b6 e6 52 57 8e` (destination, my PC) then `f4 1e 57 c7 11 a4` (source, router). The highlighted bytes are in the Q6 screenshot.

## Q9. Statistics

Statistics, then Protocol Hierarchy:

![Protocol hierarchy](report/figures/q9_protocol_hierarchy.png)

- **a. TCP: 95.7%** (44 of 46 packets). HTTP runs on TCP.
- **b. UDP: 4.3%** (2 of 46 packets). DNS runs on UDP.

ARP and ICMP do not appear here. They are in the Q1 capture.

## Q10. Flow graph

Statistics, then Flow Graph. Flow type: All Flows. Addresses: Network.

![Flow graph](report/figures/q10_flow_graph.png)

Three machines appear as columns: my PC (`192.168.1.55`), the DNS server (`10.10.200.1`), the web server (`188.184.67.127`). Reading top to bottom:

1. My PC asks the DNS server for `info.cern.ch`. It answers.
2. TCP handshake with the web server: SYN, SYN-ACK, ACK.
3. `GET /`, then `200 OK`.
4. FIN packets close the connection. The same steps repeat for the second page.
