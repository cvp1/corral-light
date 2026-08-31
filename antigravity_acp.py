#!/usr/bin/env python3
"""Small ACP bridge for Google's Antigravity CLI review lane.

Antigravity does not yet expose native ACP.  Corral still needs an ACP peer,
so this adapter translates each ACP prompt into AGY's documented headless
``stream-json`` mode and preserves the returned conversation id for later
turns.  It deliberately runs in ``plan`` mode: this is the review/bug-bash
lane Craig already uses, and it must not gain an invisible write bypass just
because AGY cannot send interactive permission cards over ACP yet.

No credential is read or copied.  The official ``agy`` binary owns auth.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path

MAX_LINE = 16 * 1024 * 1024
SESSIONS: dict[str, dict] = {}
RUNNING: dict[str, subprocess.Popen] = {}
LOCK = threading.RLock()
WRITE_LOCK = threading.Lock()
SESSION_DIR = Path(os.environ.get(
    "CORRAL_STATE", str(Path.home() / ".local/share/corral"))) / "agy-sessions"
CATALOG = SESSION_DIR.parent / "catalog.json"


def resolve_agy() -> str | None:
    explicit = os.environ.get("CORRAL_AGY_BIN")
    candidates = ([Path(explicit).expanduser()] if explicit else []) + [
        Path.home() / ".local/bin/agy",
        Path(shutil.which("agy")) if shutil.which("agy") else Path("/__missing__"),
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def send(obj):
    with WRITE_LOCK:
        sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def result(rid, value):
    send({"jsonrpc": "2.0", "id": rid, "result": value})


def error(rid, message):
    send({"jsonrpc": "2.0", "id": rid,
          "error": {"code": -32000, "message": message}})


def _session_path(sid: str) -> Path:
    # ACP session ids are generated here as hex, but load input is still
    # untrusted wire data. Never let it choose a path.
    safe = "".join(c for c in str(sid) if c in "0123456789abcdef")
    if safe != sid or not safe:
        raise ValueError("invalid session id")
    return SESSION_DIR / f"{safe}.json"


def _save_session(sid: str, session: dict):
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.chmod(0o700)
    target = _session_path(sid)
    temp = target.with_suffix(f".tmp.{os.getpid()}")
    temp.write_text(json.dumps(session, sort_keys=True), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(target)


def _load_session(sid: str) -> dict | None:
    try:
        value = json.loads(_session_path(sid).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _models(agy: str, timeout=90) -> list[dict]:
    """Read AGY's live account catalog; empty means no model picker."""
    try:
        p = subprocess.run([agy, "models"], capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if p.returncode:
        return []
    out = []
    for line in p.stdout.splitlines():
        if "\t" not in line:
            continue
        value, name = line.split("\t", 1)
        value, name = value.strip(), name.strip()
        if value and name:
            out.append({"value": value, "name": name})
    return out[:30]


def _cached_models() -> list[dict]:
    """Use Corral's vendor-seeded picker catalog inside a pane handshake."""
    try:
        entry = ((json.loads(CATALOG.read_text(encoding="utf-8")).get("gemini")
                  or {}).get("model") or {})
        out = []
        for item in entry.get("options") or []:
            if item.get("value"):
                out.append({"value": item["value"],
                            "name": item.get("name") or item["value"]})
        return out[:30]
    except (OSError, ValueError, AttributeError):
        return []


def _config_options(session: dict, agy: str) -> list[dict]:
    # Manager.seed_catalogs owns the live vendor query off Corral's startup
    # path. Reuse it here: asking AGY again inside session/new occasionally
    # contends on its own state lock for the full timeout, turning one click
    # into a 90-second blank pane. A short live fallback covers direct adapter
    # use before Corral has seeded anything.
    models = _cached_models() or _models(agy, timeout=15)
    options = []
    if models:
        current = session.get("model") or models[0]["value"]
        session["model"] = current
        options.append({"id": "model", "name": "Model",
                        "currentValue": current, "options": models})
    # AGY's catalog IDs encode effort (`gemini-3.7-flash-medium`). Offering a
    # second independent effort picker let Corral submit contradictory flags,
    # e.g. model=...-medium plus effort=high, which AGY correctly refuses.
    # The model picker is the one authority for both dimensions.
    return options


def _text_prompt(params) -> str:
    return "".join(part.get("text", "") for part in params.get("prompt", [])
                   if part.get("type") == "text")


def _emit_text(sid: str, text: str):
    if not text:
        return
    send({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk",
                                      "content": {"type": "text", "text": text}}}})


def _prompt_argv(agy: str, prompt: str, session: dict) -> list[str]:
    model = session.get("model") or ""
    argv = [agy, "-p", prompt, "--mode", "plan",
            # TEMPORARY, explicitly authorized by Craig on 2026-08-31 so the
            # AGY review lane can be exercised before it has an ACP permission
            # bridge. Plan mode remains enabled, but AGY tool confirmations are
            # auto-approved. Remove once approvals can reach Corral's rail.
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            # AGY does not infer its active workspace from the process cwd.
            # Without this it silently falls back to ~/.gemini/.../scratch.
            "--add-dir", session["cwd"]]
    if model:
        argv += ["--model", model]
    # Some non-Gemini catalog entries do not encode effort in their id. Only
    # those receive the separate flag; encoded model ids must stand alone.
    if not re.search(r"-(?:low|medium|high)$", model):
        argv += ["--effort", session.get("effort", "high")]
    if session.get("conversation_id"):
        argv += ["--conversation", session["conversation_id"]]
    return argv


def _run_prompt(rid, params, agy: str):
    sid = params.get("sessionId")
    with LOCK:
        session = SESSIONS.get(sid)
    if not session:
        return error(rid, "unknown session")
    argv = _prompt_argv(agy, _text_prompt(params), session)
    try:
        proc = subprocess.Popen(argv, cwd=session["cwd"], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
    except OSError as exc:
        return error(rid, f"could not start AGY: {exc}")
    with LOCK:
        RUNNING[sid] = proc
    emitted = False
    last_error = ""
    try:
        for raw in proc.stdout:
            if len(raw) > MAX_LINE:
                proc.kill()
                return error(rid, f"AGY output line exceeded {MAX_LINE} bytes")
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            cid = event.get("conversation_id") or \
                (event.get("init") or {}).get("conversation_id") or \
                (event.get("result") or {}).get("conversation_id")
            if cid:
                session["conversation_id"] = cid
                _save_session(sid, session)
            if event.get("event") == "step_update":
                step = event.get("step_update") or {}
                text = step.get("text_delta") or ""
                if text:
                    emitted = True
                    _emit_text(sid, text)
            elif event.get("event") == "result":
                body = event.get("result") or {}
                if not emitted and body.get("response"):
                    emitted = True
                    _emit_text(sid, body["response"])
                if body.get("status") not in (None, "SUCCESS"):
                    last_error = body.get("error") or body.get("status", "AGY failed")
        stderr = proc.stderr.read()[-2000:]
        rc = proc.wait()
        if rc:
            return error(rid, last_error or stderr.strip() or f"AGY exited rc={rc}")
        if not emitted:
            # AGY print mode can soft-deny a requested tool permission, emit a
            # SUCCESS result with an empty response, and exit zero. Treating
            # that as end_turn makes a refused request look like silence.
            return error(rid, last_error or
                         "AGY completed without a response. In the Corral "
                         "review lane this usually means Antigravity requested "
                         "a tool permission that its noninteractive mode "
                         "soft-denied; no tool ran.")
        result(rid, {"stopReason": "end_turn"})
    finally:
        with LOCK:
            RUNNING.pop(sid, None)


def _handle(msg, agy: str):
    rid, method = msg.get("id"), msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return result(rid, {"protocolVersion": 1,
            "agentInfo": {"name": "ai-os-antigravity", "version": "1"},
            "agentCapabilities": {"sessionCapabilities": {"load": True}},
            "authMethods": []})
    if method == "session/new":
        sid = uuid.uuid4().hex
        session = {"cwd": str(params.get("cwd") or os.getcwd()), "effort": "high"}
        with LOCK:
            SESSIONS[sid] = session
        _save_session(sid, session)
        return result(rid, {"sessionId": sid,
                            "configOptions": _config_options(session, agy)})
    if method == "session/load":
        sid = params.get("sessionId")
        saved = _load_session(sid)
        if not saved:
            return error(rid, "Antigravity conversation mapping is missing; "
                              "refusing to claim this session was resumed")
        with LOCK:
            SESSIONS[sid] = saved
            SESSIONS[sid]["cwd"] = str(params.get("cwd") or saved.get("cwd")
                                       or os.getcwd())
        return result(rid, {"sessionId": sid,
                            "configOptions": _config_options(SESSIONS[sid], agy)})
    if method == "session/set_config_option":
        session = SESSIONS.get(params.get("sessionId"))
        if not session:
            return error(rid, "unknown session")
        cid, value = params.get("configId"), params.get("value")
        if cid not in ("model", "effort"):
            return error(rid, f"unsupported config option: {cid}")
        session[cid] = value
        _save_session(params["sessionId"], session)
        return result(rid, {"configOptions": _config_options(session, agy)})
    if method == "session/prompt":
        threading.Thread(target=_run_prompt, args=(rid, params, agy), daemon=True).start()
        return None
    if method == "session/cancel":
        with LOCK:
            proc = RUNNING.get(params.get("sessionId"))
        if proc:
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except OSError:
                pass
        return None
    if rid is not None:
        return error(rid, f"unsupported method: {method}")


def main() -> int:
    agy = resolve_agy()
    if not agy:
        print("Antigravity unavailable: official agy binary not installed", file=sys.stderr)
        return 127
    for line in sys.stdin:
        try:
            _handle(json.loads(line), agy)
        except Exception as exc:  # keep the adapter alive and fail this request loud
            try:
                error(json.loads(line).get("id"),
                      f"Antigravity ACP error: {type(exc).__name__}: {exc}")
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
