# v2 Round 2 — sol61 (model echoed: gpt-6.1-sol, effort: high, pane 8a560ee63e6b)

**Cross-examination**

Platform statements below use my platform knowledge; exact CLI versions and measured implementation behavior remain unverifiable from the text.

- **Grok:** strongest agreement: separate platform-dependent build outputs and preserve host Git/SSH configuration. Wrong: launchd calendar jobs do not simply skip sleep intervals; `StartCalendarInterval` delivers a coalesced firing after wake. “No inotify” is also too absolute: virtiofs can propagate events, but completeness and timing need workload tests.
- **Gemini:** strongest agreement: callback forwarding must precede login acceptance, and host lifecycle supervision matters. Wrong design: replacing unrestricted host SSH with a finite action whitelist violates C4. SSH supports PTYs and pinned keys; implementation friction does not justify removing native capabilities. Keychain access is also not universally prohibited over SSH—it depends on item authorization and interaction requirements.
- **My verdicts:** change S7 from **BUILD** to **RESHAPE**: supported executable artifacts and representative tool workflows must gate releases. Retain the other seven verdicts.

**Settled recommendations**

**a. Scheduler.** Use a durable in-container scheduler plus a thin host login agent that starts/reconciles the runtime and requests reconciliation after wake; keep one scheduling authority. `StartCalendarInterval` fires after wake, coalescing multiple missed firings, and does not itself wake the Mac. When Docker is unavailable, record pending work and resume when it starts; match each existing job’s catch-up policy explicitly. Continuous native-equivalent availability requires the runtime to remain available—host timers cannot execute container jobs without it.

**b. General commands.** Keep SSH for arbitrary user-authorized host execution, with pinned host keys and a host shell wrapper that establishes the intended PATH, cwd, arguments, environment, PTY and exit status. Query explicit launchd domains rather than treating `launchctl list` as proof of GUI access. Hostd handles capabilities whose required session context SSH cannot reliably supply; it does not become a second general command runner. Native scripts that require Darwin semantics run through the host execution route, rather than receiving misleading Linux stubs.

**c. Logins.** Support both native out-of-band authorization and callback forwarding; never substitute an API key if it changes account access or billing. From platform knowledge, Codex supports device authorization and Claude Code has terminal authorization-code flows, subject to the pinned versions. Verify the exact Gemini/Antigravity binary’s Google flow and forward callbacks when required; Grok’s flow is unverifiable from the text. Expose only the required loopback listener, preserve the redirect port/path and OAuth state, then remove the forwarding lease.

**d. Dev servers.** Make on-demand `expose` primary, using SSH reverse forwarding from host loopback to the container listener; this also supports listeners bound only to container loopback without recreating the container. Offer an optional user-selected published range for convenience. Support IPv4/IPv6 localhost, port conflicts, WebSockets and lease cleanup; never publish to LAN interfaces by default. Docker Desktop has opt-in host networking in newer releases, but this design need not depend on it.

**e. Watching.** In the hub, use native events with periodic reconciliation so missed events cannot leave persistent stale state. In Seed, persist checkpoints and reconcile filesystem state after wake/restart; event notifications are hints, not scheduling truth. For CLI-launched tools, configure their supported polling modes and test host-originated edits; there is no universal polling variable. Where watching or performance still fails parity, run that tool on the host through SSH; an FSEvents feed alone cannot repair an unmodified tool’s inotify assumptions.

**Consolidated hostd contract and threat model — nine lines**

1. `secret`: preserve the existing broker’s operations; “outcomes only” is valid only if equivalent to its native contract.
2. `notify`: structured notification data, safely passed to the session notification mechanism.
3. `open`: structured URL/file/app requests under the user’s existing permissions; mount membership is not an authorization boundary.
4. `clipboard-read` / `clipboard-write`: session clipboard access with bounded payloads and no content logging.
5. `expose-create` / `expose-close`: leased loopback forwards to this installation’s container endpoints, implemented through SSH.
6. Add session-specific verbs only for demonstrated gaps; general execution and ordinary host facts remain SSH operations.
7. Panes are trusted as the user; shared credentials and SSH permit account-level actions. Packaging is not isolation.
8. Bind hostd to host loopback on both platforms, reach it through SSH, and require an owner-readable, rotatable bearer token.
9. Pin SSH keys; reject browser-origin requests; validate bounded JSON; avoid shell interpolation; redact logs; fail clearly when the login session is absent.

**Phase 0 exit criteria**

Each check runs from **every lane**, against its native baseline. All eight must pass; `doctor` and one model response are insufficient.

1. **Authentication:** real account login, callback or out-of-band completion, container recreation, credential reuse and refresh; unchanged account capabilities.
2. **Filesystem:** same-path reads/writes, spaces, symlinks, permissions and home/config overlays; host executables remain intact, with separate platform-dependent dependencies.
3. **Real development:** edit/build/test a representative Mac project; mapped Linux interpreters and host-native execution both work with correct cwd, arguments, status and cancellation.
4. **Credentials:** Git identity, private SSH fetch, configured credential helper and commit signing match native behavior, including required prompts.
5. **Session reach:** broker operation, notification, browser/file opening and clipboard round-trip work; repeat after login-session and container restarts.
6. **Networking:** a pane dev server and callback listener work through IPv4/IPv6 localhost; host-local services and required MCP dependencies remain reachable; forged proxy headers cannot obtain trusted-proxy privileges.
7. **Watching/performance:** host and container edits trigger representative watchers; search, Git and rebuild latency meet thresholds chosen against native measurements.
8. **Lifecycle:** schedule a fixture from each lane, then sleep/wake and stop/restart Docker; verify agreed catch-up, persistence, cancellation and no duplicate execution.

**For the operator:** buildable under C1–C4, with the biggest risk being a host bridge that passes demonstrations but fails ordinary Mac development workflows.
