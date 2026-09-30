"""ask_human -- an agent's question for its human -- as ONE set of cases both
products run against their own skin's real Manager, send() and drain: full
Corral's `test_peer.AskHuman` and Light's `test_corral_light.LightAskHuman`.

Mixed into a TestCase that provides `self.mgr` (a Manager with a `fake` lane
registered, testkit/fake_acp_agent.py) and `self.agent_dir`. The route is
driven through `peer_http` with a minted pane token -- the exact call each
hub's pre-cookie branch makes.

    A1 the token's pane gets the question: needs-you, the `question` event,
       the snapshot's `question`; a `pane` in the body is ignored
    A2 unknown token 401; empty / non-string / over MAX_ASK_CHARS refused and
       nothing stored (never clipped)
    A3 a second ask replaces the first: one question, the event says so
    A4 a human send() clears it (and says so in the transcript)
    A5 a peer delivery and a `via=rig` send do NOT clear it
    A6 the agent dying clears it; closing clears it
    A7 it survives in meta.json and comes back through from_meta
    A8 turn origin: a turn a peer or a rig started ends `idle`, a human's
       ends `your-turn`
"""
import json
import os
import signal
import time

import sessions

core = sessions._core
WAIT_S = 15


def wait_for(pred, timeout=WAIT_S, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


class AskCases:

    # ── helpers ──────────────────────────────────────────────────────────
    def _pane(self, seat=None):
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        if seat:
            self.mgr.bind_seat(p.id, seat)
        return p

    def _ask(self, who, question, **extra):
        tok = self.mgr.mint_pane_token(who)
        return self.mgr.peer_http("POST", "/api/peer/ask", tok,
                                  dict({"question": question}, **extra))

    @staticmethod
    def _evs(p, kind):
        return [e["data"] for e in p.events if e["kind"] == kind]

    def _ends(self, p):
        return len(self._evs(p, "turn_end"))

    @staticmethod
    def _display(p):
        return p.snapshot(since=1 << 60)["display"]

    # ── A1 ───────────────────────────────────────────────────────────────
    def test_A1_the_token_s_pane_asks_and_the_wall_reads_needs_you(self):
        a, b = self._pane(), self._pane()
        self.assertEqual(self._display(a), "your-turn")
        status, out = self._ask(a, "  Do you want me to re-scope?  ", pane=b.id)
        self.assertEqual((status, out["result"], out["replaced"]),
                         (200, "raised", False), out)
        self.assertEqual(a.question["text"], "Do you want me to re-scope?")
        self.assertIsNone(b.question, "a `pane` in the body aimed the question")
        snap = a.snapshot(since=1 << 60)
        self.assertEqual(snap["display"], "needs-you")
        self.assertEqual(snap["question"]["text"], "Do you want me to re-scope?")
        ev = self._evs(a, "question")
        self.assertEqual([e["text"] for e in ev], ["Do you want me to re-scope?"])
        self.assertNotIn("question", [e["kind"] for e in b.events])
        # The agent's words, never a `user` turn: nothing was sent to a model.
        self.assertNotIn("user", [e["kind"] for e in a.events])

    # ── A2 ───────────────────────────────────────────────────────────────
    def test_A2_unknown_token_and_bad_questions_are_refused_and_store_nothing(self):
        a = self._pane()
        status, _ = self.mgr.peer_http("POST", "/api/peer/ask", "nope",
                                       {"question": "q"})
        self.assertEqual(status, 401)
        for bad, reason in ((None, "empty"), (7, "empty"), ("   ", "empty"),
                            ("x" * (core.MAX_ASK_CHARS + 1), "too-long")):
            status, out = self._ask(a, bad)
            self.assertEqual((status, out["result"], out["reason"]),
                             (200, "refused", reason), bad)
        self.assertIsNone(a.question, "a refused ask was stored")
        self.assertEqual(self._evs(a, "question"), [])
        status, out = self._ask(a, "y" * core.MAX_ASK_CHARS)
        self.assertEqual(out["result"], "raised")
        self.assertEqual(len(a.question["text"]), core.MAX_ASK_CHARS)

    # ── A3 ───────────────────────────────────────────────────────────────
    def test_A3_a_second_ask_replaces_the_first(self):
        a = self._pane()
        self._ask(a, "first?")
        first_at = a.question["at"]
        _, out = self._ask(a, "second?")
        self.assertTrue(out["replaced"])
        self.assertEqual(a.question["text"], "second?")
        ev = self._evs(a, "question")
        self.assertEqual([e["text"] for e in ev], ["first?", "second?"])
        self.assertEqual(ev[1]["replaces"], first_at)

    # ── A4 ───────────────────────────────────────────────────────────────
    def test_A4_a_human_send_clears_it(self):
        a = self._pane()
        self._ask(a, "which branch?")
        a.send("use main")
        self.assertIsNone(a.question)
        cleared = self._evs(a, "question_cleared")
        self.assertEqual([c["reason"] for c in cleared], ["answered"])
        kinds = [e["kind"] for e in a.events]
        self.assertLess(kinds.index("user"), kinds.index("question_cleared"),
                        "cleared before the human's turn was recorded")
        self.assertTrue(wait_for(lambda: self._ends(a) == 1))
        self.assertEqual(self._display(a), "your-turn")

    # ── A5 ───────────────────────────────────────────────────────────────
    def test_A5_a_peer_delivery_does_not_clear_it(self):
        a, b = self._pane("asker"), self._pane("peer")
        self._ask(a, "may I delete the branch?")
        r = self.mgr.deliver_peer(b.id, "asker", "just do it")
        self.assertEqual(r["result"], "delivered", r)
        self.assertTrue(wait_for(lambda: self._ends(a) == 1))
        self.assertEqual(a.question["text"], "may I delete the branch?",
                         "another pane's agent answered the human's question")
        self.assertEqual(self._evs(a, "question_cleared"), [])
        self.assertEqual(self._display(a), "needs-you")

    def test_A5_a_rig_send_does_not_clear_it(self):
        a = self._pane()
        self._ask(a, "which rig?")
        a.send("opening prompt", via="rig")
        self.assertEqual(a.question["text"], "which rig?")
        self.assertTrue(wait_for(lambda: self._ends(a) == 1))
        self.assertEqual(self._display(a), "needs-you")

    # ── A6 ───────────────────────────────────────────────────────────────
    def test_A6_the_agent_dying_clears_it(self):
        a = self._pane()
        self._ask(a, "still there?")
        # Killed from outside: a `die` prompt would itself be a human turn,
        # which answers the question before the death could clear it.
        os.kill(a.client.p.pid, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: a.state == "dead"), a.state)
        self.assertIsNone(a.question)
        self.assertEqual([c["reason"] for c in self._evs(a, "question_cleared")],
                         ["dead"])
        meta = json.loads((a.dir / "meta.json").read_text(encoding="utf-8"))
        self.assertIsNone(meta.get("question"))

    def test_A6_closing_clears_it(self):
        a = self._pane()
        self._ask(a, "close me?")
        self.mgr.close(a.id)
        self.assertIsNone(a.question)
        self.assertEqual([c["reason"] for c in self._evs(a, "question_cleared")],
                         ["closed"])

    # ── A7 ───────────────────────────────────────────────────────────────
    def test_A7_it_survives_a_restart_through_meta(self):
        a = self._pane()
        self._ask(a, "persist me?")
        meta = json.loads((a.dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["question"]["text"], "persist me?")
        back = type(a).from_meta(meta, self.mgr)
        self.assertEqual(back.question["text"], "persist me?")
        self.assertEqual(back.state, "detached")
        self.assertEqual(core.display_state(back)["state"], "needs-you",
                         "a restored, paused pane with an open question hid it")
        # A malformed value degrades toward the human, loudly -- never lost.
        back = type(a).from_meta(dict(meta, question="garbage"), self.mgr)
        self.assertIn("could not be read", back.question["text"])
        back = type(a).from_meta(dict(meta, question=None), self.mgr)
        self.assertIsNone(back.question)

    # ── A8 ───────────────────────────────────────────────────────────────
    def test_A8_a_turn_a_peer_started_ends_idle_not_your_turn(self):
        a, b = self._pane("sender"), self._pane("target")
        r = self.mgr.deliver_peer(a.id, "target", "hello")
        self.assertEqual(r["result"], "delivered", r)
        self.assertTrue(wait_for(lambda: self._ends(b) == 1))
        self.assertTrue(wait_for(lambda: b.state == "ready"))
        snap = b.snapshot(since=1 << 60)
        self.assertEqual((snap["display"], snap["turnVia"]), ("idle", "peer"))
        b.send("my own turn")
        self.assertTrue(wait_for(lambda: self._ends(b) == 2))
        self.assertTrue(wait_for(lambda: b.state == "ready"))
        snap = b.snapshot(since=1 << 60)
        self.assertEqual((snap["display"], snap["turnVia"]), ("your-turn", None))

    def test_A8_a_turn_a_rig_started_ends_idle(self):
        a = self._pane()
        a.send("the rig's opening prompt", via="rig")
        self.assertTrue(wait_for(lambda: self._ends(a) == 1))
        self.assertTrue(wait_for(lambda: a.state == "ready"))
        self.assertEqual(self._display(a), "idle")
