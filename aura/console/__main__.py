"""Run the AURA ops console.

    python3 -m aura.console                       # http://127.0.0.1:8600
    python3 -m aura.console --host 0.0.0.0        # let the projector laptop join
    python3 -m aura.console --no-eo               # rehearsing without the Pi

Every screen that opens the URL shares one environment, so the presenter's
sliders drive the projector. Anyone who can reach --host can move them too:
keep the default loopback bind unless a second screen actually needs it.
"""
from __future__ import annotations

import argparse
import sys

DEFAULT_EO_URL = "http://10.55.0.1:8485/stream.mjpg"   # Pi on the USB link


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURA ops console")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--eo-url", default=DEFAULT_EO_URL,
                    help="MJPEG feed from `python3 -m aura.eo.sender --http`")
    ap.add_argument("--no-eo", action="store_true", help="do not probe for the EO node")
    args = ap.parse_args(argv)

    import uvicorn
    from .server import create_app

    app = create_app(eo_url=None if args.no_eo else args.eo_url)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    print(f"[console] AURA ops console on http://{shown}:{args.port}", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
