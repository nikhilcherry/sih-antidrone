"""EO sender -- runs ON THE RASPBERRY PI.

Captures from a USB (UVC) webcam and streams length-prefixed JPEG frames over
TCP to whichever laptop connects. The Pi is the server so the laptop side can be
restarted freely during development without touching the Pi.

    python3 -m aura.eo.sender --device /dev/video0 --size 1280x720 --fps 30
    python3 -m aura.eo.sender --http          # MJPEG over HTTP instead

Two wire modes. Raw TCP (default) carries sequence numbers and timing, which
`receiver.py` turns into link-health stats. `--http` serves MJPEG at a URL that
`cv2.VideoCapture` opens directly, so the Drone_Ml detector consumes the feed
through its existing --source argument with no code change.

Latency notes that matter more than they look:
  * CAP_PROP_BUFFERSIZE=1 -- without it V4L2/OpenCV queues frames and you end up
    tracking a drone that moved 200 ms ago. This is the single biggest source of
    "our tracker lags" in demos.
  * FOURCC=MJPG -- asks the camera to compress on-chip. On USB 2.0, raw YUYV at
    1280x720 needs ~442 Mbit/s and the camera will silently drop to ~10 fps or
    refuse the mode. MJPG keeps 30 fps within the bus budget.
  * We decode the camera's MJPEG and re-encode. That costs a few ms on a Pi 4.
    For a zero-transcode path see docs/eo_bringup.md (ffmpeg -c:v copy).
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

import cv2

from .protocol import pack_header


def parse_size(text: str) -> tuple[int, int]:
    w, _, h = text.lower().partition("x")
    return int(w), int(h)


def open_camera(device: str, width: int, height: int, fps: int,
                exposure: str = "auto") -> cv2.VideoCapture:
    # CAP_V4L2 explicitly: the default backend on Linux can pick GStreamer and
    # silently ignore the property sets below.
    index_or_path: object = int(device) if device.isdigit() else device
    cap = cv2.VideoCapture(index_or_path, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {device} -- check `v4l2-ctl --list-devices` and group 'video'")

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # UVC control values PERSIST IN THE DRIVER after the process exits. A tool
    # that once set manual exposure leaves the camera that way for every later
    # program, and the symptom is a pure-black feed that looks like a broken
    # camera or broken code. Always state the exposure mode explicitly rather
    # than inheriting whatever the last process left behind.
    # V4L2: 3 = aperture-priority (auto), 1 = manual.
    if exposure == "auto":
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
    else:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
        cap.set(cv2.CAP_PROP_EXPOSURE, float(exposure))

    actual = (
        int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        cap.get(cv2.CAP_PROP_FPS),
    )
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC)).to_bytes(4, "little", signed=False)
    print(f"[sender] camera negotiated {actual[0]}x{actual[1]} @ {actual[2]:.0f} fps, fourcc={fourcc.decode(errors='replace')}",
          file=sys.stderr)
    if (actual[0], actual[1]) != (width, height):
        print(f"[sender] WARNING: asked for {width}x{height}, camera gave {actual[0]}x{actual[1]}",
              file=sys.stderr)
    return cap


def serve(host: str, port: int, cap: cv2.VideoCapture, quality: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    print(f"[sender] listening on {host}:{port}", file=sys.stderr)

    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]

    while True:
        conn, peer = srv.accept()
        # TCP_NODELAY: Nagle would coalesce our small headers with payload and
        # add up to 40 ms of pure, invisible latency.
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print(f"[sender] client {peer[0]}:{peer[1]} connected", file=sys.stderr)

        seq = 0
        t_report = time.monotonic()
        frames_since = 0
        encode_ms_sum = 0.0
        try:
            while True:
                ok, frame = cap.read()
                cap_ts = time.monotonic()
                if not ok:
                    print("[sender] camera read failed -- USB dropout?", file=sys.stderr)
                    break

                t0 = time.monotonic()
                ok, buf = cv2.imencode(".jpg", frame, encode_params)
                encode_ms_sum += (time.monotonic() - t0) * 1e3
                if not ok:
                    continue

                payload = buf.tobytes()
                h, w = frame.shape[:2]
                conn.sendall(pack_header(seq, cap_ts, w, h, len(payload)) + payload)

                seq += 1
                frames_since += 1
                now = time.monotonic()
                if now - t_report >= 5.0:
                    fps = frames_since / (now - t_report)
                    mbps = 0.0 if not frames_since else len(payload) * 8 * fps / 1e6
                    print(f"[sender] {fps:5.1f} fps  encode {encode_ms_sum / frames_since:4.1f} ms  "
                          f"~{mbps:5.1f} Mbit/s", file=sys.stderr)
                    t_report, frames_since, encode_ms_sum = now, 0, 0.0
        except (BrokenPipeError, ConnectionResetError):
            print("[sender] client disconnected", file=sys.stderr)
        finally:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURA EO sender (Raspberry Pi side)")
    ap.add_argument("--device", default="/dev/video0", help="V4L2 device path or index")
    ap.add_argument("--host", default="0.0.0.0", help="bind address")
    ap.add_argument("--port", type=int, default=8485)
    ap.add_argument("--size", default="1280x720", help="WxH")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--quality", type=int, default=80, help="JPEG quality 1-100")
    ap.add_argument("--exposure", default="auto",
                    help="'auto', or a manual V4L2 exposure value. Short manual "
                         "exposures freeze a fast target but need a bright sky; "
                         "auto is right indoors. UVC cameras REMEMBER this "
                         "between processes, so it is always set explicitly")
    ap.add_argument("--http", action="store_true",
                    help="serve MJPEG over HTTP instead of the raw TCP protocol; "
                         "consumable by cv2.VideoCapture and by any browser")
    args = ap.parse_args(argv)

    width, height = parse_size(args.size)
    cap = open_camera(args.device, width, height, args.fps, args.exposure)
    try:
        if args.http:
            from .http_stream import serve_http
            serve_http(args.host, args.port, cap, args.quality)
        else:
            serve(args.host, args.port, cap, args.quality)
    except KeyboardInterrupt:
        print("\n[sender] stopped", file=sys.stderr)
    finally:
        cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
