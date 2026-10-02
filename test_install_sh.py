#!/usr/bin/env python3
"""Offline tests for install.sh — the parts that must hold without a network
or a real machine. Every run gets a scratch HOME and a PATH whose first
entry is a directory of stubs (uname, id, curl, systemctl, loginctl, crontab,
sudo), so nothing here can touch the real user's services or crontab.

    python3 test_install_sh.py
"""
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "install.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")


def _stub(dirpath, name, body):
    p = Path(dirpath) / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


class _Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="corral-install-sh-", dir=str(HERE))
        base = Path(self.tmp.name)
        self.home = base / "home"; self.home.mkdir()
        self.stubs = base / "stubs"; self.stubs.mkdir()
        # Safe defaults: a Linux x86_64 box, not root, with no internet.
        _stub(self.stubs, "uname", 'case "$1" in -s) echo Linux;; -m) echo x86_64;; *) echo Linux;; esac')
        _stub(self.stubs, "id", 'if [ "$1" = -u ]; then echo 1000; else echo "uid=1000"; fi')
        _stub(self.stubs, "curl", 'exit 7')
        _stub(self.stubs, "systemctl", 'exit 1')
        _stub(self.stubs, "loginctl", 'exit 1')
        _stub(self.stubs, "crontab", 'exit 0')
        _stub(self.stubs, "sudo", 'echo "sudo stub refused: $*" >&2; exit 1')

    def tearDown(self):
        self.tmp.cleanup()

    def run_sh(self, *args, stdin=subprocess.DEVNULL):
        env = {"HOME": str(self.home), "USER": "tester", "TERM": "dumb",
               "PATH": f"{self.stubs}:/usr/bin:/bin"}
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, stdin=stdin,
                              capture_output=True, text=True, timeout=60)


class Static(unittest.TestCase):
    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_strict_mode_and_error_trap(self):
        self.assertIn("set -Eeuo pipefail", TEXT)
        self.assertIn("trap on_error ERR", TEXT)

    def test_pins_are_present_and_well_formed(self):
        for name in ("NODE_VERSION", "NODE_SHA256_X64", "NODE_SHA256_ARM64",
                     "GROK_VERSION", "AIOS_SEED_REF"):
            self.assertTrue(re.search(rf'^{name}="[^"]+"', TEXT, re.M), name)
        shas = re.findall(r'^NODE_SHA256_\w+="([0-9a-f]+)"', TEXT, re.M)
        self.assertEqual(len(shas), 2)
        for sha in shas:
            self.assertEqual(len(sha), 64)
        self.assertTrue(re.search(r'^NODE_VERSION="v\d+\.\d+\.\d+"', TEXT, re.M))

    def test_never_writes_outside_home_without_sudo_prompt_text(self):
        # Every sudo use is one of the named package-manager or cron commands.
        for line in TEXT.splitlines():
            if "sudo " in line and not line.lstrip().startswith("#"):
                self.assertRegex(line, r"apt-get|dnf|pacman|zypper|apk|systemctl enable --now|loginctl|have sudo|die|printf|warn|cmd=|\$\{cmd#sudo \}",
                                 msg=line)

    def test_gated_seed_writes_go_through_approve(self):
        # C5: CLAUDE.md, mesh and hooks only ever land via install.py --approve.
        self.assertIn("--approve claude-md", TEXT)
        self.assertIn("--approve mesh-bootstrap", TEXT)
        self.assertIn("--approve memory-hooks --apply", TEXT)
        self.assertNotRegex(TEXT, r'>\s*"\$AIOS/CLAUDE\.md"')

    def test_no_piped_grep_q_under_pipefail(self):
        # grep -q exits at the first match; under pipefail the producer's SIGPIPE
        # then fails the whole pipeline (seen live: the glibc probe died on Arch).
        self.assertNotRegex(TEXT, r"\|\s*grep -q")

    def test_no_vendor_api_keys(self):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY", "GROK_CODE_XAI_API_KEY"):
            self.assertNotIn(key, TEXT)


class Behaviour(_Run):
    def test_help_and_version(self):
        r = self.run_sh("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--lanes", r.stdout)
        self.assertIn("--uninstall", r.stdout)
        r = self.run_sh("--version")
        self.assertEqual(r.returncode, 0)
        self.assertRegex(r.stdout, r"corral-light installer \d+\.\d+\.\d+")

    def test_unknown_option_is_exit_2(self):
        r = self.run_sh("--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown option", r.stderr)

    def test_refuses_root(self):
        _stub(self.stubs, "id", 'if [ "$1" = -u ]; then echo 0; else echo "uid=0"; fi')
        r = self.run_sh("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Do not run this as root", r.stdout + r.stderr)

    def test_refuses_non_linux(self):
        _stub(self.stubs, "uname", 'echo Darwin')
        r = self.run_sh("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("for Linux", r.stdout + r.stderr)

    def test_refuses_unknown_cpu(self):
        _stub(self.stubs, "uname", 'case "$1" in -m) echo mips;; *) echo Linux;; esac')
        r = self.run_sh("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported CPU", r.stdout + r.stderr)

    def test_rejects_unknown_lane_before_touching_anything(self):
        r = self.run_sh("--yes", "--lanes", "claude,bard")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unknown assistant 'bard'", r.stdout + r.stderr)
        self.assertFalse((self.home / "tools").exists())

    def test_no_internet_stops_before_any_write(self):
        r = self.run_sh("--yes", "--no-service")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("No internet connection", r.stdout + r.stderr)
        self.assertFalse((self.home / "tools").exists())
        self.assertFalse((self.home / ".local/bin").exists())

    def test_failure_names_the_step_and_the_log(self):
        r = self.run_sh("--yes", "--no-service")
        out = r.stdout + r.stderr
        self.assertIn("install.log", out)
        log = self.home / ".local/share/corral-light/install.log"
        self.assertTrue(log.is_file())
        self.assertIn("Checking this machine", log.read_text(encoding="utf-8"))

    def test_unexpected_failure_inside_main_stops_the_run_and_names_the_step(self):
        # The ERR trap must be live INSIDE the `main | tee` subshell: a plain
        # failing command (not a die) has to end the run with the step named.
        _stub(self.stubs, "curl", 'exit 0')                       # internet "reachable"
        _stub(self.stubs, "git", 'echo "git stub: boom" >&2; exit 1')
        r = self.run_sh("--yes", "--no-service", "--no-schedule")
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("Step failed: Fetching Corral Light and AI-OS Seed", out)
        self.assertNotIn("Node.js", out.split("Step failed")[0].split("[4/11]")[-1] if "[4/11]" in out else "")
        self.assertFalse((self.home / ".local/share/corral-light/node").exists())

    def test_uninstall_on_an_empty_home_is_a_clean_no_op(self):
        r = self.run_sh("--uninstall", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no service installed", r.stdout)
        self.assertIn("no Seed install", r.stdout)
        # --yes takes the safe default (N) on destructive questions: clones and state stay.
        self.assertIn("clones left in place", r.stdout)
        self.assertIn("state left at", r.stdout)


if __name__ == "__main__":
    unittest.main()
