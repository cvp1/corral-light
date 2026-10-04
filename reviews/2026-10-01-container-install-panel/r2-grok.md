# Round 2 — grok (model echoed: grok-4.7, effort: xhigh, pane 870a26b49a5c)

## Cross-exam

**Codex.** The finding I most agree with is his second: a Docker healthcheck only marks health, and `restart: unless-stopped` will not stop a hub that is already up, so "doctor and the healthcheck refuse to start the hub" is false as written. He is also right that "a failing PreToolUse hook does not block" is unverifiable from the text; the plan's severity ranking sits on that claim. His overstated finding is the sixth. `/Users/<name>` is a legal Linux path, and with `HOME` set, Python and Node homedirs follow it. Stopping there leaves a data-loss bug: Docker Desktop bind-mounts case-insensitive APFS into a case-sensitive mount namespace, so two names that differ only by case collapse on the host. A synthetic passwd entry and an explicit shadow list do not fix that collision. The part of that finding that stands is that `~/.venvs/*` is not a Compose mount.

**Gemini.** The finding I most agree with is his second: default read-write `~/notes` on a Syncthing folder is a fleet integrity bug, and host UID fixes ownership only. Three claims are wrong or overstated. P4 **BUILD** and P7 **BUILD** do not hold: the plan still keeps a host-native Seed scheduler and host Python, and Phase 0's falsifier is one prompt plus the no-op hook check. Staging on `:8099` is a tactic. P2 **KILL** is too wide: limited-default versus opt-in mounts is the right shape, and his own reversal (notes `:ro`, writes in an audited subfolder) is a reshape. His credential fix is weaker than the defect: a mode-`0400` tmpfs file remains readable by every process with the same UID. Trusting forwarded headers so a bridge peer looks like `127.0.0.1` weakens pairing. His fourth finding overstates FHS; the prefix is legal. Browser origin and the hub's peer address are different facts, and he merges them.

**Verdicts.** No changes. P3 stays **KILL**: the proposed check is what must not be built. P2 stays **RESHAPE**: read-only notes repair the scopes. P5 stays **KILL**: a same-UID file does not contain the leak his fix describes. P4 and P7 stay **RESHAPE**.

## Convergence

Must-fix in the Phase 0 spec before anything is built:

- The entrypoint refuses to start the hub unless a prohibited memory write is denied and the target bytes are unchanged; a healthcheck is not that gate; a missing or always-allow guard fails closed.
- Default `aios` `~/notes` is read-only. Fleet sync is not a write path.
- `CLAUDE_CODE_OAUTH_TOKEN` is absent from the container environment, the compose file, and every other lane's process.
- Same-path mounts have an explicit UID, passwd entry, and shadow list (no globs), and a Docker Desktop case-fold trial is part of the Mac claim.
- Pairing is tested on the published port's real peer address; `:8099` is a different WebAuthn origin from `:8098`; the in-container bind address is specified so the publish can reach the process.
- Every lane that can write is covered. `.claude/settings.json` covers Claude only.

## The fork

Hybrid. The Mac host, the operator's daily Mac, stays a uv-pinned native install with prebuilt host-arch bundles, so Keychain, USB serial, notifications, and the Seed scheduler keep working. The container is the install for a novice and for a Linux host used as a restricted sandbox, and only after the list above is in the spec. Phase 0 beside native on `:8099` is a falsification spike for mounts, pairing, and the deny gate.

## The hook guard

1. A fixed manifest names the memory-mesh guard. If the resolved config lacks that entry, exit 1.
2. Through Claude's real PreToolUse runner, send an ordinary prohibited write to a canary in the protected tree, with no test-only flag.
3. Pass only if stdout carries that guard's own deny record (decision plus the canary path), the status is non-zero, and the canary hash is unchanged. A crash, `/bin/true`, or a bare runner error emits no such record.
4. Send an allowed write outside that tree and require the bytes to land, so a dead runner cannot look like a deny.
5. From a non-Claude pane, the same path must be rejected by a read-only mount and the hash must stay unchanged.
6. Any miss: the entrypoint exits and does not start `hub.py`. A settings or mount change re-runs the check or the process exits.

## Bottom line

Keep the Mac host native; build the container only as a novice or Linux sandbox whose hub cannot start unless notes are read-only, Claude's token is out of the shared environment, and a real prohibited write is denied for every lane.
