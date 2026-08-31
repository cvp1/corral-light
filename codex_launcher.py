#!/usr/bin/env python3
"""Corral-owned ChatGPT (Codex) ACP launcher.

Launches @agentclientprotocol/codex-acp — the ACP-org adapter over OpenAI's
codex app-server (same org as the Claude adapter; Zed launches this exact
package). OpenAI is first-party for sensitive data
(decisions/openai-first-party-2026-07-31.md); lane adoption record:
decisions/codex-acp-lane-2026-08-23.md.

Two properties this launcher exists to hold:

- **A dedicated CODEX_HOME.** ~/.codex on this host is pinned to the .21
  Ollama fleet (the July codex-on-local experiment) — a lane inheriting it
  would chat with gemma while wearing a ChatGPT label, the exact
  "config outside the repo loses its pin" failure mode the opencode
  retirement eliminated. This lane's config and auth live in a corral-owned
  home instead, where there is nothing to inherit and nothing to drift.
- **Auth stays inside the CLI's own state.** ChatGPT subscription login
  (auth.json under CODEX_HOME, written by `codex login`); no key enters
  argv, env, or logs. Refuses up front when unauthenticated — the gemini
  lesson: a clear reason in the picker beats a dead pane at session/new.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ADAPTER = HERE / "spike" / "node_modules" / ".bin" / "codex-acp"
# The version-matched codex CLI the adapter bundles — the binary Craig logs
# in with, so auth state is written by the same codex the lane runs.
BUNDLED_CODEX = HERE / "spike" / "node_modules" / ".bin" / "codex"
CODEX_HOME = Path(os.environ.get(
    "CORRAL_CODEX_HOME", str(Path.home() / ".config/corral-light/codex-home")))
# Optional: a node install off PATH (hermes' bundle on ranch). Absent on a
# stock Mac, where node IS on PATH -- so a non-existent dir must not shadow it.
_NODE_BIN = Path.home() / ".hermes" / "node" / "bin"
NODE_BIN = _NODE_BIN if _NODE_BIN.is_dir() else None

# codex's sandboxed middle tier: workspace-write, network off, escalations
# become ACP permission requests in the rail. Not read-only (a coding lane
# that can never edit is a button that lies) and not agent-full-access (the
# rail must see escapes). Override: CORRAL_CODEX_MODE.
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
    return (CODEX_HOME / "auth.json").is_file()


def login_command() -> str:
    codex = BUNDLED_CODEX if BUNDLED_CODEX.is_file() else Path("codex")
    return f"CODEX_HOME={CODEX_HOME} {codex} login --device-auth"


# Vars this launcher's own docstring promises never reach the process --
# an ambient one (a dev shell with a key exported for something unrelated,
# not systemd's own clean unit env) would otherwise ride along via the
# `dict(os.environ)` copy below. Found 2026-08-23 bugbash panel (GPT-5.6-sol).
_STRIP_ENV_PREFIXES = ("OPENAI_", "ANTHROPIC_", "GEMINI_", "GOOGLE_", "XAI_", "GROK_")


def build_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(_STRIP_ENV_PREFIXES)}
    env["CODEX_HOME"] = str(CODEX_HOME)
    env["NO_BROWSER"] = "1"          # headless host; device-auth, not a browser
    # Explicit, not setdefault: this launcher OWNS the sandbox posture: an
    # ambient INITIAL_AGENT_MODE (however it got there) must not silently
    # override the mode Corral just computed for this pane.
    env["INITIAL_AGENT_MODE"] = os.environ.get("CORRAL_CODEX_MODE", DEFAULT_MODE)
    if NODE_BIN and str(NODE_BIN) not in env.get("PATH", ""):
        env["PATH"] = f"{NODE_BIN}:{env.get('PATH', '')}"
    return env


def unavailable_reason() -> str | None:
    """Why this lane cannot open right now, or None if it can."""
    if not resolve_adapter():
        return ("codex-acp adapter not installed — npm install in corral-light/spike "
                "(or set CORRAL_CODEX_ACP)")
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
        print(f"ChatGPT (Codex) unavailable: {reason}", file=sys.stderr)
        return 127
    adapter = resolve_adapter()
    if args.print_argv:
        print(adapter)
        return 0
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    CODEX_HOME.chmod(0o700)
    os.execve(adapter, [adapter], build_env())
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
