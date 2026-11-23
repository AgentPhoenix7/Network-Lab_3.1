#!/usr/bin/env bash
# Quick FTP demo: server in one terminal, client in another.
#
#   ./demo.sh open     open two terminal windows (server + client) automatically
#   ./demo.sh server   run the server here          (terminal 1)
#   ./demo.sh client   run the interactive client   (terminal 2)
#
# Files live in $FTP_DEMO_DIR (default ~/ftp-demo), not in the repo.
# uv selects the project's locked environment; the client keeps downloads as its working directory.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")" && pwd)"
SELF="$PROJECT/demo.sh"
DEMO_DIR="${FTP_DEMO_DIR:-$HOME/ftp-demo}"
PORT="${FTP_PORT:-2121}"
export FTP_PASSWORD="${FTP_PASSWORD:-lab-demo}"   # example lab password, as in the README

prepare() {
    mkdir -p "$DEMO_DIR/server-root" "$DEMO_DIR/downloads"
    [ -f "$DEMO_DIR/downloads/hello.txt" ] ||
        printf 'Hello from our FTP project!\n' > "$DEMO_DIR/downloads/hello.txt"
}

server() {
    prepare
    echo "== FTP server on 127.0.0.1:$PORT (root: $DEMO_DIR/server-root)"
    echo "== Stop with Ctrl+C"
    cd "$PROJECT"
    exec uv run --locked --no-dev --project "$PROJECT" python -m ftp_lab.server \
        --port "$PORT" --root "$DEMO_DIR/server-root"
}

client() {
    prepare
    # Wait up to 15 s for the server to start listening.
    for _ in $(seq 150); do
        (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break
        sleep 0.1
    done
    cat <<EOF
== FTP client. Password is $FTP_PASSWORD. Try these at the ftp> prompt:
   pwd
   mkdir demo
   cd demo
   put hello.txt "hello world.txt"      (uploads $DEMO_DIR/downloads/hello.txt)
   ls
   get "hello world.txt" roundtrip.txt
   passive
   quit
EOF
    cd "$DEMO_DIR/downloads"   # local files for put/get
    PYTHONPATH="$PROJECT" exec uv run --locked --no-dev --project "$PROJECT" \
        python -m ftp_lab.client --port "$PORT" --trace
}

open_terminals() {
    local q; q="$(printf '%q' "$SELF")"
    local cmd_server="$q server; exec bash" cmd_client="$q client; exec bash"
    if command -v gnome-terminal >/dev/null; then
        gnome-terminal --title="FTP server" -- bash -c "$cmd_server" &
        gnome-terminal --title="FTP client" -- bash -c "$cmd_client" &
    elif command -v konsole >/dev/null; then
        konsole --new-tab -e bash -c "$cmd_server" &
        konsole --new-tab -e bash -c "$cmd_client" &
    elif command -v xterm >/dev/null; then
        xterm -T "FTP server" -e bash -c "$cmd_server" &
        xterm -T "FTP client" -e bash -c "$cmd_client" &
    else
        echo "No known terminal emulator found. Run these in two shells:"
        echo "  $SELF server"
        echo "  $SELF client"
        exit 1
    fi
    echo "Opened server and client terminals."
}

case "${1:-}" in
    server) server ;;
    client) client ;;
    open)   open_terminals ;;
    *) echo "Usage: $0 {open|server|client}"; exit 2 ;;
esac
