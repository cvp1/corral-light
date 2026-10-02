#!/usr/bin/env python3
"""A pane can have a name (a seat).

restore(), reopen(), from_meta() and snapshot() are forked per skin, so Light
proves its own. Panes are built from meta.json files in a scratch state dir and
restored as the hub does at boot; no agent process is started.

    python3 test_seats.py        (also collected by test_corral_light.py)
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

os.environ.setdefault("CORRAL_LIGHT_STATE", tempfile.mkdtemp(prefix="corral-light-seats-"))

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sessions                                                   # noqa: E402


def write_meta(root, pid, created="2026-09-29T10:00:00Z", **kw):
    d = root / "panes" / pid
    d.mkdir(parents=True, exist_ok=True)
    m = {"id": pid, "agent": "claude", "cwd": str(Path.home()), "posture": "auto",
         "title": pid, "title_locked": True, "created": created, "closed": False}
    m.update(kw)
    (d / "meta.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    (d / "events.jsonl").write_text("", encoding="utf-8")
    return d / "meta.json"


def disk_meta(root, pid):
    return json.loads((root / "panes" / pid / "meta.json").read_text(encoding="utf-8"))


class SeatCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="seat-state-"))
        (self.root / "panes").mkdir()
        # Both globals: the skin builds pane dirs from its own STATE, the core
        # scans open metas from the core's.
        for mod in (sessions, sessions._core):
            p = mock.patch.object(mod, "STATE", self.root)
            p.start()
            self.addCleanup(p.stop)
        self.mgr = self.fresh()

    def fresh(self):
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes, m.subscribers, m.not_restored = {}, [], 0
        m._lock = threading.Lock()
        m.catalog = {}
        m.mcp = None
        m.orphans = {}
        return m

    def restore(self, mgr=None):
        mgr = mgr or self.mgr
        mgr.restore()
        return mgr


class ItRoundTrips(SeatCase):
    def test_bind_survives_a_restart_and_unbind_clears_it(self):
        """Through save_meta and from_meta; a pre-seat meta loads None, not KeyError."""
        write_meta(self.root, "p1")                       # no `seat` key at all
        self.restore()
        self.assertIsNone(self.mgr.panes["p1"].seat)
        self.mgr.bind_seat("p1", "reviewer")
        self.assertEqual(disk_meta(self.root, "p1")["seat"], "reviewer")
        again = self.restore(self.fresh())
        self.assertEqual(again.panes["p1"].seat, "reviewer")
        self.assertIs(again.seat("reviewer"), again.panes["p1"])
        snap = again.panes["p1"].snapshot()
        self.assertEqual((snap["seat"], snap["seatWithheld"]), ("reviewer", None))
        again.bind_seat("p1", "")
        self.assertIsNone(disk_meta(self.root, "p1")["seat"])
        self.assertIsNone(again.seat("reviewer"))

    def test_binding_emits_an_event_that_does_not_count_as_activity(self):
        write_meta(self.root, "p1")
        self.restore()
        p = self.mgr.panes["p1"]
        p.last_activity = 1000.0
        self.mgr.bind_seat("p1", "author")
        self.assertEqual(p.events[-1]["kind"], "seat")
        self.assertEqual(p.events[-1]["data"], {"seat": "author"})
        self.assertEqual(p.last_activity, 1000.0,
                         "naming a pane reset its idle clock")

    def test_a_bad_name_is_refused_and_nothing_is_written(self):
        write_meta(self.root, "p1")
        self.restore()
        before = (self.root / "panes" / "p1" / "meta.json").read_bytes()
        with self.assertRaises(ValueError) as e:
            self.mgr.bind_seat("p1", "Reviewer")
        self.assertIn("lowercase letter", str(e.exception))
        self.assertEqual((self.root / "panes" / "p1" / "meta.json").read_bytes(), before)


class OneOpenPanePerName(SeatCase):
    def test_a_second_live_pane_cannot_take_a_held_name(self):
        """Refused, and the refusal names the holder."""
        write_meta(self.root, "aaa", title="the author")
        write_meta(self.root, "bbb")
        self.restore()
        self.mgr.bind_seat("aaa", "author")
        with self.assertRaises(ValueError) as e:
            self.mgr.bind_seat("bbb", "author")
        self.assertIn("aaa", str(e.exception))
        self.assertIn("the author", str(e.exception))
        self.assertIsNone(self.mgr.panes["bbb"].seat)

    def test_rebinding_your_own_name_is_not_a_collision(self):
        write_meta(self.root, "aaa")
        self.restore()
        self.mgr.bind_seat("aaa", "author")
        self.mgr.bind_seat("aaa", "author")
        self.assertEqual(self.mgr.panes["aaa"].seat, "author")

    def test_two_metas_on_disk_the_later_is_withheld_and_nothing_is_rewritten(self):
        """The earlier-created keeps the name; the later keeps it in its meta but
        is not addressable by it and says so.
        """
        first = write_meta(self.root, "first", created="2026-09-29T09:00:00Z",
                           seat="reviewer")
        later = write_meta(self.root, "later", created="2026-09-29T12:00:00Z",
                           seat="reviewer")
        b1, b2 = first.read_bytes(), later.read_bytes()
        self.restore()
        self.assertIs(self.mgr.seat("reviewer"), self.mgr.panes["first"])
        lp = self.mgr.panes["later"]
        self.assertTrue(lp.seat_withheld)
        self.assertEqual(lp.seat, "reviewer", "the attribute is kept")
        snap = lp.snapshot()
        self.assertEqual((snap["seat"], snap["seatWithheld"]), (None, "reviewer"))
        self.assertTrue(any(e["kind"] == "note" and "withheld" in e["data"]["text"]
                            for e in lp.events), "the withheld pane does not say so")
        self.assertEqual((first.read_bytes(), later.read_bytes()), (b1, b2),
                         "restore rewrote a meta to resolve a collision")

    def test_only_a_human_rebind_clears_a_withhold(self):
        write_meta(self.root, "first", created="1", seat="x")
        write_meta(self.root, "later", created="2", seat="x")
        self.restore()
        with self.assertRaises(ValueError):
            self.mgr.bind_seat("later", "x")            # still held by `first`
        self.mgr.bind_seat("later", "y")
        self.assertFalse(self.mgr.panes["later"].seat_withheld)
        self.assertIs(self.mgr.seat("y"), self.mgr.panes["later"])

    def test_closing_a_pane_frees_its_name(self):
        """`closed: true` on disk holds nothing, nor does a pane closed through the
        manager.
        """
        write_meta(self.root, "gone", seat="x", closed=True)
        write_meta(self.root, "live1")
        write_meta(self.root, "live2")
        self.restore()
        self.mgr.bind_seat("live1", "x")                # the closed meta holds nothing
        self.mgr.close("live1")
        self.assertTrue(disk_meta(self.root, "live1")["closed"])
        self.mgr.bind_seat("live2", "x")
        self.assertIs(self.mgr.seat("x"), self.mgr.panes["live2"])



class ACloseIsAnnouncedAfterTheRosterDropsIt(SeatCase):
    """A pane is off the roster before `closed` goes out, or a CLI/peer close
    leaves a ghost row in every open tab.
    """

    class Client:
        """Emits `closed` from inside close(), as the adapter's exit path does."""
        def __init__(self, pane, fail=False):
            self.pane, self.fail, self.alive = pane, fail, True

        def close(self):
            if self.fail:
                raise OSError("adapter would not stop")
            self.alive = False
            self.pane.emit("closed", {"reason": "closed by you"})

    class Recorder:
        """A broadcast subscriber that notes the roster at delivery time."""
        def __init__(self, mgr):
            self.mgr, self.seen = mgr, []

        def put_nowait(self, ev):
            self.seen.append((ev.get("kind"), ev.get("pane"),
                              ev.get("pane") in self.mgr.panes))

    def test_closed_goes_out_with_the_pane_already_gone(self):
        write_meta(self.root, "p1")
        self.restore()
        pane = self.mgr.panes["p1"]
        pane.client = self.Client(pane)
        rec = self.Recorder(self.mgr)
        self.mgr.subscribe(rec)
        self.mgr.close("p1")
        closed = [r for r in rec.seen if r[0] == "closed"]
        self.assertEqual(closed, [("closed", "p1", False)],
                         f"`closed` went out while the roster still held the pane: {rec.seen}")
        self.assertNotIn("p1", self.mgr.panes)
        self.assertTrue(disk_meta(self.root, "p1")["closed"])

    def test_a_stop_that_fails_leaves_the_pane_visible(self):
        """Refusal side: a pane whose client would not stop is still running,
        so it must stay on the roster -- and was not marked closed on disk."""
        write_meta(self.root, "p1")
        self.restore()
        pane = self.mgr.panes["p1"]
        pane.client = self.Client(pane, fail=True)
        with self.assertRaises(OSError):
            self.mgr.close("p1")
        self.assertIs(self.mgr.panes.get("p1"), pane)
        self.assertFalse(disk_meta(self.root, "p1")["closed"])

class TheWholeStateDirIsTheNamespace(SeatCase):
    def test_bind_sees_a_meta_that_was_not_restored(self):
        """A meta that was not restored still holds its seat."""
        write_meta(self.root, "live")
        self.restore()
        write_meta(self.root, "ondisk", seat="reviewer")   # open, never restored
        with self.assertRaises(ValueError) as e:
            self.mgr.bind_seat("live", "reviewer")
        self.assertIn("ondisk", str(e.exception))
        self.assertIn("not open here", str(e.exception))

    def test_reopen_does_not_take_a_seat_an_open_pane_is_using(self):
        """The open pane keeps the seat even though the archived one was created
        first: a peer is addressing it now.
        """
        write_meta(self.root, "archived", created="1", seat="x", closed=True)
        write_meta(self.root, "open", created="2", seat="x")
        self.restore()
        self.assertIs(self.mgr.seat("x"), self.mgr.panes["open"])
        p = self.mgr.reopen("archived")
        self.assertTrue(p.seat_withheld)
        self.assertIs(self.mgr.seat("x"), self.mgr.panes["open"])


class ASeatDoesNotTravel(SeatCase):
    def test_export_drops_it_and_import_lands_unaddressable(self):
        """A seat is an address on this host's wall."""
        import port
        write_meta(self.root, "src", seat="reviewer")
        bundle = port.export("src", state_dir=self.root)
        self.assertNotIn("seat", bundle["meta"])
        new_id = port.import_bundle(bundle, state_dir=self.root)
        self.assertIsNone(disk_meta(self.root, new_id)["seat"])
        bundle["meta"]["seat"] = "smuggled"                # a hand-edited bundle
        new_id = port.import_bundle(bundle, state_dir=self.root)
        self.assertIsNone(disk_meta(self.root, new_id)["seat"])


class TheRouteIsHumanOnly(unittest.TestCase):
    """Over a real socket: behind the cookie, and a bad pane id or name is a
    refusal with a reason, never a 500.
    """

    @classmethod
    def setUpClass(cls):
        import auth
        import hub
        from http.server import ThreadingHTTPServer
        cls.hub = hub
        cls.root = Path(tempfile.mkdtemp(prefix="seat-route-"))
        (cls.root / "panes").mkdir()
        cls.patches = [mock.patch.object(m, "STATE", cls.root)
                       for m in (sessions, sessions._core)]
        for p in cls.patches:
            p.start()
        write_meta(cls.root, "p1")
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes, m.subscribers, m.not_restored = {}, [], 0
        m._lock = threading.Lock()
        m.catalog, m.mcp, m.orphans = {}, None, {}
        m.restore()
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

    def _post(self, body, cookie=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        raw = json.dumps(body).encode()
        h = {"Content-Type": "application/json", "Content-Length": str(len(raw))}
        if cookie:
            h["Cookie"] = self.cookie
        c.request("POST", "/api/session/seat", body=raw, headers=h)
        r = c.getresponse()
        out = json.loads(r.read() or b"{}")
        c.close()
        return r.status, out

    def test_no_cookie_is_401_and_nothing_is_bound(self):
        st, _ = self._post({"pane": "p1", "seat": "sneaky"}, cookie=False)
        self.assertEqual(st, 401)
        self.assertIsNone(self.hub.MGR.panes["p1"].seat)

    def test_a_bad_pane_id_is_refused(self):
        st, out = self._post({"pane": "nope", "seat": "x"})
        self.assertIn(st, (400, 404), out)

    def test_a_bad_name_is_a_400_quoting_the_rule(self):
        st, out = self._post({"pane": "p1", "seat": "Bad Name"})
        self.assertEqual(st, 400, out)
        self.assertIn("lowercase letter", json.dumps(out))

    def test_bind_and_unbind(self):
        st, out = self._post({"pane": "p1", "seat": "reviewer"})
        self.assertEqual((st, out.get("seat")), (200, "reviewer"), out)
        st, out = self._post({"pane": "p1", "seat": ""})
        self.assertEqual((st, out.get("seat")), (200, None), out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
