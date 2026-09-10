#!/usr/bin/env bash
# The demo: Pi camera -> laptop -> drone detector -> boxes on screen.
#
#   ./scripts/demo_live.sh              # from the Pi over the USB link
#   ./scripts/demo_live.sh --local      # rehearse on the laptop's own webcam
#   ./scripts/demo_live.sh --host 192.168.1.50   # Pi over Wi-Fi instead
#   ./scripts/demo_live.sh --local --no-gate     # rehearsing with a drone PHOTO
#   ./scripts/demo_live.sh --local --raw         # ungated, for comparison
#
# The detector is the model from the Drone_Ml repo, unmodified. It already
# accepts a URL as --source, and the Pi serves MJPEG over HTTP, so the two
# halves meet with no glue code.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DRONE_ML=${DRONE_ML:-$HOME/Projects/Drone_Ml}
PI_HOST=10.55.0.1
PORT=8485
LOCAL=0
RAW=0
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) LOCAL=1; shift ;;
    --raw)   RAW=1; shift ;;
    --host)  PI_HOST="$2"; shift 2 ;;
    --port)  PORT="$2"; shift 2 ;;
    *)       EXTRA+=("$1"); shift ;;
  esac
done

PY="$DRONE_ML/venv/bin/python"
WEIGHTS="$DRONE_ML/runs/detect/runs/detect/p2_s/weights/best.pt"

[[ -x "$PY" ]] || { echo "ERROR: no venv at $DRONE_ML/venv -- set DRONE_ML" >&2; exit 1; }
[[ -f "$WEIGHTS" ]] || { echo "ERROR: weights not found: $WEIGHTS" >&2; exit 1; }

if [[ $LOCAL == 1 ]]; then
  SOURCE=0
  echo "==> rehearsal mode: laptop webcam, no Pi involved"
  # UVC exposure settings persist in the driver between processes: if anything
  # previously put this camera in manual exposure, the feed comes up BLACK and
  # looks like a broken camera. realtime_track.py opens the device raw, so
  # force auto-exposure back on before handing over.
  "$PY" - <<'RESET'
import cv2
c = cv2.VideoCapture(0, cv2.CAP_V4L2)
if c.isOpened():
    c.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)   # 3 = auto, 1 = manual
    for _ in range(5): c.read()
    c.release()
    print("    camera reset to auto-exposure")
RESET
else
  SOURCE="http://$PI_HOST:$PORT/stream.mjpg"
  echo "==> source: $SOURCE"
  # Fail here with a useful message rather than inside OpenCV, which reports
  # a URL it cannot open as an empty capture and no reason at all.
  if ! curl -s --max-time 4 -o /dev/null "http://$PI_HOST:$PORT/"; then
    cat >&2 <<MSG
ERROR: nothing serving at http://$PI_HOST:$PORT/

On the Pi:  python3 -m himkavach.eo.sender --http
If that is running, check the link:  ./scripts/laptop_usb_link.sh
MSG
    exit 1
  fi
fi

# COSMIC exports QT_QPA_PLATFORM=wayland, and the OpenCV wheel ships no Qt
# wayland plugin -- it falls back to xcb with a wall of warnings, and on some
# sessions fails to open the window at all. Force xcb: a `:-` default would
# inherit COSMIC's wayland and defeat the point. HIMKAVACH_QT=1 to opt out.
[[ -z "${HIMKAVACH_QT:-}" ]] && export QT_QPA_PLATFORM=xcb

echo "==> weights: $WEIGHTS"
echo "==> detector: p2_s (P2 head, val mAP50 0.915). Q to quit."
echo

if [[ $RAW == 1 ]]; then
  # The Drone_Ml script unmodified: no geometry gate, so pointed at a room it
  # draws a confident full-frame box around whatever it sees. Kept for
  # comparison, and because it is the detector's own honest output.
  cd "$DRONE_ML"
  exec "$PY" realtime_track.py \
      --source "$SOURCE" --weights "$WEIGHTS" \
      --imgsz 640 --device 0 "${EXTRA[@]}"
fi

# Default: the same detector behind a geometry gate. A 0.3 m drone cannot be
# wider than ~47 px on a 640-wide frame without being closer than 3 m, so
# degenerate full-frame boxes are rejected on physics rather than confidence.
# Add --no-gate when rehearsing with a drone PHOTO held up to the camera.
cd "$REPO"
exec "$PY" -m himkavach.eo.detect_live \
    --source "$SOURCE" \
    --weights "$WEIGHTS" \
    --tracker "$DRONE_ML/cfg/bytetrack_drone.yaml" \
    --imgsz 640 \
    --device 0 \
    "${EXTRA[@]}"
