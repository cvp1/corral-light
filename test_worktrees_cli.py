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


class TheDoctor(CliCase):
    """`corral-light doctor`: git version, root filesystem, trash size (WS5.2)."""

    def lines(self):
        import doctor
        return doctor.worktree_lines(registry=self.reg)

    def test_T_DOC_1_the_git_version_against_the_minimum(self):
        from unittest import mock
        blob = "\n".join(self.lines())
        v = ".".join(map(str, wt.git_version()))
        self.assertIn(f"git {v}", blob)
        with mock.patch.object(wt, "git_version", lambda: (2, 30, 1)):
            old = "\n".join(self.lines())
        self.assertIn("git 2.30.1", old)
        self.assertIn("needs 2.38", old)
        self.assertRegex(old, r"(?m)^\s*--\s+git 2\.30\.1")

    def test_T_DOC_2_the_root_filesystem_and_tmpfs(self):
        from unittest import mock
        self.assertRegex("\n".join(self.lines()), r"(?m)^\s*ok\s+worktree root .* on btrfs")
        with mock.patch.object(wt, "_fstype", lambda p: "tmpfs"):
            blob = "\n".join(self.lines())
        self.assertRegex(blob, r"(?m)^\s*--\s+worktree root .* on tmpfs")
        self.assertIn("CORRAL_LIGHT_WORKTREES", blob)

    def test_T_DOC_3_counts_and_the_trash_size_with_the_way_to_free_it(self):
        self.make(owner="p1")
        e, r = self.trashed(owner="p2")
        Path(r["trash_path"], "big.bin").write_bytes(b"x" * (3 << 20))
        blob = "\n".join(self.lines())
        self.assertIn("2 worktrees (1 active, 1 trashed)", blob)
        self.assertRegex(blob, r"trash holds 3\.0 MiB")
        self.assertIn("corral-light worktrees purge", blob)

    def test_T_DOC_4_unknown_outcomes_and_orphans_are_flagged(self):
        e = self.make()
        op = self.reg.begin_op(e["id"], "push", url="u", ref="r", oid="o")
        self.reg.set_op(e["id"], op, state="unknown")
        (Path(e["path"]).parent / "stray").mkdir()
        blob = "\n".join(self.lines())
        self.assertIn(f"corral-light worktrees resolve {e['id']}", blob)
        self.assertIn("1 orphan", blob)

    def test_T_DOC_5_doctor_writes_nothing_and_reports_into_the_main_list(self):
        import doctor
        self.make()
        self.trashed(owner="p2")
        before = {d: _tree_snapshot(d) for d in (self.state, self.repo / ".git")}
        lines = doctor.report(root=self.tmp, agents=[])
        self.assertEqual({d: _tree_snapshot(d) for d in (self.state, self.repo / ".git")}, before)
        self.assertIn("own branches", "\n".join(lines))

    def test_M4_doctor_lists_claude_transcripts_left_by_worktrees_that_are_gone(self):
        from unittest import mock
        import re
        home = self.tmp / "home"
        projects = home / ".claude" / "projects"
        live = self.make(owner="p1")
        gone, r = self.trashed(owner="p2")
        short = gone["branch"][len("refs/heads/"):]
        wt.purge(self.reg.read(gone["id"]), short, registry=self.reg)
        slug = lambda p: re.sub(r"[^A-Za-z0-9]", "-", p)            # noqa: E731
        for e in (live, gone):
            (projects / slug(e["path"])).mkdir(parents=True)
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            blob = "\n".join(self.lines())
        [line] = [x for x in blob.splitlines() if "~/.claude/projects" in x]
        listed = line.split("never deleted): ", 1)[1].split(", ")
        self.assertEqual(listed, [slug(gone["path"])],
                         "only a gone worktree's transcripts are leftovers, never a live one's")
        self.assertIn("~/.claude/projects", blob)
        self.assertTrue((projects / slug(gone["path"])).is_dir(), "doctor lists, never deletes")

    def test_M_doctor_names_the_git_binary_it_uses(self):
        blob = "\n".join(self.lines())
        self.assertIn(wt.GIT_BIN, blob)
        self.assertTrue(os.path.isabs(wt.GIT_BIN), "git is resolved once, to a path")

    def test_M_doctor_reads_the_filesystem_without_proc(self):
        from unittest import mock
        mount = ("/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)\n"
                 "/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse)\n"
                 "map -hosts on /System/Volumes/Data/net (autofs, automounted, nobrowse)\n")
        self.assertEqual(wt._fstype_from_mount(mount, "/System/Volumes/Data/srv/x"), "apfs")
        self.assertEqual(wt._fstype_from_mount(mount, "/opt/x"), "apfs")
        self.assertEqual(wt._fstype_from_mount(mount, "/System/Volumes/Data/net/box"), "autofs")
        self.assertIsNone(wt._fstype_from_mount("", "/x"))

    def test_T_DOC_6_an_empty_install_is_one_quiet_line_each(self):
        blob = "\n".join(self.lines())
        self.assertIn("no own-branch worktrees yet", blob)
        self.assertNotIn("trash holds", blob)


class TheDispatch(unittest.TestCase):

    def test_T_CLI_17_the_launcher_routes_worktrees_and_documents_it(self):
        text = (ROOT / "corral-light").read_text(encoding="utf-8")
        self.assertIn('worktrees) shift; exec "$PY" "$D/worktrees_cli.py" "$@"', text)
        self.assertIn("corral-light worktrees", text.split("set -euo pipefail")[0])


if __name__ == "__main__":
    unittest.main()
