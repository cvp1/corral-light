You are one of three independent reviewers on a design panel. The other two are different models from different vendors; you will see their reviews in a second round. The author is a Claude agent working for the operator, who owns this system and makes the final call.

WHAT YOU ARE REVIEWING
The plan below "=== PLAN ===" moves Corral Light — a browser "wall" of AI coding-agent panes, one stdlib-Python hub per machine driving four agent CLIs (Claude Code, Codex, Grok, Antigravity/Gemini) over ACP, plus native Ollama — into one container image on every host, including the operator's daily Mac.

An earlier version was reviewed and the panel recommended keeping the Mac native. The owner rejected that. These are FIXED CONSTRAINTS. Do not argue against them; solve within them:
  C1. Containers on every host, including the daily Mac. Solve host reach; do not route around it.
  C2. No lane-specific constraints. Corral runs four CLIs. A rule or gate that only binds one vendor (for example Claude Code's PreToolUse hooks) cannot gate the build.
  C3. No assumptions about the user's folders. Whether a mounted folder is synced, backed up or versioned is the user's choice, not a design input.
  C4. The design rule is PARITY: inside the container each lane must be able to do what it does natively, and the container must not make any lane worse. Isolation is incidental, not promised.

Everything you need is in the paste. Do not open files or run commands. If a claim looks wrong but you cannot check it from the text, say "unverifiable from the text" — but you MAY draw on your own platform knowledge (Docker Desktop, macOS, Linux, each CLI) and should say when you do.

YOUR JOB — solve, then attack
1. VERDICT per section, BUILD / RESHAPE / KILL, one line each with the strongest reason: S1 mounts, S2 identity and paths, S3 host reach, S4 logins, S5 networking and pairing, S6 Seed and the in-container scheduler, S7 release manifest, and the PHASES.
2. PARITY GAPS, ranked most severe first, at most 10: something a pane on some lane can do natively that it cannot do in this design (or does worse). For each: which lane(s), the concrete failure, and a FIX that satisfies C1–C4. A gap without a fix is half an answer.
3. ANSWER THE PLAN'S OPEN QUESTIONS 1–5 directly, using platform knowledge where needed.
4. corral-hostd: is the verb set right (secret, notify; general commands over SSH)? What does its threat model need to state, and what is the smallest secure design?
5. TESTS: up to 6 acceptance tests for Phases 0–1 that would fail if parity is broken on any lane.
6. WHAT WOULD MAKE THIS FAIL in practice on the daily Mac, in one sentence.

FORMAT: Markdown, under 1,300 words. Start with the eight verdict lines. No preamble.

=== PLAN ===
# Proposal — Corral Light runs in a container, on every host

Status: **v2 FOR PANEL REVIEW**, 2026-10-01. Nothing built.
Author: Claude (the Mac host session) for the operator, who owns the system and decides.

## What changed from v1, and why

v1 was reviewed by a three-model panel (see `reviews/2026-10-01-container-install-panel/`).
The panel converged on keeping the Mac host native. The operator rejected that framing:

1. **Containers are the goal, on every host including the Mac host.** The job is to
   solve host reach, not route around it.
2. **No lane-specific constraints.** Corral drives four agent CLIs. A design
   rule that only binds Claude Code (its PreToolUse hooks) cannot gate the
   build, because it protects nothing for the other three.
3. **No assumptions about the user's folders.** v1 assumed `~/notes` is synced
   and designed around that. Whether a user syncs a folder is their choice.
   The product mounts what it is told to mount.

So v2 drops the "container as sandbox" framing. The design rule is
**parity**: inside the container, every lane must be able to do what it can do
natively, and the container adds no constraint that applies to one lane only.
Isolation the container gives for free (only mounted paths exist) is a bonus,
not a promise.

## TL;DR

- **One multi-arch image** (linux/amd64, linux/arm64): pinned Python, Node,
  both npm adapters, the four agent CLIs, and Seed, from one release manifest.
- **Mounts are a list the user owns.** Default: the workspace and `~/notes`,
  read-write, at the same absolute paths. Add any path. No sync assumptions.
- **Host reach through two channels**, both lane-neutral:
  - **SSH to the host** for general host commands (launchctl, brew, git with
    host keys), using the user's own forwarded ssh-agent.
  - **`corral-hostd`**, a small companion running in the user's login session,
    for what SSH provably cannot reach: the Keychain-backed secret broker and
    desktop notifications. Plus the rig via hamlib's own network daemon.
- **Logins**: every lane logs in once into its own config dir on a persistent
  volume. Same mechanism for all four. No tokens in the environment.
- **Path parity**: absolute interpreter paths that any lane's config refers to
  resolve to Linux builds inside the container. `doctor` reports any that do
  not. Nothing gates the hub on one vendor's hooks.

## What is messy today (measured 2026-10-01)

| Pain | Evidence |
|---|---|
| Python drift | No pin. Hub floor 3.9; the Mac host runs brew 3.14; Seed scripts need 3.10+. |
| Two service formats | launchd plist + systemd unit + watch service/timer. |
| Hidden setup step | `spike/node_modules` gitignored; `doctor.py` exists to explain the missing `npm install`. |
| Per-platform binaries | Adapters ship `darwin-arm64` builds; Grok is `grok-1.0.46-macos-aarch64`; Antigravity is a per-platform build. |
| Two repos, two clones | Seed and Corral Light installed separately, not pinned to each other. |
| Workspace path mismatch | Code assumes `~/aios`; the Mac host's workspace is `~/ai-os`. |
| Lane defaults lag | Grok defaults to 4.6 (4.7 exists), Codex to effort `low`, Gemini to 3.7 (3.8 exists). |

## The shape

```
host                                       container  ghcr.io/cvp1/corral-light:<ver>
────                                       ─────────────────────────────────────────
corral (POSIX sh shim) ─ compose ────────▶ hub.py (Python 3.12, base pinned by digest)
                                           adapters + 4 CLIs (linux builds), Seed
browser ─ 127.0.0.1:8098 ─ publish ──────▶ :8098
mounts (user's list, same paths, rw) ────▶ default: $CORRAL_WORKSPACE, ~/notes
volume corral-state ─────────────────────▶ ~/.local/share/corral-light
volume corral-home ──────────────────────▶ each lane's login/config dir
                                           │
host reach:                                │
  sshd (user's own) ◀── ssh host.docker.internal, forwarded agent
  corral-hostd (login session) ◀── HTTP, per-install token  [secret, notify]
  rigctld (hamlib) ◀── TCP 4532                              [IC-7300]
  Ollama ◀── TCP 11434
```

## 1. Mounts — the user's list

- `CORRAL_MOUNTS` is a list of host paths. Default: `$CORRAL_WORKSPACE`
  (default `~/aios`) and `~/notes`. Read-write unless the entry says `:ro`.
- Each path mounts at the **same absolute path** so transcripts, attachments
  and agent output read identically inside and out.
- `install.sh` creates a default path as the user if it is missing (Docker on
  Linux would otherwise create a root-owned directory).
- No mount is special-cased. The product does not know or care whether a
  folder is synced, backed up or a git repo.
- `home` is just a list entry (`~`), with the platform-binary rule below.

## 2. Identity and paths inside the container

- **Same UID/GID as the host user**, and an entrypoint-generated `passwd`
  entry whose home is the host's `$HOME` (e.g. `/Users/alice`), so `getpwuid`,
  `Path.home()` and `~` agree for every lane.
- **Container-owned `PATH`**: container binaries first; host binary
  directories are never on `PATH`, so no lane can exec a Mach-O by accident.
- **Path parity map**: some workspace configs name interpreters by absolute
  path (today: the workspace's Claude hooks call
  `~/.venvs/aios-seed/bin/python3`, a Mac binary). The entrypoint mounts or
  links a Linux build at each mapped path. The map is data, not code, and
  covers any lane's config, not one vendor's.
- **`doctor` reports, it does not gate.** It scans each lane's known config
  locations for absolute executable paths and lists any that will not exec in
  the container. The hub starts either way; the finding shows in the UI next
  to the lane it affects. Whether a workspace's hooks enforce anything is the
  workspace's business, as it is natively.
- **Pinned hostname**: compose sets `hostname:` to the host's name so tools
  that key on `hostname` behave as they do natively.

## 3. Host reach — solving it, per capability

| Capability | Native today | In the container | Mechanism |
|---|---|---|---|
| Ollama | localhost:11434 | host.docker.internal:11434 | existing `CORRAL_OLLAMA_URL` |
| Git/SSH with host keys | ssh-agent | same agent | Docker Desktop forwards the macOS agent at `/run/host-services/ssh-auth.sock`; Linux mounts `$SSH_AUTH_SOCK` |
| General host commands (launchctl, brew, scheduler) | run directly | `ssh host.docker.internal <cmd>` | user's own sshd and key; parity with native |
| Secret broker (Keychain) | `bin/secret` | `corral-host secret ...` | **corral-hostd**. Measured 2026-10-01: an SSH session cannot use the login Keychain ("User interaction is not allowed"), so SSH alone does not solve this |
| Desktop notifications | osascript | `corral-host notify ...` | corral-hostd |
| IC-7300 rig | rigctl on USB serial | `rigctl -m 2 -r host.docker.internal:4532` | hamlib's own `rigctld` on the host; no USB passthrough needed |

**corral-hostd** is the only new host component:

- A small stdlib Python service installed as a LaunchAgent (macOS, so it runs
  in the GUI login session and can use the Keychain) or a systemd user unit
  (Linux, with the session D-Bus for Secret Service).
- Listens on host loopback. Docker Desktop's `host.docker.internal` reaches
  it; on Linux it binds the docker bridge gateway address instead.
- A fixed verb set — `secret` (calls the existing broker, returns outcomes
  only, never values) and `notify`. No general exec verb: general commands go
  over SSH, where the user's own sshd and keys already govern them.
- Authenticated by a per-install token stored in the `corral-state` volume and
  the host's config dir, mode 600.
- A `corral-host` CLI inside the image, on `PATH` for every lane equally.

## 4. Logins — one mechanism for all four lanes

- Each lane logs in once per host, from the browser UI or `corral login <lane>`.
  Credentials land in that lane's own config directory on the `corral-home`
  volume (`~/.claude`, `~/.codex`, `~/.gemini`, `~/.grok` inside the container).
- On a Mac host, Claude Code's native credentials sit in the Keychain, which
  the Linux CLI cannot read, so Claude needs its one login like the others.
- No token goes in the environment or the compose file.
- Panes run as the same user, as they do natively. The container does not
  claim per-lane credential isolation; it is no worse than native.
- Where a lane's login wants a browser callback, the flow must work through the
  published port or a device code. Each lane is tested.

## 5. Networking and pairing

- Inside the container the hub binds `0.0.0.0`; compose publishes
  `127.0.0.1:8098`, so it is reachable only from the host.
- **Peer addresses change.** The hub sees the bridge gateway, not 127.0.0.1.
  Measured in code: browser pairing still works (the pair code and cookie are
  the gate), and seat MCP children still dial loopback inside the container.
  Two things break and are fixed lane-neutrally:
  - Tailscale Serve detection (`edge.via_serve`) trusts only loopback peers;
    in container mode the published gateway address must be the configured
    trusted proxy hop, or Serve runs inside the container.
  - `edge.local_peer_uid` reads `/proc/net/tcp` for same-user checks; across the
    gateway that is unknown, so container mode keeps peer routes loopback-only
    inside the container (they already are).
- Publish both `127.0.0.1` and `::1` so `localhost` works either way.

## 6. Seed — bundled, and its scheduler

- The image carries Seed at a pinned tag. `corral init` installs it into the
  workspace; `corral update` pulls the new image and runs Seed's update. No
  second clone.
- **Scheduler runs in the container** with a container backend (a supervised
  cron loop reading the same `scheduler/manifest.yml`) instead of
  launchd/systemd. Jobs that need the host reach it the same way agents do
  (SSH, corral-hostd, rigctld, Ollama). This is a Seed change: a third
  `sync.sh` backend.
- Workspace updates are staged and swapped; a failed update leaves the old
  workspace and image in place.

## 7. Release manifest and lane defaults

- One manifest pins Python, adapters, CLIs per arch, Seed, and each lane's
  default model and effort. CI proposes bumps (`lanes check` → candidate image
  → selftests + one real prompt per lane); a human merges the default-model
  change. No silent model moves.

## Install, as a user sees it

```
curl -fsSL https://<release-url>/install.sh | sh   # runtime check, compose file, corral-hostd, pull, start
corral pair ABC-123
corral login claude      # and each other lane, once
corral doctor
```

Prerequisite: a container runtime (Docker Desktop on macOS). `install.sh`
checks for it and stops with the link if missing.

## Phases

| Phase | Work | Done when |
|---|---|---|
| 0 | Image + compose + identity/PATH/parity map; run beside native on :8099 on the Mac host | Every lane logs in and answers one real prompt in the container; `doctor` lists the same lanes ok as native |
| 1 | corral-hostd + SSH reach + rigctld | From a pane on each lane: `corral-host secret` succeeds, a notification appears, `ssh host.docker.internal launchctl list` works, `rigctl` reads the rig frequency |
| 2 | Seed bundled, container scheduler backend, `install.sh`, multi-arch CI | The Mac host's twelve scheduled jobs run from the container for a week with the same freshness as launchd |
| 3 | Cut over | The Mac host retires the native launchd plist; the Linux host and a fresh Linux host install with the four lines |

## Open questions for the panel

1. Is corral-hostd the right shape for session-bound host capabilities, or is
   there a lighter mechanism that reaches the Keychain from a container?
2. Docker Desktop on macOS: same-path bind mounts under `/Users/<name>`,
   case-insensitive APFS through virtiofs, and file-watch behaviour. What
   breaks for agents doing real work?
3. Does any lane's login flow fail inside a container (browser callbacks,
   device codes, OS keyring expectations)?
4. Scheduler in the container: anything that makes a supervised cron loop
   worse than launchd/systemd for these jobs (sleep/wake, missed runs)?
5. What else would a native pane do that a container pane cannot, on any lane?

## Unverified

- Grok CLI Linux arm64 build [NEEDS VERIFICATION].
- Docker on the Linux host [NEEDS VERIFICATION]; the laptop unreachable 2026-10-01.
- Each lane's login flow inside a container.
