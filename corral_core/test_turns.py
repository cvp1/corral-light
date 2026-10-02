#!/usr/bin/env python3
"""Turn identity: last_answer() stops at peer events; shared id/queue/via pieces.

    python3 -m unittest discover -s corral_core -p 'test_*.py'
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corral_core import sessions as S                            # noqa: E402


def ring(*kinds_and_text):
    out, seq = [], 0
    for item in kinds_and_text:
        kind, text = item if isinstance(item, tuple) else (item, None)
        seq += 1
        out.append({"seq": seq, "kind": kind,
                    "data": {"text": text} if text is not None else {}})
    return out


def pane_with(events):
    p = S.PaneBase.__new__(S.PaneBase)
    p.events = events
    return p


class LastAnswerStopsAtAPeer(unittest.TestCase):
    def test_the_reply_to_a_peer_is_not_the_answer_to_the_human(self):
        """`user, text, turn_end, peer, text, turn_end` -> the second text only."""
        p = pane_with(ring("user", ("text", "answer to the human"), "turn_end",
                           "peer", ("text", "answer to the peer"), "turn_end"))
        self.assertEqual(p.last_answer(), ("answer to the peer", True))

    def test_a_peer_turn_still_running_is_incomplete(self):
        p = pane_with(ring("user", ("text", "a"), "turn_end",
                           "peer", ("text", "half an ans")))
        self.assertEqual(p.last_answer(), ("half an ans", False))

    def test_the_human_turn_still_reads_as_before(self):
        p = pane_with(ring("user", ("text", "one "), ("text", "two"), "turn_end"))
        self.assertEqual(p.last_answer(), ("one two", True))


class TheSharedPieces(unittest.TestCase):
    def test_an_id_is_twelve_hex_characters_like_lights_ledger(self):
        a, b = S.new_turn_id(), S.new_turn_id()
        self.assertRegex(a, r"^[0-9a-f]{12}$")
        self.assertNotEqual(a, b)

    def test_a_queued_item_is_still_a_string(self):
        """Queued items stay str so existing `_queue` readers keep working."""
        q = S.QueuedText("hello", "abc")
        self.assertIsInstance(q, str)
        self.assertEqual((q, q.turn), ("hello", "abc"))
        self.assertIsNone(getattr("plain", "turn", None))

    def test_via_is_bounded(self):
        for ok in (None, "", "consult", "cli"):
            self.assertIn(S.check_via(ok), (None, "consult", "cli"))
        for bad in ("human", "Consult", "consult ", "x" * 500, "browser"):
            with self.assertRaises(ValueError) as e:
                S.check_via(bad)
            self.assertIn("consult", str(e.exception))
            self.assertLess(len(str(e.exception)), 200,
                            "a refusal must not echo an unbounded value back")


if __name__ == "__main__":
    unittest.main(verbosity=2)
