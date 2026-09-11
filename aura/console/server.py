"""AURA ops console: FastAPI + one WebSocket bus.

Every connected screen shares ONE environment. Drag a slider on the laptop and
the projector moves with it, which is the point: the presenter drives, the
panel watches the same picture.

Work is tiered by cost so the screen never waits on the slowest thing:

  env       analytic health / endurance / atmosphere   every slider tick
  core      uncompensated + compensated runs, traces   ~0.15 s, own 2-worker pool
  ablation  five leave-one-out runs                    after core, if still current
  sweep     13 altitudes x 2 modes                     only when weather changes

A newer slider position abandons older work at each tier boundary rather than
queueing behind it, and results are cached by their inputs, so flicking between
presets is instant after the first visit.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import os
from collections import OrderedDict
from concurrent.futures import Executor, ProcessPoolExecutor
from functools import partial
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import model

log = logging.getLogger("aura.console")
STATIC = Path(__file__).parent / "static"
CACHE_SIZE = 512
PROBE_EVERY_S = 3.0


class Bus:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                self.clients.discard(ws)


class Engine:
    def __init__(self, bus: Bus, pools: tuple[Executor, Executor], eo_url: str | None) -> None:
        self.bus = bus
        # Core runs get their own pool: the screen must never wait behind a
        # sweep that a previous slider position started.
        self.fast, self.slow = pools
        self.eo_url = eo_url
        self.env = dict(model.DEFAULT_ENV)
        self.latest: dict[str, dict] = {}     # replayed to screens that join late
        self.dirty = asyncio.Event()
        self._cache: OrderedDict = OrderedDict()
        self._set_env(self.env)

    # --- inputs -------------------------------------------------------------
    def _set_env(self, env: dict) -> dict:
        self.env = env
        msg = {"type": "env", **model.instant(env)}
        self.latest["env"] = msg
        self.dirty.set()
        return msg

    def update(self, patch: dict) -> dict:
        return self._set_env(model.clamp_env(patch, self.env))

    def preset(self, key: str) -> dict:
        return self._set_env(model.preset_env(key, self.env))

    # --- compute ------------------------------------------------------------
    async def _sims(self, calls: list[tuple], pool: Executor, **kw) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await asyncio.gather(*(
            loop.run_in_executor(pool, partial(model.simulate, *c, **kw))
            for c in calls))

    async def _cached(self, tag: tuple, compute):
        if tag in self._cache:
            self._cache.move_to_end(tag)
            return self._cache[tag]
        value = await compute()
        self._cache[tag] = value
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return value

    async def _core(self, key: tuple) -> dict:
        unc, cmp = await self._sims([(*key, False), (*key, True)], self.fast,
                                     with_trace=True)
        return {"uncomp": unc, "comp": cmp}

    async def _ablation(self, key: tuple, core: dict) -> dict:
        without = await self._sims(model.ablation_calls(key), self.slow)
        return model.ablation_payload(core["comp"]["rms"], core["uncomp"]["rms"],
                                      [w["rms"] for w in without])

    async def _sweep(self, temp_offset_K: float, wind_ms: float) -> dict:
        calls = [(a, temp_offset_K, wind_ms, c)
                 for a in model.SWEEP_ALTITUDES for c in (False, True)]
        res = await self._sims(calls, self.slow)
        return {"altitudes": list(model.SWEEP_ALTITUDES),
                "uncomp": [r["rms"] for r in res[0::2]],
                "comp": [r["rms"] for r in res[1::2]],
                "temp_offset_K": temp_offset_K, "wind_ms": wind_ms}

    async def _publish(self, kind: str, key, payload: dict) -> None:
        msg = {"type": kind, "key": list(key), **payload}
        self.latest[kind] = msg
        await self.bus.broadcast(msg)

    async def work(self) -> None:
        background: asyncio.Task | None = None
        while True:
            await self.dirty.wait()
            self.dirty.clear()
            key = model.sim_key(self.env)
            try:
                core = await self._cached(("core", key), lambda: self._core(key))
                await self._publish("core", key, core)
            except Exception:
                log.exception("console core failed for %s", key)
                continue
            if self.dirty.is_set():
                continue
            # Ablation and sweep run behind the core loop, and a newer slider
            # position cancels them -- which also drops their queued pool jobs.
            if background and not background.done():
                background.cancel()
            background = asyncio.create_task(self._background(key, core))

    async def _background(self, key: tuple, core: dict) -> None:
        try:
            abl = await self._cached(("abl", key), lambda: self._ablation(key, core))
            await self._publish("ablation", key, abl)
            wx = key[1:]
            sweep = await self._cached(("sweep", wx), lambda: self._sweep(*wx))
            await self._publish("sweep", wx, sweep)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("console background compute failed for %s", key)

    # --- links --------------------------------------------------------------
    async def status(self, eo_online: bool | None = None) -> None:
        prev = self.latest.get("status", {})
        if eo_online is None:
            eo_online = prev.get("eo", {}).get("online", False)
        msg = {"type": "status",
               "eo": {"url": self.eo_url, "online": bool(eo_online)},
               "sdr": model.find_sdr(),
               "viewers": len(self.bus.clients)}
        if msg != prev:
            self.latest["status"] = msg
            await self.bus.broadcast(msg)

    async def probe(self) -> None:
        async with httpx.AsyncClient(timeout=0.8) as client:
            while True:
                online = False
                if self.eo_url:
                    try:
                        online = (await client.head(self.eo_url)).status_code < 400
                    except httpx.HTTPError:
                        online = False
                await self.status(online)
                await asyncio.sleep(PROBE_EVERY_S)


def default_pools() -> tuple[Executor, Executor]:
    # spawn, not fork: uvicorn is threaded by the time the pools start, and a
    # forked child inheriting a held lock is a hang we would meet on stage.
    ctx = multiprocessing.get_context("spawn")
    slow = max(2, min(10, (os.cpu_count() or 4) - 2))
    return (ProcessPoolExecutor(max_workers=2, mp_context=ctx),
            ProcessPoolExecutor(max_workers=slow, mp_context=ctx))


def create_app(eo_url: str | None = None, pools_factory=default_pools) -> FastAPI:
    bus = Bus()
    state: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        pools = pools_factory()
        engine = Engine(bus, pools, eo_url)
        state["engine"] = engine
        tasks = [asyncio.create_task(engine.work()), asyncio.create_task(engine.probe())]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for pool in pools:
                pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="AURA ops console", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/scenarios")
    async def scenarios() -> list[dict]:
        return model.scenarios()

    @app.get("/api/limits")
    async def limits() -> dict:
        return {k: {"min": lo, "max": hi, "step": st}
                for k, (lo, hi, st) in model.LIMITS.items()}

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        engine: Engine = state["engine"]
        await sock.accept()
        bus.clients.add(sock)
        for kind in ("env", "core", "ablation", "sweep", "status"):
            if kind in engine.latest:
                await sock.send_json(engine.latest[kind])
        await engine.status()
        try:
            while True:
                try:
                    msg = await sock.receive_json()
                except (ValueError, TypeError):
                    continue          # one malformed frame must not drop the screen
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "set" and isinstance(msg.get("env"), dict):
                    await bus.broadcast(engine.update(msg["env"]))
                elif msg.get("type") == "preset":
                    await bus.broadcast(engine.preset(str(msg.get("key"))))
        except WebSocketDisconnect:
            pass
        finally:
            bus.clients.discard(sock)
            await engine.status()

    return app
