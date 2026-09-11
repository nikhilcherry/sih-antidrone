"""Headline result: pointing accuracy across an altitude/temperature sweep.

    python3 demo_pointing.py
"""
from aura.atmosphere import atmosphere
from aura.scenarios import SCENARIOS, SEED
from aura.sim import run

print(f"{'Scenario':<26} {'T (C)':>7} {'rho':>6} {'wind':>6} "
      f"{'uncomp':>10} {'comp':>9} {'gain':>7}")
print("-" * 76)
for s in SCENARIOS:
    atm = atmosphere(s.altitude_m, s.temp_offset_K)
    u = run(atm, compensate=False, wind_speed=s.wind_ms, seed=SEED)
    c = run(atm, compensate=True,  wind_speed=s.wind_ms, seed=SEED)
    print(f"{s.name:<26} {atm.temp_C:7.1f} {atm.density:6.3f} {s.wind_ms:5.0f}m/s "
          f"{u.rms_urad:9.0f}u {c.rms_urad:8.0f}u {u.rms_urad/c.rms_urad:6.1f}x")
print("-" * 76)
print("RMS effector-to-target pointing error, microradians. Same seed, same "
      "target pass;\nthe only difference is the compensation layer.")
