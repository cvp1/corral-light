# Panel synthesis — Corral Light container install (v1, 2026-10-01)

Plan: `docs/container-install-proposal.md` @ 37b018c. Two rounds, three vendors,
each reviewing the pasted text only from an empty scratch directory.

| Seat | Model (verified) | How verified |
|---|---|---|
| Grok | grok-4.7, effort xhigh | Grok session `summary.json`, both rounds |
| Sol | gpt-6.1-sol, effort high, read-only mode | hub-echoed model and effort |
| Gemini | gemini-3.8-flash-high | hub-echoed model (effort lives in the ID) |

## Bottom line

**Unanimous: hybrid.** Keep the Mac host on a pinned native install; ship the
container as an opt-in restricted sandbox for novices and Linux hosts, and
only after the must-fixes below. Sol's framing is the sharpest: build one
**shared release bundle first** (pinned Python, adapters, CLIs, Seed, model
defaults), then prove the container as an option. "Do not ship the current
safety claims."

## Verdicts

| Part | Grok | Sol | Gemini (after R2) |
|---|---|---|---|
| P1 container image | RESHAPE | RESHAPE | RESHAPE |
| P2 mount scopes | RESHAPE | RESHAPE | KILL (reshape in effect) |
| P3 hook fail-open fix | **KILL** | **KILL** | **KILL** (changed in R2) |
| P4 Seed bundled | RESHAPE | RESHAPE | BUILD |
| P5 login/credentials | **KILL** | **KILL** | **KILL** |
| P6 CI lane updates | RESHAPE | RESHAPE | RESHAPE |
| P7 phase plan | RESHAPE | RESHAPE | BUILD |

## Converged must-fixes (all three, round 2)

1. **The hook check certifies a disabled guard.** A no-op payload proves the
   interpreter runs, not that the guard denies. An always-exit-0 hook, an
   empty settings file (zero hooks, zero failures), or a timeout all pass.
   Replace with a deny test through Claude's real PreToolUse runner, against
   a canary, requiring the guard's own deny record **and** unchanged bytes,
   plus an allowed control write that must land. A pinned manifest names the
   required guards, so a deleted config fails instead of passing.
2. **A healthcheck is not a gate.** `restart: unless-stopped` keeps an
   unhealthy hub serving. The entrypoint must run the deny test synchronously
   and refuse to exec `hub.py`; a settings or mount change re-runs it or exits.
3. **The guard covers one lane of four.** `.claude/settings.json` hooks bind
   Claude Code only; Codex, Grok and Antigravity share the same mounts and UID.
   Write protection belongs at the mount (read-only) or a broker, not in one
   vendor's hook. Sol: only a protected write broker gives a guarantee that
   outlives startup.
4. **`~/notes` read-only by default.** Mounted, as the operator asked, but `:ro`.
   Syncthing turns one injected or buggy write into fleet-wide damage; host
   UID fixes ownership, not authorization. Writes go to a bounded outbox or an
   explicit opt-in.
5. **No Claude token in the container environment.** `docker inspect`,
   `/proc/*/environ` and every child see it. A same-UID file or tmpfs mount is
   not a boundary either (Grok and Sol both rejected Gemini's tmpfs fix). The
   token reaches only the Claude process via a separate identity or a narrow
   broker; never in compose, the hub env, or other panes.
6. **Same-path `$HOME` needs an identity contract.** `/Users/<name>` is legal
   on Linux, but the UID needs a passwd entry, owned directories, and an
   explicit shadow list — Compose has no globs, so `~/.venvs/*` shadows
   nothing. Test on Docker Desktop before claiming same-path works.
7. **Pairing must be tested on the real peer address.** Published ports arrive
   from the bridge gateway, not 127.0.0.1. Do **not** "fix" by trusting
   gateway subnets or the Host header (Gemini's R1 suggestion; Grok and Sol
   both called it a pairing weakener). `:8099` is a different WebAuthn origin.
8. **Model defaults are a reviewed change, not a rebuild side effect.** CI may
   open the bump; a human merges it.

## Single-reviewer findings worth keeping

- **Grok:** `host.docker.internal` reaches every loopback service on the Mac,
  not just Ollama — needs an egress allowlist. A macOS Unix socket does not
  cross Docker Desktop's VM, so Phase 3's secret-broker bridge has no
  mechanism as written. Docker Desktop can start before the fscrypt vault
  unlocks. `corral init` running `install.py` in the container cannot register
  launchd jobs.
- **Grok vs Gemini, unresolved:** whether case-insensitive APFS through Docker
  Desktop can silently lose a file when two names differ only by case. Grok
  says data loss; Gemini says the host returns EEXIST. Settle with the test.
- **Sol:** updates need a transaction boundary — what happens when the
  workspace migrates and the host scheduler step fails, and how to roll back.
  The four-line install omits installing the container runtime and `uv`.
- **Gemini:** the home-scope shadow list will always miss some Mac binary
  (`~/bin`, `~/go/bin`, `~/.cargo/bin`); set a container-only `PATH` instead.

## Finding that applies to TODAY's native install

Finding 3 is not container-specific. Natively, Codex, Grok and Antigravity
panes can also write anywhere the user can, and the Claude PreToolUse guard
does not see them. `me/CAPABILITIES.md` says a Grok hook denies direct memory
writes, so coverage may be partial rather than absent — **unverified; worth a
check against each lane's config before relying on the memory-mesh guard.**

## Recommended v2 shape (author)

1. **Release bundle first, for every host.** One versioned manifest pins
   Python (uv 3.12), adapter and CLI builds per OS/arch, Seed, and model
   defaults. One installer; launchd/systemd stay. Fixes every measured pain in
   the v1 table without a VM, and keeps the Mac host's host reach.
2. **Container second, as the sandbox install.** Same manifest inside the
   image. Must-fixes 1–8 are entry criteria for Phase 0, not later polish.
3. **Guard independent of vendor hooks.** Read-only mounts plus a write broker
   for the memory store and `~/notes` outbox, so all four lanes are covered.

## Decisions for the operator

1. **Accept the hybrid?** It moves "release bundle" ahead of "container" in
   the build order. You said you like containers; all three reviewers say
   your daily Mac seat should not be one.
2. **`~/notes` read-only in the container default**, with writes through an
   outbox you promote? It keeps your "mount it by default" call.
3. **GHCR public or private** — unchanged from v1.
