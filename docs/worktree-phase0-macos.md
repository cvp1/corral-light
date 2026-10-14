# Worktree Phase 0 on macOS (item 0.7) — results

Run 2026-10-03 on the Mac host (Mac mini M4, macOS, APFS, `/opt/homebrew/bin/git`
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

The full suite (`python3 test_corral_light.py`) passes on the Mac host: 647 tests OK,
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

## 2026-10-14: the open items, run on the Mac host

Same method: a private hub on port 8199 with its own temp state, driven
through the hub API. The live hub was never touched. The driver is
`~/.wt-ship-driver.py` with env `~/.wt-ship.env`, both outside the repo. Every
scratch repo had a `.gitignore` and a local bare remote as `origin`.

**Verdict.** Claude, Codex and Grok meet the bar Linux was enabled on
(`docs/worktree-phase0.md`): the rail behaves as each lane's posture allows,
an agent's own commit lands on its branch only, and Publish pushes by URL and
OID to the remote with `main` unmoved. All three are enabled on macOS.

| Step | Claude | Codex | Grok |
|---|---|---|---|
| Strict posture: shell commands reach the rail | yes: 2 cards on the rail turn, 3 on the commit turn | no cards: its sandbox auto-runs (same as Linux) | no cards for `unittest` or `touch`: its strict default runs what its own policy allows (known; why it is not in `REVIEW_AT_END_LANES`) |
| A rejected card stops the command | yes: `touch` refused, marker not created | n/a | n/a: marker created |
| Agent's own `git commit` | pass, after an approved card | **pass**: the sandbox blocked it, then Codex retried with escalated access and no card. Linux failed here | pass |
| Publish to a local bare remote | pass | pass | pass |
| `main` unmoved; main checkout untouched | yes | yes | yes |
| Tool locations outside the worktree | none | 1 read, `~/.agents/skills/…/SKILL.md` | none |

Cards were answered through `/api/session/permission` with the card's digest
and `via: wall`: the route the wall's buttons call. Card rendering is covered
by the browser selftests, not by this run.

**Three lanes at once.** One repo, three files (`mod_a`, `mod_b`, `mod_c`),
Claude, Codex and Grok in parallel under posture `auto`. Each branch changed
only its own file. All three were hub-committed and published. The bare remote
holds all three `corral/*` branches, and `main` did not move.

**M5.** Claude in a worktree pane names its auto-memory directory as the
**main repo's** project folder (`~/.claude/projects/<main repo key>/memory/`),
as on Linux. Transcripts go under the worktree's own project folder, which
`doctor` lists once the worktree is gone. So a pane on an `~/ai-os` worktree
reads and writes the same memory store as `~/ai-os`.

**Not run, as on Linux.** A PR via `gh`: it needs a throwaway GitHub repo,
an outward step. `open_pr` has no platform branch; it moves to dogfood.

**Ship gate item 4 (one week of dogfood) does not block.** The maintainer's
standing ruling for this project: green-lit builds ship in days, and live use
steers but never blocks. Dogfood continues after enabling; a lost-work report
is the falsifier, and `CORRAL_LIGHT_WORKTREES_ENABLED=0` is the kill switch.

### Findings

1. **Codex escalates its own commit on macOS without a card.** Codex's posture
   is not mapped, so this is the lane's own approval policy. It wrote only the
   branch and the shared object store; `main` did not move. The hub's review
   and Publish remain the gate for Codex, as the README already says.
2. **Hub Commit picks up whatever the agent left.** In the Codex and Grok
   runs it committed `corral_rail_rejected.txt`, the marker the rail test asked
   for. The review dialog shows every file; that remains the guard.
3. **Driver note.** The deny list first matched `rm ` inside "confirm the", so
   one harmless `unittest` card on the Claude commit turn was refused. It
   changed no result.
