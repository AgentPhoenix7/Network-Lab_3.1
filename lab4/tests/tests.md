# Assignment 4 — Test Specification

This is the single source of truth for every test. Claude implements
each suite **before** the user writes that milestone's code (TDD).
Every C suite uses `tests/test_util.h`, and no test file defines its
own assert helpers (CLAUDE.md R1). Every suite exits non-zero if any
assertion fails.

Run everything from `lab4/`:
```
make test            # all C unit/socket suites that exist so far, in milestone order
make e2e             # tests/e2e_test.sh (M5+)
make -C ../lab3 test # regression: lab3 untouched
```

The report's "Test cases" section is generated from this file. Keep the
columns *Checks* and *Data / Expected* up to date.

---

## M0 — Scaffolding (`tests/cli_test.c`)

| id | checks | data / expected |
|---|---|---|
| T0.1 | int, uint, and string flags parse | `--port 9400 --n 5 --out a.bin` gives port=9400, n=5, out="a.bin" |
| T0.2 | defaults kept when a flag is absent | no `--n` means n equals its spec default |
| T0.3 | errors are rejected | unknown flag `--bogus`, a missing value (`--port` last), or non-numeric `--n x` makes `cli_parse` return −1 |
| T0.4 | build hygiene | `make clean && make test` succeeds with `-Wall -Wextra` and **no warnings** in lab4 sources; `make -C ../lab3 test` still passes |

---

## M1 — Walsh codes (`tests/walsh_test.c`)

| id | checks | data / expected |
|---|---|---|
| W1 | `walsh_length_for` | 1→1, 2→2, 3→4, 4→4, 5→8, 8→8, 9→16, 17→32, 33→64, 64→64; 0→−1, −3→−1 |
| W2 | `walsh_is_pow2` | true: 1, 2, 4, 64, 1024; false: 0, −4, 3, 6, 12, 1023 |
| W3 | base case | `walsh_matrix(1)` is `[+1]` |
| W4 | literal small matrices | L=2 gives `[[1,1],[1,−1]]`; L=4 equals the `W_4` in CLAUDE.md §6 row for row |
| W5 | entries are ±1 only | every entry of W_L for L = 1..256 (powers of 2) is +1 or −1 |
| W6 | row structure | row 0 is all +1, and every other row sums to 0 (balanced), for L up to 256 |
| W7 | **orthogonality** | `W·Wᵀ == L·I` exactly for L ∈ {1, 2, 4, …, 256} |
| W8 | Sylvester recursion | for L up to 128, W_{2L} = [[W_L, W_L], [W_L, −W_L]] block by block |
| W9 | `walsh_code` agrees with the matrix | for L=16 and every i, `walsh_code(16, i)` equals row i of `walsh_matrix(16)` |
| W10 | argument errors | `walsh_matrix(3)`, `walsh_matrix(0)`, `walsh_matrix(2048)`, `walsh_code(8, 8)`, and `walsh_code(8, −1)` all return −1 |

Tool check (M1 acceptance): `./tools/walsh_dump --gram 8` prints an 8×8
CSV with 8 on the diagonal and 0 everywhere else.

---

## M2 — CDMA core (`tests/cdma_test.c`)

Random data comes from a fixed-seed `srand`, so failures are
reproducible.

| id | checks | data / expected |
|---|---|---|
| C1 | `cdma_chip_count` | (65, 8) → 4160; (1, 1) → 8; (0, 4) → 0 |
| C2 | bit order and mapping | byte `0x80`, L=1, code `[+1]` gives chips `[+1,−1,−1,−1,−1,−1,−1,−1]` (MSB first, 1→+1, 0→−1) |
| C3 | spread block structure | byte `0xA5`, code `[+1,−1]` gives 16 chips `+c,−c,+c,−c,−c,+c,−c,+c` where `c=[+1,−1]` |
| C4 | spread range | 65 random bytes with L=16: every chip is ±1, and block j equals ±code |
| C5 | `cdma_sum` | two known vectors sum element-wise; a NULL entry contributes 0; all NULL gives all zeros; for random n=8, `max\|S\| ≤ 8` |
| C6 | **worked example** | the L=4 example: S = `[1,1,−1,3]`; `cdma_correlate` with c0..c3 gives 4, −4, 0, 4 |
| C7 | `cdma_decide` | with L=8: +8→ONE, −8→ZERO, 0→SILENT; 2, −6, 9, and 7 → INVALID |
| C8 | single sender round trip | n=1 at L=1, and n=1 at L=2: 65 random bytes decode identically; returns DATA, `ones+zeros == 520`, `silent = invalid = 0` |
| C9 | **full load** | n=L=8, all active, random frames: every station decodes its own data exactly |
| C10 | n not a power of 2 | n=5, L=8, all 5 active: stations 0..4 are exact; unused codes 5, 6, 7 give `CDMA_FRAME_SILENT` |
| C11 | partial silence | n=8 with stations {2, 5} NULL: those two give SILENT (`silent == 520`), and the rest are exact |
| C12 | **perturbation detected** | after C9, add +1 to a single chip `S[37]`: every station returns ERROR with `invalid == 1` |
| C13 | stats accumulate | calling `cdma_despread` twice into the same `cdma_stats_t` doubles the counts (the caller zeroes it) |
| C14 | **fuzz** | 500 trials: random n ∈ [1, 32], L = `walsh_length_for(n)`, a random silent mask, random 65-byte frames; every active station is exact, every silent one is SILENT, and `invalid` is always 0 |

---

## M3 — Protocol and framing (`tests/framing_test.c`)

| id | checks | data / expected |
|---|---|---|
| F1 | wire sizes | `sizeof(frame_t)==65`, `hello_msg_t==4`, `config_msg_t==12`, `snd_hdr_t==8`, `rcv_hdr_t==8`; `CDMA_FRAME_CHIPS(8)==4160` |
| F2 | build fields | `framing_build(id=3, seq=7, "HELLO", 5)` gives type 0, `src_addr=dest_addr={0,0,0,0,0,3}`, `ntohs(frame_len)==5`, `ntohs(frame_seq)==7`, payload[0..4]="HELLO" and payload[5..43] all 0 |
| F3 | FCS | `ntohl(trailer.fcs) == compute_fcs(SCHEME_CRC32, f, 61)` |
| F4 | check accepts | check on a built frame returns 0 with id=3, seq=7, len=5; NULL out-pointers are also accepted |
| F5 | **any single bit flip rejected** | flipping each of the 520 bits in turn makes check return −1 for every one |
| F6 | length bounds on build | len 0 and len 45 make build return −1; len 44 returns 0 |
| F7 | length bounds on check | hand-craft `frame_len=50` with a *valid* CRC over it: check returns −2 |
| F8 | seq wraparound | seq 65535 round-trips |
| F9 | **pure pipeline** | n=3, L=4, three frames built → spread → `cdma_sum` → despread each → `framing_check` → all 0, payloads identical |

---

## M4 — Channel server (`tests/channel_test.c`)

Each test forks `channel_serve` on its own port (9400 + test number ×
7) and drives scripted mock clients with `utils.h` sockets, like
lab3's `channel_test`. Every child is reaped, and the test times out
after 5 s.

| id | checks | data / expected |
|---|---|---|
| CH1 | handshake in any order | n=2, connecting as R1, S0, R0, S1: every client gets `config{n=2, L=2, code_index=id}` |
| CH2 | n not a power of 2 | n=3 gives L=4 in the config |
| CH3 | **one round sum** | S0 sends DATA A and S1 sends DATA B (random ±1 chips): both receivers get `round=0`, `active=2`, `ntohs(sum)==A+B` element-wise, equal to a local `cdma_sum` |
| CH4 | IDLE contributes 0 | S1 sends IDLE: receivers get sum == A and `active=1` |
| CH5 | DONE contributes 0 from then on | S1 sends DONE in round 1: rounds 1..3 are A-only, and the channel stops reading S1 (S1 closes its socket and no error follows) |
| CH6 | termination | all senders DONE: every receiver gets `should_stop=1`, and `channel_serve` returns 0 with `rounds` equal to the number of broadcast rounds |
| CH7 | **desync guard** | S0 sends `round=5` when round 0 is expected: returns −1 |
| CH8 | bad hello | a duplicate sender id, id ≥ n, or role 7 each makes it return −1 |
| CH9 | max_rounds cap | `max_rounds=3` with senders that never finish: exactly 3 sums, then stop; returns 0 |
| CH10 | metrics exact | scripted 2-round run with n=2, L=2: `data_sends`, `idle_sends`, `bytes_in`, `bytes_out`, and `max_abs_sum` equal hand-computed values |
| CH11 | extremes | n=1 (L=1) works; n=64 (L=64) one round with all DATA gives `max_abs_sum ≤ 64` |

---

## M5 — End-to-end (`tests/e2e_test.sh`)

It launches the real `./channel`, n `./sender` processes, and n
`./receiver` processes. The script uses its own ports, `timeout 30`
per case, and a `trap` that kills stragglers. Data is generated with
`head -c N /dev/urandom` under a fixed `openssl`/`python` seed. Each
case prints PASS or FAIL, and the script exits non-zero on any
failure.

| id | checks | data / expected |
|---|---|---|
| E2E1 | smallest system | n=1, a text file: `cmp` identical, receiver exits 0 |
| E2E2 | power-of-2 loads | n ∈ {2, 4, 8}, 10 000 random bytes each: all `cmp` identical |
| E2E3 | n not a power of 2 | n ∈ {3, 5, 7}: all identical |
| E2E4 | unequal sizes | n=4, sizes 1, 44, 45, and 4399 bytes (boundary sizes): identical; `frames_ok` = 1, 1, 2, 100 |
| E2E5 | empty input | n=3, sender 1 gets an empty file: out1 is empty; the others are identical; `frames_ok_1 = 0` |
| E2E6 | late start | n=3, sender 2 with `--start-round 5`: identical, and receiver 2 has `silent_rounds ≥ 5` |
| E2E7 | integrity counters | every receiver metrics file above has `fcs_fail = seq_errors = error_frames = bits_invalid = 0`; the channel's `max_abs_sum ≤ n` |
| E2E8 | stress | n=16, 20 KB each: identical within the timeout |
| E2E9 | clean shutdown | after every case, all `2n+1` processes exited 0 and `pgrep -f 'lab4/(channel\|sender\|receiver)'` is empty |

---

## M6 — Evaluation (`make eval`, checked by `tests/eval_check.py`)

| id | checks | data / expected |
|---|---|---|
| EV1 | quick mode runs | `run_eval.py --quick` finishes in under 60 s and writes both CSVs with the exact headers from `EXPERIMENTS.md` |
| EV2 | E1 correctness | every E1 row has `match=1`, `fcs_fail=seq_errors=error_frames=bits_invalid=0`, and `max_abs_sum ≤ n` |
| EV3 | E1 accounting | `frames_ok == ceil(file_bytes/44)` and `silent_rounds == rounds − frames_ok` |
| EV4 | E3 consistency | `L == next_pow2(n)` (compared against `walsh_dump --lengths`), `code_util == n/L`, and every elapsed time > 0 |
| EV5 | reproducible | two quick runs with the same `--seed` give identical E1 CSVs (E3 timings are excluded) |
| EV6 | plots | `plot.py` writes all 5 `fig_*.png` from the CSVs plus `walsh_dump` alone, with no other inputs |
| EV7 | no leaks | no orphan processes after a full run; `eval/data/` is regenerated rather than committed |

---

## M7 — Report

| id | checks | expected |
|---|---|---|
| R1 | builds | `latexmk -pdf report/assignment4_report.tex` with no errors or undefined references |
| R2 | guideline coverage | the sections Title, Design, Implementation, Test cases, Results, Analysis, and Comments are all present |
| R3 | figures and tables referenced | every `\includegraphics` and table has a `\ref` in the text |
| R4 | code snippets match the source | every snippet is copied from the current user source, and function names exist (checked with grep) |

---

## Results log (the tutor fills this in at each milestone gate)

| milestone | suite | assertions passed / total | date | notes |
|---|---|---|---|---|
| M0 | cli_test | 10 / 10 | 2026-09-28 | T0.1–T0.3 in `cli_test`; T0.4 (`-Wall -Wextra` clean, `make -C ../lab3 test` green) verified by running both builds |
| M1 | walsh_test | 123 / 123 | 2026-09-28 | one bug found and fixed: `walsh_is_pow2` used `L & (L - 1) == 0`, which C parses as `L & ((L-1)==0)` since `==` outranks `&`; fixed to `(L & (L - 1)) == 0`. `./tools/walsh_dump --gram 8` confirmed `8·I`; lab3 regression clean |
| M2 | cdma_test | 40 / 40 (173/173 incl. earlier suites) | 2026-09-28 | several bugs found and fixed directly: `cdma_chip_count` was `static` in the header (broke cross-file linkage); `cdma_correlate`'s accumulator was `uint16_t` (should be `int32_t` — wrapped negative correlations to huge positives, e.g. worked-example `-4` became `65532`, misclassifying every ZERO bit as INVALID); `cdma_despread`'s per-bit block pointer was declared `uint16_t block = sum + (pos*L)` (assigns a pointer into a 16-bit int — compile error) and indexed only by `pos` (0..7) with no byte offset, so it never located the right L-chip slice for byte>0; `int ones, zeros, silent, invalid = 0;` only initialized `invalid` (C only initializes the last name in a comma list) leaving the other three counters as garbage; `st->ones = ones` etc. used `=` instead of `+=`, so stats didn't accumulate across calls (C13); the SILENT-frame branch returned `CDMA_BIT_SILENT` (bit-level enum, value 2) instead of `CDMA_FRAME_SILENT` (frame-level enum), which numerically collided with `CDMA_FRAME_ERROR`. Also fixed: several `int`/`size_t` signed-compare warnings, and `int8_t byte` in `cdma_spread` changed to `uint8_t` (signed right-shift on a negative `int8_t` is implementation-defined). A Makefile bug was also found and fixed: `$(wildcard a b)` is OR not AND, so `SENDER_BIN`/`RECEIVER_BIN` etc. triggered as soon as any one dependency existed, not all of them. lab3 regression clean |
| M3 | framing_test | 34 / 34 (207/207 incl. earlier suites) | 2026-10-05 | bugs fixed directly: `memset(&frame, ...)` zeroed the pointer variable instead of the struct (use `memset(frame, 0, sizeof(*frame))`); `src_addr`/`dest_addr` not set to `{0,0,0,0,0,id}` (dest was set to 0xFF twice); no `len` range check; `f->payload = payload` (arrays cannot be assigned, use `memcpy`); `trailer = htonl(..)` should be `trailer.fcs`; undefined `f`, missing includes/return; `framing_check` was empty. `protocol.h`: `send_hdr_t` renamed to the locked `snd_hdr_t`, redundant scheme includes dropped. lab3 regression clean |
| M4 | channel_test | 56 / 56 | 2026-11-02 | CH1–CH11. Fixed in M4 cleanup: `cli.c` range-checks before the narrowing cast (`--port 4294967377` wrapped to 81); `channel_main.c` separates "--n is required" from "--n must be 1..64" |
| M5 | e2e_test.sh | 13 / 13 (E2E1–E2E9, 13 runs) | 2026-11-02 | all runs `cmp` identical, counters zero, n=16 at 20 KB each; shared handshake in `station.c` (R1); clean build, 0 warnings |
| M6 | eval_check + EV1–EV7 | 7 / 7 (EV1–EV7); eval_check PASS on 450 E1 + 36 E3 rows | 2026-11-02 | EV1: `--quick` ×2 ran in 0.29 s and 0.24 s, exact headers vs EXPERIMENTS.md. EV2–EV4: `eval_check.py` PASS. EV5: two quick seed-42 runs give byte-identical `EVAL_E1.csv`. EV6: `plot.py` writes all 5 figures from CSVs + `walsh_dump` (two layout defects fixed: fig_walsh ylabel collided with colorbar, fig_e3_goodput suptitle collided with panel titles; fig_e3_analytic legend moved). EV7: no leftover processes after sweeps; `eval/data/` regenerated by `run_eval.py` and gitignored. Full sweep 21 s. Deviations from expectations are in `eval/EVAL_INSIGHTS.md`, not test failures |
| M7 | report | – | – | – |
