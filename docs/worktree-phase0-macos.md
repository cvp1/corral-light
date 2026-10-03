# Worktree Phase 0 on macOS (item 0.7) — results

Run 2026-10-03 on mac-host (Mac mini M4, macOS, APFS, `/opt/homebrew/bin/git`
2.55.0, hub on Homebrew Python 3.14). Code at `0847d05` plus the macOS test
fixes in `9052d20`. Plan: `docs/worktree-plan-macos.md`.

**Verdict.** Every step that could run unattended passed on Claude, Codex and
Grok. Steps that need you are still open. Those are the approval rail, push and
PR, an agent's own `git commit`, three lanes at once, and M5. So
`WORKTREE_LANES["darwin"]` stays empty. No lane is enabled on macOS by this run.

## How it ran

- **Private hub, not the live one.** A second hub ran from `~/corral-light` on
  port 8199 with its own `CORRAL_LIGHT_STATE` in a temp dir and
  `CORRAL_LIGHT_WORKTREE_LANES=claude,codex,grok`. The live hub on 8098 was
  never restarted or touched.
- **Driven through the hub API**, the routes the browser uses. Each lane got its
  own scratch repo: `calc.py` with a broken `add`, one failing unittest, one
  commit on `main`, no remote and no `.gitignore`.
- **Posture `auto`. No permission card was answered by the script.** Any card
  would have been refused and recorded. None appeared, so the rail is untested
  here.

## Automated suites

The full suite (`python3 test_corral_light.py`) passes on mac-host: 647 tests OK,
3 skipped. The skips are the Claude login spike tests. Four worktree tests had
assumed Linux and are fixed in `9052d20`: T-DIF-6, M3 true case, T-LIF-13 and
M1 unreadable /proc. T-ISO-1 failed only because it reruns them. **T-RMV-13
(darwin) passes against the real `lsof`**, which proves M1's scan on this host.

## Lane matrix (§5.3), unattended slice

| Step | Claude | Codex | Grok |
|---|---|---|---|
| Starts on its own branch; no trust stall | pass (2.6 s) | pass (5.7 s) | pass (1.5 s) |
| Fixes the test; edits land in the worktree only | pass | pass | pass |
| Tests pass in the worktree; main checkout unchanged | pass | pass | pass |
| Tool `locations` outside the worktree | none seen | 1 read: `~/.agents/skills/using-superpowers/SKILL.md` | 3 reads under `~/.agents/skills/` |
| Shell commands reach the approval rail | not tested (posture auto, no cards) | not tested | not tested |
| Review snapshot, then hub Commit | pass | pass | pass |
| `main` unmoved after hub Commit | pass | pass | pass |
| Agent `git commit` | not run | not run | not run |
| Push (local bare remote) and PR via `gh` | not run | not run | not run |
| Pause, **restart the hub**, resume on the branch, turn works | pass | pass | pass |
| Discard, restore (`worktrees restore`), purge (`--confirm <branch>`) | pass | pass | pass |
| Recovery refs kept after purge | yes, 2 | yes, 2 | yes, 2 |

## Findings

1. **Hub Commit took the `__pycache__` files.** Running the tests left `.pyc`
   files, and the scratch repo had no `.gitignore`. Review listed them next to
   `calc.py`, and Commit committed all three. That is correct git behaviour,
   but the review dialog is the only guard. A real repo's `.gitignore` covers it.
2. **Codex and Grok read their own skill files outside the worktree.** Both read
   `SKILL.md` files under `~/.agents/skills/` at the start of the turn. Those are
   reads of agent config, not writes. The out-of-worktree warning should not
   treat them as escapes, or it will fire on every Codex and Grok turn.
3. **M4 holds live.** After a Claude own-branch pane, `~/.claude.json` has no
   `projects` entry for any worktree path.
4. **Codex reports tool locations on macOS.** It reported none on the Linux run.
   This run only recorded locations outside the worktree, so it does not show
   whether Claude reports them.

## Still open before any lane is enabled on macOS

These need you at the wall, or an outward step:

1. Approval rail: run Claude and Grok under posture `strict`, and answer their
   shell-command cards in the wall.
2. Push to a local bare remote, plus a PR via `gh` to a throwaway GitHub repo.
3. An agent's own `git commit` in a worktree. Codex is expected to fail.
4. Three lanes, three tasks, one repo, at once, all published.
5. M5: whether Claude's auto-memory follows an `~/ai-os` worktree path.
6. Ship gate item 4: one week of dogfood with no lost work.

## Leftovers on disk

Everything ran under a temp dir that macOS clears. The driver is
`~/.wt-matrix-driver.py` and its env is `~/.wt-matrix.env`, both outside the
repo. Nothing was written to the live hub's state.
