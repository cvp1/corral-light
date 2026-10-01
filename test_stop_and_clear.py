#!/usr/bin/python3
"""Stop and /clear, against a REAL agent process (testkit/fake_acp_agent.py).

Stop: the composer's ■ Stop (and Esc) interrupts the running turn AND drops
what was queued behind it; a stop that only cancelled the turn in flight let
the next queued message start the moment it landed.

/clear: Corral owns it on every lane. The pane gets a fresh agent process and
a fresh session/new; the old conversation must not come back, including
through a later pause/resume (session/load of the pre-clear session id was
the half-real reset this replaces).

Collected by test_corral_light.py, so `python3 test_corral_light.py` runs it.
"""
import os
import signal
import unittest
from pathlib import Path

from test_resilience import FakeLaneCase, wait_for

ROOT = Path(__file__).resolve().parent


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class StopMeansStop(FakeLaneCase):

    def test_stop_interrupts_the_turn_and_drops_the_queue(self):
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        p.send("sleep 30")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        p.send("first queued")
        p.send("second queued")
        self.assertTrue(p.cancel())
        self.assertTrue(wait_for(lambda: self.turn_ends(p) == 1))
        self.assertTrue(wait_for(lambda: p.state == "ready"))
        # Give a wrongly-surviving queue the chance to run, then prove it did not.
        self.assertFalse(wait_for(lambda: "echo:" in self.texts(p), timeout=1))
        notes = [e["data"]["text"] for e in p.events if e["kind"] == "note"]
        self.assertTrue(any("2 queued" in n for n in notes), notes)
        states = {t: r["state"] for t, r in p._turns().turns().items()}
        self.assertEqual(list(states.values()).count("interrupted"), 2, states)
        # And the pane still works afterwards.
        p.send("hello")
        self.assertTrue(wait_for(lambda: "echo: hello" in self.texts(p)))

    def test_stop_on_an_idle_pane_is_harmless(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.cancel()
        p.send("hello")
        self.assertTrue(wait_for(lambda: "echo: hello" in self.texts(p)))

    def test_the_composer_has_a_stop_button(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn("c.append(ac, ta, b, stopButton(p))", js)
        self.assertIn("'/api/session/pause'", js)        # the force-stop escalation
        self.assertIn(".pane.working .composer .stop", css)


class ClearIsAFreshConversation(FakeLaneCase):

    def _after_clear(self, p):
        i = max(i for i, e in enumerate(p.events) if e["kind"] == "cleared")
        return "".join((e.get("data") or {}).get("text", "")
                       for e in p.events[i:] if e["kind"] == "text")

    def test_clear_forgets_and_restarts_the_agent(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.send("remember apple")
        self.assertTrue(wait_for(lambda: self.turn_ends(p) == 1))
        old_sid, old_pid = p.acp_session, p.client.p.pid
        tid = p.send("/clear")
        self.assertEqual(p.state, "ready", p.error)
        self.assertNotEqual(p.acp_session, old_sid)
        self.assertTrue(wait_for(lambda: not _alive(old_pid)),
                        "the old agent process outlived /clear")
        self.assertIn("cleared", self.kinds(p))
        self.assertEqual(p._turns().turns()[tid]["state"], "completed")
        p.send("what word did I ask you to remember?")
        self.assertTrue(wait_for(lambda: "remember any word" in self._after_clear(p)))
        self.assertNotIn("apple", self._after_clear(p))

    def test_clear_survives_pause_and_resume(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.send("remember apple")
        self.assertTrue(wait_for(lambda: self.turn_ends(p) == 1))
        p.send("/clear")
        p.pause()
        self.mgr.resume(p.id)
        self.assertEqual(p.state, "ready", p.error)
        p.send("what word did I ask you to remember?")
        self.assertTrue(wait_for(lambda: "remember any word" in self._after_clear(p)))
        self.assertNotIn("apple", self._after_clear(p))

    def test_clear_on_a_busy_pane_interrupts_and_drops_the_queue(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.send("sleep 30")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        p.send("queued before the clear")
        p.send("/clear")
        self.assertEqual(p.state, "ready", p.error)
        self.assertFalse(wait_for(lambda: "echo: queued" in self.texts(p), timeout=1))
        states = [r["state"] for r in p._turns().turns().values()]
        self.assertNotIn("dispatched", states)
        self.assertNotIn("accepted", states)
        p.send("hello")
        self.assertTrue(wait_for(lambda: "echo: hello" in self.texts(p)))

    def test_clear_on_a_paused_pane_starts_fresh_without_loading(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.send("remember apple")
        self.assertTrue(wait_for(lambda: self.turn_ends(p) == 1))
        p.pause()
        p.send("/clear")
        self.assertEqual(p.state, "ready", p.error)
        self.assertNotIn("resumed", self.kinds(p))
        self.assertNotIn("REPLAYED HISTORY", self.texts(p))

    def test_clear_is_not_forwarded_to_the_agent(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.send("/clear")
        self.assertFalse(wait_for(lambda: "echo: /clear" in self.texts(p), timeout=1))


if __name__ == "__main__":
    unittest.main()
