#!/usr/bin/env python3
"""The seat tools' hub routes, over a real socket.

The token, never the cookie or a body `from`, identifies the sending pane; the
panes are restored from metas (detached), so deliveries come back
`refused: paused` through deliver_peer, the thing under test.

    python3 test_seat_routes.py     (also collected by test_corral_light.py)
"""
import http.client
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("light-seat-routes-")
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
        cls.root = Path(tmpdir(cls, "seat-routes-"))
        (cls.root / "panes").mkdir()
        cls.patches = [mock.patch.object(m, "STATE", cls.root)
                       for m in (sessions, sessions._core)]
        for p in cls.patches:
            p.start()
        # Distinct `created`, so the saved seat order does not depend on the disk.
        write_meta(cls.root, "aaa", seat="author", created="2026-09-29T10:00:00Z")
        write_meta(cls.root, "bbb", seat="reviewer", created="2026-09-29T10:00:01Z")
        write_meta(cls.root, "ccc", created="2026-09-29T10:00:02Z")
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

    def test_state_names_the_checkout_and_commit_the_hub_serves(self):
        st, out = self.req("GET", "/api/state", cookie=True)
        self.assertEqual(st, 200)
        self.assertEqual(out["hub"]["root"], str(self.hub.ROOT))
        self.assertEqual(out["hub"]["commit"], self.hub.BOOT_COMMIT)
        st, out = self.req("GET", "/health")
        self.assertNotIn("hub", out, "the unauthenticated probe stays counts-only")

    def test_rig_routes_are_behind_the_pairing_cookie(self):
        """401 before routing without the cookie; paired, a refused rig starts
        nothing and says why, and `save` writes the seated panes.
        """
        tok = self.mgr.mint_pane_token(self.mgr.panes["aaa"])
        for token in (None, tok):
            self.assertEqual(self.req("GET", "/api/session/rigs", token=token)[0], 401)
            for verb in ("save", "up", "rm"):
                st, _ = self.req("POST", f"/api/session/rigs/{verb}", {"name": "x"}, token=token)
                self.assertEqual(st, 401, (verb, token))
        st, out = self.req("POST", "/api/session/rigs/up", {"name": "nosuch"}, cookie=True)
        self.assertEqual(st, 400, out)
        self.assertIn("no rig 'nosuch'", out["refused"][0])
        st, out = self.req("POST", "/api/session/rigs/save", {"name": "wall"}, cookie=True)
        self.assertEqual((st, out.get("seats")), (200, ["author", "reviewer"]), out)
        st, out = self.req("GET", "/api/session/rigs", cookie=True)
        self.assertEqual(out["rigs"], [{"name": "wall", "seats": ["author", "reviewer"]}])
        st, out = self.req("POST", "/api/session/rigs/rm", {"name": "wall"}, cookie=True)
        self.assertEqual((st, out.get("removed")), (200, "wall"), out)

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
        """A token alone is not enough (it sits on a world-readable command line);
        the kernel's record of who opened the socket decides.
        """
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
        """The token is the source, the cookie is not enough, and the answer is a
        list, one per seat.
        """
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

    def test_the_turn_route_reads_the_query_and_answers_only_the_sender(self):
        from urllib.parse import urlencode
        b = self.mgr.panes["bbb"]
        b.emit("peer", {"from_pane": "aaa", "from_seat": "author",
                        "to_seat": "reviewer", "turn": "s11turn00001", "hop": 1,
                        "nonce": "n", "text": "x"}, activity=False)
        path = "/api/peer/turn?" + urlencode({"seat": "reviewer",
                                               "turn": "s11turn00001"})
        st, _ = self.req("GET", path, cookie=True)
        self.assertEqual(st, 401, "a cookie alone reached the turn route")
        tok = self.mgr.mint_pane_token(self.mgr.panes["aaa"])
        st, out = self.req("GET", path, token=tok)
        self.assertEqual((st, out.get("result"), out.get("ended"), out.get("display")),
                         (200, "turn", False, "paused"), out)
        b.emit("turn_end", {"stopReason": "end_turn", "turn": "another0001"},
               activity=False)
        self.assertFalse(self.req("GET", path, token=tok)[1]["ended"])
        b.emit("turn_end", {"stopReason": "end_turn", "turn": "s11turn00001"},
               activity=False)
        st, out = self.req("GET", path, token=tok)
        self.assertEqual((out["ended"], out["stop_reason"]), (True, "end_turn"))
        self.assertNotIn("text", out)
        # The same turn id, asked by a pane that did not send it, is unknown.
        other = self.mgr.mint_pane_token(self.mgr.panes["ccc"])
        st, out = self.req("GET", path, token=other)
        self.assertEqual((st, out["result"], out["reason"]),
                         (200, "refused", "unknown-turn"))
        # No query at all is an unknown seat, not a crash.
        st, out = self.req("GET", "/api/peer/turn", token=tok)
        self.assertEqual((st, out["reason"]), (200, "unknown-seat"))

    def test_an_unknown_peer_route_is_404_not_a_fallthrough(self):
        tok = self.mgr.mint_pane_token(self.mgr.panes["aaa"])
        self.assertEqual(self.req("GET", "/api/peer/state", token=tok)[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
