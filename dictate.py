"""Dictate: the composer's 🎙 starts the dictation tool of the device the
operator is at, and that tool types into the focused composer.

The hub only acts for a browser on its own machine (loopback, no proxy or
Serve header). It runs that machine's toggle: on macOS the Shortcuts shortcut
"Corral Dictate" (VoiceInk's "Toggle VoiceInk Recorder" with Show When Run
OFF: shown, its dialog takes focus and the transcript pastes nowhere;
packaging/make-voiceink-shortcut.py builds it), on Linux
`voxtype record toggle`. A browser elsewhere starts its own machine's tool
with a URL (see `client()`), so a click on one machine never turns on a
microphone on another.

Config (optional): ~/.config/corral-light/dictate.json
  {"mac_shortcut": "Corral Dictate",             the shortcut's exact name
   "local_command": ["voxtype", "record", "toggle"]}   overrides the default
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("CORRAL_LIGHT_CONFIG_DIR",
                             Path.home() / ".config/corral-light")) / "dictate.json"
DEFAULT_SHORTCUT = "Corral Dictate"
LINUX_SCHEME = "corral-dictate"          # packaging/corral-dictate-handler.sh
TIMEOUT_S = 15


class DictateError(Exception):
    """Why the toggle did not run; safe to show the operator."""


def load(path=None):
    p = Path(path or CONFIG)
    if not p.exists():
        return {}
    try:
        c = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise DictateError(f"dictate.json unreadable: {e}") from None
    if not isinstance(c, dict):
        raise DictateError("dictate.json must be an object")
    name = c.get("mac_shortcut")
    if name is not None and not (isinstance(name, str) and 0 < len(name) <= 80):
        raise DictateError("dictate.json: mac_shortcut must be a short string")
    cmd = c.get("local_command")
    if cmd is not None and not (isinstance(cmd, list) and cmd
                                and all(isinstance(x, str) and x for x in cmd)):
        raise DictateError("dictate.json: local_command must be a non-empty argv list")
    return c


def shortcut_name(cfg):
    return cfg.get("mac_shortcut") or DEFAULT_SHORTCUT


def local_command(cfg, platform=None):
    """The argv that toggles dictation on this machine, or None."""
    if cfg.get("local_command"):
        return [os.path.expanduser(x) for x in cfg["local_command"]]
    platform = platform or sys.platform
    if platform == "darwin":
        return ["shortcuts", "run", shortcut_name(cfg)]
    if platform.startswith("linux") and shutil.which("voxtype"):
        return ["voxtype", "record", "toggle"]
    return None


def client(cfg):
    """What the page needs to start a tool on the machine it runs on."""
    return {"macShortcut": shortcut_name(cfg), "linuxScheme": LINUX_SCHEME}


def toggle(cfg, run=subprocess.run):
    """Run this machine's toggle once."""
    argv = local_command(cfg)
    if not argv:
        raise DictateError("no dictation tool on the hub's machine")
    try:
        r = run(argv, capture_output=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise DictateError(f"{argv[0]} did not finish in {TIMEOUT_S} s") from None
    except OSError as e:
        raise DictateError(f"{argv[0]} would not start: {e.strerror}") from None
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        hint = ""
        if argv[:2] == ["shortcuts", "run"]:
            hint = (f' (no Shortcuts shortcut named "{argv[2]}"? '
                    f'packaging/make-voiceink-shortcut.py builds it)')
        raise DictateError(((err[-1] if err else f"exit {r.returncode}") + hint)[:300])
    return {"ok": True, "ran": argv[0]}
