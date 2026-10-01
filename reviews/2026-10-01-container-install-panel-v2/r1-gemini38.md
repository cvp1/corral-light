# v2 Round 1 — Gemini 3.8 Flash High (model echoed: gemini-3.8-flash-high, pane 82c102509901)

### Section Verdicts

- **S1 Mounts**: **RESHAPE** — Mounting `~` into a Linux container without masking `~/.cache`, `~/.local`, or platform dotfiles corrupts host toolchains and collides on case-insensitive APFS.
- **S2 Identity and Paths**: **BUILD** — Matching host UID/GID and generating a matching `/etc/passwd` home directory solves permission skew cleanly across all four lanes.
- **S3 Host Reach**: **RESHAPE** — Delegating general host commands to SSH introduces interactive tty, known-hosts, and latency drag; `corral-hostd` should be the unified RPC spine instead.
- **S4 Logins**: **RESHAPE** — CLI OAuth login callbacks bind ephemeral `localhost:<random_port>` inside the container, which host browsers cannot hit without host-network mode or an explicit OAuth redirect proxy.
- **S5 Networking and Pairing**: **BUILD** — Dual-stack `127.0.0.1`/`::1` publishing and bypassing raw gateway peer-UID checks preserves seat MCP pairing without vendor lock-in.
- **S6 Seed and Scheduler**: **KILL** — A containerized cron engine fails on a laptop that sleeps; launchd exists precisely to handle macOS power assertions, wake events, and missed-interval catchup.
- **S7 Release Manifest**: **BUILD** — Pinned multi-arch digests and manual gates on default model bumps eliminate silent prompt and behavior drift across all four lanes.
- **Phases**: **RESHAPE** — Phase 0 will be blocked at "every lane logs in" until the OAuth callback loopback routing and Linux ARM64 binary availability are resolved before deployment.

---

### Parity Gaps (Ranked by Severity)

1. **OAuth Loopback Capture on Interactive Logins**
   - **Lanes**: Claude Code, Codex, Antigravity/Gemini (all use browser-based OAuth callbacks).
   - **Failure**: Agent CLIs spin up a temporary loopback HTTP server (e.g., `http://localhost:4321/callback`) to receive the OAuth token. The host browser opens the link and redirects to host `127.0.0.1:4321`, which is not exposed by the container, leaving the CLI hung waiting forever.
   - **Fix**: Run `corral login` through an in-container callback forwarder, force `--manual` / device-code flows across all lanes, or let `corral-hostd` proxy local auth ports dynamically.

2. **Laptop Sleep / Wake Kills Container Scheduling**
   - **Lanes**: Seed / All lanes executing background workflows.
   - **Failure**: On Craig's daily Mac, Docker Desktop VM time drifts or freezes when macOS sleeps. A standard container cron/loop misses runs entirely without launchd's wake-on-schedule or catch-up semantics.
   - **Fix**: Keep scheduled triggers on the host via lightweight launchd user timers that invoke `docker exec` into the container, retaining container execution without losing host power-state awareness.

3. **Inotify & File Watching Desync over VirtioFS**
   - **Lanes**: All lanes (especially workspace watchers, test runners, git status).
   - **Failure**: VirtioFS on macOS does not reliably propagate host FSEvents to Linux `inotify` recursively and at high throughput. Agents editing files natively or external processes updating git trees will fail to trigger container-side lane rebuilds or watch hooks.
   - **Fix**: Configure `hub.py` and watcher tooling to support a fallback hybrid polling mode (or notify via `corral-hostd` on host-driven changes).

4. **APFS Case-Insensitive vs. Linux Ext4 Collisions**
   - **Lanes**: All lanes.
   - **Failure**: APFS is case-preserving but case-insensitive. Linux inside the container assumes case-sensitivity. An agent executing `rm file.txt` or creating `File.txt` alongside `file.txt` triggers silent overwrites or unexpected ENOENT failures.
   - **Fix**: Mount workspace roots with explicit volume checks, and enforce case-collision linting in `corral doctor`.

5. **Host GUI App / URL Open (`open` / `xdg-open`)**
   - **Lanes**: All lanes (agents opening browser previews, local web servers, diff viewers).
   - **Failure**: Invoking `open <url>` fails inside Linux (`xdg-open` missing or disconnected from display server). Agents executing web builds or previewing artifacts hang or error out.
   - **Fix**: Place an `open` wrapper on the container `PATH` that routes URL and file preview invocations back to `corral-hostd notify` / `open` handler.

6. **Host Binary / Environment Assumptions in Workspace Hooks**
   - **Lanes**: All lanes (scripts invoking `pbcopy`, `brew`, `osascript`, `sw_vers`).
   - **Failure**: Workspace maintenance scripts written on macOS assume Darwin core utils. Even with mapped Python paths, child subshells fail abruptly on missing host CLI tools.
   - **Fix**: Provide lightweight shim wrappers on container `PATH` for standard Darwin stubs that forward to `corral-hostd` or return explicit compatibility notices.

7. **Git GPG / SSH Signing Prompt Suppression**
   - **Lanes**: All lanes committing code.
   - **Failure**: While ssh-agent is forwarded for transport keys, SSH commit signing or GPG signing that invokes GUI pinentry dialogs fails silently inside the container without a tty or host pinentry bridge.
   - **Fix**: Ensure SSH agent forwarding includes the host pinentry socket or configure lane git configs inside `corral-home` to bypass signing or route prompts through `corral-hostd`.

---

### Answers to Open Questions

1. **corral-hostd vs. lighter mechanism for Keychain:**
   `corral-hostd` is the correct, minimal shape. macOS Keychain access is strictly bound to the Aqua login session (via `security` or Security.framework). SSH non-interactive sessions are systematically blocked by `securityd` with "User interaction is not allowed" error `-25308`. Unix domain sockets or loopback HTTP from a user LaunchAgent are the only reliable mechanisms.

2. **Docker Desktop on macOS (virtiofs, APFS, file watching):**
   VirtioFS performs well for bulk I/O under `/Users`, but two failure modes remain: file events (`inotify` events are frequently dropped across the hypervisor boundary during burst writes) and case-folding mismatches. Agent tools relying on rapid `chokidar` or `watchman` loops will miss changes unless polling fallbacks are enabled.

3. **Lane login flows inside a container:**
   Yes, browser callbacks fail across the board. Every modern CLI (Claude, Gemini, Codex) defaults to opening a system browser and catching `http://127.0.0.1:<port>`. Inside the container, there is no display to launch the host browser, and the host browser cannot return tokens to non-exposed random container ports. All logins must be driven by out-of-band device codes or manual copy-paste auth flows.

4. **In-container scheduler vs. launchd:**
   A container cron loop is materially worse on a daily laptop. When the MacBook lid closes, the hypervisor suspends; container cron stops ticking, misses window intervals, and does not wake the machine. launchd's `StartCalendarInterval` gracefully fires upon system wake.

5. **Native vs. container pane parity:**
   Clipboard integration (`pbcopy`/`pbpaste`), hardware notifications with click-to-focus callbacks, access to running host dev-servers (e.g. databases bound to host `localhost` rather than `0.0.0.0`), and Docker-out-of-Docker workflows (agents running `docker compose` to test projects).

---

### corral-hostd: Architecture and Threat Model

- **Verb Set**: Keep it strictly limited to `secret` (query status/execute predefined targets), `notify` (desktop banners), and add `open` (URL forwarding to host default browser).
- **Reject general command over SSH**: SSH requires maintaining an authorized keys file, host `sshd` enablement (often disabled or enterprise-restricted on macOS), and interactive pseudo-tty plumbing. Instead, define explicit whitelisted action verbs in `corral-hostd` if commands are needed, or maintain a clean boundary where the container never executes arbitrary host shell code.
- **Threat Model**:
  - The listening daemon runs inside the user's host login context with full Keychain and filesystem access.
  - The container network can reach host loopback.
  - *Risk*: An untrusted MCP tool or rogue script inside the container makes arbitrary HTTP requests to `corral-hostd`.
  - *Smallest Secure Design*:
    1. Bind strictly to `127.0.0.1`.
    2. Require an `Authorization: Bearer <token>` header on every request.
    3. Generate the token on host startup, writing it to `~/.config/corral/hostd.token` mode 0600, mounted read-only into the container.
    4. Reject arbitrary shell execution completely. Payloads are strictly typed JSON (e.g., `{"service": "github", "account": "cvp"}`), returning only success/failure or piping stdout safely without executing arbitrary shell strings.

---

### Acceptance Tests (Phases 0–1)

1. **Non-Interactive Headless OAuth Test**
   Run `corral login <lane>` for each of the four lanes entirely inside a freshly spun up container without an X11/Wayland display; authenticate successfully via printed URL and device code/token input.

2. **Keychain Secret Resolution via `corral-hostd`**
   From inside a pane on every lane, execute `corral-host secret get <item>`; verify exit code 0, correct secret resolution on host, and zero secret value leakage into container transcript logs.

3. **Cross-Boundary Host Notification Test**
   Execute `corral-host notify "Test" "Message"` from inside a container pane; verify the notification renders immediately in the macOS Notification Center with host session attribution.

4. **SSH Agent Forwarding & Git Push Test**
   Run `git ls-remote <private-ssh-repo>` from inside the container using the forwarded `/run/host-services/ssh-auth.sock`; verify authentication succeeds against host keys without prompting for passphrases.

5. **VirtioFS Case Sensitivity & Path Symmetry Test**
   Create `$CORRAL_WORKSPACE/test_parity.txt` on the host, modify it inside the container using its identical absolute path `/Users/.../test_parity.txt`, and verify changes are instantly visible on the host without file lock or timestamp corruption.

6. **Host Service Loopback Reachability (`rigctld` / Ollama)**
   Run `rigctl -m 2 -r host.docker.internal:4532` and `curl -s http://host.docker.internal:11434/api/tags` from inside a container pane; verify sub-10ms response times and zero routing drops across the bridge gateway.

---

### Failure Condition

On a daily Mac, this will fail when Docker Desktop's VirtioFS drops inotify file events and sleeping the laptop halts the in-container scheduler, causing agent panes to miss workspace changes and background runs to silently skip.
