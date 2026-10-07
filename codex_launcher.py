#!/usr/bin/env python3
"""Corral-owned ChatGPT (Codex) ACP launcher.

Launches @agentclientprotocol/codex-acp over OpenAI's codex app-server, with
a dedicated CODEX_HOME so no ambient ~/.codex config is inherited. Auth stays
in the CLI's own state (`codex login`); no key enters argv, env, or logs.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ADAPTER = HERE / "spike" / "node_modules" / ".bin" / "codex-acp"
# The version-matched codex CLI the adapter bundles, used for login so auth
# state is written by the same codex the lane runs.
BUNDLED_CODEX = HERE / "spike" / "node_modules" / ".bin" / "codex"
CODEX_HOME = Path(os.environ.get(
    "CORRAL_CODEX_HOME", str(Path.home() / ".config/corral-light/codex-home")))
# Optional node install off PATH; ignored when absent.
_NODE_BIN = Path.home() / ".hermes" / "node" / "bin"
NODE_BIN = _NODE_BIN if _NODE_BIN.is_dir() else None

# Workspace-write, network off; escalations become ACP permission requests.
# Override: CORRAL_CODEX_MODE.
DEFAULT_MODE = "agent"


def resolve_adapter(explicit: str | None = None) -> str | None:
    """Return the adapter executable without probing auth or starting it."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        env = os.environ.get("CORRAL_CODEX_ACP")
        if env:
            candidates.append(Path(env).expanduser())
        candidates.append(DEFAULT_ADAPTER)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def auth_present() -> bool:
    """True if auth.json exists and carries a usable token."""
    from sessions import usable_credential
    return usable_credential(CODEX_HOME / "auth.json")


# ── login facts for the module feed (docs/finops-module-plan.md §4.4) ──────
# From the newest rollout only: session_meta.creator_account_id names the
# account, token_count.rate_limits.plan_type the plan. auth.json is never
# opened here (its existence is a stat).
ROLLOUT_TAIL_BYTES = 256 * 1024
_ENUM_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


def _enum(v):
    return v if isinstance(v, str) and 0 < len(v) <= 64 and set(v) <= _ENUM_OK else None


def newest_rollout(home: Path | None = None) -> Path | None:
    """The most recently written rollout under CODEX_HOME/sessions."""
    root = Path(home or CODEX_HOME) / "sessions"
    best, best_m = None, -1.0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                continue
            p = Path(dirpath) / name
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m > best_m:
                best, best_m = p, m
    return best


def _rollout_account_and_plan(path: Path):
    """(creator_account_id, plan_type) out of one rollout; either may be None.
    Only these two fields are kept from what is parsed."""
    import json
    account = plan = None
    try:
        with path.open("rb") as fh:
            for _ in range(5):                    # session_meta is the header
                line = fh.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("type") == "session_meta":
                    p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                    a = p.get("creator_account_id")
                    account = a if isinstance(a, str) and a.strip() else None
                    break
            size = path.stat().st_size
            fh.seek(max(0, size - ROLLOUT_TAIL_BYTES))
            tail = fh.read()
    except OSError:
        return account, plan
    for line in reversed(tail.split(b"\n")):
        if b'"token_count"' not in line or b'"plan_type"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        p = d.get("payload") if isinstance(d, dict) else None
        rl = p.get("rate_limits") if isinstance(p, dict) else None
        if isinstance(rl, dict) and _enum(rl.get("plan_type")):
            plan = rl["plan_type"]
            break
    return account, plan


def login_facts(hasher=None, home: Path | None = None) -> dict:
    """{"present", "plan", "fingerprint", "source"}: never a token or raw id."""
    home = Path(home or CODEX_HOME)
    out = {"present": (home / "auth.json").is_file(), "plan": None,
           "fingerprint": None, "source": "rollout"}
    newest = newest_rollout(home)
    if newest is None:
        out["source"] = None
        return out
    account, plan = _rollout_account_and_plan(newest)
    out["plan"] = plan
    if hasher is not None and account:
        out["fingerprint"] = hasher(account)
    return out


def login_command() -> str:
    """The exact shell line to paste, including creating CODEX_HOME (codex
    refuses to start when it does not exist)."""
    codex = BUNDLED_CODEX if BUNDLED_CODEX.is_file() else Path("codex")
    return (f"mkdir -p {CODEX_HOME} && "
            f"CODEX_HOME={CODEX_HOME} {codex} login --device-auth")


# Ambient provider credentials never reach the adapter.
_STRIP_ENV_PREFIXES = ("OPENAI_", "ANTHROPIC_", "GEMINI_", "GOOGLE_", "XAI_", "GROK_")


def build_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(_STRIP_ENV_PREFIXES)}
    env["CODEX_HOME"] = str(CODEX_HOME)
    env["NO_BROWSER"] = "1"          # headless host; device-auth, not a browser
    # Explicit, not setdefault: an ambient INITIAL_AGENT_MODE must not win.
    env["INITIAL_AGENT_MODE"] = os.environ.get("CORRAL_CODEX_MODE", DEFAULT_MODE)
    if NODE_BIN and str(NODE_BIN) not in env.get("PATH", ""):
        env["PATH"] = f"{NODE_BIN}:{env.get('PATH', '')}"
    return env


def unavailable_reason() -> str | None:
    """Why this lane cannot open right now, or None if it can."""
    if not resolve_adapter():
        # Name the absolute path actually probed.
        return (f"codex-acp adapter not installed at {DEFAULT_ADAPTER} — "
                f"run `npm install` in {HERE / 'spike'} (or set "
                f"CORRAL_CODEX_ACP)")
    if not auth_present():
        return f"not logged in — run: {login_command()}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch the ChatGPT (Codex) ACP adapter")
    parser.add_argument("--print-argv", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    reason = unavailable_reason()
    if reason:
        print(f"ChatGPT (Codex) unavailable: {reason}", file=sys.stderr, flush=True)
        return 127
    adapter = resolve_adapter()
    if args.print_argv:
        print(adapter, flush=True)
        return 0
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    CODEX_HOME.chmod(0o700)
    os.execve(adapter, [adapter], build_env())
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
