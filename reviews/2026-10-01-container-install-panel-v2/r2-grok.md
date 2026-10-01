# v2 Round 2 — grok (model echoed: grok-4.7, effort: xhigh, pane 8018d2cd6eed)

**Cross-examination**

**Codex.** The fix I adopt is the host execution contract: cwd, argv, stdin, PTY, signals, and exit status for Mach-O tools and native extensions. A Linux interpreter mapped over a Mac virtualenv does not make that environment runnable. The claim that is wrong is that an SSH tunnel to hostd is a lighter transport than loopback HTTP. It is a tighter exposure, and it is more machinery. On the daily Mac, Remote Login is often off, while Docker Desktop already delivers `host.docker.internal` to host loopback.

**Gemini.** The fix I adopt is a host login timer as the wake and presence doorbell, with the job still executing inside the container. The claim that is wrong is that an in-container scheduler misses runs entirely and that `launchd` is what wakes the laptop. `StartCalendarInterval` does not wake the machine. Apple's `launchd.plist(5)` says that, unlike cron, missed firings coalesce into one run on next wake. A due-time tick matches that coalescing while Docker is up. S6 stays **RESHAPE**. It does not become **KILL**.

**My round 1, corrected.** I was wrong that launchd skips intervals missed during sleep. It coalesces them. S6 is therefore both a doorbell and an in-container executor, not a loop alone. I also move general host commands off SSH as the primary path. SSH lands outside the Aqua session, which was my own Phase 1 objection. S5 stays **RESHAPE**: Gemini's **BUILD** is wrong, because a bridge address does not prove Tailscale Serve. S2 stays **RESHAPE**: matching UID and passwd does not make Mach-O dependencies Linux-compatible. S7 stays **RESHAPE** until a missing Linux build for that arch fails the tag.

## Settled design

**a. Scheduler — both.** Job bodies, last-run state, overlap, timeout, and retry stay in the container, in one Seed backend, with the host time zone read at start. While Docker is up, a one-minute tick runs anything now due, once, which is the launchd coalesce rule. A login LaunchAgent (and a systemd user timer on Linux) is only the doorbell: at login, after wake, and on a coarse calendar it starts Docker if needed and signals that tick. Neither doorbell nor container tick wakes a closed lid. If Docker is quit or has not started, the in-container loop does not run and a host timer that only `docker exec`s fails until the doorbell starts Docker. Persist last-run so a recreate does not double-fire. The twelve manifests are absent here, so per-job equivalence is unverifiable from the text.

**b. General commands — one `spawn` contract, hostd on the Mac.** The line: session capabilities and host process execution go through hostd; raw shell strings go nowhere. `spawn` takes an argv array, cwd, an env allowlist, optional PTY, signal forwarding, timeout, and exit status, and it runs in the login session with the login PATH, so `brew`, Aqua `launchctl`, and workspace hooks work. SSH with pinned host keys and the same contract is the Linux fallback where sshd is already the admin path, not a second semantics. Structured verbs stay for what `spawn` still cannot see: the Keychain broker, notifications, clipboard, and `open`. Calling the existing broker preserves its real contract. "Outcomes only" is valid only if that is what the broker already returns (unverifiable from the text).

**c. Logins — both, per lane.** Platform knowledge; exact flags are unverifiable from the text. Claude: prefer `setup-token` or a pasted token, and keep an `expose` proxy so the default localhost OAuth still works. Codex: device code or pasted API key, plus the proxy for ChatGPT localhost OAuth. Gemini/Antigravity: the proxy is required if the CLI has only a Google localhost redirect; use copy-paste when the CLI offers it. Grok: unverified; accept a pasted token into `~/.grok`, and use the proxy if it is OAuth. One `corral login <lane>` UI. Store files on `corral-home` mode 700. Recreate the container and confirm the session still authenticates. Lane-specific protocol handlers satisfy C2. A rule that only one CLI must use hooks does not.

**d. Ports — both.** Publish a fixed localhost range (dev servers the pane binds). On-demand `expose` covers random ports, including OAuth. Bind publishes to `127.0.0.1` only. An `open` wrapper on container `PATH` sends `http(s)` and files under the mount list to the host. Pre-publishing the range is what makes `localhost` on the Mac match a native pane without a round trip.

**e. Watches.** Virtiofs does not deliver host FSEvents as Linux inotify (platform knowledge). The hub and Seed, which we control, poll or consume a hostd `watch` feed of paths under the mount allowlist, and they do not trust inotify for correctness. For the CLIs, set the polling variables the image can set, and run performance-sensitive native watchers through `spawn` so they use host FSEvents. Unmodified inotify-only tools will miss host-originated bursts. Doctor reports that. A FUSE bridge is later work, not Phase 0. APFS case-folding is native Mac behavior; the real bug is Linux `git` with `core.ignorecase=false`. Doctor should warn on that. Do not shim every Darwin binary; `spawn` runs the real one.

## hostd

Verbs: `secret` (existing broker, unchanged contract), `notify`, `open`, `clipboard-get`, `clipboard-set`, `expose`, `facts`, `watch`, `spawn`.
No shell strings, no raw AppleScript, no arbitrary URL schemes.
Bind `127.0.0.1` only. Mac reach is `host.docker.internal`. Linux reach is a hostd-owned forwarder from the docker bridge to loopback, or SSH where sshd already exists. Never `0.0.0.0`.
Per-install 256-bit token, mode 600, stable across restart, rotatable, constant-time compare, failure lockout. Generating a new token on every hostd start will desynchronize the container.
Panes are trusted as the user. This is packaging, not a sandbox. The token only keeps other containers and the LAN out.
`spawn`, `open`, and `expose` are confused deputies: argv not a shell, paths under the mount list, exposed ports bound to host localhost, timeouts, length caps.
Logs record verb and secret handle only. Logout and the loginwindow fail closed.

## Phase 0 exit criteria

Each check runs from a pane on every lane, on the daily Mac.

1. **Login survives recreate.** Complete that lane's real flow (paste, device code, or proxied callback). Recreate the container. An authenticated prompt still succeeds. Credential files are only on `corral-home`, mode 700.
2. **Same path.** A host write at an absolute path with a space is what the pane reads; a pane write is owned by the host user, not root. A symlink whose target is outside the mount list is reported, not followed into the host.
3. **Interpreter overlay.** The mapped absolute interpreter prints a Linux identity. The host Mach-O at that path is byte-for-byte unchanged.
4. **spawn contract.** `sw_vers` and `brew --prefix` run via `spawn` with login PATH, a cwd on the mount, an argument containing a space, the child's exit code, and cancellation. The command sees the Aqua domain.
5. **Session broker.** `secret` returns the broker's outcome for a fixture handle and does not print a secret value. `notify` appears in Notification Center.
6. **Ports.** The pane listens on a pre-published port. From the Mac, `curl 127.0.0.1` and `curl localhost` both succeed. `open` on that URL reaches the host browser. The pane reaches Ollama on `host.docker.internal`.
7. **Git and watches.** Host `user.email` matches. `git ls-remote` over the forwarded agent authenticates. A host-side edit shows in pane `git status` within the agreed budget. Doctor states that watch mode is poll or hostd feed.
8. **Serve negative test.** A request from the bridge gateway with a forged Serve header does not receive Serve trust. Pairing still requires the pair code and cookie.

## Bottom line

Buildable under C1–C4 if Phase 0 requires the `spawn` contract, login reach, and localhost ports, not a chat demo. The biggest risk is virtiofs plus a prompt-only exit, so ordinary Mac work looks fine in a pane and fails on the first native tool, file watch, or dev server.
