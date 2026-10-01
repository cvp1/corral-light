# Corral Light runs in a container, on every host

Status: **v3 — panel-converged design, ready to build Phase 0**, 2026-10-01.
Author: Claude (mac-host session) for the operator, who owns the system and decides.

## History

- **v1** (37b018c) treated the container as a sandbox. Panel (Grok 4.7,
  GPT-6.1 Sol, Gemini 3.8) recommended keeping the Mac native.
- **The operator rejected that** and fixed four constraints: **C1** containers on
  every host including the daily Mac; **C2** no lane-specific constraints;
  **C3** no assumptions about the user's folders; **C4** parity is the design
  rule — no lane may be worse off in the container than natively.
- **v2** (a25da56) redesigned around C1–C4. The same panel, two rounds,
  converged: **buildable as constrained**. v3 folds in its fixes.
  Reviews: `reviews/2026-10-01-container-install-panel-v2/`.

## Design rule

Parity, not policy. The container is packaging. Every pane is trusted as the
user, as it is natively. Isolation that falls out of mounting only some paths
is incidental. Nothing gates on one vendor's mechanism; lane-specific
*protocol handling* (each CLI's login flow) is fine, lane-specific
*restrictions* are not.

## The shape

```
host                                         container  ghcr.io/cvp1/corral-light:<ver>
────                                         ─────────────────────────────────────────
corral (POSIX sh shim) ─ compose ──────────▶ hub.py, Seed, 4 CLIs + adapters (linux builds)
browser ─ 127.0.0.1/::1:8098 ─ publish ────▶ :8098
browser ─ 127.0.0.1/::1:<dev range> ───────▶ pane dev servers
mounts: user's list, same paths, rw ───────▶ default: $CORRAL_WORKSPACE, ~/notes
volumes: corral-state, corral-home, dep/cache overlays
                                             │
host:                                        │
  corral-hostd (login session, loopback) ◀───┤ HTTP + token: secret notify open clipboard expose facts
  sshd + corral-host-shell (login PATH) ◀────┤ ssh host.docker.internal, pinned key, forwarded agent
  corral-doorbell (login agent / user timer) ┤ starts Docker, nudges scheduler catch-up
  Ollama :11434, rigctld :4532 ◀─────────────┘ CORRAL_HOST_PORTS
```

## 1. Mounts

- `CORRAL_MOUNTS` is the user's list. Default: `$CORRAL_WORKSPACE` and
  `~/notes`, read-write, at the **same absolute paths**. No mount is special;
  the product does not know whether a folder is synced or versioned.
- `CORRAL_WORKSPACE` defaults to the workspace Seed reports, not a hardcoded
  `~/aios` (mac-host's is `~/ai-os`).
- `install.sh` creates missing default paths as the user, then writes a
  sentinel into each mount and reads it from the container. A failure names
  the Docker Desktop file-sharing or Full Disk Access setting to fix (macOS
  TCC hides Desktop, Documents, Downloads and external disks otherwise).
- **Platform-dependent outputs never cross the boundary.** Anonymous volumes
  overlay `node_modules`, `.venv`, `__pycache__` and tool caches inside mounted
  projects, so a Linux `npm install` never overwrites the host's Mach-O
  dependencies, and vice versa. `doctor` lists the overlays in force.
- Symlinks resolving outside the mount list are reported by `doctor`, not
  followed.

## 2. Identity and paths

- Same UID/GID as the host user; the entrypoint writes a `passwd` entry whose
  home is the host's `$HOME`, so `getpwuid`, `Path.home()` and `~` agree.
- Container-owned `PATH`: container binaries only. Host binary directories
  are never on it.
- **Interpreter parity map** (data, not code): absolute interpreter paths that
  workspace configs name — today `~/.venvs/aios-seed/bin/python3`, called by
  the workspace's Claude hooks — get a Linux build **overlaid as a
  container-only mount** at that exact path. The host file is never written.
- Host git and SSH config: `~/.gitconfig` and `~/.ssh/known_hosts` mount
  read-only. The container writes its own SSH snippet that sets the Docker
  agent socket, then includes the host's `~/.ssh/config` aliases. A
  `credential.helper=osxkeychain` is replaced with a helper that calls the
  host's over `corral-host-shell`.
- `doctor` **reports, never gates**: absolute executables in any lane's config
  that will not exec, overlays, dangling symlinks, `core.ignorecase` mismatches
  on case-insensitive mounts, and the active watch mode.
- Pinned `hostname:` so tools keyed on it behave as native.

## 3. Host reach

| Need | Mechanism |
|---|---|
| Arbitrary host commands (brew, launchctl, Xcode, native builds, Darwin-only scripts) | **SSH** to `host.docker.internal` running `corral-host-shell`, which loads the login PATH (measured 2026-10-01: a bare SSH session's PATH is `/usr/bin:/bin:/usr/sbin:/sbin`, no Homebrew). Preserves cwd, argv, stdin, PTY, signals, exit status. Host key pinned at install. Launchd GUI-domain queries name `gui/<uid>` explicitly (measured: reachable over SSH). |
| Keychain-backed secret broker | **hostd `secret`** — calls the existing broker unchanged (measured: SSH gets "User interaction is not allowed" from the login Keychain) |
| Notifications, clipboard, opening URLs/files in the host browser/apps | **hostd** `notify`, `clipboard-get/set`, `open` |
| Host facts (OS version, session state) | **hostd** `facts` |
| Pane listeners reachable from the host browser | published `127.0.0.1`/`::1` dev range + on-demand **hostd `expose`** leases |
| Ollama, rig, other host daemons | `CORRAL_HOST_PORTS` (default 11434, 4532) via `host.docker.internal`; rig via hamlib `rigctld` |
| Git over SSH | Docker Desktop's forwarded agent at `/run/host-services/ssh-auth.sock`; Linux mounts `$SSH_AUTH_SOCK` |

`install.sh` checks and reports the prerequisites: Remote Login on, host key
pinned, Docker Desktop's SSH-agent forwarding and VirtioFS file sharing on.

### corral-hostd

- Stdlib Python; LaunchAgent on macOS (runs in the Aqua login session),
  systemd user unit on Linux (session D-Bus for Secret Service).
- Binds host loopback only; on macOS the container reaches it via
  `host.docker.internal`; on Linux a hostd-owned forwarder bridges the docker
  gateway to loopback. Never `0.0.0.0`.
- Verbs: `secret`, `notify`, `open`, `clipboard-get`, `clipboard-set`,
  `expose`, `facts`. No exec, no shell strings, no raw AppleScript. General
  execution is SSH's job, not hostd's.
- `secret` keeps the native broker's contract exactly.
- **Threat model.** Panes are trusted as the user; SSH and shared credentials
  give them account-level reach, as natively. The token exists to keep out
  other containers, other local processes and browsers. 256-bit, stable
  across restarts, rotatable, mode 600 on host and mounted read-only, compared
  in constant time. Bounded typed JSON, no CORS, browser `Origin` rejected.
  `open` and `expose` are confused deputies: argv only, `http(s)` and real
  paths only, exposed ports bound to host loopback, leases expire. Logs record
  verb and secret handle, never values, clipboard bytes or notification
  bodies. At logout hostd is absent and calls fail clearly.

## 4. Logins — same interface, per-lane protocol

- `corral login <lane>` (and the browser UI) for all four lanes. Credentials
  land in that lane's own config dir on `corral-home`, mode 700. No tokens in
  the environment or compose file.
- Flow per lane: an out-of-band flow where the CLI offers one (device code,
  pasted token, copy-paste OAuth code); otherwise an **`expose` lease** that
  binds the CLI's localhost callback port on the host, preserving redirect
  port, path and state, then closes. Never swap in an API key where it would
  change account access or billing.
- Per-lane flows are verified in Phase 0, not assumed. Platform expectations
  from the panel: Claude (`setup-token` or callback), Codex (device auth or
  callback), Antigravity (Google callback, copy-paste where offered), Grok
  (unverified).
- Acceptance includes refresh: recreate the container and the session still
  authenticates.

## 5. Networking and pairing

- The hub binds `0.0.0.0` inside; compose publishes `127.0.0.1` and `::1`
  only.
- Browser pairing is unchanged: pair code and cookie are the gate.
- The hub now sees the bridge gateway as the browser's peer. Tailscale Serve
  trust (`edge.via_serve`) must **not** be granted to the gateway address,
  since every container on the bridge shares it. In container mode, Serve
  runs inside the container, or Serve trust is off. A forged Serve header from
  the gateway gets no Serve trust (Phase 0 test).
- Seat MCP children still dial loopback inside the container; peer routes stay
  container-loopback-only.

## 6. Seed and the scheduler

- Seed ships in the image at a pinned tag. `corral init` installs it into the
  workspace; `corral update` stages and atomically swaps, leaving the old
  workspace and image on failure.
- **One scheduling authority, in the container**: a Seed `container` backend
  reads the same `scheduler/manifest.yml`, keeps durable last-run state,
  overlap, timeout and retry per job, and ticks every minute, running anything
  due once. That matches launchd's documented behaviour: `StartCalendarInterval`
  does not wake the machine, and missed firings during sleep coalesce into one
  run on wake.
- **corral-doorbell**, a host login agent (systemd user timer on Linux), is
  the only host-side scheduling piece: at login it starts Docker Desktop and
  the compose project; after wake it signals the container to run its
  catch-up tick. It runs no jobs itself.
- When Docker is not running, nothing runs; the doorbell starts it, and the
  catch-up tick runs whatever came due. The host time zone is passed in at
  start. Jobs needing host facts get them from hostd or SSH, not `uname`.

## 7. File watching

- Virtiofs event delivery from host-originated writes is not reliable enough to
  trust. The hub and Seed treat events as hints and **reconcile by polling**,
  so a missed event never leaves stale state.
- For CLI tools, the image sets supported polling switches (for example
  `CHOKIDAR_USEPOLLING`); there is no universal one.
- Watch-heavy or performance-sensitive native tools run on the host through
  `corral-host-shell`, where FSEvents work.
- Phase 0 measures `git status`, search and a host-written sentinel against
  native and sets the budget.

## 8. Release manifest

- One manifest pins Python, adapters, the four CLIs per OS/arch, Seed, and
  each lane's default model and effort.
- **A tag fails** if any lane lacks a Linux build for a supported arch (Grok
  linux/arm64 is unverified today). A Mach-O cannot be emulated; an amd64
  build under emulation is a doctor-labelled degraded fallback only.
- CI proposes bumps (`lanes check` → candidate image → selftests and the Phase 0
  parity suite); a human merges default-model changes.

## Install

```
curl -fsSL https://<release-url>/install.sh | sh   # checks runtime + prerequisites, installs hostd + doorbell, writes compose, pulls, starts
corral pair ABC-123
corral login <lane>        # once per lane
corral doctor
```

## Phase 0 exit criteria — every check from a pane on every lane, on mac-host

A chat reply and a green `doctor` are not sufficient. All eight must pass.

1. **Login survives recreate.** Real account login via that lane's flow;
   recreate the container; an authenticated prompt still works; credential
   files only on `corral-home`, mode 700.
2. **Same-path files.** Host and pane read each other's writes at absolute
   paths including a space; pane writes are owned by the host user; a symlink
   leaving the mount list is reported.
3. **No cross-contamination.** `npm install` / `pip install` in the pane leaves
   the host's own dependencies byte-for-byte intact; the mapped interpreter
   prints a Linux identity; the host binary at that path is unchanged.
4. **Host execution.** Via `corral-host-shell`: `brew --prefix`, `sw_vers`,
   `launchctl print gui/<uid>`, an argument with a space, correct cwd and exit
   status, and cancellation.
5. **Session reach.** `secret` returns the broker's native outcome for a
   fixture handle without printing a value; `notify` appears; clipboard round-
   trips; `open` reaches the host browser.
6. **Ports.** A pane dev server on the published range answers on host
   `127.0.0.1` and `localhost`; an `expose` lease works for an unpublished
   port; Ollama and `rigctld` answer.
7. **Git and watching.** Host `user.email` matches; `git ls-remote` over the
   forwarded agent authenticates; a host-side edit appears in pane `git status`
   within the measured budget.
8. **Lifecycle and trust.** A scheduled fixture survives sleep/wake and a Docker
   restart with exactly one catch-up run; a forged Serve header from the
   gateway gets no Serve trust; pairing still requires code and cookie.

## Phases

| Phase | Work | Done when |
|---|---|---|
| 0 | Image, compose, identity/overlays, hostd, `corral-host-shell`, login flows; on mac-host beside native on :8099 against a **copy** of the workspace | The eight checks pass on all four lanes |
| 1 | Seed container backend + doorbell; `install.sh`; multi-arch CI with the parity suite | mac-host's scheduled jobs run from the container for a week, including a sleep and a Docker restart, at native freshness |
| 2 | Cut over mac-host; install linux-host and a fresh Linux host | Native launchd plist retired; the four install lines work on Linux |

## Biggest risk (all three reviewers)

Demos pass while ordinary Mac work fails: the first native tool, file watch or
dev server that the bridge does not carry. That is why Phase 0 runs real
workflows on every lane, not a prompt round-trip.

## Unverified

- Grok CLI Linux arm64 build, and Grok's login flow.
- Antigravity's exact Google login flow in a container.
- Docker on linux-host (its ssh accepts only mesh commands); laptop-host was
  unreachable 2026-10-01.
- Docker Desktop's current settings on mac-host (macOS privacy protection
  blocked reading them).
