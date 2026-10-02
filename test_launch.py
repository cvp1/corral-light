#!/usr/bin/env python3
"""Offline tests for `corral-light launch` (launch.py) and the browser side
of the pre-approved pairing code.    python3 test_launch.py"""
import io
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import auth      # noqa: E402
import launch    # noqa: E402


class _TempState(unittest.TestCase):
    """auth binds its paths at import; point them at a scratch dir per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="corral-launch-", dir=str(HERE))
        base = Path(self.tmp.name)
        self._saved = (auth.STATE, auth.KEYFILE, auth.LOCKFILE, auth.PAIRFILE)
        auth.STATE, auth.KEYFILE = base, base / "session.key"
        auth.LOCKFILE, auth.PAIRFILE = base / "pair.lock", base / "pairing.json"

    def tearDown(self):
        auth.STATE, auth.KEYFILE, auth.LOCKFILE, auth.PAIRFILE = self._saved
        self.tmp.cleanup()


class PairedUrl(_TempState):
    def test_code_is_approved_single_use_and_claimable(self):
        url = launch.paired_url("http://127.0.0.1:8098")
        m = re.fullmatch(r"http://127\.0\.0\.1:8098/\?pair=([A-Z0-9]{3}-[A-Z0-9]{3})", url)
        self.assertIsNotNone(m, url)
        code = m.group(1)
        tok, status = auth.claim(code)
        self.assertEqual(status, "ok")
        self.assertEqual(auth.verify(tok), "operator")
        # Single use: the same code claims as expired the second time.
        self.assertEqual(auth.claim(code), (None, "expired"))

    def test_mint_limit_surfaces_as_exit_3(self):
        with mock.patch.object(launch, "hub_alive", return_value=True), \
             mock.patch.object(auth, "new_code", side_effect=auth.TooMany("x")):
            err = io.StringIO()
            with redirect_stderr(err):
                rc = launch.main(["--print"])
        self.assertEqual(rc, 3)
        self.assertIn("pairing code", err.getvalue())


class HubUrl(unittest.TestCase):
    def test_default_and_overrides(self):
        self.assertEqual(launch.hub_url({}), "http://127.0.0.1:8098")
        self.assertEqual(launch.hub_url({"CORRAL_LIGHT_URL": "http://h:1/"}), "http://h:1")
        self.assertEqual(launch.hub_url({"CORRAL_LIGHT_PORT": "8099"}), "http://127.0.0.1:8099")
        # Bound to every interface: the browser still dials loopback.
        self.assertEqual(launch.hub_url({"CORRAL_LIGHT_BIND": "0.0.0.0"}), "http://127.0.0.1:8098")

    def test_print_mode_prints_url_and_opens_nothing(self):
        with mock.patch.object(launch, "hub_alive", return_value=True), \
             mock.patch.object(launch, "paired_url", return_value="http://127.0.0.1:8098/?pair=ABC-DEF"), \
             mock.patch.object(launch.subprocess, "Popen") as popen:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = launch.main(["--print"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "http://127.0.0.1:8098/?pair=ABC-DEF")
        popen.assert_not_called()

    def test_hub_down_is_exit_2_with_the_start_command(self):
        with mock.patch.object(launch, "hub_alive", return_value=False):
            err = io.StringIO()
            with redirect_stderr(err):
                rc = launch.main(["--print", "--url", "http://127.0.0.1:1"])
        self.assertEqual(rc, 2)
        self.assertIn("corral-light serve", err.getvalue())

    def test_no_display_means_no_opener(self):
        self.assertIsNone(launch.opener(platform="linux", env={}))
        with mock.patch.object(launch.shutil, "which", side_effect=lambda n: "/usr/bin/xdg-open" if n == "xdg-open" else None):
            self.assertEqual(launch.opener(platform="linux", env={"DISPLAY": ":0"}), ["/usr/bin/xdg-open"])


class BrowserSide(unittest.TestCase):
    """The static page must consume ?pair= once and only accept a code shape."""

    def test_app_js_reads_and_strips_the_param(self):
        js = (HERE / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("searchParams.get('pair')", js)
        self.assertIn("searchParams.delete('pair')", js)
        self.assertIn("history.replaceState", js)
        self.assertIn("/^[A-Z0-9]{3}-[A-Z0-9]{3}$/", js)
        # The preset path must still poll claim, never mint a second code.
        self.assertIn("const preset = presetPairCode();", js)

    def test_wrapper_dispatches_launch(self):
        sh = (HERE / "corral-light").read_text(encoding="utf-8")
        self.assertIn('launch) shift; exec "$PY" "$D/launch.py" "$@" ;;', sh)


if __name__ == "__main__":
    unittest.main()
