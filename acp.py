#!/usr/bin/python3
"""acp — alias for `corral_core/acp.py`, the shared ACP client.

Replaces this module with the core module OBJECT (not a namespace copy), so
tests that patch module globals affect the running code.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corral_core import acp as _core          # noqa: E402

sys.modules[__name__] = _core
