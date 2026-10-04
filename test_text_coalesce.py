"""Streamed text is coalesced into few events without losing a character or
reordering anything; the lane probe never blocks a request on a stale cache.

Run: python3 -m unittest test_text_coalesce -v
"""
import os
import queue
import threading
import time
import unittest

from testkit.scratch import default_state  # noqa: E402
default_state("corral-light-coalesce-")

import sessions                                   # noqa: E402
from corral_core import sessions as core          # noqa: E402


class _Mgr:
    """Just enough manager for a pane to broadcast into."""
    broadcast = core.ManagerBase.broadcast
    subscribe = core.ManagerBase.subscribe

    def __init__(self):
        self.subscribers = []
        self._lock = threading.Lock()


def _chunk(text):
    return {"content": {"type": "text", "text": text}}


class TextCoalescing(unittest.TestCase):
    def setUp(self):
        self.mgr = _Mgr()
        self.q = queue.Queue()
        self.mgr.subscribe(self.q)
        self.p = sessions.Pane("claude", sessions.STATE, "auto", self.mgr)

    def tearDown(self):
        self.p._flush_text()

    def kinds(self):
        return [e["kind"] for e in self.p.events]

    def text(self):
        return "".join(e["data"]["text"] for e in self.p.events
                       if e["kind"] == "text")

    def test_the_first_chunk_shows_at_once(self):
        """Nothing waits to appear: a run's first chunk is an event immediately."""
        self.p._on_event("agent_message_chunk", _chunk("Hel"))
        self.assertEqual(self.kinds(), ["text"])
        self.assertEqual(self.text(), "Hel")

    def test_a_burst_becomes_few_events_with_every_character(self):
        chunks = [f"w{i} " for i in range(400)]
        for c in chunks:
            self.p._on_event("agent_message_chunk", _chunk(c))
        # The throttle timer flushes the tail on its own; wait for it.
        deadline = time.time() + 2
        while time.time() < deadline and self.text() != "".join(chunks):
            time.sleep(0.02)
        self.assertEqual(self.text(), "".join(chunks), "characters lost or reordered")
        n = self.kinds().count("text")
        self.assertLess(n, 10, f"{n} text events for 400 chunks — not coalesced")
        self.assertGreaterEqual(n, 1)

    def test_a_tool_call_flushes_text_first_so_order_holds(self):
        for c in ("I will ", "read ", "the file."):
            self.p._on_event("agent_message_chunk", _chunk(c))
        self.p._on_event("tool_call", {"toolCallId": "t1", "title": "Read",
                                       "kind": "read", "status": "pending"})
        for c in ("Done", "."):
            self.p._on_event("agent_message_chunk", _chunk(c))
        self.p._flush_text()
        # Leading edge "I will ", the buffered rest flushed by the tool call,
        # the tool, then the buffered tail.
        self.assertEqual(self.kinds(), ["text", "text", "tool", "text"])
        self.assertEqual(self.text(), "I will read the file.Done.")
        ix = self.kinds().index("tool")
        self.assertEqual("".join(e["data"]["text"] for e in self.p.events[:ix]
                                 if e["kind"] == "text"), "I will read the file.")

    def test_a_thought_between_text_runs_keeps_its_place(self):
        self.p._on_event("agent_message_chunk", _chunk("a"))
        self.p._on_event("agent_thought_chunk", _chunk("hmm"))
        self.p._on_event("agent_message_chunk", _chunk("b"))
        self.p._flush_text()
        self.assertEqual(self.kinds(), ["text", "thought", "text"])

    def test_a_large_buffer_goes_out_before_the_timer(self):
        big = "x" * (core.TEXT_FLUSH_CHARS // 2 + 1)
        self.p._on_event("agent_message_chunk", _chunk(big))     # leading edge
        self.p._on_event("agent_message_chunk", _chunk(big))     # buffered
        self.p._on_event("agent_message_chunk", _chunk(big))     # over the cap
        self.assertGreaterEqual(self.kinds().count("text"), 2)
        self.p._flush_text()
        self.assertEqual(len(self.text()), 3 * len(big))

    def test_replayed_chunks_are_dropped_not_buffered(self):
        """A session/load replay is suppressed; nothing of it may surface later."""
        self.p._replaying = True
        self.p._on_event("agent_message_chunk", _chunk("old words"))
        self.p._replaying = False
        time.sleep(core.TEXT_FLUSH_S * 2)
        self.p._flush_text()
        self.assertEqual(self.text(), "")

    def test_every_text_event_is_broadcast_once(self):
        for c in ("a", "b", "c"):
            self.p._on_event("agent_message_chunk", _chunk(c))
        self.p._flush_text()
        got = []
        while not self.q.empty():
            got.append(self.q.get_nowait())
        self.assertEqual([e["seq"] for e in got if e["kind"] == "text"],
                         [e["seq"] for e in self.p.events if e["kind"] == "text"])


class ProbeCacheNeverBlocksARequest(unittest.TestCase):
    def setUp(self):
        import lane_probe
        self.lp = lane_probe
        self._saved = (lane_probe._cache.copy(), lane_probe._probe_now,
                       lane_probe.CACHE_S)
        lane_probe._cache.clear()

    def tearDown(self):
        self.lp._cache.clear()
        self.lp._cache.update(self._saved[0])
        self.lp._probe_now = self._saved[1]
        self.lp.CACHE_S = self._saved[2]

    def test_a_stale_entry_is_served_while_one_thread_refreshes(self):
        calls, started = [], threading.Event()

        def slow(key, cwd):
            calls.append(threading.current_thread().name)
            started.set()
            time.sleep(0.2)
            r = {"ok": True, "config": {}, "error": "fresh"}
            with self.lp._lock:
                self.lp._cache[key] = (time.time(), r)
            return r
        self.lp._probe_now = slow
        self.lp._cache["claude"] = (time.time() - 10_000,
                                    {"ok": True, "config": {}, "error": "stale"})
        t0 = time.time()
        a = self.lp.probe("claude")
        b = self.lp.probe("claude")
        self.assertLess(time.time() - t0, 0.1, "a stale hit made the caller wait")
        self.assertEqual((a["error"], b["error"]), ("stale", "stale"))
        self.assertTrue(started.wait(1))
        time.sleep(0.3)
        self.assertEqual(len(calls), 1, "more than one refresh ran for one key")
        self.assertEqual(self.lp.probe("claude")["error"], "fresh")
        self.assertEqual(len(calls), 1)

    def test_force_still_probes_now(self):
        self.lp._probe_now = lambda key, cwd: {"ok": True, "config": {}, "error": "now"}
        self.lp._cache["claude"] = (time.time(), {"ok": True, "config": {}, "error": "hit"})
        self.assertEqual(self.lp.probe("claude")["error"], "hit")
        self.assertEqual(self.lp.probe("claude", force=True)["error"], "now")


if __name__ == "__main__":
    unittest.main()
