You are one of three independent bug-bashers on a panel. The other two are different models from different vendors. The author is a Claude agent working for the operator, who owns this system and makes the final call. This is a BUG BASH on finished, committed code, not a design review: find real defects, prove them, rank them.

WHAT YOU ARE TESTING
Corral Light is a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP (lanes: Claude Code, ChatGPT/Codex, Grok, Antigravity/Gemini, remote `host:` shell lanes, chat-only Ollama). The feature just finished is "own branches": a pane can start on its own git worktree and branch cut from a repo the user picks; the user reviews the agent's work as a diff, then Commits (hub plumbing commit, no hooks), Pushes / opens a PR, sends feedback, or Discards (recovery ref + move to trash; real deletion only by typed purge). macOS follow-ups M1-M4 landed on top.

Your working directory is a private clone of the repo at HEAD b6f7dfc. The feature is `git log 5767fa9^..HEAD` (about 6,800 lines across 20 files). Start with:
- docs/worktree-review-plan.md (the spec: §1 requirements, §2 architecture, §5.4 ship gate, §6 risks) and docs/worktree-plan-macos.md
- worktrees.py (the only module allowed to run git), worktrees_cli.py
- sessions.py and hub.py (lifecycle, guards, /api/session/worktree/* routes)
- static/app.js, static/index.html (pill, review dialog, rail card)
- doctor.py; tests: test_worktrees*.py, test_worktree_routes.py, selftest_review.mjs, testkit/fake_acp_agent.py

RULES OF ENGAGEMENT
- Read anything in this clone. You MAY edit files and run code inside this clone and inside ~/.cache/corral-bugbash-2026-10-02/scratch (build throwaway git repos there to reproduce bugs).
- Run tests only with the isolated environment, from the clone root:
  HOME=~/.cache/corral-bugbash-2026-10-02/home TMPDIR=~/.cache/corral-bugbash-2026-10-02/tmp python3 -m unittest test_worktrees test_worktree_routes test_worktrees_cli test_worktrees_iso
  (Node selftests: node selftest_review.mjs.) Do not run the whole test_corral_light suite unless you need it, and only with the same env.
- Do NOT touch anything outside those two directories: not ~/tools/corral-light, not ~/.local/share/corral-light, not ~/.claude, not ~/aios. Do not start a hub on port 8765 or talk to the running hub. No git push, no gh, no network.
- Label each finding PROVEN (you reproduced it: failing test, script, or exact trace through the code with line numbers) or SUSPECTED (reasoned but not reproduced). PROVEN beats volume.

WHAT TO HUNT, in priority order
1. LOST OR MISPLACED WORK. Any path where agent edits, a commit, the branch, the recovery ref, the user's main checkout or the remote can be lost, overwritten, committed with content other than what was reviewed, or pushed somewhere unintended. Include crash/restart mid-operation (the op journal and reconcile), concurrent requests, a busy pane, discard/restore/purge, and the trash.
2. SAFETY-RULE VIOLATIONS. git run outside worktrees.py; git touching the main checkout's index or working tree; automatic prune; hooks running; env vars (GIT_DIR, GIT_INDEX_FILE, GIT_WORK_TREE etc.) leaking through; path traversal or symlink escapes in branch names, repo paths or the trash; actions not bound to the reviewed tree OID.
3. STATE AND RACE BUGS. Pane meta round-trip (META_KEYS / from_meta), registry and journal consistency, the 5 s observe tick versus in-flight actions, Manager._lock discipline, restart resolution, a failed start that leaves an owner-less worktree.
4. GIT EDGE CASES. Detached HEAD, unborn branch, bare repos, submodules, LFS, sparse checkout, renames, binary and huge diffs, non-UTF-8 and spaces/newlines in paths, core.autocrlf, commit.gpgSign, safe.directory, repos on another filesystem, case-insensitive filesystems (macOS M3), git version differences.
5. BROWSER AND ROUTES. Wrong button enabled, stale diff shown while actions bind to a newer tree, XSS (text must go through textContent), route input validation, error paths that hide failure.
6. TESTS THAT LIE. Tests that pass but do not prove what their ID claims, mocks that hide real git behaviour, ship-gate items in §5.4 with no real coverage.

FORMAT (Markdown, under 2,000 words, no preamble)
1. One line: SHIP / FIX FIRST / DO NOT SHIP, with the strongest reason.
2. FINDINGS, ranked most severe first, at most 15. For each: severity (critical / high / medium / low), PROVEN or SUSPECTED, file:line, the trigger, what goes wrong, and the minimal fix. If PROVEN, include the repro (command, test, or script path in scratch).
3. TEST GAPS: at most 6 tests that should exist and would catch a finding above or a likely regression.
4. What you checked and found sound, in at most 8 bullets, so the author knows what was covered.
5. ONE SENTENCE: the bug most likely to bite the operator in the first week.
