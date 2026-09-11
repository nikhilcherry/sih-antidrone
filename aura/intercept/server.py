"""AURA Intercept: stdlib HTTP around the engine. No web framework.

The Drone_Ml venv (the one with CUDA torch) has no FastAPI, and the night
before a demo is not when to install one, so this is http.server:

  GET  /                 the dashboard
  GET  /stream.mjpg      annotated video (boxes drawn server-side, so they can
                         never drift from the frame they belong to)
  GET  /events           Server-Sent Events, board state at ~8 Hz
  GET  /api/sources      cameras + recorded clips on disk
  GET  /report           after-action report (print it to PDF)
  GET  /api/report       the same, as JSON
  GET  /evidence/<s>/<f> evidence frames captured at each decision
  POST /api/control      {"action": ..., ...}
  POST /api/upload       raw video bytes, X-Filename header
  GET  /api/voice/config which speech and brain backends are live
  POST /api/voice        push-to-talk audio -> ElevenLabs STT -> order/answer
  POST /api/command      {"text": ...}, the typed equivalent
  GET  /api/tts?text=    ElevenLabs speech; 204 means "use the browser's voice"
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from urllib.parse import parse_qs, urlparse

from .engine import SESSIONS, Engine
from .voice import Voice, phonetic

HERE = Path(__file__).parent
STATIC = HERE / "static"
FONTS = STATIC / "fonts"
UPLOADS = HERE.parent.parent / "data" / "intercept_uploads"
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
MAX_UPLOAD = 1 << 30
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
         ".woff2": "font/woff2", ".svg": "image/svg+xml", ".jpg": "image/jpeg"}
SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def list_sources(pi_url: str) -> dict:
    cams = sorted(int(p.name[5:]) for p in Path("/dev").glob("video*") if p.name[5:].isdigit())
    UPLOADS.mkdir(parents=True, exist_ok=True)
    clips = sorted((p for p in UPLOADS.iterdir() if p.suffix.lower() in VIDEO_EXT),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return {"cameras": cams, "pi": pi_url,
            "clips": [{"name": p.name, "path": str(p)} for p in clips]}


class Operator:
    """Turns an interpreted utterance into console actions, and says what happened.

    The model proposes; this class disposes. Orders are checked against the
    live board, a strike on a FRIENDLY needs a spoken confirmation (fratricide
    guard), and the reply is built from what was actually executed -- the
    model's own wording is used only to answer questions.
    """

    def __init__(self, engine: Engine, voice: Voice) -> None:
        self.engine, self.voice = engine, voice
        self.pending: dict | None = None
        self.lock = threading.Lock()

    def handle(self, text: str, via: str) -> dict:
        text = text.strip()
        self.voice.reload()           # keys can be added mid-demo without a restart
        if not text:
            return {"reply": "Nothing heard.", "intent": "unknown", "targets": [], "transcript": ""}
        with self.lock:
            if self.pending and time.time() > self.pending["expires"]:
                self.pending = None
            board = self.engine.board()
            d = self.voice.interpret(text, board, self.pending)
            reply, level = self._execute(d, board)
        self.engine.note("info", None, f'Operator ({via}): "{text}"')
        self.engine.note(level, None, f"AURA: {reply}")
        audio = self.voice.speak(reply)
        return {"transcript": text, "intent": d["intent"], "targets": d.get("targets", []),
                "reply": reply, "brain": d.get("brain"),
                "audio": base64.b64encode(audio).decode() if audio else None}

    def _execute(self, d: dict, board: dict) -> tuple[str, str]:
        intent, eng = d["intent"], self.engine
        have = {c["letter"] for c in board["contacts"]}
        asked = [L for L in d.get("targets", []) if L.isalpha()]
        targets = [L for L in asked if L in have]
        missing = [L for L in asked if L not in have]
        name = lambda L: phonetic(L).lower()  # noqa: E731
        orders = ("engage", "pass", "abort", "confirm")

        if intent in orders and not eng.hitl:
            return "Human in the loop is off. Switch it on to give orders.", "warn"
        if intent in ("engage", "pass") and missing and not targets:
            return f"No contact {', '.join(name(L) for L in missing)} on the board.", "warn"

        if intent == "engage":
            said, go = [], []
            for L in targets:
                t = eng.by_letter(L)
                r = eng.order_engage(t.tid) if t else "missing"
                if r == "friendly":
                    self.pending = {"intent": "engage", "targets": [L], "expires": time.time() + 15}
                    said.append(f"{phonetic(L)} is friendly. Say confirm to engage.")
                elif r == "done":
                    said.append(f"{phonetic(L)} already neutralised.")
                elif r == "engaging":
                    said.append(f"{phonetic(L)} already engaged.")
                elif r == "queued":
                    said.append(f"{phonetic(L)} queued. Effector busy.")
                elif r == "ordered":
                    go.append(L)
            if len(go) == 1:
                said.insert(0, f"Engaging {name(go[0])}.")
            elif go:
                said.insert(0, f"Engaging {', '.join(name(L) for L in go[:-1])} and {name(go[-1])}. "
                               "One effector, nearest first.")
            if missing:
                said.append(f"No contact {', '.join(name(L) for L in missing)}.")
            return " ".join(said) or "No contact engaged.", "crit"

        if intent == "confirm":
            p, self.pending = self.pending, None
            if not p:
                return "Nothing awaiting confirmation.", "info"
            said = []
            for L in p["targets"]:
                t = eng.by_letter(L)
                if t and eng.order_engage(t.tid, force=True) in ("ordered", "queued"):
                    said.append(f"Confirmed. Engaging {name(L)}.")
            return " ".join(said) or "Contact no longer on the board.", "crit"

        if intent == "cancel":
            self.pending = None
            return "Cancelled. No action taken.", "info"

        if intent == "pass":
            said = []
            for L in targets:
                t = eng.by_letter(L)
                r = eng.order_pass(t.tid) if t else "missing"
                said.append({"passed": f"{phonetic(L)} cleared to pass.",
                             "already": f"{phonetic(L)} already friendly.",
                             "neutralised": f"{phonetic(L)} already neutralised.",
                             "engaging": f"{phonetic(L)} engagement in progress. Say abort first."}.get(r, ""))
            return " ".join(x for x in said if x) or "No contact cleared.", "ok"

        if intent == "abort":
            # Named targets: just those. Bare "abort" / "cease fire": everything.
            ids = [eng.by_letter(L).tid for L in targets if eng.by_letter(L)] or eng.ordered_ids()
            done = [tid for tid in ids if eng.abort(tid) == "aborted"]
            if not done:
                return "No engagement in progress.", "info"
            return ("Cease fire. " if len(done) > 1 else "Engagement aborted. ") + \
                f"{len(done)} target{'s' if len(done) > 1 else ''} held.", "warn"

        return d.get("reply") or "Order not understood.", "info"


def make_handler(engine: Engine, pi_url: str, op: Operator):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet: the terminal is for errors
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _file(self, path: Path) -> None:
            if not path.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

        # ------------------------------------------------------------ GET
        def do_GET(self):  # noqa: N802
            p = self.path.split("?", 1)[0]
            if p in ("/", "/index.html"):
                return self._file(STATIC / "index.html")
            if p == "/report":
                return self._file(STATIC / "report.html")
            if p == "/api/report":
                return self._json(engine.report())
            if p.startswith("/evidence/"):
                parts = p.split("/")[2:]
                if len(parts) == 2 and all(SAFE.match(x) and x not in (".", "..") for x in parts):
                    return self._file(SESSIONS / parts[0] / parts[1])
                return self._send(404, b"not found", "text/plain")
            if p.startswith("/static/"):
                return self._file(STATIC / Path(p[8:]).name)
            if p.startswith("/fonts/"):
                return self._file(FONTS / Path(p[7:]).name)
            if p == "/stream.mjpg":
                return self._mjpeg()
            if p == "/events":
                return self._sse()
            if p == "/api/sources":
                return self._json(list_sources(pi_url))
            if p == "/api/state":
                return self._json(engine.snapshot())
            if p == "/api/voice/config":
                op.voice.reload()
                return self._json(op.voice.config())
            if p == "/api/tts":
                text = (parse_qs(urlparse(self.path).query).get("text") or [""])[0][:300]
                audio = op.voice.speak(text)
                if audio is None:
                    return self._send(204, b"", "text/plain")
                return self._send(200, audio, "audio/mpeg")
            self._send(404, b"not found", "text/plain")

        def _mjpeg(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            seen = -1
            try:
                while True:
                    with engine.frame_cv:
                        engine.frame_cv.wait_for(lambda: engine.pub_seq != seen, timeout=2.0)
                        jpg, seen = engine.jpeg, engine.pub_seq
                    if jpg is None:
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        def _sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    self.wfile.write(f"data: {json.dumps(engine.snapshot())}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.12)
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        # ------------------------------------------------------------ POST
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            if self.path == "/api/upload":
                return self._upload(n)
            if self.path == "/api/voice":
                audio = self.rfile.read(n) if 0 < n < 20_000_000 else b""
                op.voice.reload()
                try:
                    text = op.voice.transcribe(audio, self.headers.get("Content-Type", ""))
                except Exception as e:  # noqa: BLE001 -- the page falls back to typing
                    return self._json({"error": str(e)[:200]}, 502)
                return self._json(op.handle(text, "voice"))
            if self.path == "/api/command":
                try:
                    text = str(json.loads(self.rfile.read(n) or b"{}").get("text", ""))[:300]
                except json.JSONDecodeError:
                    return self._json({"error": "bad json"}, 400)
                return self._json(op.handle(text, self.headers.get("X-Via", "typed")))
            if self.path != "/api/control":
                return self._json({"error": "not found"}, 404)
            try:
                msg = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)

            a = msg.get("action")
            if a == "source":
                src = str(msg.get("source", "")).strip()
                if not src:
                    return self._json({"error": "empty source"}, 400)
                engine.request_source(src)
            elif a == "mode":
                engine.set_mode(str(msg.get("mode", "AUTO")))
            elif a == "beacon":
                engine.set_beacon(bool(msg.get("on")))
            elif a == "gate":
                engine.set_gate(bool(msg.get("on")))
            elif a == "override":
                v = msg.get("verdict")
                if v not in ("friendly", "hostile"):
                    return self._json({"error": "verdict must be friendly|hostile"}, 400)
                engine.override(int(msg.get("track", -1)), v)
            elif a == "engage":
                engine.engage(int(msg.get("track", -1)))
            elif a == "reset":
                engine.reset()
            elif a == "pause":
                engine.set_paused(bool(msg.get("on")))
            elif a == "restart":
                engine.restart()
            elif a == "record":
                engine.set_recording(bool(msg.get("on")))
            elif a == "hitl":
                engine.set_hitl(bool(msg.get("on")))
                if msg.get("on"):
                    op.voice.reload()
                    op.voice.prewarm()
            else:
                return self._json({"error": f"unknown action {a!r}"}, 400)
            self._json({"ok": True})

        def _upload(self, n: int) -> None:
            if n <= 0 or n > MAX_UPLOAD:
                return self._json({"error": "empty or too large"}, 413)
            raw = self.headers.get("X-Filename") or "clip.mp4"
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(raw).name).strip("._") or "clip.mp4"
            if Path(name).suffix.lower() not in VIDEO_EXT:
                self.rfile.read(n)
                return self._json({"error": "not a video file"}, 415)
            UPLOADS.mkdir(parents=True, exist_ok=True)
            dest = UPLOADS / name
            with open(dest, "wb") as f:
                left = n
                while left:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            engine.request_source(str(dest))
            self._json({"ok": True, "path": str(dest)})

    return H


def serve(engine: Engine, host: str, port: int, pi_url: str) -> None:
    threading.Thread(target=engine.run, name="engine", daemon=True).start()
    op = Operator(engine, Voice())
    op.voice.prewarm()                # warm the TLS session and the common phrases now
    httpd = ThreadingHTTPServer((host, port), make_handler(engine, pi_url, op))
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()
        httpd.server_close()
