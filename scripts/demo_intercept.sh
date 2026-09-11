#!/usr/bin/env bash
# The intercept dashboard: footage in, NEUTRALISE / LET GO out, in a browser.
#
#   ./scripts/demo_intercept.sh                      # laptop webcam
#   ./scripts/demo_intercept.sh --source 2           # another camera index
#   ./scripts/demo_intercept.sh --source clip.mp4    # recorded footage
#   ./scripts/demo_intercept.sh --source http://10.55.0.1:8485/stream.mjpg
#
# Then open http://127.0.0.1:8610. Sources can also be switched, and video
# uploaded, from the page itself.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DRONE_ML=${DRONE_ML:-$HOME/Projects/Drone_Ml}
PY="$DRONE_ML/venv/bin/python"
WEIGHTS="$DRONE_ML/runs/detect/runs/detect/p2_s/weights/best.pt"

[[ -x "$PY" ]] || { echo "ERROR: no venv at $DRONE_ML/venv -- set DRONE_ML" >&2; exit 1; }
[[ -f "$WEIGHTS" ]] || { echo "ERROR: weights not found: $WEIGHTS" >&2; exit 1; }

cd "$REPO"
PORT=8610
for ((i = 1; i <= $#; i++)); do [[ ${!i} == --port ]] && { j=$((i + 1)); PORT=${!j}; }; done
( sleep 4; xdg-open "http://127.0.0.1:$PORT" >/dev/null 2>&1 || true ) &
exec "$PY" -m aura.intercept "$@"
