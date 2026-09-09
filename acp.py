#!/usr/bin/python3
"""acp — Corral Light's ACP client. The code lives in `corral_core/acp.py`.

This module is an ALIAS, not an implementation, and that is the whole point.
Until 2026-09-09 there were two ACP clients — one here, one in full Corral —
and on 2026-08-31 each of them got a safety fix the other never saw: Corral
got a three-model panel's fifteen permission-rail fixes, Light got the
ambient-credential strip. Nine days later, five of the rail contract's ten
tests still failed here, including an agent being able to rename the request a
human was answering (principle 17). Nobody was careless; there was simply no
seam that made "we fixed it" mean "the product our users run is fixed".

`import acp` gives you the shared module OBJECT — not a copy of its namespace.
That distinction is load-bearing: a copy looks identical until a test sets
`acp.STALL_NOTICE_S = 0.1` and the running code, reading its own module
global, never sees it. Then the suite passes while proving nothing.

The contract is `corral_core/test_acp_rail.py`, which this tree's suite runs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corral_core import acp as _core          # noqa: E402

sys.modules[__name__] = _core
