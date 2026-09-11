"""Live drone detection with a geometry gate. Runs on the LAPTOP.

Wraps the Drone_Ml detector rather than modifying it, and adds the one thing a
general-purpose detector cannot know: how big a drone is allowed to look.

The problem this solves, measured 2026-09-10: pointed at a room, the detector
put a box on 100% of frames at median confidence 0.767, spanning 89% of the
frame -- including a confident box around a person. Out-of-distribution input
produces degenerate full-frame boxes, and they are CONFIDENT, so raising --conf
removes none of them (every one survived 0.50).

Geometry removes them. A 0.3 m quadcopter is ~42 px wide at 10 m on a 78 deg
lens. A box spanning most of the frame implies a drone centimetres from the
lens. The threshold comes from `optics.max_plausible_px()`, so it is a physical
argument rather than a tuned constant, and it cannot reject a target at any
range worth engaging.

    python3 -m aura.eo.detect_live --source 0
    python3 -m aura.eo.detect_live --source http://10.55.0.1:8485/stream.mjpg

Rejected boxes are counted and shown, never silently dropped -- a filter you
cannot see is a filter you cannot defend.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from pathlib import Path

import cv2

from .optics import Sensor, max_plausible_px

DRONE_ML = Path.home() / "Projects" / "Drone_Ml"


def build_gate(frame_w: int, frame_h: int, hfov_deg: float, target_m: float,
               min_range_m: float, hard_cap_frac: float) -> float:
    """Maximum plausible box width in pixels, for this frame size and lens."""
    sensor = Sensor("live", frame_w, frame_h, hfov_deg)
    geometric = max_plausible_px(sensor, target_m, min_range_m)
    # A hard cap keeps the gate sane if the FOV is mis-stated: no drone
    # detection should ever span a third of the frame regardless of optics.
    return min(geometric, hard_cap_frac * frame_w)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gated live drone detection")
    ap.add_argument("--source", default="0", help="camera index, URL, or file")
    ap.add_argument("--weights", default=str(
        DRONE_ML / "runs/detect/runs/detect/p2_s/weights/best.pt"))
    ap.add_argument("--tracker", default=str(DRONE_ML / "cfg/bytetrack_drone.yaml"))
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default="0")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--det-floor", type=float, default=0.03,
                    help="what reaches the TRACKER; ByteTrack's second "
                         "association stage needs low-score boxes")
    ap.add_argument("--hfov", type=float, default=78.0,
                    help="camera horizontal FOV in degrees; sets the gate")
    ap.add_argument("--target-size", type=float, default=0.3,
                    help="expected drone width in metres")
    ap.add_argument("--min-range", type=float, default=3.0,
                    help="closest range a real target could be. The gate only "
                         "has to reject the IMPOSSIBLE, not be maximally tight: "
                         "at 3 m the gate is ~47 px on a 640-wide frame, which "
                         "still rejects the measured 573 px full-frame box by "
                         "12x while accepting a drone anywhere beyond 3 m. The "
                         "deployed system would use 10 m")
    ap.add_argument("--max-box-frac", type=float, default=0.33,
                    help="hard cap as a fraction of frame width")
    ap.add_argument("--no-gate", action="store_true",
                    help="disable the geometry gate. Needed when rehearsing "
                         "with a drone PHOTO held to the camera -- the pictured "
                         "drone is 30 cm away and the gate is right to reject it")
    ap.add_argument("--save", metavar="PATH", help="write an annotated mp4")
    args = ap.parse_args(argv)

    from ultralytics import YOLO

    weights = Path(args.weights)
    if not weights.exists():
        raise SystemExit(f"weights not found: {weights}")
    model = YOLO(str(weights))

    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"cannot open source: {args.source}")
    if isinstance(src, int):
        # UVC controls persist between processes; a previous manual exposure
        # leaves the feed black. Say what we want rather than inheriting it.
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)

    ok, frame = cap.read()
    if not ok:
        raise SystemExit("source opened but returned no frame")
    H, W = frame.shape[:2]

    gate_px = build_gate(W, H, args.hfov, args.target_size,
                         args.min_range, args.max_box_frac)
    if args.no_gate:
        print("[detect] geometry gate DISABLED", file=sys.stderr)
    else:
        print(f"[detect] frame {W}x{H}, {args.hfov:.0f} deg lens", file=sys.stderr)
        print(f"[detect] gate: reject boxes wider than {gate_px:.0f} px "
              f"({gate_px / W:.1%} of frame) -- a {args.target_size} m drone at "
              f"{args.min_range:.0f} m", file=sys.stderr)

    writer = None
    if args.save:
        writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"), 25, (W, H))

    fps_hist: deque[float] = deque(maxlen=30)
    frames = kept_frames = shown = rejected = 0
    print("[detect] running. Q to quit.", file=sys.stderr)

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t0 = time.time()
        r = model.track(frame, imgsz=args.imgsz, device=args.device,
                        conf=min(args.det_floor, args.conf), persist=True,
                        tracker=args.tracker, verbose=False)[0]
        fps_hist.append(1.0 / max(1e-6, time.time() - t0))
        frames += 1

        kept = []
        if r.boxes is not None and len(r.boxes):
            ids = (r.boxes.id.int().tolist() if r.boxes.id is not None
                   else [None] * len(r.boxes))
            for box, conf, tid in zip(r.boxes.xyxy.cpu().numpy(),
                                      r.boxes.conf.cpu().numpy(), ids):
                x1, y1, x2, y2 = box
                width = x2 - x1
                too_big = (not args.no_gate) and width > gate_px
                if too_big:
                    rejected += 1
                    # Drawn, dimmed, and labelled: a filter you cannot see is a
                    # filter you cannot defend to a panel.
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)),
                                  (90, 90, 90), 1)
                    cv2.putText(frame, f"rejected: {width:.0f}px > {gate_px:.0f}px",
                                (int(x1) + 4, int(y1) + 18),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 120, 120), 1)
                    continue
                if conf >= args.conf:
                    kept.append((box, float(conf), tid))

        if kept:
            kept_frames += 1
        for box, conf, tid in kept:
            x1, y1, x2, y2 = (int(v) for v in box)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
            tag = f"drone {conf:.2f}" + (f"  id{tid}" if tid is not None else "")
            cv2.putText(frame, tag, (x1, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            shown += 1

        fps = sum(fps_hist) / len(fps_hist)
        cv2.putText(frame, f"{fps:5.1f} FPS   drones: {len(kept)}   "
                           f"gate rejected: {rejected}",
                    (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.imshow("AURA EO - gated (Q to quit)", frame)
        if writer:
            writer.write(frame)
        if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
            break

    cap.release()
    if writer:
        writer.release()
        print(f"[detect] saved {args.save}", file=sys.stderr)
    cv2.destroyAllWindows()
    print(f"\n[detect] frames {frames}   frames with a drone {kept_frames} "
          f"({kept_frames / max(1, frames):.0%})   boxes drawn {shown}   "
          f"gate rejected {rejected}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
