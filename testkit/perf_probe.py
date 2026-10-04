#!/usr/bin/env python3
"""perf_probe — measure what the hub costs a client, as the plan's falsifier.

Reads the live hub as the hub's own UNIX account (it mints a session cookie
from the state dir's key, like `corral-light pair` does) and prints one JSON
document with:

  health_ms                 /health round trip
  state_full_ms / _kb       /api/state with no `since` (what a reconnect costs)
  state_delta_ms / _kb      /api/state?since=<current seqs> (what a poller pays)
  panes                     how many panes were on the wall during the run
  turn (optional, --turn)   one trivial prompt on --lane: spawn, first text,
                            turn_end and close, in ms, plus tokens carried

Latencies are p50/p95 over --n samples. Read-only apart from --turn, which
opens one background pane, sends one short prompt and closes it.

Run from the repo root:  python3 testkit/perf_probe.py [--n 20] [--turn --lane claude]
"""
import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import auth                                                       # noqa: E402

BASE = "http://127.0.0.1:8098"
COOKIE = "corral_light"
TURN_PROMPT = "Reply with exactly the single word: pong"


def _call(tok, path, body=None, timeout=240):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={
        "Cookie": f"{COOKIE}={tok}", "Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return (time.perf_counter() - t) * 1000, raw


def _pct(xs):
    xs = sorted(xs)
    return {"p50": round(statistics.median(xs), 1),
            "p95": round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 1)}


def sample(tok, n):
    out = {}
    out["health_ms"] = _pct([_call(tok, "/health")[0] for _ in range(n)])
    fulls, kb = [], 0
    for _ in range(max(3, n // 4)):
        ms, raw = _call(tok, "/api/state")
        fulls.append(ms)
        kb = len(raw) / 1024
    out["state_full_ms"], out["state_full_kb"] = _pct(fulls), round(kb)
    st = json.loads(raw)
    out["panes"] = len(st["panes"])
    since = {p["id"]: p["seq"] for p in st["panes"]}
    q = urllib.parse.quote(json.dumps(since))
    deltas, kb = [], 0
    for _ in range(n):
        ms, raw = _call(tok, "/api/state?since=" + q)
        deltas.append(ms)
        kb = len(raw) / 1024
    out["state_delta_ms"], out["state_delta_kb"] = _pct(deltas), round(kb, 1)
    # What the browser's refresh() pays: a cursor plus full=1 (hubs before the
    # 2026-10-04 delta contract ignore `full` and answer as above).
    brs, kb = [], 0
    for _ in range(max(3, n // 4)):
        ms, raw = _call(tok, "/api/state?full=1&since=" + q)
        brs.append(ms)
        kb = len(raw) / 1024
    out["state_browser_ms"], out["state_browser_kb"] = _pct(brs), round(kb, 1)
    try:
        out["lanes_ms"] = _pct([_call(tok, "/api/lanes")[0] for _ in range(max(3, n // 4))])
    except urllib.error.HTTPError as e:
        out["lanes_ms"] = f"no /api/lanes on this hub ({e.code})"
    h = json.loads(_call(tok, "/health")[1])
    out["hub"] = {k: h.get(k) for k in ("started_at", "code", "code_stale", "emit_perf")
                  if k in h}
    return out


def turn(tok, lane, cwd):
    rec = {"lane": lane}
    t0 = time.perf_counter()
    ms, raw = _call(tok, "/api/session/new", {"agent": lane, "cwd": cwd,
                                               "background": True,
                                               "title": "perf-probe"})
    pane = json.loads(raw)["pane"]
    pid, seq = pane["id"], pane["seq"]
    rec["spawn_ms"] = round(ms)
    ms, raw = _call(tok, "/api/session/send", {"pane": pid, "text": TURN_PROMPT})
    rec["send_ms"] = round(ms)
    t_send = time.perf_counter()
    first = end = None
    polls, poll_ms = 0, []
    while time.perf_counter() - t_send < 180 and end is None:
        ms, raw = _call(tok, "/api/state?since=" + urllib.parse.quote(json.dumps({pid: seq})))
        polls += 1
        poll_ms.append(ms)
        p = next(x for x in json.loads(raw)["panes"] if x["id"] == pid)
        for e in p["events"]:
            seq = max(seq, e["seq"])
            if e["kind"] == "text" and first is None:
                first = time.perf_counter() - t_send
            if e["kind"] == "turn_end":
                end = time.perf_counter() - t_send
                rec["tokens_used"] = (e["data"].get("usage") or {}).get("used")
        if end is None:
            time.sleep(0.2)
    rec["first_text_ms"] = round(first * 1000) if first else None
    rec["turn_end_ms"] = round(end * 1000) if end else None
    rec["polls"], rec["poll_ms"] = polls, _pct(poll_ms)
    ms, _ = _call(tok, "/api/session/close", {"pane": pid})
    rec["close_ms"] = round(ms)
    rec["total_ms"] = round((time.perf_counter() - t0) * 1000)
    if end is not None:
        # What Corral adds around the model: everything but send-to-turn_end.
        rec["corral_overhead_ms"] = rec["total_ms"] - rec["turn_end_ms"]
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=20, help="samples per latency figure")
    ap.add_argument("--turn", action="store_true", help="also run one trivial prompt")
    ap.add_argument("--lane", default="claude")
    ap.add_argument("--cwd", default=str(Path.home()))
    ap.add_argument("--url", default=BASE,
                    help="hub to probe; its CORRAL_LIGHT_STATE must be this shell's "
                         "(the cookie is minted from that state dir's key)")
    a = ap.parse_args(argv)
    globals()["BASE"] = a.url.rstrip("/")
    tok = auth.mint()
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **sample(tok, a.n)}
    if a.turn:
        out["turn"] = turn(tok, a.lane, a.cwd)
    print(json.dumps(out, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
