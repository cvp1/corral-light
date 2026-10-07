You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for the operator, who owns this system and makes the final call. This is ROUND THREE, a QUICK CODE REVIEW of what was built. Keep it focused.

WHAT YOU ARE REVIEWING
Corral Light is a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine. The plan, docs/finops-module-plan.md (rev 5), designs a module seam so features live in their own repositories, with FinOps as the first. Phase 0 measured the vendors' files (docs/finops-phase0.md). Phase 1, the seam, is now BUILT on this branch (last two commits). Rounds one and two reviewed the plan; their record is in reviews/2026-10-07-finops-module-panel/.

The new code, in order of risk:
- module_sandbox.py: the bubblewrap allowlist profile collectors run in (no network, empty root, declared reads only), resource limits, availability probe.
- modules.py: manifest validation, install from git at a pinned commit, tree digest, verify-before-every-run, generations, update/rollback, the capped runner (run_capped, run_collector, Runner), snapshot validation (validate_snapshot), CLI and the enabled-module dispatch.
- hub.py: /api/modules, /api/module/<name>, POST /api/module/<name>/refresh, /health counts, the runner and reports threads.
- module_feed.py and the sessions.py change (_on_usage_update): the redacted feed and Claude quota capture; login facts in claude_auth.py, codex_launcher.py, grok_launcher.py.
- adapter_patches.py: the pinned patch to the Claude adapter (fixture excerpt in testkit/fixtures/module-feed/).
- vendor_reports.py: the core runs `grok usage` in its own sandbox with two files bound.
- static/app.js (search "Modules" / renderModuleView / modSafeLink), static/style.css (.mod*), selftest_modules.mjs.
- Tests: test_modules.py, test_module_feed.py, test_vendor_reports.py, fixture module testkit/modules/probe/, wrapper `corral-light`, doctor.py.

RULES OF ENGAGEMENT
- READ ONLY. This clone is read-only. Do not edit or create files, do not run the test suite, do not start a hub, do not talk to any running hub, no network, no git push.
- Do not open files outside this clone. Do not read anything under the home directory's vendor folders (~/.claude, ~/.codex, ~/.grok, ~/.gemini, ~/.config, ~/.local/share): they hold private conversations and logins. Do not run vendor CLIs or bwrap.
- Label each finding PROVEN (file:line, and the input that triggers it) or SUSPECTED. Prefer a few real defects over many style notes.

WHAT TO ANSWER
1. One line: SHIP / FIX-FIRST / REWORK for the seam, with the strongest reason.
2. Up to EIGHT findings, most severe first. For each: severity (high/medium/low), PROVEN or SUSPECTED, file:line, the concrete failure (input → wrong result), and the smallest fix. Look hardest at: a way for a module to read or write outside what the plan allows, reach the network or the hub, or survive its timeout; a way for a changed or extra file to run without the digest catching it (TOCTOU between verify and exec included); a way for snapshot text to become markup, a link, a class or a style in the page; a token, raw account id or prompt text reaching the feed; the quota merge losing or mislabelling a window; races between run, update, remove and rollback.
3. TESTS: one listed test that would still pass with its property broken, and the one missing test that matters most.
4. ONE SENTENCE: the single change that most improves the seam before it merges.

FORMAT: Markdown, under 1,200 words, no preamble.
