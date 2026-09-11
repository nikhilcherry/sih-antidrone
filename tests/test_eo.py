"""Guard the EO chain: wire format and sensor geometry.

Same contract as test_models.py -- these numbers appear in the pitch, so they
are not allowed to drift silently.
"""
import pytest

from aura.degradation import eo_detection_range
from aura.eo import optics
from aura.eo.optics import (
    DETECT_PX, RECOGNISE_PX, Sensor, focal_length_mm, hfov_deg,
    hfov_for_range, pixels_on_target, pointing_error_px, range_for_pixels,
)
from aura.eo.protocol import HEADER_SIZE, pack_header, unpack_header


# --- wire format -------------------------------------------------------------

def test_header_roundtrips():
    blob = pack_header(seq=42, cap_ts=123.456, width=1280, height=720, nbytes=9001)
    assert len(blob) == HEADER_SIZE == 24
    seq, ts, w, h, n = unpack_header(blob)
    assert (seq, w, h, n) == (42, 1280, 720, 9001)
    assert ts == pytest.approx(123.456)


def test_header_rejects_desynchronised_stream():
    """A short read that slips the framing must fail loudly, not decode garbage."""
    with pytest.raises(ValueError):
        unpack_header(b"XXXX" + b"\x00" * 20)


def test_sequence_wraps_instead_of_overflowing():
    """uint32 seq: a long field run must not raise 32 bits in."""
    seq, *_ = unpack_header(pack_header(2**32 + 7, 0.0, 640, 480, 1))
    assert seq == 7


# --- sensor geometry ---------------------------------------------------------

def test_ifov_matches_hand_calculation():
    # 1.361 rad over 1920 px = 709 urad/px
    assert Sensor("t", 1920, 1080, 78.0).ifov_urad == pytest.approx(709.0, rel=0.01)


def test_pixels_on_target_falls_as_inverse_range():
    s = Sensor("t", 1920, 1080, 78.0)
    assert pixels_on_target(s, 0.3, 200) == pytest.approx(
        pixels_on_target(s, 0.3, 100) / 2.0, rel=1e-9)


def test_range_for_pixels_inverts_pixels_on_target():
    s = Sensor("t", 1920, 1080, 21.8)
    r = range_for_pixels(s, 0.3, RECOGNISE_PX)
    assert pixels_on_target(s, 0.3, r) == pytest.approx(RECOGNISE_PX, rel=1e-9)


def test_stock_webcam_cannot_recognise_a_small_drone_past_60_m():
    """The boundary we state out loud instead of hiding: a wide-FOV webcam is a
    short-range cueing sensor, not a surveillance sensor."""
    c920 = optics.CATALOG[0]
    assert range_for_pixels(c920, 0.3, RECOGNISE_PX) < 60.0
    assert range_for_pixels(c920, 0.3, DETECT_PX) < 250.0


def test_tele_lens_buys_an_order_of_magnitude():
    wide, tele = optics.CATALOG[0], optics.CATALOG[3]
    assert range_for_pixels(tele, 0.3, RECOGNISE_PX) > \
           7.0 * range_for_pixels(wide, 0.3, RECOGNISE_PX)


def test_pointing_budget_is_subpixel_on_a_wide_lens():
    """Why this matters: on the stock webcam the 295 urad compensated pointing
    error is invisible, so the EO channel cannot demonstrate the compensation
    claim. It only becomes visible below roughly a 33 deg lens."""
    assert pointing_error_px(optics.CATALOG[0], 295.0) < 1.0
    assert pointing_error_px(optics.CATALOG[2], 295.0) > 1.0


def test_lens_conversions_are_mutually_inverse():
    f = focal_length_mm(5.6, 21.8)
    assert hfov_deg(5.6, f) == pytest.approx(21.8, rel=1e-9)


def test_hfov_for_range_is_consistent_with_range_for_pixels():
    fov = hfov_for_range(1920, 0.3, 1000.0, RECOGNISE_PX)
    s = Sensor("derived", 1920, 1080, fov)
    assert range_for_pixels(s, 0.3, RECOGNISE_PX) == pytest.approx(1000.0, rel=1e-6)


def test_weather_only_bites_once_the_optics_are_good_enough():
    """The crossover that decides where to spend money on the EO channel.

    Koschmieder clamps range to the meteorological visibility. A 35 mm lens
    tops out at ~1.6 km on a 0.3 m target, so at 2 km visibility the lens is
    still the binding constraint and the weather model changes nothing. Only
    below ~1.6 km visibility does the atmosphere start to matter. Conclusion
    for the pitch: for webcam-class optics the EO range limit is GEOMETRY, and
    the environmental degradation story applies to the mechanics and the RF
    chain -- not to how far this camera can see.
    """
    tele = optics.CATALOG[3]
    clear = range_for_pixels(tele, 0.3, DETECT_PX)
    assert clear == pytest.approx(1634, rel=0.01)

    # Good visibility: geometry-limited, weather is a no-op.
    assert eo_detection_range(clear, visibility_km=5.0) == pytest.approx(clear)
    # Blowing snow: now the atmosphere is what you are fighting.
    assert eo_detection_range(clear, visibility_km=1.0) == pytest.approx(1000.0, rel=0.01)


def test_wide_webcam_is_never_weather_limited():
    """A stock webcam gives up at ~212 m detect; no realistic Himalayan
    visibility is that bad, so its weather curve is flat. Stated so nobody
    claims environmental compensation improves this sensor."""
    c920 = optics.CATALOG[0]
    clear = range_for_pixels(c920, 0.3, DETECT_PX)
    assert eo_detection_range(clear, visibility_km=0.5) == pytest.approx(clear)


def test_range_rejects_nonsense():
    s = Sensor("t", 1920, 1080, 78.0)
    with pytest.raises(ValueError):
        pixels_on_target(s, 0.3, 0.0)
    with pytest.raises(ValueError):
        range_for_pixels(s, 0.3, 0.0)


def test_max_plausible_px_rejects_the_full_frame_box():
    """The measured failure: a 573x478 box in a 640x480 frame, conf 0.71, around
    a person. Geometry rejects it; no confidence threshold does."""
    c920 = optics.CATALOG[0]
    limit = optics.max_plausible_px(c920, target_size_m=0.3, min_range_m=10.0)
    assert limit == pytest.approx(42.0, rel=0.05)      # ~42 px at 10 m

    observed_box_px = 573 * (c920.h_px / 640)          # scale to sensor width
    assert observed_box_px > limit * 10                # rejected by an order of magnitude


def test_gate_keeps_targets_at_every_useful_range():
    """A gate that rejected real targets would be worse than no gate."""
    c920 = optics.CATALOG[0]
    limit = optics.max_plausible_px(c920, 0.3, min_range_m=10.0)
    for r in (15.0, 50.0, 200.0, 1000.0):
        assert pixels_on_target(c920, 0.3, r) < limit


def test_closer_minimum_range_permits_a_wider_box():
    c920 = optics.CATALOG[0]
    assert (optics.max_plausible_px(c920, 0.3, 5.0)
            > optics.max_plausible_px(c920, 0.3, 20.0))


def test_max_plausible_px_rejects_nonsense():
    with pytest.raises(ValueError):
        optics.max_plausible_px(optics.CATALOG[0], 0.3, 0.0)
