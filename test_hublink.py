#!/usr/bin/python3
"""Hub links (hublink.py): two services in one process, real TLS on loopback,
real git, fake pane managers."""
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import hublink


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class FakePane:
    def __init__(self, pid, cwd, title="fix the parser", state="idle"):
        self.id, self.cwd, self.title, self.state = pid, str(cwd), title, state
        self.agent, self.posture = "claude", "auto"
        self.pending, self.question, self.worktree_id = {}, None, None
        self.last_activity, self.created = time.time() - 5, "2026-10-09T00:00:00Z"
        self.ported_from, self.notes, self.cancelled = None, [], False

    def emit(self, kind, data, **kw):
        self.notes.append((kind, data))

    def cancel(self):
        self.cancelled, self.state = True, "idle"

    def last_answer(self):
        return "the parser is fixed", True


class FakeMgr:
    def __init__(self):
        self.panes, self.closed = {}, []

    def close(self, pid, by=None):
        self.closed.append((pid, by))
        self.gone = getattr(self, "gone", {})
        self.gone[pid] = self.panes.pop(pid)
        return self.gone[pid]

    def reopen(self, pid):
        why = self.gate(pid) if getattr(self, "gate", None) else None
        if why:                                  # as sessions.Manager.reopen does
            raise ValueError(why)
        self.panes[pid] = self.gone.pop(pid)
        return self.panes[pid]


class Hub:
    """One side: a Service with recorded hooks."""

    def __init__(self, root, name):
        self.root = Path(root) / name
        self.mgr = FakeMgr()
        self.opened, self.imported, self.notices = [], [], []
        self.svc = hublink.Service(
            self.mgr, self.root, notify=lambda t, b: self.notices.append(b),
            hooks={"compose": lambda p: {"text": f"pack for {p.title}", "sha": "x"},
                   "export": lambda pid: {"schema": 1, "meta": {"id": pid}, "events": []},
                   "import_bundle": self._import, "open": self._open})
        self.mgr.gate = self.svc.reopen_refusal
        self.port = _free_port()

    def _import(self, b):
        self.imported.append(b)
        return "a0a0a0a0a0a0"

    def _open(self, lane, cwd, title, text, origin):
        self.opened.append({"lane": lane, "cwd": cwd, "title": title, "text": text,
                            "origin": origin})
        return "b1b1b1b1b1b1"

    def up(self):
        self.svc.set_name(self.root.name)
        self.svc.enable("127.0.0.1", self.port)
        return self


class HubLinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = Hub(self.tmp.name, "office").up()
        self.b = Hub(self.tmp.name, "bedroom").up()
        self.addCleanup(self.a.svc.stop)
        self.addCleanup(self.b.svc.stop)

    def pair(self, a_allows="see", b_allows="see"):
        tok = self.a.svc.invite(a_allows)["token"]
        r = self.b.svc.join(tok, b_allows)
        self.aid = self.a.svc.identity()[0]
        self.bid = self.b.svc.identity()[0]
        return r

    # ── pairing ─────────────────────────────────────────────────────────
    def test_pair_both_sides_know_each_other(self):
        r = self.pair("see,watch", "see")
        self.assertEqual(r["name"], "office")
        self.assertEqual(r["weMay"], ["see", "watch"])
        self.assertIn(self.bid, self.a.svc.peers())
        self.assertIn(self.aid, self.b.svc.peers())
        # the same key on both sides, files private
        self.assertEqual(self.a.svc.peers()[self.bid]["key"],
                         self.b.svc.peers()[self.aid]["key"])
        for side in (self.a, self.b):
            mode = os.stat(side.svc._p("peers.json")).st_mode & 0o777
            self.assertEqual(mode, 0o600)
            self.assertEqual(os.stat(side.svc._p("hub.key")).st_mode & 0o777, 0o600)
        # both operators get a notice, and the ledger records it
        self.assertTrue(any("paired" in n for n in self.a.notices))
        self.assertTrue(any(e["event"] == "pair" for e in self.a.svc.ledger_tail()))

    def test_join_by_address_and_short_code(self):
        inv = self.a.svc.invite("see,watch")
        self.assertRegex(inv["code"], r"^[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$")
        typed = inv["code"].lower().replace("-", " ")          # how a person types it
        r = self.b.svc.join("127.0.0.1", "see", code=typed, port=self.a.port)
        self.assertEqual((r["name"], r["weMay"]), ("office", ["see", "watch"]))
        aid = self.a.svc.identity()[0]
        self.assertEqual(self.b.svc.peers()[aid]["fp"], self.a.svc.identity()[2])
        self.assertTrue(self.b.svc.remote_roster("office")["reachable"])

    def test_wrong_code_pairs_nothing_and_never_sends_the_code(self):
        inv = self.a.svc.invite("see")
        wrong = "ZZZZ-ZZZZ-ZZZZ" if inv["code"] != "ZZZZ-ZZZZ-ZZZZ" else "YYYY-YYYY-YYYY"
        with self.assertRaises(hublink.Refused):
            self.b.svc.join("127.0.0.1", code=wrong, port=self.a.port)
        self.assertEqual(self.a.svc.peers(), {})
        self.assertEqual(self.b.svc.peers(), {})
        # the real code still works: a wrong guess did not spend it
        self.b.svc.join("127.0.0.1", code=inv["code"], port=self.a.port)

    def test_a_hub_that_cannot_prove_the_code_is_refused(self):
        """A relay with its own certificate cannot answer with a valid proof."""
        inv = self.a.svc.invite("see")
        real = self.a.svc._on_pair

        def relay(body, ip):                  # answers, but without knowing the code
            out = real(body, ip)
            out["proof"] = "0" * 64
            return out
        self.a.svc._on_pair = relay
        with self.assertRaises(hublink.Refused) as e:
            self.b.svc.join("127.0.0.1", code=inv["code"], port=self.a.port)
        self.assertIn("could not prove", e.exception.reason)
        self.assertEqual(self.b.svc.peers(), {})

    def test_invite_is_single_use(self):
        tok = self.a.svc.invite("see")["token"]
        self.b.svc.join(tok)
        c = Hub(self.tmp.name, "third").up()
        self.addCleanup(c.svc.stop)
        with self.assertRaises(hublink.Refused) as e:
            c.svc.join(tok)
        self.assertIn("refused the pairing", e.exception.reason)

    def test_invite_needs_listener_and_known_grants(self):
        with self.assertRaises(hublink.Refused):
            self.a.svc.invite("see,root")
        self.a.svc.disable()
        with self.assertRaises(hublink.Refused):
            self.a.svc.invite("see")

    def test_token_pins_certificate(self):
        tok = self.a.svc.invite("see")["token"]
        t = hublink.Service.parse_token(tok)
        t["fp"] = "0" * 64
        import base64
        bad = "clhub1:" + base64.urlsafe_b64encode(json.dumps(t).encode()).decode().rstrip("=")
        with self.assertRaises(hublink.Refused) as e:
            self.b.svc.join(bad)
        self.assertIn("different certificate", e.exception.reason)
        # nothing was sent: the invite is still live for the real hub
        self.b.svc.join(tok)

    def _pair_body(self, code):
        hid, name, fp = self.b.svc.identity()
        afp = self.a.svc.identity()[2]
        return {"proof": hublink.pair_proof(code, "join", afp, fp, hid),
                "id": hid, "name": name, "addr": "127.0.0.1",
                "port": self.b.port, "fp": fp, "cert": self.b.svc._p("hub.crt").read_text(),
                "key": hublink._b64e(os.urandom(32))}

    def test_pairing_attempts_are_capped(self):
        t = hublink.Service.parse_token(self.a.svc.invite("see")["token"])
        target = {"addr": t["addr"], "port": t["port"], "fp": t["fp"]}
        for _ in range(hublink.PAIR_FAIL_MAX_IP):
            st, _r = self.b.svc._raw_call(target, "POST", "/fed/v1/pair",
                                          self._pair_body("AAAA-AAAA-AAAA"), signed=None)
            self.assertEqual(st, 403)
        st, r = self.b.svc._raw_call(target, "POST", "/fed/v1/pair",
                                     self._pair_body(t["code"]), signed=None)
        self.assertEqual(st, 429)          # even the right code, once the cap is hit
        self.assertEqual(self.a.svc.peers(), {})

    def test_pairing_never_replaces_a_paired_id(self):
        self.pair()
        t = hublink.Service.parse_token(self.a.svc.invite("see,takeover")["token"])
        target = {"addr": t["addr"], "port": t["port"], "fp": t["fp"]}
        body = self._pair_body(t["code"])          # B's id again, a new key
        st, r = self.b.svc._raw_call(target, "POST", "/fed/v1/pair", body, signed=None)
        self.assertEqual(st, 409)
        self.assertEqual(self.a.svc.peers()[self.bid]["grants"], ["see"])
        with self.assertRaises(hublink.Refused):
            self.b.svc.join(self.a.svc.invite("see")["token"])     # joiner refuses too

    def test_inbound_needs_the_paired_certificate(self):
        """The pair key alone is not enough: the TLS client certificate must be
        the one pinned for that hub."""
        self.pair()
        c = Hub(self.tmp.name, "intruder").up()
        self.addCleanup(c.svc.stop)
        # The intruder somehow has B's id and pair key, but not B's certificate.
        peers = {self.aid: dict(self.b.svc.peers()[self.aid])}
        c.svc._save_peers(peers)
        cme = c.svc._doc("id.json", {})
        cme["id"] = self.bid
        c.svc._save_doc("id.json", cme)
        with self.assertRaises(hublink.Refused) as e:
            c.svc.remote_roster("office")
        self.assertIn("refused this hub's certificate", e.exception.reason)

    def test_replay_protection_survives_a_restart(self):
        self.pair()
        st, _ = self._signed("GET", "/fed/v1/hello", nonce="cd" * 16)
        self.assertEqual(st, 200)
        fresh = hublink.Service(self.a.mgr, self.a.root)       # a restarted hub
        self.a.svc.stop()
        fresh.start()
        self.addCleanup(fresh.stop)
        self.a.svc = fresh
        st, r = self._signed("GET", "/fed/v1/hello", nonce="cd" * 16)
        self.assertEqual((st, r.get("error")), (401, "replayed request"))

    def test_shell_lanes_never_take_work(self):
        self.pair("see,delegate")
        o = self.b.svc.offer("office", "rm -rf ~", lane="host:server")
        with self.assertRaises(hublink.Refused) as e:
            self.a.svc.accept(o["offer"])
        self.assertIn("cannot take work", e.exception.reason)
        self.assertEqual(self.a.opened, [])
        self.assertEqual(self.a.svc.status()["inbox"][0]["state"], "offered")

    # ── request authentication ──────────────────────────────────────────
    def _signed(self, method, path, body=b"", **over):
        """A hand-built signed call from B to A, for tampering."""
        import http.client
        import hmac as _h
        import secrets
        peer = self.b.svc.peers()[self.aid]
        key = hublink._b64d(peer["key"])
        ts = str(int(over.get("ts", time.time())))
        nonce = over.get("nonce") or secrets.token_hex(16)
        to = over.get("to", self.aid)
        sig = hublink._mac(key, method, path, ts, nonce, over.get("signed_body", body),
                           self.bid, to)
        conn = http.client.HTTPSConnection("127.0.0.1", self.a.port,
                                           context=self.b.svc._client_ctx(), timeout=5)
        conn.request(method, path, body=body or None, headers={
            "X-Hub-From": self.bid, "X-Hub-To": to, "X-Hub-Ts": ts,
            "X-Hub-Nonce": nonce, "X-Hub-Sig": sig, "Content-Length": str(len(body))})
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def test_signed_requests(self):
        self.pair()
        st, _ = self._signed("GET", "/fed/v1/roster")
        self.assertEqual(st, 200)
        # replay of the same nonce
        st, _ = self._signed("GET", "/fed/v1/hello", nonce="ab" * 16)
        self.assertEqual(st, 200)
        st, r = self._signed("GET", "/fed/v1/hello", nonce="ab" * 16)
        self.assertEqual((st, r["error"]), (401, "replayed request"))
        # body changed after signing
        st, r = self._signed("POST", "/fed/v1/offer", body=b'{"offer":"x"}',
                             signed_body=b'{"offer":"y"}')
        self.assertEqual((st, r["error"]), (401, "bad signature"))
        # addressed to another hub
        st, r = self._signed("GET", "/fed/v1/hello", to="f" * 16)
        self.assertEqual(st, 401)
        # stale clock
        st, r = self._signed("GET", "/fed/v1/hello", ts=time.time() - 3600)
        self.assertEqual(st, 401)
        self.assertIn("clocks", r["error"])

    def test_pinned_certificate_on_every_call(self):
        self.pair()
        peers = self.b.svc.peers()
        peers[self.aid]["fp"] = "1" * 64
        self.b.svc._save_peers(peers)
        with self.assertRaises(hublink.Refused) as e:
            self.b.svc.remote_roster("office")
        self.assertIn("different certificate", e.exception.reason)

    def test_forget_cuts_the_link(self):
        self.pair()
        self.a.svc.forget("bedroom")
        with self.assertRaises(hublink.Refused) as e:
            self.b.svc.remote_roster("office")
        self.assertIn("not paired", e.exception.reason)

    # ── seeing ──────────────────────────────────────────────────────────
    def test_roster_and_watch_grants(self):
        self.pair("see")
        self.a.mgr.panes["aaaaaaaaaaaa"] = FakePane("aaaaaaaaaaaa", "/srv/x/proj")
        r = self.b.svc.remote_roster("office")
        self.assertTrue(r["reachable"])
        p = r["panes"][0]
        self.assertEqual((p["title"], p["cwd"]), ("fix the parser", "proj"))
        self.assertNotIn("/srv/x", json.dumps(r))      # no full paths
        with self.assertRaises(hublink.Refused) as e:
            self.b.svc.remote_pane("office", "aaaaaaaaaaaa")
        self.assertIn("may not watch", e.exception.reason)
        self.assertTrue(any(x["event"] == "refused" for x in self.a.svc.ledger_tail()))
        self.a.svc.grant("bedroom", "see,watch")
        d = self.b.svc.remote_pane("office", "aaaaaaaaaaaa")
        self.assertEqual(d["id"], "aaaaaaaaaaaa")
        # B learns what it may do there from the answers
        self.assertEqual(self.b.svc.status()["peers"][0]["weMay"], ["see", "watch"])

    def test_asleep_peer_shows_last_seen(self):
        self.pair()
        self.a.mgr.panes["aaaaaaaaaaaa"] = FakePane("aaaaaaaaaaaa", "/x/proj")
        self.b.svc.remote_roster("office")
        self.a.svc.disable()
        r = self.b.svc.remote_roster("office")
        self.assertFalse(r["reachable"])
        self.assertTrue(r["cached"])
        self.assertEqual(r["panes"][0]["id"], "aaaaaaaaaaaa")
        self.assertIsNotNone(r["lastSeen"])

    # ── takeover ────────────────────────────────────────────────────────
    def _repos(self):
        root = Path(self.tmp.name)
        origin = root / "origin"
        origin.mkdir()
        git("init", "-q", "-b", "main", cwd=origin)
        git("config", "user.email", "t@t", cwd=origin)
        git("config", "user.name", "t", cwd=origin)
        (origin / "a.txt").write_text("one\n")
        git("add", ".", cwd=origin)
        git("commit", "-qm", "first", cwd=origin)
        office = root / "office-checkout"
        git("clone", "-q", str(origin), str(office), cwd=root)
        bedroom = root / "bedroom-checkout"
        git("clone", "-q", str(origin), str(bedroom), cwd=root)
        return office, bedroom

    def test_takeover_moves_transcript_and_code(self):
        self.pair("see,takeover")
        office, bedroom = self._repos()
        (office / "a.txt").write_text("one\ntwo\n")              # uncommitted edit
        (office / "new.txt").write_text("brand new\n")           # untracked file
        status_before = git("status", "--porcelain", cwd=office)
        refs_before = git("for-each-ref", cwd=office)
        pane = FakePane("aaaaaaaaaaaa", office)
        self.a.mgr.panes[pane.id] = pane
        r = self.b.svc.take("office", "aaaaaaaaaaaa", cwd=str(bedroom))
        self.assertEqual(r["state"], "landed")
        self.assertTrue(r["acked"])
        self.assertEqual(r["live"], "b1b1b1b1b1b1")
        self.assertEqual(r["archived"], "a0a0a0a0a0a0")
        # the code arrived with the uncommitted work, on its own worktree
        wd = Path(r["workDir"])
        self.assertEqual((wd / "a.txt").read_text(), "one\ntwo\n")
        self.assertEqual((wd / "new.txt").read_text(), "brand new\n")
        self.assertTrue(r["branch"].startswith("corral/from-office/main-"))
        self.assertEqual(git("status", "--porcelain", cwd=bedroom), "")   # untouched
        # the source checkout, its index and refs are untouched
        self.assertEqual(git("status", "--porcelain", cwd=office), status_before)
        self.assertEqual(git("for-each-ref", cwd=office), refs_before)
        # the source pane is closed and fenced
        self.assertEqual(self.a.mgr.closed[0][0], "aaaaaaaaaaaa")
        self.assertIn("handed to hub bedroom", self.a.svc.reopen_refusal("aaaaaaaaaaaa"))
        with self.assertRaises(hublink.Refused):
            self.a.svc.reclaim(r["transfer"])
        # the live pane got the brief, framed
        o = self.b.opened[0]
        self.assertIn("Taken over from hub office", o["text"])
        self.assertIn("pack for fix the parser", o["text"])
        self.assertIn(str(wd), o["text"])
        self.assertEqual(o["lane"], "claude")
        self.assertTrue(any("took over" in n for n in self.a.notices))

    def test_takeover_refused_without_grant_or_when_busy(self):
        self.pair("see")
        pane = FakePane("aaaaaaaaaaaa", "/nonexistent")
        self.a.mgr.panes[pane.id] = pane
        with self.assertRaises(hublink.Refused) as e:
            self.b.svc.take("office", pane.id)
        self.assertIn("may not takeover", e.exception.reason)
        self.a.svc.grant("bedroom", "see,takeover")
        pane.state = "busy"
        with self.assertRaises(hublink.Refused) as e:
            self.b.svc.take("office", pane.id)
        self.assertIn("mid-turn", e.exception.reason)
        self.assertIn(pane.id, self.a.mgr.panes)                 # still running there
        r = self.b.svc.take("office", pane.id, interrupt=True)
        self.assertTrue(pane.cancelled)
        self.assertEqual(r["state"], "landed")

    def test_unconfirmed_takeover_can_be_reclaimed_or_fetched(self):
        self.pair("see,takeover")
        pane = FakePane("aaaaaaaaaaaa", "/nonexistent")
        self.a.mgr.panes[pane.id] = pane
        # Simulate the answer lost on the way back: A does the work, B never lands it.
        tid = "c" * 16
        st, res = self.b.svc.call("office", "POST", "/fed/v1/takeover",
                                  {"pane": pane.id, "transfer": tid})
        self.assertEqual(st, 200)
        self.assertIn("being handed", self.a.svc.reopen_refusal(pane.id))
        # B collects it later with fetch
        ins = self.b.svc._doc("transfers-in.json", {})
        ins[tid] = {"transfer": tid, "peer": self.aid, "fromName": "office",
                    "pane": pane.id, "state": "unreachable"}
        self.b.svc._save_doc("transfers-in.json", ins)
        r = self.b.svc.fetch(tid)
        self.assertEqual(r["state"], "landed")
        # a second takeover that A's operator reclaims first
        p2 = FakePane("dddddddddddd", "/nonexistent")
        self.a.mgr.panes[p2.id] = p2
        t2 = "e" * 16
        self.b.svc.call("office", "POST", "/fed/v1/takeover", {"pane": p2.id, "transfer": t2})
        self.a.svc.reclaim(t2)
        self.assertIsNone(self.a.svc.reopen_refusal(p2.id))
        st, res = self.b.svc.call("office", "POST", "/fed/v1/takeover/ack", {"transfer": t2})
        self.assertEqual(st, 410)

    def test_takeover_that_cannot_carry_its_code_moves_nothing(self):
        self.pair("see,takeover")
        office, _bedroom = self._repos()
        (office / "big.bin").write_bytes(os.urandom(1 << 16))
        pane = FakePane("aaaaaaaaaaaa", office)
        self.a.mgr.panes[pane.id] = pane
        old = hublink.FULL_BUNDLE_MAX
        hublink.FULL_BUNDLE_MAX = 1000
        self.addCleanup(setattr, hublink, "FULL_BUNDLE_MAX", old)
        with self.assertRaises(hublink.Refused) as e:
            self.b.svc.take("office", pane.id)
        self.assertIn("nothing moved", e.exception.reason)
        self.assertIn(pane.id, self.a.mgr.panes)                 # back on the office hub
        self.assertIsNone(self.a.svc.reopen_refusal(pane.id))
        self.assertEqual(self.b.opened, [])

    def _requested(self, pane, tid):
        """B asks for a takeover and records it, but lands nothing yet."""
        st, res = self.b.svc.call("office", "POST", "/fed/v1/takeover",
                                  {"pane": pane.id, "transfer": tid})
        self.assertEqual(st, 200, res)
        ins = self.b.svc._doc("transfers-in.json", {})
        ins[tid] = {"transfer": tid, "peer": self.aid, "fromName": "office",
                    "pane": pane.id, "state": "requested"}
        self.b.svc._save_doc("transfers-in.json", ins)

    def test_reclaim_before_confirmation_starts_nothing_here(self):
        self.pair("see,takeover")
        pane = FakePane("aaaaaaaaaaaa", "/nonexistent")
        self.a.mgr.panes[pane.id] = pane
        self._requested(pane, "9" * 16)
        pkg = self.a.svc._doc("transfers-out.json", {})["9" * 16]["package"]
        hublink._write_private(self.b.svc.dir / "in" / ("9" * 16) / "package.json",
                               json.dumps(pkg))          # B got the answer...
        self.a.svc.reclaim("9" * 16)                       # ...but the office reclaimed first
        r = self.b.svc.fetch("9" * 16)
        self.assertEqual(r["state"], "reclaimed")
        self.assertEqual(self.b.opened, [])                # one owner: the office
        self.assertIsNone(self.a.svc.reopen_refusal(pane.id))

    def test_unconfirmed_landing_waits_then_finishes(self):
        self.pair("see,takeover")
        pane = FakePane("aaaaaaaaaaaa", "/nonexistent")
        self.a.mgr.panes[pane.id] = pane
        self._requested(pane, "8" * 16)
        pkg = self.a.svc._doc("transfers-out.json", {})["8" * 16]["package"]
        self.a.svc.disable()                               # the office falls asleep
        r = self.b.svc._land("8" * 16, self.aid, self.b.svc.peers()[self.aid], pkg,
                             lane=None, cwd=None, send=True)
        self.assertEqual(r["state"], "staged")
        self.assertEqual(self.b.opened, [])
        self.a.svc.enable("127.0.0.1", self.a.port)        # and wakes up
        r = self.b.svc.fetch("8" * 16)                     # from the stored package
        self.assertEqual(r["state"], "landed")
        self.assertEqual(len(self.b.opened), 1)
        self.assertEqual(len(self.b.imported), 1)          # archived once, not twice
        r = self.b.svc.fetch("8" * 16)                     # idempotent
        self.assertEqual(len(self.b.opened), 1)

    def test_confirming_needs_the_grant(self):
        self.pair("see,takeover")
        pane = FakePane("aaaaaaaaaaaa", "/nonexistent")
        self.a.mgr.panes[pane.id] = pane
        self._requested(pane, "7" * 16)
        self.a.svc.grant("bedroom", "see")
        st, r = self.b.svc.call("office", "POST", "/fed/v1/takeover/ack",
                                {"transfer": "7" * 16})
        self.assertEqual(st, 403)
        self.assertIn("being handed", self.a.svc.reopen_refusal(pane.id))
        self.a.svc.reclaim("7" * 16)                       # the office keeps it

    def test_confirmation_must_name_the_package_received(self):
        self.pair("see,takeover")
        pane = FakePane("aaaaaaaaaaaa", "/nonexistent")
        self.a.mgr.panes[pane.id] = pane
        self._requested(pane, "6" * 16)
        st, r = self.b.svc.call("office", "POST", "/fed/v1/takeover/ack",
                                {"transfer": "6" * 16, "digest": "0" * 64})
        self.assertEqual(st, 409)
        self.assertIn("being handed", self.a.svc.reopen_refusal(pane.id))

    def test_no_agent_starts_before_its_code_is_checked_out(self):
        self.pair("see,takeover")
        office, bedroom = self._repos()
        (office / "a.txt").write_text("edited\n")
        pane = FakePane("aaaaaaaaaaaa", office)
        self.a.mgr.panes[pane.id] = pane
        r = self.b.svc.take("office", pane.id)              # no --cwd
        self.assertEqual(r["state"], "landed")
        self.assertEqual(self.b.opened, [])
        self.assertTrue(any("starts once the code is checked out" in n for n in r["notes"]))
        r = self.b.svc.fetch(r["transfer"], cwd=str(bedroom))
        self.assertEqual((Path(r["workDir"]) / "a.txt").read_text(), "edited\n")
        self.assertEqual(len(self.b.opened), 1)
        self.assertEqual(self.b.opened[0]["cwd"], r["workDir"])

    def test_an_existing_ref_is_never_clobbered(self):
        self.pair("see,takeover")
        office, _bedroom = self._repos()
        tid = "5" * 16
        head = git("rev-parse", "HEAD", cwd=office)
        git("update-ref", f"refs/corral-handoff/{tid}", head, cwd=office)
        (office / "a.txt").write_text("edited\n")
        pane = FakePane("aaaaaaaaaaaa", office)
        self.a.mgr.panes[pane.id] = pane
        st, r = self.b.svc.call("office", "POST", "/fed/v1/takeover",
                                {"pane": pane.id, "transfer": tid})
        self.assertEqual(st, 409)
        self.assertIn("nothing moved", r["error"])
        self.assertEqual(git("rev-parse", f"refs/corral-handoff/{tid}", cwd=office), head)
        self.assertIn(pane.id, self.a.mgr.panes)           # reopened despite the fence

    def test_concurrent_takeovers_of_one_pane(self):
        import threading
        self.pair("see,takeover")
        pane = FakePane("aaaaaaaaaaaa", "/nonexistent")
        self.a.mgr.panes[pane.id] = pane
        out = {}

        def go(tid):
            out[tid] = self.b.svc.call("office", "POST", "/fed/v1/takeover",
                                       {"pane": pane.id, "transfer": tid})
        ts = [threading.Thread(target=go, args=(c * 16,)) for c in "12"]
        ts += [threading.Thread(target=go, args=("1" * 16,))]      # a retry of the first
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        codes = sorted(st for st, _ in out.values())
        self.assertEqual(codes.count(200), 1, out)                 # one owner
        self.assertEqual(len(self.a.mgr.closed), 1)

    def test_land_refuses_hostile_package_fields(self):
        _office, bedroom = self._repos()
        bfile = Path(self.tmp.name) / "x.bundle"
        git("bundle", "create", str(bfile), "HEAD", "main", cwd=bedroom)
        tid = "f" * 16
        for code in ({"ref": "--upload-pack=touch /tmp/pwned"},
                     {"ref": "refs/heads/main"}):
            with self.assertRaises(hublink.Refused) as e:
                hublink.git_land(bedroom, bfile, code, "office", tid, Path(self.tmp.name))
            self.assertIn("unexpected ref", e.exception.reason)

    # ── delegation ──────────────────────────────────────────────────────
    def test_offer_needs_grant_then_lands_and_is_accepted(self):
        self.pair("see")
        o = self.b.svc.offer("office", "Write the release notes", title="notes")
        self.assertEqual(o["state"], "refused")
        self.a.svc.grant("bedroom", "see,delegate,watch")
        o = self.b.svc.offer("office", "Write the release notes", title="notes",
                             lane="codex", cwd_hint=self.tmp.name)
        self.assertEqual(o["state"], "offered")
        inbox = self.a.svc.status()["inbox"]
        self.assertEqual((inbox[0]["title"], inbox[0]["fromName"]), ("notes", "bedroom"))
        self.assertTrue(any("offered work" in n for n in self.a.notices))
        r = self.a.svc.accept(o["offer"][:6])
        self.assertEqual(r["pane"], "b1b1b1b1b1b1")
        opened = self.a.opened[0]
        self.assertEqual((opened["lane"], opened["cwd"]), ("codex", self.tmp.name))
        self.assertIn("Treat it", opened["text"])
        self.assertTrue(opened["text"].endswith("Write the release notes"))
        # B sees the state, and the answer because it may watch
        self.a.mgr.panes["b1b1b1b1b1b1"] = FakePane("b1b1b1b1b1b1", self.tmp.name, "notes")
        s = self.b.svc.offer_status(o["offer"])
        self.assertEqual(s["state"], "accepted")
        self.assertEqual(s["remote"]["pane"]["answer"], "the parser is fixed")
        with self.assertRaises(hublink.Refused):
            self.a.svc.accept(o["offer"])                       # once only

    def test_offer_waits_for_a_sleeping_hub(self):
        self.pair("see,delegate")
        self.a.svc.disable()
        o = self.b.svc.offer("office", "Run the slow suite")
        self.assertEqual(o["state"], "queued")
        self.assertEqual(o["attempts"], 1)
        self.a.svc.enable("127.0.0.1", self.a.port)
        self.b.svc._out_update(o["offer"], nextTry=0)
        self.b.svc._deliver(o["offer"])
        self.assertEqual(self.b.svc._doc("outbox.json", {})[o["offer"]]["state"], "offered")
        # delivered twice is still one offer
        self.b.svc._out_update(o["offer"], state="queued")
        self.b.svc._deliver(o["offer"])
        self.assertEqual(len(self.a.svc.status()["inbox"]), 1)

    def test_decline_and_withdraw(self):
        self.pair("see,delegate")
        o1 = self.b.svc.offer("office", "one")
        o2 = self.b.svc.offer("office", "two")
        self.a.svc.decline(o1["offer"])
        self.assertEqual(self.b.svc.offer_status(o1["offer"])["state"], "declined")
        self.b.svc.cancel_offer(o2["offer"])
        self.assertEqual({o["state"] for o in self.a.svc.status()["inbox"]},
                         {"declined", "withdrawn"})


class HelpersTest(unittest.TestCase):
    def test_strip_userinfo(self):
        self.assertEqual(hublink._strip_userinfo("https://u:tok@github.com/x/y.git"),
                         "https://github.com/x/y.git")
        self.assertEqual(hublink._strip_userinfo("git@github.com:x/y.git"),
                         "git@github.com:x/y.git")

    def test_sas_is_symmetric(self):
        self.assertEqual(hublink.sas("a" * 64, "b" * 64), hublink.sas("b" * 64, "a" * 64))

    def test_bind_must_be_one_address(self):
        with tempfile.TemporaryDirectory() as d:
            s = hublink.Service(FakeMgr(), d)
            for bad in ("0.0.0.0", "::", "hub.example"):
                with self.assertRaises(hublink.Refused):
                    s.enable(bad, _free_port())


if __name__ == "__main__":
    unittest.main()
