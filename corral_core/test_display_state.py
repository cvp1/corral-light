#!/usr/bin/env python3
"""display_state(): matches the shared table and never raises.

`display_cases.json` is shared with the JavaScript mirrors; every value in
DISPLAY_STATES must be reachable from it. Any pane-like object must classify.

    python3 -m unittest discover -s corral_core -p 'test_*.py'
"""
import json
import time
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corral_core import sessions as S                            # noqa: E402

CASES = json.loads((Path(__file__).resolve().parent / "display_cases.json")
                   .read_text(encoding="utf-8"))


class _Pane:
    """Exactly the attributes a case names, and no others."""

    def __init__(self, spec):
        for k, v in spec.items():
            setattr(self, "_gate_hold" if k == "gate_hold" else k, v)


class TheSharedTable(unittest.TestCase):
    def test_every_case_in_the_shared_table(self):
        self.assertGreaterEqual(len(CASES["cases"]), 15,
                                "the table is the contract; do not shrink it")
        for c in CASES["cases"]:
            got = S.display_state(_Pane(c["pane"]))
            self.assertEqual(got["state"], c["want"],
                             f'{c["why"]}: {c["pane"]} '
                             f'-> {got["state"]}, wanted {c["want"]}')

    def test_every_answer_is_in_the_declared_enum(self):
        for c in CASES["cases"]:
            got = S.display_state(_Pane(c["pane"]))
            self.assertIn(got["state"], S.DISPLAY_STATES)

    def test_every_declared_state_is_reachable_from_the_table(self):
        """Every DISPLAY_STATES value is produced by some case."""
        seen = {S.display_state(_Pane(c["pane"]))["state"] for c in CASES["cases"]}
        self.assertEqual(seen, set(S.DISPLAY_STATES))

    def test_the_threshold_is_a_named_constant(self):
        self.assertEqual(S.IDLE_DISPLAY_S, 1800)


class ItNeverRaises(unittest.TestCase):
    def test_an_object_with_nothing_on_it_still_classifies(self):
        class Bare:
            pass
        got = S.display_state(Bare())
        self.assertEqual(got["state"], "working",
                         "no state at all reads as `starting`, which is working")
        self.assertEqual(got["since_s"], 0)

    def test_a_light_pane_has_no_gate_hold_and_that_is_not_an_error(self):
        class LightPane:
            state = "ready"
            pending = {}
            last_activity = time.time()
        self.assertEqual(S.display_state(LightPane())["state"], "your-turn")

    def test_garbage_in_the_clock_does_not_raise(self):
        for bad in ("soon", None, object()):
            class P:
                state = "ready"
                pending = ()
            P.idle_s = bad
            self.assertIn(S.display_state(P())["state"], S.DISPLAY_STATES)

    def test_a_last_activity_that_is_not_a_number_does_not_raise(self):
        class P:
            state = "ready"
            pending = ()
            last_activity = "yesterday"
        self.assertEqual(S.display_state(P())["state"], "your-turn",
                         "an unreadable clock reads as 0s, i.e. just now")


class WhatItReports(unittest.TestCase):
    def test_since_s_comes_from_last_activity_when_there_is_no_idle_s(self):
        class P:
            state = "busy"
            pending = ()
            last_activity = 1000.0
        self.assertEqual(S.display_state(P(), now=1042.0)["since_s"], 42)

    def test_since_s_prefers_the_wires_own_idle_s(self):
        """`idle_s` from the hub wins over a local-clock recomputation."""
        class P:
            state = "busy"
            pending = ()
            idle_s = 7
            last_activity = 0.0
        self.assertEqual(S.display_state(P(), now=9e9)["since_s"], 7)

    def test_there_is_no_read_receipt_in_the_core(self):
        """The core projection neither takes nor reports an `unread` flag."""
        import inspect
        self.assertNotIn("unread", inspect.signature(S.display_state).parameters)

        class P:
            state = "ready"
            pending = ()
            idle_s = 0
        self.assertEqual(set(S.display_state(P())), {"state", "since_s"})

    def test_a_detached_pane_is_paused_not_idle(self):
        """A detached pane needs a human to resume it, so it shows paused."""
        class P:
            state = "detached"
            pending = ()
            idle_s = 5
        self.assertEqual(S.display_state(P())["state"], "paused")

    def test_a_state_override_wins_over_the_panes_own_field(self):
        """`snapshot()` corrects a pane whose process has exited to `dead`
        without writing that back; the projection must see the correction."""
        class P:
            state = "ready"
            pending = ()
            idle_s = 0
        self.assertEqual(S.display_state(P(), state="dead")["state"], "dead")


if __name__ == "__main__":
    unittest.main(verbosity=2)
