You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for Craig, who owns this system and makes the final call. This is ROUND 11, the SHIP DECISION after round 10's fixes, for the "own branches" feature of Corral Light. Rounds 1 to 10 found defects; the author says every blocking one is now fixed. Rounds 6 to 10 found one or two blockers each, nearly all ways to make Publish's checks and its push read different things. Your job: decide whether this ships, and find anything that should stop it.

WHAT YOU ARE REVIEWING
Corral Light is a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP. "Own branches": a pane can start on its own git worktree and branch; the user reviews the agent's work as a diff, then Commits (hub plumbing commit), Publishes (push / PR), or Discards (recovery ref + move to trash; deletion only by typed purge). An op journal in a per-worktree registry file makes every mutating action crash-safe; an op left `unknown` or `intent` blocks further actions and dispatch until settled.

Your working directory is your seat folder, <SEATDIR>. The code is the private clone in ./corral-light (no remote). Its branch `review` has one top commit titled SEAT SANDBOX ONLY that points every default state path at <SEATDIR>/state; do not review it. Everything below it is in scope. The feature's history: round 1 fixes 6500638, second-Manager guard 78ab44f, round 2 fixes 6f86aa4, round 1 leftovers 452a623, round 3 fixes e8a24eb, round 4 fixes b2d2a73, round 5 fixes 466da50, round 6 fix d673d22, round 7 fix f99d8b0, round 8 fix 65c9810, round 9 fixes 33b4ad2, round 10 fixes HEAD~1 (`git log --oneline -14`). Findings and their status: reviews/2026-10-02-own-branch-bugbash/synthesis.md and r2- to r10-synthesis.md; the per-seat reports are r3-* to r10-*. The spec is docs/worktree-review-plan.md.

WHAT CHANGED SINCE ROUND 10 (HEAD~1; r10-synthesis.md has the detail)
- read_regular(): every Python read of a file the agent can replace (the worktree's .git file, the index, the repository config files in the fingerprint) opens non-blocking without following links and refuses anything but a regular file, so a fifo cannot block verify(), snapshot, commit or a resume holding the action lock. (git resolves a symlinked index itself, so the hub reads the same file git does.)
- Publish refuses a relative local push URL (reason `relative`, is_relative_local_url); a restart check of an interrupted push runs via _repo_git and treats a relative URL as unknown.
- Deliberate choices you may challenge, with evidence: transport settings in the user's global or system git config and in the hub's environment are the user's own and are honoured (an agent rewriting the user's global config is outside this feature's boundary); a repo on another filesystem than the worktree root is allowed (spec D2); a process whose /proc cwd AND fds are both unreadable (non-dumpable) is not counted by the Discard scan; Discard's settle check may refuse once while files are still changing (T-RMV-11); an agent that switches branches blocks its pane, Discard included, until the branch is switched back.

THE SHIP BAR (use it for your verdict)
- DO NOT SHIP or FIX FIRST only for a PROVEN finding in one of: work lost or made unreachable; an agent running, or writing, outside its own branch or into trash; a gate bypassed (an action or dispatch while an op is unknown or intent, or during another action); a push or PR going somewhere the user did not confirm; the hub's live state or agents harmed by a second process; a deadlock or a pane stuck forever.
- Everything else (UX text, suspected-only issues, hardening ideas, style) is listed but does not block: say SHIP and list them.

RULES OF ENGAGEMENT
- HARD RULE, because breaking it in round 2 killed every agent on this machine, including the reviewers: any Python you run that imports `sessions` or `hub`, or constructs `sessions.Manager()`, must run with CORRAL_LIGHT_STATE=<SEATDIR>/state and CORRAL_LIGHT_WORKTREES=<SEATDIR>/state/worktrees set in that same command. Never point either variable anywhere else.
- Read anything in this clone. You MAY edit files and run code inside <SEATDIR> only: the clone, <SEATDIR>/scratch (build throwaway git repos there), <SEATDIR>/state, <SEATDIR>/home and <SEATDIR>/tmp.
- Run tests only with the isolated environment, from the clone root (cd <SEATDIR>/corral-light):
  CORRAL_LIGHT_STATE=<SEATDIR>/state CORRAL_LIGHT_WORKTREES=<SEATDIR>/state/worktrees HOME=<SEATDIR>/home TMPDIR=<SEATDIR>/tmp python3 -m unittest test_worktrees test_worktree_routes test_worktrees_cli test_resilience
  Tests that open sockets may fail inside a sandbox that denies them; say so rather than counting them as findings.
- Do NOT write to anything outside <SEATDIR>: not ~/tools/corral-light, not ~/.local/share/corral-light, not ~/.claude, not ~/aios, not other seats' folders. Do not start a hub or talk to the running one. No git push, no gh, no network.
- Label each finding PROVEN (reproduced: failing test, script, or exact trace with line numbers) or SUSPECTED. PROVEN beats volume. Put repro scripts in <SEATDIR>/scratch.

WHAT TO HUNT, in priority order
1. FIXES THAT DO NOT HOLD: try each round 1 and round 2 repro and a variant against HEAD.
2. NEW DEFECTS IN THE NEW CODE: how Publish decides where a push goes (push_urls, rewrite_rule, transport_override, push, and the ls-remote check after it): any repository-scoped setting or file that still changes where a push to a literal URL goes; and anything in the feature the earlier rounds did not reach. For push destination checks, read rewrite_rule and push(), run BugBash3Publish, BugBash4Publish and BugBash5Publish, and add a test case in their style if you see a gap.
3. REGRESSIONS: anything that used to work and now refuses wrongly, or blocks a healthy pane.
4. TESTS THAT LIE: BugBash6* in test_worktrees.py.

FORMAT (Markdown, under 1,500 words, no preamble)
1. First line exactly one of: SHIP / FIX FIRST / DO NOT SHIP, then the strongest reason, judged by the ship bar.
2. BLOCKING FINDINGS (only those meeting the ship bar), ranked: severity, PROVEN or SUSPECTED, file:line, trigger, what goes wrong, minimal fix, repro path.
3. NON-BLOCKING: at most 8 one-liners.
4. ONE SENTENCE: what most likely bites Craig in the first week.
