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


class NextUpIsWithdrawable(FakeLaneCase):
    """Next up: messages typed ahead of a running turn are listed in order and
    can be taken back before they start, never after; a withdrawn one is never
    sent, and the ledger closes it `interrupted`."""

    def _queue_events(self, p):
        return [e["data"]["items"] for e in p.events if e["kind"] == "queue"]

    def test_a_withdrawn_message_never_runs_and_the_next_one_does(self):
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        p.send("sleep 2")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        t1 = p.send("first queued")
        p.send("second queued")
        self.assertEqual([i["text"] for i in p.queued_view()],
                         ["first queued", "second queued"], "Next up is not in run order")
        self.assertEqual([i["text"] for i in p.snapshot()["queued"]],
                         ["first queued", "second queued"], "the snapshot does not carry Next up")
        self.assertEqual(p.withdraw(t1), "first queued", "withdraw must return what was typed")
        self.assertEqual([i["text"] for i in p.queued_view()], ["second queued"])
        self.assertTrue(wait_for(lambda: "echo: second queued" in self.texts(p), timeout=15))
        self.assertNotIn("echo: first queued", self.texts(p), "a withdrawn message ran")
        rec = p._turns().turns()[t1]
        self.assertEqual((rec["state"], rec.get("why")),
                         ("interrupted", "withdrawn before it was sent"))
        self.assertIn(t1, [e["data"]["turn"] for e in p.events if e["kind"] == "withdrawn"])
        self.assertTrue(wait_for(lambda: self._queue_events(p)[-1:] == [[]]),
                        "the last Next up event does not say the list is empty")

    def test_a_started_message_cannot_be_withdrawn(self):
        p = self.mgr.create("fake", self.agent_dir)
        tid = p.send("hello")
        self.assertTrue(wait_for(lambda: "echo: hello" in self.texts(p)))
        with self.assertRaises(LookupError):
            p.withdraw(tid)
        with self.assertRaises(LookupError):
            p.withdraw("")

    def test_another_panes_message_is_listed_not_withdrawable(self):
        import sessions
        p = self.mgr.create("fake", self.agent_dir)
        p.send("sleep 30")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        item = sessions._QueuedText("from a peer", "t-peer")
        item.peer = True
        with p._turn_lock:
            p._queue.append(item)
        self.assertEqual(p.queued_view(), [{"turn": "t-peer", "peer": True, "text": "from a peer"}])
        with self.assertRaises(ValueError):
            p.withdraw("t-peer")
        self.assertEqual(len(p.queued_view()), 1, "a refused withdraw changed the queue")
        p.cancel()

    def test_stop_says_the_list_is_empty(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.send("sleep 30")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        p.send("queued")
        p.cancel()
        self.assertEqual(self._queue_events(p)[-1], [])
        self.assertEqual(p.queued_view(), [])


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
