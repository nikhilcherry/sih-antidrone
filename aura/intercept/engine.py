"""Detect -> identify -> decide, per track. Runs on the LAPTOP, in the Drone_Ml venv.

The detector has ONE class, `drone`. It cannot tell friend from foe, and no
camera-only system honestly can: a friendly quad and a hostile quad are the
same airframe. So the foe decision comes from where it does in a real C-UAS
chain -- an IFF / Remote-ID interrogation -- and the camera only supplies the
track. Every confirmed track is interrogated; no valid reply means HOSTILE.

    TRACKING --confirmed--> IFF_QUERY --valid reply--> FRIENDLY  (allowed to pass)
                                      --no reply----> HOSTILE --> ENGAGING --> NEUTRALISED

There is ONE effector. It services one hostile at a time, nearest first (the
widest box); the rest are held in a queue. That is what a swarm does to a
real site, and it is the question a panel asks.

In the demo the IFF reply is supplied by the operator console (the "friendly
beacon" switch, or a per-track declaration), because there is no transponder
to fly. The console labels it SIMULATED rather than hiding it.

The effector is modelled too. The SDR is receive-only and transmitting is
unlawful (Indian Telegraph Act / WPC), so NEUTRALISED is a fire-control
decision and a logged command, never an emission.

Every confirmed track becomes an engagement row with an evidence frame, kept
under data/intercept_sessions/<session>/ as the after-action record.

Two clocks. Decision logic runs on a clock that stops while recorded footage
is paused, so a track does not "time out" while the presenter talks. Every
timestamp a human reads is wall-clock (Zulu).
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from aura.eo.detect_live import build_gate
from aura.eo.optics import Sensor, range_for_pixels

from .voice import phonetic

DRONE_ML = Path.home() / "Projects" / "Drone_Ml"
WEIGHTS = DRONE_ML / "runs/detect/runs/detect/p2_s/weights/best.pt"
TRACKER = DRONE_ML / "cfg/bytetrack_drone.yaml"
FONT = Path(__file__).parent / "static" / "fonts" / "IBMPlexMono-500.woff2"
SESSIONS = Path(__file__).resolve().parents[2] / "data" / "intercept_sessions"

# Timings are in seconds, not frames, so a 15 FPS camera and a 60 FPS file
# make the same decision at the same moment on screen.
CONFIRM_HITS = 4       # confident frames before a track counts as real
IFF_WINDOW_S = 1.2     # interrogation time before "no reply" is declared
ENGAGE_S = 1.6         # effector dwell from fire command to kill assessment
LOST_S = 2.5           # unseen this long -> track dropped
FRESH_S = 0.5          # seen this recently -> the effector may be laid on it

TRACKING, IFF_QUERY, FRIENDLY, HOSTILE, ENGAGING, NEUTRALISED = (
    "TRACKING", "IFF_QUERY", "FRIENDLY", "HOSTILE", "ENGAGING", "NEUTRALISED")
URGENCY = (ENGAGING, HOSTILE, IFF_QUERY, NEUTRALISED, FRIENDLY, TRACKING)


def _deg(v: float) -> str:
    """Signed, one decimal, typographic minus: +18.0 / −18.7."""
    return f"{v:+.1f}".replace("-", "−")


def _bgr(hex_: str) -> tuple[int, int, int]:
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return b, g, r


# Doctrine palette. Colour is semantic only: green = own / friendly / history,
# oxide = contact / alert. Everything else is ink and paper.
PAPER = _bgr("#F4F2EC")
PAPER_RAISED = _bgr("#FBFAF6")
RULE = _bgr("#C9C4B6")
GRID = _bgr("#E2DED1")
INK = _bgr("#1B2430")
INK_FAINT = _bgr("#8A8578")
GREEN = _bgr("#2F5D50")
OXIDE = _bgr("#9C3B22")


@dataclass
class Track:
    tid: int
    first_seen: float                 # logic clock, like last_seen / state_since
    last_seen: float
    hits: int = 0
    conf: float = 0.0
    box: tuple[float, float, float, float] = (0, 0, 0, 0)
    state: str = TRACKING
    state_since: float = 0.0
    override: str | None = None       # "friendly" | "hostile", declared by the operator
    fire_ordered: bool = False
    bearing: float = 0.0
    elevation: float = 0.0
    range_m: float | None = None
    eng: int | None = None            # engagement number, once confirmed
    queue: int | None = None          # position behind the effector, if waiting
    evidence: str | None = None       # URL of the frame captured at decision
    letter: str | None = None         # human-in-the-loop designation, A..Z
    hold: bool = False                # operator aborted: never auto-engage again
    was_queued: bool = False
    trail: deque = field(default_factory=lambda: deque(maxlen=90))      # pixels
    trail_ae: deque = field(default_factory=lambda: deque(maxlen=60))   # bearing, elevation
    record: dict = field(default_factory=dict)   # stage -> (wall time, note, logic time)

    def public(self, now: float) -> dict:
        progress = None
        if self.state == IFF_QUERY:
            progress = min(1.0, (now - self.state_since) / IFF_WINDOW_S)
        elif self.state == ENGAGING:
            progress = min(1.0, (now - self.state_since) / ENGAGE_S)
        return {
            "id": self.tid, "state": self.state, "conf": round(self.conf, 3),
            "hits": self.hits, "age": round(now - self.first_seen, 1),
            "bearing": round(self.bearing, 1), "elevation": round(self.elevation, 1),
            "range": None if self.range_m is None else round(self.range_m),
            "override": self.override, "progress": progress, "queue": self.queue,
            "eng": self.eng, "fresh": now - self.last_seen < FRESH_S, "letter": self.letter,
            "record": {k: [round(w, 2), note] for k, (w, note, _) in self.record.items()},
            "trail": [[round(b, 1), round(e, 1)] for b, e in list(self.trail_ae)[::2]],
        }


class Engine:
    """Owns the capture, the model, and the decision table. One thread."""

    def __init__(self, source: str = "0", device: str = "0", imgsz: int = 640,
                 conf: float = 0.25, det_floor: float = 0.03, hfov: float = 78.0,
                 target_m: float = 0.3, min_range_m: float = 3.0) -> None:
        self.device, self.imgsz = device, imgsz
        self.conf, self.det_floor = conf, det_floor
        self.hfov, self.target_m, self.min_range_m = hfov, target_m, min_range_m
        self.vfov = hfov * 9 / 16

        self.lock = threading.Lock()
        self.frame_cv = threading.Condition()
        self.jpeg: bytes | None = None
        self.pub_seq = 0              # bumps on every published JPEG, wakes MJPEG clients
        self.frame_no = 0

        # operator-facing settings
        self.mode = "AUTO"            # AUTO = weapons free, HOLD = operator authorises
        self.iff_beacon = False       # simulated friendly transponder on air
        self.gate_on = True

        self.tracks: dict[int, Track] = {}
        self.events: deque[dict] = deque(maxlen=60)
        self.event_seq = 0
        self.counts = {"detected": 0, "allowed": 0, "neutralised": 0}
        self.gate_rejected = 0
        self.fps = 0.0
        self.source = source
        self.source_label = ""
        self.source_error: str | None = None
        self.frame_size = (0, 0)
        self.src_fps = 25.0

        # playback (recorded footage only) and the logic clock it freezes
        self.paused = False
        self._pause_wall: float | None = None
        self._paused_total = 0.0
        self._restart = False
        self._last_drawn = None

        # recording of the annotated feed
        self.rec_want = False
        self._rec: dict | None = None
        self.rec_file: str | None = None

        # human in the loop: letter designations, orders by voice, spoken callouts
        self.hitl = False
        self._mode_before_hitl = "AUTO"
        self.callouts: deque[dict] = deque(maxlen=20)
        self.callout_seq = 0
        self._letter_next = 0         # designations advance; never reused within a session

        self._new_session()
        self._pending_source: str | None = None
        self._stop = threading.Event()
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self.model = None
        self.status = "loading model"
        self.request_source(source)

    # ---------------------------------------------------------------- clocks
    def _now(self) -> float:
        """Logic clock: wall time minus time spent paused. Frozen while paused."""
        if self._pause_wall is not None:
            return self._pause_wall - self._paused_total
        return time.time() - self._paused_total

    # ---------------------------------------------------------------- session
    def _new_session(self) -> None:
        self.session_start = time.time()
        self.session_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.session_start))
        self.session_dir = SESSIONS / self.session_id
        self.engagements: dict[int, dict] = {}
        self.eng_seq = 0
        self.log_all: list[dict] = []

    def _session_write(self, name: str, text: str, append: bool = False) -> None:
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            with open(self.session_dir / name, "a" if append else "w") as f:
                f.write(text)
        except OSError:
            pass                      # the record is a convenience; never stop the loop for it

    def report(self) -> dict:
        with self.lock:
            rows = [dict(e) for e in self.engagements.values()]
            d2k = [e["d2k"] for e in rows if e["d2k"] is not None]
            return {
                "session": self.session_id, "start": self.session_start, "end": time.time(),
                "source": self.source_label, "counts": dict(self.counts),
                "mean_d2k": round(sum(d2k) / len(d2k), 2) if d2k else None,
                "engagements": rows, "log": list(self.log_all),
                "mode": self.mode, "hfov": self.hfov, "model": "YOLO11 p2_s",
                "dir": str(self.session_dir),
            }

    # ---------------------------------------------------------------- control
    def request_source(self, source: str) -> None:
        if not self.is_live(source) and Path(source).exists():
            source = str(Path(source).resolve())   # the page matches sources by absolute path
        with self.lock:
            self._pending_source = source

    def set_mode(self, mode: str) -> None:
        with self.lock:
            self.mode = "HOLD" if mode.upper() == "HOLD" else "AUTO"
            if self.mode == "AUTO" and self.hitl:
                self.hitl = False     # weapons free and a human in the loop are contradictory
                for t in self.tracks.values():
                    t.letter = None
                self._log("info", None, "Human in the loop disengaged.")
            self._log("info", None, "Rules of engagement set: " + (
                "weapons free." if self.mode == "AUTO" else "hold. Operator authorises each engagement."))

    def set_beacon(self, on: bool) -> None:
        with self.lock:
            self.iff_beacon = bool(on)
            self._log("info", None, f"Friendly IFF beacon {'on' if on else 'off'}. Simulated.")

    def set_gate(self, on: bool) -> None:
        with self.lock:
            self.gate_on = bool(on)
            self._log("info", None, f"Geometry gate {'on' if on else 'off'}.")

    def set_paused(self, on: bool) -> None:
        with self.lock:
            if self.is_live(self.source) or bool(on) == self.paused:
                return                # live sensors are never held
            if on:
                self._pause_wall = time.time()
            else:
                self._paused_total += time.time() - self._pause_wall
                self._pause_wall = None
            self.paused = bool(on)
            self._log("info", None, "Playback held." if on else "Playback resumed.")
            held = self._last_drawn.copy() if on and self._last_drawn is not None else None
        if held is not None:
            self._plate(held, 14, 14 + 40, "PLAYBACK HELD", INK, INK)
            self._publish(held)

    def restart(self) -> None:
        with self.lock:
            if not self.is_live(self.source):
                self._restart = True
        self.set_paused(False)

    def set_recording(self, on: bool) -> None:
        with self.lock:
            self.rec_want = bool(on)

    # ---------------------------------------------------------------- human in the loop
    def set_hitl(self, on: bool) -> None:
        with self.lock:
            if bool(on) == self.hitl:
                return
            self.hitl = bool(on)
            if on:
                self._letter_next = 0
                self._mode_before_hitl = self.mode
                self.mode = "HOLD"
                for t in sorted(self.tracks.values(), key=lambda t: t.tid):
                    if t.eng is not None:
                        self._designate(t)
                n = sum(1 for t in self.tracks.values() if t.letter)
                self._log("info", None, "Human in the loop engaged. Contacts designated by letter; "
                          "engagement on operator order only.")
                self._callout("Human in the loop. " + (f"{n} contact{'s' if n != 1 else ''} designated."
                                                       if n else "Awaiting contacts."))
            else:
                self.mode = self._mode_before_hitl
                for t in self.tracks.values():
                    t.letter = None
                self._log("info", None, "Human in the loop disengaged. Rules of engagement: " +
                          ("weapons free." if self.mode == "AUTO" else "hold."))

    def _designate(self, t: Track) -> None:
        """Next letter in sequence, never the lowest free one. If "alpha" leaves and a
        new drone arrives while an order about alpha is still being transcribed,
        reusing A would put that order on the wrong aircraft."""
        if t.letter:
            return
        used = {x.letter for x in self.tracks.values() if x.letter}
        for k in range(26):
            i = (self._letter_next + k) % 26
            if chr(65 + i) not in used:
                t.letter = chr(65 + i)
                self._letter_next = (i + 1) % 26
                return

    def _desig(self, t: Track) -> str:
        return f"contact {phonetic(t.letter)}" if t.letter else f"track {t.tid}"

    def _callout(self, text: str) -> None:
        self.callout_seq += 1
        self.callouts.append({"seq": self.callout_seq, "text": text})

    def by_letter(self, letter: str) -> Track | None:
        with self.lock:
            return next((t for t in self.tracks.values() if t.letter == letter), None)

    def note(self, level: str, tid: int | None, text: str) -> None:
        with self.lock:
            self._log(level, tid, text)

    def board(self) -> dict:
        """What the operator's question can be answered from: the board, nothing else."""
        with self.lock:
            now = self._now()
            cs = [{"letter": t.letter, "track": t.tid, "state": t.state,
                   "confidence": round(t.conf, 2), "bearing_deg": round(t.bearing, 1),
                   "elevation_deg": round(t.elevation, 1),
                   "range_m": None if t.range_m is None else round(t.range_m),
                   "in_view": now - t.last_seen < FRESH_S, "queue_position": t.queue,
                   "seconds_tracked": round(now - t.first_seen, 1),
                   "ordered_engage": t.fire_ordered}
                  for t in sorted(self.tracks.values(), key=lambda t: t.letter or "~") if t.letter]
            d2k = [e["d2k"] for e in self.engagements.values() if e["d2k"] is not None]
            return {"contacts": cs, "rules_of_engagement": "hold, human in the loop" if self.hitl else
                    ("weapons free" if self.mode == "AUTO" else "hold"),
                    "effectors": 1, "totals": dict(self.counts),
                    "mean_detect_to_kill_s": round(sum(d2k) / len(d2k), 1) if d2k else None,
                    "source": self.source_label, "undesignated_tracks": sum(1 for t in self.tracks.values() if not t.letter),
                    "recent_log": [e["text"] for e in list(self.events)[:8]]}

    def order_engage(self, tid: int, force: bool = False) -> str:
        """Operator orders a strike. Returns what happened, for the spoken reply."""
        with self.lock:
            t = self.tracks.get(tid)
            if t is None:
                return "missing"
            if t.state == NEUTRALISED:
                return "done"
            if t.state == ENGAGING:
                return "engaging"
            if t.state == FRIENDLY and not force:
                return "friendly"
            now, wall = self._now(), time.time()
            if t.state != HOSTILE:
                if t.state == FRIENDLY:
                    self.counts["allowed"] -= 1
                    t.record.pop("decide", None)
                    t.record.pop("effect", None)
                    t.evidence = None
                t.record["identify"] = (wall, "OPERATOR", now)
                self._enter(t, HOSTILE, now)
                self._sync(t, "OPEN")
            t.override, t.fire_ordered, t.hold = "hostile", True, False
            busy = any(x.state == ENGAGING for x in self.tracks.values())
            self._log("crit", tid, f"Operator ordered engagement of {self._desig(t)}.")
            return "queued" if busy else "ordered"

    def order_pass(self, tid: int) -> str:
        with self.lock:
            t = self.tracks.get(tid)
            if t is None:
                return "missing"
            if t.state in (NEUTRALISED, ENGAGING):
                return t.state.lower()
            if t.state == FRIENDLY:
                return "already"
            now, wall = self._now(), time.time()
            t.override, t.fire_ordered = "friendly", False
            t.record["identify"] = (wall, "OPERATOR", now)
            self._friendly(t, now, f"Operator cleared {self._desig(t)} to pass.")
            return "passed"

    def abort(self, tid: int) -> str:
        """Stop an engagement in progress, or withdraw a queued fire order."""
        with self.lock:
            t = self.tracks.get(tid)
            if t is None or not (t.state == ENGAGING or (t.state == HOSTILE and t.fire_ordered)):
                return "idle"
            t.fire_ordered, t.hold = False, True
            if t.state == ENGAGING:
                t.record.pop("decide", None)
                self._enter(t, HOSTILE, self._now())
                self._sync(t, "OPEN")
            self._log("warn", tid, f"Engagement of {self._desig(t)} aborted by operator. Target held.")
            return "aborted"

    def ordered_ids(self) -> list[int]:
        """Everything the effector is on or about to be on: what "cease fire" stops."""
        with self.lock:
            return [t.tid for t in self.tracks.values()
                    if t.state == ENGAGING or (t.state == HOSTILE and t.fire_ordered)]

    def override(self, tid: int, verdict: str) -> None:
        with self.lock:
            t = self.tracks.get(tid)
            if not t or t.state in (NEUTRALISED, ENGAGING):
                return
            t.override = verdict
            if t.state == TRACKING:
                self._log("info", tid, f"Operator declared track {tid} {verdict}. "
                          "Applied on confirmation.")
                return
            now, wall = self._now(), time.time()
            if verdict == "friendly" and t.state != FRIENDLY:
                t.record["identify"] = (wall, "OPERATOR", now)
                self._friendly(t, now, f"Operator declared track {tid} friendly. Allowed to pass.")
            elif verdict == "hostile" and t.state != HOSTILE:
                if t.state == FRIENDLY:
                    self.counts["allowed"] -= 1
                    t.record.pop("decide", None)
                    t.record.pop("effect", None)
                    t.evidence = None
                t.record["identify"] = (wall, "OPERATOR", now)
                self._enter(t, HOSTILE, now)
                self._sync(t, "OPEN")
                self._log("crit", tid, f"Operator declared track {tid} hostile.")

    def engage(self, tid: int) -> None:
        with self.lock:
            t = self.tracks.get(tid)
            if t and t.state == HOSTILE:
                t.fire_ordered = True
                self._log("crit", tid, f"Operator authorised engagement of track {tid}.")

    def reset(self) -> None:
        """Clear the board and open a new session: the report covers one board."""
        with self.lock:
            self.tracks.clear()
            self.counts = {k: 0 for k in self.counts}
            self.gate_rejected = 0
            self.events.clear()
            self._letter_next = 0
            self._new_session()
            self._reset_tracker()
            self._log("info", None, "Board cleared. New session opened.")

    def stop(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        """Called on exit: finish any recording so the MP4 is not truncated."""
        self._stop.set()
        self.rec_want = False
        if self._rec is not None:
            self._rec_stop(wait=True)

    # ---------------------------------------------------------------- state
    def snapshot(self) -> dict:
        with self.lock:
            now = self._now()
            tracks = [t.public(now) for t in sorted(self.tracks.values(), key=lambda t: t.tid)]
            verdict, lead = self._verdict(tracks)
            d2k = [e["d2k"] for e in self.engagements.values() if e["d2k"] is not None]
            return {
                "status": self.status, "source": self.source, "source_label": self.source_label,
                "source_error": self.source_error, "frame": list(self.frame_size),
                "fps": round(self.fps, 1), "mode": self.mode, "iff_beacon": self.iff_beacon,
                "gate": self.gate_on, "gate_rejected": self.gate_rejected,
                "counts": dict(self.counts), "tracks": tracks, "verdict": verdict, "lead": lead,
                "events": list(self.events), "hfov": self.hfov, "vfov": round(self.vfov, 1),
                "target_m": self.target_m, "live": self.is_live(self.source),
                "paused": self.paused, "recording": self._rec is not None,
                "rec_file": self.rec_file, "session": self.session_id,
                "mean_d2k": round(sum(d2k) / len(d2k), 1) if d2k else None,
                "engagements": len(self.engagements),
                "hitl": self.hitl, "callouts": list(self.callouts),
            }

    @staticmethod
    def _verdict(tracks: list[dict]) -> tuple[str, int | None]:
        """The finding on the sheet, and the track it is about: the most urgent one."""
        live = [t for t in tracks if t["fresh"] or t["state"] == NEUTRALISED]
        for s in URGENCY:
            for t in live:
                if t["state"] == s:
                    return s, t["id"]
        return "CLEAR", None

    def _log(self, level: str, tid: int | None, text: str) -> None:
        self.event_seq += 1
        e = {"seq": self.event_seq, "t": time.time(), "level": level, "track": tid, "text": text}
        self.events.appendleft(e)
        if len(self.log_all) < 5000:
            self.log_all.append(e)
        self._session_write("log.jsonl", json.dumps(e) + "\n", append=True)

    def _enter(self, t: Track, state: str, now: float) -> None:
        t.state, t.state_since = state, now

    def _friendly(self, t: Track, now: float, why: str) -> None:
        wall = time.time()
        self._enter(t, FRIENDLY, now)
        t.record["decide"] = (wall, "PASS", now)
        t.record["effect"] = (wall, "NONE", now)
        self.counts["allowed"] += 1
        self._sync(t, "PASSED")
        self._log("ok", t.tid, why)

    def _sync(self, t: Track, outcome: str | None = None) -> None:
        """Mirror a track's record into its engagement row, and persist the rows."""
        e = self.engagements.get(t.eng) if t.eng else None
        if e is None:
            return
        e["record"] = {k: [round(w, 2), note] for k, (w, note, _) in t.record.items()}
        e["evidence"] = t.evidence
        e["conf"] = round(t.conf, 2)
        e["bearing"], e["elevation"] = round(t.bearing, 1), round(t.elevation, 1)
        if outcome:
            e["outcome"] = outcome
        if outcome == "NEUTRALISED" and "effect" in t.record and "detect" in t.record:
            # Logic clock, so time spent paused mid-engagement is not counted.
            e["d2k"] = round(t.record["effect"][2] - t.record["detect"][2], 2)
        self._session_write("engagements.json", json.dumps(list(self.engagements.values()), indent=1))

    # ---------------------------------------------------------------- source
    @staticmethod
    def _open(source: str):
        src = int(source) if source.isdigit() else source
        cap = cv2.VideoCapture(src, cv2.CAP_V4L2) if isinstance(src, int) else cv2.VideoCapture(src)
        if not cap.isOpened():
            return None, f"cannot open {source}"
        if isinstance(src, int):
            # UVC exposure persists between processes; a previous manual setting
            # leaves the feed black. Ask for auto rather than inheriting it.
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return cap, None

    @staticmethod
    def is_live(source: str) -> bool:
        return source.isdigit() or source.startswith(("http://", "https://", "rtsp://"))

    def _label(self, source: str) -> str:
        if source.isdigit():
            return f"Camera {source}, live"
        if self.is_live(source):
            return "EO node stream, live"
        return f"{Path(source).name}, recorded"

    def _reset_tracker(self) -> None:
        pred = getattr(self.model, "predictor", None)
        for tr in getattr(pred, "trackers", None) or []:
            try:
                tr.reset()
            except Exception:
                pass

    def _drop_tracks(self) -> None:
        """Footage looped or restarted: open rows close as LOST, the tracker starts over."""
        for t in self.tracks.values():
            e = self.engagements.get(t.eng) if t.eng else None
            if e is not None and e["outcome"] == "OPEN":
                self._sync(t, "LOST")
        self.tracks.clear()
        self._letter_next = 0         # the board is empty, so no order can refer to an old letter
        self._reset_tracker()

    # ---------------------------------------------------------------- loop
    def run(self) -> None:
        from ultralytics import YOLO
        self.model = YOLO(str(WEIGHTS))
        # First inference compiles kernels (~1 s); pay it before anyone is watching.
        self.model.predict(np.zeros((480, 640, 3), np.uint8), imgsz=self.imgsz,
                           device=self.device, verbose=False)
        self.status = "ready"

        cap, is_file, frame_dt = None, False, 0.0
        gate_px, sensor = 1e9, None
        fps_hist: deque[float] = deque(maxlen=30)
        placeholder_at = 0.0

        while not self._stop.is_set():
            with self.lock:
                pending, self._pending_source = self._pending_source, None
            if pending is not None:
                if cap is not None:
                    cap.release()
                if self._rec is not None:
                    self._rec_stop()  # new frame size; a new file starts if still wanted
                cap, err = self._open(pending)
                is_file = not self.is_live(pending)
                with self.lock:
                    if self.paused:
                        self._paused_total += time.time() - self._pause_wall
                        self._pause_wall, self.paused = None, False
                    self.source, self.source_label = pending, self._label(pending)
                    self.source_error = err
                    self._drop_tracks()
                    # Footage of unknown optics cannot be gated on physics; a live
                    # camera in a room must be, or it "engages" the people in it.
                    self.gate_on = not is_file
                    if err:
                        self._log("warn", None, f"Source unavailable: {err}.")
                    else:
                        self._log("info", None, f"Source set: {self.source_label}. "
                                  f"Geometry gate {'on' if self.gate_on else 'off'}.")
                frame_dt = 0.0
                if cap is not None:
                    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                    self.src_fps = fps if 1 < fps < 121 else 25.0
                    frame_dt = 1.0 / self.src_fps if is_file else 0.0
                sensor = None

            if cap is None:
                if time.time() - placeholder_at > 0.5:
                    self._publish(self._placeholder(self.source_error or "no source selected"))
                    placeholder_at = time.time()
                time.sleep(0.05)
                continue

            if self._restart:
                self._restart = False
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                with self.lock:
                    self._drop_tracks()
                    self._log("info", None, "Playback restarted from the first frame.")

            if self.paused:
                if self._rec is not None and not self.rec_want:
                    self._rec_stop()
                time.sleep(0.03)
                continue

            t_frame = time.time()
            ok, frame = cap.read()
            if not ok:
                if is_file:           # loop footage so the demo never ends on a black frame
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    with self.lock:
                        self._drop_tracks()
                    continue
                with self.lock:
                    self.source_error = "stream ended"
                    self._log("warn", None, "Signal lost. Stream ended.")
                cap.release()
                cap = None
                continue

            H, W = frame.shape[:2]
            if W > 1280:              # 4K phone footage: the detector sees 640 anyway
                frame = cv2.resize(frame, (1280, int(H * 1280 / W)), interpolation=cv2.INTER_AREA)
                H, W = frame.shape[:2]
            if sensor is None:
                sensor = Sensor("live", W, H, self.hfov)
                gate_px = build_gate(W, H, self.hfov, self.target_m, self.min_range_m, 0.33)
                self.frame_size = (W, H)
                self.vfov = 2 * math.degrees(math.atan(math.tan(math.radians(self.hfov / 2)) * H / W))

            t0 = time.time()
            r = self.model.track(frame, imgsz=self.imgsz, device=self.device,
                                 conf=min(self.det_floor, self.conf), persist=True,
                                 tracker=str(TRACKER), verbose=False)[0]
            fps_hist.append(1.0 / max(1e-6, time.time() - t0))

            rejected_boxes = []
            with self.lock:
                now = self._now()
                self.fps = sum(fps_hist) / len(fps_hist)
                seen: set[int] = set()
                if r.boxes is not None and len(r.boxes) and r.boxes.id is not None:
                    for box, cf, tid in zip(r.boxes.xyxy.cpu().numpy(),
                                            r.boxes.conf.cpu().numpy(),
                                            r.boxes.id.int().tolist()):
                        x1, y1, x2, y2 = (float(v) for v in box)
                        if self.gate_on and (x2 - x1) > gate_px:
                            self.gate_rejected += 1
                            rejected_boxes.append((x1, y1, x2, y2))
                            continue
                        self._observe(tid, (x1, y1, x2, y2), float(cf), now, sensor, W, H)
                        seen.add(tid)
                self._advance(now, seen, frame)       # frame is still clean here: evidence
                self._draw(frame, now, rejected_boxes, gate_px)
                self._last_drawn = frame
            self._publish(frame)
            self._record(frame)
            self.frame_no += 1

            if frame_dt:              # play files at their own speed, not GPU speed
                spare = frame_dt - (time.time() - t_frame)
                if spare > 0:
                    time.sleep(spare)

        if cap is not None:
            cap.release()

    def _observe(self, tid, box, cf, now, sensor, W, H) -> None:
        t = self.tracks.get(tid)
        if t is None:
            t = self.tracks[tid] = Track(tid, now, now, state_since=now)
            t.record["detect"] = (time.time(), f"P {cf:.2f}", now)
        t.last_seen, t.box = now, box
        t.conf = cf if t.hits == 0 else 0.7 * t.conf + 0.3 * cf
        if cf >= self.conf:
            t.hits += 1
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        t.trail.append((cx, cy))
        t.bearing = (cx / W - 0.5) * self.hfov
        t.elevation = (0.5 - cy / H) * self.vfov
        t.trail_ae.append((t.bearing, t.elevation))
        # Range from apparent size needs the real lens. Recorded footage has
        # unknown optics, so it gets no range rather than a confident wrong one.
        t.range_m = (range_for_pixels(sensor, self.target_m, max(2.0, x2 - x1))
                     if self.is_live(self.source) else None)

    def _advance(self, now: float, seen: set[int], frame) -> None:
        wall = time.time()
        for t in list(self.tracks.values()):
            if t.tid not in seen and now - t.last_seen > LOST_S:
                if self.hitl and t.letter and t.state in (ENGAGING, HOSTILE):
                    self._callout(f"{phonetic(t.letter)} lost.")
                if t.state == ENGAGING:
                    self._log("warn", t.tid, f"Track {t.tid} lost before kill assessment.")
                elif t.state == FRIENDLY:
                    self._log("ok", t.tid, f"Track {t.tid} left sector.")
                elif t.state in (TRACKING, IFF_QUERY, HOSTILE):
                    self._log("info", t.tid, f"Track {t.tid} lost.")
                if t.eng and self.engagements.get(t.eng, {}).get("outcome") == "OPEN":
                    self._sync(t, "LOST")
                del self.tracks[t.tid]
                continue

            if t.state == TRACKING and t.hits >= CONFIRM_HITS:
                self.counts["detected"] += 1
                self.eng_seq += 1
                t.eng = self.eng_seq
                self.engagements[t.eng] = {
                    "no": t.eng, "track": t.tid, "source": self.source_label, "outcome": "OPEN",
                    "d2k": None, "record": {}, "evidence": None, "conf": None,
                    "bearing": None, "elevation": None}
                t.record["confirm"] = (wall, f"{t.hits} HITS", now)
                if self.hitl:
                    self._designate(t)
                if t.override == "friendly":
                    t.record["identify"] = (wall, "OPERATOR", now)
                    self._friendly(t, now, f"Track {t.tid} confirmed. Operator-declared friendly. "
                                   "Allowed to pass.")
                elif t.override == "hostile":
                    t.record["identify"] = (wall, "OPERATOR", now)
                    self._enter(t, HOSTILE, now)
                    self._sync(t)
                    self._log("crit", t.tid, f"Track {t.tid} confirmed. Operator-declared hostile.")
                else:
                    self._enter(t, IFF_QUERY, now)
                    self._sync(t)
                    self._log("info", t.tid, f"Track {t.tid} acquired. Confidence {t.conf:.2f}. "
                              "IFF interrogation begun.")
            elif t.state == IFF_QUERY and now - t.state_since >= IFF_WINDOW_S:
                if self.iff_beacon:
                    t.record["identify"] = (wall, "VALID REPLY", now)
                    self._friendly(t, now, f"Track {t.tid}: valid IFF reply. Classified friendly. "
                                   "Allowed to pass.")
                    if self.hitl and t.letter:
                        self._callout(f"Contact {phonetic(t.letter)}, friendly. Passing.")
                else:
                    t.record["identify"] = (wall, "NO REPLY", now)
                    self._enter(t, HOSTILE, now)
                    self._sync(t)
                    self._log("crit", t.tid, f"Track {t.tid}: no IFF reply in {IFF_WINDOW_S:.1f} s. "
                              "Classified hostile.")
                    if self.hitl and t.letter:
                        self._callout(f"Contact {phonetic(t.letter)}, hostile. Awaiting orders.")
            elif t.state == ENGAGING and now - t.state_since >= ENGAGE_S:
                t.record["effect"] = (wall, "KILL ASSESSED", now)
                self._enter(t, NEUTRALISED, now)
                self.counts["neutralised"] += 1
                self._sync(t, "NEUTRALISED")
                d2k = self.engagements[t.eng]["d2k"] if t.eng else None
                self._log("crit", t.tid, f"Track {t.tid} neutralised. Kill assessed"
                          + (f", {d2k:.1f} s from first detection." if d2k is not None else "."))
                if self.hitl and t.letter:
                    self._callout(f"{phonetic(t.letter)} neutralised.")

            # Evidence: the clean frame at the moment of decision, once, while in view.
            if "decide" in t.record and t.evidence is None and t.tid in seen:
                t.evidence = self._evidence(t, frame)
                self._sync(t)

        # One effector. It is laid on the nearest ready hostile; the rest queue.
        ready = sorted((t for t in self.tracks.values()
                        if t.state == HOSTILE and now - t.last_seen < FRESH_S
                        and ((self.mode == "AUTO" and not t.hold) or t.fire_ordered)),
                       key=lambda t: t.box[2] - t.box[0], reverse=True)
        if ready and not any(t.state == ENGAGING for t in self.tracks.values()):
            t = ready.pop(0)
            t.record["decide"] = (wall, "ENGAGE, OPERATOR" if t.fire_ordered else "ENGAGE, AUTO", now)
            self._enter(t, ENGAGING, now)
            t.queue = None
            self._sync(t)
            self._log("crit", t.tid, f"Track {t.tid} engaged. Bearing {_deg(t.bearing)}°, "
                      f"elevation {_deg(t.elevation)}°.")
            if self.hitl and t.letter and t.was_queued:
                self._callout(f"Engaging {phonetic(t.letter).lower()}.")
            if t.tid in seen and t.evidence is None:
                t.evidence = self._evidence(t, frame)
                self._sync(t)
        waiting = {t.tid for t in ready}
        for t in self.tracks.values():
            if t.tid not in waiting:
                t.queue = None
        for i, t in enumerate(ready, 1):
            if t.queue is None:
                self._log("crit", t.tid, f"Track {t.tid} queued, position {i}. Effector busy.")
            t.queue, t.was_queued = i, True

    def _evidence(self, t: Track, frame) -> str | None:
        H, W = frame.shape[:2]
        x1, y1, x2, y2 = t.box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        half = max(48.0, 1.6 * max(x2 - x1, y2 - y1))
        a, b = int(max(0, cx - half)), int(max(0, cy - half))
        c, d = int(min(W, cx + half)), int(min(H, cy + half))
        crop = frame[b:d, a:c]
        if crop.size == 0 or t.eng is None:
            return None
        crop = cv2.resize(crop, (240, max(1, int(240 * crop.shape[0] / crop.shape[1]))),
                          interpolation=cv2.INTER_CUBIC)
        name = f"E{t.eng:03d}_T{t.tid}.jpg"
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(self.session_dir / name), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        except OSError:
            return None
        return f"/evidence/{self.session_id}/{name}"

    # ---------------------------------------------------------------- recording
    def _record(self, frame) -> None:
        if self.rec_want and self._rec is None:
            self._rec_start(frame.shape[1], frame.shape[0])
        elif not self.rec_want and self._rec is not None:
            self._rec_stop()
        rec = self._rec
        if rec is None:
            return
        try:
            if rec["proc"] is not None:
                rec["proc"].stdin.write(frame.tobytes())
            else:
                rec["writer"].write(frame)
            rec["frames"] += 1
        except (BrokenPipeError, OSError, ValueError):
            self._rec_stop()

    def _rec_start(self, W: int, H: int) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / f"feed_{time.strftime('%H%M%SZ', time.gmtime())}.mp4"
        fps = round(self.src_fps, 3)
        ff = shutil.which("ffmpeg") or str(Path.home() / ".local/bin/ffmpeg")
        rec = {"path": path, "frames": 0, "fps": fps, "proc": None, "writer": None}
        if Path(ff).exists():
            # H.264 so the file plays anywhere (browser, WhatsApp, the portal).
            rec["proc"] = subprocess.Popen(
                [ff, "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                 "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-an",
                 "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
                stdin=subprocess.PIPE)
        else:
            rec["writer"] = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
        self._rec = rec
        self.rec_file = str(path)
        with self.lock:
            self._log("info", None, f"Recording started: {path.name}.")

    def _rec_stop(self, wait: bool = False) -> None:
        rec, self._rec = self._rec, None
        if rec is None:
            return
        secs = rec["frames"] / rec["fps"] if rec["fps"] else 0

        def finish():
            if rec["proc"] is not None:
                try:
                    rec["proc"].stdin.close()
                except OSError:
                    pass
                rec["proc"].wait(timeout=30)
            else:
                rec["writer"].release()
            with self.lock:
                self._log("info", None, f"Recording saved: {rec['path'].name}, {secs:.1f} s.")

        if wait:
            finish()
        else:
            threading.Thread(target=finish, daemon=True).start()

    # ---------------------------------------------------------------- drawing
    def _font(self, px: int) -> ImageFont.FreeTypeFont:
        f = self._fonts.get(px)
        if f is None:
            try:
                f = ImageFont.truetype(str(FONT), px)
            except OSError:
                f = ImageFont.load_default()
            self._fonts[px] = f
        return f

    def _plate(self, img, x: int, y_bottom: int, text: str, colour, border) -> None:
        """A caption plate: paper-raised ground, 1px rule, mono text. Opaque, square."""
        H, W = img.shape[:2]
        font = self._font(max(12, round(W / 64)))
        l, t, r, b = font.getbbox(text)
        pw, ph = r - l + 12, b - t + 10
        x = int(np.clip(x, 0, max(0, W - pw)))
        y = int(np.clip(y_bottom - ph, 0, max(0, H - ph)))
        plate = Image.new("RGB", (pw, ph), PAPER_RAISED)      # BGR order throughout
        d = ImageDraw.Draw(plate)
        d.rectangle((0, 0, pw - 1, ph - 1), outline=border)
        d.text((6 - l, 5 - t), text, font=font, fill=colour)
        patch = np.asarray(plate)
        img[y:y + ph, x:x + pw] = patch[: H - y, : W - x]

    @staticmethod
    def _keyline_rect(img, p1, p2, colour, th) -> None:
        # A 1px paper keyline outside the stroke keeps it legible on dark ground.
        cv2.rectangle(img, (p1[0] - 1, p1[1] - 1), (p2[0] + 1, p2[1] + 1), PAPER, 1)
        cv2.rectangle(img, p1, p2, colour, th)

    def _draw(self, img, now, rejected, gate_px) -> None:
        H, W = img.shape[:2]
        s = max(1.0, W / 960)
        th = 1 if W <= 1300 else 2
        # The feed is shown at roughly 0.65x on a projector sheet, so marks are
        # sized to land at the Doctrine's 9px / 7px on screen, not in the frame.
        mark = max(9, round(W / 90))          # contact square
        dot = max(7, round(W / 120))          # track-history origin dot

        for (x1, y1, x2, y2) in rejected:
            p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
            cv2.rectangle(img, p1, p2, RULE, 1)
            self._plate(img, p1[0], p1[1] - 4, f"REJECTED  {x2 - x1:.0f} PX > {gate_px:.0f} PX",
                        INK_FAINT, RULE)

        for t in self.tracks.values():
            if now - t.last_seen > 0.4:
                continue
            x1, y1, x2, y2 = (int(v) for v in t.box)
            pad = round(5 * s)
            x1, y1, x2, y2 = x1 - pad, y1 - pad, x2 + pad, y2 + pad
            contact = t.state in (HOSTILE, ENGAGING, NEUTRALISED)
            col = OXIDE if contact else GREEN if t.state == FRIENDLY else INK

            # Track history: 1px green polyline from a hollow green origin dot.
            if len(t.trail) > 1:
                pts = np.array(t.trail, np.int32).reshape(-1, 1, 2)
                cv2.polylines(img, [pts], False, GREEN, th, cv2.LINE_AA)
                ox, oy = (int(v) for v in t.trail[0])
                cv2.circle(img, (ox, oy), dot // 2 + 1, PAPER, -1, cv2.LINE_AA)
                cv2.circle(img, (ox, oy), dot // 2, GREEN, 1, cv2.LINE_AA)

            self._keyline_rect(img, (x1, y1), (x2, y2), col, th)
            mx, my = (x1 + x2) // 2, (y1 + y2) // 2

            if t.state == ENGAGING:           # plotted lines to the frame edge, no animation
                for a, b in (((0, my), (x1, my)), ((x2, my), (W, my)),
                             ((mx, 0), (mx, y1)), ((mx, y2), (mx, H))):
                    cv2.line(img, a, b, OXIDE, 1, cv2.LINE_AA)
            if t.state == NEUTRALISED:
                cv2.line(img, (x1, y1), (x2, y2), OXIDE, th, cv2.LINE_AA)
                cv2.line(img, (x2, y1), (x1, y2), OXIDE, th, cv2.LINE_AA)

            # The mark sits on the frame's top-left corner; the callsign 10px above.
            h = mark // 2
            if contact:
                cv2.rectangle(img, (x1 - h - 1, y1 - h - 1), (x1 + h + 1, y1 + h + 1), PAPER, -1)
                cv2.rectangle(img, (x1 - h, y1 - h), (x1 + h, y1 + h), OXIDE, -1)
            elif t.state == FRIENDLY:
                cv2.circle(img, (x1, y1), h + 2, PAPER, -1, cv2.LINE_AA)
                cv2.circle(img, (x1, y1), h, GREEN, max(1, th), cv2.LINE_AA)
            else:
                cv2.rectangle(img, (x1 - h - 1, y1 - h - 1), (x1 + h + 1, y1 + h + 1), PAPER, -1)
                cv2.rectangle(img, (x1 - h, y1 - h), (x1 + h, y1 + h), INK, 1)

            word = {
                TRACKING: f"{t.conf:.2f}",
                IFF_QUERY: "IFF QUERY",
                FRIENDLY: "FRIENDLY",
                HOSTILE: f"HOSTILE  Q{t.queue}" if t.queue else "HOSTILE",
                ENGAGING: f"ENGAGING {min(1.0, (now - t.state_since) / ENGAGE_S):.0%}",
                NEUTRALISED: "NEUTRALISED",
            }[t.state]
            tag = f"{t.letter} · T{t.tid}" if t.letter else f"T{t.tid}"
            self._plate(img, x1 - h, y1 - h - round(10 * s), f"{tag}  {word}", col, col)

    def _placeholder(self, text: str):
        """No signal: an empty plot field, stated as such."""
        W, H = 1280, 720
        img = np.full((H, W, 3), PAPER_RAISED, np.uint8)
        for x in range(0, W, 26):
            img[:, x] = GRID
        for y in range(0, H, 26):
            img[y, :] = GRID
        cv2.rectangle(img, (0, 0), (W - 1, H - 1), INK, 1)
        self._plate(img, 26, H // 2 + 10, f"NO SIGNAL  —  {text.upper()[:60]}", INK, RULE)
        return img

    def _publish(self, frame) -> None:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 84])
        if ok:
            with self.frame_cv:
                self.jpeg = buf.tobytes()
                self.pub_seq += 1
                self.frame_cv.notify_all()
