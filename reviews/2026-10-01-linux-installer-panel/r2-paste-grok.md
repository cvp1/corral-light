ROUND 2 — same panel, same fixed constraints C1–C5, same rules (text only; platform knowledge allowed and labelled).

WHAT HAPPENED SINCE ROUND 1
The author applied the round-1 findings that were verifiable and shipped install.sh 1.1.1 (1.1.0 plus: an INT trap that explains a re-run; each sign-in capped at ten minutes with `timeout --foreground`; the summary and README name `bash ~/tools/corral-light/install.sh --uninstall`, the copy the clone keeps; launch.py catches an approve failure (exit 3) and treats an opener that exits non-zero within 3 s as failure (exit 4, URL printed); the end summary says to open a NEW terminal). Changes: the two questions (which assistants, memory on/off) moved to the start, in plain words; `--lanes` is validated at parse time; `exec </dev/null` so no child can eat the piped script, with questions read from /dev/tty; logging is `main | tee` with PIPESTATUS, and `set -E` so the ERR trap reaches functions; `die` names the step and the log; a glibc 2.28+ / musl check via getconf; a free-disk check (1.2 GB, 3.6 GB with Gemini); the internet check now runs after the package step; a port-in-use check before starting the hub, and the health wait requires the JSON to identify itself as corral-light; the systemd drop-in quotes its Environment= lines and the service is restarted when the checkout or drop-in changed; the Seed step reconciles each phase on a re-run (install → verify → CLAUDE.md region → mesh → hooks → sync → audit), reading gated_writes from Seed's receipt instead of hiding behind the receipt's existence; the Claude login check parses JSON; the codex binary is checked to exist; `--yes` means "all four assistants, memory on"; no terminal and no --yes answers every question "no"; every `cmd | grep -q` was removed (pipefail + SIGPIPE made the glibc probe fail on a real machine — a round-1 reviewer predicted this class); Claude Code is installed at a pinned version through Anthropic's installer; the pairing code moved to the URL fragment (`/#pair=`), and the page strips it with replaceState; the end summary lists, per assistant, signed-in or the exact command, and says "Ready" only when every chosen assistant is signed in and the browser opened.
Also: the page's full pairing flow was cut off in the round-1 paste. The part you did not see: after the code is shown, a 1.5 s poll calls `/api/pair/claim?code=…`; `ok` hides the pairing screen and starts the app; `expired` (unknown, used, or timed-out code) discards the preset and mints a fresh code the normal way. So a preset code IS claimed, and expiry recovers.
The installer was run end to end twice in an isolated fake HOME with stubbed `crontab` (first run and re-run), with the hub started on port 8099 and the pre-approved code claimed once (second claim: expired). The service, cron, mesh and login steps were exercised only by reading, not by running, because the test machine is the author's live system.

Rejected or deferred, with reasons: "switch Seed to systemd timers" (Seed's Linux scheduler is cron by design, out of scope); "a desktop double-click entry point" (Linux desktops block untrusted .desktop launchers; the README now states the one-paste-line floor honestly); "rollback" (the design is idempotent re-run, not transaction); "Ubuntu 24.04 dropped cron" (not true — the `cron` package is present; it is installed if missing); "CODEX_HOME is not persisted for the service" (not needed — codex_launcher.py sets CODEX_HOME to that path itself).

Below are the other two reviewers' round-1 reviews, then the NEW install.sh 1.1.1 in full.

YOUR JOB — converge
1. CROSS-EXAMINE. For each other reviewer: the finding of theirs you most agree with, and the claim you think is WRONG (factually or as a design), with why. Change any of your own round-1 verdicts if warranted and say so.
2. RE-VERDICT on install.sh 1.1.1: BUILD / RESHAPE / KILL, one line.
3. REMAINING BUGS in 1.1.1, ranked, at most 8, each with the exact construct, the trigger, and a fix. New bugs introduced by the changes count double.
4. THE MOM TEST, round 2: on the 1.1.1 flow (two questions, then unattended, then sign-ins), what still stops her? At most 5, each with the fix.
5. SHIP GATE: the shortest list of checks (at most 6) that, if green, would justify telling Craig "send this to your mother". Each must be runnable by one person in under an hour on a fresh Ubuntu 24.04 VM.
6. ONE-LINE BOTTOM LINE for Craig.

FORMAT: Markdown, under 1,200 words. No preamble.


=== ROUND-1 REVIEW BY ASTRA ===
# Astra (gpt-6-astra, high) — round 1

pane 11d02ed1a81d · complete True

Overall approach — **BUILD:** a native user service with vendor-owned sign-ins fits the constraints; dependable onboarding needs explicit completion checks.
install.sh — **RESHAPE:** a Seed receipt suppresses unfinished setup, so rerunning can permanently preserve a broken installation.
README top — **RESHAPE:** it promises repair, reproducibility, and browser readiness that the implementation does not establish.
launch.py + page change — **RESHAPE:** automatic pairing is reasonable, but hub identity, credential exposure, opener success, and expired-code recovery need verification.

**The mom test**

On a fresh Ubuntu 24.04 desktop:

- She must find a terminal and paste a command; this does not meet “clicking on a link.”
- If `curl` is absent, she sees `curl: command not found`. The installer cannot install its own downloader before being downloaded.
- `Assistants: claude,codex,grok,gemini` selects everything without asking which accounts she owns, including the 1.5 GB download.
- `Missing: … Will run: sudo apt-get install …` introduces package-manager terminology. The password prompt displays no typing feedback; explain that explicitly.
- A download, checksum, package, or selftest failure ends with `Fix what it names`. That is a support request disguised as recovery guidance.
- `Wire the memory hooks now? [Y/n]` asks her to decide about unfamiliar memory behavior and edits to `settings.json`.
- `Claude: … account (Pro or Max)` and subsequent account prompts require credentials and possibly subscriptions she lacks. Skipping remains possible, but readiness becomes unclear.
- `service survives logout (linger enabled)` describes a technical policy choice without explaining background resource use.
- `opening … (paired)` can appear even when the opener immediately fails.
- `You are set up` appears despite failed logins or launch. The suggested `cd … && claude` fails if Claude was excluded.
- README’s `bytes and a digest`, `/status`, `doctor`, and `journalctl` require knowledge she should not need.

**Bugs and reliability failures, highest severity first**

1. **Destructive uninstall without ownership checks.** Constructs: `rm -rf "$NODE_DIR" "$TOOLS_PREFIX" "$BIN/grok"` and recursive clone/state deletion; uninstall precedes the root refusal. Existing user installations, overridden paths, or root execution can delete unrelated content. **Fix:** validate paths and UID before either mode; use an ownership manifest, protect preexisting files, and show exact removal targets.

2. **Resume skips incomplete Seed setup.** `if [ -f "$AIOS/.cc-seed/receipt.json" ]; then skip …` encloses all subsequent verification, approvals, hooks, scheduling, and audit. Failure after receipt creation—or rerunning without `--no-schedule`—leaves those steps undone. **Fix:** reconcile each phase independently, using Seed’s supported checks; retain `install.py --approve` for gated writes.

3. **Service updates and readiness are unreliable.** `enable --now … || restart …` does not restart an already-running service after changes. Existing units retain their original arguments; whether port arguments override the environment is unverifiable from the text. Any successful `/health` response passes, including another application on occupied port 8098. **Fix:** verify hub identity/version/configuration, detect port conflicts, restart changed deployments, and persist the address for later `launch`.

4. **Bootstrap ordering breaks missing-tool recovery.** `curl -fsSI` runs before package installation. Without curl, a saved installer reports “No internet connection.” If Python is absent, `pyyaml` never enters `missing`. **Fix:** bootstrap required tools first; always verify/install YAML afterward. Distinguish DNS, proxy, TLS, and HTTP failures.

5. **Distribution support exceeds implementation.** Python 3.8 is rejected only after package changes. From platform knowledge, official Linux Node binaries require glibc and generally fail on Alpine’s musl; non-systemd cron is never verified as running. ARM64 Node selection alone establishes no other runtime’s compatibility. **Fix:** preflight Python/libc/init and every selected runtime; publish a tested support matrix. Node version/hash validity and Antigravity ARM64 support are **unverifiable from the text**.

6. **Seed detection fails open.** `--detect … || true`, human-output regexes, and `grep -v -F "$AIOS"` discard errors and permit substring collisions between workspace names. Another install can be missed or ordinary output mistaken for one. **Fix:** require successful structured detection and compare canonical paths exactly.

7. **Terminal and logging handling is incomplete.** `[ -r /dev/tty ]` does not prove the process has a controlling terminal. Opening it can still fail. Unredirected children such as npm inherit the piped script’s stdin and can consume it. `tee` removes stdout’s TTY status, changing interactive CLI behavior; process-substitution failure is not reliably checked. **Fix:** download before execution, open/test a dedicated terminal descriptor, isolate child stdin, and keep interactive authentication outside blanket logging.

8. **Failure reporting masks unsuccessful completion.** `launch … || true`, `doctor || true`, and contract failure followed by `ok "memory hooks wired and proven"` permit false success. `ERR` lacks inheritance into functions/subshells; `set -e` is suppressed in conditional/`||` contexts, so it is not a completion framework. **Fix:** explicit phase outcomes, meaningful exit codes, truthful degraded status, and contextual error reporting.

9. **Service execution differs from installation.** `SERVICE_PATH` omits locations accepted by `have claude`, and may select a different Python. Unquoted `Environment=PATH=$SERVICE_PATH` breaks paths containing whitespace; systemd specifiers need escaping. Generated shell wrappers also embed paths without shell-safe encoding. **Fix:** resolve runtime executables explicitly, generate correctly escaped units/wrappers, and validate them before activation.

10. **Authentication checks are weak.** Grepping `"loggedIn": true` depends on formatting; with `pipefail`, early `grep -q` termination can also produce false negatives. A nonempty auth file proves neither validity nor subscription access. Codex’s executable is used without checking it exists. **Fix:** parse documented machine-readable status, validate required executables, and report authenticated versus usable separately. Current CLI contracts are **unverifiable from the text**.

11. **Options lack a coherent contract.** Missing `$2` values abort abruptly; invalid ports fail late; `--yes` still launches interactive authentication and may need sudo credentials. `--skip-logins` does not guarantee Gemini won’t prompt later. No-display handling only explicitly selects device authentication for Grok/Codex. **Fix:** validate arguments first; document unattended combinations and unsupported headless flows; generate lane-specific next steps.

12. **Launch can declare success without opening or pairing.** `Popen` success proves process creation only; an immediate `xdg-open` failure is discarded. `--url` can target an unrelated host while approval modifies local auth state. Literal IPv6 addresses lack brackets. The pasted JS ends before claiming/fallback, so expiry recovery is **unverifiable from the text**. **Fix:** validate endpoint/state correspondence, format URLs properly, observe opener failure, and test claim/recovery end to end.

**Security**

- **`curl | bash`: real supply-chain risk.** TLS protects transport, not a compromised moving branch. Smallest improvement: download an immutable release, verify its authenticated digest/signature, then execute.
- **Pins:** `master` moves; a tag is not intrinsically immutable; Claude’s installer/channel and existing Claude binaries are unpinned. Grok’s top-level version does not freeze dependencies; missing lockfile falls back to resolution. Use immutable commits and artifact hashes, require locks, record actual versions, and describe Claude’s separate update policy honestly.
- **Pair URL:** a preapproved code is a bearer credential. Query strings can reach request logs, history, referrers, process listings, and—on fallback—the installer log. Prefer a fragment, remove it immediately, redact logs, and enforce atomic single-use, expiry, rate limiting, and sufficient entropy. Those backend guarantees are **unverifiable from the text**.
- **Shell PATH edits:** user-local PATH is normal, but prepending changes command precedence and modifying two startup files is intrusive. Avoid duplicates and record reversible changes.
- **Systemd PATH:** explicit PATH is appropriate; escaping and trusted directory ownership are required. Avoid inherited arbitrary search paths.

**Missing for repeatability**

1. A desktop launch/install entry point with prerequisites handled.
2. A tested release manifest covering architectures, dependencies, and actual versions.
3. Transactional staging, concurrency locking, rollback, and per-phase reconciliation.
4. Early disk, runtime, network, privilege, and port preflight.
5. Verified browser pairing and one usable selected lane before declaring success.
6. A bounded, redacted diagnostic bundle and actionable recovery screen.

**Automated checks**

1. Bash syntax/ShellCheck plus argument-table tests on every PR.
2. Fake command binaries under temporary HOME to inject failure after every phase; rerun and assert completion without duplication.
3. Disposable systemd VMs: active-service upgrade, custom port, occupied port, logout persistence.
4. PTY and no-controlling-terminal tests for piped input, sudo, login, and logging.
5. x86-64/ARM64 distro runners checking executable compatibility and scheduler operation.
6. Python/browser integration tests for wrong hub, opener failure, expired/reused codes, URL removal, and credential-free logs.

**Most likely failure:** her machine lacks `curl`, so the advertised installation command fails before the installer can help.


=== ROUND-1 REVIEW BY GEMINI38 ===
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

The installer will most likely fail on Craig's mother's machine when it abruptly halts at an intimidating `[sudo]` password prompt to install basic packages like `cron` and `git`, followed by opening multiple confusing browser tabs demanding paid account logins before showing any working user interface.


=== install.sh 1.1.1 ===
```bash
#!/usr/bin/env bash
# Corral Light + AI-OS Seed — the Linux installer.
#
#   curl -fsSL https://raw.githubusercontent.com/cvp1/corral-light/master/install.sh | bash
#   wget -qO-  https://raw.githubusercontent.com/cvp1/corral-light/master/install.sh | bash
#
# One command. It asks two questions at the start (which assistants you have
# an account for, and whether Claude may keep memory between conversations),
# then works on its own. When it finishes, Corral Light is running as a user
# service, your browser is open on it (already paired), AI-OS Seed is
# installed and verified in ~/aios, and each assistant you chose has been
# offered its own sign-in. Run it again any time: every step checks before it
# changes anything, so a second run repairs or updates and never duplicates.
#
# What it pins (so two machines installed a month apart get the same thing):
#   Corral Light     git ref  $CORRAL_LIGHT_REF  (default: master; set a commit for an exact repeat —
#                    the commit actually installed is written to the receipt)
#   AI-OS Seed       git tag  $AIOS_SEED_REF     (default: v0.4.9-alpha)
#   Node.js          v24.21.0 LTS, SHA-256 checked, private copy (never touches a system Node)
#   Claude + ChatGPT adapters   spike/package-lock.json in the Corral Light checkout (npm ci)
#   Grok CLI         @xai-official/grok 1.0.46 from npm, into a private prefix
#   Antigravity      the release pinned in install_antigravity_acp.py (SHA-256 checked)
#   Claude Code      2.1.285 through Anthropic's own installer, which verifies the binary's
#                    SHA-256; Claude Code then keeps itself current (Anthropic's policy, not ours)
#
# Options:
#   --lanes a,b,c     which assistants to set up: claude, codex, grok, gemini
#                     (default: ask; all four with --yes). gemini is a 1.5 GB download.
#   --workspace DIR   where AI-OS Seed lives (default: ~/aios)
#   --port N          hub port (default: 8098)
#   --yes             no questions: all four assistants, memory on; sign-ins still run
#   --skip-logins     do not start any sign-in (you can run them later)
#   --no-service      do not install the systemd user service; start the hub
#                     for this session only
#   --no-schedule     (testing) skip Seed's scheduler, memory mesh and hooks
#   --uninstall       stop the service and remove what this script installed
#   --help
#
# Everything it prints also goes to ~/.local/share/corral-light/install.log.
# It never runs as root, never touches a system Python or Node, and the only
# step that asks for your password is installing missing distro packages.

set -Eeuo pipefail

INSTALLER_VERSION="1.1.1"

# ── pins ────────────────────────────────────────────────────────────────────
CORRAL_LIGHT_REPO="${CORRAL_LIGHT_REPO:-https://github.com/cvp1/corral-light}"
CORRAL_LIGHT_REF="${CORRAL_LIGHT_REF:-master}"
AIOS_SEED_REPO="${AIOS_SEED_REPO:-https://github.com/cvp1/ai-os-seed}"
AIOS_SEED_REF="${AIOS_SEED_REF:-v0.4.9-alpha}"
NODE_VERSION="v24.21.0"
NODE_SHA256_X64="fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6"
NODE_SHA256_ARM64="6ad1325edbdb5649c379b75a237147a666c95d4f9ae8d340fef2d1575d289ad2"
GROK_PACKAGE="@xai-official/grok"
GROK_VERSION="1.0.46"
CLAUDE_INSTALL_URL="https://claude.ai/install.sh"
CLAUDE_VERSION="2.1.285"

# ── places ──────────────────────────────────────────────────────────────────
TOOLS="$HOME/tools"
CL="$TOOLS/corral-light"
SEED="$TOOLS/ai-os-seed"
AIOS="${AIOS_WORKSPACE:-$HOME/aios}"
STATE="${CORRAL_LIGHT_STATE:-$HOME/.local/share/corral-light}"
NODE_DIR="$STATE/node"
TOOLS_PREFIX="$STATE/tools"
BIN="$HOME/.local/bin"
LOG="$STATE/install.log"
RECEIPT="$STATE/install-receipt.json"
UNIT_DIR="$HOME/.config/systemd/user"
DROPIN="$UNIT_DIR/corral-light.service.d/10-installer.conf"
CODEX_HOME_DIR="$HOME/.config/corral-light/codex-home"
PORT="${CORRAL_LIGHT_PORT:-8098}"

# ── options ─────────────────────────────────────────────────────────────────
LANES=""
YES=0
SKIP_LOGINS=0
NO_SERVICE=0
NO_SCHEDULE=0
UNINSTALL=0

usage() { sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'; }
need_value() { [ $# -ge 2 ] && [ -n "$2" ] || { echo "option $1 needs a value (try --help)" >&2; exit 2; }; }

while [ $# -gt 0 ]; do
  case "$1" in
    --lanes) need_value "$@"; LANES="$2"; shift 2 ;;
    --lanes=*) LANES="${1#*=}"; shift ;;
    --workspace) need_value "$@"; AIOS="$2"; shift 2 ;;
    --workspace=*) AIOS="${1#*=}"; shift ;;
    --port) need_value "$@"; PORT="$2"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --yes|-y) YES=1; shift ;;
    --skip-logins) SKIP_LOGINS=1; shift ;;
    --no-service) NO_SERVICE=1; shift ;;
    --no-schedule) NO_SCHEDULE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --version) echo "corral-light installer $INSTALLER_VERSION"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done
case "$AIOS" in /*) ;; *) AIOS="$PWD/$AIOS" ;; esac
case "$PORT" in ''|*[!0-9]*) echo "--port must be a number (got '$PORT')" >&2; exit 2 ;; esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then echo "--port must be between 1024 and 65535" >&2; exit 2; fi
for l in ${LANES//,/ }; do
  case "$l" in claude|codex|grok|gemini) ;; *) echo "Unknown assistant '$l' in --lanes (choose from: claude, codex, grok, gemini)" >&2; exit 2 ;; esac
done
case "$HOME" in *[[:space:]]*) echo "This installer cannot handle a home directory with spaces in its path ($HOME)." >&2; exit 2 ;; esac

# When this script arrives through `curl | bash`, stdin IS the script. No child
# may read it (an npm or vendor installer that reads stdin would eat the rest
# of this file). Questions go to the terminal directly, below.
exec </dev/null

# ── output ──────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
else
  B=""; G=""; Y=""; R=""; D=""; N=""
fi
STEP=0; STEPS=11; CURRENT=""
step() { STEP=$((STEP+1)); CURRENT="$1"; printf '\n%s[%d/%d] %s%s\n' "$B" "$STEP" "$STEPS" "$1" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
skip() { printf '  %s·%s %s\n' "$D" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
die()  {
  printf '\n%s✗ %s%s\n' "$R" "$1" "$N" >&2
  [ -n "${2:-}" ] && printf '  %s\n' "$2" >&2
  [ -n "$CURRENT" ] && printf '  (while: %s)  Log: %s\n' "$CURRENT" "$LOG" >&2
  exit 1
}

# A terminal we can actually read from, or none.
TTY_IN=/dev/null
if [ -r /dev/tty ] && { : < /dev/tty; } 2>/dev/null; then TTY_IN=/dev/tty; fi
ask_yn() {   # ask_yn "question" default(Y|N)  → 0 yes, 1 no. No terminal and no --yes: "no".
  local q="$1" def="${2:-Y}" ans
  if [ "$YES" = 1 ]; then [ "$def" = Y ]; return; fi
  if [ "$TTY_IN" = /dev/null ]; then printf '  %s — no terminal to ask on, taking "no" (--yes says yes to everything)\n' "$q"; return 1; fi
  if [ "$def" = Y ]; then printf '  %s [Y/n] ' "$q"; else printf '  %s [y/N] ' "$q"; fi
  read -r ans < "$TTY_IN" || ans=""
  ans="$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')"
  case "$ans" in
    "") [ "$def" = Y ] ;;
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}
press_enter() {   # 0 = go on, 1 = skip
  [ "$YES" = 1 ] && return 0
  [ "$TTY_IN" = /dev/null ] && return 1
  printf '  %s(press Enter to continue, or type s to skip)%s ' "$D" "$N"
  local ans; read -r ans < "$TTY_IN" || ans=""
  [ "$ans" != "s" ] && [ "$ans" != "S" ]
}

has_lane() { case ",$LANES," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
have() { command -v "$1" >/dev/null 2>&1; }
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }
hub_alive() { curl -fs --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep >/dev/null '"service": "corral-light"'; }

# Sign-in state, per assistant. Only "has a credential file"; whether the
# subscription behind it works is the assistant's own business.
claude_logged_in() {
  local bin="$1" out
  out="$("$bin" auth status 2>/dev/null)" || return 1
  printf '%s' "$out" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("loggedIn") is True else 1)' 2>/dev/null
}
claude_bin() { if [ -x "$BIN/claude" ]; then echo "$BIN/claude"; elif have claude; then command -v claude; fi; }
signed_in() {
  case "$1" in
    claude) local c; c="$(claude_bin)"; [ -n "$c" ] && claude_logged_in "$c" ;;
    grok)   [ -s "$HOME/.grok/auth.json" ] ;;
    codex)  [ -s "$CODEX_HOME_DIR/auth.json" ] ;;
    gemini) [ -s "$HOME/.gemini/antigravity-acp/acp_token.json" ] ;;
    *) return 1 ;;
  esac
}

on_error() {
  local rc=$?
  printf '\n%s✗ Step failed: %s%s\n' "$R" "${CURRENT:-start}" "$N" >&2
  printf '  The full log is at %s\n' "$LOG" >&2
  printf '  Run the same command again: it continues where it left off. If it stops at the same place,\n' >&2
  printf '  the lines just above this one say what went wrong; send them with the log when asking for help.\n' >&2
  exit "$rc"
}
trap on_error ERR
on_int() { printf '\n\n  Interrupted. Nothing is half-written that a re-run cannot finish: run the same command again to continue.\n' >&2; exit 130; }
trap on_int INT

# ── logging: everything also goes to the log file ───────────────────────────
mkdir -p "$STATE"
: >> "$LOG"
printf '\n===== corral-light installer %s — %s — %s =====\n' "$INSTALLER_VERSION" "$(date -Is)" "$*" >> "$LOG"

# ── uninstall ───────────────────────────────────────────────────────────────
uninstall() {
  STEPS=5
  printf '%sThis removes what the installer added. Sign-ins (~/.claude, ~/.grok, …) and your own\n' "$B"
  printf 'files stay unless you say otherwise. Each removal names its target first.%s\n' "$N"
  step "Service"
  if [ "$NO_SERVICE" = 0 ] && systemctl --user list-unit-files corral-light.service 2>/dev/null | grep >/dev/null '^corral-light.service'; then
    printf '  stopping and removing: corral-light.service, corral-light-watch.timer, %s\n' "$DROPIN"
    systemctl --user disable --now corral-light.service 2>/dev/null || true
    systemctl --user disable --now corral-light-watch.timer 2>/dev/null || true
    rm -f "$UNIT_DIR/corral-light.service" "$UNIT_DIR/corral-light-watch.service" "$UNIT_DIR/corral-light-watch.timer"
    rm -rf "$UNIT_DIR/corral-light.service.d"
    systemctl --user daemon-reload 2>/dev/null || true
    ok "service removed"
  else
    skip "no service installed"
  fi
  step "AI-OS Seed"
  if [ -f "$AIOS/.cc-seed/receipt.json" ] && [ -f "$SEED/install.py" ]; then
    if ask_yn "Remove the AI-OS Seed install at $AIOS (de-schedules its jobs; your own files stay)?" N; then
      python3 "$SEED/install.py" --target "$AIOS" --uninstall || warn "Seed uninstall reported a problem — see above"
      systemctl --user disable --now memory-fold.timer memory-home-watch.timer 2>/dev/null || true
      rm -f "$UNIT_DIR"/memory-{fold,home-watch}.{service,timer}
      systemctl --user daemon-reload 2>/dev/null || true
      ok "Seed removed (~/memory-events is yours and was left alone)"
    else
      skip "Seed left in place"
    fi
  else
    skip "no Seed install at $AIOS"
  fi
  step "Private Node, Grok CLI, adapters"
  printf '  removing: %s %s %s\n' "$NODE_DIR" "$TOOLS_PREFIX" "$BIN/grok"
  rm -rf "$NODE_DIR" "$TOOLS_PREFIX"; rm -f "$BIN/grok"
  ok "removed"
  step "Clones"
  if ask_yn "Remove the source clones $CL and $SEED (and the corral-light command)?" N; then
    rm -rf "$CL" "$SEED"; rm -f "$BIN/corral-light"; ok "removed"
  else
    skip "clones left in place"
  fi
  step "Hub state"
  if ask_yn "Remove saved conversations and hub state at $STATE?" N; then
    rm -rf "$STATE"; ok "removed"
  else
    skip "state left at $STATE"
  fi
  printf '\n%sDone.%s Claude Code itself (~/.local/bin/claude) and each assistant'"'"'s sign-in were not touched.\n' "$B" "$N"
}

main() {
  # Both modes: never as root, Linux only.
  [ "$(uname -s)" = Linux ] || die "This installer is for Linux." "macOS and Windows are not covered by this first release."
  [ "$(id -u)" != 0 ] || die "Do not run this as root." "Assistants sign in and work as you. Run it as your normal user; it asks for sudo only if a package is missing."
  if [ "$UNINSTALL" = 1 ]; then uninstall; return 0; fi

  printf '%sCorral Light installer%s %s\n' "$B" "$N" "$D$INSTALLER_VERSION$N"
  printf 'Workspace: %s   Log: %s\n' "$AIOS" "$LOG"

  # ── 1. this machine ───────────────────────────────────────────────────────
  step "Checking this machine"
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) NODE_ARCH=x64; NODE_SHA="$NODE_SHA256_X64" ;;
    aarch64|arm64) NODE_ARCH=arm64; NODE_SHA="$NODE_SHA256_ARM64" ;;
    *) die "Unsupported CPU: $ARCH" "Supported: x86_64 and arm64." ;;
  esac
  ok "Linux $ARCH"
  libc="$(getconf GNU_LIBC_VERSION 2>/dev/null || true)"
  case "$libc" in glibc\ *) ;; *) die "This Linux does not use glibc (Alpine/musl?)." "The prebuilt Node.js and assistant binaries need glibc 2.28+. Use a glibc-based distro for this release." ;; esac
  glibc="${libc#glibc }"
  if [ -n "$glibc" ] && [ "$(printf '%s\n2.28\n' "$glibc" | sort -V | head -1)" != "2.28" ]; then
    die "glibc $glibc is too old; 2.28 or newer is needed (Ubuntu 20.04+, Debian 10+, Fedora 29+)."
  fi
  ok "glibc ${glibc:-present}"
  if [ "$NO_SERVICE" = 0 ] && ! systemctl --user show-environment >/dev/null 2>&1; then
    warn "no systemd user session here (a container, or no login session) — the hub will run for this session only"
    NO_SERVICE=1
  fi
  HEADLESS=0; [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && HEADLESS=1
  if [ "$HEADLESS" = 0 ]; then ok "a desktop is available (the browser can open)"; else warn "no desktop display — sign-ins use device codes and the browser address is printed for you"; fi

  # ── 2. distro packages ────────────────────────────────────────────────────
  step "Base tools (git, python3, PyYAML, curl, tar, xz, cron)"
  missing=()
  have git     || missing+=(git)
  have curl    || missing+=(curl)
  have tar     || missing+=(tar)
  have xz      || missing+=(xz)
  have python3 || missing+=(python3)
  have crontab || missing+=(cron)
  if have python3 && ! python3 -c 'import yaml' 2>/dev/null; then missing+=(pyyaml); fi
  if ! have python3; then missing+=(pyyaml); fi
  if [ "$HEADLESS" = 0 ] && ! have xdg-open; then missing+=(xdg-utils); fi
  pkg_names() {  # generic names → this distro's packages
    local mgr="$1"; shift
    local out=()
    for m in "$@"; do
      case "$mgr:$m" in
        apt:pyyaml) out+=(python3-yaml) ;;   dnf:pyyaml) out+=(python3-pyyaml) ;;
        pacman:pyyaml) out+=(python-yaml) ;; zypper:pyyaml) out+=(python3-PyYAML) ;;
        apt:xz) out+=(xz-utils) ;;           *:xz) out+=(xz) ;;
        apt:cron) out+=(cron) ;;             *:cron) out+=(cronie) ;;
        *) out+=("$m") ;;
      esac
    done
    printf '%s\n' "${out[@]}"
  }
  if [ ${#missing[@]} -gt 0 ]; then
    if have apt-get; then MGR=apt; elif have dnf; then MGR=dnf; elif have pacman; then MGR=pacman; elif have zypper; then MGR=zypper; else MGR=""; fi
    [ -n "$MGR" ] || die "Missing: ${missing[*]} — and no known package manager (apt, dnf, pacman, zypper)." "Install them with your distro's tool, then run this again."
    mapfile -t pkgs < <(pkg_names "$MGR" "${missing[@]}")
    case "$MGR" in
      apt)    cmd="sudo apt-get install -y ${pkgs[*]}"; pre="sudo apt-get update -qq" ;;
      dnf)    cmd="sudo dnf install -y ${pkgs[*]}"; pre="" ;;
      pacman) cmd="sudo pacman -S --needed --noconfirm ${pkgs[*]}"; pre="" ;;
      zypper) cmd="sudo zypper install -y ${pkgs[*]}"; pre="" ;;
    esac
    printf '  Missing: %s\n  Will run: %s\n' "${missing[*]}" "$cmd"
    have sudo || die "sudo is not available, and these packages are missing: ${pkgs[*]}" "Ask an administrator to run: ${cmd#sudo }"
    printf '  %sThis asks for your password, for the system package manager only. Nothing shows while you type it.%s\n' "$D" "$N"
    [ -z "$pre" ] || $pre < "$TTY_IN"
    $cmd < "$TTY_IN"
    for m in "${missing[@]}"; do
      case "$m" in
        pyyaml) python3 -c 'import yaml' || die "PyYAML still not importable after install" ;;
        cron) have crontab || die "crontab still missing after install" ;;
        xdg-utils) ;;
        *) have "$m" || die "$m still missing after install" ;;
      esac
    done
    ok "installed: ${pkgs[*]}"
  else
    ok "all present"
  fi
  PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "python3 is $PYV; 3.9 or newer is required."
  python3 -c 'import yaml' 2>/dev/null || die "PyYAML is not importable by $(command -v python3)." "Install your distro's python3 YAML package, then run again."
  ok "python3 $PYV with PyYAML"
  # cron must be running for Seed's scheduled jobs. Enabling it is a one-time admin action.
  if [ "$NO_SCHEDULE" = 0 ]; then
    cron_unit=""
    for u in cronie cron crond; do
      if systemctl list-unit-files "$u.service" 2>/dev/null | grep >/dev/null "^$u.service"; then cron_unit="$u"; break; fi
    done
    if [ -n "$cron_unit" ] && ! systemctl is-active --quiet "$cron_unit"; then
      if have sudo; then
        printf '  cron (%s) is installed but not running; starting it so scheduled jobs run (asks for your password).\n' "$cron_unit"
        sudo systemctl enable --now "$cron_unit" < "$TTY_IN" || warn "could not start $cron_unit — Seed's scheduled jobs will not run until it is"
      else
        warn "cron ($cron_unit) is not running and sudo is unavailable — scheduled jobs will not run until it is"
      fi
    elif [ -z "$cron_unit" ] && ! pgrep -x cron >/dev/null 2>&1 && ! pgrep -x crond >/dev/null 2>&1; then
      warn "no cron daemon found running — Seed's scheduled jobs need one (cron or cronie)"
    fi
  fi
  if ! curl -fsSI --max-time 15 https://github.com >/dev/null 2>&1; then
    die "No internet connection (cannot reach github.com)." "Connect, then run the installer again."
  fi
  ok "internet reachable"

  # ── the two questions, before anything slow ───────────────────────────────
  if [ -z "$LANES" ]; then
    if [ "$YES" = 1 ] || [ "$TTY_IN" = /dev/null ]; then
      LANES="claude,codex,grok,gemini"
    else
      printf '\n  %sWhich assistants do you have an account for?%s\n' "$B" "$N"
      printf '    1  Claude    (claude.ai — Pro or Max)\n'
      printf '    2  ChatGPT   (Plus, Pro or Team)\n'
      printf '    3  Grok      (SuperGrok or X Premium)\n'
      printf '    4  Gemini    (a Google account; this one is a 1.5 GB download)\n'
      printf '  Type the numbers, like  1 3  — or just press Enter for all four: '
      read -r picks < "$TTY_IN" || picks=""
      if [ -z "${picks//[^1-4]/}" ]; then
        LANES="claude,codex,grok,gemini"
      else
        LANES=""
        case "$picks" in *1*) LANES="$LANES,claude" ;; esac
        case "$picks" in *2*) LANES="$LANES,codex" ;; esac
        case "$picks" in *3*) LANES="$LANES,grok" ;; esac
        case "$picks" in *4*) LANES="$LANES,gemini" ;; esac
        LANES="${LANES#,}"
      fi
    fi
  fi
  ok "assistants: $LANES"
  WIRE_HOOKS=0
  if [ "$NO_SCHEDULE" = 0 ]; then
    printf '\n  %sMay Claude keep what it learns between conversations in %s?%s\n' "$B" "$AIOS" "$N"
    printf '  This adds a few lines to Claude Code'"'"'s settings file. The exact lines are printed when it\n'
    printf '  happens, and  install.py --revoke memory-hooks  takes them out again.\n'
    if ask_yn "Keep memory between conversations?" Y; then WIRE_HOOKS=1; fi
  fi
  need_mb=1200; has_lane gemini && need_mb=3600
  free_mb="$(df -Pm "$HOME" | awk 'NR==2 {print $4}')"
  if [ -n "$free_mb" ] && [ "$free_mb" -lt "$need_mb" ]; then
    die "Not enough free space in $HOME: ${free_mb} MB free, about ${need_mb} MB needed." "Free some space (or leave Gemini out), then run again."
  fi
  ok "disk: ${free_mb} MB free"

  # ── 3. the two checkouts ──────────────────────────────────────────────────
  step "Fetching Corral Light and AI-OS Seed"
  mkdir -p "$TOOLS" "$BIN"
  CL_CHANGED=0
  sync_repo() {  # sync_repo <dir> <url> <ref> <label>  → sets SYNC_CHANGED=1 when HEAD moved
    local dir="$1" url="$2" ref="$3" label="$4" before="" after=""
    SYNC_CHANGED=0
    if [ -d "$dir/.git" ]; then
      before="$(git -C "$dir" rev-parse HEAD)"
      if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
        warn "$label at $dir has local changes — left exactly as it is (not updated)"
        return 0
      fi
      git -C "$dir" fetch -q --tags origin
      if git -C "$dir" show-ref -q --verify "refs/remotes/origin/$ref"; then
        git -C "$dir" checkout -q -B "$ref" "origin/$ref"
      else
        git -C "$dir" checkout -q --detach "$ref"
      fi
      after="$(git -C "$dir" rev-parse HEAD)"
      if [ "$before" = "$after" ]; then skip "$label already at $ref ($(git -C "$dir" rev-parse --short HEAD))"
      else SYNC_CHANGED=1; ok "$label updated to $ref ($(git -C "$dir" rev-parse --short HEAD))"; fi
    elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
      die "$dir exists and is not a git checkout." "Move it aside, then run the installer again."
    else
      git clone -q "$url" "$dir"
      if git -C "$dir" show-ref -q --verify "refs/remotes/origin/$ref"; then
        git -C "$dir" checkout -q -B "$ref" "origin/$ref"
      else
        git -C "$dir" checkout -q --detach "$ref"
      fi
      SYNC_CHANGED=1
      ok "$label cloned at $ref ($(git -C "$dir" rev-parse --short HEAD))"
    fi
  }
  sync_repo "$CL" "$CORRAL_LIGHT_REPO" "$CORRAL_LIGHT_REF" "Corral Light"; CL_CHANGED=$SYNC_CHANGED
  sync_repo "$SEED" "$AIOS_SEED_REPO" "$AIOS_SEED_REF" "AI-OS Seed"
  [ -f "$CL/hub.py" ] || die "$CL does not look like Corral Light (no hub.py)."
  [ -f "$SEED/install.py" ] || die "$SEED does not look like AI-OS Seed (no install.py)."
  cat > "$BIN/corral-light" <<EOF
#!/bin/bash
exec "$CL/corral-light" "\$@"
EOF
  chmod +x "$BIN/corral-light"
  ok "command: $BIN/corral-light"
  line='export PATH="$HOME/.local/bin:$PATH"'
  added=0
  for rc in "$HOME/.profile" "$HOME/.bashrc"; do
    if ! grep -qsF "$line" "$rc"; then printf '\n# added by the Corral Light installer\n%s\n' "$line" >> "$rc"; added=1; fi
  done
  case ":$PATH:" in *":$BIN:"*) ;; *) export PATH="$BIN:$PATH" ;; esac
  if [ "$added" = 1 ]; then ok "added ~/.local/bin to PATH in ~/.profile and ~/.bashrc (new terminals pick it up)"; else skip "~/.local/bin already on PATH in ~/.profile and ~/.bashrc"; fi

  # ── 4. private Node.js ────────────────────────────────────────────────────
  step "Node.js $NODE_VERSION (private copy for the adapters)"
  if [ -x "$NODE_DIR/bin/node" ] && [ "$("$NODE_DIR/bin/node" --version)" = "$NODE_VERSION" ]; then
    skip "already present"
  else
    tarball="node-$NODE_VERSION-linux-$NODE_ARCH.tar.xz"
    dl="$STATE/node.download"       # beside the destination, not /tmp
    rm -rf "$dl"; mkdir -p "$dl"
    curl -fsSL --retry 3 -o "$dl/$tarball" "https://nodejs.org/dist/$NODE_VERSION/$tarball"
    got="$(sha256_of "$dl/$tarball")"
    [ "$got" = "$NODE_SHA" ] || die "Node download checksum mismatch" "expected $NODE_SHA, got $got — the download was corrupted or altered; run again."
    tar -xJf "$dl/$tarball" -C "$dl"
    rm -rf "$NODE_DIR"
    mv "$dl/node-$NODE_VERSION-linux-$NODE_ARCH" "$NODE_DIR"
    rm -rf "$dl"
    ok "installed at $NODE_DIR (checksum verified)"
  fi
  export PATH="$NODE_DIR/bin:$PATH"
  export CORRAL_NODE_BIN="$NODE_DIR/bin"
  export NPM_CONFIG_UPDATE_NOTIFIER=false

  # ── 5. adapters ───────────────────────────────────────────────────────────
  step "Claude and ChatGPT adapters (npm ci, from the lock file)"
  [ -f "$CL/spike/package-lock.json" ] || die "$CL/spike/package-lock.json is missing" "The checkout is incomplete; the adapters are installed from that lock file so every install gets the same versions."
  (cd "$CL/spike" && npm ci --no-audit --no-fund --loglevel=error)
  [ -x "$CL/spike/node_modules/.bin/claude-agent-acp" ] || die "claude-agent-acp did not install" "see $LOG"
  [ -x "$CL/spike/node_modules/.bin/codex-acp" ] || die "codex-acp did not install" "see $LOG"
  [ -x "$CL/spike/node_modules/.bin/codex" ] || die "the bundled codex CLI did not install" "see $LOG"
  ok "adapters installed"

  # ── 6. assistants' own programs ───────────────────────────────────────────
  step "Assistant programs"
  if has_lane claude; then
    if [ -n "$(claude_bin)" ]; then
      skip "Claude Code already installed ($(claude_bin))"
    else
      printf '  Installing Claude Code %s with Anthropic'"'"'s installer (%s)…\n' "$CLAUDE_VERSION" "$CLAUDE_INSTALL_URL"
      curl -fsSL --retry 3 "$CLAUDE_INSTALL_URL" | bash -s "$CLAUDE_VERSION"
      [ -n "$(claude_bin)" ] || die "Claude Code did not install" "see $LOG"
      ok "Claude Code installed"
    fi
  fi
  if has_lane grok; then
    if [ -x "$TOOLS_PREFIX/bin/grok" ] && [ "$("$TOOLS_PREFIX/bin/grok" --version 2>/dev/null | awk '{print $2}')" = "$GROK_VERSION" ]; then
      skip "Grok CLI $GROK_VERSION already present"
    else
      mkdir -p "$TOOLS_PREFIX"
      npm install -g --prefix "$TOOLS_PREFIX" --no-audit --no-fund --loglevel=error "$GROK_PACKAGE@$GROK_VERSION"
      [ -x "$TOOLS_PREFIX/bin/grok" ] || die "Grok CLI did not install" "see $LOG"
      ok "Grok CLI $GROK_VERSION installed"
    fi
    cat > "$BIN/grok" <<EOF
#!/bin/sh
# Grok CLI, installed by the Corral Light installer with its private Node.
export PATH="$NODE_DIR/bin:\$PATH"
exec "$TOOLS_PREFIX/bin/grok" "\$@"
EOF
    chmod +x "$BIN/grok"
  fi
  if has_lane codex; then
    ok "ChatGPT (Codex) uses the CLI bundled with its adapter — nothing more to install"
  fi
  if has_lane gemini; then
    if python3 "$CL/install_antigravity_acp.py" --check >/dev/null 2>&1; then
      skip "Antigravity (Gemini) runtime already present"
    else
      printf '  Downloading the Antigravity runtime (about 1.5 GB, checksum verified; this is the slow part)…\n'
      python3 "$CL/install_antigravity_acp.py" --install
      ok "Antigravity (Gemini) runtime installed"
    fi
  fi

  # ── 7. AI-OS Seed in the workspace ────────────────────────────────────────
  # Each phase checks its own state, so a run that stopped halfway finishes
  # on the next run instead of hiding behind the receipt.
  step "AI-OS Seed in $AIOS"
  if [ ! -f "$AIOS/.cc-seed/receipt.json" ]; then
    # Never two installs on one machine: the scheduler owns one managed block.
    detect="$(python3 "$SEED/install.py" --detect 2>&1 || true)"
    other="$(printf '%s\n' "$detect" | python3 -c '
import os, re, sys
mine = os.path.realpath(sys.argv[1])
for line in sys.stdin:
    m = re.search(r"install root (\S+)", line) or re.search(r"dir: (\S+) — an AI-OS Seed INSTALL", line)
    if m and os.path.realpath(m.group(1).rstrip(",")) != mine:
        print(line.rstrip())
' "$AIOS")"
    if [ -n "$other" ]; then
      printf '%s\n' "$detect"
      die "Another AI-OS Seed install exists on this machine (above)." "One machine supports one install. Use --workspace to point at it, or remove it first (see AGENT-INSTALL.md Phase 0)."
    fi
    if [ -e "$AIOS" ] && [ -n "$(ls -A "$AIOS" 2>/dev/null)" ]; then
      if [ -f "$AIOS/CLAUDE.md" ] || [ -d "$AIOS/.claude" ]; then
        printf '  %s already has content; the seed will join it (--into) and touch none of your files.\n' "$AIOS"
        python3 "$SEED/install.py" --target "$AIOS" --into
      else
        die "$AIOS exists and is not empty." "Choose an empty folder with --workspace DIR, or move its contents aside."
      fi
    else
      python3 "$SEED/install.py" --target "$AIOS"
    fi
    ok "installed"
  else
    skip "already installed (receipt present) — checking each piece"
  fi
  gated() { python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if sys.argv[2] in (r.get("gated_writes") or {}) else 1)' "$AIOS/.cc-seed/receipt.json" "$1" 2>/dev/null; }

  printf '  Verifying…\n'
  python3 "$AIOS/_lib/selftest.py" >/dev/null
  python3 "$AIOS/session-brief/session_brief.py" selftest >/dev/null
  python3 "$AIOS/observability/log_run.py" --job hello_fleet -- python3 "$AIOS/demo/hello_fleet.py"
  python3 "$AIOS/observability/log_run.py" --job repo_hygiene -- python3 "$AIOS/observability/repo_hygiene.py" --root "$AIOS" --findings-exit0
  python3 "$AIOS/observability/report.py" --job hello_fleet | tail -n 2
  ok "selftests pass; the demo job ran through the real run logger"

  if ! grep -qs "cc-seed:start" "$AIOS/CLAUDE.md" 2>/dev/null; then
    mkdir -p "$AIOS/.cc-seed/staged"
    # Unquoted heredoc on purpose: paths are filled in; the backticks are escaped.
    cat > "$AIOS/.cc-seed/staged/claude-md.proposed" <<EOF
# AI-OS — this workspace

This is an AI-OS workspace at \`$AIOS\`, installed from AI-OS Seed
$AIOS_SEED_REF by the Corral Light installer on $(date +%Y-%m-%d). The source
clone lives at \`$SEED\` and is never edited in place.

## What's here
- \`scheduler/\` — jobs in \`manifest.yml\`, synced to cron by \`scheduler/sync.sh\`.
- \`observability/\` — \`log_run.py\` wraps every job run into \`runs.db\`;
  \`report.py\` shows history; \`freshness.py --all\` flags anything gone quiet.
- \`keyvault/\` — secrets live here, never in transcripts or code.
- \`memory/\` + \`memory-mesh/\` — durable memory and the event log beneath it.
- \`session-brief/\`, \`friction-miner/\`, \`mcp-guard/\`, \`views/\`, \`demo/\`.
- Skills in \`.claude/skills/\`: /status, /recall, /capture, /improve, /freeze, /skill-center.

Read \`PRINCIPLES.md\` first, then each component's \`README.md\` before changing it.

## Machine notes
- Linux. Scheduled jobs run from cron; if this machine sleeps, runs that
  fall during sleep are skipped, so a stale freshness line may mean
  "asleep", not "broken". Check before fixing.
- No first job chosen yet; \`demo/hello_fleet.py\` is a placeholder.

## Asking another model
Second opinions go through \`corral-light consult\` (the window's lanes, on
this user's own subscription logins) — never a vendor API key by default.
\`corral-light\` is installed (clone at \`$CL\`, command in \`~/.local/bin\`);
\`consult\` needs the hub running (\`systemctl --user status corral-light\`)
and \`corral-light doctor\` shows which lanes are live. If a lane is down, do
not fall back to an API key: that silently changes which vendors see this
user's data. Ask first.
EOF
    python3 "$SEED/install.py" --target "$AIOS" --approve claude-md
    ok "CLAUDE.md written (staged, then approved)"
  else
    skip "CLAUDE.md already has the seed region"
  fi

  if [ "$NO_SCHEDULE" = 0 ]; then
    if gated mesh-bootstrap; then
      skip "memory mesh already bootstrapped"
    else
      python3 "$SEED/install.py" --target "$AIOS" --approve mesh-bootstrap
      ok "memory mesh started (event log at ~/memory-events, fold every 5 minutes)"
    fi
    if gated memory-hooks; then
      skip "memory hooks already wired"
    elif [ "$WIRE_HOOKS" = 1 ]; then
      CI=true python3 "$SEED/install.py" --target "$AIOS" --approve memory-hooks --apply
      if python3 "$SEED/install.py" --target "$AIOS" --contract; then
        ok "memory hooks wired, and the memory contract holds"
      else
        warn "memory hooks wired, but the memory contract reported a problem (above)"
      fi
    else
      warn "memory hooks not wired (you said no) — later: python3 $SEED/install.py --target $AIOS --approve memory-hooks"
    fi
    bash "$AIOS/scheduler/sync.sh"
    ok "scheduler synced to cron (crontab -l shows the cc-seed block)"
    python3 "$SEED/install.py" --target "$AIOS" --audit --package "$SEED" || die "Seed's post-install audit flagged a difference (above)."
    ok "post-install audit clean"
  else
    warn "--no-schedule: scheduler, memory mesh and hooks skipped"
    python3 "$SEED/install.py" --target "$AIOS" --audit --package "$SEED" || warn "audit FLAGGED — expected under --no-schedule (the scheduler was not synced)"
  fi
  python3 "$AIOS/observability/freshness.py" --all | grep -E '^\[(OK|MISSING|DRIFT|STALE)' | head -n 6 || true

  # ── 8. service ────────────────────────────────────────────────────────────
  step "Running Corral Light"
  SERVICE_PATH="$NODE_DIR/bin:$BIN:/usr/local/bin:/usr/bin:/bin"
  if ! hub_alive; then
    # Nothing of ours answers; if the port is held anyway, say so now instead
    # of waiting 60 s for a hub that can never bind it.
    if ! python3 -c "import socket; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', $PORT)); s.close()" 2>/dev/null; then
      die "Port $PORT is in use by another program." "Run the installer again with --port 8099 (or any free port)."
    fi
  fi
  if [ "$NO_SERVICE" = 0 ]; then
    if [ ! -f "$UNIT_DIR/corral-light.service" ]; then
      "$CL/corral-light" install-service --port "$PORT" >/dev/null
      ok "wrote $UNIT_DIR/corral-light.service"
    else
      skip "service file exists — left as it is"
    fi
    mkdir -p "$(dirname "$DROPIN")"
    new_dropin="$(cat <<EOF
# Written by the Corral Light installer. The hub needs the private Node and
# ~/.local/bin on its PATH; a user service does not inherit your shell's.
[Service]
Environment="PATH=$SERVICE_PATH"
Environment="CORRAL_NODE_BIN=$NODE_DIR/bin"
Environment="CORRAL_LIGHT_PORT=$PORT"
EOF
)"
    DROPIN_CHANGED=0
    if [ ! -f "$DROPIN" ] || [ "$(cat "$DROPIN")" != "$new_dropin" ]; then printf '%s\n' "$new_dropin" > "$DROPIN"; DROPIN_CHANGED=1; fi
    systemctl --user daemon-reload
    if systemctl --user is-active --quiet corral-light.service; then
      if [ "$CL_CHANGED" = 1 ] || [ "$DROPIN_CHANGED" = 1 ]; then
        systemctl --user restart corral-light.service
        ok "service restarted on the updated code"
      else
        skip "service already running, nothing changed"
      fi
    else
      systemctl --user enable --now corral-light.service >/dev/null 2>&1 || systemctl --user start corral-light.service
      ok "service enabled and started"
    fi
    if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep >/dev/null 'Linger=yes'; then
      if loginctl enable-linger "$USER" 2>/dev/null; then ok "service survives logout (linger enabled)"
      else warn "could not enable linger; the hub stops when you log out (sudo loginctl enable-linger $USER fixes that)"; fi
    fi
    if [ -f "$CL/corral-light-watch.timer" ] && [ ! -f "$UNIT_DIR/corral-light-watch.timer" ]; then
      sed "s|%HERE%|$CL|" "$CL/corral-light-watch.service" > "$UNIT_DIR/corral-light-watch.service"
      cp "$CL/corral-light-watch.timer" "$UNIT_DIR/"
      systemctl --user daemon-reload
      systemctl --user enable --now corral-light-watch.timer >/dev/null 2>&1 || true
      ok "watchdog timer enabled (pages you if the hub goes down; never restarts it)"
    fi
  else
    if hub_alive; then
      skip "a Corral Light hub already answers on port $PORT"
    else
      ( cd "$CL" && PATH="$SERVICE_PATH" CORRAL_LIGHT_PORT="$PORT" nohup "$CL/corral-light" serve >> "$STATE/hub.log" 2>&1 < /dev/null & )
      ok "hub started for this session (log: $STATE/hub.log)"
    fi
  fi
  for _ in $(seq 1 60); do hub_alive && break; sleep 1; done
  hub_alive || die "The hub did not answer on port $PORT within 60 s." "journalctl --user -u corral-light -n 50   shows why."
  ok "hub answering at http://127.0.0.1:$PORT/"

  # ── 9. sign-ins ───────────────────────────────────────────────────────────
  step "Signing in to each assistant"
  if [ "$SKIP_LOGINS" = 1 ]; then
    warn "--skip-logins: nothing started (the summary below says how to sign in later)"
  else
    if has_lane claude; then
      if signed_in claude; then ok "Claude: already signed in"
      else
        printf '  %sClaude:%s your browser will open to sign in with your Claude account (Pro or Max).\n' "$B" "$N"
        if press_enter; then
          if timeout --foreground 600 "$(claude_bin)" auth login < "$TTY_IN"; then ok "Claude: signed in"; else warn "Claude sign-in did not finish (ten-minute limit) — later: claude auth login"; fi
        else skip "Claude sign-in skipped"; fi
      fi
    fi
    if has_lane grok; then
      if signed_in grok; then ok "Grok: already signed in"
      else
        printf '  %sGrok:%s sign in with your X / Grok account.\n' "$B" "$N"
        if press_enter; then
          grok_login=("$BIN/grok" login); [ "$HEADLESS" = 1 ] && grok_login+=(--device-auth)
          if timeout --foreground 600 "${grok_login[@]}" < "$TTY_IN"; then ok "Grok: signed in"
          else warn "Grok sign-in did not finish (ten-minute limit) — later: grok login"; fi
        else skip "Grok sign-in skipped"; fi
      fi
    fi
    if has_lane codex; then
      if signed_in codex; then ok "ChatGPT: already signed in"
      else
        printf '  %sChatGPT:%s sign in with your ChatGPT account (Plus, Pro or Team).\n' "$B" "$N"
        if press_enter; then
          mkdir -p "$CODEX_HOME_DIR"; chmod 700 "$CODEX_HOME_DIR"
          codex_login=("$CL/spike/node_modules/.bin/codex" login); [ "$HEADLESS" = 1 ] && codex_login+=(--device-auth)
          if CODEX_HOME="$CODEX_HOME_DIR" timeout --foreground 600 "${codex_login[@]}" < "$TTY_IN"; then ok "ChatGPT: signed in"
          else warn "ChatGPT sign-in did not finish — later: CODEX_HOME=$CODEX_HOME_DIR $CL/spike/node_modules/.bin/codex login"; fi
        else skip "ChatGPT sign-in skipped"; fi
      fi
    fi
    if has_lane gemini; then
      if signed_in gemini; then ok "Gemini: already signed in"
      else ok "Gemini: signs in with your Google account the first time you open a Gemini conversation"; fi
    fi
  fi

  # ── 10. open the wall ─────────────────────────────────────────────────────
  step "Opening Corral Light"
  LAUNCH_OK=0
  if CORRAL_LIGHT_PORT="$PORT" "$CL/corral-light" launch; then LAUNCH_OK=1; else warn "could not open the browser — open http://127.0.0.1:$PORT/ yourself, or run: corral-light launch"; fi

  # ── 11. receipt + what you have ───────────────────────────────────────────
  step "Done"
  python3 - "$RECEIPT" "$INSTALLER_VERSION" "$CL" "$SEED" "$AIOS" "$NODE_VERSION" "$LANES" "$PORT" <<'PY'
import json, subprocess, sys, datetime
rc, ver, cl, seed, aios, node, lanes, port = sys.argv[1:9]
def rev(d):
    try: return subprocess.check_output(["git", "-C", d, "rev-parse", "HEAD"], text=True).strip()
    except Exception: return None
json.dump({"installer": ver, "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "corral_light": {"path": cl, "commit": rev(cl)}, "ai_os_seed": {"path": seed, "commit": rev(seed)},
           "workspace": aios, "node": node, "lanes": lanes.split(","), "port": int(port)},
          open(rc, "w"), indent=1)
PY
  ok "receipt: $RECEIPT"
  printf '\n'
  todo=()
  for l in ${LANES//,/ }; do
    case "$l" in
      claude) if signed_in claude; then ok "Claude: signed in"; else todo+=("Claude:   claude auth login"); fi ;;
      grok)   if signed_in grok; then ok "Grok: signed in"; else todo+=("Grok:     grok login"); fi ;;
      codex)  if signed_in codex; then ok "ChatGPT: signed in"; else todo+=("ChatGPT:  CODEX_HOME=$CODEX_HOME_DIR $CL/spike/node_modules/.bin/codex login"); fi ;;
      gemini) if signed_in gemini; then ok "Gemini: signed in"; else todo+=("Gemini:   open a Gemini conversation on the wall; the Google sign-in opens"); fi ;;
    esac
  done
  if [ ${#todo[@]} -eq 0 ] && [ "$LAUNCH_OK" = 1 ]; then
    printf '\n%sReady.%s Corral Light is running and every assistant you chose is signed in.\n' "$B" "$N"
  else
    printf '\n%sInstalled.%s Corral Light is running. Still to do:\n' "$B" "$N"
    for t in "${todo[@]}"; do printf '    %s\n' "$t"; done
    [ "$LAUNCH_OK" = 1 ] || printf '    Open the wall:  corral-light launch   (or http://127.0.0.1:%s/ in your browser)\n' "$PORT"
  fi
  printf '\n  The wall:        http://127.0.0.1:%s/   (any time: %scorral-light launch%s)\n' "$PORT" "$B" "$N"
  printf '  Your workspace:  %s\n' "$AIOS"
  if has_lane claude; then printf '  Try next:        open a NEW terminal, run  %scd %s && claude%s  and type  %s/status%s\n' "$B" "$AIOS" "$N" "$B" "$N"; fi
  printf '  Health:          corral-light doctor   ·   Log: %s\n' "$LOG"
  printf '  Update:          run the same install line again (a copy is at %s/install.sh)\n' "$CL"
  printf '  Remove:          bash %s/install.sh --uninstall\n\n' "$CL"
}

trap - ERR
set +e
main "$@" 2>&1 | tee -a "$LOG"
rc="${PIPESTATUS[0]}"
exit "$rc"

```
