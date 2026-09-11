"""EO receiver -- runs ON THE LAPTOP.

Connects to the Pi's sender, decodes frames, and reports link health. Also the
intake point for the detection track: `frames()` is a generator, so the YOLO
stage plugs in without knowing anything about sockets.

    python3 -m aura.eo.receiver --host 10.55.0.1 --show

Reported numbers, and what each one is honestly worth:
  fps        -- frames actually delivered per second end to end. Trustworthy.
  jitter     -- stdev of inter-arrival gaps. This is what a tracker feels; a
                stable 20 fps beats a bursty 30.
  decode     -- laptop-side JPEG decode cost. Trustworthy.
  gaps       -- frames the sender emitted that never arrived (sequence holes).
  NOT REPORTED: end-to-end latency. The two clocks are unsynchronised, so any
                such figure would be fiction. Measure it with the screen-timer
                procedure in docs/eo_bringup.md and quote that instead.
"""

from __future__ import annotations

import argparse
import socket
import statistics
import sys
import time
from collections import deque
from typing import Iterator

import cv2
import numpy as np

from .protocol import HEADER_SIZE, recv_exactly, unpack_header


class LinkStats:
    def __init__(self, window: int = 120) -> None:
        self.gaps: deque[float] = deque(maxlen=window)
        self.decode_ms: deque[float] = deque(maxlen=window)
        self.dropped = 0
        self.received = 0
        self._last_arrival: float | None = None
        self._last_seq: int | None = None

    def note(self, seq: int, arrival: float, decode_ms: float) -> None:
        self.received += 1
        self.decode_ms.append(decode_ms)
        if self._last_arrival is not None:
            self.gaps.append((arrival - self._last_arrival) * 1e3)
        self._last_arrival = arrival
        if self._last_seq is not None and seq != self._last_seq + 1:
            self.dropped += max(0, seq - self._last_seq - 1)
        self._last_seq = seq

    def summary(self) -> str:
        if len(self.gaps) < 2:
            return "collecting..."
        mean_gap = statistics.fmean(self.gaps)
        jitter = statistics.stdev(self.gaps)
        return (f"{1000.0 / mean_gap:5.1f} fps  gap {mean_gap:5.1f} ms  "
                f"jitter {jitter:4.1f} ms  decode {statistics.fmean(self.decode_ms):4.1f} ms  "
                f"gaps {self.dropped}")


def frames(host: str, port: int, retry: bool = True) -> Iterator[tuple[int, float, float, np.ndarray]]:
    """Yield (seq, sender_capture_ts, decode_ms, BGR frame). Reconnects if dropped."""
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            sock.settimeout(5.0)
            sock.connect((host, port))
            sock.settimeout(None)
            print(f"[receiver] connected to {host}:{port}", file=sys.stderr)
            while True:
                seq, cap_ts, w, h, nbytes = unpack_header(recv_exactly(sock, HEADER_SIZE))
                payload = recv_exactly(sock, nbytes)
                t0 = time.monotonic()
                frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
                decode_ms = (time.monotonic() - t0) * 1e3
                if frame is None:
                    print(f"[receiver] corrupt JPEG at seq {seq}, skipping", file=sys.stderr)
                    continue
                yield seq, cap_ts, decode_ms, frame
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"[receiver] link down: {exc}", file=sys.stderr)
            if not retry:
                return
            time.sleep(1.0)
        finally:
            sock.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURA EO receiver (laptop side)")
    ap.add_argument("--host", default="10.55.0.1", help="Pi address (USB gadget default)")
    ap.add_argument("--port", type=int, default=8485)
    ap.add_argument("--show", action="store_true", help="display the stream (q to quit)")
    ap.add_argument("--record", metavar="PATH", help="write frames to an .mp4")
    ap.add_argument("--max-frames", type=int, default=0, help="stop after N frames (0 = forever)")
    ap.add_argument("--no-retry", action="store_true")
    args = ap.parse_args(argv)

    stats = LinkStats()
    writer: cv2.VideoWriter | None = None
    t_report = time.monotonic()
    n = 0

    try:
        for seq, _cap_ts, decode_ms, frame in frames(args.host, args.port, retry=not args.no_retry):
            arrival = time.monotonic()
            stats.note(seq, arrival, decode_ms)
            n += 1

            if args.record:
                if writer is None:
                    h, w = frame.shape[:2]
                    writer = cv2.VideoWriter(args.record, cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (w, h))
                writer.write(frame)

            if args.show:
                cv2.imshow("AURA EO", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            now = time.monotonic()
            if now - t_report >= 5.0:
                print(f"[receiver] {stats.summary()}", file=sys.stderr)
                t_report = now

            if args.max_frames and n >= args.max_frames:
                break
    except KeyboardInterrupt:
        print("\n[receiver] stopped", file=sys.stderr)
    finally:
        if writer is not None:
            writer.release()
        if args.show:
            cv2.destroyAllWindows()

    print(f"[receiver] final: {stats.summary()}  ({stats.received} frames)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
