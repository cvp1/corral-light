# Worktree plan — macOS (dogma-2) addendum

Status: 2026-10-02, written on dogma-2. An addendum to `docs/worktree-review-plan.md`,
kept as its own file so it never collides with the plan's own edits. Fold it into the
plan (as a "macOS" section, Phase 0 item 0.7 and a ship-gate line) when the plan is next
amended, then delete this file.

The plan and Phase 0 (`docs/worktree-phase0.md`) were run on omarchy-laptop (Linux).
dogma-2 is a Mac mini M4 on macOS 27 with APFS. The facts below were checked there against
the running hub's code and config. Phase 0's v3.2 results are taken as given for Linux.
Each item says whether they carry over to macOS.

## Status (2026-10-02, built and tested on omarchy-laptop)

Code for M1 to M4 is on `master` once merged; each was tested on Linux by
forcing the darwin path. What only dogma-2 can prove is still Phase 0.7.

| Item | Built | Still for 0.7 on dogma-2 |
|---|---|---|
| M1 | Off Linux, discard scans with `lsof +D` under a 20 s timeout; any failure (missing, error, timeout) refuses. lsof's exit code is ignored: it is 1 both on a find and on a bad path. Darwin T-RMV-13 runs here against the real lsof. | Run it under macOS lsof. |
| M2 | Lanes are keyed by platform. macOS has none, so own branches are refused there with the reason. `CORRAL_LIGHT_WORKTREE_LANES` opts lanes in for the matrix run. | The §5.3 matrix for Claude, Codex, Grok; then list the passing lanes. |
| M3 | On darwin the repo dir hash uses the common dir in its on-disk letter case (T-CRT-8). The suite realpaths its temp roots. | Run the suite on dogma-2. |
| M4 | A darwin T-LIF-14 shows the hub leaves `~/.claude` and `~/.claude.json` byte-identical. `doctor` lists `~/.claude/projects` folders of gone worktrees. | Assert the same with real Claude Code. The `session_git_guard.py` hook must skip the worktree root before it is ever re-wired. |
| M5 | Nothing to build yet. | Record memory behaviour for an `~/ai-os` worktree. |
| Facts | git is resolved once and `doctor` prints its path; `doctor` reads the filesystem from `mount` without `/proc`. | None. |

**2026-10-03, run on dogma-2** (`docs/worktree-phase0-macos.md`). M1 is done:
darwin T-RMV-13 passes against the real lsof. M3 is done: the full suite passes
there after four Linux-only tests were fixed. M4 holds with real Claude Code. M2
is partly done: the unattended matrix slice passed for Claude, Codex and Grok,
but the rail, push and PR, agent commit and three-at-once steps still need a
human. No lane is enabled yet. M5 is not run.

## Changes design or tests

### M1. Discard's `/proc` scan does not exist on macOS

§2.2 `discard` refuses while any process has its cwd or an open file inside the
worktree, found by scanning `/proc/*/cwd` and `/proc/*/fd`. Phase 0 made this scan more
important, because it caught `seat_mcp.py` running in a worktree. macOS has no `/proc`. If
the scan is built as written, it finds nothing there and the move goes ahead.

- On darwin use `/usr/sbin/lsof`, under the wrapper's timeout. Use
  `lsof -nP -a -u <uid> -d cwd` for cwds, and `lsof -nP -u <uid> +D <worktree>` for open files.
- **If a scan fails or times out, refuse the discard.** It must fail closed.
- T-RMV-13 gets a darwin variant that uses the `lsof` path.
- `corral_core.acp.process_start_token` already falls back to `ps -o lstart=` off Linux.
  That fallback only has one-second resolution. T-GIT-9 should state that limit.

### M2. Lane results are per platform

Codex's workspace-write sandbox is Seatbelt on macOS, which is a different mechanism from
Linux's. A Linux pass therefore doesn't enable a lane on macOS. Phase 0 also found that
Codex events carry no `locations`, so Codex relies entirely on its sandbox. That makes the
macOS sandbox run more important, not less.

- Key lane enabling by `(lane, sys.platform)`.
- Run the §5.3 matrix on dogma-2 for Claude, Codex and Grok.
- Gemini's lane shows `unknown` in `lanes-check.json` on dogma-2. It is held anyway under D8.

### M3. Case-insensitive APFS and `/private` temp paths

- **Letter case.** `/Users` is case-insensitive here, and `os.path.realpath` doesn't fold
  case: `.../Ab` opened as `.../aB` resolves to `.../aB`. A `<hash6>` taken from the realpath
  of the common dir can therefore split one repo into two root dirs. The per-repo lock uses
  dev:inode, so it stays correct.
  - Fix: derive `<hash6>` from `common_dir_id` (dev:inode), or canonicalise case with
    `F_GETPATH`.
  - New test T-CRT-8: one repo opened under two letter-cases gives one repo dir.
- **Temp paths.** `tempfile.mkdtemp()` returns `/var/folders/...`, which is a symlink to
  `/private/var/...`. T-CRT-6 ("root is a symlink → refused") would refuse every darwin test
  run unless the suite realpaths its temp roots.
  - The suite realpaths its temp roots, and T-CRT-6 builds its own symlink explicitly.

### M4. Claude panes share the real `~/.claude` on macOS

`sessions.darwin_keychain_blocks_isolation()` returns true on darwin. Setting
`CLAUDE_CONFIG_DIR` there changes the Keychain service name, so the login isn't found. As a
result, Claude panes on macOS run on the user's real `~/.claude` and `~/.claude.json`, not on
a per-pane dir. Phase 0's "no pre-trust" result removes the worst case: nothing writes trust
into the real file. Three effects remain:

- **Config test.** T-LIF-14 ("the pane's config dir gains no trust entry") becomes a
  check on the real `~/.claude.json` on darwin. Phase 0.7 should assert it is byte-identical
  across a worktree create, start and first turn.
- **Leftover project dirs.** Every worktree path leaves a permanent
  `~/.claude/projects/<cwd-slug>/` with transcripts in the real home. `reconcile` and purge
  never touch it, and `doctor` should list the dirs that purged worktrees leave behind.
- **Global hooks.** User-level hooks run inside Claude panes. None are wired on 2026-10-02.
  `~/ai-os/tools/session_git_guard.py` is a Stop hook that says "commit + push", and it
  exists only in a settings backup. If it is re-wired, it would tell a worktree pane to push,
  which goes against D15. It must skip cwds under the worktree root before it is re-wired.

### M5. ai-os memory in a worktree pane (unproven on macOS)

Phase 0 found that Claude's auto-memory for a worktree resolves to the main repo. That
test ran on Linux with Claude Code 2.1.287. One older dogma-2 data point points the other
way. A 2026-09 Claude Code `--worktree` session of `~/ai-os`, on v2.1.219 and inside the
repo, loaded no memory-mesh index.

`~/ai-os` matters here because its store is fold-generated and write-guarded. The guard
derives the store from its own install path. Phase 0.7 should start a Claude pane in an
`~/ai-os` worktree on dogma-2 and record two things: whether it loads the
`-Users-craigvandeputte-ai-os` memory index, and where a memory write lands. If the write
doesn't land in that store, the dialog should warn for repos whose store carries
`.mesh-generated`.

## Facts for this host (no design change)

| Plan / Phase 0 says (laptop) | dogma-2 |
|---|---|
| `~/tools/corral-light` | `~/corral-light`. launchd runs the hub from here, and it must never be a worktree. |
| `~/aios` | `~/ai-os` |
| `/home` is one btrfs subvolume | `~/.local/share`, `~/corral-light`, `~/ai-os` and `~/Github/CC` share one APFS volume (same `st_dev`) |
| snapper skips `/home` | Time Machine includes `~/.local/share`, so worktrees and `.trash/` are backed up and trash grows the backup. Add no exclusion without Craig. |
| git 2.55 | `/opt/homebrew/bin/git` 2.55.0 comes first on the hub's launchd PATH. `/usr/bin/git` 2.54.0 (Apple) is second. Resolve git once at start, and have `doctor` print which one. |
| gh 2.101 | gh 2.96.0, signed in as `cvp1` |
| Claude Code 2.1.287 | CLI 2.1.285. Panes use the adapter's bundled binary. |
| mise installed | Not installed, so the mise part of 0.6 is laptop-only |
| Chromium for T-VIS | None installed, so T-VIS skips loudly here and the visual gate runs on the laptop |
| git 2.38 via container | Docker 29.6.2 is running, so this route works here too |

## Proposed Phase 0 item 0.7 (macOS, on dogma-2)

Run the §5.3 matrix for Claude, Codex and Grok on dogma-2. Prove M1's `lsof` scan finds a
shell whose cwd is inside the worktree and refuses when the scan fails. Assert that
`~/.claude.json` is unchanged across a Claude worktree pane (M4). Record M5's memory
behaviour for an `~/ai-os` worktree. Write the results to `docs/worktree-phase0-macos.md`.

Proposed ship-gate line: on macOS, M1 to M3 are resolved, T-RMV-13 is green on darwin, and
lanes are enabled only from the dogma-2 matrix.
