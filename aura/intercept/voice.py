"""Human in the loop: speech in, an order or an answer out, speech back.

    operator speaks --ElevenLabs STT--> text --Gemini + board state--> intent
    console executes the intent (the model never fires anything itself)
    reply text --ElevenLabs TTS--> spoken back

Every stage degrades rather than fails, because venue Wi-Fi is a coin toss:
  no ElevenLabs key / no network  -> the page uses the browser's own speech
  no Gemini key / Gemini error    -> a deterministic phrase parser (rules)
  no microphone                   -> the typed command box
The typed box and the rule parser need nothing but this laptop.

Keys live OUTSIDE the repo, in ~/.config/aura/keys.env (KEY=value lines), or
in the environment:
    GEMINI_API_KEY, ELEVENLABS_API_KEY
    optional: GEMINI_MODEL, ELEVENLABS_VOICE_ID, ELEVENLABS_TTS_MODEL, ELEVENLABS_STT_MODEL
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import requests

KEYS_FILE = Path.home() / ".config" / "aura" / "keys.env"
NATO = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India",
        "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo",
        "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "X-ray", "Yankee", "Zulu"]
# Spoken forms of letters. Deliberately NOT "see", "you", "are", "why": those are
# ordinary English words, and a misheard sentence must not name a target.
WORD_TO_LETTER = {w.lower().replace("-", ""): chr(65 + i) for i, w in enumerate(NATO)}
WORD_TO_LETTER.update({"alfa": "A", "juliet": "J", "xray": "X", "bee": "B", "dee": "D",
                       "gee": "G", "jay": "J", "kay": "K", "vee": "V"})
INTENTS = ("engage", "pass", "abort", "confirm", "cancel", "query", "unknown")


def phonetic(letter: str) -> str:
    return NATO[ord(letter.upper()) - 65] if letter and letter.isalpha() else letter


def load_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    try:
        for line in KEYS_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in ("GEMINI_API_KEY", "ELEVENLABS_API_KEY", "GEMINI_MODEL", "ELEVENLABS_VOICE_ID",
              "ELEVENLABS_TTS_MODEL", "ELEVENLABS_STT_MODEL"):
        if os.environ.get(k):
            keys[k] = os.environ[k]
    return keys


SYSTEM = """You are AURA, the voice interface of a counter-drone fire-control console.
A human operator speaks orders or questions. You translate each utterance into ONE intent.
The console executes intents; you never claim to have fired anything yourself.

Contacts are drones on the board, designated by letters and spoken with the NATO
alphabet (A = alpha, B = bravo ...). Speech-to-text may mishear letters: "bravo",
"b", "bee" all mean B. Only use letters that exist on the board.

Intents:
  engage   destroy / neutralise / take out / kill / fire on the targets
  pass     let go / allow / clear to pass / mark friendly
  abort    abort / cease fire / stop the engagement in progress (targets optional)
  confirm  yes / confirm / affirmative, answering a pending confirmation
  cancel   no / negative / cancel, answering a pending confirmation
  query    a question about the board; answer it from the board JSON only
  unknown  anything else, or an order whose target is ambiguous or absent

"All hostiles" means every letter whose state is HOSTILE. If the operator names a
letter that is not on the board, use intent unknown and say so.

reply: what AURA says back, spoken aloud. At most 22 words. Declarative, log
register, military brevity. Use NATO names for letters. State units. No
exclamations, no first person, no emoji. For engage say e.g. "Engaging bravo."
For a query, answer precisely from the board; if the board cannot answer, say so."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {"type": "STRING", "enum": list(INTENTS)},
        "targets": {"type": "ARRAY", "items": {"type": "STRING"}},
        "reply": {"type": "STRING"},
    },
    "required": ["intent", "targets", "reply"],
}


class Voice:
    def __init__(self) -> None:
        self.keys = load_keys()
        self.tts_cache: dict[str, bytes] = {}
        self.lock = threading.Lock()
        self.last_error: str | None = None
        # One kept-alive session: a cold TLS handshake to each API cost ~2 s per
        # request in testing; warm, speech-to-text is ~1 s and speech ~0.4 s.
        self.http = requests.Session()

    def prewarm(self) -> None:
        """Voice the phrases AURA says most, in the background, so callouts are instant."""
        if not self.keys.get("ELEVENLABS_API_KEY"):
            return
        phrases = ["Human in the loop. Awaiting contacts.", "No engagement in progress.",
                   "Cancelled. No action taken.", "Nothing awaiting confirmation."]
        for L in "ABCDEF":
            n = phonetic(L)
            phrases += [f"Contact {n}, hostile. Awaiting orders.", f"Engaging {n.lower()}.",
                        f"{n} neutralised.", f"{n} lost.", f"{n} cleared to pass.",
                        f"Contact {n}, friendly. Passing.", f"{n} is friendly. Say confirm to engage.",
                        f"Confirmed. Engaging {n.lower()}."]

        def run():
            for p in phrases:
                if self.speak(p) is None:
                    break             # no network or no credit: stop, the page falls back
        threading.Thread(target=run, daemon=True, name="tts-prewarm").start()

    # ------------------------------------------------------------ config
    def reload(self) -> None:
        self.keys = load_keys()

    @property
    def gemini_model(self) -> str:
        return self.keys.get("GEMINI_MODEL", "gemini-2.5-flash")

    def config(self) -> dict:
        el = bool(self.keys.get("ELEVENLABS_API_KEY"))
        return {
            "stt": "elevenlabs" if el else "browser",
            "tts": "elevenlabs" if el else "browser",
            "brain": f"gemini ({self.gemini_model})" if self.keys.get("GEMINI_API_KEY") else "rules",
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------ ElevenLabs
    def transcribe(self, audio: bytes, mime: str) -> str:
        key = self.keys.get("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("no ElevenLabs key")
        ext = "webm" if "webm" in mime else "ogg" if "ogg" in mime else "wav" if "wav" in mime else "mp4"
        model = self.keys.get("ELEVENLABS_STT_MODEL", "scribe_v2")

        def post(model_id: str):
            return self.http.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": key},
                data={"model_id": model_id, "language_code": "en", "tag_audio_events": "false"},
                files={"file": (f"utterance.{ext}", audio, mime or "application/octet-stream")},
                timeout=20)
        r = post(model)
        if r.status_code in (400, 422) and model != "scribe_v1":
            r = post("scribe_v1")     # older model id, if this account lacks the newer one
        if r.status_code != 200:
            self.last_error = f"ElevenLabs STT {r.status_code}: {r.text[:160]}"
            raise RuntimeError(self.last_error)
        return (r.json().get("text") or "").strip()

    def speak(self, text: str) -> bytes | None:
        """MP3 bytes, or None when the page should fall back to browser speech."""
        key = self.keys.get("ELEVENLABS_API_KEY")
        if not key or not text:
            return None
        with self.lock:
            if text in self.tts_cache:
                return self.tts_cache[text]
        voice = self.keys.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
        try:
            r = self.http.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_64",
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={"text": text, "model_id": self.keys.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
                      "voice_settings": {"stability": 0.7, "similarity_boost": 0.7}},
                timeout=15)
        except requests.RequestException as e:
            self.last_error = f"ElevenLabs TTS: {e.__class__.__name__}"
            return None
        if r.status_code != 200:
            self.last_error = f"ElevenLabs TTS {r.status_code}: {r.text[:120]}"
            return None
        with self.lock:
            if len(self.tts_cache) > 200:
                self.tts_cache.clear()
            self.tts_cache[text] = r.content
        return r.content

    # ------------------------------------------------------------ Gemini
    def interpret(self, text: str, board: dict, pending: dict | None) -> dict:
        key = self.keys.get("GEMINI_API_KEY")
        if key:
            try:
                return self._gemini(text, board, pending, key)
            except Exception as e:  # noqa: BLE001 -- any failure falls back to rules
                self.last_error = f"Gemini: {e}"[:200]
        out = rules(text, board)
        out["brain"] = "rules"
        return out

    def _gemini(self, text: str, board: dict, pending: dict | None, key: str) -> dict:
        prompt = (f"BOARD (JSON):\n{json.dumps(board)}\n\n"
                  f"PENDING CONFIRMATION: {json.dumps(pending) if pending else 'none'}\n\n"
                  f"OPERATOR SAID: {text!r}")
        t0 = time.time()
        body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json",
                                     "responseSchema": SCHEMA, "temperature": 0.1}}
        if "2.5" in self.gemini_model:
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}   # latency over depth
        r = self.http.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=body, timeout=12)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:160]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        out = json.loads("".join(p.get("text", "") for p in parts))
        if out.get("intent") not in INTENTS:
            out["intent"] = "unknown"
        out["targets"] = [str(x).strip().upper()[:1] for x in out.get("targets") or [] if str(x).strip()]
        out["reply"] = str(out.get("reply") or "")[:240]
        out["brain"] = f"gemini {time.time() - t0:.1f} s"
        return out


# ---------------------------------------------------------------- rule parser
ENGAGE = re.compile(r"\b(destroy|engage|kill|neutrali[sz]e|take (it |them )?out|shoot|fire (on|at)|splash)\b")
PASS = re.compile(r"\b(let .{0,14}go|allow|pass|clear(ed)?|friendly|ignore|spare)\b")
ABORT = re.compile(r"\b(abort|cease ?fire|hold fire|stop|call off|belay)\b")
CONFIRM = re.compile(r"^\W*(yes|yeah|confirm(ed)?|affirmative|do it|go ahead|correct)\b")
CANCEL = re.compile(r"^\W*(no|negative|cancel|don'?t|never ?mind)\b")
QUESTION = re.compile(r"\b(how|what|which|where|status|report|many|count|closest|nearest|sitrep)\b|\?")


def letters_in(text: str, board: dict, named_only: bool = False) -> list[str]:
    """Letters named in the utterance that exist on the board, in spoken order.
    With named_only, every letter named, on the board or not."""
    have = {c["letter"] for c in board.get("contacts", [])}
    low = text.lower()
    out: list[str] = []
    for tok in re.findall(r"[a-z\-]+", low):
        tok = tok.replace("-", "")
        L = WORD_TO_LETTER.get(tok)
        # A bare single letter counts, except "a" and "i", which are words --
        # unless it is plainly a designation ("contact a", "destroy a").
        if L is None and len(tok) == 1 and (tok not in ("a", "i") or
                                            re.search(rf"\b(contact|target|drone|destroy|engage|kill) {tok}\b", low)):
            L = tok.upper()
        if L and (named_only or L in have) and L not in out:
            out.append(L)
    if re.search(r"\ball\b", low):
        for c in board.get("contacts", []):
            if c["state"] == "HOSTILE" and c["letter"] not in out:
                out.append(c["letter"])
    return out


def rules(text: str, board: dict) -> dict:
    t = text.lower().strip()
    tg = letters_in(t, board)
    if CONFIRM.search(t):
        return {"intent": "confirm", "targets": tg, "reply": "Confirmed."}
    if CANCEL.search(t):
        return {"intent": "cancel", "targets": [], "reply": "Cancelled."}
    if ABORT.search(t):
        return {"intent": "abort", "targets": tg, "reply": "Aborting engagement."}
    named = [L for L in letters_in(t, board, named_only=True) if L not in tg]
    if (ENGAGE.search(t) or PASS.search(t)) and not tg and named:
        return {"intent": "engage" if ENGAGE.search(t) else "pass", "targets": named,
                "reply": f"No contact {', '.join(phonetic(x).lower() for x in named)} on the board."}
    if ENGAGE.search(t):
        if not tg:
            return {"intent": "unknown", "targets": [], "reply": "Say which contact to engage."}
        return {"intent": "engage", "targets": tg,
                "reply": "Engaging " + ", ".join(phonetic(x).lower() for x in tg) + "."}
    if PASS.search(t):
        if not tg:
            return {"intent": "unknown", "targets": [], "reply": "Say which contact to clear."}
        return {"intent": "pass", "targets": tg, "reply": ", ".join(phonetic(x) for x in tg) + " cleared to pass."}
    if QUESTION.search(t) or not tg:
        return {"intent": "query", "targets": tg, "reply": sitrep(board)}
    return {"intent": "unknown", "targets": tg,
            "reply": "Order not understood. Say destroy, or let go, and a contact."}


def sitrep(board: dict) -> str:
    cs = board.get("contacts", [])
    if not cs:
        return "No contacts on the board."
    parts = [f"{phonetic(c['letter'])} {c['state'].lower().replace('_', ' ')}" for c in cs[:5]]
    return f"{len(cs)} contact{'s' if len(cs) != 1 else ''}. " + ", ".join(parts) + "."
