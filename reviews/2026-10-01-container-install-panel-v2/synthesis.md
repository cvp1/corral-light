# Panel synthesis — v2 (containers everywhere, under C1–C4)

Plan reviewed: `docs/container-install-proposal.md` @ a25da56. Result folded
into v3 of the same file.

| Seat | Model (verified) | How |
|---|---|---|
| Grok | grok-4.7 / xhigh | Grok `summary.json`, both rounds |
| Sol | gpt-6.1-sol / high, read-only | hub echo |
| Gemini | gemini-3.8-flash-high | hub echo |

## Bottom line

All three: **buildable as constrained.** Shared biggest risk: demos pass while
ordinary Mac work fails on the first native tool, file watch, or dev server —
so Phase 0 must run real workflows on every lane.

## Converged gaps (now fixed in v3)

| Gap | Fix in v3 |
|---|---|
| OAuth localhost callbacks can't reach the container | per-lane out-of-band flow, else a hostd `expose` lease; refresh tested after recreate |
| Pane dev servers invisible to the Mac browser | published loopback dev range + `expose` |
| Linux `node_modules`/`.venv` clobber host deps on shared mounts | anonymous overlay volumes; Phase 0 on a workspace copy |
| Mach-O tools and Darwin scripts can't run in Linux | `corral-host-shell` over SSH with the full exec contract |
| Virtiofs drops/lag on host-originated file events | hub and Seed reconcile by polling; tool polling switches; heavy watchers on host |
| Session services (Keychain, notify, clipboard, open) | hostd verbs; no exec on hostd |
| Git config, known_hosts, osxkeychain helper | read-only mounts, container SSH snippet, helper over host shell |
| TCC-hidden folders, file-sharing settings | install sentinel per mount |
| Missing Linux build for a lane | release tag fails |
| Tailscale Serve trust via bridge gateway | never trust the gateway; forged-header test |
| Scheduler lifecycle (sleep, Docker quit) | in-container authority + host doorbell |

## Disagreements and how they were settled

- **launchd after sleep.** Grok (R1) said missed calendar intervals are
  skipped. Sol, Gemini, and Grok himself in R2: they **coalesce into one run
  on wake**, and launchd does not wake the machine. v3 matches that.
- **Scheduler authority.** Gemini: host launchd fires each job via
  `docker exec`. Grok and Sol: one authority in the container, host agent only
  as a doorbell. v3 takes the latter (2–1): one manifest, one state store.
- **General host commands.** Grok (R2): a hostd `spawn` verb, since SSH lands
  outside the Aqua session. Sol and Gemini: SSH with a login-PATH wrapper,
  hostd for session-only capabilities. Author measured on the Mac host: SSH *can*
  reach `launchctl print gui/<uid>`, and its PATH lacks Homebrew. v3 takes SSH
  + `corral-host-shell` (2–1, backed by the measurement).
- **hostd transport.** Sol: tunnel through SSH. Grok and Gemini: direct to
  host loopback via `host.docker.internal` with a token. v3 takes direct (2–1).
- **Keychain over SSH.** Sol: depends on item ACLs. Author's measurement on the
  login keychain: "User interaction is not allowed". v3 keeps `secret` on hostd.

## Not adopted

- Gemini: shim every Darwin binary on the container PATH — rejected by Grok
  and Sol; the host shell runs the real binary.
- Gemini: a FSEvents→inotify injector for CLIs — deferred; polling plus host
  execution covers Phase 0.
