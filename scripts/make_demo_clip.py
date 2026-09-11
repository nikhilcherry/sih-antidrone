"""Build a rehearsal clip for the intercept dashboard from Drone_Ml val stills.

There is no drone to fly and no flight video on disk, so this makes one: a
virtual camera pans across two real val-set photographs (a quad by a tower
block, a quad by a pylon), separated by empty sky, then shows both at once
side by side -- a two-drone swarm for the single-effector queue. The drones move through
the frame the way a slewing camera would see them, so the tracker and the
friend/foe state machine get exercised end to end.

It is SYNTHETIC -- stills panned, not flight footage -- and the filename says
so. Real footage beats it; use this when there is none.

    ~/Projects/Drone_Ml/venv/bin/python scripts/make_demo_clip.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import cv2
import numpy as np

VAL = Path.home() / "Projects/Drone_Ml/dataset_public/images/val"
SHOTS = [
    "rf_anti_uav_01434_jpg.rf.fa865f7c2ba70d4a2b17a7b00eb8e17b.jpg",   # quad by tower block
    "rf_anti_uav_00902_jpg.rf.d9e4fb1c8e1ceedc9983d8f3584fcde2.jpg",   # quad by pylon
]
OUT = Path(__file__).resolve().parent.parent / "data/intercept_uploads/rehearsal_two_drones_SYNTHETIC.mp4"
W, H, FPS = 1280, 720, 30
WIN_W, WIN_H = 448, 252          # crop window on the 640x640 still -> ~2.9x zoom


def pan(img, start, end, seconds, shake=2.0):
    """Frames of a window sliding from `start` to `end` (window centre, px)."""
    n = int(seconds * FPS)
    for i in range(n):
        u = i / max(1, n - 1)
        u = 0.5 - 0.5 * math.cos(math.pi * u)            # ease in/out, like a slewing mount
        cx = start[0] + (end[0] - start[0]) * u + shake * math.sin(i * 0.37)
        cy = start[1] + (end[1] - start[1]) * u + shake * math.cos(i * 0.29)
        x0 = int(np.clip(cx - WIN_W / 2, 0, img.shape[1] - WIN_W))
        y0 = int(np.clip(cy - WIN_H / 2, 0, img.shape[0] - WIN_H))
        crop = img[y0:y0 + WIN_H, x0:x0 + WIN_W]
        yield cv2.resize(crop, (W, H), interpolation=cv2.INTER_CUBIC)


def pan_pair(a, b, a_path, b_path, seconds):
    """Two drones in one frame: each half is its own slewing window. Swarm segment."""
    for fa, fb in zip(pan(a, *a_path, seconds), pan(b, *b_path, seconds)):
        half = W // 2
        yield np.hstack([fa[:, half // 2: half // 2 + half], fb[:, half // 2: half // 2 + half]])


def main() -> int:
    a, b = (cv2.imread(str(VAL / s)) for s in SHOTS)
    if a is None or b is None:
        print(f"val stills not found under {VAL}", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(OUT), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))

    segments = [
        pan(a, (230, 130), (230, 130), 2.5),     # empty sky: SKY CLEAR
        pan(a, (230, 150), (420, 300), 7.0),     # drone A drifts into frame and across
        pan(b, (200, 120), (200, 120), 2.5),     # empty sky again
        pan(b, (180, 160), (380, 280), 7.0),     # drone B
        pan(a, (230, 130), (230, 130), 2.0),     # empty sky
        # Two hostiles at once: the single effector takes the nearer, queues the other.
        pan_pair(a, b, ((330, 260), (440, 300)), ((300, 230), (360, 300)), 9.0),
        pan(a, (230, 130), (230, 130), 1.5),
    ]
    n = 0
    for seg in segments:
        for frame in seg:
            vw.write(frame)
            n += 1
    vw.release()
    print(f"wrote {OUT}  ({n} frames, {n / FPS:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
