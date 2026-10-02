# Grok (grok-4.7, xhigh) — round 2

pane 9c83eaa096c0 · complete True

**Approach: BUILD** (was RESHAPE). Phase reconciliation, a quoted user-unit drop-in, identity-checked `/health`, and an honest Ready line are the right installer. **install.sh 1.1.1: RESHAPE. launch.py: BUILD**, on the described fragment, exit 3, and exit 4, plus the claim/expiry run. The new `launch.py` is not in this paste.

## Cross-examine

**Astra.** Most agreed: the advertised `curl | bash` line cannot install curl, so a machine without it never reaches the script. I underweighted that. Wrong as a design: rollback and transactional staging. `apt-get` and vendor OAuth cannot be undone as one transaction. Astra's own fix, per-phase reconcile, is the right one, and 1.1.1 does it. Root is now refused before uninstall.

**Gemini.** Most agreed: a sudo prompt plus several paid browser logins is the real wall, and the round-1 paste did not show a pair claim. That claim is retired: the author describes a 1.5 s `/api/pair/claim` poll, `expired` mints a fresh code, and a second claim expired in their run. Wrong on the facts: Ubuntu 24.04 did not drop `cron` for `systemd-timesyncd`. Timesyncd is NTP. The `cron` package is still the right dependency (platform knowledge). The heredoc backticks are escaped on purpose. Putting `/usr/bin` ahead of the private Node would break the pin. `CODEX_HOME` in the unit is unnecessary if `codex_launcher.py` sets it; that file is not in this paste.

## Re-verdict

**install.sh 1.1.1: RESHAPE** — resume, port, health, and Ready are fixed, but the new `main | tee` footer turns errexit off inside `main` and hides the two questions.

## Remaining bugs

1. **New. `main` runs with `set +e` and no ERR trap.** `trap - ERR` and `set +e` run before `main "$@" 2>&1 | tee`. A pipeline stage is a subshell and inherits that state. `set -E` cannot pass on a trap that was just cleared. Bare failures no longer stop the run: `python3 "$SEED/install.py" --target "$AIOS"` can fail and the next line still prints `✓ installed`. The selftests are the same shape, and `>/dev/null` throws away the traceback, so the new “lines just above” sentence is false. Later `|| die` and the audit sometimes stop it; that is accidental. **Fix:** first lines of `main` are `set -e` and `trap on_error ERR`. Keep the parent’s `set +e` only so it can read `PIPESTATUS[0]`.

2. **New. The two questions are written into a block-buffered pipe.** `printf '…press Enter for all four: '` and `ask_yn` / `press_enter` have no newline, then `read < /dev/tty`. Platform knowledge: bash block-buffers stdout when it is a pipe, and a non-interactive `read` does not flush. She gets a blank terminal while the script waits. **Fix:** `printf … >/dev/tty` for every prompt (closing the tty flushes), and log with `stdbuf -oL tee`.

3. **New. No terminal selects every lane.** The comment and `ask_yn` say no tty and no `--yes` means no. The lane block does the opposite: `if [ "$YES" = 1 ] || [ "$TTY_IN" = /dev/null ]; then LANES="claude,codex,grok,gemini"`. An SSH session without a tty, packages already present, downloads Gemini and all four CLIs, skips hooks, and skips logins. **Fix:** if there is no tty and no `--lanes`, exit 2 and name `--lanes` and `--yes`.

4. **New. Any answer without the digits 1–4 means all four.** `if [ -z "${picks//[^1-4]/}" ]; then LANES=all`. Enter is documented. `claude`, `no`, or `help` is not: those also install Gemini. **Fix:** empty input is Claude only; anything outside `[1-4]` and spaces asks again.

5. **Seed detect still fails open.** `detect="$(python3 "$SEED/install.py" --detect 2>&1 || true)"` plus two regexes. A crash, or a line that does not match `install root` / `dir:`, installs a second Seed. Realpath compare is an improvement. **Fix:** require exit 0 and one JSON field; abort when the field is missing.

6. **The Claude pin is skipped whenever any `claude` exists.** `claude_bin` accepts `$BIN/claude` or `command -v claude`, then the installer never runs `bash -s 2.1.285`. A different or older binary sticks. The installer script at `claude.ai/install.sh` is still an unpinned pipe; only the version argument is pinned. **Fix:** install when `claude --version` is not `2.1.285`, and record the version the binary actually prints.

7. **`git checkout -B "$ref" "origin/$ref"` drops local commits** when the worktree is clean. A dirty tree warns, returns, and leaves `CL_CHANGED=0`, so the running unit is not restarted. **Fix:** `git merge --ff-only`; if the tree is dirty or the merge is not fast-forward, stop with that sentence.

8. **The glibc probe puts `sort -V | head -1` back under `pipefail`.** It sits inside `if`, so a SIGPIPE 141 is unlikely to abort, and `head` still prints the line. This is the class that already broke a real run. **Fix:** `printf '%s\n%s\n' 2.28 "$glibc" | sort -C -V`.

## Mom test, round 2

1. **`curl: command not found`.** Fresh Ubuntu desktop images include `python3` and often not `curl` (platform knowledge). The script never starts. **Fix:** README bootstrap via `python3` and `urllib.request`, saving `install.sh`, then `bash` that file. Pin the URL to a commit.
2. **A blank wait where the questions should be.** Bug 2. She never sees “Type the numbers, like 1 3”. **Fix:** prompts on `/dev/tty`.
3. **sudo still comes before those questions.** `Missing: … Will run: sudo apt-get install -y …` and “Nothing shows while you type it.” That sentence is good. She still cannot proceed if she does not know the password or is not an admin. **Fix:** ask the two questions first; keep the password sentence.
4. **Enter still means Claude, ChatGPT, Grok, and a 1.5 GB Gemini download,** then one sign-in each. **Fix:** empty input is Claude only; Gemini only if she types 4.
5. **The memory question runs even when she did not choose Claude,** and it still names `settings` and `install.py --revoke`. **Fix:** ask only when `claude` is selected; one yes/no, no command names.

## Ship gate

One fresh Ubuntu 24.04 desktop VM, nothing preinstalled. All six, then it is fair to send.

1. Run only the documented command. It must download and start. A `curl: command not found` is a fail.
2. The lane question and the memory question are visible before any download. Answer `1` and yes. Confirm the log shows no Antigravity download and no second assistant login.
3. Finish or skip Claude on a visible “press Enter / type s”. The browser opens on `/#pair=`. The wall is usable without typing a code. Loading that same URL again does not stick on a dead code.
4. Log out and back in. `systemctl --user is-active corral-light` is active. A new terminal finds `corral-light` and `claude`. `crontab -l` shows the seed block. `curl -fsS http://127.0.0.1:8098/health` contains `"service": "corral-light"`.
5. Re-run and choose `1` again. Exit 0, one PATH export, Claude still signed in. Bind 8098 with another process and run once more: it exits in a few seconds and names `--port`.
6. Rename one file the Seed selftest needs and re-run. The installer exits non-zero and the traceback is on screen, not only in a later audit line.

Service, linger, cron, mesh, and a real Claude login were read, not run, on the author’s machine. Items 3, 4, and 6 are the ones that close that gap.

Do not send this build: the logging change turns off `set -e` inside `main` and can hide the only two questions, and a stock Ubuntu desktop often has no `curl` to start the paste line.
