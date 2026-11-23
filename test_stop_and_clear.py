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
import json
import os
import signal
import time
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
        self.assertIn("c.append(att, ac, ta, b, stopButton(p))", js)
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


class EveryDroppedMessageIsAnnounced(FakeLaneCase):
    """A queued message dropped before the agent saw it, by any path, emits
    `unsent` with its turn id, so whoever waits on that turn can stop. The
    turn already running is NOT announced: its own turn_end still comes."""

    def _unsent(self, p):
        return [e["data"]["turn"] for e in p.events if e["kind"] == "unsent"]

    def _busy_with_two_queued(self):
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        running = p.send("sleep 30")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        return p, running, p.send("one"), p.send("two")

    def test_withdraw(self):
        p, running, a, b = self._busy_with_two_queued()
        p.withdraw(a)
        self.assertEqual(self._unsent(p), [a])
        p.cancel()

    def test_stop(self):
        p, running, a, b = self._busy_with_two_queued()
        p.cancel()
        self.assertEqual(self._unsent(p), [a, b])

    def test_pause(self):
        p, running, a, b = self._busy_with_two_queued()
        p.pause()
        self.assertTrue(wait_for(lambda: set(self._unsent(p)) >= {a, b}))
        self.assertNotIn(running, self._unsent(p))

    def test_clear(self):
        p, running, a, b = self._busy_with_two_queued()
        p.clear_context()
        self.assertTrue(wait_for(lambda: set(self._unsent(p)) >= {a, b}))
        self.assertNotIn(running, self._unsent(p))


class ReplyFromTheCard(FakeLaneCase):
    """A reply typed into a rail card carries what the card showed
    (`expect` = reply_target()). It lands when that still holds, and is
    refused with nothing recorded when the pane has moved on: a stale card
    must never steer a different turn than the one it showed."""

    def _users(self, p):
        return [e["data"]["text"] for e in p.events if e["kind"] == "user"]

    def _ready_after(self, p, text):
        p.send(text)
        self.assertTrue(wait_for(lambda: f"echo: {text}" in self.texts(p)))
        self.assertTrue(wait_for(lambda: p.state == "ready"))

    def test_a_question_reply_lands_and_answers_it(self):
        p = self.mgr.create("fake", self.agent_dir)
        self._ready_after(p, "hello")
        p.ask("Which branch?")
        tgt = p.reply_target()
        self.assertEqual(tgt, f"q:{p.question['id']}")
        p.send("main", expect=tgt)
        self.assertTrue(wait_for(lambda: "echo: main" in self.texts(p)))
        self.assertIsNone(p.question, "the reply did not answer the question")

    def test_a_reply_to_a_replaced_question_is_refused_and_not_recorded(self):
        import sessions
        p = self.mgr.create("fake", self.agent_dir)
        self._ready_after(p, "hello")
        p.ask("Which branch?")
        old = p.reply_target()
        # Same second, on purpose: `at` alone could not tell them apart.
        p.ask("Actually: which remote?")
        self.assertNotEqual(p.reply_target(), old)
        users, turns = self._users(p), len(p._turns().turns())
        with self.assertRaises(sessions.ReplyMoved) as cm:
            p.send("main", expect=old)
        self.assertEqual(cm.exception.current, p.reply_target())
        self.assertEqual(self._users(p), users, "a refused reply reached the transcript")
        self.assertEqual(len(p._turns().turns()), turns, "a refused reply was recorded")
        self.assertEqual(p.question["text"], "Actually: which remote?")

    def test_a_waiting_turn_reply_lands_once(self):
        import sessions
        p = self.mgr.create("fake", self.agent_dir)
        self._ready_after(p, "hello")
        end = [e for e in p.events if e["kind"] == "turn_end"][-1]
        tgt = p.reply_target()
        self.assertEqual(tgt, f"t:{end['seq']}")
        p.send("go on", expect=tgt)
        self.assertTrue(wait_for(lambda: "echo: go on" in self.texts(p)))
        # The same card again (a double tap, a second tab): it moved on.
        with self.assertRaises(sessions.ReplyMoved):
            p.send("go on", expect=tgt)
        self.assertEqual(self._users(p).count("go on"), 1)

    def test_someone_else_writing_first_makes_the_card_stale(self):
        import sessions
        p = self.mgr.create("fake", self.agent_dir)
        self._ready_after(p, "hello")
        tgt = p.reply_target()
        p.send("sleep 30")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        self.assertIsNone(p.reply_target(), "a busy pane has nothing to reply to")
        with self.assertRaises(sessions.ReplyMoved):
            p.send("late", expect=tgt)
        self.assertNotIn("late", self._users(p))
        p.cancel()

    def test_a_question_keeps_its_id_across_a_restart(self):
        import corral_core.sessions as core
        q = core.PaneBase.restore_question({"question": {"text": "a", "at": "x", "id": "abc"}})
        self.assertEqual(q["id"], "abc")
        q = core.PaneBase.restore_question({"question": {"text": "a", "at": "x"}})
        self.assertNotIn("id", q)        # older record: reply_target falls back to `at`

    def test_an_empty_or_made_up_target_is_refused(self):
        import sessions
        p = self.mgr.create("fake", self.agent_dir)
        self._ready_after(p, "hello")
        for bad in ("", "t:0", "q:nope"):
            with self.assertRaises(sessions.ReplyMoved):
                p.send("x", expect=bad)
        self.assertNotIn("x", self._users(p))


class RemoteParityOnTheDrivenHub(FakeLaneCase):
    """A pane driven from another hub: `reply` and `withdraw` run the same
    checks as on this wall, and their refusals cross the channel as 409s
    with a reason (moved / started), not as a generic error."""

    def _adapter(self):
        import drive_host, sessions
        return drive_host.HostAdapter(self.mgr, sessions)

    def test_reply_lands_when_fresh_and_is_refused_when_moved(self):
        import drive
        p = self.mgr.create("fake", self.agent_dir)
        p.send("hello")
        self.assertTrue(wait_for(lambda: "echo: hello" in self.texts(p)))
        self.assertTrue(wait_for(lambda: p.state == "ready"))
        a, tgt = self._adapter(), p.reply_target()
        r = a.op("reply", {"pane": p.id, "text": "go on", "expect": tgt}, {})
        self.assertTrue(r["turn"])
        self.assertTrue(wait_for(lambda: "echo: go on" in self.texts(p)))
        with self.assertRaises(drive.DriveError) as cm:
            a.op("reply", {"pane": p.id, "text": "again", "expect": tgt}, {})
        self.assertEqual((cm.exception.status, cm.exception.reason), (409, "moved"))
        with self.assertRaises(drive.DriveError):
            a.op("reply", {"pane": p.id, "text": "no target"}, {})   # empty expect: refused

    def test_withdraw_takes_back_or_says_it_started(self):
        import drive
        p = self.mgr.create("fake", self.agent_dir)
        p.send("sleep 30")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        t = p.send("queued one")
        a = self._adapter()
        self.assertEqual(a.op("withdraw", {"pane": p.id, "turn": t}, {})["text"], "queued one")
        with self.assertRaises(drive.DriveError) as cm:
            a.op("withdraw", {"pane": p.id, "turn": t}, {})
        self.assertEqual((cm.exception.status, cm.exception.reason), (409, "started"))
        p.cancel()

    def test_a_refusal_keeps_its_reason_across_the_channel(self):
        import drive, tempfile, os
        d = tempfile.mkdtemp(prefix="corral-mut-")
        m = drive.Mutations(os.path.join(d, "m.json"))
        def moved():
            raise drive.DriveError("moved on", 409, reason="moved")
        self.assertEqual(m.once("peer", "a" * 12, moved),
                         {"ok": False, "error": "moved on", "status": 409, "reason": "moved"})


class EachTurnSaysWhenItStarts(FakeLaneCase):
    """`turn_start` {turn} marks the moment the agent takes a queued message,
    in queue order, once each: what `corral-light say` waits for."""

    def test_turn_start_in_order(self):
        p = self.mgr.create("fake", self.agent_dir)
        a = p.send("sleep 1")
        b = p.send("one")
        c = p.send("two")
        self.assertTrue(wait_for(lambda: "echo: two" in self.texts(p), timeout=15))
        starts = [e["data"]["turn"] for e in p.events if e["kind"] == "turn_start"]
        self.assertEqual(starts, [a, b, c])
        kinds = [(e["kind"], (e.get("data") or {}).get("turn")) for e in p.events
                 if e["kind"] in ("turn_start", "turn_end")]
        self.assertEqual(kinds[:2], [("turn_start", a), ("turn_end", a)],
                         "a turn starts before it ends, and the next waits")


PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


class PastedImages(FakeLaneCase):
    """An image pasted into the composer: stored by hash in the pane's folder,
    sent as an ACP image block to a lane that says it takes images, refused
    by one that does not, and never written into the event log as bytes."""

    def _pane(self, images):
        if images:
            os.environ["FAKE_IMAGES"] = "1"
            self.addCleanup(os.environ.pop, "FAKE_IMAGES", None)
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        return p

    def test_an_image_reaches_a_lane_that_takes_them(self):
        p = self._pane(images=True)
        self.assertTrue(p.accepts_images())
        self.assertTrue(p.snapshot()["acceptsImages"])
        sha = p.save_image(PNG_1PX, "image/png")
        self.assertEqual(p.save_image(PNG_1PX, "image/png"), sha, "the same image is stored once")
        p.send("what is this", images=[sha])
        self.assertTrue(wait_for(lambda: f"echo: what is this [images: image/png {len(PNG_1PX)}B]" in self.texts(p)))
        user = [e for e in p.events if e["kind"] == "user"][-1]["data"]
        self.assertEqual(user["images"], [{"sha": sha, "type": "image/png"}])
        self.assertNotIn("iVBOR", json.dumps(p.events), "image bytes leaked into the event log")
        self.assertEqual(oct(p.image_path(sha).stat().st_mode & 0o777), "0o600")

    def test_an_image_alone_is_a_message(self):
        p = self._pane(images=True)
        sha = p.save_image(PNG_1PX, "image/png")
        p.send("", images=[sha])
        self.assertTrue(wait_for(lambda: "[images: image/png" in self.texts(p)))
        self.assertEqual(p.title, "an image")

    def test_a_lane_without_images_refuses_before_anything_is_sent(self):
        p = self._pane(images=False)
        self.assertFalse(p.accepts_images())
        sha = p.save_image(PNG_1PX, "image/png")
        users = len([e for e in p.events if e["kind"] == "user"])
        with self.assertRaises(ValueError) as cm:
            p.send("look", images=[sha])
        self.assertIn("does not take images", str(cm.exception))
        self.assertEqual(len([e for e in p.events if e["kind"] == "user"]), users)

    def test_what_is_refused_at_the_door(self):
        p = self._pane(images=True)
        for data, ctype, why in ((PNG_1PX, "image/svg+xml", "only PNG"),
                                 (b"GIF89a" + b"x" * 10, "image/png", "not the image type"),
                                 (b"", "image/png", "empty"),
                                 (b"\x89PNG\r\n\x1a\n" + b"0" * sessions_IMAGE_MAX(), "image/png", "the limit")):
            with self.assertRaises(ValueError, msg=why) as cm:
                p.save_image(data, ctype)
            self.assertIn(why, str(cm.exception))
        with self.assertRaises(ValueError):
            p.send("x", images=["0" * 64])               # not on this pane
        with self.assertRaises(ValueError):
            p.send("x", images=[p.save_image(PNG_1PX, "image/png")] * 5)   # over the count
        self.assertIsNone(p.image_path("../../etc/passwd"))


def sessions_IMAGE_MAX():
    import sessions
    return sessions.IMAGE_MAX


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
