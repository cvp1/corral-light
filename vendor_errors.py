"""vendor_errors — tell a vendor failure apart from a real answer.

2026-11-21: the native Antigravity server answered a prompt with
"Agent execution error: Error 404, Message: Requested entity was not found. ..."
as ordinary agent text and ended the turn with stopReason end_turn. Consult
reported it `ok: true`, and a panel would have sealed it as that arm's answer.
A turn whose whole answer is the vendor's error frame is a failed turn.

Matched only at the START of the answer, in the vendor's own wording, so a
model that discusses an error (or quotes one mid-answer) is never misread.
"""
from __future__ import annotations

import re

# Each pattern is the vendor's literal error frame, anchored at the start.
_FRAMES = (
    # Antigravity (native ACP server), seen live 2026-11-21.
    re.compile(r"Agent execution error:"),
    re.compile(r"request failed \(code \d{3}\)"),
)
WHY_CAP = 300


def vendor_error(text):
    """The vendor's error line if `text` is a vendor failure frame, else None."""
    s = (text or "").lstrip()
    if not any(p.match(s) for p in _FRAMES):
        return None
    return "vendor error: " + s.splitlines()[0][:WHY_CAP]
