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
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

import ollama_acp


def _config_dir_only(spec):
    """The claude lane as it was BEFORE the ACP `mode` route existed: posture
    imposable only through CLAUDE_CONFIG_DIR.

    The three tests below were written when that was the only mechanism, so
    they said "the config dir is unusable here" by asserting
    `posture_enforceable` is False. That is no longer the same sentence: the
    lane can now impose a posture over session/set_config_option, so the
    overall answer is legitimately True on a host where the config dir is
    dead. Narrowing the spec keeps each test pointed at the mechanism it was
    actually written to guard — the Keychain finding of 2026-08-31 is still
    fully asserted — instead of quietly asserting the new route away.
    """
    narrowed = dict(spec)
    narrowed.pop("posture_via_acp_mode", None)
    return narrowed


def _pin_sessions_platform(test, name):
    """Force sessions.sys.platform for the rest of this test.

    Isolation via CLAUDE_CONFIG_DIR is a platform fact (darwin refuses it).
    Tests of the copy/resync/chmod path must pin linux; tests of the
    refusal must pin darwin. Leaving them on the host's platform is how
    the suite went red on dogma-2 the day the Keychain finding landed.
    """
    import sessions
    real = sessions.sys.platform
    sessions.sys.platform = name
    test.addCleanup(lambda: setattr(sessions.sys, "platform", real))


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
        """The fleet modules are absent from the tree AND from every import.

        `ssh_acp` LEFT this list on 2026-09-01, deliberately and with Craig's
        go: the host shell lanes came back. It is the one heavy module whose
        dependency was never the fleet — ranch generated its lane list from the
        lightsail estate, but the adapter itself needs only ssh and a bash, so
        it ports to a standalone host intact. Light supplies the inventory from
        a hand-written ssh-hosts.json instead (sessions.EXTRA_SSH_HOSTS), and
        `estate`/`fleet`/`lightsail` stay banned above — which is what keeps
        this an added lane rather than the fork quietly reconverging.

        Everything else on this list still has a fleet-shaped dependency and
        stays out. Moving a name off this list is a decision, not a fix.
        """
        heavy = ("fleet", "estate", "finops", "runs", "attention", "asks",
                 "push", "schedule", "library", "mail", "today", "residency",
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
        allowed_prefixes = ("/api/session/", "/api/pair/", "/api/content/")
        allowed_exact = {"/health", "/", "/index.html", "/sw.js",
                         "/manifest.json", "/api/state", "/api/stream",
                         "/api/search"}
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

    def test_nothing_hardcodes_a_path_from_the_machine_it_was_built_on(self):
        """No `/home/<someone>`, no `/Users/<someone>`, outside the plist.

        This is the whole "does it work on a blank box" question in one test.
        It was NOT hypothetical: app.js shipped `S.lastCwd ||
        '/home/cvande/Github/CC'` as the new-conversation default, inherited
        from the full Corral, so the first thing a fresh install did was refuse
        to start a pane in a directory that does not exist there. The host
        knows its own home; nothing here should be guessing at it.
        """
        import re
        # This file is excluded, and only this file: it is the one place whose
        # CONTENT is the rule, so it quotes the literal it forbids. Same trap
        # the CC-workspace scanner fell into — a check that cannot survive its
        # own documentation gets deleted the first time it cries wolf.
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
        """Every per-user config path is corral-light's own.

        A shared MCP config would mean a server added on the fleet host
        silently appears in every pane here, on a machine that may have
        neither its credential nor a network path to it.
        """
        for name in ("mcp.py", "codex_launcher.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                self.assertNotIn('.config/corral/', s,
                                 f"{name}:{i} shares the full Corral's config")

    def test_every_print_flushes(self):
        """Under a service manager, stdout is block-buffered, not line-buffered.

        The startup banner — the one signal that the server bound its port —
        sat in an 8 KB buffer and never reached the log. Measured from a fresh
        clone: a healthy server with a zero-byte log file, which reads exactly
        like a server that failed to start. Every diagnostic print here is
        read by someone through `systemctl --user status` or a log file, never
        through a terminal.
        """
        # ast, not string matching. The first cut walked the source counting
        # parentheses and its depth counter was already 0 before it reached
        # the opening one, so every call "ended" at the word `print` and the
        # check compared the flag against the literal string 'print'. It
        # failed loudly here rather than passing vacuously, which is the only
        # reason it got fixed instead of shipped.
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
        """Both builds must be able to run on ONE host without touching.

        Ports, cookies, MCP config and codex home were all namespaced from the
        start; `antigravity_acp.py` was not. It arrived from upstream still
        reading CORRAL_STATE and deriving CATALOG from it — so on a host
        running both, this lane wrote its session records into the OTHER
        product's directory and seeded its model picker from the OTHER hub's
        negotiated catalog. Found 2026-08-31 by auditing that exact question.

        Checks the whole tree, because the leak came in with a vendored file
        and the next one will too.
        """
        for f in self.PY_FILES:
            if f.name == Path(__file__).name:
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                self.assertNotIn('"CORRAL_STATE"', s,
                                 f"{f.name}:{i} reads the full Corral's state var")
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

    def test_dotdirs_are_not_indexed(self):
        """A `.hidden/secret.md` matching the query must not surface.

        Vaults carry `.obsidian`, `.trash` and `.git`; indexing those puts
        deleted notes and plugin config into a search box that looks like it
        is showing you your notes.
        """
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
        """Eviction is accretion's other half (P23) — a store that only grows
        keeps answering with files that are gone."""
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
        """The reason mdview.py could stay deleted.

        Notes carry pasted third-party text and this is an authed control
        surface (P20). The snippet is the only file-derived string that
        reaches the page, and el() sets textContent — so an innerHTML
        assignment fed by a hit would be the whole argument collapsing.
        """
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
    """A lane must not report available on a host that cannot run it.

    Craig ran `doctor` on a Mac 2026-08-31; Antigravity showed the honest
    "not installed". The obvious next step — `install_antigravity_acp.py
    --install` — would have downloaded the pinned LINUX x86-64 archive,
    verified its SHA correctly, installed it, and then doctor would have
    reported the lane **ok** for a binary that cannot exec there. Worse than
    the honest failure it replaced, because the operator stops looking.
    """

    def test_the_pinned_release_names_its_platform(self):
        import install_antigravity_acp as m
        self.assertEqual(m.PLATFORM, ("Linux", "x86_64"))
        self.assertIn("linux", m.URL)

    def test_install_refuses_on_the_wrong_platform(self):
        import install_antigravity_acp as m
        real_system, real_machine = m.platform.system, m.platform.machine
        try:
            m.platform.system, m.platform.machine = (lambda: "Darwin"), (lambda: "arm64")
            self.assertIsNotNone(m.platform_problem())
            with self.assertRaises(RuntimeError) as cm:
                m.install(Path(tempfile.gettempdir()) / "corral-light-never")
            self.assertIn("Darwin", str(cm.exception))
        finally:
            m.platform.system, m.platform.machine = real_system, real_machine

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


class PrintedCommandsWork(unittest.TestCase):
    """A command shown to a human is a promise that running it does the thing.

    Measured on dogma-2, 2026-08-31: `doctor` told Craig to run
    `CODEX_HOME=… codex login --device-auth`, and codex refused —
    "CODEX_HOME points to '…', but that path does not exist". The directory is
    only created at pane-spawn time, which cannot have happened yet, because
    not being logged in is exactly why the message is on screen. The lane
    printed an instruction that could never work as pasted.
    """

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
    """Installed is not the same question as usable.

    Three separate times this build shipped a lane that reported available
    and then died on its first prompt — antigravity (wrong platform), codex
    (a login command that could not run), and grok (`ok` from a resolver whose
    own docstring says it does not probe auth; Craig hit `Authentication
    required` on dogma-2, 2026-08-31). available_agents() exists to stop a
    picker listing a binary that is not installed; a binary that is installed
    and cannot authenticate is the same lie one layer in.
    """

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
    """The new-conversation dialog must work BEFORE anything has run.

    All three of Craig's dogma-2 symptoms on 2026-08-31 were this: a Claude
    pane that died with `Authentication required`, no model to pick, no effort
    to pick. One cause — a lane's model/effort lists come only from a
    completed session/new, so a lane that cannot authenticate can never fill
    its own pickers, and the first thing an operator does on a new box is open
    that dialog.
    """

    def test_the_claude_lane_is_probed_live_not_guessed_at(self):
        """A credential-FILE check is a guess about where a vendor keeps its
        secret, and for Claude on macOS that guess is wrong (Keychain). The
        only portable answer is to run the handshake."""
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
    """A client going away is not an incident; a bug still is.

    dogma-2, 2026-08-31: the log filled with ConnectionResetError tracebacks
    raised inside handle_one_request's `rfile.readline` — before any of this
    code runs, which is why Handler's own guards never saw them. That is the
    normal end of a browser preconnect, an abandoned SSE stream, and every
    keep-alive socket a laptop takes with it when it sleeps.

    A cockpit that prints a stack trace for the routine case teaches its
    operator that stack traces are routine, and the next one — which is real
    — gets scrolled past. So: silence exactly the "peer left" exceptions and
    nothing else. This test exists to keep that list from growing.
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
    """The directory that gives a pane its posture must not cost it its login.

    dogma-2, 2026-08-31: `claude` worked in a terminal, and every Corral pane
    died at its first prompt with `Authentication required`. A pane runs under
    a private CLAUDE_CONFIG_DIR seeded by copying
    ~/.claude/.credentials.json — and on macOS that file need not exist, because
    Claude Code can keep the OAuth in the Keychain. So the private dir was
    created with no credential in it and the agent could not authenticate. The
    pane was broken by the very mechanism that exists to give it a posture.
    """

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
        """The normal path must be unchanged — this is a fallback, not a
        replacement for the posture mechanism.

        Note the payload: `{}` used to be enough here, because the rule was
        "the file exists". It is now "the file carries a token", so this test
        had to say what a real credential looks like. That is the contract
        change made visible, which is what a test is for.
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
    """The login the operator verified must be the one the agent uses.

    dogma-2, 2026-08-31, in the order Craig found them: logged in; verified he
    was logged in; ran /usage and got token STATISTICS instead of the
    subscription usage page; the next prompt failed `Authentication required`.
    A statistics page instead of a subscription page is what API-key mode
    looks like — the agent was never using the login he had just checked.

    acp.AcpClient merges this process's whole environment into the child,
    which is right for PATH and HOME and wrong for a credential: an
    ANTHROPIC_API_KEY exported in the shell that started the hub outranks the
    OAuth login silently. codex_launcher has stripped these prefixes since
    2026-08-23 with a comment naming the case; the Claude lane — the one
    everybody uses — never got the same guard.
    """

    def test_stripped_vars_do_not_reach_the_child_process(self):
        """Measured at the process boundary, not asserted about a dict."""
        import acp, sessions, tempfile, sys as _sys, time
        spy = Path(tempfile.mkdtemp()) / "spy.py"
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

    def test_both_spawn_sites_strip(self):
        """start() and resume() are two doors into the same room; a guard on
        one of them is a guard on neither."""
        sess = (ROOT / "sessions.py").read_text(encoding="utf-8")
        self.assertEqual(sess.count("strip_env=strip_prefixes()"), 2,
                         "start() and resume() must both strip")

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
    """A pane must not inherit another Claude Code session's identity.

    MEASURED 2026-08-31 — the first thing in this investigation that was
    measured rather than proposed. Running `corral-light diagnose` from inside
    a Claude Code session showed eleven CLAUDE_* variables from the PARENT
    session reaching the spawned agent, CLAUDE_CONFIG_DIR among them.

    That last one is the sharp edge, and it interacts with the fallback added
    two commits earlier: Corral sets CLAUDE_CONFIG_DIR when it can impose a
    posture and deliberately does NOT set it when it cannot. In exactly that
    case an inherited value wins — so a hub started from inside a Claude Code
    session would hand every pane the parent's config directory. "Do not set
    it" only means "use the default" when nothing else is setting it.
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
        """GROK_AGENT / GROK_SESSION_ID are this process's session, not a
        vendor API key. Treating the GROK_ prefix as a credential made
        `doctor` nag inside a Grok TUI session about variables nobody
        exported as a secret."""
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
    """The instrument built after three unmeasured theories in a row."""

    def test_it_prompts_which_is_the_step_doctor_skips(self):
        import diagnose, inspect
        src = inspect.getsource(diagnose.diagnose)
        self.assertIn("client.prompt", src,
                      "doctor already answers 'can it start'; this command "
                      "exists to answer 'does a turn run'")

    def test_it_surfaces_adapter_stderr(self):
        """acp.py has always captured this and only ever used the last line
        inside an exit reason — the detail behind every failure in this saga
        was collected and thrown away."""
        import diagnose, inspect
        self.assertIn("stderr_tail", inspect.getsource(diagnose.diagnose))

    def test_it_runs_a_positive_control(self):
        """Narrowing suspects is not the same as settling the question. The
        control re-runs with ONE variable removed — the private config dir,
        which is the only thing Corral adds to a terminal that already works."""
        import diagnose, inspect
        src = inspect.getsource(diagnose._control)
        self.assertIn("_run_once(spec, cwd, None", src,
                      "the control must run WITHOUT the private config dir")

    def test_the_credential_shape_reports_lengths_not_values(self):
        """A token's LENGTH is diagnostic; its content is not. Craig's file is
        322 bytes where a working one is 508 — knowing which field is missing
        is the difference between a theory and a fact."""
        import diagnose, tempfile, io, contextlib
        d = Path(tempfile.mkdtemp()) / "c.json"
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
        d = Path(tempfile.mkdtemp()) / "c.json"
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
    """The measured root cause, found on ranch-server 2026-08-31.

    ~/.claude/.credentials.json existed, parsed, and carried every expected
    key — accessToken, refreshToken, expiresAt, scopes, subscriptionType —
    with accessToken="" , refreshToken="" and expiresAt=0. A file that is
    complete by every structural test and authenticates nothing.

    That defeats every check this build made before it. `is_file()` passes.
    The copy into the pane's private CLAUDE_CONFIG_DIR succeeds. The
    directory then LOOKS credentialed, the handshake succeeds because it does
    not authenticate, and the first prompt fails. Four theories died on this
    because all four asked whether the file was THERE.
    """

    def _cred(self, payload):
        d = Path(tempfile.mkdtemp()) / ".credentials.json"
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
        bad = Path(tempfile.mkdtemp()) / "c.json"
        bad.write_text("{ not json")
        self.assertFalse(sessions.usable_credential(bad))

    def test_token_names_are_matched_at_any_depth(self):
        """The vendor's key names and nesting are theirs to change; asserting
        a shape would be one more guess of the kind that cost four rounds."""
        import sessions
        self.assertTrue(sessions.usable_credential(self._cred(
            {"a": {"b": {"c": {"access_token": "z" * 40}}}})))

    def test_an_unusable_credential_means_no_private_config_dir(self):
        """The whole point: refuse the directory rather than build one that
        only looks credentialed."""
        import sessions
        home = Path(tempfile.mkdtemp())
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
    """The measured root cause, round two — found by the positive control.

    dogma-2, 2026-08-31: the control settled that this WAS the private config
    dir, but the credential in the copy was real (108 chars, a genuine future
    expiresAt) — ruling out both earlier theories (missing file, empty
    token). What was left: seed_config_dir() copied the credential exactly
    ONCE, ever, guarded by `if not dst.is_file()`. diagnose and the picker's
    probe both reuse ONE FIXED directory across every invocation, so a copy
    made the first time that directory existed is frozen from that moment —
    and Claude Code rotates OAuth tokens (Craig's own terminal: "login
    expires in 2 days"), so a frozen copy's refresh token goes invalid at the
    auth server while looking, structurally, exactly like a working one.
    """

    def _home_with_cred(self, token="a"):
        home = Path(tempfile.mkdtemp())
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

    def test_an_unrotated_source_is_left_alone(self):
        """Re-copying on every call, unconditionally, would be simpler and
        wrong: a running pane may have refreshed ITS OWN copy more recently
        than the source, and clobbering that with an older global file would
        actively break a working pane."""
        import sessions
        _pin_sessions_platform(self, "linux")
        home, cred = self._home_with_cred("a")
        real = Path.home
        try:
            Path.home = staticmethod(lambda: home)
            d = sessions.seed_config_dir(home / "cfg", "auto")
            before = (d / ".credentials.json").stat().st_mtime
            sessions.seed_config_dir(home / "cfg", "auto")   # called again
            after = (d / ".credentials.json").stat().st_mtime
            self.assertEqual(before, after, "an unchanged source was re-copied")
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
            self.assertIn("a" * 20, (d2 / ".credentials.json").read_text())
        finally:
            Path.home = real


class TheStaleCopyTheoryWasWrong(unittest.TestCase):
    """Recorded honestly: fix #2 (stale-copy resync) did not resolve Craig's
    failure. His `expiresAt` was IDENTICAL across the run before and after
    that fix shipped — proof the source token had not rotated at all, so
    there was nothing for a resync to fix. The mechanism was never staleness.

    What survived: byte-identical, valid-looking credentials, still failing
    ONLY when read through the private config dir rather than ~/.claude
    directly. That points at the directory, not the file's content — so the
    next instruments are a permission audit and a content-equality proof,
    and the next code change (locking the dir to 0700) is defensive
    hardening offered honestly as unproven, not as another confident theory.
    """

    def test_the_config_dir_is_locked_to_the_owner(self):
        """Measured 2026-08-31: plain mkdir left it at 0o775 on this host —
        group AND world read/execute on a directory built to carry a copied
        OAuth token. Some credential-handling CLIs refuse to trust a token in
        a loosely-permissioned directory even when the file itself is
        locked down, the way ssh refuses a loose ~/.ssh."""
        import sessions
        _pin_sessions_platform(self, "linux")
        home = Path(tempfile.mkdtemp())
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
        home = Path(tempfile.mkdtemp())
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
    """The instruments built after fix #2 turned out not to be the answer."""

    def test_it_compares_real_and_private_permissions(self):
        import diagnose, inspect
        src = inspect.getsource(diagnose)
        self.assertIn("_permission_audit", src)
        self.assertIn("_content_equality", src)

    def test_content_equality_never_prints_the_hash_or_the_bytes(self):
        import diagnose, tempfile as tf, io, contextlib
        a = Path(tf.mkdtemp()) / "a.json"; a.write_text("secret-value-a")
        b = Path(tf.mkdtemp()) / "b.json"; b.write_text("secret-value-a")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            diagnose._content_equality(a, b)
        out = buf.getvalue()
        self.assertIn("yes", out)
        self.assertNotIn("secret-value-a", out)

    def test_content_equality_detects_a_real_difference(self):
        import diagnose, tempfile as tf, io, contextlib
        a = Path(tf.mkdtemp()) / "a.json"; a.write_text("one")
        b = Path(tf.mkdtemp()) / "b.json"; b.write_text("two")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            diagnose._content_equality(a, b)
        self.assertIn("DIFFERS", buf.getvalue())


class DarwinKeychainMakesIsolationImpossible(unittest.TestCase):
    """The confirmed root cause — read from the vendor's own source, not
    inferred, after five theories that were.

    spike/node_modules/@anthropic-ai/claude-agent-sdk/cli.js:

        function Kg(A=""){
          let q=O8();
          let Y = !process.env.CLAUDE_CONFIG_DIR ? "" :
                  `-${sha256(q).digest('hex').substring(0,8)}`;
          return `Claude Code${D4().OAUTH_FILE_SUFFIX}${A}${Y}`
        }

    the macOS Keychain service-name generator used with
    `security find-generic-password`. Setting CLAUDE_CONFIG_DIR — to ANY
    value — switches the lookup to a suffixed service name no interactive
    `claude login` has ever provisioned. Craig's positive control proved the
    credential ITSELF was real and byte-identical on both sides of the
    failure; this is why that made no difference — it was never a
    credential-content problem. This finding is what the empty-token and
    stale-copy fixes, both plausible and both wrong, were reaching for.
    """

    def _patch_darwin(self):
        import sessions
        real = sessions.sys.platform
        sessions.sys.platform = "darwin"
        self.addCleanup(lambda: setattr(sessions.sys, "platform", real))

    def _fake_home_with_real_credential(self):
        home = Path(tempfile.mkdtemp())
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
            Path(tempfile.mkdtemp()) / "cfg", "auto"))
        self.assertFalse(sessions.posture_enforceable(
            _config_dir_only(sessions.AGENTS["claude"])))
        # ...and yet the lane as it really is DOES have a posture here, over
        # ACP. Both halves asserted together, because the pair is the whole
        # 2026-09-01 finding: the platform quirk is real and it was never the
        # only road.
        self.assertTrue(
            sessions.posture_enforceable(sessions.AGENTS["claude"]))

    def test_linux_is_unaffected(self):
        """The fix must be scoped to the platform that actually has this
        Keychain quirk — not a blanket new restriction everywhere."""
        import sessions
        _pin_sessions_platform(self, "linux")
        self._fake_home_with_real_credential()
        self.assertIsNotNone(sessions.seed_config_dir(
            Path(tempfile.mkdtemp()) / "cfg", "auto"))
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
                pass          # the handshake itself will fail in this sandbox;
                              # only the CREDENTIAL/CONFIG section is under test
        out = buf.getvalue()
        self.assertIn("Keychain", out)
        self.assertIn("CLAUDE_CONFIG_DIR", out)


class EveryUiCallHasADefinition(unittest.TestCase):
    """A function called from a click handler but never defined does not
    throw at build time — `node --check` only parses syntax, it does not run
    the file — so it ships silently and dies the first time someone clicks.

    Measured 2026-08-31: `setMin` is called from five places (the roster row,
    the pane header, the minbar chip, the rail's restore action, the palette
    focus fallback) and was defined in NONE of them. It sat right next to
    loadAttention/loadFleet/askResolve in the full Corral's app.js, and the
    cut that removed those three took setMin with them while leaving every
    caller intact. "Can't minimize panes" was the first anyone noticed.

    THIS TEST'S OWN FIRST VERSION HAD THE SAME CLASS OF BUG IT WAS BUILT TO
    CATCH. It stripped string and template literals with a regex
    (`` `(?:[^`\\]|\\.)*` ``) that is not safe against this file's own
    content — one unbalanced or nested backtick collapses the match across
    everything between two UNRELATED template literals, and here it ate 85%
    of the file (65,044 -> 10,171 chars) on the first real run. Verified by
    deliberately re-deleting setMin: that version reported zero problems: a
    passing test that could not have failed is worse than no test, because it
    is trusted. Rebuilt WITHOUT string/template stripping — false positives
    from a stray "word(" inside a string are cheap to allowlist by hand;
    false negatives from over-eager stripping are silent. Re-verified the
    same way: with setMin actually deleted, this version reports exactly
    `['setMin']`.
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
    # Confirmed by hand, one at a time, to be "word(" appearing inside a
    # STRING (a button title, a template-literal sentence) rather than a
    # real call with no definition — never added blind. `close` and `match`
    # have ZERO bare (non-dot) occurrences anywhere in the file, proven by
    # `grep -n '[^.]close(' | grep -v '\.close('` before either was added
    # here; `earlier` and `minimize` only ever appear as "...earlier (" and
    # "minimize (keeps running)" inside strings.
    KNOWN_LOCAL_FALSE_POSITIVES = {"close", "earlier", "match", "minimize"}

    def test_every_bare_call_has_a_matching_definition(self):
        import re
        src = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        # Only comments are stripped, and only the two forms that cannot
        # runaway-match in this language: block comments (non-nesting in JS)
        # and full line comments. String and template-literal content is
        # LEFT IN on purpose — see the class docstring for why stripping it
        # was the more dangerous choice, measured, not assumed.
        src = re.sub(r"/\*[\s\S]*?\*/", "", src)
        src = re.sub(r"(?<!:)//.*", "", src)   # skip `://` inside URL strings

        defined = set(re.findall(r"\bfunction (\w+)", src))
        defined |= set(re.findall(
            r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", src))
        defined |= set(re.findall(
            r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function", src))
        # Single bare-param arrows: `const name = x => {`, no parens around
        # the parameter. Several real definitions in this file are shaped
        # this way (accept, answer, paneRow, planNode, pushStep, set,
        # stepNode) and were false positives here before this line existed.
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
    """P17: an approval proves only the bytes that were on screen.

    The digest used to be a label on the card and a stamp on the audit
    event. The POST that actually grants sent only requestId + optionId,
    so a client that never saw the payload could still approve it.
    """

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
        """Pop the pending record BEFORE waking the agent. Two tabs clicking
        allow and reject used to both pass the option check; last writer to
        the waiter won."""
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
    """The digest guards GRANTING. Gating refusal too is a deadlock.

    Measured 2026-08-31, on the very pane that was committing the digest
    change: Craig's browser still held the app.js from before it shipped, so
    it posted no digest at all. Every button on the card failed -- including
    "reject" -- and the agent sat blocked on a permission that could no
    longer be answered in either direction. He had to kill the pane.

    Refusal is the fail-closed default. Nothing is protected by making it
    hard to say no, and an agent stuck waiting is the harm. The oversize
    rule beside it always got this right (`and not kind.startswith("reject")`);
    the digest check shipped without the same clause.
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


class ARequestIdIsNotUniqueInATranscript(unittest.TestCase):
    """A requestId is the agent's own JSON-RPC id.

    JSON-RPC only requires an id to be unique among a peer's IN-FLIGHT
    requests. Grok numbers every permission it asks `0`: measured on pane
    495d803d, 2026-08-31 -- two permission cards 18,000 events apart, both
    requestId "0", in one transcript.

    So "is this requestId pending?" is true of every stale card sharing the
    id, and each of those carries the digest of ITS OWN bytes. The browser
    re-armed an hour-old card; clicking it posted the hour-old digest and the
    server refused it -- correctly, and (before the fix above) unanswerably.
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

    def test_a_reused_id_releases_the_old_waiter(self):
        """Assigning over a live slot stranded the old thread on an Event
        nothing would ever set -- PERMISSION_TIMEOUT is None, so it waited
        forever and the agent's earlier request was never replied to."""
        import acp, threading
        c = acp.AcpClient.__new__(acp.AcpClient)
        c._perm_answers = {}
        c._last_activity = 0.0
        c.alive = True
        c._closed = False
        events, perms, written = [], [], []
        c.on_event = lambda k, d: events.append((k, d))
        c.on_permission = perms.append
        c._write = written.append
        first = threading.Event()
        c._perm_answers["0"] = {"ev": first, "option": None}

        c._on_request({"method": "session/request_permission", "id": 0,
                       "params": {"toolCall": {"title": "second"}}})

        self.assertTrue(first.is_set(), "the stranded waiter must be released")
        self.assertIn(("permission_expired",
                       {"requestId": "0",
                        "reason": "the agent reused this request id"}), events)
        self.assertEqual(len(perms), 1)
        # And the NEW slot is the one now under the key, not the old one.
        self.assertIsNot(c._perm_answers["0"]["ev"], first)

    def test_the_old_waiter_does_not_pop_the_new_slot(self):
        """A plain pop(key) on wake removed whatever was under the id -- after
        a reuse that is the NEXT request's slot, so answering the new card
        found nothing to wake and hung the same way the reuse used to."""
        import acp, threading
        c = acp.AcpClient.__new__(acp.AcpClient)
        c._perm_answers = {}
        c.alive = True
        c._write = lambda msg: None
        old_ev = threading.Event()
        new_ev = threading.Event()
        c._perm_answers["0"] = {"ev": new_ev, "option": None}   # the survivor
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


class MacosPlistIsThisHost(unittest.TestCase):
    """The launchd unit is the one file allowed to hardcode a home path.
    It must be THIS account, not the ranch user it was copied from."""

    def test_the_plist_does_not_point_at_the_ranch_user(self):
        text = (ROOT / "com.cvande.corral-light.plist").read_text(encoding="utf-8")
        self.assertNotIn("/Users/cvande/", text)
        home = str(Path.home())
        self.assertIn(f"{home}/corral-light", text)
        self.assertIn(f"{home}/Library/Logs/corral-light.log", text)
        self.assertIn("/opt/homebrew/bin/python3", text)


class PairCodeIsNotPython(unittest.TestCase):
    """The pair CLI is the identity proof. Interpolating the code into
    `python -c` meant a quote in argv became arbitrary Python as Craig."""

    def test_the_wrapper_does_not_interpolate_the_code_into_python(self):
        text = (ROOT / "corral-light").read_text(encoding="utf-8")
        self.assertNotIn("approve('${2", text)
        self.assertNotIn('approve("${2', text)

    def test_a_quote_in_the_pair_code_is_not_executed(self):
        import subprocess
        marker = Path(tempfile.mkdtemp()) / "pwned"
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
        # SSE has its own header path; it must apply the same lock.
        stream = hub.split("def _stream", 1)[1].split("def ", 1)[0]
        self.assertIn("FRAME_LOCK", stream)


class LiveCapIsNotJustCreate(unittest.TestCase):
    """MAX_PANES was a create() check. Pause-then-resume, or reopen, started
    as many agent processes as you liked."""

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
        state = Path(tempfile.mkdtemp())
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
        home = Path(tempfile.mkdtemp())
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
    """File-exists was the Claude empty-token bug, still open on Grok and Codex."""

    def test_grok_empty_auth_json_is_not_present(self):
        import grok_launcher
        home = Path(tempfile.mkdtemp())
        (home / "auth.json").write_text("{}")
        real = grok_launcher.GROK_HOME
        grok_launcher.GROK_HOME = home
        try:
            self.assertFalse(grok_launcher.auth_present())
        finally:
            grok_launcher.GROK_HOME = real

    def test_codex_empty_auth_json_is_not_present(self):
        import codex_launcher
        home = Path(tempfile.mkdtemp())
        (home / "auth.json").write_text("{}")
        real = codex_launcher.CODEX_HOME
        codex_launcher.CODEX_HOME = home
        try:
            self.assertFalse(codex_launcher.auth_present())
        finally:
            codex_launcher.CODEX_HOME = real

    def test_a_token_bearing_file_still_counts(self):
        import grok_launcher
        home = Path(tempfile.mkdtemp())
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
    """An extra model option is layered on in remember_catalog, not at any one
    probe site — because a REAL session/new response overwrites the catalog
    wholesale (remember_catalog's own contract). If the append lived anywhere
    else, the option would show once on a fresh box and vanish the moment a
    live pane ran.

    The mechanism is exercised here with a SYNTHETIC entry. It used to be
    exercised with `opusplan`, the only real one, and that entry has been
    withdrawn — see OpusPlanWasNeverARealAlias below for the measurement.
    This class proves the LOCAL bookkeeping, which was never the part that
    was wrong.
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
        d = Path(tempfile.mkdtemp())
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
        """The vendor's own session/new response is what OVERWRITES the
        catalog on every real session — proving this survives a second write
        is proving it survives exactly that, not just the first seed."""
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
    """The ssh lane's guarantees, exercised against a LOCAL bash.

    ssh_acp's whole reason to be testable is its connect override: the adapter
    does not care that the far end arrived over ssh, only that it is a bash
    reading stdin. So every bound below is proved against the real code path,
    on this machine, with no host and no network — which is the only kind of
    proof that survives ranch-server being down.
    """

    def _shell(self):
        import ssh_acp
        sh = ssh_acp.Shell(["bash", "--noprofile", "--norc"])
        self.addCleanup(sh.kill)
        return sh

    def test_a_command_runs_and_reports_its_exit_status(self):
        import ssh_acp
        sh = self._shell()
        seen = []
        self.assertEqual(sh.run("echo hello", 10, seen.append), "0")
        self.assertIn("hello", "".join(seen))
        # A SUBSHELL exit, not a bare `exit` — see the test below for why that
        # distinction is the whole difference between a status and a hangup.
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
        """Deliberately UNLIKE acp.py, where no clock ends a turn. There the
        agent is working and only Craig can judge it; here the shell is a
        command runner and a `tail -f` that ate the sentinel will never return
        on its own. The bound is the designed degrade, and it says so."""
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
    """No rail, ON PURPOSE — and therefore it must never become agent-driven.

    The consent argument is that the human typed the exact bytes that run. That
    holds only while a human is the one typing, so the absence of a rail here
    is load-bearing on the lane never being exposed as a tool.
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
        d = Path(tempfile.mkdtemp())
        f = d / "ssh-hosts.json"
        f.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        real = sessions.EXTRA_SSH_HOSTS
        # AGENTS is module-global and refresh_host_lanes is add/update-NEVER-
        # delete by contract, so a test's fake hosts survive into the next test
        # as tombstones. That is correct for the product and wrong for the
        # suite: restore the exact key set, or every later assertion about
        # "how many host lanes exist" counts this test's leftovers.
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
    """The 2026-09-01 finding: Corral had a second, working way to impose a
    permission posture and never used it.

    Craig's report was "Corral Light is ignoring auto mode — the picker won't
    let me choose it". The chain: darwin_keychain_blocks_isolation() is True on
    every Mac, so posture_enforceable() said False, so the dialog DISABLED the
    permissions select and every Claude pane ran at whatever ambient
    defaultMode ~/.claude carries. DEFAULT_POSTURE = "auto" was dead code for
    the lane it mattered most on.

    Measured on dogma-2 the same day, from the adapter's own session/new:
        mode -> currentValue "default",
                options [auto, default, acceptEdits, plan, dontAsk,
                         bypassPermissions]
    That is a live, settable option on the same wire call that already carried
    model and effort. No config dir, so no Keychain.
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
        """The picker's enabled/disabled state reads posture_enforceable. On
        darwin it said False and greyed the control out — Craig's symptom."""
        import sessions
        _pin_sessions_platform(self, "darwin")
        self.assertTrue(
            sessions.posture_enforceable(sessions.AGENTS["claude"]))

    def test_a_lane_that_manages_its_own_permissions_still_says_false(self):
        """The fix must not turn every lane green: grok/codex/ollama run under
        their own policy and must keep reporting that honestly."""
        import sessions
        for lane in ("grok", "ollama"):
            with self.subTest(lane=lane):
                self.assertFalse(
                    sessions.posture_enforceable(sessions.AGENTS[lane]))


class OpusPlanWasNeverARealAlias(unittest.TestCase):
    """Craig, 2026-09-01: "we are still missing opus plan ... when started in
    that mode we default to opus5".

    He was right, and the pane header was the honest control all along: the
    dialog offered catalog + MODEL_EXTRAS, the header cycled p.config straight
    from the live agent, and the option appeared in one and not the other
    because the agent had never advertised it.

    Measured against the real adapter (dogma-2, 2026-09-01):

        opusplan               -> accepted, echoes "opus[1m]"
        opus-plan              -> accepted, echoes "opus[1m]"
        opus-total-garbage-xyz -> accepted, echoes "opus[1m]"
        opus!!!                -> accepted, echoes "opus[1m]"
        sonnet-garbage         -> accepted, echoes "sonnet"
        opusXYZ                -> REFUSED
        plan                   -> REFUSED

    The adapter matches a model name up to a separator and drops the rest.
    The original entry was justified by "garbage is refused, opusplan is
    accepted, therefore it is a vendor alias" — but that garbage began with no
    model name, so it only ever proved the refusal path existed. `opus!!!` is
    the control that decides it, and it says acceptance proves nothing.
    """

    def test_no_invented_model_is_offered_for_claude(self):
        import sessions
        self.assertEqual(sessions.MODEL_EXTRAS.get("claude", []), [])

    def test_the_withdrawal_and_its_control_are_recorded(self):
        """A future reader will be tempted to re-add this; the measurement
        that killed it has to survive in the source, not just in a commit."""
        import inspect, sessions
        src = inspect.getsource(sessions)
        self.assertIn("opus!!!", src)
        self.assertIn("opusXYZ", src)


class AnAckIsNotAdoption(unittest.TestCase):
    """The systemic half of the opusplan bug: Corral believed a set_config
    that came back 200 without reading what came back IN it.

    A pane could run for an hour on a model nobody chose while the header pill
    agreed with the request — the same class of lie as claiming a permission
    posture nothing imposed, and it deserves the same treatment: read the
    agent's echoed value, and say so when it differs.
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
