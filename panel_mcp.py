#!/usr/bin/env python3
"""corral-panel — the stdio MCP server a pane's agent uses to run a panel.

Tools: panel_start, panel_status, panel_await, panel_next, panel_ack. The
agent that starts a panel is its AUTHOR: the hub runs the arms, seals their
answers, synthesizes, and raises the packet as a question on the author's
pane when it is done, so the author's context never carries the arms' text.
Every call carries CORRAL_PANE_TOKEN; the hub, not the caller, decides the
sender. All calls are bounded; panel_await polls the hub at most once a
second for at most AWAIT_MAX_S.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HUB_TIMEOUT_S = 20
AWAIT_S = 300
AWAIT_MAX_S = 600
POLL_S = 2.0
MAX_RESPONSE_BYTES = 512 * 1024
TOKEN_HEADER = "X-Corral-Pane-Token"
PROTOCOL = "2024-11-05"
SERVER = "corral-panel"
ENV = "CORRAL_PANEL_MCP"            # "0" = do not offer the tools

TOOLS = [
    {"name": "panel_start",
     "description": (
         "Start a DURABLE cross-vendor review panel that the hub runs for you. "
         "Give the charge (the full review brief, or a path to a file holding "
         "it), the arm lanes (other vendors: e.g. codex, grok, gemini -- never "
         "your own lane), and a short topic. The hub opens one background pane "
         "per arm, sends the charge blind, seals every answer to a file when "
         "its turn ends, has a fresh pane on an uninvolved lane synthesize from "
         "the sealed files, and writes ONE packet (verdict per arm, a findings "
         "table, verbatim dissent, next action, file paths). You get the packet "
         "as a question on this pane when it is done; you do NOT need to wait, "
         "read the arms, or relay anything. Returns at once with the run id. "
         "Default one round; `rounds: 2` cross-feeds the arms once. The run "
         "survives your /clear and a hub restart."),
     "inputSchema": {"type": "object",
                     "properties": {
                         "topic": {"type": "string", "maxLength": 80},
                         "charge": {"type": "string",
                                    "description": "the full charge text (or use charge_file)"},
                         "charge_file": {"type": "string",
                                         "description": "path to the charge, absolute or under this pane's cwd"},
                         "lanes": {"type": "array", "items": {"type": "string"},
                                   "minItems": 1, "maxItems": 4,
                                   "description": "arm lanes, each a different vendor from yours"},
                         "cwd": {"type": "string",
                                 "description": "the folder the arms review (default: this pane's)"},
                         "rounds": {"type": "integer", "minimum": 1, "maximum": 2},
                         "synth_lane": {"type": "string",
                                        "description": "who synthesizes (default: a lane that is neither an arm nor you)"},
                         "brief_id": {"type": "string"}},
                     "required": ["topic", "lanes"],
                     "additionalProperties": False}},
    {"name": "panel_status",
     "description": ("One panel's state by id (round, each arm's turn status and verdict, the "
                     "synthesizer, the packet path), or every panel on this hub when no id is given."),
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                     "additionalProperties": False}},
    {"name": "panel_await",
     "description": (f"Wait, here, until a panel is done (or failed/cancelled), polling the hub; "
                     f"returns its status and the packet path. At most {AWAIT_MAX_S} s per call "
                     f"(default {AWAIT_S}); `timed_out` means it is still running -- call again, or "
                     "simply end your turn: the packet arrives as a question on this pane."),
     "inputSchema": {"type": "object",
                     "properties": {"id": {"type": "string"},
                                    "timeout_s": {"type": "integer", "minimum": 1, "maximum": AWAIT_MAX_S}},
                     "required": ["id"], "additionalProperties": False}},
    {"name": "panel_next",
     "description": ("One more round on a finished panel. REFUSED unless the charge file or the "
                     "tree changed since the last round, and past the round cap: identical "
                     "inputs do not get another round."),
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                     "required": ["id"], "additionalProperties": False}},
    {"name": "panel_ack",
     "description": "Acknowledge a finished panel's packet so it leaves the wall's rail.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                     "required": ["id"], "additionalProperties": False}},
]


class HubError(Exception):
    pass


def _hub(method, path, body=None, env=None, timeout=HUB_TIMEOUT_S):
    env = os.environ if env is None else env
    url = (env.get("CORRAL_HUB_URL") or "").rstrip("/")
    token = env.get("CORRAL_PANE_TOKEN") or ""
    if not url or not token:
        raise HubError("this pane was not given a token by its hub "
                       "(CORRAL_HUB_URL / CORRAL_PANE_TOKEN absent)")
    if method == "GET" and body:
        path += "?" + urllib.parse.urlencode({k: v for k, v in body.items() if v is not None})
        body = None
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url + path, data=data, method=method,
                                 headers={TOKEN_HEADER: token, "Content-Type": "application/json"})
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


def descriptor(pane, hub_url, token):
    """ACP McpServerStdio entry for a pane, or None when opted out."""
    if os.environ.get(ENV) == "0" or not hub_url or not token:
        return None
    return {"name": SERVER, "command": sys.executable,
            "args": [os.path.abspath(__file__)],
            "env": [{"name": "CORRAL_PANE_TOKEN", "value": token},
                    {"name": "CORRAL_HUB_URL", "value": hub_url}]}


def _await(args, env=None, clock=time.monotonic, sleep=time.sleep):
    pid = str(args.get("id") or "")
    try:
        timeout = int(args.get("timeout_s") or AWAIT_S)
    except (TypeError, ValueError):
        timeout = AWAIT_S
    timeout = max(1, min(timeout, AWAIT_MAX_S))
    end = clock() + timeout
    while True:
        r = _hub("GET", "/api/peer/panel/status", {"id": pid}, env=env)
        if r.get("result") == "refused":
            return r, False
        pan = r.get("panel") or {}
        if pan.get("status") in ("done", "failed", "cancelled"):
            return {"result": pan.get("status"), "panel": pan,
                    "packet": pan.get("packet_path")}, False
        if clock() >= end:
            return {"result": "timed_out", "panel": pan,
                    "why": "still running; call again or end your turn and the "
                           "packet arrives as a question on this pane"}, False
        sleep(min(POLL_S, max(0.0, end - clock())))


def call_tool(name, args, env=None):
    args = args if isinstance(args, dict) else {}
    try:
        if name == "panel_start":
            body = {k: args.get(k) for k in ("topic", "charge", "charge_file", "lanes", "cwd",
                                             "rounds", "synth_lane", "brief_id")}
            if not body.get("charge") and not body.get("charge_file"):
                return {"result": "refused", "reason": "arguments",
                        "why": "give `charge` (the text) or `charge_file` (a path)"}, False
            return _hub("POST", "/api/peer/panel/start", body, env=env), False
        if name == "panel_status":
            return _hub("GET", "/api/peer/panel/status", {"id": args.get("id")}, env=env), False
        if name == "panel_await":
            return _await(args, env=env)
        if name == "panel_next":
            return _hub("POST", "/api/peer/panel/next", {"id": args.get("id")}, env=env), False
        if name == "panel_ack":
            return _hub("POST", "/api/peer/panel/ack", {"id": args.get("id")}, env=env), False
    except HubError as e:
        return {"result": "failed", "reason": "hub", "why": str(e)}, True
    return {"result": "failed", "reason": "unknown-tool", "why": f"no tool {str(name)[:40]!r}"}, True


def handle(msg, env=None):
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                  "serverInfo": {"name": SERVER, "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        out, is_error = call_tool(p.get("name"), p.get("arguments"), env=env)
        result = {"content": [{"type": "text", "text": json.dumps(out)}], "isError": is_error}
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
        with _OUT_LOCK:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


def main():
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
