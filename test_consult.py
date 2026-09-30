#!/usr/bin/python3
"""consult.py offline tests — the answer collector against a scripted hub.

No hub, no lane, no network: `Hub` is replaced by a stub that serves a
scripted sequence of /api/state deltas and records every POST. What is under
test is the part that decides whether an answer IS an answer (turn_end after
OUR user event), whether a stall is a stall, and whether a timeout cancels
through the hub rather than pretending.  Run: python3 test_consult.py
"""
import json
import sys
import time
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import consult  # noqa: E402


class StubHub:
    """Serves /api/state from a queue of pane snapshots; the last one repeats."""

    def __init__(self, frames, pane="P1"):
        self.frames = list(frames)
        self.pane = pane
        self.posts = []

    def get(self, path, timeout=None):
        since = {}
        if "since=" in path:
            since = json.loads(urllib.parse.unquote(path.split("since=", 1)[1]))
        frame = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        if frame is None:
            return {"panes": []}
        floor = since.get(self.pane, 0)
        events = [e for e in frame.get("events", []) if e["seq"] > floor]
        return {"panes": [dict(frame, id=self.pane, events=events,
                               seq=max([e["seq"] for e in frame.get("events", [])] or [0]))]}

    def post(self, path, body, timeout=None):
        self.posts.append((path, body))
        return {"ok": True}


def ev(seq, kind, **data):
    return {"seq": seq, "kind": kind, "data": data}


class WaitTurn(unittest.TestCase):
    def setUp(self):
        consult.POLL_S = 0            # the stub has no latency to wait out

    def test_collects_text_until_turn_end(self):
        hub = StubHub([
            {"state": "busy", "events": [ev(1, "user", text="q"), ev(2, "text", text="Hel")]},
            {"state": "busy", "events": [ev(1, "user", text="q"), ev(2, "text", text="Hel"),
                                          ev(3, "thought", text="…"), ev(4, "text", text="lo")]},
            {"state": "ready", "events": [ev(1, "user", text="q"), ev(2, "text", text="Hel"),
                                           ev(3, "thought", text="…"), ev(4, "text", text="lo"),
                                           ev(5, "turn_end", stopReason="end_turn")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10)
        self.assertTrue(r["complete"])
        self.assertEqual(r["text"], "Hello")
        self.assertEqual(r["stop_reason"], "end_turn")
        self.assertFalse(r["timed_out"])
        self.assertEqual(hub.posts, [])          # nothing cancelled

    def test_text_before_our_user_event_is_not_ours(self):
        # seq0 = 3: an older answer (seq 1-2) must not leak into this one.
        hub = StubHub([
            {"state": "ready", "events": [ev(1, "user", text="old"), ev(2, "text", text="OLD"),
                                           ev(3, "turn_end"), ev(4, "user", text="q"),
                                           ev(5, "text", text="NEW"), ev(6, "turn_end")]},
        ])
        r = consult.wait_turn(hub, "P1", 3, timeout_s=10)
        self.assertTrue(r["complete"])
        self.assertEqual(r["text"], "NEW")

    def test_timeout_cancels_through_the_hub_and_says_incomplete(self):
        hub = StubHub([
            {"state": "busy", "events": [ev(1, "user", text="q"), ev(2, "text", text="partial")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=0)
        self.assertFalse(r["complete"])
        self.assertTrue(r["timed_out"])
        self.assertTrue(r["cancelled"])
        self.assertEqual(hub.posts, [("/api/session/cancel", {"pane": "P1"})])
        self.assertEqual(r["text"], "partial")   # returned, but marked incomplete

    def test_dead_pane_is_a_failure_not_an_answer(self):
        hub = StubHub([
            {"state": "dead", "error": "adapter exited 1",
             "events": [ev(1, "user", text="q"), ev(2, "text", text="Some text")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10)
        self.assertFalse(r["complete"])
        self.assertIn("died", r["why"])
        self.assertFalse(r["timed_out"])

    def test_vanished_pane_is_reported(self):
        hub = StubHub([None])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10)
        self.assertFalse(r["complete"])
        self.assertIn("vanished", r["why"])

    def test_a_second_user_prompt_ends_attribution(self):
        # the operator typed into the pane mid-turn: what follows is not our answer.
        hub = StubHub([
            {"state": "busy", "events": [ev(1, "user", text="q"), ev(2, "text", text="A"),
                                          ev(3, "user", text="operator"), ev(4, "text", text="B"),
                                          ev(5, "turn_end")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10)
        self.assertFalse(r["complete"])
        self.assertEqual(r["text"], "A")
        self.assertIn("another prompt", r["why"])

    def test_needs_you_is_timed_not_fatal(self):
        hub = StubHub([
            {"state": "needs-you", "pending": ["r1"],
             "events": [ev(1, "user", text="q")]},
            {"state": "ready", "events": [ev(1, "user", text="q"),
                                           ev(2, "text", text="x" * 10), ev(3, "turn_end")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10)
        self.assertTrue(r["complete"])
        self.assertGreaterEqual(r["needs_you_s"], 0.0)

    def test_text_is_bounded(self):
        old = consult.MAX_TEXT
        consult.MAX_TEXT = 5
        try:
            hub = StubHub([
                {"state": "ready", "events": [ev(1, "user", text="q"),
                                               ev(2, "text", text="abcdefgh"), ev(3, "turn_end")]},
            ])
            r = consult.wait_turn(hub, "P1", 0, timeout_s=10)
            self.assertEqual(r["text"], "abcde")
            self.assertTrue(r["truncated"])
        finally:
            consult.MAX_TEXT = old


class Attribution(unittest.TestCase):
    def setUp(self):
        consult.POLL_S = 0

    def test_craigs_prompt_between_read_and_send_is_not_our_answer(self):
        # seq read = 0; the operator's prompt lands as seq 1 with its answer, ours is seq 4.
        hub = StubHub([
            {"state": "ready", "events": [ev(1, "user", text="operator's question"),
                                           ev(2, "text", text="CRAIG ANSWER"), ev(3, "turn_end"),
                                           ev(4, "user", text="our prompt"),
                                           ev(5, "text", text="OUR ANSWER"), ev(6, "turn_end")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10, own="our prompt")
        self.assertTrue(r["complete"])
        self.assertEqual(r["text"], "OUR ANSWER")

    def test_crossfeed_prompt_matches_on_preamble_prefix(self):
        pre = "Round two. Attack the rivals below on their stated reasons, not their tone."
        hub = StubHub([
            {"state": "ready", "events": [ev(1, "user", text=pre + "\n\nFrom Grok:\n\"\"\"…\"\"\""),
                                           ev(2, "text", text="R2"), ev(3, "turn_end")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10, own=pre, prefix=True)
        self.assertTrue(r["complete"])
        self.assertEqual(r["text"], "R2")

    def test_hub_outage_mid_turn_cancels_and_reports(self):
        class Dying(StubHub):
            def get(self, path, timeout=None):
                raise consult.ConsultError("corral hub unreachable")
        hub = Dying([])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10, own="q")
        self.assertFalse(r["complete"])
        self.assertIn("stopped answering", r["why"])
        self.assertEqual(hub.posts, [("/api/session/cancel", {"pane": "P1"})])

    def test_cancelled_event_ends_the_wait_incomplete(self):
        hub = StubHub([
            {"state": "ready", "events": [ev(1, "user", text="q"), ev(2, "text", text="par"),
                                           ev(3, "cancelled")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10, own="q")
        self.assertFalse(r["complete"])
        self.assertIn("cancelled", r["why"])


class HubEventOrder(unittest.TestCase):
    """The hub's REAL sequences (Grok 4.6, panel review 2026-09-03)."""

    def setUp(self):
        consult.POLL_S = 0

    def test_cancel_then_turn_end_is_not_an_answer(self):
        # sessions.Pane.cancel emits `cancelled`; _drain then emits turn_end.
        hub = StubHub([
            {"state": "ready", "events": [ev(1, "user", text="q"),
                                           ev(2, "text", text="partial answer that looks done"),
                                           ev(3, "cancelled"),
                                           ev(4, "turn_end", stopReason="cancelled")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10, own="q")
        self.assertFalse(r["complete"])
        self.assertIn("cancelled", r["why"])

    def test_turn_end_with_cancel_reason_alone_is_not_an_answer(self):
        hub = StubHub([
            {"state": "ready", "events": [ev(1, "user", text="q"), ev(2, "text", text="x" * 300),
                                           ev(3, "turn_end", stopReason="cancelled")]},
        ])
        r = consult.wait_turn(hub, "P1", 0, timeout_s=10, own="q")
        self.assertFalse(r["complete"])

    def test_send_waits_for_a_busy_pane_before_sending(self):
        # Turn A still draining; our send must not be enqueued behind it.
        class Recording(StubHub):
            def __init__(self, frames):
                super().__init__(frames)
                self.sent_at_frame = None
            def post(self, path, body, timeout=None):
                if path == "/api/session/send":
                    self.sent_at_frame = len(self.frames)
                return super().post(path, body, timeout)
        hub = Recording([
            {"state": "busy", "events": [ev(10, "user", text="A"), ev(11, "text", text="a1")]},
            {"state": "busy", "events": [ev(10, "user", text="A"), ev(11, "text", text="a1"),
                                          ev(12, "text", text=" — the answer is 4.")]},
            {"state": "ready", "events": [ev(10, "user", text="A"), ev(11, "text", text="a1"),
                                           ev(12, "text", text=" — the answer is 4."),
                                           ev(13, "turn_end")]},
            {"state": "ready", "events": [ev(10, "user", text="A"), ev(11, "text", text="a1"),
                                           ev(12, "text", text=" — the answer is 4."),
                                           ev(13, "turn_end"), ev(14, "user", text="write a sonnet"),
                                           ev(15, "text", text="Shall I compare thee"),
                                           ev(16, "turn_end")]},
        ])
        r = consult.send_and_wait(hub, "P1", "write a sonnet", timeout_s=10)
        self.assertTrue(r["complete"])
        self.assertEqual(r["text"], "Shall I compare thee")
        self.assertEqual(hub.posts[0][0], "/api/session/send")
        self.assertEqual(hub.sent_at_frame, 1)        # sent only once `ready` was seen

    def test_send_refuses_a_pane_that_never_frees_up(self):
        hub = StubHub([{"state": "busy", "events": [ev(1, "user", text="A")]}])
        with self.assertRaises(consult.ConsultError):
            consult.send_and_wait(hub, "P1", "q", timeout_s=0)
        self.assertEqual(hub.posts, [])              # nothing was sent

    def test_rejected_config_closes_the_pane_and_refuses(self):
        class Lane(StubHub):
            def get(self, path, timeout=None):
                return {"agents": [{"key": "codex", "available": True, "label": "ChatGPT"}]}
            def post(self, path, body, timeout=None):
                self.posts.append((path, body))
                if path == "/api/session/new":
                    return {"pane": {"id": "P9"}}
                if path == "/api/session/config":
                    raise consult.ConsultError("POST /api/session/config -> 400: unknown configId")
                return {"ok": True}
        hub = Lane([])
        with self.assertRaises(consult.ConsultError):
            consult.open_pane(hub, "gpt", "/tmp", config=["mode=read-only"])
        self.assertIn(("/api/session/close", {"pane": "P9"}), hub.posts)
        self.assertFalse(any(p == "/api/session/send" for p, _ in hub.posts))


class CookieScope(unittest.TestCase):
    def test_cached_cookie_only_goes_to_its_own_hub(self):
        import tempfile
        old = consult.CFG
        try:
            with tempfile.TemporaryDirectory() as d:
                consult.CFG = Path(d) / "s.json"
                consult._save_token("tok", "http://127.0.0.1:8098")
                self.assertEqual(consult._load_token("http://127.0.0.1:8098"), "tok")
                self.assertIsNone(consult._load_token("http://127.0.0.1:8123"))
                self.assertIsNone(consult._load_token("http://evil.example:8098"))
                # A TUI-written file (no url) belongs to the default hub only.
                consult.CFG.write_text(json.dumps({"token": "t2", "exp": time.time() + 99}))
                self.assertEqual(consult._load_token(consult.DEFAULT_URL), "t2")
                self.assertIsNone(consult._load_token("http://127.0.0.1:8123"))
        finally:
            consult.CFG = old


class PairHub:
    """/api/pair/new and /api/pair/claim only; counts every claim poll."""

    def __init__(self, code, claim_ok_after=None, url="http://127.0.0.1:18231",
                 host=None):
        self.code, self.claims, self.claim_ok_after = code, 0, claim_ok_after
        self.host_tag = host
        self.url = url
        self.token = None

    def _do(self, method, path, body=None, timeout=None):
        class R:
            def getheader(self, name):
                return f"{consult.COOKIE_NAME}=tok-ok; Path=/"
        if path == "/api/pair/new":
            obj = {"code": self.code}
            if self.host_tag is not None:
                obj["host"] = self.host_tag
            return 200, R(), obj
        self.claims += 1
        if self.claim_ok_after is not None and self.claims >= self.claim_ok_after:
            return 200, R(), {"status": "ok"}
        return 200, R(), {"status": "pending"}


class PairingAgainstAnotherStore(unittest.TestCase):
    """A private hub driven from a shell without its scratch CORRAL_LIGHT_STATE: the
    client approves the hub's fresh code against the WRONG pairing store.
    It used to wait out all of PAIR_WAIT_S (the S12 live 'hang'); it must fail
    at once, naming the cause and the exact `corral-light pair CODE` line — while the
    genuine wait-for-a-human case keeps its wait."""

    def setUp(self):
        import tempfile
        import auth
        self.auth = auth
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.saved = {k: getattr(auth, k) for k in ("STATE", "LOCKFILE", "PAIRFILE", "KEYFILE")}
        auth.STATE, auth.LOCKFILE = d / "state", d / "state" / "pair.lock"
        auth.PAIRFILE, auth.KEYFILE = d / "state" / "pairing.json", d / "state" / "session.key"
        self.cfg, consult.CFG = consult.CFG, d / "tui.json"
        self.wait, consult.PAIR_WAIT_S = consult.PAIR_WAIT_S, 3

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(self.auth, k, v)
        consult.CFG, consult.PAIR_WAIT_S = self.cfg, self.wait
        self.tmp.cleanup()

    def test_a_code_our_store_never_saw_fails_fast_and_says_why(self):
        # This host's hub, a store this shell does not read.
        hub = PairHub("ABC-DEF", host=self.auth.host_id())
        t = time.monotonic()
        with self.assertRaises(consult.ConsultError) as cm:
            consult.pair(hub)
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertEqual(hub.claims, 0, "nothing can approve it, so nothing polls")
        msg = str(cm.exception)
        self.assertIn("corral-light pair ABC-DEF", msg)
        self.assertIn("CORRAL_LIGHT_STATE", msg)
        self.assertIn(str(self.auth.STATE), msg)
        self.assertIsNone(hub.token)

    def test_the_shared_store_still_pairs(self):
        code, _ttl = self.auth.new_code()       # the hub and this client share a store
        hub = PairHub(code, claim_ok_after=1)
        consult.pair(hub)
        self.assertEqual(hub.token, "tok-ok")

    def test_a_tunnelled_hub_on_another_host_still_waits(self):
        # ssh -L to another host: the URL is 127.0.0.1, the store is over
        # there, this client's own store never saw the code, and a human
        # there approves it. Real local approval, separate stores: it polls.
        hub = PairHub("ABC-DEF", claim_ok_after=2, url="http://127.0.0.1:8098",
                      host="0123456789abcdef")
        consult.pair(hub)
        self.assertEqual(hub.claims, 2)
        self.assertEqual(hub.token, "tok-ok")
        self.assertEqual(consult._load_token(hub.url), "tok-ok")

    def test_a_hub_too_old_to_name_its_host_still_waits(self):
        hub = PairHub("ABC-DEF", claim_ok_after=2)          # no "host" field
        consult.pair(hub)
        self.assertEqual(hub.claims, 2)
        self.assertEqual(hub.token, "tok-ok")

    def test_the_host_tag_is_stable_and_names_nothing(self):
        import socket
        tag = self.auth.host_id()
        self.assertEqual(tag, self.auth.host_id())
        self.assertRegex(tag, r"^[0-9a-f]{16}$")
        self.assertNotIn(socket.gethostname().lower(), tag)

    def test_a_genuine_wait_for_a_human_still_waits(self):
        # Not this account (or auth would not import): a human elsewhere can
        # still approve, so the claim poll keeps its PAIR_WAIT_S.
        real = consult._approve_locally
        consult._approve_locally = lambda code: (False, "PermissionError: not yours", None)
        try:
            hub = PairHub("ABC-DEF", claim_ok_after=2)
            consult.pair(hub)
        finally:
            consult._approve_locally = real
        self.assertEqual(hub.claims, 2)
        self.assertEqual(hub.token, "tok-ok")


class HubErrors(unittest.TestCase):
    def test_http_exception_is_a_clean_consult_error(self):
        import http.client
        hub = consult.Hub("http://127.0.0.1:1")
        class Conn:
            def __init__(self, *a, **k): pass
            def request(self, *a, **k): raise http.client.IncompleteRead(b"")
            def close(self): pass
        hub._conn = lambda timeout=None: Conn()
        with self.assertRaises(consult.ConsultError) as cm:
            hub.get("/api/state")
        self.assertIn("IncompleteRead", str(cm.exception))

    def test_token_file_is_born_private(self):
        import os, stat, tempfile
        old = consult.CFG
        try:
            with tempfile.TemporaryDirectory() as d:
                consult.CFG = Path(d) / "s.json"
                consult._save_token("tok", consult.DEFAULT_URL)
                mode = stat.S_IMODE(os.stat(consult.CFG).st_mode)
                self.assertEqual(mode, 0o600)
                self.assertFalse((Path(d) / "s.json.tmp").exists())
        finally:
            consult.CFG = old


class LaneNames(unittest.TestCase):
    def test_panel_arm_names_map_to_lanes(self):
        self.assertEqual(consult.lane_key("gpt"), "codex")
        self.assertEqual(consult.lane_key("GEMINI"), "gemini")
        self.assertEqual(consult.lane_key("grok"), "grok")
        self.assertEqual(consult.lane_key("delegate:foo"), "delegate:foo")


class AnUnsetPostureIsNotAClaim(unittest.TestCase):
    """DESIGN-5 S2. `open_pane` used to default `posture="strict"`, so every
    scripted arm -- every panel run, every eval pass -- wrote `strict` into the
    pane's meta.json, including on the lanes where nothing can impose it. 244
    of 446 panes on this fleet carry that annotation. No historic meta is
    rewritten (a stored fact stays a fact); the point is that no NEW pane gets
    an unearned one.
    """

    LANES = [{"key": "grok", "label": "Grok", "available": True,
              "postureEnforced": False}]

    class Lane(StubHub):
        def get(self, path, timeout=None):
            st = super().get(path, timeout=timeout)
            st["agents"] = AnUnsetPostureIsNotAClaim.LANES
            return st

        def post(self, path, body, timeout=None):
            self.posts.append((path, body))
            if path == "/api/session/new":
                return {"pane": {"id": "P1", "agent": body["agent"]}}
            return {"ok": True}

    def _new_body(self, **kw):
        hub = self.Lane([{"state": "ready", "events": []}])
        consult.open_pane(hub, "grok", "/tmp", **kw)
        return next(b for p, b in hub.posts if p == "/api/session/new")

    def test_no_posture_means_no_posture_key(self):
        self.assertNotIn("posture", self._new_body(),
                         "an unset posture must post NO key, not an empty "
                         "string and not a default the hub never chose")

    def test_an_explicit_posture_is_still_posted(self):
        self.assertEqual(self._new_body(posture="strict")["posture"], "strict")

    def test_the_cli_no_longer_defaults_to_strict(self):
        """The default lived in TWO places -- the function signature and the
        argparse flag -- and fixing only one would leave every command-line
        caller posting `strict` exactly as before."""
        for verb in ("ask", "fanout"):
            args = consult.build_parser().parse_args(
                [verb, "--lane", "grok", "--prompt", "x"])
            self.assertIsNone(args.posture,
                              f"consult {verb} still defaults --posture")


class AScriptedTurnSaysSo(unittest.TestCase):
    """DESIGN-5 S5, T5.3: a turn this script sends is marked `via: consult`
    in the transcript, so it never reads as the human typing. Client-declared
    -- the hub ignores the key on an older build, which is why it is safe to
    send to either."""

    def test_send_and_wait_declares_consult(self):
        hub = StubHub([
            {"state": "ready", "events": []},
            {"state": "ready", "events": [ev(1, "user", text="q"),
                                          ev(2, "text", text="a"), ev(3, "turn_end")]},
        ])
        consult.send_and_wait(hub, "P1", "q", timeout_s=10)
        path, body = next((p, b) for p, b in hub.posts if p == "/api/session/send")
        self.assertEqual(body.get("via"), "consult", body)


if __name__ == "__main__":
    unittest.main(verbosity=1)
