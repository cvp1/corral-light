#!/usr/bin/env python3
"""AI-OS-owned Grok ACP launcher.

Provider discovery and process startup belong to AI-OS. Authentication stays
inside the Grok CLI; no credential enters argv, environment, logs, or context.
Herdr is optional and is not part of this ACP path.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_CANDIDATES = (
    Path.home() / ".grok" / "bin" / "grok",
    Path.home() / ".local" / "bin" / "grok",
)


def resolve_grok(explicit: str | None = None) -> str | None:
    """Return an executable path without probing auth or starting a process."""
    if explicit:
        candidate = Path(explicit).expanduser()
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    candidates = []
    env = os.environ.get("AIOS_GROK_BIN") or os.environ.get("CORRAL_GROK_BIN")
    if env:
        candidates.append(Path(env).expanduser())
    on_path = shutil.which("grok")
    if on_path:
        candidates.append(Path(on_path))
    candidates.extend(DEFAULT_CANDIDATES)
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


# Where the Grok CLI keeps its own credential.
GROK_HOME = Path(os.environ.get("CORRAL_GROK_HOME", Path.home() / ".grok"))


def auth_present() -> bool:
    """True if auth.json exists and carries a usable token."""
    from sessions import usable_credential
    return usable_credential(GROK_HOME / "auth.json")


def login_facts() -> dict:
    """Sanitized login facts for the module feed. The CLI keeps its auth mode
    in auth.json beside the tokens, so it is not read: the mode is reported
    as "present" (a stat), never parsed out of the token file."""
    present = (GROK_HOME / "auth.json").is_file()
    return {"present": present, "auth_mode": "present" if present else None,
            "fingerprint": None, "source": "stat"}


def login_command(grok: str | None = None) -> str:
    return f"{grok or 'grok'} login"


def unavailable_reason() -> str | None:
    """Why this lane cannot open right now (not installed or not signed in),
    or None if it can."""
    grok = resolve_grok()
    if not grok:
        return "Grok CLI not installed"
    if not auth_present():
        return f"not logged in — run: {login_command(grok)}"
    return None


# Corral postures the Grok agent can realize, and the argv that realizes each.
# `grok agent stdio` advertises no ACP permission mode (configOptions: null)
# and accepts no --permission-mode; its only knob is --always-approve, applied
# at spawn. With no flag the agent runs reads itself but raises a card for
# every shell command its own policy does not auto-allow (pipelines, heredocs,
# process inspection...), so a pane under `auto` blocked on a card per command.
#   auto   -> --always-approve: Grok approves every tool call itself (no
#             classifier, no escalation — looser than Claude's `auto`).
#   strict -> no flag: Grok's own default, a card for each shell command it
#             does not auto-allow (never looser than Claude's `default`).
#   edits  -> not realizable: Grok has no accept-edits mode, so the pane
#             reports the posture as NOT enforced and runs under Grok's default.
POSTURE_ARGV = {
    "auto": ["--always-approve"],
    "strict": [],
}
POSTURE_REALIZED = {
    "auto": "--always-approve: Grok approves every tool call itself",
    "strict": "Grok's own default: a card for each shell command it does not auto-allow",
}


def build_argv(grok: str, model: str | None = None,
               posture: str | None = None) -> list[str]:
    # --model and --always-approve are options of `grok agent` and must
    # precede `stdio`.
    argv = [grok, "agent"]
    if model:
        # The agent does not support session/set_config_option, so the model
        # can only be chosen at spawn.
        argv += ["--model", model]
    if posture:
        # An unmapped posture (edits) adds nothing: Grok's default applies and
        # sessions.py reports the posture as not enforced.
        argv += POSTURE_ARGV.get(posture, [])
    argv.append("stdio")
    return argv


_AVAILABLE_RE = re.compile(r"^\s*([*-])\s*(\S+?)(?:\s*\(default\))?\s*$")


def resolve_models(grok: str, timeout: float = 5.0) -> list[dict]:
    """The account's live model list, parsed from `grok models` (no LLM call).
    Empty on any failure, meaning no picker; never a guessed list."""
    try:
        result = subprocess.run([grok, "models"], capture_output=True,
                                text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    out, in_list = [], False
    for line in result.stdout.splitlines():
        if line.strip().startswith("Available models"):
            in_list = True
            continue
        if not in_list:
            continue
        m = _AVAILABLE_RE.match(line)
        if not m:
            if line.strip():
                break     # a non-blank, non-matching line ends the list
            continue
        marker, model_id = m.groups()
        out.append({"value": model_id, "name": model_id,
                    "default": marker == "*"})
    return out


def resolve_default_model(grok: str, timeout: float = 5.0) -> str | None:
    models = resolve_models(grok, timeout)
    return next((m["value"] for m in models if m.get("default")),
                models[0]["value"] if models else None)


def unavailable_message() -> str:
    return (
        "Grok unavailable: install the Grok CLI or set AIOS_GROK_BIN; "
        "AI-OS does not fall back to oc or raw opencode."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch Grok's ACP stdio agent")
    parser.add_argument("--print-argv", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    reason = unavailable_reason()
    if reason:
        print(f"Grok unavailable: {reason}", file=sys.stderr, flush=True)
        return 127
    grok = resolve_grok()
    if not grok:
        print(unavailable_message(), file=sys.stderr, flush=True)
        return 127
    # CORRAL_GROK_MODEL: set only when a model was requested; otherwise the
    # CLI's default applies. CORRAL_POSTURE: the pane's posture, set by
    # sessions.spawn_env for this lane.
    command = build_argv(grok, os.environ.get("CORRAL_GROK_MODEL") or None,
                         os.environ.get("CORRAL_POSTURE") or None)
    if args.print_argv:
        print(" ".join(command), flush=True)
        return 0
    os.execv(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
