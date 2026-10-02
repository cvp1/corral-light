"""Bounded reply-queue cases for a waiting pane, shared by both products and
run against each skin's real drain.

Mixed into a TestCase that provides `self.mgr` (a Manager with a `fake` lane
registered) and `self.agent_dir`. The fake agent's `sleep <s>` line holds the
author's turn open, standing in for a turn blocked in `seat_wait`;
`peer_turn(...)` is the poll seat_wait's child makes.

    T11b.1 queued, delivered as the next turn, one peer
    T11b.2 anyone the waiter is not waiting on is still `busy`
    T11b.3 a second message while one is queued -> `queue-full`
    T11b.4 TTL expiry recorded on both panes
    T11b.5 close / cancel / pause / death of the waiter -> `dropped`, recorded
           (and a hub restart: the orphaned record is closed, never re-sent)
    T11b.6 a card raised on the waiter -> refused `card-pending` at delivery
    T11b.7 the hop bound at delivery, the hop stamped at delivery
    T11b.8 `_turn_lock` taken once per path, never nested
"""
import os
import signal
import sys
import threading
import time
from unittest import mock

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


class NestCheckLock:
    """A non-reentrant lock that RECORDS (and refuses) a second acquisition
    by the thread already holding it, instead of deadlocking silently."""

    def __init__(self):
        self._l = threading.Lock()
        self.owner = None
        self.nested = []
        self.taken = 0

    def acquire(self, blocking=True, timeout=-1):
        if self.owner == threading.get_ident():
            self.nested.append(threading.current_thread().name)
            raise RuntimeError("_turn_lock taken twice by one thread")
        ok = self._l.acquire(blocking, timeout)
        if ok:
            self.owner = threading.get_ident()
            self.taken += 1
        return ok

    def release(self):
        self.owner = None
        self._l.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


class ReplyQueueCases:

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _k(p):
        return [e["kind"] for e in p.events]

    @staticmethod
    def _q(p, side=None):
        """The peer_queue records on a pane, as (status, reason)."""
        return [(e["data"]["status"], e["data"].get("reason"))
                for e in p.events if e["kind"] == "peer_queue"
                and (side is None or e["data"].get("side") == side)]

    @staticmethod
    def _turn(p):
        return getattr(p._in_flight, "turn", None)

    def _ends(self, p):
        return [e["data"].get("turn") for e in p.events if e["kind"] == "turn_end"]

    def _no_nesting(self, *panes):
        for p in panes:
            self.assertEqual(p._turn_lock.nested, [],
                             f"_turn_lock taken twice by one thread on {p.id}")

    def waiting_pair(self, a_sleep=3, b_sleep=2):
        """author (a) is mid-turn and waiting on the turn it sent reviewer (b),
        which b is running now. -> (a, b, a's turn, b's turn).

        Both panes get a NestCheckLock, so a path that re-took `_turn_lock` fails
        instead of deadlocking the drain.
        """
        a = self.mgr.create("fake", self.agent_dir)
        b = self.mgr.create("fake", self.agent_dir)
        self.assertEqual((a.state, b.state), ("ready", "ready"), (a.error, b.error))
        a._turn_lock, b._turn_lock = NestCheckLock(), NestCheckLock()
        self.addCleanup(self._no_nesting, a, b)
        self.mgr.bind_seat(a.id, "author")
        self.mgr.bind_seat(b.id, "reviewer")
        ta = a.send(f"sleep {a_sleep}")
        self.assertTrue(wait_for(lambda: self._turn(a) == ta), "the author never started")
        r = self.mgr.deliver_peer(a.id, "reviewer", f"please review\nsleep {b_sleep}")
        self.assertEqual(r["result"], "delivered", r)
        self.assertTrue(wait_for(lambda: self._turn(b) == r["turn"]),
                        "the reviewer never started the author's turn")
        w = self.mgr.peer_turn(a.id, "reviewer", r["turn"])        # seat_wait's poll
        self.assertEqual((w["result"], w["ended"]), ("turn", False), w)
        return a, b, ta, r["turn"]

    def test_T11b_1_a_reply_to_a_pane_waiting_on_its_sender_is_queued_then_delivered(self):
        a, b, ta, tb = self.waiting_pair()
        r = self.mgr.deliver_peer(b.id, "author", "my review: looks fine")
        self.assertEqual(r["result"], "queued", r)
        self.assertEqual(r["behind_turn"], ta, "not the turn it waits behind")
        self.assertNotIn("turn", r, "a queued message has no turn yet")
        self.assertEqual(r["expires_in_s"], core.PEER_QUEUE_TTL_S)
        self.assertIn("NOT been delivered", r["why"])
        self.assertNotIn("peer", self._k(a), "delivered before the waiter's turn ended")
        self.assertTrue(wait_for(lambda: len(self._ends(a)) == 2),
                        f"never delivered: {self._q(a)}")
        peers = [e for e in a.events if e["kind"] == "peer"]
        self.assertEqual(len(peers), 1, "not exactly one peer event")
        p = peers[0]["data"]
        self.assertEqual((p["from_seat"], p["hop"]), ("reviewer", 2),
                         "the hop is not stamped at delivery")
        self.assertEqual(self._ends(a), [ta, p["turn"]],
                         "not delivered as the waiter's NEXT turn")
        kinds = self._k(a)
        self.assertLess(kinds.index("turn_end"), kinds.index("peer"))
        self.assertIn("my review: looks fine", "".join(
            (e.get("data") or {}).get("text", "") for e in a.events if e["kind"] == "text"))
        self.assertEqual(self._q(a, "to"), [("queued", None), ("delivered", None)])
        self.assertTrue(wait_for(lambda: self._q(b, "from") ==
                                 [("queued", None), ("delivered", None)]))
        rec = [e["data"] for e in a.events if e["kind"] == "peer_queue"]
        self.assertNotIn("text", rec[0], "the queued record carried the body")
        self.assertEqual(rec[-1]["turn"], p["turn"])

    def test_T11b_2_anyone_the_waiter_is_not_waiting_on_is_still_busy(self):
        a, b, ta, tb = self.waiting_pair(a_sleep=4, b_sleep=1)
        c = self.mgr.create("fake", self.agent_dir)
        self.mgr.bind_seat(c.id, "critic")
        tc = c.send("sleep 3")
        self.assertTrue(wait_for(lambda: self._turn(c) == tc))
        r = self.mgr.deliver_peer(c.id, "author", "not awaited")
        self.assertEqual((r["result"], r["reason"], r.get("retry_after")),
                         ("refused", "busy", "your-turn"), r)
        # the awaited sender, but the wait went stale (its child stopped polling)
        r = self.mgr.deliver_peer(b.id, "author", "stale",
                                  now=time.time() + core.PEER_WAIT_SEEN_S + 1)
        self.assertEqual(r["reason"], "busy", r)
        # the awaited sender after the awaited turn has ended
        self.assertTrue(wait_for(lambda: tb in self._ends(b) and self._turn(b) is None))
        r = self.mgr.deliver_peer(b.id, "author", "from a later moment")
        self.assertEqual(r["reason"], "busy", r)
        self.assertEqual(self._q(a), [], "something was queued")
        c.cancel()

    def test_T11b_3_a_second_message_while_one_is_queued_is_queue_full(self):
        a, b, ta, tb = self.waiting_pair()
        self.assertEqual(self.mgr.deliver_peer(b.id, "author", "one")["result"], "queued")
        r = self.mgr.deliver_peer(b.id, "author", "two")
        self.assertEqual((r["result"], r["reason"]), ("refused", "queue-full"), r)
        self.assertNotIn("retry_after", r, "queue-full must carry no retry hint")
        self.assertIn(str(core.PEER_QUEUE_MAX), r["why"])
        self.assertTrue(wait_for(lambda: len(self._ends(a)) == 2))
        self.assertEqual(self._k(a).count("peer"), 1)

    def test_T11b_4_an_undelivered_message_expires_on_both_panes(self):
        a, b, ta, tb = self.waiting_pair(a_sleep=3)
        with mock.patch.object(core, "PEER_QUEUE_TTL_S", 0.3):
            r = self.mgr.deliver_peer(b.id, "author", "too late")
        self.assertEqual((r["result"], r["expires_in_s"]), ("queued", 0.3))
        self.assertTrue(wait_for(lambda: ("expired", None) in self._q(a, "to")
                                 and ("expired", None) in self._q(b, "from"), 5))
        rec = [e["data"] for e in a.events if e["kind"] == "peer_queue"][-1]
        self.assertIn("not delivered within", rec["why"])
        self.assertTrue(wait_for(lambda: ta in self._ends(a)))
        time.sleep(0.3)
        self.assertNotIn("peer", self._k(a), "an expired message was delivered")
        self.assertEqual(a.__dict__.get("_peer_held"), [])

    def test_T11b_5_close_cancel_pause_or_death_of_the_waiter_drops_it(self):
        def kill(a):
            os.kill(a.client.p.pid, signal.SIGKILL)
        for how, act in (("cancelled", lambda a: a.cancel()),
                         ("paused", lambda a: a.pause()),
                         ("closed", lambda a: self.mgr.close(a.id)),
                         ("dead", kill)):
            with self.subTest(how=how):
                a, b, ta, tb = self.waiting_pair(a_sleep=5, b_sleep=2)
                try:
                    self.assertEqual(self.mgr.deliver_peer(b.id, "author", "x")["result"],
                                     "queued")
                    act(a)
                    self.assertTrue(wait_for(lambda: ("dropped", how) in self._q(a, "to")
                                             and ("dropped", how) in self._q(b, "from")),
                                    (how, self._q(a), self._q(b)))
                    time.sleep(0.3)
                    self.assertNotIn("peer", self._k(a), f"{how}: delivered anyway")
                    if how == "cancelled":
                        self.assertTrue(wait_for(lambda: ta in self._ends(a)))
                        time.sleep(0.2)
                        self.assertNotIn("peer", self._k(a))
                finally:
                    for p in (a, b):          # the next subtest reuses the seats
                        if p.id in self.mgr.panes:
                            self.mgr.close(p.id)

    def test_T11b_5_a_hub_restart_closes_the_orphaned_record_and_resends_nothing(self):
        """In memory only: what a restarted hub finds is a `queued` record with
        no outcome after it. restore() records `dropped: hub-restart` on the
        pane holding it, once, and delivers nothing."""
        a = self.mgr.create("fake", self.agent_dir)
        a.emit("peer_queue", {"status": "queued", "side": "to", "qid": "q1",
                              "from_pane": "gone", "from_seat": "reviewer",
                              "to_pane": a.id, "to_seat": "author",
                              "behind_turn": "t0", "chars": 3, "ttl_s": 600},
               activity=False)
        a.emit("peer_queue", {"status": "queued", "side": "to", "qid": "q2"},
               activity=False)
        a.emit("peer_queue", {"status": "delivered", "side": "to", "qid": "q2"},
               activity=False)
        self.mgr._peer_queue_orphans()
        self.mgr._peer_queue_orphans()                  # idempotent
        drops = [e["data"] for e in a.events if e["kind"] == "peer_queue"
                 and e["data"]["status"] == "dropped"]
        self.assertEqual([(d["qid"], d["reason"]) for d in drops], [("q1", "hub-restart")])
        self.assertNotIn("peer", self._k(a))

    def test_T11b_6_a_card_on_the_waiter_refuses_it_at_delivery(self):
        a, b, ta, tb = self.waiting_pair(a_sleep=2)
        self.assertEqual(self.mgr.deliver_peer(b.id, "author", "x")["result"], "queued")
        a.pending["late"] = {"title": "raised before the turn ended"}
        try:
            self.assertTrue(wait_for(lambda: ("refused", "card-pending") in self._q(a, "to")
                                     and ("refused", "card-pending") in self._q(b, "from")),
                            self._q(a))
            time.sleep(0.2)
            self.assertNotIn("peer", self._k(a), "delivered over a pending card")
            self.assertEqual(self._ends(a), [ta])
        finally:
            a.pending.clear()

    def test_T11b_7_the_hop_bound_is_enforced_at_delivery(self):
        a, b, ta, tb = self.waiting_pair(a_sleep=2)
        with mock.patch.object(core, "MAX_PEER_HOPS", 1):
            r = self.mgr.deliver_peer(b.id, "author", "over at enqueue")
            self.assertEqual(r["reason"], "peer-chain", "not checked at enqueue")
        self.assertEqual(self.mgr.deliver_peer(b.id, "author", "x")["result"], "queued")
        with mock.patch.object(core, "MAX_PEER_HOPS", 1):     # the chain grew meanwhile
            self.assertTrue(wait_for(lambda: ("refused", "peer-chain") in self._q(a, "to")),
                            self._q(a))
        self.assertNotIn("peer", self._k(a))
        rec = [e["data"] for e in a.events if e["kind"] == "peer_queue"]
        self.assertNotIn("hop", rec[0], "the hop was stamped at enqueue")

    def test_T11b_8_the_lock_is_taken_once_and_never_nested(self):
        a, b, ta, tb = self.waiting_pair()
        lock = a._turn_lock
        out = {}
        t = threading.Thread(target=lambda: out.update(
            self.mgr.deliver_peer(b.id, "author", "through the checked lock")), daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "enqueue deadlocked")
        self.assertEqual(out.get("result"), "queued", out)
        self.assertTrue(wait_for(lambda: len(self._ends(a)) == 2),
                        "delivery from the drain did not happen")
        self.assertEqual(self._k(a).count("peer"), 1)
        # the expiry timer's path too
        with mock.patch.object(core, "PEER_QUEUE_TTL_S", 0.2):
            ta2 = a.send("sleep 2")
            self.assertTrue(wait_for(lambda: self._turn(a) == ta2))
            r = self.mgr.deliver_peer(a.id, "reviewer", "again\nsleep 2")
            self.assertTrue(wait_for(lambda: self._turn(b) == r["turn"]))
            self.mgr.peer_turn(a.id, "reviewer", r["turn"])
            self.assertEqual(self.mgr.deliver_peer(b.id, "author", "y")["result"], "queued")
            self.assertTrue(wait_for(lambda: ("expired", None) in self._q(a, "to"), 5))
        self.assertEqual(lock.nested, [], "_turn_lock was taken twice by one thread")
        self.assertGreater(lock.taken, 4)
