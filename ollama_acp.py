#!/usr/bin/python3
"""ollama_acp — an ACP agent over a local Ollama. The sovereign lane.

WHY THIS EXISTS
    Every other lane in Corral Light needs a vendor: a login, a key, a WAN.
    This one needs a process on localhost. When the connection is down, the
    key vault is locked, or a provider is having a bad day, this is the lane
    that still answers — which is the whole reason a "light" build is worth
    running on a machine rather than just opening a browser tab.

CHAT ONLY, AND IT SAYS SO
    It exposes NO tools: no file reads, no writes, no shell. So it never sends
    `session/request_permission`, and a pane on this lane has an empty rail
    *because there is nothing to approve* — not because the rail broke. That
    distinction is invisible from the outside, so the lane's label and its
    `needs` line in sessions.AGENTS both state it, and serverInfo repeats it
    on the wire. A silent no-tools lane sitting next to four tool-bearing ones
    is an operator reading a green rail that means nothing.

    Adding tools here would mean adding a permission gate here, and a gate is
    only worth what its weakest path is worth. If this lane ever grows tools,
    the gate goes in first, with a filesystem-level test proving it fails
    CLOSED — that ordering is not negotiable.

BOUNDS (P8)
    History per session is capped by turns AND characters, oldest dropped
    first; the model list is capped; every HTTP call carries a timeout; the
    response body streams rather than accumulating unbounded.

Speaks JSON-RPC 2.0 over stdio, matching what acp.AcpClient sends:
    initialize · session/new · session/load · session/prompt ·
    session/set_config_option · session/cancel
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
CONNECT_TIMEOUT = 5           # reaching localhost is instant or it is not there
MAX_MODELS = 40               # the picker is a menu, not an inventory
MAX_TURNS = 40                # per session, oldest dropped first
MAX_HISTORY_CHARS = 400_000   # and a byte bound, because 40 long turns is not
                              # a bound on anything that matters
PROTOCOL_VERSION = 1

DATA_CLASS_NOTE = ("local — the prompt never leaves this machine")


def _get(path, timeout=CONNECT_TIMEOUT):
    with urllib.request.urlopen(f"{URL}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def list_models():
    """Model names the local Ollama actually has pulled. [] on any failure.

    Never a hardcoded list: a picker offering a model this node does not have
    is a control that fails on use, and the whole point of the sovereign lane
    is that what it offers is what is on the disk in front of you.
    """
    try:
        tags = _get("/api/tags").get("models") or []
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []
    names = [m.get("name") for m in tags if m.get("name")]
    return sorted(names)[:MAX_MODELS]


def unavailable_reason():
    """None if this lane can serve, else the sentence to show in the picker.

    Checked at pick time, not at import: Ollama is a service that starts and
    stops, and a lane that was available when the hub booted is not evidence
    about now. Distinguishes "not running" from "running with nothing pulled",
    because the fix is different and the operator is the one who has to make it.
    """
    try:
        tags = _get("/api/tags").get("models") or []
    except (urllib.error.URLError, OSError, TimeoutError):
        return f"Ollama is not answering at {URL} — start it, or set CORRAL_OLLAMA_URL"
    except ValueError:
        return f"{URL} answered, but not with an Ollama model list"
    if not tags:
        return "Ollama is running but has no models pulled — `ollama pull <model>`"
    return None


class Server:
    """One stdio ACP conversation host. One process per pane, so a session map
    of one is the normal case; it is a map because session/load may re-attach a
    fresh process to an id an earlier one minted."""

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
                        # Stated, not implied. The client renders these, and a
                        # lane claiming a capability it does not have is the
                        # same lie as a picker listing an uninstalled binary.
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
                # Re-attach. The transcript lives with the CLIENT (Corral holds
                # events.jsonl and replays it), so there is nothing to hand
                # back — but the model context is genuinely gone with the old
                # process, and pretending otherwise would have the pane look
                # continuous while the model had forgotten everything. Say it.
                sid = params.get("sessionId") or uuid.uuid4().hex
                self.sessions.setdefault(sid, {"model": self.model, "history": []})
                self._update(sid, {"sessionUpdate": "agent_message_chunk",
                                   "content": {"type": "text", "text":
                                               "_(resumed — this local lane keeps no "
                                               "context across a restart, so the model "
                                               "starts fresh from here)_\n\n"}})
                return self._result(rid, {"configOptions": self._config_options()})
            if method == "session/set_config_option":
                return self._set_config(rid, params)
            if method == "session/prompt":
                # OFF the reader thread, deliberately. A prompt streams for as
                # long as the model takes, and running it here would mean
                # stdin is not being read for that whole time — so
                # `session/cancel` could not arrive until the turn it is
                # cancelling had already finished. Same lesson acp.py learned
                # on the client side with permission waits.
                threading.Thread(target=self._prompt_guarded, args=(rid, params),
                                 daemon=True).start()
                return
            if method == "session/cancel":
                self._cancel.add(params.get("sessionId"))
                return
            self._error(rid, -32601, f"unsupported: {method}")
        except Exception as e:                       # noqa: BLE001
            # A bug in here must arrive as an error the pane can render, never
            # as a dead process the operator has to guess about.
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
            # Refuse the SESSION, not the first prompt. A pane that opens
            # cleanly and then dies on the first message reads as a Corral
            # fault; refusing here names the actual dependency.
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
        """Bound the context BOTH ways, oldest first (P8).

        Turn count alone is not a bound — one pasted file blows the model's
        window with three messages in the list. Characters alone would drop a
        long-but-recent turn while keeping forty stale one-liners.
        """
        del history[:-MAX_TURNS]
        total = sum(len(m.get("content") or "") for m in history)
        while len(history) > 1 and total > MAX_HISTORY_CHARS:
            total -= len(history.pop(0).get("content") or "")

    def _prompt_guarded(self, rid, params):
        """Every exit from a prompt thread MUST answer `rid`.

        The client waits on a prompt with no deadline (acp.py, deliberately),
        so an exception that killed this thread silently would leave the pane
        `busy` forever with nothing to observe — the exact failure a clock was
        removed to avoid, arriving by the other door. `_prompt` answers on its
        own paths; this catches everything it did not anticipate.
        """
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
            # No read timeout on the stream: a local model thinking for four
            # minutes on the first token of a long answer is working, not
            # wedged, and no clock here can tell those apart. The connection
            # attempt IS bounded (above, in the availability check) and the
            # turn ends when the process ends — the same rule acp.py settled on
            # after two clocks killed healthy turns.
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
            # The half-answer stays in the transcript (the client already
            # rendered it) but must NOT go into history as if it were a
            # complete assistant turn — the next prompt would build on a
            # truncated statement without anything saying so.
            s["history"].pop()
            return self._error(rid, -32000, f"ollama: {e}"[:300])
        cancelled = sid in self._cancel
        self._cancel.discard(sid)
        if acc and not cancelled:
            s["history"].append({"role": "assistant", "content": "".join(acc)})
            self._trim(s["history"])
        elif acc:
            # Cancelled mid-answer: keep what was said, marked, so the model
            # sees the same truncated turn the operator is looking at.
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
