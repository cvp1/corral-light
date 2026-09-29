#!/usr/bin/python3
"""Resilience tests — the kill paths from docs/RESILIENCE-REVIEW-2026-09-28.md.

Every test here drives a REAL agent process (testkit/fake_acp_agent.py) through
the real Pane/Manager code: spawned, killed with SIGKILL, resumed, interrupted.
A stub client cannot prove a kill path, because the thing under test is what
happens when a process the hub does not control goes away.

Collected by test_corral_light.py (`from test_resilience import *`), so the
one command `python3 test_corral_light.py` runs these too.
"""
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("CORRAL_LIGHT_STATE",
                      tempfile.mkdtemp(prefix="corral-light-test-"))

ROOT = Path(__file__).resolve().parent
FAKE = ROOT / "testkit" / "fake_acp_agent.py"
WAIT_S = 15          # generous for a loaded CI box; each wait ends on success


def wait_for(pred, timeout=WAIT_S, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


class FakeLaneCase(unittest.TestCase):
    """A Manager (not the hub's global one) with a `fake` lane registered."""

    def setUp(self):
        import sessions
        self.sessions = sessions
        self.agent_dir = tempfile.mkdtemp(prefix="fake-acp-")
        sessions.AGENTS["fake"] = {
            "label": "Fake", "argv": [sys.executable, str(FAKE)],
            "requires": (str(FAKE),), "posture_via_config_dir": False,
            "tools": True, "env": {"FAKE_ACP_DIR": self.agent_dir}}
        self.addCleanup(sessions.AGENTS.pop, "fake", None)
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes, m.subscribers, m.not_restored = {}, [], 0
        m._lock = threading.Lock()
        m.catalog = {}
        m.mcp = None
        self.mgr = m
        self.addCleanup(self._close_all)

    def _close_all(self):
        for pid in list(self.mgr.panes):
            try:
                self.mgr.close(pid)
            except Exception:                     # noqa: BLE001
                pass

    def kinds(self, pane):
        return [e["kind"] for e in pane.events]

    def texts(self, pane):
        return "".join((e.get("data") or {}).get("text", "")
                       for e in pane.events if e["kind"] == "text")

    def turn_ends(self, pane):
        return sum(1 for e in pane.events if e["kind"] == "turn_end")


class ResumeFromDead(FakeLaneCase):
    """P0-a' (Astra/Grok 2026-09-28): a pane whose agent died comes back."""

    def _kill_agent(self, pane):
        os.kill(pane.client.p.pid, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: pane.state == "dead"),
                        f"pane never noticed its agent died: {pane.state}")

    def test_kill9_then_type_resumes_and_the_stale_queue_is_not_sent(self):
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        p.send("remember apple")
        self.assertTrue(wait_for(lambda: self.turn_ends(p) == 1))
        self._kill_agent(p)
        # What `agent_exit` leaves behind when a process dies with type-ahead
        # waiting: the drain loop sees `dead` and returns, queue untouched.
        p._queue = ["stale type-ahead that must never run"]
        p.send("what word did I ask you to remember?")
        self.assertIn("resumed", self.kinds(p))
        resumed = next(e for e in p.events if e["kind"] == "resumed")
        self.assertEqual(resumed["data"]["from"], "dead")
        self.assertTrue(wait_for(lambda: self.turn_ends(p) == 2))
        self.assertIn("apple", self.texts(p))
        self.assertNotIn("stale type-ahead", self.texts(p),
                         "the dead attachment's queue was drained into the new process")
        notes = [e["data"]["text"] for e in p.events if e["kind"] == "note"]
        self.assertTrue(any("stale type-ahead" in n and "will not be sent" in n
                            for n in notes), notes)
        self.assertNotIn("REPLAYED HISTORY", self.texts(p),
                         "session/load replay leaked into the transcript")

    def test_explicit_resume_from_dead_and_the_old_exit_is_fenced(self):
        p = self.mgr.create("fake", self.agent_dir)
        old_bind = p._bind(p._generation)
        self._kill_agent(p)
        self.mgr.resume(p.id)
        self.assertEqual(p.state, "ready", p.error)
        self.assertEqual(self.kinds(p).count("resumed"), 1)
        # A late agent_exit from the dead attachment's reader must not flip
        # the resumed pane back to dead.
        old_bind["on_event"]("agent_exit", {"reason": "late", "closed": False})
        self.assertEqual(p.state, "ready")
        p.send("hello")
        self.assertTrue(wait_for(lambda: "echo: hello" in self.texts(p)))

    def test_resume_refuses_a_live_pane(self):
        p = self.mgr.create("fake", self.agent_dir)
        with self.assertRaises(ValueError):
            p.resume()

    def test_a_dead_pane_with_no_session_says_so(self):
        p = self.mgr.create("fake", self.agent_dir)
        self._kill_agent(p)
        p.acp_session = None
        with self.assertRaises(ValueError):
            p.send("hi")
        self.assertFalse(p.snapshot()["resumable"])

    def test_the_ui_offers_resume_on_a_dead_row(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("'↻'", js)
        self.assertIn("p.state === 'dead') return p.resumable ? 'live' : 'none'", js)


if __name__ == "__main__":
    unittest.main()
