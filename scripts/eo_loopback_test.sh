#!/usr/bin/env bash
# Prove the EO chain on the laptop alone, before the Pi exists.
# Runs sender and receiver over loopback against the laptop's own webcam, so
# every part except the USB link is exercised.
#
#   ./scripts/eo_loopback_test.sh [device] [WxH] [frames]
set -euo pipefail

DEVICE=${1:-/dev/video0}
SIZE=${2:-1280x720}
FRAMES=${3:-200}
PORT=8487

cd "$(dirname "${BASH_SOURCE[0]}")/.."

[[ -e "$DEVICE" ]] || { echo "ERROR: $DEVICE not present. Try: ls /dev/video*" >&2; exit 1; }

echo "==> starting sender on $DEVICE at $SIZE"
python3 -m himkavach.eo.sender --device "$DEVICE" --size "$SIZE" --port "$PORT" &
SENDER_PID=$!
# Kill by PID, never `pkill -f himkavach.eo.sender` -- that pattern also matches
# this script's own command line and takes the whole shell down with it.
trap 'kill "$SENDER_PID" 2>/dev/null || true' EXIT

sleep 3
kill -0 "$SENDER_PID" 2>/dev/null || { echo "ERROR: sender died on startup" >&2; exit 1; }

echo "==> receiving $FRAMES frames"
python3 -m himkavach.eo.receiver --host 127.0.0.1 --port "$PORT" \
    --max-frames "$FRAMES" --no-retry

echo
echo "Loopback OK. Numbers above are the laptop's own webcam and CPU;"
echo "the Pi will be slower to encode and adds the USB hop on top."
