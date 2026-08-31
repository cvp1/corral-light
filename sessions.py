#!/usr/bin/python3
"""sessions — Corral Light's session manager: spawn agents, hold state, persist
events.

This is the Live-tab half of ranch-server's `corral/sessions.py`, forked for
dogma-2 on 2026-08-31. Everything the fleet made true on ranch — delegate
boxes, ssh-shell lanes, the scheduler, the AI-OS slash-command router, the
vault-backed memory reader — is GONE, not disabled: a light build that carries
dead branches is the heavy build with a smaller menu. What remains is the part
that makes a multi-model workspace: one process per pane, one ordered event
stream per pane, and pending permission requests as real backpressure on a
real process.

WHAT IS AND IS NOT STORED HERE
------------------------------
ACP already owns conversation identity: `session/list` returns
{sessionId, cwd, title, updatedAt} with auto-generated titles, and advertises
resume/fork/close/delete. So Corral does NOT keep a second registry of
conversations — that would be one-home-per-fact violated with an independent
writer, and it would drift the moment the agent renames a thread.

What Corral does own, because nothing else does:
  - which agent process is running right now, and whether it is actually alive
  - the ordered event stream per pane, so a browser reload replays instead of
    losing the conversation
  - pending permission requests, which are backpressure on a live process

PERMISSION POSTURE IS OURS. Every Claude Code pane launches under a
CLAUDE_CONFIG_DIR Corral writes. Measured on ranch: inheriting the host's
ambient `defaultMode: auto` made the agent act with zero prompts, which would
render a pane with no approvals at all. Posture is a visible per-pane
property, never an inherited global — and for a lane Corral cannot impose it
on, the pane SAYS so rather than displaying a safety property nobody
established.
"""
import hashlib
import json
import os
import queue
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import re

import acp
import mcp

ROOT = Path(__file__).resolve().parent
# Deliberately NOT ~/.local/share/corral. If the full Corral is ever installed
# on the same machine, two hubs sharing one state dir would share panes, the
# pairing key, and the catalog — and each would restore the other's panes with
# lanes it does not have. A separate name is what keeps "light" a second
# product rather than a corrupting second writer.
STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))
# Optional: a node install that is not on PATH. On ranch this pointed at
# hermes' bundled node; on a Mac, node is normally on PATH already, so an
# absent directory must cost nothing (an empty entry in PATH is harmless, but
# a WRONG one shadows the real node).
_NODE_BIN = Path(os.environ.get("CORRAL_NODE_BIN",
                                Path.home() / ".hermes" / "node" / "bin"))
NODE_BIN = _NODE_BIN if _NODE_BIN.is_dir() else None
ADAPTER = Path(os.environ.get("CORRAL_CLAUDE_ADAPTER",
                              ROOT / "spike" / "node_modules" / ".bin" / "claude-agent-acp"))

MAX_EVENTS = 4000               # per-pane ring in memory; JSONL on disk is the record
MAX_PANES = 12                  # bounded: a wall of panes is not a workspace
MAX_ROSTER = MAX_PANES * 5      # ALL panes tracked, live or detached. MAX_PANES
                                 # only counts live ones, so repeated
                                 # create-then-pause never tripped it: every
                                 # detached pane stays in Manager.panes forever,
                                 # each holding an open transcript handle. This
                                 # is the cap on the roster itself.
MAX_PROMPT = 200_000
MAX_PERM_BYTES = 262_144       # a consent payload past this is REFUSED, not clipped
MAX_PENDING_PERMS = 20         # a wedged/hostile adapter cannot grow the needs-you
                               # backlog without bound; past this, auto-refuse
MAX_QUEUED_TURNS = 4           # type-ahead depth per pane; beyond it, say no
MAX_LOG_BYTES = 64 * 1024 * 1024   # per-pane transcript on disk, then rotate
STALL_S = 300                  # busy with nothing emitted for this long = suspect

# Permission postures Corral offers, mapped to Claude Code's own modes. The
# descriptions are the AGENT's, read off session/new's configOptions:
#   auto        "Use a model classifier to approve/deny permission prompts"
#   default     "Standard behavior, prompts for dangerous operations"
#   acceptEdits "Auto-accept file edit operations"
# What a pane inherits from ~/.claude by symlink. These are things Claude Code
# READS: capability and context, authored outside Corral, so a link stays
# current where a copy would fossilise at pane creation.
LINKED_CONFIG = ("skills", "agents", "commands", "plugins", "prompts", "CLAUDE.md")

# What a pane does NOT inherit from settings.json.
#   permissions — replaced by the pane's own posture; owning that is the whole
#                 reason Corral keeps a config dir at all.
#   hooks       — auto-run code. Wiring hooks into a new surface is
#                 self-modification and needs to be asked for in so many words.
#                 Skills and subagents are inert until invoked; hooks are not.
#   statusLine  — a terminal affordance. ACP has no status line, so this would
#                 only spawn a subprocess with nowhere to render.
SETTINGS_DROPPED = ("permissions", "hooks", "statusLine")

POSTURES = {
    "strict": {"defaultMode": "default"},      # prompts on dangerous operations
    "edits":  {"defaultMode": "acceptEdits"},  # auto-accept edits, prompt the rest
    "auto":   {"defaultMode": "auto"},         # a classifier decides; still escalates
}
DEFAULT_POSTURE = "auto"

GROK_LAUNCHER = ROOT / "grok_launcher.py"
OLLAMA_ACP = ROOT / "ollama_acp.py"
NATIVE_ANTIGRAVITY_LAUNCHER = ROOT / "antigravity_acp_launcher.py"
NATIVE_ANTIGRAVITY_BIN = Path.home() / ".local/lib/corral/antigravity-acp/agy_acp_server.par"
NATIVE_ANTIGRAVITY_HELPER = NATIVE_ANTIGRAVITY_BIN.with_name("localharness_external")


# --- Catalog probes: a lane's model list WITHOUT starting a pane ------------
# The new-pane dialog reads the remembered catalog (CATALOG on disk), which is
# only written once a pane of that agent has completed session/new. For an
# agent that has never run on this host that entry does not exist, so the model
# dropdown correctly renders its "never seen this agent" empty state. A probe
# fills it in from the lane's OWN catalog — never a hardcoded list, and never a
# model the pane would then refuse. A pane's real session/new response still
# overwrites whatever was seeded, so the live agent remains the authority.
def _probe_ollama():
    """(values, default) for the local Ollama lane, or None. Never raises."""
    try:
        import ollama_acp
        tags = ollama_acp.list_models()
        return (tags, tags[0]) if tags else None
    except Exception as e:  # noqa: BLE001 — a probe is a nicety, never a blocker
        print(f"corral-light: ollama catalog probe skipped: {e}",
              file=sys.stderr, flush=True)
        return None


AGENTS = {
    "claude": {
        "label": "Claude Code",
        "argv": [str(ADAPTER)],
        "requires": (str(ADAPTER),),
        "posture_via_config_dir": True,
        "tools": True,
        # KNOWN GAP, left open deliberately. Every other lane refuses at pick
        # time when its credential is missing; this one cannot check cheaply
        # without risking the opposite lie. A pane seeds its config dir from
        # `~/.claude/.credentials.json`, so testing for that file looks like
        # the obvious probe — but on macOS Claude Code can keep its
        # credential in the Keychain instead, where an absent file proves
        # nothing. Refusing a lane that works is the same class of wrong as
        # offering one that doesn't, so until there is a real check (asking
        # the CLI, not guessing at a path), this lane stays optimistic and an
        # unauthenticated pane fails at its first prompt with the vendor's own
        # message.
    },
    "codex": {
        # ChatGPT via OpenAI's codex, over the ACP-org adapter
        # (@agentclientprotocol/codex-acp — same org as the Claude adapter,
        # pinned EXACT in spike/package.json because it tracks codex's
        # unversioned app-server protocol). The launcher owns the two
        # properties that matter: a dedicated CODEX_HOME (inheriting ~/.codex
        # would chat with whatever that config points at, wearing a ChatGPT
        # label) and auth inside the CLI's own state — no key in argv, env, or
        # logs. Approvals arrive as session/request_permission and land in the
        # rail; INITIAL_AGENT_MODE=agent keeps escalations visible there.
        "label": "ChatGPT (Codex)",
        "argv": [sys.executable, str(ROOT / "codex_launcher.py")],
        "posture_via_config_dir": False,
        "tools": True,
        "needs": "needs ChatGPT login (device-auth) — see codex_launcher.py",
    },
    "grok": {
        # The vendor's own ACP stdio mode, launched directly. The Grok CLI owns
        # auth; the credential never enters argv, env, or logs.
        "label": "Grok",
        "argv": [sys.executable, str(GROK_LAUNCHER)],
        "requires": (str(GROK_LAUNCHER),),
        "posture_via_config_dir": False,
        "tools": True,
        "needs": "needs Grok CLI authentication",
    },
    "gemini": {
        # Google's registered antigravity-acp binary speaks ACP itself: its
        # session identity, model catalog, tool calls, permission requests and
        # cancellation flow arrive unmodified. Do not seed the catalog through
        # `agy models`: it is a different API and can promise model ids the
        # native server will reject, so the first real session/new is the
        # authority. Install/verify the pinned release with
        # `python3 install_antigravity_acp.py --install` / `--check`.
        "label": "Antigravity (Gemini)",
        "argv": [sys.executable, str(NATIVE_ANTIGRAVITY_LAUNCHER)],
        "requires": (str(NATIVE_ANTIGRAVITY_LAUNCHER),
                     str(NATIVE_ANTIGRAVITY_BIN),
                     str(NATIVE_ANTIGRAVITY_HELPER)),
        "posture_via_config_dir": False,
        "tools": True,
        "needs": "official Google native ACP — authenticated by Antigravity OAuth",
    },
    "ollama": {
        # The sovereign lane, and the reason Corral Light is worth running at
        # all when the WAN is down: ollama_acp.py speaks ACP over stdio against
        # a local Ollama. CHAT ONLY — it has no tools and therefore raises no
        # permission requests, which is stated on the lane rather than left for
        # the operator to discover. A lane that silently had no rail would be
        # indistinguishable from a rail that stopped working.
        "label": "Local (Ollama) — chat only",
        "argv": ["/usr/bin/env", "python3", str(OLLAMA_ACP)],
        "requires": (str(OLLAMA_ACP),),
        "posture_via_config_dir": False,
        # The one lane that cannot read a file for itself. Attaching a note to
        # a pane here has to QUOTE it, because handing a path to an agent with
        # no filesystem is a dead end that looks like a working feature.
        "tools": False,
        "needs": "answers from the local Ollama — no key, works offline; "
                 "chat only, no tools and no permission rail",
        "catalog_probe": lambda: _probe_ollama(),
    },
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def spawn_env(spec, config_dir=None):
    """The environment ONE agent process launches under.

    One home, because there are two spawn sites (start and resume) and keeping
    them in sync by hand is how a pane comes up on the wrong provider from
    whichever site someone forgot.
    """
    env = {}
    if NODE_BIN:
        env["PATH"] = f"{NODE_BIN}:{os.environ.get('PATH', '')}"
    env.update(spec.get("env") or {})
    if spec["posture_via_config_dir"] and config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env


def _skill_commands(agent):
    """The local skill/command catalog, for the composer's `/` completion.

    Read off disk, never hardcoded: ACP's `available_commands_update` replaces
    and augments this after session/new, and Claude and Grok will not advertise
    the same set. This is only what makes the composer useful BEFORE that
    notification arrives (or if it never does).
    """
    out = {}
    roots = (Path.home() / ".claude" / "skills", Path.home() / ".claude" / "commands")
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob("*/SKILL.md") if root.name == "skills" else root.glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")[:5000]
            except (OSError, UnicodeError):
                continue
            name = path.parent.name if root.name == "skills" else path.stem
            match = re.search(r"^description:\s*(.+)$", text, re.M)
            description = (match.group(1).strip().strip('"\'') if match else "")[:160]
            out[name] = {"name": name, "description": description,
                         "status": "native" if agent == "claude" else "prompt-only"}
    # Corral's own, so every backend discovers it the same way.
    out["mcp"] = {"name": "mcp", "description": "Show or reconnect MCP servers",
                  "status": "native"}
    return sorted(out.values(), key=lambda item: item["name"])


# --- Picker grouping --------------------------------------------------------
# Light has exactly one family, and it is still declared rather than implied:
# the browser tags each lane with its group and needs the definitions that name
# the groups shipped alongside. A second family (were one ever added back) then
# costs a dict entry, not a UI change.
AGENT_GROUPS = {
    "agents": {
        "label": "Agents",
        "keys": ("claude", "codex", "grok", "gemini", "ollama"),
        "hint": "one conversation, one process, one transcript",
    },
}


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


def available_agents():
    """Only offer what actually exists on this host — an agent picker listing a
    binary that isn't installed is a button that lies."""
    out = []
    for key, spec in AGENTS.items():
        exe = Path(spec["argv"][0])
        # argv[0] alone can lie for interpreter-launched lanes: python3 exists
        # whether or not the adapter script does. `requires` names the rest of
        # what the lane needs on disk.
        missing = [p for p in spec.get("requires", ()) if not Path(p).exists()]
        # grok resolves its binary at call time (PATH, AIOS_GROK_BIN, or the
        # CLI's default install dirs), so a static argv[0] check cannot answer
        # "is it installed" for this lane. And installed is not the question
        # anyway: this asked only resolve_grok(), which documents itself as
        # returning a path "without probing auth", so a host with the CLI
        # present but never signed in reported the lane AVAILABLE and the pane
        # died on its first prompt with `Authentication required` (dogma-2,
        # 2026-08-31). Ask the launcher for the whole answer, as codex and
        # ollama already do.
        if key == "grok":
            from grok_launcher import unavailable_reason
            reason = unavailable_reason()
            out.append({"key": key, "label": spec["label"],
                        "available": reason is None, "why": reason or "",
                        "postureEnforced": bool(spec["posture_via_config_dir"]),
                        "tools": bool(spec.get("tools"))})
            continue
        # codex availability is adapter-present AND logged-in, both resolved at
        # call time by its launcher — refuse in the picker with the exact login
        # command, not with a pane that dies at session/new.
        if key == "codex":
            from codex_launcher import unavailable_reason
            reason = unavailable_reason()
            out.append({"key": key, "label": spec["label"],
                        "available": reason is None, "why": reason or "",
                        "postureEnforced": bool(spec["posture_via_config_dir"]),
                        "tools": bool(spec.get("tools"))})
            continue
        # ollama needs a RUNNING SERVER, not just a file on disk: the adapter
        # is always present, so the generic exists() check below would call the
        # lane available with Ollama stopped and hand over a pane that dies on
        # its first prompt. Same argument as codex above, different dependency.
        if key == "ollama":
            reason = f"not installed: {missing[0]}" if missing else None
            if reason is None:
                import ollama_acp
                reason = ollama_acp.unavailable_reason()
            out.append({"key": key, "label": spec["label"],
                        "available": reason is None,
                        "why": reason or spec.get("needs", ""),
                        "postureEnforced": bool(spec["posture_via_config_dir"]),
                        "tools": bool(spec.get("tools"))})
            continue
        # The Antigravity runtime is a PINNED LINUX x86-64 binary. Files
        # existing on disk is the wrong question for it: a Linux .par sitting
        # in ~/.local/lib on a Mac satisfies `requires` perfectly and the pane
        # then dies at exec. The installer refuses to put it there, but a hand
        # copy or a synced home directory can, so the picker asks the platform
        # too rather than trusting the filesystem alone.
        if key == "gemini" and not missing:
            from install_antigravity_acp import platform_problem
            problem = platform_problem()
            if problem:
                out.append({"key": key, "label": spec["label"],
                            "available": False,
                            "why": problem.split("\n")[0],
                            "postureEnforced": False,
                            "tools": bool(spec.get("tools"))})
                continue
        if spec.get("unavailable"):
            ok, why = False, spec["unavailable"]
        elif not exe.exists():
            ok, why = False, f"not installed: {exe}"
        elif missing:
            ok, why = False, f"not installed: {missing[0]}"
        else:
            ok, why = True, spec.get("needs", "")
        out.append({"key": key, "label": spec["label"], "available": ok, "why": why,
                    # So the dialog can stop OFFERING a posture it cannot set.
                    "postureEnforced": bool(spec["posture_via_config_dir"]),
                    "tools": bool(spec.get("tools"))})
    # One pass over every append site above, so a lane added later cannot miss
    # its group by being appended somewhere this was forgotten.
    for item in out:
        gid = _group_of(item["key"])
        if gid:
            item["group"] = gid
            item["memberLabel"] = (item["key"].split(":", 1)[1]
                                   if ":" in item["key"] else item["label"])
    return out


class Pane:
    """One conversation: an agent process + its event history."""

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

    def __init__(self, agent, cwd, posture, mgr, model=None, effort=None):
        self.id = uuid.uuid4().hex[:12]
        self.agent = agent
        self.cwd = str(cwd)
        self.posture = posture if posture in POSTURES else DEFAULT_POSTURE
        self.mgr = mgr
        self.want_model = model
        self.want_effort = effort
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

    def _init_runtime(self):
        """Every field that is NOT persisted — the live half of a pane.

        ONE home for it, because there are three ways a Pane comes into being
        (__init__, from_meta's __new__, and the selftest's hand-built stub) and
        each one that sets these itself is a copy that drifts. It has drifted
        twice already: `commands` was added to __init__ and not to from_meta,
        which made /api/state 500 for EVERY pane after a restart.
        """
        self.error = None
        self.events = []
        self.pending = {}                 # requestId -> the permission payload
        self.client = None
        self.usage = {}
        self.config = {}          # {id: {value, label, options}} straight from ACP
        # Start with the local skill files; ACP may replace/augment this after
        # session/new, but the composer must be useful even if that notification
        # is missing or delayed.
        # A few lightweight restore/selftest paths construct Pane via __new__
        # before attaching metadata; default their catalog to Claude's local
        # skills until the real agent identity is available.
        self.commands = _skill_commands(getattr(self, "agent", "claude"))
        self.model = None
        self.effort = None
        self._seq = 0             # monotonic for the life of the pane
        self._queue = []          # type-ahead; drained strictly in order
        self._turn_running = False
        self._turn_lock = threading.Lock()
        # Bumped every time self.client is replaced (pause, resume). A _drain
        # thread captures its generation before calling the blocking
        # client.prompt(); if the generation has since moved on when prompt()
        # returns, this thread's client is not the pane's live one anymore and
        # it must touch NOTHING shared. See _drain(), pause(), resume().
        self._generation = 0
        self._expect_exit = False  # we are the ones killing it; not a fault
        self._since_rotate_check = 0
        self._replaying = False
        self._log = None
        self.last_activity = time.time()
        self._lock = threading.Lock()

    META_KEYS = ("id", "agent", "cwd", "posture", "title", "title_locked",
                 "minimized", "acp_session", "created", "want_model",
                 "want_effort", "order", "pinned")

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

    @classmethod
    def from_meta(cls, meta, mgr):
        """Rebuild a pane from disk WITHOUT starting an agent.

        It comes back `detached`: the transcript is there and the row is in the
        roster exactly as it was left, but no process is running. Spawning N
        agents at boot would be slow, expensive, and mostly wasted -- most
        panes will not be touched. resume() attaches on demand, and sending a
        message attaches implicitly.
        """
        p = cls.__new__(cls)
        p.id = meta["id"]
        p.agent = meta.get("agent") or "claude"   # null in old metas = claude
        if p.agent not in AGENTS:
            # A pane whose lane no longer exists on this build. snapshot()
            # reads AGENTS[agent] directly, so register a dead stub rather
            # than KeyError the whole of /api/state.
            AGENTS[p.agent] = {
                "label": p.agent, "argv": ["/nonexistent"],
                "posture_via_config_dir": False,
                "unavailable": "this lane no longer exists on this host — "
                               "the transcript remains readable"}
        p.cwd = meta.get("cwd", str(Path.home()))
        p.posture = meta.get("posture", DEFAULT_POSTURE)
        p.mgr = mgr
        p.title_locked = bool(meta.get("title_locked"))
        stored_title = meta.get("title")
        # A pre-fix pane's stored title IS the "CC" collision (see
        # _default_title) if it's un-renamed and literally the bare
        # directory name on a non-Claude agent -- that's not a name Craig
        # chose, it's the old bug frozen to disk. Migrate it on restore
        # rather than leave every already-open Grok/Codex/SSH pane reading
        # "CC" until individually renamed by hand.
        stale_collision = (stored_title and not p.title_locked and
                          p.agent != "claude" and stored_title == Path(p.cwd).name)
        p.title = (None if stale_collision else stored_title) or \
            Pane._default_title(p.agent, p.cwd)
        p.minimized = bool(meta.get("minimized"))
        p.order = meta.get("order")
        p.pinned = bool(meta.get("pinned"))
        p.created = meta.get("created", _now())
        p.acp_session = meta.get("acp_session")
        p.want_model = meta.get("want_model")
        p.want_effort = meta.get("want_effort")
        p._init_runtime()
        p.state = "detached"
        p.dir = STATE / "panes" / p.id
        p.dir.mkdir(parents=True, exist_ok=True)
        p.events = p._read_events()
        # Resume the counter past everything on disk, or a restored pane would
        # re-issue sequence numbers the browser already holds and its new
        # events would be discarded as duplicates.
        p._seq = max((e.get("seq", 0) for e in p.events), default=0)
        p._rotate_log()
        p._log = (p.dir / "events.jsonl").open("a", encoding="utf-8")
        return p

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

    def resume(self):
        """Attach a fresh agent process to this pane's existing conversation."""
        if self.state != "detached":
            raise ValueError(f"pane is {self.state}, not detached")
        if not self.acp_session:
            raise ValueError("this pane has no agent session to resume")
        if self._log is None:      # pause() closed it; reopen for this attachment
            self._log = (self.dir / "events.jsonl").open("a", encoding="utf-8")
        spec = AGENTS[self.agent]
        env = spawn_env(spec, self._config_dir())
        self._expect_exit = False        # a NEW process; its exit is real news
        with self._turn_lock:
            self._generation += 1        # a new attachment; retire any stale drain
        try:
            self.client = acp.AcpClient(spec["argv"], self.cwd, env=env,
                                        on_event=self._on_event,
                                        on_permission=self._on_permission)
            self.client.initialize()
            # The agent replays the whole transcript on load. We already have
            # it; emitting it again would double the conversation on screen.
            self._replaying = True
            try:
                r = self.client.load_session(self.acp_session, self.cwd,
                                              self._mcp_servers())
            finally:
                self._replaying = False
            self._absorb_config((r or {}).get("configOptions") or [])
            self.state = "ready"
            self.emit("resumed", {"model": self.model, "effort": self.effort,
                                  "config": self.config})
        except acp.AgentError as e:
            self._reap_failed_client()      # same leak as start(); see there
            self.state, self.error = "dead", f"could not resume: {e}"
            self.emit("dead", {"reason": self.error})
        self.save_meta()
        return self

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

    def _on_event(self, kind, data):
        if kind != "agent_thought_chunk":
            self._flush_thought()       # one coalesced event, in stream order
        if kind == "agent_message_chunk":
            self.emit("text", {"text": (data.get("content") or {}).get("text", "")})
        elif kind == "agent_thought_chunk":
            # Rendered only behind the pane's "every step" eye (app.js), the
            # same latch that reveals tool calls — quiet by default, native-
            # Claude gray italic when asked. Emitting (vs the old drop) is
            # what makes the choice the viewer's instead of the server's.
            # COALESCED, not per-fragment (2026-08-30 panel, 3/3 arms): ACP
            # streams thought as many small chunks, and one ring event per
            # chunk let a hidden monologue evict user/permission rows from
            # the client's capped slice and fire an SSE tick per fragment.
            # Buffer here; _flush_thought emits ONE event when any other
            # event kind arrives (bounded: a turn always ends in one).
            self._thought_acc = (getattr(self, "_thought_acc", "") or "") + \
                (data.get("content") or {}).get("text", "")
        elif kind in ("tool_call", "tool_call_update"):
            self.emit("tool", {
                "id": data.get("toolCallId"), "title": data.get("title"),
                "kind": data.get("kind"), "status": data.get("status"),
                "content": (data.get("content") or [])[:6],
                "locations": (data.get("locations") or [])[:8]})
        elif kind == "stall_notice":
            # The agent has been quiet a long time. This used to KILL the turn;
            # now it only says so, because a clock cannot tell a wedged agent
            # from a slow one and killing took Craig's live work with it. The
            # pane stays attached and keeps waiting; snapshot() will show it
            # `uncertain`, and pause/stop are his to press.
            self.emit("note", {"text": data.get("text") or "no output for a "
                                       "while — still attached and waiting"})
        elif kind == "permission_expired":
            # The request is finished whether or not a human touched it. Drop
            # it from `pending` so the rail stops offering an approval that
            # can no longer be delivered, and record WHY in the transcript —
            # a card that silently disappears is its own kind of lie.
            rid = data.get("requestId")
            if self.pending.pop(rid, None) is not None:
                self.emit("permission_expired",
                          {"requestId": rid,
                           "reason": data.get("reason") or "expired"})
            if self.state == "needs-you" and not self.pending:
                self.state = "ready"
        elif kind == "available_commands_update":
            # The agent tells us its own command list -- 70 of them, names and
            # descriptions, sent unprompted right after session/new. Corral
            # dropped it, so the composer could not complete a skill and Craig
            # had to already know the name to use one. Never a hardcoded list:
            # this arrives again whenever the agent's skills change, and Grok
            # and Claude will not advertise the same set.
            advertised = [{"name": c.get("name"), "description":
                           (c.get("description") or "")[:160]}
                          for c in (data.get("availableCommands") or [])
                          if c.get("name")]
            merged = {c["name"]: c for c in self.commands}
            for command in advertised:
                if command["name"] in merged:
                    merged[command["name"]].update(command)
                else:
                    merged[command["name"]] = command
            self.commands = sorted(merged.values(), key=lambda item: item["name"])[:400]
            self.emit("commands", {"n": len(self.commands)})
        elif kind == "plan":
            self.emit("plan", {"entries": (data.get("entries") or [])[:20]})
        elif kind == "usage_update":
            self.usage = data
        elif kind == "agent_exit":
            # A pause KILLS the process on purpose, and the reader thread
            # reports that exit asynchronously — so `pause()` set `detached`
            # and this handler raced in behind it with `dead`. Which one won
            # depended on thread timing, which meant a pane the operator
            # deliberately parked could come back reading as a crash.
            if self._expect_exit:
                return
            self.state = "dead"
            self._clear_pending("agent_exit")
            if data.get("closed"):
                self.error = None                  # deliberate: not a fault
                self.emit("closed", {"reason": data.get("reason")})
            else:
                self.error = data.get("reason")
                self.emit("dead", {"reason": self.error})

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

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self):
        spec = AGENTS[self.agent]
        env = spawn_env(spec, self._config_dir())
        self._expect_exit = False        # a NEW process; its exit is real news
        try:
            self.client = acp.AcpClient(spec["argv"], self.cwd, env=env,
                                        on_event=self._on_event,
                                        on_permission=self._on_permission)
            info = self.client.initialize()
            new = self.client.new_session_full(self.cwd, self._mcp_servers())
            self.acp_session = new.get("sessionId")
            self._absorb_config(new.get("configOptions") or [])
            if self.agent == "grok" and not self.config.get("model"):
                # Grok's ACP session reports configOptions: null -- never a
                # real offer, so _absorb_config left self.config empty and
                # remember_catalog (inside it) correctly persisted nothing.
                # Sets display state directly, with zero options, so it can
                # never render as a PICKER Craig or the new-pane dialog could
                # choose from. It still has to reach remember_catalog below,
                # though: the dialog's fillCfg reads entry.value (not just
                # entry.options) to show Grok's real model as a plain
                # informational line instead of a disabled dropdown -- found
                # 2026-08-23, live-tested with a real Grok turn: without this
                # call catalog.json's grok entry stays `{}` forever, no
                # matter how many turns run, and the dialog never leaves its
                # "never seen this agent" empty state.
                from grok_launcher import resolve_default_model, resolve_grok
                grok_bin = resolve_grok()
                model = resolve_default_model(grok_bin) if grok_bin else None
                if model:
                    self.config["model"] = {"value": model, "name": "Model",
                                            "realId": "model", "options": []}
                    self.model = model
                    self.mgr.remember_catalog(self.agent, self.config)
            for cid, want in (("model", self.want_model), ("effort", self.want_effort)):
                if want and want != "default":
                    try:
                        real_id = (self.config.get(cid) or {}).get("realId", cid)
                        r = self.client.set_config(self.acp_session, real_id, want)
                        self._absorb_config((r or {}).get("configOptions") or [])
                    except acp.AgentError as e:
                        self.emit("note", {"text": f"could not set {cid}={want}: {e}"})
            self.state = "ready"
            self.emit("ready", {
                "agent": self.agent, "cwd": self.cwd, "posture": self.posture,
                "acpSession": self.acp_session, "model": self.model,
                "effort": self.effort, "config": self.config,
                "agentInfo": info.get("agentInfo", {})})
            self.save_meta()
        except acp.AgentError as e:
            # The spawn may have SUCCEEDED and only the handshake failed —
            # initialize() timing out, session/new refused. Marking the pane
            # dead while self.client still held a live process orphaned the
            # whole group, invisibly, until reboot (Gemini adversarial review
            # 2026-08-31, finding 4). close() tolerates an already-dead group.
            self._reap_failed_client()
            self.state, self.error = "dead", str(e)
            self.emit("dead", {"reason": str(e)})
        return self

    def _mcp_servers(self):
        registry = getattr(self.mgr, "mcp", None)
        return registry.session_servers() if registry else []

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

    def set_config(self, config_id, value):
        if config_id not in ("model", "effort", "fast"):
            raise ValueError(f"{config_id!r} is not settable from here")
        # No advertised options at all -- e.g. Grok's ACP session reports
        # configOptions: null -- is a REFUSAL, not an unfiltered value to
        # forward. Letting it through used to reach the live agent and come
        # back as a raw "Method not found" AgentError instead of this clean
        # message (the failure Craig hit once the stale catalog offered
        # effort levels the vendor CLI has never supported).
        cfg = self.config.get(config_id) or {}
        allowed = {o["value"] for o in cfg.get("options", [])}
        if not allowed:
            raise ValueError(f"{AGENTS[self.agent]['label']} does not offer a "
                             f"{config_id!r} setting")
        if value not in allowed:
            raise ValueError(f"{value!r} is not offered for {config_id}; "
                             f"the agent lists {sorted(allowed)}")
        if self.client is None:
            # Detached (paused): the config survives from the live session but
            # there is no wire to speak on, and self.client.set_config here
            # was an AttributeError dressed as a 500 (Gemini adversarial
            # review 2026-08-31, finding 6). Model/effort are preferences the
            # attach path already applies — the want_ loop in _attach — so
            # store the choice, persist it, and let resume make it real.
            if config_id not in ("model", "effort"):
                raise ValueError(f"{config_id!r} needs a running agent — "
                                 f"resume this pane first")
            setattr(self, f"want_{config_id}", value)
            setattr(self, config_id, value)   # the pill shows the queued choice
            cfg["value"] = value
            self.save_meta()
            self.emit("config", {"model": self.model, "effort": self.effort,
                                 "config": self.config})
            return self.config.get(config_id)
        # Forward under the ADAPTER's own id (`realId`) -- Corral's "effort"
        # is a display alias, and the wire call has to speak the vendor's
        # vocabulary (e.g. Codex's `reasoning_effort`), not ours.
        r = self.client.set_config(self.acp_session, cfg.get("realId", config_id), value)
        self._absorb_config((r or {}).get("configOptions") or [])
        self.emit("config", {"model": self.model, "effort": self.effort,
                             "config": self.config})
        return self.config.get(config_id)

    def _config_dir(self):
        """A config dir Corral owns, carrying THIS pane's posture.

        ONLY the permission policy is meant to be ours. Everything else must
        be the config Craig actually uses, and the first cut got that wrong:
        an isolated dir holding nothing but credentials meant a pane had none
        of his 27 personal skills, none of his subagents or plugins, and not
        even ~/.claude/CLAUDE.md — so the whole prompt stack was absent from
        every conversation. Craig: "skills don't work in corral."

        Measured 2026-08-01, `claude -p` under each config dir:
          bare dir              -> 11 built-in skills, "NO CONTEXT"
          dir + skills/CLAUDE.md -> 38 skills, and it knows the ranch's name

        Capability directories are SYMLINKED, not copied: 27 skills copied per
        pane go stale the moment he edits one, and this dir is created fresh
        for every pane.

        Credentials AND ~/.claude.json are seeded from the real config so no
        second login is needed and the account's full entitlements apply. Both
        files are copied, never read into memory or logged. ~/.claude.json
        stays a COPY on purpose where the rest are links — Claude Code writes
        to it continuously, and several panes writing through to his real one
        is a corruption race for no gain.

        .claude.json is not optional. Measured 2026-08-01: a config dir holding
        only credentials offered ['default','opus[1m]','sonnet','haiku'] --
        Fable was MISSING -- while the same dir plus ~/.claude.json offered
        claude-fable-5[1m] as well. Craig noticed before I did ("why is fable
        not in the list"). Whatever entitlement state the model list is derived
        from lives in that file, so an isolated config dir that omits it
        silently downgrades which models the account can reach.
        """
        d = self.dir / "config"
        d.mkdir(parents=True, exist_ok=True)
        for src in (Path.home() / ".claude" / ".credentials.json",
                    Path.home() / ".claude.json"):
            dst = d / src.name
            if src.is_file() and not dst.is_file():
                try:
                    shutil.copy2(src, dst)
                    dst.chmod(0o600)
                except OSError:
                    pass      # a missing seed costs models, never correctness
        real = Path.home() / ".claude"
        for name in LINKED_CONFIG:
            src, dst = real / name, d / name
            if not src.exists():
                continue
            try:
                if dst.is_symlink():
                    if dst.readlink() == src:
                        continue
                    dst.unlink()
                elif dst.exists():
                    continue                  # something real is there; leave it
                dst.symlink_to(src)
            except OSError:
                pass          # a missing link costs a capability, never safety

        try:
            base = json.loads((real / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            base = {}
        merged = {k: v for k, v in base.items() if k not in SETTINGS_DROPPED}
        # Keep any allow/deny he has set; the pane owns defaultMode and nothing
        # else. Overwriting the whole permissions block would silently discard
        # a deny rule the day he writes one.
        perm = dict(base.get("permissions") or {})
        perm.update(POSTURES[self.posture])
        perm.setdefault("allow", [])
        perm.setdefault("deny", [])
        merged["permissions"] = perm
        (d / "settings.json").write_text(json.dumps(merged, indent=1), encoding="utf-8")
        return d

    def send(self, text):
        if self.state == "detached":
            self.resume()            # implicit: typing into a pane means using it
            if self.state == "dead":
                raise acp.AgentError(self.error or "could not resume")
        if self.state == "dead":
            raise acp.AgentError(f"pane is dead: {self.error}")
        if not self.client or not self.client.alive:
            raise acp.AgentError("agent is not running")
        text = (text or "").strip()
        if not text:
            raise ValueError("empty prompt")
        if len(text) > MAX_PROMPT:
            raise ValueError(f"prompt exceeds {MAX_PROMPT} chars")
        # First prompt names the conversation -- what you ASKED tells you more
        # than a generic default. Compares against the computed DEFAULT, not
        # "has a user event ever appeared in self.events": that ring is
        # bounded (MAX_EVENTS), so on a long-running pane the original first
        # prompt eventually rotates out, and the old any(...) scan silently
        # went blind and re-fired on Craig's NEXT message -- overwriting a
        # meaningful title with whatever he happened to type at turn 4,001.
        if not self.title_locked and self.title == self._default_title(self.agent, self.cwd):
            first = " ".join(text.split())[:42]
            self.title = first + ("…" if len(" ".join(text.split())) > 42 else "")
            self.save_meta()      # or a restart restores the generic name back
        # Light intercepts NOTHING. The full Corral answered a handful of
        # AI-OS slash commands itself (/recall, /brief, …) before the agent saw
        # them; those capabilities do not exist on this build, and a command
        # that silently falls through to the model as a raw prompt is worse
        # than one that was never offered — the operator reads a plausible
        # answer and thinks a capability ran. So every keystroke goes to the
        # agent, and `/` completion offers only what the agent itself
        # advertises (see _skill_commands and available_commands_update).
        # ONE TURN AT A TIME, per pane. send() used to spawn a thread per call
        # with no lock, so two fast messages produced overlapping
        # session/prompt RPCs on one ACP session: interleaved output, racing
        # turn_end/ready transitions, and a pane reporting `ready` while a
        # request was still in flight. Parallelism belongs ACROSS panes.
        #
        # A queue rather than a refusal, because typing ahead while an agent
        # works is normal and the message should still land — it just lands in
        # order. Bounded, so a stuck turn cannot accumulate forever.
        with self._turn_lock:
            if len(self._queue) >= MAX_QUEUED_TURNS:
                raise ValueError(
                    f"{MAX_QUEUED_TURNS} messages already waiting on this pane "
                    f"— it is still working through them")
            self._queue.append(text)
            self.emit("user", {"text": text})
            if text == "/clear":
                # The SDK special-cases this literal text: it resets ITS OWN
                # context and emits a `conversation_reset` notification that
                # the vendored ACP adapter drops on the wire (acp-agent.js,
                # "the client owns its own transcript view" -- Corral IS that
                # client, and until now did nothing with the ownership). Still
                # queue the real turn above so the agent's memory actually
                # clears; this marker just tells every renderer (this pane's
                # own replay, every connected browser) to fold everything up
                # to and including it out of view -- the same "gone" feel as
                # a real terminal's clear. Nothing is deleted: events.jsonl on
                # disk is untouched, so the fold is reversible via the
                # existing "load earlier" affordance, never a silent loss
                # (PRINCIPLES 18).
                self.emit("cleared", {})
            self.state = "busy"
            if self._turn_running:
                return
            self._turn_running = True
            threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        """Run queued prompts strictly in order until the pane is empty."""
        while True:
            with self._turn_lock:
                # `self.client` is read INSIDE the lock and used from a local:
                # pause() sets it to None from another thread, and
                # `self.client.prompt(...)` would then raise AttributeError —
                # which this loop did not catch, so the drain thread died with
                # `_turn_running` still True. After that the pane accepted
                # every message and ran none of them, silently, forever.
                client = self.client
                gen = self._generation
                if not self._queue or self.state == "dead" or client is None:
                    self._turn_running = False
                    return
                text = self._queue.pop(0)
            try:
                r = client.prompt(self.acp_session, text)
            except acp.AgentError as e:
                with self._turn_lock:
                    if self._generation != gen:
                        # pause()/resume() replaced this attachment while
                        # prompt() was blocked. That newer generation already
                        # owns _turn_running, _queue and self.state; a stale
                        # thread mutating any of them would be this dead
                        # client's failure overwriting a LIVE one nobody asked
                        # about. Measured failure mode: pause() sets
                        # `detached`, an immediate resume/send starts a new
                        # client and thread, the OLD prompt() then wakes with
                        # AgentError and this handler clobbered the resumed
                        # pane back to `dead`. gpt-5.6-sol, third-pass review,
                        # finding 4.
                        return
                    # Do not silently swallow what was still waiting.
                    dropped, self._queue = len(self._queue), []
                    self._turn_running = False
                # Since 2026-08-31 a prompt carries NO deadline, so the only
                # way to reach here is the agent process actually dying or its
                # stdin closing — never a clock deciding Craig's session is
                # over. close() is then a harmless no-op on an already-dead
                # group, and it stays because it is the one thing that reaps a
                # half-dead group's survivors. (It was added 2026-08-23, when a
                # TIMED-OUT pane kept streaming tool/text events to its own log
                # for minutes after Corral had told Craig the agent stopped —
                # the orphan that bug left behind. That timeout is gone now;
                # the reaping is still right.)
                client.close()
                self.state, self.error = "dead", str(e)
                self.emit("dead", {"reason": str(e) + (
                    f" ({dropped} queued message(s) were not sent)" if dropped else "")})
                return
            except Exception as e:              # noqa: BLE001
                # Anything else is a bug in us, and a bug that kills this
                # thread quietly is the worst outcome available: the pane keeps
                # taking messages and stops running them. Fail LOUDLY in the
                # transcript and leave the pane usable.
                with self._turn_lock:
                    if self._generation != gen:
                        return
                    dropped, self._queue = len(self._queue), []
                    self._turn_running = False
                self.emit("note", {
                    "text": f"the turn could not be run ({type(e).__name__}: {e})"
                            + (f"; {dropped} queued message(s) were dropped"
                               if dropped else "")})
                if self.state not in ("dead", "detached"):
                    self.state = "ready"
                return
            with self._turn_lock:
                if self._generation != gen:
                    return
            self._flush_thought()       # a turn ending on a thought still shows it
            self.emit("turn_end", {"stopReason": (r or {}).get("stopReason"),
                                   "usage": self.usage,
                                   "queued": len(self._queue)})
            if self.state != "dead":
                self.state = "needs-you" if self.pending else (
                    "busy" if self._queue else "ready")

    def answer(self, request_id, option_id):
        req = self.pending.get(request_id)
        if not req:
            raise ValueError("no such pending permission (already answered?)")
        valid = {o.get("optionId") for o in req.get("options") or []}
        if option_id not in valid:
            raise ValueError(f"invalid option {option_id!r}; expected one of {sorted(valid)}")
        # Refuse to GRANT what was never displayable, at the server too. The UI
        # withholds the allow buttons, but a control that only exists in the
        # browser is a suggestion — the gate has to be where the authority is.
        # From the PENDING record, which lives exactly as long as the authority
        # it guards. Reading it out of the bounded event ring meant a request
        # that outlived MAX_EVENTS became approvable (see _on_permission).
        rec = req.get("_gate") or {}
        kind = next((str(o.get("kind", "")) for o in req.get("options") or []
                     if o.get("optionId") == option_id), "")
        if rec.get("oversize") and not kind.startswith("reject"):
            raise ValueError(
                "this request was too large to display, so it cannot be "
                "approved here — only refused. An approval proves only what "
                "you could see.")
        ok = self.client.answer_permission(request_id, option_id)
        self.pending.pop(request_id, None)
        if self.state != "dead":
            self.state = "needs-you" if self.pending else "busy"
        # Bind the answer to the exact bytes that were on screen. Without the
        # digest the record says only WHICH option was chosen, not what for.
        self.emit("permission_answered", {"requestId": request_id,
                                          "optionId": option_id, "kind": kind,
                                          "digest": rec.get("digest"),
                                          "delivered": ok})
        return ok

    def rename(self, title):
        title = " ".join((title or "").split())[:60]
        if not title:
            raise ValueError("a name cannot be empty")
        self.title = title
        self.title_locked = True       # never auto-retitled again
        self.save_meta()
        self.emit("renamed", {"title": title})
        return title

    def set_minimized(self, flag):
        """Minimizing hides the PANE, never the pane's state.

        The roster keeps showing it, live, including a permission it is blocked
        on -- otherwise minimizing would be a way to make an agent wait forever
        while the UI looks calm, which is the dust-gathering failure wearing a
        new hat.
        """
        self.minimized = bool(flag)
        self.save_meta()
        return self.minimized

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

    def snapshot(self, since=0):
        # Ask the OS, not our own bookkeeping. `client.alive` only flips when
        # stdout closes or a write fails, so an adapter that wedged with its
        # pipe open still read `ready`. poll() is the ground truth, and where
        # the two disagree we say `uncertain` rather than pick the flattering
        # one — a status that lies is worse than a status that admits doubt.
        alive = bool(self.client and self.client.alive)
        idle = time.time() - getattr(self, "last_activity", time.time())
        state_override = None
        if alive:
            try:
                if self.client.p.poll() is not None:
                    # The process is GONE. `client.alive` merely had not
                    # noticed yet. This used to be reported as `uncertain`,
                    # which was wrong in the direction that matters: there is
                    # nothing uncertain about an exited process, and dressing a
                    # corpse as "maybe" is the flattering answer.
                    alive = False
                    state_override = "dead"
                elif self.state == "uncertain" and idle <= STALL_S:
                    state_override = "busy"     # it started talking again
                elif self.state == "busy" and idle > STALL_S:
                    # Alive, mid-turn, and nothing has come out of it for
                    # minutes. THIS is the uncertain case — the wedged adapter
                    # holding its pipe open, which the old check reported as a
                    # healthy `busy` indefinitely because poll() was still None.
                    state_override = "uncertain"
            except Exception:                       # noqa: BLE001
                pass
        if state_override and state_override != self.state:
            self.state = state_override
            # Tell the glass. This mutation used to be silent: the attention
            # tick would mark a wedged pane `uncertain` (or a corpse `dead`)
            # and no SSE event carried it, so the browser kept the pulsing
            # `busy` dot until a manual reload — defeating the observability
            # the state exists to provide (Gemini adversarial review
            # 2026-08-31). activity=False: see emit().
            self.emit("state", {"state": state_override}, activity=False)
        # Liveness from the PROCESS, not from our own bookkeeping — a manager
        # that believes its own state field reports a corpse as running.
        # `detached` is exempt: it means "restored from disk, deliberately not
        # running yet", which is a legitimate not-alive state. Folding it into
        # `dead` made every restored pane look like a crash.
        state = (self.state if alive or self.state in
                 ("dead", "detached", "uncertain") else "dead")
        return {
            "id": self.id, "agent": self.agent, "label": AGENTS[self.agent]["label"],
            "minimized": self.minimized, "titleLocked": self.title_locked,
            "order": self.order, "pinned": self.pinned,
            "model": self.model, "effort": self.effort, "config": self.config,
            "commands": self.commands,
            # How long since ANYTHING came out of this pane. "busy" is a
            # pulsing dot with no evidence behind it; this is the evidence.
            "idleS": int(idle),
            "cwd": self.cwd, "posture": self.posture, "title": self.title,
            # Whether Corral actually IMPOSED that posture. Only agents driven
            # through a CLAUDE_CONFIG_DIR are; `oc acp` runs under its own
            # policy. The dialog offered strict/edits/auto for every agent and
            # the pill displayed the choice regardless, so a Grok pane could
            # read `strict` while nothing had made it strict — a UI asserting a
            # safety property it never established.
            "postureEnforced": bool(AGENTS[self.agent]["posture_via_config_dir"]),
            # Whether this lane can read a file itself — what attaching a note
            # to it means (a reference, or a quoted excerpt).
            "tools": bool(AGENTS[self.agent].get("tools")),
            "state": state, "error": self.error, "created": self.created,
            "pending": list(self.pending.keys()),
            "usage": self.usage, "alive": alive,
            "events": [e for e in self.events if e["seq"] > since],
            "seq": self.events[-1]["seq"] if self.events else 0,
        }


CATALOG = STATE / "catalog.json"


class Manager:
    """Panes, plus the agent's own config CATALOG.

    The catalog is remembered on disk because the new-conversation dialog needs
    the model/effort option lists BEFORE any pane exists -- it used to scrape
    them off a live pane, so with nothing running (i.e. every fresh start) the
    pickers offered only "Default" and Craig could not choose a model for his
    first conversation. Craig: "now model and effort does not work."

    Still never hardcoded: this is the agent's own list, cached. Each new pane
    refreshes it, so a model appearing or disappearing upstream propagates on
    the next session rather than being frozen.
    """

    def __init__(self):
        self.panes = {}
        self.subscribers = []
        self.not_restored = 0
        self._lock = threading.Lock()
        self.mcp = mcp.Registry()
        self.catalog = self._load_catalog()
        self.restore()
        # Off the constructor's thread: a probe is a network call, and Corral
        # must finish starting whether or not a vendor answers. Results land in
        # the same catalog the dialog reads, so a seeded list appears on the
        # next state() poll rather than blocking the UI on boot.
        threading.Thread(target=self.seed_catalogs, daemon=True).start()

    def seed_catalogs(self):
        """Fill in the model list for lanes that can enumerate without a pane.

        Only for an agent we have NEVER seen options from: a remembered list
        came from a real session and is better evidence than a probe, and an
        agent that truthfully offers no choice (grok) must keep its empty list
        rather than have one invented for it. Best-effort throughout — every
        failure leaves the honest "never seen this agent" empty state, which is
        exactly what the dialog already renders correctly.
        """
        for agent, spec in list(AGENTS.items()):
            probe = spec.get("catalog_probe")
            if not probe:
                continue
            if ((self.catalog.get(agent) or {}).get("model") or {}).get("options"):
                continue                      # already known from a real session
            try:
                got = probe()
            except Exception as e:  # noqa: BLE001 — never break startup
                print(f"corral-light: catalog probe for {agent} failed: {e}",
                      file=sys.stderr, flush=True)
                continue
            if not got:
                continue
            values, default = got
            self.remember_catalog(agent, {"model": {
                "name": "Model", "realId": "model",
                "value": default if default in values else values[0],
                "options": [{"value": v, "name": v, "description": ""}
                            for v in values]}})

    @staticmethod
    def _load_catalog():
        try:
            return json.loads(CATALOG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def remember_catalog(self, agent, config):
        """Record what THIS agent offers RIGHT NOW. Keyed by agent, since
        opencode and gemini will not offer Claude's models.

        Every caller passes a live ACP response (new_session_full,
        load_session, or a set_config ack) — never a "haven't checked yet"
        placeholder. So an empty config is not missing data to skip; it is
        the agent truthfully reporting it offers nothing, and has to
        overwrite whatever was remembered before. Skipping the write here
        is how grok's catalog entry survived the 2026-08-13 transport switch
        from oc/opencode (real model+effort options) to the vendor CLI
        (configOptions: null) with its old, wrong options still served to
        the new-pane dialog — a stale catalog offering effort levels the
        live transport rejects with "Method not found".

        Keeps `value` alongside `options` now: Grok reports a real model with
        an EMPTY options list (one fixed choice, not a picker), and dropping
        `value` along with the (correctly) dropped empty list left the dialog
        unable to tell "never seen this agent" from "seen it, it offers no
        choice" — both rendered as the same disabled dropdown. A key with a
        value but no options still survives the filter below.
        """
        self.catalog[agent] = {k: {"name": v.get("name"), "options": v.get("options", []),
                                   "value": v.get("value")}
                               for k, v in (config or {}).items()
                               if v.get("options") or v.get("value")}
        try:
            CATALOG.parent.mkdir(parents=True, exist_ok=True)
            CATALOG.write_text(json.dumps(self.catalog, indent=1), encoding="utf-8")
        except OSError:
            pass

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

    def create(self, agent, cwd, posture=DEFAULT_POSTURE, model=None, effort=None):
        if agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r}")
        spec = AGENTS[agent]
        if spec.get("unavailable"):
            raise ValueError(f"{spec['label']}: {spec['unavailable']}")
        exe = Path(spec["argv"][0])
        if not exe.exists():
            raise ValueError(f"{spec['label']} is not installed at {exe}")
        # `requires` too, not just argv[0]. Four of the five lanes here launch
        # through an interpreter, so argv[0] is `python3` and exists on any
        # host — the check above passes for a lane whose adapter or vendor
        # binary is absent, and the refusal then arrives as a dead pane instead
        # of a sentence in the dialog. available_agents() already greys these
        # out; this is the same answer at the point that acts on it.
        missing = [p for p in spec.get("requires", ()) if not Path(p).exists()]
        if missing:
            raise ValueError(f"{spec['label']} is not installed: {missing[0]}")
        cwd = Path(cwd).expanduser()
        if not cwd.is_dir():
            raise ValueError(f"not a directory: {cwd}")
        # Reserve the slot under the lock, and REGISTER before starting.
        # Unlocked, two simultaneous /api/session/new calls both read a count
        # under the cap and both proceed. And starting first meant Pane.start()
        # broadcast `ready` for a pane the manager did not yet contain, so
        # another open browser refreshed, still could not find it, and sat
        # stale until something unrelated woke it up.
        with self._lock:
            live = [p for p in self.panes.values()
                    if p.state not in ("dead", "detached")]
            if len(live) >= MAX_PANES:
                raise ValueError(f"{MAX_PANES} live panes is the cap — close one first")
            if len(self.panes) >= MAX_ROSTER:
                raise ValueError(
                    f"{MAX_ROSTER} panes are already on the roster, live or "
                    f"detached — close or forget one before starting another")
            pane = Pane(agent, cwd, posture, self, model, effort)
            self.panes[pane.id] = pane
        try:
            pane.start()
        except Exception:
            self.panes.pop(pane.id, None)     # never leave a phantom in the roster
            raise
        return pane

    def restore(self):
        """Bring back up to MAX_PANES panes that were not deliberately closed.

        Craig: "I would like to be able to close the local window and open it
        again and have all my tabs there the way I left them." Closing the
        BROWSER always worked -- the panes lived in server memory. Restarting
        the SERVER did not, and deploying is how that kept happening to him.

        Restored panes come back `detached`: title, order, minimize state and
        the transcript's bounded recent tail are as left (see _read_events --
        it never reads a rotated events.jsonl.1, so a conversation past
        MAX_LOG_BYTES or MAX_EVENTS has already lost its earlier turns before
        restore ever runs), with no agent process running until one is wanted.
        Anything past MAX_PANES is skipped, not restored -- `not_restored`
        below is how that stays visible instead of silent.
        """
        root = STATE / "panes"
        if not root.is_dir():
            return
        metas = []
        for d in root.iterdir():
            try:
                m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue                     # no meta = pre-persistence pane
            if m.get("closed") or not m.get("id"):
                continue
            metas.append(m)
        metas.sort(key=lambda m: (0 if m.get("pinned") else 1,
                                  m.get("order") if m.get("order") is not None else 10_000,
                                  m.get("created") or ""))
        # The HEAD of that sort, not the tail. `metas[-MAX_PANES:]` took the
        # far end — so once more than MAX_PANES panes were saved, the panes
        # dropped on restart were exactly the pinned and earliest-ordered ones.
        # Pinning made a pane MORE likely to disappear. Positive control
        # (2026-08-01): 15 metas, 2 pinned; the old slice kept neither.
        skipped = max(0, len(metas) - MAX_PANES)
        for m in metas[:MAX_PANES]:
            try:
                self.panes[m["id"]] = Pane.from_meta(m, self)
            except Exception:
                continue                     # one bad pane must not block the rest
        self.not_restored = skipped          # said out loud, not silently dropped

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

    def reopen(self, pane_id):
        """Bring an archived conversation back, detached."""
        d = STATE / "panes" / pane_id
        try:
            m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError(f"no archived conversation {pane_id}")
        if not m.get("closed"):
            raise ValueError("that conversation is not archived")
        if pane_id in self.panes:
            return self.panes[pane_id]
        pane = Pane.from_meta(m, self)
        pane.save_meta(closed=False)      # or the next restart re-archives it
        self.panes[pane_id] = pane
        pane.emit("reopened", {})
        return pane

    def get(self, pane_id):
        p = self.panes.get(pane_id)
        if not p:
            raise ValueError(f"no pane {pane_id}")
        return p

    def reorder(self, ids):
        """Set an explicit order from a list of pane ids.

        Panes rendered in creation order, which is not an ordering system —
        Craig's whole complaint about terminals was that you cannot arrange
        running work. Unknown ids are ignored rather than rejected: the client
        may be a moment stale, and a drag should not fail because a pane closed
        while the mouse was down.
        """
        if not isinstance(ids, list):
            raise ValueError("reorder needs a list of pane ids")
        seen = 0
        for i, pid in enumerate(ids[:MAX_PANES * 4]):
            p = self.panes.get(pid)
            if p:
                p.order = i
                p.save_meta()
                seen += 1
        self._resort()
        return seen

    def set_pinned(self, pane_id, flag):
        p = self.get(pane_id)
        p.pinned = bool(flag)
        p.save_meta()
        self._resort()
        return p.pinned

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
        p.save_meta(closed=True)     # same hole as stop(): dismissed, then back
        self.panes.pop(pane_id, None)
        return pane_id

    def state(self, since=None):
        since = since or {}
        # list() first — the GIL-atomic copy hub.py's attention loop already
        # uses. Iterating the live dict spans Python-level snapshot() calls,
        # and another HTTP thread creating or closing a pane mid-iteration
        # raises RuntimeError and 500s /api/state.
        return {"panes": [p.snapshot(since.get(p.id, 0)) for p in list(self.panes.values())],
                "agents": available_agents(),
                # Ships WITH `agents`, not beside it: the per-lane `group` tag
                # is meaningless without the definitions that name and order the
                # groups. They were briefly served from local.py instead — a
                # different consumer entirely — so the browser tagged every lane
                # with a group it had no label for (found live, 2026-08-31).
                "agentGroups": agent_groups(),
                "postures": sorted(POSTURES),
                # Where a new conversation starts, when nothing else is
                # remembered. The BROWSER used to carry this as a literal
                # ('/home/cvande/Github/CC'), inherited from the full Corral —
                # which on any other machine is a directory that does not
                # exist, so the first thing a new install did was refuse to
                # start a pane. The host knows its own home; the client should
                # not be guessing at it.
                "defaultCwd": str(Path.home()),
                "catalog": self.catalog,
                "archived": self.archived(),
                # Panes the cap kept from being restored. They are still on
                # disk and invisible in the product, which is fine only if the
                # product SAYS so — an unannounced drop reads as a deletion.
                "notRestored": self.not_restored,
                "at": int(time.time())}
