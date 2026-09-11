"""Everything the ops console shows, computed from the physics core.

There are no dashboard-only numbers. Each value is a projection of
`aura.atmosphere`, `aura.degradation` or `aura.sim`, so a figure on screen traces
to the same equation the tests and the README use. The one exception is the
status banding in `health()` -- those are display thresholds chosen for the
operator, not physics, and they sit in one place so they can be argued about.
"""
from __future__ import annotations

import math
from pathlib import Path

from .. import degradation as deg
from ..atmosphere import atmosphere, convective_cooling_factor, paschen_derating_factor
from ..scenarios import SCENARIOS, SEED
from ..sim import BORESIGHT_CAL_RESIDUAL, TECHNIQUES, TurretSpec, run

# name: (min, max, step). Values are snapped to the step so repeated slider
# positions hit the result cache instead of re-running the sim.
LIMITS = {
    "altitude_m":    (0.0, 6000.0, 50.0),
    "temp_offset_K": (-35.0, 15.0, 1.0),
    "wind_ms":       (0.0, 30.0, 0.5),
    "visibility_km": (0.2, 20.0, 0.1),
    "range_m":       (100.0, 2000.0, 10.0),
}

DEFAULT_ENV = {
    "altitude_m": 0.0, "temp_offset_K": 0.0, "wind_ms": 6.0,
    "visibility_km": 10.0, "range_m": 400.0, "compensate": False,
}

RATED_MISSION_MIN = 40.0     # planning endurance with cells at +25 C
EO_CLEAR_RANGE_M = 4000.0    # specified EO sensor in clear air (README table)
TARGET_SIZE_M = 0.3          # small quadcopter, as in docs/eo_bringup.md
TRACE_STRIDE = 10            # 10 000 sim steps -> 1000 points on the wire
SWEEP_ALTITUDES = tuple(range(0, 6001, 500))

TECHNIQUE_LABELS = {
    "boresight_cal": "Boresight calibration",
    "cable_ff":      "Cable torque feedforward",
    "gain_schedule": "Gain scheduling",
    "wind_observer": "Wind disturbance observer",
    "gyro_bias":     "Gyro bias estimation",
}

# RTL2832U dongles: the receive-only SDR the RF track uses.
SDR_USB_IDS = {("0bda", "2838"), ("0bda", "2832")}


def clamp_env(patch: dict, base: dict | None = None) -> dict:
    """Merge a client's partial update into `base`, clamped and snapped.

    Unknown keys and non-finite values are ignored rather than rejected: the
    console is driven by sliders, and one bad message must not stall the bus.
    """
    env = dict(DEFAULT_ENV if base is None else base)
    for key, value in patch.items():
        if key == "compensate":
            env[key] = bool(value)
            continue
        if key not in LIMITS:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        lo, hi, step = LIMITS[key]
        env[key] = round(min(hi, max(lo, round(v / step) * step)), 6)
    return env


def sim_key(env: dict) -> tuple[float, float, float]:
    """The inputs that change the pointing sim. Range, visibility and the
    compensation toggle only change how results are displayed."""
    return (env["altitude_m"], env["temp_offset_K"], env["wind_ms"])


def preset_env(key: str, base: dict) -> dict:
    for s in SCENARIOS:
        if s.key == key:
            return clamp_env({"altitude_m": s.altitude_m,
                              "temp_offset_K": s.temp_offset_K,
                              "wind_ms": s.wind_ms}, base)
    return dict(base)


# --- the sim, as the process pool sees it -----------------------------------

def simulate(altitude_m: float, temp_offset_K: float, wind_ms: float,
             compensate: bool, techniques=None, with_trace: bool = False) -> dict:
    """One pointing run. Top-level so it pickles into a worker process."""
    r = run(atmosphere(altitude_m, temp_offset_K), compensate=compensate,
            techniques=techniques, wind_speed=wind_ms, seed=SEED)
    out = {"rms": r.rms_urad, "peak": r.peak_urad, "saturated": r.saturated_frac}
    if with_trace:
        out["dt"] = float(r.t[TRACE_STRIDE] - r.t[0])
        out["trace"] = [round(float(v), 1) for v in r.err_urad[::TRACE_STRIDE]]
    return out


def ablation_calls(key: tuple) -> list[tuple]:
    """Leave-one-out: all five techniques on, minus one."""
    return [(*key, True, tuple(t for t in TECHNIQUES if t != skip))
            for skip in TECHNIQUES]


def ablation_payload(full_rms: float, uncomp_rms: float, without: list[float]) -> dict:
    return {
        "full": full_rms,
        "uncomp": uncomp_rms,
        "items": [{"id": t, "label": TECHNIQUE_LABELS[t], "rms_without": w,
                   "cost": w - full_rms} for t, w in zip(TECHNIQUES, without)],
    }


# --- instant, analytic panels -----------------------------------------------

def _band(value: float, ok: float, bad: float, *, lower_is_worse: bool = False) -> str:
    if lower_is_worse:
        return "ok" if value >= ok else "degraded" if value >= bad else "critical"
    return "ok" if value < ok else "degraded" if value < bad else "critical"


def health(env: dict) -> list[dict]:
    atm = atmosphere(env["altitude_m"], env["temp_offset_K"])
    T = atm.temp_C
    spec = TurretSpec()

    cable = deg.cable_stiffness_multiplier(T)
    bearing = deg.bearing_friction_multiplier(T)
    boresight = deg.boresight_drift_urad(T)
    motor = deg.motor_torque_derate(T, atm)
    cooling = convective_cooling_factor(atm.density_ratio)
    hv = paschen_derating_factor(atm.pressure_Pa)
    gyro = deg.gyro_bias_instability(T)
    wind = deg.wind_torque(atm, env["wind_ms"])
    wind_frac = wind / (spec.torque_limit * motor)
    battery = deg.battery_capacity_fraction(T)
    eo = deg.eo_detection_range(EO_CLEAR_RANGE_M, env["visibility_km"])
    rf = deg.rf_noise_floor_shift_dB(T)

    def row(id_, label, value, text, status, note, comp=None, residual=None):
        return {"id": id_, "label": label, "value": float(value), "text": text,
                "status": status, "note": note, "comp": comp,
                "residual": residual}

    return [
        row("cable", "Cable harness stiffness", cable, f"×{cable:.1f}",
            _band(cable, 2.0, 6.0), "jacket nears glass transition at −30 °C",
            "cable_ff"),
        row("bearing", "Bearing drag", bearing, f"×{bearing:.1f}",
            _band(bearing, 1.5, 4.0), "grease viscosity climbs in the cold",
            "gain_schedule"),
        row("boresight", "Boresight drift", boresight, f"{boresight:+.0f} µrad",
            _band(abs(boresight), 100.0, 300.0), "optical bench vs mount CTE mismatch",
            "boresight_cal", f"{boresight * BORESIGHT_CAL_RESIDUAL:+.0f} µrad left"),
        row("wind", "Wind torque", wind, f"{wind:.2f} N·m",
            _band(wind_frac, 0.05, 0.15), f"{wind_frac:.0%} of the derated torque limit",
            "wind_observer"),
        row("gyro", "Gyro bias instability", gyro, f"{gyro:.2f} °/h",
            _band(gyro, 1.0, 2.0), "MEMS tempco", "gyro_bias"),
        row("motor", "Motor torque limit", motor, f"{motor:.0%}",
            _band(motor, 0.90, 0.70, lower_is_worse=True),
            "winding cooling loss beats magnet gain"),
        row("cooling", "Convective cooling", cooling, f"{cooling:.0%}",
            _band(cooling, 0.80, 0.60, lower_is_worse=True),
            "thin air: electronics run hotter, not colder"),
        row("hv", "HV standoff (Paschen)", hv, f"{hv:.0%}",
            _band(hv, 0.80, 0.60, lower_is_worse=True), "derate supply clearances"),
        row("battery", "Battery capacity", battery, f"{battery:.0%}",
            _band(battery, 0.80, 0.50, lower_is_worse=True), "cold-soaked Li-ion cells"),
        row("eo", "EO detection range", eo, f"{eo:,.0f} m",
            _band(eo / EO_CLEAR_RANGE_M, 0.75, 0.40, lower_is_worse=True),
            f"Koschmieder extinction at {env['visibility_km']:.1f} km visibility"),
        row("rf", "RF noise floor", rf, f"{rf:+.2f} dB",
            "ok" if rf <= 0.5 else "degraded",
            "cold LNA is quieter — altitude helps here"),
    ]


def instant(env: dict) -> dict:
    """Everything that is cheap enough to recompute on every slider tick."""
    atm = atmosphere(env["altitude_m"], env["temp_offset_K"])
    battery = deg.battery_capacity_fraction(atm.temp_C)
    return {
        "env": env,
        "key": list(sim_key(env)),
        "atm": {"temp_C": atm.temp_C, "pressure_kPa": atm.pressure_Pa / 1000.0,
                "density": atm.density, "density_ratio": atm.density_ratio},
        "health": health(env),
        "endurance": {"rated_min": RATED_MISSION_MIN, "fraction": battery,
                      "minutes": RATED_MISSION_MIN * battery},
        "eo": {"range_m": deg.eo_detection_range(EO_CLEAR_RANGE_M, env["visibility_km"]),
               "clear_m": EO_CLEAR_RANGE_M},
        "target": {"size_m": TARGET_SIZE_M},
    }


def scenarios() -> list[dict]:
    return [{"key": s.key, "name": s.name, "altitude_m": s.altitude_m,
             "temp_offset_K": s.temp_offset_K, "wind_ms": s.wind_ms}
            for s in SCENARIOS]


def find_sdr(usb_root: Path = Path("/sys/bus/usb/devices")) -> dict:
    """Is an RTL-SDR plugged in? Reads sysfs, so no tooling needs installing."""
    try:
        devices = list(usb_root.iterdir())
    except OSError:
        devices = []
    for dev in devices:
        try:
            vid = (dev / "idVendor").read_text().strip()
            pid = (dev / "idProduct").read_text().strip()
        except OSError:
            continue
        if (vid, pid) in SDR_USB_IDS:
            return {"present": True, "id": f"{vid}:{pid}"}
    return {"present": False, "id": None}
