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
