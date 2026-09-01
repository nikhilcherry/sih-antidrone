"""Guard the physics. These are the numbers the pitch depends on."""
import numpy as np
import pytest

from himkavach.atmosphere import atmosphere, convective_cooling_factor, RHO_SL
from himkavach import degradation as deg
from himkavach.sim import run


def test_isa_matches_known_values():
    sl = atmosphere(0)
    assert sl.temp_C == pytest.approx(15.0, abs=0.1)
    assert sl.density == pytest.approx(RHO_SL, rel=1e-3)
    # ISA 5000 m: 54.0 kPa, -17.5 C
    a = atmosphere(5000)
    assert a.pressure_Pa / 1000 == pytest.approx(54.0, rel=0.01)
    assert a.temp_C == pytest.approx(-17.5, abs=0.3)


def test_altitude_rejects_stratosphere():
    with pytest.raises(ValueError):
        atmosphere(12000)


def test_cold_day_is_denser_than_standard_day():
    """A cold day at fixed pressure altitude means MORE dense air, not less."""
    assert atmosphere(4500, -25).density > atmosphere(4500, 0).density


def test_thin_air_cools_worse():
    assert convective_cooling_factor(atmosphere(5000).density_ratio) < 0.8


def test_cable_stiffens_monotonically_as_it_cools():
    temps = np.linspace(20, -50, 40)
    mult = [deg.cable_stiffness_multiplier(t) for t in temps]
    assert all(b >= a for a, b in zip(mult, mult[1:]))
    assert deg.cable_stiffness_multiplier(20) < 1.5
    assert deg.cable_stiffness_multiplier(-45) > 8.0


def test_boresight_drift_sign_and_scale():
    """Cooling 50 K below calibration must blow a sub-100 urad budget."""
    assert deg.boresight_drift_urad(-30) < -350
    assert deg.boresight_drift_urad(20) == pytest.approx(0.0)


def test_thin_air_lowers_wind_load_at_equal_speed():
    assert deg.wind_torque(atmosphere(5000), 15) < deg.wind_torque(atmosphere(0), 15)


def test_cold_kills_battery_but_helps_noise_floor():
    assert deg.battery_capacity_fraction(-30) < 0.5
    assert deg.rf_noise_floor_shift_dB(-40) < 0  # colder LNA = quieter


def test_compensation_improves_and_flattens_pointing():
    hot = atmosphere(0)
    cold = atmosphere(5500, -25)
    u_cold = run(cold, compensate=False, wind_speed=20, seed=7)
    c_cold = run(cold, compensate=True, wind_speed=20, seed=7)
    c_warm = run(hot, compensate=True, wind_speed=6, seed=7)

    assert c_cold.rms_urad < u_cold.rms_urad / 2, "compensation must halve RMS error"
    # The real claim: compensated performance is near altitude-invariant.
    assert c_cold.rms_urad < 1.5 * c_warm.rms_urad
