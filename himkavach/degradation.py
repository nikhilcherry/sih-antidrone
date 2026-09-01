"""Environmental degradation models for a high-altitude anti-drone turret.

Each function maps an environmental state to a physical parameter change in a
named subsystem. These are engineering-grade approximations with cited forms,
not fitted hardware data -- they exist so the compensation layer has something
honest to fight, and so every claim on the dashboard traces to an equation.
"""
import numpy as np

from .atmosphere import AtmState


# --- Mechanical -------------------------------------------------------------

def cable_stiffness_multiplier(temp_C: float, Tg_C: float = -30.0,
                               width_K: float = 9.0, max_mult: float = 12.0) -> float:
    """Cable bundle bending stiffness vs temperature.

    Plasticised PVC / TPE jackets stiffen by roughly an order of magnitude as
    they approach the glass transition. Modelled as a sigmoid centred on Tg.
    This is the PS's named concern: a stiffening harness adds a position-
    dependent restoring torque on the azimuth axis, biasing pointing and
    detuning the servo loop.
    """
    return 1.0 + (max_mult - 1.0) / (1.0 + np.exp((temp_C - Tg_C) / width_K))


def bearing_friction_multiplier(temp_C: float, ref_C: float = 20.0) -> float:
    """Bearing drag rises with grease viscosity as temperature falls.

    Vogel-form viscosity growth, clipped so a -50 C case stays physical
    (roughly 6x nominal drag, consistent with low-temp grease datasheets).
    """
    b_vogel = 700.0
    theta = 150.0
    visc = np.exp(b_vogel / (temp_C + theta)) / np.exp(b_vogel / (ref_C + theta))
    return float(np.clip(visc, 1.0, 6.0))


def boresight_drift_urad(temp_C: float, ref_C: float = 20.0,
                         tempco_urad_per_K: float = 8.0) -> float:
    """Thermo-elastic boresight shift from CTE mismatch in the optical bench.

    Aluminium bench (23 ppm/K) against steel mounts (12 ppm/K) over a ~0.3 m
    baseline yields single-digit urad/K. Static, repeatable, and therefore the
    single most compensable error in the whole system.
    """
    return tempco_urad_per_K * (temp_C - ref_C)


def wind_torque(atm: AtmState, wind_speed: float, Cd: float = 1.2,
                area_m2: float = 0.12, arm_m: float = 0.15) -> float:
    """Quasi-static wind torque on the turret head, N*m.

    Note the altitude nuance worth presenting: for a FIXED wind speed, thinner
    air reduces this load. High altitude hurts because wind SPEEDS are higher,
    not because the aerodynamics worsen.
    """
    return 0.5 * atm.density * wind_speed ** 2 * Cd * area_m2 * arm_m


def gust_series(n: int, dt: float, mean_speed: float, turbulence: float = 0.18,
                length_scale_s: float = 1.2, rng=None) -> np.ndarray:
    """First-order (Dryden-like) filtered noise gust profile, m/s."""
    rng = rng or np.random.default_rng(0)
    alpha = dt / (length_scale_s + dt)
    sigma = turbulence * mean_speed
    out = np.empty(n)
    v = 0.0
    for i in range(n):
        v += alpha * (rng.normal(0.0, sigma) - v)
        out[i] = mean_speed + v
    return out


# --- Electrical -------------------------------------------------------------

def battery_capacity_fraction(temp_C: float) -> float:
    """Usable Li-ion capacity vs cell temperature.

    Anchored to typical 18650 discharge curves: ~1.0 at 25 C, 0.80 at 0 C,
    0.60 at -20 C, 0.42 at -30 C. Drives the endurance number on the health
    panel -- the operator needs to know a 40 min mission is now 16 min.
    """
    pts_T = np.array([-40.0, -30.0, -20.0, -10.0, 0.0, 10.0, 25.0, 45.0])
    pts_C = np.array([0.30, 0.42, 0.60, 0.72, 0.80, 0.90, 1.00, 1.02])
    return float(np.interp(temp_C, pts_T, pts_C))


def motor_torque_derate(temp_C: float, atm: AtmState) -> float:
    """Continuous torque limit derating.

    Two opposing effects: NdFeB remanence rises ~0.12%/K as it cools (helps Kt),
    while thin air cripples convective cooling of the windings (hurts I_cont).
    Thermal loss dominates above ~3 km.
    """
    from .atmosphere import convective_cooling_factor
    magnet_gain = 1.0 + 0.0012 * (20.0 - temp_C)
    thermal_limit = convective_cooling_factor(atm.density_ratio) ** 0.5
    return float(np.clip(magnet_gain * thermal_limit, 0.4, 1.15))


# --- Sensing ----------------------------------------------------------------

def gyro_bias_instability(temp_C: float, base_deg_per_hr: float = 0.5) -> float:
    """MEMS gyro bias instability inflates at cold. Returns deg/hr."""
    return base_deg_per_hr * (1.0 + 0.035 * max(0.0, 20.0 - temp_C))


def eo_detection_range(clear_air_range_m: float, visibility_km: float) -> float:
    """EO detection range under dust / blowing snow via Koschmieder extinction.

    sigma = 3.912 / V. Range shrinks where contrast falls below threshold.
    """
    sigma = 3.912 / max(0.05, visibility_km * 1000.0)
    # Contrast-limited range: solve exp(-sigma*R) = exp(-sigma*R_clear) * floor
    return float(min(clear_air_range_m, np.log(1.0 / 0.02) / sigma))


def rf_noise_floor_shift_dB(temp_C: float, ref_C: float = 20.0) -> float:
    """LNA noise figure improves in the cold -- one place altitude HELPS.

    Thermal noise power scales with physical temperature: 10*log10(T/T_ref).
    """
    T_k = temp_C + 273.15
    return 10.0 * np.log10(T_k / (ref_C + 273.15))
