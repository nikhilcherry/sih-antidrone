# EO node bring-up — Raspberry Pi + USB webcam → laptop

The electro-optical channel. A Pi carries the camera at the mount, the laptop
carries the RTX 5050 and runs detection. One USB cable between them, no router,
no Wi-Fi — which is the right choice for a field demo where there is no
infrastructure to depend on, and a defensible one for a latency figure.

Everything below has been run except the parts that need the Pi itself; those
are marked **(untested — no board yet)**.

---

## 1. What to buy

### Board — get a Pi 4 (4 GB)

USB gadget mode plus a USB webcam constrains this harder than it looks, because
gadget mode consumes an OTG-capable port and a webcam needs a host port:

| Board | Gadget mode | Port left for the webcam | Verdict |
|---|---|---|---|
| **Pi 4 (2/4/8 GB)** | USB-C, works | 4× USB-A free | **buy this** |
| Pi 5 | USB-C, fussier on older kernels | 4× USB-A free | works, but power is worse (below) |
| Pi Zero 2 W | micro-USB OTG, works | **none** — the OTG port *is* the only data port | blocked for this plan |
| Pi 3 / 2 / older | no OTG controller | — | impossible |

The Zero 2 W looks attractive for a mount-mounted camera and is the one that
fails: its two micro-USB ports are PWR (power only, no data lines) and USB
(OTG). Use the OTG port to tether the laptop and the webcam has nowhere to go.
An OTG hub does not rescue this — the port is either device or host, not both.
If you end up with a Zero 2 W anyway, drop gadget mode and stream over Wi-Fi;
`setup_pi_gadget.sh` will warn you before it does anything.

### The power trap, which will bite you

A Pi 4 wants **5 V / 3 A**. A laptop port supplies far less:

| Source | Guaranteed current |
|---|---|
| USB-A 2.0 | 0.5 A |
| USB-A 3.0 | 0.9 A |
| USB-C, no PD negotiation | 0.5–1.5 A depending on what the laptop advertises |

A Pi 4 with a webcam attached and the encoder busy draws roughly **1.0–1.3 A**.
It will boot off the laptop and then brown out under load — and the symptom is
not a clean failure, it is *frames going missing and the clock silently
throttling*, which you will misread as a code problem for an hour.

The Pi 4 has only one USB-C port, and gadget mode is using it. So power comes
in on the GPIO header instead:

> Feed **5 V into pin 4** and **GND into pin 6** from a regulated 5 V / 3 A
> supply. USB-C then carries data only.
> This bypasses the Pi's input polyfuse and protection circuit — use a proper
> regulated supply, get the polarity right, and do not also feed USB-C power at
> the same time.

Verify after every session, before trusting any measurement:

```bash
vcgencmd get_throttled     # 0x0 = clean.  bit 0 = under-voltage NOW
                           #               bit 16 = under-voltage HAS occurred
```

Pi 5 makes this worse, not better — it wants 5 A and negotiates PD, so a laptop
port is even further from adequate. That is the reason the recommendation is a
Pi 4 despite the Pi 5 being the better computer.

### Camera — the stock-webcam decision is the one that costs you range

Run `python3 -m himkavach.eo.optics` for the live table. As of now:

| Camera | IFOV | px on a 0.3 m drone @ 500 m | Recognise range |
|---|---|---|---|
| Logitech C920, stock 78° | 709 µrad/px | 0.8 px | **53 m** |
| Generic webcam, 60° | 818 µrad/px | 0.7 px | 46 m |
| Arducam UVC + 16 mm M12 | 198 µrad/px | 3.0 px | 189 m |
| Arducam UVC + 35 mm M12 | 92 µrad/px | 6.5 px | 408 m |

A stock wide-angle webcam recognises a small quadcopter out to about **50 m**.
That is not a surveillance sensor, and no amount of model tuning fixes it —
the target is under one pixel. If the EO channel is to carry any weight in the
pitch, buy a **USB UVC module with an M12 lens mount** (Arducam and similar,
roughly ₹4–7k) and fit a 16–35 mm lens, rather than a sealed consumer webcam.

Two consequences worth saying out loud in the presentation, both guarded by
tests in `tests/test_eo.py`:

1. **The compensated pointing error is sub-pixel on a wide lens.** 295 µrad on
   the C920 is 0.42 px — the servo out-resolves the sensor, so the compensation
   result is *invisible* in the EO channel. It only becomes visible below about
   a 33° lens. This tells you where spending helps.
2. **Weather is not the EO limit here; geometry is.** Koschmieder extinction
   clamps range to the meteorological visibility, but a 35 mm lens gives up at
   ~1.6 km on this target anyway. At 2 km visibility the atmosphere changes
   nothing. The environmental degradation story applies to the mechanics and
   the RF chain — claiming it improves *this* camera's reach would not survive
   a question.

Honest framing for the panel: **EO is a short-range cueing and identification
sensor slaved to the RF channel, and we quantify the gap rather than paper over
it.** That is consistent with the rest of the project's posture on the SDR.

### Cable

A **data** USB cable. Charge-only cables are extremely common, will power the
Pi perfectly, and carry nothing — the Pi boots, no interface appears on the
laptop, and everything looks broken. `laptop_usb_link.sh` names this first in
its error message for a reason.

---

## 2. Bring-up **(untested — no board yet)**

On the Pi, with the repo cloned:

```bash
./scripts/setup_pi_gadget.sh            # add --service to autostart the sender
sudo reboot
```

It enables the `dwc2` OTG controller, loads `g_ether` at boot, and pins
`10.55.0.1` on `usb0` via a systemd unit bound to the interface. It backs up
`config.txt` and `cmdline.txt` first and prints the undo command.

Two failure modes it guards against, both of which look like success:
`dtoverlay=dwc2` landing under a filtered section of `config.txt` where it is
ignored, and `cmdline.txt` picking up a second line — that file must stay one
line or everything after the newline is silently discarded.

Then plug the cable into the laptop (Pi 4: the USB-C port) and:

```bash
./scripts/laptop_usb_link.sh
```

It finds the `cdc_ether`/`rndis_host` interface, pins `10.55.0.2` on it, and
pings the Pi. It creates a **NetworkManager profile** rather than calling
`ip addr add`, because on Pop!\_OS NetworkManager reverts a raw address the
moment it notices the carrier.

Stream:

```bash
# Pi
python3 -m himkavach.eo.sender --device /dev/video0 --size 1280x720 --fps 30
# laptop
python3 -m himkavach.eo.receiver --host 10.55.0.1 --show
```

---

## 3. Test it now, without the Pi ✅ *(run on this laptop)*

```bash
./scripts/eo_loopback_test.sh                    # 1280x720, 200 frames
./scripts/eo_loopback_test.sh /dev/video0 640x480 120
```

Measured on this laptop against its built-in webcam:

| Mode | Delivered | Inter-frame gap | Jitter | Encode | Decode | Dropped |
|---|---|---|---|---|---|---|
| 1280×720 | 15.0 fps | 66.7 ms | 4.2 ms | 4.1 ms | 6.0 ms | 0 |
| 640×480 | 20.0 fps | 50.1 ms | 14.3 ms | 1.3 ms | 1.5 ms | 0 |

Note the 15 fps at 720p when 30 was requested. That is **this laptop's webcam**,
not the code and not the transport — raw capture with no networking at all
measures 15.0 fps, and forcing manual exposure only reaches 18 fps, so it is a
sensor limit rather than an auto-exposure one. Check the real numbers on
whatever camera you buy with `v4l2-ctl --list-formats-ext`.

Bandwidth is a non-issue: 720p JPEG at q80 runs about **7 Mbit/s**, against
roughly 100–200 Mbit/s realistically available on a USB 2.0 `g_ether` link.

---

## 4. Measuring latency honestly

The Pi's clock and the laptop's clock are not synchronised, so any figure from
subtracting a Pi timestamp from a laptop timestamp is fiction. `protocol.py`
carries the capture timestamp anyway, for *sender-side* pipeline timing only,
and `receiver.py` deliberately refuses to print an end-to-end latency.

Measure it optically instead:

```bash
python3 scripts/latency_timer.py                          # laptop
python3 -m himkavach.eo.receiver --host 10.55.0.1 --show  # laptop, other terminal
```

Point the Pi's camera at the timer window, put both windows on screen, take one
screenshot, and read the counter twice — live, and as it appears inside the
received frame. The difference is glass-to-display latency with no clock
assumptions. Repeat ten times and report **median and spread**, not the best
run. That procedure, written down, is exactly the kind of thing the PS clause
on "field evaluation methodologies" is asking for.

---

## 5. Design notes

**Why raw TCP and not RTSP/WebRTC.** Those stacks hide buffering inside
themselves, and the number in the pitch has to be defensible. Length-prefixed
JPEG over one TCP socket is ~60 lines, and every millisecond is attributable.

**Latency details that are easy to lose.** `CAP_PROP_BUFFERSIZE=1` — without it
V4L2 queues frames and the tracker follows a drone that moved 200 ms ago.
`TCP_NODELAY` — Nagle coalesces the small header with the payload and adds up
to 40 ms that no profiler will show you. `FOURCC=MJPG` — raw YUYV at 720p30
needs ~442 Mbit/s and USB 2.0 will not carry it, so the camera silently drops
to ~10 fps.

**Zero-transcode alternative.** The sender decodes the camera's MJPEG and
re-encodes, costing a few ms on a Pi 4. To avoid it entirely:

```bash
ffmpeg -f v4l2 -input_format mjpeg -video_size 1280x720 -i /dev/video0 \
       -c:v copy -f mpjpeg tcp://10.55.0.2:8485?listen
```

Cheaper, but you lose the sequence numbers and per-frame timing that
`receiver.py` reports link health from. Worth it only if the Pi turns out to be
CPU-bound.

**Where this plugs into detection.** `receiver.frames()` is a generator
yielding `(seq, cap_ts, decode_ms, frame)`. The YOLO stage consumes that and
never learns there was a socket involved. That venv still needs the cu128 torch
build — see `requirements.txt`.


---

## 6. Detection on the laptop ✅ *(measured)*

The detector is the model from **`~/Projects/Drone_Ml`**, used unmodified. That
repo's `realtime_track.py` already accepts a URL as `--source`, and the Pi can
serve MJPEG over HTTP, so the two halves meet with no glue code:

```bash
# Pi
python3 -m himkavach.eo.sender --http

# laptop
./scripts/demo_live.sh                 # or --local to rehearse on the laptop cam
```

`demo_live.sh` picks the `p2_s` weights, checks the stream is actually serving
before handing off (OpenCV reports an unopenable URL as an empty capture and no
reason at all), and forces `QT_QPA_PLATFORM=xcb`.

### Which model, and why

| Run | val mAP50 | Notes |
|---|---|---|
| **`p2_s`** | **0.915** | P2 detection head — extra stride-4 level for small objects |
| `public_s` | 0.904 (test 0.899) | no P2 head |

`p2_s` is the pick. The P2 head exists precisely for targets a few pixels
across, which is what §1 shows a drone is at any useful range.

Spot-checked here against the leak-free val split, at `conf 0.25`:

| Sample | Recall | False alarms |
|---|---|---|
| 60 positives / 40 negatives | 83% | 2% |
| 120 positives / 60 negatives | 77.5% | 3.3% |

Consistent with the repo's reported R = 0.855. **Note the val split contains 66
negatives** — an image with no drone is not a failure, and the first file
alphabetically is one of them, which makes a naive "run it on the first image"
smoke test look broken.

### Measured speed, and what it means

| Path | Rate | Live-capable at 30 fps? |
|---|---|---|
| Pure inference, imgsz 640 | **131 FPS** | yes, 4× headroom |
| TTA (`--tta`) | 46.7 FPS | yes |
| Tiled (`--tile`, 320 px) | 5.2 FPS | **no** |
| Full chain, laptop webcam | 17.5 FPS | camera-limited, not GPU-limited |

Three conclusions:

1. **The camera is the bottleneck, not the GPU.** The chain ran at 17.5 FPS
   because this laptop's webcam delivers 18. The RTX 5050 has roughly 7× more
   detection throughput than the sensor can feed it. Buying a faster camera buys
   frames; optimising the model buys nothing.
2. **Tiling stays offline.** At 5.2 FPS it cannot drive a live feed, confirming
   the warning already in `realtime_track.py`. Use it on recorded footage.
3. **Skip TTA for the demo.** It is fast enough, but measured here it bought
   +3.3 pp recall for +1.7 pp false alarms on 120/60 images — inside the noise
   at that sample size — and the Drone_Ml test split already showed TTA
   *losing* (mAP50 0.891 vs 0.899). Fast does not mean better.

### Honest limits to state before anyone asks

`realtime_track.py` says it plainly and so should the pitch: the model was
trained on drones against **sky and buildings**. Against grass or trees it
detects far less, and tracking cannot recover a target the detector never finds
once. Combined with §1, the EO channel is a **short-range cueing and
identification sensor**, and the RF channel is what carries detection range.


---

## 7. Two failures found while rehearsing (2026-09-10)

### The feed came up black

Mean frame brightness was 1/255 — no light at all, not a dark room. Cause: a
benchmark had put the camera in **manual exposure**, and **UVC control values
persist in the kernel driver after the process that set them exits**. Every
later program inherited a black frame.

Guarded now: `sender.py` sets the exposure mode explicitly on every open
(`--exposure auto|<value>`), and `demo_live.sh --local` resets auto-exposure
before handing the camera over. If a feed is ever black, suspect this before
suspecting the camera or the cable — it applies to the Pi's USB camera too.

For a real drone against bright sky, prefer **manual** short exposure
(`--exposure 50`): it freezes a fast target instead of smearing it. Auto is only
right indoors.

### The model called a person a drone, at 0.71 confidence

With a normal picture, the detector put a box on **100% of frames, median
confidence 0.767** — pointed at an empty room and at a person. The box measured
573x478 in a 640x480 frame: **89% of the frame area**. That is not a detection,
it is a degenerate full-frame box on out-of-distribution input.

This is exactly the limit `realtime_track.py` already states — trained on drones
against sky and buildings — but it is worth seeing the failure shape, because it
is confident, not tentative. Raising `--conf` does **not** help: every one of
those boxes survives a 0.50 threshold.

**Consequences for the demo:**

1. **Point the camera at sky.** Indoors, at a room, or at a person, it will
   confidently label them a drone. In front of a judging panel that is worse
   than detecting nothing.
2. **A geometry gate rejects it for free — now implemented.** §1 gives the
   argument: a 0.3 m drone is 4.2 px wide at 100 m on a wide lens. A box
   spanning 89% of the frame would be a drone a few centimetres from the lens,
   which is physically absurd. `optics.max_plausible_px()` supplies the
   threshold and `himkavach/eo/detect_live.py` applies it.

### Measured, same camera, same room

| | Boxes drawn | Frames with a "drone" |
|---|---|---|
| Ungated (`--raw`) | 2312 | **99%** |
| Gated (default) | **0** | **0%** (866 rejected) |

The gate defaults to a 3 m minimum range — 47 px on a 640-wide frame. It only
has to reject the *impossible*, not be maximally tight: that still vetoes the
measured 573 px box by 12x while accepting a drone anywhere beyond 3 m. Tests
assert it never rejects a target at 15 m, 50 m, 200 m or 1 km. The deployed
system would use 10 m.

Rejected boxes are drawn dimmed and labelled rather than dropped silently — a
filter nobody can see is a filter nobody can defend. `--raw` runs the ungated
Drone_Ml script for a before/after comparison, which is worth showing a panel.

**Use `--no-gate` when rehearsing with a drone photo held to the camera.** The
pictured drone is 30 cm away, so the gate is correct to reject it.
