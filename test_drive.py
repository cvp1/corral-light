#!/usr/bin/python3
"""The drive channel (drive.py) between two real hub-link services on
loopback, over real mutual TLS, with a fake pane manager on the driven side."""
import queue
import tempfile
import threading
import time
import unittest

import drive
from test_hublink import Hub


class FakeDriven:
    """The driven hub's side: panes with numbered events and an event bus."""

    RING = 50

    def __init__(self):
        self.lock = threading.Lock()
        self.panes = {}
        self.subs = []
        self.ops = []
        self.n = 0

    # adapter interface
    def lanes(self):
        return {"lanes": [{"key": "fake", "label": "Fake", "available": True}]}

    HOME = "the-operator-home"

    def home(self):
        return self.HOME

    def subscribe(self, q):
        with self.lock:
            self.subs.append(q)

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def owned(self, owner):
        with self.lock:
            return [p for p, v in self.panes.items()
                    if v["owner"] == (owner["hub"], owner["gen"])]

    def snapshot(self, pane, since):
        with self.lock:
            v = self.panes.get(pane)
            if v is None:
                return None
            evs = [e for e in v["events"] if e["seq"] > since][-self.RING:]
            return {"id": pane, "state": "idle", "events": evs, "seq": v["seq"]}

    def op(self, op, args, owner):
        with self.lock:
            self.ops.append((op, dict(args)))
        if op == "open":
            with self.lock:
                self.n += 1
                pid = f"p{self.n:011d}"
                self.panes[pid] = {"owner": (owner["hub"], owner["gen"]),
                                   "seq": 0, "events": []}
            if args.get("text"):
                self.emit(pid, "user", {"text": args["text"]})
            return {"pane": pid}
        if op == "send":
            self.emit(args["pane"], "user", {"text": args.get("text")})
            return {"pane": args["pane"]}
        if op == "close":
            return {"pane": args["pane"], "closed": True}
        return {"pane": args.get("pane")}

    # test helpers
    def local_pane(self):
        with self.lock:
            self.n += 1
            pid = f"p{self.n:011d}"
            self.panes[pid] = {"owner": ("someone-else", "x"), "seq": 0, "events": []}
            return pid

    def emit(self, pane, kind, data):
        with self.lock:
            v = self.panes[pane]
            v["seq"] += 1
            ev = {"seq": v["seq"], "pane": pane, "kind": kind, "data": data,
                  "at": time.time()}
            v["events"].append(ev)
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass
        return ev


class Collector:
    """The driving hub's side: what arrived, by pane, and the cursor."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = {}           # pane -> [seq...]
        self.frames = []
        self.cond = threading.Condition(self.lock)

    def on_frame(self, link, f):
        with self.cond:
            self.frames.append(f)
            snaps = f.get("panes") or []
            for s in snaps:
                for e in s.get("events") or []:
                    self._take(e)
            for e in f.get("evs") or []:
                self._take(e)
            self.cond.notify_all()

    def _take(self, e):
        got = self.events.setdefault(e["pane"], [])
        if not got or e["seq"] > got[-1]:
            got.append(e["seq"])
        else:
            got.append(("dup", e["seq"]))

    def cursors(self):
        with self.lock:
            return {p: s[-1] for p, s in self.events.items()
                    if s and isinstance(s[-1], int)}

    def wait(self, pred, timeout=10):
        with self.cond:
            return self.cond.wait_for(lambda: pred(self), timeout)


class DriveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = Hub(self.tmp.name, "office").up()        # drives
        self.b = Hub(self.tmp.name, "bedroom").up()       # is driven
        self.addCleanup(self.a.svc.stop)
        self.addCleanup(self.b.svc.stop)
        self.driven = FakeDriven()
        self.b.svc.hooks["drive"] = self.driven
        tok = self.b.svc.invite("see,drive")["token"]     # b lets a: see, drive
        self.a.svc.join(tok, "see")
        self.bid = self.b.svc.identity()[0]
        self.aid = self.a.svc.identity()[0]
        self.col = Collector()

    def link(self):
        link = self.a.svc.drive_link(self.bid, self.col.on_frame, self.col.cursors)
        self.assertTrue(link.up.wait(10), link.error)
        return link

    def test_needs_the_drive_grant(self):
        self.b.svc.grant(self.aid, "see")
        link = self.a.svc.drive_link(self.bid, self.col.on_frame, self.col.cursors)
        time.sleep(1.0)
        self.assertFalse(link.up.is_set())
        self.assertIn("may not drive", link.error or "")
        # A refusal is not a drop: the link waits long before asking again,
        # so a revoked grant never fills the other hub's ledger.
        time.sleep(3)
        refused = [e for e in self.b.svc.ledger_tail() if e["event"] == "refused"]
        self.assertEqual(len(refused), 1)

    def test_open_and_stream(self):
        link = self.link()
        ready = link.ready
        self.assertEqual(ready["lanes"]["lanes"][0]["key"], "fake")
        self.assertEqual(ready["home"], FakeDriven.HOME)
        pane = link.call("open", {"lane": "fake", "cwd": "~", "text": "hello"})["pane"]
        for i in range(40):
            self.driven.emit(pane, "text", {"text": str(i)})
        self.assertTrue(self.col.wait(lambda c: len(c.events.get(pane, [])) >= 41),
                        self.col.events)
        self.assertEqual(self.col.events[pane], list(range(1, 42)))   # in order, no dups
        self.assertTrue(any(e["event"] == "drive" and e["op"] == "open"
                            for e in self.b.svc.ledger_tail()))

    def test_a_retried_command_runs_once(self):
        link = self.link()
        r1 = link.call("open", {"lane": "fake", "cwd": "~"}, mid="m" * 16)
        r2 = link.call("open", {"lane": "fake", "cwd": "~"}, mid="m" * 16)
        self.assertEqual(r1, r2)
        self.assertEqual(sum(1 for op, _ in self.driven.ops if op == "open"), 1)

    def test_only_panes_it_opened(self):
        link = self.link()
        other = self.driven.local_pane()
        with self.assertRaises(drive.DriveError) as cm:
            link.call("send", {"pane": other, "text": "x"})
        self.assertEqual(cm.exception.status, 403)
        self.driven.emit(other, "text", {"text": "private"})
        mine = link.call("open", {"lane": "fake", "cwd": "~"})["pane"]
        self.driven.emit(mine, "text", {"text": "ok"})
        self.assertTrue(self.col.wait(lambda c: mine in c.events))
        self.assertNotIn(other, self.col.events)

    def test_revoking_drive_closes_the_channel(self):
        link = self.link()
        self.b.svc.grant(self.aid, "see")
        deadline = time.time() + 5
        while link.up.is_set() and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(link.up.is_set())
        with self.assertRaises(drive.DriveError):
            link.call("open", {"lane": "fake", "cwd": "~"}, timeout=1.5)

    def test_resume_after_a_drop_loses_nothing(self):
        link = self.link()
        pane = link.call("open", {"lane": "fake", "cwd": "~"})["pane"]
        for i in range(5):
            self.driven.emit(pane, "text", {"text": str(i)})
        self.assertTrue(self.col.wait(lambda c: len(c.events.get(pane, [])) >= 5))
        # Cut the connection, keep emitting while it is down, then let it redial.
        link.kick.clear()
        link.wire.close()
        for i in range(5, 20):
            self.driven.emit(pane, "text", {"text": str(i)})
        self.assertTrue(self.col.wait(lambda c: len(c.events.get(pane, [])) >= 20, 15),
                        (link.error, self.col.events))
        self.assertEqual(self.col.events[pane], list(range(1, 21)))
        self.assertTrue(link.up.wait(5))

    def test_a_command_sent_while_down_runs_once_after(self):
        link = self.link()
        pane = link.call("open", {"lane": "fake", "cwd": "~"})["pane"]
        link.wire.close()
        r = link.call("send", {"pane": pane, "text": "after the drop"}, timeout=15)
        self.assertEqual(r["pane"], pane)
        sends = [a for op, a in self.driven.ops if op == "send"]
        self.assertEqual(len(sends), 1)

    def test_a_channel_does_not_hold_a_listener_slot(self):
        self.link()
        import hublink
        out = []

        def roster():
            try:
                out.append(self.a.svc.call(self.bid, "GET", "/fed/v1/roster")[0])
            except Exception as e:                            # noqa: BLE001
                out.append(repr(e))
        ts = [threading.Thread(target=roster) for _ in range(hublink.MAX_CONNS_IP)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(15)
        self.assertEqual(out, [200] * hublink.MAX_CONNS_IP)

    def test_forget_stops_the_link(self):
        link = self.link()
        self.a.svc.forget(self.bid)
        self.assertTrue(link.stopped.is_set())

    def test_latency_on_loopback(self):
        link = self.link()
        pane = link.call("open", {"lane": "fake", "cwd": "~"})["pane"]
        lat = []
        for i in range(30):
            t0 = time.perf_counter()
            self.driven.emit(pane, "text", {"text": str(i)})
            want = i + 1
            self.assertTrue(self.col.wait(lambda c: len(c.events.get(pane, [])) >= want))
            lat.append(time.perf_counter() - t0)
        lat.sort()
        med = lat[len(lat) // 2]
        print(f"\ndrive loopback event latency: median {med * 1000:.1f} ms, "
              f"max {lat[-1] * 1000:.1f} ms", flush=True)
        self.assertLess(med, 0.02)
        t0 = time.perf_counter()
        for _ in range(20):
            link.call("send", {"pane": pane, "text": "x"})
        per = (time.perf_counter() - t0) / 20
        print(f"drive loopback command round trip: {per * 1000:.1f} ms", flush=True)
        self.assertLess(per, 0.1)

    def test_frames_are_capped(self):
        with self.assertRaises(drive.ProtocolError):
            drive.pack({"t": "x", "blob": "a" * (drive.FRAME_MAX + 1)})


if __name__ == "__main__":
    unittest.main()
