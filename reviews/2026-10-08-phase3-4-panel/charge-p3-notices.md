You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for the operator, who owns this system and makes the final call. This is a QUICK CODE REVIEW of what was built and is now live. Keep it focused.

WHAT YOU ARE REVIEWING
This folder holds two read-only clones. `corral-light/` is Corral Light, a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine, with a module seam so features live in their own repositories. `corral-light-finops/` is FinOps, the first module. The plan is `corral-light/docs/finops-module-plan.md` (rev 7). Earlier panel rounds are in `corral-light/reviews/2026-10-07-finops-module-panel/`.

Phase 3, RAIL NOTICES, is specified in plan §4.7 and built:
- corral-light/modules.py: `notices` in the manifest (opt-in), `_validate_notices` and `validate_snapshot(raw, notices=...)` (ids, levels, caps, ordering), `notices(now)` (expiry at read time: own expires_at, two missed collector periods, a 24 h cap; enabled, runnable, opted-in modules only; 3 per module, 8 in all), `describe`, the update re-confirm.
- corral-light/hub.py: `moduleNotices` added to the full /api/state only. corral-light/sessions.py LIGHT_OMITS comment.
- corral-light/static/app.js: `railNotices`, `noticeCard`, `noticeSeen`/`noticeMarkSeen`, `NOTICE_LEVEL_CLASS`, the rail block in `render` (search "Module notices"); static/style.css `.ncard.nmod`.
- corral-light-finops/finops/view.py: `notices()` and `notice_id()` (near-limit quota windows at the tiles' levels, frozen sources), `build` adding them; finops/config.py `notices = "off"`.
- Tests: corral-light/test_modules.py (TheNoticeField, TheNoticeManifest, TheNotices, TheNoticeRoute), corral-light/selftest_inbox.mjs (NOT-*), corral-light-finops/tests/test_notices.py.

RULES OF ENGAGEMENT
- READ ONLY. Do not edit or create files, do not run tests, do not start a hub, do not talk to any running hub, no network, no git push.
- Do not open files outside this folder. Do not read anything under the home directory's vendor folders (~/.claude, ~/.codex, ~/.grok, ~/.gemini, ~/.config, ~/.local/share): they hold private conversations and logins. Do not run vendor CLIs or bwrap.
- Label each finding PROVEN (file:line, and the input that triggers it) or SUSPECTED. Prefer a few real defects over many style notes.

WHAT TO ANSWER
1. One line: SHIP / FIX-FIRST / REWORK for Phase 3, with the strongest reason.
2. Up to SIX findings, most severe first. For each: severity (high/medium/low), PROVEN or SUSPECTED, file:line, the concrete failure (input → wrong result), and the smallest fix. Look hardest at: a notice that outlives its module, its grant of trust or its freshness rule; module text reaching markup, a class, a style or a link in the page; a notice that blocks, pops the phone rail or enters the hot count; Not now hiding a notice that escalated; a FinOps notice disagreeing with its tile, or carrying a path, prompt text or raw account id; ordering or cap errors.
3. TESTS: one listed test that would still pass with its property broken, and the one missing test that matters most.
4. ONE SENTENCE: the single change that most improves Phase 3.

FORMAT: Markdown, under 1,000 words, no preamble.
