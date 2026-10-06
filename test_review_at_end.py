#!/usr/bin/env python3
""""Review at the end" (10x UX Part B): the sealed-snapshot permission policy.

A pane on its own branch with `review_at_end` has edits whose every path is
provably inside its worktree allowed once by the hub; everything else raises
today's card. Test IDs are T-SNP-* from docs/ux-10x-plan.md §5.2 (not the
worktree plan's T-SNP-*, which live in test_worktrees.py).

The `fake` lane is a real ACP process; `permjson` asks one permission built
from a JSON spec and reports the answer. Nothing here answers a real card.

    python3 -m unittest test_review_at_end -v   (also collected by test_corral_light.py)
"""
import http.client
import json
import os
import time
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

from testkit.scratch import default_state  # noqa: E402
default_state("corral-light-rae-")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import worktrees as wt                                            # noqa: E402
from test_resilience import wait_for                              # noqa: E402
from test_worktrees import LifecycleCase                          # noqa: E402

ALWAYS = [{"optionId": "always", "name": "Always", "kind": "allow_always"},
          {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
          {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]


class RaeCase(LifecycleCase):

    def rae(self, **kw):
        return self.pane(review_at_end=True, **kw)

    def root(self, p):
        return self.entry(p)["path"]

    def ask(self, p, spec):
        """Send one permjson turn. -> 'auto' when the hub answered it, or the
        pending request id when it raised a card."""
        n_auto = len(self.events(p, "permission_auto"))
        n_card = len(self.events(p, "permission"))
        n_end = self.turn_ends(p)
        p.send("permjson " + json.dumps(spec))
        self.assertTrue(wait_for(lambda: len(self.events(p, "permission_auto")) > n_auto
                                 or len(self.events(p, "permission")) > n_card, timeout=10),
                        self.texts(p)[-300:])
        if len(self.events(p, "permission_auto")) > n_auto:
            self.assertTrue(wait_for(lambda: self.turn_ends(p) > n_end and p.state != "busy",
                                     timeout=10))
            return "auto"
        rid = next(iter(p.pending))
        return rid

    def refuse(self, p, rid):
        n_end = self.turn_ends(p)
        p.answer(rid, "deny")
        self.assertTrue(wait_for(lambda: self.turn_ends(p) > n_end and p.state != "busy",
                                 timeout=10))

    def assert_card(self, p, spec):
        r = self.ask(p, spec)
        self.assertNotEqual(r, "auto", f"auto-allowed: {spec}")
        self.refuse(p, r)

    def edit(self, *paths, kind="edit", **extra):
        spec = {"kind": kind, "title": "Edit", "locations": [{"path": x} for x in paths]}
        spec.update(extra)
        return spec


class ThePolicy(RaeCase):

    def test_T_SNP_1_an_in_tree_edit_is_allowed_once_with_the_card_digest(self):
        p = self.rae()
        path = os.path.join(self.root(p), "a.txt")
        spec = self.edit(path, rawInput={"file_path": path, "old_string": "a/b",
                                         "new_string": "x // y"},
                         content=[{"type": "diff", "path": path, "oldText": "a", "newText": "b"}])
        self.assertEqual(self.ask(p, spec), "auto")
        [auto] = self.events(p, "permission_auto")
        self.assertEqual(auto["data"]["paths"], [path])
        self.assertEqual(auto["data"]["optionId"], "allow")
        self.assertEqual(self.events(p, "permission"), [])
        self.assertIn('"optionId": "allow"', self.texts(p))
        # The same request on a plain own-branch pane raises a card: same digest.
        q = self.pane()
        qpath = os.path.join(self.root(q), "a.txt")
        spec_q = json.loads(json.dumps(spec).replace(path, qpath))
        rid = self.ask(q, spec_q)
        [card] = self.events(q, "permission")
        size, digest = self.sessions.perm_digest(
            {"toolCall": {k: spec_q[k] for k in ("rawInput", "content", "locations")}})
        self.assertEqual(card["data"]["digest"], digest)
        self.assertEqual(card["data"]["bytes"], size)
        self.assertEqual(auto["data"]["digest"], self.sessions.perm_digest(
            {"toolCall": {k: spec[k] for k in ("rawInput", "content", "locations")}})[1])
        self.refuse(q, rid)

    def test_T_SNP_1b_grok_shape_path_only_in_rawInput(self):
        p = self.rae()
        path = os.path.join(self.root(p), "pkg", "new.py")
        spec = {"kind": "edit", "title": "Edit", "locations": [],
                "rawInput": {"file_path": path, "old_string": "", "new_string": "x = 1\n",
                             "replace_all": False, "variant": "SearchReplace"}}
        self.assertEqual(self.ask(p, spec), "auto")

    def test_T_SNP_1c_a_relative_path_resolves_against_the_agent_cwd(self):
        p = self.rae()
        self.assertEqual(self.ask(p, self.edit("sub/new.txt")), "auto")

    def test_T_SNP_2_an_edit_outside_raises_a_card(self):
        p = self.rae()
        self.assert_card(p, self.edit(str(self.tmp / "elsewhere.txt")))
        self.assert_card(p, self.edit(str(self.repo / "a.txt")))   # the main checkout

    def test_T_SNP_3_one_inside_and_one_outside_raises_a_card(self):
        p = self.rae()
        inside = os.path.join(self.root(p), "a.txt")
        self.assert_card(p, self.edit(inside, str(self.tmp / "x.txt")))
        self.assert_card(p, self.edit(inside, rawInput={"file_path": str(self.tmp / "x.txt")}))
        self.assert_card(p, self.edit(inside, content=[{"type": "diff",
                                                        "path": str(self.tmp / "x.txt")}]))

    def test_T_SNP_4_an_edit_with_no_path_raises_a_card(self):
        p = self.rae()
        self.assert_card(p, {"kind": "edit", "title": "Edit"})
        self.assert_card(p, {"kind": "edit", "title": "Edit", "locations": [{}],
                             "rawInput": {"old_string": "a", "new_string": "b"}})
        self.assert_card(p, self.edit(""))

    def test_T_SNP_5_dotdot_and_symlink_escapes_raise_a_card(self):
        p = self.rae()
        root = self.root(p)
        self.assert_card(p, self.edit(os.path.join(root, "..", "escaped.txt")))
        self.assert_card(p, self.edit("../../escaped.txt"))
        os.symlink(str(self.tmp), os.path.join(root, "link"))
        self.assert_card(p, self.edit(os.path.join(root, "link", "x.txt")))
        os.symlink(str(self.tmp / "target.txt"), os.path.join(root, "flink"))
        self.assert_card(p, self.edit(os.path.join(root, "flink")))
        self.assertEqual(self.events(p, "permission_auto"), [])

    def test_T_SNP_6_the_git_admin_dir_raises_a_card(self):
        p = self.rae()
        e = self.entry(p)
        admin = Path(e["common_dir"]) / "worktrees" / e["admin_name"]
        self.assert_card(p, self.edit(os.path.join(self.root(p), ".git")))
        self.assert_card(p, self.edit(str(admin / "HEAD")))
        self.assert_card(p, self.edit(os.path.join(self.root(p), "vendor", ".git", "config")))
        self.assert_card(p, self.edit(self.root(p)))               # the root itself

    def test_T_SNP_7_an_unrecognised_path_like_key_raises_a_card(self):
        p = self.rae()
        inside = os.path.join(self.root(p), "a.txt")
        self.assert_card(p, self.edit(inside, rawInput={"file_path": inside,
                                                        "target": str(self.tmp / "x")}))
        self.assert_card(p, self.edit(inside, rawInput={"file_path": inside,
                                                        "extra": {"to": "/etc/x"}}))
        self.assert_card(p, self.edit(inside, rawInput="/etc/passwd"))
        self.assert_card(p, self.edit(inside, rawInput={"file_path": 7}))
        # A recognised content key full of slashes, and an unknown flat key, pass.
        self.assertEqual(self.ask(p, self.edit(inside, rawInput={
            "file_path": inside, "new_string": "see /etc/hosts", "flag": True})), "auto")

    def test_T_SNP_7b_paths_hidden_beside_a_good_one_raise_a_card(self):
        p = self.rae()
        inside = os.path.join(self.root(p), "a.txt")
        outside = str(self.tmp / "x")
        # A path nested in MultiEdit's edits list.
        self.assert_card(p, self.edit(inside, rawInput={
            "file_path": inside, "edits": [{"file_path": outside, "new_string": "x"}]}))
        self.assert_card(p, self.edit(inside, rawInput={"file_path": inside, "edits": "x"}))
        # A parent step or a home shorthand with no slash in it.
        self.assert_card(p, self.edit(inside, rawInput={"file_path": inside, "target": ".."}))
        self.assert_card(p, self.edit(inside, rawInput={"file_path": inside, "target": "~"}))
        # A location that names its file some other way.
        self.assert_card(p, self.edit(inside, rawInput={"file_path": inside},
                                      locations=[{"path": inside}, {"uri": "file://" + outside}]))
        self.assert_card(p, self.edit(inside, rawInput={"file_path": inside},
                                      locations=[{"path": inside, "uri": "file://" + outside}]))
        # The shapes the lanes really send still pass (docs/ux-10x-phase0.md).
        self.assertEqual(self.ask(p, self.edit(inside, rawInput={
            "file_path": inside, "edits": [{"old_string": "a/b", "new_string": "c", "replace_all": False}]})),
            "auto")
        self.assertEqual(self.ask(p, self.edit(inside, locations=[{"path": inside, "line": 3}],
                                                rawInput={"file_path": inside, "variant": "SearchReplace",
                                                          "old_string": "a", "new_string": "b",
                                                          "replace_all": False})), "auto")

    def test_T_SNP_8_other_kinds_raise_a_card(self):
        p = self.rae()
        inside = os.path.join(self.root(p), "a.txt")
        for kind in ("execute", "fetch", "other", "read", "think"):
            self.assert_card(p, self.edit(inside, kind=kind))
        self.assert_card(p, {"title": "x", "locations": [{"path": inside}]})
        # The write kinds pass.
        for kind in ("delete", "move"):
            self.assertEqual(self.ask(p, self.edit(inside, kind=kind)), "auto")

    def test_T_SNP_9_an_oversize_payload_raises_a_refuse_only_card(self):
        p = self.rae()
        inside = os.path.join(self.root(p), "a.txt")
        rid = self.ask(p, self.edit(inside, rawInput={"file_path": inside},
                                    pad=self.sessions._core.MAX_PERM_BYTES + 10))
        self.assertNotEqual(rid, "auto")
        [card] = self.events(p, "permission")
        self.assertTrue(card["data"]["oversize"])
        self.refuse(p, rid)

    def test_T_SNP_10_an_open_question_or_a_hold_raises_a_card(self):
        p = self.rae()
        inside = os.path.join(self.root(p), "a.txt")
        # The agent asks mid-turn, then requests an edit (a human turn would
        # have closed the question, so set it once the turn is running).
        n = len(self.events(p, "permission"))
        p.send("permjson " + json.dumps(self.edit(inside, delay=1.0)))
        self.assertTrue(wait_for(lambda: p.state == "busy"))
        p.question = {"text": "which one?", "at": 0, "turn": None}
        self.assertTrue(wait_for(lambda: len(self.events(p, "permission")) > n, timeout=10))
        self.refuse(p, next(iter(p.pending)))
        self.assertEqual(self.events(p, "permission_auto"), [])
        p.question = None
        p._gate_hold = True
        self.assert_card(p, self.edit(inside))
        p._gate_hold = False
        self.assertEqual(self.ask(p, self.edit(inside)), "auto")

    def test_T_SNP_11_without_review_at_end_every_request_is_a_card(self):
        p = self.pane()
        self.assertFalse(p.review_at_end)
        inside = os.path.join(self.root(p), "a.txt")
        rid = self.ask(p, self.edit(inside))
        self.assertNotEqual(rid, "auto")
        [card] = self.events(p, "permission")
        self.assertEqual([o["kind"] for o in card["data"]["options"]],
                         ["allow_once", "reject_once"])
        self.refuse(p, rid)
        self.assertEqual(self.events(p, "permission_auto"), [])

    def test_T_SNP_12_the_answer_is_allow_once_even_when_always_is_offered(self):
        p = self.rae()
        inside = os.path.join(self.root(p), "a.txt")
        self.assertEqual(self.ask(p, self.edit(inside, options=ALWAYS)), "auto")
        [auto] = self.events(p, "permission_auto")
        self.assertEqual((auto["data"]["optionId"], auto["data"]["optionKind"]),
                         ("allow", "allow_once"))
        self.assertIn('"optionId": "allow"', self.texts(p))
        # No allow_once offered: a card, never a broader grant.
        self.assert_card(p, self.edit(inside, options=[ALWAYS[0], ALWAYS[2]]))

    def test_T_SNP_13_an_exception_in_the_check_raises_a_card_and_is_logged(self):
        p = self.rae()
        inside = os.path.join(self.root(p), "a.txt")
        boom = mock.patch.object(type(p), "_in_tree_write", side_effect=RuntimeError("boom"))
        with boom, mock.patch("sys.stderr") as err:
            self.assert_card(p, self.edit(inside))
        self.assertIn("boom", "".join(str(c) for c in err.write.call_args_list))

    def test_T_SNP_14_the_guard_still_stops_an_out_of_tree_edit_event(self):
        p = self.rae()
        outside = str(self.tmp / "elsewhere.txt")
        self.say(p, f"tool-edit {outside}", timeout=10)
        [esc] = [x for x in self.events(p, "worktree") if "escape" in (x["data"] or {})]
        self.assertEqual(esc["data"]["escape"]["paths"], [outside])


class TheFlag(RaeCase):

    def test_T_SNP_15_the_flag_survives_a_restart(self):
        p = self.rae()
        meta = json.loads((p.dir / "meta.json").read_text())
        self.assertTrue(meta["review_at_end"])
        q = self.sessions.Pane.from_meta(meta, self.mgr)
        self.assertTrue(q.review_at_end)
        self.assertTrue(p.snapshot()["worktree"]["reviewAtEnd"])
        old = {k: v for k, v in meta.items() if k != "review_at_end"}
        self.assertFalse(self.sessions.Pane.from_meta(old, self.mgr).review_at_end)
        # Never on a pane without a worktree, whatever the meta says.
        plain = dict(meta, worktree_id=None)
        self.assertFalse(self.sessions.Pane.from_meta(plain, self.mgr).review_at_end)

    def test_T_SNP_15b_resume_keeps_auto_allowing(self):
        p = self.rae()
        p.pause()
        self.assertTrue(wait_for(lambda: p.state == "detached"))
        p.resume()
        self.assertTrue(wait_for(lambda: p.state == "ready", timeout=15), p.error)
        self.assertEqual(self.ask(p, self.edit(os.path.join(self.root(p), "a.txt"))), "auto")

    def summary_after(self, p, text):
        n = len([e for e in self.events(p, "worktree") if "summary" in (e["data"] or {})])
        self.say(p, text)
        self.assertTrue(wait_for(lambda: len([e for e in self.events(p, "worktree")
                                              if "summary" in (e["data"] or {})]) > n))
        return p.worktree_view()

    def test_T_SNP_16_a_changed_tree_stays_unreviewed_until_marked_reviewed(self):
        p = self.rae()
        v = self.summary_after(p, "write notes.txt one")
        self.assertTrue(v["reviewAtEnd"])
        self.assertGreater(v["summary"]["files"], 0)
        self.assertNotEqual(v["reviewedDigest"], v["summary"]["digest"])
        # Opening review grants nothing.
        snap = self.mgr.worktree_snapshot(p.id)
        self.assertIsNone(p.worktree_view()["reviewedDigest"])
        r = self.mgr.worktree_mark_reviewed(p.id, snap["tree"])
        v = p.worktree_view()
        self.assertEqual(v["reviewedDigest"], v["summary"]["digest"])
        self.assertEqual(r["reviewed"], v["reviewedDigest"])
        self.assertTrue(any((e["data"] or {}).get("reviewed") == v["reviewedDigest"]
                            for e in self.events(p, "worktree")))
        # Persisted: the card stays cleared after a restart.
        meta = json.loads((p.dir / "meta.json").read_text())
        self.assertEqual(meta["reviewed_digest"], v["reviewedDigest"])
        v2 = self.summary_after(p, "write notes.txt two")
        self.assertNotEqual(v2["reviewedDigest"], v2["summary"]["digest"])

    def test_mark_reviewed_is_bound_to_the_tree_the_operator_saw(self):
        p = self.rae()
        self.summary_after(p, "write notes.txt one")
        seen = self.mgr.worktree_snapshot(p.id)
        self.summary_after(p, "write notes.txt changed after the dialog opened")
        with self.assertRaises(wt.Refused) as cm:
            self.mgr.worktree_mark_reviewed(p.id, seen["tree"])
        self.assertEqual(cm.exception.reason, "changed")
        self.assertIsNone(p.worktree_view()["reviewedDigest"])
        # Not a Review-at-the-end pane: nothing to mark.
        q = self.pane()
        self.say(q, "write a.txt x")
        with self.assertRaises(wt.Refused):
            self.mgr.worktree_mark_reviewed(q.id, self.mgr.worktree_snapshot(q.id)["tree"])

    def test_a_write_during_the_grant_is_never_covered(self):
        p = self.rae()
        self.summary_after(p, "write notes.txt one")
        seen = self.mgr.worktree_snapshot(p.id)
        real = wt.snapshot

        def racing(e, tmp_dir=None):           # another process writes mid-grant
            out = real(e, tmp_dir=tmp_dir)
            Path(e["path"], "notes.txt").write_text("changed while granting")
            return out
        with mock.patch.object(wt, "snapshot", racing):
            with self.assertRaises(wt.Refused):
                self.mgr.worktree_mark_reviewed(p.id, seen["tree"])
        self.assertIsNone(p.worktree_view()["reviewedDigest"])

    def test_a_restored_mtime_still_reads_as_changed(self):
        p = self.rae()
        self.summary_after(p, "write notes.txt aaaa")
        f = Path(self.root(p), "notes.txt")
        e = self.mgr.worktree_entry(p)
        d1 = wt.summary(e)["digest"]
        st = f.stat()
        time.sleep(0.02)
        f.write_text(f.read_text().replace("aaaa", "bbbb"))   # same size, same line counts
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))     # and the old mtime
        self.assertNotEqual(wt.summary(e)["digest"], d1)

    def test_commit_is_the_review(self):
        p = self.rae()
        self.summary_after(p, "write notes.txt one")
        snap = self.mgr.worktree_snapshot(p.id)
        self.mgr.worktree_commit(p.id, snap["tree"], snap["head"], snap["index_id"], "m")
        v = p.worktree_view()
        self.assertEqual(v["reviewedDigest"], v["summary"]["digest"])

    def test_T_SNP_17_commit_after_auto_allowed_edits_writes_the_reviewed_tree(self):
        p = self.rae()
        root = self.root(p)
        self.assertEqual(self.ask(p, self.edit(os.path.join(root, "a.txt"))), "auto")
        self.say(p, "write a.txt changed")
        self.say(p, "write b.txt new")
        snap = self.mgr.worktree_snapshot(p.id)
        r = self.mgr.worktree_commit(p.id, snap["tree"], snap["head"], snap["index_id"], "m")
        self.assertEqual(wt.git(["rev-parse", r["commit"] + "^{tree}"], cwd=root).text.strip(),
                         snap["tree"])
        # A change after review refuses, auto-allowed or not.
        self.say(p, "write b.txt later")
        snap2 = self.mgr.worktree_snapshot(p.id)
        self.say(p, "write b.txt after-review")
        with self.assertRaises(wt.Refused):
            self.mgr.worktree_commit(p.id, snap2["tree"], snap2["head"], snap2["index_id"], "m")

    def test_T_SNP_18_a_plain_pane_cannot_take_review_at_end(self):
        with self.assertRaises(ValueError) as cm:
            self.mgr.create("fake", str(self.repo), review_at_end=True)
        self.assertIn("own branch", str(cm.exception))
        self.assertEqual([p for p in self.mgr.panes.values()], [])


class TheCreateRoute(RaeCase):
    """The New route over a real socket (the harness of test_worktree_routes)."""

    def setUp(self):
        super().setUp()
        import auth
        import hub
        from http.server import ThreadingHTTPServer
        self._old_mgr, hub.MGR = hub.MGR, self.mgr
        self.addCleanup(setattr, hub, "MGR", self._old_mgr)
        self.cookie = f"{hub.COOKIE}={auth.mint()}"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=60)
        raw = json.dumps(body).encode()
        c.request(method, path, body=raw, headers={
            "Content-Type": "application/json", "Content-Length": str(len(raw)),
            "Cookie": self.cookie})
        r = c.getresponse()
        out = json.loads(r.read() or b"{}")
        c.close()
        return r.status, out

    def test_T_SNP_18b_the_new_route_carries_the_option(self):
        st, out = self.req("POST", "/api/session/new",
                           {"agent": "fake", "cwd": str(self.repo), "reviewAtEnd": True})
        self.assertNotEqual(st, 200, out)
        self.assertIn("own branch", out.get("error", ""))
        st, out = self.req("POST", "/api/session/new",
                           {"agent": "fake", "cwd": str(self.repo), "worktree": True,
                            "reviewAtEnd": True})
        self.assertEqual(st, 200, out)
        self.assertTrue(out["pane"]["worktree"]["reviewAtEnd"])
        st, out = self.req("POST", "/api/session/new",
                           {"agent": "fake", "cwd": str(self.repo), "worktree": True,
                            "reviewAtEnd": "yes"})
        self.assertEqual(st, 200, out)
        self.assertFalse(out["pane"]["worktree"]["reviewAtEnd"])


if __name__ == "__main__":
    unittest.main()
