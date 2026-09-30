#!/usr/bin/env python3
"""A tiny, real ACP agent for Corral Light's tests. Stdlib only.

Speaks just enough of the protocol over stdio for the lifecycle tests to use
a REAL process (spawned, killed, resumed) instead of a hand-built stub:

    initialize, session/new, session/load (replays one chunk, which the
    hub must suppress; refused when FAKE_ACP_NO_LOAD is set), session/prompt,
    session/cancel, session/list

Prompt verbs (the text of the prompt):
    remember <word>   -> stores <word> in the session file and says "ok"
    what word ...     -> answers with the remembered word (context continuity
                         across a process restart: the file is the "memory")
    sleep <s>         -> streams a chunk, then sleeps; session/cancel ends it
                         (also a line that is exactly `sleep <s>`, so a peer
                         message inside the hub's envelope can hold a turn)
    die               -> exits the process mid-turn (rc 3)
    perm, or any text with "touch " in it
                      -> asks session/request_permission and reports the
                         answer (it never runs anything, whatever the answer)
    anything else     -> "echo: <text>"

State lives in $FAKE_ACP_DIR (a test's temp dir): one JSON file per session,
plus `pid-<pid>` marker files so a test can find and kill the process.
"""
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

DIR = Path(os.environ.get("FAKE_ACP_DIR") or "/tmp/fake-acp")
DIR.mkdir(parents=True, exist_ok=True)
(DIR / f"pid-{os.getpid()}").write_text(str(os.getpgid(0)))
_wlock = threading.Lock()
_cancel = threading.Event()
_answers = {}
_next = [10_000]


def send(obj):
    with _wlock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def chunk(sid, text):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk",
                                     "content": {"type": "text", "text": text}}}})


def mem(sid):
    try:
        return json.loads((DIR / f"session-{sid}.json").read_text())
    except (OSError, ValueError):
        return {}


def save(sid, m):
    (DIR / f"session-{sid}.json").write_text(json.dumps(m))


def _sleep_line(text):
    """A line that is exactly `sleep <s>` anywhere in the prompt: a peer
    message arrives wrapped in the hub's envelope, so the verb is on its own
    line, not first. -> the seconds as a string, or None."""
    for line in text.splitlines():
        w = line.split()
        if len(w) == 2 and w[0] == "sleep":
            try:
                float(w[1])
                return w[1]
            except ValueError:
                pass
    return None


def prompt(rid, params):
    sid = params.get("sessionId")
    text = "".join(p.get("text", "") for p in params.get("prompt") or [])
    m = mem(sid)
    words = text.split()
    low = text.lower()
    if "remember" in low and not low.startswith("what word") and len(words) > 1:
        # "remember <word>" or "...remember this word...: <word>. Reply..."
        after = text.split(":", 1)[1].split() if ":" in text else words[-1:]
        m["word"] = after[0].strip(".") if after else words[-1].strip(".")
        save(sid, m)
        chunk(sid, "ok")
    elif text.lower().startswith("what word"):
        chunk(sid, m.get("word") or "I do not remember any word")
    elif words[:1] == ["sleep"] or _sleep_line(text):
        chunk(sid, "sleeping")
        _cancel.clear()
        n = _sleep_line(text) or (words[1] if len(words) > 1 else "30")
        _cancel.wait(float(n))
        if _cancel.is_set():
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
            return
    elif text == "die":
        chunk(sid, "dying")
        os._exit(3)
    elif text == "perm" or "touch " in text:     # never touches anything
        _next[0] += 1
        pid = _next[0]
        ev = threading.Event()
        _answers[pid] = {"ev": ev}
        send({"jsonrpc": "2.0", "id": pid, "method": "session/request_permission",
              "params": {"sessionId": sid, "toolCall": {
                  "toolCallId": "t1", "title": "touch /tmp/x", "kind": "execute",
                  "rawInput": {"command": "touch /tmp/x"}},
                  "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                              {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]}})
        ev.wait()
        chunk(sid, "permission: " + json.dumps(_answers[pid].get("result")))
    else:
        chunk(sid, "echo: " + text)
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None and rid in _answers:           # a reply to our ask
            _answers[rid]["result"] = msg.get("result")
            _answers[rid]["ev"].set()
            continue
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": 1, "agentCapabilities": {"loadSession": True},
                "agentInfo": {"name": "fake"}}})
        elif method == "session/new":
            sid = uuid.uuid4().hex
            save(sid, {})
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": sid,
                                                          "configOptions": []}})
        elif method == "session/load" and os.environ.get("FAKE_ACP_NO_LOAD"):
            # A lane that cannot reload a conversation (rigs: `rebuilt`).
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32002, "message": "session not found"}})
        elif method == "session/load":
            sid = params.get("sessionId")
            chunk(sid, "REPLAYED HISTORY")          # the hub must suppress this
            result = {"configOptions": []}
            if os.environ.get("FAKE_ACP_NOTICE"):  # like ollama_acp's context-lost notice
                result["_meta"] = {"corral/notice": os.environ["FAKE_ACP_NOTICE"],
                                   "corral/contextLost": True}
            send({"jsonrpc": "2.0", "id": rid, "result": result})
        elif method == "session/prompt":
            if os.environ.get("FAKE_ACP_AUTH_FAIL"):
                # The claude-code-acp adapter's own shape for a lapsed login:
                # RequestError.authRequired() → -32000 "Authentication required".
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32000, "message": "Authentication required"}})
                continue
            threading.Thread(target=prompt, args=(rid, params), daemon=True).start()
        elif method == "session/cancel":
            _cancel.set()
        elif method == "session/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessions": []}})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": f"unsupported: {method}"}})


if __name__ == "__main__":
    main()
