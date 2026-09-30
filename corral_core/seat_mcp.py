#!/usr/bin/env python3
"""corral-seats — the MCP server a pane's agent uses to reach other panes.

DESIGN-5 S8 (S10, S11), plus ask_human. Five tools, nothing else:

    seat_list()              -> who can be addressed: [{seat, display, lane,
                                tool}] -- no titles, no transcripts
    seat_send(seat, text)    -> the hub's answer, verbatim: delivered (with a
                                turn id) | refused (with the reason) | failed
                                | queued (S11b: held for a waiter's next turn)
    seat_broadcast(text)     -> one seat_send per OTHER seated pane, each with
                                its own answer; one refusal unsends nothing
    seat_wait(seat, turn|until, timeout_s)
                             -> block HERE, bounded, until a turn this pane
                                sent has ended or the seat shows a state:
                                {result, state, turn_ended} -- never text
    ask_human(question)      -> raise ONE open question on THIS pane for its
                                human: the roster reads needs-you and shows
                                it until a human sends the pane a turn. A
                                second ask replaces the first.

Stdlib JSON-RPC over stdio, one message per line -- the same three methods
Corral's registry proxy speaks (initialize, tools/list, tools/call). The hub
appends this server to every eligible pane at session/new and session/load;
the adapter (Claude Code, Codex, ...) starts it as a child and hands its
tools to the model.

WHO IS SENDING
    The HUB decides, not this process and not the model. The adapter starts
    this server with three environment entries the hub put in the descriptor:
    CC_RUNBOOK_SESSION (the pane id, a label), CORRAL_PANE_TOKEN and
    CORRAL_HUB_URL. Every call carries the token in X-Corral-Pane-Token; the
    hub maps it to the pane it minted it for and uses THAT pane as the
    sender. A `from` a caller puts anywhere is ignored. The token is minted
    fresh at every spawn and lives only in the hub's memory and in this
    process's environment -- never in meta.json, never on disk.

WHAT THE TOKEN IS NOT (the honest threat statement, DESIGN-5 section 7.9)
    It is a LABEL for the supported path, not a secret. It sits in this
    process's environment, and -- measured live 2026-09-29 -- the Claude
    adapter also puts it on the `claude` process's COMMAND LINE, which any
    local user can read with `ps` on a host whose /proc is not hidepid. So the
    hub does not trust the token alone: on Linux it looks up who opened the
    calling socket (/proc/net/tcp) and refuses any caller that is not the
    hub's own UNIX user. What remains is the documented boundary: any process
    running AS THAT USER can send as a pane -- and an agent with a shell
    (Codex and Grok were measured running one without a permission card) can
    already type into any pane through `corral consult send`. This path adds
    provenance and a gate on the route that is SUPPOSED to be used, not a
    principal those processes cannot forge.

BOUNDS
    Every hub call has a timeout; a response is capped; the tool text a model
    sees is capped. Nothing here retries: `refused` is an answer, not an error.
    A wait is bounded (PEER_WAIT_MAX_S), polls at PEER_WAIT_POLL_S, and at most
    one is in flight per process -- one process per pane spawn, so one per
    source pane. The hub is only ever asked a question it answers at once; a
    poll that fails after the first one (the hub restarted, which also revokes
    this process's token) ends the wait `interrupted`, never a silent retry.

    python3 seat_mcp.py            (started by an adapter, not by hand)
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HUB_TIMEOUT_S = 15
PEER_WAIT_S = 120                 # seat_wait's default bound (DESIGN-5 S11)
PEER_WAIT_MAX_S = 600             # the most a caller may ask for; more is refused
PEER_WAIT_POLL_S = 1.0            # one read of the hub per second while waiting
WAIT_UNTIL = ("your-turn", "idle", "needs-you", "dead")
# Neither changes by itself: a human must resume a paused pane or restart a
# dead one, so waiting on through either is waiting on nothing.
WAIT_BLOCKED = ("paused", "dead")
_WAIT_LOCK = threading.Lock()     # one wait in flight per process
MAX_RESPONSE_BYTES = 256 * 1024
MAX_ASK_CHARS = 2000              # one question for a human; more is REFUSED
TOKEN_HEADER = "X-Corral-Pane-Token"
PROTOCOL = "2024-11-05"           # what Corral's registry proxy speaks today

# Said to the model verbatim (DESIGN-5 S8). The one sentence that decides
# whether an agent treats a refusal as a wall or as a reason to hammer: it is
# the hub's answer about another pane's state, not a fault in this call.
REFUSAL_GUIDANCE = (
    "`refused` means the other seat is busy, waiting on its human, or not "
    "allowed to receive this; it is not an error to retry — call `seat_list` "
    "to see its state and continue your own work.")

TOOLS = [
    {"name": "seat_list",
     "description": (
         "List the panes on this Corral wall that can be addressed by name "
         "(their `seat`), with each one's state: working, your-turn (ready "
         "for a message), idle, needs-you (waiting on its human), paused (a "
         "human must resume it) or dead. `tool` says whether that pane's "
         "agent was offered this tool too -- a pane without it can receive a "
         "message but cannot reply through Corral. No titles, no "
         "transcripts."),
     "inputSchema": {"type": "object", "properties": {},
                     "additionalProperties": False}},
    {"name": "seat_send",
     "description": (
         "Send one message to the agent in another pane, by its seat. It "
         "arrives in that pane's transcript as a message from THIS pane, "
         "fenced and marked untrusted -- never as that pane's human. The "
         "result is `delivered` (with a turn id), `refused` (with a reason), "
         "`failed`, or `queued`: the other seat is waiting on the turn YOU "
         "are running, so the message is held and delivered as its next turn "
         "once its current turn ends -- it has NOT been delivered yet, and it "
         "can still be refused, expire, or be dropped (at most one is held "
         "per seat). " + REFUSAL_GUIDANCE + " After four messages pass "
         "between panes with no human turn, sending is refused until a human "
         "speaks. The sender label is the supported path, not proof: any "
         "process running as this user could send as this pane."),
     "inputSchema": {"type": "object",
                     "properties": {
                         "seat": {"type": "string",
                                  "description": "the other pane's seat, "
                                                 "without the @"},
                         "text": {"type": "string",
                                  "description": "the message"}},
                     "required": ["seat", "text"],
                     "additionalProperties": False}},
    {"name": "seat_broadcast",
     "description": (
         "Send one message to EVERY other seated pane at once. Each seat is "
         "its own `seat_send`: the result is a list with one entry per seat "
         "-- `delivered`, `refused` or `failed` -- and a refusal for one seat "
         "does not stop or undo the others (a delivered message cannot be "
         "taken back). Each seat counts as one send against this pane's "
         "hourly limit. " + REFUSAL_GUIDANCE),
     "inputSchema": {"type": "object",
                     "properties": {
                         "text": {"type": "string",
                                  "description": "the message"}},
                     "required": ["text"],
                     "additionalProperties": False}},
    {"name": "seat_wait",
     "description": (
         "Wait, here, until another seat is done -- instead of calling "
         "`seat_list` over and over. Give EITHER `turn` (the turn id "
         "`seat_send` returned: waits for that message's turn to end) OR "
         "`until` (a state: your-turn, idle, needs-you or dead; your-turn is "
         "also met by idle). After a `seat_send`, wait on its `turn`. The "
         "result is `reached`, `timed_out` (after `timeout_s`, default "
         f"{PEER_WAIT_S}, at most {PEER_WAIT_MAX_S}), `blocked` (the seat is "
         "paused or dead, which only a human changes), `not-run` (the message "
         "was withdrawn before it ran), `interrupted` (the hub restarted or "
         "stopped answering) or `refused`. It returns the seat's state and "
         "whether the turn ended -- NEVER what the other agent wrote; if it "
         "has something for you, it sends it with its own `seat_send`. While "
         "you wait you are working: only a message from the seat you wait on, "
         "sent during the turn you wait on, is queued for you; it arrives as "
         "your NEXT turn, after this one ends. Anyone else is refused `busy`. "
         "One wait at a time."),
     "inputSchema": {"type": "object",
                     "properties": {
                         "seat": {"type": "string",
                                  "description": "the other pane's seat, "
                                                 "without the @"},
                         "turn": {"type": "string",
                                  "description": "a turn id seat_send "
                                                 "returned"},
                         "until": {"type": "string", "enum": list(WAIT_UNTIL),
                                   "description": "a state to wait for"},
                         "timeout_s": {"type": "integer", "minimum": 1,
                                       "maximum": PEER_WAIT_MAX_S,
                                       "description": "seconds, default "
                                                      f"{PEER_WAIT_S}"}},
                     "required": ["seat"],
                     "additionalProperties": False}},
    {"name": "ask_human",
     "description": (
         "Ask YOUR human -- the person at this Corral wall -- for a decision, "
         "an answer, or their attention. Use this WHENEVER you need them "
         "before you can continue, then end your turn. Prose alone will not "
         "raise anything: a question written only in your reply looks exactly "
         "like a pane that simply finished, and nobody is told. This marks "
         "your pane `needs-you` and shows the question on the wall until the "
         "human sends you a message (their answer arrives as your next turn). "
         "One open question per pane: asking again replaces the previous "
         "one. Not for talking to other panes -- that is `seat_send`. "
         f"At most {MAX_ASK_CHARS} characters; longer is refused, not cut."),
     "inputSchema": {"type": "object",
                     "properties": {
                         "question": {"type": "string",
                                      "maxLength": MAX_ASK_CHARS,
                                      "description": "the question, in full, "
                                                     "as the human should "
                                                     "read it"}},
                     "required": ["question"],
                     "additionalProperties": False}},
]


class HubError(Exception):
    pass


def _hub(method, path, body=None, env=None, timeout=HUB_TIMEOUT_S):
    """One call to the hub, with the token. Raises HubError with a readable
    reason; never retries."""
    env = os.environ if env is None else env
    url = (env.get("CORRAL_HUB_URL") or "").rstrip("/")
    token = env.get("CORRAL_PANE_TOKEN") or ""
    if not url or not token:
        raise HubError("this pane was not given a seat token by its hub "
                       "(CORRAL_HUB_URL / CORRAL_PANE_TOKEN absent)")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url + path, data=data, method=method,
                                 headers={TOKEN_HEADER: token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(MAX_RESPONSE_BYTES) or b"{}").get("error")
        except ValueError:
            msg = None
        raise HubError(f"hub said {e.code}: {msg or e.reason}")
    except (urllib.error.URLError, OSError) as e:
        raise HubError(f"hub unreachable at {url}: {e}")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise HubError("hub response too large")
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise HubError("hub answered with something that is not JSON")


def _wait_args(args):
    """-> (seat, turn, until, timeout_s, None) or a refusal dict as the last
    item. Out of range is REFUSED, not clamped: a model that asked for an
    hour and silently got ten minutes would read the timeout as the answer."""
    seat, turn, until = args.get("seat"), args.get("turn"), args.get("until")
    timeout = args.get("timeout_s", PEER_WAIT_S)

    def no(why):
        return None, None, None, None, {"result": "refused",
                                        "reason": "arguments", "why": why}
    if not isinstance(seat, str) or not seat.lstrip("@"):
        return no("seat_wait needs a `seat` string")
    if (turn is None) == (until is None):
        return no("give exactly one of `turn` (a turn id seat_send returned) "
                  "or `until` (a state)")
    if turn is not None and (not isinstance(turn, str) or not turn):
        return no("`turn` must be the turn id string seat_send returned")
    if until is not None and until not in WAIT_UNTIL:
        return no(f"`until` must be one of {', '.join(WAIT_UNTIL)}")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
            or not 1 <= timeout <= PEER_WAIT_MAX_S:
        return no(f"`timeout_s` must be a number of seconds from 1 to "
                  f"{PEER_WAIT_MAX_S}")
    return seat.lstrip("@"), turn, until, float(timeout), None


def seat_wait(args, env=None, clock=time.monotonic, sleep=time.sleep):
    """-> (result_dict, is_error). Blocks THIS process, never the hub: each
    poll is one immediate read (/api/peer/turn with a turn, /api/peer/seats
    with a state). Bounded by `timeout_s`; one at a time per process."""
    seat, turn, until, timeout, refusal = _wait_args(args)
    if refusal:
        return refusal, False
    if not _WAIT_LOCK.acquire(blocking=False):
        return {"result": "refused", "reason": "wait-in-flight",
                "why": "this pane is already waiting on a seat; one wait at a "
                       "time"}, False
    try:
        return _wait(seat, turn, until, timeout, env, clock, sleep)
    finally:
        _WAIT_LOCK.release()


def _wait(seat, turn, until, timeout, env, clock, sleep):
    start = clock()
    deadline = start + timeout
    state, polled = None, False

    def done(result, ended=False, **extra):
        out = {"result": result, "seat": seat, "state": state,
               "turn_ended": ended,
               "waited_s": round(max(0.0, clock() - start), 1)}
        if turn is not None:
            out["turn"] = turn
        out.update({k: v for k, v in extra.items() if v is not None})
        return out, False

    while True:
        per_call = max(0.5, min(HUB_TIMEOUT_S, deadline - clock()))
        try:
            if turn is not None:
                q = urllib.parse.urlencode({"seat": seat, "turn": turn})
                r = _hub("GET", "/api/peer/turn?" + q, env=env, timeout=per_call)
                if r.get("result") != "turn":
                    raise _Answered(r)
                state = r.get("display")
                if r.get("not_run"):
                    return done("not-run", why=r["not_run"])
                if r.get("ended"):
                    return done("reached", True, stop_reason=r.get("stop_reason"))
            else:
                r = _hub("GET", "/api/peer/seats", env=env, timeout=per_call)
                if r.get("you") == seat:
                    raise _Answered({"result": "refused", "reason": "self",
                                     "why": "a pane cannot wait on itself"})
                row = next((x for x in r.get("seats") or ()
                            if isinstance(x, dict) and x.get("seat") == seat),
                           None)
                if row is None:
                    raise _Answered({"result": "refused",
                                     "reason": "unknown-seat",
                                     "why": f"no open pane answers to @{seat[:40]}"
                                            f" — call seat_list to see who does"})
                state = row.get("display")
                if state == until or (until == "your-turn" and state == "idle"):
                    return done("reached")
        except _Answered as a:
            if not polled:                 # the hub's answer to the question
                return a.answer, False
            # The seat closed, or the turn left the hub's memory, mid-wait.
            return done("interrupted", why=str(a.answer.get("why") or
                                               a.answer.get("reason")))
        except HubError as e:
            if not polled:
                return {"result": "failed", "reason": "hub", "why": str(e)}, True
            return done("interrupted", why=str(e))
        polled = True
        if state in WAIT_BLOCKED and state != until:
            return done("blocked", why=f"@{seat} is {state}; only a human "
                                       f"changes that")
        left = deadline - clock()
        if left <= 0:
            return done("timed_out")
        sleep(min(PEER_WAIT_POLL_S, left))


class _Answered(Exception):
    """The hub answered, but not with what a wait can use (a refusal)."""
    def __init__(self, answer):
        super().__init__(answer.get("reason"))
        self.answer = answer


def call_tool(name, args, env=None):
    """-> (result_dict, is_error). A refusal is NOT an error: it is the hub's
    answer, and a model told "error" tends to retry."""
    args = args if isinstance(args, dict) else {}
    try:
        if name == "seat_list":
            return _hub("GET", "/api/peer/seats", env=env), False
        if name == "seat_send":
            seat, text = args.get("seat"), args.get("text")
            if not isinstance(seat, str) or not isinstance(text, str):
                return {"result": "refused", "reason": "arguments",
                        "why": "seat_send needs a `seat` and a `text`, both "
                               "strings"}, False
            # Only these two keys leave this process: whatever else a caller
            # passed -- a `from`, a pane id -- never reaches the hub.
            return _hub("POST", "/api/peer/send",
                        {"seat": seat.lstrip("@"), "text": text}, env=env), False
        if name == "seat_broadcast":
            text = args.get("text")
            if not isinstance(text, str):
                return {"result": "refused", "reason": "arguments",
                        "why": "seat_broadcast needs a `text` string"}, False
            return _hub("POST", "/api/peer/broadcast", {"text": text},
                        env=env), False
        if name == "seat_wait":
            return seat_wait(args, env=env)
        if name == "ask_human":
            q = args.get("question")
            if not isinstance(q, str) or not q.strip():
                return {"result": "refused", "reason": "arguments",
                        "why": "ask_human needs a `question` string"}, False
            if len(q) > MAX_ASK_CHARS:
                return {"result": "refused", "reason": "too-long",
                        "why": f"the question is {len(q)} characters; the "
                               f"limit is {MAX_ASK_CHARS}. Shorten it -- it "
                               f"was not sent"}, False
            # Only the question leaves this process: the token decides
            # WHICH pane is asking, never a field the caller supplies.
            return _hub("POST", "/api/peer/ask", {"question": q}, env=env), False
    except HubError as e:
        return {"result": "failed", "reason": "hub", "why": str(e)}, True
    return {"result": "failed", "reason": "unknown-tool",
            "why": f"no tool {str(name)[:40]!r}"}, True


def handle(msg, env=None):
    """One JSON-RPC message -> the response dict, or None for a notification."""
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None                                   # notifications/initialized etc.
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL,
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "corral-seats", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        out, is_error = call_tool(p.get("name"), p.get("arguments"), env=env)
        result = {"content": [{"type": "text", "text": json.dumps(out)}],
                  "isError": is_error}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"unsupported: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


_OUT_LOCK = threading.Lock()


def _reply(msg):
    out = handle(msg)
    if out is not None:
        with _OUT_LOCK:                   # one whole line per response
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


def main():
    """Read requests in order. A tools/call runs on its own thread so a
    seat_wait blocking here does not stop this process from answering the
    next call -- a second wait is then REFUSED `wait-in-flight`, rather than
    queued behind the first. At end of input the calls still running are
    answered before this process exits; every call is bounded, so every
    thread ends."""
    calls = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("method") == "tools/call":
            t = threading.Thread(target=_reply, args=(msg,), daemon=True)
            t.start()
            calls = [c for c in calls if c.is_alive()] + [t]
        else:
            _reply(msg)
    for t in calls:
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
