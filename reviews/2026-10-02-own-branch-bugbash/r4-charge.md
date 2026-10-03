You are one of three independent reviewers on a panel. The other two are different models from different vendors. The author is a Claude agent working for Craig, who owns this system and makes the final call. This is ROUND 4, the SHIP DECISION after round 3's fixes, for the "own branches" feature of Corral Light. Rounds 1 to 3 found defects; the author says every blocking one is now fixed. Round 3 split: SHIP, FIX FIRST, DO NOT SHIP. Your job: decide whether this ships, and find anything that should stop it.

WHAT YOU ARE REVIEWING
Corral Light is a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP. "Own branches": a pane can start on its own git worktree and branch; the user reviews the agent's work as a diff, then Commits (hub plumbing commit), Publishes (push / PR), or Discards (recovery ref + move to trash; deletion only by typed purge). An op journal in a per-worktree registry file makes every mutating action crash-safe; an op left `unknown` or `intent` blocks further actions and dispatch until settled.

Your working directory is your seat folder, <SEATDIR>. The code is the private clone in ./corral-light (no remote). Its branch `review` has one top commit titled SEAT SANDBOX ONLY that points every default state path at <SEATDIR>/state; do not review it. Everything below it is in scope. The feature's history: round 1 fixes 6500638, second-Manager guard 78ab44f, round 2 fixes 6f86aa4, round 1 leftovers 452a623, round 3 fixes HEAD~1 (`git log --oneline -7`). Findings and their status: reviews/2026-10-02-own-branch-bugbash/synthesis.md, r2-synthesis.md and r3-synthesis.md; the round 3 reports are r3-*.md. The spec is docs/worktree-review-plan.md.

WHAT CHANGED SINCE ROUND 3 (HEAD~1; r3-synthesis.md has the detail)
- The first agent start holds the pane's action lock, and review actions refuse a `starting` pane.
- _worktree_action releases only a hold it took; type-ahead behind a failed action on a live agent is parked without touching the attachment (_park_held_queue), so its events and permission cards keep arriving.
- rewrite_rule matches an empty insteadOf prefix, refuses when git config cannot be read, and asks `git ls-remote --get-url` what git makes of the URL (a remote's name, or a rewrite); anything but the literal URL is refused.
- The dispatch and resume gate runs verify() (symbolic HEAD is our branch, admin dir, under the root) plus the agent-folder check.
- Restart recovery of an interrupted `pr` op keeps only a PR whose head owner matches the journalled head (_pr_head_owner, shared with open_pr); open_pr no longer reuses a PR with no owner.
- fit_review also trims the too_big inventory and the ignored sample.
- Deliberate choices you may challenge, with evidence: a repo on another filesystem than the worktree root is allowed (spec D2); a process whose /proc cwd AND fds are both unreadable (non-dumpable) is not counted by the Discard scan; Discard's settle check may refuse once while files are still changing (T-RMV-11).

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
2. NEW DEFECTS IN THE NEW CODE: the action lock now taken in create() (deadlock with _turn_lock or with D14's dead-pane path), _park_held_queue vs _park_stale_queue, verify() on every dispatch (cost, false refusals while the agent itself runs git), rewrite_rule's ls-remote --get-url, the pr resolve owner rule, fit_review on inventories.
3. REGRESSIONS: anything that used to work and now refuses wrongly, or blocks a healthy pane.
4. TESTS THAT LIE: BugBash4* in test_worktrees.py.

FORMAT (Markdown, under 1,500 words, no preamble)
1. First line exactly one of: SHIP / FIX FIRST / DO NOT SHIP, then the strongest reason, judged by the ship bar.
2. BLOCKING FINDINGS (only those meeting the ship bar), ranked: severity, PROVEN or SUSPECTED, file:line, trigger, what goes wrong, minimal fix, repro path.
3. NON-BLOCKING: at most 8 one-liners.
4. ONE SENTENCE: what most likely bites Craig in the first week.
