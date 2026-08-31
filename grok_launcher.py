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


def build_argv(grok: str, model: str | None = None) -> list[str]:
    # --model belongs to `grok agent`, not `grok agent stdio` -- it is a
    # Clap option on the PARENT command and must appear before the
    # subcommand token or the CLI refuses the whole invocation ("unexpected
    # argument '--model' found"), confirmed live, 2026-08-23.
    argv = [grok, "agent"]
    if model:
        # ACP's session/set_config_option returns -32601 "Method not found"
        # for this agent -- confirmed live, 2026-08-23 -- so a model can
        # only be chosen at PROCESS SPAWN, as a CLI flag, never afterward
        # against a running session. There is no live "change model" path
        # for Grok and there cannot be one until the vendor CLI's ACP mode
        # reports real configOptions.
        argv += ["--model", model]
    argv.append("stdio")
    return argv


_AVAILABLE_RE = re.compile(r"^\s*([*-])\s*(\S+?)(?:\s*\(default\))?\s*$")


def resolve_models(grok: str, timeout: float = 5.0) -> list[dict]:
    """The account's real, live model list, parsed from `grok models` -- a
    local CLI metadata read against the CLI's own cache/account state, not
    an LLM call. Empty list on any failure; callers must treat that as
    "no picker available", never fall back to a guessed/hardcoded list.

    Exists because ACP mode reports configOptions: null for this agent --
    no model, no effort, nothing -- so both the pane header (which model is
    this?) and the new-pane dialog (which model do you want?) had nothing
    to show, even though the underlying CLI has always known both the
    default (grok-4.6, live-confirmed) and the full list.
    """
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
    grok = resolve_grok()
    if not grok:
        print(unavailable_message(), file=sys.stderr)
        return 127
    # Set by sessions.py's Pane.start()/resume() from self.want_model, only
    # when Craig actually requested one in the new-pane dialog -- absent,
    # this launches with no --model flag at all, same as before this
    # existed, and the CLI's own default (grok-4.6) applies.
    command = build_argv(grok, os.environ.get("CORRAL_GROK_MODEL") or None)
    if args.print_argv:
        print(" ".join(command))
        return 0
    os.execv(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
