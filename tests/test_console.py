"""Ops console guards: the screen must show the same numbers as the README."""
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aura.atmosphere import atmosphere
from aura.console import model
from aura.console.server import create_app
from aura.scenarios import SCENARIOS, SEED
from aura.sim import TECHNIQUES, run


def _err(**kw):
    return run(atmosphere(4500, -25), wind_speed=15, seed=SEED, **kw).err_urad


def test_all_techniques_is_exactly_the_old_compensated_run():
    assert np.array_equal(_err(compensate=True), _err(compensate=True, techniques=TECHNIQUES))


def test_no_techniques_is_exactly_uncompensated():
    assert np.array_equal(_err(compensate=False), _err(compensate=True, techniques=()))


def test_unknown_technique_is_rejected():
    with pytest.raises(ValueError):
        _err(compensate=True, techniques=("flux_capacitor",))


def test_clamp_env_clamps_snaps_and_ignores_junk():
    env = model.clamp_env({"altitude_m": 99999, "wind_ms": 7.26, "temp_offset_K": "nan",
                           "visibility_km": "fog", "evil": 1, "compensate": 1})
    assert env["altitude_m"] == 6000.0
    assert env["wind_ms"] == 7.5                       # snapped to the 0.5 step
    assert env["temp_offset_K"] == model.DEFAULT_ENV["temp_offset_K"]
    assert env["visibility_km"] == model.DEFAULT_ENV["visibility_km"]
    assert "evil" not in env and env["compensate"] is True


def test_presets_are_the_readme_scenarios():
    for s in SCENARIOS:
        env = model.preset_env(s.key, model.DEFAULT_ENV)
        assert model.sim_key(env) == (s.altitude_m, s.temp_offset_K, s.wind_ms)


def test_health_rows_are_well_formed_and_physical():
    siachen = model.clamp_env({"altitude_m": 5500, "temp_offset_K": -25, "wind_ms": 20})
    rows = {r["id"]: r for r in model.health(siachen)}
    assert len(rows) == len(model.health(siachen))           # ids unique
    assert all(r["status"] in ("ok", "degraded", "critical") for r in rows.values())
    assert rows["boresight"]["status"] == "critical"
    assert rows["rf"]["value"] < 0                            # cold LNA is quieter
    assert {r["comp"] for r in rows.values() if r["comp"]} == set(TECHNIQUES)
    sea = {r["id"]: r for r in model.health(model.DEFAULT_ENV)}
    assert sea["battery"]["status"] == "ok" and sea["boresight"]["status"] == "ok"


def test_find_sdr_reads_sysfs(tmp_path):
    dongle, mouse = tmp_path / "1-2", tmp_path / "1-3"
    for d, (vid, pid) in ((mouse, ("046d", "c077")), (dongle, ("0bda", "2838"))):
        d.mkdir()
        (d / "idVendor").write_text(vid + "\n")
        (d / "idProduct").write_text(pid + "\n")
    assert model.find_sdr(tmp_path) == {"present": True, "id": "0bda:2838"}
    assert model.find_sdr(tmp_path / "missing")["present"] is False


def _until(ws, pred, limit=200):
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("expected message never arrived")


def test_websocket_bus_reproduces_the_headline_numbers():
    pools = lambda: (ThreadPoolExecutor(2), ThreadPoolExecutor(4))   # noqa: E731
    with TestClient(create_app(eo_url=None, pools_factory=pools)) as client:
        with client.websocket_connect("/ws") as ws:
            assert _until(ws, lambda m: m["type"] == "env")["env"]["altitude_m"] == 0.0
            ws.send_text("not json")                         # must not drop the screen
            ws.send_json({"type": "preset", "key": "siachen"})
            core = _until(ws, lambda m: m["type"] == "core" and m["key"] == [5500, -25, 20])
            assert round(core["uncomp"]["rms"]) == 697       # README headline table
            assert round(core["comp"]["rms"]) == 295
            assert len(core["comp"]["trace"]) == 10000 // model.TRACE_STRIDE
            abl = _until(ws, lambda m: m["type"] == "ablation" and m["key"] == [5500, -25, 20])
            assert [i["id"] for i in abl["items"]] == list(TECHNIQUES)
            assert max(abl["items"], key=lambda i: i["cost"])["id"] == "boresight_cal"
            sweep = _until(ws, lambda m: m["type"] == "sweep" and m["key"] == [-25, 20])
            assert len(sweep["uncomp"]) == len(model.SWEEP_ALTITUDES)
