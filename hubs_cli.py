#!/usr/bin/python3
"""hubs_cli — link Corral Light hubs: see, take over and delegate work.

    corral-light hubs                              this hub, its peers, inbox, transfers
    corral-light hubs enable --bind ADDR [--port N] [--name NAME]
    corral-light hubs disable
    corral-light hubs invite [--allow see,watch,takeover,delegate] [--ttl S]
    corral-light hubs join ADDR CODE [--allow …]   pair with the hub whose invite showed CODE
    corral-light hubs join TOKEN [--allow …]       ...or with its full token
    corral-light hubs grant PEER --allow …         what PEER may do HERE (replaces)
    corral-light hubs forget PEER                  unpair (deletes the key here)
    corral-light hubs addr PEER ADDR [--port N]    the peer moved to a new address
    corral-light hubs ls [PEER]                    what the other hub(s) are working on
    corral-light hubs show PEER PANE               a pane's recent turns and its diff
    corral-light hubs take PEER PANE [--cwd REPO] [--lane L] [--interrupt] [--no-send]
    corral-light hubs fetch TRANSFER [--cwd REPO] [--lane L]   collect a lost takeover
    corral-light hubs reclaim TRANSFER             take back a pane not yet confirmed
    corral-light hubs offer PEER [--lane L] [--cwd-hint D] [--title T] [text…]
    corral-light hubs offers [OFFER]               offers this hub sent, and their state
    corral-light hubs withdraw OFFER
    corral-light hubs inbox                        offers other hubs sent here
    corral-light hubs accept OFFER [--cwd D] [--lane L]
    corral-light hubs decline OFFER

Grants are what a peer may do on THIS hub; each operator sets their own side.
A client of the local hub, like the rest of the CLI.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import consult                                          # noqa: E402
from cli import Cli                                     # noqa: E402
from consult import ConsultError                        # noqa: E402

LONG = 240          # seconds: a takeover may bundle a large repository


def _q(**kw):
    return "?" + urllib.parse.urlencode({k: v for k, v in kw.items() if v is not None})


def _age(iso):
    if not iso:
        return "never"
    import time
    try:
        t = time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except ValueError:
        return iso
    s = int(time.time() - t)
    for n, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if s >= n:
            return f"{s // n}{unit} ago"
    return f"{s}s ago"


def v_status(c, a):
    st = c.get("/api/hubs")
    if a.json:
        c.say(json.dumps(st, indent=2))
        return 0
    if not st.get("enabled"):
        c.say("hub links: off  (turn on: corral-light hubs enable --bind <LAN or tailnet address>)")
    else:
        c.say(f"hub links: on  {st.get('name')} ({st.get('id')})  "
              f"{st.get('bind')}:{st.get('port')}  "
              f"{'listening' if st.get('listening') else 'NOT listening — see the hub log'}  "
              f"cert {st.get('fingerprint')}")
    peers = st.get("peers") or []
    if peers:
        c.say("\npeers:")
        for p in peers:
            err = f"  ! {p['lastError']}" if p.get("lastError") else ""
            c.say(f"  {p['name']:<16} {p['id']}  {p['addr']}:{p['port']}  "
                  f"seen {_age(p.get('lastSeen'))}{err}")
            c.say(f"  {'':<16} it may here: {', '.join(p.get('theyMay') or []) or 'nothing'}"
                  f"   we may there: {', '.join(p.get('weMay') or []) if p.get('weMay') is not None else '?'}")
    inbox = [o for o in st.get("inbox") or [] if o.get("state") == "offered"]
    if inbox:
        c.say("\ninbox (accept or decline):")
        for o in inbox:
            c.say(f"  {o['offer'][:8]}  from {o.get('fromName')}: {o.get('title')}")
    out = [o for o in st.get("outbox") or [] if o.get("state") in ("queued", "offered", "accepted")]
    if out:
        c.say("\noffers sent:")
        for o in out:
            c.say(f"  {o['offer'][:8]}  to {o.get('toName')}: {o.get('state'):<9} {o.get('title')}")
    ho = [t for t in st.get("handedOut") or [] if t.get("state") == "offered"]
    if ho:
        c.say("\nhanded out, not yet confirmed (reclaim possible):")
        for t in ho:
            c.say(f"  {t['transfer']}  pane {t['pane']} to {t.get('toName')}: {t.get('title')}")
    return 0


def v_enable(c, a):
    st = c.post("/api/hubs/enable", {"bind": a.bind, "port": a.port})
    if a.name:                 # only once the listener is up: a failed enable renames nothing
        st = c.post("/api/hubs/name", {"name": a.name})
    c.say(f"hub links on: {st.get('name')} listening on {st.get('bind')}:{st.get('port')}")
    c.say(f"certificate {st.get('fingerprint')}")
    c.say("next: `corral-light hubs invite` here, then `corral-light hubs join <this "
          "address> <code>` on the other hub (its firewall and this one's must allow "
          f"port {st.get('port')})")
    return 0


def v_disable(c, a):
    c.post("/api/hubs/disable", {})
    c.say("hub links off: the listener is closed. Peers stay paired; "
          "`corral-light hubs forget PEER` unpairs.")
    return 0


def v_invite(c, a):
    r = c.post("/api/hubs/invite", {"grants": a.allow, "ttl": a.ttl})
    c.say(f"Pairing code:  {r['code']}      (good for {r['expiresIn'] // 60} minutes, once)\n")
    c.say("On the other hub run:\n")
    port = "" if r["port"] == 8097 else f" --port {r['port']}"
    c.say(f"  corral-light hubs join {r['addr']} {r['code']}{port}\n")
    c.say(f"or, if you can copy and paste between the machines:\n\n  corral-light hubs join {r['token']}\n")
    c.say(f"This hub's certificate: {r['fingerprint']}  (the other side prints what it pinned)")
    c.say(f"It may do here: {', '.join(r['grants']) or 'nothing'} "
          f"(change later with `corral-light hubs grant`).")
    c.say("Keep the code to yourself until it is used.")
    return 0


def v_join(c, a):
    if a.target.startswith("clhub1:"):
        body = {"token": a.target}
    else:
        if not a.code:
            raise ConsultError("join ADDRESS needs the code the other hub's invite showed")
        body = {"addr": a.target, "code": a.code, "port": a.port}
    body["grants"] = a.allow
    r = c.post("/api/hubs/join", body, timeout=30)
    c.say(f"paired with {r['name']} ({r['peer']})")
    c.say(f"its certificate: {r.get('fingerprint')}  — the same as its invite showed")
    c.say(f"check code: {r['sas']}")
    c.say(f"it may do here: {', '.join(r['theyMay']) or 'nothing'}")
    c.say(f"we may do there: {', '.join(r['weMay']) or 'nothing'}")
    return 0


def v_grant(c, a):
    r = c.post("/api/hubs/grant", {"peer": a.peer, "grants": a.allow})
    c.say(f"{a.peer} may now do here: {', '.join(r['theyMay']) or 'nothing'}")
    return 0


def v_forget(c, a):
    c.post("/api/hubs/forget", {"peer": a.peer})
    c.say(f"unpaired {a.peer}: its key is deleted here")
    return 0


def v_addr(c, a):
    c.post("/api/hubs/addr", {"peer": a.peer, "addr": a.addr, "port": a.port})
    c.say(f"{a.peer} is now dialled at {a.addr}")
    return 0


def _show_roster(c, h):
    head = h.get("name") or h.get("peer")
    if not h.get("reachable", True):
        c.say(f"{head}: not answering ({h.get('lastError')}); last seen "
              f"{_age(h.get('lastSeen'))}" + (" — showing what it had then" if h.get("panes") else ""))
    else:
        c.say(f"{head}:")
    panes = h.get("panes") or []
    if not panes:
        c.say("  (no panes)")
    for p in panes:
        wt = p.get("worktree") or {}
        br = f" ⎇ {wt['branch']}" if wt.get("branch") else ""
        flags = (f" !{p['pending']}" if p.get("pending") else "") + (" ?asking" if p.get("asking") else "")
        c.say(f"  {p['id']}  {(p.get('display') or p.get('state') or ''):<10} "
              f"{p.get('agent', ''):<8} idle {p.get('idleS', 0)}s{flags}  "
              f"{p.get('title') or ''}  [{p.get('cwd') or ''}{br}]")
    for t in h.get("handedOut") or []:
        c.say(f"  ({t['pane']} handed to {t.get('to')}: {t.get('state')})")


def v_ls(c, a):
    if a.peer:
        hubs = [c.get("/api/hubs/roster" + _q(peer=a.peer), timeout=30)]
    else:
        hubs = c.get("/api/hubs/roster", timeout=60).get("hubs") or []
    if a.json:
        c.say(json.dumps(hubs, indent=2))
        return 0
    if not hubs:
        c.say("no paired hubs (corral-light hubs invite / join)")
    for h in hubs:
        _show_roster(c, h)
    return 0


def v_show(c, a):
    r = c.get("/api/hubs/pane" + _q(peer=a.peer, pane=a.pane), timeout=40)
    if a.json:
        c.say(json.dumps(r, indent=2))
        return 0
    c.say(f"{r.get('title')}  ({r.get('agent')}, {r.get('state')})")
    for t in r.get("turns") or []:
        c.say(f"\n▸ {t.get('from', 'operator')}: {t.get('ask', '').strip()[:2000]}")
        if t.get("answer"):
            c.say(f"◂ {t['answer'].strip()}")
        for tool in t.get("tools") or []:
            c.say(f"  ⏺ {tool}")
    if r.get("question"):
        c.say(f"\nasking: {r['question']}")
    for card in r.get("pending") or []:
        c.say(f"waiting on a card there: {card.get('title')} (answer it on that hub)")
    code = r.get("code")
    if code:
        c.say(f"\ncode: branch {code.get('branch') or '(detached)'}, changes against "
              f"{code.get('against')}:")
        c.say(code.get("stat") or "  (no changes)")
        if code.get("untracked"):
            c.say("untracked: " + ", ".join(code["untracked"][:30]))
        if a.diff and code.get("diff"):
            c.say(code["diff"])
        elif code.get("diff"):
            c.say("(add --diff for the full diff)")
    return 0


def _landed(c, r):
    c.say(f"took over “{r.get('title')}” from {r.get('fromName')} (transfer {r['transfer']})")
    if r.get("live"):
        c.say(f"  live pane here: {r['live']}  (corral-light watch {r['live']})")
    if r.get("archived"):
        c.say(f"  transcript archived here as pane {r['archived']}")
    if r.get("workDir"):
        c.say(f"  code: {r['workDir']}  branch {r.get('branch')}")
    for n in r.get("notes") or []:
        c.say(f"  note: {n}")
    return 0


def v_take(c, a):
    cwd = str(Path(a.cwd).expanduser().resolve()) if a.cwd else None
    r = c.post("/api/hubs/take", {"peer": a.peer, "pane": a.pane, "interrupt": a.interrupt,
                                  "lane": a.lane, "cwd": cwd, "send": not a.no_send},
               timeout=LONG)
    return _landed(c, r)


def v_fetch(c, a):
    cwd = str(Path(a.cwd).expanduser().resolve()) if a.cwd else None
    r = c.post("/api/hubs/fetch", {"transfer": a.transfer, "lane": a.lane, "cwd": cwd},
               timeout=LONG)
    return _landed(c, r)


def v_reclaim(c, a):
    r = c.post("/api/hubs/reclaim", {"transfer": a.transfer})
    c.say(f"reclaimed; {r.get('next')}")
    return 0


def v_offer(c, a):
    text = " ".join(a.text) if a.text else (sys.stdin.read() if not sys.stdin.isatty() else "")
    if not text.strip():
        raise ConsultError("describe the work: as arguments or on stdin")
    r = c.post("/api/hubs/offer", {"peer": a.peer, "prompt": text, "title": a.title,
                                   "lane": a.lane, "cwdHint": a.cwd_hint}, timeout=30)
    if r.get("state") == "queued":
        c.say(f"offer {r['offer'][:8]} queued: {r.get('toName')} is not answering "
              f"({r.get('lastError')}). It is retried until it lands.")
    else:
        c.say(f"offer {r['offer'][:8]} {r.get('state')} at {r.get('toName')}"
              + (f": {r.get('lastError')}" if r.get("lastError") else ""))
    c.say(f"follow it: corral-light hubs offers {r['offer'][:8]}")
    return 0


def _find_offer(c, ref):
    out = c.get("/api/hubs").get("outbox") or []
    hits = [o for o in out if o["offer"].startswith(ref)]
    if len(hits) != 1:
        raise ConsultError("no such offer" if not hits else "that matches several offers")
    return hits[0]["offer"]


def v_offers(c, a):
    if a.offer:
        r = c.get("/api/hubs/offer" + _q(offer=_find_offer(c, a.offer)), timeout=30)
        if a.json:
            c.say(json.dumps(r, indent=2))
            return 0
        c.say(f"{r['offer'][:8]} to {r.get('toName')}: {r.get('state')}  {r.get('title')}")
        pane = (r.get("remote") or {}).get("pane")
        if pane:
            c.say(f"  pane {pane['id']} there: {pane.get('state')}, idle {pane.get('idleS')}s"
                  + (f", {pane['pending']} card(s) waiting there" if pane.get("pending") else ""))
            if pane.get("answer"):
                c.say(("\n" if pane.get("answerComplete") else "\n(still writing)\n")
                      + pane["answer"])
        if r.get("lastError"):
            c.say(f"  {r['lastError']}")
        return 0
    for o in c.get("/api/hubs").get("outbox") or []:
        c.say(f"{o['offer'][:8]}  {o.get('state'):<9} to {o.get('toName')}: {o.get('title')}")
    return 0


def v_withdraw(c, a):
    c.post("/api/hubs/offer/withdraw", {"offer": _find_offer(c, a.offer)})
    c.say("withdrawn")
    return 0


def v_inbox(c, a):
    inbox = c.get("/api/hubs").get("inbox") or []
    if a.json:
        c.say(json.dumps(inbox, indent=2))
        return 0
    if not inbox:
        c.say("inbox empty")
    for o in inbox:
        c.say(f"{o['offer'][:8]}  {o.get('state'):<9} from {o.get('fromName')} "
              f"({o.get('at')}): {o.get('title')}"
              + (f"  lane {o['lane']}" if o.get("lane") else "")
              + (f"  cwd {o['cwdHint']}" if o.get("cwdHint") else ""))
        if o.get("state") == "offered":
            c.say("    " + o.get("prompt", "").strip().replace("\n", "\n    ")[:3000])
    return 0


def v_accept(c, a):
    cwd = str(Path(a.cwd).expanduser().resolve()) if a.cwd else None
    r = c.post("/api/hubs/accept", {"offer": a.offer, "cwd": cwd, "lane": a.lane}, timeout=60)
    c.say(f"accepted: pane {r['pane']} has the work  (corral-light watch {r['pane']})")
    return 0


def v_decline(c, a):
    c.post("/api/hubs/decline", {"offer": a.offer})
    c.say("declined")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="corral-light hubs",
                                 description="Link Corral Light hubs: see, take over "
                                             "and delegate work.")
    ap.add_argument("--url", default=consult.DEFAULT_URL)
    sub = ap.add_subparsers(dest="cmd")
    allow = dict(default="see", help="comma list of: see, watch, takeover, delegate "
                                     "(default: see)")

    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=v_status)
    s = sub.add_parser("enable", help="listen for paired hubs on one address")
    s.add_argument("--bind", required=True, help="this machine's LAN or tailnet IP")
    s.add_argument("--port", type=int, default=8097)
    s.add_argument("--name", help="what other hubs call this one")
    s.set_defaults(fn=v_enable)
    s = sub.add_parser("disable")
    s.set_defaults(fn=v_disable)
    s = sub.add_parser("invite", help="a one-time code another hub joins with")
    s.add_argument("--allow", **allow)
    s.add_argument("--ttl", type=int, default=600, help="seconds the code lives (60-1800)")
    s.set_defaults(fn=v_invite)
    s = sub.add_parser("join", help="join ADDRESS CODE, or join TOKEN")
    s.add_argument("target", help="the other hub's IP address, or its clhub1: token")
    s.add_argument("code", nargs="?", help="the code its invite showed")
    s.add_argument("--port", type=int, default=8097)
    s.add_argument("--allow", **allow)
    s.set_defaults(fn=v_join)
    s = sub.add_parser("grant")
    s.add_argument("peer")
    s.add_argument("--allow", required=True, help="see, watch, takeover, delegate; "
                                                  "'' for nothing")
    s.set_defaults(fn=v_grant)
    s = sub.add_parser("forget")
    s.add_argument("peer")
    s.set_defaults(fn=v_forget)
    s = sub.add_parser("addr")
    s.add_argument("peer")
    s.add_argument("addr")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=v_addr)
    s = sub.add_parser("ls", help="what the other hubs are working on")
    s.add_argument("peer", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=v_ls)
    s = sub.add_parser("show", help="a pane's recent turns and diff on another hub")
    s.add_argument("peer")
    s.add_argument("pane")
    s.add_argument("--diff", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=v_show)
    s = sub.add_parser("take", help="take a pane over from another hub")
    s.add_argument("peer")
    s.add_argument("pane")
    s.add_argument("--cwd", help="your checkout of the same repository")
    s.add_argument("--lane", help="continue on this lane (default: the same one)")
    s.add_argument("--interrupt", action="store_true",
                   help="stop its turn if it is mid-turn")
    s.add_argument("--no-send", action="store_true",
                   help="land transcript and code, open no live pane")
    s.set_defaults(fn=v_take)
    s = sub.add_parser("fetch")
    s.add_argument("transfer")
    s.add_argument("--cwd")
    s.add_argument("--lane")
    s.set_defaults(fn=v_fetch)
    s = sub.add_parser("reclaim")
    s.add_argument("transfer")
    s.set_defaults(fn=v_reclaim)
    s = sub.add_parser("offer", help="offer work to another hub")
    s.add_argument("peer")
    s.add_argument("--lane")
    s.add_argument("--cwd-hint")
    s.add_argument("--title")
    s.add_argument("text", nargs="*")
    s.set_defaults(fn=v_offer)
    s = sub.add_parser("offers")
    s.add_argument("offer", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=v_offers)
    s = sub.add_parser("withdraw")
    s.add_argument("offer")
    s.set_defaults(fn=v_withdraw)
    s = sub.add_parser("inbox")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=v_inbox)
    s = sub.add_parser("accept")
    s.add_argument("offer")
    s.add_argument("--cwd")
    s.add_argument("--lane")
    s.set_defaults(fn=v_accept)
    s = sub.add_parser("decline")
    s.add_argument("offer")
    s.set_defaults(fn=v_decline)

    a = ap.parse_args(argv)
    if not a.cmd:
        a = ap.parse_args(["status", *(argv or [])])
    if hasattr(a, "allow") and isinstance(a.allow, str):
        a.allow = [g for g in a.allow.replace(" ", ",").split(",") if g]
    c = Cli(a.url)
    try:
        return a.fn(c, a)
    except ConsultError as e:
        print(f"corral-light hubs: {e}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
