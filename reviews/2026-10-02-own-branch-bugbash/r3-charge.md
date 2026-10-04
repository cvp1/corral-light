You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for the operator, who owns this system and makes the final call. This is ROUND 3, the SHIP DECISION for the "own branches" feature of Corral Light. Rounds 1 and 2 found defects; the author says every one is now fixed. Your job: decide whether this ships, and find anything that should stop it.

WHAT YOU ARE REVIEWING
Corral Light is a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP. "Own branches": a pane can start on its own git worktree and branch; the user reviews the agent's work as a diff, then Commits (hub plumbing commit), Publishes (push / PR), or Discards (recovery ref + move to trash; deletion only by typed purge). An op journal in a per-worktree registry file makes every mutating action crash-safe; an op left `unknown` or `intent` blocks further actions and dispatch until settled.

Your working directory is your seat folder, <SEATDIR>. The code is the private clone in ./corral-light (no remote). Its branch `review` has one top commit titled SEAT SANDBOX ONLY that points every default state path at <SEATDIR>/state; do not review it. Everything below it is in scope. The feature's history: round 1 fixes 6500638, second-Manager guard 78ab44f, round 2 fixes 6f86aa4, round 1 leftovers HEAD~1 (`git log --oneline -6`). Findings and their status: reviews/2026-10-02-own-branch-bugbash/synthesis.md (round 1) and r2-synthesis.md (round 2). The spec is docs/worktree-review-plan.md.

WHAT CHANGED SINCE ROUND 2 (HEAD~2 and HEAD~1)
- Gate: `intent` blocks send, peer dispatch, resume and /clear (except the pane's own running action); resume and /clear hold the pane's action lock, so they never interleave with a review action. restore() and open_pr() refuse unsettled ops.
- Discard: the preflight refuses an outdated review before stopping the agent when two looks 0.5 s apart agree (still-changing files mean the agent is writing, so it is stopped first, spec T-RMV-11); processes in the worktree's git admin dir count; a stale index.lock is set aside only when no process holds it open.
- Restart: a commit journal without an index identity never rebuilds the index; a failing post-commit check is `unknown`; a repository that cannot be listed flags its own entries and reconcile goes on.
- Round 1 leftovers: a confirmed push URL that git would rewrite again (url.*.insteadOf / pushInsteadOf) is refused; the agent folder must resolve inside its worktree before every start, resume and dispatch; one deadline covers git's stdin, process and inherited pipes; the review response is budgeted by its JSON-encoded size (fit_review); CLI `resolve --op` refuses a landed commit whose index does not match; open_pr reuses an open PR only when its head owner is ours; restore recreates the repo folder under the root; lsof exit 1 with only warnings is "none"; an unreadable /proc cwd still has its fds read; the review shows staged_differs and no-op commits honestly; roster pops in create() hold the lock.
- Deliberate choices you may challenge, with evidence: a repo on another filesystem than the worktree root is allowed (spec D2); a process whose /proc cwd AND fds are both unreadable (non-dumpable) is not counted by the Discard scan, because failing closed there would block every Discard on a normal desktop.

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
2. NEW DEFECTS IN THE NEW CODE: the action lock around resume and /clear (deadlock with _turn_lock, a send that implicitly resumes, auth_sweep and schedule resume paths), the settle check in discard_preflight, _set_aside_stale_lock with processes_in on a file, rewrite_rule, check_agent_cwd, _run's single deadline, fit_review, the CLI resolve check, open_pr owner matching.
3. REGRESSIONS: anything that used to work and now refuses wrongly, or blocks a healthy pane.
4. TESTS THAT LIE: BugBash2* and BugBash3* in test_worktrees.py, T-CLI-15b.

FORMAT (Markdown, under 1,500 words, no preamble)
1. First line exactly one of: SHIP / FIX FIRST / DO NOT SHIP, then the strongest reason, judged by the ship bar.
2. BLOCKING FINDINGS (only those meeting the ship bar), ranked: severity, PROVEN or SUSPECTED, file:line, trigger, what goes wrong, minimal fix, repro path.
3. NON-BLOCKING: at most 8 one-liners.
4. ONE SENTENCE: what most likely bites the operator in the first week.
