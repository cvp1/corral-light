#!/usr/bin/python3
"""lane_matrix — does every lane survive a pause/resume WITH ITS MEMORY?

    python3 lane_matrix.py [--url URL] [--lane L ...] [--cwd DIR] [--no-perm]

For every lane the hub reports available (or the ones named), through the
same hub API a browser uses:

    open -> say "remember the word <random>" -> pause -> resume
         -> say "what word did I ask you to remember?"  -> word back?
         -> (tools lanes) ask for one shell command under the strict
            posture, expect a permission card, REFUSE it  -> round-trip?
         -> close

and print a markdown table. The permission probe is always refused, and the
target file is checked afterwards. Each turn is bounded by TURN_BUDGET_S.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import consult                                          # noqa: E402
from consult import ConsultError                        # noqa: E402

TURN_BUDGET_S = 300         # past this a turn is reported as timed out
POLL_S = 1.0
WORDS = ("amber", "basalt", "cobalt", "dahlia", "ember", "fjord", "garnet",
         "harbor", "indigo", "juniper", "kestrel", "lantern", "marigold",
         "nutmeg", "obsidian", "paprika", "quartz", "saffron", "tundra", "walnut")


def _pane(hub, pid, since):
    st = hub.get("/api/state?since=" + urllib.parse.quote(json.dumps({pid: since})))
    for p in st.get("panes") or []:
        if p["id"] == pid:
            return p
    raise ConsultError(f"pane {pid} vanished")


def turn(hub, pid, text, budget=TURN_BUDGET_S):
    """Send, wait for THIS turn's end (by ledger id), refuse any permission.
    Returns {text, ok, why, permissions, notes}."""
    seq = int(_pane(hub, pid, 1 << 40).get("seq") or 0)
    r = hub.post("/api/session/send", {"pane": pid, "text": text},
                 timeout=consult.HANDSHAKE_S)
    tid = r.get("turn")
    out = {"text": "", "ok": False, "why": None, "permissions": [], "notes": []}
    mine, t0 = False, time.time()
    while time.time() - t0 < budget:
        p = _pane(hub, pid, seq)
        for ev in p.get("events") or []:
            seq = max(seq, ev["seq"])
            k, d = ev["kind"], ev.get("data") or {}
            if k == "user" and d.get("turn") == tid:
                mine = True
            elif not mine:
                continue
            elif k == "text":
                out["text"] += d.get("text") or ""
            elif k == "note":
                out["notes"].append(d.get("text") or "")
            elif k == "turn_end" and d.get("turn") == tid:
                out["ok"] = True
                out["why"] = d.get("stopReason")
                return out
            elif k == "dead":
                out["why"] = f"dead: {d.get('reason')}"
                return out
        if p.get("pending"):
            pend = hub.get("/api/session/pending?pane=" + pid)["pending"]
            for card in pend:
                reject = next((o for o in card.get("options") or []
                               if str(o.get("kind", "")).startswith("reject")), None)
                out["permissions"].append(card.get("title") or card.get("kind"))
                if reject:
                    hub.post("/api/session/permission",
                             {"pane": pid, "requestId": card["requestId"],
                              "optionId": reject["optionId"], "digest": "",
                              "via": "script"})
        if p.get("state") == "dead":
            out["why"] = f"dead: {p.get('error')}"
            return out
        time.sleep(POLL_S)
    try:
        hub.post("/api/session/cancel", {"pane": pid})
    except ConsultError:
        pass
    out["why"] = f"timed out after {budget}s (cancelled)"
    return out


def run_lane(hub, lane, cwd, perm=True):
    row = {"lane": lane["key"], "label": lane.get("label"), "open": "—",
           "model": "—", "first": "—", "resume": "—", "remembered": "—",
           "permission": "n/a" if not lane.get("tools") else "—", "notes": []}
    word = random.choice(WORDS) + str(random.randint(10, 99))
    try:
        p = hub.post("/api/session/new", {"agent": lane["key"], "cwd": str(cwd),
                                          "posture": "strict", "background": True},
                     timeout=consult.HANDSHAKE_S)["pane"]
    except ConsultError as e:
        row["open"] = f"no: {e}"[:80]
        return row
    pid = p["id"]
    if p.get("state") == "dead":
        row["open"] = f"no: {p.get('error')}"[:80]
        return row
    row["open"], row["model"] = "yes", p.get("model") or "(lane default)"
    try:
        hub.post("/api/session/rename", {"pane": pid, "title": f"lane matrix · {lane['key']}"})
        t1 = turn(hub, pid, f"Please remember this word for later: {word}. "
                            f"Reply with just OK.")
        row["first"] = "yes" if t1["ok"] else f"no ({t1['why']})"
        r = hub.post("/api/session/pause", {"pane": pid})
        r = hub.post("/api/session/resume", {"pane": pid}, timeout=consult.HANDSHAKE_S)
        st = (r.get("pane") or {}).get("state")
        row["resume"] = "yes" if st == "ready" else f"no ({st}: {(r.get('pane') or {}).get('error')})"
        lost = [e["data"]["text"] for e in (_pane(hub, pid, 0).get("events") or [])
                if e["kind"] == "note" and e["data"].get("contextLost")]
        if lost:
            row["notes"].append("lane says: context lost on resume")
        if st == "ready":
            t2 = turn(hub, pid, "What word did I ask you to remember? "
                                "Reply with just the word.")
            got = t2["text"].strip()
            row["remembered"] = ("yes" if word.lower() in got.lower() else
                                 f"no (answered {got[:40]!r})" if t2["ok"] else
                                 f"no ({t2['why']})")
        if perm and lane.get("tools") and st == "ready":
            probe = Path(cwd) / f"corral-matrix-{word}"
            t3 = turn(hub, pid, "Use your shell/terminal tool to run exactly this "
                                f"command and nothing else: touch {probe}")
            if t3["permissions"]:
                row["permission"] = "asked, refused"
            elif t3["ok"]:
                row["permission"] = "no card (did not ask)"
            else:
                row["permission"] = f"no ({t3['why']})"
            if probe.exists():
                row["notes"].append("PROBE FILE WAS CREATED")
                probe.unlink()
    except ConsultError as e:
        row["notes"].append(str(e)[:120])
    finally:
        try:
            hub.post("/api/session/close", {"pane": pid})
        except ConsultError:
            pass
    return row


def table(rows, when, url):
    out = [f"Lane matrix — {when} — hub {url}", "",
           "| lane | opens | model | first turn | pause → resume | remembers after resume | permission round-trip | notes |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['lane']} | {r['open']} | {r['model']} | {r['first']} | "
                   f"{r['resume']} | {r['remembered']} | {r['permission']} | "
                   f"{'; '.join(r['notes'])} |")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lane_matrix", description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=consult.DEFAULT_URL)
    ap.add_argument("--lane", action="append", help="only these lanes (repeatable)")
    ap.add_argument("--cwd", default=str(Path.cwd()))
    ap.add_argument("--no-perm", action="store_true", help="skip the permission probe")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    hub = consult.connect(a.url)
    lanes = [x for x in hub.get("/api/state").get("agents") or []
             if x.get("available") and not x["key"].startswith("host:")]
    if a.lane:
        want = {consult.lane_key(x) for x in a.lane}
        lanes = [x for x in lanes if x["key"] in want]
    when = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    rows = []
    for lane in lanes:
        print(f"lane_matrix: {lane['key']} …", file=sys.stderr, flush=True)
        rows.append(run_lane(hub, lane, Path(a.cwd).resolve(), not a.no_perm))
    print(json.dumps(rows, indent=2) if a.json else table(rows, when, a.url), flush=True)
    return 0 if rows and all(r["remembered"] == "yes" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
