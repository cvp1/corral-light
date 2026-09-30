"""The hop limit raises itself to the human -- as ONE set of cases both
products run against their own skin's real Manager and drain: full Corral's
`test_peer.HopPause` and Light's `test_corral_light.LightHopPause`.

Measured 2026-09-30: in a rig review loop @reviewer's PASS was refused
`peer-chain` (the fifth message with no human turn), @author's seat_wait
returned `reached`, both panes read as ordinary finished turns, and the loop
sat stalled until a human noticed by eye. Now the refusal opens a
HUB-originated question (`source: hop-limit`) on the SENDING pane: needs-you
everywhere, cleared by the next human turn exactly like ask_human's.

    H1 a direct send over the limit raises it on the sender, not the target
    H2 a broadcast over the limit raises it
    H3 the reply-queue drain over the limit raises it on the replier
    H4 busy, unknown seat, transfer gate: no question
    H5 an agent's own open question is never overwritten; the refusal is
       still recorded
    H6 a human turn on the sender clears it, and the chain restarts for BOTH
       panes: the next sends are admitted, from hop 1

Mixed into a TestCase with `self.mgr` (a `fake` lane registered) and
`self.agent_dir`.
"""
from unittest import mock

import sessions
from reply_queue_cases import ReplyQueueCases, wait_for

core = sessions._core
SRC = "hop-limit"      # core.HOP_PAUSE_SOURCE; spelled out so a rename fails here


class HopPauseCases:

    # Borrowed, not inherited: mixing in ReplyQueueCases would run its tests
    # a second time under this class's name.
    waiting_pair = ReplyQueueCases.waiting_pair
    _turn = staticmethod(ReplyQueueCases._turn)
    _no_nesting = ReplyQueueCases._no_nesting
    _q = staticmethod(ReplyQueueCases._q)

    # ── helpers ──────────────────────────────────────────────────────────
    def _pair(self):
        a = self.mgr.create("fake", self.agent_dir)
        b = self.mgr.create("fake", self.agent_dir)
        self.assertEqual((a.state, b.state), ("ready", "ready"), (a.error, b.error))
        self.mgr.bind_seat(a.id, "author")
        self.mgr.bind_seat(b.id, "reviewer")
        return a, b

    @staticmethod
    def _ends(p):
        return sum(1 for e in p.events if e["kind"] == "turn_end")

    def _send(self, src, seat, dst, text="hi"):
        n = self._ends(dst)
        r = self.mgr.deliver_peer(src.id, seat, text)
        self.assertEqual(r["result"], "delivered", r)
        self.assertTrue(wait_for(lambda: self._ends(dst) == n + 1
                                 and dst.state == "ready"), dst.state)
        return r

    def _to_the_limit(self, a, b):
        """author -> reviewer -> author -> reviewer ... until the chain is
        at MAX_PEER_HOPS. -> the pane that sent last."""
        panes = ((a, "reviewer", b), (b, "author", a))
        for i in range(core.MAX_PEER_HOPS):
            src, seat, dst = panes[i % 2]
            r = self._send(src, seat, dst, f"message {i + 1}")
            self.assertEqual(r["hop"], i + 1)
        return panes[core.MAX_PEER_HOPS % 2]     # who sends next

    def _hub_q(self, p):
        q = p.question
        return q if q and q.get("source") == SRC else None

    def _assert_paused(self, sender, target_seat, sender_seat, limit=None):
        q = self._hub_q(sender)
        self.assertIsNotNone(q, f"no hub question on the sender: {sender.question}")
        for needle in (f"@{sender_seat}", f"@{target_seat}",
                       f"Refused: @{sender_seat} → @{target_seat}",
                       f"{limit or core.MAX_PEER_HOPS}-message limit"):
            self.assertIn(needle, q["text"])
        self.assertEqual(core.display_state(sender)["state"], "needs-you")
        ev = [e["data"] for e in sender.events if e["kind"] == "question"]
        self.assertEqual(ev[-1].get("source"), SRC,
                         "the transcript would attribute the hub's pause to the agent")

    # ── H1 ───────────────────────────────────────────────────────────────
    def test_H1_a_send_over_the_limit_raises_it_on_the_sender(self):
        a, b = self._pair()
        src, seat, dst = self._to_the_limit(a, b)
        r = self.mgr.deliver_peer(src.id, seat, "PASS")
        self.assertEqual((r["result"], r["reason"]), ("refused", "peer-chain"), r)
        self.assertTrue(r.get("raised_to_human"), r)
        self._assert_paused(src, seat, src.seat)
        self.assertIsNone(dst.question, "the target was flagged too")
        snap = src.snapshot(since=1 << 60)
        self.assertEqual((snap["display"], snap["question"]["source"]), ("needs-you", SRC))

    # ── H2 ───────────────────────────────────────────────────────────────
    def test_H2_a_broadcast_over_the_limit_raises_it(self):
        a, b = self._pair()
        src, seat, dst = self._to_the_limit(a, b)
        out = self.mgr.broadcast_peer(src.id, "to everyone")
        self.assertEqual([(x["to_seat"], x["reason"]) for x in out["results"]],
                         [(seat, "peer-chain")])
        self._assert_paused(src, seat, src.seat)

    # ── H3 ───────────────────────────────────────────────────────────────
    def test_H3_the_queue_drain_over_the_limit_raises_it_on_the_replier(self):
        a, b, ta, tb = self.waiting_pair(a_sleep=2)
        self.assertEqual(self.mgr.deliver_peer(b.id, "author", "reply")["result"],
                         "queued")
        with mock.patch.object(core, "MAX_PEER_HOPS", 1):     # the chain grew meanwhile
            self.assertTrue(wait_for(lambda: ("refused", "peer-chain") in self._q(a, "to")),
                            self._q(a))
            self.assertTrue(wait_for(lambda: self._hub_q(b) is not None), b.question)
        self._assert_paused(b, "author", "reviewer", limit=1)
        self.assertIsNone(a.question)

    # ── H4 ───────────────────────────────────────────────────────────────
    def test_H4_other_refusals_raise_nothing(self):
        a, b = self._pair()
        r = self.mgr.deliver_peer(a.id, "nobody-here", "x")
        self.assertEqual(r["reason"], "unknown-seat")
        tb = b.send("sleep 2")
        self.assertTrue(wait_for(lambda: self._turn(b) == tb))
        self.assertEqual(self.mgr.deliver_peer(a.id, "reviewer", "x")["reason"], "busy")
        self.assertTrue(wait_for(lambda: b.state == "ready", 10))
        with mock.patch.object(core, "TRANSFER_GATE", lambda s, d: "not for this lane"):
            self.assertEqual(self.mgr.deliver_peer(a.id, "reviewer", "x")["reason"],
                             "transfer-gate")
        self.assertIsNone(a.question, a.question)
        self.assertNotIn("peer_paused", [e["kind"] for e in a.events])

    # ── H5 ───────────────────────────────────────────────────────────────
    def test_H5_an_agents_own_question_is_not_overwritten(self):
        a, b = self._pair()
        src, seat, dst = self._to_the_limit(a, b)
        tok = self.mgr.mint_pane_token(src)
        self.mgr.peer_http("POST", "/api/peer/ask", tok, {"question": "mine?"})
        r = self.mgr.deliver_peer(src.id, seat, "PASS")
        self.assertEqual(r["reason"], "peer-chain")
        self.assertFalse(r.get("raised_to_human"), r)
        self.assertEqual((src.question["text"], src.question.get("source")),
                         ("mine?", None), "the hub overwrote the agent's question")
        paused = [e["data"] for e in src.events if e["kind"] == "peer_paused"]
        self.assertEqual([(p["to_seat"], p["raised"]) for p in paused], [(seat, False)])
        self.assertEqual(core.display_state(src)["state"], "needs-you")

    # ── H6 ───────────────────────────────────────────────────────────────
    def test_H6_a_human_turn_clears_it_and_the_loop_can_continue(self):
        a, b = self._pair()
        src, seat, dst = self._to_the_limit(a, b)
        self.mgr.deliver_peer(src.id, seat, "PASS")
        self.assertIsNotNone(self._hub_q(src))
        n = self._ends(src)
        src.send("go on")
        self.assertIsNone(src.question)
        self.assertTrue(wait_for(lambda: self._ends(src) == n + 1 and src.state == "ready"))
        # The chain restarts for BOTH panes: the pause named both, and the
        # human answered it -- one more message each way would otherwise
        # re-stall the loop on the target's side of the old count.
        self.assertIn("peer_chain_reset", [e["kind"] for e in dst.events])
        r = self._send(src, seat, dst, "PASS, resent")
        self.assertEqual(r["hop"], 1, r)
        back = self._send(dst, src.seat, src, "thanks")
        self.assertEqual(back["hop"], 2, back)
        self.assertIsNone(src.question)
