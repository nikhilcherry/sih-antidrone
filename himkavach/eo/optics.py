"""What the EO channel can actually see, from sensor geometry alone.

`degradation.eo_detection_range()` takes clear-air range as a given. This module
derives it, so the number in the pitch traces back to a lens instead of an
assertion. It also answers the question that decides which camera to buy:
how many pixels does the target subtend, and is that enough to detect it.

Convention: angles in microradians, to sit alongside the pointing budget in
`sim.py`. That shared unit is the point -- it lets us ask whether pointing
accuracy or sensor resolution is the binding constraint, which is not obvious
and turns out to depend entirely on the lens.

Johnson criteria (1958), the standard DRI thresholds, expressed in pixels at
2 px per resolvable line pair across the target's critical dimension:
    detect 2 px, recognise 8 px, identify 12.8 px.
These are human-observer thresholds. A CNN detector on a small, low-contrast
target against bright sky wants more -- treat 2 px as a floor nobody achieves
in practice and RECOGNISE as the honest operating threshold.
"""
from dataclasses import dataclass
from math import atan, degrees, radians, tan

# Johnson criteria, pixels across the target's critical dimension.
DETECT_PX = 2.0
RECOGNISE_PX = 8.0
IDENTIFY_PX = 12.8


@dataclass(frozen=True)
class Sensor:
    """A camera as the geometry actually cares about it."""
    name: str
    h_px: int
    v_px: int
    hfov_deg: float          # horizontal field of view

    @property
    def ifov_urad(self) -> float:
        """Instantaneous field of view: microradians subtended by one pixel."""
        return radians(self.hfov_deg) / self.h_px * 1e6


def pixels_on_target(sensor: Sensor, target_size_m: float, range_m: float) -> float:
    """Horizontal pixels the target spans at a given range.

    Small-angle: a 0.3 m quadcopter at 500 m subtends 600 urad, so this is
    accurate to far better than the pixel we are counting.
    """
    if range_m <= 0:
        raise ValueError("range must be positive")
    target_urad = target_size_m / range_m * 1e6
    return target_urad / sensor.ifov_urad


def range_for_pixels(sensor: Sensor, target_size_m: float, min_px: float) -> float:
    """Maximum range at which the target still spans `min_px` pixels.

    This is the clear-air, diffraction-ignoring, perfect-contrast ceiling --
    feed it to `degradation.eo_detection_range()` to apply atmospheric
    extinction on top. Reality lands below both.
    """
    if min_px <= 0:
        raise ValueError("min_px must be positive")
    return target_size_m / (min_px * sensor.ifov_urad * 1e-6)


def hfov_for_range(sensor_h_px: int, target_size_m: float, range_m: float,
                   min_px: float = RECOGNISE_PX) -> float:
    """Horizontal FOV in degrees needed to hold `min_px` on target at `range_m`.

    The buy-decision function: it converts an operational requirement into a
    lens specification.
    """
    ifov_urad = target_size_m / range_m * 1e6 / min_px
    return degrees(ifov_urad * 1e-6 * sensor_h_px)


def focal_length_mm(sensor_width_mm: float, hfov_deg: float) -> float:
    """Lens focal length giving `hfov_deg` on a sensor `sensor_width_mm` wide."""
    return sensor_width_mm / (2.0 * tan(radians(hfov_deg) / 2.0))


def hfov_deg(sensor_width_mm: float, focal_mm: float) -> float:
    """Inverse of `focal_length_mm` -- what FOV a given lens gives."""
    return degrees(2.0 * atan(sensor_width_mm / (2.0 * focal_mm)))


def pointing_error_px(sensor: Sensor, pointing_error_urad: float) -> float:
    """Pointing error expressed in pixels of smear on the focal plane.

    Below ~1 px the servo is better than the sensor and improving it buys
    nothing visible. Above it, the compensation layer is what keeps the target
    inside the detection window. This is where the EO track and the pointing
    track meet.
    """
    return pointing_error_urad / sensor.ifov_urad


# Candidate hardware. FOVs are manufacturer horizontal figures at 16:9.
CATALOG = (
    Sensor("Logitech C920 (78 deg, stock)", 1920, 1080, 78.0),
    Sensor("Generic USB webcam (60 deg)", 1280, 720, 60.0),
    Sensor("Arducam UVC + 16 mm M12", 1920, 1080, 21.8),
    Sensor("Arducam UVC + 35 mm M12", 1920, 1080, 10.1),
    Sensor("Pi GS cam + 50 mm C-mount", 1456, 1088, 7.5),
)


def _table() -> None:
    target = 0.3          # DJI Mini class, 0.3 m across
    compensated_urad = 295.0   # sim.py, 5500 m compensated RMS pointing error

    print(f"Target: {target:.2f} m quadcopter.  Johnson recognise = {RECOGNISE_PX:.0f} px on target.\n")
    hdr = (f"{'Sensor':<32}{'IFOV':>10}{'px @100m':>10}{'px @500m':>10}"
           f"{'detect':>9}{'recognise':>11}{'ptg err':>9}")
    print(hdr)
    print("-" * len(hdr))
    for s in CATALOG:
        print(f"{s.name:<32}{s.ifov_urad:>7.0f} ur"
              f"{pixels_on_target(s, target, 100):>10.1f}"
              f"{pixels_on_target(s, target, 500):>10.1f}"
              f"{range_for_pixels(s, target, DETECT_PX):>8.0f} m"
              f"{range_for_pixels(s, target, RECOGNISE_PX):>10.0f} m"
              f"{pointing_error_px(s, compensated_urad):>8.2f}p")
    print(f"\n'ptg err' = the 295 urad compensated pointing error, in pixels.")
    print("Below 1 px the servo out-resolves the sensor and compensation is invisible.")
    print(f"\nTo recognise that target at 1 km you need a {hfov_for_range(1920, target, 1000):.1f} deg lens")
    print(f"  = {focal_length_mm(5.6, hfov_for_range(1920, target, 1000)):.0f} mm on a 5.6 mm-wide (1/2.7\") sensor.")


if __name__ == "__main__":
    _table()
