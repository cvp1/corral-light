#!/usr/bin/python3
"""Tests for `corral-light lanes update`.

The npm and download steps are stubbed; the file swap is real, on a scratch
checkout. Every refusal also checks that nothing on disk moved.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from testkit.scratch import default_state  # noqa: E402
default_state("corral-light-test-")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import install_antigravity_acp as inst  # noqa: E402
import lanes  # noqa: E402

CODEX = lanes.NPM["codex"]["pkg"]
CLAUDE = lanes.NPM["claude"]["pkg"]


def digest_tree(path):
    """Bytes of every file under path (symlinks by target), for byte-identity."""
    h = hashlib.sha256()
    path = Path(path)
    for p in sorted(path.rglob("*")) if path.is_dir() else [path]:
        h.update(str(p.relative_to(path) if p != path else p.name).encode())
        if p.is_symlink():
            h.update(os.readlink(p).encode())
        elif p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def write_tree(nm, versions):
    for pkg, (ver, binname) in versions.items():
        d = nm / pkg
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.json").write_text(json.dumps({"name": pkg, "version": ver}))
        (nm / ".bin").mkdir(exist_ok=True)
        (nm / ".bin" / binname).write_text(f"#!adapter {pkg} {ver}\n")


class Checkout(unittest.TestCase):
    """A scratch checkout: spike/package.json + lock + node_modules."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lanes-test-"))
        self.root = self.tmp / "checkout"
        self.spike = self.root / "spike"
        self.spike.mkdir(parents=True)
        self.trash = self.tmp / "Trash"
        (self.spike / "package.json").write_text(json.dumps({"dependencies": {
            CLAUDE: "^0.84.0", CODEX: "2.0.1", "@other/thing": "^1.0.0"}}, indent=2))
        (self.spike / "package-lock.json").write_text('{"lock": "old"}')
        write_tree(self.spike / "node_modules", {CODEX: ("2.0.1", "codex-acp"),
                                                 CLAUDE: ("0.84.0", "claude-agent-acp")})
        shutil.copy2(ROOT / "install_antigravity_acp.py", self.root)
        self.calls, self.probes, self.notes = [], [], []
        self.latest = "2.1.1"
        self.extra_change = False

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_stub(self, argv, timeout, cwd=None, env=None):
        self.calls.append(list(argv))
        if argv[:2] == ["npm", "view"]:
            return 0, self.latest + "\n", ""
        if argv[:2] == ["npm", "install"]:
            scratch = Path(argv[argv.index("--prefix") + 1])
            pkg, ver = argv[argv.index("--prefix") + 2].rsplit("@", 1)
            pj = json.loads((scratch / "package.json").read_text())
            pj["dependencies"][pkg] = ver
            if self.extra_change:
                pj["dependencies"]["@other/thing"] = "^9.9.9"
            (scratch / "package.json").write_text(json.dumps(pj, indent=2))
            (scratch / "package-lock.json").write_text('{"lock": "new"}')
            write_tree(scratch / "node_modules", {
                CODEX: (ver if pkg == CODEX else "2.0.1", "codex-acp"),
                CLAUDE: (ver if pkg == CLAUDE else "0.84.0", "claude-agent-acp")})
            return 0, "added 1 package", ""
        if argv[1:] == ["update", "--check", "--json"]:
            return 0, json.dumps({"currentVersion": "1.0.44", "latestVersion": "1.0.46"}), ""
        return 99, "", f"unexpected argv {argv}"

    def probe_stub(self, ok):
        def fn(root, lane, overrides):
            self.probes.append((lane, dict(overrides)))
            for v in overrides.values():   # the staged adapter really exists
                self.assertTrue(Path(v).exists(), v)
            return {"ok": ok, "handshake": ok, "reply": "pong" if ok else "",
                    "models": ["m1", "m2"] if ok else [], "model": "m1",
                    "why": None if ok else "Authentication required"}
        return fn

    def update(self, lane, ok=True, **kw):
        return lanes.update(lane, root=self.root, run=self.run_stub,
                            probe_fn=self.probe_stub(ok),
                            notify_fn=lambda t, b: self.notes.append((t, b)),
                            trash=self.trash, **kw)

    def snapshot(self):
        return {n: digest_tree(self.spike / n)
                for n in ("package.json", "package-lock.json", "node_modules")}

    def leftovers(self):
        return sorted(p.name for p in self.spike.iterdir() if p.name.startswith(".lanes-stage-"))


class AFailingProbe(Checkout):
    def test_leaves_the_pin_the_lock_and_the_live_adapter_byte_identical(self):
        before = self.snapshot()
        rec = self.update("codex", ok=False)
        self.assertEqual(rec["outcome"], "red")
        self.assertIn("Authentication required", rec["why"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.leftovers(), [], "staging dir left behind")
        self.assertFalse(self.trash.exists() and any(self.trash.iterdir()))
        self.assertEqual(len(self.notes), 1, "red must notify once")
        self.assertEqual(len(self.probes), 1)

    def test_a_staged_tree_that_moves_two_pins_is_red_and_unprobed(self):
        self.extra_change = True
        before = self.snapshot()
        rec = self.update("codex")
        self.assertEqual(rec["outcome"], "red")
        self.assertIn("@other/thing", rec["why"])
        self.assertEqual(self.probes, [], "probed a tree that was already wrong")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.leftovers(), [])

    def test_npm_view_failing_is_red_not_current(self):
        self.latest = "not a version"
        before = self.snapshot()
        rec = self.update("codex")
        self.assertEqual(rec["outcome"], "red")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(c[:2] == ["npm", "install"] for c in self.calls))


class APassingProbe(Checkout):
    def test_changes_exactly_one_pin(self):
        old_pj = json.loads((self.spike / "package.json").read_text())
        self.latest = "0.85.0"
        rec = self.update("claude")
        self.assertEqual(rec["outcome"], "updated", rec)
        new_pj = json.loads((self.spike / "package.json").read_text())
        changed = {k for k in old_pj["dependencies"]
                   if old_pj["dependencies"][k] != new_pj["dependencies"].get(k)}
        self.assertEqual(changed, {CLAUDE})
        self.assertEqual(new_pj["dependencies"][CLAUDE], "0.85.0", "pin is not exact")
        self.assertEqual((self.spike / "package-lock.json").read_text(), '{"lock": "new"}')
        self.assertEqual(lanes.npm_installed(self.root, "claude"), "0.85.0")
        self.assertEqual(lanes.npm_installed(self.root, "codex"), "2.0.1")
        self.assertEqual(self.probes[0][1], {"CORRAL_CLAUDE_ADAPTER": self.probes[0][1][
            "CORRAL_CLAUDE_ADAPTER"]})
        self.assertIn(".lanes-stage-", self.probes[0][1]["CORRAL_CLAUDE_ADAPTER"])
        prev = Path(rec["replaced"])
        self.assertEqual(prev.parent, self.trash)
        self.assertEqual(json.loads((prev / CLAUDE / "package.json").read_text())["version"],
                         "0.84.0", "the replaced tree was not kept")
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.notes, [])

    def test_a_worktree_symlink_is_replaced_not_followed(self):
        real = self.tmp / "live-node_modules"
        os.rename(self.spike / "node_modules", real)
        os.symlink(real, self.spike / "node_modules")
        before = digest_tree(real)
        rec = self.update("codex")
        self.assertEqual(rec["outcome"], "updated", rec)
        self.assertEqual(digest_tree(real), before, "wrote through the symlink")
        self.assertFalse((self.spike / "node_modules").is_symlink())


class TheLiveHub(Checkout):
    def test_its_pid_and_panes_are_unchanged_across_an_update(self):
        state = self.tmp / "state"
        (state / "panes" / "abc").mkdir(parents=True)
        (state / "panes" / "abc" / "meta.json").write_text('{"agent": "codex"}')
        hub = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        old_adapter = (self.spike / "node_modules" / ".bin" / "codex-acp").open("rb")
        try:
            before = digest_tree(state)
            rec = self.update("codex")
            self.assertEqual(rec["outcome"], "updated", rec)
            self.assertIsNone(hub.poll(), "the live hub process was disturbed")
            self.assertEqual(digest_tree(state), before)
            # A running adapter keeps the bytes it opened; a new spawn gets the new tree.
            self.assertIn(b"2.0.1", old_adapter.read())
            self.assertIn("2.1.1", (self.spike / "node_modules" / ".bin" / "codex-acp")
                          .read_text())
        finally:
            old_adapter.close()
            hub.kill()
            hub.wait()


class AlreadyCurrent(Checkout):
    def test_is_a_no_op_that_reports_current(self):
        self.latest = "2.0.1"
        before = self.snapshot()
        rec = self.update("codex")
        self.assertEqual(rec["outcome"], "current")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.probes, [])
        self.assertEqual([c[:2] for c in self.calls], [["npm", "view"]])
        self.assertEqual(self.notes, [])


class Grok(Checkout):
    def test_reports_and_probes_and_never_installs(self):
        before = self.snapshot()
        with mock.patch("grok_launcher.resolve_grok", return_value="/fake/grok"):
            rec = self.update("grok")
        self.assertEqual(rec["outcome"], "checked")
        self.assertEqual((rec["installed"], rec["latest"]), ("1.0.44", "1.0.46"))
        self.assertIn("grok update", rec["why"])
        self.assertEqual(self.probes, [("grok", {})])
        self.assertEqual(self.calls, [["/fake/grok", "update", "--check", "--json"]])
        self.assertEqual(self.snapshot(), before)

    def test_a_failing_probe_is_red(self):
        with mock.patch("grok_launcher.resolve_grok", return_value="/fake/grok"):
            rec = self.update("grok", ok=False)
        self.assertEqual(rec["outcome"], "red")
        self.assertEqual(len(self.notes), 1)


class Antigravity(Checkout):
    def setUp(self):
        super().setUp()
        self.runtime = self.tmp / "lib" / "antigravity-acp"
        self.runtime.mkdir(parents=True)
        for name in inst.FILES:
            (self.runtime / name).write_text("old build")
        self.patch = mock.patch.object(inst, "RUNTIME", self.runtime)
        self.patch.start()
        self.fetched = []

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def fetch_stub(self, row, workdir, expect_sha):
        self.fetched.append((row["release"], row["url"], expect_sha))
        extract = Path(workdir) / "extract"
        extract.mkdir()
        for name in inst.FILES:
            (extract / name).write_text("new build")
        return extract, "ab" * 32

    def state(self):
        return digest_tree(self.runtime), (self.root / "install_antigravity_acp.py").read_text()

    def test_without_a_release_name_it_is_unknown_and_fetches_nothing(self):
        before = self.state()
        rec = self.update("antigravity", fetch=self.fetch_stub)
        self.assertEqual(rec["outcome"], "unknown")
        self.assertEqual(self.fetched, [])
        self.assertEqual(self.state(), before)

    def test_a_malformed_release_name_is_refused_before_any_fetch(self):
        before = self.state()
        rec = self.update("gemini", release="../../evil", fetch=self.fetch_stub)
        self.assertEqual(rec["outcome"], "red")
        self.assertEqual(self.fetched, [])
        self.assertEqual(self.state(), before)

    @unittest.skipIf(inst.release_for() is None, "no pinned row for this host")
    def test_green_rewrites_this_hosts_row_and_swaps_the_runtime(self):
        row = inst.release_for()
        rec = self.update("gemini", release="agy_acp_server_20261101_01_RC01",
                          fetch=self.fetch_stub)
        self.assertEqual(rec["outcome"], "updated", rec)
        name, url, expect = self.fetched[0]
        self.assertIsNone(expect, "a new release is trust-on-first-download")
        self.assertTrue(name.startswith("agy_acp_server_20261101_01_RC01-"))
        self.assertIn(f"/{row['dir']}/", url)
        text = (self.root / "install_antigravity_acp.py").read_text()
        self.assertIn(f'"release": "{name}"', text)
        self.assertIn('"sha256": "' + "ab" * 32 + '",  # TOFU', text)
        self.assertNotIn(row["sha256"], text)
        self.assertEqual((self.runtime / inst.FILES[0]).read_text(), "new build")
        self.assertTrue(Path(rec["replaced"]).parent == self.trash)
        compile(text, "installer", "exec")

    @unittest.skipIf(inst.release_for() is None, "no pinned row for this host")
    def test_red_leaves_the_row_and_the_runtime_alone(self):
        before = self.state()
        rec = self.update("gemini", ok=False, release="agy_acp_server_20261101_01_RC01",
                          fetch=self.fetch_stub)
        self.assertEqual(rec["outcome"], "red")
        self.assertEqual(self.state(), before)
        self.assertEqual([p for p in self.runtime.parent.iterdir()
                          if p.name.startswith(".lanes-stage-")], [])


class TheCheck(Checkout):
    """`lanes check`: read only, unknown on failure, edge-triggered."""

    def setUp(self):
        super().setUp()
        self.state = self.tmp / "state"
        self.grok = mock.patch("grok_launcher.resolve_grok", return_value="/fake/grok")
        self.grok.start()
        self.heads = []

    def tearDown(self):
        self.grok.stop()
        super().tearDown()

    def head(self, codes):
        def fn(url):
            self.heads.append(url)
            return codes(url)
        return fn

    def rows(self, head=None):
        before = self.snapshot()
        rows = lanes.check(root=self.root, run=self.run_stub,
                           head=head or self.head(lambda u: 200 if "20260818" in u else 404),
                           today=lanes.datetime(2026, 10, 1).date())
        self.assertEqual(self.snapshot(), before, "a check changed the checkout")
        self.assertFalse(any(c[:2] == ["npm", "install"] or c[1:2] == ["update"]
                             and "--check" not in c for c in self.calls))
        return {r["lane"]: r for r in rows}

    def job(self, rows, hour):
        notes = []
        out = lanes.run_job(list(rows.values()), state_dir=self.state,
                            notify_fn=lambda t, b: notes.append(b),
                            now=lanes.datetime(2026, 10, 1, hour))
        return out, notes

    def test_a_failing_check_is_unknown_never_current(self):
        self.latest = "garbage"
        def broken(argv, timeout, cwd=None, env=None):
            self.calls.append(list(argv))
            return (1, "", "boom") if argv[0] != "npm" else self.run_stub(argv, timeout)
        r = {x["lane"]: x for x in lanes.check(root=self.root, run=broken,
                                                head=lambda u: None)}
        self.assertEqual({k: v["status"] for k, v in r.items()},
                         {"codex": "unknown", "claude": "unknown",
                          "gemini": "unknown", "grok": "unknown"})

    @unittest.skipIf(inst.release_for() is None, "no pinned row for this host")
    def test_gemini_finds_a_newer_build_and_never_claims_current(self):
        r = self.rows(self.head(lambda u: 200 if ("20260818" in u or "20260920" in u)
                                else 404))
        self.assertEqual(r["gemini"]["status"], "behind")
        self.assertIn("--release agy_acp_server_20260920_01_RC01", r["gemini"]["why"])
        self.heads.clear()
        r = self.rows()
        self.assertEqual(r["gemini"]["status"], "unknown")
        self.assertLessEqual(len(self.heads), lanes.MAX_HEADS + 1, "unbounded scan")

    def test_one_notice_per_edge(self):
        self.latest = "2.0.1"                        # codex current, claude behind
        rows = self.rows()
        self.assertEqual((rows["codex"]["status"], rows["claude"]["status"]),
                         ("current", "behind"))
        out, notes = self.job(rows, 6)
        self.assertEqual(len([n for n in notes if n.startswith("claude")]), 1)
        self.assertTrue(any(n.startswith("grok is behind") for n in notes))
        self.assertFalse(any(n.startswith("codex") for n in notes))
        self.assertTrue(out.startswith("FINDINGS: "), out)
        out, notes = self.job(self.rows(), 7)        # same facts, next day
        self.assertEqual(notes, [], "a held state re-notified")
        self.latest = "0.84.0"                       # claude current, codex behind
        out, notes = self.job(self.rows(), 8)
        self.assertIn("claude is current again at 0.84.0", notes)
        self.assertEqual(len([n for n in notes if n.startswith("codex is behind")]), 1)

    def test_steady_state_emits_nothing(self):
        rows = {k: dict(v, status="current", why="") for k, v in self.rows().items()}
        self.assertEqual(self.job(rows, 6), ("", []))
        self.assertEqual(self.job(rows, 7), ("", []))
        self.assertTrue((self.state / lanes.CHECK_STATE).is_file())

    def test_unknown_is_findings_but_never_a_notice(self):
        rows = self.rows()
        out, notes = self.job({k: dict(v, status="unknown") for k, v in rows.items()}, 6)
        self.assertEqual(notes, [])
        self.assertTrue(out.startswith("FINDINGS: "))


class TheCliVerb(unittest.TestCase):
    def test_an_unknown_lane_is_a_usage_error_and_notifies_nobody(self):
        with mock.patch.object(lanes, "_notify") as n, \
                mock.patch("sys.stderr"), self.assertRaises(SystemExit) as e:
            lanes.main(["update", "nope"])
        self.assertEqual(e.exception.code, 2)
        n.assert_not_called()

    def test_the_wrapper_dispatches_lanes(self):
        p = subprocess.run([str(ROOT / "corral-light"), "lanes", "--help"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("update", p.stdout)


if __name__ == "__main__":
    unittest.main()


class TheProbeReadsTheFullState(unittest.TestCase):
    """The probe's model list lives in the pane's `config`, which a cursor-
    only /api/state (the poller's light delta, since the 2026-10-04 perf
    contract) leaves out. Every Claude update was refused with "no model
    list" until the probe asked for the full document."""

    def test_full_state_is_asked_for(self):
        asked = []
        pane = {"id": "p1", "model": "fable",
                "config": {"model": {"value": "fable",
                                     "options": [{"value": "fable"}, {"value": "haiku"}]}}}

        class Hub:
            def get(self, path, timeout=None):
                asked.append(path)
                full = "full=1" in path
                return {"panes": [pane if full else {"id": "p1", "model": "fable"}],
                        **({} if full else {"light": True})}

            def post(self, path, body):
                return {}

        import consult
        import lane_probe
        with mock.patch.object(lane_probe, "probe", return_value={"ok": True}), \
                mock.patch.object(consult, "connect", return_value=Hub()), \
                mock.patch.object(consult, "open_pane", return_value={"id": "p1"}), \
                mock.patch.object(consult, "send_and_wait",
                                  return_value={"complete": True, "text": "pong"}):
            rec = lanes.probe_client("claude", "http://127.0.0.1:1", "/tmp")
        self.assertTrue(rec["ok"], rec.get("why"))
        self.assertEqual(rec["models"], ["fable", "haiku"])
        self.assertTrue(any("full=1" in a for a in asked), asked)
