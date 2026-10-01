# Round 1 — GPT-6.1 Sol (codex lane, read-only mode, model echoed: gpt-6.1-sol, effort echoed: high, pane 2c45db3452f3)

P1 **RESHAPE** — Packaging helps, but retaining native mode and a host scheduler preserves the platform support burden.  
P2 **RESHAPE** — Default write access to fleet-synced notes makes the advertised sandbox’s damage propagate across machines.  
P3 **KILL** — Hook execution success does not prove enforcement; the proposed check can certify a disabled guard.  
P4 **RESHAPE** — Bundled Seed still needs an explicit boundary between container installation and host scheduler changes.  
P5 **KILL** — A container-wide OAuth environment variable unnecessarily exposes one lane’s credential to other agents.  
P6 **RESHAPE** — “Newest model” couples maintenance updates to unapproved changes in behavior and cost.  
P7 **RESHAPE** — The acceptance criteria prove basic availability while leaving enforcement, recovery, and Mac installation untested.

### Findings, most severe first

1. **“Executes every configured hook once with a no-op payload.”** A hook can exit successfully after rejecting the payload as irrelevant, skipping its policy, finding no applicable workspace, or swallowing an exception. That certifies execution while forbidden writes remain possible. Missing registrations also cannot be discovered by executing only registered hooks. Require a manifest of mandatory guards plus behavioral tests: a forbidden operation must be denied and an allowed operation must succeed. Prove which lanes enforce the guard; `.claude/settings.json` alone establishes nothing about the other three.

2. **“Doctor (and the healthcheck) … refuses to start the hub.”** A Docker healthcheck marks health; it does not itself prevent startup or make `unless-stopped` restart an unhealthy running process. The entrypoint must synchronously gate hub execution. Even that leaves later settings changes or hook failures unprotected. Enforce denial at the operation boundary, make guard loss visible, and define how configuration changes invalidate validation. The claimed PreToolUse failure behavior is **unverifiable from the text**.

3. **“CLAUDE_CODE_OAUTH_TOKEN injected at start.”** Container configuration exposes environment credentials to Docker-authorized operators; children normally inherit them, and same-user process inspection may expose them. Every pane can potentially inherit Claude’s token. Docker operators already have broad power, but cross-pane exposure is avoidable. Supply credentials only to the Claude process through a narrowly scoped mechanism and scrub unrelated child environments. A permissioned file avoids automatic inheritance, although it does not isolate agents sharing a UID and filesystem.

4. **“~/notes … writes leave the machine.”** This is a fleet-wide integrity boundary, not merely a local sandbox decision. An injected agent can corrupt or delete the second brain and have those changes synchronized. Host UID fixes ownership, not authorization. Default notes to read-only; provide explicit write enablement or a staging area whose changes Craig accepts. Syncthing synchronization is not evidence of recoverable backups.

5. **“Home … native reach for anything that is a file.”** This includes SSH keys, cloud credentials, shell startup files, and host service configuration. An agent can steal credentials or plant changes that execute later on the host without invoking a host service directly. Shadowing binaries does not contain that exposure. Describe `home` as granting broad host-user file authority; prefer individually selected mounts and separate credential provisioning.

6. **“Same absolute paths … $HOME equals the host’s.”** `/Users/<name>` is a valid Linux path; the prefix itself is not the defect. The missing implementation contract is creation, ownership, passwd identity, writable configuration directories, and population of shadow volumes under an arbitrary host home. Setting `$HOME` alone establishes none of those. Failures include login state written elsewhere, denied writes, and hooks resolving absent paths. Specify initialization under the effective UID, enumerate shadow mounts explicitly, and verify actual credential and transcript destinations. Do not assume `~/.venvs/*` is an executable Compose mount specification.

7. **“corral update … install.py --update”; “scheduler stays host-native.”** The text does not specify how container-run Seed updates install or reconcile host jobs, or what happens when workspace changes succeed but scheduler changes fail. Nor does pulling an older image necessarily reverse workspace migrations. Define the host runner, transaction boundary, compatible version record, backup, and rollback procedure. Otherwise updates can leave workspace, scheduler, and image on incompatible versions.

8. **“Pairing gate and WebAuthn origin stay exactly as today.”** Keeping the browser URL at `http://localhost:8098` can preserve the origin; container hostname is irrelevant to browser origin. But Phase 0’s `8099` changes it, and port forwarding can change the peer address the hub observes. Any reliance on peer-loopback status or trusted forwarding needs explicit review. Binding only inside the container to loopback can also make the published service unreachable. Existing authentication assumptions are **unverifiable from the text**; test the real published endpoint, Origin/Host validation, and credential enrollment.

9. **“Lane defaults follow the newest model.”** A routine adapter update can change cost, latency, tool behavior, and reasoning effort without a user decision. One successful prompt does not validate those consequences. Select defaults as an explicit release policy, preserve user overrides, announce changes, and test intended workflows. Availability of the stated newer models is **unverifiable from the text**.

10. **“Fresh Linux host goes from nothing”; “host.docker.internal.”** The four lines omit runtime installation and the host Python/uv requirement. Resolving the Ollama hostname also does not prove that native Ollama accepts connections from the container; its listening configuration is unspecified. A novice can reach `/status` while local inference and scheduler jobs are broken. Acceptance must cover fresh Mac and Linux installation, both architectures, reboot recovery, and an actual Ollama request without unintentionally exposing it to the LAN.

### Container alternative

The strongest alternative is uv-pinned Python, one installer with small OS-specific service templates, and versioned prebuilt adapter bundles, paired with a pinned Seed release. That plausibly captures most dependency and setup benefits while retaining Keychain, desktop, and device access. The exact “80%” is **unverifiable from the text**. It does not provide container isolation, but neither does default writable synced storage provide strong data protection. Compare both approaches before committing to two execution environments; offer containers where restricted host reach is a deliberate product requirement.

### Missing tests

1. Remove a mandatory hook, replace it with an always-successful stub, and corrupt its policy input: forbidden writes must remain denied.
2. From every non-Claude pane, attempt to obtain Claude credentials through environment, process inspection, and mounted login storage.
3. Install under `/Users/<name>` with fresh volumes and a non-root UID; verify ownership, login persistence, and actual write destinations.
4. Exercise pairing and WebAuthn through published ports `8098` and `8099`, including forged Origin/Host headers and unpaired requests.
5. Interrupt an update between workspace migration and host scheduler reconciliation; verify recovery and rollback with existing user data.

### What would change my mind

I would change P3 from KILL to BUILD after end-to-end tests demonstrate that missing, crashing, misconfigured, or always-successful hooks cannot permit a prohibited operation in any supported lane.
