"""DESIGN-5 S11b: the reply queue's bounds are named constants, say what the
brief says, and are stated to the model and in the README.

The behaviour (queued / delivered / queue-full / expired / dropped / refused
at delivery / one lock) is driven through real agent processes on both skins'
drains by testkit/reply_queue_cases.py -- full Corral's test_peer.ReplyQueue
and Light's test_corral_light.LightReplyQueue.

    python3 -m unittest discover -s corral_core -p 'test_*.py'
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from corral_core import seat_mcp                              # noqa: E402
from corral_core import sessions as core                      # noqa: E402


class TheBoundsAreNamedAndStated(unittest.TestCase):
    def test_depth_one(self):
        self.assertEqual(core.PEER_QUEUE_MAX, 1)

    def test_age_is_the_longest_a_wait_can_be(self):
        self.assertEqual(core.PEER_QUEUE_TTL_S, seat_mcp.PEER_WAIT_MAX_S)
        self.assertEqual(core.PEER_QUEUE_TTL_S, 600)

    def test_a_wait_counts_as_in_flight_only_while_it_polls(self):
        self.assertGreater(core.PEER_WAIT_SEEN_S, seat_mcp.PEER_WAIT_POLL_S)
        self.assertLess(core.PEER_WAIT_SEEN_S, seat_mcp.PEER_WAIT_S)

    def test_the_model_is_told_queued_is_not_delivered(self):
        send = next(t for t in seat_mcp.TOOLS if t["name"] == "seat_send")["description"]
        self.assertIn("`queued`", send)
        self.assertIn("NOT been delivered", send)
        wait = next(t for t in seat_mcp.TOOLS if t["name"] == "seat_wait")["description"]
        self.assertIn("NEXT turn", wait)
        self.assertIn("Anyone else is refused `busy`", wait)

    def test_the_readme_states_every_bound(self):
        text = (HERE.parent / "README.md").read_text(encoding="utf-8")
        for needle in ("`PEER_QUEUE_MAX`", "`queue-full`", "`PEER_QUEUE_TTL_S`",
                       "`expired`", "**memory only**", "same size and envelope checks"):
            self.assertIn(needle, text, needle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
