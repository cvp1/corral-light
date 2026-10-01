# Round 1 — Gemini 3.8 Flash High (gemini lane, model echoed: gemini-3.8-flash-high, pane 80a92e056d03)

**P1: Container image itself** — **RESHAPE**: Packaging agent runtimes in a Linux VM strips the primary Mac workstation of OS integrations (Keychain, USB serial, osascript, notifications) while keeping a native "dev mode" indefinitely.
**P2: Mount scopes** — **KILL**: Mounting Syncthing-replicated `~/notes` read-write by default turns a compromised or prompt-injected agent into a fleet-wide write vector.
**P3: Hook fail-open fix** — **RESHAPE**: "Execute once with a no-op payload" checks only interpreter existence, not runtime evaluation or hook logic, allowing broken guards to pass healthchecks.
**P4: Seed bundled in image** — **BUILD**: Pinning Seed to Corral Light eliminates repo drift and unifies workspace updates under a single tagged artifact.
**P5: Login/credential handling** — **KILL**: Injecting `CLAUDE_CODE_OAUTH_TOKEN` into container environment variables exposes it to `docker inspect`, `/proc/$PID/environ`, and all co-located agent panes.
**P6: CI-driven lane updates** — **RESHAPE**: Silently pinning lane defaults to the newest model at build time creates unprompted parameter and cost drift without user opt-in.
**P7: Phase plan** — **BUILD**: Parallel testing on `:8099` with concrete falsification criteria before cutover is sound.

---

### Findings

#### 1. Global Credential Leak via Container Environment
- **Claim:** `"The container gets CLAUDE_CODE_OAUTH_TOKEN injected at start"`
- **Defect:** Environment variables inside a container are globally visible via `docker inspect`, world-readable in `/proc/*/environ` to the container user, and inherited by default by every spawned sub-process (Grok, Codex, Antigravity, shell hooks).
- **Concrete Failure:** Any lane or tool execution can dump environment variables and exfiltrate the master Claude OAuth token.
- **Fix:** Pass tokens via an in-memory secret mount (`tmpfs` volume or Docker secret mounted at a mode `0400` file path) scoped strictly to the Claude CLI process.

#### 2. Fleet-Wide Data Corruption via Syncthing Blast Radius
- **Claim:** `"Agents see ~/aios and ~/notes by default (the aios scope) ... ~/notes is Syncthing-synced"`
- **Defect:** `~/notes` is mounted read-write by default in the baseline sandbox. Any untrusted code or prompt-injected agent can rewrite, corrupt, or truncate the "second brain."
- **Concrete Failure:** Because Syncthing propagates changes fleet-wide, an errant write on one node immediately destroys notes across the entire personal fleet.
- **Fix:** Mount `~/notes` as read-only (`:ro`) by default. Require explicit opt-in (`CORRAL_NOTES_RW=true`) or route modifications through an audited note-taking API.

#### 3. No-Op Hook Check Passes Broken Semantic Guards
- **Claim:** `"executes every configured hook once with a no-op payload and refuses to start the hub if any hook cannot execute"`
- **Defect:** Executing a hook with a dummy/no-op payload only verifies that exit code 0 or an execution handle is returned; it does not test if the hook actually parses inputs, queries the memory mesh, or blocks invalid operations.
- **Concrete Failure:** If a hook has a syntax bug or missing runtime dependency on a non-empty payload, the no-op check passes at boot, the hook crashes during actual agent tool use, and Claude's PreToolUse fails open.
- **Fix:** Implement a positive blocking test: execute each guard against an *explicitly prohibited* test payload and assert that the hook exits non-zero and successfully denies the action.

#### 4. Linux Userspace Path & User Lookup Failure with Mac `$HOME`
- **Claim:** `"Same absolute paths inside and out ... container's $HOME equals the host's"`
- **Defect:** On macOS, `$HOME` is `/Users/<name>`. Linux standard tools, glibc lookups (`getpwuid`), PAM, and various toolchains expect `/home/<name>` or fall back to `/etc/passwd`. Running as arbitrary host `${UID}:${GID}` without an explicit `/etc/passwd` entry matching `/Users/<name>` leaves user identity indeterminate.
- **Concrete Failure:** CLIs relying on `os.path.expanduser("~")`, `$USER`, or NSS user queries fail or resolve to `/` or `/nonexistent`, scattering configuration outside mounted volumes.
- **Fix:** Generate a synthetic container `/etc/passwd` at entrypoint mapping `${UID}` to host `$HOME`, or standardize container-internal paths and translate symlinks at the mount boundary.

#### 5. Phantom Pairing Success across Docker Bridge / VM Boundary
- **Claim:** `"Publish 127.0.0.1:8098:8098. The pairing gate and WebAuthn origin stay exactly as today."`
- **Defect:** The host reaches the hub over `127.0.0.1`, but from the hub’s internal perspective inside Docker Desktop's VM, incoming traffic arrives from the Docker bridge gateway (`172.x.x.x`), not `127.0.0.1`.
- **Concrete Failure:** If Corral Light enforces remote/local pairing restrictions or origin validation checks on client IP addresses (`request.remote_addr == '127.0.0.1'`), access will be denied or misclassified as external.
- **Fix:** Configure the hub's reverse-proxy/forwarded-header trust to account for container gateway subnets, or verify that binding relies strictly on the HTTP `Host` header.

#### 6. Silent Behavioral & Cost Drift via Build-Time Lane Updates
- **Claim:** `"Lane defaults follow the newest model ... pinned ... to the newest available at build time"`
- **Defect:** Upgrading an image tag automatically bumps default models, context windows, and effort levels across lanes without explicit operator review.
- **Concrete Failure:** Routine security patches or container updates trigger unvetted model behavior shifts, token rate-limit exhaustions, or unanticipated API bills.
- **Fix:** Treat model bumps as explicit schema configuration changes decoupled from runtime image updates. Default models should be pinned in host configuration, not baked into container defaults.

#### 7. Shadowed Volumes Conceal Toolchain Execution Discrepancies
- **Claim:** `"home scope: shadow the Mac binaries ... container-only volumes mounted over these paths"`
- **Defect:** Shadowing individual paths like `~/.local/bin` and `~/.venvs/*` with empty/named container volumes leaves other host binaries un-shadowed. If an agent executes scripts in `~/bin`, `~/go/bin`, or `~/.cargo/bin`, it attempts to run host Mach-O binaries inside Linux.
- **Concrete Failure:** Scripts work in native dev mode but produce confusing `exec format error` failures in `home` scope whenever auxiliary binaries are invoked.
- **Fix:** Do not attempt fine-grained selective shadowing of binary directories. Provide an explicit container `$PATH` containing only container-provided binaries, and disallow executing host binaries.

---

### Attacks

#### Container vs. Non-Container
Steelman non-container: Corral Light is a hub driving host-interactive developer CLIs. A container introduces a hypervisor VM on macOS, breaks Keychain access, cuts off serial devices, breaks desktop notifications, complicates file permissions, and forces maintaining a native "dev mode" anyway. A non-container bundle—pinned via `uv`, leveraging portable Node binaries or `pnpm`, and managed by a unified `install.sh`—eliminates Python drift and the missing `npm install` pain point while delivering 80% of container consistency without sacrificing native host reach.

#### Hook Fail-Open Vulnerability
The three-part fix is incomplete. Running a no-op payload only tests that the binary runs, not that it blocks. Furthermore, if `.claude/settings.json` points to an interpreter that disappears or changes post-launch, the PreToolUse hook silently fails open during operation. The hub must configure Claude CLI with strict hook error handling or enforce memory-mesh validation at the hub ACP proxy layer rather than relying on client hooks.

#### Same-Path Bind Mounts on macOS
Setting `$HOME=/Users/<name>` inside a Linux container breaks software expecting the standard Filesystem Hierarchy Standard (FHS). Without a valid `/etc/passwd` record matching that UID to `/Users/<name>`, tools such as `ssh`, `git`, and `coreutils` behave unpredictably.

#### Syncthing Blast Radius on `~/notes`
`~/notes` mounted read-write is a high-risk blast radius. Agents equipped with tool-use can delete, overwrite, or maliciously alter files. Syncthing will instantly replicate this damage across the entire home fleet, defeating offline backups if file versioning is not configured.

#### Credential Exposure via Environment
`CLAUDE_CODE_OAUTH_TOKEN` in the container environment is completely unsegmented. Any subprocess or co-tenanted agent pane can read `/proc/*/environ`. File-based secrets with strict filesystem permissions (`0400`) within a `tmpfs` volume provide significantly superior isolation.

#### Pairing & WebAuthn Boundaries
On macOS Docker Desktop, connections to `127.0.0.1:8098` traverse a userland proxy. The socket peer address inside the container is a bridge IP (`172.17.0.1`). If the pairing logic checks peer IP to permit loopback pairing without authentication, it will fail. WebAuthn relies on origin (`http://localhost:8098`), which works only if the browser accesses `localhost`, not a bridged IP.

#### Lane Defaults Follow Newest Model
This is silent behavior drift. Automated image builds changing underlying models break deterministic evaluations and can lead to unexpected cost spikes.

---

### Missing Tests

1. **Negative Hook-Blocking Test:** Feed an illegal memory write payload to the PreToolUse hook inside the container and verify that the operation is actively blocked (non-zero exit).
2. **IP/Origin Peer Test:** Connect to the published container port from the host browser and verify that loopback-only pairing policies correctly identify the client.
3. **Environment Leakage Test:** Run a non-Claude lane (e.g., Grok) and assert that it cannot read `CLAUDE_CODE_OAUTH_TOKEN` from its process environment.
4. **Mach-O Execution Trap:** Place a mock Mach-O binary in an unshadowed directory in `home` scope and verify that the hub logs a clear diagnostic rather than a raw, unhandled kernel failure.
5. **Syncthing Read-Only Enforcement Test:** Verify that writes to `~/notes` in default `aios` scope fail with `EPERM` / read-only filesystem error.

---

### What Would Change My Mind

On **P2 (Mount scopes)**: I would approve the mount scopes if `~/notes` is mounted read-only by default with Syncthing conflict versioning strictly verified, and writes are restricted to an explicit, audited workspace subfolder.
