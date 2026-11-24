#!/usr/bin/python3
"""Dictate (dictate.py): the composer's 🎙. A fake runner stands in for
`shortcuts` / `voxtype`, so no microphone is touched. Collected by
test_corral_light.py."""
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("corral-light-test-")

import dictate  # noqa: E402


class Dictate(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tmpdir(self, "corral-light-dictate-"))

    def cfg(self, obj):
        f = self.dir / "dictate.json"
        f.write_text(json.dumps(obj))
        return dictate.load(f)

    def test_no_config_means_defaults(self):
        c = dictate.load(self.dir / "absent.json")
        self.assertEqual(c, {})
        self.assertEqual(dictate.local_command(c, "darwin"),
                         ["shortcuts", "run", "VoiceInk Toggle"])
        self.assertEqual(dictate.client(c),
                         {"macShortcut": "VoiceInk Toggle", "linuxScheme": "corral-dictate"})

    def test_linux_uses_voxtype_only_when_installed(self):
        with mock.patch("shutil.which", return_value="/usr/bin/voxtype"):
            self.assertEqual(dictate.local_command({}, "linux"), ["voxtype", "record", "toggle"])
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(dictate.local_command({}, "linux"))
        self.assertIsNone(dictate.local_command({}, "win32"))

    def test_config_names_the_shortcut_or_overrides_the_command(self):
        self.assertEqual(dictate.local_command(self.cfg({"mac_shortcut": "Dictate"}), "darwin"),
                         ["shortcuts", "run", "Dictate"])
        self.assertEqual(dictate.local_command(self.cfg({"local_command": ["mytool", "go"]}), "darwin"),
                         ["mytool", "go"])

    def test_bad_config_is_loud(self):
        for bad in ([], {"mac_shortcut": ""}, {"mac_shortcut": "x" * 81},
                    {"local_command": "voxtype record toggle"}, {"local_command": []}):
            with self.subTest(bad=bad), self.assertRaises(dictate.DictateError):
                self.cfg(bad)

    def test_toggle_runs_the_command_once(self):
        calls = []
        def run(argv, **kw):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stderr=b"")
        c = {"local_command": ["fake", "toggle"]}
        self.assertEqual(dictate.toggle(c, run=run), {"ok": True, "ran": "fake"})
        self.assertEqual(calls, [["fake", "toggle"]])

    def test_a_missing_shortcut_says_what_to_make(self):
        def run(argv, **kw):
            return SimpleNamespace(returncode=1, stderr=b"Error: The shortcut could not be found.\n")
        with mock.patch.object(dictate, "local_command",
                               return_value=["shortcuts", "run", "VoiceInk Toggle"]):
            with self.assertRaisesRegex(dictate.DictateError, "Toggle VoiceInk Recorder"):
                dictate.toggle({}, run=run)

    def test_timeout_and_missing_binary_are_readable(self):
        def slow(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 15)
        def gone(argv, **kw):
            raise FileNotFoundError(2, "No such file or directory")
        c = {"local_command": ["fake"]}
        with self.assertRaisesRegex(dictate.DictateError, "did not finish"):
            dictate.toggle(c, run=slow)
        with self.assertRaisesRegex(dictate.DictateError, "would not start"):
            dictate.toggle(c, run=gone)


if __name__ == "__main__":
    unittest.main()
