#!/usr/bin/env python3
"""corral-seats — the MCP server a pane's agent uses to reach other panes.

DESIGN-5 S8. Two tools, nothing else:

    seat_list()              -> who can be addressed: [{seat, display, lane,
                                tool}] -- no titles, no transcripts
    seat_send(seat, text)    -> the hub's answer, verbatim: delivered (with a
                                turn id) | refused (with the reason) | failed

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
    It is a LABEL for the supported path, not a secret. Any process running
    as the same UNIX user can read it from /proc/<pid>/environ of this server
    or of the adapter, and send as that pane. And an agent with a shell
    (Codex and Grok were measured running one without a permission card)
    can already type into any pane through `corral consult send`. What this
    path adds is provenance and a gate on the path that is SUPPOSED to be
    used -- not a principal those panes cannot forge.

BOUNDS
    Every hub call has a timeout; a response is capped; the tool text a model
    sees is capped. Nothing here retries: `refused` is an answer, not an error.

    python3 seat_mcp.py            (started by an adapter, not by hand)
"""
import json
import os
import sys
import urllib.error
import urllib.request

HUB_TIMEOUT_S = 15
MAX_RESPONSE_BYTES = 256 * 1024
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
         "or `failed`. " + REFUSAL_GUIDANCE + " After four messages pass "
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
]


class HubError(Exception):
    pass


def _hub(method, path, body=None, env=None):
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
        with urllib.request.urlopen(req, timeout=HUB_TIMEOUT_S) as r:
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


def main():
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
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
