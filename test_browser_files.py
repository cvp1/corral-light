#!/usr/bin/python3
"""The browser files as a browser gets them: app.js and sw.js parse, every
dialog in index.html is closed, style.css's braces balance, and every
selftest_*.mjs passes. In CI, through test_corral_light.

Why: the selftests extract functions one by one, so a missing brace between
two of them passed every check; and nothing ran them in CI at all. On
2026-11-23 a merge dropped openPanel's closing brace and the Ask-a-panel
dialog's closing tags, and master went green with a wall that would not load.
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed")
class TheBrowserFilesLoad(unittest.TestCase):
    def test_the_scripts_parse(self):
        for f in ("static/app.js", "static/sw.js"):
            r = subprocess.run([NODE, "--check", str(ROOT / f)], capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, f"{f} does not parse:\n{r.stderr[-800:]}")

    def test_every_dialog_is_closed(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        for tag in ("dialog", "form", "menu", "div", "label", "select"):
            opened = len(re.findall(rf"<{tag}[\s>]", html))
            closed = html.count(f"</{tag}>")
            self.assertEqual(opened, closed, f"index.html: {opened} <{tag}> but {closed} </{tag}>")

    def test_the_stylesheet_balances(self):
        css = re.sub(r"/\*.*?\*/", "", (ROOT / "static" / "style.css").read_text(encoding="utf-8"),
                     flags=re.S)
        self.assertEqual(css.count("{"), css.count("}"), "style.css: unbalanced braces")

    def test_every_selftest_passes(self):
        files = sorted(ROOT.glob("selftest_*.mjs"))
        self.assertGreater(len(files), 10)
        bad = []
        for f in files:
            r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=120, cwd=str(ROOT))
            if r.returncode != 0:
                bad.append(f"{f.name}:\n{(r.stderr or r.stdout)[-600:]}")
        self.assertEqual(bad, [], "\n\n".join(bad))


if __name__ == "__main__":
    unittest.main()
