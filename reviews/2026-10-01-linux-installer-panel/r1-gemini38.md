# Gemini (gemini-3.8-flash-high) — round 1

pane 595f30ca1c7b · complete True

### 1. Verdict

* **Overall Approach: RESHAPE** — Monolithic installer tries to orchestrate six distinct external ecosystems (distro pkgs, systemd user services, npm node distribution, four vendor AI CLI auth flows, and Seed cron/git hooks) in one brittle shell pass rather than decoupling runtime provisioning from interactive authorization.
* **install.sh: RESHAPE** — Fatal I/O redirection bug silently breaks error trapping, non-standard cron handling breaks modern Ubuntu 24.04, and unescaped heredoc parameter expansions mutate runtime configs during installation.
* **README.md (top): RESHAPE** — Promises a magical zero-friction one-liner ("my mom could use") but hides mandatory interactive `sudo`, four disparate corporate OAuth sign-ins, and complex command-line diagnostics behind a facade.
* **launch.py and static/app.js: RESHAPE** — The CLI side mints and pre-approves correctly, but the browser JavaScript fails to auto-claim the preset code, leaving the user staring at an unnecessary pairing prompt.

---

### 2. The Mom Test

Walking a clean Ubuntu 24.04 LTS desktop:

1. **Terminal Barrier**:
   * *Experience*: She is asked to "Open a terminal and paste this: `curl -fsSL ... | bash`". A non-technical user does not know what terminal shortcut to press, what `curl` is, or how to paste without accidentally triggering unwanted shell escapes.
2. **Sudo Password Prompt**:
   * *Line she sees*: `Missing: git pyyaml cron ... Will run: sudo apt-get install -y git python3-yaml cron`
   * *Experience*: The terminal stops and asks `[sudo] password for <user>:`. She does not know what `sudo` means, why her keystrokes are invisible, or whether typing her password into an untrusted script is safe.
3. **Internal Architecture Jargon (Memory Hooks)**:
   * *Line she sees*: `Memory hooks. Without them, memory is written but never read back into a conversation. Wiring them edits Claude Code's settings.json; ... Wire the memory hooks now? [Y/n]`
   * *Experience*: Complete paralysis. She has no idea what "memory hooks", "settings.json", or "wiring" mean.
4. **Three Uncoordinated Browser OAuth Takeovers**:
   * *Lines she sees*:
     * `Claude: your browser will open to sign in with your Claude account (Pro or Max). (press Enter to continue, or type s to skip)`
     * `Grok: sign in with your X / Grok account.`
     * `ChatGPT: sign in with your ChatGPT account (Plus, Pro or Team).`
   * *Experience*: Her browser pops open 3 to 4 times consecutively requesting external logins. If she lacks paid subscriptions for all four, the installer halts or marks steps as failed.
5. **Dangling Terminal Command**:
   * *Line she sees*: `First thing to try: open a terminal, run cd ~/aios && claude and type /status`
   * *Experience*: Rather than delivering a ready-to-use GUI desktop app, she is told to navigate shell directories and execute subcommands.

---

### 3. Bugs and Reliability Failures

1. **`exec > >(tee -a "$LOG")` Breaks `trap ... ERR` and Subshell Error Handling**
   * *Construct*: `exec > >(tee -a "$LOG") 2>&1` (Line 115) combined with `set -e` and `trap on_error ERR`.
   * *Trigger*: In Bash, piping file descriptors through asynchronous process substitution causes standard error traps to miss failures inside pipeline segments or subshells, and background `tee` processes can race with script exit.
   * *User sees*: Silent premature exits or hanging installs without triggering `on_error`.
   * *Fix*: Avoid `exec > >(tee ...)`. Instead wrap the invocation or tee explicitly inside targeted commands, or invoke `exec > >(tee -a "$LOG")` only after setting `set -o pipefail` and handling subshell trap propagation via `set -E`.

2. **Cron Package Missing / Deprecated on Modern Distros (Ubuntu 24.04)**
   * *Construct*: `apt:cron) out+=(cron) ;;` (Line 173) and `systemctl enable --now "$cron_unit"` (Line 210).
   * *Trigger*: Ubuntu 24.04 LTS drops standard `cron` by default in favor of `systemd-timesyncd` and systemd timers. Installing `cron` works, but checking `systemctl list-unit-files "$u.service"` fails if the daemon is masked or replaced by `cronie`/`systemd-cron`.
   * *User sees*: Warning `could not start cron — Seed's scheduled jobs will not run until it is`.
   * *Fix*: Standardize on native systemd user timers instead of requiring a root-managed cron daemon.

3. **CLAUDE.md Staged Heredoc Evaluates Variables at Install Time**
   * *Construct*: `cat > "$AIOS/.cc-seed/staged/claude-md.proposed" <<EOF ... \`$AIOS\` ... EOF` (Lines 319–358).
   * *Trigger*: The heredoc delimiter `<<EOF` is unquoted. Bash expands `$AIOS`, `$CL`, `$SEED`, and any backticks inside the documentation block at install time.
   * *User sees*: Staged content corrupted or shell evaluation syntax errors during generation if backticks are evaluated.
   * *Fix*: Quote the heredoc delimiter: `cat > "..." <<'EOF'` and interpolate only strictly needed environment variables through controlled templating (e.g., `envsubst` or sed).

4. **Preset Pairing Code in `static/app.js` Is Never Claimed**
   * *Construct*: `app.js` sets `code = preset; ttl = 300; how = ...` but does not issue a claim/redeem call to `/api/pair/claim` or the hub's authorization endpoint.
   * *Trigger*: `corral-light launch` opens `http://127.0.0.1:8098/?pair=ABC-XYZ`.
   * *User sees*: The browser wall opens, strips `?pair=` from the address bar, but remains stuck on the pairing modal showing `ABC-XYZ` waiting for manual approval.
   * *Fix*: In `app.js`, when `preset` is non-null, immediately invoke `await api('/api/pair/claim', { code: preset })` to obtain the session cookie and dismiss the modal.

5. **Antigravity Installer Missing Executable/Flag Guarantees**
   * *Construct*: `python3 "$CL/install_antigravity_acp.py" --install` (Line 282).
   * *Trigger*: Script fails if external dependencies (e.g. system glibc, tarballs, or missing python requests) fail; download is 1.5 GB without bandwidth timeout checks.
   * *User sees*: Step 6 fails mid-download, terminating the script via `set -e`.
   * *Fix*: Wrap execution in retry logic and verify download checksum before triggering installation.

6. **Hardcoded Port Conflict Detection**
   * *Construct*: `curl -fs --max-time 2 "http://127.0.0.1:$PORT/health"` (Line 395).
   * *Trigger*: Port 8098 occupied by another application (or a zombie hub instance).
   * *User sees*: If another service runs on 8098, `install-service` succeeds but the service fails to bind; curl health check times out after 60s and script aborts: `die "The hub did not answer on port 8098 within 60 s."`.
   * *Fix*: Check port availability before starting: `python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', $PORT))"` and prompt or increment port automatically.

7. **`claude auth status` JSON Parsing Failure**
   * *Construct*: `"$BIN/claude" auth status 2>/dev/null | grep -q '"loggedIn": true'` (Line 414).
   * *Trigger*: Anthropic updates Claude CLI output format (plain text, markdown, or updated JSON schema keys like `"status": "authenticated"`).
   * *User sees*: False negative; installer repeatedly forces user into `claude auth login` on every installer run even if already logged in.
   * *Fix*: Test execution exit status or use robust JSON parsing: `claude auth status --json 2>/dev/null | python3 -c "import sys, json; sys.exit(0 if json.load(sys.stdin).get('loggedIn') else 1)"`.

8. **`ask_yn` Default Logic Overrides Explicit Input on EOF**
   * *Construct*: `read -r ans < "$TTY_IN" || ans=""` (Line 100).
   * *Trigger*: Non-interactive terminal redirect or severed terminal connection during piped execution (`curl | bash`).
   * *User sees*: If `/dev/tty` is unavailable, defaults are automatically selected without user consent, potentially overwriting settings.
   * *Fix*: Explicitly check `[ -t 0 ]` and fail fast if user interaction is required but no TTY exists.

9. **`launch.py` Assumes Localhost Only**
   * *Construct*: `if bind in ("0.0.0.0", "", "::"): bind = "127.0.0.1"` (Line 38).
   * *Trigger*: Hub executed in WSL2, container, or remote VM where the browser is on the host machine.
   * *User sees*: Browser attempts to connect to `127.0.0.1` inside a container or host where port 8098 is not forwarded.
   * *Fix*: Respect `CORRAL_LIGHT_PUBLIC_URL` or display clear host IP instructions when running headless/remote.

10. **Systemd User Unit Drop-in PATH Injection Vulnerability**
    * *Construct*: `Environment=PATH=$SERVICE_PATH` (Line 377).
    * *Trigger*: `$HOME/.local/bin` is prepended to the systemd service PATH before `/usr/bin`.
    * *User sees*: Any rogue binary placed in `~/.local/bin` shadows system utilities executed by the hub service.
    * *Fix*: Order system directories first or isolate runner environments: `Environment=PATH=/usr/local/bin:/usr/bin:/bin:$BIN:$NODE_DIR/bin`.

11. **Idempotency Failure on Repo Checkout with Uncommitted User Edits**
    * *Construct*: `sync_repo` skips updates if `git status --porcelain` is non-empty (Line 223).
    * *Trigger*: User edits `~/tools/corral-light` or `~/tools/ai-os-seed`.
    * *User sees*: Installer outputs a warning and silently leaves outdated code; subsequent steps expecting new features fail down the line.
    * *Fix*: Warn and prompt the user explicitly whether to stash changes or abort.

12. **Codex Token Path Hardcoded Across Environments**
    * *Construct*: `CODEX_HOME_DIR="$HOME/.config/corral-light/codex-home"` (Line 432).
    * *Trigger*: Codex ACP adapter expects auth at standard `$CODEX_HOME` or default home directory; environment variable is only exported during login subshell, not persisted globally.
    * *User sees*: ChatGPT lane fails in the web wall because the systemd service does not inherit `CODEX_HOME`.
    * *Fix*: Add `Environment=CODEX_HOME=%h/.config/corral-light/codex-home` to `10-installer.conf`.

---

### 4. Security

* **`curl | bash` Pattern**:
  * *Risk*: Real. MitM or compromised CDN can serve arbitrary code directly into user context.
  * *Fix*: Publish checksums/GPG signatures; instruct users to download, inspect, and run: `curl -O ... && bash install.sh`.
* **Unpinned Claude Code Installer**:
  * *Risk*: High. `curl -fsSL https://claude.ai/install.sh | bash` downloads and runs unpinned, mutable remote code without hash verification.
  * *Fix*: Pin the Anthropic installer script hash or install a pinned version via npm/binary archive with SHA-256 verification.
* **Pre-approved Pairing Code in URL (`/?pair=...`)**:
  * *Risk*: Moderate. URLs leak via browser history, process listing (`ps aux` showing browser command line), and web proxy logs.
  * *Fix*: Pass code via URL hash fragment `/#pair=...` (never sent over HTTP, not stored in server logs) and enforce a strict 60-second single-use expiry.
* **PATH Edits to `~/.profile` and `~/.bashrc`**:
  * *Risk*: Low. Standard pattern, but appending unquoted strings can allow injection if directory paths contain spaces.
  * *Fix*: Use safe quoting and check if path is already present in `PATH` before appending.
* **Drop-in `Environment=PATH=` Line**:
  * *Risk*: Moderate. Placing user-writable directories (`$BIN`, `$NODE_DIR/bin`) ahead of system paths in a persistent service allows persistent local binary hijacking.
  * *Fix*: Restrict permissions on `$BIN` and place system bin dirs first.

---

### 5. What Is Missing for "100% Reliable and Repeatable"

1. **Systemd User Session Linger Activation Verification**:
   * *Problem*: `loginctl enable-linger` requires root/polkit privileges; if it fails, the user service terminates on logout.
   * *Addition*: Verify linger state via `loginctl show-user $USER --property=Linger` and provide a clear warning if it cannot be enabled without sudo.
2. **Dynamic Free Port Negotiation**:
   * *Problem*: Hardcoded port `8098` causes catastrophic installation halt if already bound.
   * *Addition*: Implement fallback port scanning in `launch.py` and `hub.py`, updating the receipt and drop-in configuration accordingly.
3. **Automated Offline Dependency Bundling**:
   * *Problem*: Flaky network during the 1.5 GB Gemini runtime or Node tarball download breaks the run mid-stream.
   * *Addition*: Resume support (`curl -C -`) and pre-flight disk space check (verifying at least 3 GB free before starting).
4. **Node Architecture & Glibc Verification**:
   * *Problem*: Downloaded Node v24 LTS tarball requires Glibc 2.28+; running on musl distros (Alpine) or older Glibc crashes immediately.
   * *Addition*: Run `ldd --version` check prior to downloading prebuilt Node binaries, falling back to distro node if incompatible.
5. **Headless / Browserless Graceful Degrade**:
   * *Problem*: When no desktop exists, the installer claims "the browser URL will be printed", but script flow assumes browser pairing.
   * *Addition*: Explicitly detect SSH/headless sessions and format the output with an ASCII QR code or copy-paste link with token.
6. **Atomic Rollback Mechanism**:
   * *Problem*: A failure at Step 7 leaves half-installed systemd units and cloned git repos in an invalid state.
   * *Addition*: Trap errors to run an internal cleanup/rollback of incomplete steps before exiting.

---

### 6. Automated Regression Tests

1. **Clean Room Container Test (Ubuntu 24.04 LTS)**:
   * *Test*: Run `install.sh --yes --skip-logins` in a minimal `ubuntu:noble` Docker image with `systemd` enabled.
   * *Validation*: Verify exit code 0, all receipts written, systemd service active, and curl to `/health` returns 200.
2. **Idempotence & No-Op Test**:
   * *Test*: Run `install.sh --yes --skip-logins` twice consecutively on the same VM.
   * *Validation*: Second run completes with zero package installations, zero modified git files, and identical `install-receipt.json`.
3. **Dirty Git Repo Conflict Test**:
   * *Test*: Modify a tracked file in `~/tools/corral-light` and run `install.sh`.
   * *Validation*: Script exits gracefully with a distinct error code without clobbering uncommitted work.
4. **Custom Port Collision Test**:
   * *Test*: Spin up a dummy `nc -l 8098` socket and run the installer with `--port 8098`.
   * *Validation*: Assert installer rejects or relocates port gracefully rather than timing out on curl health check.
5. **Pairing Handshake Verification Test (Python Playwright)**:
   * *Test*: Run `corral-light launch --print`, capture URL, navigate via headless Chromium, and verify session cookie exchange.
   * *Validation*: Page reaches dashboard without human intervention; pairing modal disappears automatically.
6. **Uninstall Cleanliness Audit**:
   * *Test*: Run `install.sh --uninstall` answering yes to all prompts.
   * *Validation*: Inspect `$HOME` and systemd directories to verify no orphan unit files, drop-ins, or path leaks remain.

---

### 7. The Single Point of Failure

The installer will most likely fail on the operator's mother's machine when it abruptly halts at an intimidating `[sudo]` password prompt to install basic packages like `cron` and `git`, followed by opening multiple confusing browser tabs demanding paid account logins before showing any working user interface.
