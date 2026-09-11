"""MJPEG-over-HTTP server for the EO link.

The raw TCP mode in `sender.py` gives per-frame sequence numbers and link-health
stats. This mode gives something more valuable for a demo: a URL that everything
already understands. `cv2.VideoCapture("http://10.55.0.1:8485/stream.mjpg")`
opens it directly, which means the detector in the Drone_Ml repo consumes the Pi
feed through its existing `--source` argument with no code change at all. A
browser opens the same URL, so a phone on the link is a second monitor.

One capture thread owns the camera and publishes the newest frame; each client
sends whatever is current when it is ready. Slow clients therefore DROP frames
rather than queue them, which is the correct behaviour for live video -- a
queued client drifts further behind the target with every frame it fails to
keep up with, and for a tracking demo a late frame is worse than no frame.
"""

from __future__ import annotations

import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

BOUNDARY = "auraframe"

_PAGE = b"""<!doctype html><meta charset=utf-8>
<title>AURA EO</title>
<style>
 body{margin:0;background:#0b0d10;color:#c9d1d9;font:14px system-ui,sans-serif;
      display:flex;flex-direction:column;align-items:center;gap:12px;padding:16px}
 img{max-width:100%;border:1px solid #30363d;border-radius:6px}
 code{color:#8b949e}
</style>
<h3>AURA &mdash; EO node</h3>
<img src="/stream.mjpg" alt="live feed">
<code>cv2.VideoCapture("http://HOST:PORT/stream.mjpg")</code>
"""


class FrameBroker:
    """Newest-frame-wins hand-off between the capture thread and the clients."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._seq = 0
        self._stop = False

    def publish(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg = jpeg
            self._seq += 1
            self._cond.notify_all()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    def wait_for_next(self, last_seq: int, timeout: float = 5.0):
        """Block until a frame newer than `last_seq` exists. Returns (seq, jpeg)."""
        with self._cond:
            if not self._cond.wait_for(
                lambda: self._stop or (self._jpeg is not None and self._seq != last_seq),
                timeout=timeout,
            ):
                return last_seq, None          # timed out: camera stalled
            if self._stop:
                return last_seq, None
            return self._seq, self._jpeg


def capture_loop(cap: cv2.VideoCapture, broker: FrameBroker, quality: int) -> None:
    params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    frames = 0
    t_report = time.monotonic()
    while True:
        ok, frame = cap.read()
        if not ok:
            print("[http] camera read failed -- USB dropout?", file=sys.stderr)
            broker.stop()
            return
        ok, buf = cv2.imencode(".jpg", frame, params)
        if ok:
            broker.publish(buf.tobytes())
            frames += 1
        now = time.monotonic()
        if now - t_report >= 5.0:
            print(f"[http] capturing {frames / (now - t_report):5.1f} fps", file=sys.stderr)
            frames, t_report = 0, now


def make_handler(broker: FrameBroker):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"     # no keep-alive bookkeeping to get wrong

        def log_message(self, fmt, *args):
            pass                           # silence per-request noise

        def do_HEAD(self) -> None:
            # curl -I should not 501; probing the URL is the first thing
            # anyone does when the stream looks dead.
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(_PAGE)))
                self.end_headers()
                self.wfile.write(_PAGE)
                return

            if self.path not in ("/stream.mjpg", "/stream", "/video"):
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type",
                             f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.end_headers()
            print(f"[http] client {self.client_address[0]} attached", file=sys.stderr)

            last = -1
            try:
                while True:
                    last, jpeg = broker.wait_for_next(last)
                    if jpeg is None:
                        break
                    self.wfile.write(f"--{BOUNDARY}\r\n".encode())
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpeg)))
                    self.end_headers()
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                print(f"[http] client {self.client_address[0]} gone", file=sys.stderr)

    return Handler


def serve_http(host: str, port: int, cap: cv2.VideoCapture, quality: int) -> None:
    broker = FrameBroker()
    threading.Thread(target=capture_loop, args=(cap, broker, quality), daemon=True).start()

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = Server((host, port), make_handler(broker))
    shown = host if host not in ("0.0.0.0", "") else "10.55.0.1"
    print(f"[http] serving on http://{shown}:{port}/stream.mjpg", file=sys.stderr)
    print(f"[http] open http://{shown}:{port}/ in a browser for the demo page",
          file=sys.stderr)
    try:
        srv.serve_forever()
    finally:
        broker.stop()
        srv.server_close()
