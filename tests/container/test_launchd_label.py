"""diagnose.installed_label: the renamed default label, and discovery of a
LaunchAgent installed under its old name (a host installed before the
2026-10-01 rename keeps that label until it is reinstalled).

    python3 -m unittest tests/container/test_launchd_label.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import diagnose      # noqa: E402
import install_service  # noqa: E402


class InstalledLabel(unittest.TestCase):
    def test_installer_and_diagnose_agree(self):
        self.assertEqual(install_service.LABEL, diagnose.DEFAULT_LAUNCHD_LABEL)

    def test_env_wins(self):
        self.assertEqual(diagnose.installed_label("/nonexistent",
                                                  {"CORRAL_LAUNCHD_LABEL": "x.y"}), "x.y")

    def test_nothing_installed_gives_default(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(diagnose.installed_label(d, {}),
                             diagnose.DEFAULT_LAUNCHD_LABEL)

    def test_old_label_discovered(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "com.example.corral-light.plist").write_text("x")
            Path(d, "com.example.corral-light-watch.plist").write_text("x")
            self.assertEqual(diagnose.installed_label(d, {}),
                             "com.example.corral-light")

    def test_default_preferred_over_old(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "com.example.corral-light.plist").write_text("x")
            Path(d, f"{diagnose.DEFAULT_LAUNCHD_LABEL}.plist").write_text("x")
            self.assertEqual(diagnose.installed_label(d, {}),
                             diagnose.DEFAULT_LAUNCHD_LABEL)


if __name__ == "__main__":
    unittest.main()
