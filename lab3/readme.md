# JU_lab

Computer Networks Lab assignments (BCSE-3.1). Assignment 3 (`lab3/`) is a CSMA/MAC simulation: a `channel` server process arbitrates a shared slotted medium, and N `station` client processes contend for it under one of four access strategies (non-persistent, 1-persistent, p-persistent, CSMA/CD).

## lab3 file tree

```
lab3/
|-- protocol.h              wire structs and enums shared by channel and station
|-- channel.h, channel.c     medium state machine and the slot-barrier server
|-- channel_main.c           CLI entry point for the channel process
|-- backoff.h, backoff.c    truncated binary exponential backoff
|-- csma.h, csma.c           the per-slot strategy decision function
|-- station.h, station.c    station client: workload, framing, the tick loop
|-- eval/
|   |-- eval_main.c          fork/exec sweep harness, CSV output
|   `-- plot.py              renders the required figures from the CSV
`-- tests/
    |-- channel_test.c       resolver unit tests and socket integration tests
    |-- csma_test.c          per-strategy decision tables
    |-- backoff_test.c       backoff window and give-up bounds
    |-- station_test.c       build and link check
    `-- integration_test.sh  manual channel + station end-to-end run

reused from Assignment 1 and the shared utilities:
lab1/frame.h                  frame layout
lab1/error.c, lab1/scheme.c   checksum FCS compute and verify
utils/utils.c, utils/utils.h  socket setup, full send and receive
```

## Build and test

```
cd lab3
make all    # builds channel, station, eval/eval_main, and all test binaries
make test   # runs channel_test, backoff_test, csma_test, station_test
```

## Running the evaluation sweep

```
cd lab3
./eval/eval_main [master_seed] [T_frame]   # defaults: 42, 16
python3 eval/plot.py
```

`eval_main` forks the channel and N stations per run, sweeping p-persistent
over p (fixed N=20) and all four schemes over N (2 to 100), 3 trials per
point, and writes `eval/EVAL_RESULTS.csv`. `plot.py` reads that CSV, picks
the best p from the p-sweep, and writes `eval/EVAL_PLOTS.png`.

The full sweep is 123 runs and takes well under a minute.