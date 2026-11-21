"""A vendor's error frame is a failed turn, never an answer.

2026-11-21: the native Antigravity server returned a 404 as plain agent text
with stopReason end_turn; consult reported `ok: true` and a panel would have
sealed it as the arm's answer (vendor_errors.py).
"""
import unittest
import unittest.mock
from pathlib import Path

from test_panel import _PanelBase, wait_for  # noqa: F401  (binds STATE first)

import consult
import vendor_errors

LIVE_404 = ("Agent execution error: Error 404, Message: Requested entity was not found., "
            "Status: NOT_FOUND, Details: []\nrequest failed (code 404): Requested entity "
            "was not found.: Error 404, Message: Requested entity was not found., "
            "Status: NOT_FOUND, Details: []\nrequest failed (code 404): Requested entity "
            "was not found.")


class TheDetector(unittest.TestCase):
    def test_the_live_404_is_a_vendor_error(self):
        why = vendor_errors.vendor_error(LIVE_404)
        self.assertTrue(why.startswith("vendor error: Agent execution error: Error 404"))
        self.assertNotIn("\n", why)

    def test_a_bare_request_failed_frame_is_a_vendor_error(self):
        self.assertTrue(vendor_errors.vendor_error("  request failed (code 503): unavailable"))

    def test_real_answers_are_not(self):
        for text in ("NEON-OK", "17 multiplied by 23 is 391.", "", None):
            self.assertIsNone(vendor_errors.vendor_error(text), text)

    def test_an_answer_that_discusses_the_error_is_not(self):
        text = ("The log shows 'Agent execution error: Error 404' because the model id "
                "was retired.\nrequest failed (code 404) is the client's echo.")
        self.assertIsNone(vendor_errors.vendor_error(text))


class ConsultOk(unittest.TestCase):
    def test_the_live_404_is_not_ok_and_says_why(self):
        rec = {"complete": True, "text": LIVE_404, "why": None}
        self.assertFalse(consult.answer_ok(rec))
        self.assertFalse(rec["ok"])
        self.assertIn("Error 404", rec["why"])

    def test_a_real_answer_is_ok(self):
        rec = {"complete": True, "text": "NEON-OK", "why": None}
        self.assertTrue(consult.answer_ok(rec))
        self.assertIsNone(rec["why"])

    def test_incomplete_or_empty_is_still_not_ok(self):
        self.assertFalse(consult.answer_ok({"complete": False, "text": "partial"}))
        self.assertFalse(consult.answer_ok({"complete": True, "text": ""}))


def _vendor_fails_on(lane, real):
    """turn_text that answers with the live 404 for one lane's panes."""
    def text(pane, tid):
        return LIVE_404 if pane.agent == lane else real(pane, tid)
    return text


class PanelRefusesVendorErrors(_PanelBase):
    def test_an_arm_whose_vendor_fails_is_failed_not_sealed(self):
        with unittest.mock.patch.object(self.panel, "turn_text",
                                        _vendor_fails_on("fake2", self.panel.turn_text)):
            pid = self._start()["id"]
            self.assertTrue(self._done(self.mgr.panels, pid))
        rec = self.mgr.panels.get(pid)
        arms = {a["lane"]: a["rounds"]["1"] for a in rec["arms"]}
        self.assertEqual(arms["fake"]["status"], "sealed", arms["fake"])
        self.assertEqual(arms["fake2"]["status"], "failed", arms["fake2"])
        self.assertIn("vendor error", arms["fake2"]["why"])
        self.assertFalse(arms["fake2"].get("file"), "the vendor error was sealed as an answer")
        self.assertFalse(list(Path(rec["out_dir"]).glob("r1-fake2.md")))

    def test_a_synthesizer_whose_vendor_fails_is_failed_not_sealed(self):
        with unittest.mock.patch.object(self.panel, "turn_text",
                                        _vendor_fails_on("fake4", self.panel.turn_text)):
            pid = self._start(author=self.mgr.create("fake3", str(self.work)).id)["id"]
            self.assertTrue(self._done(self.mgr.panels, pid))
        rec = self.mgr.panels.get(pid)
        syn = rec["synth"]
        self.assertEqual(syn["lane"], "fake4", syn)
        self.assertEqual(syn["status"], "failed", syn)
        self.assertIn("vendor error", syn["why"])
        self.assertFalse((Path(rec["out_dir"]) / "synthesis.md").exists())


if __name__ == "__main__":
    unittest.main()
