#!/usr/bin/env python3
"""A tiny, real ACP agent for Corral Light's tests. Stdlib only.

Speaks just enough of the protocol over stdio for lifecycle tests to use a real
process (spawned, killed, resumed):

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
    write <rel> <text> -> writes <text> (+ newline) to <rel> under the cwd
    rm <rel>          -> deletes <rel> under the cwd
    commit <msg>      -> git add -A && git commit -m <msg> in the cwd
    bg-write <rel> <n> <interval>
                      -> spawns a child (in the agent's process group) that
                         appends to <rel> n times, every <interval> s, after
                         the turn ends; bg-write-setsid detaches it instead
    checkout <branch> -> git checkout -q -b <branch>
    reset-hard <rev>  -> git reset -q --hard <rev>
    pwd               -> answers with os.getcwd()
    replay <name>     -> sends each line of $FAKE_ACP_REPLAY/<name>.jsonl (one
                         recorded ACP `update` object per line) as a
                         session/update, then says "replayed <n>"
    perm-always       -> like perm, but also offers an allow_always option
    permjson <json>   -> asks one session/request_permission built from a JSON
                         object {kind, title, locations, rawInput, content,
                         options}; any key left out is left out of the
                         request (options default to allow_once/reject_once).
                         `pad: n` sets rawInput.new_string to n bytes (an
                         oversize request without an oversize prompt);
                         `delay: s` waits s seconds before asking.
                         Reports the answer; never runs anything
    tool-edit <path>, tool-read <path>
                      -> reports a tool_call of that kind at <path>, then waits
                         3 s (a cancel ends the turn early)
    a blind-challenge prompt ("You are reviewing a change you did not write")
                      -> saves the prompt to $FAKE_ACP_DIR/review-prompt.txt,
                         then answers per $FAKE_ACP_REVIEW: `json` (findings,
                         after a decoy block), `broken` (bad json), `none` (no
                         block), `slow` (waits for a cancel), `die`, `probe`
                         (tries the sandbox's walls, asks to edit, reports)
    anything else     -> "echo: <text>"
    (file verbs refuse any path containing "..")

State lives in $FAKE_ACP_DIR: one JSON file per session, plus `pid-<pid>`
marker files so a test can find and kill the process.
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


def _file_verb(words, text):
    """The worktree tests' verbs; paths are relative to the agent's cwd."""
    import subprocess
    verb = words[0]
    if verb == "pwd":
        return os.getcwd()
    if any(".." in w for w in words[1:2]):
        return "refused: .. in path"
    if verb == "write":
        rel, body = words[1], text.split(None, 2)[2] if len(words) > 2 else ""
        Path(rel).parent.mkdir(parents=True, exist_ok=True)
        Path(rel).write_text(body + "\n")
        return f"wrote {rel}"
    if verb == "rm":
        Path(words[1]).unlink()
        return f"removed {words[1]}"
    if verb == "commit":
        msg = text.split(None, 1)[1] if len(words) > 1 else "fake commit"
        r = subprocess.run(["sh", "-c", 'git add -A && git commit -q -m "$1"', "sh", msg],
                           capture_output=True, text=True)
        return f"commit rc={r.returncode} {r.stderr.strip()[:200]}"
    if verb in ("bg-write", "bg-write-setsid"):
        rel, n, every = words[1], int(words[2]), float(words[3])
        script = ("import time,sys\n"
                  "for i in range(%d):\n"
                  "    open(%r,'a').write('bg %%d\\n' %% i); time.sleep(%r)\n" % (n, rel, every))
        child = subprocess.Popen([sys.executable, "-c", script],
                                 start_new_session=(verb == "bg-write-setsid"),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        (DIR / f"bg-{child.pid}").write_text(str(child.pid))
        return f"bg-write started pid {child.pid}"
    if verb == "checkout":
        r = subprocess.run(["git", "checkout", "-q", "-b", words[1]], capture_output=True, text=True)
        return f"checkout rc={r.returncode}"
    if verb == "reset-hard":
        r = subprocess.run(["git", "reset", "-q", "--hard", words[1]], capture_output=True, text=True)
        return f"reset rc={r.returncode}"
    return "?"


def prompt(rid, params):
    sid = params.get("sessionId")
    text = "".join(p.get("text", "") for p in params.get("prompt") or [])
    if text.startswith("[Corral] ") and "\n\n" in text:
        # An own-branch preamble (sessions.WORKTREE_PREAMBLE): acknowledge it,
        # then treat what follows as the prompt, as a real agent would.
        chunk(sid, "preamble received; ")
        text = text.split("\n\n", 1)[1]
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
    elif words[:1] in (["write"], ["rm"], ["commit"], ["bg-write"], ["bg-write-setsid"], ["checkout"],
                       ["reset-hard"], ["pwd"]):
        chunk(sid, _file_verb(words, text))
    elif words[:1] in (["tool-edit"], ["tool-read"]):
        kind = "edit" if words[0] == "tool-edit" else "read"
        send({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": sid, "update": {"sessionUpdate": "tool_call", "toolCallId": "tc-" + kind,
                                         "title": f"{kind} {words[1]}", "kind": kind,
                                         "status": "completed",
                                         "locations": [{"path": words[1]}]}}})
        chunk(sid, f"{kind} reported")
        _cancel.clear()
        _cancel.wait(3)
        if _cancel.is_set():
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
            return
    elif text == "perm-always":
        _next[0] += 1
        pid = _next[0]
        ev = threading.Event()
        _answers[pid] = {"ev": ev}
        send({"jsonrpc": "2.0", "id": pid, "method": "session/request_permission",
              "params": {"sessionId": sid, "toolCall": {
                  "toolCallId": "t2", "title": "git commit", "kind": "execute",
                  "rawInput": {"command": "git commit"}},
                  "options": [{"optionId": "always", "name": "Always allow", "kind": "allow_always"},
                              {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                              {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]}})
        ev.wait()
        chunk(sid, "permission: " + json.dumps(_answers[pid].get("result")))
    elif text.startswith("You are reviewing a change you did not write"):
        (DIR / "review-prompt.txt").write_text(text)
        mode = os.environ.get("FAKE_ACP_REVIEW", "json")
        if mode == "die":
            os._exit(3)
        if mode == "slow":
            chunk(sid, "reading")
            _cancel.clear()
            _cancel.wait(60)
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
            return
        if mode == "probe":
            # The reviewer sandbox, seen from inside: try each way out and
            # report what happened as findings (FAKE_ACP_PROBE: JSON of
            # {name: path} to write, plus "read:<name>": path to read).
            spec = json.loads(os.environ.get("FAKE_ACP_PROBE") or "{}")
            out = []
            for name, path in spec.items():
                path = path.format(pane=os.environ.get("CORRAL_PANE_ID", ""), cwd=os.getcwd())
                try:
                    if name.startswith("env:"):
                        if path not in os.environ:
                            raise OSError("unset")
                    elif name.startswith("connect:"):
                        import socket
                        h, _, port = path.rpartition(":")
                        socket.create_connection((h, int(port)), timeout=3).close()
                    elif name.startswith("abstract:"):
                        import socket
                        u = socket.socket(socket.AF_UNIX)
                        u.connect(b"\0" + path.encode())
                        u.close()
                    elif name.startswith("read:"):
                        Path(path).read_bytes()
                    else:
                        Path(path).write_text("probe")
                    out.append({"file": name, "line": 1, "severity": "low", "claim": "open",
                                "evidence": path})
                except OSError as e:
                    out.append({"file": name, "line": 1, "severity": "low", "claim": "closed",
                                "evidence": type(e).__name__})
            out.append({"file": "cwd", "line": 1, "severity": "low", "claim": os.getcwd(),
                        "evidence": ",".join(sorted(os.listdir(".")))[:200]})
            out.append({"file": "env", "line": 1, "severity": "low",
                        "claim": os.environ.get("SSH_AUTH_SOCK", "unset"),
                        "evidence": os.environ.get("CORRAL_REVIEW_SANDBOX", "0")})
            # Then ask to write, as an injected reviewer would.
            _next[0] += 1
            pid = _next[0]
            ev = threading.Event()
            _answers[pid] = {"ev": ev}
            send({"jsonrpc": "2.0", "id": pid, "method": "session/request_permission",
                  "params": {"sessionId": sid, "toolCall": {
                      "toolCallId": "tp", "title": "Edit README.md", "kind": "edit",
                      "rawInput": {"file_path": "README.md", "new_string": "pwned"}},
                      "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                                  {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]}})
            ev.wait(30)
            out.append({"file": "permission", "line": 1, "severity": "low",
                        "claim": json.dumps(_answers[pid].get("result")), "evidence": ""})
            chunk(sid, "```json\n" + json.dumps({"verdict": "amend", "findings": out}) + "\n```\n")
        elif mode == "json":
            chunk(sid, "An example first:\n```json\n{\"verdict\": \"accept\", \"findings\": []}\n```\n"
                       "Real answer:\n```json\n" + json.dumps({
                           "verdict": "amend", "findings": [
                               {"file": "a.txt", "line": 1, "severity": "high",
                                "claim": "drops the base line", "evidence": "line 1 replaced"},
                               {"file": "b.txt", "line": "x", "severity": "odd",
                                "claim": "no line", "evidence": ""}]}) + "\n```\n")
        elif mode == "broken":
            chunk(sid, "```json\n{\"verdict\": \"amend\", \"findings\": [\n```")
        else:
            chunk(sid, "Looks fine to me, no findings.")
    elif words[:1] == ["permjson"]:
        spec = json.loads(text.split(None, 1)[1])
        _next[0] += 1
        pid = _next[0]
        ev = threading.Event()
        _answers[pid] = {"ev": ev}
        tc = {"toolCallId": "tj%d" % pid}
        for k in ("kind", "title", "locations", "rawInput", "content"):
            if k in spec:
                tc[k] = spec[k]
        if spec.get("pad"):
            tc["rawInput"] = dict(tc.get("rawInput") or {}, new_string="x" * int(spec["pad"]))
        if spec.get("delay"):
            time.sleep(float(spec["delay"]))
        opts = spec.get("options") or [
            {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
            {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]
        send({"jsonrpc": "2.0", "id": pid, "method": "session/request_permission",
              "params": {"sessionId": sid, "toolCall": tc, "options": opts}})
        ev.wait()
        chunk(sid, "permission: " + json.dumps(_answers[pid].get("result")))
    elif len(words) == 2 and words[0] == "replay" and ".." not in words[1] \
            and "/" not in words[1]:
        src = Path(os.environ.get("FAKE_ACP_REPLAY", "")) / (words[1] + ".jsonl")
        n = 0
        for line in src.read_text(encoding="utf-8").splitlines():
            if line.strip():
                send({"jsonrpc": "2.0", "method": "session/update",
                      "params": {"sessionId": sid, "update": json.loads(line)}})
                n += 1
        chunk(sid, f"replayed {n}")
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
