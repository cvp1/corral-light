#!/usr/bin/python3
"""The permission rail's contract, as tests both products run.

WHY IT LIVES HERE
    These assertions are the reason `corral_core` exists. On 2026-08-31 a
    three-model panel found fifteen rail defects and they were fixed in full
    Corral only; for nine days Corral Light — the public product — shipped the
    unfixed rail, and nothing in either tree could have told you. A test that
    lives in one product proves one product. This one is imported by both
    suites and exercises the single shared module, so a rail defect cannot be
    fixed for one audience and not the other again.

RE-PROVING THE 2026-09-09 FINDING
    Every test below FAILS against the pre-merge `corral-light/acp.py`. Point
    it at any candidate implementation to check:

        CORRAL_ACP_UNDER_TEST=/path/to/acp.py python3 -m unittest \
            corral_core.test_acp_rail

    That seam is deliberate. The finding was that two implementations of one
    contract drifted apart silently; the cheapest guard against a repeat is a
    contract you can point at a file.

WHAT THESE ARE NOT
    Not a substitute for `corral/selftest_corral.py`'s rail checks, which run
    the same properties from Corral's side. Overlap here is the point: this
    contract has two audiences and should be over-covered, not shared out.
"""
import importlib.util
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path

_UNDER_TEST = os.environ.get("CORRAL_ACP_UNDER_TEST")
if _UNDER_TEST:
    _spec = importlib.util.spec_from_file_location("_acp_under_test", _UNDER_TEST)
    acp = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(acp)
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from corral_core import acp


def _client():
    """A client with real state and no child process.

    `__new__` + `_init_state()` — never a hand-copied echo of the fields. The
    2026-08-31 panel added `_permlock` and the stub that hand-copied state
    failed with an AttributeError instead of proving the fix.
    """
    c = acp.AcpClient.__new__(acp.AcpClient)
    if hasattr(c, "_init_state"):
        c._init_state()
    else:                       # pre-merge Light: state was inlined in __init__
        c._id = 0
        c._pending = {}
        c._wlock = threading.Lock()
        c._idlock = threading.Lock()
        c._perm_answers = {}
        c._last_activity = time.monotonic()
        c.alive = False
        c.exit_reason = None
        c.stderr_tail = []
        c._closed = False
    c.alive = True
    c.writes = []
    c.events = []
    c._write = lambda obj: c.writes.append(obj)
    c.on_event = lambda k, d=None: c.events.append((k, d))
    c.on_permission = lambda req: c.events.append(("permission", req))
    return c


def _ask(c, rid, options=(("yes", "allow_once"), ("no", "reject_once")), **params):
    p = {"options": [{"optionId": o, "kind": k} for o, k in options]}
    p.update(params)
    c._on_request({"method": "session/request_permission", "id": rid, "params": p})
    time.sleep(0.05)            # let the waiter thread reach its poll loop


def _settle(c, n=1, timeout=3.0):
    end = time.time() + timeout
    while len(c.writes) < n and time.time() < end:
        time.sleep(0.01)
    return c.writes


class ConsentIsBoundToTheBytesTheHumanSaw(unittest.TestCase):
    """Principle 17. The rail's whole reason to exist."""

    def test_the_agent_cannot_rename_the_request_the_human_is_answering(self):
        """gpt finding 1, 2026-08-31 panel.

        `{"requestId": key, **params}` lets an agent-supplied
        `params.requestId` win. The card then reaches the rail under an id the
        waiter is NOT keyed on: the human's click returns False and the agent
        blocks forever — and worse, an id naming some OTHER pending card means
        an approval displayed for one action is recorded against another.
        Corral's wire id must win.
        """
        c = _client()
        _ask(c, 11, requestId="999-attacker-chosen")
        cards = [d for k, d in c.events if k == "permission"]
        self.assertTrue(cards, "no permission card reached the rail at all")
        self.assertEqual(cards[-1]["requestId"], "11",
                         "the agent renamed the request the human is being "
                         "shown; consent would bind to the wrong bytes")
        self.assertTrue(c.answer_permission("11", "yes"),
                        "the human's click could not find its own card")

    def test_the_first_answer_wins_and_a_deny_is_not_overwritten(self):
        """gpt finding 2. Two clicks arriving together both returned True and
        the LAST one won, so an allow could overwrite a deny while the person
        who pressed deny was told it landed."""
        c = _client()
        _ask(c, 12)
        results, barrier = [], threading.Barrier(2)

        def click(opt):
            barrier.wait()
            results.append(c.answer_permission("12", opt))

        ts = [threading.Thread(target=click, args=(o,)) for o in ("no", "yes")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(results), [False, True],
                         f"both clicks were accepted ({results}) — one of them "
                         f"was told it landed when it did not")
        w = _settle(c)
        self.assertEqual(len(w), 1, f"expected exactly one reply, got {w}")

    def test_a_falsy_option_id_is_still_a_choice(self):
        """grok finding 8. A vendor is entitled to an option id of "" or 0.
        Read as 'no selection', the human's click simply vanished."""
        c = _client()
        _ask(c, 13, options=(("", "allow_once"),))
        self.assertTrue(c.answer_permission("13", ""),
                        "a falsy option id was refused at the click")
        w = _settle(c)
        self.assertEqual(len(w), 1, f"the click wrote nothing back: {w}")
        outcome = ((w[-1].get("result") or {}).get("outcome") or {})
        self.assertEqual(outcome.get("outcome"), "selected")
        self.assertEqual(outcome.get("optionId"), "")

    def test_an_unanswered_card_is_never_answered_for_the_human(self):
        """Fail-closed, and no clock ends a turn. A waiter released without a
        selection must write NOTHING — never a guessed outcome."""
        c = _client()
        _ask(c, 14, options=(("yes", "allow_once"),))
        c._perm_answers["14"]["ev"].set()       # woken with no selection
        time.sleep(0.3)
        self.assertEqual(c.writes, [],
                         f"something other than the human ended the turn: {c.writes}")


class TheRailStaysBounded(unittest.TestCase):
    """Principle 8, on the paths where an agent controls the volume."""

    def test_a_reused_request_id_cannot_defeat_the_pending_bound(self):
        """gemini finding 3 / gpt finding 4. The bound is on THREADS, checked
        via len() of a dict — and a duplicate key overwrote its predecessor
        without growing the dict, so waiters accumulated past the bound while
        len() stayed put. Grok numbers every permission it asks `0`, so this
        is measured, not theoretical."""
        c = _client()
        _ask(c, 0)
        before = threading.active_count()
        for _ in range(acp.MAX_PENDING_PERMISSIONS + 5):
            _ask(c, 0)
        self.assertLessEqual(
            threading.active_count() - before, acp.MAX_PENDING_PERMISSIONS,
            "reusing one request id grew the waiter threads past the bound")
        self.assertTrue(c.answer_permission("0", "yes"),
                        "the surviving card became unanswerable")

    def test_an_unterminated_stderr_line_is_bounded(self):
        """grok finding 7 / gpt finding 5. stderr was a bare readline, so one
        unterminated line grew RSS without limit and the tail truncation that
        was supposed to cap it never ran."""
        self.assertTrue(hasattr(acp, "MAX_STDERR_LINE"),
                        "stderr has no bound at all")

        class Endless:
            def __init__(self, total):
                self.total, self.sent = total, 0

            def readline(self, size=-1):
                n = min(size if size and size > 0 else 1024, self.total - self.sent)
                if n <= 0:
                    return ""
                self.sent += n
                return "x" * n

        with self.assertRaises(ValueError):
            acp.AcpClient._read_bounded_line(
                Endless(acp.MAX_STDERR_LINE + 10_000), acp.MAX_STDERR_LINE)

    def test_an_unterminated_stdout_line_is_refused_not_buffered(self):
        class Endless:
            def __init__(self, total):
                self.total, self.sent = total, 0

            def readline(self, size=-1):
                n = min(size if size and size > 0 else 1024, self.total - self.sent)
                if n <= 0:
                    return ""
                self.sent += n
                return "x" * n

        with self.assertRaises(ValueError):
            acp.AcpClient._read_bounded_line(Endless(acp.MAX_STDOUT_LINE + 10_000))


class TheReaderSurvivesWhatAnAgentCanSend(unittest.TestCase):

    def test_a_non_object_json_line_does_not_kill_the_reader(self):
        """grok finding 6. `null`, `true`, `3` and `[]` are all valid JSON and
        all blew up `"id" in msg` with a TypeError the handler did not catch:
        the reader thread died and a perfectly healthy agent was then reported
        as exited."""
        c = _client()
        for junk in ("null", "3", "[]", "true", '"str"'):
            with self.subTest(junk=junk):
                try:
                    c._handle(json.loads(junk)) if hasattr(c, "_handle") else None
                except Exception as e:            # noqa: BLE001
                    self.fail(f"a non-object JSON line raised {type(e).__name__}")

    def test_the_reader_loop_skips_non_objects(self):
        """The property above, through the real loop rather than a helper."""
        lines = ['null\n', '3\n', '[]\n',
                 json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) + "\n",
                 ""]

        class Stream:
            def __init__(self):
                self._it = iter(lines)

            def readline(self, size=-1):
                return next(self._it, "")

        c = _client()
        c.p = type("P", (), {"stdout": Stream(), "stderr": None,
                             "poll": staticmethod(lambda: 0),
                             "wait": staticmethod(lambda timeout=None: 0)})()
        c.pgid = None
        ev = threading.Event()
        c._pending[1] = {"ev": ev, "result": None, "error": None}
        try:
            c._read_stdout()
        except Exception as e:                    # noqa: BLE001
            self.fail(f"the reader died on a non-object line: "
                      f"{type(e).__name__}: {e}")
        self.assertEqual(c._pending.get(1, {}).get("result"), {"ok": True},
                         "the reader stopped before the real message")


class AFailedSendLeavesNothingBehind(unittest.TestCase):

    def test_a_write_that_fails_does_not_strand_a_pending_slot(self):
        """Principle 8. The slot was inserted before the write, so a failed
        write left it forever and every retry against a dying client added
        another. Nothing will ever answer a request that was never sent."""
        c = _client()

        def boom(obj):
            raise acp.AgentError("pipe is gone")

        c._write = boom
        with self.assertRaises(acp.AgentError):
            c.request("session/new", {}, timeout=1)
        self.assertEqual(c._pending, {},
                         "a request that was never sent is still pending")


if __name__ == "__main__":
    unittest.main()
