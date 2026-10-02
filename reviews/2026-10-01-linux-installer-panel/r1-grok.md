# Grok (grok-4.7, xhigh) — round 1

pane 9c83eaa096c0 · completed after a human nudge in the pane ("are you still working on this?"); the consult call itself had already returned empty

**Overall approach: RESHAPE** — a native per-user install, private Node, a systemd user unit, and Seed `--approve` fit the constraints, but this one-liner is not yet something a non-technical person can finish twice with the same result.
**install.sh: RESHAPE** — a Seed receipt skips the rest of setup, a second run never restarts the hub, and the sign-in prompts wait on input she cannot see.
**README top: RESHAPE** — it promises a pinned, self-repairing install that leaves her signed in, and the uninstall command it prints is not left on disk by the one-liner.
**launch.py and `?pair=`: RESHAPE** — a pre-approved code in the query string is a bearer token, and the page keeps it only when it matches `XXX-XXX`, so a format mismatch dumps her on the manual pair screen.

## The mom test

Fresh Ubuntu 24.04 desktop, stock user, no git, curl, PyYAML, Claude, Node, or these repos. She can reach a browser. She cannot interpret sudo, cron, JSON, or “lanes.”

1. The README says “Open a terminal and paste this,” then the `curl … | bash` line. There is no link to click. Finding Terminal is the first stop. The operator’s bar was a link that ends in a running session.
2. Step 2 almost always runs, because git, curl, and `python3-yaml` are missing. She sees `Missing: git curl pyyaml` and `Will run: sudo apt-get install -y …`, then `[sudo] password for <name>:`. The line “(your password is for the system package manager, nothing else)” is the only explanation. If she is not an admin, sudo says she is not in the sudoers file and the script exits. She cannot fix that.
3. After packages, Node, `npm ci`, Claude’s installer, and a default Gemini lane: `Downloading the Antigravity runtime (about 1.5 GB, checksum verified)…` Then silence for a long time. The one-liner chose all four lanes, including the 1.5 GB one.
4. Seed then prints: `Memory hooks. Without them, memory is written but never read back into a conversation. Wiring them edits Claude Code's settings.json…` and `Wire the memory hooks now? [Y/n]`. That `[Y/n]` is a `printf` with no newline, after `exec > >(tee …)` has pointed stdout at a pipe. The script is non-interactive, and `read` is from `/dev/tty`, so bash does not flush. She sees the paragraph and a freeze. Enter would accept the default Yes. She has no way to know that.
5. Same hang, three times, after: `Claude: your browser will open to sign in with your Claude account (Pro or Max).` / `Grok: sign in with your X / Grok account.` / `ChatGPT: sign in with your ChatGPT account (Plus, Pro or Team).` The visible sentence does not say that the next key is Enter, or that `s` skips. She needs three paid accounts she was not asked to pick. Each login can sit forever. Ctrl-C kills the whole installer. There is no trap on INT.
6. If a browser does open, the wall is paired only when `auth.new_code()` matches `/^[A-Z0-9]{3}-[A-Z0-9]{3}$/`. That format is unverifiable from this paste. On a mismatch the page drops `?pair=` and shows a normal code plus `corral-light pair XXX-XXX`. She cannot run that.
7. The closing line is `open a terminal, run cd ~/aios && claude and type /status`. `curl | bash` does not change the parent shell’s PATH. In that same terminal, `claude` and `corral-light` are “command not found” until she opens a new one. `~/.bashrc` is updated; the shell she is looking at is not.
8. On any failure she sees `Step failed: <name>`, `The full log is at ~/.local/share/corral-light/install.log`, and `Fix what it names, then run the same command again — it continues where it left off.` A Python traceback or `journalctl --user -u corral-light -n 50` is not something she can act on. The “left off” claim is false once a Seed receipt exists.

## Bugs and reliability

1. **Seed receipt skips the rest of step 7.** `if [ -f "$AIOS/.cc-seed/receipt.json" ]; then skip "already installed"`. Trigger: `install.py --target` writes the receipt, then a selftest, `--approve`, `sync.sh`, or the audit fails. Second run skips CLAUDE.md, mesh, hooks, cron, and audit, and still prints success. She sees `· already installed (receipt present)` and a hub with no floor. **Fix:** persist a stage list (`installed`, `verified`, `claude-md`, `mesh`, `hooks`, `cron`, `audit`) and rerun any stage that is missing. Treat the receipt as a checkpoint, not as “done.”

2. **Second run does not restart the hub.** `enable --now corral-light.service >/dev/null 2>&1 || restart`, and the health loop accepts any HTTP 200. Trigger: unit already active, or a stale process on 8098. Git updates and the new `Environment=PATH` drop-in sit unused. `enable --now` on an active unit does not restart it, and both systemctl errors are discarded. **Fix:** `daemon-reload` then `restart`, and accept `/health` only when it reports this receipt’s commit, or when `systemctl show -p MainPID` owns the port.

3. **Prompts never appear under `curl | bash`.** `exec > >(tee -a "$LOG") 2>&1`, then `ask_yn` / `press_enter` use `printf '… [Y/n] '` with no newline and `read -r ans < "$TTY_IN"`. Trigger: the documented one-liner. stdout is a pipe. The script is non-interactive, so `read` does not flush. She sees a hang at the memory-hooks question and at each sign-in. **Fix:** write every prompt with `printf '…\n' >/dev/tty` (closing `/dev/tty` flushes), and run `stdbuf -oL tee`.

4. **No tty still starts logins.** `TTY_IN=/dev/null` makes `press_enter` return success, then `claude auth login < /dev/null` (and grok, codex). Trigger: `ssh host 'curl … | bash'` without `-t`, or no `/dev/tty`. The login waits forever, or fails in a loop. **Fix:** if `/dev/tty` is missing, force `SKIP_LOGINS=1` and say so.

5. **`--detect` grep can abort a clean machine or hide a second Seed.** `grep -E 'INSTALL$|install root' | grep -v -F "$AIOS"`. Trigger: `--detect` prints `no install root` (matches, path absent, false “another install”), or another root `/home/mom/aios-old` while `AIOS=/home/mom/aios` (substring, false “clear”). Exact `--detect` text is unverifiable from this paste. The filter is unsafe either way. **Fix:** `install.py --detect --json` and compare canonical paths for equality.

6. **Port 8098 in use becomes a 60-second failure she cannot read.** The loop `curl …/health` then `die "The hub did not answer on port $PORT within 60 s"` and points at `journalctl`. Trigger: another listener, or the unit crash-looping while an old hub still returns 200 (bug 2). **Fix:** before start, if the port is taken and `/health` is not ours, name the PID and exit immediately with `rerun with --port N`.

7. **`claude auth status` is a brittle string match, and login has no timeout.** `"$BIN/claude" auth status | grep -q '"loggedIn": true'`. Trigger: compact JSON (`"loggedIn":true`), a text status, or exit 1 while logged in. She is pushed through OAuth every run. A changed Claude CLI making this fail is consistent with how often that command’s format has moved. **Fix:** parse JSON in Python, accept only a boolean, and run each login under `timeout 300`. On timeout, warn and continue.

8. **Clean local commits are thrown away.** `git checkout -q -B "$ref" "origin/$ref"` after a porcelain check that ignores commits. Trigger: second run, committed local edits, clean worktree. The branch pointer moves to `origin/master`. **Fix:** `git merge --ff-only`, and stop with a message when that cannot fast-forward.

9. **Corral Light is not pinned, so “repair” and “the same install” are different trees.** `CORRAL_LIGHT_REF` defaults to `master`. `npm ci` follows whatever master is that minute. Two machines a month apart differ. A dirty tree warns and `return 0`, so the run still reports success on old code. **Fix:** default to a tag or full SHA, record `git rev-parse HEAD` in the receipt, and fail the run when the checkout is not that commit.

10. **Cron is started only through systemd, and the log banner can die on Alpine before the trap exists.** `systemctl list-unit-files cronie|cron|crond` then `sudo systemctl enable --now`. Trigger: Alpine/OpenRC, a chroot, or cron started by a non-systemd supervisor. The `if` swallows the failure. Scheduled jobs never run. Separately, `date -Is` runs before `trap on_error`, and BusyBox `date` rejects `-Is`. **Fix:** refuse non-systemd in this release, or start cron with `service` / `rc-service`. Use `date -Iseconds` and, on failure, `date -u +%Y-%m-%dT%H:%M:%SZ`.

11. **The service drop-in is one unquoted line, and enable errors are deleted.** `Environment=PATH=$SERVICE_PATH` with `$HOME` expanded. Trigger: a space in `$HOME` splits the assignment. A `%` is a systemd specifier (`%h`, `%u`). She sees a hub that exits at start, with the systemctl reason in `/dev/null`. **Fix:** write `Environment="PATH=…"` with `%` doubled, and leave systemctl stderr in the log.

12. **A wrong `claude` on `PATH` skips Anthropic’s installer.** `[ -x "$BIN/claude" ] || have claude`. Trigger: any other `claude` binary. Grok’s check, `awk '{print $2}'`, reinstalls forever or skips forever if `--version` is not `name version`. That output format is unverifiable from this paste. **Fix:** require `$BIN/claude` and a version flag you parse in one place. Same for grok.

`--yes` means “take the default,” and uninstall defaults are No, so `--yes --uninstall` removes the unit and the private Node and keeps Seed, clones, and state. `list-unit-files corral-light.service` exits 0 even when that unit is absent, so uninstall always enters the remove branch.

`python3` below 3.9 does fail closed (`python3 is $PYV; 3.9 or newer is required`). arm64 Node uses the `linux-arm64` tarball name, which matches nodejs.org. Whether those two SHA-256 lines are the real v24.21.0 digests is unverifiable from this paste. Whether `install_antigravity_acp.py` has an arm64 build is unverifiable too.

## Security

**`curl | bash` from `master` is a real supply-chain break.** TLS checks GitHub, not the script. A truncated pipe can execute a prefix because the body runs as it arrives. A redirect (`-L`) is followed. **Fix:** end the file with `main "$@"` so a short read runs nothing. Publish a SHA-256 next to a URL that pins a commit. Have the script check its own hash, or download to a file and then exec it.

**Claude Code is not pinned.** `curl -fsSL https://claude.ai/install.sh | bash -s stable` is a second live pipe, and the stable channel updates itself. Node, the npm lock, Grok `1.0.46`, and Antigravity’s hash are pinned. The adapter lock is only a pin while the git ref is fixed, and the git ref is `master`. **Fix:** install a pinned `@anthropic-ai/claude-code` tarball or npm version, check SHA-256, and record that version in the receipt.

**`?pair=` is a real local bearer token, with a small blast radius.** `auth.approve()` runs before the browser opens. The code sits in the Firefox/Chrome command line (`ps`), in the hub’s request log, and in `install.log` if `xdg-open` is missing (`launch` prints the URL, and the installer tees stdout). It is single-use, five minutes, and bound to loopback, so this is not a remote hole. It is weaker than `corral-light pair`, which required a shell as the user. `replaceState` runs too late to keep it out of the access log. **Fix:** put the code in the fragment (`#pair=`), redeem it with a POST, keep a 60-second TTL, and never print or log the URL. `RuntimeError` from `approve()` is uncaught and shows a traceback. Catch it and exit 3.

**`PATH=` lines in `~/.profile` and `~/.bashrc` are not a new privilege.** Prepending `~/.local/bin` is the normal user-bin pattern. Ubuntu’s stock `.profile` already adds that directory when it exists at login, so this often writes a second export. It does not update the shell that launched the installer, and it skips graphical apps until re-login. **Fix:** append only when `~/.local/bin` is absent from both files and from the current session path. Tell her to open a new terminal.

**The drop-in `Environment=PATH=` is not a privilege boundary** (the user already owns the unit). The real fault is parsing: spaces and `%`. Quote it, as in bug 11. Do not put secrets in the drop-in. This one does not.

## Missing for a repeatable install

1. **One immutable pin set.** Tag or full SHA for both repos, SHA-256 for Node, Claude, Grok, and Antigravity, written into `install-receipt.json`. The installer refuses to continue when `HEAD` disagrees.
2. **A checkpoint file and an always-restart.** Stages from bug 1. After every successful run, restart the user unit and require `/health` to match the receipt.
3. **A preflight a person can pass.** Free disk (2.5 GB if Gemini is in `--lanes`), python ≥ 3.9, a passwordless `sudo -n true` probe that fails with one sentence, port free, and `/dev/tty` present or logins skipped. Default lanes to none until she picks the one account she has. Gemini stays opt-in.
4. **Progress and a bounded login.** The 1.5 GB download prints bytes. Each login has a five-minute cap and a visible “press Enter, or type s” on `/dev/tty`.
5. **Distros this release actually supports.** CI on Ubuntu 24.04 and Fedora, x86-64 and arm64. Anything without systemd user sessions or with python 3.8 exits at step 1 with one line. Drop Alpine from the README until OpenRC is implemented.
6. **A file she can run again.** Save `~/tools/corral-light/install.sh` and print `bash ~/tools/corral-light/install.sh --uninstall`. The README’s `bash install.sh --uninstall` refers to a file the one-liner never creates.

## Tests

1. **`shellcheck -S warning install.sh` and `bash -n install.sh`** in CI on every push.
2. **Grep fixture.** Extract the detect filter. Feed “no install root”, this path, another path, and `/home/u/aios-old` vs `/home/u/aios`. Fail on a false hit. Pure Python or bash, no root.
3. **`launch.py` against a fake `auth` and a localhost `/health`.** Down → exit 2. Approved code → URL fragment matches the JS regex. No `DISPLAY`/`WAYLAND_DISPLAY` → URL on stdout, exit 0, no browser. `approve` failure → exit 3, no traceback. `pytest` on the runner, no browser.
4. **Second run in a rootless podman Ubuntu 24.04** with file:// git repos, `--yes --skip-logins --no-schedule --lanes claude`, network stubs for Node. Run twice. Assert one PATH export, the unit’s `MainPID` changed after a sentinel edit, and the receipt SHA equals `git rev-parse HEAD`.
5. **Prompt flush.** Run the script with stdout piped to `cat` and a PTY as `/dev/tty` (Python `pty`). Assert `Wire the memory hooks now?` is on the PTY before the script reads. This fails on the current `tee` exec.
6. **Truncation and busy port.** `head -c 1500 install.sh | bash` must not create `~/tools` (fails until `main` is at the end). A Python socket on 8098 must make the installer exit in under 5 seconds with `--port` in the message.

## What fails on her machine

The one-liner reaches a sudo password and then an invisible “press Enter” in front of Claude, Grok, and ChatGPT sign-in — three accounts she was never asked whether she has — while a 1.5 GB Gemini download runs because that is the default.
