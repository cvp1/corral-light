"""vendor_reports: the core's sandboxed `grok usage` runs (plan §4.2, §8.1
"Vendor report sandbox").

The sandbox tests run the real module sandbox (bubblewrap) and skip only
when this host cannot build it. The stand-in binary is a Python script
(testkit/fixtures/vendor-reports/grok_standin.py) installed in the real
Grok layout: bin/grok a symlink to a versioned file in the same folder as
the login file. The shipped binary check takes ELF files only, so the
script tests swap in a check that also takes the stand-in; the ELF check
itself is tested with a copy of a real ELF executable, run end to end.
"""
import json
import os
import shutil
import socket
import stat
import sys
import time
import unittest
from unittest import mock

import module_sandbox
import vendor_reports
from testkit.scratch import tmpdir

HERE = os.path.dirname(os.path.abspath(__file__))
STANDIN = os.path.join(HERE, "testkit", "fixtures", "vendor-reports", "grok_standin.py")
REPORT = os.path.join(HERE, "testkit", "fixtures", "vendor-reports", "usage_report.json")

SANDBOX_OK, SANDBOX_WHY = (module_sandbox.available() if sys.platform.startswith("linux")
                           else (False, "Linux only"))
HOST_PY = "/usr/bin/python3"
NEEDS_SANDBOX = unittest.skipUnless(SANDBOX_OK, f"module sandbox unavailable: {SANDBOX_WHY}")


def sid(n):
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def load(path):
    return json.loads(read(path))


def accept_standin(path):
    return vendor_reports.is_elf(path) or read(path, "rb")[:2] == b"#!"


class Layout(object):
    """A fake Grok home in the real layout, plus an out dir."""

    def __init__(self, test):
        self.root = tmpdir(test, "vr-")
        self.home = os.path.join(self.root, "grokhome")
        self.out = os.path.join(self.root, "out")
        bindir = os.path.join(self.home, "bin")
        os.makedirs(bindir)
        self.auth = os.path.join(bindir, "auth.json")
        self.top_auth = os.path.join(self.home, "auth.json")
        for p in (self.auth, self.top_auth):
            with open(p, "w") as f:
                f.write('{"token": "SENTINEL-TOKEN"}')
        versioned = os.path.join(bindir, "grok-9.9.9")
        py = HOST_PY if os.path.exists(HOST_PY) else os.path.realpath(sys.executable)
        with open(STANDIN) as src, open(versioned, "w") as dst:
            dst.write(f"#!{py} -I\n" + src.read())
        os.chmod(versioned, 0o755)
        os.symlink("grok-9.9.9", os.path.join(bindir, "grok"))
        self.binary = os.path.join(bindir, "grok")
        self.py = py
        with open(REPORT) as f:
            self.template = json.load(f)

    def session(self, n, mode="ok", cwd="%2Fsome%2Fproject", **summary):
        s = sid(n)
        d = os.path.join(self.home, "sessions", cwd, s)
        os.makedirs(d, exist_ok=True)
        rep = json.loads(json.dumps(self.template))
        rep["sessionId"] = s
        rep["session"]["inputTokens"] = 1000 + n
        self.write(d, "usage.json", rep)
        summary.setdefault("created_at", "2026-10-01T10:00:00Z")
        summary["standin_mode"] = mode
        self.write(d, "summary.json", summary)
        with open(os.path.join(d, "chat_history.jsonl"), "w") as f:
            f.write('{"text": "SENTINEL-PROMPT"}\n')
        # Distinct mtimes so "newest first" is well defined.
        t = 1_700_000_000 + n
        os.utime(os.path.join(d, "usage.json"), (t, t))
        return s, d

    @staticmethod
    def write(d, name, obj):
        with open(os.path.join(d, name), "w") as f:
            json.dump(obj, f)

    def report(self, s):
        with open(os.path.join(self.out, s + ".json")) as f:
            return json.load(f)

    def refresh(self, **kw):
        kw.setdefault("grok_home", self.home)
        kw.setdefault("binary", self.binary)
        return vendor_reports.refresh_grok(self.out, **kw)


@NEEDS_SANDBOX
class SandboxedRuns(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(HOST_PY) and not os.path.realpath(sys.executable).startswith("/usr/"):
            self.skipTest("no python under /usr to run the stand-in inside the sandbox")
        p = mock.patch.object(vendor_reports, "BINARY_CHECK", accept_standin)
        p.start()
        self.addCleanup(p.stop)
        self.L = Layout(self)

    def test_ok_report_kept_fields_and_mode(self):
        s, d = self.L.session(1, parent_session_id=sid(9), forked_at="2026-10-02T11:00:00.5Z")
        r = self.L.refresh()
        self.assertEqual((r["ran"], r["failed"], r["stale"], r["skipped"]), (1, 0, [], 0))
        self.assertEqual(r["version"], "grok 9.9.9 (standin) [stable]")
        rep = self.L.report(s)
        self.assertEqual(rep["sessionId"], s)
        self.assertEqual(rep["parent_session_id"], sid(9))
        self.assertEqual(rep["forked_at"], "2026-10-02T11:00:00.5Z")
        self.assertEqual(rep["grok_version"], "grok 9.9.9 (standin) [stable]")
        self.assertEqual(rep["session"]["inputTokens"], 1001)
        self.assertEqual(rep["turns"][0]["turnNumber"], 1)
        self.assertIsInstance(rep["turns"][0]["costUsdTicks"], int)
        self.assertIn("grok-test-model", rep["turns"][0]["modelUsage"])
        self.assertFalse(rep["stale"])
        body = read(os.path.join(self.L.out, s + ".json"))
        for word in ("SENTINEL", "prompt", "text", "toolCalls"):
            self.assertNotIn(word, body)
        mode = stat.S_IMODE(os.stat(os.path.join(self.L.out, s + ".json")).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertFalse(os.path.exists(os.path.join(self.L.out, ".scratch")))

    def test_hostile_standins_fail_and_others_succeed(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        good1, _ = self.L.session(1)
        other, other_dir = self.L.session(2)
        probes = [self.L.auth, self.L.top_auth, os.path.join(other_dir, "usage.json"),
                  os.path.join(other_dir, "chat_history.jsonl"),
                  os.path.realpath(self.L.binary)]
        probe, _ = self.L.session(3, mode="probe", standin_probes=probes, standin_ports=[port])
        hang, _ = self.L.session(4, mode="hang")
        mem, _ = self.L.session(5, mode="memory")
        big, _ = self.L.session(6, mode="big")
        garbage, _ = self.L.session(7, mode="garbage")
        flt, _ = self.L.session(8, mode="float")
        good2, _ = self.L.session(10)
        t0 = time.monotonic()
        r = self.L.refresh(timeout_s=3)
        elapsed = time.monotonic() - t0
        self.assertEqual(r["ran"], 9)
        self.assertEqual(sorted(r["stale"]), sorted([hang, mem, big, garbage, flt]))
        self.assertEqual(r["failed"], 5)
        self.assertLess(elapsed, 3 + 15, "the hang was not cut off at its timeout")
        for s in (good1, other, good2):
            self.assertEqual(self.L.report(s)["sessionId"], s)
        rep = self.L.report(probe)
        self.assertEqual(rep["session"]["inputTokens"], 0, "a forbidden read or connect worked")
        self.assertEqual(rep["session"]["outputTokens"], 1, "the control read failed")
        for s in (hang, mem, big, garbage, flt):
            self.assertFalse(os.path.exists(os.path.join(self.L.out, s + ".json")), s)
        idx = load(os.path.join(self.L.out, ".index.json"))
        self.assertEqual(idx[hang]["why"], "timeout")
        self.assertEqual(idx[big]["why"], "too large")
        self.assertEqual(idx[garbage]["why"], "not json")
        self.assertEqual(idx[flt]["why"], "float in a numeric field")
        self.assertTrue(idx[mem]["why"].startswith("exit"))

    def test_unchanged_sessions_are_not_rerun(self):
        a, da = self.L.session(1)
        b, _ = self.L.session(2)
        self.assertEqual(self.L.refresh()["ran"], 2)
        r = self.L.refresh()
        self.assertEqual((r["ran"], r["skipped"]), (0, 0))
        rep = load(os.path.join(da, "usage.json"))
        rep["session"]["inputTokens"] = 5555
        Layout.write(da, "usage.json", rep)
        r = self.L.refresh()
        self.assertEqual(r["ran"], 1)
        self.assertEqual(self.L.report(a)["session"]["inputTokens"], 5555)

    def test_limit_newest_first(self):
        ids = [self.L.session(n)[0] for n in range(1, 6)]
        r = self.L.refresh(limit=2)
        self.assertEqual((r["ran"], r["skipped"]), (2, 3))
        written = sorted(f[:-5] for f in os.listdir(self.L.out) if f.endswith(".json") and f[0] != ".")
        self.assertEqual(written, sorted(ids[-2:]))
        r = self.L.refresh(limit=10)
        self.assertEqual((r["ran"], r["skipped"]), (3, 0))

    def test_failure_after_success_flags_old_report_stale(self):
        s, d = self.L.session(1)
        self.L.refresh()
        summ = load(os.path.join(d, "summary.json"))
        summ["standin_mode"] = "garbage"
        Layout.write(d, "summary.json", summ)
        rep = load(os.path.join(d, "usage.json"))
        rep["session"]["inputTokens"] = 7
        Layout.write(d, "usage.json", rep)
        r = self.L.refresh()
        self.assertEqual(r["stale"], [s])
        old = self.L.report(s)
        self.assertTrue(old["stale"])
        self.assertEqual(old["session"]["inputTokens"], 1001)

    def test_bad_ids_and_symlinked_files_are_never_used(self):
        good, _ = self.L.session(1)
        bad = os.path.join(self.L.home, "sessions", "x", "not-a-session-id")
        os.makedirs(bad)
        Layout.write(bad, "usage.json", self.L.template)
        Layout.write(bad, "summary.json", {})
        s3, d3 = self.L.session(3)
        os.unlink(os.path.join(d3, "usage.json"))
        os.symlink(self.L.auth, os.path.join(d3, "usage.json"))
        r = self.L.refresh()
        self.assertEqual(r["ran"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.L.out, good + ".json")))
        self.assertFalse(os.path.exists(os.path.join(self.L.out, s3 + ".json")))

    def test_invalid_fork_fields_are_dropped(self):
        s, _ = self.L.session(1, parent_session_id="../../etc/x" + "a" * 300,
                              forked_at="x" * 500)
        self.L.refresh()
        rep = self.L.report(s)
        self.assertIsNone(rep["parent_session_id"])
        self.assertIsNone(rep["forked_at"])

    def test_grok_home_env_override(self):
        s, _ = self.L.session(1)
        with mock.patch.dict(os.environ, {"CORRAL_GROK_HOME": self.L.home}):
            r = vendor_reports.refresh_grok(self.L.out, binary=self.L.binary)
        self.assertEqual(r["ran"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.L.out, s + ".json")))


@NEEDS_SANDBOX
class BinaryCheck(unittest.TestCase):
    def setUp(self):
        self.L = Layout(self)

    def test_script_binary_refused_and_never_run(self):
        s, _ = self.L.session(1)
        with mock.patch.object(vendor_reports, "_run") as run:
            r = self.L.refresh()
        run.assert_not_called()
        self.assertEqual((r["ran"], r["skipped"]), (0, 1))
        self.assertIn("not an ELF", r["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.L.out, s + ".json")))

    def test_real_elf_runs_in_sandbox_and_bad_output_is_stale(self):
        true = shutil.which("true")
        if not true or not vendor_reports.is_elf(os.path.realpath(true)):
            self.skipTest("no ELF `true` on this host")
        dst = os.path.join(self.L.home, "bin", "grok-elf")
        shutil.copy(os.path.realpath(true), dst)
        os.chmod(dst, 0o755)
        s, _ = self.L.session(1)
        r = self.L.refresh(binary=dst)
        self.assertEqual((r["ran"], r["failed"], r["stale"]), (1, 1, [s]))
        # GNU `true` answers --version; busybox's does not.
        self.assertTrue(r["version"] is None or "true" in r["version"])
        # It really ran and exited 0: stale because its (empty) output is no
        # report, not because the sandbox failed to build (round three).
        with open(os.path.join(self.L.out, ".index.json")) as f:
            why = json.load(f)[s]["why"]
        self.assertNotIn(why, ("sandbox", "spawn", "timeout", "too large"))
        self.assertFalse(why.startswith("exit "), why)


class SandboxUnavailable(unittest.TestCase):
    def test_nothing_runs(self):
        L = Layout(self)
        L.session(1)
        L.session(2)
        with mock.patch.object(module_sandbox, "available", return_value=(False, "no bwrap")), \
                mock.patch.object(vendor_reports, "_run") as run, \
                mock.patch.object(vendor_reports, "BINARY_CHECK", accept_standin):
            r = L.refresh()
        run.assert_not_called()
        self.assertEqual((r["ran"], r["failed"], r["skipped"]), (0, 0, 2))
        self.assertIn("no bwrap", r["reason"])


class Reduce(unittest.TestCase):
    def setUp(self):
        with open(REPORT) as f:
            self.rep = json.load(f)
        self.sid = self.rep["sessionId"]

    def reduce(self, doc):
        return vendor_reports.reduce_report(json.dumps(doc).encode(), self.sid)

    def test_keeps_numbers_ids_stamps_only(self):
        doc = dict(self.rep, prompt="SENTINEL")
        doc["turns"] = [dict(doc["turns"][0], text="SENTINEL", toolCalls=[{"x": 1}])]
        out = self.reduce(doc)
        self.assertNotIn("SENTINEL", json.dumps(out))
        self.assertEqual(out["turns"][0]["endedAt"], self.rep["turns"][0]["endedAt"])
        self.assertEqual(out["session"]["primaryModelId"], "grok-test-model")

    def test_float_ticks_rejected(self):
        for where in ("session", "turn", "model"):
            doc = json.loads(json.dumps(self.rep))
            if where == "session":
                doc["session"]["costUsdTicks"] = 12.0
            elif where == "turn":
                doc["turns"][0]["costUsdTicks"] = 1.5
            else:
                doc["turns"][0]["modelUsage"]["grok-test-model"]["costUsdTicks"] = 2.5
            with self.assertRaises(vendor_reports.BadReport, msg=where):
                self.reduce(doc)

    def test_string_ticks_rejected(self):
        doc = json.loads(json.dumps(self.rep))
        doc["turns"][0]["costUsdTicks"] = "100"
        with self.assertRaises(vendor_reports.BadReport):
            self.reduce(doc)

    def test_shape_checks(self):
        bad = [b"", b"[]", b"not json", b'{"sessionId": "x"}', b"NaN",
               json.dumps(dict(self.rep, sessionId="other")).encode(),
               json.dumps({"sessionId": self.sid, "session": {}, "turns": [{}]}).encode(),
               b'{"sessionId":"%s","session":{"inputTokens":NaN},"turns":[]}' % self.sid.encode(),
               b"x" * (vendor_reports.MAX_OUT + 1)]
        for raw in bad:
            with self.assertRaises(vendor_reports.BadReport, msg=raw[:40]):
                vendor_reports.reduce_report(raw, self.sid)

    def test_version_line(self):
        self.assertEqual(vendor_reports._version(b"grok 1.0.0 (abc) [stable]\n"),
                         "grok 1.0.0 (abc) [stable]")
        self.assertIsNone(vendor_reports._version(b"\x1b[31mevil\n"))
        self.assertIsNone(vendor_reports._version(b"x" * 500))


if __name__ == "__main__":
    unittest.main()
