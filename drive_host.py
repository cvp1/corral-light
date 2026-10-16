"""The driven side of drive.py: this hub's own panes, opened and worked by a
paired hub that holds the `drive` grant (docs/remote-panes-plan.md).

A driven pane is an ordinary pane here. It runs this machine's lanes with
this machine's logins, in a folder under this user's home, exactly as if the
operator had opened it at this wall, and this wall can see and work it too.
The only extra is `driven_by`, kept in the pane's meta, naming the hub that
opened it and the pairing generation; only that hub, under that pairing,
may work it from afar.
"""
import os
import time
from pathlib import Path

HISTORY_MAX = 500


class HostAdapter:
    def __init__(self, mgr, sessions, pending=None):
        self.mgr, self.s = mgr, sessions
        self.pending = pending or (lambda p: [])

    # What the picker on the driving wall needs, sent with `ready` and again
    # whenever it changes.
    def lanes(self):
        agents, _auth, _at = self.s.AVAIL.read()
        out = []
        for a in agents:
            key = str(a.get("key") or "")
            if key.startswith("host:"):
                continue        # an SSH host lane already covers a plain shell
            out.append({"key": key, "label": a.get("label") or key,
                        "available": bool(a.get("available")),
                        "why": a.get("why") or "",
                        "postureEnforced": a.get("postureEnforced"),
                        "catalog": (self.mgr.catalog or {}).get(key) or {}})
        return {"lanes": out, "postures": sorted(self.s.POSTURES),
                "defaultPosture": self.s.DEFAULT_POSTURE}

    def home(self):
        panes = list(self.mgr.panes.values())
        try:
            sugg = self.s.cwd_suggestions([p.cwd for p in panes])
        except Exception:                                # noqa: BLE001
            sugg = []
        return {"home": str(Path.home()), "defaultCwd": str(self.s.default_cwd()),
                "suggestions": sugg}

    def subscribe(self, q):
        self.mgr.subscribe(q)

    def unsubscribe(self, q):
        self.mgr.unsubscribe(q)

    def owned(self, owner):
        out = []
        for p in list(self.mgr.panes.values()):
            d = getattr(p, "driven_by", None) or {}
            if d.get("hub") == owner["hub"] and d.get("gen") == owner["gen"]:
                out.append(p.id)
        return out

    def snapshot(self, pane, since):
        p = self.mgr.panes.get(pane)
        return p.snapshot(since) if p is not None else None

    # ── commands ─────────────────────────────────────────────────────────
    def op(self, op, args, owner):
        if op == "open":
            return self._open(args, owner)
        p = self.mgr.get(str(args.get("pane") or ""))
        if op in ("send", "answer_question"):
            # A human turn: it also answers an open ask_human question.
            turn = p.send(str(args.get("text") or ""))
            return {"pane": p.id, "turn": turn}
        if op == "answer":
            card = str(args.get("card") or "")
            if not card:
                raise ValueError("an answer from another hub must name the card it answers")
            ok = p.answer(str(args.get("requestId") or ""), str(args.get("optionId") or ""),
                          digest=str(args.get("digest") or ""), via="hub", card=card)
            return {"pane": p.id, "delivered": ok}
        if op == "cancel":
            return {"pane": p.id, "ok": p.cancel()}
        if op == "close":
            self.mgr.close(p.id)
            return {"pane": p.id, "closed": True}
        if op == "resume":
            q = self.mgr.resume(p.id)
            return {"pane": q.id, "state": q.state}
        if op == "pending":
            # The cards in full, from the pane's own records (not the ring).
            return {"pane": p.id, "state": p.state, "pending": self.pending(p)}
        if op == "history":
            n = max(1, min(int(args.get("n") or 200), HISTORY_MAX))
            return {"pane": p.id, "events": p.history(int(args.get("before") or 0), n)}
        raise ValueError(f"unknown command {op!r}")

    def _open(self, args, owner):
        lane = str(args.get("lane") or "")
        if lane.startswith("host:") or lane not in self.s.AGENTS:
            raise ValueError(f"no agent lane {lane!r} on this machine")
        cwd = self.folder(args.get("cwd"))
        posture = args.get("posture") or self.s.DEFAULT_POSTURE
        model = (str(args.get("model") or "")).strip() or None
        effort = (str(args.get("effort") or "")).strip() or None
        title = (str(args.get("title") or "")).strip()[:80] or None
        try:
            pane = self.mgr.create(lane, cwd, posture, model, effort, title=title)
        except Exception:
            self.s.AVAIL.invalidate()
            raise
        pane.driven_by = {"hub": owner["hub"], "gen": owner["gen"],
                          "name": owner.get("name"),
                          "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        pane.save_meta()
        pane.emit("note", {"text": f"opened from hub {owner.get('name') or owner['hub']}"},
                  activity=False)
        text = str(args.get("text") or "")
        turn = pane.send(text) if text.strip() else None
        return {"pane": pane.id, "turn": turn, "cwd": cwd}

    @staticmethod
    def folder(raw):
        """A folder under this user's home, as an SSH login there could cd to.
        Resolved with realpath, so a link cannot lead out of home; it must
        exist; it is never created."""
        home = os.path.realpath(str(Path.home()))
        raw = str(raw or "").strip() or "~"
        p = os.path.expanduser(raw)
        if not os.path.isabs(p):
            p = os.path.join(home, p)
        real = os.path.realpath(p)
        if real != home and not real.startswith(home + os.sep):
            raise ValueError(f"{raw} is not under this machine's home folder")
        if not os.path.isdir(real):
            raise ValueError(f"{raw} is not a folder on this machine")
        return real
