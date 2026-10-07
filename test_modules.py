"""The module seam: manifest, install and pin, verification, runner, isolation
(docs/finops-module-plan.md §4, §5, §8.1). Fixture module: testkit/modules/probe.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import module_sandbox  # noqa: E402
import modules  # noqa: E402

FIXTURE = ROOT / "testkit" / "modules" / "probe"
GIT_ID = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
          "-c", "init.defaultBranch=main", "-c", "commit.gpgSign=false"]
HAVE_BWRAP = bool(shutil.which(os.environ.get("CORRAL_BWRAP", "bwrap"))) and \
    sys.platform.startswith("linux")


def git(cwd, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git"] + GIT_ID + list(args), cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout


def quiet(*_a, **_k):
    pass


class Base(unittest.TestCase):
    """A private state dir, config dir and home for each test."""

    def setUp(self):
        # Under $HOME, not /tmp: the sandbox binds by real path either way,
        # but $HOME is where a real install lives.
        self.tmp = Path(tempfile.mkdtemp(prefix="corral-mod-test-",
                                         dir=os.path.expanduser("~/.cache")
                                         if os.path.isdir(os.path.expanduser("~/.cache"))
                                         else None))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = self.tmp / "state"
        self.config = self.tmp / "config"
        self.home = self.tmp / "home"
        for d in (self.state, self.config, self.home):
            d.mkdir()
        # What a real state dir holds that a module must never see.
        (self.state / "session.key").write_text("SESSION-KEY-SENTINEL")
        (self.state / "pairing.json").write_text("{}")
        pane = self.state / "panes" / "p1"
        pane.mkdir(parents=True)
        (pane / "events.jsonl").write_text('{"kind":"user","text":"PROMPT-SENTINEL"}\n')
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh" / "id_test").write_text("SSH-SENTINEL")
        self._patch(modules, "STATE", self.state)
        self._patch(modules, "CONFIG", self.config)
        self._patch(modules, "INDEX", self.tmp / "index.json")
        self._patch(modules, "RUNNER", None)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def _patch(self, obj, attr, value):
        p = mock.patch.object(obj, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def make_repo(self, name="src", mutate=None, commit=True):
        src = self.tmp / name
        shutil.copytree(FIXTURE, src)
        if mutate:
            mutate(src)
        git(src, "init", "-q")
        if commit:
            git(src, "add", "-A")
            git(src, "commit", "-q", "-m", "fixture")
        return src

    def install(self, src=None, **kw):
        src = src or self.make_repo()
        kw.setdefault("confirm", "probe")
        return modules.add(str(src), out=quiet, **kw)

    def set_mode(self, mode, *args):
        cfg = modules.config_dir("probe")
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.toml").write_text("\n".join((mode,) + args) + "\n")


def good_manifest(**over):
    m = json.loads((FIXTURE / "module.json").read_text())
    m.update(over)
    return m


class TheManifest(unittest.TestCase):
    def refused(self, obj, needle, root=FIXTURE):
        with self.assertRaises(modules.ModuleError) as cm:
            modules.validate_manifest(obj, root)
        self.assertIn(needle, str(cm.exception))

    def test_the_fixture_is_valid(self):
        m = modules.validate_manifest(good_manifest(), FIXTURE)
        self.assertEqual(m["name"], "probe")
        self.assertEqual(m["collector"]["timeout_s"], 5)

    def test_bad_names(self):
        for bad in ("Probe", "1probe", "pro_be", "x" * 33, "", None, "../x"):
            self.refused(good_manifest(name=bad), "must match")

    def test_a_core_verb_is_refused(self):
        for verb in ("doctor", "module", "update", "serve", "pair"):
            self.refused(good_manifest(name=verb), "corral-light verb")

    def test_unknown_key(self):
        self.refused(good_manifest(extra=1), "unknown keys")
        m = good_manifest()
        m["collector"]["shell"] = "sh"
        self.refused(m, "unknown keys")

    def test_dotdot_and_absolute_scripts(self):
        for script in ("../collector.py", "a/../collector.py", "/usr/bin/x.py", "./collector.py"):
            m = good_manifest()
            m["collector"]["script"] = script
            self.refused(m, "script")

    def test_interpreter_options_are_impossible(self):
        for script in ("-c", "-m", "-cimport os", "collector"):
            m = good_manifest()
            m["collector"]["script"] = script
            self.refused(m, "script")

    def test_a_symlinked_script_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "m"
            shutil.copytree(FIXTURE, root)
            (root / "real.py").write_text("")
            os.remove(root / "collector.py")
            os.symlink("real.py", root / "collector.py")
            self.refused(good_manifest(), "symlink", root)

    def test_unknown_reads_and_reports(self):
        self.refused(good_manifest(reads=["home"]), "reads")
        self.refused(good_manifest(vendor_reports=["claude-usage"]), "vendor_reports")

    def test_network_must_be_none(self):
        self.refused(good_manifest(network=["api.example.com"]), "network")
        self.refused(good_manifest(network="host"), "network")

    def test_unsupported_core_api(self):
        self.refused(good_manifest(core_api=2), "core_api")
        self.refused(good_manifest(schema="x"), "schema")

    def test_nothing_is_written_on_refusal(self):
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(modules, "STATE", Path(t) / "s"), \
                    mock.patch.object(modules, "CONFIG", Path(t) / "c"):
                src = Path(t) / "src"
                shutil.copytree(FIXTURE, src)
                m = good_manifest(name="doctor")
                (src / "module.json").write_text(json.dumps(m))
                git(src, "init", "-q")
                git(src, "add", "-A")
                git(src, "commit", "-q", "-m", "x")
                with self.assertRaises(modules.ModuleError):
                    modules.add(str(src), confirm="doctor", out=quiet)
                self.assertFalse((Path(t) / "c" / "modules.json").exists())
                left = list((Path(t) / "s" / "modules").glob("*/*")) \
                    if (Path(t) / "s" / "modules").exists() else []
                self.assertEqual([p for p in left if not p.name.startswith(".")], [])


class InstallAndPin(Base):
    def test_from_a_local_path(self):
        self.assertEqual(self.install(), "probe")
        pin = modules.load_pins()["probe"]
        self.assertTrue(pin["enabled"])
        gen = modules.module_dir("probe") / pin["commit"]
        self.assertTrue((gen / "collector.py").is_file())
        self.assertFalse((gen / ".git").exists())
        self.assertEqual((modules.module_dir("probe") / "current").read_text().strip(),
                         pin["commit"])

    def test_from_a_file_url(self):
        src = self.make_repo()
        self.install(src="file://" + str(src))
        self.assertIn("probe", modules.load_pins())

    def test_confirmation_is_the_typed_name(self):
        with self.assertRaises(modules.ModuleError):
            self.install(confirm="yes")
        self.assertEqual(modules.load_pins(), {})

    def test_a_dirty_tree_is_refused(self):
        src = self.make_repo()
        (src / "collector.py").write_text("# changed\n")
        with self.assertRaises(modules.ModuleError) as cm:
            self.install(src)
        self.assertIn("uncommitted", str(cm.exception))

    def test_a_stray_untracked_file_is_refused(self):
        src = self.make_repo()
        (src / "stray.py").write_text("")
        with self.assertRaises(modules.ModuleError):
            self.install(src)

    def test_a_committed_symlink_is_refused(self):
        src = self.make_repo(mutate=lambda s: os.symlink("/etc/passwd", s / "link"))
        with self.assertRaises(modules.ModuleError) as cm:
            self.install(src)
        self.assertIn("symlink", str(cm.exception))

    def test_a_pth_file_is_refused(self):
        src = self.make_repo(mutate=lambda s: (s / "evil.pth").write_text("import os\n"))
        with self.assertRaises(modules.ModuleError) as cm:
            self.install(src)
        self.assertIn("evil.pth", str(cm.exception))
        src2 = self.make_repo("src2", mutate=lambda s: (s / "sitecustomize.py").write_text(""))
        with self.assertRaises(modules.ModuleError):
            self.install(src2)

    def test_a_git_hook_never_runs(self):
        marker = self.tmp / "hook-ran"
        src = self.make_repo()
        hook = src / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        # A hook path configured inside the repository is ignored too.
        git(src, "config", "core.hooksPath", str(src / ".git" / "hooks"))
        self.install(src)
        self.assertFalse(marker.exists(), "a hook ran during install")

    def test_the_digest_ignores_dot_git(self):
        src = self.make_repo()
        self.install(src)
        shutil.rmtree(src / ".git")
        self.assertEqual(modules.tree_digest(src), modules.load_pins()["probe"]["digest"])

    def test_a_short_name_resolves_through_the_index(self):
        src = self.make_repo()
        (self.tmp / "index.json").write_text(json.dumps(
            {"modules": {"probe": {"url": "file://" + str(src)}}}))
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            self.assertEqual(modules.add("probe", confirm="probe", out=quiet), "probe")
        finally:
            os.chdir(cwd)

    def test_the_first_party_index_is_valid(self):
        d = json.loads((ROOT / "modules" / "index.json").read_text())
        for name, entry in d["modules"].items():
            modules.check_name(name)
            self.assertTrue(entry["url"].startswith("https://"))


class Tamper(Base):
    def setUp(self):
        super().setUp()
        self.install()
        self.set_mode("ok")
        self.gen = modules.module_dir("probe") / modules.load_pins()["probe"]["commit"]

    def change_a_byte(self):
        p = self.gen / "NOTES.txt"
        p.write_bytes(p.read_bytes()[:-1] + b"!")

    def test_the_collector_is_refused(self):
        self.change_a_byte()
        st = modules.run_collector("probe")
        self.assertEqual(st["state"], "failing")
        self.assertIn("changed on disk", st["error"])
        self.assertFalse(modules.load_pins()["probe"]["enabled"])

    def test_cli_and_doctor_are_refused(self):
        self.change_a_byte()
        for entry in ("cli", "doctor"):
            with self.assertRaises(modules.ModuleError):
                modules.build_run("probe", entry, interactive=True)

    def test_a_new_file_is_caught(self):
        (self.gen / "extra.py").write_text("")
        with self.assertRaises(modules.ModuleError):
            modules.verify("probe")

    def test_a_good_snapshot_survives_a_later_tamper(self):
        self.assertEqual(modules.run_collector("probe")["state"], "ok")
        self.change_a_byte()
        modules.run_collector("probe")
        d = modules.detail("probe")
        self.assertEqual(d["state"], "disabled")
        self.assertIsNotNone(d["snapshot"])
        self.assertIn("changed on disk", d["error"])


class UpdateAndRollback(Base):
    def setUp(self):
        super().setUp()
        self.src = self.make_repo()
        self.install(self.src)
        self.first = modules.load_pins()["probe"]["commit"]

    def new_commit(self, text="v2\n"):
        (self.src / "NOTES.txt").write_text(text)
        git(self.src, "commit", "-qam", "v2")
        return git(self.src, "rev-parse", "HEAD").strip()

    def test_update_then_rollback(self):
        second = self.new_commit()
        self.assertTrue(modules.update("probe", out=quiet))
        self.assertEqual(modules.load_pins()["probe"]["commit"], second)
        modules.verify("probe")
        modules.rollback("probe", out=quiet)
        self.assertEqual(modules.load_pins()["probe"]["commit"], self.first)
        modules.verify("probe")

    def test_a_failed_stage_leaves_the_previous_generation(self):
        with self.assertRaises(modules.ModuleError):
            modules.update("probe", ref="no-such-ref", out=quiet)
        self.assertEqual(modules.load_pins()["probe"]["commit"], self.first)
        modules.verify("probe")

    def test_a_bad_manifest_in_the_update_is_refused(self):
        (self.src / "module.json").write_text(json.dumps(good_manifest(network="host")))
        git(self.src, "commit", "-qam", "bad")
        with self.assertRaises(modules.ModuleError):
            modules.update("probe", out=quiet)
        modules.verify("probe")

    def test_a_crash_between_pin_and_current_is_caught_and_rolled_back(self):
        self.new_commit()
        real = modules._atomic_write

        def crash(path, data, mode=0o600):
            if Path(path).name == "current":
                raise KeyboardInterrupt("simulated crash")
            return real(path, data, mode)
        with mock.patch.object(modules, "_atomic_write", crash):
            with self.assertRaises(KeyboardInterrupt):
                modules.update("probe", out=quiet)
        with self.assertRaises(modules.ModuleError):
            modules.verify("probe")          # detected, and disabled
        modules.set_enabled("probe", True, out=quiet)
        modules.rollback("probe", out=quiet)
        modules.verify("probe")
        self.assertEqual(modules.load_pins()["probe"]["commit"], self.first)

    def test_update_waits_for_a_run_in_flight(self):
        self.set_mode("sleep")
        self.new_commit()
        t = threading.Thread(target=modules.run_collector, args=("probe",))
        t.start()
        time.sleep(1.0)
        t0 = time.monotonic()
        modules.update("probe", out=quiet)
        waited = time.monotonic() - t0
        t.join()
        self.assertGreater(waited, 2.0, "update did not wait for the run")
        modules.verify("probe")

    def test_remove_waits_for_a_run_in_flight_and_purge_deletes_data(self):
        self.set_mode("sleep")
        t = threading.Thread(target=modules.run_collector, args=("probe",))
        t.start()
        time.sleep(1.0)
        modules.remove("probe", purge=True, out=quiet)
        t.join()
        self.assertNotIn("probe", modules.load_pins())
        self.assertFalse(modules.data_dir("probe").exists())
        self.assertFalse(modules.config_dir("probe").exists())

    def test_a_run_while_busy_is_skipped_not_queued_twice(self):
        self.set_mode("sleep")
        t = threading.Thread(target=modules.run_collector, args=("probe",))
        t.start()
        time.sleep(1.0)
        st = modules.run_collector("probe")
        t.join()
        self.assertIn("busy", st["error"])


class TheRunner(Base):
    def setUp(self):
        super().setUp()
        self.install()
        self.set_mode("ok")
        self.assertEqual(modules.run_collector("probe")["state"], "ok")
        self.good = modules.detail("probe")["snapshot"]

    def failing(self, mode, needle, *args):
        self.set_mode(mode, *args)
        t0 = time.monotonic()
        st = modules.run_collector("probe")
        self.assertEqual(st["state"], "failing", st)
        self.assertIn(needle, st["error"])
        self.assertEqual(modules.detail("probe")["snapshot"], self.good,
                         "the last good snapshot was lost")
        return time.monotonic() - t0

    def test_non_zero_exit(self):
        self.failing("exit1", "exited 1: boom")

    def test_non_json(self):
        self.failing("nonjson", "not JSON")

    def test_oversized_stdout(self):
        self.failing("bigout", "cap")

    def test_a_stderr_flood(self):
        self.failing("stderrflood", "cap")

    def test_a_sleep_past_the_timeout(self):
        took = self.failing("sleep", "timeout")
        self.assertLess(took, 15)

    @unittest.skipUnless(HAVE_BWRAP, "needs bubblewrap")
    def test_a_child_that_leaves_the_group_dies_too(self):
        marker = f"corral-escape-{os.getpid()}-{time.time_ns()}"
        self.failing("escape", "timeout", marker)
        time.sleep(0.5)
        for p in Path("/proc").iterdir():
            if not p.name.isdigit():
                continue
            try:
                cmd = (p / "cmdline").read_bytes()
            except OSError:
                continue
            self.assertNotIn(marker.encode(), cmd, "an escaped child outlived the run")

    def test_the_runner_thread_survives_anything(self):
        r = modules.Runner(tick_s=0.05, grace_s=0)
        with mock.patch.object(modules, "run_collector", side_effect=RuntimeError("x")):
            r.start()
            time.sleep(0.3)
        self.assertTrue(any(t.name == "module-runner" and t.is_alive()
                            for t in threading.enumerate()))
        r.stop()

    def test_refresh_is_rate_limited_and_queued_once(self):
        r = modules.Runner(grace_s=0)
        ok, _ = r.refresh("probe")
        self.assertTrue(ok)
        ok2, why = r.refresh("probe")
        self.assertTrue(ok2)
        self.assertEqual(why, "already queued")
        r.tick()
        ok3, why3 = r.refresh("probe")
        self.assertFalse(ok3)
        self.assertIn("wait", why3)
        self.assertFalse(r.refresh("nope")[0])


class Isolation(Base):
    """Linux: fails rather than skips where bubblewrap exists (§8.1)."""

    def setUp(self):
        super().setUp()
        if not HAVE_BWRAP:
            self.skipTest("no bubblewrap on this host")
        ok, why = module_sandbox.available(refresh=True)
        self.assertTrue(ok, f"bubblewrap is installed but the sandbox fails: {why}")
        self.install()
        feed = modules.feed_dir()
        feed.mkdir(parents=True, exist_ok=True)
        (feed / "host.json").write_text("{}")

    def run_attempts(self, lines, extra_env=None):
        self.set_mode("isolation", *lines)
        with mock.patch.dict(os.environ, extra_env or {}):
            st = modules.run_collector("probe")
        self.assertEqual(st["state"], "ok", st)
        snap = modules.detail("probe")["snapshot"]
        rows = dict((r[0], r[1]) for r in snap["view"][0]["rows"])
        info = json.loads(snap["view"][1]["text"])
        return rows, info

    def test_every_forbidden_attempt_fails_and_the_run_still_succeeds(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        lines = [f"read {self.state / 'session.key'}",
                 f"read {self.state / 'pairing.json'}",
                 f"read {self.state / 'panes' / 'p1' / 'events.jsonl'}",
                 f"read {self.home / '.ssh' / 'id_test'}",
                 f"list {self.home / '.ssh'}",
                 f"read {modules.pins_path()}",
                 f"read /proc/{os.getpid()}/environ",
                 f"write {self.state / 'planted'}",
                 f"write {modules.module_dir('probe') / 'planted'}",
                 f"write {self.home / 'planted'}",
                 f"write {modules.feed_dir() / 'planted'}",
                 f"connect 127.0.0.1 {port}",
                 "connect 1.1.1.1 443"]
        rows, info = self.run_attempts(lines)
        for line in lines:
            self.assertTrue(rows[line].startswith("refused:"), f"{line} -> {rows[line]}")
        self.assertEqual(info["feed"], "OPENED")
        self.assertEqual(info["data"], "OPENED")
        self.assertFalse((self.state / "planted").exists())
        self.assertFalse((self.home / "planted").exists())

    def test_the_environment_is_exactly_the_documented_set(self):
        _rows, info = self.run_attempts([], {"CORRAL_TEST_SECRET": "s3cret",
                                             "ANTHROPIC_API_KEY": "sk-test"})
        allowed = {"PATH", "HOME", "LANG", "TZ", "PWD", "CORRAL_MODULE_API",
                   "CORRAL_MODULE_NAME", "CORRAL_MODULE_CONFIG", "CORRAL_MODULE_DATA",
                   "CORRAL_MODULE_FEED", "CORRAL_MODULE_SANDBOXED"}
        self.assertEqual(set(info["env"]) - allowed, set())
        self.assertNotIn("CORRAL_TEST_SECRET", info["env"])

    def test_the_cli_may_write_its_config_and_the_collector_may_not(self):
        argv, env, cwd, _ = modules.build_run("probe", "cli", ["--write-config"],
                                              interactive=True)
        r = subprocess.run(argv, env=env or {}, cwd=cwd, capture_output=True, text=True,
                           timeout=30)
        self.assertIn("wrote config", r.stdout, r.stderr)
        rows, _ = self.run_attempts([f"write {modules.config_dir('probe') / 'config.toml'}"])
        self.assertTrue(list(rows.values())[0].startswith("refused:"))


class Unsandboxed(Base):
    def no_sandbox(self):
        self._patch(module_sandbox, "available", lambda refresh=False: (False, "test host"))

    def test_install_needs_the_typed_acknowledgement(self):
        self.no_sandbox()
        with self.assertRaises(modules.ModuleError):
            self.install(ack_unsandboxed="yes")
        self.assertEqual(modules.load_pins(), {})
        self.install(src=self.make_repo("src2"), ack_unsandboxed="unsandboxed")
        self.assertTrue(modules.load_pins()["probe"]["unsandboxed_ack"])
        s = modules.summary("probe")
        self.assertFalse(s["sandboxed"])
        self.set_mode("ok")
        self.assertEqual(modules.run_collector("probe")["state"], "ok")

    def test_a_module_installed_sandboxed_does_not_run_unsandboxed(self):
        if not module_sandbox.available()[0]:
            self.skipTest("needs a sandbox at install time")
        self.install()
        self.no_sandbox()
        self.assertEqual(modules.summary("probe")["state"], "unacknowledged")
        st = modules.run_collector("probe")
        self.assertEqual(st["state"], "failing")
        self.assertIn("cannot sandbox", st["error"])

    def test_the_unsandboxed_environment_is_built_from_nothing(self):
        self.no_sandbox()
        self.install(ack_unsandboxed="unsandboxed")
        with mock.patch.dict(os.environ, {"CORRAL_TEST_SECRET": "x"}):
            _argv, env, _cwd, sb = modules.build_run("probe", "collector")
        self.assertFalse(sb)
        self.assertNotIn("CORRAL_TEST_SECRET", env)
        self.assertEqual(env["HOME"], str(modules.data_dir("probe")))


class TheSnapshot(unittest.TestCase):
    def v(self, obj):
        snap, err = modules.validate_snapshot(json.dumps(obj).encode())
        self.assertIsNone(err)
        return snap

    def base(self, view):
        return {"schema": "corral-light.module/1", "ok": True, "view": view}

    def test_kind_level_and_pct_are_mapped(self):
        s = self.v(self.base([
            {"type": "tiles", "items": [{"label": "a", "kind": "evil class",
                                         "level": "x\" onclick=\"y", "value": 3}]},
            {"type": "meter", "label": "m", "pct": 250},
            {"type": "meter", "label": "n", "pct": "50"}]))
        t = s["view"][0]["items"][0]
        self.assertEqual((t["kind"], t["level"], t["value"]), ("unknown", "info", "3"))
        self.assertEqual(s["view"][1]["pct"], 100)
        self.assertEqual(s["view"][2]["pct"], 0)

    def test_infinity_is_not_a_percent(self):
        snap, _ = modules.validate_snapshot(
            b'{"schema":"corral-light.module/1","view":[{"type":"meter","pct":Infinity}]}')
        self.assertEqual(snap["view"][0]["pct"], 0)

    def test_links_are_https_with_a_host_only(self):
        for url, safe in (("https://example.com/x", True), ("javascript:alert(1)", False),
                          ("https:alert(1)", False), ("http://example.com", False),
                          ("https://u:p@example.com", False), ("https://", False),
                          ("https://exa mple.com", False)):
            s = self.v(self.base([{"type": "link", "label": "l", "url": url}]))
            self.assertEqual(s["view"][0]["safe"], safe, url)

    def test_bounds_truncate_and_say_so(self):
        s = self.v(self.base(
            [{"type": "table", "columns": [str(i) for i in range(20)],
              "rows": [["c"] * 20] * 300},
             {"type": "tiles", "items": [{"label": "t"}] * 30},
             {"type": "note", "text": "x" * 2000}] + [{"type": "note", "text": "n"}] * 60))
        self.assertEqual(len(s["view"]), modules.MAX_BLOCKS)
        self.assertEqual(s["truncated"], {"blocks": 13})
        tbl = s["view"][0]
        self.assertEqual((len(tbl["rows"]), len(tbl["columns"])), (200, 12))
        self.assertEqual(tbl["dropped"]["rows"], 100)
        self.assertEqual(s["view"][1]["dropped"]["items"], 6)
        self.assertLessEqual(len(s["view"][2]["text"]), modules.CELL_CAP)

    def test_unknown_blocks_and_nesting(self):
        s = self.v(self.base([{"type": "html", "html": "<b>"}, "text",
                              {"type": "tiles", "items": [{"label": {"nested": 1}}]}]))
        self.assertEqual(s["view"][0], {"type": "unsupported", "was": "html"})
        self.assertEqual(s["view"][1]["type"], "unsupported")
        self.assertEqual(s["view"][2]["items"][0]["label"], "")

    def test_wrong_schema_and_non_objects(self):
        for raw in (b"[]", b'{"schema":"x"}', b"\xff\xfe", b"x" * (modules.STDOUT_CAP + 1)):
            self.assertIsNone(modules.validate_snapshot(raw)[0])


class TheRoutes(Base):
    """Module routes need the cookie; unknown, disabled and traversal names
    are 404; /health carries counts only (§8.1)."""

    def setUp(self):
        super().setUp()
        import http.client  # noqa: F401
        import auth
        import hub
        import sessions
        from http.server import ThreadingHTTPServer
        self.hub = hub
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes, m.subscribers, m.not_restored = {}, [], 0
        m._lock = threading.Lock()
        m.catalog, m.mcp, m.orphans = {}, None, {}
        self._patch(hub, "MGR", m)
        self.cookie = f"{hub.COOKIE}={auth.mint()}"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.install()
        self.set_mode("ok")
        modules.run_collector("probe")
        self._patch(modules, "RUNNER", modules.Runner(grace_s=0))

    def req(self, method, path, cookie=True):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=30)
        h = {"Cookie": self.cookie} if cookie else {}
        if method == "POST":
            h.update({"Content-Type": "application/json", "Content-Length": "2"})
        c.request(method, path, body=b"{}" if method == "POST" else None, headers=h)
        r = c.getresponse()
        out = json.loads(r.read() or b"{}")
        c.close()
        return r.status, out

    def test_the_cookie_is_required(self):
        for method, path in (("GET", "/api/modules"), ("GET", "/api/module/probe"),
                             ("POST", "/api/module/probe/refresh")):
            self.assertEqual(self.req(method, path, cookie=False)[0], 401, path)

    def test_list_and_detail(self):
        st, out = self.req("GET", "/api/modules")
        self.assertEqual(st, 200)
        self.assertEqual([m["name"] for m in out["modules"]], ["probe"])
        st, d = self.req("GET", "/api/module/probe")
        self.assertEqual(st, 200)
        self.assertEqual(d["snapshot"]["view"][0]["type"], "tiles")

    def test_unknown_disabled_and_traversal_are_404(self):
        for path in ("/api/module/nope", "/api/module/../modules", "/api/module/%2e%2e",
                     "/api/module/probe/../../x", "/api/module/doctor", "/api/module/"):
            self.assertEqual(self.req("GET", path)[0], 404, path)
        modules.set_enabled("probe", False, out=quiet)
        self.assertEqual(self.req("GET", "/api/module/probe")[0], 404)
        self.assertEqual(self.req("POST", "/api/module/probe/refresh")[0], 404)

    def test_refresh_is_rate_limited(self):
        self.assertEqual(self.req("POST", "/api/module/probe/refresh")[0], 200)
        modules.RUNNER.tick()
        st, out = self.req("POST", "/api/module/probe/refresh")
        self.assertEqual(st, 429)
        self.assertIn("wait", out["error"])

    def test_health_carries_counts_only(self):
        self.set_mode("exit1")
        modules.run_collector("probe")
        st, out = self.req("GET", "/health", cookie=False)
        self.assertEqual((out["modules"], out["modules_failing"]), (1, 1))
        self.assertNotIn("probe", json.dumps(out))
        self.assertNotIn("boom", json.dumps(out))


class TheDispatch(Base):
    def test_a_core_verb_never_reaches_a_module(self):
        self.assertEqual(modules.dispatch(["doctor"]), 2)
        self.assertEqual(modules.dispatch(["nope"]), 2)

    def test_an_enabled_module_with_a_cli_runs(self):
        self.install()
        with mock.patch.object(modules, "run_interactive", return_value=0) as ri:
            self.assertEqual(modules.dispatch(["probe", "a", "b"]), 0)
        ri.assert_called_once_with("probe", "cli", ["a", "b"])
        modules.set_enabled("probe", False, out=quiet)
        self.assertEqual(modules.dispatch(["probe"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
