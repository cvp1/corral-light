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
            self.assertFalse(
                sessions.posture_enforceable(sessions.AGENTS["claude"]))
        finally:
            Path.home = real

    def test_a_credential_file_still_gets_a_private_dir(self):
        """The normal path must be unchanged — this is a fallback, not a
        replacement for the posture mechanism."""
        import sessions
        home = self._fake_home()
        (home / ".claude" / ".credentials.json").write_text("{}")
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
            self.assertEqual(sessions.vendor_env_present(), [])
        finally:
            os.environ.pop("CLAUDECODE", None)


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

    def test_it_never_prints_a_secret_value(self):
        import diagnose, inspect
        src = inspect.getsource(diagnose)
        self.assertIn("value not shown", src)
        # Names and lengths only — no dict dump of the environment anywhere.
        self.assertNotIn("json.dumps(dict(os.environ", src)
        self.assertNotIn("print(os.environ", src)


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
