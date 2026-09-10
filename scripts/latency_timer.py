#!/usr/bin/env python3
"""Glass-to-display latency measurement for the EO link. Runs on the LAPTOP.

Why this exists: the Pi's clock and the laptop's clock are not synchronised, so
subtracting timestamps across the link produces a number that looks precise and
means nothing. The only honest measurement is optical.

Procedure:
  1. On the laptop:  python3 scripts/latency_timer.py
  2. Point the Pi's webcam at this window.
  3. Also on the laptop, in another terminal:
         python3 -m himkavach.eo.receiver --host 10.55.0.1 --show
  4. Arrange both windows side by side and take ONE screenshot.
  5. Read the counter twice: live in this window, and as it appears inside the
     received frame. The difference in milliseconds IS the end-to-end latency
     -- capture, encode, USB, decode, render, all of it, with no clock
     assumptions at all.
  6. Repeat 10 times. Report median and spread, not a single best case.

The counter is drawn in large digits with a moving block so a blurred frame is
still readable, and it deliberately shows only the low 5 digits of the
millisecond count (wraps every 100 s) to keep the digits big.
"""
import time

import cv2
import numpy as np

W, H = 1000, 420


def main() -> int:
    cv2.namedWindow("HIMKAVACH latency timer", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("HIMKAVACH latency timer", W, H)
    t0 = time.monotonic()
    print(__doc__)
    while True:
        elapsed_ms = int((time.monotonic() - t0) * 1000.0)
        frame = np.zeros((H, W, 3), dtype=np.uint8)

        # White on black, maximum contrast: the camera will be running short
        # exposure against a bright screen and anything subtler smears.
        cv2.putText(frame, f"{elapsed_ms % 100000:05d}", (40, 260),
                    cv2.FONT_HERSHEY_SIMPLEX, 6.0, (255, 255, 255), 14, cv2.LINE_AA)
        cv2.putText(frame, "ms", (820, 260), cv2.FONT_HERSHEY_SIMPLEX,
                    2.0, (120, 120, 120), 4, cv2.LINE_AA)

        # A block that steps once per 100 ms -- a coarse check that you read the
        # digits off the right frame when motion blur makes them ambiguous.
        x = (elapsed_ms // 100) % 10
        cv2.rectangle(frame, (40 + x * 95, 300), (120 + x * 95, 380), (255, 255, 255), -1)

        cv2.putText(frame, "point the Pi camera here, screenshot both windows together",
                    (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (90, 90, 90), 2, cv2.LINE_AA)

        cv2.imshow("HIMKAVACH latency timer", frame)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
