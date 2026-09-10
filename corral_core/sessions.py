#!/usr/bin/python3
"""The pane and manager machinery Corral and Corral Light must not fork.

WHAT IS IN HERE AND WHY IT IS ONLY THIS
    Exactly the code that was **byte-for-byte identical** in both products'
    `sessions.py` on 2026-09-09 — 37 functions plus the bounds they read.
    Nothing was rewritten to make it fit and nothing divergent was unified by
    hand: identity was the entry criterion, so moving it cannot change
    behaviour. What it CAN do is stop the two copies drifting apart, which is
    the actual defect. The sibling `acp.py` merge the same day found five rail
    contract failures that had been shipping in the public product for nine
    days, in code both sides believed was the same code.

    The rest — pane lifecycle details, lane probing, `available_agents`,
    `resume`, `snapshot` — genuinely differs between a fleet console and a
    laptop app, and stays in each product as an override. Unifying a divergent
    body is where a refactor invents behaviour; that work is per-unit
    judgement, not a bulk move.

HOW THE PRODUCTS USE IT
    Each skin subclasses and overrides what differs::

        from corral_core import sessions as _core
        _core.configure(AGENTS=AGENTS, AGENT_GROUPS=AGENT_GROUPS, STATE=STATE)

        class Pane(_core.PaneBase):
            def _init_runtime(self): ...      # this product's version

    `configure()` exists because three module globals legitimately differ —
    the agent roster, its grouping, and the state directory — while the shared
    methods read them by name. Injecting them into THIS module's namespace
    lets the moved bodies stay verbatim; rewriting them to take a config
    object would have meant editing all 37, which is exactly the kind of
    "while we are in here" change that turns a provable move into a rewrite.

    Call `configure()` before constructing a pane. It is not lazy on purpose:
    a missing roster should fail at import, loudly, not at the first click.

NOTHING HERE MAY IMPORT FROM `corral/`
    Corral Light is the public, MIT, standalone product and this package lives
    in its tree. `TheCoreNeverImportsFullCorral` in Light's suite enforces it.
"""
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from corral_core import acp

# ── injected by each product's configure() ────────────────────────────────
# Declared here so the shared methods below resolve them in THIS namespace,
# and so a product that forgets to configure fails on a name that says so
# rather than on a confusing AttributeError three frames down.
AGENTS = None            # {lane: spec} — the roster this product offers
AGENT_GROUPS = None      # ordered grouping of that roster for the picker
STATE = None             # Path: where panes and transcripts live
CATALOG = None           # derived from STATE, not a constant — see configure()


def configure(*, AGENTS, AGENT_GROUPS, STATE,                    # noqa: N803
              ALLOW_VENDOR_ENV_VAR="CORRAL_ALLOW_VENDOR_ENV"):      # noqa: N803
    """Bind the globals that legitimately differ between products.

    `ALLOW_VENDOR_ENV_VAR` names the escape hatch that lets ambient vendor
    keys through to a pane (see `strip_prefixes`). It is a product name, not
    a shared one: Light shipped and documented `CORRAL_LIGHT_ALLOW_VENDOR_ENV`
    on 2026-08-31, full Corral has no reason to spell its own with LIGHT in
    it, and renaming Light's would break a documented operator knob.
    """
    g = globals()
    if AGENTS is None or AGENT_GROUPS is None or STATE is None:
        raise ValueError("corral_core.sessions.configure needs all three of "
                         "AGENTS, AGENT_GROUPS and STATE")
    g["AGENTS"], g["AGENT_GROUPS"] = AGENTS, AGENT_GROUPS
    g["STATE"] = Path(STATE)
    g["ALLOW_VENDOR_ENV_VAR"] = str(ALLOW_VENDOR_ENV_VAR)
    # `CATALOG = STATE / "catalog.json"` is spelled identically in both
    # products and is therefore easy to mistake for a shared constant. It is
    # not: it is derived from the one path that differs, so it has to be
    # recomputed here rather than evaluated at import against a STATE that is
    # still None.
    g["CATALOG"] = g["STATE"] / "catalog.json"


# ── ambient credentials never reach a pane ────────────────────────────────
# A vendor credential exported in the shell that started the hub silently
# OUTRANKS the login the operator verified — the agent runs as a different
# identity than the one the picker described, and the failure arrives later
# and elsewhere (the operator, 2026-08-31: logged in, verified it, /usage
# showed token STATISTICS instead of the subscription page, next prompt failed
# `Authentication required` — API-key mode, the login never used). Light
# shipped the strip that day (caf616e); full Corral did not get it until the
# 2026-09-09 completion review found the reason it was parked did not hold.
#
# Also stripped, MEASURED the same day: the eleven CLAUDE_* variables a Claude
# Code session exports into its children, CLAUDE_CONFIG_DIR among them. That
# one is the sharp edge — a hub started from inside a Claude Code session would
# otherwise hand every pane the parent session's config directory in exactly
# the fallback case where the product deliberately does not set its own.
# Product overrides are applied AFTER the strip, so setting CLAUDE_CONFIG_DIR
# on purpose still works.
#
# Fail safe: strip by default and SAY SO in the picker (each product's
# `available_agents` attaches `vendor_env_present()` as an envNote), because
# someone deliberately using an API key deserves to learn we removed it, not
# to debug why. The opt-in hatch is the env var named by `configure()`.
STRIP_ENV_PREFIXES = ("ANTHROPIC_", "OPENAI_", "GEMINI_", "GOOGLE_",
                      "XAI_", "GROK_",
                      "CLAUDECODE", "CLAUDE_")
ALLOW_VENDOR_ENV_VAR = "CORRAL_ALLOW_VENDOR_ENV"   # configure() may rename


def vendor_env_present():
    """Vendor credential vars in this process's environment, if any."""
    if os.environ.get(ALLOW_VENDOR_ENV_VAR) == "1":
        return []
    # Only CREDENTIALS are worth a note. The Claude Code session variables are
    # stripped too, but nobody exported those on purpose and saying so on
    # every lane would be noise that trains the eye to skip the line.
    # The prefix alone is not enough: GROK_AGENT / GROK_SESSION_ID are this
    # process's session identity (a Grok TUI session exports them), not a
    # key. Same split already used for CLAUDE_* — strip the session vars,
    # nag only on something that looks like a secret.
    creds = ("ANTHROPIC_", "OPENAI_", "GEMINI_", "GOOGLE_", "XAI_", "GROK_",
             "CLAUDE_")
    hints = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")
    return sorted(k for k in os.environ
                  if k.startswith(creds) and any(h in k.upper() for h in hints))


def strip_prefixes():
    """() when the operator has explicitly opted into ambient vendor auth."""
    if os.environ.get(ALLOW_VENDOR_ENV_VAR) == "1":
        return ()
    return STRIP_ENV_PREFIXES


def vendor_env_note(stripped):
    """The picker line both products show when something was stripped."""
    if not stripped:
        return ""
    return (f"ignoring {', '.join(stripped)} from this environment — panes "
            f"use the login on this host, not an ambient key. Unset it, or "
            f"set {ALLOW_VENDOR_ENV_VAR}=1 to use it.")


# ── bounds, identical in both products ────────────────────────────────────

# `auto` because that is what Craig actually uses (2026-08-01: "I use auto by
# default"), and it matches his standing ~/.claude setting. Corral shipped
# `strict` on the argument that a pane which never asks defeats the rail --
# but `auto` is NOT "never asks": per the agent's own description it runs a
# classifier and still escalates what the classifier will not approve. The
# posture pill keeps whichever mode is live visible on every pane, which is
# the property that actually mattered.
DEFAULT_POSTURE = "auto"

MAX_EVENTS = 4000               # per-pane ring in memory; JSONL on disk is the record

MAX_LOG_BYTES = 64 * 1024 * 1024   # per-pane transcript on disk, then rotate

MAX_PANES = 12                  # bounded: a wall of panes is not a workspace

MAX_PENDING_PERMS = 20         # a wedged/hostile adapter cannot grow the needs-you

MAX_PERM_BYTES = 262_144       # a consent payload past this is REFUSED, not clipped

POSTURES = {
    "strict": {"defaultMode": "default"},      # prompts on dangerous operations
    "edits":  {"defaultMode": "acceptEdits"},  # auto-accept edits, prompt the rest
    "auto":   {"defaultMode": "auto"},         # a classifier decides; still escalates
}

QUOTE_CHARS = 12_000           # of one pane's last answer carried into another


# ── shared helpers ────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _group_of(key):
    if not isinstance(key, str):
        return None
    for gid, g in AGENT_GROUPS.items():
        if key in (g.get("keys") or ()):
            return gid
    for gid, g in AGENT_GROUPS.items():
        if g.get("prefix") and key.startswith(g["prefix"]):
            return gid
    return None

def agent_groups():
    """Group definitions for the picker, with each group's member count.

    A group with no members is omitted entirely rather than offered as an empty
    submenu — the same "a button that lies" argument available_agents() is built
    on. (`agents` can shrink but never empties: `claude` is always present.)
    """
    out = {}
    for gid, g in AGENT_GROUPS.items():
        members = [k for k in AGENTS if _group_of(k) == gid]
        if members:
            out[gid] = {"label": g["label"], "hint": g.get("hint", ""),
                        "count": len(members)}
    return out


class PaneBase:
    """The pane behaviour both products share, verbatim.

    Every method below was byte-identical in the two `sessions.py` files. A
    product overrides what it genuinely does differently — `_init_runtime`,
    `resume`, `send`, `answer`, `snapshot`, `start`, `_on_event`, `_drain`,
    `set_config`, `_config_dir`, `from_meta` — by defining it in its subclass.
    """

    # `role`, `role_sha` and `role_delivery` are ANNOTATIONS, not controls
    # (full Corral's roles.py; Light does not ship roles and simply leaves them
    # None). They record which named preset started this conversation, the
    # digest of the preset's bytes AT THAT MOMENT, and how its instructions
    # were delivered -- today "preamble", i.e. user turn 0 under the vendor's
    # own system prompt, never a native --agent-profile. Nothing reads them to
    # decide anything after spawn; the day delivery changes, the record says so
    # rather than the change being invisible.
    META_KEYS = ("id", "agent", "cwd", "posture", "title", "title_locked",
                 "minimized", "acp_session", "created", "want_model",
                 "want_effort", "order", "pinned",
                 "role", "role_sha", "role_delivery")

    # Corral's own vocabulary is `model`/`effort`; adapters don't all use it.
    # Codex's ACP session (confirmed live, 2026-08-23, codex-acp 1.6.2) reports
    # a real, working reasoning knob -- 6 options, a real current value -- but
    # under the id `reasoning_effort`, not `effort`. Every consumer of
    # self.config (this class, the header pill, the new-pane dialog) only
    # ever looked for the literal string "effort", so a fully live config was
    # silently dropped on the floor: the dialog showed Model as a real
    # dropdown and Effort disabled, the same half-applied "can't do this"
    # affordance as Grok's fully-vendor-limited case -- except here Corral
    # just wasn't looking in the right place.
    _EFFORT_ALIASES = ("effort", "reasoning_effort", "reasoningEffort")

    def __init__(self, agent, cwd, posture, mgr, model=None, effort=None):
        self.id = uuid.uuid4().hex[:12]
        self.agent = agent
        self.cwd = str(cwd)
        self.posture = posture if posture in POSTURES else DEFAULT_POSTURE
        self.mgr = mgr
        self.want_model = model
        self.want_effort = effort
        self.role = None            # set by the caller that resolved it, if any
        self.role_sha = None
        self.role_delivery = None
        self._replaying = False
        self.title = self._default_title(agent, cwd)
        self.title_locked = False      # True once Craig renames it by hand
        self.minimized = False
        self.order = None         # explicit position; None = by age
        self.pinned = False
        self.created = _now()
        self._init_runtime()
        self.state = "starting"           # starting|ready|busy|needs-you|dead
        self.acp_session = None
        self.dir = STATE / "panes" / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._rotate_log()
        self._log = (self.dir / "events.jsonl").open("a", encoding="utf-8")

    @staticmethod
    def _default_title(agent, cwd):
        # The bare directory name collides with an agent-identity reading when
        # cwd happens to BE named that way -- Craig's own daily-driver repo is
        # `~/Github/CC`, so a fresh Grok or ChatGPT pane opened there defaulted
        # to the title "CC" and looked exactly like a Claude Code conversation
        # before it had said anything. Claude Code keeps the directory default
        # (still the useful "which repo" signal across many same-agent panes);
        # every other lane defaults to its own label instead.
        return (Path(cwd).name if agent == "claude" else None) \
            or AGENTS[agent]["label"]

    def save_meta(self, closed=False):
        """Write what is needed to rebuild this pane after a restart.

        Only metadata -- the transcript already lives in events.jsonl, and the
        conversation itself lives with the agent (session/load re-attaches to
        it). Called on every state change a human made, because losing a title
        or a minimize on restart is the same broken promise as losing the pane.
        """
        try:
            data = {k: getattr(self, k, None) for k in self.META_KEYS}
            data["closed"] = closed
            # Atomic, like auth.py's pairfile: a crash mid-write used to leave
            # a truncated meta.json, and from_meta skips unparseable panes —
            # so a power cut during a title edit could silently delete the
            # pane from the roster (Gemini adversarial review 2026-08-31).
            tmp = self.dir / "meta.json.tmp"
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self.dir / "meta.json")
        except OSError:
            pass

    def _read_events(self):
        """Read only the TAIL. We keep MAX_EVENTS, so slurping a months-old
        log into memory first — as this did — makes every restart slower and
        hungrier for events that get thrown away on the next line."""
        f = self.dir / "events.jsonl"
        try:
            size = f.stat().st_size
        except OSError:
            return []
        # Generous per-event allowance so the tail always contains MAX_EVENTS.
        want = MAX_EVENTS * 2048
        try:
            with f.open("rb") as fh:
                if size > want:
                    fh.seek(size - want)
                    fh.readline()             # discard a half-line at the seam
                raw = fh.read().decode("utf-8", "replace")
        except OSError:
            return []
        out = []
        for line in raw.splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out[-MAX_EVENTS:]

    @staticmethod
    def _read_back(path, before_seq, need):
        """Newest-first events with seq < before_seq, reading the file
        BACKWARDS in bounded chunks. The transcript caps at MAX_LOG_BYTES
        (64 MB) — slurping it for a history click would cost more memory
        than every pane's ring combined, so this never reads more than it
        needs (P8)."""
        out = []
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                pos, buf = size, b""
                while pos > 0 and len(out) < need:
                    step = min(256 * 1024, pos)
                    pos -= step
                    fh.seek(pos)
                    buf = fh.read(step) + buf
                    lines = buf.split(b"\n")
                    # lines[0] may be a partial line whose head is still
                    # unread; keep it for the next chunk (or the tail parse).
                    buf = lines[0]
                    for line in reversed(lines[1:]):
                        if len(out) >= need:
                            break
                        try:
                            e = json.loads(line)
                        except ValueError:
                            continue
                        if 0 < e.get("seq", 0) < before_seq:
                            out.append(e)
                if pos == 0 and len(out) < need and buf.strip():
                    try:
                        e = json.loads(buf)
                        if 0 < e.get("seq", 0) < before_seq:
                            out.append(e)
                    except ValueError:
                        pass
        except OSError:
            pass
        return out

    def history(self, before_seq, limit=200):
        """Transcript events OLDER than `before_seq`, from DISK (Phase 5b).

        The in-memory ring keeps MAX_EVENTS; everything older lives only in
        events.jsonl (+ one rotated generation). This is the paging read the
        Gemini arm called 'just a disk read' — chronological, ending right
        before the oldest event the client already holds.
        """
        limit = max(1, min(int(limit or 200), 500))
        newest_first = []
        for name in ("events.jsonl", "events.jsonl.1"):
            if len(newest_first) >= limit:
                break
            newest_first += self._read_back(self.dir / name, before_seq,
                                            limit - len(newest_first))
        return list(reversed(newest_first))

    def _rotate_log(self):
        """Cap the on-disk transcript. Append-only durability is right, but
        unbounded is not (P8) — one chatty agent could fill the state volume
        and take every other pane's persistence down with it.

        Under self._lock, the same lock emit() writes under: rotating without
        it let a concurrent emit hit the just-closed handle (ValueError,
        swallowed — the event silently missing from the durable transcript)
        or land an append in the just-renamed file, where the next rotation
        deletes it (Gemini adversarial review 2026-08-31). Callers must not
        hold the lock; emit() calls this after releasing it."""
        with self._lock:
            self._rotate_log_locked()

    def _rotate_log_locked(self):
        f = self.dir / "events.jsonl"
        try:
            if f.stat().st_size <= MAX_LOG_BYTES:
                return
            old = self.dir / "events.jsonl.1"
            if old.exists():
                old.unlink()                  # keep exactly one generation
            f.rename(old)
        except OSError:
            return
        # Rotating under a LIVE pane means our open handle now points at the
        # renamed file: appends would keep landing in events.jsonl.1 and the
        # next rotation would delete them. Reopen, or rotation quietly becomes
        # deletion of everything written since.
        log = getattr(self, "_log", None)
        if log is not None:
            try:
                log.close()
            except Exception:                 # noqa: BLE001
                pass
            try:
                self._log = (self.dir / "events.jsonl").open("a", encoding="utf-8")
            except OSError:
                self._log = None

    def _reap_failed_client(self):
        """Kill and drop a client whose attach never completed."""
        if self.client is None:
            return
        try:
            self.client.close()
        except Exception:                   # noqa: BLE001
            pass
        self.client = None

    # ── event plumbing ───────────────────────────────────────────────────
    def emit(self, kind, payload, activity=True):
        if getattr(self, "_replaying", False):
            return None            # history we already hold; see resume()
        # activity=False for synthetic observations (snapshot's state edges):
        # they must not reset the idle clock, or marking a pane `uncertain`
        # would itself look like the pane waking up and flip it back to
        # `busy` every STALL_S, forever.
        # A COUNTER, not len(events). The ring is bounded at MAX_EVENTS, so
        # `len(self.events) + 1` stalled at MAX_EVENTS+1 forever once the pane
        # filled up: every later event carried the same seq, the client's
        # dedup (`ev.seq <= last.seq`) dropped all of them, and snapshot's
        # `seq > since` could not backfill them either. A busy pane simply
        # went silent and no error was raised anywhere. Found by GPT-5.6 in
        # review, 2026-08-01; reproduced with a positive control before fixing.
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "at": _now(), "pane": self.id,
                  "kind": kind, "data": payload}
            self.events.append(ev)
            del self.events[:-MAX_EVENTS]          # bounded ring
            if activity:
                self.last_activity = time.time()
            try:
                if self._log is not None:
                    self._log.write(json.dumps(ev) + "\n")
                    self._log.flush()
                self._since_rotate_check += 1
            except (OSError, ValueError):
                pass
        # Rotation used to happen ONLY at construction and restore, so the
        # 64 MB cap held across restarts and not while running — which is the
        # whole time that matters. A pane chatting all day could pass it by an
        # order of magnitude and nothing would notice until the next boot.
        if self._since_rotate_check >= 500:
            self._since_rotate_check = 0
            self._rotate_log()
        self.mgr.broadcast(ev)
        return ev

    def _flush_thought(self):
        acc = getattr(self, "_thought_acc", "")
        if acc and acc.strip():
            self._thought_acc = ""
            self.emit("thought", {"text": acc})
        else:
            self._thought_acc = ""

    def _clear_pending(self, reason):
        """Drop every pending permission and tell the transcript WHY.

        Pause and agent_exit both used to leave `self.pending` untouched --
        the process that would have answered it is gone, but the rail (and a
        reconnected browser reading `snapshot()`) kept offering an approval
        for a request nothing is listening for anymore. Same fix as
        `permission_expired` above, applied to the other two ways a pending
        request goes stale: reused so both paths stay in sync with it.
        """
        if not self.pending:
            return
        stale = list(self.pending.keys())
        self.pending = {}
        for rid in stale:
            self.emit("permission_expired", {"requestId": rid, "reason": reason})

    def _on_permission(self, req):
        """Record the WHOLE thing being approved, or refuse to offer approval.

        PRINCIPLES 17: an approval proves only what the human could SEE. This
        used to keep `content[:4]` and `locations[:6]`, and the browser then
        sliced rawInput to 4,000 characters and rendered only the first diff.
        So a multi-file edit or a long command could be approved with its
        meaningful part never displayed — a signature on bytes nobody saw,
        which is the exact failure the principle was written from.

        Now: keep it all, hash it, and show it all. If a payload is genuinely
        too large to hold, the request is marked `oversize` and the UI offers
        ONLY refusal — never approval of something we cannot display. The cap
        exists because P8 says bound every output; refusing at the cap is what
        keeps the bound from silently becoming a truncation.
        """
        rid = req.get("requestId")
        if len(self.pending) >= MAX_PENDING_PERMS:
            # Fail-closed doctrine already says an unanswered permission is a
            # refusal; this just makes that happen NOW instead of after the
            # backlog grows without bound. A malfunctioning or hostile adapter
            # firing permission requests faster than a human can answer them
            # would otherwise grow self.pending (and the rendered rail)
            # forever. gpt-5.6-sol, third-pass review, finding 5.
            reject = next((o.get("optionId") for o in (req.get("options") or [])
                          if str(o.get("kind", "")).startswith("reject")), None)
            self.emit("note", {"text": f"more than {MAX_PENDING_PERMS} "
                              f"permissions are already waiting on you here; "
                              f"auto-refusing this one rather than letting "
                              f"the backlog grow without bound"})
            if reject is not None and self.client:
                try:
                    self.client.answer_permission(rid, reject)
                except acp.AgentError:
                    pass
            return
        self.pending[rid] = req
        self.state = "needs-you"
        tc = req.get("toolCall") or {}
        body = {"rawInput": tc.get("rawInput"),
                "content": tc.get("content") or [],
                "locations": tc.get("locations") or []}
        try:
            blob = json.dumps(body, sort_keys=True, default=str)
        except (TypeError, ValueError):
            blob = repr(body)
        digest = hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()
        oversize = len(blob) > MAX_PERM_BYTES
        # Keep the VERDICT with the pending request, not only in the event
        # stream. `self.events` is a bounded ring (MAX_EVENTS); a permission
        # left unanswered while the pane stays busy falls out of it, and
        # answer() used to recover `oversize` by SEARCHING that ring. Once
        # evicted the lookup returned {}, `oversize` read falsy, and the
        # server granted a request it had already judged undisplayable --
        # the consent gate deriving its authority from a lossy presentation
        # cache. gpt-5.6-sol, third-pass review, finding 2.
        req["_gate"] = {"oversize": oversize, "digest": digest,
                        "bytes": len(blob)}
        self.emit("permission", {
            "requestId": rid, "title": tc.get("title"), "kind": tc.get("kind"),
            # The digest binds the approval to these exact bytes, and survives
            # even when the body does not.
            "digest": digest, "bytes": len(blob), "oversize": oversize,
            "rawInput": None if oversize else body["rawInput"],
            "content": [] if oversize else body["content"],
            "locations": [] if oversize else body["locations"],
            "options": req.get("options") or []})

    def _mcp_servers(self):
        registry = getattr(self.mgr, "mcp", None)
        return registry.session_servers() if registry else []

    def _absorb_config(self, options):
        """Record what the agent says its config IS -- never what we asked for.

        Asking for a model is a request; the agent decides. Rendering the
        requested value would show Craig a model that may not be serving him.
        """
        for co in options:
            real_id = co.get("id")
            cid = "effort" if real_id in self._EFFORT_ALIASES else real_id
            self.config[cid] = {
                "value": co.get("currentValue"),
                "name": co.get("name"),
                "realId": real_id,        # what the ADAPTER calls this, for set_config
                "options": [{"value": o.get("value"), "name": o.get("name"),
                             "description": (o.get("description") or "")[:120]}
                            for o in (co.get("options") or [])][:20],
            }
        self.model = (self.config.get("model") or {}).get("value")
        self.effort = (self.config.get("effort") or {}).get("value")
        self.mgr.remember_catalog(self.agent, self.config)

    def cancel(self):
        if self.client and self.acp_session:
            self.client.cancel(self.acp_session)
            self.emit("cancelled", {})
            return True
        return False

    def pause(self):
        """Stop the process, KEEP the pane. The middle state that was missing.

        Close was the only exit: it ended the process and removed the row, so
        interrupted work had nowhere to sit. Pause puts a pane in exactly the
        state a server restart already produced — `detached`, transcript
        intact, no agent running — which resume() and send() both already know
        how to pick back up. It costs nothing to keep and nothing to run.
        """
        if self.state == "detached":
            return self
        # State FIRST, and announce the expected exit, so the reader thread's
        # agent_exit does not overwrite `detached` with `dead`. Clearing the
        # queue before the close also stops _drain from picking up one more
        # turn against a client that is about to vanish.
        self._expect_exit = True
        self.state = "detached"
        self._clear_pending("paused")
        with self._turn_lock:
            dropped, self._queue = len(self._queue), []
            self._turn_running = False
            # Retire any _drain() thread still blocked in the OLD client's
            # prompt(): once this changes, its captured generation is stale
            # and it will touch nothing when prompt() finally returns.
            self._generation += 1
        if self.client:
            self.client.close()
        self.client = None
        self.error = None                 # paused is not a fault
        self.emit("paused", {"dropped": dropped})
        self.save_meta()
        # Release the open transcript handle while detached. Every detached
        # pane used to keep holding one for as long as the server ran, so
        # repeated create+pause grew the open-fd count right alongside the
        # roster it sits in. resume() reopens it. gpt-5.6-sol, third-pass
        # review, finding 7.
        try:
            if self._log is not None:
                self._log.close()
        except Exception:
            pass
        self._log = None
        return self

    def stop(self):
        if self.client:
            self.client.close()
        self.state = "dead"
        self.error = self.error or "closed by you"
        # Mark it closed ON DISK. restore() skips panes carrying this flag, and
        # nothing was setting it -- so closing a pane stopped the process and
        # cleared the row, and the next server restart resurrected it from
        # meta.json. Craig: "when I click the x on a pane to close it, it pops
        # right back up on restart." The transcript is deliberately left alone;
        # this hides the pane, it does not delete the conversation.
        self.save_meta(closed=True)
        try:
            if self._log is not None:
                self._log.close()
        except Exception:
            pass

    def rename(self, title):
        title = " ".join((title or "").split())[:60]
        if not title:
            raise ValueError("a name cannot be empty")
        self.title = title
        self.title_locked = True       # never auto-retitled again
        self.save_meta()
        self.emit("renamed", {"title": title})
        return title

    def last_answer(self):
        """The agent's most recent answer, as (text, complete).

        Read off the bounded ring, newest first, back to the `user` event
        that asked for it: every `text` chunk in between IS the answer (tool
        rows, thoughts and notes are not). `complete` is whether a
        `turn_end` has landed since that user event -- a cross-feed that
        quotes a half-written answer would hand the other arms a sentence
        the model had not finished, so callers that compose must check it.
        Empty text with complete=True is a real state (the turn produced only
        tool calls) and is reported as such, never padded.
        """
        chunks, complete = [], False
        for ev in reversed(self.events):
            k = ev["kind"]
            if k == "user":
                break
            if k == "turn_end":
                complete = True
            elif k == "text":
                chunks.append((ev.get("data") or {}).get("text") or "")
        chunks.reverse()
        return "".join(chunks).strip(), complete


class ManagerBase:
    """The manager behaviour both products share, verbatim.

    `__init__`, `seed_catalogs`, `remember_catalog`, `create`, `restore`,
    `reopen`, `reorder`, `set_pinned` and `state` differ and stay in each
    product; everything here did not.
    """

    @staticmethod
    def _load_catalog():
        try:
            return json.loads(CATALOG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def broadcast(self, ev):
        with self._lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                # A slow subscriber must not stall an agent — but DROPPING its
                # events silently is worse than making it wait. The browser's
                # only ordering check is `seq <= last`, so a lost `permission`
                # followed by a delivered `turn_end` leaves it showing `ready`
                # for a pane that is actually blocked: the UI lies in the one
                # direction this product exists to prevent. So we throw the
                # backlog away and leave a single resync marker, which the
                # client answers with a full refresh.
                try:
                    while True:
                        q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait({"seq": 0, "at": _now(), "pane": None,
                                  "kind": "resync",
                                  "data": {"reason": "this browser fell behind"}})
                except Exception:       # noqa: BLE001
                    pass
            except Exception:           # noqa: BLE001
                pass

    def subscribe(self, q):
        with self._lock:
            self.subscribers.append(q)

    def unsubscribe(self, q):
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def resume(self, pane_id):
        return self.get(pane_id).resume()

    def pause(self, pane_id):
        return self.get(pane_id).pause()

    def archived(self, limit=40):
        """Conversations that were closed, newest first.

        Closing wrote `closed: true` and restore() skipped it forever, so a
        finished conversation was gone from the product while its transcript
        sat on disk untouched. That is a deletion the operator never asked
        for. This reads them back so they can be found and reopened.
        """
        root = STATE / "panes"
        out = []
        if not root.is_dir():
            return out
        for d in root.iterdir():
            if d.name in self.panes:
                continue
            try:
                m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not m.get("closed") or not m.get("id"):
                continue
            out.append({"id": m["id"], "title": m.get("title") or m["id"],
                        "agent": m.get("agent", "?"), "cwd": m.get("cwd", ""),
                        "created": m.get("created", "")})
        out.sort(key=lambda m: m["created"], reverse=True)
        return out[:limit]

    def get(self, pane_id):
        p = self.panes.get(pane_id)
        if not p:
            raise ValueError(f"no pane {pane_id}")
        return p

    # --- Composition: panes feeding panes ------------------------------------
    # Until 2026-09-01 the only router between panes was the human. Five
    # lanes side by side were five chat windows; the pattern that actually
    # earned its keep this month (49 rival panels in 9 days) ran headless,
    # outside the window. These three verbs put it inside, on the same rail:
    # nothing here bypasses a pane's own permission gate, and every prompt a
    # verb composes is emitted as that pane's `user` event, so what was sent
    # is exactly what the transcript shows (PRINCIPLES 17, 18).
    def quote(self, from_id, to_id=None):
        """Text for a composer: one pane's last answer, fenced and attributed.

        Returns text only -- nothing is sent (the same P17 stance as
        content attach). An SSH pane is never a source or a target: its
        transcript is shell output, and a quoted answer pasted onto a
        command line is a command you did not mean to type.
        """
        src = self.get(from_id)
        if src.agent.startswith("host:"):
            raise ValueError("an SSH pane has no answer to quote")
        if to_id:
            dst = self.get(to_id)
            if dst.agent.startswith("host:"):
                raise ValueError("SSH panes cannot receive quoted answers")
            if dst.id == src.id:
                raise ValueError("a pane cannot quote itself")
        body, complete = src.last_answer()
        if not body:
            raise ValueError(f"{src.title} has not answered yet")
        clipped = len(body) > QUOTE_CHARS
        label = AGENTS.get(src.agent, {}).get("label") or src.agent
        tail = "\n[\u2026truncated]" if clipped else ""      # no backslash inside an
        text = (f"From {label} \u2014 {src.title}:\n\n\"\"\"\n"   # f-string expr: 3.9
                f"{body[:QUOTE_CHARS]}{tail}\n\"\"\"\n\n")
        return {"text": text, "complete": complete, "title": src.title,
                "label": label, "clipped": clipped}

    def fanout(self, ids, text):
        """One prompt to many panes. Per-pane bounds and queues still apply;
        one pane refusing does not stop the others, and the refusal is
        returned by pane id, never swallowed."""
        ids = list(dict.fromkeys(i for i in ids if i))[:MAX_PANES]
        if not ids:
            raise ValueError("no panes to send to")
        results, sent = {}, 0
        for pid in ids:
            try:
                self.get(pid).send(text)
                results[pid] = None
                sent += 1
            except Exception as e:      # noqa: BLE001 - reported, per pane
                results[pid] = str(e)
        return {"sent": sent, "results": results}

    def crossfeed(self, ids, preamble):
        """Round two: every pane receives every OTHER pane's last answer,
        under one preamble. Refuses -- for all, not some -- if any arm has
        not finished: a round two over a partial round one is a panel with
        a missing arm that nobody was told about."""
        ids = list(dict.fromkeys(i for i in ids if i))[:MAX_PANES]
        if len(ids) < 2:
            raise ValueError("cross-feed needs at least two panes")
        panes = [self.get(i) for i in ids]
        quotes = {}
        for p in panes:
            if p.agent.startswith("host:"):
                raise ValueError(f"{p.title} is an SSH pane and cannot take part")
            body, complete = p.last_answer()
            if not any(ev["kind"] == "user" for ev in p.events):
                # A fresh pane on the wall is not an arm: it was never asked.
                # Saying it "has not finished answering" here was a lie that
                # sent Craig looking for a hung agent (2026-09-02).
                raise ValueError(f"{p.title} has not been asked anything yet "
                                 f"\u2014 send it the question first, or leave "
                                 f"it out of the cross-feed")
            if not complete:
                raise ValueError(f"{p.title} is still answering "
                                 f"\u2014 cross-feed waits for every arm")
            if not body:
                raise ValueError(f"{p.title} finished with no text to quote "
                                 f"(tool calls only) \u2014 ask it to answer in words first")
            quotes[p.id] = self.quote(p.id)["text"]
        preamble = (preamble or "").strip()
        results, sent = {}, 0
        for p in panes:
            others = "".join(quotes[o.id] for o in panes if o.id != p.id)
            prompt = (preamble + "\n\n" + others).strip()
            try:
                p.send(prompt)
                results[p.id] = None
                sent += 1
            except Exception as e:      # noqa: BLE001
                results[p.id] = str(e)
        return {"sent": sent, "results": results}

    def _resort(self):
        """Rebuild the registry in display order. Pinned first, then explicit
        order, then age — and the dict's own insertion order carries it, so
        every consumer (snapshot, roster, grid) agrees without a second sort."""
        def key(p):
            return (0 if p.pinned else 1,
                    p.order if p.order is not None else 10_000,
                    p.created or "")
        self.panes = {p.id: p for p in sorted(self.panes.values(), key=key)}

    def close(self, pane_id):
        """Close AND remove. Closing used to leave a dead row in the roster and
        an "agent stopped" card in the needs-you rail until dismissed again --
        Craig: "when I close a pane it shows agent stopped and leaves an
        artifact." A close you asked for is finished business; only a pane that
        died on its own is news, and that one still stays for `forget`."""
        p = self.get(pane_id)
        # SAY SO. A close writes `closed: true` on disk and restore() then
        # skips the pane forever; until 2026-09-10 it left no trace anywhere
        # but that flag. Six panes were found closed inside one 37-second
        # window with nothing in the journal, the run registry or the
        # transcripts to name what did it -- an unauditable disappearance of
        # the operator's work (P18). One line makes the next one answerable.
        print(f"corral: close pane {p.id} ({p.agent}) {p.title!r}",
              file=sys.stderr, flush=True)
        p.stop()
        self.panes.pop(pane_id, None)
        return p

    def forget(self, pane_id):
        """Drop a DEAD pane from the roster.

        Closing stopped the process but left the row on screen forever, so a
        finished or crashed conversation accumulated as permanent clutter with
        no way to clear it -- and it sat in the needs-you rail as "agent
        stopped" indefinitely. Only dead panes can be forgotten: a live one has
        to be closed first, deliberately, so this can never become an
        accidental kill. The transcript on disk is untouched."""
        p = self.get(pane_id)
        if p.state != "dead" or (p.client and p.client.alive):
            raise ValueError("close it first — a live conversation cannot be "
                             "dismissed by accident")
        print(f"corral: forget pane {p.id} ({p.agent}) {p.title!r}",
              file=sys.stderr, flush=True)
        p.save_meta(closed=True)     # same hole as stop(): dismissed, then back
        self.panes.pop(pane_id, None)
        return pane_id
