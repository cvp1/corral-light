#!/usr/bin/python3
"""Tests for Corral Light: lane behaviour, bounds, and structural
independence from full Corral.

Run: python3 -m unittest test_corral_light -v   (from this directory)
"""
import json
import os
import tempfile
import threading
import sys
import time
import types
import uuid
import unittest
from pathlib import Path

# Default the state dir before anything imports sessions (STATE binds at
# import time), so the suite never writes into the live store.
from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("corral-light-test-")

ROOT = Path(__file__).resolve().parent


def acp_unanswered():
    """The core's "no human selection yet" sentinel, distinct from a falsy option id."""
    import acp
    return acp._UNANSWERED

import ollama_acp

# The shared permission-rail and edge contracts, collected into this suite.
from corral_core.test_acp_rail import *          # noqa: F401,F403
from corral_core.test_edge import *              # noqa: F401,F403


class TheSuiteNeverTouchesTheLiveConfig(unittest.TestCase):
    """A hub a test starts runs the module runner, which reads the pins in
    the config dir. With a scratch state dir but the live config dir, it
    found the operator's installed modules missing from the scratch state
    and disabled them in the live pins. Config is scratch too."""

    def test_config_dir_is_scratch(self):
        cfg = os.environ.get("CORRAL_LIGHT_CONFIG_DIR")
        self.assertTrue(cfg, "CORRAL_LIGHT_CONFIG_DIR is not set for the suite")
        live = Path.home() / ".config" / "corral-light"
        self.assertNotEqual(Path(cfg).resolve(), live.resolve())
        tmp = str(Path(tempfile.gettempdir()).resolve())
        self.assertTrue(str(Path(cfg).resolve()).startswith(tmp + os.sep), cfg)

    def test_spawned_hub_envs_carry_the_scratch_config(self):
        for name in ("test_cli.py", "test_resilience.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            self.assertEqual(src.count('"CORRAL_LIGHT_CONFIG_DIR":'),
                             src.count('"CORRAL_LIGHT_STATE":'),
                             f"{name}: a hub env sets the state dir but not the config dir")


class TheCoreNeverImportsFullCorral(unittest.TestCase):
    """The core never imports from full Corral; the dependency points one way."""

    def test_no_module_in_the_core_reaches_into_corral(self):
        """Reads parsed imports, not text, so prose about `corral/` does not count."""
        import ast
        core = ROOT / "corral_core"
        for f in sorted(core.glob("*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            names = []
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    names += [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.module:
                    names.append(n.module)
            for mod in names:
                self.assertNotEqual(
                    mod.split(".")[0], "corral",
                    f"{f.name} imports {mod!r} from full Corral — the public "
                    f"product must stand alone")

    def test_the_core_names_no_host(self):
        """The public core names no machine, LAN address or account."""
        import re
        bad = re.compile(r"\b192\.168\.\d|/home/[a-z]|/Users/[a-z]")
        # *.toml too: the rig template ships in the core and is copied by users.
        files = sorted((ROOT / "corral_core").glob("*.py")) + \
            sorted((ROOT / "corral_core").glob("*.toml"))
        self.assertIn("rig.example.toml", [f.name for f in files])
        for f in files:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                m = bad.search(line)
                self.assertIsNone(
                    m, f"{f.name}:{i} names a host or account in a public "
                       f"repository: {line.strip()[:90]}")

    # Private machine, account and person names, stored as SHA-256 digests so
    # this public file does not publish the very names it guards. Compare by
    # hashing each lowercased [a-z0-9-] token. Add a name with:
    #   printf '%s' name | shasum -a 256
    _BANNED_TOKEN_SHA256 = frozenset({
        "d9999399b7c4d4fe56538604ed39fbe591786e74f9016017ec64b1fd75bf8b64",
        "6957dc07e908dacf131a71fae3d014ea03320ef1d10100d2e7c294dcec7838a8",
        "cabdfa67f88f656fd39b2728d3802380bf10cc1122482a949490bed40f3fe533",
        "0ff87b356f0c980ceecb306a5864a20cfbad72760550d09470fe6e205585bf18",
        "fc1d4d30fc51ae5fd0c907657255c43847fe086f1a1604135fe95f7bc1adbd47",
        "87ca5ee7de4c7947b0162a295bb7d0d6c909d13472e38ba6bd5236ca9cfc116d",
        "ef9a42a96b9e9a928200c25097b8a72cda08d8d32e4e8ce9ad035f32376d2ea7",
        "3f8dc034c5f3e9d87b702d63594eb3b5d0dc2dd90c58d2effcb966f0fc613817",
        "c157a0b0d40f9d9506c72fae584069d1692b816da16cd76c648d5761c588057a",
    })
    _BANNED_NET24_SHA256 = frozenset({
        "6c6d2c9533c0ecb6e557384314d063cb75cce7d5b8a5b52ac706f33526c4dac7",
    })

    def test_the_whole_repo_names_no_private_host_account_or_person(self):
        """Every shipped text file, docs and reviews included: no private
        machine, account or person name, and no address on the private LAN.
        Hyphenated tokens are checked whole and by part. LICENSE keeps the
        copyright holder's name."""
        import hashlib
        import re
        token = re.compile(r"[a-z0-9][a-z0-9-]*")
        net24 = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d")
        banned = self._BANNED_TOKEN_SHA256 | self._BANNED_NET24_SHA256
        sha = lambda t: hashlib.sha256(t.encode()).hexdigest()
        skip_dirs = {".git", "node_modules", "__pycache__"}
        hits = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for name in filenames:
                if name == "LICENSE":
                    continue
                # A git worktree's `.git` is a file pointing at the main
                # checkout by absolute path: local, never shipped.
                if name == ".git":
                    continue
                f = Path(dirpath) / name
                try:
                    text = f.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    words = set()
                    for t in token.findall(line.lower()):
                        words.add(t)
                        words.update(t.split("-"))
                    words.update(net24.findall(line))
                    if any(sha(w) in banned for w in words):
                        hits.append(f"{f.relative_to(ROOT)}:{i}")
        self.assertEqual(hits[:20], [], f"{len(hits)} lines name a private "
                                         f"host, account, person or LAN")

    def test_the_core_imports_with_nothing_but_this_tree_on_the_path(self):
        """Import the core in a clean interpreter whose path holds only this directory."""
        import subprocess
        # Keep the stdlib, drop every workspace entry.
        prog = ("import sys; "
                "sys.path[:] = [%r] + [p for p in sys.path "
                "                      if 'Github/CC' not in p and p]; "
                "from corral_core import acp; print(acp.MAX_STDOUT_LINE)" % str(ROOT))
        r = subprocess.run([sys.executable, "-c", prog],
                           capture_output=True, text=True, timeout=60, cwd="/")
        self.assertEqual(r.returncode, 0,
                         f"the core cannot import standalone: {r.stderr[-500:]}")


def _config_dir_only(spec):
    """The claude lane with posture imposable only through CLAUDE_CONFIG_DIR
    (no ACP `mode` route).
    """
    narrowed = dict(spec)
    narrowed.pop("posture_via_acp_mode", None)
    return narrowed


def _pin_sessions_platform(test, name):
    """Force sessions.sys.platform for the rest of this test; CLAUDE_CONFIG_DIR
    isolation is platform-dependent (darwin refuses it).
    """
    import sessions
    real = sessions.sys.platform
    sessions.sys.platform = name
    test.addCleanup(lambda: setattr(sessions.sys, "platform", real))


class StructuralIndependence(unittest.TestCase):
    """Light must run from its own directory, on a host with no CC workspace."""

    PY_FILES = sorted(p for p in ROOT.glob("*.py"))

    def test_no_module_imports_the_cc_workspace(self):
        """No `_lib`, `harness`, `lightsail` or sibling-project import: Light must
        run on a host that has none of them.
        """
        banned = ("_lib", "harness", "lightsail", "cc_handoff", "Github/CC/")
        for f in self.PY_FILES:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                # Only lines that could execute, so docstrings describing the rule don't match.
                if not (s.startswith(("import ", "from ")) or "Path(" in s):
                    continue
                for token in banned:
                    if token in s:
                        self.fail(f"{f.name}:{i} reaches into the CC "
                                  f"workspace: {s[:90]}")

    def test_no_heavy_corral_module_is_imported(self):
        """The heavy fleet modules are absent from the tree and from every import."""
        heavy = ("fleet", "estate", "finops", "runs", "attention", "asks",
                 "push", "library", "mail", "today", "residency",
                 "delegates", "browser_ui", "foreign", "aios_memory",
                 "gmail_local", "local_acp", "herdr_bridge")
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
        # /api/module/: installed modules' snapshots and refresh, added
        # 2026-10-07 for the module seam (docs/finops-module-plan.md §4.6).
        # A module never adds a route; these serve every module the same way.
        allowed_prefixes = ("/api/session/", "/api/pair/", "/api/content/", "/api/module/")
        allowed_exact = {"/health", "/", "/index.html", "/sw.js",
                         "/manifest.json", "/api/state", "/api/stream",
                         "/api/search",
                         # The lane list alone, for consult (PERF-REVIEW-2026-10-04 item 2).
                         "/api/lanes",
                         # Starts the vendor's login for the Live tab's Claude lane; exact path only.
                         "/api/claude/login",
                         # The installed modules' list (module seam, 2026-10-07).
                         "/api/modules"}
        for r in routes:
            if r in allowed_exact or r.startswith(allowed_prefixes):
                continue
            self.fail(f"hub.py serves {r}, which is not a Live-surface route")

    def test_frontend_calls_no_route_the_hub_does_not_serve(self):
        """Every `api()` path in the front end is a route the hub serves."""
        import re
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        hub = (ROOT / "hub.py").read_text(encoding="utf-8")
        called = set(re.findall(r"""api\(['"](/api/[\w/-]+)""", js))
        called |= set(re.findall(r"""EventSource\(['"](/api/[\w/-]+)""", js))
        for path in sorted(called):
            self.assertIn(f'"{path}"', hub,
                          f"app.js calls {path}, which hub.py does not serve")

    def test_nothing_hardcodes_a_path_from_the_machine_it_was_built_on(self):
        """No `/home/<someone>` or `/Users/<someone>` path outside the plist."""
        import re
        # This file is excluded: it quotes the literals it forbids.
        checked = [f for f in self.PY_FILES if f.name != Path(__file__).name]
        checked += [ROOT / "static" / "app.js", ROOT / "static" / "index.html",
                    ROOT / "corral-light"]
        pattern = re.compile(r"/(?:home|Users)/[a-z]")
        for f in checked:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                if s.startswith(("#", "//", "*", "<!--")):
                    continue            # prose about paths is fine
                if pattern.search(s):
                    self.fail(f"{f.name}:{i} hardcodes a path from the build "
                              f"machine: {s[:90]}")

    def test_the_new_conversation_default_comes_from_the_server(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("S.defaultCwd", js)
        sess = (ROOT / "sessions.py").read_text(encoding="utf-8")
        self.assertIn('"defaultCwd"', sess)

    def test_config_dirs_are_not_the_full_corrals(self):
        """Every per-user config path is corral-light's own, not full Corral's."""
        for name in ("mcp.py", "codex_launcher.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                self.assertNotIn('.config/corral/', s,
                                 f"{name}:{i} shares the full Corral's config")

    def test_every_print_flushes(self):
        """Every print flushes: under a service manager stdout is block-buffered."""
        # Walk the AST rather than matching strings.
        import ast
        for f in self.PY_FILES:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "print"):
                    continue
                flush = next((k for k in node.keywords if k.arg == "flush"), None)
                self.assertTrue(
                    flush is not None and getattr(flush.value, "value", None) is True,
                    f"{f.name}:{node.lineno} prints without flush=True")

    def test_nothing_reads_or_writes_the_full_corrals_state(self):
        """No module reads or writes full Corral's state (CORRAL_STATE)."""
        for f in self.PY_FILES:
            if f.name == Path(__file__).name:
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                self.assertNotIn('"CORRAL_STATE"', s,
                                 f"{f.name}:{i} reads the full Corral's state var")
                # The reviewer sandbox names it only to HIDE it from a blind
                # reviewer (its session key must stay out of reach); hiding
                # is neither a read nor a write.
                if f.name == "review_sandbox.py" and "# the full Corral's state" in s:
                    continue
                self.assertNotIn('.local/share/corral"', s,
                                 f"{f.name}:{i} points at the full Corral's state dir")

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


class IntentionalPauseLifecycle(unittest.TestCase):
    """A deliberate pause must survive the child process teardown window."""

    def test_expected_pause_exit_stays_detached(self):
        import sessions

        pane = sessions.Pane.__new__(sessions.Pane)
        pane.id = "pause-test"
        pane.agent = "claude"
        pane.cwd = "/tmp"
        pane.title = "pause test"
        pane.title_locked = False
        pane.minimized = False
        pane.order = None
        pane.pinned = False
        pane.model = None
        pane.effort = None
        pane.config = {}
        pane.commands = []
        pane.posture = "auto"
        pane.posture_enforced = False
        pane.error = None
        pane.pending = {}
        pane.usage = {}
        pane.created = "now"
        pane.mgr = types.SimpleNamespace(broadcast=lambda event: None)
        pane.client = types.SimpleNamespace(
            alive=True, p=types.SimpleNamespace(poll=lambda: 0))
        pane.state = "detached"
        pane._expect_exit = True
        pane.last_activity = time.time()
        pane.events = []
        pane._seq = 0
        pane._lock = threading.Lock()
        pane._replaying = False
        pane._log = None
        pane._since_rotate_check = 0
        result = pane.snapshot()
        self.assertEqual(result["state"], "detached")
        self.assertFalse(any(e["kind"] == "state" for e in pane.events))


class LayoutBroadcasts(unittest.TestCase):
    """Shared layout mutations reach every open browser tab."""

    def test_minimize_broadcasts_authoritative_layout(self):
        import queue
        import sessions

        manager = sessions.Manager.__new__(sessions.Manager)
        manager.subscribers = []
        manager._lock = threading.Lock()
        q = queue.Queue()
        manager.subscribe(q)

        pane = sessions.Pane.__new__(sessions.Pane)
        pane.id = "layout-test"
        pane.mgr = manager
        pane.minimized = False
        pane.pinned = False
        pane.order = None
        pane.save_meta = lambda: None
        pane.set_minimized(True)

        event = q.get_nowait()
        self.assertEqual(event["kind"], "layout")
        self.assertEqual(event["pane"], pane.id)
        self.assertTrue(event["data"]["minimized"])

    def test_layout_events_are_handled_before_pane_sequence_deduplication(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = js.index("es.onmessage = m =>")
        body = js[start:js.index("/* ── new-conversation", start)]
        self.assertIn("ev.kind === 'layout'", body)
        self.assertLess(body.index("ev.kind === 'layout'"),
                        body.index("ev.seq <= last"))

    def test_pin_and_reorder_broadcast_the_updated_roster(self):
        import queue
        import sessions

        manager = sessions.Manager.__new__(sessions.Manager)
        manager.subscribers = []
        manager._lock = threading.Lock()
        manager.panes = {}
        q = queue.Queue()
        manager.subscribe(q)
        for i, pane_id in enumerate(("one", "two")):
            manager.panes[pane_id] = types.SimpleNamespace(
                id=pane_id, minimized=False, pinned=False, order=None,
                created=str(i), save_meta=lambda: None)

        manager.set_pinned("two", True)
        pinned_events = [q.get_nowait() for _ in range(2)]
        self.assertTrue(any(e["pane"] == "two" and e["data"]["pinned"]
                            for e in pinned_events))

        manager.reorder(["one", "two"])
        reorder_events = [q.get_nowait() for _ in range(2)]
        self.assertTrue(any(e["pane"] == "one" and e["data"]["order"] == 0
                            for e in reorder_events))


class MobileSurface(unittest.TestCase):
    """The narrow viewport still exposes the core live-tab actions."""

    def test_mobile_keeps_new_search_and_navigation_controls(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for control in ("mobile-actions", "mobile-new", "mobile-search",
                        "mobile-pane"):
            self.assertIn(control, html)
        self.assertIn(".mobile-actions", css)
        self.assertIn("@media(max-width:820px)", css)
        self.assertIn("wireMobileActions", js)


class ContentIndex(unittest.TestCase):
    """The index behind ⌘K. Runs against a scratch tree, never the real vault."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "state").mkdir()
        self.notes = base / "notes"
        (self.notes / "sub").mkdir(parents=True)
        (self.notes / "alpha.md").write_text(
            "# Alpha Note\n\nThe quick brown fox jumps over the lazy dog.\n")
        (self.notes / "sub" / "beta.md").write_text(
            "# Beta Note\n\nA note about hydroponics and lettuce.\n")
        (self.notes / "ignored.png").write_bytes(b"\x89PNG not indexable")
        (self.notes / ".hidden").mkdir()
        (self.notes / ".hidden" / "secret.md").write_text("# Secret\n\nfox\n")
        cfg = base / "content.json"
        cfg.write_text(json.dumps(
            [{"key": "notes", "label": "notes", "root": str(self.notes)}]))

        import importlib, content
        os.environ["CORRAL_LIGHT_STATE"] = str(base / "state")
        os.environ["CORRAL_CONTENT_CONFIG"] = str(cfg)
        self.content = importlib.reload(content)

    def tearDown(self):
        for k in ("CORRAL_CONTENT_CONFIG",):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def test_indexes_markdown_and_finds_it(self):
        hits = self.content.search("fox")["hits"]
        self.assertEqual([h["title"] for h in hits], ["Alpha Note"])
        self.assertIn("fox", hits[0]["snippet"].lower())

    def test_index_reads_only_the_configured_file_bound(self):
        large = "x" * (self.content.MAX_FILE + 1000)
        (self.notes / "large.txt").write_text(large)
        self.content.refresh(force=True)
        row = self.content.get("notes:large.txt")
        self.assertLessEqual(len(row["body"]), self.content.MAX_FILE)
        source = (ROOT / "content.py").read_text(encoding="utf-8")
        self.assertIn("fh.read(MAX_FILE)", source)

    def test_dotdirs_are_not_indexed(self):
        """A `.hidden/secret.md` matching the query must not surface."""
        titles = [h["title"] for h in self.content.search("fox")["hits"]]
        self.assertNotIn("Secret", titles)

    def test_a_symlink_escaping_the_root_is_not_followed(self):
        """Otherwise a link in a vault indexes the whole filesystem."""
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "leak.md").write_text("# Leak\n\nfox outside the root\n")
        try:
            (self.notes / "link.md").symlink_to(outside / "leak.md")
        except OSError:
            self.skipTest("no symlink support here")
        self.content.refresh(force=True)
        self.assertNotIn("Leak",
                         [h["title"] for h in self.content.search("fox")["hits"]])

    def test_get_refuses_a_path_that_now_escapes_the_root(self):
        """Index-time containment is not enough: replace the file with a
        symlink after indexing and attach would hand the agent a path that
        now points outside the vault."""
        hits = self.content.search("fox")["hits"]
        self.assertTrue(hits)
        pid = hits[0]["id"]
        self.assertIsNotNone(self.content.get(pid))
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir(exist_ok=True)
        secret = outside / "secret.md"
        secret.write_text("# Secret\nleaked\n")
        target = self.notes / "alpha.md"
        try:
            target.unlink()
            target.symlink_to(secret)
        except OSError:
            self.skipTest("no symlink support here")
        self.assertIsNone(self.content.get(pid),
                          "get() served a path that now resolves outside the root")

    def test_deleted_files_leave_the_index(self):
        """A store that only grows keeps answering with files that are gone."""
        self.assertTrue(self.content.search("hydroponics")["hits"])
        (self.notes / "sub" / "beta.md").unlink()
        self.content.refresh(force=True)
        self.assertFalse(self.content.search("hydroponics")["hits"])

    def test_user_text_is_never_fts_syntax(self):
        """`C++ (notes)` or a bare `*` must be a SEARCH, not a syntax error
        and not a query meaning something nobody typed."""
        for q in ("C++ (notes)", '"', "*", "fox OR NOT bar", "a AND"):
            with self.subTest(q=q):
                r = self.content.search(q)
                self.assertIsInstance(r["hits"], list)
                self.assertNotIn("syntax", r["error"].lower())

    def test_a_malformed_config_is_an_error_not_a_silent_default(self):
        """A typo must not look identical to having no config at all."""
        Path(os.environ["CORRAL_CONTENT_CONFIG"]).write_text("{ not json")
        roots, err = self.content.roots()
        self.assertEqual(roots, [])
        self.assertIn("unreadable", err)

    def test_status_explains_an_empty_index(self):
        Path(os.environ["CORRAL_CONTENT_CONFIG"]).unlink()
        import importlib
        c = importlib.reload(self.content)
        st = c.status()
        # With no config and (in this scratch HOME) no ~/notes, the empty
        # state must SAY what to do, not just report zero.
        if not st["roots"]:
            self.assertTrue(st["error"], "an empty index with no explanation")


class AttachSemantics(unittest.TestCase):
    """What attaching a note MEANS is decided by the lane, in one place."""

    def test_the_rule_is_stated_where_it_is_enforced(self):
        hub = (ROOT / "hub.py").read_text(encoding="utf-8")
        self.assertIn("/api/content/attach", hub)
        # A lane with tools gets a reference; one without gets an excerpt.
        self.assertIn('"tools"', hub)
        self.assertIn("ATTACH_EXCERPT_CHARS", hub)

    def test_the_excerpt_is_bounded(self):
        import hub
        self.assertLessEqual(hub.ATTACH_EXCERPT_CHARS, 20000)

    def test_every_lane_declares_whether_it_has_tools(self):
        """Undeclared reads as False, which would silently quote a whole note
        into a lane that could have read the file itself."""
        import sessions
        for key, spec in sessions.AGENTS.items():
            self.assertIn("tools", spec,
                          f"{key} does not say whether it can read a file")

    def test_the_browser_never_renders_content_as_markup(self):
        """File-derived snippets reach the page via textContent, never innerHTML."""
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for bad in ("innerHTML = r.snippet", "innerHTML = h.snippet",
                    "innerHTML = d.text", "insertAdjacentHTML"):
            self.assertNotIn(bad, js)
        self.assertFalse((ROOT / "mdview.py").exists(),
                         "a markdown renderer is back; if content is rendered "
                         "again, the P20 escaping argument has to come with it")

    def test_ssh_panes_are_rejected_as_attachment_targets(self):
        """A note excerpt must never become a remote shell command."""
        hub = (ROOT / "hub.py").read_text(encoding="utf-8")
        self.assertIn("SSH panes cannot receive note attachments", hub)

    def test_attachment_targets_exclude_non_composer_panes(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        target = js[js.index("function attachTarget()"):js.index(
            "function paletteResults", js.index("function attachTarget()"))]
        self.assertIn("p.state !== 'detached'", target)
        self.assertIn("!p.agent.startsWith('host:')", target)

    def test_palette_invalidates_before_short_query_return(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = js.index("function paletteResults(query)")
        body = js[start:js.index("function renderPalette", start)]
        self.assertLess(body.index("++PAL.seq"), body.index(
            "if (needle.length < 2) return"))

    def test_attachment_target_is_recomputed_when_activated(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = js.index("async function activatePalette")
        body = js[start:js.index("async function attachContent", start)]
        self.assertIn("const target = newPane ? null : attachTarget()", body)
        self.assertIn("target ? target.id : null", body)


class PlatformHonesty(unittest.TestCase):
    """A lane must not report available on a host that cannot run it."""

    def test_each_pinned_row_names_its_own_platform(self):
        """A row's URL directory and release suffix both match its (system, machine) key."""
        import install_antigravity_acp as m
        self.assertEqual(set(m.RELEASES), {("Linux", "x86_64"),
                                           ("Linux", "arm64"),
                                           ("Darwin", "arm64")})
        suffix = {("Linux", "x86_64"): ("linux", "-linux-x86_64"),
                  ("Linux", "arm64"): ("linux", "-linux-arm64"),
                  ("Darwin", "arm64"): ("macos", "-darwin-arm64")}
        for (system, machine), (folder, tail) in suffix.items():
            row = m.release_for(system, machine)
            self.assertIsNotNone(row, (system, machine))
            self.assertTrue(row["url"].startswith(f"{m.BASE_URL}{folder}/"), row)
            self.assertTrue(row["release"].endswith(tail), row)
            self.assertTrue(row["url"].endswith(f"{row['release']}.zip"), row)
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$", (system, machine))

    def test_each_build_is_started_with_its_own_flags(self):
        """Each platform build is launched with its own flags."""
        import install_antigravity_acp as m
        import antigravity_acp_launcher as la
        mac = m.release_for("Darwin", "arm64")
        linux = m.release_for("Linux", "x86_64")
        self.assertEqual(la.server_argv("/b", row=mac), ["/b"])
        self.assertEqual(la.server_argv("/b", row=linux), ["/b", "--uid="])
        self.assertEqual(m.release_for("Linux", "aarch64")["args"], ["--uid="])
        real = m.platform.system, m.platform.machine
        try:
            m.platform.system, m.platform.machine = (lambda: "Darwin"), (lambda: "arm64")
            self.assertEqual(la.server_argv("/b"), ["/b"])
            m.platform.system, m.platform.machine = (lambda: "Linux"), (lambda: "x86_64")
            self.assertEqual(la.server_argv("/b"), ["/b", "--uid="])
        finally:
            m.platform.system, m.platform.machine = real

    def test_aarch64_and_arm64_are_the_same_platform(self):
        import install_antigravity_acp as m
        for name in ("arm64", "aarch64"):
            self.assertEqual(m.release_for("Linux", name)["release"],
                             m.RELEASES[("Linux", "arm64")]["release"], name)

    def test_install_refuses_on_a_platform_with_no_row(self):
        """A platform with no pinned build (e.g. darwin-x86_64) is refused before
        any download.
        """
        import install_antigravity_acp as m
        real = m.platform.system, m.platform.machine, m.download
        calls = []
        try:
            m.platform.system, m.platform.machine = (lambda: "Darwin"), (lambda: "x86_64")
            m.download = lambda url, out: calls.append(url)
            self.assertIsNone(m.release_for())
            self.assertIsNotNone(m.platform_problem())
            with tempfile.TemporaryDirectory() as root:
                destination = Path(root) / "lib" / "antigravity-acp"
                with self.assertRaises(RuntimeError) as cm:
                    m.install(destination, settings=Path(root) / "settings.json")
                self.assertFalse(destination.parent.exists())
            self.assertIn("Darwin x86_64", str(cm.exception))
            self.assertIn("Darwin arm64", str(cm.exception))   # what IS pinned
            self.assertEqual(calls, [])
        finally:
            m.platform.system, m.platform.machine, m.download = real

    def test_a_mac_on_apple_silicon_resolves(self):
        import install_antigravity_acp as m
        real = m.platform.system, m.platform.machine
        try:
            m.platform.system, m.platform.machine = (lambda: "Darwin"), (lambda: "arm64")
            self.assertIsNone(m.platform_problem())
            self.assertIn("/macos/", m.release_for()["url"])
        finally:
            m.platform.system, m.platform.machine = real

    def test_a_digest_mismatch_installs_nothing(self):
        """A digest mismatch leaves the install directory and its parent untouched."""
        import zipfile
        import install_antigravity_acp as m
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            payload = root / "release.src.zip"
            with zipfile.ZipFile(payload, "w") as zf:
                for name in m.FILES:
                    zf.writestr(name, b"#!/bin/sh\n")
            lib = root / "lib"
            lib.mkdir()
            destination = lib / "antigravity-acp"
            real = m.download, m.release_for
            try:
                m.download = lambda url, out: Path(out).write_bytes(payload.read_bytes())
                m.release_for = lambda *a: {"release": "r", "url": "u", "sha256": "0" * 64}
                with self.assertRaises(RuntimeError) as cm:
                    m.install(destination, settings=root / "settings.json")
            finally:
                m.download, m.release_for = real
            self.assertIn("SHA-256 mismatch", str(cm.exception))
            self.assertEqual(list(lib.iterdir()), [])
            self.assertFalse((root / "settings.json").exists())

    def test_a_short_download_is_named_not_hashed(self):
        """A truncated download is reported as short, not hashed."""
        import io
        import install_antigravity_acp as m

        class Short(io.BytesIO):
            headers = {"Content-Length": "100"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        real = m.urllib.request.urlopen
        try:
            m.urllib.request.urlopen = lambda url, timeout: Short(b"x" * 40)
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaises(RuntimeError) as cm:
                    m.download("u", Path(root) / "a.zip")
            self.assertIn("40 of 100", str(cm.exception))
            m.urllib.request.urlopen = lambda url, timeout: Short(b"x" * 100)
            with tempfile.TemporaryDirectory() as root:
                m.download("u", Path(root) / "a.zip")
                self.assertEqual((Path(root) / "a.zip").stat().st_size, 100)
        finally:
            m.urllib.request.urlopen = real

    def test_short_reads_retry_a_bounded_number_of_times(self):
        """Two short reads then a whole archive installs; DOWNLOAD_ATTEMPTS
        short reads in a row refuse and leave nothing behind."""
        import hashlib
        import zipfile
        import install_antigravity_acp as m
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            payload = root / "release.src.zip"
            with zipfile.ZipFile(payload, "w") as zf:
                for name in m.FILES:
                    zf.writestr(name, b"#!/bin/sh\n")
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            for shorts, ok in ((2, True), (m.DOWNLOAD_ATTEMPTS, False)):
                lib = root / f"lib{shorts}"
                destination = lib / "antigravity-acp"
                calls = []

                def fake(url, out, shorts=shorts, calls=calls):
                    calls.append(url)
                    if len(calls) <= shorts:
                        Path(out).write_bytes(b"part")
                        raise m.ShortDownload("download ended short: 4 of 9 bytes")
                    Path(out).write_bytes(payload.read_bytes())
                real = m.download, m.release_for
                try:
                    m.download = fake
                    m.release_for = lambda *a: {"release": "r", "url": "u", "sha256": digest}
                    if ok:
                        m.install(destination, settings=root / "s.json")
                    else:
                        with self.assertRaises(m.ShortDownload):
                            m.install(destination, settings=root / "s.json")
                finally:
                    m.download, m.release_for = real
                self.assertEqual(m.installed_ok(destination), ok, shorts)
                self.assertEqual(len(calls), min(shorts + 1, m.DOWNLOAD_ATTEMPTS))
                if not ok:
                    self.assertEqual(list(lib.iterdir()), [])

    def test_amd64_and_x86_64_are_the_same_platform(self):
        """Windows/WSL and some BSDs report AMD64; refusing there would be a
        guard that lies in the other direction."""
        import install_antigravity_acp as m
        real_system, real_machine = m.platform.system, m.platform.machine
        try:
            m.platform.system = lambda: "Linux"
            for name in ("x86_64", "amd64", "AMD64"):
                m.platform.machine = (lambda n=name: n)
                self.assertIsNone(m.platform_problem(), name)
        finally:
            m.platform.system, m.platform.machine = real_system, real_machine

    def test_the_picker_asks_the_platform_not_just_the_filesystem(self):
        """Belt and braces: the installer refuses, but a hand copy or a synced
        home directory can still put the files there."""
        sess = (ROOT / "sessions.py").read_text(encoding="utf-8")
        self.assertIn("platform_problem", sess)


class AntigravityInstallsBesideItsDestination(unittest.TestCase):
    """The work dir sits beside the destination: os.replace is atomic only
    within one filesystem (EXDEV across a tmpfs /tmp).
    """

    def test_the_final_rename_stays_in_the_destination_directory(self):
        import hashlib
        import zipfile
        import install_antigravity_acp as m
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            payload = root / "release.src.zip"
            with zipfile.ZipFile(payload, "w") as zf:
                for name in m.FILES:
                    zf.writestr(name, b"#!/bin/sh\n")
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            destination = root / "lib" / "antigravity-acp"
            renames = []
            real = (m.download, m.release_for, m.os.replace)
            try:
                m.download = lambda url, out: Path(out).write_bytes(payload.read_bytes())
                m.release_for = lambda *a: {"release": "r", "url": "u", "sha256": digest}

                def replace(src, dst):
                    renames.append((Path(src), Path(dst)))
                    return real[2](src, dst)
                m.os.replace = replace
                m.install(destination, settings=root / "settings.json")
            finally:
                m.download, m.release_for, m.os.replace = real
            self.assertTrue(m.installed_ok(destination))
            src, dst = next(r for r in renames if r[1] == destination)
            self.assertEqual(src.parent.parent, destination.parent)
            self.assertEqual([p.name for p in destination.parent.iterdir()],
                             [destination.name])

    def test_no_work_dir_is_pinned_to_tmp(self):
        src = (ROOT / "install_antigravity_acp.py").read_text(encoding="utf-8")
        self.assertNotIn('dir="/tmp"', src)


class AntigravitySignInIsSelected(unittest.TestCase):
    """The installer selects the operator's Google login; never an API key,
    and never overriding a choice already made.
    """

    def test_install_selects_the_google_login_where_nothing_is_set(self):
        import install_antigravity_acp as m
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / "antigravity-acp" / "settings.json"
            self.assertIsNotNone(m.auth_problem(settings))
            m.select_auth(settings)
            self.assertEqual(m.auth_type(settings), "oauth-personal")
            self.assertIsNone(m.auth_problem(settings))

    def test_a_choice_already_made_is_left_alone(self):
        import install_antigravity_acp as m
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / "settings.json"
            body = '{"auth": {"type": "gemini-api-key"}, "other": 1}\n'
            settings.write_text(body, encoding="utf-8")
            m.select_auth(settings)
            self.assertEqual(settings.read_text(encoding="utf-8"), body)

    def test_other_settings_survive_the_selection(self):
        import install_antigravity_acp as m
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / "settings.json"
            settings.write_text('{"theme": "dark", "auth": {"x": 1}}', encoding="utf-8")
            m.select_auth(settings)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data, {"theme": "dark", "auth": {"x": 1, "type": "oauth-personal"}})

    def test_a_file_that_is_not_ours_to_rewrite_is_refused(self):
        import install_antigravity_acp as m
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / "settings.json"
            settings.write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                m.select_auth(settings)
            self.assertEqual(settings.read_text(encoding="utf-8"), "[1, 2]")

    def test_the_picker_reports_no_sign_in_rather_than_ok(self):
        import sessions
        import install_antigravity_acp as m
        if m.platform_problem() or not m.installed_ok():
            self.skipTest("the pinned release is not installed on this host")
        real = m.SETTINGS
        with tempfile.TemporaryDirectory() as root:
            try:
                m.SETTINGS = Path(root) / "settings.json"
                gemini = [a for a in sessions.available_agents() if a["key"] == "gemini"]
            finally:
                m.SETTINGS = real
        self.assertFalse(gemini[0]["available"])
        self.assertIn("no sign-in method selected", gemini[0]["why"])


class PrintedCommandsWork(unittest.TestCase):
    """A command shown to a human must work as pasted."""

    def test_the_codex_login_command_creates_its_own_home(self):
        import codex_launcher
        cmd = codex_launcher.login_command()
        self.assertIn("mkdir -p", cmd)
        self.assertIn("login --device-auth", cmd)
        # The mkdir must come FIRST — after the login it is decoration.
        self.assertLess(cmd.index("mkdir -p"), cmd.index("login --device-auth"))
        self.assertIn(str(codex_launcher.CODEX_HOME), cmd)

    def test_the_unavailable_reason_carries_that_command(self):
        """The reason string is what the picker and `doctor` actually show."""
        import codex_launcher
        real = codex_launcher.auth_present
        try:
            codex_launcher.auth_present = lambda: False
            reason = codex_launcher.unavailable_reason()
        finally:
            codex_launcher.auth_present = real
        if reason and "not logged in" in reason:
            self.assertIn("mkdir -p", reason)


class LanesRefuseAtPickTime(unittest.TestCase):
    """A lane that is installed but cannot authenticate is refused at pick time."""

    CREDENTIALED = ("codex", "grok")     # lanes gated on a vendor login

    def test_each_credentialed_lane_exposes_a_real_probe(self):
        import importlib
        for lane in self.CREDENTIALED:
            with self.subTest(lane=lane):
                mod = importlib.import_module(f"{lane}_launcher")
                self.assertTrue(hasattr(mod, "unavailable_reason"),
                                f"{lane} has no unavailable_reason()")
                self.assertTrue(hasattr(mod, "auth_present"),
                                f"{lane} cannot tell whether it is logged in")

    def test_the_picker_asks_that_probe_not_just_the_filesystem(self):
        sess = (ROOT / "sessions.py").read_text(encoding="utf-8")
        for lane in self.CREDENTIALED:
            self.assertIn(f"from {lane}_launcher import unavailable_reason", sess,
                          f"the {lane} lane is judged without its auth probe")

    def test_a_missing_login_names_the_command_that_fixes_it(self):
        """'not logged in' with no remedy is a dead end, not a diagnosis."""
        import grok_launcher
        real = grok_launcher.auth_present
        try:
            grok_launcher.auth_present = lambda: False
            reason = grok_launcher.unavailable_reason()
        finally:
            grok_launcher.auth_present = real
        if reason and "not logged in" in reason:
            self.assertIn("login", reason)


class DialogIsUsableOnDayOne(unittest.TestCase):
    """The new-conversation dialog must work before any session has run."""

    def test_the_claude_lane_is_probed_live_not_guessed_at(self):
        """On macOS Claude keeps its secret in the Keychain, so the lane is probed
        with a live handshake rather than a credential-file check.
        """
        import sessions
        spec = sessions.AGENTS["claude"]
        self.assertTrue(spec.get("live_probe"))
        self.assertTrue(spec.get("catalog_probe"))

    def test_the_probe_answers_both_questions_from_one_handshake(self):
        import lane_probe, inspect
        src = inspect.getsource(lane_probe._handshake)
        self.assertIn("new_session_full", src,
                      "session/new is where auth surfaces AND where the model "
                      "catalog comes from; a probe that stops at initialize "
                      "answers neither")
        self.assertIn("client.close", src, "a probe must not leak a process")

    def test_the_probe_is_cached(self):
        """Otherwise the picker spawns a subprocess per render."""
        import lane_probe
        self.assertGreaterEqual(lane_probe.CACHE_S, 30)

    def test_directory_suggestions_are_real_and_bounded(self):
        import sessions
        s = sessions.cwd_suggestions(["/tmp"])
        self.assertLessEqual(len(s), sessions.MAX_CWD_SUGGESTIONS)
        self.assertEqual(len(s), len(set(s)), "duplicate suggestions")
        for d in s:
            self.assertTrue(Path(d).is_dir(),
                            f"suggested {d}, which is not a directory — the "
                            f"picker lying in miniature")

    def test_a_nonexistent_recent_cwd_is_not_suggested(self):
        import sessions
        s = sessions.cwd_suggestions(["/nope/not/here"])
        self.assertNotIn("/nope/not/here", s)

    def test_the_directory_field_stays_free_text(self):
        """A datalist, never a <select>. Any path on the host is valid; a
        dropdown would turn a helpful list into the only allowed answers."""
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="f-cwd"', html)
        self.assertIn('list="cwdlist"', html)
        self.assertIn('<datalist id="cwdlist">', html)
        self.assertNotIn('<select id="f-cwd"', html)


class QuietOnlyWhereItIsNotAnError(unittest.TestCase):
    """Silence only the "peer left" connection errors; every other exception
    still logs.
    """

    def _handle(self, exc):
        """Push one exception through the server's handle_error hook."""
        import io, sys, hub
        srv = hub.Server.__new__(hub.Server)
        buf, real = io.StringIO(), sys.stderr
        sys.stderr = buf
        try:
            try:
                raise exc
            except type(exc):
                srv.handle_error(None, ("127.0.0.1", 1))
        finally:
            sys.stderr = real
        return buf.getvalue()

    def test_peer_left_exceptions_are_silent(self):
        for exc in (ConnectionResetError(54, "Connection reset by peer"),
                    BrokenPipeError(32, "Broken pipe"),
                    ConnectionAbortedError(53, "Software caused abort"),
                    TimeoutError("timed out")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(self._handle(exc), "")

    def test_a_real_error_is_still_loud(self):
        out = self._handle(RuntimeError("a real bug in a handler"))
        self.assertIn("a real bug in a handler", out)

    def test_the_quiet_list_is_only_connection_errors(self):
        """Adding, say, OSError here would swallow a full disk."""
        import hub
        for exc_type in hub.Server._QUIET:
            self.assertTrue(
                issubclass(exc_type, (ConnectionError, TimeoutError)),
                f"{exc_type.__name__} is not a 'the peer left' exception")


class PrivateConfigDirCannotBreakTheLane(unittest.TestCase):
    """The private config dir that imposes posture must not cost the pane its login."""

    def _fake_home(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        (home / ".claude").mkdir()
        return home

    def test_no_credential_file_means_no_private_dir(self):
        """Refuse the dir rather than hand back one that cannot authenticate."""
        import sessions
        home = self._fake_home()                     # ~/.claude, no credentials
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            self.assertIsNone(sessions.seed_config_dir(home / "cfg", "auto"))
        finally:
            Path.home = real

    def test_a_none_config_dir_means_inherit_not_crash(self):
        import sessions
        env = sessions.spawn_env(sessions.AGENTS["claude"], None)
        self.assertNotIn("CLAUDE_CONFIG_DIR", env,
                         "a pane that cannot have a private config dir must "
                         "run under the user's own, not under a broken one")

    def test_posture_is_not_claimed_when_it_cannot_be_imposed(self):
        """The pane must wear `agent-set`, not a posture nobody established."""
        import sessions
        home = self._fake_home()
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            self.assertFalse(sessions.posture_enforceable(
                _config_dir_only(sessions.AGENTS["claude"])))
        finally:
            Path.home = real

    def test_a_credential_file_still_gets_a_private_dir(self):
        """A real credential file still gets a private dir; this is a fallback,
        not a replacement.
        """
        import sessions
        _pin_sessions_platform(self, "linux")
        home = self._fake_home()
        (home / ".claude" / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "a" * 108,
                                          "refreshToken": "r" * 108}}))
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            d = sessions.seed_config_dir(home / "cfg", "auto")
            self.assertIsNotNone(d)
            self.assertTrue((Path(d) / ".credentials.json").is_file())
            self.assertTrue((Path(d) / "settings.json").is_file())
            self.assertTrue(
                sessions.posture_enforceable(sessions.AGENTS["claude"]))
        finally:
            Path.home = real

    def test_the_probe_runs_the_way_a_pane_runs(self):
        """A probe that does not reproduce the pane's environment is a second,
        easier question that happens to have a nicer answer."""
        import lane_probe, inspect
        src = inspect.getsource(lane_probe.sessions_env)
        self.assertIn("seed_config_dir", src)
        self.assertNotIn("spawn_env(spec, None)", src)


class AmbientVendorKeysCannotHijackALane(unittest.TestCase):
    """Ambient vendor API keys are stripped so the agent uses the verified
    login, not API-key mode.
    """

    def test_stripped_vars_do_not_reach_the_child_process(self):
        """Measured at the process boundary, not asserted about a dict."""
        import acp, sessions, tempfile, sys as _sys, time
        spy = Path(tmpdir(self)) / "spy.py"
        spy.write_text(
            "import json,os,sys\n"
            "sys.stderr.write(json.dumps(sorted(k for k in os.environ "
            "if k.startswith(('ANTHROPIC_','OPENAI_'))))+chr(10))\n"
            "sys.stderr.flush()\n")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        try:
            c = acp.AcpClient([_sys.executable, str(spy)], "/tmp", env={},
                              strip_env=sessions.STRIP_ENV_PREFIXES)
            time.sleep(1.0)
            leaked = "".join(c.stderr_tail)
            c.close()
            self.assertIn("[]", leaked, f"a credential leaked: {leaked}")
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_both_doors_strip_for_every_lane(self):
        """start() and resume() both strip vendor keys, measured at the process
        boundary for every lane.
        """
        import sessions, tempfile, sys as _sys, time, json as _json
        work = Path(tmpdir(self))
        spy = work / "spy.py"
        spy.write_text(
            "import json,os,sys\n"
            "seen=sorted(k for k in os.environ if k.startswith(%r))\n"
            "open(sys.argv[1],'w').write(json.dumps("
            "{'seen':seen,'cfg':os.environ.get('CLAUDE_CONFIG_DIR')}))\n"
            % (sessions.STRIP_ENV_PREFIXES,))
        canaries = {"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_API_KEY": "sk-x",
                    "GOOGLE_APPLICATION_CREDENTIALS": "/parent/sa.json",
                    "XAI_API_KEY": "x", "CLAUDECODE": "1",
                    "CLAUDE_CONFIG_DIR": "/parent/config"}
        saved = {k: os.environ.get(k) for k in canaries}
        roster = dict(sessions.AGENTS)
        os.environ.update(canaries)
        panes = []

        class _Mgr:
            def broadcast(self, ev): pass
            def remember_catalog(self, agent, config): pass
            def _reserve_live(self, pane): pass      # the live-cap gate; not under test here

        def _report(path):
            for _ in range(200):
                if path.is_file() and path.stat().st_size:
                    return _json.loads(path.read_text())
                time.sleep(0.05)
            self.fail(f"spy never wrote {path}")
        try:
            for key, spec in roster.items():
                for door in ("start", "resume"):
                    with self.subTest(lane=key, door=door):
                        out = work / f"{key}.{door}.json"
                        sessions.AGENTS[key] = dict(
                            spec, argv=[_sys.executable, str(spy), str(out)])
                        p = sessions.Pane(key, str(work), "auto", _Mgr())
                        p._config_dir = lambda: "/ours/config"
                        panes.append(p)
                        if door == "start":
                            p.start()
                        else:
                            p.state, p.acp_session = "detached", "s-1"
                            p.resume()
                        r = _report(out)
                        leaked = [k for k in r["seen"] if k != "CLAUDE_CONFIG_DIR"]
                        self.assertEqual(leaked, [], f"{key}.{door}() leaked {leaked}")
                        if spec.get("posture_via_config_dir"):
                            self.assertEqual(r["cfg"], "/ours/config",
                                             "our config dir must win, not the parent's")
                        else:
                            self.assertIsNone(r["cfg"], "parent CLAUDE_CONFIG_DIR leaked")
        finally:
            sessions.AGENTS.clear(); sessions.AGENTS.update(roster)
            for k, v in saved.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
            for p in panes:
                try:
                    if p.client: p.client.close()
                except Exception: pass
                if getattr(p, "_log", None): p._log.close()

    def test_the_probe_strips_too(self):
        lp = (ROOT / "lane_probe.py").read_text(encoding="utf-8")
        self.assertIn("strip_env=", lp,
                      "a probe running under different credentials than a "
                      "pane is not evidence about the pane")

    def test_stripping_is_announced_not_silent(self):
        """Someone deliberately using an API key deserves to learn we removed
        it, not to debug why their key is ignored."""
        import sessions
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        try:
            self.assertIn("ANTHROPIC_API_KEY", sessions.vendor_env_present())
            note = next((a.get("envNote") for a in sessions.available_agents()
                         if a.get("envNote")), "")
            self.assertIn("ANTHROPIC_API_KEY", note)
            self.assertIn("CORRAL_LIGHT_ALLOW_VENDOR_ENV", note)
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_there_is_an_opt_in_escape_hatch(self):
        """Fail safe, not fail closed-forever: API-key auth is legitimate."""
        import sessions
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        os.environ["CORRAL_LIGHT_ALLOW_VENDOR_ENV"] = "1"
        try:
            self.assertEqual(sessions.strip_prefixes(), ())
            self.assertEqual(sessions.vendor_env_present(), [])
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("CORRAL_LIGHT_ALLOW_VENDOR_ENV", None)

    def test_google_adc_and_claude_api_key_are_stripped(self):
        """GOOGLE_API does not match GOOGLE_APPLICATION_CREDENTIALS.
        CLAUDE_CONFIG_DIR does not match CLAUDE_API_KEY."""
        import sessions
        prefixes = sessions.strip_prefixes()
        for var in ("GOOGLE_APPLICATION_CREDENTIALS", "CLAUDE_API_KEY"):
            with self.subTest(var=var):
                self.assertTrue(var.startswith(prefixes),
                                f"{var} would leak into a pane")
        saved = {k: os.environ.get(k) for k in
                 ("GOOGLE_APPLICATION_CREDENTIALS", "CLAUDE_API_KEY")}
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/sa.json"
        os.environ["CLAUDE_API_KEY"] = "sk-ant-test"
        try:
            nag = sessions.vendor_env_present()
            self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", nag)
            self.assertIn("CLAUDE_API_KEY", nag)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class NoParentSessionLeaksIntoAPane(unittest.TestCase):
    """A pane must not inherit a parent Claude Code session's CLAUDE_* variables,
    CLAUDE_CONFIG_DIR above all.
    """

    PARENT_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
                   "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION",
                   "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_PID", "CLAUDE_EFFORT",
                   "CLAUDE_CONFIG_DIR")

    def test_every_parent_session_var_is_stripped(self):
        import sessions
        for var in self.PARENT_VARS:
            with self.subTest(var=var):
                self.assertTrue(var.startswith(sessions.STRIP_ENV_PREFIXES),
                                f"{var} would leak into a pane")

    def test_our_own_config_dir_still_wins_after_stripping(self):
        """The strip must not defeat the mechanism it protects: overrides are
        applied AFTER, so setting CLAUDE_CONFIG_DIR deliberately still works."""
        import sessions
        env = sessions.spawn_env(sessions.AGENTS["claude"], "/tmp/some-config")
        self.assertEqual(env.get("CLAUDE_CONFIG_DIR"), "/tmp/some-config")

    def test_the_note_does_not_nag_about_session_vars(self):
        """Only credentials are worth a picker note. Warning about variables
        nobody exported on purpose trains the eye to skip the line."""
        import sessions
        os.environ["CLAUDECODE"] = "1"
        try:
            self.assertNotIn("CLAUDECODE", sessions.vendor_env_present())
        finally:
            os.environ.pop("CLAUDECODE", None)

    def test_session_identity_vars_are_not_called_credentials(self):
        """GROK_AGENT / GROK_SESSION_ID are session identity, not a vendor API key."""
        import sessions
        saved = {k: os.environ.get(k) for k in ("GROK_AGENT", "GROK_SESSION_ID")}
        os.environ["GROK_AGENT"] = "grok"
        os.environ["GROK_SESSION_ID"] = "sess"
        try:
            got = sessions.vendor_env_present()
            self.assertNotIn("GROK_AGENT", got)
            self.assertNotIn("GROK_SESSION_ID", got)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class DiagnoseIsSafeToPaste(unittest.TestCase):
    """`diagnose` runs a real turn and reports what doctor cannot."""

    def test_it_prompts_which_is_the_step_doctor_skips(self):
        import diagnose, inspect
        src = inspect.getsource(diagnose.diagnose)
        self.assertIn("client.prompt", src,
                      "doctor already answers 'can it start'; this command "
                      "exists to answer 'does a turn run'")

    def test_it_surfaces_adapter_stderr(self):
        """The adapter's stderr tail is surfaced, not discarded."""
        import diagnose, inspect
        self.assertIn("stderr_tail", inspect.getsource(diagnose.diagnose))

    def test_it_runs_a_positive_control(self):
        """The control re-runs without the private config dir, the only thing
        Corral adds to a working terminal.
        """
        import diagnose, inspect
        src = inspect.getsource(diagnose._control)
        self.assertIn("_run_once(spec, cwd, None", src,
                      "the control must run WITHOUT the private config dir")

    def test_the_credential_shape_reports_lengths_not_values(self):
        """Token lengths are reported; values never are."""
        import diagnose, tempfile, io, contextlib
        d = Path(tmpdir(self)) / "c.json"
        d.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "SUPERSECRETVALUE" * 4, "expiresAt": 123}}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            diagnose._credential_shape(d)
        out = buf.getvalue()
        self.assertNotIn("SUPERSECRETVALUE", out, "a token value was printed")
        self.assertIn("accessToken=<64 chars>", out)

    def test_a_missing_token_field_is_called_out(self):
        import diagnose, tempfile, io, contextlib
        d = Path(tmpdir(self)) / "c.json"
        d.write_text(json.dumps({"claudeAiOauth": {"expiresAt": 1}}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            diagnose._credential_shape(d)
        out = buf.getvalue()
        self.assertIn("MISSING", out)
        self.assertIn("accessToken", out)

    def test_it_never_prints_a_secret_value(self):
        import diagnose, inspect
        src = inspect.getsource(diagnose)
        self.assertIn("value not shown", src)
        # Names and lengths only — no dict dump of the environment anywhere.
        self.assertNotIn("json.dumps(dict(os.environ", src)
        self.assertNotIn("print(os.environ", src)


class AnEmptyTokenIsNotACredential(unittest.TestCase):
    """A credential file with empty tokens is not a usable credential."""

    def _cred(self, payload):
        d = Path(tmpdir(self)) / ".credentials.json"
        d.write_text(json.dumps(payload))
        return d

    def test_empty_tokens_are_not_usable(self):
        import sessions
        self.assertFalse(sessions.usable_credential(self._cred(
            {"claudeAiOauth": {"accessToken": "", "refreshToken": "",
                               "expiresAt": 0, "subscriptionType": "max"}})))

    def test_a_real_token_is_usable(self):
        import sessions
        self.assertTrue(sessions.usable_credential(self._cred(
            {"claudeAiOauth": {"accessToken": "x" * 108,
                               "refreshToken": "y" * 108}})))
        # Either one alone is enough — a refresh token can mint an access one.
        self.assertTrue(sessions.usable_credential(self._cred(
            {"claudeAiOauth": {"refreshToken": "y" * 108}})))

    def test_a_metadata_only_stub_is_not_usable(self):
        """macOS shape: the secret is in the Keychain, the file is metadata."""
        import sessions
        self.assertFalse(sessions.usable_credential(self._cred(
            {"claudeAiOauth": {"expiresAt": 1, "scopes": ["a"],
                               "subscriptionType": "max"}})))

    def test_missing_or_unparseable_is_not_usable(self):
        import sessions
        self.assertFalse(sessions.usable_credential(Path("/nope/none.json")))
        bad = Path(tmpdir(self)) / "c.json"
        bad.write_text("{ not json")
        self.assertFalse(sessions.usable_credential(bad))

    def test_token_names_are_matched_at_any_depth(self):
        """Token keys are matched at any depth; the vendor owns the nesting."""
        import sessions
        self.assertTrue(sessions.usable_credential(self._cred(
            {"a": {"b": {"c": {"access_token": "z" * 40}}}})))

    def test_an_unusable_credential_means_no_private_config_dir(self):
        """The whole point: refuse the directory rather than build one that
        only looks credentialed."""
        import sessions
        home = Path(tmpdir(self))
        (home / ".claude").mkdir()
        (home / ".claude" / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": ""}}))
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            self.assertIsNone(sessions.seed_config_dir(home / "cfg", "auto"))
            self.assertFalse(sessions.posture_enforceable(
                _config_dir_only(sessions.AGENTS["claude"])))
        finally:
            Path.home = real


class TheCopiedCredentialResyncs(unittest.TestCase):
    """The private config dir's credential tracks the source instead of
    freezing at first copy (OAuth tokens rotate).
    """

    def _home_with_cred(self, token="a"):
        home = Path(tmpdir(self))
        (home / ".claude").mkdir()
        cred = home / ".claude" / ".credentials.json"
        cred.write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": token * 108,
                               "refreshToken": token * 108}}))
        return home, cred

    def test_a_rotated_token_is_picked_up_on_the_next_seed(self):
        import sessions, time
        _pin_sessions_platform(self, "linux")
        home, cred = self._home_with_cred("a")
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            d = sessions.seed_config_dir(home / "cfg", "auto")
            self.assertIn("a" * 20, (d / ".credentials.json").read_text())

            time.sleep(1.1)   # a distinguishable mtime, like copy2 relies on
            cred.write_text(json.dumps(
                {"claudeAiOauth": {"accessToken": "b" * 108,
                                   "refreshToken": "b" * 108}}))
            d2 = sessions.seed_config_dir(home / "cfg", "auto")
            self.assertIn("b" * 20, (d2 / ".credentials.json").read_text(),
                          "the copy did not resync after the source rotated")
        finally:
            Path.home = real

    def test_the_pane_shares_the_one_login_file(self):
        """The pane's credential is a link to the one login file, so there is
        nothing per-pane to go stale; re-seeding keeps it a link.
        """
        import sessions
        _pin_sessions_platform(self, "linux")
        home, cred = self._home_with_cred("a")
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            d = sessions.seed_config_dir(home / "cfg", "auto")
            dst = d / ".credentials.json"
            self.assertTrue(dst.is_symlink(), "the pane got a copy, not a link")
            self.assertEqual(os.readlink(dst), str(cred))
            sessions.seed_config_dir(home / "cfg", "auto")   # called again
            self.assertTrue(dst.is_symlink())
            # a refresh the PANE makes lands in the one file everyone reads
            dst.write_text(json.dumps({"claudeAiOauth": {"accessToken": "c" * 108}}))
            self.assertIn("c" * 20, cred.read_text())
        finally:
            Path.home = real

    def test_a_legacy_copy_is_replaced_by_the_link(self):
        """A legacy credential COPY is replaced by the link on the next seed."""
        import sessions
        _pin_sessions_platform(self, "linux")
        home, cred = self._home_with_cred("b")
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            (home / "cfg").mkdir()
            old = home / "cfg" / ".credentials.json"
            old.write_text(json.dumps({"claudeAiOauth": {"refreshToken": "a" * 108}}))
            d = sessions.seed_config_dir(home / "cfg", "auto")
            self.assertTrue((d / ".credentials.json").is_symlink())
            self.assertIn("b" * 20, (d / ".credentials.json").read_text())
        finally:
            Path.home = real

    def test_a_transient_bad_read_does_not_discard_a_working_copy(self):
        """If the source is mid-write when we happen to look, keep the
        already-validated copy rather than treating a bad snapshot as ground
        truth and returning None."""
        import sessions, time
        _pin_sessions_platform(self, "linux")
        home, cred = self._home_with_cred("a")
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            d = sessions.seed_config_dir(home / "cfg", "auto")
            self.assertIsNotNone(d)
            time.sleep(1.1)
            cred.write_text("{ mid-write, not valid json")   # newer, broken
            d2 = sessions.seed_config_dir(home / "cfg", "auto")
            self.assertIsNotNone(d2, "a transient bad source read bricked "
                                     "an already-working pane")
            self.assertTrue((d2 / ".credentials.json").is_symlink(),
                            "a transient bad read dropped the link")
        finally:
            Path.home = real


class TheStaleCopyTheoryWasWrong(unittest.TestCase):
    """The private config dir is locked to its owner and audited."""

    def test_the_config_dir_is_locked_to_the_owner(self):
        """The config dir is 0700: some CLIs refuse a token in a loosely
        permissioned directory.
        """
        import sessions
        _pin_sessions_platform(self, "linux")
        home = Path(tmpdir(self))
        (home / ".claude").mkdir()
        (home / ".claude" / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "a" * 108}}))
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            d = sessions.seed_config_dir(home / "cfg", "auto")
            self.assertEqual(oct(d.stat().st_mode & 0o777), "0o700")
        finally:
            Path.home = real

    def test_locking_the_dir_does_not_disturb_an_existing_wider_one(self):
        """chmod must not raise if it cannot apply — a dir on a filesystem
        that ignores POSIX modes (some network mounts) must not brick a
        pane over a permission bit nobody can set anyway."""
        import sessions
        _pin_sessions_platform(self, "linux")
        home = Path(tmpdir(self))
        (home / ".claude").mkdir()
        (home / ".claude" / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "a" * 108}}))
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            d = sessions.seed_config_dir(home / "cfg", "auto")
            d.chmod(0o777)                          # simulate a loose dir
            d2 = sessions.seed_config_dir(home / "cfg", "auto")  # called again
            self.assertEqual(oct(d2.stat().st_mode & 0o777), "0o700",
                             "a call on an already-existing loose dir must "
                             "still tighten it")
        finally:
            Path.home = real


class DiagnoseAuditsPermissionsAndContent(unittest.TestCase):
    """diagnose audits permissions and content equality of the credential."""

    def test_it_compares_real_and_private_permissions(self):
        import diagnose, inspect
        src = inspect.getsource(diagnose)
        self.assertIn("_permission_audit", src)
        self.assertIn("_content_equality", src)

    def test_content_equality_never_prints_the_hash_or_the_bytes(self):
        import diagnose, tempfile as tf, io, contextlib
        a = Path(tmpdir(self)) / "a.json"; a.write_text("secret-value-a")
        b = Path(tmpdir(self)) / "b.json"; b.write_text("secret-value-a")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            diagnose._content_equality(a, b)
        out = buf.getvalue()
        self.assertIn("yes", out)
        self.assertNotIn("secret-value-a", out)

    def test_content_equality_detects_a_real_difference(self):
        import diagnose, tempfile as tf, io, contextlib
        a = Path(tmpdir(self)) / "a.json"; a.write_text("one")
        b = Path(tmpdir(self)) / "b.json"; b.write_text("two")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            diagnose._content_equality(a, b)
        self.assertIn("DIFFERS", buf.getvalue())


class DarwinKeychainMakesIsolationImpossible(unittest.TestCase):
    """On darwin, setting CLAUDE_CONFIG_DIR to any value switches Claude Code's
    Keychain service name to one no `claude login` provisioned, so isolation via
    that dir cannot authenticate.
    """

    def _patch_darwin(self):
        import sessions
        real = sessions.sys.platform
        sessions.sys.platform = "darwin"
        self.addCleanup(lambda: setattr(sessions.sys, "platform", real))

    def _fake_home_with_real_credential(self):
        home = Path(tmpdir(self))
        (home / ".claude").mkdir()
        (home / ".claude" / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "a" * 108,
                               "refreshToken": "b" * 108,
                               "expiresAt": 9999999999999}}))
        real_home = Path.home
        Path.home = staticmethod(lambda: home)
        self.addCleanup(lambda: setattr(Path, "home", real_home))
        return home

    def test_darwin_refuses_isolation_even_with_a_real_credential(self):
        """The whole point: a perfectly valid, non-empty, freshly-copyable
        token must NOT be enough on this platform."""
        import sessions
        self._patch_darwin()
        self._fake_home_with_real_credential()
        self.assertIsNone(sessions.seed_config_dir(
            Path(tmpdir(self)) / "cfg", "auto"))
        self.assertFalse(sessions.posture_enforceable(
            _config_dir_only(sessions.AGENTS["claude"])))
        # ...yet the lane still has a posture here, imposed over ACP.
        self.assertTrue(
            sessions.posture_enforceable(sessions.AGENTS["claude"]))

    def test_linux_is_unaffected(self):
        """The fix must be scoped to the platform that actually has this
        Keychain quirk — not a blanket new restriction everywhere."""
        import sessions
        _pin_sessions_platform(self, "linux")
        self._fake_home_with_real_credential()
        self.assertIsNotNone(sessions.seed_config_dir(
            Path(tmpdir(self)) / "cfg", "auto"))
        self.assertTrue(
            sessions.posture_enforceable(sessions.AGENTS["claude"]))

    def test_diagnose_explains_the_mechanism_not_just_the_verdict(self):
        import diagnose, io, contextlib
        self._patch_darwin()
        self._fake_home_with_real_credential()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                diagnose.diagnose("claude", cwd="/tmp")
            except Exception:
                pass  # the handshake fails in this sandbox; only config is under test
        out = buf.getvalue()
        self.assertIn("Keychain", out)
        self.assertIn("CLAUDE_CONFIG_DIR", out)


class EveryUiCallHasADefinition(unittest.TestCase):
    """Every bare call in app.js has a definition (`node --check` only parses).

    String and template literals are deliberately not stripped: false positives
    are cheap to allowlist, false negatives from over-eager stripping are silent.
    """

    # JS builtins/globals this scan does not otherwise track.
    KNOWN_GLOBALS = {
        "if", "for", "while", "switch", "catch", "function", "return",
        "typeof", "new", "document", "window", "location", "console",
        "fetch", "JSON", "Math", "Date", "Array", "Object", "Promise",
        "Set", "Map", "String", "Number", "Boolean", "requestAnimationFrame",
        "setTimeout", "setInterval", "clearInterval", "clearTimeout",
        "navigator", "localStorage", "URL", "Event", "EventSource",
        "AbortController", "structuredClone", "Intl", "RegExp",
        "encodeURIComponent", "decodeURIComponent", "parseInt", "parseFloat",
        "isNaN", "globalThis", "Symbol", "self", "alert", "confirm",
        "prompt", "atob", "btoa", "async",
    }
    # Each confirmed by hand to be "word(" inside a string, not an undefined
    # call.
    KNOWN_LOCAL_FALSE_POSITIVES = {"approval", "close", "earlier", "match",
                                   "minimize",
                                   # seenPanes' box lookup parameter and
                                   # askPreamble's Promise executor argument,
                                   # both checked by hand.
                                   "rectOf", "resolve"}

    def test_every_bare_call_has_a_matching_definition(self):
        import re
        src = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        # Strip only block and full-line comments; string content stays in (see
        # the class docstring).
        src = re.sub(r"/\*[\s\S]*?\*/", "", src)
        src = re.sub(r"(?<!:)//.*", "", src)   # skip `://` inside URL strings

        defined = set(re.findall(r"\bfunction (\w+)", src))
        defined |= set(re.findall(
            r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", src))
        defined |= set(re.findall(
            r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function", src))
        # Single bare-param arrows: `const name = x => {`.
        defined |= set(re.findall(
            r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?"
            r"[a-zA-Z_$][\w$]*\s*=>", src))
        known = defined | self.KNOWN_GLOBALS | self.KNOWN_LOCAL_FALSE_POSITIVES

        # Bare calls only: NOT preceded by `.` (a method call on some
        # object, which this file does not define and should not have to),
        # and not the `function name(` at a definition site itself.
        bare = []
        for m in re.finditer(r"(?<![.\w$])([a-zA-Z_$][\w$]*)\s*\(", src):
            before = src[max(0, m.start() - 12):m.start()]
            if re.search(r"function\s*$", before):
                continue
            bare.append(m.group(1))

        missing = sorted({name for name in bare
                          if name not in known
                          and not name[0].isupper()   # constructors: too noisy
                          and len(name) > 2})
        self.assertEqual(missing, [],
                         f"app.js calls these as functions with no visible "
                         f"definition: {missing} — either define them or "
                         f"add them to KNOWN_LOCAL_FALSE_POSITIVES with a "
                         f"reason (confirmed by hand, not assumed — see the "
                         f"class docstring for why), the way setMin's "
                         f"absence should have been caught before a click "
                         f"found it")


class PermissionDigestIsConsent(unittest.TestCase):
    """An approval must carry the digest of the payload that was on screen."""

    def _pane(self, digest="deadbeef", oversize=False):
        import sessions
        p = sessions.Pane.__new__(sessions.Pane)
        p.pending = {
            "r1": {
                "requestId": "r1",
                "options": [
                    {"optionId": "allow_once", "kind": "allow_once"},
                    {"optionId": "reject_once", "kind": "reject_once"},
                ],
                "_gate": {"digest": digest, "oversize": oversize, "bytes": 12},
            }
        }
        p.state = "needs-you"
        p.client = type("C", (), {
            "answer_permission": staticmethod(lambda rid, oid: True),
        })()
        p.emit = lambda *a, **k: None
        import threading
        p._lock = threading.Lock()
        return p

    def test_a_second_answer_is_refused_not_raced(self):
        """The pending record is popped before waking the agent, so two answers
        cannot race.
        """
        import threading, time
        p = self._pane()
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow(rid, oid):
            calls.append(oid)
            started.set()
            release.wait(1)
            return True

        p.client.answer_permission = slow
        err = []

        def other():
            started.wait(1)
            try:
                p.answer("r1", "reject_once", digest="deadbeef")
            except ValueError as e:
                err.append(str(e))

        t = threading.Thread(target=other)
        t.start()
        ok = p.answer("r1", "allow_once", digest="deadbeef")
        release.set()
        t.join(1)
        self.assertTrue(ok)
        self.assertEqual(calls, ["allow_once"])
        self.assertTrue(err, "the second click must be refused, not delivered")
        self.assertNotIn("r1", p.pending)

    def test_a_grant_without_the_digest_is_refused(self):
        p = self._pane()
        with self.assertRaises(ValueError):
            p.answer("r1", "allow_once")
        self.assertIn("r1", p.pending)

    def test_a_wrong_digest_is_refused(self):
        p = self._pane()
        with self.assertRaises(ValueError):
            p.answer("r1", "allow_once", digest="0000")
        self.assertIn("r1", p.pending)

    def test_the_matching_digest_grants(self):
        p = self._pane()
        self.assertTrue(p.answer("r1", "allow_once", digest="deadbeef"))
        self.assertNotIn("r1", p.pending)

    def test_oversize_still_cannot_be_granted_even_with_the_digest(self):
        p = self._pane(oversize=True)
        with self.assertRaises(ValueError):
            p.answer("r1", "allow_once", digest="deadbeef")
        self.assertIn("r1", p.pending)

    def test_the_browser_posts_the_digest(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("digest: d.digest", js)
        self.assertGreaterEqual(js.count("digest: d.digest"), 2,
                                "card click and composer 1-9/Esc must both send it")

    def test_the_hub_forwards_the_digest(self):
        hub = (ROOT / "hub.py").read_text(encoding="utf-8")
        self.assertIn("digest", hub.split("/api/session/permission", 1)[1][:400])


class RefusalIsNeverGatedOnTheDigest(unittest.TestCase):
    """The digest gates granting only; a refusal is always deliverable, or a
    stale tab deadlocks the agent.
    """

    def _pane(self, oversize=False):
        return PermissionDigestIsConsent._pane(
            PermissionDigestIsConsent(), oversize=oversize)

    def test_a_refusal_with_no_digest_at_all_still_delivers(self):
        p = self._pane()
        self.assertTrue(p.answer("r1", "reject_once"))
        self.assertNotIn("r1", p.pending)

    def test_a_refusal_with_a_stale_digest_still_delivers(self):
        """The exact stale-tab case: the browser holds a digest from an
        earlier request that reused this same requestId."""
        p = self._pane()
        self.assertTrue(p.answer("r1", "reject_once", digest="0000"))
        self.assertNotIn("r1", p.pending)

    def test_an_oversize_request_can_still_be_refused_with_no_digest(self):
        p = self._pane(oversize=True)
        self.assertTrue(p.answer("r1", "reject_once"))
        self.assertNotIn("r1", p.pending)

    def test_the_refusal_message_tells_you_what_to_do(self):
        """A refused GRANT must say the way out, or the card reads as broken."""
        p = self._pane()
        with self.assertRaises(ValueError) as cm:
            p.answer("r1", "allow_once", digest="0000")
        self.assertIn("Reload", str(cm.exception))


class AnAnswerSaysWhoGaveIt(unittest.TestCase):
    """The transcript must not claim the operator chose what a script chose:
    every answer records the answering client's label, and only a known label
    is kept."""

    def _answer(self, **kw):
        p = PermissionDigestIsConsent._pane(PermissionDigestIsConsent())
        got = []
        p.emit = lambda kind, data, **k: got.append((kind, data))
        p.answer("r1", "reject_once", **kw)
        return [d for k, d in got if k == "permission_answered"][0]

    def test_each_known_label_is_recorded(self):
        import sessions
        for via in sessions.ANSWER_VIAS:
            self.assertEqual(self._answer(via=via)["via"], via)

    def test_no_label_is_recorded_as_unknown(self):
        self.assertIsNone(self._answer()["via"])

    def test_an_unknown_label_is_recorded_as_unknown(self):
        self.assertIsNone(self._answer(via="operator")["via"])

    def test_the_browser_never_claims_an_answer_it_cannot_place(self):
        _run_node_selftest(self, "selftest_answered.mjs",
                           "who answered a permission, in the browser")

    def test_the_hub_forwards_the_label(self):
        hub = (ROOT / "hub.py").read_text(encoding="utf-8")
        self.assertIn('via=b.get("via")', hub.split("/api/session/permission", 1)[1][:400])

    def test_each_client_declares_itself(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(js.count("digest: d.digest, via: 'wall'"), 2,
                         "card click and composer 1-9/Esc must both say wall")
        self.assertIn('"via": "terminal"', (ROOT / "cli.py").read_text(encoding="utf-8"))
        self.assertIn('"via": "script"', (ROOT / "lane_matrix.py").read_text(encoding="utf-8"))


class ARequestIdIsNotUniqueInATranscript(unittest.TestCase):
    """A requestId is only unique among in-flight requests; stale cards in a
    transcript may share it.
    """

    def test_the_card_is_told_whether_it_is_live_not_left_to_guess(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function permCard(p, d, outcome, live)", js)
        self.assertIn("const answered = !live;", js)
        self.assertNotIn("const answered = !p.pending.includes(d.requestId);", js,
                         "deriving liveness from the id alone is the bug")

    def test_outcomes_are_paired_by_position_not_by_id(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("permOutcomes.set(open, e)", js)
        self.assertNotIn("permOutcomes.set((e.data || {}).requestId, e)", js)
        self.assertIn("permOutcomes.get(e.seq)", js)

    def _stub(self):
        """A client with real state (via `_init_state()`) and no child process."""
        import acp
        c = acp.AcpClient.__new__(acp.AcpClient)
        c._init_state()
        c.alive = True
        c.events, c.perms, c.written = [], [], []
        c.on_event = lambda k, d=None: c.events.append((k, d))
        c.on_permission = c.perms.append
        c._write = c.written.append
        return c

    def test_a_reused_id_is_refused_and_the_live_card_survives(self):
        """A duplicate pending id is refused; the card already on the rail stays
        answerable and the pending bound holds.
        """
        import threading
        c = self._stub()
        first = threading.Event()
        c._perm_answers["0"] = {"ev": first, "option": acp_unanswered()}

        c._on_request({"method": "session/request_permission", "id": 0,
                       "params": {"toolCall": {"title": "second"}}})

        self.assertIs(c._perm_answers["0"]["ev"], first,
                      "the live card was replaced by the duplicate")
        self.assertFalse(first.is_set(),
                         "the human's pending card was cancelled out from "
                         "under them by the agent reusing an id")
        reasons = [d.get("reason") for k, d in c.events if k == "permission_expired"]
        self.assertTrue(any("already pending" in (r or "") for r in reasons),
                        f"the duplicate was not refused loudly: {c.events}")
        self.assertEqual(c.perms, [],
                         "a second card was drawn for a refused duplicate")

    def test_the_old_waiter_does_not_pop_the_new_slot(self):
        """A late waiter for an old request must not pop the next request's slot."""
        import threading
        c = self._stub()
        old_ev = threading.Event()
        new_ev = threading.Event()
        c._perm_answers["0"] = {"ev": new_ev, "option": acp_unanswered()}
        old_ev.set()                                            # old one released
        c._await_permission("0", 0, {}, old_ev)
        self.assertIn("0", c._perm_answers, "the new slot must survive")
        self.assertIs(c._perm_answers["0"]["ev"], new_ev)


class StaticPathContainment(unittest.TestCase):
    """A string prefix check is not a containment check."""

    def test_traversal_out_of_static_is_refused(self):
        import hub
        for bad in ("../hub.py", "../../etc/passwd", "../static-secret/x",
                    "../../../../../../etc/shadow"):
            self.assertIsNone(hub._safe_static_path(bad), bad)

    def test_percent_encoded_traversal_resolves_INSIDE_static(self):
        """`..%2fhub.py` is not percent-decoded, so it resolves to a filename
        inside static/.
        """
        import hub
        got = hub._safe_static_path("..%2fhub.py")
        self.assertIsNotNone(got)
        self.assertEqual(got.parent, (ROOT / "static").resolve())
        self.assertFalse(got.is_file())

    def test_a_real_asset_resolves(self):
        import hub
        self.assertIsNotNone(hub._safe_static_path("app.js"))


class MacosPlistIsThisHost(unittest.TestCase):
    """The launchd plist template names no account; rendered for an account,
    it points at that account's tree."""

    def test_the_plist_does_not_point_at_the_ranch_user(self):
        text = (ROOT / "com.cvp1.corral-light.plist").read_text(encoding="utf-8")
        self.assertNotIn("/Users/<user>/", text)
        self.assertIn("/Users/USER/corral-light", text)
        self.assertIn("/opt/homebrew/bin/python3", text)
        rendered = text.replace("/Users/USER", "/Users/alice")
        self.assertNotIn("/Users/USER", rendered)
        self.assertIn("/Users/alice/corral-light/hub.py", rendered)
        self.assertIn("/Users/alice/Library/Logs/corral-light.log", rendered)


class TheServiceRunsThisTree(unittest.TestCase):
    """The installed LaunchAgent must run this tree; another checkout lacks the
    gitignored adapters.
    """

    def _plist(self, program=None, workdir=None):
        import plistlib
        d = {}
        if program:
            d["ProgramArguments"] = ["/opt/homebrew/bin/python3", str(program)]
        if workdir:
            d["WorkingDirectory"] = str(workdir)
        path = Path(tmpdir(self)) / "com.cvp1.corral-light.plist"
        with open(path, "wb") as fh:
            plistlib.dump(d, fh)
        return path

    def test_the_matching_tree_is_silent(self):
        """A correct host says nothing (the control for the failing cases below)."""
        import diagnose
        tree = Path(tmpdir(self)).resolve()
        plist = self._plist(tree / "hub.py", tree)
        self.assertIsNone(diagnose.service_tree_problem(
            root=tree, path=plist, label="nope.not.loaded"))

    def test_a_worktree_in_program_arguments_is_caught(self):
        import diagnose
        root = Path(tmpdir(self)).resolve()
        other = Path(tmpdir(self)).resolve()
        plist = self._plist(other / "hub.py", other)
        problem = diagnose.service_tree_problem(
            root=root, path=plist, label="nope.not.loaded")
        self.assertIsNotNone(problem)
        self.assertIn(str(other), problem)
        self.assertIn(str(root), problem)

    def test_the_reason_names_the_gitignored_adapters(self):
        """The reason names the gitignored node_modules adapters, explaining which
        lanes vanish.
        """
        import diagnose
        root = Path(tmpdir(self)).resolve()
        plist = self._plist(Path(tmpdir(self)).resolve() / "hub.py")
        problem = diagnose.service_tree_problem(
            root=root, path=plist, label="nope.not.loaded")
        self.assertIn("spike/node_modules/", problem)
        self.assertIn("kickstart", problem)

    def test_no_installed_agent_is_not_a_fault(self):
        """Running hub.py from a checkout with no installed agent is supported,
        not a fault.
        """
        import diagnose
        root = Path(tmpdir(self)).resolve()
        self.assertEqual(diagnose.installed_service_trees(root / "absent.plist"), [])
        self.assertIsNone(diagnose.service_tree_problem(
            root=root, path=root / "absent.plist", label="nope.not.loaded"))

    def test_the_interpreter_is_not_mistaken_for_the_tree(self):
        """argv[0] is /opt/homebrew/bin/python3. Reading the tree off it would
        report /opt/homebrew/bin on every correctly configured host."""
        import diagnose
        tree = Path(tmpdir(self)).resolve()
        trees = diagnose.installed_service_trees(self._plist(tree / "hub.py"))
        self.assertEqual(trees, [tree])

    @unittest.skipUnless(sys.platform == "darwin",
                         "launchd plist is a macOS artifact; the shipped absolute "
                         "paths are the mac tree, not this checkout")
    @unittest.skipIf((ROOT / ".git").is_file(),
                     "a linked git worktree is never the tree the shipped plist "
                     "names; diagnose says so at run time, and this check runs "
                     "in the main checkout")
    def test_the_repo_plist_passes_its_own_check(self):
        """The file we ship must be the file that satisfies this. Otherwise
        the documented fix (`cp` it into LaunchAgents) reinstalls a fault."""
        import diagnose
        template = (ROOT / "com.cvp1.corral-light.plist").read_text(encoding="utf-8")
        rendered = Path(tmpdir(self)) / "com.cvp1.corral-light.plist"
        rendered.write_text(template.replace("/Users/USER", str(Path.home())),
                            encoding="utf-8")
        self.assertIsNone(diagnose.service_tree_problem(
            root=ROOT, path=rendered, label="nope.not.loaded"))


class UnavailableReasonsNameWhatWasChecked(unittest.TestCase):
    """A lane's unavailable reason names only locations that were probed."""

    def test_codex_names_the_path_it_probed(self):
        import codex_launcher
        real_adapter, real_here = codex_launcher.DEFAULT_ADAPTER, codex_launcher.HERE
        stray = Path(tmpdir(self)).resolve()
        codex_launcher.DEFAULT_ADAPTER = stray / "spike/node_modules/.bin/codex-acp"
        codex_launcher.HERE = stray
        self.addCleanup(setattr, codex_launcher, "DEFAULT_ADAPTER", real_adapter)
        self.addCleanup(setattr, codex_launcher, "HERE", real_here)
        os.environ.pop("CORRAL_CODEX_ACP", None)
        reason = codex_launcher.unavailable_reason()
        self.assertIn(str(stray), reason)
        # Must not name a tree this process may not be running from.
        self.assertNotIn("npm install in corral-light/spike", reason)


class TheGeminiLaneAnswersPlatformFirst(unittest.TestCase):
    """With no pinned build for this host, the gemini lane reports the platform,
    not the missing files.
    """

    def test_an_unpinned_host_is_told_the_platform_not_the_missing_file(self):
        import sessions
        import install_antigravity_acp as ia
        real = ia.release_for
        try:
            ia.release_for = lambda *a: None
            gemini = [a for a in sessions.available_agents() if a["key"] == "gemini"]
        finally:
            ia.release_for = real
        self.assertEqual(len(gemini), 1)
        self.assertFalse(gemini[0]["available"])
        self.assertNotIn("not installed:", gemini[0]["why"])
        self.assertIn("pinned Antigravity ACP release", gemini[0]["why"])


class PairCodeIsNotPython(unittest.TestCase):
    """The pair code is passed as data, never interpolated into `python -c`."""

    def test_the_wrapper_does_not_interpolate_the_code_into_python(self):
        text = (ROOT / "corral-light").read_text(encoding="utf-8")
        self.assertNotIn("approve('${2", text)
        self.assertNotIn('approve("${2', text)

    def test_a_quote_in_the_pair_code_is_not_executed(self):
        import subprocess
        marker = Path(tmpdir(self)) / "pwned"
        code = f"x'; open(r'{marker}','w').write('pwned')#"
        r = subprocess.run(
            [str(ROOT / "corral-light"), "pair", code],
            capture_output=True, text=True, timeout=10)
        self.assertFalse(marker.is_file(),
                         "pair interpolated argv into python -c: "
                         + (r.stdout + r.stderr)[:300])
        self.assertNotEqual(r.returncode, 0)


class ContentLengthAndFrames(unittest.TestCase):
    """A cookie-authed control plane on loopback still has to bound bodies
    and refuse to be iframed from another local port."""

    def test_negative_content_length_is_refused(self):
        import hub
        with self.assertRaises(ValueError):
            hub.parse_content_length("-1")
        with self.assertRaises(ValueError):
            hub.parse_content_length(str(hub.MAX_BODY + 1))
        self.assertEqual(hub.parse_content_length("0"), 0)
        self.assertEqual(hub.parse_content_length("12"), 12)

    def test_every_response_refuses_framing(self):
        hub = (ROOT / "hub.py").read_text(encoding="utf-8")
        self.assertIn("X-Frame-Options", hub)
        self.assertIn("frame-ancestors 'none'", hub)
        self.assertIn("_stream", hub)
        # SSE has its own header path (_stream_body); it must apply the same lock.
        stream = hub.split("def _stream_body", 1)[1].split("def ", 1)[0]
        self.assertIn("FRAME_LOCK", stream)


class LiveCapIsNotJustCreate(unittest.TestCase):
    """The live-process cap applies on resume and reopen, not just create()."""

    def _mgr(self):
        import sessions, threading
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes = {}
        m._lock = threading.Lock()
        m.subscribers = []
        return m

    def _pane(self, mgr, state):
        import sessions, uuid
        p = sessions.Pane.__new__(sessions.Pane)
        p.id = uuid.uuid4().hex[:12]
        p.agent = "ollama"
        p.mgr = mgr
        p._init_runtime()
        p.state = state
        p.acp_session = "sess"
        return p

    def test_resume_refuses_at_the_live_cap(self):
        import sessions
        m = self._mgr()
        for _ in range(sessions.MAX_PANES):
            p = self._pane(m, "ready")
            m.panes[p.id] = p
        extra = self._pane(m, "detached")
        m.panes[extra.id] = extra
        with self.assertRaises(ValueError) as ar:
            extra.resume()
        self.assertIn(str(sessions.MAX_PANES), str(ar.exception))
        self.assertEqual(extra.state, "detached")

    def test_reopen_refuses_at_the_roster_cap(self):
        import sessions
        m = self._mgr()
        for _ in range(sessions.MAX_ROSTER):
            p = self._pane(m, "detached")
            m.panes[p.id] = p
        with self.assertRaises(ValueError) as ar:
            m.reopen("no-such-pane")
        self.assertIn(str(sessions.MAX_ROSTER), str(ar.exception))

    def test_restore_keeps_more_than_the_live_cap(self):
        """Restart must not drop conversations 13–60. They come back detached;
        MAX_PANES only applies when one of them wants a process."""
        import sessions, json
        state = Path(tmpdir(self))
        n = sessions.MAX_PANES + 3
        for i in range(n):
            d = state / "panes" / f"p{i:02d}"
            d.mkdir(parents=True)
            (d / "meta.json").write_text(json.dumps({
                "id": f"p{i:02d}", "agent": "ollama",
                "cwd": str(state),
                "created": f"2026-01-{i + 1:02d}T00:00:00Z",
            }))
            (d / "events.jsonl").write_text("")
        real = sessions.STATE
        sessions.STATE = state
        m = self._mgr()
        try:
            m.restore()
            self.assertEqual(len(m.panes), n,
                             "restore used the live cap, not the roster cap")
            self.assertEqual(getattr(m, "not_restored", 0), 0)
        finally:
            sessions.STATE = real
            for p in m.panes.values():
                log = getattr(p, "_log", None)
                if log is not None:
                    try:
                        log.close()
                    except Exception:
                        pass


class StrictDoesNotInheritHostAllow(unittest.TestCase):
    """A host Bash(*) allow in ~/.claude/settings.json must not ride into a
    pane labelled strict. Deny stays; allow starts empty."""

    def test_strict_drops_host_allow_and_keeps_deny(self):
        import sessions
        _pin_sessions_platform(self, "linux")
        home = Path(tmpdir(self))
        (home / ".claude").mkdir()
        (home / ".claude" / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "a" * 108}}))
        (home / ".claude" / "settings.json").write_text(json.dumps({
            "permissions": {"allow": ["Bash(*)"], "deny": ["Read(./.env)"]},
            "defaultMode": "auto",
        }))
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            d = sessions.seed_config_dir(home / "cfg", "strict")
            perm = json.loads((Path(d) / "settings.json").read_text())["permissions"]
            self.assertEqual(perm.get("allow"), [])
            self.assertEqual(perm.get("deny"), ["Read(./.env)"])
            self.assertEqual(perm.get("defaultMode"), "default")
        finally:
            Path.home = real


class EmptyAuthJsonIsNotALogin(unittest.TestCase):
    """An empty auth.json is not a login (Grok, Codex)."""

    def test_grok_empty_auth_json_is_not_present(self):
        import grok_launcher
        home = Path(tmpdir(self))
        (home / "auth.json").write_text("{}")
        real = grok_launcher.GROK_HOME
        grok_launcher.GROK_HOME = home
        try:
            self.assertFalse(grok_launcher.auth_present())
        finally:
            grok_launcher.GROK_HOME = real

    def test_codex_empty_auth_json_is_not_present(self):
        import codex_launcher
        home = Path(tmpdir(self))
        (home / "auth.json").write_text("{}")
        real = codex_launcher.CODEX_HOME
        codex_launcher.CODEX_HOME = home
        try:
            self.assertFalse(codex_launcher.auth_present())
        finally:
            codex_launcher.CODEX_HOME = real

    def test_a_token_bearing_file_still_counts(self):
        import grok_launcher
        home = Path(tmpdir(self))
        (home / "auth.json").write_text(json.dumps(
            {"accessToken": "g" * 40}))
        real = grok_launcher.GROK_HOME
        grok_launcher.GROK_HOME = home
        try:
            self.assertTrue(grok_launcher.auth_present())
        finally:
            grok_launcher.GROK_HOME = real

    def test_grok_main_refuses_when_unauthenticated(self):
        import grok_launcher, inspect
        self.assertIn("unavailable_reason", inspect.getsource(grok_launcher.main))


class ModelExtrasSurviveARealSession(unittest.TestCase):
    """Model extras are layered on in remember_catalog, so they survive a real
    session/new overwriting the catalog (exercised with a synthetic entry).
    """

    def _with_extra(self, sessions, entry):
        real = sessions.MODEL_EXTRAS
        sessions.MODEL_EXTRAS = {"claude": [entry]}
        self.addCleanup(lambda: setattr(sessions, "MODEL_EXTRAS", real))

    def _mgr(self):
        import sessions
        m = sessions.Manager.__new__(sessions.Manager)
        m.catalog = {}
        real = sessions.CATALOG
        d = Path(tmpdir(self))
        sessions.CATALOG = d / "catalog.json"
        self.addCleanup(lambda: setattr(sessions, "CATALOG", real))
        return m, sessions

    def test_an_extra_is_appended_to_claudes_model_options(self):
        m, sessions = self._mgr()
        self._with_extra(sessions, {"value": "made-up", "name": "Made Up",
                                    "description": ""})
        m.remember_catalog("claude", {"model": {
            "name": "Model", "value": "opus[1m]",
            "options": [{"value": "opus[1m]", "name": "Opus", "description": ""}]}})
        values = [o["value"] for o in m.catalog["claude"]["model"]["options"]]
        self.assertIn("made-up", values)

    def test_a_repeated_write_does_not_duplicate_it(self):
        """Survives a second write, as every real session/new rewrites the catalog."""
        m, sessions = self._mgr()
        self._with_extra(sessions, {"value": "made-up", "name": "Made Up",
                                    "description": ""})
        for _ in range(3):
            m.remember_catalog("claude", {"model": {
                "name": "Model", "value": "opus[1m]",
                "options": [{"value": "opus[1m]", "name": "Opus", "description": ""}]}})
        values = [o["value"] for o in m.catalog["claude"]["model"]["options"]]
        self.assertEqual(values.count("made-up"), 1)

    def test_other_agents_are_unaffected(self):
        """MODEL_EXTRAS is keyed by agent — grok's model list must not grow an
        option grok was never asked about and cannot serve."""
        m, sessions = self._mgr()
        self._with_extra(sessions, {"value": "made-up", "name": "Made Up",
                                    "description": ""})
        m.remember_catalog("grok", {"model": {
            "name": "Model", "value": "grok-4", "options": []}})
        # Grok's `model` key legitimately survives with an EMPTY options list
        # (a fixed choice, not a picker — remember_catalog's own docstring);
        # the point here is that it stays empty, not that the key is dropped.
        self.assertEqual(m.catalog["grok"]["model"]["options"], [])
        m.remember_catalog("ollama", {"model": {
            "name": "Model", "value": "llama3",
            "options": [{"value": "llama3", "name": "llama3", "description": ""}]}})
        values = [o["value"] for o in m.catalog["ollama"]["model"]["options"]]
        self.assertNotIn("made-up", values)

    def test_an_agent_with_no_model_key_is_not_given_one(self):
        """An agent whose config carries no model at all (grok reports a bare
        value with no options, dropped by the filter above) must not have a
        `model` key manufactured just to hang an extra off of."""
        m, sessions = self._mgr()
        m.remember_catalog("codex", {"effort": {
            "name": "Effort", "value": "default",
            "options": [{"value": "default", "name": "Default", "description": ""}]}})
        self.assertNotIn("model", m.catalog["codex"])


class SshShellIsBounded(unittest.TestCase):
    """The ssh lane's bounds, exercised against a local bash via the connect
    override.
    """

    def _shell(self):
        import ssh_acp
        sh = ssh_acp.Shell(["bash", "--noprofile", "--norc"])
        self.addCleanup(sh.kill)
        return sh

    def test_reader_treats_closed_pipe_as_normal_eof(self):
        import queue
        import ssh_acp

        class ClosedStdout:
            def readline(self, _limit):
                raise ValueError("I/O operation on closed file")

        output = queue.Queue()
        ssh_acp.Shell._reader(
            types.SimpleNamespace(stdout=ClosedStdout()), output)
        self.assertIsNone(output.get_nowait())

    def test_a_command_runs_and_reports_its_exit_status(self):
        import ssh_acp
        sh = self._shell()
        seen = []
        self.assertEqual(sh.run("echo hello", 10, seen.append), "0")
        self.assertIn("hello", "".join(seen))
        # A subshell exit, not a bare `exit` (which hangs up the shell; see below).
        self.assertEqual(sh.run("(exit 3)", 10, lambda t: None), "3",
                         "a nonzero status must reach the pane, not be swallowed")

    def test_typing_exit_closes_the_shell_and_the_next_command_reopens_it(self):
        """`exit` is a command a human WILL type into a shell pane, and in a
        persistent bash it kills the far end before the sentinel printf can
        run. So it can never report a status — it reports a closed connection,
        which is the truth. What must not happen is the lane staying broken."""
        import ssh_acp
        sh = self._shell()
        with self.assertRaises(ssh_acp.ShellError) as cm:
            sh.run("exit 3", 10, lambda t: None)
        self.assertIn("shell exited", str(cm.exception))
        seen = []
        self.assertEqual(sh.run("echo reopened", 10, seen.append), "0",
                         "a typed `exit` left the lane dead")
        self.assertIn("reopened", "".join(seen))

    def test_the_shell_is_persistent_across_commands(self):
        """State set by one command is visible to the next — that is the whole
        difference between this and `ssh host <cmd>` per prompt."""
        sh = self._shell()
        sh.run("CORRAL_MARKER=kept", 10, lambda t: None)
        seen = []
        sh.run("echo $CORRAL_MARKER", 10, seen.append)
        self.assertIn("kept", "".join(seen))

    def test_output_is_capped_and_the_shell_recovers_clean(self):
        import ssh_acp
        sh = self._shell()
        real = ssh_acp.MAX_OUTPUT_BYTES
        ssh_acp.MAX_OUTPUT_BYTES = 2048
        self.addCleanup(lambda: setattr(ssh_acp, "MAX_OUTPUT_BYTES", real))
        with self.assertRaises(ssh_acp.ShellError) as cm:
            sh.run("seq 1 100000", 20, lambda t: None)
        self.assertIn("exceeded", str(cm.exception))
        # The recovery is the point: a killed shell must not poison the lane.
        seen = []
        self.assertEqual(sh.run("echo back", 10, seen.append), "0")
        self.assertIn("back", "".join(seen))

    def test_a_command_that_never_finishes_is_killed_by_the_clock(self):
        """Unlike acp.py, the shell lane has a clock: a command that never returns
        is killed, and the reason says so.
        """
        import ssh_acp
        sh = self._shell()
        with self.assertRaises(ssh_acp.ShellError) as cm:
            sh.run("sleep 30", 0.5, lambda t: None)
        self.assertIn("no completion", str(cm.exception))
        seen = []
        self.assertEqual(sh.run("echo alive", 10, seen.append), "0")
        self.assertIn("alive", "".join(seen))

    def test_the_commands_own_output_cannot_forge_completion(self):
        """The sentinel carries a per-command nonce. Without it, `echo`ing the
        sentinel would end the command early and hand the NEXT command's reader
        a stream that starts mid-output."""
        import ssh_acp
        sh = self._shell()
        seen = []
        status = sh.run(
            f'echo "{ssh_acp.SENTINEL} deadbeefcafe 0"; echo after; (exit 5)',
            10, seen.append)
        out = "".join(seen)
        self.assertEqual(status, "5", "the forged line ended the command early")
        self.assertIn("after", out, "output after the forgery was lost")

    def test_cancel_stops_the_command(self):
        import ssh_acp, threading
        sh = self._shell()
        def cancel_soon():
            import time
            time.sleep(0.4)
            sh.cancelled = True
        threading.Thread(target=cancel_soon, daemon=True).start()
        with self.assertRaises(ssh_acp.ShellError) as cm:
            sh.run("sleep 30", 30, lambda t: None)
        self.assertIn("cancelled", str(cm.exception))


class SshLaneHasNoPermissionRail(unittest.TestCase):
    """The ssh lane has no permission rail (the human types the bytes), so it
    must never be exposed as an agent tool.
    """

    def test_the_adapter_never_asks_for_permission(self):
        src = (ROOT / "ssh_acp.py").read_text(encoding="utf-8")
        self.assertNotIn("request_permission", src.replace(
            "no permission rail", ""),
            "a rail here would be a gate whose weakest path is the shell itself")

    def test_the_lane_declares_no_tools(self):
        import sessions
        sessions.refresh_host_lanes()
        specs = [s for k, s in sessions.AGENTS.items() if k.startswith("host:")]
        for s in specs:
            self.assertFalse(s.get("tools"),
                             "a tools:true shell lane would put it in reach of "
                             "the attach path meant for agents")

    def test_the_adapter_is_documented_as_human_only(self):
        src = (ROOT / "ssh_acp.py").read_text(encoding="utf-8")
        self.assertIn("never be handed to an agent", src)


class SshHostsComeFromAFileNotTheFleet(unittest.TestCase):
    """Light has no estate. The inventory is one hand-written file, and its
    failure modes must not take the picker with them."""

    def _with_hosts(self, payload):
        import sessions
        d = Path(tmpdir(self))
        f = d / "ssh-hosts.json"
        f.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        real = sessions.EXTRA_SSH_HOSTS
        # AGENTS is add/update-never-delete, so restore the exact key set or later
        # host-lane counts include this test's leftovers.
        before = {k: v for k, v in sessions.AGENTS.items()
                  if k.startswith("host:")}

        def restore():
            sessions.EXTRA_SSH_HOSTS = real
            for k in [k for k in sessions.AGENTS if k.startswith("host:")]:
                del sessions.AGENTS[k]
            sessions.AGENTS.update(before)

        sessions.EXTRA_SSH_HOSTS = f
        self.addCleanup(restore)
        return sessions

    def test_a_host_becomes_a_prefixed_lane_in_the_ssh_group(self):
        s = self._with_hosts([{"name": "testbox", "ip": "10.0.0.9"}])
        s.refresh_host_lanes()
        self.assertIn("host:testbox", s.AGENTS)
        self.assertEqual(s._group_of("host:testbox"), "ssh",
                         "the prefix seam is what consolidates hosts under one "
                         "picker entry instead of one row per machine")
        self.assertIn("ssh", s.agent_groups())

    def test_a_removed_host_is_tombstoned_never_deleted(self):
        """Its pane must still render the transcript. Deleting the lane key
        would KeyError /api/state via snapshot()."""
        s = self._with_hosts([{"name": "goingaway", "ip": "10.0.0.9"}])
        s.refresh_host_lanes()
        self.assertIn("host:goingaway", s.AGENTS)
        s.EXTRA_SSH_HOSTS.write_text("[]")
        s.refresh_host_lanes()
        self.assertIn("host:goingaway", s.AGENTS, "the lane was deleted")
        self.assertIn("no longer listed",
                      s.AGENTS["host:goingaway"]["unavailable"])

    def test_a_malformed_file_does_not_break_the_picker(self):
        s = self._with_hosts("{not json at all")
        s.refresh_host_lanes()
        self.assertTrue(s.available_agents(),
                        "a typo in ssh-hosts.json must not empty the agent list")

    def test_the_host_list_is_bounded(self):
        s = self._with_hosts([{"name": f"b{i}", "ip": "10.0.0.1"}
                              for i in range(40)])
        # Assert on what the FILE yields, not on AGENTS: the never-delete
        # contract means AGENTS legitimately holds tombstones from earlier
        # host lists, so counting it would measure history, not the bound.
        self.assertLessEqual(len(s._live_ssh_hosts()), s.MAX_SSH_HOSTS)
        s.refresh_host_lanes()
        fresh = [k for k in s.AGENTS if k.startswith("host:b")]
        self.assertLessEqual(len(fresh), s.MAX_SSH_HOSTS)

    def test_a_connect_override_becomes_lane_env_not_argv(self):
        """The test/local form. It must arrive as env so the command is never
        word-split into argv by us — shlex in the adapter owns that."""
        s = self._with_hosts([{"name": "local", "connect": "bash --norc"}])
        s.refresh_host_lanes()
        spec = s.AGENTS["host:local"]
        self.assertEqual(spec["env"]["SSH_ACP_CONNECT"], "bash --norc")
        self.assertNotIn("bash --norc", " ".join(spec["argv"]))


class PostureRidesTheAcpMode(unittest.TestCase):
    """Permission posture is imposed over the ACP `mode` config option, which
    needs no config dir (so no Keychain).
    """

    def _pane(self, posture="auto", mode_value="default", agent="claude"):
        import sessions, uuid
        p = sessions.Pane.__new__(sessions.Pane)
        p.id = uuid.uuid4().hex[:12]
        p.agent = agent
        p.mgr = self._mgr()
        p.cwd = "/tmp"
        p.posture = posture
        p.want_model = p.want_effort = None
        p.acp_session = "sess"
        p._init_runtime()
        p.state = "ready"
        p.config = {"mode": {
            "value": mode_value, "name": "Permission mode", "realId": "mode",
            "options": [{"value": v, "name": v, "description": ""} for v in
                        ("auto", "default", "acceptEdits", "plan", "dontAsk",
                         "bypassPermissions")]}}
        return p

    def _mgr(self):
        import sessions, threading
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes = {}
        m._lock = threading.Lock()
        m.subscribers = []
        m.remember_catalog = lambda *a, **k: None
        return m

    class _Client:
        """Records set_config calls and echoes the new value back the way the
        real adapter does — the ack IS the evidence _apply_posture reads."""
        def __init__(self, echo=True, raises=None, echo_value=None):
            self.calls, self.echo = [], echo
            self.raises, self.echo_value = raises, echo_value

        def set_config(self, session_id, config_id, value):
            self.calls.append((config_id, value))
            if self.raises:
                raise self.raises
            if not self.echo:
                return {}
            got = self.echo_value or value
            return {"configOptions": [{"id": "mode", "currentValue": got,
                                       "name": "Permission mode",
                                       "options": []}]}

    def test_the_map_only_names_modes_the_agent_actually_offers(self):
        """Corral must never send a permission mode the adapter would reject —
        the values are transcribed from the live handshake, not invented."""
        import sessions
        offered = {"auto", "default", "acceptEdits", "plan", "dontAsk",
                   "bypassPermissions"}
        self.assertTrue(set(sessions.POSTURE_MODE.values()) <= offered)
        self.assertEqual(set(sessions.POSTURE_MODE), set(sessions.POSTURES))

    def test_auto_is_actually_sent_and_reported_enforced(self):
        """The bug, directly: a pane whose posture is `auto` must leave the
        handshake with the session really in auto."""
        p = self._pane(posture="auto")
        p.client = self._Client()
        self.assertTrue(p._apply_posture())
        self.assertEqual(p.client.calls, [("mode", "auto")])
        self.assertEqual(p.config["mode"]["value"], "auto")

    def test_each_posture_maps_to_the_agents_own_name_for_it(self):
        for posture, wire in (("auto", "auto"), ("edits", "acceptEdits"),
                              ("strict", "default")):
            with self.subTest(posture=posture):
                p = self._pane(posture=posture, mode_value="plan")
                p.client = self._Client()
                self.assertTrue(p._apply_posture())
                self.assertEqual(p.client.calls, [("mode", wire)])

    def test_a_session_already_in_that_mode_costs_no_wire_call(self):
        p = self._pane(posture="strict", mode_value="default")
        p.client = self._Client()
        self.assertTrue(p._apply_posture())
        self.assertEqual(p.client.calls, [])

    def test_a_refused_mode_is_not_reported_as_enforced(self):
        """postureEnforced exists to stop Corral asserting a safety property
        it never established. An AgentError must not become a green pill."""
        import acp
        p = self._pane(posture="auto")
        p.client = self._Client(raises=acp.AgentError("nope"))
        self.assertFalse(p._apply_posture())
        self.assertTrue(any("could not set permissions" in
                            (e.get("data") or {}).get("text", "")
                            for e in p.events))

    def test_an_ack_that_did_not_take_is_not_enforced(self):
        """The adapter answering 200 with a DIFFERENT mode is the subtlest
        version of the same lie — read the echoed value, not the status."""
        p = self._pane(posture="auto")
        p.client = self._Client(echo_value="default")
        self.assertFalse(p._apply_posture())
        self.assertEqual(p.config["mode"]["value"], "default")

    def test_a_lane_advertising_no_mode_says_so_instead_of_guessing(self):
        p = self._pane(posture="auto")
        p.config = {}
        p.client = self._Client()
        self.assertFalse(p._apply_posture())
        self.assertEqual(p.client.calls, [])
        self.assertTrue(any("did not advertise a permission mode" in
                            (e.get("data") or {}).get("text", "")
                            for e in p.events))

    def test_an_unmapped_posture_is_never_silently_defaulted(self):
        """Guessing a permission mode is the one guess this file must not
        make: an unknown posture leaves the agent alone and says so."""
        p = self._pane(posture="something-new")
        p.client = self._Client()
        self.assertFalse(p._apply_posture())
        self.assertEqual(p.client.calls, [])

    def test_resume_reimposes_the_posture(self):
        """session/load hands back a session at the AGENT's defaults. Without
        re-imposing, a paused `auto` pane came back prompting while the pill
        still read `auto` — the pill was never the thing that made it auto."""
        p = self._pane(posture="auto")
        p.client = self._Client()
        p._apply_wants()
        self.assertEqual(p.client.calls, [("mode", "auto")])
        self.assertTrue(p.posture_enforced)

    def test_the_dialog_offers_the_posture_on_macos(self):
        """The dialog offers posture on macOS."""
        import sessions
        _pin_sessions_platform(self, "darwin")
        self.assertTrue(
            sessions.posture_enforceable(sessions.AGENTS["claude"]))

    def test_a_lane_that_manages_its_own_permissions_still_says_false(self):
        """The fix must not turn every lane green: codex/ollama run under
        their own policy and must keep reporting that honestly."""
        import sessions
        for lane in ("codex", "ollama"):
            with self.subTest(lane=lane):
                self.assertFalse(
                    sessions.posture_enforceable(sessions.AGENTS[lane]))


class GrokPostureRidesArgv(unittest.TestCase):
    """`grok agent stdio` advertises no ACP permission mode and raises a card
    for every shell command its own policy does not auto-allow (measured
    2026-10-02: 11 cards in 314 tool calls, all `Execute`), so a pane under
    `auto` blocked on a card per command. The launcher realizes the posture with the
    agent's only knob, --always-approve, and the pane says so honestly.
    """

    def test_auto_adds_always_approve_before_stdio(self):
        import grok_launcher
        argv = grok_launcher.build_argv("/g", None, "auto")
        self.assertEqual(argv, ["/g", "agent", "--always-approve", "stdio"])
        argv = grok_launcher.build_argv("/g", "grok-4.7", "auto")
        self.assertEqual(argv, ["/g", "agent", "--model", "grok-4.7",
                                "--always-approve", "stdio"])

    def test_strict_and_edits_add_no_flag(self):
        import grok_launcher
        for posture in ("strict", "edits", None):
            with self.subTest(posture=posture):
                self.assertEqual(grok_launcher.build_argv("/g", None, posture),
                                 ["/g", "agent", "stdio"])

    def test_main_reads_the_posture_from_the_spawn_env(self):
        import grok_launcher, inspect
        self.assertIn("CORRAL_POSTURE", inspect.getsource(grok_launcher.main))

    def test_spawn_env_hands_the_posture_only_to_argv_lanes(self):
        import sessions
        env = sessions.spawn_env(sessions.AGENTS["grok"], None, "auto")
        self.assertEqual(env.get("CORRAL_POSTURE"), "auto")
        for lane in ("claude", "codex", "ollama"):
            with self.subTest(lane=lane):
                env = sessions.spawn_env(sessions.AGENTS[lane], None, "auto")
                self.assertNotIn("CORRAL_POSTURE", env)

    def test_enforceable_per_posture(self):
        """auto and strict are realized; edits has no Grok mode and stays
        honest (pill reads `Grok policy`)."""
        import sessions
        spec = sessions.AGENTS["grok"]
        self.assertTrue(sessions.posture_enforceable(spec))
        self.assertTrue(sessions.posture_enforceable(spec, "auto"))
        self.assertTrue(sessions.posture_enforceable(spec, "strict"))
        self.assertFalse(sessions.posture_enforceable(spec, "edits"))

    def test_the_pane_reports_what_the_flag_does(self):
        import sessions
        p = sessions.Pane.__new__(sessions.Pane)
        p.agent = "grok"
        notes = []
        p.emit = lambda kind, d: notes.append((kind, d["text"]))
        p.posture = "auto"
        self.assertTrue(p._apply_posture())
        self.assertIn("--always-approve", notes[-1][1])
        p.posture = "edits"
        self.assertFalse(p._apply_posture())
        self.assertIn("no mode for posture 'edits'", notes[-1][1])


class OpusPlanWasNeverARealAlias(unittest.TestCase):
    """`opusplan` is not a vendor alias: the adapter accepts any name prefixed
    by a model and drops the rest, so acceptance proves nothing.
    """

    def test_no_invented_model_is_offered_for_claude(self):
        import sessions
        self.assertEqual(sessions.MODEL_EXTRAS.get("claude", []), [])

    def test_the_withdrawal_and_its_control_are_recorded(self):
        """The opus!!! / opusXYZ measurement stays recorded in sessions.py."""
        import inspect, sessions
        src = inspect.getsource(sessions)
        self.assertIn("opus!!!", src)
        self.assertIn("opusXYZ", src)


class AnAckIsNotAdoption(unittest.TestCase):
    """A set_config ack is checked against the echoed value, and a mismatch
    is reported.
    """

    def _pane(self, want_model):
        import sessions, uuid, threading
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes = {}; m._lock = threading.Lock(); m.subscribers = []
        m.remember_catalog = lambda *a, **k: None
        p = sessions.Pane.__new__(sessions.Pane)
        p.id = uuid.uuid4().hex[:12]
        p.agent = "claude"; p.mgr = m; p.cwd = "/tmp"; p.posture = "auto"
        p.want_model, p.want_effort = want_model, None
        p.acp_session = "sess"
        p._init_runtime()
        p.state = "ready"
        p.config = {"mode": {"value": "auto", "realId": "mode", "name": "m",
                             "options": [{"value": "auto", "name": "auto",
                                          "description": ""}]}}
        return p

    class _CoercingClient:
        """The real adapter's behaviour: accept the suffixed name, resolve it
        to the base model, and echo the base model back."""
        def __init__(self, resolves_to):
            self.resolves_to = resolves_to
        def set_config(self, session_id, config_id, value):
            if config_id == "mode":
                return {"configOptions": [{"id": "mode", "currentValue": value,
                                           "name": "m", "options": []}]}
            return {"configOptions": [{"id": "model",
                                       "currentValue": self.resolves_to,
                                       "name": "Model", "options": []}]}

    def test_a_coerced_model_is_reported_not_hidden(self):
        p = self._pane("opusplan")
        p.client = self._CoercingClient("opus[1m]")
        p._apply_wants()
        self.assertEqual(p.model, "opus[1m]")
        self.assertTrue(any("asked for model=opusplan" in
                            (e.get("data") or {}).get("text", "")
                            for e in p.events),
                        "a silently substituted model must leave a note")

    def test_an_honoured_model_says_nothing(self):
        """Edge-triggered: the note is news, so an exact match is silent."""
        p = self._pane("sonnet")
        p.client = self._CoercingClient("sonnet")
        p._apply_wants()
        self.assertEqual(p.model, "sonnet")
        self.assertFalse([e for e in p.events if e["kind"] == "note"])




class PanesFeedPanes(unittest.TestCase):
    """Composition verbs (quote, fan-out, cross-feed), asserted on the prompt
    each pane receives.
    """

    def _pane(self, agent, title, turns, state="ready"):
        import sessions
        p = sessions.Pane.__new__(sessions.Pane)
        p.id, p.agent, p.title, p.state = title, agent, title, state
        p.events, p._seq, p._lock = [], 0, threading.Lock()
        p._replaying, p._log, p._since_rotate_check = False, None, 0
        p.mgr = types.SimpleNamespace(broadcast=lambda e: None)
        p.last_activity = time.time()
        p.sent = []
        p.send = lambda text: p.sent.append(text)
        for kind, data in turns:
            p.emit(kind, data)
        return p

    def _mgr(self, *panes):
        import sessions
        m = sessions.Manager.__new__(sessions.Manager)
        m.panes = {p.id: p for p in panes}
        return m

    def test_last_answer_is_only_the_text_since_the_last_user_turn(self):
        p = self._pane("claude", "A", [
            ("user", {"text": "first"}), ("text", {"text": "old"}), ("turn_end", {}),
            ("user", {"text": "second"}), ("text", {"text": "new "}),
            ("tool", {"title": "Read"}), ("thought", {"text": "hm"}),
            ("text", {"text": "answer"}), ("turn_end", {})])
        self.assertEqual(p.last_answer(), ("new answer", True))

    def test_last_answer_reports_an_unfinished_turn(self):
        p = self._pane("claude", "A", [("user", {"text": "q"}),
                                       ("text", {"text": "half"})])
        self.assertEqual(p.last_answer(), ("half", False))

    def test_quote_is_attributed_fenced_and_bounded(self):
        import sessions
        long = "x" * (sessions.QUOTE_CHARS + 50)
        p = self._pane("grok", "A", [("user", {"text": "q"}),
                                     ("text", {"text": long}), ("turn_end", {})])
        r = self._mgr(p).quote("A")
        self.assertTrue(r["clipped"])
        self.assertTrue(r["text"].startswith("From Grok — A:"))
        self.assertIn("[…truncated]", r["text"])
        self.assertLess(len(r["text"]), sessions.QUOTE_CHARS + 200)

    def test_quote_refuses_ssh_panes_self_and_silence(self):
        a = self._pane("claude", "A", [("user", {"text": "q"}),
                                       ("text", {"text": "ans"}), ("turn_end", {})])
        quiet = self._pane("claude", "Q", [])
        ssh = self._pane("host:box", "S", [("text", {"text": "$ ls"})])
        m = self._mgr(a, quiet, ssh)
        with self.assertRaises(ValueError): m.quote("S")
        with self.assertRaises(ValueError): m.quote("A", "S")
        with self.assertRaises(ValueError): m.quote("A", "A")
        with self.assertRaises(ValueError): m.quote("Q")

    def test_fanout_delivers_the_same_text_and_reports_each_refusal(self):
        a = self._pane("claude", "A", [])
        b = self._pane("grok", "B", [])
        c = self._pane("gemini", "C", [])
        def refuse(text): raise ValueError("4 messages already waiting")
        c.send = refuse
        r = self._mgr(a, b, c).fanout(["A", "B", "C", "A", "missing"], "hello")
        self.assertEqual(a.sent, ["hello"])
        self.assertEqual(b.sent, ["hello"])
        self.assertEqual(r["sent"], 2)
        self.assertIn("waiting", r["results"]["C"])
        self.assertIn("no pane", r["results"]["missing"])

    def test_crossfeed_gives_each_arm_only_the_others_answers(self):
        panes = [self._pane(ag, t, [("user", {"text": "q"}),
                                    ("text", {"text": f"{t} says"}), ("turn_end", {})])
                 for ag, t in (("claude", "A"), ("grok", "B"), ("gemini", "C"))]
        r = self._mgr(*panes).crossfeed(["A", "B", "C"], "Round two.")
        self.assertEqual(r["sent"], 3)
        for p in panes:
            self.assertEqual(len(p.sent), 1)
            got = p.sent[0]
            self.assertTrue(got.startswith("Round two."))
            self.assertNotIn(f"{p.title} says", got)
            for o in panes:
                if o is not p:
                    self.assertIn(f"{o.title} says", got)

    def test_crossfeed_refuses_entirely_while_any_arm_is_unfinished(self):
        done = self._pane("claude", "A", [("user", {"text": "q"}),
                                          ("text", {"text": "done"}), ("turn_end", {})])
        busy = self._pane("grok", "B", [("user", {"text": "q"}),
                                        ("text", {"text": "half"})], state="busy")
        with self.assertRaises(ValueError) as cm:
            self._mgr(done, busy).crossfeed(["A", "B"], "go")
        self.assertIn("B", str(cm.exception))
        self.assertEqual(done.sent, [])          # nobody was sent a partial round

    def test_crossfeed_names_a_never_asked_pane_as_such(self):
        # A never-asked pane is named as such, not "has not finished answering".
        done = self._pane("claude", "A", [("user", {"text": "q"}),
                                          ("text", {"text": "done"}), ("turn_end", {})])
        fresh = self._pane("grok", "B", [("ready", {})])
        with self.assertRaises(ValueError) as cm:
            self._mgr(done, fresh).crossfeed(["A", "B"], "go")
        self.assertIn("has not been asked anything yet", str(cm.exception))
        self.assertNotIn("finished", str(cm.exception))
        self.assertEqual(done.sent, [])

    def test_crossfeed_names_an_empty_finished_answer_as_such(self):
        done = self._pane("claude", "A", [("user", {"text": "q"}),
                                          ("text", {"text": "done"}), ("turn_end", {})])
        silent = self._pane("grok", "B", [("user", {"text": "q"}),
                                          ("tool", {"title": "Read"}), ("turn_end", {})])
        with self.assertRaises(ValueError) as cm:
            self._mgr(done, silent).crossfeed(["A", "B"], "go")
        self.assertIn("no text to quote", str(cm.exception))
        self.assertEqual(done.sent, [])

    def test_crossfeed_needs_two_and_no_ssh(self):
        a = self._pane("claude", "A", [("user", {"text": "q"}),
                                       ("text", {"text": "x"}), ("turn_end", {})])
        s = self._pane("host:box", "S", [("user", {"text": "ls"}),
                                         ("text", {"text": "x"}), ("turn_end", {})])
        m = self._mgr(a, s)
        with self.assertRaises(ValueError): m.crossfeed(["A"], "go")
        with self.assertRaises(ValueError): m.crossfeed(["A", "S"], "go")
        self.assertEqual(a.sent, [])


class AgentsSurviveTheirSpawningThread(unittest.TestCase):
    """A Linux agent with a parent-death signal must outlive the HTTP thread
    that spawned its pane.
    """

    @unittest.skipUnless(sys.platform.startswith("linux"), "PDEATHSIG is Linux")
    def test_child_with_pdeathsig_outlives_the_thread_that_made_it(self):
        import acp
        # The child does what Grok Build does: prctl(PR_SET_PDEATHSIG=1, SIGTERM=15).
        # AcpClient's own reader owns the child's stdout, so readiness is a
        # marker file, not a line: the child writes it AFTER prctl has run.
        mark = Path(tmpdir(self)) / "armed"
        argv = [sys.executable, "-c",
                "import ctypes, time, sys, pathlib; ctypes.CDLL(None).prctl(1, 15); "
                f"pathlib.Path({str(mark)!r}).write_text('1'); time.sleep(20)"]
        box = {}
        def make():
            c = acp.AcpClient(argv, "/tmp")
            for _ in range(100):
                if mark.exists(): break
                time.sleep(0.05)
            box["c"] = c
        th = threading.Thread(target=make); th.start(); th.join(10)
        self.assertTrue(mark.exists(), "child never armed its parent-death signal")
        self.assertIn("c", box)
        time.sleep(1.0)                    # a doomed child is dead well within this
        c = box["c"]
        try:
            self.assertIsNone(c.p.poll(), "child was killed when its spawning thread exited")
        finally:
            c.p.kill(); c.p.wait(5)


class TheEdgeGuardsHoldOnARealSocket(unittest.TestCase):
    """corral_core/edge_live.py drives this skin's Handler over a real socket."""

    def test_edge_live(self):
        import hub
        import auth
        from corral_core import edge_live
        self.assertEqual(edge_live.run(hub, auth), [])


class TheWireCarriesTheDisplayProjection(unittest.TestCase):
    """`snapshot()` carries the `display` triage projection for non-browser
    consumers; the browser's JS mirror is pinned by selftest_display.mjs.
    """

    def _pane(self, state, pending=(), alive=True, exited=None):
        import sessions
        p = sessions.Pane.__new__(sessions.Pane)
        p.id = "disp-test"
        p.agent = "claude"
        p.cwd = "/tmp"
        p.title = "display test"
        p.title_locked = False
        p.minimized = False
        p.order = None
        p.pinned = False
        p.model = None
        p.effort = None
        p.config = {}
        p.commands = []
        p.posture = "auto"
        p.posture_enforced = False
        p.error = None
        p.pending = {k: {} for k in pending}
        p.usage = {}
        p.created = "now"
        p.mgr = types.SimpleNamespace(broadcast=lambda event: None)
        p.client = types.SimpleNamespace(
            alive=alive, p=types.SimpleNamespace(poll=lambda: exited))
        p.state = state
        p._expect_exit = False
        p.last_activity = time.time()
        p.events = []
        p._seq = 0
        p._lock = threading.Lock()
        p._replaying = False
        p._log = None
        p._since_rotate_check = 0
        return p

    def test_snapshot_carries_display(self):
        self.assertEqual(self._pane("ready").snapshot()["display"], "your-turn")
        self.assertEqual(self._pane("busy").snapshot()["display"], "working")

    def test_a_pending_card_shows_as_needs_you_even_though_state_says_ready(self):
        snap = self._pane("ready", pending=("req-1",)).snapshot()
        self.assertEqual(snap["state"], "ready",
                         "the raw enum is the record and does not change")
        self.assertEqual(snap["display"], "needs-you")

    def test_the_projection_sees_snapshots_own_correction(self):
        """snapshot() reports a process that has exited as `dead` WITHOUT
        writing that back to self.state on this path. A projection computed
        from the stale field would say `your-turn` about a corpse."""
        snap = self._pane("ready", alive=False).snapshot()
        self.assertEqual(snap["state"], "dead")
        self.assertEqual(snap["display"], "dead")

    def test_a_detached_pane_is_paused_on_the_wire(self):
        """A detached pane shows as paused: a human must resume it."""
        snap = self._pane("detached").snapshot()
        self.assertEqual((snap["state"], snap["display"]), ("detached", "paused"))

    def test_an_old_read_ready_pane_is_idle_not_your_turn(self):
        import sessions
        p = self._pane("ready")
        p.last_activity = time.time() - sessions.IDLE_DISPLAY_S - 1
        self.assertEqual(p.snapshot()["display"], "idle")

    def test_the_javascript_mirror_answers_the_same_case_table(self):
        """app.js's mirror answers the core's case table (skipped loudly without node)."""
        _run_node_selftest(self, "selftest_display.mjs",
                           "the browser's copy of display_state")


def _run_node_selftest(case, name, what):
    """Run one .mjs selftest as a subprocess. Skips LOUDLY when node is absent:
    a check that did not run must not read as a check that passed."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest(
            f"node absent: {name} did NOT run, so {what} is unverified here")
    r = subprocess.run([node, str(ROOT / name)],
                       capture_output=True, text=True, timeout=60)
    case.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class ThePillSaysOnlyWhatIsTrue(unittest.TestCase):
    """The wire's `rail` key lets the pill tell vendor-decided, fail-closed and
    no-tools lanes apart.
    """

    def test_the_wire_carries_rail(self):
        import sessions
        p = TheWireCarriesTheDisplayProjection._pane(self, "ready")
        self.assertIn("rail", p.snapshot())
        self.assertFalse(p.snapshot()["rail"])
        try:
            sessions.AGENTS["claude"]["rail"] = True
            self.assertTrue(p.snapshot()["rail"])
        finally:
            sessions.AGENTS["claude"].pop("rail", None)

    def test_the_chat_only_lane_declares_it_has_no_tools(self):
        """The `chat only` pill is derived from `tools`, which the ollama lane
        has always declared. If that ever flips, the pill must stop claiming
        there is nothing to ask about."""
        import sessions
        self.assertFalse(sessions.AGENTS["ollama"].get("tools"))
        self.assertFalse(sessions.AGENTS["ollama"].get("rail"),
                         "a lane with a rail must not also read as chat-only")

    def test_the_posture_pill_and_the_new_dialog_are_honest(self):
        _run_node_selftest(self, "selftest_posture.mjs",
                           "the posture pill and the New dialog")

    def test_own_branch_review_in_the_browser(self):
        """T-UI-1..14: the New row, the pill, parseUnified, the review dialog."""
        _run_node_selftest(self, "selftest_review.mjs",
                           "the own-branch row, pill and review dialog")

    def test_the_needs_you_rail_covers_every_kind_of_waiting(self):
        """T-INB-1..9: questions and paused panes in the rail, the phone's
        solo view, seen means on screen, the cross-feed dialog."""
        _run_node_selftest(self, "selftest_inbox.mjs",
                           "the Needs-you rail, solo view and cross-feed dialog")


class TheServiceInstallerResolvesAndStopsThere(unittest.TestCase):
    """install_service resolves every path from the running checkout and never
    enables or starts the service.
    """

    def _plan(self, platform, home):
        import install_service
        return install_service.plan(platform=platform, root=ROOT,
                                    python="/usr/bin/python3", home=home)

    def test_the_linux_unit_resolves_the_checkout_and_the_interpreter(self):
        with tempfile.TemporaryDirectory() as home:
            p = self._plan("linux", home)
            self.assertIn(f"ExecStart=/usr/bin/python3 {ROOT}/hub.py", p["text"])
            self.assertIn(f"WorkingDirectory={ROOT}", p["text"])
            self.assertTrue(Path(f"{ROOT}/hub.py").is_file(),
                            "ExecStart names a file that does not exist")
            self.assertTrue((ROOT).is_dir())
            self.assertNotIn("%HERE%", p["text"],
                             "the template placeholder survived — the reader "
                             "is back to running sed by hand")
            self.assertEqual(p["path"],
                             Path(home) / ".config/systemd/user/corral-light.service")

    def test_the_macos_plist_resolves_the_same_way_from_a_linux_host(self):
        """Rendered for darwin ON THIS HOST. The generator takes the platform
        rather than reading it, so the Mac answer is testable without a Mac —
        which is the only way the macOS path gets tested at all here."""
        with tempfile.TemporaryDirectory() as home:
            p = self._plan("darwin", home)
            self.assertIn(f"<string>{ROOT}/hub.py</string>", p["text"])
            self.assertIn(f"<string>{ROOT}</string>", p["text"])
            self.assertIn("/usr/bin/python3", p["text"])
            self.assertIn(str(Path(home) / "Library/Logs/corral-light.log"),
                          p["text"])
            self.assertEqual(
                p["path"],
                Path(home) / "Library/LaunchAgents/com.cvp1.corral-light.plist")

    def test_an_unknown_platform_refuses_and_names_itself(self):
        """A unit written into a directory that means nothing on that OS is worse
        than a refusal.
        """
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaises(SystemExit) as e:
                self._plan("sunos5", home)
            self.assertIn("sunos5", str(e.exception))

    def test_print_writes_nothing(self):
        import contextlib
        import io
        import install_service
        with tempfile.TemporaryDirectory() as home:
            target = install_service.plan(platform=sys.platform, root=ROOT,
                                          home=home)["path"]
            old = os.environ.get("HOME")
            os.environ["HOME"] = home
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = install_service.main(["--print"])
            finally:
                if old is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old
            self.assertEqual(rc, 0)
            self.assertFalse(target.exists(),
                             "--print wrote the file it promised only to show")
            self.assertFalse(target.parent.exists(),
                             "--print created the directory")
            self.assertIn("hub.py", buf.getvalue())

    def test_writing_it_enables_nothing_and_says_what_to_run(self):
        import contextlib
        import io
        import install_service
        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("HOME")
            os.environ["HOME"] = home
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = install_service.main([])
                out = buf.getvalue()
                target = install_service.plan(platform=sys.platform, root=ROOT,
                                              home=home)["path"]
                self.assertEqual(rc, 0)
                self.assertTrue(target.exists())
                self.assertIn(str(target), out, "it does not say what it wrote")
                self.assertIn("NOT enabled", out)
                self.assertIn("enable" if sys.platform.startswith("linux")
                              else "bootstrap", out,
                              "the enable command is not printed, so the "
                              "operator has to go and find it")
                # A re-run must not clobber an installed file someone edited.
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc2 = install_service.main([])
                self.assertEqual(rc2, 1)
                self.assertIn("already exists", err.getvalue())
            finally:
                if old is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old

    def test_the_generated_files_name_no_host_or_account(self):
        """The generated service files name no host or account; paths arrive at
        render time.
        """
        import re
        import install_service
        bad = re.compile(r"\b192\.168\.\d|/home/[a-z]|/Users/[a-z]")
        src = Path(install_service.__file__).read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            m = bad.search(line)
            self.assertIsNone(m, f"install_service.py:{i} names a host or "
                                 f"account in a public repository: "
                                 f"{line.strip()[:90]}")


class DoctorNamesTheStepNobodyMentioned(unittest.TestCase):
    """doctor names the missing `npm install` for the gitignored
    spike/node_modules adapters.
    """

    LANES = [{"key": "claude", "label": "Claude Code", "available": False,
              "why": "not installed: …/spike/node_modules/.bin/claude-agent-acp"}]

    def test_doctor_names_the_npm_step_when_the_adapters_are_absent(self):
        import doctor
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "spike").mkdir()
            (Path(tmp) / "spike" / "package.json").write_text("{}", encoding="utf-8")
            lines = doctor.report(root=tmp, agents=self.LANES)
        blob = "\n".join(lines)
        self.assertIn("npm install", blob,
                      "doctor does not name the command that fixes it")
        self.assertIn(str(Path(tmp) / "spike"), blob,
                      "doctor does not say WHERE to run it")
        self.assertIn("Claude Code", blob, "the lane list is gone")

    def test_doctor_is_quiet_about_npm_once_it_is_installed(self):
        """Edge-triggered: a note on every healthy run is a note nobody reads."""
        import doctor
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "spike" / "node_modules").mkdir(parents=True)
            (Path(tmp) / "spike" / "package.json").write_text("{}", encoding="utf-8")
            lines = doctor.report(root=tmp, agents=self.LANES)
        self.assertNotIn("npm install", "\n".join(lines))

    def test_an_incomplete_checkout_is_a_different_sentence(self):
        """No package.json at all is not a missing npm install — telling
        someone to run it there would send them down a dead end."""
        import doctor
        with tempfile.TemporaryDirectory() as tmp:
            problem = doctor.npm_problem(root=tmp)
        self.assertIn("no package.json", problem)
        self.assertNotIn("npm install", problem)

    def test_the_real_checkout_answers_too(self):
        """Against THIS tree, whatever state it is in — the function must not
        depend on a fixture to run at all."""
        import doctor
        self.assertIn(doctor.npm_problem(root=ROOT), (None,))

    def test_the_gemini_lane_names_the_platform_it_cannot_run_on(self):
        """The gemini lane's refusal names both the pinned platform and this host."""
        from install_antigravity_acp import platform_problem
        import platform as _p
        problem = platform_problem()
        if problem is None:
            import install_antigravity_acp as ia
            self.assertIsNotNone(ia.release_for(),
                                 "no platform problem reported on a host "
                                 "with no pinned row")
            raise unittest.SkipTest(
                f"this host ({_p.system()} {_p.machine()}) has a pinned row, "
                f"so the refusal cannot be observed here; the message's shape "
                f"is asserted on a fake below")
        self.assertIn(_p.system(), problem)
        self.assertIn(_p.machine(), problem)

    def test_the_platform_refusal_says_both_platforms(self):
        """Driven with a fake platform so it is checked on every host, not
        only on the Macs where it fires."""
        import install_antigravity_acp as ia
        real = ia.platform.system, ia.platform.machine
        try:
            ia.platform.system = lambda: "Darwin"
            ia.platform.machine = lambda: "x86_64"
            problem = ia.platform_problem()
        finally:
            ia.platform.system, ia.platform.machine = real
        self.assertIsNotNone(problem)
        # This host (Darwin x86_64) and what is pinned (Linux, arm64 among it).
        for needle in ("Darwin x86_64", "Linux", "arm64"):
            self.assertIn(needle, problem,
                          f"the refusal does not name {needle} — 'unavailable' "
                          f"without the platform invites an install that "
                          f"cannot work")

    def test_the_empty_state_and_the_shortcut_overlay(self):
        _run_node_selftest(self, "selftest_onboarding.mjs",
                           "the empty state and the ? overlay")


class TheSeatIsOnThePane(unittest.TestCase):
    """The seat in the header pill, withheld seats, ⌘K by seat, and the dialog
    in the shipped page.
    """

    def test_the_browser_side(self):
        _run_node_selftest(self, "selftest_seats.mjs", "the seat pill and ⌘K")


class ARigRendersPerSeat(unittest.TestCase):
    """The Rigs… dialog: one row per seat, every refusal reason, a two-click
    Remove, and both doors (New and ⌘K).
    """

    def test_the_browser_side(self):
        _run_node_selftest(self, "selftest_rigs.mjs", "the Rigs… dialog")


class APeerMessageRendersAsWhatItIs(unittest.TestCase):
    """A peer message renders as a peer message in the browser."""

    def test_the_browser_side(self):
        _run_node_selftest(self, "selftest_peer.mjs", "the peer block and reducer")


class AnAgentsQuestionRendersAsTheAgentAsking(unittest.TestCase):
    """ask_human in the browser: the banner, the roster preview, the
    transcript block and the reducer (selftest_ask.mjs)."""

    def test_the_browser_side(self):
        _run_node_selftest(self, "selftest_ask.mjs", "the ask_human banner and reducer")


class ModuleViewsInTheBrowser(unittest.TestCase):
    """The module seam's page side: every block renders as text, links are
    https with a host only, enums map to fixed classes (selftest_modules.mjs)."""

    def test_the_browser_side(self):
        _run_node_selftest(self, "selftest_modules.mjs", "module views, links and the module dialog")


class PairingByKeyInTheBrowser(unittest.TestCase):
    """DESIGN-6 S8, T8.1-T8.4: Touch your key only under all three
    conditions, base64url, the localhost link on 127.0.0.1, one fallback on
    NotAllowedError, and Security keys with no Remove (selftest_keypair.mjs)."""

    def test_the_browser_side(self):
        _run_node_selftest(self, "selftest_keypair.mjs", "pairing by key in the page")

    def test_t85_the_browser_files_name_no_host(self):
        """T8.5. The page, the README and the selftests ship in this PUBLIC
        repository too, and S8 put the key-pairing text in all three. Same
        rule as the core's guard. `user@example.com`-style placeholders are
        fine; a real host, LAN address or home path is not."""
        import re
        bad = re.compile(r"\b192\.168\.\d|/home/[a-z]|/Users/[a-z]")
        files = (sorted((ROOT / "static").glob("*.js")) + sorted((ROOT / "static").glob("*.html"))
                 + sorted((ROOT / "static").glob("*.css")) + sorted(ROOT.glob("selftest_*.mjs"))
                 + [ROOT / "README.md"])
        self.assertIn("selftest_keypair.mjs", [f.name for f in files])
        for f in files:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                m = bad.search(line)
                self.assertIsNone(
                    m, f"{f.name}:{i} names a host or account in a public "
                       f"repository: {line.strip()[:90]}")


from test_resilience import FakeLaneCase as _FakeLaneCase, wait_for as _wait_for  # noqa: E402


class LightTurnsHaveIds(_FakeLaneCase):
    """Turn ids come back from send() and ride on `user` and `turn_end`, even
    with no ledger; `via` is bounded.
    """

    def _ends(self, p):
        return [e for e in p.events if e["kind"] == "turn_end"]

    def test_send_returns_the_id_the_turn_end_carries(self):
        p = self.mgr.create("fake", self.agent_dir)
        a, b = p.send("first"), p.send("second")
        self.assertNotEqual(a, b)
        self.assertTrue(_wait_for(lambda: len(self._ends(p)) == 2))
        self.assertEqual([e["data"]["turn"] for e in self._ends(p)], [a, b])

    def test_a_pane_with_no_ledger_still_gets_an_id(self):
        """The null ledger returns None from accept(); a turn_end with no id
        cannot be matched to anything, so the core mints one instead."""
        import ledger
        p = self.mgr.create("fake", self.agent_dir)
        p._turns = lambda: ledger.NullLedger()
        tid = p.send("no ledger here")
        self.assertRegex(tid or "", r"^[0-9a-f]{12}$")
        self.assertTrue(_wait_for(lambda: self._ends(p)))
        self.assertEqual(self._ends(p)[-1]["data"]["turn"], tid)

    def test_via_is_carried_and_bounded(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.send("from the terminal", via="cli")
        self.assertEqual([e for e in p.events if e["kind"] == "user"][-1]["data"]["via"],
                         "cli")
        before = len(p.events)
        with self.assertRaises(ValueError):
            p.send("x", via="anything-a-script-likes")
        self.assertEqual(len(p.events), before, "a refused send still emitted")


class LightDeliversPeers(_FakeLaneCase):
    """Peer delivery through Light's own drain and ledger."""

    def pair(self):
        a = self.mgr.create("fake", self.agent_dir)
        b = self.mgr.create("fake", self.agent_dir)
        self.mgr.bind_seat(a.id, "author")
        self.mgr.bind_seat(b.id, "reviewer")
        return a, b

    def test_delivered_through_lights_drain(self):
        a, b = self.pair()
        r = self.mgr.deliver_peer(a.id, "reviewer", "hello from a peer")
        self.assertEqual(r["result"], "delivered", r)
        self.assertTrue(_wait_for(lambda: "turn_end" in self.kinds(b)[-3:]))
        self.assertNotIn("user", self.kinds(b)[-6:])
        self.assertIn('<corral-peer from="@author"', self.texts(b))
        self.assertEqual([e for e in b.events if e["kind"] == "turn_end"][-1]["data"]["turn"],
                         r["turn"])

    def test_the_ledger_names_a_peer_turn_and_recover_reports_it(self):
        """A peer turn is ledgered as kind: peer; one cut off by a restart is
        reported interrupted, never re-sent.
        """
        a, b = self.pair()
        r = self.mgr.deliver_peer(a.id, "reviewer", "ledger me")
        self.assertTrue(_wait_for(lambda: "turn_end" in self.kinds(b)[-3:]))
        rec = b._turns().turns()[r["turn"]]
        self.assertEqual((rec.get("kind"), rec.get("state")), ("peer", "completed"))
        tid = b._turns().accept("cut off by a restart", kind="peer")
        closed = b._turns().recover()
        self.assertEqual([(c["turn"], c.get("kind")) for c in closed], [(tid, "peer")])
        self.assertEqual(b._turns().turns()[tid]["state"], "interrupted")

    def test_a_card_after_admission_fails_the_peer_turn_in_lights_drain(self):
        """A card after admission fails the peer turn in Light's drain."""
        a, b = self.pair()
        with b._turn_lock:
            b._turn_running = True
        r = self.mgr.deliver_peer(a.id, "reviewer", "must not run")
        b.pending["late"] = {"title": "arrived after admission"}
        with b._turn_lock:
            b._turn_running = False
        import threading as _t
        _t.Thread(target=b._drain, daemon=True).start()
        self.assertTrue(_wait_for(lambda: "peer_result" in self.kinds(b)))
        res = [e for e in b.events if e["kind"] == "peer_result"][-1]["data"]
        self.assertEqual((res["turn"], res["reason"]), (r["turn"], "card-pending"))
        self.assertNotIn("must not run", self.texts(b))
        self.assertEqual(b._turns().turns()[r["turn"]]["state"], "interrupted")
        b.pending.clear()

    def test_a_broadcast_through_lights_drain_and_ledger(self):
        """Each broadcast seat gets its own ledgered peer turn; a refused seat
        leaves the delivered ones standing.
        """
        a, b = self.pair()
        c = self.mgr.create("fake", self.agent_dir)
        self.mgr.bind_seat(c.id, "critic")
        c.pending["r1"] = {"title": "t"}
        out = self.mgr.broadcast_peer(a.id, "to everyone")
        c.pending.clear()
        self.assertEqual([(r["to_seat"], r["result"]) for r in out["results"]],
                         [("critic", "refused"), ("reviewer", "delivered")])
        tid = out["results"][1]["turn"]
        self.assertTrue(_wait_for(lambda: "turn_end" in self.kinds(b)[-3:]))
        self.assertIn("to everyone", self.texts(b))
        self.assertNotIn("peer", self.kinds(c))
        rec = b._turns().turns()[tid]
        self.assertEqual((rec.get("kind"), rec.get("state")), ("peer", "completed"))

    def test_a_wait_on_a_ledgered_turn_ends_with_its_turn_end(self):
        """The turn id minted at admission is the one turn_end carries, so the
        sender's wait ends.
        """
        a, b = self.pair()
        r = self.mgr.deliver_peer(a.id, "reviewer", "tell me when")
        self.assertEqual(r["result"], "delivered", r)
        self.assertTrue(_wait_for(lambda: self.mgr.peer_turn(
            a.id, "reviewer", r["turn"])["ended"]))
        self.assertEqual(b._turns().turns()[r["turn"]].get("state"), "completed")
        self.assertEqual(self.mgr.peer_turn(b.id, "author", r["turn"])["reason"],
                         "unknown-turn")


# The bounded reply queue through Light's drain and ledger (shared cases).
sys.path.append(str(Path(__file__).resolve().parent / "testkit"))
from reply_queue_cases import ReplyQueueCases     # noqa: E402


class LightReplyQueue(ReplyQueueCases, _FakeLaneCase):
    def test_the_delivered_reply_is_ledgered_as_a_peer_turn(self):
        a, b, ta, tb = self.waiting_pair()
        self.assertEqual(self.mgr.deliver_peer(b.id, "author", "ledger me")["result"],
                         "queued")
        self.assertTrue(_wait_for(lambda: len(self._ends(a)) == 2))
        tid = [e for e in a.events if e["kind"] == "peer"][0]["data"]["turn"]
        rec = a._turns().turns()[tid]
        self.assertEqual((rec.get("kind"), rec.get("state")), ("peer", "completed"))


# Rigs: cases shared with full Corral.
from rig_cases import RigCases                   # noqa: E402


class LightRigs(RigCases, _FakeLaneCase):
    def test_this_skin_injects_its_roles_and_its_roster_cap(self):
        import sessions
        self.assertIs(sessions._core.ROLE_RESOLVER, sessions._rig_role)
        self.assertEqual(sessions._core.ROSTER_CAP, sessions.MAX_ROSTER)


class BackgroundPanes(_FakeLaneCase):
    """Bulk spawners (rigs, panels, evals, schedules) start panes minimized;
    a background pane restores itself when it needs the operator."""

    def setUp(self):
        super().setUp()
        import queue
        self.q = queue.Queue()
        self.mgr.subscribe(self.q)
        rigs_dir = rigs_mod().rigs_dir()
        import shutil
        shutil.rmtree(rigs_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rigs_dir, True)

    def layouts(self, pane):
        out = []
        while not self.q.empty():
            ev = self.q.get_nowait()
            if ev.get("kind") == "layout" and ev.get("pane") == pane.id:
                out.append(ev["data"])
        return out

    def test_a_background_pane_starts_minimized_and_says_so(self):
        p = self.mgr.create("fake", self.agent_dir, background=True)
        self.assertEqual(p.state, "ready", p.error)
        self.assertTrue(p.minimized and p.background)
        snap = p.snapshot()
        self.assertTrue(snap["minimized"] and snap["background"])
        meta = json.loads((p.dir / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["minimized"] and meta["background"])
        plain = self.mgr.create("fake", self.agent_dir)
        self.assertFalse(plain.minimized or plain.background)

    def test_a_permission_restores_a_background_pane(self):
        p = self.mgr.create("fake", self.agent_dir, background=True)
        self.layouts(p)
        p.send("perm")
        self.assertTrue(_wait_for(lambda: p.pending), "no permission arrived")
        self.assertTrue(_wait_for(lambda: not p.minimized))
        self.assertFalse(p.background)
        self.assertIn({"minimized": False, "background": False, "pinned": False,
                       "order": None}, self.layouts(p))
        notes = [e["data"]["text"] for e in p.events if e["kind"] == "note"]
        self.assertTrue(any("restored from the background" in n for n in notes),
                        notes)

    def test_a_pane_the_operator_minimized_stays_minimized(self):
        p = self.mgr.create("fake", self.agent_dir, background=True)
        p.set_minimized(True)           # the operator's own choice now
        self.assertFalse(p.background)
        p.send("perm")
        self.assertTrue(_wait_for(lambda: p.pending), "no permission arrived")
        self.assertTrue(p.minimized, "an operator-minimized pane was restored")

    def test_a_question_restores_a_background_pane(self):
        p = self.mgr.create("fake", self.agent_dir, background=True)
        p.ask("which branch?")
        self.assertFalse(p.minimized or p.background)

    def test_a_pane_that_already_needs_the_operator_is_not_hidden(self):
        p = self.mgr.create("fake", self.agent_dir)
        p.ask("which branch?")
        self.assertFalse(p.to_background())
        self.assertFalse(p.minimized)

    def test_the_flag_survives_a_restart_only_while_minimized(self):
        import sessions
        p = self.mgr.create("fake", self.agent_dir, background=True)
        meta = json.loads((p.dir / "meta.json").read_text(encoding="utf-8"))
        q = sessions.Pane.from_meta(meta, self.mgr)
        self.assertTrue(q.minimized and q.background)
        meta["minimized"] = False
        q = sessions.Pane.from_meta(meta, self.mgr)
        self.assertFalse(q.background)

    def test_rig_up_minimizes_its_seats_unless_told_otherwise(self):
        rigs = rigs_mod()
        a, b = f"bg-{uuid.uuid4().hex[:6]}", f"fg-{uuid.uuid4().hex[:6]}"
        d = rigs.rigs_dir()
        d.mkdir(parents=True, exist_ok=True)
        for name, seat in (("bgrig", a), ("fgrig", b)):
            (d / f"{name}.toml").write_text(rigs.compose(
                [{"id": seat, "agent": "fake", "cwd": self.agent_dir}]),
                encoding="utf-8")
        pa = self.mgr.panes[rigs.up(self.mgr, "bgrig")["outcomes"][0]["pane"]]
        self.assertTrue(pa.minimized and pa.background)
        out = rigs.route(self.mgr, "POST", "/api/session/rigs/up",
                         {"name": "fgrig", "background": False})
        pb = self.mgr.panes[out[1]["outcomes"][0]["pane"]]
        self.assertFalse(pb.minimized or pb.background)

    def test_the_spawners_ask_for_the_background(self):
        root = ROOT
        hub = (root / "hub.py").read_text(encoding="utf-8")
        self.assertIn('background=b.get("background") is True', hub)
        self.assertIn('"background": True', (root / "lane_matrix.py")
                      .read_text(encoding="utf-8"))
        self.assertIn("background=True", (root / "schedule.py")
                      .read_text(encoding="utf-8"))


class AgentsKnowTheirPane(_FakeLaneCase):
    def test_the_agent_process_carries_its_pane_id(self):
        p = self.mgr.create("fake", self.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        environ = Path(f"/proc/{p.client.p.pid}/environ")
        if not environ.exists():
            self.skipTest("no /proc here")
        env = dict(x.split("=", 1) for x in
                   environ.read_bytes().decode().split("\0") if "=" in x)
        self.assertEqual(env.get("CORRAL_PANE_ID"), p.id)


def rigs_mod():
    from corral_core import rigs
    return rigs


# ask_human cases through Light's own send(), drain and ledger.
from ask_cases import AskCases                   # noqa: E402


class LightAskHuman(AskCases, _FakeLaneCase):
    pass


# The hop limit raises itself to the human (testkit/hop_pause_cases.py).
from hop_pause_cases import HopPauseCases        # noqa: E402


class LightHopPause(HopPauseCases, _FakeLaneCase):
    pass


# The resilience suite: real agent processes through kill, resume,
# shutdown and restore.
from test_resilience import *                    # noqa: F401,F403,E402
from test_stop_and_clear import *                # noqa: F401,F403,E402
from test_worktrees import *                     # noqa: F401,F403,E402
# 2026-09-30: the Claude login foreseen (claude_auth) and survived (auth_sweep).
from test_claude_auth import *                   # noqa: F401,F403,E402
from test_cli import *                           # noqa: F401,F403,E402
from test_update import *                        # noqa: F401,F403,E402
from test_ports import *                         # noqa: F401,F403,E402
# Seats: the forked half (restore/reopen/from_meta/snapshot).
from test_seats import *                         # noqa: F401,F403,E402
# Every consumer of the `peer` kind (port pack, index, digest).
from test_peer_consumers import ThePortPack, TheIndex   # noqa: F401,E402
# The seat tools' routes on this hub, over a real socket.
from test_seat_routes import Routes as SeatRoutes      # noqa: F401,E402
# Own-branch worktree routes (WS3), over a real socket.
from test_worktree_routes import WorktreeRoutes        # noqa: F401,E402
# `corral-light worktrees`: list, restore, purge, resolve (WS5.1).
from test_worktrees_cli import (TheList, TheRestore, ThePurge, TheResolve,  # noqa: F401,E402
                                TheDoctor, TheDispatch)
# 10x UX Part B: "Review at the end", the sealed-snapshot permission policy.
from test_review_at_end import ThePolicy, TheFlag, TheCreateRoute  # noqa: F401,E402
# 10x UX Part C: the blind challenge.
from test_challenge import (Refusals, TheRun, ThePrompt, ReviewerModes,  # noqa: F401,E402
                            Sandboxed as ChallengeSandboxed, TheRoute as ChallengeRoute)
# The reviewer sandbox: egress policy, sign-in lifetimes, the proxy.
from test_review_sandbox import (EgressPolicy, SignInLifetime, TheGate,  # noqa: F401,E402
                                 TheArgv as SandboxArgv, TheProxy as EgressProxy,
                                 Availability as SandboxAvailability)
# The module seam (docs/finops-module-plan.md §4, §8.1).
from test_modules import (TheManifest, InstallAndPin, Tamper, UpdateAndRollback,  # noqa: F401,E402
                          TheRunner as ModuleRunner, Isolation as ModuleIsolation,
                          Unsandboxed as ModuleUnsandboxed, TheSnapshot as ModuleSnapshot,
                          TheRoutes as ModuleRoutes, TheDispatch as ModuleDispatch,
                          Locks as ModuleLocks, Limits as ModuleLimits,
                          TheNoticeField as ModuleNoticeField,
                          TheNoticeManifest as ModuleNoticeManifest,
                          TheNotices as ModuleNotices, TheNoticeRoute as ModuleNoticeRoute)
# Module fetchers: keys, grants, the exact-host proxy, the fetch sandbox (§6.7).
from test_module_fetch import (ExactHosts, TheFetcherManifest, Keys,  # noqa: F401,E402
                               Grants as FetchGrants, Scrubbing as FetchScrubbing,
                               NoSandboxNoFetch, Runs as FetchRuns,
                               TheFetchProxy, TheGoogleToken)
# The module feed, Claude quota capture, the adapter patch, login facts.
from test_module_feed import (FeedFiles, QuotaCapture, TurnEndCarriesQuota,  # noqa: F401,E402
                              AdapterPatch, LoginFacts, QuotaMerge,
                              PaneTitles as FeedPaneTitles)
# Core-run vendor reports (grok usage) in their own sandbox.
from test_vendor_reports import (SandboxedRuns as GrokReportRuns,  # noqa: F401,E402
                                 BinaryCheck as GrokBinaryCheck,
                                 SandboxUnavailable as GrokReportUnavailable,
                                 Reduce as GrokReportReduce)
# T-ISO-1: the own-branch suites, rerun with HOME and TMPDIR in a temp dir.
from test_worktrees_iso import Isolation as WorktreeIsolation  # noqa: F401,E402


if __name__ == "__main__":
    unittest.main(verbosity=2)
