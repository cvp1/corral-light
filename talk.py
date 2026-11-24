"""Talk: a chat-or-talk button that reaches a Home Assistant Assist agent.

The wall's 🎙 button opens a small drawer. Typed text, or a held-to-talk clip,
goes to the hub; the hub asks Home Assistant and returns the reply (and, when
asked, the reply spoken by HA's TTS). Nothing here picks the agent from the
request: the agent, the STT and TTS engines and the HA address come only from
the operator's config, so a page can never redirect a turn to a different
(say, cloud) agent.

Config: ~/.config/corral-light/talk.json (absent = feature off, button hidden)
  {
    "url":      "http://homeassistant.local:8123",
    "agent_id": "conversation.my_local_agent",
    "stt":      "stt.faster_whisper",          optional: no talk, chat only
    "tts":      "tts.piper",                   optional: replies stay text
    "language": "en",
    "title":    "Sasha (local)",
    "fetch":    ["/path/to/secret", "fetch", "secret://ha-token", "--inject", "bearer"]
  }

The HA token never enters this process. `fetch` is a credential-broker argv
that performs one authed HTTP call and prints the response body; this module
appends --url/--method/--data-file/--content-type/--header. A broker that
cannot take --header can still serve chat and TTS; STT needs it.
"""
import base64
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

CONFIG = Path(os.environ.get("CORRAL_LIGHT_CONFIG_DIR",
                             Path.home() / ".config/corral-light")) / "talk.json"

TEXT_MAX = 2000                 # characters in one typed or heard turn
PCM_MAX = 16000 * 2 * 15        # 15 s of 16 kHz mono int16: under MAX_BODY as base64
AUDIO_OUT_MAX = 4 << 20         # a spoken reply we will relay
CALL_TIMEOUT = 60               # one HA call, broker included
ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]{1,64}$")
CONV_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class TalkError(Exception):
    """A turn that failed; the message is safe to show the operator."""


def load(path=None):
    """The validated config, or None when the feature is off. A present but
    broken file raises, so a typo is loud rather than a silently missing button."""
    p = Path(path or CONFIG)
    if not p.exists():
        return None
    try:
        c = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise TalkError(f"talk.json unreadable: {e}") from None
    if not isinstance(c, dict):
        raise TalkError("talk.json must be an object")
    u = urlparse(str(c.get("url") or ""))
    if u.scheme not in ("http", "https") or not u.netloc or u.path not in ("", "/"):
        raise TalkError("talk.json: url must be http(s)://host[:port]")
    if not ENTITY_RE.match(str(c.get("agent_id") or "")):
        raise TalkError("talk.json: agent_id must be an entity id like conversation.x")
    for k in ("stt", "tts"):
        if c.get(k) is not None and not ENTITY_RE.match(str(c[k])):
            raise TalkError(f"talk.json: {k} must be an entity id")
    lang = str(c.get("language") or "en")
    if not LANG_RE.match(lang):
        raise TalkError("talk.json: language must look like en or en-US")
    f = c.get("fetch")
    if not (isinstance(f, list) and f and all(isinstance(x, str) and x for x in f)):
        raise TalkError("talk.json: fetch must be a non-empty argv list")
    return {"url": f"{u.scheme}://{u.netloc}", "agent_id": c["agent_id"],
            "stt": c.get("stt"), "tts": c.get("tts"), "language": lang,
            "title": str(c.get("title") or "Talk")[:40],
            "fetch": [os.path.expanduser(x) for x in f]}


def public(cfg):
    """What the browser may know: enough to draw the drawer, nothing to aim it."""
    if not cfg:
        return {"enabled": False}
    return {"enabled": True, "title": cfg["title"], "talk": bool(cfg["stt"]),
            "speak": bool(cfg["tts"]), "maxSeconds": PCM_MAX // 32000}


def _call(cfg, method, path, body=b"", content_type="application/json", headers=()):
    """One authed HA call through the broker; the response body as bytes."""
    argv = list(cfg["fetch"]) + ["--url", cfg["url"] + path, "--method", method]
    with tempfile.TemporaryDirectory(prefix="corral-talk-") as d:
        if method != "GET":
            data = Path(d) / "body"
            fd = os.open(data, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            argv += ["--data-file", str(data), "--content-type", content_type]
        for h in headers:
            argv += ["--header", h]
        try:
            r = subprocess.run(argv, capture_output=True, timeout=CALL_TIMEOUT,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise TalkError("Home Assistant did not answer in time") from None
        except OSError as e:
            raise TalkError(f"credential broker would not start: {e.strerror}") from None
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise TalkError((msg[-1] if msg else f"broker exited {r.returncode}")[:300])
    return r.stdout


def _json_call(cfg, path, obj):
    out = _call(cfg, "POST", path, json.dumps(obj).encode())
    try:
        return json.loads(out)
    except ValueError:
        raise TalkError("Home Assistant sent something that is not JSON") from None


def transcribe(cfg, pcm):
    """16 kHz mono int16 PCM → text, via HA's STT REST endpoint."""
    if not cfg.get("stt"):
        raise TalkError("talking is off: no stt engine in talk.json")
    if not pcm or len(pcm) > PCM_MAX or len(pcm) % 2:
        raise TalkError(f"a clip must be 16-bit audio, at most {PCM_MAX // 32000} s")
    spec = (f"format=wav; codec=pcm; sample_rate=16000; bit_rate=16; channel=1; "
            f"language={cfg['language']}")
    out = _call(cfg, "POST", f"/api/stt/{cfg['stt']}", _wav(pcm),
                content_type="audio/wav", headers=(f"X-Speech-Content: {spec}",))
    try:
        d = json.loads(out)
    except ValueError:
        raise TalkError("speech-to-text sent something that is not JSON") from None
    if d.get("result") != "success":
        raise TalkError("didn't catch that: speech-to-text heard nothing")
    return str(d.get("text") or "").strip()[:TEXT_MAX]


def ask(cfg, text, conversation_id=None):
    """One conversation turn → (reply text, conversation id)."""
    text = (text or "").strip()
    if not text:
        raise TalkError("nothing to say")
    if len(text) > TEXT_MAX:
        raise TalkError(f"a turn may be at most {TEXT_MAX} characters")
    body = {"text": text, "language": cfg["language"], "agent_id": cfg["agent_id"]}
    if conversation_id:
        if not CONV_RE.match(str(conversation_id)):
            raise TalkError("bad conversation id")
        body["conversation_id"] = conversation_id
    d = _json_call(cfg, "/api/conversation/process", body)
    resp = d.get("response") or {}
    speech = ((resp.get("speech") or {}).get("plain") or {}).get("speech") or ""
    if resp.get("response_type") == "error" and not speech:
        speech = "The agent returned an error."
    return str(speech)[:8000], d.get("conversation_id")


def speak(cfg, text):
    """Reply text → (mime, audio bytes) from HA's TTS, or None when TTS is off."""
    if not cfg.get("tts") or not text:
        return None
    d = _json_call(cfg, "/api/tts_get_url",
                   {"engine_id": cfg["tts"], "message": text[:TEXT_MAX],
                    "language": cfg["language"]})
    path = urlparse(str(d.get("path") or d.get("url") or "")).path
    if not path.startswith("/api/tts_proxy/"):
        raise TalkError("text-to-speech gave no audio link")
    # The proxy link is HA's own unauthenticated, single-file URL; no token.
    req = urllib.request.Request(cfg["url"] + path)
    with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as r:
        mime = r.headers.get_content_type() or "audio/mpeg"
        audio = r.read(AUDIO_OUT_MAX + 1)
    if len(audio) > AUDIO_OUT_MAX:
        raise TalkError("the spoken reply was too long to relay")
    if not mime.startswith("audio/"):
        raise TalkError("text-to-speech sent something that is not audio")
    return mime, audio


def turn(cfg, b):
    """POST /api/talk body → the answer the drawer draws.
    {text | audio(base64 PCM)}, conversationId?, speak? → {heard, reply, conversationId, audio?}"""
    t0 = time.monotonic()
    heard = None
    if b.get("audio"):
        try:
            pcm = base64.b64decode(str(b["audio"]), validate=True)
        except ValueError:
            raise TalkError("audio must be base64") from None
        heard = transcribe(cfg, pcm)
        if not heard:
            raise TalkError("didn't catch that: try again a little closer")
        text = heard
    else:
        text = str(b.get("text") or "")
    reply, conv = ask(cfg, text, b.get("conversationId") or None)
    out = {"heard": heard, "reply": reply, "conversationId": conv}
    if b.get("speak"):
        try:
            got = speak(cfg, reply)
        except (TalkError, OSError) as e:
            out["speakError"] = str(e)[:200]
        else:
            if got:
                out["audio"] = {"mime": got[0], "b64": base64.b64encode(got[1]).decode()}
    out["ms"] = int((time.monotonic() - t0) * 1000)
    return out


def _wav(pcm, rate=16000):
    """A WAV container around mono int16 PCM."""
    import struct
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
