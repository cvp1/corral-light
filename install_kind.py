"""install_kind — how this copy of Corral Light was installed.

A git checkout (install.sh, or a clone by hand) updates itself with git. A
Homebrew install does not: its code lives in a versioned keg that only brew
may change, so `update`, `lanes update` and `install-service` hand over to
brew instead. The formula writes install-kind.json into the keg; a keg
without it (an older formula, or a copy made by hand) is still recognised by
its Cellar path, and then every brew detail falls back to a safe default.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKER = "install-kind.json"
FORMULA = "corral-light"
# `brew services` labels: current Homebrew, then older releases.
BREW_LABELS = ("sh.brew.corral-light", "homebrew.mxcl.corral-light")


def detect(root=None):
    """-> {"kind": "git"|"homebrew"|"unknown", ...}. Homebrew adds
    formula, opt_root (the unversioned path that survives an upgrade),
    python and brew. Never raises."""
    root = Path(root or ROOT)
    marker = root / MARKER
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict) and data.get("kind") == "homebrew":
        return {"kind": "homebrew",
                "formula": str(data.get("formula") or FORMULA),
                "opt_root": str(data.get("opt_root") or root),
                "python": data.get("python"),
                "brew": str(data.get("brew") or shutil.which("brew") or "brew")}
    if (root / ".git").exists():
        return {"kind": "git"}
    parts = root.resolve().parts
    if "Cellar" in parts:
        i = parts.index("Cellar")
        prefix = Path(*parts[:i]) if i else Path("/")
        name = parts[i + 1] if len(parts) > i + 1 else FORMULA
        return {"kind": "homebrew", "formula": name,
                "opt_root": str(prefix / "opt" / name / "libexec"),
                "python": None,
                "brew": str(prefix / "bin" / "brew")}
    return {"kind": "unknown"}


def is_homebrew(root=None):
    return detect(root)["kind"] == "homebrew"
