#!/usr/bin/python3
"""Tests for Corral Light.

TWO KINDS, AND THE SECOND IS THE POINT
    1. Behaviour: the ollama lane's protocol, the bounds, the static-path
       containment.
    2. STRUCTURE: proof that this build has not quietly grown back the parts it
       was forked to shed. A "light" fork does not get heavy in one commit; it
       gets heavy one convenience import at a time, and by then the fork's whole
       justification is gone and nobody notices because everything still works.
       These tests fail loudly on the FIRST such import.

Run: python3 -m unittest test_corral_light -v   (from this directory)
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

import ollama_acp


class StructuralIndependence(unittest.TestCase):
    """Light must run from its own directory, on a host with no CC workspace."""

    PY_FILES = sorted(p for p in ROOT.glob("*.py"))

    def test_no_module_imports_the_cc_workspace(self):
        """No `_lib`, no `harness`, no `lightsail`, no sibling project.

        This is the load-bearing one. Light is meant to be copyable to a
        machine that has none of the fleet on it; a single `from _lib import …`
        makes that false, and the failure appears not at import time but the
        first time someone runs it somewhere else.
        """
        banned = ("_lib", "harness", "lightsail", "cc_handoff", "Github/CC/")
        for f in self.PY_FILES:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                # Only lines that could EXECUTE. A first cut flagged any line
                # containing both a banned word and "import", which matched
                # this file's own docstring describing the rule — a scanner
                # that cannot survive its own documentation is a scanner that
                # gets deleted the first time it cries wolf.
                if not (s.startswith(("import ", "from ")) or "Path(" in s):
                    continue
                for token in banned:
                    if token in s:
                        self.fail(f"{f.name}:{i} reaches into the CC "
                                  f"workspace: {s[:90]}")

    def test_no_heavy_corral_module_is_imported(self):
        """The fleet modules are absent from the tree AND from every import."""
        heavy = ("fleet", "estate", "finops", "runs", "attention", "asks",
                 "push", "schedule", "library", "mail", "today", "residency",
                 "delegates", "browser_ui", "foreign", "aios_memory",
                 "gmail_local", "ssh_acp", "local_acp", "herdr_bridge")
        for name in heavy:
            self.assertFalse((ROOT / f"{name}.py").exists(),
                             f"{name}.py is back in the tree")
        for f in self.PY_FILES:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                if not s.startswith(("import ", "from ")):
                    continue
                mod = s.split()[1].split(".")[0]
                if mod in heavy:
                    self.fail(f"{f.name}:{i} imports the heavy module {mod}")

    def test_hub_serves_only_the_live_control_plane(self):
        """Every route is a session route, the pairing pair, or static.

        A route added here for "just one" fleet reading is how the fork ends.
        """
        text = (ROOT / "hub.py").read_text(encoding="utf-8")
        import re
        routes = set(re.findall(r'p == "(/[^"]*)"', text))
        allowed_prefixes = ("/api/session/", "/api/pair/")
        allowed_exact = {"/health", "/", "/index.html", "/sw.js",
                         "/manifest.json", "/api/state", "/api/stream"}
        for r in routes:
            if r in allowed_exact or r.startswith(allowed_prefixes):
                continue
            self.fail(f"hub.py serves {r}, which is not a Live-surface route")

    def test_frontend_calls_no_route_the_hub_does_not_serve(self):
        """The browser and the server agree on the API surface.

        The front end is a TRIM of the full Corral's app.js, so a leftover
        `api('/api/attention')` would not be a syntax error — it would be a
        silent 404 on every render, which is exactly the class of bug a trim
        produces.
        """
        import re
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        hub = (ROOT / "hub.py").read_text(encoding="utf-8")
        called = set(re.findall(r"""api\(['"](/api/[\w/-]+)""", js))
        called |= set(re.findall(r"""EventSource\(['"](/api/[\w/-]+)""", js))
        for path in sorted(called):
            self.assertIn(f'"{path}"', hub,
                          f"app.js calls {path}, which hub.py does not serve")

    def test_state_dir_is_not_the_full_corrals(self):
        """Two hubs sharing one state dir share panes and the session key."""
        for name in ("sessions.py", "auth.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("corral-light", text, f"{name} state path")
            self.assertNotIn('".local/share/corral"', text,
                             f"{name} points at the full Corral's state dir")


class LanesAreHonest(unittest.TestCase):
    """A picker that lists what is not installed is a button that lies."""

    def test_every_lane_declares_what_it_needs_on_disk(self):
        import sessions
        for key, spec in sessions.AGENTS.items():
            with self.subTest(lane=key):
                self.assertTrue(spec.get("requires") or key in ("codex",),
                                f"{key} has no `requires`, so availability "
                                f"would be judged by argv[0] alone")

    def test_interpreter_lanes_do_not_rely_on_argv0(self):
        """argv[0] is python3 for four of five lanes and always exists."""
        import sessions
        for key, spec in sessions.AGENTS.items():
            argv0 = spec["argv"][0]
            if "python" in argv0 or argv0.endswith("/env"):
                self.assertTrue(spec.get("requires") or key == "codex",
                                f"{key} launches via an interpreter but names "
                                f"nothing in `requires`")

    def test_the_no_tools_lane_says_so(self):
        """The Ollama lane raises no permission requests. An operator reading
        an empty rail must be able to tell that from a broken rail."""
        import sessions
        spec = sessions.AGENTS["ollama"]
        self.assertIn("chat only", spec["label"].lower())
        self.assertIn("no tools", spec["needs"].lower())

    def test_posture_is_only_claimed_where_it_is_imposed(self):
        import sessions
        for key, spec in sessions.AGENTS.items():
            if key != "claude":
                self.assertFalse(spec["posture_via_config_dir"],
                                 f"{key} claims Corral sets its permission "
                                 f"posture; only the CLAUDE_CONFIG_DIR lane does")


class OllamaAdapter(unittest.TestCase):

    def setUp(self):
        self.sent = []
        self.srv = ollama_acp.Server(out=self._Out(self.sent))

    class _Out:
        def __init__(self, sink): self.sink = sink
        def write(self, s):
            if s.strip():
                self.sink.append(json.loads(s))
        def flush(self): pass

    def test_initialize_advertises_no_tools(self):
        self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {}})
        r = self.sent[-1]["result"]
        self.assertEqual(r["protocolVersion"], ollama_acp.PROTOCOL_VERSION)
        self.assertIn("no tools", r["agentInfo"]["description"])
        # No fs/terminal capability is claimed anywhere in the handshake.
        self.assertNotIn("fs", r["agentCapabilities"])

    def test_unknown_method_is_refused_not_ignored(self):
        """A silently dropped request leaves the client waiting forever —
        acp.py's prompt wait has no clock by design."""
        self.srv.handle({"jsonrpc": "2.0", "id": 7, "method": "session/nonsense"})
        self.assertEqual(self.sent[-1]["error"]["code"], -32601)

    def test_prompt_for_an_unknown_session_answers_with_an_error(self):
        self.srv.handle({"jsonrpc": "2.0", "id": 9, "method": "session/prompt",
                         "params": {"sessionId": "nope", "prompt": []}})
        # Threaded; the guard must still produce exactly one reply for id 9.
        import time
        for _ in range(50):
            if any(m.get("id") == 9 for m in self.sent):
                break
            time.sleep(0.02)
        replies = [m for m in self.sent if m.get("id") == 9]
        self.assertEqual(len(replies), 1)
        self.assertIn("error", replies[0])

    def test_set_config_refuses_a_model_that_is_not_pulled(self):
        self.srv.handle({"jsonrpc": "2.0", "id": 3,
                         "method": "session/set_config_option",
                         "params": {"configId": "model",
                                    "value": "a-model-nobody-has:latest"}})
        self.assertIn("error", self.sent[-1])

    def test_set_config_refuses_an_unknown_option(self):
        self.srv.handle({"jsonrpc": "2.0", "id": 4,
                         "method": "session/set_config_option",
                         "params": {"configId": "effort", "value": "high"}})
        self.assertEqual(self.sent[-1]["error"]["code"], -32602)

    def test_history_is_bounded_by_turns_and_by_bytes(self):
        h = [{"role": "user", "content": "x"} for _ in range(ollama_acp.MAX_TURNS + 25)]
        ollama_acp.Server._trim(h)
        self.assertEqual(len(h), ollama_acp.MAX_TURNS)

        big = "y" * (ollama_acp.MAX_HISTORY_CHARS // 2)
        h = [{"role": "user", "content": big} for _ in range(6)]
        ollama_acp.Server._trim(h)
        total = sum(len(m["content"]) for m in h)
        self.assertLessEqual(total, ollama_acp.MAX_HISTORY_CHARS,
                             "turn count alone is not a bound: one pasted file "
                             "blows the window in three messages")
        self.assertGreaterEqual(len(h), 1, "trimming must never empty history")


class StaticPathContainment(unittest.TestCase):
    """A string prefix check is not a containment check."""

    def test_traversal_out_of_static_is_refused(self):
        import hub
        for bad in ("../hub.py", "../../etc/passwd", "../static-secret/x",
                    "../../../../../../etc/shadow"):
            self.assertIsNone(hub._safe_static_path(bad), bad)

    def test_percent_encoded_traversal_resolves_INSIDE_static(self):
        """`..%2fhub.py` is not a traversal here, and the reason matters.

        hub.py reads `urlparse(self.path).path`, which does NOT percent-decode.
        So `%2f` stays a literal character in a FILENAME rather than becoming a
        separator: the path resolves to `static/..%2fhub.py`, inside the root,
        and the caller's `is_file()` then 404s it because no such file exists.
        Asserting None here (the first version of this test did) would encode
        the wrong mechanism and would start failing the day someone adds a
        legitimate unquote — while the real risk, decoding BEFORE containment,
        would go untested. Pin the actual behaviour instead.
        """
        import hub
        got = hub._safe_static_path("..%2fhub.py")
        self.assertIsNotNone(got)
        self.assertEqual(got.parent, (ROOT / "static").resolve())
        self.assertFalse(got.is_file())

    def test_a_real_asset_resolves(self):
        import hub
        self.assertIsNotNone(hub._safe_static_path("app.js"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
