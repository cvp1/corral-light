#!/usr/bin/python3
"""ollama_acp — a chat-only ACP agent over a local Ollama, speaking JSON-RPC
2.0 on stdio. Exposes no tools, so it never requests permission; any future
tool must add a fail-closed permission gate first.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

URL = os.environ.get("CORRAL_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
CONNECT_TIMEOUT = 5
MAX_MODELS = 40
MAX_TURNS = 40                # per session, oldest dropped first
MAX_HISTORY_CHARS = 400_000
PROTOCOL_VERSION = 1

DATA_CLASS_NOTE = ("local — the prompt never leaves this machine")


def _get(path, timeout=CONNECT_TIMEOUT):
    with urllib.request.urlopen(f"{URL}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def list_models():
    """Model names the local Ollama actually has pulled. [] on any failure."""
    try:
        tags = _get("/api/tags").get("models") or []
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []
    names = [m.get("name") for m in tags if m.get("name")]
    return sorted(names)[:MAX_MODELS]


def unavailable_reason(timeout=CONNECT_TIMEOUT):
    """None if this lane can serve, else the sentence to show in the picker."""
    try:
        tags = _get("/api/tags", timeout=timeout).get("models") or []
    except (urllib.error.URLError, OSError, TimeoutError):
        return f"Ollama is not answering at {URL} — start it, or set CORRAL_OLLAMA_URL"
    except ValueError:
        return f"{URL} answered, but not with an Ollama model list"
    if not tags:
        return "Ollama is running but has no models pulled — `ollama pull <model>`"
    return None


# Sent on every session/load: model context does not survive a restart.
RESUME_NOTICE = ("resumed — this local lane keeps no context across a "
                 "restart, so the model starts fresh from here")

class Server:
    """One stdio ACP conversation host (one process per pane)."""

    def __init__(self, out=sys.stdout):
        self.out = out
        self.sessions = {}            # id -> {"model": str, "history": [msg]}
        self.model = os.environ.get("CORRAL_OLLAMA_MODEL") or ""
        self._wlock = threading.Lock()
        self._cancel = set()

    # ── wire ─────────────────────────────────────────────────────────────
    def _send(self, obj):
        with self._wlock:
            self.out.write(json.dumps(obj) + "\n")
            self.out.flush()

    def _result(self, rid, result):
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    def _error(self, rid, code, message):
        self._send({"jsonrpc": "2.0", "id": rid,
                    "error": {"code": code, "message": message}})

    def _update(self, session_id, update):
        self._send({"jsonrpc": "2.0", "method": "session/update",
                    "params": {"sessionId": session_id, "update": update}})

    # ── protocol ─────────────────────────────────────────────────────────
    def handle(self, msg):
        method, rid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if method is None:
            return                                   # a response to us; we ask nothing
        try:
            if method == "initialize":
                return self._result(rid, {
                    "protocolVersion": PROTOCOL_VERSION,
                    "agentCapabilities": {
                        "loadSession": True,
                        "promptCapabilities": {"image": False, "audio": False,
                                               "embeddedContext": False},
                    },
                    "agentInfo": {"name": "ollama-acp",
                                  "version": "1",
                                  "description": "local Ollama, chat only — no "
                                                 "tools, so no permission requests",
                                  "dataClass": DATA_CLASS_NOTE},
                })
            if method == "session/new":
                return self._new_session(rid)
            if method == "session/load":
                # Re-attach. The client holds the transcript; model context is
                # gone with the old process, so say so.
                sid = params.get("sessionId") or uuid.uuid4().hex
                self.sessions.setdefault(sid, {"model": self.model, "history": []})
                self._update(sid, {"sessionUpdate": "agent_message_chunk",
                                   "content": {"type": "text", "text":
                                               "_(" + RESUME_NOTICE + ")_\n\n"}})
                # Also in the result: clients may suppress chunks during load
                # as replayed history. `_meta` is ACP's extension slot.
                return self._result(rid, {"configOptions": self._config_options(),
                                          "_meta": {"corral/notice": RESUME_NOTICE,
                                                    "corral/contextLost": True}})
            if method == "session/set_config_option":
                return self._set_config(rid, params)
            if method == "session/prompt":
                # Off the reader thread, so session/cancel can arrive mid-turn.
                threading.Thread(target=self._prompt_guarded, args=(rid, params),
                                 daemon=True).start()
                return
            if method == "session/cancel":
                self._cancel.add(params.get("sessionId"))
                return
            self._error(rid, -32601, f"unsupported: {method}")
        except Exception as e:                       # noqa: BLE001
            if rid is not None:
                self._error(rid, -32603, f"{type(e).__name__}: {e}"[:300])

    def _config_options(self):
        models = list_models()
        if not self.model and models:
            self.model = models[0]
        return [{
            "id": "model",
            "name": "Model",
            "currentValue": self.model,
            "options": [{"value": m, "name": m, "description": ""}
                        for m in models],
        }]

    def _new_session(self, rid):
        reason = unavailable_reason()
        if reason:
            # Refuse the session, not the first prompt, naming the dependency.
            return self._error(rid, -32000, reason)
        sid = uuid.uuid4().hex
        opts = self._config_options()
        self.sessions[sid] = {"model": self.model, "history": []}
        self._result(rid, {"sessionId": sid, "configOptions": opts})

    def _set_config(self, rid, params):
        cid, value = params.get("configId"), params.get("value")
        if cid != "model":
            return self._error(rid, -32602, f"{cid!r} is not settable on this lane")
        if value not in list_models():
            return self._error(rid, -32602,
                               f"{value!r} is not pulled on this Ollama")
        self.model = value
        s = self.sessions.get(params.get("sessionId"))
        if s:
            s["model"] = value
        self._result(rid, {"configOptions": self._config_options()})

    @staticmethod
    def _trim(history):
        """Bound the context by turns and characters, oldest first."""
        del history[:-MAX_TURNS]
        total = sum(len(m.get("content") or "") for m in history)
        while len(history) > 1 and total > MAX_HISTORY_CHARS:
            total -= len(history.pop(0).get("content") or "")

    def _prompt_guarded(self, rid, params):
        """Run _prompt, answering `rid` even on an unexpected exception
        (the client waits with no deadline)."""
        try:
            self._prompt(rid, params)
        except Exception as e:                        # noqa: BLE001
            self._error(rid, -32603, f"{type(e).__name__}: {e}"[:300])

    def _prompt(self, rid, params):
        sid = params.get("sessionId")
        s = self.sessions.get(sid)
        if s is None:
            return self._error(rid, -32602, f"no session {sid}")
        text = "".join(b.get("text") or "" for b in (params.get("prompt") or [])
                       if b.get("type") == "text")
        self._cancel.discard(sid)
        s["history"].append({"role": "user", "content": text})
        self._trim(s["history"])
        body = json.dumps({"model": s["model"] or self.model,
                           "messages": s["history"], "stream": True}).encode()
        req = urllib.request.Request(f"{URL}/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        acc = []
        try:
            # No read timeout: a slow first token is indistinguishable from a
            # wedge. The connection is bounded by the availability check.
            with urllib.request.urlopen(req) as r:
                for line in r:
                    if sid in self._cancel:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    if chunk.get("error"):
                        raise RuntimeError(str(chunk["error"])[:300])
                    piece = (chunk.get("message") or {}).get("content") or ""
                    if piece:
                        acc.append(piece)
                        self._update(sid, {"sessionUpdate": "agent_message_chunk",
                                           "content": {"type": "text", "text": piece}})
                    if chunk.get("done"):
                        break
        except (urllib.error.URLError, OSError, RuntimeError, TimeoutError) as e:
            # Drop the user turn; a half-answer must not enter history as complete.
            s["history"].pop()
            return self._error(rid, -32000, f"ollama: {e}"[:300])
        cancelled = sid in self._cancel
        self._cancel.discard(sid)
        if acc and not cancelled:
            s["history"].append({"role": "assistant", "content": "".join(acc)})
            self._trim(s["history"])
        elif acc:
            # Cancelled mid-answer: keep what was said, marked as cancelled.
            s["history"].append({"role": "assistant",
                                 "content": "".join(acc) + "\n[cancelled]"})
            self._trim(s["history"])
        self._result(rid, {"stopReason": "cancelled" if cancelled else "end_turn"})


def main():
    server = Server()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        server.handle(msg)


if __name__ == "__main__":
    main()
