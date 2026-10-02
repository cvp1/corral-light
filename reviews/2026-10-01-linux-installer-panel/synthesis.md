# Panel synthesis — Corral Light Linux installer (2026-10-01)

Reviewed: `install.sh` (1.0.0 in round 1, 1.1.1 in round 2), the README top,
`launch.py` and the `#pair=` page change. Two rounds, three vendors, each
reading pasted text only from an empty scratch directory.

| Seat | Model (verified) | How verified | Notes |
|---|---|---|---|
| Grok | grok-4.7, effort xhigh | hub-echoed model and effort | Stalled twice at xhigh with no output (pane went `uncertain`); the second attempt finished only after a human typed "are you still working on this?" into the pane. The consult call had already returned empty. |
| Astra | gpt-6-astra, effort high | hub-echoed model and effort | `--config mode=read-only` is refused by the codex lane from consult; ran in the lane's default mode in an empty directory |
| Gemini | gemini-3.8-flash-high | hub-echoed model (effort lives in the ID) | |

## Bottom line

Round 1: **RESHAPE** from all three on the installer and README; Astra BUILD on
the overall approach, Grok and Gemini RESHAPE. Round 2 on 1.1.1: Gemini and
Astra RESHAPE, both naming the same new regression (the outer shell cleared
`set -e` and the ERR trap before the `main | tee` pipeline, so the subshell
inherited neither); Astra additionally found that `exec </dev/null` at top
level ends a script that bash is reading from stdin — the published
`curl | bash` path. Both are fixed in 1.1.3, with tests that run the script
the piped way. Grok's round 2 moved the overall approach to **BUILD** and `launch.py` to
BUILD, kept the installer at RESHAPE for the same two regressions, and added:
prompts should be written to the terminal, not the log pipe; an answer with
no digits should mean Claude only and anything else should be asked again;
the memory question should be asked only when Claude is chosen, and both
questions should come before the sudo step; a clean checkout with local
commits must not be reset by `checkout -B`; the glibc compare should use
`sort -C`; Seed's `--detect` output must be recognised, not merely grepped.
All of these are in 1.1.4.

**Converged across all three in round 2:** the `main | tee` regression; ask
first, then sudo, then download; default to one assistant; the ship gate must
run on a real Ubuntu 24.04 VM with systemd and cron, which the author could
not do here.

## What each round changed

**Round 1 → 1.1.0/1.1.1** (all three reviewers, converged):
- Seed receipt no longer hides unfinished setup: each phase (install, verify,
  CLAUDE.md region, mesh, hooks, sync, audit) is checked separately on a re-run,
  reading `gated_writes` from Seed's receipt.
- The two questions (which assistants; memory) moved to the start, in plain
  words; `--lanes` validated at parse time; `--yes` = all four + memory on; no
  terminal and no `--yes` = "no" to every question.
- Children never read the piped script (`main … </dev/null`); questions read
  `/dev/tty`.
- Port-in-use check before starting; health wait requires the hub to identify
  itself; service restarted when the checkout or drop-in changed; drop-in
  `Environment=` lines quoted.
- glibc 2.28+/musl check via `getconf`; free-disk check; internet check after
  the package step.
- Claude login check parses JSON; codex binary existence checked; every
  `cmd | grep -q` removed (pipefail + SIGPIPE — Astra predicted the class, and
  the glibc probe then failed on a real machine exactly that way).
- Pairing code in the URL fragment (`/#pair=`), never in a request line.
- Claude Code installed at a pinned version through Anthropic's installer.
- INT trap; ten-minute cap per sign-in (`timeout --foreground -k 10 600`);
  the uninstall command names the copy the clone keeps; the end summary lists
  per-assistant state and says "Ready" only when every chosen assistant has a
  sign-in and the browser was opened.

**Round 2 → 1.1.2/1.1.3**:
- errexit and the ERR trap re-armed inside `main` (Gemini, Astra).
- No stdin redirection above the last line; `--help` is self-contained
  (`$0` is `bash` when piped) (Astra; found by the new piped test).
- **1.1.4 (Grok R2):** prompts go to `/dev/tty`; the two questions come
  right after the machine check, before any sudo or download; the memory
  question only when Claude is chosen; an answer without digits = Claude
  only, other junk is asked again (three tries); `sync_repo` refuses to move
  a checkout whose HEAD is not an ancestor of the target ref; glibc compare
  via `sort -C -V`; Seed `--detect` output must be one of its two known
  forms or the install stops; the receipt records the Claude Code version the
  binary reports.
- Background hub in `--no-service` mode fully redirected and exec'd, so the
  installer can exit (found while testing Astra's point).
- Health JSON parsed, not pattern-matched (Gemini).
- Service enablement reconciled on every run (Astra).
- Enter at the assistant question = Claude only (Gemini, Grok); no terminal =
  Claude only with a warning (Astra).
- A "no" on memory where hooks are already wired keeps them and prints the
  revoke command (Astra).

## Rejected, with reasons

- Switch Seed's scheduler to systemd timers (Gemini R1): Seed's Linux backend
  is cron by design; out of scope.
- "Ubuntu 24.04 dropped cron" (Gemini R1): not true; Gemini retracted in R2.
- Transactional rollback (Astra, Gemini R1): the design is idempotent re-run;
  Gemini argued the same in R2.
- A desktop double-click entry point (Grok, Gemini): Linux desktops block
  untrusted `.desktop` launchers; the README now states the one-paste-line
  floor honestly.
- `CODEX_HOME` not persisted for the service (Gemini R1): `codex_launcher.py`
  sets it itself.
- "The preset code is never claimed" (Gemini R1): the poll was cut from the
  paste; retracted in R2.
- Re-escape paths in the detect filter (Gemini R2): the filter compares
  `realpath`s, not regexes of the path.
- `press_enter` keystroke flushing (Gemini R2): not reproducible as described;
  left as is.
- A progress bar for the 1.5 GB Antigravity download (Gemini, Astra): would
  need a change in `install_antigravity_acp.py`; noted as a follow-up.

## Ship gate (merged from the three lists)

On a fresh Ubuntu 24.04 desktop VM, one person, under an hour:

1. The published pipe command, choosing `1` and memory `Y`: exit 0, the wall
   opens at `/#pair=…`, the pairing screen vanishes without a keystroke.
2. Re-run the same line: nothing re-installed, service restarted only if the
   code changed, receipt updated. Then hold port 8098 with `nc -l` and re-run:
   fails within seconds naming `--port`.
3. Inject a failure (make `npm ci` fail): the step is named, the log path is
   printed, exit non-zero, no "Ready".
4. Interrupt after the Seed install and before the hooks; re-run: one cron
   block, mesh and hooks present, no duplicate configuration.
5. Log out, log back in: `systemctl --user is-active corral-light` is `active`;
   `crontab -l` shows the cc-seed block; a job has run.
6. `bash ~/tools/corral-light/install.sh --uninstall`: unit, drop-in and timer
   gone, no orphan processes; sign-ins and user files intact.

What was actually run by the author before this synthesis: the full installer
in an isolated fake HOME with stubbed `crontab`, four times (first run, re-run,
first run of 1.1.0, piped first run of 1.1.3), with the hub on port 8099 and
the pre-approved code claimed once; `test_install_sh.py` (20 checks, including
the piped invocation and an injected failure); `test_launch.py`; the full
Corral Light suite. Not run: the systemd service, cron, mesh/hooks, and any
vendor sign-in, because the only machine available is the author's live
system. Items 1, 2, 5 and 6 of the gate are therefore still open.
