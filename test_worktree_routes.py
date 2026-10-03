#!/usr/bin/env python3
"""The own-branch hub routes (WS3), over a real socket.

A real hub Handler on a loopback port, with `hub.MGR` swapped for the test's
Manager (the `fake` lane, a real ACP process) and every git call in temp repos.
Test IDs are the plan's T-RTE-* catalogue (docs/worktree-review-plan.md).

    python3 -m unittest test_worktree_routes -v   (also collected by test_corral_light.py)
"""
import http.client
import json
import os
import sys
import threading
import unittest
from pathlib import Path

# Before anything imports sessions or hub (STATE binds at import): never the live store.
from testkit.scratch import default_state  # noqa: E402
default_state("corral-light-wt-routes-")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import worktrees as wt                                            # noqa: E402
from test_resilience import wait_for                              # noqa: E402
from test_worktrees import LifecycleCase, _tree_snapshot          # noqa: E402

POSTS = ("snapshot", "commit", "publish", "discard")


class WorktreeRoutes(LifecycleCase):

    def setUp(self):
        super().setUp()
        import auth
        import hub
        from http.server import ThreadingHTTPServer
        self.hub = hub
        self._old_mgr, hub.MGR = hub.MGR, self.mgr
        self.addCleanup(setattr, hub, "MGR", self._old_mgr)
        self.cookie = f"{hub.COOKIE}={auth.mint()}"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body=None, cookie=True, origin=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h, raw = {}, None
        if body is not None:
            raw = json.dumps(body).encode()
            h.update({"Content-Type": "application/json", "Content-Length": str(len(raw))})
        if cookie:
            h["Cookie"] = self.cookie
        if origin:
            h["Origin"] = origin
        c.request(method, path, body=raw, headers=h)
        r = c.getresponse()
        out = json.loads(r.read() or b"{}")
        c.close()
        return r.status, out

    def post(self, action, **body):
        return self.req("POST", f"/api/session/worktree/{action}", body)

    def probe(self, cwd):
        from urllib.parse import quote
        return self.req("GET", "/api/session/worktree/probe?cwd=" + quote(str(cwd)))

    def wt_events(self, p):
        return [e["data"] for e in self.events(p, "worktree")]

    def reviewed(self, p, text="write a.txt reviewed"):
        self.say(p, text)
        st, snap = self.post("snapshot", pane=p.id)
        self.assertEqual(st, 200, snap)
        return snap

    def committed(self, p):
        snap = self.reviewed(p)
        st, out = self.post("commit", pane=p.id, tree=snap["tree"], head=snap["head"],
                            index_id=snap["index_id"], message="feat: reviewed")
        self.assertEqual(st, 200, out)
        return snap, out

    def with_remote(self):
        remote = self.tmp / "remote.git"
        wt.git(["init", "-q", "--bare", str(remote)], cwd=self.tmp)
        wt.git(["remote", "add", "origin", str(remote)], cwd=self.repo)
        wt.git(["push", "-q", "origin", "main"], cwd=self.repo)
        return str(remote)

    # ── probe ────────────────────────────────────────────────────────────

    def test_T_RTE_1_probe_describes_a_repo_and_each_lane(self):
        st, out = self.probe(self.repo)
        self.assertEqual(st, 200, out)
        pr = out["probe"]
        self.assertTrue(pr["inside"])
        self.assertEqual(os.path.realpath(pr["top"]), os.path.realpath(self.repo))
        self.assertEqual(pr["refusals"], [])
        self.assertNotIn("common_dir", pr)
        self.assertIsNone(out["laneRefusals"]["fake"])
        self.assertIn("not enabled", out["laneRefusals"]["claude"])

    def test_T_RTE_2_probe_refuses_what_is_not_a_directory(self):
        (self.tmp / "f.txt").write_text("x\n")
        for cwd in (self.repo / "a.txt", self.tmp / "nope", ""):
            st, out = self.probe(cwd)
            self.assertEqual(st, 400, (cwd, out))
        st, out = self.probe(self.tmp)
        self.assertEqual((st, out["probe"]["inside"]), (200, False), out)

    def test_T_RTE_3_probe_writes_nothing(self):
        self.state.mkdir(parents=True, exist_ok=True)
        before = {d: _tree_snapshot(d) for d in (self.repo, self.state)}
        for _ in range(3):
            self.assertEqual(self.probe(self.repo)[0], 200)
        self.assertEqual({d: _tree_snapshot(d) for d in (self.repo, self.state)}, before)
        self.assertFalse((self.tmp / "wtroot").exists(), "probe created the worktree root")

    # ── snapshot ─────────────────────────────────────────────────────────

    def test_T_RTE_4_snapshot_shape(self):
        p = self.pane()
        snap = self.reviewed(p)
        for k in ("tree", "head", "index_id", "base_sha", "too_big", "ignored",
                  "staged_differs", "diff", "summary", "remotes"):
            self.assertIn(k, snap)
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["diff"]["tree"], snap["tree"])
        [f] = snap["diff"]["files"]
        self.assertEqual((f["path"], f["status"]), ("a.txt", "M"))
        self.assertIn("+reviewed", f["patch"])
        self.assertEqual(snap["remotes"], [])

    def test_T_RTE_5_snapshot_caps_large_untracked_files(self):
        p = self.pane()
        path = Path(self.entry(p)["path"])
        (path / "big.bin").write_bytes(b"x" * (wt.SNAP_UNTRACKED_MAX + 1))
        snap = self.reviewed(p, "write small.txt ok")
        self.assertEqual([t["path"] for t in snap["too_big"]], ["big.bin"])
        self.assertEqual([f["path"] for f in snap["diff"]["files"]], ["small.txt"])
        self.assertLess(len(json.dumps(snap)), 64 << 10)

    def test_T_RTE_6_snapshot_of_a_plain_or_unknown_pane_or_bad_oid_is_400(self):
        plain = self.mgr.create("fake", str(self.repo))
        self.assertEqual(plain.state, "ready", plain.error)
        for pane in (plain.id, "nosuch"):
            st, out = self.post("snapshot", pane=pane)
            self.assertEqual(st, 400, (pane, out))
        p = self.pane()
        for bad in (None, "HEAD", "abc123", "A" * 40, "0" * 40 + "\n"):
            st, out = self.post("discard", pane=p.id, tree=bad)
            self.assertEqual(st, 400, (bad, out))
        self.assertEqual(self.entry(p)["phase"], "active")
        st, out = self.post("rebase", pane=p.id)
        self.assertEqual(st, 404, out)

    # ── commit / publish / discard ───────────────────────────────────────

    def test_T_RTE_7_commit_moves_the_branch_and_emits_one_event(self):
        p = self.pane()
        n = len(self.wt_events(p))
        snap, out = self.committed(p)
        self.assertTrue(out["ok"])
        e = self.entry(p)
        self.assertEqual(wt.git(["rev-parse", e["branch"]], cwd=self.repo).text.strip(),
                         out["commit"])
        self.assertEqual(wt.git(["rev-parse", out["commit"] + "^{tree}"],
                                cwd=self.repo).text.strip(), snap["tree"])
        new = [d for d in self.wt_events(p)[n:] if "commit" in d]
        self.assertEqual([d["commit"] for d in new], [out["commit"]])

    def test_T_RTE_8_commit_after_a_change_is_409_changed(self):
        p = self.pane()
        snap = self.reviewed(p)
        self.say(p, "write a.txt sneaky")
        st, out = self.post("commit", pane=p.id, tree=snap["tree"], head=snap["head"],
                            index_id=snap["index_id"], message="m")
        self.assertEqual((st, out.get("reason")), (409, "changed"), out)
        self.assertEqual(wt.git(["rev-parse", self.entry(p)["branch"]],
                                cwd=self.repo).text.strip(), snap["head"])

    def test_T_RTE_9_publish_pushes_the_reviewed_commit_and_emits_an_event(self):
        url = self.with_remote()
        p = self.pane()
        snap, c = self.committed(p)
        st, again = self.post("snapshot", pane=p.id)
        [origin] = again["remotes"]
        self.assertEqual((origin["name"], origin["pushUrls"]), ("origin", [url]))
        st, out = self.post("publish", pane=p.id, oid=c["commit"], tree=snap["tree"],
                            remote="origin", push_url=url)
        self.assertEqual(st, 200, out)
        ref = self.entry(p)["branch"]
        there = wt.git(["ls-remote", url, ref], cwd=self.tmp).text.split()
        self.assertEqual(there[0], c["commit"])
        self.assertEqual([d["published"]["pushed"] for d in self.wt_events(p)
                          if "published" in d], [c["commit"]])

    def test_T_RTE_10_publish_refusals_are_machine_readable(self):
        url = self.with_remote()
        p = self.pane()
        snap, c = self.committed(p)
        st, out = self.post("publish", pane=p.id, oid=c["commit"], tree=snap["tree"],
                            remote="origin", push_url=url + ".elsewhere")
        self.assertEqual((st, out.get("reason")), (409, "remote_changed"), out)
        st, out = self.post("publish", pane=p.id, oid=snap["head"], tree=snap["tree"],
                            remote="origin", push_url=url)
        self.assertEqual((st, out.get("reason")), (409, "identity"), out)
        self.say(p, "write a.txt later")
        st, out = self.post("publish", pane=p.id, oid=c["commit"], tree=snap["tree"],
                            remote="origin", push_url=url)
        self.assertEqual((st, out.get("reason")), (409, "uncommitted"), out)
        self.assertEqual(wt.git(["ls-remote", url, self.entry(p)["branch"]],
                                cwd=self.tmp).text, "")
        self.assertFalse([d for d in self.wt_events(p) if "published" in d])

    def test_T_RTE_11_discard_keeps_a_recovery_ref_and_emits_an_event(self):
        p = self.pane()
        snap = self.reviewed(p)
        st, out = self.post("discard", pane=p.id, tree=snap["tree"])
        self.assertEqual(st, 200, out)
        self.assertEqual(self.entry(p)["phase"], "trashed")
        self.assertTrue(Path(out["trash_path"], "a.txt").exists())
        self.assertTrue(wt.git(["rev-parse", "--verify", out["recovery_ref"]],
                               cwd=self.repo).text.strip())
        self.assertEqual(p.state, "detached")
        self.assertEqual(len([d for d in self.wt_events(p) if "discarded" in d]), 1)

    def test_T_RTE_12_busy_and_stale_discard_are_409_with_reasons(self):
        p = self.pane()
        snap = self.reviewed(p)
        p.send("sleep 5")
        self.assertTrue(wait_for(lambda: "sleeping" in self.texts(p)))
        for action, body in (("snapshot", {}), ("discard", {"tree": snap["tree"]}),
                             ("commit", {"tree": snap["tree"], "head": snap["head"],
                                         "index_id": snap["index_id"], "message": "m"})):
            st, out = self.post(action, pane=p.id, **body)
            self.assertEqual((st, out.get("reason")), (409, "busy"), (action, out))
        p.cancel()
        self.assertTrue(wait_for(lambda: p.state == "ready", timeout=15), p.state)
        self.say(p, "write a.txt after review")
        st, out = self.post("discard", pane=p.id, tree=snap["tree"])
        self.assertEqual((st, out.get("reason")), (409, "changed"), out)
        self.assertEqual(self.entry(p)["phase"], "active")

    # ── access ───────────────────────────────────────────────────────────

    def test_T_RTE_13_every_route_needs_the_cookie_and_posts_need_our_origin(self):
        p = self.pane()
        for path in ("/api/session/worktree/probe?cwd=" + str(self.repo), "/api/session/worktrees"):
            self.assertEqual(self.req("GET", path, cookie=False)[0], 401, path)
        for action in POSTS:
            path = f"/api/session/worktree/{action}"
            self.assertEqual(self.req("POST", path, {"pane": p.id}, cookie=False)[0], 401)
            st, out = self.req("POST", path, {"pane": p.id}, origin="http://evil.example")
            self.assertEqual(st, 403, (action, out))
        st, out = self.req("POST", "/api/session/new",
                           {"agent": "fake", "cwd": str(self.repo), "worktree": True},
                           origin="http://evil.example")
        self.assertEqual(st, 403, out)
        self.assertEqual(self.entry(p)["phase"], "active")
        self.assertEqual(len(self.reg.all()), 1)

    def test_T_RTE_14_the_list_never_leaks_common_dir_or_outside_paths(self):
        p = self.pane()
        snap = self.reviewed(p)
        self.pane()
        self.assertEqual(self.post("discard", pane=p.id, tree=snap["tree"])[0], 200)
        st, out = self.req("GET", "/api/session/worktrees")
        self.assertEqual(st, 200, out)
        self.assertEqual(len(out["worktrees"]), 2)
        self.assertEqual({r["phase"] for r in out["worktrees"]}, {"active", "trashed"})
        blob = json.dumps(out)
        common = os.path.realpath(self.repo / ".git")
        self.assertNotIn("common_dir", blob)
        self.assertNotIn(common, blob)
        allowed = (os.path.realpath(wt.worktree_root()), os.path.realpath(self.repo))

        def strings(v):
            if isinstance(v, dict):
                for x in v.values():
                    yield from strings(x)
            elif isinstance(v, list):
                for x in v:
                    yield from strings(x)
            elif isinstance(v, str):
                yield v
        for s in strings(out):
            if s.startswith("/"):
                r = os.path.realpath(s)
                self.assertTrue(any(r == a or r.startswith(a + os.sep) for a in allowed)
                                and not r.startswith(common), s)


if __name__ == "__main__":
    unittest.main()
