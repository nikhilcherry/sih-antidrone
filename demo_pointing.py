"""Headline result: pointing accuracy across an altitude/temperature sweep.

    python3 demo_pointing.py
"""
from aura.atmosphere import atmosphere
from aura.sim import run

CASES = [
    ("Sea level, standard day",      0,   0.0,  6.0),
    ("Leh, 3500 m, winter",       3500, -20.0, 12.0),
    ("Forward post, 4500 m",      4500, -25.0, 15.0),
    ("Siachen-class, 5500 m",     5500, -25.0, 20.0),
]

print(f"{'Scenario':<26} {'T (C)':>7} {'rho':>6} {'wind':>6} "
      f"{'uncomp':>10} {'comp':>9} {'gain':>7}")
print("-" * 76)
for name, alt, dT, wind in CASES:
    atm = atmosphere(alt, dT)
    u = run(atm, compensate=False, wind_speed=wind, seed=7)
    c = run(atm, compensate=True,  wind_speed=wind, seed=7)
    print(f"{name:<26} {atm.temp_C:7.1f} {atm.density:6.3f} {wind:5.0f}m/s "
          f"{u.rms_urad:9.0f}u {c.rms_urad:8.0f}u {u.rms_urad/c.rms_urad:6.1f}x")
print("-" * 76)
print("RMS effector-to-target pointing error, microradians. Same seed, same "
      "target pass;\nthe only difference is the compensation layer.")
