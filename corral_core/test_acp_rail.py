#!/usr/bin/python3
"""The permission rail's contract, shared by both products' suites.

Point it at another implementation with:

    CORRAL_ACP_UNDER_TEST=/path/to/acp.py python3 -m unittest \
        corral_core.test_acp_rail
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
    """A client with real state and no child process."""
    c = acp.AcpClient.__new__(acp.AcpClient)
    if hasattr(c, "_init_state"):
        c._init_state()
    else:                       # implementations without _init_state
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
    """Consent binds to the request the human was shown."""

    def test_the_agent_cannot_rename_the_request_the_human_is_answering(self):
        """An agent-supplied params.requestId must not override the wire id."""
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
        """Of two near-simultaneous clicks, only the first is accepted."""
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
        """An option id of "" is a real selection."""
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
        """A waiter released without a selection writes nothing."""
        c = _client()
        _ask(c, 14, options=(("yes", "allow_once"),))
        c._perm_answers["14"]["ev"].set()       # woken with no selection
        time.sleep(0.3)
        self.assertEqual(c.writes, [],
                         f"something other than the human ended the turn: {c.writes}")


class TheRailStaysBounded(unittest.TestCase):
    """Bounds on paths where an agent controls the volume."""

    def test_a_reused_request_id_cannot_defeat_the_pending_bound(self):
        """Repeated request ids cannot grow waiter threads past the bound."""
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
        """An unterminated stderr line raises instead of buffering."""
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
        """Valid non-object JSON (`null`, `3`, `[]`) must not raise."""
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
        """A failed write removes its pending slot."""
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


class ArgvPieceMatchesAcrossInterpreterReexec(unittest.TestCase):
    """The argv check survives macOS framework Python re-exec as `.../Python`."""

    def test_interpreter_matches_by_family_script_by_path(self):
        args = ("/Library/Developer/CommandLineTools/Library/Frameworks/"
                "Python3.framework/Versions/3.9/Resources/Python.app/Contents/"
                "MacOS/Python /opt/corral-light/grok_launcher.py")
        self.assertTrue(acp._argv_piece_present("/usr/bin/python3", args))
        self.assertTrue(acp._argv_piece_present(
            "/opt/corral-light/grok_launcher.py", args))
        self.assertFalse(acp._argv_piece_present(
            "/opt/corral-light/codex_launcher.py", args))

    def test_a_non_interpreter_never_matches_by_family(self):
        self.assertFalse(acp._argv_piece_present("/usr/bin/sleep",
                                                 "/usr/bin/python3 foo.py"))
        self.assertFalse(acp._argv_piece_present("/usr/bin/python3",
                                                 "/usr/bin/sleep 30"))
