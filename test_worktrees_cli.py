#!/usr/bin/python3
"""`corral-light worktrees`: list, restore, purge, resolve (plan F9, WS5.1).

Real git in temp repos and a temp registry, never the user's. The CLI works on
the registry directly, so it runs with the hub down.

Collected by test_corral_light.py. Run alone: python3 -m unittest test_worktrees_cli -v
"""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORRAL_LIGHT_STATE", tempfile.mkdtemp(prefix="corral-light-test-"))
ROOT = Path(__file__).resolve().parent

import worktrees as wt                                    # noqa: E402
import worktrees_cli as cli                               # noqa: E402
from test_worktrees import CreateCase, _tree_snapshot     # noqa: E402


class Tty(io.StringIO):
    def isatty(self):
        return True


class CliCase(CreateCase):

    def run_cli(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        rc = cli.main(list(argv), out=out, err=err, stdin=stdin or io.StringIO(""))
        return rc, out.getvalue(), err.getvalue()

    def trashed(self, owner="pane0001"):
        e = self.make(owner=owner)
        (Path(e["path"]) / "a.txt").write_text("worth keeping\n")
        snap = wt.snapshot(e)
        r = wt.discard(e, snap["tree"], registry=self.reg)
        return self.reg.read(e["id"]), r

    def meta(self, pane, **kw):
        d = self.state / "panes" / pane
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"id": pane, **kw}))


class TheList(CliCase):

    def test_T_CLI_1_an_empty_registry_says_so(self):
        rc, out, _ = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertIn("no own-branch worktrees", out)

    def test_T_CLI_2_every_entry_with_its_phase_pane_and_way_out(self):
        live = self.make(title="fix login", owner="aaa111")
        gone, r = self.trashed(owner="bbb222")
        self.meta("aaa111", closed=False)
        self.meta("bbb222", closed=True)
        rc, out, _ = self.run_cli("list")
        self.assertEqual(rc, 0, out)
        for e in (live, gone):
            self.assertIn(e["id"], out)
            self.assertIn(e["branch"][len("refs/heads/"):], out)
        self.assertIn("active", out)
        self.assertIn("trashed", out)
        self.assertIn("pane aaa111 (open)", out)
        self.assertIn("pane bbb222 (archived)", out)
        self.assertIn(r["trash_path"], out)
        self.assertIn(f"corral-light worktrees restore {gone['id']}", out)
        self.assertIn(f"corral-light worktrees purge {gone['id']}", out)
        self.assertNotIn(live["common_dir"], out)

    def test_T_CLI_3_json_is_complete_and_never_carries_common_dir(self):
        e = self.make()
        rc, out, _ = self.run_cli("list", "--json")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual([x["id"] for x in d["worktrees"]], [e["id"]])
        self.assertEqual(d["orphans"], [])
        self.assertNotIn("common_dir", out)

    def test_T_CLI_4_orphans_are_listed_and_left_alone(self):
        e = self.make()
        stray = Path(e["path"]).parent / "stray"
        stray.mkdir()
        (stray / "x.txt").write_text("x\n")
        rc, out, _ = self.run_cli("list")
        self.assertIn(str(stray), out)
        self.assertIn("orphan", out)
        self.assertTrue((stray / "x.txt").exists())

    def test_T_CLI_5_list_writes_nothing(self):
        self.make()
        self.trashed(owner="p2")
        # A worktree deleted by hand: reconcile would mark it missing. list must not.
        gone = self.make(title="moved away", owner="p3")
        os.rename(gone["path"], self.tmp / "moved-away")
        before = {d: _tree_snapshot(d) for d in (self.state, self.repo / ".git", wt.worktree_root())}
        for args in ((), ("list",), ("list", "--json")):
            self.run_cli(*args)
        after = {d: _tree_snapshot(d) for d in (self.state, self.repo / ".git", wt.worktree_root())}
        self.assertEqual(after, before)

    def test_T_CLI_6_unknown_outcomes_are_flagged_with_the_command(self):
        e = self.make()
        op = self.reg.begin_op(e["id"], "push", url="u", ref="r", oid="o")
        self.reg.set_op(e["id"], op, state="unknown")
        rc, out, _ = self.run_cli()
        self.assertIn("unknown outcome", out)
        self.assertIn(f"corral-light worktrees resolve {e['id']}", out)

    def test_T_CLI_7_an_unreadable_entry_is_reported_not_fatal(self):
        self.make()
        (wt.registry_dir() / "wt-broken.json").write_text("{not json")
        rc, out, _ = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertIn("wt-broken", out)
        self.assertIn("unreadable", out)


class TheRestore(CliCase):

    def test_T_CLI_8_restore_brings_it_back_and_names_the_pane(self):
        e, r = self.trashed(owner="ccc333")
        rc, out, err = self.run_cli("restore", e["id"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.reg.read(e["id"])["phase"], "active")
        self.assertEqual(Path(e["path"], "a.txt").read_text(), "worth keeping\n")
        self.assertIn("ccc333", out)

    def test_T_CLI_9_restore_refuses_what_is_not_in_trash(self):
        e = self.make()
        rc, out, err = self.run_cli("restore", e["id"])
        self.assertEqual(rc, 2)
        self.assertIn("not in trash", err)

    def test_T_CLI_10_an_unknown_id_is_refused(self):
        for verb in ("restore", "purge", "resolve"):
            rc, _, err = self.run_cli(verb, "wt-nosuch")
            self.assertEqual(rc, 2, verb)
            self.assertIn("no worktree wt-nosuch", err)


class ThePurge(CliCase):

    def test_T_CLI_11_purge_needs_the_typed_branch_name(self):
        e, r = self.trashed()
        short = e["branch"][len("refs/heads/"):]
        rc, out, err = self.run_cli("purge", e["id"], stdin=io.StringIO(short + "\n"))
        self.assertEqual(rc, 2, "a pipe is not a person typing")
        self.assertIn(f"--confirm {short}", err)
        self.assertNotIn("type the branch name (", out)
        for args, stdin in ((("purge", e["id"], "--confirm", "nope"), None),
                            (("purge", e["id"]), Tty("wrong\n"))):
            rc, out, err = self.run_cli(*args, stdin=stdin)
            self.assertEqual(rc, 2, (args, out, err))
            self.assertTrue(Path(r["trash_path"], "a.txt").exists(), args)
        self.assertEqual(self.reg.read(e["id"])["phase"], "trashed")
        rc, out, err = self.run_cli("purge", e["id"], stdin=Tty(short + "\n"))
        self.assertEqual(rc, 0, err)
        self.assertIn(short, out)
        self.assertFalse(os.path.lexists(r["trash_path"]))
        self.assertEqual(self.reg.read(e["id"])["phase"], "purged")

    def test_T_CLI_12_purge_says_what_goes_and_keeps_the_recovery_ref(self):
        e, r = self.trashed()
        short = e["branch"][len("refs/heads/"):]
        rc, out, err = self.run_cli("purge", e["id"], "--confirm", short)
        self.assertEqual(rc, 0, err)
        self.assertIn(r["trash_path"], out)
        self.assertIn(r["recovery_ref"], out)
        self.assertEqual(wt.git(["cat-file", "-t", r["recovery_ref"]], cwd=self.repo).text.strip(),
                         "commit")

    def test_T_CLI_13_purge_refuses_an_active_worktree(self):
        e = self.make()
        short = e["branch"][len("refs/heads/"):]
        rc, out, err = self.run_cli("purge", e["id"], "--confirm", short)
        self.assertEqual(rc, 2)
        self.assertIn("only a discarded", err)
        self.assertNotIn("deletes", out, "refuse before announcing a deletion")
        self.assertTrue(Path(e["path"]).is_dir())


class TheResolve(CliCase):

    def unknown(self):
        e = self.make()
        op = self.reg.begin_op(e["id"], "push", url="git@example:r.git", ref="refs/heads/x", oid="abc")
        self.reg.set_op(e["id"], op, state="unknown")
        return e, op

    def test_T_CLI_14_resolve_without_an_op_lists_and_changes_nothing(self):
        e, op = self.unknown()
        before = (wt.registry_dir() / f"{e['id']}.json").read_bytes()
        rc, out, _ = self.run_cli("resolve", e["id"])
        self.assertEqual(rc, 0)
        self.assertIn(op, out)
        self.assertIn("push", out)
        self.assertIn("git@example:r.git", out)
        self.assertEqual((wt.registry_dir() / f"{e['id']}.json").read_bytes(), before)

    def test_T_CLI_15_resolve_an_op_clears_the_block(self):
        import sessions
        e, op = self.unknown()
        m = sessions.Manager.__new__(sessions.Manager)
        self.assertIn("unknown outcome", m._worktree_blocked_reason(self.reg.read(e["id"])))
        rc, out, err = self.run_cli("resolve", e["id"], "--op", op)
        self.assertEqual(rc, 0, err)
        got = [o for o in self.reg.read(e["id"])["ops"] if o["op_id"] == op][0]
        self.assertEqual((got["state"], got["stage"]), ("done", "resolved_by_user"))
        self.assertIsNone(m._worktree_blocked_reason(self.reg.read(e["id"])))

    def test_T_CLI_16_resolve_refuses_an_op_that_is_not_unknown(self):
        e, op = self.unknown()
        rc, _, err = self.run_cli("resolve", e["id"], "--op", "op-nosuch")
        self.assertEqual(rc, 2)
        done = self.reg.begin_op(e["id"], "commit")
        self.reg.set_op(e["id"], done, state="done")
        rc, _, err = self.run_cli("resolve", e["id"], "--op", done)
        self.assertEqual(rc, 2)
        self.assertIn("not unknown", err)


class TheDispatch(unittest.TestCase):

    def test_T_CLI_17_the_launcher_routes_worktrees_and_documents_it(self):
        text = (ROOT / "corral-light").read_text(encoding="utf-8")
        self.assertIn('worktrees) shift; exec "$PY" "$D/worktrees_cli.py" "$@"', text)
        self.assertIn("corral-light worktrees", text.split("set -euo pipefail")[0])


if __name__ == "__main__":
    unittest.main()
