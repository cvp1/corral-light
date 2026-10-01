# v2 Round 1 — GPT-6.1 Sol (read-only mode, model echoed: gpt-6.1-sol, effort: high, pane 8a560ee63e6b)

S1 mounts — **RESHAPE:** same paths help, but symlinks, overlapping mounts and container-only overlays need explicit rules.  
S2 identity and paths — **RESHAPE:** UID and interpreter replacement cannot make macOS executables or their dependencies Linux-compatible.  
S3 host reach — **RESHAPE:** the channels are sound, but SSH needs an execution contract and verified session access.  
S4 logins — **RESHAPE:** persistent credentials are appropriate; callback reachability and credential refresh remain unproven.  
S5 networking and pairing — **RESHAPE:** trusting a bridge address cannot establish that a request passed through Tailscale Serve.  
S6 Seed and scheduler — **RESHAPE:** cron needs durable missed-run, restart and concurrency semantics to match existing jobs.  
S7 release manifest — **BUILD:** reproducible versions are useful; artifact availability and functional parity must gate releases.  
PHASES — **RESHAPE:** move host execution, callbacks and representative tool workflows into Phase 0; answering prompts proves little parity.

Platform details below use my platform knowledge. Exact CLI builds, broker behavior and claimed code measurements are **unverifiable from the text**.

**Parity gaps, ranked**

1. **All lanes: native build and execution environments.** A Mac workspace can contain Mach-O programs, native extensions, macOS SDK dependencies and absolute executables beyond interpreters. Linux replacements do not reproduce macOS builds. **Fix:** provide documented host execution through SSH, preserving working directory, arguments, stdin, PTY, signals and exit status; add compatibility wrappers for existing entrypoints. Keep agents containerized and validate representative native workflows.

2. **All lanes: implicit host-local dependencies.** Existing MCP servers, databases and tools may reference `localhost`, host Unix sockets, host DNS or VPN routes. Inside the container those references change meaning. **Fix:** inventory dependencies and provide TCP/Unix-socket forwarding, configurable endpoints and host execution where necessary. Verify VPN, proxy and trust-store behavior.

3. **All lanes: filesystem and configuration compatibility.** Symlinks can leave mounted trees; overlapping home/config mounts can hide files; replacing a mounted Mac virtualenv could damage native execution. **Fix:** detect unresolved targets and mount conflicts; use container-only overlays for Linux executables. Preserve required configuration separately from lane credentials, without rewriting host binaries.

4. **All lanes: authentication lifecycle.** A callback bound to container loopback is unreachable through ordinary Docker port publication. Login success also says nothing about token refresh or browser opening. **Fix:** implement each supported login protocol behind the same user-facing interface; use narrowly published callback listeners or an internal forwarding proxy, host browser URLs and device codes where supported. Test refresh after restart.

5. **All lanes: file watching and interactive performance.** Docker Desktop file sharing can change latency and event delivery, especially for host-originated edits and dependency-heavy trees. **Fix:** test both directions; enable polling where necessary and run performance-sensitive native tools through host execution. Set measurable parity thresholds.

6. **Any affected lane: missing Linux artifacts.** The plan explicitly lacks Grok Linux arm64 verification; Antigravity’s usable Linux/ACP packaging is also unproven here. **Fix:** prove supported builds before image implementation. Evaluate a supported amd64 image under emulation where needed, with performance acceptance; otherwise obtain a compatible build before declaring parity.

7. **All lanes and Seed: session services.** Forwarded SSH keys do not reproduce GUI-session environment, unlocked Keychain access, Secret Service or privacy permissions. **Fix:** preserve actual broker operations in a session companion, verify authorization prompts, and test after logout/login and agent-socket changes.

8. **All scheduled lane work: lifecycle behavior.** Docker Desktop shutdown, VM suspension or container recreation can lose cron executions and in-flight work. **Fix:** persist scheduling state; define catch-up/coalescing, overlap, retry, timeout and timezone behavior per job; supervise children and restore schedules after restart.

**Open questions**

1. **Yes, a session companion is appropriate.** A LaunchAgent can reach the user session, but its presence does not automatically satisfy Keychain access controls or macOS privacy permissions. Calling the existing broker preserves more behavior than reimplementing it. An SSH tunnel to that companion is a lighter transport than exposing another host HTTP listener.

2. **Same-path mounts preserve names, not execution semantics.** Shared APFS retains its case behavior; Linux-owned volumes may differ. Watch events, metadata, permissions and I/O performance require measurement. Mac virtualenvs and native dependency trees must remain separate from Linux equivalents.

3. **No universal failure or success can be asserted.** Exact versions are unverifiable from the text. Loopback callbacks, headless browser launches, keyring selection, persistence and refresh are the required probes. Lane-specific protocol handling satisfies C2; lane-specific restrictions do not.

4. **Yes: missed runs and lifecycle supervision.** launchd calendar jobs can coalesce missed firings after sleep; systemd timer behavior depends on configuration. A cron loop must deliberately reproduce the actual twelve jobs’ behavior. Their manifests are absent, so equivalence is unverifiable.

5. **Beyond the listed capabilities:** native GUI automation, SDK builds, hardware tools, host-local sockets, host credentials and session permissions. SSH can cover much of this only when execution routing preserves the relevant context.

**corral-hostd**

`secret` and `notify` are reasonable; general commands should remain over SSH. But “outcomes only, never values” is compatible with parity only if that matches the native broker’s contract. Otherwise define equivalent broker operations before freezing the verbs.

The threat model must state that panes are trusted as the user: shared credentials and unrestricted host SSH grant substantial account authority. This is no sandbox. Protect against unauthenticated network clients, browser requests, token leakage and confused-deputy inputs; do not promise protection from an authorized malicious pane.

Smallest secure design: run hostd in the login session, bind loopback on both platforms, and reach it through a supervised authenticated SSH tunnel. Pin host keys during installation. Retain a random, rotatable, owner-readable token; use bounded JSON POST requests, reject browser origins, avoid permissive CORS, validate structured arguments, invoke the broker without shell interpolation, and redact logs. Verify the broker’s actual Keychain/Secret Service permissions.

**Six acceptance tests for Phases 0–1**

1. **Every lane:** login, complete any browser callback, restart/recreate the container, then exercise credential refresh and an authenticated prompt.
2. **Every lane:** edit, build and test a representative workspace; invoke a native Mac tool through host execution and verify cwd, quoting, output, exit status and cancellation.
3. **Every lane:** exercise a mapped interpreter, home/config overlays, paths with spaces, symlinks and ownership; confirm host executables remain usable afterward.
4. **Every lane:** observe edits made from both host and container using a real watcher; compare rebuild correctness and latency with native execution.
5. **Every lane:** use SSH keys, the secret broker, notifications, Ollama and rigctld; repeat after login-session and container restarts.
6. **Every lane:** connect to a host-local dependency, exercise pairing and Tailscale Serve, and verify forged proxy headers cannot obtain Serve-specific trust.

**What would make this fail on the daily Mac:** successful chat demos masking broken native tools, host-local dependencies and file-watch workflows until the operator’s ordinary work repeatedly requires repairing the bridge.
