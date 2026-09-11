"""International Standard Atmosphere plus a cold-bias offset for Himalayan winter.

Everything downstream (wind torque, convective cooling, EO extinction, battery
derating) keys off the state returned by `atmosphere()`, so a single altitude /
temperature-offset pair drives the whole degradation chain.
"""
from dataclasses import dataclass

T0 = 288.15        # ISA sea-level temperature, K
P0 = 101325.0      # ISA sea-level pressure, Pa
LAPSE = 0.0065     # tropospheric lapse rate, K/m
G = 9.80665
M_AIR = 0.0289644  # molar mass of dry air, kg/mol
R_UNIV = 8.314462

RHO_SL = 1.225     # ISA sea-level density, kg/m^3


@dataclass(frozen=True)
class AtmState:
    altitude_m: float
    temp_K: float
    temp_C: float
    pressure_Pa: float
    density: float
    density_ratio: float   # rho / rho_sea_level


def atmosphere(altitude_m: float, temp_offset_K: float = 0.0) -> AtmState:
    """ISA troposphere (valid to 11 km) with an additive temperature offset.

    `temp_offset_K` models departure from standard day. Ladakh / Siachen winter
    runs roughly -20 to -30 K below ISA, which is what turns a -14 C standard
    day at 4500 m into the -40 C case the PS actually cares about.
    """
    if not 0.0 <= altitude_m <= 11000.0:
        raise ValueError("ISA troposphere model is valid for 0-11000 m")

    temp_std = T0 - LAPSE * altitude_m
    # Pressure follows the STANDARD profile; a cold day changes density, not the
    # hydrostatic column we are standing under.
    pressure = P0 * (temp_std / T0) ** (G * M_AIR / (R_UNIV * LAPSE))

    temp = temp_std + temp_offset_K
    density = pressure * M_AIR / (R_UNIV * temp)

    return AtmState(
        altitude_m=altitude_m,
        temp_K=temp,
        temp_C=temp - 273.15,
        pressure_Pa=pressure,
        density=density,
        density_ratio=density / RHO_SL,
    )


def paschen_derating_factor(pressure_Pa: float) -> float:
    """Voltage-standoff derating for reduced air breakdown strength.

    Air's dielectric strength falls roughly with density above ~2 km, so HV
    clearances (PA supplies, motor drives) must be derated. Simplified linear
    fit to the right-hand branch of the Paschen curve, normalised to 1.0 at
    sea level.
    """
    return max(0.35, (pressure_Pa / P0) ** 0.7)


def convective_cooling_factor(density_ratio: float) -> float:
    """Forced-convection coefficient scales ~rho^0.8 (Nu ~ Re^0.8).

    The high-altitude trap: ambient air is colder, but thin air carries heat
    away far worse. Sealed RF/PA enclosures can run HOTTER at 4500 m and -30 C
    than at sea level and +25 C.
    """
    return density_ratio ** 0.8
