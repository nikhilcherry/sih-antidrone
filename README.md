# HIMKAVACH — High-Altitude Anti-Drone System

**SIH 2026 · PS SIH26050 · DRDO / Dept. of Defence Production (iDEX) · Hardware**

> Anti-drone systems are tuned for standard conditions. Above 3000 m — thin air,
> −40 °C, dust, high wind — the mechanics, electronics, RF and electro-optics all
> shift, and a system specified in microradians loses its pointing budget to
> physics nobody compensated for.

HIMKAVACH is a detect–track–identify–engage pipeline **plus** the thing the PS
actually asks for and most teams will skip: a quantified environmental
degradation model and a compensation layer that holds pointing accuracy
constant with altitude.

## The headline result

`python3 demo_pointing.py`

| Scenario | T (°C) | ρ (kg/m³) | Uncompensated | Compensated |
|---|---|---|---|---|
| Sea level, standard day | +15.0 | 1.225 | 271 µrad | 246 µrad |
| Leh, 3500 m, winter | −27.8 | 0.934 | 517 µrad | 280 µrad |
| Forward post, 4500 m | −39.2 | 0.860 | 582 µrad | 292 µrad |
| Siachen-class, 5500 m | −45.8 | 0.774 | 697 µrad | 295 µrad |

The claim is not "we made it better." It is **performance becomes
altitude-invariant**: uncompensated RMS error grows 2.6× from sea level to
5500 m; compensated error moves 20%.

## What degrades, and why

| Effect | Model | At 4500 m / −39 °C |
|---|---|---|
| Cable bundle stiffening | Sigmoid about jacket Tg (−30 °C) | ×9.1 stiffness |
| Bearing drag | Vogel grease viscosity | ×6.0 friction |
| Thermo-elastic boresight | CTE mismatch, 8 µrad/K | −474 µrad **static** |
| Wind torque | ½ρv²C_dAr | 2.09 N·m (*less* than sea level at equal speed) |
| Battery capacity | Li-ion discharge curves | 31% of rated |
| Motor torque limit | Magnet gain vs. convective loss | 93% |
| Gyro bias instability | Cold tempco | 1.54 °/hr |
| EO range (2 km vis) | Koschmieder extinction | 2000 m of 4000 m |
| RF noise floor | 10·log₁₀(T/T_ref) | **−0.98 dB (improves)** |
| HV standoff | Paschen right branch | derate to 0.67 |

Two findings worth defending out loud, because they run against intuition:
**thin air makes electronics run hotter** (convection ∝ ρ^0.8) despite −40 °C
ambient, and **RF propagation is essentially unharmed** — the RF subsystem
degrades through its electronics and mechanics, not its physics.

## Compensation

1. **Thermo-elastic boresight feedforward** — calibrated θ(T) map, removes ~88%.
2. **Cable restoring-torque feedforward** — model-based, 85% accurate.
3. **Gain scheduling** — retunes against the measured cold plant to hold loop bandwidth.
4. **Wind disturbance observer** — reconstructs unmodelled torque, ~70% authority.
5. **Gyro bias state estimation** — low-passed bias tracker.

Every residual is deliberate. Perfect cancellation would be a lie.

## Neutralisation

Modelled, never transmitted. RF jamming is unlawful for us to emit (Indian
Telegraph Act / WPC licensing), so the soft-kill path is a J/S link-budget model
evaluated against the tracked target. The SDR is receive-only.

## Layout

```
himkavach/atmosphere.py    ISA + cold-bias, Paschen derating, convective scaling
himkavach/degradation.py   per-subsystem environmental degradation models
himkavach/sim.py           azimuth pointing loop, uncompensated vs compensated
himkavach/eo/optics.py     sensor geometry: what the camera can actually see
himkavach/eo/sender.py     Pi side: USB webcam -> JPEG -> TCP
himkavach/eo/receiver.py   laptop side: decode, link health, feeds detection
himkavach/eo/protocol.py   the wire format, 24-byte header per frame
demo_pointing.py           the headline sweep
tests/test_models.py       physics guards — these protect the pitch numbers
tests/test_eo.py           wire-format and sensor-geometry guards
scripts/setup_sdr.sh       RTL-SDR bring-up (blacklist DVB-T, udev rules)
scripts/setup_pi_gadget.sh Pi USB-Ethernet gadget mode (run ON the Pi)
scripts/laptop_usb_link.sh laptop end of the USB link
scripts/eo_loopback_test.sh prove the EO chain with no Pi attached
scripts/latency_timer.py   optical glass-to-display latency measurement
scripts/demo_live.sh       the demo: Pi camera -> laptop -> detector -> boxes
himkavach/eo/http_stream.py MJPEG over HTTP, so any tool can open the feed
```

## Setup

```bash
pip install -r requirements.txt
python3 -m pytest tests/ -q
python3 demo_pointing.py

./scripts/setup_sdr.sh        # once, then replug the dongle
```

```bash
./scripts/eo_loopback_test.sh   # EO chain, laptop webcam, no Pi needed
```

Vision track needs its own venv — system torch is CPU-only and the RTX 5050
(Blackwell, sm_120) requires the cu128 build. See `requirements.txt`.

## The EO channel, and its honest limit

A Raspberry Pi carries the camera at the mount and streams to the laptop over a
single USB cable in Ethernet-gadget mode. Bring-up, the buy decision and the
power trap are in [`docs/eo_bringup.md`](docs/eo_bringup.md).

`python3 -m himkavach.eo.optics` derives detection range from sensor geometry
rather than asserting it. Two results we state rather than hide:

- A **stock wide-angle webcam recognises a 0.3 m quadcopter to about 50 m**.
  The target is under one pixel at 500 m. EO is a short-range cueing and ID
  sensor slaved to the RF channel — an M12 lens mount and a 35 mm lens takes
  that to ~400 m, which is where the money should go.
- The **295 µrad compensated pointing error is 0.42 px on that webcam** — the
  servo out-resolves the sensor, so the compensation result is invisible in the
  EO channel until the lens is narrower than ~33°.

Link latency is measured optically (`scripts/latency_timer.py`), never by
subtracting unsynchronised clocks across the USB link.

## The live demo

```bash
python3 -m himkavach.eo.sender --http     # on the Pi
./scripts/demo_live.sh                    # on the laptop  (--local to rehearse)
```

Detection is the model from [`Drone_Ml`](https://github.com/nikhilcherry/Drone_Ml)
(`p2_s`, val mAP50 **0.915**), used **unmodified** — the Pi serves MJPEG over
HTTP and that repo's `realtime_track.py` already takes a URL as `--source`, so
the two halves meet with no glue code.

Measured on this laptop: **131 FPS** pure inference on the RTX 5050 against a
camera delivering 18. The sensor is the bottleneck by ~7×, so the demo is
frame-rate limited by what you point at the sky, not by the model. Tiled
inference (5.2 FPS) stays offline; TTA is affordable but did not measurably
help. Details and the honest limits are in `docs/eo_bringup.md`.
