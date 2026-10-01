# Corral Light container — implementation and test plan

Status: **PLAN, for later implementation.** Written 2026-10-01. Nothing built.
Design of record: `docs/container-install-proposal.md` (v3, panel-converged).
Panel record: `reviews/2026-10-01-container-install-panel/` (v1) and
`reviews/2026-10-01-container-install-panel-v2/` (v2).

This document says **what to build, in what order, and how each piece is
proven**. It does not restate the design's reasoning; read v3 for that.

---

## 0. Decisions locked (the operator, 2026-10-01)

| Decision | Value |
|---|---|
| Where it runs | A container on every host, including the daily Mac (C1) |
| Lane rules | Nothing lane-specific gates the build (C2); per-lane *login protocol* handling is allowed |
| Folders | Mounts are the user's list; no assumptions about sync or backup (C3) |
| Design rule | Parity with native, per lane (C4) |
| Image architecture | **linux/amd64 only** for now. No linux/arm64 build. |
| Image visibility | **Public** on GHCR (`ghcr.io/cvp1/corral-light`). Source repos `cvp1/corral-light` and `cvp1/ai-os-seed` are already public (checked 2026-10-01). |
| Test hosts | **mac-host**, an **Intel Mac laptop running Omarchy**, and **thinkpad-host** (IBM ThinkPad running Omarchy) |

### Consequence of amd64-only on mac-host — must be tested, not assumed

mac-host is Apple Silicon (`uname -m` = arm64; Docker Desktop reports
`aarch64 linux`). An amd64-only image runs there **under emulation** — Docker
Desktop's "Use Rosetta for x86_64/amd64 emulation" setting, or QEMU if Rosetta
is off. That is a supported mode, but:

- Every lane's Linux binary must start and work under Rosetta. Some x86
  binaries assume CPU features emulation does not provide [NEEDS VERIFICATION
  per binary]. Test T-ARCH below covers it.
- Expect slower CPU-bound work (npm install, builds) than native. Phase 0
  measures it.
- If a lane fails under emulation, the fix is adding linux/arm64 to the build
  matrix — a CI flag, not a redesign. Until then that lane is reported as
  degraded on mac-host by `doctor`.
- The release gate in v3 ("tag fails if a lane lacks a Linux build for a
  supported arch") now means **amd64 only**.

---

## 1. Target matrix

| | mac-host | Intel Mac laptop (Omarchy) | thinkpad-host (ThinkPad, Omarchy) |
|---|---|---|---|
| Hostname | `mac-host` | **to confirm** | `thinkpad-host` [to confirm exact] |
| OS | macOS 27 | Omarchy (Arch Linux, Hyprland, Wayland) | Omarchy |
| CPU | Apple M4 (arm64) | Intel x86_64 | Intel/AMD x86_64 |
| Image runs | amd64 **under Rosetta** | amd64 native | amd64 native |
| Runtime | Docker Desktop (29.6.2) | Docker Engine (systemd) [NEEDS VERIFICATION: installed by default on Omarchy] | same |
| File sharing | virtiofs (watch caveats apply) | native bind mounts (inotify works) | same |
| `host.docker.internal` | built in | `extra_hosts: host-gateway` | same |
| hostd reach | host loopback via Docker Desktop | hostd forwarder on the docker bridge gateway | same |
| Session services | LaunchAgent; Keychain; Notification Center; `pbcopy`; `open` | systemd user unit; Secret Service [NEEDS VERIFICATION: provider on Omarchy]; mako via `notify-send`; `wl-copy`/`wl-paste`; `xdg-open` | same |
| SSH agent | `/run/host-services/ssh-auth.sock` | mount `$SSH_AUTH_SOCK` | same |
| Host shell | Remote Login (sshd) + `corral-host-shell` | sshd + `corral-host-shell` [NEEDS VERIFICATION: sshd enabled] | same |
| Scheduler doorbell | LaunchAgent | systemd user timer | same |
| Existing AI-OS | yes (`~/ai-os`, launchd jobs) | unknown — fresh install path | unknown — fresh install path |
| Role in testing | daily seat; full parity incl. Keychain, rig, launchd jobs | clean Linux install; Intel hardware | second clean Linux install; proves it isn't one machine's luck |

The two Omarchy hosts are the **clean-install** proof. mac-host is the
**migration** proof (existing workspace, existing scheduled jobs, existing
native hub to retire).

---

## 2. Deliverables (files to create)

All paths in `cvp1/corral-light` unless marked **[seed]** (`cvp1/ai-os-seed`).

| Path | What |
|---|---|
| `container/Dockerfile` | amd64 image: pinned base by digest, Python 3.12, Node, adapters, four CLIs, Seed, `corral-host` client |
| `container/release-manifest.json` | The one manifest: base digest, Python, Node, adapter versions, CLI versions + Linux x64 artifact URLs and checksums, Seed tag, per-lane default model/effort |
| `container/entrypoint.sh` | Identity (passwd/group), overlays check, parity-map links, SSH snippet, hostname check, then exec hub |
| `container/parity-map.example.json` | Example interpreter map; the real one is user config, never baked into the public image |
| `container/compose.yaml.tmpl` | Template `install.sh` renders: mounts, overlays, volumes, ports (8098 + dev range, v4 and v6 loopback), hostname, extra_hosts, env |
| `bin/corral` | POSIX sh shim: `up/down/pair/login/doctor/update/logs/shell/expose` → compose / `docker exec` |
| `install.sh` | Prereq checks, renders compose, installs hostd + doorbell + host shell, pins host key, mount sentinels, pull, start |
| `hostd/corral_hostd.py` | Stdlib host companion: `secret notify open clipboard-get clipboard-set expose facts` |
| `hostd/macos/com.cvp1.corral-hostd.plist.tmpl` | LaunchAgent |
| `hostd/linux/corral-hostd.service.tmpl` | systemd user unit (+ gateway forwarder socket) |
| `hostd/client/corral-host` | In-image client CLI (stdlib), on every lane's PATH |
| `host/corral-host-shell` | Host-side login-PATH wrapper invoked over SSH; preserves cwd/argv/stdin/PTY/signals/exit |
| `host/doorbell/` | LaunchAgent (macOS) and systemd user timer (Linux): start runtime at login, nudge catch-up after wake |
| `hub.py`, `corral_core/edge.py`, `sessions.py`, `doctor.py` | Changes listed in WS3 |
| `tests/container/` | Unit tests for new code (stdlib `unittest`, the repo's style) |
| `tests/parity/` | The parity suite (section 5) |
| `.github/workflows/image.yml` | Build, scan, test, publish amd64 image to GHCR (public) |
| **[seed]** `scheduler/backends/container.py` | Third scheduler backend alongside cron and launchd in `scheduler/sync.py` |
| **[seed]** `scheduler/tick.py` | Minute tick: run due jobs once, durable last-run state, overlap/timeout/retry |

---

## 3. Workstreams

Each workstream lists tasks and the test IDs (section 5) that prove it.
Order of work is in section 4.

### WS1 — Image and release manifest
1. Write `release-manifest.json` with every pinned artifact and checksum.
2. Dockerfile installs **only** from the manifest; build fails on checksum
   mismatch.
3. Fetch Linux x64 builds of all four CLIs: Claude Code, Codex (via the
   adapter's npm deps), Grok CLI (`grok-<ver>-linux-x86_64` or equivalent —
   confirm artifact name), Antigravity ACP server (Linux x86-64 row of
   `install_antigravity_acp.RELEASES`).
4. Bake `spike/node_modules` from the lockfile with `npm ci` for linux/x64.
5. Install Seed at the manifest tag into `/opt/aios-seed` with its own venv.
6. Public-image hygiene: no host paths, usernames, hostnames, tokens or
   personal config in any layer. Parity map, mounts and hostnames are runtime
   config only.
7. Lane defaults (model/effort) come from the manifest into the image's
   seeded config.
- Proven by: T-IMG-1..5, T-ARCH.

### WS2 — Entrypoint: identity, paths, overlays
1. Create passwd/group entries for `$HOST_UID`/`$HOST_GID` with home =
   `$HOST_HOME` (same absolute path as the host).
2. Set a container-only `PATH`; no host bin dir ever appears on it.
3. Apply the interpreter parity map: for each entry, the compose template has
   already mounted a container-only volume or file at that exact path; the
   entrypoint verifies it executes and reports otherwise. Never write to a
   host path.
4. Write the container SSH snippet (agent socket, then `Include` the host's
   `~/.ssh/config`); mount `~/.gitconfig` and `~/.ssh/known_hosts` read-only;
   install the `git-credential-corral` helper that calls the host's helper via
   `corral-host-shell` when the host config names `osxkeychain` or similar.
5. Overlays: compose declares anonymous volumes over `node_modules`, `.venv`,
   `__pycache__` and tool caches inside each mounted project listed in
   config; entrypoint lists them for `doctor`.
6. Verify `hostname` equals `$HOST_HOSTNAME`; report mismatch.
7. Exec the hub. **No gate** on hooks or on any lane's config.
- Proven by: T-ID-1..4, T-OVL-1..3, T-GIT-1..3.

### WS3 — Hub and doctor changes
1. `CORRAL_LIGHT_BIND` defaults to `0.0.0.0` when `CORRAL_CONTAINER=1`;
   compose publishes loopback only.
2. `CORRAL_WORKSPACE` drives the default pane directory
   (replaces the hardcoded `~/aios` in `sessions.py`); default = Seed's
   reported workspace.
3. Tailscale Serve: in container mode `edge.via_serve` grants Serve trust
   **only** when Serve runs inside the container (loopback peer) or is
   disabled; never to the bridge gateway address.
4. `doctor`: add container sections — per-lane non-executable absolute paths
   in each lane's known config locations, overlays in force, dangling
   symlinks out of the mount list, `core.ignorecase` mismatch on
   case-insensitive mounts, watch mode, emulation status (Rosetta/QEMU) per
   lane binary, hostd reachability, host-shell reachability, Docker Desktop
   prerequisites. Reports only; never refuses to start.
5. File watching: hub and Seed treat filesystem events as hints and reconcile
   by polling at a set interval; set `CHOKIDAR_USEPOLLING` and similar in the
   image environment.
6. Lane defaults read from the manifest-seeded config; consult and the UI show
   the effective model and effort.
- Proven by: T-NET-1..4, T-DOC-1..3, T-WATCH-1..2, existing native suites.

### WS4 — corral-hostd
1. Stdlib HTTP server bound to host loopback. On Linux, add a forwarder
   from the docker bridge gateway to loopback, started by the same unit.
2. Token: 256-bit, created at install, mode 600 at
   `~/.config/corral-light/hostd.token`, mounted read-only into the
   container; stable across restarts; `corral hostd rotate` regenerates and
   restarts both sides.
3. Verbs, typed JSON, size caps, timeouts:
   - `secret` → existing broker (`~/ai-os/bin/secret` or the Seed equivalent),
     unchanged contract; returns its outcome.
   - `notify` → macOS Notification Center / Linux `notify-send`.
   - `open` → `open` / `xdg-open`; `http(s)` URLs and real paths only.
   - `clipboard-get` / `clipboard-set` → `pbpaste`/`pbcopy` or
     `wl-paste`/`wl-copy`; capped; never logged.
   - `expose` → lease: bind a host loopback port (v4 and v6) and forward to the
     container listener; auto-expire; `expose-close`.
   - `facts` → OS, version, session state, hostname.
4. Reject requests carrying a browser `Origin`; no CORS; constant-time token
   compare; failure lockout; logs record verb and secret handle only.
5. Units: LaunchAgent (Aqua session) and systemd user unit.
- Proven by: T-HOSTD-1..8, T-SEC-1..4.

### WS5 — Host shell over SSH
1. `corral-host-shell`: loads the user's login environment (zsh/bash login
   files; on macOS adds `/opt/homebrew/bin` as the login shell would), then
   execs the requested argv in the requested cwd. PTY when asked. Signals and
   exit status pass through.
2. In-image `corral-host-run` client: `ssh -o StrictHostKeyChecking=yes
   host.docker.internal corral-host-shell --cwd ... -- argv...`.
3. `install.sh` checks sshd is enabled (macOS Remote Login; Linux sshd), adds
   the host key to the image's known_hosts at install, and authorizes the
   user's own key for loopback logins only if not already present (shown to
   the user, never silent).
- Proven by: T-HOST-1..5.

### WS6 — Logins, per lane
1. `corral login <lane>` and the UI button, same interface for all four.
2. Per-lane flow, verified in Phase 0 (record what actually works):
   - Claude Code: `claude setup-token` / pasted token, else callback via
     `expose`.
   - Codex: device auth where offered, else callback via `expose`.
   - Antigravity: Google flow; copy-paste code where offered, else `expose`.
   - Grok: discover in Phase 0; pasted token or `expose`.
3. Credentials only in that lane's config dir on `corral-home`, mode 700.
4. Refresh after container recreate.
- Proven by: T-LOGIN-1..4 per lane.

### WS7 — Ports
1. Compose publishes 8098 and a dev range (default `3000-3010`, `5173`,
   `8000-8010`; configurable) on `127.0.0.1` and `::1` only.
2. `corral expose <port>` and the `expose` hostd verb for anything else,
   including OAuth callbacks.
3. `CORRAL_HOST_PORTS` (default `11434,4532`) documented for host daemons.
- Proven by: T-PORT-1..4.

### WS8 — Seed in the container, scheduler, doorbell
1. **[seed]** `scheduler/backends/container.py`: reconcile `manifest.yml` into
   the tick's job table (no launchd/cron writes).
2. **[seed]** `scheduler/tick.py`: one-minute tick; durable last-run state on
   the workspace or `corral-state`; run each due job once (launchd-equivalent
   coalescing); overlap guard, timeout, retry per job; host TZ from env;
   wrap via `observability/log_run.py` so `runs.db` and freshness keep working.
3. Supervisor inside the container keeps hub and tick running.
4. Doorbell (host): at login start the runtime and `corral up`; after wake
   send the container a catch-up signal. Runs no jobs.
5. `corral init` installs Seed into an empty workspace; `corral update`
   stages and atomically swaps, keeps the previous image tag and workspace
   snapshot for rollback.
6. mac-host migration: export the current launchd jobs from
   `scheduler/manifest.yml`, classify each as container-runnable or
   host-needing (via `corral-host-run`/hostd), dry-run both backends side by
   side, then unload the launchd units **only at cutover**.
- Proven by: T-SCHED-1..6, T-SEED-1..3.

### WS9 — `install.sh` and the `corral` shim
1. Detect OS; check runtime (Docker Desktop on macOS, Docker Engine on Linux)
   and stop with the install link if missing.
2. macOS prerequisites: Docker Desktop file sharing for each mount, VirtioFS,
   SSH agent forwarding, Rosetta emulation on Apple Silicon, Remote Login.
3. Render compose from template + user config (mounts, ports, workspace,
   parity map, hostname).
4. Install hostd, host shell, doorbell; generate hostd token.
5. Mount sentinels: write on host, read in container, and the reverse; a
   failure names the setting to fix (TCC folders, file sharing).
6. `corral up`, then `corral doctor`.
7. Idempotent: re-running changes nothing that is already right; prints a
   diff of anything it would change.
8. `install.sh --uninstall`: stops the stack, removes units, leaves volumes
   and user folders untouched unless `--purge`.
- Proven by: T-INST-1..5.

### WS10 — CI and publishing (public GHCR)
1. `.github/workflows/image.yml`: build linux/amd64 from the manifest; run
   unit tests and image tests; scan layers for secrets and personal strings;
   push to `ghcr.io/cvp1/corral-light` as **public**.
2. Tags: `vX.Y.Z` (immutable) and `latest`; images signed (cosign keyless)
   and SBOM attached.
3. Lane bumps: scheduled `lanes check` → candidate image → full image tests
   → PR that changes the manifest; **default-model changes need a human
   merge**.
4. The release gate fails if any lane lacks a Linux x64 artifact.
- Proven by: T-IMG-*, T-REL-1..3.

### WS11 — Parity test suite
See section 5. Lives in `tests/parity/`, runs on all three hosts, writes a
JSON report per run to `reviews/parity-runs/<host>-<date>.json`.

---

## 4. Sequence, entry and exit criteria

| Phase | Hosts | Work | Entry | Exit |
|---|---|---|---|---|
| **P0a — image boots** | mac-host, thinkpad-host | WS1, WS2, WS3 (bind, workspace, doctor basics) | This plan approved | T-IMG, T-ARCH, T-ID pass on both; hub reachable and pairs on :8099 (mac-host) / :8098 (thinkpad-host) |
| **P0b — parity on mac-host** | mac-host | WS4, WS5, WS6, WS7, rest of WS3 | P0a exit | The eight v3 Phase 0 checks (section 5.3) pass on **all four lanes**, against a **copy** of `~/ai-os`, beside the live native hub |
| **P0c — parity on Linux** | thinkpad-host, Intel Mac laptop (Omarchy) | Linux variants of WS4/WS5 | P0a exit on thinkpad-host | Same eight checks pass on all four lanes on both Omarchy hosts |
| **P1 — Seed and scheduler** | all three | WS8 | P0b and P0c exit | mac-host's scheduled jobs run from the container for **7 days** at native freshness (`observability/freshness.py`), including at least one sleep/wake and one Docker restart, with no duplicate or missed runs; on the Omarchy hosts `corral init` produces a working Seed workspace |
| **P2 — installer and release** | Intel Mac laptop (Omarchy) first, then thinkpad-host | WS9, WS10 | P1 exit | From a clean user account: the four install lines → `/status` answers; public image tag published with signature and SBOM |
| **P3 — cutover mac-host** | mac-host | Stop native hub, unload launchd plist and native scheduler units, switch container to :8098 and the real workspace | P2 exit + the operator's go | 48 hours of daily use with no fallback to native; rollback rehearsed (below) |

**Rollback (every phase).** Native install stays untouched until P3. P3
rollback: `corral down`, reload the saved launchd plists
(`com.cvp1.corral-light`, watch, Seed jobs), start the native hub. Rehearse
once before cutover.

**Never during P0–P2 on mac-host:** bind the container to :8098, point it at
the real `~/ai-os`, or unload any launchd unit.

---

## 5. Test plan

### 5.1 Test levels

| Level | Where | Runs | Tooling |
|---|---|---|---|
| Unit | `tests/container/`, hostd, host shell, Seed tick | every commit, CI | stdlib `unittest` (repo convention) |
| Image | built image | every CI build | `docker run` scripts |
| Parity (mechanics) | each test host | each phase gate | `tests/parity/run.py --direct` via `docker exec` |
| Parity (per lane) | each test host | each phase gate | `tests/parity/run.py --lanes` drives each lane through `corral-light consult` |
| Lifecycle | mac-host primarily | P1 | scripted sleep/wake + Docker restart |
| Security | each host | each phase gate | `tests/parity/security.py` |
| Native regression | mac-host native checkout | every change to hub code | existing `test_*.py` and `selftest_*.mjs` suites |

**Why two parity modes.** `--direct` proves the plumbing deterministically.
`--lanes` proves each lane's own tool runner actually has that reach: the
harness sends each lane a fixed instruction to run a check script and return
its JSON verbatim, then compares results across lanes. A check passes only if
it passes on **all four lanes**.

### 5.2 Test catalogue

**Image and architecture**
- T-IMG-1 Build from manifest reproduces the same digest twice.
- T-IMG-2 Checksum mismatch in the manifest fails the build.
- T-IMG-3 Every lane binary present, Linux x64, runs `--version`.
- T-IMG-4 Layer scan finds no host paths, usernames, hostnames, tokens.
- T-IMG-5 Image size recorded; regression > 15% flags the PR.
- T-ARCH On mac-host under Rosetta: each lane CLI starts, completes one real
  prompt, and runs one tool call; record wall time vs thinkpad-host. A failure marks
  that lane degraded on Apple Silicon and opens the arm64 build task.

**Identity, paths, overlays, git**
- T-ID-1 `id`, `getent passwd $UID`, `Path.home()`, `~` all agree with the host.
- T-ID-2 Pane write at an absolute path containing a space is owned by the
  host user on the host.
- T-ID-3 `PATH` contains no host binary directory.
- T-ID-4 Mapped interpreter path prints a Linux identity; host file at that
  path byte-identical before and after.
- T-OVL-1 `npm ci` and `pip install` inside the container leave the host's
  `node_modules` / `.venv` byte-identical.
- T-OVL-2 Host-side `npm ci` leaves the container's overlay working.
- T-OVL-3 Symlink out of the mount list is reported by `doctor`, not followed.
- T-GIT-1 `git config user.email` matches the host.
- T-GIT-2 `git ls-remote` to a private SSH remote authenticates via the
  forwarded agent.
- T-GIT-3 HTTPS push using the host credential helper works where the host
  uses one.

**Host reach**
- T-HOST-1 `corral-host-run brew --prefix` (mac-host) / `pacman -V`
  (Omarchy) exits 0 with login PATH.
- T-HOST-2 cwd and an argument with a space arrive intact.
- T-HOST-3 Non-zero exit status propagates.
- T-HOST-4 Ctrl-C cancels the host process (no orphan left).
- T-HOST-5 `launchctl print gui/$(id -u)` succeeds (mac-host).
- T-HOSTD-1 `secret` returns the broker's native outcome for a fixture handle;
  no value appears in the pane transcript, hostd log, or container log.
- T-HOSTD-2 `notify` appears (Notification Center / mako).
- T-HOSTD-3 Clipboard round-trip; payload over the cap is refused.
- T-HOSTD-4 `open` launches the host browser for an `http(s)` URL; a
  `file:` path outside real paths and other schemes are refused.
- T-HOSTD-5 `expose` lease reachable on host `127.0.0.1` and `::1`; expires.
- T-HOSTD-6 `facts` reports the host OS and session state.
- T-HOSTD-7 hostd absent (logged out / unit stopped): every verb fails with a
  clear error, the hub keeps running.
- T-HOSTD-8 Token rotation: old token refused, new token accepted, no restart
  of the hub needed beyond the documented step.

**Logins**
- T-LOGIN-1 Each lane completes its real flow from `corral login <lane>`.
- T-LOGIN-2 Credentials exist only under `corral-home`, mode 700; nothing in
  `docker inspect`, compose, or the hub's environment.
- T-LOGIN-3 Recreate the container; authenticated prompt still works.
- T-LOGIN-4 Token refresh observed after the CLI's refresh interval (or
  forced), still authenticated.

**Ports and network**
- T-PORT-1 Pane dev server on 5173 answers on host `127.0.0.1` and `localhost`.
- T-PORT-2 Unpublished port reachable only after `expose`.
- T-PORT-3 Ollama `/api/tags` answers from a pane via `host.docker.internal`.
- T-PORT-4 `rigctl -m 2 -r host.docker.internal:4532 f` returns the IC-7300
  frequency (mac-host, radio on).
- T-NET-1 Hub reachable only from host loopback; not from the LAN IP.
- T-NET-2 Pairing still requires pair code and cookie.
- T-NET-3 Request from the bridge gateway with a forged Tailscale Serve header
  gets no Serve trust.
- T-NET-4 Seat MCP peer routes answer only container-loopback callers.

**Watching and performance** (budgets set from native measurements in P0)
- T-WATCH-1 Host-side edit visible in pane `git status` within budget.
- T-WATCH-2 Hub content index picks up a host-side new note within one
  reconcile interval.
- T-PERF-1 `git status` and a content search on `~/notes` and the workspace:
  container time ≤ budget × native time (budget agreed after first run).

**Scheduler and Seed**
- T-SCHED-1 Fixture job runs at its scheduled minute, once.
- T-SCHED-2 Sleep across two scheduled times → exactly one catch-up run on
  wake (launchd-equivalent coalescing).
- T-SCHED-3 Docker quit across a scheduled time → doorbell restarts it at
  login/wake → one catch-up run.
- T-SCHED-4 Container recreate does not re-run a job that already ran.
- T-SCHED-5 Overlap guard: a long job is not started twice.
- T-SCHED-6 `runs.db` and `freshness.py` show the same result as launchd did
  for the same jobs.
- T-SEED-1 `corral init` on an empty workspace yields a working Seed.
- T-SEED-2 `corral update` failure leaves the previous workspace and image.
- T-SEED-3 `corral update` success: workspace swapped atomically, jobs reload.

**Security** (lane-neutral; packaging hygiene, not a sandbox claim)
- T-SEC-1 hostd refuses: no token, wrong token, browser `Origin`, oversized
  body, unknown verb.
- T-SEC-2 hostd and hub not reachable from another container on the bridge
  without the token (Linux) / at all (hub).
- T-SEC-3 No secret value in any log after a full parity run (grep logs for
  fixture secret).
- T-SEC-4 Image public scan: no personal data (T-IMG-4) re-run on the
  published tag.

**Install and release**
- T-INST-1 Clean user account → four install lines → `/status` answers.
- T-INST-2 Missing Docker / Remote Login / file sharing → installer stops with
  the named fix.
- T-INST-3 Re-run is a no-op.
- T-INST-4 Uninstall removes units, keeps volumes and folders.
- T-INST-5 Mount sentinel failure names the setting (macOS TCC case).
- T-REL-1 Published tag is public, signed, has an SBOM.
- T-REL-2 Default-model change cannot merge without a human review.
- T-REL-3 Missing Linux x64 artifact fails the release.

### 5.3 Phase 0 gate — the eight v3 checks mapped to tests

| v3 check | Tests |
|---|---|
| 1 Login survives recreate | T-LOGIN-1..3 |
| 2 Same-path files | T-ID-1..2, T-OVL-3 |
| 3 No cross-contamination | T-OVL-1..2, T-ID-4 |
| 4 Host execution | T-HOST-1..5 |
| 5 Session reach | T-HOSTD-1..4 |
| 6 Ports | T-PORT-1..4, T-HOSTD-5 |
| 7 Git and watching | T-GIT-1..2, T-WATCH-1 |
| 8 Lifecycle and trust | T-SCHED-2..3 (P1 on mac-host), T-NET-2..3 |

Each row must pass on **all four lanes** on the host under test. One lane
failing fails the row.

### 5.4 Host × phase matrix

| Test group | mac-host | Intel Mac laptop (Omarchy) | thinkpad-host |
|---|---|---|---|
| T-IMG, T-REL | CI | CI | CI |
| T-ARCH | **P0a** (Rosetta) | n/a (native) | n/a (native) |
| T-ID, T-OVL, T-GIT | P0b | P0c | P0c |
| T-HOST, T-HOSTD | P0b | P0c | P0c |
| T-PORT-4 (rig) | P0b | n/a | n/a |
| T-LOGIN | P0b | P0c | P0c |
| T-NET | P0b | P0c | P0c |
| T-WATCH, T-PERF | P0b (virtiofs: main risk) | P0c | P0c |
| T-SCHED | **P1, 7-day soak** | P1 smoke | P1 smoke |
| T-SEED | P1 (migration) | P1 (fresh init) | P1 (fresh init) |
| T-INST | P2 | **P2 first** | P2 |
| Native regression | every hub change | n/a | n/a |

### 5.5 Reporting

- `tests/parity/run.py` writes `reviews/parity-runs/<host>-<YYYY-MM-DD>.json`:
  per test, per lane, pass/fail, timings, and the evidence string.
- A run summary prints a host × lane grid; a red cell names the test.
- Phase gates are decided from these files, not from memory of a run.

---

## 6. Risks and mitigations

| Risk | Where | Mitigation | Test |
|---|---|---|---|
| A lane binary misbehaves under Rosetta | mac-host | Detect in P0a; add linux/arm64 to the matrix for that build | T-ARCH |
| Demos pass, real Mac work fails (panel's top risk) | mac-host | Gate on real workflows per lane, not prompts | 5.3 |
| virtiofs watch drops and slow trees | mac-host | Poll-reconcile in hub/Seed; heavy watchers via host shell | T-WATCH, T-PERF |
| OAuth flow can't complete in container | all | Out-of-band first, `expose` lease second | T-LOGIN |
| Docker Desktop not running at login | mac-host | Doorbell starts it; hub shows "starting" | T-SCHED-3 |
| Secret Service absent on Omarchy | Omarchy hosts | hostd `secret` reports unavailable cleanly; document provider install | T-HOSTD-1, T-HOSTD-7 |
| sshd off on a host | all | Installer checks and stops with the fix | T-INST-2 |
| Public image leaks personal data | CI | Runtime-only config; layer scan; re-scan published tag | T-IMG-4, T-SEC-4 |
| Scheduler double-runs or misses after recreate | mac-host | Durable last-run state; overlap guard | T-SCHED-4..5 |
| Cutover breaks the daily seat | mac-host | Native untouched until P3; rehearsed rollback | P3 |

---

## 7. Open items to resolve during implementation

1. Hostname of the Intel Mac laptop running Omarchy; exact hostname of thinkpad-host.
2. Docker Engine, sshd and a Secret Service provider on stock Omarchy.
3. Grok CLI Linux x86_64 artifact name and its login flow.
4. Antigravity's Google login flow inside a container.
5. Docker Desktop settings on mac-host (file sharing, VirtioFS, SSH agent,
   Rosetta) — macOS privacy protection blocked reading them from a session;
   read them in the Docker Desktop UI.
6. Which of mac-host's scheduled jobs need host reach (WS8 step 6).
7. Where the secret broker lives on hosts without `~/ai-os` (Seed path).
