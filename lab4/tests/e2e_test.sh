#!/bin/bash
# End-to-end (tests.md E2E1-E2E9): real channel + n senders + n receivers over TCP.
# Each case gets its own port, every process runs under `timeout`, a trap kills
# stragglers, and outputs are checked with cmp plus the receiver metrics.
# Exits non-zero if any case fails.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT"
PROC_TIMEOUT=60
BASE_PORT=9600
TMP="$(mktemp -d)"
PIDS=()
pass=0
fail=0
case_no=0

cleanup() {
    local p
    for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
    wait 2>/dev/null
    if [ "$fail" -eq 0 ]; then
        rm -rf "$TMP"
    else
        echo "logs kept in $TMP"
    fi
}
trap cleanup EXIT

# metric FILE KEY -> value of key=value line (empty if missing)
metric() { grep -m1 "^$2=" "$1" 2>/dev/null | cut -d= -f2; }

# make_input FILE KIND(rand|text) SIZE SEED
make_input() {
    timeout 60 python3 -I -c '
import random, sys
path, kind, size, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed)
if kind == "text":
    words = ["walsh", "chip", "code", "frame", "channel", "sum", "silent"]
    data = ""
    while len(data) < size:
        data += " ".join(rng.choice(words) for _ in range(8)) + "\n"
    data = data[:size].encode()
else:
    data = rng.randbytes(size)
open(path, "wb").write(data)
' "$@"
}

# run_case LABEL N "SIZES" [LATE_ID LATE_ROUNDS] [KIND]
run_case() {
    local label=$1 n=$2 sizes=$3 late_id=${4:--1} late_rounds=${5:-0} kind=${6:-rand}
    local port=$((BASE_PORT + case_no))
    local seed_base=$((case_no * 100))
    case_no=$((case_no + 1))
    local d="$TMP/case$case_no"
    mkdir -p "$d"
    local -a sz
    read -r -a sz <<< "$sizes"
    if [ "${#sz[@]}" -ne "$n" ]; then
        echo "FAIL $label: test bug, ${#sz[@]} sizes for n=$n"
        fail=$((fail + 1))
        return
    fi

    local i
    for ((i = 0; i < n; i++)); do
        make_input "$d/in$i" "$kind" "${sz[$i]}" $((seed_base + i)) || {
            echo "FAIL $label: could not make input $i"; fail=$((fail + 1)); return; }
    done

    timeout "$PROC_TIMEOUT" "$BIN/channel" --port "$port" --n "$n" \
        --metrics-file "$d/channel.metrics" >"$d/channel.log" 2>&1 &
    local chan_pid=$!
    PIDS+=("$chan_pid")

    local -a spids=() rpids=()
    for ((i = 0; i < n; i++)); do
        local -a extra=()
        [ "$i" -eq "$late_id" ] && extra=(--start-round "$late_rounds")
        timeout "$PROC_TIMEOUT" "$BIN/sender" --port "$port" --id "$i" --in "$d/in$i" \
            ${extra[@]+"${extra[@]}"} 2>"$d/s$i.err" &
        spids+=("$!"); PIDS+=("$!")
        timeout "$PROC_TIMEOUT" "$BIN/receiver" --port "$port" --id "$i" --out "$d/out$i" \
            --metrics-file "$d/r$i.metrics" 2>"$d/r$i.err" &
        rpids+=("$!"); PIDS+=("$!")
    done

    local -a sst=() rst=()
    local p
    for p in "${spids[@]}"; do wait "$p"; sst+=("$?"); done
    for p in "${rpids[@]}"; do wait "$p"; rst+=("$?"); done
    wait "$chan_pid"; local cst=$?

    local -a why=()
    [ "$cst" -eq 0 ] || why+=("channel exit $cst")
    local max_abs
    max_abs=$(metric "$d/channel.metrics" max_abs_sum)
    [ -n "$max_abs" ] && [ "$max_abs" -le "$n" ] || why+=("channel max_abs_sum=$max_abs > n")

    for ((i = 0; i < n; i++)); do
        [ "${sst[$i]}" -eq 0 ] || why+=("sender $i exit ${sst[$i]}")
        [ "${rst[$i]}" -eq 0 ] || why+=("receiver $i exit ${rst[$i]}")
        cmp -s "$d/in$i" "$d/out$i" || why+=("receiver $i output differs")

        local expect_frames=$(( (sz[i] + 43) / 44 ))
        [ "$(metric "$d/r$i.metrics" frames_ok)" = "$expect_frames" ] \
            || why+=("receiver $i frames_ok != $expect_frames")
        local key
        for key in fcs_fail seq_errors error_frames bits_invalid; do
            [ "$(metric "$d/r$i.metrics" $key)" = "0" ] || why+=("receiver $i $key != 0")
        done
    done

    if [ "$late_id" -ge 0 ]; then
        local silent
        silent=$(metric "$d/r$late_id.metrics" silent_rounds)
        [ -n "$silent" ] && [ "$silent" -ge "$late_rounds" ] \
            || why+=("late receiver silent_rounds=$silent < $late_rounds")
    fi

    # No process of this case may survive.
    if pgrep -f -- "$BIN/(channel|sender|receiver) .*--port $port( |\$)" >/dev/null; then
        why+=("orphan process left on port $port")
    fi

    if [ "${#why[@]}" -eq 0 ]; then
        echo "PASS $label (n=$n, sizes: $sizes)"
        pass=$((pass + 1))
    else
        echo "FAIL $label: ${why[*]}"
        fail=$((fail + 1))
        tail -n 5 "$d"/*.err "$d/channel.log" 2>/dev/null | sed 's/^/    /'
    fi
    # Anything still alive from this case is killed before the next one.
    for p in "$chan_pid" "${spids[@]}" "${rpids[@]}"; do kill "$p" 2>/dev/null; done
    wait 2>/dev/null
}

rep() { local out="" k; for ((k = 0; k < $2; k++)); do out+="$1 "; done; echo "$out"; }

run_case "E2E1 smallest system" 1 "300" -1 0 text
for n in 2 4 8; do
    run_case "E2E2 power-of-2 load" "$n" "$(rep 10000 $n)"
done
run_case "E2E3 n=3" 3 "5000 1234 7000"
run_case "E2E3 n=5" 5 "3000 9001 1 4400 12"
run_case "E2E3 n=7" 7 "4096 1 2222 8888 300 45 6000"
run_case "E2E4 boundary sizes" 4 "1 44 45 4399"
run_case "E2E5 empty input" 3 "2000 0 2000"
run_case "E2E6 late start" 3 "3000 3000 3000" 2 5
run_case "E2E7 integrity counters" 5 "2000 2000 2000 2000 2000"
run_case "E2E8 stress n=16" 16 "$(rep 20000 16)"

# E2E9: after all cases, nothing from this build may still be running.
if pgrep -f -- "$BIN/(channel|sender|receiver)" >/dev/null; then
    echo "FAIL E2E9 clean shutdown: processes still running"
    fail=$((fail + 1))
else
    echo "PASS E2E9 clean shutdown"
    pass=$((pass + 1))
fi

echo "E2E: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
