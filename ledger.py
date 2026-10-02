#!/usr/bin/python3
"""ledger — a durable, bounded record of every turn a pane accepted.

`accepted` is fsynced before the send is acknowledged; every later edge is
appended as it happens (append-only JSONL, one file per pane).

STATES, one line per edge, keyed by turn id
    accepted -> dispatched -> completed | interrupted | uncertain

    completed    the agent answered session/prompt (any stopReason, incl.
                 cancelled — that is the agent's own answer)
    interrupted  the turn cannot have finished: the agent died or the pane
                 was paused while it ran, the hub stopped, or it never left
                 the queue. The agent MAY have done part of it.
    uncertain    something in Corral itself failed around the turn; whether
                 the agent ran it is not known.

    Nothing is ever replayed: open turns are marked `interrupted` at the
    next boot (recover()) and surfaced as a note.

Folded back to the newest LEDGER_TURNS turns once past LEDGER_MAX_LINES.
"""
import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

LEDGER_TURNS = 200          # turns kept after a fold
LEDGER_MAX_LINES = LEDGER_TURNS * 5   # ~4 edges per turn plus slack; past this, fold
LEDGER_TEXT_CHARS = 2000    # prompt prefix kept, alongside full length and sha256

OPEN = ("accepted", "dispatched")
TERMINAL = ("completed", "interrupted", "uncertain")
STATES = OPEN + TERMINAL


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TurnLedger:
    """One pane's turns. Thread-safe; every method is best-effort except
    accept(), which raises if the acceptance cannot be made durable."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._lines = None              # counted lazily, once

    # ── writes ──────────────────────────────────────────────────────────
    def _append(self, rec, sync=False):
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                if sync:
                    os.fsync(fh.fileno())
            if self._lines is None:
                self._lines = self._count()
            else:
                self._lines += 1
            if self._lines > LEDGER_MAX_LINES:
                self._fold_locked()

    def accept(self, text, kind=None):
        """Record an accepted turn durably and return its id. Raises OSError
        when that is impossible; the caller must then refuse the send.

        `kind` is "peer" for a message another pane's agent sent."""
        text = text or ""
        tid = uuid.uuid4().hex[:12]
        rec = {"turn": tid, "state": "accepted", "at": _now(),
               "text": text[:LEDGER_TEXT_CHARS], "chars": len(text),
               "sha256": hashlib.sha256(text.encode("utf-8", "replace"))
               .hexdigest()}
        if kind:
            rec["kind"] = kind
        self._append(rec, sync=True)
        return tid

    def mark(self, tid, state, why=None, **extra):
        """Append one edge. Never raises."""
        if not tid or state not in STATES:
            return
        rec = {"turn": tid, "state": state, "at": _now()}
        if why:
            rec["why"] = str(why)[:300]
        rec.update(extra)
        try:
            self._append(rec)
        except OSError:
            pass

    # ── reads ───────────────────────────────────────────────────────────
    def _count(self):
        try:
            with self.path.open("rb") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0

    def turns(self):
        """{turn_id: folded record} in first-seen order; unparseable lines
        are skipped (a torn last line after a crash is expected)."""
        out = {}
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    tid = r.get("turn") if isinstance(r, dict) else None
                    if not tid:
                        continue
                    cur = out.setdefault(tid, {"turn": tid})
                    cur.update(r)
                    cur.setdefault("accepted_at", r.get("at")
                                   if r.get("state") == "accepted" else None)
        except OSError:
            return {}
        return out

    def open_turns(self):
        return [r for r in self.turns().values() if r.get("state") in OPEN]

    def recover(self, why="the hub stopped before this turn finished"):
        """Boot: every turn still `accepted`/`dispatched` is `interrupted`.
        Returns the records it closed (for the pane's note). Never replays."""
        closed = []
        for r in self.open_turns():
            self.mark(r["turn"], "interrupted", why=why,
                      was=r.get("state"))
            closed.append(r)
        return closed

    # ── bound ───────────────────────────────────────────────────────────
    def _fold_locked(self):
        folded = list(self.turns_unlocked().values())[-LEDGER_TURNS:]
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for r in folded:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        self._lines = len(folded)

    # turns() takes no lock; the fold already holds it.
    turns_unlocked = turns


class NullLedger:
    """For panes built without a directory (test stubs). Records nothing."""

    def accept(self, text, kind=None):
        return None

    def mark(self, *a, **k):
        return None

    def turns(self):
        return {}

    def open_turns(self):
        return []

    def recover(self, why=""):
        return []
