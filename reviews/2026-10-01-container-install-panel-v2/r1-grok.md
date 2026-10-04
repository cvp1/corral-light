# v2 Round 1 — Grok 4.7 (summary.json verified below; pane 8018d2cd6eed)

**S1 Mounts — RESHAPE.** A user-owned list, mounted read-write at the same absolute paths, is the right model, but the default `~/aios` repeats the measured workspace mismatch, and a `~` entry covers the login volumes unless those paths are overlaid on purpose.

**S2 Identity and paths — RESHAPE.** Same UID/GID, a generated passwd home, container-first `PATH`, a data-only parity map, and an advisory `doctor` satisfy C2 and C4; the map must be a container-only file mount so a Linux interpreter is never written onto the host binary.

**S3 Host reach — RESHAPE.** Ollama, `rigctld`, a forwarded agent, SSH for general commands, and hostd for Keychain and notifications are the right split; an SSH session on macOS is outside the GUI domain, so it misses Aqua `launchctl`, the login Keychain, and Homebrew's PATH.

**S4 Logins — RESHAPE.** One login per lane into a persistent config dir, with no tokens in the environment, is the right shared mechanism; a browser callback to localhost inside the container never reaches the browser on the Mac.

**S5 Networking and pairing — RESHAPE.** Publishing the hub only on host loopback is sound; treating the bridge gateway as a Tailscale Serve trusted hop marks every neighboring container as that hop, and the host shim's peer-UID check sees an unknown UID.

**S6 Seed and the scheduler — RESHAPE.** Pinning Seed in the image with a container scheduler backend matches C1; the loop matches launchd only if it ticks, catches up after sleep, inherits the host time zone, and starts at login with Docker itself.

**S7 Release manifest — RESHAPE.** One manifest, CI-proposed bumps, and a human merge for default models is the right gate; a missing Linux build for the host arch (Grok arm64 is still unverified) must fail the tag.

**PHASES — RESHAPE.** The sequence is right; Phase 0 beside native on the live workspace will clobber shared `node_modules` and virtualenvs, and Phase 1's `launchctl list` check can pass while GUI-session reach is still broken.

## Parity gaps

Platform knowledge is marked. Claims the plan says it measured in code are unverifiable from the text.

1. **All lanes — pane localhost is a different host.** Docker Desktop on macOS has no host network. A dev server or OAuth listener the pane binds is invisible to the Mac browser. **Fix:** publish a user-owned localhost port range, plus one structured `expose` verb (SSH `-R` or hostd) for ports outside it. Use that same verb for login callbacks.

2. **All lanes — virtiofs drops watches and slows trees.** inotify does not cross Docker Desktop virtiofs, so watchers in every CLI and in Seed miss host-side writes; `git status` and search on a large notes tree run far slower than native. Userspace cannot inject inotify into unmodified CLIs. **Fix:** the hub and Seed poll, or consume an FSEvents feed from hostd; set the usual polling variables; put a Phase 0 time budget on `git status` and on a host-written sentinel. A FUSE mirror is the later way to give CLIs real inotify.

3. **All lanes — SSH back lands outside the GUI session.** `ssh host.docker.internal launchctl list` is the SSH domain. Non-interactive sshd's PATH omits `/opt/homebrew/bin`. Remote Login, the firewall, and a pinned host key are unspoken prerequisites, and the VM address changes. **Fix:** a host `corral-host-shell` that supplies the login PATH; GUI-domain queries stay structured hostd verbs. Pin the host key at install. Keep a general shell off hostd.

4. **Claude, Codex, Gemini; Grok unverified — login and keyring.** Localhost OAuth fails as in gap 1. Device codes and pasted tokens work. The Linux CLI cannot read the macOS Keychain (this matches the plan's measurement and platform behavior). libsecret and `osxkeychain` are absent. **Fix:** `corral login` prefers device code or a pasted token for every lane, otherwise the one-shot `expose` proxy. Store files on `corral-home` mode 700.

5. **All lanes — git and SSH config.** A synthesized HOME omits `~/.gitconfig`, `~/.ssh/config`, and `known_hosts`. A mounted host SSH config can point `IdentityAgent` at a Mac socket and override Docker Desktop's `/run/host-services/ssh-auth.sock`. `credential.helper=osxkeychain` fails in Linux. Host SSH agent forwarding is a Desktop setting install must check. **Fix:** mount gitconfig and known_hosts read-only; write a container SSH snippet that sets the Docker agent socket and then includes host aliases; replace the credential helper with a small helper that calls the host helper over SSH.

6. **All lanes — shared build outputs.** Linux `node_modules` and `.venv` on the APFS mount break native tools, and the reverse. SQLite plus virtiofs locking is a known corruption risk (platform knowledge). **Fix:** anonymous volumes over dependency and cache dirs only. Phase 0 uses a separate workspace copy until cutover.

7. **All lanes — process and service namespace.** `ps`, `lsof`, and `localhost` other than proxied ports are the container. Ollama is special-cased; other host daemons are not. **Fix:** one preamble injected into every lane, plus a user-owned `CORRAL_HOST_PORTS` list defaulting to 11434 and 4532.

8. **All lanes — clipboard and `open`.** `pbcopy` and `open` are daily Mac agent tools and are absent. **Fix:** hostd verbs `clipboard-get`, `clipboard-set` (size cap), and `open` limited to files under the mount list and to `http(s)`.

9. **All lanes — TCC and file sharing.** Desktop, Documents, Downloads, external disks, and `/opt/homebrew` stay empty until Docker has the share and Full Disk Access. **Fix:** `install.sh` reads a sentinel in each configured mount and stops with the setting that failed. Leave `~` off the default list.

10. **Any lane with no Linux build for that arch.** If Grok has no linux/arm64 binary, that pane cannot start on the Mac host. qemu-user can run a linux/amd64 build only as a doctor-labeled degraded fallback. A Mach-O binary cannot be emulated. **Fix:** the manifest refuses the tag when any lane lacks a Linux build for the arch.

## Open questions

**1.** hostd is the right shape. Nothing lighter reaches the login Keychain from the Linux VM. Mounting `~/Library/Keychains` gives the container a database it cannot speak. `security` over SSH returns errSecInteractionNotAllowed. A host Unix socket does not survive virtiofs. A LaunchAgent, or a systemd user unit with session D-Bus, is the minimum.

**2.** Same-path mounts under `/Users/<name>` work when `/Users` is shared. What breaks real work: no inotify; slower git and search; APFS case-folding unless the repo already has `core.ignorecase`; symlinks into `/opt/homebrew`; uid quirks; SQLite locking; native addons; TCC folders; resource forks that do not round-trip. Install should require VirtioFS and the host SSH agent.

**3.** See gap 4. Expect Claude (`setup-token` or localhost OAuth), Codex (ChatGPT OAuth or device code), and Gemini/Antigravity (Google localhost OAuth, with a copy-paste fallback) to need the proxy or a pasted token. Grok's flow is unverifiable from the text. All four are a Phase 0 exit condition.

**4.** A loop that sleeps until the next timestamp wakes late after lid-close: the Docker VM freezes and `sleep` is relative. A one-minute tick that runs anything now due is closer to systemd `Persistent=true`, and better than launchd calendar jobs, which skip intervals missed during sleep. State that policy. Pass the host time zone in at start. Jobs that branch on `uname` or `sw_vers` take the Linux branch; host facts come from hostd or SSH. Workspace swaps must be an atomic rename on the mount. The larger gap is that launchd runs when Docker is quit. Under C1, a login LaunchAgent starts Docker and the compose project, then runs a catch-up tick. Phase 2's week includes one sleep and one Docker restart.

**5.** Also different from native: LAN IP and mDNS (pinned hostname, bridge IP, Linux `uname`); `sudo` and TCC prompts; a visible browser; `docker`, which should be reached over SSH so the daemon socket stays unmounted; the `staff` group and macOS ACLs; FileVault logout, where the LaunchAgent hostd is absent. A lane-neutral preamble plus hostd `facts` covers identity.

## corral-hostd

`secret` and `notify` on hostd, and general commands on SSH, is the right split. Add only structured verbs parity needs: `clipboard-get`, `clipboard-set`, `open`, `expose`, `facts`. No exec verb and no raw AppleScript.

Threat model the design should state:

- The container is a packaging boundary. Every pane is as trusted as the user. C4 means isolation is incidental.
- Docker Desktop lets every container dial host loopback. On Linux every container on the bridge can dial the gateway. The token is the only gate. The LAN is not a client.
- `open` and `expose` are confused-deputy risks. Confine paths to the mount allowlist and bind exposed ports to localhost.
- The SSH channel is a full user shell, including `docker` when the user can run it.
- At logout and at the loginwindow, hostd is down. Calls fail closed.
- Logs record the verb and the secret handle, and omit values, clipboard bytes, and notification bodies.

Smallest design: stdlib HTTP on `127.0.0.1` (Mac) or the current bridge gateway (Linux), rebound when that address changes. A 256-bit token in two mode-600 files, bearer header, constant-time compare, failure lockout. JSON schemas, timeouts, length caps. `secret` is a fixed argv to the existing broker and returns the outcome only. `notify` passes the body as `on run argv` data or calls a notification API directly. User privileges, no mTLS.

## Tests for Phases 0–1

Each test runs in a pane on every lane. One lane failing fails the phase.

1. **Login and prompt.** Finish `corral login` with that lane's real flow and receive one real model reply. Credentials exist only on `corral-home`, mode 700.
2. **Same-path ownership.** The pane writes a sentinel at an absolute workspace path. The host reads those bytes as the host user. A host edit is what the pane reads next.
3. **Interpreter map.** A shared fixture names an absolute host interpreter. Inside the container that path executes and prints a Linux identity. `doctor` reports no absolute executable that fails `exec`. The host binary is unchanged.
4. **GUI reach.** `corral-host secret` returns an outcome for a fixture handle and prints no secret. A structured notify appears in the GUI session. SSH sees `/opt/homebrew/bin` and the Aqua launchd domain. `ssh-add -l` works through `/run/host-services/ssh-auth.sock`.
5. **Ports.** The pane listens on a published dev port. From the Mac, `curl 127.0.0.1` and `curl localhost` both succeed. The pane reads Ollama and, when the radio is on, rig frequency via `host.docker.internal`.
6. **Git.** `git config user.email` matches the host, `git status` shows a host-written sentinel within the Phase 0 time budget, and `git ls-remote` to an SSH remote authenticates with the forwarded agent.

## What would make this fail

On the daily Mac the wall is down whenever Docker Desktop is not already running, and while it is running virtiofs drops file-watch events and the pane's localhost servers and OAuth callbacks never reach the browser, so a Phase 1 `launchctl list` over SSH can look green while the GUI session is still unreachable.
