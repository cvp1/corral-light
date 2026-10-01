# v2 Round 2 — gemini38 (model echoed: gemini-3.8-flash-high, effort: None, pane 82c102509901)

### 1. Cross-Examination

#### Grok (grok-4.7)
- **Most agree with**: **Gap 6 (Shared build outputs)**. Overwriting host macOS virtualenvs and `node_modules` with Linux ELF binaries on a shared bind mount will instantly break native toolchains. Anonymous overlay volumes over dependency and cache directories are mandatory to prevent host cross-contamination.
- **Where Grok is wrong**: Grok claims that *"launchd calendar jobs skip intervals missed during sleep"* to justify an in-container cron loop. *Platform knowledge*: Apple’s `launchd` documentation and kernel implementation explicitly state that if a Mac is asleep when `StartCalendarInterval` arrives, the job fires immediately upon wake (coalesced into one run). Furthermore, Grok’s in-container loop completely dies if Docker Desktop is stopped or quit.
- **Verdict change**: In Round 1, I recommended **KILL** for S6 (Seed / Scheduler). Under C1 ("Containers on every host; solve host reach, do not route around it"), I change S6 to **RESHAPE**: The clock/trigger must stay on the host via `launchd` (surviving sleep and wake catch-up), which executes the job inside the container via `docker exec`.

#### Codex (sol-6.1)
- **Most agree with**: **Gap 1 (Native build & execution environments)**. macOS workspaces contain Mach-O executables, native dylibs, and Darwin-specific build scripts that no Linux container can magically execute via path remapping alone. Preserving transparent host execution for native build targets is essential for parity (C4).
- **Where Codex is wrong**: Codex recommends routing `corral-hostd` through an *"authenticated SSH tunnel"* rather than direct HTTP on loopback. This is circular and brittle: establishing and supervising an SSH tunnel just to broker Keychain tokens or notifications introduces SSH connection latency, tunnel teardown edge-cases, and key-management dependencies for local IPC that Docker Desktop’s `host.docker.internal` handles natively.
- **Verdict change**: In Round 1, I recommended rejecting SSH in favor of hostd for general host commands. I change S3 to **RESHAPE**: General commands should use SSH with a strict login-shell wrapper, reserving `corral-hostd` for Aqua GUI-session capabilities.

---

### 2. Settle the Disagreements

#### a. Scheduler: Host Trigger + Container Execution
Adopt a hybrid model: host `launchd` (macOS) and `systemd` (Linux) act as the system clock, dispatching jobs inside the container via `docker compose exec`. *Platform knowledge*: `launchd`’s `StartCalendarInterval` handles macOS power assertions and reliably executes missed runs immediately upon system wake, whereas a sleep-suspended Docker VM clock drifts and misses jobs silently. If Docker is stopped, the host wrapper starts Docker Desktop headlessly, waits for the socket, and executes the containerized job.

#### b. General Host Commands: The SSH / hostd Line
General shell commands belong over SSH, invoked via a dedicated wrapper (`corral-host-shell`) that explicitly loads `/etc/zprofile`, `~/.zprofile`, and `/opt/homebrew/bin` to mirror the user's interactive login PATH. Pin the host key in the image during install to prevent MITM prompts. The boundary is absolute: arbitrary commands, compilers, and `brew` run over SSH; session-bound GUI services (Keychain broker, notifications, URL opening, and Aqua clipboard) run exclusively through `corral-hostd`.

#### c. Logins: Device Code Preferred, Dynamic Proxy Fallback
*Platform knowledge*: Claude Code supports token entry (`claude setup-token`), Codex supports ChatGPT browser/device auth, Antigravity/Gemini provides Google OAuth with manual copy-paste fallback, and Grok uses device-code/token auth. Use device codes or pasted tokens as the primary flow across all lanes. For lanes requiring a browser redirect, `corral-hostd` provides a temporary `expose` proxy that listens on host `127.0.0.1:<port>` and tunnels traffic to the container callback listener so host browser redirects complete seamlessly.

#### d. Dev Servers and Pane Ports: Static Range + On-Demand Forwarding
Configure `docker-compose.yml` with a published default port range on `127.0.0.1` (e.g., `3000-3010`, `8000-8010`) to provide zero-configuration parity for standard agent dev servers (Vite, Next.js, FastAPI). For non-standard or dynamically chosen ports, provide an on-demand `corral-host expose <port>` verb (implemented via hostd or SSH remote forwarding `ssh -R`) to bind the host port dynamically.

#### e. File Watching over VirtioFS: Polling Hub + Synthetic In-Container Events
VirtioFS does not reliably propagate recursive FSEvents into Linux `inotify` under high write volume. Configure `hub.py` and Seed watchers to use hybrid polling (`CHOKIDAR_USEPOLLING=true`, 1-second interval) as a robust fallback. For CLI file watchers, `corral-hostd` can run an FSEvents listener that emits directory-level touch events across a socket into the container to trigger immediate `inotify` wakes on host-originated edits.

---

### 3. Consolidated hostd Verb Set & Threat Model

**Verb Set**:
- `secret get <handle>`: Calls host secret broker; returns status and securely piped data.
- `notify <title> <body>`: Dispatches native desktop notification banner.
- `open <url|path>`: Opens browser URL or validated workspace path on host.
- `clipboard get|set`: Reads or writes host system pasteboard.
- `expose <port>`: Binds host loopback port and forwards to container listener.
- `facts`: Returns host OS version, paths, and Aqua/login-session status.

**Threat Model**:
- *Trust Boundary*: Panes and container are as trusted as the host user (C4 parity; isolation is incidental).
- *Attacker*: Unauthenticated local processes, other containers, or malicious web pages executing CSRF/DNS rebinding against loopback.
- *Auth & Bind*: Bind strictly to host `127.0.0.1`. Require `Authorization: Bearer <token>` (random 256-bit token in `~/.config/corral/hostd.token`, mode 0600, mounted read-only).
- *Transport*: HTTP JSON RPC; reject browser Origin headers (no CORS); strictly typed payloads with size caps.
- *Execution*: No arbitrary shell interpolation (`execve` fixed argument lists only). Confine `open` paths strictly to mounted directory allowlists.

---

### 4. Phase 0 Exit Criteria (Runnable on Every Lane)

1. **Authentication & Session Persistence**: Complete `corral login <lane>` for all 4 lanes using device codes or the callback proxy; restart the container and execute an authenticated model query with zero manual intervention.
2. **Bidirectional Filesystem Parity**: A pane creates `/Users/.../workspace/sentinel.txt`; host verifies ownership matches host UID/GID; host edits the file; pane reads the update within 250ms.
3. **Build Toolchain Isolation**: Run `npm install` or `pip install` inside the container; verify native host tooling remains completely uncorrupted by Linux binaries or missing dynamic linkers.
4. **Interpreter Path Mapping**: Execute mapped interpreter paths (e.g., `~/.venvs/aios-seed/bin/python3`) from inside a pane; verify it executes as a Linux binary without modifying the host Mach-O file.
5. **Session-Bound Service Reach**: Run `corral-host secret`, `notify`, `clipboard`, and `open`; verify Keychain resolution, GUI notification, clipboard round-trip, and host browser launch.
6. **Host Shell & Environment Integrity**: Execute `ssh host.docker.internal "brew --version && launchctl print gui/$(id -u)"`; verify successful zero-exit execution and full Homebrew PATH availability.
7. **Port Reachability & Dev Servers**: Start an HTTP server on a dev port inside a pane; verify the host browser accesses both `http://127.0.0.1:<port>` and `http://localhost:<port>`.
8. **Host Daemons Reach**: Connect to Ollama (`host.docker.internal:11434`) and hamlib rig (`host.docker.internal:4532`); verify complete request-response round trips under 15ms.

---

### 5. Bottom Line

Buildable if host `launchd` drives containerized Seed jobs and logins use a hybrid proxy/device-code bridge, but virtiofs file-event latency and APFS case-folding collisions during heavy agent builds remain the single biggest operational risk.
