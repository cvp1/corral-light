#!/usr/bin/python3
"""cli — Corral Light from a terminal, on every lane. Stdlib only.

    corral-light panes                         list panes: state, lane, model, pending
    corral-light open --lane L [--cwd D] [--model M] [--effort E] [--posture P] [--title T]
    corral-light say <pane> [text…]            send, stream the reply, answer permissions here
    corral-light watch <pane>                  follow a pane live (Ctrl-C stops watching)
    corral-light pending <pane>                the full payload + digest of every waiting card
    corral-light ok <pane> [n] [--digest D]    approve card n (payload printed first)
    corral-light no <pane> [n]                 refuse card n
    corral-light cancel|pause|resume|close|forget|reopen <pane>
    corral-light rename <pane> <title…>
    corral-light config <pane> <id> <value>    model / effort / fast
    corral-light attach <pane> <note-id>       composer text for a note (printed, not sent)
    corral-light quote <from> [<to>]           composer text quoting a pane's last answer

A CLIENT of the running hub, exactly like the browser: every verb is the same
route a click uses, so a pane opened here is an ordinary pane on the wall and
nothing bypasses its permission rail. Pairing and the cached session are
consult.py's (`Hub`, self-pairing on the hub host), re-paired once on a 401.

THE PERMISSION RULES (resilience review v2, CLI section; Astra and Grok
2026-09-28), which is why `say` is not built on consult.wait_turn:
  - a turn is NEVER cancelled on a timer. consult cancels on its budget and
    on a transport error, which in a foreground terminal kills the very turn
    the human was about to approve. `say` waits for as long as the turn
    takes; Ctrl-C DETACHES (the turn keeps running on the wall) and only
    the `cancel` verb, or answering `cancel` at a card, cancels.
  - when the pane needs you, the FULL pending payload is printed — every
    byte the digest covers, from /api/session/pending (the authoritative
    record, not the event ring) — and ok / no / cancel is taken on the SAME
    terminal. An approval carries the digest of what was printed (P17).
  - `ok` from a script (no TTY) must name the digest (--digest, a prefix of
    at least DIGEST_MIN hex chars) of the payload it prints; refusing never
    needs one, because refusal is the fail-closed direction.
"""
from __future__ import annotations

import argparse
import http.client
import json
import queue
import sys
import threading
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import consult                                          # noqa: E402
from consult import ConsultError                        # noqa: E402

DIGEST_MIN = 12             # hex chars of the sha256 a script must name to approve:
                            # 48 bits — no accidental match, still pasteable
SEEN_EVERY_S = 1.0          # at most one "a human saw this" report per second
STREAM_RETRY_S = 2.0        # reconnect delay when the event stream drops
STREAM_UP_S = 10            # wait this long for the stream before backfilling
                            # anyway; the per-second backfill covers the rest
MAX_SHOW_CHARS = 262_144    # printing cap for one payload field; the hub already
                            # refuses to offer approval past MAX_PERM_BYTES


class Cli:
    def __init__(self, url, out=None, inp=None, interactive=None):
        self.url = url
        self.out = out or sys.stdout
        self.inp = inp or sys.stdin
        self.hub = None
        self.interactive = (self.inp.isatty() if interactive is None
                            else interactive)

    # ── transport ────────────────────────────────────────────────────────
    def connect(self):
        if self.hub is None:
            self.hub = consult.connect(self.url)
        return self.hub

    def _retry(self, fn, *a, **k):
        """One re-pair on a 401 (Astra 2026-09-28, K10: consult re-paired on
        the FIRST call only; a 12 h cookie expiring mid-session just raised)."""
        try:
            return fn(*a, **k)
        except ConsultError as e:
            if str(e) != "not paired":
                raise
            self.hub.token = None
            consult.pair(self.hub)
            return fn(*a, **k)

    def get(self, path, timeout=consult.HTTP_TIMEOUT_S):
        self.connect()
        return self._retry(self.hub.get, path, timeout=timeout)

    def post(self, path, body, timeout=consult.HTTP_TIMEOUT_S):
        self.connect()
        return self._retry(self.hub.post, path, body, timeout=timeout)

    def say(self, *parts, end="\n"):
        self.out.write(" ".join(str(p) for p in parts) + end)
        self.out.flush()

    def pane(self, pid):
        st = self.get("/api/state?since=" +
                      urllib.parse.quote(json.dumps({pid: 1 << 40})))
        for p in st.get("panes") or []:
            if p["id"] == pid:
                return p
        # A prefix is enough, like git: panes are 12 hex chars.
        hits = [p for p in st.get("panes") or [] if p["id"].startswith(pid)]
        if len(hits) == 1:
            return hits[0]
        raise ConsultError(f"no pane {pid!r}" if not hits else
                           f"{pid!r} matches {len(hits)} panes — use more of the id")

    def events_since(self, pid, seq):
        st = self.get("/api/state?since=" + urllib.parse.quote(json.dumps({pid: seq})))
        for p in st.get("panes") or []:
            if p["id"] == pid:
                return p, p.get("events") or []
        raise ConsultError(f"pane {pid} is gone")

    # ── stream ───────────────────────────────────────────────────────────
    def stream(self, sink, stop, up=None):
        """Feed every SSE event into `sink` until `stop` is set. Reconnects;
        a reconnect is reported as a `resync` so the reader backfills. `up`
        is set once the hub has answered the stream request, so the caller
        can backfill AFTER it — the browser's stream-then-snapshot order."""
        first = True
        while not stop.is_set():
            h = self.connect()
            conn = h._conn(timeout=60)
            try:
                conn.request("GET", "/api/stream", headers=h._headers())
                r = conn.getresponse()
                if r.status == 401:
                    self.hub.token = None
                    consult.pair(self.hub)
                    continue
                if not first:
                    sink.put({"kind": "resync", "pane": None, "seq": 0})
                first = False
                if up is not None:
                    up.set()
                while not stop.is_set():
                    line = r.fp.readline()
                    if not line:
                        break
                    line = line.decode("utf-8", "replace").strip()
                    if line.startswith("data: "):
                        try:
                            sink.put(json.loads(line[6:]))
                        except ValueError:
                            pass
            except (OSError, http.client.HTTPException, ConsultError):
                pass
            finally:
                conn.close()
            if not stop.is_set():
                time.sleep(STREAM_RETRY_S)

    # ── rendering ────────────────────────────────────────────────────────
    def render(self, ev, state):
        k, d = ev.get("kind"), ev.get("data") or {}
        if k == "text":
            if not state.get("in_text"):
                state["in_text"] = True
            self.out.write(d.get("text") or "")
            self.out.flush()
            return
        if state.get("in_text"):
            self.say("")
            state["in_text"] = False
        if k == "user":
            self.say(f"\n› {d.get('text', '')}")
        elif k == "peer":
            # Another pane's agent, not the human (DESIGN-5 S7): never the `›`
            # the operator's own lines wear.
            self.say(f"\n⇄ from @{d.get('from_seat') or d.get('from_pane') or '?'}: "
                     f"{d.get('text', '')}")
        elif k == "peer_result" and d.get("delivered") is False:
            self.say(f"  · that message was not run: {d.get('reason') or 'unknown'}")
        elif k == "seat":
            self.say(f"  · seat @{d['seat']}" if d.get("seat") else "  · seat removed")
        elif k == "tool":
            status = d.get("status") or ""
            self.say(f"  ⚙ {d.get('title') or d.get('kind') or 'tool'}"
                     + (f" [{status}]" if status else ""))
        elif k in ("note", "dead", "closed", "cancelled", "resumed", "paused",
                   "permission_answered", "permission_expired"):
            txt = d.get("text") or d.get("reason") or ""
            self.say(f"  · {k}" + (f": {txt}" if txt else ""))
        elif k == "turn_end":
            self.say(f"  · turn ended ({d.get('stopReason') or 'end_turn'})")
        elif k == "permission":
            self.say(f"  ! permission requested: {d.get('title') or d.get('kind')}")

    def mark_seen(self, pid, seq, state):
        """The terminal in the foreground IS a human looking (P0-e')."""
        if not getattr(self.out, "isatty", lambda: False)():
            return                     # piped output: nobody is necessarily looking
        now = time.time()
        if seq and now - state.get("seen_at", 0) >= SEEN_EVERY_S:
            state["seen_at"] = now
            try:
                self.post("/api/session/seen", {"pane": pid, "seq": seq})
            except ConsultError:
                pass

    # ── permissions ──────────────────────────────────────────────────────
    def pending(self, pid):
        return self.get("/api/session/pending?pane=" + urllib.parse.quote(pid))["pending"]

    def show_card(self, i, card):
        self.say(f"\n── card {i}: {card.get('title') or card.get('kind') or 'tool call'} "
                 f"({card.get('kind') or '?'}) ──")
        if card.get("oversize"):
            self.say(f"  too large to display ({card.get('bytes')} bytes) — it can "
                     f"only be REFUSED; an approval proves only what you could see")
        else:
            for label, key in (("input", "rawInput"), ("content", "content"),
                               ("locations", "locations")):
                val = card.get(key)
                if val in (None, [], {}):
                    continue
                s = json.dumps(val, indent=2, ensure_ascii=False)
                if len(s) > MAX_SHOW_CHARS:
                    s = s[:MAX_SHOW_CHARS] + f"\n… ({len(s)} chars; not all shown)"
                self.say(f"  {label}:")
                for line in s.splitlines():
                    self.say("    " + line)
        self.say(f"  digest: {card.get('digest')}")
        for j, o in enumerate(card.get("options") or [], 1):
            self.say(f"  option {j}: {o.get('name')} ({o.get('kind')}) [{o.get('optionId')}]")

    @staticmethod
    def option(card, grant):
        opts = card.get("options") or []
        want = (lambda k: not k.startswith("reject")) if grant else \
            (lambda k: k.startswith("reject"))
        for o in opts:
            if want(str(o.get("kind", ""))):
                return o
        return None

    def answer(self, pid, card, grant):
        o = self.option(card, grant)
        if o is None:
            raise ConsultError("this card offers no " + ("approve" if grant else "refuse")
                               + " option")
        if grant and card.get("oversize"):
            raise ConsultError("too large to display, so it cannot be approved here")
        r = self.post("/api/session/permission",
                      {"pane": pid, "requestId": card["requestId"],
                       "optionId": o["optionId"],
                       "digest": card.get("digest") if grant else ""})
        return o, r

    def take_cards(self, pid):
        """Print every waiting card IN FULL and answer each on this terminal.
        Returns "cancelled" when the operator cancels the turn."""
        cards = self.pending(pid)
        for i, card in enumerate(cards, 1):
            self.show_card(i, card)
            while True:
                self.out.write("  answer [ok / no / cancel] > ")
                self.out.flush()
                line = self.inp.readline()
                if not line:                          # EOF: nobody to ask
                    self.say("\n  (no terminal input — the card stays waiting; "
                             f"answer with `corral-light ok|no {pid}`)")
                    return "unanswered"
                a = line.strip().lower()
                if a in ("ok", "y", "yes", "allow"):
                    o, _ = self.answer(pid, card, True)
                    self.say(f"  approved: {o.get('name')}")
                    break
                if a in ("no", "n", "deny", "refuse"):
                    o, _ = self.answer(pid, card, False)
                    self.say(f"  refused: {o.get('name')}")
                    break
                if a == "cancel":
                    self.post("/api/session/cancel", {"pane": pid})
                    self.say("  turn cancelled")
                    return "cancelled"
        return "answered"

    # ── the foreground turn ──────────────────────────────────────────────
    def follow(self, pid, turn=None, seq0=0, until_turn_end=True):
        """Stream pane `pid` from seq0. With `turn`, return when THAT turn
        ends (by ledger id). Never cancels on a clock (see module doc)."""
        sink, stop, up = queue.Queue(), threading.Event(), threading.Event()
        th = threading.Thread(target=self.stream, args=(sink, stop, up), daemon=True)
        th.start()
        state, last = {}, seq0
        mine = turn is None
        asked = set()
        try:
            # Backfill AFTER the stream is up, so nothing falls in a gap
            # (found in the first live run: a fast reply landed between the
            # backfill and the stream connecting, and `say` waited forever).
            up.wait(STREAM_UP_S)
            p, evs = self.events_since(pid, last)
            backlog = list(evs)
            while True:
                if backlog:
                    ev = backlog.pop(0)
                else:
                    try:
                        ev = sink.get(timeout=1.0)
                    except queue.Empty:
                        ev = None
                if ev is not None and ev.get("kind") == "resync":
                    p, backlog = self.events_since(pid, last)
                    continue
                if ev is not None and ev.get("pane") == pid and ev.get("seq", 0) > last:
                    last = ev["seq"]
                    d = ev.get("data") or {}
                    k = ev.get("kind")
                    if k == "user" and turn and d.get("turn") == turn:
                        mine = True
                    if mine or turn is None:
                        self.render(ev, state)
                    self.mark_seen(pid, last, state)
                    if mine and k == "turn_end" and (turn is None or d.get("turn") == turn):
                        if until_turn_end:
                            return 0 if str(d.get("stopReason") or "end_turn") \
                                not in consult.CANCEL_REASONS else 1
                    if mine and k in ("dead", "closed"):
                        return 1
                    if k == "permission" and self.interactive and \
                            d.get("requestId") not in asked:
                        asked.add(d.get("requestId"))
                        if state.get("in_text"):
                            self.say("")
                            state["in_text"] = False
                        if self.take_cards(pid) == "cancelled" and turn:
                            continue
                elif ev is None:
                    # Quiet second: backfill from the authoritative state (a
                    # safety net under the stream) and re-check it, so a card
                    # that arrived before we attached is still offered.
                    p, more = self.events_since(pid, last)
                    if more:
                        backlog.extend(more)
                        continue
                    if p.get("state") == "dead" and mine:
                        self.say(f"  · dead: {p.get('error') or ''}")
                        return 1
                    if p.get("pending") and self.interactive:
                        fresh = [r for r in p["pending"] if r not in asked]
                        if fresh:
                            asked.update(fresh)
                            self.take_cards(pid)
                    elif p.get("pending") and not state.get("told"):
                        state["told"] = True
                        for i, c in enumerate(self.pending(pid), 1):
                            self.show_card(i, c)
                        self.say(f"  (waiting on you — answer with `corral-light "
                                 f"ok|no {pid}`; this command keeps waiting)")
        except KeyboardInterrupt:
            if turn is None:
                self.say("\n  stopped watching")
                return 0
            self.say(f"\n  detached — the turn keeps running on the wall "
                     f"(`corral-light cancel {pid}` to stop it)")
            return 130
        finally:
            stop.set()


# ── verbs ────────────────────────────────────────────────────────────────
def _text(args):
    if args.text:
        return " ".join(args.text)
    if sys.stdin.isatty():
        raise ConsultError("no text: pass it as arguments or pipe it on stdin")
    return sys.stdin.read()


def v_panes(c, a):
    st = c.get("/api/state")
    rows = st.get("panes") or []
    if a.json:
        c.say(json.dumps([{k: p.get(k) for k in ("id", "state", "agent", "model",
                                                 "effort", "title", "cwd", "pending",
                                                 "resumable", "seat", "seatWithheld")}
                          for p in rows],
                         indent=2))
        return 0
    if not rows:
        c.say("no panes")
    for p in rows:
        pend = len(p.get("pending") or [])
        # The seat column: `@name`, or `(@name withheld)` when another open
        # pane holds it -- shown, so the operator can see why a peer cannot
        # reach this one (DESIGN-5 S6).
        seat = (f"@{p['seat']}" if p.get("seat") else
                f"(@{p['seatWithheld']} withheld)" if p.get("seatWithheld") else "-")
        c.say(f"{p['id']}  {p.get('state', ''):<10} {p.get('agent', ''):<10} "
              f"{(p.get('model') or '-'):<18} {seat:<16} "
              f"{('!' + str(pend)) if pend else '  '}  "
              f"{p.get('title') or ''}")
    if st.get("notRestored"):
        c.say(f"({st['notRestored']} pane(s) were not restored — see the hub log)")
    return 0


def v_open(c, a):
    body = {"agent": consult.lane_key(a.lane),
            "cwd": str(Path(a.cwd).expanduser().resolve())}
    for k in ("model", "effort", "posture", "role"):
        if getattr(a, k, None):
            body[k] = getattr(a, k)
    r = c.post("/api/session/new", body, timeout=consult.HANDSHAKE_S)
    p = r["pane"]
    if p.get("state") == "dead":
        c.say(f"{p['id']} dead: {p.get('error')}")
        return 1
    if a.title:
        c.post("/api/session/rename", {"pane": p["id"], "title": a.title[:60]})
    for n in r.get("notes") or []:
        print(f"corral-light: {n}", file=sys.stderr, flush=True)
    c.say(p["id"] if not a.json else json.dumps(p, indent=2))
    if r.get("preamble"):
        # The role's instructions are turn 0. With --ask they go with it,
        # composed exactly as roles.compose does; without, they are shown and
        # NOT sent — a script must see what it is about to send (P17).
        if a.ask:
            import roles
            text = roles.compose(r["preamble"], a.ask)
            sr = c.post("/api/session/send", {"pane": p["id"], "text": text,
                                              "via": "cli"})
            return c.follow(p["id"], turn=sr.get("turn"), seq0=int(p.get("seq") or 0))
        print(f"corral-light: role instructions NOT sent (pass --ask to send them "
              f"with your first message):\n{r['preamble']}", file=sys.stderr, flush=True)
    return 0


def v_say(c, a):
    p = c.pane(a.pane)
    text = _text(a)
    # `via: cli` -- a turn typed at a terminal is still the human, but it is
    # not the browser, and the transcript says which (DESIGN-5 section 7.11).
    r = c.post("/api/session/send", {"pane": p["id"], "text": text, "via": "cli"},
               timeout=consult.HANDSHAKE_S)       # a dead pane resumes first
    return c.follow(p["id"], turn=r.get("turn"), seq0=int(p.get("seq") or 0))


def v_watch(c, a):
    p = c.pane(a.pane)
    back = max(0, int(p.get("seq") or 0) - a.back)
    c.follow(p["id"], turn=None, seq0=back, until_turn_end=False)
    return 0


def v_pending(c, a):
    p = c.pane(a.pane)
    cards = c.pending(p["id"])
    if a.json:
        c.say(json.dumps(cards, indent=2))
        return 0
    if not cards:
        c.say("nothing waiting")
    for i, card in enumerate(cards, 1):
        c.show_card(i, card)
    return 0


def _pick(c, pid, n):
    cards = c.pending(pid)
    if not cards:
        raise ConsultError("nothing waiting on this pane")
    if n is None:
        if len(cards) > 1:
            for i, card in enumerate(cards, 1):
                c.show_card(i, card)
            raise ConsultError(f"{len(cards)} cards are waiting — name one: "
                               f"`corral-light ok {pid} <n>`")
        n = 1
    if not 1 <= n <= len(cards):
        raise ConsultError(f"card {n} does not exist (1..{len(cards)})")
    return cards[n - 1]


def v_ok(c, a):
    p = c.pane(a.pane)
    card = _pick(c, p["id"], a.n)
    c.show_card(a.n or 1, card)               # printed in full BEFORE approving (P17)
    dg = card.get("digest") or ""
    if a.digest:
        if len(a.digest) < DIGEST_MIN or not dg.startswith(a.digest.lower()):
            raise ConsultError(f"--digest does not match the payload shown "
                               f"(need ≥{DIGEST_MIN} hex chars of {dg[:DIGEST_MIN]}…)")
    elif c.interactive:
        c.out.write("  approve exactly this? [y/N] > ")
        c.out.flush()
        if c.inp.readline().strip().lower() not in ("y", "yes"):
            c.say("  not approved")
            return 1
    else:
        raise ConsultError(f"no terminal to confirm on: re-run with --digest "
                           f"{dg[:DIGEST_MIN]} to approve the payload printed above")
    o, r = c.answer(p["id"], card, True)
    c.say(f"approved: {o.get('name')} (delivered={r.get('delivered')})")
    return 0


def v_no(c, a):
    p = c.pane(a.pane)
    card = _pick(c, p["id"], a.n)
    c.show_card(a.n or 1, card)
    o, r = c.answer(p["id"], card, False)
    c.say(f"refused: {o.get('name')} (delivered={r.get('delivered')})")
    return 0


def _simple(route, key="pane"):
    def run(c, a):
        p = c.pane(a.pane) if route != "reopen" else {"id": a.pane}
        r = c.post(f"/api/session/{route}", {key: p["id"]},
                   timeout=consult.HANDSHAKE_S if route == "resume" else
                   consult.HTTP_TIMEOUT_S)
        snap = r.get("pane") or {}
        c.say(f"{route}: {p['id']}" + (f" -> {snap.get('state')}" if snap else "")
              + (f" ({snap.get('error')})" if snap.get("error") else ""))
        return 0 if r.get("ok", True) else 1
    return run


def v_rename(c, a):
    p = c.pane(a.pane)
    c.say(c.post("/api/session/rename", {"pane": p["id"], "title": " ".join(a.title)})["title"])
    return 0


def v_seat(c, a):
    """Bind a seat (DESIGN-5 S6), or unbind it with `-`. A human verb: this is
    the operator at a terminal, holding the same pairing the browser does."""
    p = c.pane(a.pane)
    name = "" if a.name == "-" else a.name
    r = c.post("/api/session/seat", {"pane": p["id"], "seat": name})
    c.say(f"@{r['seat']}" if r.get("seat") else "unbound")
    return 0


def v_config(c, a):
    p = c.pane(a.pane)
    r = c.post("/api/session/config", {"pane": p["id"], "configId": a.id, "value": a.value})
    c.say(json.dumps(r.get("config"), indent=2))
    return 0


def v_attach(c, a):
    p = c.pane(a.pane)
    r = c.post("/api/content/attach", {"pane": p["id"], "id": a.note})
    c.say(r["text"], end="")                  # composer text: shown, never sent
    return 0


def v_quote(c, a):
    body = {"from": c.pane(a.src)["id"]}
    if a.dst:
        body["pane"] = c.pane(a.dst)["id"]
    r = c.post("/api/session/quote", body)
    c.say(r["text"], end="")
    return 0


def v_search(c, a):
    q = urllib.parse.urlencode({"q": " ".join(a.query), "n": a.limit})
    out = c.get("/api/session/search?" + q)
    for h in out.get("hits") or []:
        c.say(f"{h['at']}  {h['pane']}  {h['title']}{' [archived]' if h['closed'] else ''}"
              f" · {h['agent']} #{h['seq']} ({h['kind']})\n    {h['snippet']}")
    if out.get("partial"):
        c.say(f"(index partial for {', '.join(out['partial'])} — older turns not indexed)")
    if out.get("error"):
        c.say(f"(search error: {out['error']})")
    if not out.get("hits"):
        c.say("(nothing)")
    return 0


def v_digest(c, a):
    c.say(c.get(f"/api/session/digest?hours={a.hours}")["text"], end="")
    return 0


def v_port(c, a):
    """Carry a pane's transcript to another lane: print the EXACT pack and its
    sha, then confirm on the terminal or by --sha (P17), then send."""
    p = c.pane(a.pane)
    lane = consult.lane_key(a.lane)
    pack = c.post("/api/session/port/preview", {"pane": p["id"], "agent": lane})
    c.say(pack["text"])
    c.say(f"\n── {pack['chars']} chars · {pack['turns_carried']}/{pack['turns_total']} "
          f"turns · to {pack['label']} ({pack['vendor']}) · sha {pack['sha']}")
    if a.sha:
        if len(a.sha) < DIGEST_MIN or not pack["sha"].startswith(a.sha.lower()):
            raise ConsultError("--sha does not match the pack printed above")
    elif c.interactive:
        c.out.write("  send exactly this? [y/N] > ")
        c.out.flush()
        if c.inp.readline().strip().lower() not in ("y", "yes"):
            c.say("  not sent")
            return 1
    else:
        raise ConsultError(f"no terminal to confirm on: re-run with --sha "
                           f"{pack['sha'][:DIGEST_MIN]}")
    r = c.post("/api/session/port", {"pane": p["id"], "agent": lane, "sha": pack["sha"]},
               timeout=consult.HANDSHAKE_S)
    c.say(f"{r['pane']['id']} " + ("delivered" if r["delivered"] else
                                   f"NOT delivered: {r['error']}"))
    return 0 if r["delivered"] else 1


def v_later(c, a):
    """Scheduled prompts (schedule.py): list, add, rm."""
    if a.later_cmd == "list":
        jobs = c.get("/api/session/schedule")["jobs"]
        if not jobs:
            c.say("nothing scheduled")
        for j in jobs:
            c.say(f"{j['id']}  {j['at']}  {(j.get('repeat') or 'once'):<6} "
                  f"{j['action']:<6} {j.get('agent') or '':<8} "
                  f"{'FAILED: ' + str(j.get('last_error')) if j.get('failed') else j.get('title')}")
        return 0
    if a.later_cmd == "rm":
        c.say("removed " + c.post("/api/session/schedule/remove", {"id": a.id})["removed"])
        return 0
    body = {"when": a.at, "repeat": a.repeat or "", "prompt": a.prompt or "",
            "action": a.action}
    if a.pane:
        body["pane"] = c.pane(a.pane)["id"]
    else:
        body.update({"agent": consult.lane_key(a.lane or ""),
                     "cwd": str(Path(a.cwd).expanduser().resolve())})
    for k in ("posture", "role", "title"):
        if getattr(a, k):
            body[k] = getattr(a, k)
    j = c.post("/api/session/schedule/add", body)["job"]
    for n in j.get("role_notes") or []:
        print(f"corral-light: {n}", file=sys.stderr, flush=True)
    c.say(f"{j['id']} at {j['at']} ({j.get('repeat') or 'once'})")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="corral-light",
                                 description="Corral Light from a terminal.")
    ap.add_argument("--url", default=consult.DEFAULT_URL)
    ap.add_argument("--interactive", action="store_true",
                    help="take ok/no/cancel answers from stdin even when it is "
                         "not a terminal (a script that pipes its answers)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("panes", help="list panes")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=v_panes)

    s = sub.add_parser("open", help="open a pane on a lane")
    s.add_argument("--lane", required=True)
    s.add_argument("--cwd", default=str(Path.cwd()))
    s.add_argument("--model")
    s.add_argument("--effort")
    s.add_argument("--posture", choices=("strict", "edits", "auto"))
    s.add_argument("--title")
    s.add_argument("--role", help="a role preset (roles.py list)")
    s.add_argument("--ask", help="with --role: send the role's instructions plus "
                                 "this ask as the first turn, and follow it")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=v_open)

    s = sub.add_parser("say", help="send, stream the reply, answer permissions here")
    s.add_argument("pane")
    s.add_argument("text", nargs="*")
    s.add_argument("--interactive", action="store_true", dest="say_interactive",
                   help="read ok/no/cancel from stdin even when it is piped")
    s.set_defaults(fn=v_say)

    s = sub.add_parser("watch", help="follow a pane live")
    s.add_argument("pane")
    s.add_argument("--back", type=int, default=40, help="events of backlog to show")
    s.set_defaults(fn=v_watch)

    s = sub.add_parser("pending", help="full payload + digest of every waiting card")
    s.add_argument("pane")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=v_pending)

    s = sub.add_parser("ok", help="approve a waiting card (payload printed first)")
    s.add_argument("pane")
    s.add_argument("n", nargs="?", type=int)
    s.add_argument("--digest", help=f"≥{DIGEST_MIN} hex chars of the printed digest "
                                    f"(required without a terminal)")
    s.set_defaults(fn=v_ok)

    s = sub.add_parser("no", help="refuse a waiting card")
    s.add_argument("pane")
    s.add_argument("n", nargs="?", type=int)
    s.set_defaults(fn=v_no)

    for verb in ("cancel", "pause", "resume", "close", "forget", "reopen"):
        s = sub.add_parser(verb, help=f"{verb} a pane")
        s.add_argument("pane")
        s.set_defaults(fn=_simple(verb))

    s = sub.add_parser("rename", help="rename a pane")
    s.add_argument("pane")
    s.add_argument("title", nargs="+")
    s.set_defaults(fn=v_rename)

    s = sub.add_parser("seat", help="name a pane so other panes can address it "
                                     "(`-` unbinds)")
    s.add_argument("pane")
    s.add_argument("name")
    s.set_defaults(fn=v_seat)

    s = sub.add_parser("config", help="set model / effort / fast")
    s.add_argument("pane")
    s.add_argument("id")
    s.add_argument("value")
    s.set_defaults(fn=v_config)

    s = sub.add_parser("attach", help="composer text for a note (printed, not sent)")
    s.add_argument("pane")
    s.add_argument("note")
    s.set_defaults(fn=v_attach)

    s = sub.add_parser("quote", help="composer text quoting a pane's last answer")
    s.add_argument("src")
    s.add_argument("dst", nargs="?")
    s.set_defaults(fn=v_quote)

    s = sub.add_parser("port", help="carry a pane's transcript to another lane")
    s.add_argument("pane")
    s.add_argument("--lane", required=True)
    s.add_argument("--sha", help=f"≥{DIGEST_MIN} hex chars of the printed sha "
                                 f"(required without a terminal)")
    s.set_defaults(fn=v_port)

    s = sub.add_parser("search", help="full-text search of what was said in any pane")
    s.add_argument("query", nargs="+")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=v_search)

    s = sub.add_parser("digest", help="mechanical what-the-agents-did (no model)")
    s.add_argument("--hours", type=float, default=24)
    s.set_defaults(fn=v_digest)

    s = sub.add_parser("later", help="scheduled prompts: list | add | rm")
    lsub = s.add_subparsers(dest="later_cmd", required=True)
    lsub.add_parser("list")
    x = lsub.add_parser("rm")
    x.add_argument("id")
    x = lsub.add_parser("add")
    x.add_argument("--at", required=True, help="local YYYY-MM-DDTHH:MM, or …Z for UTC")
    x.add_argument("--repeat", choices=("daily", "weekly"))
    x.add_argument("--lane")
    x.add_argument("--cwd", default=str(Path.cwd()))
    x.add_argument("--pane", help="land on this existing pane (with --action nudge|resume)")
    x.add_argument("--action", default="start", choices=("start", "nudge", "resume"))
    x.add_argument("--prompt")
    x.add_argument("--posture", choices=("strict", "edits", "auto"))
    x.add_argument("--role")
    x.add_argument("--title")
    s.set_defaults(fn=v_later)

    a = ap.parse_args(argv)
    c = Cli(a.url, interactive=True if (a.interactive or getattr(
        a, "say_interactive", False)) else None)
    try:
        return a.fn(c, a)
    except ConsultError as e:
        print(f"corral-light: {e}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
