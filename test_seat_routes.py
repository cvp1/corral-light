#!/usr/bin/env python3
"""The seat tools' hub routes, over a real socket (DESIGN-5 S8, section 7.8).

  T8.2  refused with no token, an unknown token, and a closed pane's token;
        a `from` in the body is ignored -- the token IS the sender.
  T8.5  the branch runs BEFORE the cookie check and never consults the
        cookie (a paired browser cannot send as a pane; a pane needs no
        cookie); a token from a previous spawn is refused; every OTHER route
        still 401s without the cookie.
  S10   POST /api/peer/broadcast: the token is the source, one answer per seat.

The panes are restored from metas (detached, no agent process), so a delivery
comes back `refused: paused` -- which is the hub answering through
deliver_peer, the thing under test.

    python3 test_seat_routes.py     (also collected by test_corral_light.py)

Corral Light's copy of full Corral's test: the route logic is the core's
(ManagerBase.peer_http), but each hub owns its own pre-cookie branch.
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("CORRAL_LIGHT_STATE", tempfile.mkdtemp(prefix="light-seat-routes-"))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sessions                                                   # noqa: E402

TOKEN_HEADER = "X-Corral-Pane-Token"


def write_meta(root, pid, **kw):
    d = root / "panes" / pid
    d.mkdir(parents=True, exist_ok=True)
    m = {"id": pid, "agent": "claude", "cwd": str(Path.home()), "posture": "auto",
         "title": f"secret title {pid}", "title_locked": True,
         "created": "2026-09-29T10:00:00Z", "closed": False}
    m.update(kw)
    (d / "meta.json").write_text(json.dumps(m), encoding="utf-8")
    (d / "events.jsonl").write_text("", encoding="utf-8")


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import auth
        import hub
        from http.server import ThreadingHTTPServer
        cls.hub = hub
        cls.root = Path(tempfile.mkdtemp(prefix="seat-routes-"))
        (cls.root / "panes").mkdir()
        cls.patches = [mock.patch.object(m, "STATE", cls.root)
                       for m in (sessions, sessions._core)]
        for p in cls.patches:
            p.start()
        write_meta(cls.root, "aaa", seat="author")
        write_meta(cls.root, "bbb", seat="reviewer")
        write_meta(cls.root, "ccc")
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes, m.subscribers, m.not_restored = {}, [], 0
        m._lock = threading.Lock()
        m.catalog, m.mcp, m.orphans = {}, None, {}
        m.restore()
        cls.mgr = m
        cls._old, hub.MGR = hub.MGR, m
        cls.cookie = f"{hub.COOKIE}={auth.mint()}"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.hub.MGR = cls._old
        for p in cls.patches:
            p.stop()

    def req(self, method, path, body=None, token=None, cookie=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {}
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            h.update({"Content-Type": "application/json",
                      "Content-Length": str(len(raw))})
        if token is not None:
            h[TOKEN_HEADER] = token
        if cookie:
            h["Cookie"] = self.cookie
        c.request(method, path, body=raw, headers=h)
        r = c.getresponse()
        out = json.loads(r.read() or b"{}")
        c.close()
        return r.status, out

    def test_no_token_unknown_token_and_a_cookie_is_not_a_token(self):
        for tok, cookie in ((None, False), ("nope", False), (None, True)):
            st, _ = self.req("POST", "/api/peer/send", {"seat": "reviewer", "text": "x"},
                             token=tok, cookie=cookie)
            self.assertEqual(st, 401, (tok, cookie))
            st, _ = self.req("GET", "/api/peer/seats", token=tok, cookie=cookie)
            self.assertEqual(st, 401, (tok, cookie))

    def test_a_token_needs_no_cookie_and_is_the_sender(self):
        tok = self.mgr.mint_pane_token(self.mgr.panes["aaa"])
        st, out = self.req("POST", "/api/peer/send",
                           {"seat": "reviewer", "text": "hi", "from": "bbb",
                            "from_pane": "bbb"}, token=tok)
        self.assertEqual(st, 200, out)
        # Detached target -> the hub answered through deliver_peer, as the
        # pane the TOKEN names (a `from` of bbb would have been refused `self`).
        self.assertEqual((out["result"], out["reason"]), ("refused", "paused"), out)

    def test_seat_list_is_an_address_book_not_a_window(self):
        tok = self.mgr.mint_pane_token(self.mgr.panes["aaa"])
        st, out = self.req("GET", "/api/peer/seats", token=tok)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["you"], "author")
        self.assertEqual([s["seat"] for s in out["seats"]], ["author", "reviewer"])
        self.assertEqual({s["display"] for s in out["seats"]}, {"paused"})
        self.assertNotIn("secret title", json.dumps(out), "seat_list leaked a title")

    def test_a_previous_spawns_token_is_refused(self):
        p = self.mgr.panes["aaa"]
        old = self.mgr.mint_pane_token(p)
        new = self.mgr.mint_pane_token(p)
        self.assertEqual(self.req("GET", "/api/peer/seats", token=old)[0], 401)
        self.assertEqual(self.req("GET", "/api/peer/seats", token=new)[0], 200)

    def test_a_closed_panes_token_is_refused(self):
        p = self.mgr.panes["ccc"]
        tok = self.mgr.mint_pane_token(p)
        self.mgr.panes.pop("ccc")
        try:
            self.assertEqual(self.req("GET", "/api/peer/seats", token=tok)[0], 401)
        finally:
            self.mgr.panes["ccc"] = p

    def test_every_other_route_still_wants_the_cookie(self):
        tok = self.mgr.mint_pane_token(self.mgr.panes["aaa"])
        st, _ = self.req("GET", "/api/state", token=tok)
        self.assertEqual(st, 401, "a pane token opened a cookie-only route")
        st, _ = self.req("POST", "/api/session/send", {"pane": "bbb", "text": "x"},
                         token=tok)
        self.assertEqual(st, 401, "a pane token reached the HUMAN send path")

    @unittest.skipUnless(sys.platform.startswith("linux"), "the uid check is Linux-only")
    def test_another_unix_user_or_an_unknown_caller_is_refused(self):
        """A token alone is not enough: the Claude adapter puts it on a
        world-readable command line, so the kernel's record of who opened
        the calling socket decides (measured live, S9)."""
        tok = self.mgr.mint_pane_token(self.mgr.panes["aaa"])
        from corral_core import edge
        for fake, why in ((os.getuid() + 1, "another user"), (None, "unknown")):
            with mock.patch.object(edge, "local_peer_uid", lambda *a, **k: fake):
                st, out = self.req("GET", "/api/peer/seats", token=tok)
            self.assertEqual(st, 403, (why, out))
            self.assertIn("UNIX user", out.get("error", ""))
        self.assertEqual(self.req("GET", "/api/peer/seats", token=tok)[0], 200,
                         "the real check refused this test's own user")

    def test_broadcast_is_the_token_holders_and_answers_per_seat(self):
        """S10 over the wire: the token is the source (so it is left out),
        the cookie is not enough, and the answer is a list, one per seat."""
        st, _ = self.req("POST", "/api/peer/broadcast", {"text": "x"}, cookie=True)
        self.assertEqual(st, 401, "a cookie alone reached the broadcast route")
        tok = self.mgr.mint_pane_token(self.mgr.panes["aaa"])
        st, out = self.req("POST", "/api/peer/broadcast",
                           {"text": "all hands", "from": "bbb"}, token=tok)
        self.assertEqual(st, 200, out)
        self.assertEqual([(r["to_seat"], r["result"], r["reason"])
                          for r in out["results"]],
                         [("reviewer", "refused", "paused")])
        self.assertEqual((out["delivered"], out["refused"]), (0, 1))

    def test_an_unknown_peer_route_is_404_not_a_fallthrough(self):
        tok = self.mgr.mint_pane_token(self.mgr.panes["aaa"])
        self.assertEqual(self.req("GET", "/api/peer/state", token=tok)[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
