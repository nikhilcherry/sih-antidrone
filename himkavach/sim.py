"""Single-axis (azimuth) pointing loop under high-altitude degradation.

Models the error that actually matters operationally: the angle between the
EFFECTOR axis and the target, which is servo tracking lag plus thermo-elastic
boresight misalignment between the tracking sensor and the effector.

Run uncompensated vs compensated to produce the headline chart.
"""
from dataclasses import dataclass

import numpy as np

from .atmosphere import AtmState
from . import degradation as deg


@dataclass
class TurretSpec:
    inertia: float = 0.8           # kg*m^2, portable EO/RF head
    visc_damping_ref: float = 2.0  # N*m*s/rad at +20 C
    coulomb_ref: float = 0.5       # N*m at +20 C
    cable_k_ref: float = 0.15      # N*m/rad at +20 C
    torque_limit: float = 40.0     # N*m continuous at +20 C, sea level
    loop_bw_hz: float = 8.0
    damping_ratio: float = 0.8


@dataclass
class SimResult:
    t: np.ndarray
    err_urad: np.ndarray
    rms_urad: float
    peak_urad: float
    saturated_frac: float


def crossing_target(t: np.ndarray, closest_range_m: float = 120.0,
                    speed_ms: float = 25.0) -> np.ndarray:
    """Bearing to a drone flying a straight crossing pass, rad."""
    t0 = t[-1] / 2.0
    return np.arctan2(speed_ms * (t - t0), closest_range_m)


def run(atm: AtmState, spec: TurretSpec = TurretSpec(), *, compensate: bool,
        wind_speed: float = 15.0, duration_s: float = 20.0, dt: float = 0.002,
        seed: int = 0) -> SimResult:
    rng = np.random.default_rng(seed)
    n = int(duration_s / dt)
    t = np.arange(n) * dt
    T_C = atm.temp_C

    # --- plant parameters under the current environment ---------------------
    b = spec.visc_damping_ref * deg.bearing_friction_multiplier(T_C)
    coulomb = spec.coulomb_ref * deg.bearing_friction_multiplier(T_C)
    k_cable = spec.cable_k_ref * deg.cable_stiffness_multiplier(T_C)
    tau_max = spec.torque_limit * deg.motor_torque_derate(T_C, atm)
    boresight = deg.boresight_drift_urad(T_C) * 1e-6          # rad
    gyro_sigma = deg.gyro_bias_instability(T_C) * (np.pi / 180.0) / 3600.0

    winds = deg.gust_series(n, dt, wind_speed, rng=rng)
    tau_wind = np.array([deg.wind_torque(atm, w) for w in winds])

    # --- controller ---------------------------------------------------------
    w_n = 2 * np.pi * spec.loop_bw_hz
    if compensate:
        # Gain scheduling: retune against the MEASURED cold plant so the loop
        # keeps its designed bandwidth and damping instead of going sluggish.
        kp = spec.inertia * w_n ** 2 + k_cable
        kd = 2 * spec.damping_ratio * spec.inertia * w_n - b
        kd = max(kd, 0.15 * 2 * spec.damping_ratio * spec.inertia * w_n)
    else:
        # Fixed sea-level / room-temperature tune.
        kp = spec.inertia * w_n ** 2 + spec.cable_k_ref
        kd = 2 * spec.damping_ratio * spec.inertia * w_n - spec.visc_damping_ref
    ki = 0.5 * kp

    target = crossing_target(t)
    theta = target[0]
    omega = 0.0
    integ = 0.0
    bias = 0.0
    bias_est = 0.0
    dob_est = 0.0                      # wind disturbance observer state
    err = np.empty(n)
    sat = 0

    # Residual errors the compensation CANNOT remove -- model fidelity limits.
    bs_resid = boresight * (0.12 if compensate else 1.0)   # 88% cal removal
    ff_gain = 0.85 if compensate else 0.0                  # cable model accuracy
    dob_gain = 0.70 if compensate else 0.0                 # observer authority

    for i in range(n):
        bias += rng.normal(0.0, gyro_sigma * np.sqrt(dt))
        if compensate:
            # Slow bias tracker (Kalman bias state, heavily low-passed).
            bias_est += 0.004 * (bias - bias_est)
        meas = theta + bias - bias_est + rng.normal(0.0, 15e-6)

        e = target[i] - meas
        integ = np.clip(integ + e * dt, -0.05, 0.05)
        # Rate feedforward keeps the loop ahead of a crossing target.
        rate_ff = (target[i] - target[i - 1]) / dt if i else 0.0

        tau = kp * e + ki * integ + kd * (rate_ff - omega)
        tau += ff_gain * k_cable * theta          # cable restoring-torque cancel
        tau -= dob_gain * dob_est                 # wind disturbance rejection

        tau_c = float(np.clip(tau, -tau_max, tau_max))
        if abs(tau) > tau_max:
            sat += 1

        d = tau_wind[i]
        friction = b * omega + coulomb * np.tanh(omega / 1e-3)
        alpha = (tau_c - friction - k_cable * theta + d) / spec.inertia
        omega += alpha * dt
        theta += omega * dt

        if compensate:
            # Observer: reconstruct the unmodelled torque from the residual.
            resid = spec.inertia * alpha - (tau_c - b * omega - k_cable * theta)
            dob_est += 0.02 * (resid - dob_est)

        err[i] = (target[i] - theta) + bs_resid

    err_urad = err * 1e6
    return SimResult(
        t=t,
        err_urad=err_urad,
        rms_urad=float(np.sqrt(np.mean(err_urad ** 2))),
        peak_urad=float(np.max(np.abs(err_urad))),
        saturated_frac=sat / n,
    )
