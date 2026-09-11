"""AURA Intercept dashboard: live footage in, engage / let-go decision out.

    ./scripts/demo_intercept.sh                 # laptop webcam
    ./scripts/demo_intercept.sh --source clip.mp4
    ./scripts/demo_intercept.sh --source http://10.55.0.1:8485/stream.mjpg

Must run in the Drone_Ml venv (CUDA torch + ultralytics); the script finds it.
"""
from __future__ import annotations

import argparse
import sys

from .engine import Engine
from .server import serve


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aura.intercept",
                                 description="AURA Intercept dashboard")
    ap.add_argument("--source", default="0", help="camera index, stream URL, or video file")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8610)
    ap.add_argument("--device", default="0", help="CUDA device, or cpu")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--hfov", type=float, default=78.0, help="camera horizontal FOV, deg")
    ap.add_argument("--pi", default="http://10.55.0.1:8485/stream.mjpg", help="EO node stream URL")
    a = ap.parse_args(argv)

    engine = Engine(source=a.source, device=a.device, conf=a.conf, hfov=a.hfov)
    print(f"[intercept] dashboard on http://{a.host}:{a.port}  (Ctrl+C to stop)", file=sys.stderr)
    serve(engine, a.host, a.port, a.pi)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
