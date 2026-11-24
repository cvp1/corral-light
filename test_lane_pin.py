"""The Gemini lane is pinned by intent, so a renamed model id can't downgrade it.

2026-10-29: Antigravity dropped `gemini-pro-agent` around 2026-10-16. Every
Gemini panel pane afterwards ran the vendor default (gemini-4-argon-medium)
for two weeks, and only a pane-log note said so. The lane now resolves
"Pro-class at the highest effort" against whatever the server offers, and
refuses to start rather than run a weaker tier.
"""
import unittest
import unittest.mock

from testkit.scratch import default_state  # noqa: F401  (binds STATE before sessions)

import acp
import sessions

INTENT = sessions.AGENTS["gemini"]["model_intent"]
# The catalog the live server offered on 2026-11-21 (Neon added).
TODAY = ["gemini-4-neon-high", "gemini-4-neon-xhigh", "gemini-4-neon-medium",
         "gemini-4-neon-low", "gemini-4-argon-medium", "gemini-4-argon-high",
         "gemini-4-argon-low", "gemini-4-hydrogen-high", "gemini-4-hydrogen-xhigh",
         "gemini-4-hydrogen-medium", "gemini-4-hydrogen-low", "gemini-3.8-flash-high",
         "gemini-3.8-flash-medium", "gemini-3.8-flash-low"]
BEFORE_NEON = ["gemini-3.8-flash-high", "gemini-3.8-flash-low", "gemini-3.8-flash-medium",
               "gemini-4-argon-high", "gemini-4-argon-low", "gemini-4-argon-medium"]


class TheResolverPicksTheIntendedTier(unittest.TestCase):
    def test_todays_catalog_resolves_to_neon_high(self):
        self.assertEqual(sessions.resolve_model_intent(TODAY, INTENT),
                         "gemini-4-neon-high")

    def test_neon_withdrawn_falls_back_to_argon_high(self):
        self.assertEqual(sessions.resolve_model_intent(BEFORE_NEON, INTENT),
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
        offered = ["gemini-4.1-argon-high", "gemini-4-neon-high"]
        self.assertEqual(sessions.resolve_model_intent(offered, INTENT),
                         "gemini-4-neon-high")

    def test_flash_is_never_picked(self):
        offered = ["gemini-3.8-flash-high", "gemini-9-flash-lite-high"]
        self.assertIsNone(sessions.resolve_model_intent(offered, INTENT))

    def test_ids_without_an_effort_suffix_are_not_guessed_at(self):
        self.assertIsNone(sessions.resolve_model_intent(["gemini-pro-agent"], INTENT))

    def test_nothing_offered_is_none(self):
        self.assertIsNone(sessions.resolve_model_intent([], INTENT))


class _Vendor:
    """An ACP-shaped Antigravity: set_config replies with configOptions the way the
    live server does (verified 2026-11-21), and the pane parses them with its REAL
    _absorb_config. `ignore` = config ids whose setter is acked but not applied;
    `reply_mode` overrides what a reply reports for mode ("omit" drops it)."""
    MODES = ("default", "auto_edit", "yolo")

    def __init__(self, offered, model, mode="default", modes=MODES, ignore=(),
                 reply_mode=None):
        self.offered, self.modes = list(offered), list(modes)
        self.state = {"model": model, "mode": mode}
        self.ignore, self.reply_mode = set(ignore), reply_mode
        self.sets = []

    def options(self):
        mode = self.state["mode"] if self.reply_mode is None else self.reply_mode
        out = [{"id": "model", "name": "Model", "currentValue": self.state["model"],
                "options": [{"value": v, "name": v} for v in self.offered]}]
        if mode != "omit":
            out.append({"id": "mode", "name": "Mode", "currentValue": mode,
                        "options": [{"value": v, "name": v} for v in self.modes]})
        return out

    def set_config(self, sid, cid, v):
        self.sets.append((cid, v))
        if cid not in self.ignore:
            self.state[cid] = v
        return {"configOptions": self.options()}


def _pane(offered, current, want=None, echo=None, **vendor):
    vendor.setdefault("ignore", ("model",) if echo else ())
    v = _Vendor(offered, current, **vendor)
    p = sessions.Pane.__new__(sessions.Pane)
    p.agent = "gemini"
    p.want_model = want
    p.acp_session = "s"
    p.config = {}
    p.mgr = type("M", (), {"remember_catalog": staticmethod(lambda *a, **k: None)})()
    p.notes = []
    p.emit = lambda kind, data, **k: p.notes.append(data.get("text", ""))
    p.client = v
    p._absorb_config(v.options())          # what session/new reported
    p.sets = v.sets
    p.vendor = v
    return p


class ThePaneLandsOnTheIntendedModel(unittest.TestCase):
    def test_the_vendor_default_is_replaced(self):
        p = _pane(TODAY, "gemini-4-argon-medium")
        p._apply_lane_defaults()
        self.assertEqual(p.config["model"]["value"], "gemini-4-neon-high")
        self.assertIn(("model", "gemini-4-neon-high"), p.sets)

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
        p = _pane(TODAY, "gemini-4-argon-medium", echo=True)
        with self.assertRaises(acp.AgentError):
            p._apply_lane_defaults()

    def test_an_explicit_pane_model_is_left_to_the_pane(self):
        p = _pane(["gemini-3.8-flash-high"], "gemini-3.8-flash-high",
                  want="gemini-3.8-flash-high")
        p._apply_lane_defaults()                 # no refusal, no model switch
        self.assertEqual([c for c in p.sets if c[0] == "model"], [])


class TheLaneNeverRunsInYolo(unittest.TestCase):
    """2026-11-21: the lane default was mode=yolo, so Antigravity asked for no
    permission and Corral showed no card, whatever the pane's posture."""

    def test_the_lane_default_is_default_and_yolo_is_forbidden(self):
        spec = sessions.AGENTS["gemini"]
        self.assertEqual(spec["default_config"]["mode"], "default")
        self.assertIn("yolo", spec["forbidden_modes"])

    def test_a_session_in_yolo_is_moved_to_default_from_the_reply(self):
        p = _pane(TODAY, "gemini-4-neon-high", mode="yolo")
        p._apply_lane_defaults()
        self.assertEqual(p.vendor.state["mode"], "default")
        self.assertEqual(p.config["mode"]["value"], "default")
        self.assertTrue(any("mode = default (lane default, confirmed" in n for n in p.notes))

    def test_the_setter_is_sent_even_when_the_cache_says_default(self):
        """A cached value can be a previous attachment's: always ask, read the reply."""
        p = _pane(TODAY, "gemini-4-neon-high", mode="default")
        p._apply_lane_defaults()
        self.assertIn(("mode", "default"), p.sets)

    def test_a_reply_still_reporting_yolo_refuses_the_pane(self):
        p = _pane(TODAY, "gemini-4-neon-high", mode="yolo", ignore=("mode",))
        with self.assertRaises(acp.AgentError) as cm:
            p._apply_lane_defaults()
        self.assertIn("reports 'yolo'", str(cm.exception))

    def test_a_reply_without_a_mode_refuses_the_pane(self):
        p = _pane(TODAY, "gemini-4-neon-high", mode="default")
        p.vendor.reply_mode = "omit"
        with self.assertRaises(acp.AgentError) as cm:
            p._apply_lane_defaults()
        self.assertIn("reports None", str(cm.exception))

    def test_a_null_mode_refuses_the_pane(self):
        p = _pane(TODAY, "gemini-4-neon-high", mode="default")
        p.vendor.reply_mode = None
        p.vendor.state["mode"] = None          # the reply carries currentValue: null
        p.vendor.ignore.add("mode")
        with self.assertRaises(acp.AgentError):
            p._apply_lane_defaults()

    def test_default_not_offered_refuses_the_pane(self):
        p = _pane(TODAY, "gemini-4-neon-high", mode="yolo", modes=("yolo",))
        with self.assertRaises(acp.AgentError) as cm:
            p._apply_lane_defaults()
        self.assertIn("does not offer permission mode 'default'", str(cm.exception))
        self.assertEqual(p.sets, [])

    def test_a_setter_error_refuses_the_pane(self):
        p = _pane(TODAY, "gemini-4-neon-high", mode="yolo")

        def boom(*a):
            raise acp.AgentError("internal error")
        p.client = type("C", (), {"set_config": staticmethod(boom)})()
        with self.assertRaises(acp.AgentError) as cm:
            p._apply_lane_defaults()
        self.assertIn("could not set", str(cm.exception))


class LaterSwitchesAndKeepers(unittest.TestCase):
    def _live(self, agent="gemini"):
        p = _pane(TODAY, "gemini-4-neon-high", mode="default")
        p.agent = agent
        p.challenge_of = None
        p.review_at_end = False
        p.effort = None
        return p

    def test_switching_to_yolo_is_refused_before_it_reaches_the_vendor(self):
        p = self._live()
        with self.assertRaises(ValueError) as cm:
            p.set_config("mode", "yolo")
        self.assertIn("'yolo'", str(cm.exception))
        self.assertEqual(p.sets, [])

    def test_auto_edit_is_still_an_operator_choice(self):
        p = self._live()
        p.set_config("mode", "auto_edit")
        self.assertEqual(p.sets, [("mode", "auto_edit")])

    def test_a_lane_without_forbidden_modes_is_unaffected(self):
        p = self._live(agent="claude")
        self.assertNotIn("forbidden_modes", sessions.AGENTS["claude"])
        p.config["mode"]["options"].append({"value": "yolo", "name": "yolo", "description": ""})
        p.set_config("mode", "yolo")              # not Corral's to refuse on this lane
        self.assertEqual(p.sets, [("mode", "yolo")])

    def test_a_gemini_pane_gets_no_keeper(self):
        import tempfile
        from pathlib import Path
        p = sessions.Pane.__new__(sessions.Pane)
        p.id, p.dir = "abc123def456", Path(tempfile.mkdtemp())
        for agent, expect_keeper in (("gemini", False), ("claude", True)):
            p.agent = agent
            got = p._keeper_spec()
            if sessions.KEEPER_ENABLED:
                self.assertEqual(got is not None, expect_keeper, agent)
            else:
                self.assertIsNone(got)


class ThePostureIsTheLanesOwnMode(unittest.TestCase):
    """2026-11-23: POSTURE_MODE's "auto" is not one of Antigravity's modes, so
    every Gemini pane ran in its lane default with postureEnforced: false,
    whatever its posture. The lane now maps each posture to its own modes and
    reads the result back from the vendor's reply."""

    def _attach(self, posture, **vendor):
        p = _pane(TODAY, "gemini-4-neon-high", **vendor)
        p.posture = posture
        p._apply_lane_defaults()                   # mode=default, verified first
        return p, p._apply_posture()

    def test_auto_is_auto_edit_read_back(self):
        p, enforced = self._attach("auto")
        self.assertTrue(enforced)
        self.assertEqual(p.vendor.state["mode"], "auto_edit")
        self.assertEqual(p.sets[-1], ("mode", "auto_edit"))
        self.assertTrue(any("posture auto on Antigravity (Gemini): mode auto_edit" in n for n in p.notes))

    def test_strict_stays_default(self):
        p, enforced = self._attach("strict")
        self.assertTrue(enforced)
        self.assertEqual(p.vendor.state["mode"], "default")

    def test_edits_is_auto_edit(self):
        p, enforced = self._attach("edits")
        self.assertTrue(enforced)
        self.assertEqual(p.vendor.state["mode"], "auto_edit")

    def test_the_lane_reports_its_posture_enforceable(self):
        self.assertTrue(sessions.posture_enforceable(sessions.AGENTS["gemini"]))

    def test_a_vendor_without_auto_edit_is_not_enforced(self):
        p, enforced = self._attach("auto", modes=("default", "yolo"))
        self.assertFalse(enforced)
        self.assertEqual(p.vendor.state["mode"], "default")
        self.assertTrue(any("does not offer permission mode 'auto_edit'" in n for n in p.notes))

    def test_an_ack_that_does_not_take_is_not_enforced(self):
        p = _pane(TODAY, "gemini-4-neon-high")
        p.posture = "auto"
        p._apply_lane_defaults()
        p.vendor.ignore.add("mode")                # acked, not applied
        self.assertFalse(p._apply_posture())
        self.assertEqual(p.vendor.state["mode"], "default")
        self.assertTrue(any("it reports 'default'" in n for n in p.notes))

    def test_a_posture_map_never_reaches_a_forbidden_mode(self):
        spec = sessions.AGENTS["gemini"]
        with unittest.mock.patch.dict(spec, {"posture_modes": {"auto": "yolo"}}):
            p, enforced = self._attach("auto")
        self.assertFalse(enforced)
        self.assertNotIn(("mode", "yolo"), p.sets)
        self.assertEqual(p.vendor.state["mode"], "default")


if __name__ == "__main__":
    unittest.main()
