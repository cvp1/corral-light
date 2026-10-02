# Astra (gpt-6-astra, high) — round 2

pane 11d02ed1a81d · complete True

install.sh 1.1.1 — **RESHAPE:** two new shell-control regressions break the advertised pipe installation and disable fail-fast handling.

**Cross-examination**

- **Grok:** strongest agreement: the receipt shortcut prevented recovery after partial Seed installation. The new phase reconciliation improves this substantially. Wrong: “prompts never appear” because Bash `printf` lacks a newline. **Bash/platform knowledge:** builtin `printf` and ordinary `tee` do not require newline flushing for this prompt to appear; piping stdout can change CLI behavior, but does not establish that claimed hang.
- **Gemini:** strongest agreement: prebuilt Node needs an explicit libc compatibility check. Wrong: redirecting output through process substitution inherently breaks `ERR` traps. Trap inheritance and asynchronous `tee` failure are separate issues. The proposed explanation was inaccurate—and the replacement logging implementation now explicitly disables error handling. The unclaimed-code accusation also depended on omitted code and is now disproved.
- **My verdicts:** overall **BUILD**, installer and README **RESHAPE** remain. I withdraw the unresolved claim/recovery concern about pairing; that flow is now explained. Opener acceptance still does not establish successful browser pairing.

**Remaining bugs, ranked**

1. **NEW: the script disconnects its own source.** Construct: top-level `exec </dev/null`, before subsequent functions and `main` are parsed. Trigger: the documented `curl … | bash` or `wget … | bash`. **Bash knowledge:** stdin is also Bash’s script input; replacing it can terminate parsing at the next read, leaving the installer unexecuted. File-based tests miss this. **Fix:** remove this redirection; apply `</dev/null` to the final `main` invocation, after all definitions have been parsed.

2. **NEW: fail-fast and error reporting are disabled.** Constructs: `trap - ERR; set +e; main "$@" … | tee …`. Trigger: any ordinary failed command inside `main`, including installation, verification, approval, or service restart. Execution continues and can finish successfully. `rc="${PIPESTATUS[0]}"` also ignores logging failure. **Fix:** restore `set -Eeuo pipefail` and `trap on_error ERR` inside the pipeline’s `main` process; leave the parent able to capture both pipeline statuses immediately and reject either failure.

3. **User-owned content remains unprotected.** Constructs: `git checkout -B "$ref" "origin/$ref"`, unconditional wrapper creation, and uninstall’s `rm -rf "$CL" "$SEED"` / removal of `$BIN/grok`. Trigger: clean local commits, preexisting commands, or modified clones. Branch history is displaced, commands overwritten, or content deleted. **Fix:** record ownership and backups, refuse divergent repositories, and preserve modified/preexisting content. Read the installation receipt for custom workspace/state locations during uninstall.

4. **Restart detection loses failures across runs.** Constructs: `CL_CHANGED=$SYNC_CHANGED` and restart only when checkout/drop-in changed. Trigger: checkout updates, installation fails before restart, then rerun finds unchanged files while the old hub remains active. An active but disabled unit is also never enabled. **Fix:** compare against the last successfully activated deployment, always reconcile enablement, and verify running version/configuration. Ensure custom ports persist for subsequent `launch`; existing unit argument precedence is **unverifiable from the text**.

5. **Seed discovery still fails open.** Construct: `--detect 2>&1 || true` followed by regexes containing `(\S+)`. Trigger: detection failure, changed output, or another workspace containing spaces. Installation may proceed despite another Seed root. **Fix:** require successful structured discovery, canonicalize complete paths, and reject unreadable or ambiguous results.

6. **The memory answer can be ignored.** Construct: `if gated memory-hooks; then skip …` precedes checking `WIRE_HOOKS`. Trigger: rerun an installation with hooks already approved and answer “no.” Hooks remain enabled. Receipt membership also does not prove hooks or mesh still work. **Fix:** distinguish “keep current setting” from “disable,” use Seed’s supported revoke operation for disabling, and verify actual state even when approval exists.

7. **“Ready” still exceeds the evidence.** Constructs: credential-file size checks, `LAUNCH_OK=1` on launch exit zero, and substring-based `hub_alive`. Trigger: stale credentials, an opener that accepts a URL without a working page, or compact JSON health output. **Fix:** parse health JSON, distinguish credential presence from successful authentication, and require browser claim acknowledgement before reporting browser readiness. The supplied claim endpoint still puts the bearer code in a query string; redact that parameter from logs or redeem via POST.

8. **No-terminal selection contradicts the stated policy.** Construct: `[ "$YES" = 1 ] || [ "$TTY_IN" = /dev/null ]` selects all four assistants. Invalid input such as `none` also selects all four. Trigger: unattended execution without `--yes`, or misunderstood selection. **Fix:** require explicit lanes or `--yes` without a terminal; reject invalid selections and offer a clear cancel choice.

**Mom test, round 2**

1. **The advertised paste can silently stop:** fix stdin handling and test the exact published command.
2. **The questions come after package installation:** move choices before package/network work where feasible; provide a tested downloader alternative when curl is missing.
3. **Enter or an invalid answer selects every assistant:** show the selected accounts clearly and default to a modest, explicit choice.
4. **A long download or abandoned login looks stalled:** show progress and elapsed time, with clear cancel/retry instructions. `timeout --foreground 600` sends TERM but is not an absolute bound; add a kill-after interval.
5. **Gemini still needs another action after installation:** guide her directly into its first conversation and keep pending setup visible in the wall.

**Ship gate**

Run these on disposable Ubuntu 24.04 desktop VM snapshots, using real systemd and cron; each check should fit within an hour.

1. **Actual delivery:** run the published pipe command from a fresh terminal, then its saved-file equivalent. Both must reach setup, display prompts, and preserve answers. Include a deliberately truncated download.
2. **Failure handling:** inject a failing dependency command, failed Seed verification, and unwritable log separately. Require nonzero exit, useful diagnosis, and no “Ready.”
3. **Recovery:** interrupt after checkout and after each Seed approval; rerun. Verify service version, one scheduler block, working mesh/hooks, and no duplicate configuration.
4. **Real lifecycle:** install normally, reboot, reopen from a new terminal, test a custom port and an occupied port, then uninstall. Verify scheduled execution and preservation of user-created files.
5. **Real onboarding:** complete an actual conversation with every assistant the operator’s mother will select, using her intended memory setting. Cancel one login and confirm clear recovery.
6. **Browser pairing:** test fresh, expired, and reused codes; broken opener; and no display. Require truthful status and no bearer codes in request/install logs.

The operator: **hold this release—the recovery design is improving, but the published entry point and error handling currently contain release-blocking regressions.**
