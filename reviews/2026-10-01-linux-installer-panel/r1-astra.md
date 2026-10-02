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
