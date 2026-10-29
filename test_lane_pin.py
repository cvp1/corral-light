"""The Gemini lane is pinned by intent, so a renamed model id can't downgrade it.

2026-10-29: Antigravity dropped `gemini-pro-agent` around 2026-10-16. Every
Gemini panel pane afterwards ran the vendor default (gemini-4-argon-medium)
for two weeks, and only a pane-log note said so. The lane now resolves
"Pro-class at the highest effort" against whatever the server offers, and
refuses to start rather than run a weaker tier.
"""
import unittest

from testkit.scratch import default_state  # noqa: F401  (binds STATE before sessions)

import acp
import sessions

INTENT = sessions.AGENTS["gemini"]["model_intent"]
TODAY = ["gemini-3.8-flash-high", "gemini-3.8-flash-low", "gemini-3.8-flash-medium",
         "gemini-4-argon-high", "gemini-4-argon-low", "gemini-4-argon-medium"]


class TheResolverPicksTheIntendedTier(unittest.TestCase):
    def test_todays_catalog_resolves_to_argon_high(self):
        self.assertEqual(sessions.resolve_model_intent(TODAY, INTENT),
                         "gemini-4-argon-high")

    def test_a_rename_still_lands_on_the_pro_tier_at_high(self):
        """The exact 2026-10-16 failure, one generation on."""
        renamed = ["gemini-4.5-flash-high", "gemini-4.5-flash-medium",
                   "gemini-5-zinc-low", "gemini-5-zinc-medium", "gemini-5-zinc-high"]
        self.assertEqual(sessions.resolve_model_intent(renamed, INTENT),
                         "gemini-5-zinc-high")

    def test_high_effort_beats_a_newer_version(self):
        offered = ["gemini-4-argon-high", "gemini-5-zinc-medium"]
        self.assertEqual(sessions.resolve_model_intent(offered, INTENT),
                         "gemini-4-argon-high")

    def test_the_newest_wins_at_equal_effort(self):
        offered = ["gemini-4-zinc-high", "gemini-4.1-zinc-high"]
        self.assertEqual(sessions.resolve_model_intent(offered, INTENT),
                         "gemini-4.1-zinc-high")

    def test_the_preferred_id_wins_while_it_is_offered(self):
        offered = ["gemini-4.1-argon-high", "gemini-4-argon-high"]
        self.assertEqual(sessions.resolve_model_intent(offered, INTENT),
                         "gemini-4-argon-high")

    def test_flash_is_never_picked(self):
        offered = ["gemini-3.8-flash-high", "gemini-9-flash-lite-high"]
        self.assertIsNone(sessions.resolve_model_intent(offered, INTENT))

    def test_ids_without_an_effort_suffix_are_not_guessed_at(self):
        self.assertIsNone(sessions.resolve_model_intent(["gemini-pro-agent"], INTENT))

    def test_nothing_offered_is_none(self):
        self.assertIsNone(sessions.resolve_model_intent([], INTENT))


def _pane(offered, current, want=None, echo=None):
    p = sessions.Pane.__new__(sessions.Pane)
    p.agent = "gemini"
    p.want_model = want
    p.acp_session = "s"
    p.config = {"model": {"value": current, "realId": "model",
                          "options": [{"value": v} for v in offered]},
                "mode": {"value": "yolo", "realId": "mode",
                         "options": [{"value": "yolo"}]}}
    p.notes, p.sets = [], []
    p.emit = lambda kind, data, **k: p.notes.append(data.get("text", ""))

    def set_config(sid, cid, v):
        p.sets.append((cid, v))
        p.config["model"]["value"] = echo or v
        return {}
    p.client = type("C", (), {"set_config": staticmethod(set_config)})()
    p._absorb_config = lambda opts: None
    return p


class ThePaneLandsOnTheIntendedModel(unittest.TestCase):
    def test_the_vendor_default_is_replaced(self):
        p = _pane(TODAY, "gemini-4-argon-medium")
        p._apply_lane_defaults()
        self.assertEqual(p.config["model"]["value"], "gemini-4-argon-high")
        self.assertIn(("model", "gemini-4-argon-high"), p.sets)

    def test_a_resolved_rename_says_so_in_the_pane(self):
        p = _pane(["gemini-5-zinc-high", "gemini-5-zinc-medium"], "gemini-5-zinc-medium")
        p._apply_lane_defaults()
        self.assertEqual(p.config["model"]["value"], "gemini-5-zinc-high")
        self.assertTrue(any("resolved from intent" in n for n in p.notes))

    def test_no_pro_class_model_refuses_the_pane(self):
        p = _pane(["gemini-3.8-flash-high"], "gemini-3.8-flash-high")
        with self.assertRaises(acp.AgentError) as cm:
            p._apply_lane_defaults()
        self.assertIn("no model matching the lane intent", str(cm.exception))

    def test_an_agent_that_ignores_the_switch_refuses_the_pane(self):
        p = _pane(TODAY, "gemini-4-argon-medium", echo="gemini-4-argon-medium")
        with self.assertRaises(acp.AgentError):
            p._apply_lane_defaults()

    def test_an_explicit_pane_model_is_left_to_the_pane(self):
        p = _pane(["gemini-3.8-flash-high"], "gemini-3.8-flash-high",
                  want="gemini-3.8-flash-high")
        p._apply_lane_defaults()                 # no refusal, no switch
        self.assertEqual(p.sets, [])


if __name__ == "__main__":
    unittest.main()
