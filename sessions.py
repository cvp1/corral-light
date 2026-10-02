#!/usr/bin/python3
"""sessions — Corral Light's session manager: spawn agents, hold state, persist
events.

Corral stores only what ACP does not: which agent process is live, the ordered
event stream per pane, and pending permission requests. Each pane's permission
posture is set by Corral, never inherited from the host's ambient config.
"""
import contextlib
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
import claude_auth
import ledger
import mcp
import worktrees as _wt

ROOT = Path(__file__).resolve().parent
# Separate from the full Corral's state dir so two hubs never share panes or keys.
STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))
# Optional node install not on PATH; ignored when absent.
_NODE_BIN = Path(os.environ.get("CORRAL_NODE_BIN",
                                Path.home() / ".hermes" / "node" / "bin"))
NODE_BIN = _NODE_BIN if _NODE_BIN.is_dir() else None
ADAPTER = Path(os.environ.get("CORRAL_CLAUDE_ADAPTER",
                              ROOT / "spike" / "node_modules" / ".bin" / "claude-agent-acp"))

# ── the shared core ───────────────────────────────────────────────────────
# Pane/Manager behaviour shared with the full Corral lives in corral_core;
# bounds are re-exported by name for importers of this module.
from corral_core import sessions as _core                        # noqa: E402

DEFAULT_POSTURE = _core.DEFAULT_POSTURE
DISPLAY_STATES = _core.DISPLAY_STATES
IDLE_DISPLAY_S = _core.IDLE_DISPLAY_S
display_state = _core.display_state
MAX_EVENTS = _core.MAX_EVENTS
MAX_LOG_BYTES = _core.MAX_LOG_BYTES
MAX_PANES = _core.MAX_PANES
MAX_PENDING_PERMS = _core.MAX_PENDING_PERMS
MAX_PERM_BYTES = _core.MAX_PERM_BYTES
POSTURES = _core.POSTURES
QUOTE_CHARS = _core.QUOTE_CHARS
_now = _core._now

_CATALOG_LOCK = threading.Lock()   # one writer at a time for catalog.json


# Re-exported for importers of the old name.
_QueuedText = _core.QueuedText
TURN_VIAS = _core.TURN_VIAS

MAX_ROSTER = MAX_PANES * 5      # cap on all panes, live or detached
MAX_PROMPT = 200_000
MAX_QUEUED_TURNS = 4           # type-ahead depth per pane; beyond it, say no

# ── own-branch worktrees (docs/worktree-review-plan.md) ──────────────────────
# Lanes that passed the Phase 0 matrix (docs/worktree-phase0.md). Gemini is held:
# its lane runs `yolo` whatever the posture. Never host:* (cwd ignored) or ollama
# (no tools). Override with CORRAL_LIGHT_WORKTREE_LANES=a,b; kill switch
# CORRAL_LIGHT_WORKTREES_ENABLED=0 refuses new worktree panes (existing ones
# still resume and can still be discarded).
WORKTREE_LANES = ("claude", "codex", "grok")
WORKTREE_PREAMBLE = (
    "[Corral] You are working in a git worktree on your own branch. Work only "
    "inside this folder. Do not push, rebase, reset, or touch other branches; "
    "the user reviews and publishes.")


def worktree_lanes():
    raw = os.environ.get("CORRAL_LIGHT_WORKTREE_LANES")
    return tuple(x.strip() for x in raw.split(",") if x.strip()) if raw else WORKTREE_LANES


def worktree_refusal(agent):
    """Why `agent` cannot start on its own branch here, or None."""
    if os.environ.get("CORRAL_LIGHT_WORKTREES_ENABLED", "1") == "0":
        return "own branches are switched off (CORRAL_LIGHT_WORKTREES_ENABLED=0)"
    if agent.startswith("host:"):
        return "remote lanes ignore the folder, so they cannot use an own branch"
    if agent == "ollama":
        return "the local lane has no tools, so an own branch would do nothing"
    if agent not in worktree_lanes():
        return f"own branches are not enabled for the {agent} lane yet"
    return None


def inside_worktree_root(path):
    root = os.path.realpath(_wt.worktree_root())
    p = os.path.realpath(path)
    return p == root or p.startswith(root + os.sep)

STALL_S = 300                  # busy with nothing emitted for this long = suspect
PARKED_PREVIEW_CHARS = 120     # chars of each parked message quoted in a note

# Config a pane inherits from ~/.claude by symlink (stays current, unlike a copy).
LINKED_CONFIG = ("skills", "agents", "commands", "plugins", "prompts", "CLAUDE.md")

# settings.json keys a pane does not inherit: permissions (the pane's posture
# owns it), hooks (auto-run code), statusLine (no ACP surface).
SETTINGS_DROPPED = ("permissions", "hooks", "statusLine")


# Corral postures mapped to the agent's ACP `mode` values, set live via
# session/set_config_option (no config dir, so no macOS Keychain issue).
# A posture with no entry is left unset and reported, never guessed.
POSTURE_MODE = {
    "strict": "default",       # prompt on dangerous operations
    "edits":  "acceptEdits",   # auto-accept edits, prompt the rest
    "auto":   "auto",          # a classifier decides; still escalates
}

GROK_LAUNCHER = ROOT / "grok_launcher.py"
OLLAMA_ACP = ROOT / "ollama_acp.py"
NATIVE_ANTIGRAVITY_LAUNCHER = ROOT / "antigravity_acp_launcher.py"
NATIVE_ANTIGRAVITY_BIN = Path.home() / ".local/lib/corral/antigravity-acp/agy_acp_server.par"
NATIVE_ANTIGRAVITY_HELPER = NATIVE_ANTIGRAVITY_BIN.with_name("localharness_external")


# --- Catalog probes: a lane's model list WITHOUT starting a pane ------------
# Seeds the new-pane dialog's model list from the lane's own catalog;
# a pane's real session/new still overwrites it.
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
        # Preferred over the config dir: the mode is acked on the wire.
        "posture_via_acp_mode": True,
        "tools": True,
        # A live handshake: checks the lane works and reads its model/effort lists.
        "catalog_probe": lambda: __import__("lane_probe").catalog_probe("claude"),
        "probe_config": lambda a: __import__("lane_probe").full_config(a),
        "live_probe": True,
        # Detects a lapsed login, which session/new alone cannot see.
        "auth_status": lambda: __import__("claude_auth").status(),
    },
    "codex": {
        # ChatGPT via codex-acp; the launcher uses a dedicated CODEX_HOME and keeps
        # auth inside the CLI's own state.
        "label": "ChatGPT (Codex)",
        "argv": [sys.executable, str(ROOT / "codex_launcher.py")],
        "posture_via_config_dir": False,
        "tools": True,
        "needs": "needs ChatGPT login (device-auth) — see codex_launcher.py",
    },
    "grok": {
        # Grok CLI's own ACP stdio mode; the CLI owns auth.
        "label": "Grok",
        "argv": [sys.executable, str(GROK_LAUNCHER)],
        "requires": (str(GROK_LAUNCHER),),
        "posture_via_config_dir": False,
        "tools": True,
        "needs": "needs Grok CLI authentication",
    },
    "gemini": {
        # Native antigravity-acp server. Do not seed the catalog from `agy models`:
        # it can list ids the server rejects. Install/verify via install_antigravity_acp.py.
        "label": "Antigravity (Gemini)",
        "argv": [sys.executable, str(NATIVE_ANTIGRAVITY_LAUNCHER)],
        # The lane's own approval mode, applied at session/new; the vendor enforces it.
        "default_config": {"mode": "yolo"},
        "requires": (str(NATIVE_ANTIGRAVITY_LAUNCHER),
                     str(NATIVE_ANTIGRAVITY_BIN),
                     str(NATIVE_ANTIGRAVITY_HELPER)),
        "posture_via_config_dir": False,
        "tools": True,
        "needs": "official Google native ACP — authenticated by Antigravity OAuth",
    },
    "ollama": {
        # Local Ollama over ACP: chat only, no tools, no permission rail.
        "label": "Local (Ollama) — chat only",
        "argv": ["/usr/bin/env", "python3", str(OLLAMA_ACP)],
        "requires": (str(OLLAMA_ACP),),
        "posture_via_config_dir": False,
        # No filesystem access: attached notes must be quoted inline.
        "tools": False,
        # No mcpServers support: can receive peer messages but not send them.
        "mcp": False,
        "needs": "answers from the local Ollama — no key, works offline; "
                 "chat only, no tools and no permission rail",
        "catalog_probe": lambda: _probe_ollama(),
    },
}


# --- Host shell lanes: one SSH SHELL pane per configured host ---------------
# Hosts come from a hand-written JSON file, not ~/.ssh/config (which lists
# non-shell hosts). ssh_acp.py runs exactly what the user types: no LLM, no
# permission rail. Never hand this lane to an agent as a tool.
SSH_ADAPTER = ROOT / "ssh_acp.py"
# [{"name", "ip", "user", "key"}] (`ip` may be an ssh_config alias), or
# [{"name", "connect": "<command whose stdin is a bash>"}] for a local test lane.
EXTRA_SSH_HOSTS = Path(os.environ.get(
    "CORRAL_LIGHT_SSH_HOSTS",
    str(Path.home() / ".config/corral-light/ssh-hosts.json")))
MAX_SSH_HOSTS = 8


def _live_ssh_hosts():
    hosts = []
    try:
        if EXTRA_SSH_HOSTS.is_file():
            for h in json.loads(EXTRA_SSH_HOSTS.read_text())[:MAX_SSH_HOSTS]:
                if not h.get("name"):
                    continue
                entry = {"name": h["name"], "tier": h.get("tier", ""),
                         "argv": [sys.executable, str(SSH_ADAPTER),
                                  "--name", h["name"]],
                         "needs": "shell over ssh — runs what you type, as you"}
                if h.get("connect"):
                    entry["env"] = {"SSH_ACP_CONNECT": h["connect"]}
                else:
                    entry["argv"] += ["--ip", h.get("ip", "")]
                    if h.get("key"):
                        entry["argv"] += ["--key", h["key"]]
                    if h.get("user"):
                        entry["argv"] += ["--user", h["user"]]
                    entry["needs"] = (f"shell over ssh to {h.get('ip', '?')} — "
                                      f"runs what you type, as you")
                hosts.append(entry)
    except Exception as e:  # noqa: BLE001 — a malformed file must not break the picker
        print(f"corral-light: {EXTRA_SSH_HOSTS} unreadable: {e}",
              file=sys.stderr, flush=True)
    return hosts


def refresh_host_lanes():
    """(Re)build one `host:<name>` shell lane per configured host.

    Removed hosts are tombstoned, not dropped, so existing transcripts still render.
    """
    current = {}
    for h in _live_ssh_hosts():
        key = f"host:{h['name']}"
        tier = f" ({h['tier']})" if h.get("tier") else ""
        spec = {"label": f"SSH — {h['name']}{tier}",
                "argv": h["argv"],
                "requires": (str(SSH_ADAPTER),),
                "posture_via_config_dir": False,
                # No tools, so no permission rail.
                "tools": False,
                "needs": h.get("needs", "")}
        if h.get("env"):
            spec["env"] = h["env"]
        current[key] = spec
    for key, spec in AGENTS.items():
        if isinstance(key, str) and key.startswith("host:") and key not in current:
            spec["unavailable"] = (f"no longer listed in {EXTRA_SSH_HOSTS.name} "
                                   "— the transcript remains readable")
    AGENTS.update(current)


refresh_host_lanes()


MAX_CWD_SUGGESTIONS = 24


def default_cwd():
    """Default cwd for a new conversation: ~/aios if present, else home."""
    aios = Path.home() / "aios"
    return aios if aios.is_dir() else Path.home()


def cwd_suggestions(recent=()):
    """Existing directories to suggest as a cwd, best first.

    Open panes' dirs, then checkouts, then common containers under home; bounded.
    """
    out, seen = [], set()

    def add(path):
        try:
            p = Path(path).expanduser()
            s = str(p)
        except (OSError, ValueError):
            return
        if s in seen or not p.is_dir():
            return
        seen.add(s)
        out.append(s)

    for c in recent:                       # panes already open here
        add(c)
    home = Path.home()
    add(home / "aios")                     # AIOS workspace, if Seed is installed
    add(home)
    containers = []
    for name in ("Github", "github", "Projects", "projects", "src", "code",
                 "dev", "Developer", "Documents", "repos", "work", "notes"):
        d = home / name
        if d.is_dir():
            containers.append(d)
    for d in containers:
        add(d)
    # One level inside each container, checkouts first; not recursive.
    for d in containers:
        try:
            children = sorted(x for x in d.iterdir()
                              if x.is_dir() and not x.name.startswith("."))
        except OSError:
            continue
        for x in children:
            if (x / ".git").exists():
                add(x)
        for x in children:
            add(x)
        if len(out) >= MAX_CWD_SUGGESTIONS:
            break
    return out[:MAX_CWD_SUGGESTIONS]




def seed_config_dir(d, posture):
    """A config dir Corral owns, carrying THIS pane's posture.

    Links capability dirs from ~/.claude and copies ~/.claude.json (needed for
    model entitlements). Returns None if no usable credential can be carried.
    """
    d = Path(d)
    real = Path.home() / ".claude"
    if darwin_keychain_blocks_isolation():
        return None            # platform fact, not a credential-content one
    d.mkdir(parents=True, exist_ok=True)
    # Owner-only: the dir carries a credential. Applied on every call.
    try:
        d.chmod(0o700)
    except OSError:
        pass
    # Without a usable credential a private dir cannot authenticate; return None
    # so the caller falls back to the user's own config.
    cred_dst = d / ".credentials.json"
    cred_src = real / ".credentials.json"
    # Link, not copy: a copy goes stale when another holder refreshes the token.
    # A metadata-only stub (Keychain-backed account) authenticates nothing.
    if usable_credential(cred_src):
        if not _core.link_shared_credential(cred_src, cred_dst) and not (
                cred_dst.is_symlink() or cred_dst.is_file()):
            return None
    elif not (cred_dst.is_symlink() or cred_dst.is_file()):
        return None                 # nothing usable, and nothing to fall back on
    # else: keep an existing link through a transient bad read of the source.
    src = Path.home() / ".claude.json"
    if src.is_file() and not (d / src.name).is_file():
        try:
            shutil.copy2(src, d / src.name)
            (d / src.name).chmod(0o600)
        except OSError:
            pass          # a missing seed costs models, never correctness
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
    # Keep the host's deny rules, drop its allow rules (a host allow would bypass
    # a strict posture), then overlay defaultMode from POSTURES.
    host_perm = base.get("permissions") or {}
    perm = {"allow": [], "deny": list(host_perm.get("deny") or [])}
    perm.update(POSTURES[posture])
    merged["permissions"] = perm
    (d / "settings.json").write_text(json.dumps(merged, indent=1), encoding="utf-8")
    return d


# Ambient vendor credentials never reach a pane; the strip list lives in
# corral_core. Opt-out: CORRAL_LIGHT_ALLOW_VENDOR_ENV=1.
STRIP_ENV_PREFIXES = _core.STRIP_ENV_PREFIXES
vendor_env_present = _core.vendor_env_present
strip_prefixes = _core.strip_prefixes


def spawn_env(spec, config_dir=None):
    """The environment one agent process launches under (start and resume)."""
    env = {}
    if NODE_BIN:
        env["PATH"] = f"{NODE_BIN}:{os.environ.get('PATH', '')}"
    env.update(spec.get("env") or {})
    # None: no private dir is possible, so the agent uses ~/.claude and the pane
    # reports postureEnforced: false.
    if spec["posture_via_config_dir"] and config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env


def darwin_keychain_blocks_isolation():
    """True when per-pane CLAUDE_CONFIG_DIR isolation cannot work on this platform.

    On macOS, setting CLAUDE_CONFIG_DIR suffixes the Keychain service name the
    SDK looks up, so the existing login is not found.
    """
    return sys.platform == "darwin"


def usable_credential(path):
    """Does this credentials file carry a non-empty access/refresh token?

    Searches any nesting depth; never returns or logs the value.
    """
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    def has_token(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if (k in ("accessToken", "refreshToken", "access_token",
                          "refresh_token")
                        and isinstance(v, str) and v.strip()):
                    return True
                if has_token(v):
                    return True
        elif isinstance(o, list):
            return any(has_token(x) for x in o)
        return False

    return has_token(doc)


def posture_enforceable(spec):
    """Can Corral impose a posture on this lane on this host?"""
    # ACP mode needs no config dir; the live pane reports what it actually got.
    if spec.get("posture_via_acp_mode"):
        return True
    if not spec.get("posture_via_config_dir"):
        return False
    if darwin_keychain_blocks_isolation():
        return False
    return usable_credential(Path.home() / ".claude" / ".credentials.json")


def _skill_commands(agent):
    """Local skill/command catalog for `/` completion before the agent advertises its own."""
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
# Picker families. A lane joins by explicit `keys` (wins) or key `prefix`.
# Order is display order; `agents` stays first so its first live lane is preselected.
AGENT_GROUPS = {
    "agents": {
        "label": "Agents",
        "keys": ("claude", "codex", "grok", "gemini", "ollama"),
        "hint": "one conversation, one process, one transcript",
    },
    "ssh": {
        "label": "SSH — remote shell",
        "prefix": "host:",
        "hint": "one persistent shell on the host; runs exactly what you type, "
                "with no agent and no permission rail",
    },
}
def _rig_role(role_id, agent, posture):
    """A rig seat's `role`, resolved by this product's roles.py."""
    import roles                                        # noqa: WPS433
    from corral_core import rigs as _rigs
    return _rigs.resolve_with(roles, role_id, agent, posture)


# Configure the core at import, before any pane exists.
_core.configure(AGENTS=AGENTS, AGENT_GROUPS=AGENT_GROUPS, STATE=STATE,
                ALLOW_VENDOR_ENV_VAR="CORRAL_LIGHT_ALLOW_VENDOR_ENV",
                ROLE_RESOLVER=_rig_role, ROSTER_CAP=MAX_ROSTER)

# Derived from STATE, so it is only correct after configure().
CATALOG = _core.CATALOG
_group_of = _core._group_of
agent_groups = _core.agent_groups






def available_agents():
    """The picker's lane list: only what can actually run on this host."""
    # Re-read the host file on every render so new hosts appear without a restart.
    refresh_host_lanes()
    out = []
    # Reported on every lane so the operator sees it when choosing.
    stripped = vendor_env_present()
    for key, spec in AGENTS.items():
        exe = Path(spec["argv"][0])
        # `requires` covers files beyond argv[0] (e.g. the script an interpreter runs).
        missing = [p for p in spec.get("requires", ()) if not Path(p).exists()]
        # grok: the launcher checks binary and auth; argv[0] alone cannot.
        if key == "grok":
            from grok_launcher import unavailable_reason
            reason = unavailable_reason()
            out.append({"key": key, "label": spec["label"],
                        "available": reason is None, "why": reason or "",
                        "postureEnforced": bool(spec["posture_via_config_dir"]),
                        "tools": bool(spec.get("tools"))})
            continue
        # codex: adapter present AND logged in, resolved by its launcher.
        if key == "codex":
            from codex_launcher import unavailable_reason
            reason = unavailable_reason()
            out.append({"key": key, "label": spec["label"],
                        "available": reason is None, "why": reason or "",
                        "postureEnforced": bool(spec["posture_via_config_dir"]),
                        "tools": bool(spec.get("tools"))})
            continue
        # ollama needs a running server, not just the adapter on disk.
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
        # Antigravity is a pinned Linux x86-64 binary: check the platform before files,
        # then the sign-in method (the server handshakes without one but refuses session/new).
        if key == "gemini":
            from install_antigravity_acp import auth_problem, platform_problem
            problem = platform_problem() or (None if missing else auth_problem())
            if problem:
                out.append({"key": key, "label": spec["label"],
                            "available": False,
                            "why": problem.split("\n")[0],
                            "postureEnforced": False,
                            "tools": bool(spec.get("tools"))})
                continue
        # live_probe lanes are judged by a real (cached) handshake, which also fills
        # the model/effort pickers.
        if spec.get("live_probe") and not missing:
            import lane_probe
            r = lane_probe.probe(key)
            ok, why = bool(r["ok"]), r["error"] or spec.get("needs", "")
            # A lapsed login passes the handshake but fails at the first prompt; the
            # credential's expiry overrides.
            auth = spec.get("auth_status")
            sign_in = False
            if ok and auth:
                a = auth() or {}
                if a.get("ok") is False:
                    ok, why = False, a.get("why") or why
                    sign_in = True     # the picker offers Sign in
                elif a.get("why"):
                    why = a["why"]
            out.append({"key": key, "label": spec["label"],
                        "available": ok, "why": why, "signIn": sign_in,
                        "postureEnforced": posture_enforceable(spec),
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
                    # Asks the host, not just the lane, whether a posture can be set.
                    "postureEnforced": posture_enforceable(spec),
                    "tools": bool(spec.get("tools"))})
    if stripped:
        note = _core.vendor_env_note(stripped)
        for item in out:
            item["envNote"] = note
    # Group every lane in one pass after all appends.
    for item in out:
        gid = _group_of(item["key"])
        if gid:
            item["group"] = gid
            item["memberLabel"] = (item["key"].split(":", 1)[1]
                                   if ":" in item["key"] else item["label"])
    return out


class Pane(_core.PaneBase):
    """One conversation: an agent process + its event history."""

    # worktree_id: the registry entry (worktrees.py) this pane owns, or None.
    META_KEYS = _core.PaneBase.META_KEYS + ("worktree_id",)
    worktree_id = None



    def _init_runtime(self):
        """Initialise every non-persisted field; shared by all construction paths."""
        self.error = None
        self.dead_cause = None            # "auth" when the login killed it
        self.dead_login = None            # the login (refresh expiry) it died under
        self.events = []
        self.pending = {}                 # requestId -> the permission payload
        self.client = None
        self.usage = {}
        self.config = {}          # {id: {value, label, options}} straight from ACP
        # Local skills until the agent advertises its own commands.
        self.commands = _skill_commands(getattr(self, "agent", "claude"))
        self.model = None
        self.effort = None
        # What Corral actually imposed; starts as the lane-level claim and is
        # replaced by _apply_posture's result.
        self.posture_enforced = posture_enforceable(
            AGENTS.get(getattr(self, "agent", "claude"), {}))
        self._seq = 0             # monotonic for the life of the pane
        self._queue = []          # type-ahead; drained strictly in order
        self._turn_running = False
        self._in_flight = None    # the prompt _drain popped and is running now
        self._turn_lock = threading.Lock()
        # Bumped whenever self.client is replaced; a _drain thread whose generation
        # has moved on must touch nothing shared.
        self._generation = 0
        self._expect_exit = False  # we are the ones killing it; not a fault
        self._since_rotate_check = 0
        self._replaying = False
        self._log = None
        self.last_activity = time.time()
        self._lock = threading.Lock()
        self._text_lock = threading.Lock()   # coalesced streamed text (core)
        self._text_acc, self._text_timer, self._text_last = "", None, 0.0
        # Own-branch worktrees: review actions hold the send queue (D11).
        self.held = False
        self._action_lock = threading.Lock()
        self.worktree_summary = None
        self.worktree_blocked = None      # why resume is refused (missing/tampered/trashed)
        self._preamble_due = False        # D15: the first prompt carries the preamble



    @classmethod
    def from_meta(cls, meta, mgr):
        """Rebuild a pane from disk in `detached` state, without starting an agent."""
        p = cls.__new__(cls)
        p.id = meta["id"]
        p.agent = meta.get("agent") or "claude"   # null in old metas = claude
        if p.agent not in AGENTS:
            # Lane gone from this build: register a dead stub so snapshot() can't KeyError.
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
        # Migrate an un-renamed non-Claude title equal to the bare cwd name (an old
        # default-title collision).
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
        # Unused by Light, but restored so save_meta() does not erase it.
        p.ported_from = meta.get("ported_from")
        # Restored so a Corral seat resumed here stays reapable.
        p.ephemeral = bool(meta.get("ephemeral"))
        # None = unaddressable.
        p.seat = meta.get("seat")
        p.question = _core.PaneBase.restore_question(meta)   # ask_human
        p.seat_withheld = False
        # Restored so the next save does not blank them.
        p.role = meta.get("role")
        p.role_sha = meta.get("role_sha")
        p.role_delivery = meta.get("role_delivery")
        p.worktree_id = meta.get("worktree_id")
        # No process of its own yet.
        p.pid = p.pgid = p.pid_start = None
        p._init_runtime()
        p.state = "detached"
        p.dir = STATE / "panes" / p.id
        p.dir.mkdir(parents=True, exist_ok=True)
        p.events = p._read_events()
        # Resume the seq counter past what is on disk, or the browser would drop new
        # events as duplicates.
        p._seq = max((e.get("seq", 0) for e in p.events), default=0)
        p._rotate_log()
        p._log = (p.dir / "events.jsonl").open("a", encoding="utf-8")
        return p






    # A `dead` pane is resumable too (closed panes leave the roster). Resume
    # closes the old client and parks, never drains, any stale type-ahead.
    RESUMABLE = ("detached", "dead")

    def _park_stale_queue(self):
        """Drop a dead attachment's type-ahead and name every message dropped.

        Never re-sent to the new process; quoted in a `note` so nothing disappears.
        """
        with self._turn_lock:
            parked, self._queue = list(self._queue), []
            self._turn_running = False
            self._generation += 1        # retire any drain still holding the old client
        for t in parked:
            self._turns().mark(getattr(t, "turn", None), "interrupted",
                               why="the agent stopped before it was sent",
                               was="accepted")
        if parked:
            names = "; ".join(repr(t[:PARKED_PREVIEW_CHARS] +
                                   ("…" if len(t) > PARKED_PREVIEW_CHARS else ""))
                              for t in parked)
            self.emit("note", {"text": f"{len(parked)} queued message(s) were "
                                       f"not sent when the agent stopped, and "
                                       f"will not be sent now: {names}",
                               "parked": parked})
        return parked

    def resume(self):
        """Attach a fresh agent process to this pane's conversation (from detached or dead)."""
        if self.state not in self.RESUMABLE:
            raise ValueError(f"pane is {self.state}, not detached or dead")
        if self.worktree_id and self.worktree_blocked:
            raise ValueError(self.worktree_blocked)
        if not self.acp_session and self.worktree_id:
            # D14: a worktree pane whose first start failed retries, in place.
            self.mgr._reserve_live(self)
            if self._log is None:
                self._log = (self.dir / "events.jsonl").open("a", encoding="utf-8")
            self.error = None
            self._preamble_due = True
            self.start()
            return self
        if not self.acp_session:
            raise ValueError("this pane has no agent session to resume")
        prior = self.state
        if prior == "dead":
            # Close the crashed client first so its late agent_exit can't hit the new
            # attachment, and its process group is reaped.
            self._expect_exit = True
            self._reap_failed_client()
            self._park_stale_queue()
        self.mgr._reserve_live(self)
        try:
            if self._log is None:      # pause() closed it; reopen for this attachment
                self._log = (self.dir / "events.jsonl").open("a", encoding="utf-8")
            spec = AGENTS[self.agent]
            env = spawn_env(spec, self._config_dir())
            self._expect_exit = False        # a NEW process; its exit is real news
            with self._turn_lock:
                self._generation += 1        # a new attachment; retire any stale drain
                gen = self._generation
            self.error = None
            self.dead_cause = self.dead_login = None
            self.client = acp.AcpClient(spec["argv"], self.cwd, env=env,
                                        strip_env=strip_prefixes(),
                                        **self._bind(gen))
            self._record_pid()
            self.client.initialize()
            # The agent replays the whole transcript on load. We already have
            # it; emitting it again would double the conversation on screen.
            self._replaying = True
            try:
                r = self.client.load_session(self.acp_session, self.cwd,
                                              self._mcp_servers())
            finally:
                self._replaying = False
            # A lane's load notice arrives in `_meta`, since in-band chunks are
            # suppressed with the replay.
            notice = (((r or {}).get("_meta") or {}).get("corral/notice"))
            if isinstance(notice, str) and notice.strip():
                self.emit("note", {"text": notice.strip()[:500],
                                   "contextLost": bool(((r or {}).get("_meta") or {})
                                                       .get("corral/contextLost"))})
            self._absorb_config((r or {}).get("configOptions") or [])
            # session/load returns the agent's defaults; re-impose posture, model, effort.
            self._apply_wants()
            self.state = "ready"
            self.emit("resumed", {"model": self.model, "effort": self.effort,
                                  "config": self.config,
                                  "postureEnforced": self.posture_enforced,
                                  "from": prior})
        except acp.AgentError as e:
            self._reap_failed_client()      # same leak as start(); see there
            self._dead(f"could not resume: {e}")
        except Exception:
            # Release the `starting` reservation; a dead pane returns to dead.
            if self.state == "starting":
                self.state = prior
            raise
        self.save_meta()
        return self




    def _turns(self):
        """This pane's turn ledger, created lazily (only panes with a dir keep one)."""
        lg = getattr(self, "_ledger", None)
        if lg is None:
            d = getattr(self, "dir", None)
            lg = ledger.TurnLedger(d / "turns.jsonl") if d else ledger.NullLedger()
            self._ledger = lg
        return lg

    def _close_open_turns(self, why):
        """Mark the in-flight turn and every queued one `interrupted`."""
        with self._turn_lock:
            items = ([getattr(self, "_in_flight", None)]
                     + list(getattr(self, "_queue", [])))
        lg = self._turns()
        for i, t in enumerate(x for x in items if x is not None):
            lg.mark(getattr(t, "turn", None), "interrupted", why=why,
                    was="dispatched" if (i == 0 and items[0] is not None)
                    else "accepted")

    def pause(self):
        self._close_open_turns("paused")
        return super().pause()

    def cancel(self):
        """The Stop button (and Esc): stop the running turn AND what is
        queued behind it.

        The base cancel only interrupted the turn in flight, so anything typed
        ahead ran the moment the cancel landed: pressing stop on a pane with
        three queued messages started the next one. Stop means stop. The
        dropped turns are closed `interrupted` in the ledger and named in the
        transcript; nothing is re-sent. Returns True when there was anything
        to stop.
        """
        with self._turn_lock:
            dropped, self._queue = list(self._queue), []
        lg = self._turns()
        for t in dropped:
            lg.mark(getattr(t, "turn", None), "interrupted",
                    why="stopped before it was sent", was="accepted")
        ok = super().cancel()
        if dropped:
            self.emit("note", {"text": f"stopped — {len(dropped)} queued "
                                       f"message(s) were not sent"})
        return bool(ok or dropped)

    def clear_context(self, via=None):
        """/clear: the same pane, a brand-new conversation, on every lane.

        It used to forward the literal text and trust the agent. Only the
        Claude SDK special-cases `/clear`; Codex, Grok, Antigravity and Ollama
        got it as an ordinary prompt and remembered everything. Even on
        Claude the reset was half-real: the SDK moved to a new conversation
        id but this pane kept the OLD acp_session, so the next pause, resume
        or hub restart ran session/load on the pre-clear conversation and
        quietly brought it all back.

        So Corral owns it. Stop whatever is running, end the agent process,
        and start a fresh one with session/new: start(), the exact path every
        new pane takes, so model, effort and posture are re-imposed by
        _apply_wants() and no lane needs a special case. The pane id, title,
        seat and on-disk transcript are kept; the `cleared` marker folds the
        old turns out of view without deleting a byte (PRINCIPLES 18).
        Returns the turn id of the /clear itself.
        """
        if self.state == "starting":
            raise ValueError("this pane is still starting — try /clear again "
                             "in a moment")
        if self._log is None:      # paused: pause() closed it; emit() needs it
            self._log = (self.dir / "events.jsonl").open("a", encoding="utf-8")
        lg = self._turns()
        try:
            tid = lg.accept("/clear")
        except OSError as e:
            raise ValueError(f"could not record this turn durably, so it "
                             f"was not accepted: {e}")
        tid = tid or _core.new_turn_id()
        user = {"text": "/clear", "turn": tid}
        if via:
            user["via"] = via
        self.emit("user", user)
        self._note_turn(via)
        # Everything in flight or queued belonged to the old conversation.
        self._close_open_turns("cleared by /clear")
        self._clear_pending("cleared")
        with self._turn_lock:
            self._queue = []
            self._drop_held_peers_locked("cleared")
            self._turn_running = False
            self._in_flight = None
            # Retire any drain still blocked in the OLD client's prompt(): its
            # captured generation goes stale and it will touch nothing.
            self._generation += 1
        old, self.client = self.client, None
        self._expect_exit = True        # the old process's exit is not news
        if old is not None:
            try:
                old.close()
            except Exception:           # noqa: BLE001 — it is going away anyway
                pass
        self.pid = self.pgid = self.pid_start = None
        self.acp_session = None
        self.usage = {}
        self.error = None
        self.dead_cause = self.dead_login = None
        self.emit("cleared", {})
        # The cap still applies: a paused pane coming back is a new live one.
        self.mgr._reserve_live(self)
        self.state = "starting"
        self.start()                    # marks the pane dead itself on failure
        if self.state == "dead":
            lg.mark(tid, "interrupted", why=f"could not start a fresh "
                                            f"session: {self.error}",
                    was="accepted")
        else:
            lg.mark(tid, "completed", stopReason="cleared")
            self.emit("turn_end", {"stopReason": "cleared", "usage": self.usage,
                                   "turn": tid, "queued": 0})
        self.save_meta()
        return tid

    def stop(self):
        self._close_open_turns("closed")
        return super().stop()

    def shutdown_note(self, why):
        """Write one note naming the in-flight and queued prompts a hub exit cuts off.

        Changes nothing else. Returns the note text, or None if nothing was pending.
        """
        with self._turn_lock:
            running = getattr(self, "_in_flight", None)
            queued = list(getattr(self, "_queue", []))
        if running is None and not queued:
            return None

        def show(t):
            return repr(t[:PARKED_PREVIEW_CHARS] +
                        ("…" if len(t) > PARKED_PREVIEW_CHARS else ""))
        parts = [f"the hub is shutting down ({why})"]
        if running is not None:
            parts.append(f"this turn was interrupted: {show(running)}")
        if queued:
            parts.append(f"{len(queued)} queued message(s) were not sent: "
                         + "; ".join(show(t) for t in queued))
        parts.append("nothing will be re-sent automatically")
        text = " — ".join(parts)
        self._close_open_turns(f"hub shutdown ({why})")
        self.emit("note", {"text": text, "shutdown": True,
                           "interrupted": running, "queued": queued})
        return text

    def _record_pid(self):
        """Persist the adapter's pid/pgid/start time before the handshake, so a
        hub crash mid-handshake still leaves a record of the orphan."""
        c = self.client
        self.pid = getattr(getattr(c, "p", None), "pid", None)
        self.pgid = getattr(c, "pgid", None)
        self.pid_start = getattr(c, "start_token", None)
        self.save_meta()

    def _bind(self, gen):
        """Callbacks for one attachment; events from a superseded one are dropped."""
        def on_event(kind, data):
            if gen != self._generation:
                return
            self._on_event(kind, data)

        def on_permission(req):
            if gen != self._generation:
                return
            self._on_permission(req)
        return {"on_event": on_event, "on_permission": on_permission}

    def _on_event(self, kind, data):
        # Each buffer flushes when anything else arrives, so stream order holds.
        if kind != "agent_message_chunk":
            self._flush_text()
        if kind != "agent_thought_chunk":
            self._flush_thought()       # one coalesced event, in stream order
        if kind == "agent_message_chunk":
            # Coalesced (TEXT_FLUSH_S): a replayed chunk is dropped here, not
            # buffered, or it would surface after the replay guard lifts.
            if not self._replaying:
                self._buffer_text((data.get("content") or {}).get("text", ""))
        elif kind == "agent_thought_chunk":
            # Coalesced: buffered here and emitted once by _flush_thought when any other
            # event arrives, so thought fragments cannot flood the event ring.
            self._thought_acc = (getattr(self, "_thought_acc", "") or "") + \
                (data.get("content") or {}).get("text", "")
        elif kind in ("tool_call", "tool_call_update"):
            self.emit("tool", {
                "id": data.get("toolCallId"), "title": data.get("title"),
                "kind": data.get("kind"), "status": data.get("status"),
                "content": (data.get("content") or [])[:6],
                "locations": (data.get("locations") or [])[:8]})
            if self.worktree_id and not self._replaying:
                self._worktree_guard(data)
        elif kind == "stall_notice":
            # Informational only: a clock can't tell a wedged agent from a slow one.
            self.emit("note", {"text": data.get("text") or "no output for a "
                                       "while — still attached and waiting"})
        elif kind == "permission_expired":
            # Drop the expired request from `pending` and record why in the transcript.
            rid = data.get("requestId")
            if self.pending.pop(rid, None) is not None:
                self.emit("permission_expired",
                          {"requestId": rid,
                           "reason": data.get("reason") or "expired"})
            if self.state == "needs-you" and not self.pending:
                self.state = "ready"
        elif kind == "available_commands_update":
            # The agent's own command list, merged over the local skill catalog.
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
            # A deliberate kill (pause) reports its exit asynchronously; ignore it.
            if self._expect_exit:
                return
            self.state = "dead"
            self._drop_held_peers("dead")
            self._clear_pending("agent_exit")
            self._clear_question("dead")
            if data.get("closed"):
                self.error = None                  # deliberate: not a fault
                self.emit("closed", {"reason": data.get("reason")})
            else:
                self._dead(data.get("reason"))

    def _dead(self, reason):
        """Record that the agent stopped on its own.

        Auth deaths carry a plain-English remedy, a `cause`, and the login they
        died under, so auth_sweep resumes them only after a new sign-in.
        """
        cause = "auth" if claude_auth.is_auth_error(reason) else None
        self._flush_text()              # its last words land before `dead`
        self._flush_thought()
        self.state = "dead"
        self.error = claude_auth.explain(reason)
        self.dead_cause = cause
        self.dead_login = (claude_auth.status().get("refreshExpiresAt")
                           if cause else None)
        self.emit("dead", {"reason": self.error, "cause": cause})



    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self):
        spec = AGENTS[self.agent]
        env = spawn_env(spec, self._config_dir())
        self._expect_exit = False        # a NEW process; its exit is real news
        try:
            self.client = acp.AcpClient(spec["argv"], self.cwd, env=env,
                                        strip_env=strip_prefixes(),
                                        **self._bind(self._generation))
            self._record_pid()
            info = self.client.initialize()
            new = self.client.new_session_full(self.cwd, self._mcp_servers())
            self.acp_session = new.get("sessionId")
            self._absorb_config(new.get("configOptions") or [])
            if self.agent == "grok" and not self.config.get("model"):
                # Grok reports configOptions: null. Record its model as display-only (no
                # options, so never a picker) and persist it so the dialog can show it.
                from grok_launcher import resolve_default_model, resolve_grok
                grok_bin = resolve_grok()
                model = resolve_default_model(grok_bin) if grok_bin else None
                if model:
                    self.config["model"] = {"value": model, "name": "Model",
                                            "realId": "model", "options": []}
                    self.model = model
                    self.mgr.remember_catalog(self.agent, self.config)
            self._apply_wants()
            self.state = "ready"
            self.emit("ready", {
                "agent": self.agent, "cwd": self.cwd, "posture": self.posture,
                "acpSession": self.acp_session, "model": self.model,
                "effort": self.effort, "config": self.config,
                "agentInfo": info.get("agentInfo", {})})
            self.save_meta()
        except acp.AgentError as e:
            # The handshake can fail after a successful spawn; reap the process group
            # rather than orphan it.
            self._reap_failed_client()
            self._dead(str(e))
        return self




    def _apply_wants(self):
        """Re-impose this pane's posture, model and effort on a fresh session."""
        self._apply_lane_defaults()
        for cid, want in (("model", self.want_model), ("effort", self.want_effort)):
            if want and want != "default":
                try:
                    real_id = (self.config.get(cid) or {}).get("realId", cid)
                    r = self.client.set_config(self.acp_session, real_id, want)
                    self._absorb_config((r or {}).get("configOptions") or [])
                except acp.AgentError as e:
                    self.emit("note", {"text": f"could not set {cid}={want}: {e}"})
                    continue
                # An ack is not adoption: the adapter may silently resolve to a different
                # model. Report when the echoed value differs.
                got = (self.config.get(cid) or {}).get("value")
                if got != want:
                    self.emit("note", {"text": f"asked for {cid}={want}; "
                                               f"{AGENTS[self.agent]['label']} "
                                               f"is running {got!r}"})
        self.posture_enforced = self._apply_posture()

    def _apply_posture(self):
        """Set this pane's permission posture on the live session.

        Returns True only when the agent's echoed config confirms it; otherwise
        leaves a note in the pane and returns False.
        """
        spec = AGENTS[self.agent]
        if not spec.get("posture_via_acp_mode"):
            # Config-dir lanes: the posture was set (or not) at spawn.
            return posture_enforceable(spec)
        want = POSTURE_MODE.get(self.posture)
        cfg = self.config.get("mode") or {}
        allowed = {o["value"] for o in cfg.get("options") or []}
        label = spec["label"]
        if want is None:
            self.emit("note", {"text": f"no permission mode is mapped for "
                                       f"posture {self.posture!r}; left at "
                                       f"{label}'s own default"})
            return False
        if not cfg:
            self.emit("note", {"text": f"{label} did not advertise a permission "
                                       f"mode on this session; it runs under "
                                       f"its own default"})
            return False
        if allowed and want not in allowed:
            self.emit("note", {"text": f"{label} does not offer permission mode "
                                       f"{want!r} (it lists {sorted(allowed)}); "
                                       f"left at its own default"})
            return False
        if cfg.get("value") != want:
            try:
                r = self.client.set_config(self.acp_session,
                                           cfg.get("realId", "mode"), want)
                self._absorb_config((r or {}).get("configOptions") or [])
            except acp.AgentError as e:
                self.emit("note", {"text": f"could not set permissions to "
                                           f"{self.posture} ({want}): {e}"})
                return False
        got = (self.config.get("mode") or {}).get("value")
        if got != want:
            # The ack did not take.
            self.emit("note", {"text": f"asked {label} for permission mode "
                                       f"{want!r}; it reports {got!r}"})
            return False
        return True

    # Values no lane default may carry, per config id.
    FORBIDDEN_DEFAULTS = {}

    def _apply_lane_defaults(self):
        """Apply this lane's default config if the agent advertised it; note either way."""
        for cid, value in (AGENTS[self.agent].get("default_config") or {}).items():
            if value in self.FORBIDDEN_DEFAULTS.get(cid, set()):
                self.emit("note", {"text": f"refusing lane default {cid}={value} "
                                           f"— not allowed as a default"})
                continue
            cfg = self.config.get(cid) or {}
            allowed = {o["value"] for o in cfg.get("options", [])}
            if not allowed or value not in allowed:
                self.emit("note", {"text": f"lane default {cid}={value} not offered "
                                           f"by {AGENTS[self.agent]['label']} "
                                           f"(offers {sorted(allowed) or 'nothing'}) "
                                           f"— left at {cfg.get('value')!r}"})
                continue
            if cfg.get("value") == value:
                continue
            try:
                r = self.client.set_config(self.acp_session,
                                           cfg.get("realId", cid), value)
                self._absorb_config((r or {}).get("configOptions") or [])
                self.emit("note", {"text": f"{cid} = {value} (lane default)"})
            except acp.AgentError as e:
                self.emit("note", {"text": f"could not set lane default "
                                           f"{cid}={value}: {e}"})

    def set_config(self, config_id, value):
        # `mode` is the lane's own approval-mode option, validated like the rest.
        if config_id not in ("model", "effort", "fast", "mode"):
            raise ValueError(f"{config_id!r} is not settable from here")
        # No advertised options (e.g. Grok's null configOptions) is a refusal.
        cfg = self.config.get(config_id) or {}
        allowed = {o["value"] for o in cfg.get("options", [])}
        if not allowed:
            raise ValueError(f"{AGENTS[self.agent]['label']} does not offer a "
                             f"{config_id!r} setting")
        if value not in allowed:
            raise ValueError(f"{value!r} is not offered for {config_id}; "
                             f"the agent lists {sorted(allowed)}")
        if self.client is None:
            # Detached: store model/effort as preferences applied on resume.
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
        # Forward under the adapter's own id (`realId`); Corral's names are aliases.
        r = self.client.set_config(self.acp_session, cfg.get("realId", config_id), value)
        self._absorb_config((r or {}).get("configOptions") or [])
        self.emit("config", {"model": self.model, "effort": self.effort,
                             "config": self.config})
        return self.config.get(config_id)

    def _config_dir(self):
        return seed_config_dir(self.dir / "config", self.posture)

    def send(self, text, via=None):
        # Validate `via` before anything is resumed, titled or queued.
        via = _core.check_via(via)
        # /clear is Corral's, on EVERY lane, and it is checked before the
        # resume below: clearing a paused pane means "start it fresh", and
        # loading the old conversation only to throw it away is wasted work.
        if (text or "").strip() == "/clear":
            return self.clear_context(via)
        # Dead too, since 2026-09-28 (P0-a'): typing into a pane whose agent
        # stopped means "bring it back", exactly as it does for a paused one.
        # resume() parks — never sends — whatever was queued when it died.
        if self.state in self.RESUMABLE:
            if not (text or "").strip():
                raise ValueError("empty prompt")
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
        # The first prompt names the conversation. Compare against the default title,
        # not the (bounded) event ring.
        if not self.title_locked and self.title == self._default_title(self.agent, self.cwd):
            first = " ".join(text.split())[:42]
            self.title = first + ("…" if len(" ".join(text.split())) > 42 else "")
            self.save_meta()      # or a restart restores the generic name back
        # Light intercepts nothing: every message goes to the agent.
        # One turn at a time per pane; extra messages queue in order, bounded.
        with self._turn_lock:
            if len(self._queue) >= MAX_QUEUED_TURNS:
                raise ValueError(
                    f"{MAX_QUEUED_TURNS} messages already waiting on this pane "
                    f"— it is still working through them")
            # Durable before acknowledged: refuse the send if the turn can't be fsynced.
            try:
                tid = self._turns().accept(text)
            except OSError as e:
                raise ValueError(f"could not record this turn durably, so it "
                                 f"was not accepted: {e}")
            # The null ledger (a pane with no durable record) returns no id;
            # a turn still needs one for its `turn_end` to be matched to it.
            tid = tid or _core.new_turn_id()
            user = {"text": text, "turn": tid}
            if via:
                user["via"] = via
            self.emit("user", user)
            self._note_turn(via)     # a human turn answers an open question
            # (/clear never reaches here: send() hands it to clear_context.)
            # The queue-and-drain half is shared with peer delivery (DESIGN-5
            # S7); everything ABOVE this line is what makes it the human's.
            agent_text = text
            if self._preamble_due:            # D15: once, on the first prompt
                agent_text = WORKTREE_PREAMBLE + "\n\n" + text
                self._preamble_due = False
            self._dispatch(_QueuedText(agent_text, tid))
            return tid

    def _drain(self):
        """Run queued prompts strictly in order until the pane is empty."""
        while True:
            with self._turn_lock:
                # Read the client inside the lock: pause() can set it to None concurrently.
                client = self.client
                gen = self._generation
                # Re-admit replies held behind the turn that just ended.
                self._release_held_peers_locked()
                if not self._queue or self.state == "dead" or client is None:
                    self._turn_running = False
                    return
                text = self._queue.pop(0)
                # Re-check for a pending card: a peer turn never runs while one is open.
                if getattr(text, "peer", False) and self.pending:
                    self._peer_withdrawn(text, "card-pending")
                    continue
                # Kept while in flight so a shutdown note can name it.
                self._in_flight = text
            lg = self._turns()
            tid = getattr(text, "turn", None)
            lg.mark(tid, "dispatched")
            try:
                r = client.prompt(self.acp_session, text)
            except acp.AgentError as e:
                with self._turn_lock:
                    if self._generation != gen:
                        # A newer attachment owns the pane state now; touch nothing.
                        return
                    # Do not silently swallow what was still waiting.
                    lost, self._queue = self._queue, []
                    dropped = len(lost)
                    self._turn_running = False
                    self._in_flight = None
                lg.mark(tid, "interrupted", why=str(e), was="dispatched")
                if getattr(text, "peer", False):
                    self.emit("peer_result", {"turn": tid, "delivered": False,
                                              "reason": str(e)}, activity=False)
                for t in lost:
                    lg.mark(getattr(t, "turn", None), "interrupted",
                            why="the agent stopped before it was sent",
                            was="accepted")
                # Prompts have no deadline, so the agent died or closed stdin. close()
                # reaps any surviving members of the process group.
                client.close()
                self._drop_held_peers("dead")
                self._dead(str(e) + (
                    f" ({dropped} queued message(s) were not sent)" if dropped else ""))
                return
            except Exception as e:              # noqa: BLE001
                # A bug in us: fail loudly in the transcript and keep the pane usable.
                with self._turn_lock:
                    if self._generation != gen:
                        return
                    lost, self._queue = self._queue, []
                    dropped = len(lost)
                    self._turn_running = False
                    self._in_flight = None
                # Unknown whether the agent ran it: `uncertain`, never replayed.
                lg.mark(tid, "uncertain", why=f"{type(e).__name__}: {e}")
                for t in lost:
                    lg.mark(getattr(t, "turn", None), "interrupted",
                            why="dropped after an internal error", was="accepted")
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
                self._in_flight = None
            lg.mark(tid, "completed", stopReason=(r or {}).get("stopReason"))
            self._flush_text()          # a throttled tail is written before turn_end
            self._flush_thought()       # a turn ending on a thought still shows it
            self.emit("turn_end", {"stopReason": (r or {}).get("stopReason"),
                                   "usage": self.usage, "turn": tid,
                                   "queued": len(self._queue)})
            if self.state != "dead":
                self.state = "needs-you" if self.pending else (
                    "busy" if self._queue else "ready")
            if self.worktree_id:
                self.mgr.worktree_turn_ended(self)

    # ── own-branch worktrees: the held queue (D11) ──────────────────────────

    def _dispatch(self, item, at=None):
        """As the core's, except that while a review action holds the pane the
        prompt is queued and no drain starts."""
        if self.held:
            if at is None:
                self._queue.append(item)
            else:
                self._queue.insert(at, item)
            return
        return super()._dispatch(item, at)

    WRITE_KINDS = ("edit", "delete", "move")

    def _worktree_guard(self, data):
        """An edit/delete/move tool event outside the worktree (and its admin dir)
        cancels the turn and tells the user at once (§2.6). The write already
        happened; the point is hearing about it now, not at review. Shell
        commands usually report only their cwd, so they are not covered."""
        if data.get("kind") not in self.WRITE_KINDS:
            return
        e = self.mgr.worktree_entry(self)
        if not e:
            return
        allowed = [os.path.realpath(e["path"]),
                   os.path.realpath(Path(e["common_dir"]) / "worktrees" / e["admin_name"])]
        base = _wt.agent_cwd(e)
        outside = []
        for loc in data.get("locations") or []:
            raw = (loc or {}).get("path")
            if not raw:
                continue
            real = os.path.realpath(raw if os.path.isabs(raw) else base / raw)
            if not any(real == a or real.startswith(a + os.sep) for a in allowed):
                outside.append(raw)
        if not outside:
            return
        if getattr(self, "_escape_turn", None) == self._in_flight and self._in_flight is not None:
            return                                  # one card per turn
        self._escape_turn = self._in_flight
        self.emit("worktree", {"escape": {"paths": outside[:8], "tool": data.get("title"),
                                          "kind": data.get("kind")}})
        self.emit("note", {"text": "this agent changed a file outside its own branch: "
                                   + ", ".join(outside[:8]) + " — its turn was stopped"})
        threading.Thread(target=self._cancel_quietly, daemon=True).start()

    def _cancel_quietly(self):
        with contextlib.suppress(Exception):
            self.cancel()

    def worktree_view(self):
        """What the browser shows for this pane's own branch, or None."""
        if not self.worktree_id:
            return None
        e = self.mgr.worktree_entry(self) or {}
        branch = (e.get("branch") or "")[len("refs/heads/"):]
        return {"id": self.worktree_id, "branch": branch, "path": e.get("path"),
                "repo": e.get("repo_top"), "subdir": e.get("subdir"),
                "base": (e.get("base_ref") or "")[len("refs/heads/"):],
                "baseSha": e.get("base_sha"), "phase": e.get("phase"),
                "blocked": self.worktree_blocked, "held": self.held,
                "summary": self.worktree_summary,
                "published": e.get("published"), "lastCommit": e.get("last_commit")}

    def release_hold(self, drain):
        """End a review action's hold. `drain`: run what queued meanwhile (after
        Commit or Publish); otherwise keep it visible as not sent (Discard, failure)."""
        with self._turn_lock:
            self.held = False
            if not self._queue:
                return
            if drain and self.state not in ("dead", "detached") and not self._turn_running:
                self.state = "busy"
                self._turn_running = True
                threading.Thread(target=self._drain, daemon=True).start()
                return
        if not drain:
            self._park_stale_queue()

    def _on_permission(self, req):
        """Worktree panes never offer "allow always": Phase 0 showed Claude saves
        such rules to the MAIN checkout's .claude/settings.local.json, so one
        worktree's approval would widen every later session there."""
        if self.worktree_id:
            opts = req.get("options") or []
            kept = [o for o in opts if str(o.get("kind", "")) != "allow_always"]
            if kept and len(kept) != len(opts):
                req = dict(req, options=kept)
        return super()._on_permission(req)

    def answer(self, request_id, option_id, digest=None):
        req = self.pending.get(request_id)
        if not req:
            raise ValueError("no such pending permission (already answered?)")
        valid = {o.get("optionId") for o in req.get("options") or []}
        if option_id not in valid:
            raise ValueError(f"invalid option {option_id!r}; expected one of {sorted(valid)}")
        # Enforce the display gate server-side, from the pending record (which lives
        # exactly as long as the authority it guards).
        rec = req.get("_gate") or {}
        kind = next((str(o.get("kind", "")) for o in req.get("options") or []
                     if o.get("optionId") == option_id), "")
        # The digest binds an approval to the bytes shown. It gates granting only:
        # refusing must always work, even from a stale client. Which option refuses
        # comes from the agent's declared `kind`, never from the client.
        shown = rec.get("digest") or ""
        granting = not kind.startswith("reject")
        if granting and (not shown or digest != shown):
            raise ValueError(
                "the digest on this approval does not match the bytes that "
                "were shown — an approval proves only what you could see. "
                "Reload the page and answer the card again; refusing works "
                "either way.")
        if rec.get("oversize") and granting:
            raise ValueError(
                "this request was too large to display, so it cannot be "
                "approved here — only refused. An approval proves only what "
                "you could see.")
        # Pop before waking the agent so concurrent answers can't both win;
        # restore the record if the wake fails.
        with self._lock:
            if request_id not in self.pending:
                raise ValueError("no such pending permission (already answered?)")
            self.pending.pop(request_id, None)
        try:
            ok = self.client.answer_permission(request_id, option_id)
        except Exception:
            with self._lock:
                self.pending.setdefault(request_id, req)
            raise
        if self.state != "dead":
            self.state = "needs-you" if self.pending else "busy"
        # Bind the answer to the exact bytes that were on screen.
        self.emit("permission_answered", {"requestId": request_id,
                                          "optionId": option_id, "kind": kind,
                                          "digest": rec.get("digest"),
                                          "delivered": ok})
        return ok


    def set_minimized(self, flag):
        """Hide the pane; the roster still shows its live state and pending permissions."""
        self.minimized = bool(flag)
        self.save_meta()
        self.mgr.broadcast_layout(self)
        return self.minimized





    def snapshot(self, since=0):
        # Ask the OS (poll()), not `client.alive`; when they disagree, say `uncertain`.
        alive = bool(self.client and self.client.alive)
        idle = time.time() - getattr(self, "last_activity", time.time())
        state_override = None
        if alive:
            try:
                if self.client.p.poll() is not None:
                    # The process has exited; `client.alive` just hasn't noticed.
                    alive = False
                    if self.state != "detached" and not self._expect_exit:
                        state_override = "dead"
                elif self.state == "uncertain" and idle <= STALL_S:
                    state_override = "busy"     # it started talking again
                elif self.state == "busy" and idle > STALL_S:
                    # Alive and mid-turn but silent for STALL_S: possibly wedged.
                    state_override = "uncertain"
            except Exception:                       # noqa: BLE001
                pass
        if state_override and state_override != self.state:
            self.state = state_override
            # Broadcast the change so browsers update. activity=False: see emit().
            self.emit("state", {"state": state_override}, activity=False)
        # Liveness from the process; `detached` (deliberately not running) is not dead.
        state = (self.state if alive or self.state in
                 ("dead", "detached", "uncertain") else "dead")
        return {
            "id": self.id, "agent": self.agent, "label": AGENTS[self.agent]["label"],
            "minimized": self.minimized, "titleLocked": self.title_locked,
            "order": self.order, "pinned": self.pinned,
            "model": self.model, "effort": self.effort, "config": self.config,
            "commands": self.commands,
            # Seconds since anything came out of this pane.
            "idleS": int(idle),
            "cwd": self.cwd, "posture": self.posture, "title": self.title,
            # Whether Corral actually imposed the posture on this pane (measured).
            "postureEnforced": self.posture_enforced,
            # Whether this lane can read a file itself — what attaching a note
            # to it means (a reference, or a quoted excerpt).
            "tools": bool(AGENTS[self.agent].get("tools")),
            # Whether the adapter enforces its own fail-closed permission rail, distinct
            # from postureEnforced. No lane sets it today.
            "rail": bool(AGENTS[self.agent].get("rail")),
            "state": state, "error": self.error, "created": self.created,
            # Triage projection over `state`, computed once in the core.
            "display": _core.display_state(self, state=state)["state"],
            "pending": list(self.pending.keys()),
            # The pane's address for other panes; a withheld seat is shown, not served.
            "seat": None if self.seat_withheld else self.seat,
            "seatWithheld": self.seat if self.seat_withheld else None,
            # The agent's open ask_human question ({text, at, turn}) or None; `turnVia`
            # is the latest turn's origin (None = the human).
            "question": self.question,
            "turnVia": self.turn_via,
            # Resumable only with an ACP session id to load.
            "resumable": bool(getattr(self, "acp_session", None)),
            # "auth" when a lapsed login killed it.
            "deadCause": getattr(self, "dead_cause", None),
            "role": getattr(self, "role", None),
            # Set when the transcript was carried from another lane (port.py).
            "portedFrom": getattr(self, "ported_from", None),
            # Own branch (worktrees.py): None for an ordinary pane.
            "worktree": self.worktree_view(),
            "usage": self.usage, "alive": alive,
            "events": [e for e in self.events if e["seq"] > since],
            "seq": self.events[-1]["seq"] if self.events else 0,
        }


# --- Known aliases the live handshake does not enumerate --------------------
# Model ids added to the advertised catalog in remember_catalog. Empty: the
# adapter accepts `<model><sep><anything>` (even "opus!!!") and echoes the base
# model, while "opusXYZ" is refused; acceptance does not prove an alias is real.
MODEL_EXTRAS = {}



def _clear_pid_record(pane_dir):
    """Null the pid fields in a meta.json, touching nothing else (atomic).

    Not save_meta(), which would blank keys Light does not restore.
    """
    f = Path(pane_dir) / "meta.json"
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
        for k in ("pid", "pgid", "pid_start"):
            m[k] = None
        tmp = f.with_name("meta.json.tmp")
        tmp.write_text(json.dumps(m, indent=1), encoding="utf-8")
        os.replace(tmp, f)
    except (OSError, ValueError):
        pass


class Manager(_core.ManagerBase):
    """Panes, plus a cached copy of each agent's config catalog.

    Cached on disk so the new-conversation dialog has model/effort options
    before any pane exists; every new session refreshes it.
    """

    def __init__(self):
        self.panes = {}
        self.subscribers = []
        self.not_restored = 0
        self.orphans = {}          # pane id -> what restore() did to its old adapter
        import schedule               # scheduled prompts; the hub starts its ticker
        self.schedule = schedule.Scheduler(self, STATE / "schedule.json")
        self._lock = threading.Lock()
        self.mcp = mcp.Registry()
        self.catalog = self._load_catalog()
        self.restore()
        # Probe in the background so startup never waits on a vendor.
        threading.Thread(target=self.seed_catalogs, daemon=True).start()


    # ── the Claude login, watched ────────────────────────────────────────
    AUTO_RESUME_MAX = 8        # panes brought back per sweep

    def auth_sweep(self, now=None, notify_fn=None):
        """Edge-triggered watch on the Claude login, run from the hub's observe tick.

        On a new sign-in, resume panes that died of auth under the old login; on
        expiry or near-expiry, notify once per (state, expiry). Never raises.
        """
        acted = {"resumed": [], "failed": [], "notified": None}
        try:
            st = claude_auth.status(now=now) or {}
        except Exception:                          # noqa: BLE001
            return acted
        ok, ref = st.get("ok"), st.get("refreshExpiresAt")
        if ok:
            victims = [p for p in list(self.panes.values())
                       if getattr(p, "state", None) == "dead"
                       and getattr(p, "dead_cause", None) == "auth"
                       and getattr(p, "acp_session", None)
                       and getattr(p, "dead_login", None) != ref]
            for p in victims[:self.AUTO_RESUME_MAX]:
                try:
                    p.resume()
                    if p.state != "dead":
                        p.emit("note", {"text": "Claude login is back — this pane "
                                                "resumed by itself; send your last "
                                                "message again (it was not sent)"})
                        acted["resumed"].append(p.id)
                    else:
                        acted["failed"].append(p.id)
                except Exception:                  # noqa: BLE001
                    acted["failed"].append(p.id)
                    p.dead_login = ref             # not again until the next login
        key = (ok, bool(st.get("warn")), ref)
        if key != getattr(self, "_auth_said", None):
            self._auth_said = key
            if ok is False or st.get("warn"):
                try:
                    import notify
                    (notify_fn or notify.desktop)("Corral Light — Claude login",
                                                  st.get("why") or "")
                except Exception:                  # noqa: BLE001
                    pass
                acted["notified"] = st.get("why")
        return acted

    def port_preview(self, from_id, to_agent):
        """The exact pack a port would send, its sha, and who receives it.
        Refusals are the same function Manager.port() applies."""
        import port as port_mod
        src = self.get(from_id)
        why = port_mod.refuse_target(to_agent, AGENTS.get(to_agent),
                                     source_agent=src.agent, src_pane=src)
        if why:
            raise ValueError(why)
        pack = port_mod.compose(src, to_agent)
        pack["vendor"] = port_mod.vendor_of(to_agent)
        pack["label"] = (AGENTS.get(to_agent) or {}).get("label") or to_agent
        return pack

    def port(self, from_id, to_agent, *, sha, cwd=None, posture=None):
        """Carry a conversation's transcript to a new pane on another lane.

        `sha` must match the freshly recomposed pack, so a transcript that grew
        since the preview is refused. Delivery is reported separately.
        """
        import socket
        import port as port_mod
        if self.get(from_id).worktree_id:
            raise ValueError("a pane on its own branch cannot be ported yet; "
                             "publish or discard its branch first")
        pack = self.port_preview(from_id, to_agent)
        if not sha or pack["sha"] != sha:
            raise ValueError("the preview is out of date — reopen and check it")
        src = self.get(from_id)
        pane = self.create(to_agent, cwd or src.cwd, posture or src.posture)
        pane.ported_from = {"pane": src.id, "agent": src.agent,
                            "host": socket.gethostname()[:64], "at": _now(),
                            "turns": pack["turns_carried"],
                            "omitted": pack["omitted"]}
        pane.title, pane.title_locked = src.title, True   # not "# Handoff —…"
        pane.save_meta()
        try:
            pane.send(pack["text"])
        except (acp.AgentError, ValueError) as e:
            pane.emit("note", {"text": f"the handoff pack was not delivered: {e}"})
            pane.ported_from.update(turns=0, delivered=False, error=str(e)[:300])
            pane.save_meta()
            return {"pane": pane, "delivered": False, "error": str(e)[:300]}
        pane.ported_from["delivered"] = True
        pane.save_meta()
        return {"pane": pane, "delivered": True, "error": None}

    def shutdown_notes(self, why):
        """Write a shutdown note on every pane with work in flight; returns the count.

        Called from the hub's signal handler; one failure never stops the rest.
        """
        n = 0
        for p in list(self.panes.values()):
            try:
                if p.shutdown_note(why):
                    n += 1
            except Exception as e:             # noqa: BLE001
                print(f"corral-light: shutdown note for {p.id} failed: {e}",
                      file=sys.stderr, flush=True)
        return n

    def seed_catalogs(self):
        """Seed model lists for never-seen lanes via their catalog probes.

        A list remembered from a real session wins; failures leave the empty state.
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
            seeded = {"model": {
                "name": "Model", "realId": "model",
                "value": default if default in values else values[0],
                "options": [{"value": v, "name": v, "description": ""}
                            for v in values]}}
            # Also fold in effort and other options from the probe's full configOptions.
            if spec.get("probe_config"):
                try:
                    seeded.update({k: v for k, v in
                                   spec["probe_config"](agent).items()
                                   if k != "model"})
                except Exception as e:  # noqa: BLE001
                    print(f"corral-light: effort seed for {agent} skipped: {e}",
                          file=sys.stderr, flush=True)
            self.remember_catalog(agent, seeded)


    def remember_catalog(self, agent, config):
        """Record what this agent offers right now, keyed by agent.

        Always a live ACP response, so an empty config overwrites (the agent offers
        nothing). Keeps `value` without options so a fixed model still displays.
        """
        self.catalog[agent] = {k: {"name": v.get("name"), "options": v.get("options", []),
                                   "value": v.get("value")}
                               for k, v in (config or {}).items()
                               if v.get("options") or v.get("value")}
        model = self.catalog[agent].get("model")
        if model is not None:
            have = {o.get("value") for o in model["options"]}
            model["options"] += [e for e in MODEL_EXTRAS.get(agent, ())
                                 if e["value"] not in have]
        # Atomic and serialized: a partial catalog.json would read as {}.
        with _CATALOG_LOCK:
            try:
                CATALOG.parent.mkdir(parents=True, exist_ok=True)
                tmp = CATALOG.with_name(CATALOG.name + ".tmp")
                tmp.write_text(json.dumps(self.catalog, indent=1), encoding="utf-8")
                os.replace(tmp, CATALOG)
            except OSError:
                pass




    def create(self, agent, cwd, posture=DEFAULT_POSTURE, model=None, effort=None,
               role=None, role_sha=None, worktree=False, title=None):
        if agent.startswith("host:"):
            # The picker's list could be seconds stale; the spawn must not be.
            refresh_host_lanes()
        if agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r}")
        spec = AGENTS[agent]
        if spec.get("unavailable"):
            raise ValueError(f"{spec['label']}: {spec['unavailable']}")
        exe = Path(spec["argv"][0])
        if not exe.exists():
            raise ValueError(f"{spec['label']} is not installed at {exe}")
        # Check `requires` too: interpreter-launched lanes always have argv[0].
        missing = [p for p in spec.get("requires", ()) if not Path(p).exists()]
        if missing:
            raise ValueError(f"{spec['label']} is not installed: {missing[0]}")
        cwd = Path(cwd).expanduser()
        if not cwd.is_dir():
            raise ValueError(f"not a directory: {cwd}")
        # D14: a worktree has exactly one owner, which resumes rather than creates.
        if inside_worktree_root(cwd):
            raise ValueError("that folder belongs to another pane's own branch; "
                             "open or resume that pane instead")
        pr = None
        if worktree:
            why = worktree_refusal(agent)
            if why:
                raise ValueError(why)
            pr = _wt.probe(cwd)
            if pr["refusals"]:
                raise ValueError(pr["refusals"][0])
        # Reserve the slot under the lock and register before start(), so the cap
        # holds under concurrency and `ready` never names an unknown pane.
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
            # Role annotation (roles.py), set before start() so the first save carries it.
            if role:
                pane.role, pane.role_sha, pane.role_delivery = role, role_sha, "preamble"
            self.panes[pane.id] = pane
        if worktree:
            try:
                e = _wt.create(pr, title or "", pane.id, registry=self.worktree_registry())
            except Exception:
                self.panes.pop(pane.id, None)  # no worktree, no pane
                raise
            pane.worktree_id = e["id"]
            pane.cwd = str(_wt.agent_cwd(e))
            pane.title = Pane._default_title(agent, cwd)   # the repo's name, not the slug
            pane._preamble_due = True
            pane.save_meta()
        try:
            pane.start()
        except Exception as e:
            if pane.worktree_id:
                # D14: the pane stays, dead, owning its worktree; Resume retries
                # there and Forget offers Discard.
                pane.state, pane.error = "dead", f"could not start: {e}"
                pane.save_meta()
                raise
            self.panes.pop(pane.id, None)     # never leave a phantom in the roster
            raise
        return pane

    def restore(self):
        """Bring back up to MAX_ROSTER panes that were not deliberately closed.

        Restored panes are `detached` (no process until wanted); MAX_PANES caps
        live processes on resume.
        """
        root = STATE / "panes"
        if not root.is_dir():
            return
        metas, unreadable, orphans = [], 0, []
        try:
            dirs = list(root.iterdir())
        except OSError as e:
            # Loud: a hub that cannot list its panes must not come up looking empty.
            print(f"corral-light: cannot read {root}: {e}", file=sys.stderr,
                  flush=True)
            try:
                (STATE / "DEAD").write_text(f"restore failed: cannot read "
                                            f"{root}: {e}\n", encoding="utf-8")
            except OSError:
                pass
            raise
        for d in dirs:
            mf = d / "meta.json"
            if not mf.exists():
                continue                     # no meta = pre-persistence pane
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
                if not isinstance(m, dict):
                    raise ValueError("not an object")
            except (OSError, ValueError) as e:
                # Counted into notRestored, never silently skipped.
                unreadable += 1
                print(f"corral-light: pane {d.name} not restored — "
                      f"meta.json unreadable: {e}", file=sys.stderr, flush=True)
                continue
            if m.get("pgid") and m.get("agent") in AGENTS:
                orphans.append((m.get("id") or d.name, m.get("pid"), m.get("pgid"),
                                m.get("pid_start"), AGENTS[m["agent"]]["argv"][:2]))
            if m.get("closed") or not m.get("id"):
                continue
            metas.append(m)
        # Reap the previous hub's orphaned adapters before any session/load.
        reaped = acp.reap_orphans(orphans) if orphans else {}
        self.orphans = {k: v for k, v in reaped.items()}
        for key, outcome in reaped.items():
            print(f"corral-light: previous hub's adapter for pane {key}: "
                  f"{outcome}", file=sys.stderr, flush=True)
        metas.sort(key=lambda m: (0 if m.get("pinned") else 1,
                                  m.get("order") if m.get("order") is not None else 10_000,
                                  m.get("created") or ""))
        # Keep the head of the sort (pinned and earliest-ordered first).
        skipped = max(0, len(metas) - MAX_ROSTER)
        for m in metas[:MAX_ROSTER]:
            try:
                p = Pane.from_meta(m, self)
            except Exception as e:           # noqa: BLE001
                unreadable += 1              # one bad pane must not block the rest
                print(f"corral-light: pane {m.get('id')} not restored: "
                      f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
                continue
            self.panes[m["id"]] = p
            # Turns the previous hub left open were cut off: say so once, never re-send.
            try:
                cut = p._turns().recover()
            except Exception:                # noqa: BLE001
                cut = []
            if cut:
                def _show(r):
                    t = r.get("text") or ""
                    # Label cut-off peer messages as such.
                    return ("a message from another pane, " if r.get("kind") == "peer"
                            else "") + repr(t[:PARKED_PREVIEW_CHARS] +
                                            ("…" if len(t) > PARKED_PREVIEW_CHARS else ""))
                p.emit("note", {
                    "text": f"{len(cut)} turn(s) were interrupted when the hub "
                            f"stopped and will not be re-sent: "
                            + "; ".join(f"{_show(r)} ({r.get('state')})" for r in cut),
                    "interrupted_turns": [r["turn"] for r in cut]})
            outcome = self.orphans.get(m["id"])
            if outcome in ("reaped", "killed"):
                p.emit("note", {"text": "the agent process from before the hub "
                                        "restarted was still running; it was "
                                        f"stopped ({outcome}) so resuming this "
                                        "conversation cannot attach a second "
                                        "agent to it"})
            if m.get("pgid") or m.get("pid"):
                _clear_pid_record(p.dir)     # the previous hub's process is handled
        # Two open metas naming one seat: the earlier-created one keeps it.
        self._withhold_colliding_seats(metas)
        # Replies the previous hub held in memory are recorded dropped, never re-sent.
        self._peer_queue_orphans()
        self.not_restored = skipped + unreadable   # said out loud, not dropped
        self._worktree_restore()

    def _reserve_live(self, pane):
        """Refuse to attach a process when MAX_PANES live ones exist; marks the
        pane `starting` under the lock so concurrent resumes cannot both pass."""
        with self._lock:
            live = [p for p in self.panes.values()
                    if p is not pane and p.state not in ("dead", "detached")]
            if len(live) >= MAX_PANES:
                raise ValueError(
                    f"{MAX_PANES} live panes is the cap — close or pause one first")
            if pane.state in ("detached", "dead"):
                pane.state = "starting"




    def reopen(self, pane_id):
        """Bring an archived conversation back, detached."""
        if pane_id in self.panes:
            return self.panes[pane_id]
        with self._lock:
            if len(self.panes) >= MAX_ROSTER:
                raise ValueError(
                    f"{MAX_ROSTER} panes are already on the roster, live or "
                    f"detached — close or forget one before reopening another")
        d = STATE / "panes" / pane_id
        try:
            m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError(f"no archived conversation {pane_id}")
        if not m.get("closed"):
            raise ValueError("that conversation is not archived")
        pane = Pane.from_meta(m, self)
        if pane.worktree_id:
            pane.worktree_blocked = self._worktree_blocked_reason(self.worktree_entry(pane))
        pane.save_meta(closed=False)      # or the next restart re-archives it
        with self._lock:
            if pane_id in self.panes:
                return self.panes[pane_id]
            if len(self.panes) >= MAX_ROSTER:
                raise ValueError(
                    f"{MAX_ROSTER} panes are already on the roster, live or "
                    f"detached — close or forget one before reopening another")
            self.panes[pane_id] = pane
        pane.emit("reopened", {})
        self._withhold_if_taken(pane)
        return pane





    def reorder(self, ids):
        """Set an explicit order from a list of pane ids; unknown ids are ignored."""
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
        if seen:
            self._broadcast_layouts()
        return seen

    def set_pinned(self, pane_id, flag):
        p = self.get(pane_id)
        p.pinned = bool(flag)
        p.save_meta()
        self._resort()
        self._broadcast_layouts()
        return p.pinned

    def broadcast_layout(self, pane):
        """Tell every browser about a persisted layout change.

        A lightweight event outside the pane's event ring and sequence.
        """
        self.broadcast({"seq": 0, "at": _now(), "pane": pane.id,
                        "kind": "layout", "data": {
                            "minimized": bool(pane.minimized),
                            "pinned": bool(pane.pinned),
                            "order": pane.order,
                        }})

    def _broadcast_layouts(self):
        for pane in self.panes.values():
            self.broadcast_layout(pane)




    # ── own-branch worktrees ─────────────────────────────────────────────────

    def worktree_registry(self):
        reg = getattr(self, "_wt_registry", None)
        if reg is None:
            reg = self._wt_registry = _wt.Registry()
        return reg

    def worktree_entry(self, pane):
        """The pane's registry entry, or None (unreadable entries count as none)."""
        if not pane.worktree_id:
            return None
        try:
            return self.worktree_registry().read(pane.worktree_id)
        except (OSError, ValueError, _wt.RegistryVersionError):
            return None

    def _worktree_blocked_reason(self, entry):
        if entry is None:
            return "this pane's own-branch record is missing; see `corral-light worktrees`"
        phase = entry.get("phase")
        if phase == "trashed":
            return "this branch was discarded; restore it from `corral-light worktrees` to resume"
        if phase in ("missing", "tampered", "purged"):
            return f"this pane's worktree is {phase}: {entry.get('error') or ''}".strip()
        if any(o.get("state") == "unknown" for o in entry.get("ops") or []):
            return "an action on this branch has an unknown outcome; resolve it with `corral-light worktrees`"
        return None

    def _worktree_restore(self):
        """After restore(): block panes whose worktree is not usable, then
        reconcile in the background and report what it found."""
        owned = [p for p in self.panes.values() if p.worktree_id]
        if not owned and not self.worktree_registry().all():
            return
        for p in owned:
            p.worktree_blocked = self._worktree_blocked_reason(self.worktree_entry(p))

        def run():
            try:
                notes = _wt.reconcile(self.worktree_registry())
            except Exception as e:           # noqa: BLE001 — a note, never a crash
                print(f"corral-light: worktree reconcile failed: {e}", file=sys.stderr, flush=True)
                return
            by_id = {p.worktree_id: p for p in list(self.panes.values()) if p.worktree_id}
            for n in notes:
                p = by_id.get(n.get("id"))
                if p:
                    p.emit("note", {"text": f"own branch: {n['note']}"}, activity=False)
                else:
                    print(f"corral-light: worktrees: {n['note']}", file=sys.stderr, flush=True)
            for p in by_id.values():
                p.worktree_blocked = self._worktree_blocked_reason(self.worktree_entry(p))
        t = threading.Thread(target=run, daemon=True, name="worktree-reconcile")
        self._wt_reconcile_thread = t
        t.start()

    def worktree_turn_ended(self, pane):
        """Queue a summary for `pane` on the single worker (at most one pending each)."""
        lock = self.__dict__.setdefault("_wt_sum_lock", threading.Lock())
        with lock:
            pending = self.__dict__.setdefault("_wt_sum_pending", [])
            if pane.id not in pending:
                pending.append(pane.id)
            if self.__dict__.get("_wt_sum_running"):
                return
            self._wt_sum_running = True
        threading.Thread(target=self._worktree_summary_worker, daemon=True,
                         name="worktree-summary").start()

    def _worktree_summary_worker(self):
        lock = self._wt_sum_lock
        while True:
            with lock:
                if not self._wt_sum_pending:
                    self._wt_sum_running = False
                    return
                pid = self._wt_sum_pending.pop(0)
            p = self.panes.get(pid)
            e = self.worktree_entry(p) if p else None
            if not e or e.get("phase") != "active":
                continue
            try:
                summ = _wt.summary(e)
            except Exception as err:         # noqa: BLE001 — a note, never a crash
                p.emit("note", {"text": f"could not count this branch's changes: {err}"},
                       activity=False)
                continue
            p.worktree_summary = summ
            p.emit("worktree", {"summary": summ}, activity=False)

    def _worktree_action(self, pane_id, fn, drain_after):
        """Run a review action on a quiet pane (D11): refuse while busy, hold the
        queue meanwhile, then drain (Commit/Publish) or park (Discard/failure)."""
        p = self.get(pane_id)
        if not p.worktree_id:
            raise ValueError("this pane is not on its own branch")
        e = self.worktree_entry(p)
        if e is None:
            raise _wt.Refused("missing", "this pane's own-branch record is missing")
        if not p._action_lock.acquire(blocking=False):
            raise _wt.Refused("busy", "another action is already running on this pane")
        ok = False
        try:
            with p._turn_lock:
                if p._turn_running or p.state in ("busy", "needs-you", "uncertain") or p.pending:
                    raise _wt.Refused("busy", "the agent is still working; wait for its turn to end")
                p.held = True
            result = fn(p, e)
            ok = True
            return result
        finally:
            try:
                p.release_hold(drain=ok and drain_after)
            finally:
                p._action_lock.release()

    def worktree_snapshot(self, pane_id):
        """Open review: snapshot (a tree OID) plus its diff."""
        def go(p, e):
            snap = _wt.snapshot(e, tmp_dir=p.dir)
            snap["diff"] = _wt.diff(e, snap["tree"])
            snap["summary"] = p.worktree_summary = _wt.summary(e)
            return snap
        return self._worktree_action(pane_id, go, drain_after=True)

    def worktree_commit(self, pane_id, tree, head, index_id, message):
        def go(p, e):
            r = _wt.commit_tree(e, tree, index_id, message, expect_head=head,
                                registry=self.worktree_registry(), tmp_dir=p.dir)
            p.worktree_summary = _wt.summary(self.worktree_entry(p))
            p.emit("worktree", {"summary": p.worktree_summary, "commit": r["commit"]},
                   activity=False)
            return r
        return self._worktree_action(pane_id, go, drain_after=True)

    def worktree_publish(self, pane_id, oid, tree, remote, push_url, pr=None):
        def go(p, e):
            r = _wt.push(e, remote, push_url, oid, tree, registry=self.worktree_registry())
            if pr:
                r.update(_wt.open_pr(self.worktree_entry(p), pr.get("title") or "",
                                     pr.get("body") or "", pr.get("repo") or "",
                                     registry=self.worktree_registry(), remote=remote))
            p.emit("worktree", {"published": r}, activity=False)
            return r
        return self._worktree_action(pane_id, go, drain_after=True)

    def worktree_discard(self, pane_id, tree):
        """Stop the agent (cancel, then end its process group), then discard (D9)."""
        def go(p, e):
            if p.client and p.client.alive:
                p._expect_exit = True
                with contextlib.suppress(Exception):
                    p.client.cancel(p.acp_session)
                p.client.close()           # TERM then KILL of the adapter's group
                p.client = None
            p.pid = p.pgid = p.pid_start = None
            p.state = "detached"
            r = _wt.discard(e, tree, registry=self.worktree_registry(), tmp_dir=p.dir)
            p.worktree_blocked = self._worktree_blocked_reason(self.worktree_entry(p))
            p.save_meta()
            p.emit("worktree", {"discarded": r}, activity=False)
            p.emit("note", {"text": f"branch discarded: kept as {r['recovery_ref']}; "
                                    f"files moved to {r['trash_path']}"})
            return r
        return self._worktree_action(pane_id, go, drain_after=False)

    def state(self, since=None):
        since = since or {}
        # Copy with list(): another thread may add or remove panes mid-iteration.
        return {"panes": [p.snapshot(since.get(p.id, 0)) for p in list(self.panes.values())],
                "agents": available_agents(),
                # The Claude login's expiry, for the rail's early warning (cached).
                "claudeAuth": claude_auth.status(),
                # Group definitions ship with `agents`, whose `group` tags reference them.
                "agentGroups": agent_groups(),
                "postures": sorted(POSTURES),
                # Default cwd, decided by the host (see default_cwd).
                "defaultCwd": str(default_cwd()),
                # Where transcripts live on this machine, shown in the empty state.
                "dataDir": str(STATE),
                # Real directories on this host, offered as a datalist; the field stays free text.
                "cwdSuggestions": cwd_suggestions(
                    [p.cwd for p in list(self.panes.values())]),
                "catalog": self.catalog,
                "archived": self.archived(),
                # Panes the roster cap kept from being restored; surfaced, not hidden.
                "notRestored": self.not_restored,
                # Scheduled prompts (schedule.py) — what will start on its own.
                "schedule": (self.schedule.list()
                             if getattr(self, "schedule", None) else []),
                "at": int(time.time())}
