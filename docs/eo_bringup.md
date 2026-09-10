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
