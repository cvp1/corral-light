#!/usr/bin/env python3
"""savvy-progress — the stdio MCP server behind a pane's flow progress strip.

The Corral end of the savvy-progress Claude Code mod (github.com/JohnnyVizz/
claude-kit, plugins/savvy-progress). Its two tools keep the mod's names, so the
unmodified savvy-flow skill and its workers find them:

  mcp__savvy-progress__progress  the orchestrator reports plan, phase, accepted
  mcp__savvy-progress__step      a worker reports its own steps

Neither tool sends anything anywhere. The call itself is the report: the
adapter streams each tool call (name, input, and the Agent call a subagent's
call belongs to) and the hub keeps a bounded copy of these tools' input on the
`tool` event (kept_input below). The wall draws the strip and the agents panel
from those events, and it ignores a `progress` call made inside a subagent, so
a worker cannot rename, finish or reset the flow.
"""
import json
import os
import sys

SERVER = "savvy-progress"
PROGRESS = f"mcp__{SERVER}__progress"
STEP = f"mcp__{SERVER}__step"
SUBAGENT_TOOLS = ("Agent", "Task")
ENV = "CORRAL_PROGRESS_MCP"          # "0" = do not offer the tools
PROTOCOL = "2024-11-05"

PHASES = ("plan", "design", "delegate", "review", "close")
TIERS = ("fable", "heavy", "careful", "medium", "light")
# The schemas are advice to the model, not a check: what a call may ask the
# wall to draw is bounded here, before it reaches events.jsonl.
MAX_TASKS = 200
MAX_AFTER = 20
MAX_TITLE = 120
MAX_COUNT = 1000

TOOLS = [
    {"name": "progress",
     "description": (
         "Report /savvy-flow progress to the progress strip above this pane's "
         "prompt and its agents panel on the Corral wall. Call it after "
         "presenting the plan (title, total, tasks, phase \"delegate\"), each "
         "time a task is accepted (done), when switching phase or re-planning "
         "(tasks), and once at the end with finished: true. Fields left out "
         "keep their previous value. For the main session only: a call from a "
         "subagent is ignored (workers report with `step`)."),
     "inputSchema": {
         "type": "object",
         "properties": {
             "title": {"type": "string",
                       "description": "Short name of the overall task, a few words."},
             "total": {"type": "integer", "minimum": 0,
                       "description": "Number of planned worker tasks."},
             "done": {"type": "integer", "minimum": 0,
                      "description": "Number of tasks accepted after review."},
             "phase": {"type": "string", "enum": list(PHASES)},
             "finished": {"type": "boolean",
                          "description": "True once the flow is closed."},
             "tasks": {
                 "type": "array",
                 "description": (
                     "The planned worker tasks in order, numbered from 1. Each "
                     "title must equal the Agent tool `description` the task "
                     "will be delegated with, so the panel can match runs to "
                     "tasks."),
                 "items": {
                     "type": "object",
                     "properties": {
                         "title": {"type": "string",
                                   "description": "A few words; reused verbatim "
                                                  "as the Agent description."},
                         "tier": {"type": "string", "enum": list(TIERS)},
                         "after": {"type": "array", "items": {"type": "integer"},
                                   "description": "Numbers of the tasks this "
                                                  "one waits for."}},
                     "required": ["title", "tier"]}}}}},
    {"name": "step",
     "description": (
         "For savvy-flow workers: report progress on your own task to the "
         "agents panel. Right after reading the brief, call it with `total` "
         "(your plan in 3-8 steps) and `done: 0`; call it again as each step "
         "finishes. Cheap and silent: it only draws a bar."),
     "inputSchema": {
         "type": "object",
         "properties": {
             "done": {"type": "integer", "minimum": 0,
                      "description": "Steps finished so far."},
             "total": {"type": "integer", "minimum": 1,
                       "description": "Steps planned; may change if the plan "
                                      "changes."},
             "note": {"type": "string",
                      "description": "The step in progress, a few words."}},
         "required": ["done"]}},
]


def _text(v, cap):
    return v.strip()[:cap] if isinstance(v, str) and v.strip() else None


def _count(v, cap=MAX_COUNT):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return max(0, min(cap, int(round(v))))


def _tier(v):
    t = (v or "").strip().lower() if isinstance(v, str) else ""
    t = t[len("savvy-"):] if t.startswith("savvy-") else t
    return t if t in TIERS else ""


def _progress_input(raw):
    out = {}
    if (t := _text(raw.get("title"), MAX_TITLE)) is not None:
        out["title"] = t
    for k in ("total", "done"):
        if (n := _count(raw.get(k), MAX_TASKS)) is not None:
            out[k] = n
    if raw.get("phase") in PHASES:
        out["phase"] = raw["phase"]
    if raw.get("finished") is True:
        out["finished"] = True
    tasks = raw.get("tasks")
    if isinstance(tasks, list):
        kept = []
        for t in tasks[:MAX_TASKS]:
            if not isinstance(t, dict) or not _text(t.get("title"), MAX_TITLE):
                continue
            after = t.get("after") if isinstance(t.get("after"), list) else []
            kept.append({"title": _text(t["title"], MAX_TITLE),
                         "tier": _tier(t.get("tier")),
                         "after": [n for n in after
                                   if isinstance(n, int) and not isinstance(n, bool)
                                   and 0 < n <= MAX_TASKS][:MAX_AFTER]})
        out["tasks"] = kept
    return out


def _step_input(raw):
    out = {}
    for k in ("done", "total"):
        if (n := _count(raw.get(k))) is not None:
            out[k] = n
    if (note := _text(raw.get("note"), MAX_TITLE)) is not None:
        out["note"] = note
    return out


def _subagent_input(raw):
    """What the panel shows of an Agent call. Not the prompt: it is the whole
    brief, and the transcript row already carries it."""
    out = {}
    for k, cap in (("description", MAX_TITLE), ("subagent_type", 80), ("model", 40)):
        if (v := _text(raw.get(k), cap)) is not None:
            out[k] = v
    if raw.get("run_in_background") is True:
        out["background"] = True
    return out


def kept_input(name, raw):
    """The bounded part of a tool call's input the hub keeps on its `tool`
    event, or None: only these tools' input is kept, never anyone else's."""
    if not isinstance(raw, dict) or not raw:
        return None
    if name == PROGRESS:
        out = _progress_input(raw)
    elif name == STEP:
        out = _step_input(raw)
    elif name in SUBAGENT_TOOLS:
        out = _subagent_input(raw)
    else:
        return None
    return out or None


def descriptor(pane):
    """ACP McpServerStdio entry for a pane, or None: Claude lanes only (the
    flow is a Claude Code skill), and `CORRAL_PROGRESS_MCP=0` opts out."""
    if os.environ.get(ENV) == "0" or getattr(pane, "agent", None) != "claude":
        return None
    return {"name": SERVER, "command": sys.executable,
            "args": [os.path.abspath(__file__)], "env": []}


def call_tool(name, args):
    """-> (text, is_error). The reply is a word for the model; the wall reads
    the call's input from the transcript, not from here."""
    args = args if isinstance(args, dict) else {}
    if name == "progress":
        kept = _progress_input(args)
        what = []
        if "done" in kept or "total" in kept:
            what.append(f"{kept.get('done', '?')}/{kept.get('total', '?')}")
        if "phase" in kept:
            what.append(kept["phase"])
        if kept.get("finished"):
            what.append("finished")
        return "ok" + (": " + ", ".join(what) if what else ""), False
    if name == "step":
        if "done" not in _step_input(args):
            return "step needs `done`, a whole number of steps finished", True
        return "ok", False
    return f"no tool {str(name)[:40]!r}", True


def handle(msg):
    """One JSON-RPC message -> the response dict, or None for a notification."""
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
        text, is_error = call_tool(p.get("name"), p.get("arguments"))
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
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
