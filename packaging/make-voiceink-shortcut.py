#!/usr/bin/env python3
"""Build and open the "Corral Dictate" Shortcuts shortcut for the composer's 🎙
(dictate.py) on a Mac with VoiceInk installed.

It holds one action, VoiceInk's "Toggle VoiceInk Recorder", with Show When Run
OFF. That matters: shown, the action's "VoiceInk recorder toggled" dialog takes
focus from the browser, so VoiceInk records and transcribes but has nowhere to
paste. The file is signed with `shortcuts sign` and opened; macOS then asks
you to click Add Shortcut (and, on the first run, to allow it to use VoiceInk).

    python3 packaging/make-voiceink-shortcut.py [--name "Corral Dictate"] [--no-open]
"""
import argparse
import plistlib
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

BUNDLE = "com.prakashjoshipax.VoiceInk"
INTENT = "ToggleMiniRecorderIntent"


def team_id(app):
    out = subprocess.run(["codesign", "-dv", str(app)], capture_output=True, text=True).stderr
    for line in out.splitlines():
        if line.startswith("TeamIdentifier="):
            return line.split("=", 1)[1].strip()
    sys.exit(f"could not read VoiceInk's team id from {app}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Corral Dictate")
    ap.add_argument("--app", default="/Applications/VoiceInk.app")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    app = Path(a.app)
    if not app.exists():
        sys.exit(f"VoiceInk is not at {app}")
    wf = {
        "WFWorkflowMinimumClientVersion": 900, "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": 61440},
        "WFWorkflowImportQuestions": [], "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowTypes": [], "WFWorkflowOutputContentItemClasses": [], "WFQuickActionSurfaces": [],
        "WFWorkflowHasShortcutInputVariables": False, "WFWorkflowHasOutputFallback": False,
        "WFWorkflowActions": [{
            "WFWorkflowActionIdentifier": f"{BUNDLE}.{INTENT}",
            "WFWorkflowActionParameters": {
                "AppIntentDescriptor": {"AppIntentIdentifier": INTENT, "BundleIdentifier": BUNDLE,
                                        "Name": "VoiceInk", "TeamIdentifier": team_id(app)},
                "ShowWhenRun": False,
                "UUID": str(uuid.uuid4()).upper()}}],
    }
    d = Path(tempfile.mkdtemp(prefix="corral-dictate-shortcut-"))
    raw, signed = d / "unsigned.shortcut", d / f"{a.name}.shortcut"
    with open(raw, "wb") as f:
        plistlib.dump(wf, f, fmt=plistlib.FMT_BINARY)
    subprocess.run(["shortcuts", "sign", "--mode", "people-who-know-me",
                    "--input", str(raw), "--output", str(signed)], check=True)
    print(signed)
    if not a.no_open:
        subprocess.run(["open", str(signed)], check=True)
        print("Click Add Shortcut. If you named it something other than "
              f"'Corral Dictate', set mac_shortcut in ~/.config/corral-light/dictate.json.")


if __name__ == "__main__":
    main()
