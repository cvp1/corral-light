"""Unit tests for the container entrypoint, doctor's container section and the
WS3 basics (bind default, workspace default). Stdlib unittest, runs on the
host — no Docker needed.

    python3 -m unittest tests/container/test_entrypoint.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "container"))

import entrypoint as ep          # noqa: E402
import doctor                    # noqa: E402

ENV = {"HOST_UID": "501", "HOST_GID": "20", "HOST_USER": "alice",
       "HOST_HOME": "/Users/alice"}


class Identity(unittest.TestCase):
    def test_complete_env(self):
        self.assertEqual(ep.identity(ENV), {"uid": 501, "gid": 20, "user": "alice",
                                            "home": "/Users/alice"})

    def test_missing_names_every_key(self):
        with self.assertRaises(ep.IdentityError) as cm:
            ep.identity({"HOST_UID": "501"})
        for k in ("HOST_GID", "HOST_USER", "HOST_HOME"):
            self.assertIn(k, str(cm.exception.code))

    def test_root_refused(self):
        with self.assertRaises(ep.IdentityError):
            ep.identity(ENV | {"HOST_UID": "0"})

    def test_bad_values_refused(self):
        for k, v in (("HOST_UID", "x"), ("HOST_HOME", "rel/path"),
                     ("HOST_HOME", "/a:b"), ("HOST_USER", "a:b"), ("HOST_USER", "a b")):
            with self.subTest(k=k, v=v), self.assertRaises(ep.IdentityError):
                ep.identity(ENV | {k: v})

    def test_home_with_space_allowed(self):
        self.assertEqual(ep.identity(ENV | {"HOST_HOME": "/Users/a b"})["home"],
                         "/Users/a b")


class PasswdGroup(unittest.TestCase):
    GROUP = ["root:x:0:", "dialout:x:20:", "staff:x:50:"]
    PASSWD = ["root:x:0:0:root:/root:/bin/bash",
              "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin",
              "olduser:x:501:501::/home/old:/bin/sh"]

    def test_existing_gid_kept_by_number(self):
        lines, name = ep.merge_group(self.GROUP, 20, "alice")
        self.assertEqual(name, "dialout")
        self.assertEqual(lines, self.GROUP)

    def test_new_gid_added(self):
        lines, name = ep.merge_group(self.GROUP, 1000, "alice")
        self.assertEqual(name, "alice")
        self.assertIn("alice:x:1000:", lines)

    def test_new_gid_name_clash(self):
        _, name = ep.merge_group(self.GROUP, 1000, "staff")
        self.assertEqual(name, "staff-host")

    def test_passwd_replaces_same_uid_and_keeps_system(self):
        out = ep.merge_passwd(self.PASSWD, ep.identity(ENV))
        self.assertNotIn("olduser:x:501:501::/home/old:/bin/sh", out)
        self.assertIn("root:x:0:0:root:/root:/bin/bash", out)
        self.assertEqual(out[-1], "alice:x:501:20:alice:/Users/alice:/bin/bash")
        self.assertEqual(sum(1 for l in out if ":501:" in l), 1)

    def test_write_identity_files(self):
        with tempfile.TemporaryDirectory() as d:
            etc = Path(d)
            (etc / "group").write_text("\n".join(self.GROUP) + "\n")
            (etc / "passwd").write_text("\n".join(self.PASSWD) + "\n")
            g = ep.write_identity(ep.identity(ENV), etc)
            self.assertEqual(g, "dialout")
            self.assertIn("alice:x:501:20:", (etc / "passwd").read_text())


class PathReport(unittest.TestCase):
    def test_image_path_clean(self):
        self.assertTrue(ep.path_report(ep.IMAGE_PATH, "/Users/alice")["ok"])

    def test_host_dirs_flagged(self):
        r = ep.path_report("/opt/homebrew/bin:/usr/bin:/Users/alice/.local/bin",
                           "/Users/alice")
        self.assertFalse(r["ok"])
        self.assertEqual(r["host_dirs"], ["/opt/homebrew/bin", "/Users/alice/.local/bin"])

    def test_child_env_forces_image_path(self):
        ident = ep.identity(ENV)
        e = ep.child_env({"PATH": "/opt/homebrew/bin", "HOME": "/root", "X": "1"},
                         ident, {"ssh": {"config": "/run/corral/ssh_config"}})
        self.assertEqual(e["PATH"], ep.IMAGE_PATH)
        self.assertEqual(e["HOME"], "/Users/alice")
        self.assertEqual(e["X"], "1")
        self.assertIn("-F /run/corral/ssh_config", e["GIT_SSH_COMMAND"])


class ParityMap(unittest.TestCase):
    def test_absent_map(self):
        self.assertEqual(ep.parity_report(None), [])

    def test_unreadable_map(self):
        r = ep.parity_report("/nonexistent/map.json")
        self.assertFalse(r[0]["ok"])

    def test_missing_and_working_entries(self):
        with tempfile.TemporaryDirectory() as d:
            good = Path(d, "py")
            good.write_text("#!/bin/sh\necho Python 3.12.0\n")
            good.chmod(0o755)
            m = Path(d, "map.json")
            m.write_text(json.dumps([{"path": str(good), "expect": "python"},
                                     {"path": str(Path(d, "nope")), "expect": "python"}]))
            r = ep.parity_report(str(m))
            self.assertTrue(r[0]["ok"])
            self.assertEqual(r[0]["version"], "Python 3.12.0")
            self.assertFalse(r[1]["ok"])
            self.assertIn("not present", r[1]["why"])

    def test_wrong_format_binary_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d, "macho")
            bad.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 64)   # Mach-O magic
            bad.chmod(0o755)
            m = Path(d, "map.json")
            m.write_text(json.dumps([{"path": str(bad)}]))
            r = ep.parity_report(str(m))
            self.assertFalse(r[0]["ok"])


class Ssh(unittest.TestCase):
    def test_config_order_and_ignore(self):
        cfg = ep.ssh_config("/Users/a b", "/run/host-services/ssh-auth.sock", True)
        lines = cfg.splitlines()
        self.assertTrue(lines[0].startswith("IgnoreUnknown UseKeychain"))
        self.assertIn("IdentityAgent /run/host-services/ssh-auth.sock", lines)
        self.assertEqual(lines[-1], "Include /Users/a b/.ssh/config")
        # container-writable known_hosts FIRST: ssh appends to the first file,
        # and the host's is mounted read-only
        kh = next(l for l in lines if l.startswith("UserKnownHostsFile"))
        self.assertLess(kh.index("/var/lib/corral"), kh.index("/Users/a b"))

    def test_no_agent_no_include(self):
        cfg = ep.ssh_config("/h", None, False)
        self.assertNotIn("IdentityAgent", cfg)
        self.assertNotIn("Include", cfg)


class OverlaysHostname(unittest.TestCase):
    def test_mountinfo_parse_with_space(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("36 35 0:1 / /Users/a\\040b/p/node_modules rw - ext4 x rw\n"
                    "37 35 0:2 / /proc rw - proc proc rw\n")
        try:
            mp = ep.mountpoints(f.name)
        finally:
            os.unlink(f.name)
        self.assertIn("/Users/a b/p/node_modules", mp)
        r = ep.overlay_report("/Users/a b/p/node_modules:/x/.venv", mp)
        self.assertEqual([o["mounted"] for o in r], [True, False])

    def test_hostname(self):
        self.assertTrue(ep.hostname_report(None)["ok"])
        self.assertFalse(ep.hostname_report("surely-not-this-host-xyz")["ok"])


class Doctor(unittest.TestCase):
    def test_elf_arch(self):
        with tempfile.TemporaryDirectory() as d:
            x = Path(d, "x"); x.write_bytes(b"\x7fELF" + b"\0" * 14 + b"\x3e\x00")
            a = Path(d, "a"); a.write_bytes(b"\x7fELF" + b"\0" * 14 + b"\xb7\x00")
            s = Path(d, "s"); s.write_text("#!/bin/sh\n")
            m = Path(d, "m"); m.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 20)
            self.assertEqual(doctor.elf_arch(x), "x86-64")
            self.assertEqual(doctor.elf_arch(a), "aarch64")
            self.assertEqual(doctor.elf_arch(s), "script")
            self.assertEqual(doctor.elf_arch(m), "not-elf")
            self.assertIsNone(doctor.elf_arch(Path(d, "missing")))

    def test_silent_outside_container(self):
        with mock.patch.dict(os.environ, {"CORRAL_CONTAINER": ""}):
            self.assertEqual(doctor.container_report(), [])

    def test_reports_never_raise(self):
        with mock.patch.dict(os.environ, {"CORRAL_CONTAINER": "1"}):
            out = doctor.container_report(run_report="/nonexistent.json")
        self.assertTrue(any("unreadable" in l for l in out))

    def test_full_report(self):
        rep = {"identity": {"user": "alice", "uid": 501, "gid": 20, "home": "/Users/alice"},
               "emulation": {"machine": "x86_64", "mode": "rosetta"},
               "path": {"ok": True}, "hostname": {"ok": False, "actual": "abc",
                                                   "expected": "mac-host"},
               "parity_map": [], "overlays": [{"path": "/p/node_modules", "mounted": False}],
               "ssh": {"agent": None}, "workspace": "/nonexistent", "notes": []}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(rep, f)
        try:
            with mock.patch.dict(os.environ, {"CORRAL_CONTAINER": "1"}):
                out = "\n".join(doctor.container_report(run_report=f.name))
        finally:
            os.unlink(f.name)
        self.assertIn("emulation=rosetta", out)
        self.assertIn("hostname 'abc' != expected 'mac-host'", out)
        self.assertIn("overlay /p/node_modules", out)
        self.assertIn("ssh agent none", out)
        self.assertIn("!!  workspace /nonexistent", out)


class Ws3Basics(unittest.TestCase):
    def test_workspace_default(self):
        import sessions
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"CORRAL_WORKSPACE": d}):
                self.assertEqual(sessions.default_cwd(), Path(d))
                self.assertEqual(sessions.cwd_suggestions()[0], d)
            with mock.patch.dict(os.environ, {"CORRAL_WORKSPACE": d + "/missing"}):
                self.assertNotEqual(sessions.default_cwd(), Path(d + "/missing"))

    def test_bind_default(self):
        import importlib
        import subprocess
        code = "import hub; print(hub.BIND)"
        for env, want in (({"CORRAL_CONTAINER": "1"}, "0.0.0.0"),
                          ({}, "127.0.0.1"),
                          ({"CORRAL_CONTAINER": "1", "CORRAL_LIGHT_BIND": "127.0.0.1"},
                           "127.0.0.1")):
            e = {k: v for k, v in os.environ.items()
                 if k not in ("CORRAL_CONTAINER", "CORRAL_LIGHT_BIND")} | env
            with tempfile.TemporaryDirectory() as st:
                e["CORRAL_LIGHT_STATE"] = st
                out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e,
                                     capture_output=True, text=True, timeout=60)
            with self.subTest(env=env):
                self.assertEqual(out.stdout.strip().splitlines()[-1], want, out.stderr)


if __name__ == "__main__":
    unittest.main()
