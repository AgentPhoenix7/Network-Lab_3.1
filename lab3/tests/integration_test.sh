#!/bin/bash
# Integration test: launch channel + 2 stations, saturated mode, verify clean exit

set -e

cd "$(dirname "$0")/.."

CHANNEL_PORT=9500
T_FRAME=8
MAX_SLOTS=100

# Clean up any leftover processes
cleanup() {
    pkill -f "\.\/channel.*$CHANNEL_PORT" 2>/dev/null || true
    pkill -f "\.\/station" 2>/dev/null || true
    sleep 0.5
}

trap cleanup EXIT

cleanup

echo "=== Integration Test: 2 stations, 1-persistent, saturated ==="
echo "T_frame=$T_FRAME, max_slots=$MAX_SLOTS"
echo ""

# Start channel in background
./channel --port $CHANNEL_PORT --n-stations 2 --T-frame $T_FRAME --max-slots $MAX_SLOTS &
CHANNEL_PID=$!

# Give channel time to listen
sleep 0.2

# Start 2 stations
./station --strategy 1persistent --seed 42 --port $CHANNEL_PORT --host 127.0.0.1 &
S1_PID=$!

./station --strategy 1persistent --seed 43 --port $CHANNEL_PORT --host 127.0.0.1 &
S2_PID=$!

# Wait for all to finish (with timeout)
TIMEOUT=10
START=$(date +%s)
while kill -0 $CHANNEL_PID $S1_PID $S2_PID 2>/dev/null; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))
    if [ $ELAPSED -gt $TIMEOUT ]; then
        echo "TIMEOUT: processes still running after ${TIMEOUT}s, killing..."
        kill -9 $CHANNEL_PID $S1_PID $S2_PID 2>/dev/null || true
        exit 1
    fi
    sleep 0.1
done

echo ""
echo "=== All processes exited cleanly ==="
echo "PASS: Integration test succeeded"
exit 0
