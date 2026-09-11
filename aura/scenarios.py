"""The four reference deployments behind the headline table.

Shared by `demo_pointing.py` and the ops console so the numbers on screen and
the numbers in the README come out of one table and one seed.
"""
from dataclasses import dataclass

SEED = 7


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    altitude_m: float
    temp_offset_K: float   # departure from ISA standard day
    wind_ms: float


SCENARIOS = (
    Scenario("sea",     "Sea level, standard day",     0,   0.0,  6.0),
    Scenario("leh",     "Leh, 3500 m, winter",      3500, -20.0, 12.0),
    Scenario("forward", "Forward post, 4500 m",     4500, -25.0, 15.0),
    Scenario("siachen", "Siachen-class, 5500 m",    5500, -25.0, 20.0),
)
