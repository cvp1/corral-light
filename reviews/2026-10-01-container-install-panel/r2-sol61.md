# Round 2 — sol61 (model echoed: gpt-6.1-sol, effort: high, pane 2c45db3452f3)

### Cross-examination

**Grok**

- **Strongest agreement:** The absent-settings case: “zero hooks, and zero failures is a pass.” This exposes a fundamental defect in discovering required protections from whatever configuration happens to exist. Required guards need an independent manifest; otherwise deleting the configuration makes validation easier.
- **Overstated:** “`/Users/<name>` is false symmetry on Linux.” That path is valid in Linux; an image can provision the UID, passwd entry, directories, and permissions there. A fixed `/corral` home introduces path translation rather than automatically solving the problem. The claimed case-collision destruction and categorical macOS socket behavior are **unverifiable from the text**; they belong in platform tests, not established findings.
- **Another correction:** A separate process tree does not isolate credentials from other processes running as the same UID. Grok’s proposed credential fix needs an actual access boundary.

**Antigravity**

- **Strongest agreement:** Read-write, synchronized notes make a compromised pane a fleet-wide integrity threat. Its read-only default directly addresses the damage propagation mechanism. Whether damage propagates “instantly” or defeats backups is **unverifiable from the text**.
- **Wrong fix:** A `0400` secret file on tmpfs does not provide “significantly superior isolation” between panes sharing its owner UID and mount namespace. Tmpfs changes persistence; permissions distinguish identities, not process names. The fix requires separate access identities or namespaces, or a credential broker with a narrow interface.
- **Dangerous additional suggestion:** Trusting gateway subnets or the HTTP `Host` header does not establish that a request came from an authorized local browser. Forwarded-header trust requires an identified proxy that strips attacker-supplied headers; `Host` is client-controlled.
- **P7 BUILD is premature:** A useful parallel trial does not make its acceptance criteria sufficient. Prompt completion and interpreter removal leave the central enforcement claim unproved.

**My verdicts:** No P1–P7 changes. Grok strengthens the case for an independent guard manifest; Antigravity’s credential remedy reinforces why P5 needs redesign.

### Convergence: must-fix design requirements before Phase 0 implementation

- Specify mandatory guards independently of discovered settings, with behavioral denial tests through the actual tool runner.
- Make validation an entrypoint gate; define fail-closed behavior after configuration changes or guard failure.
- Establish protection for every lane with write access, rather than assuming Claude hooks cover the others.
- Default synchronized notes to read-only and provide an explicit, bounded write path.
- Keep Claude credentials out of container-wide configuration and unrelated pane environments; define enforceable credential separation.
- Specify UID, home, directory ownership, and explicit shadow mounts; test their actual behavior on macOS.
- Test pairing and WebAuthn through the published endpoint without treating gateway addresses or headers as authentication.
- Separate reviewed model-default changes from automatic adapter rebuilds.

### The fork

**Hybrid.** Use the pinned native bundle on the operator’s daily Mac seat and for users requiring Keychain, desktop, or device integration; use restricted containers for file-focused work on the Linux iMac/laptop and for novices choosing that scope. Keep both on one release manifest for Python, adapters, Seed, and model defaults, and defer making containers the universal installer until the platform and enforcement tests pass.

### The hook guard

No startup-only test can guarantee continuing enforcement by mutable hooks over writable mounts. To meet the requested guarantee, move the memory-write boundary into a protected broker and make direct writes unavailable:

1. Before listening, require a pinned manifest of guards, policies, and covered write routes; missing or mismatched entries fail startup.
2. Use a disposable protected fixture; keep the broker and guard code outside agent write authority.
3. Force a fresh prohibited write through each lane’s actual tool runner, using a nonce and unauthorized writer identity.
4. Require independently observed **policy denial from the required guard** and unchanged fixture bytes; execution errors, timeouts, or model refusal do not count.
5. Force an authorized control write through the same route and require the expected bytes, so a disconnected or universally denying system fails.
6. Any mismatch prevents startup; during operation, the broker mediates every protected write and denies requests whenever its guard is unavailable.

**The operator: build a shared release bundle first, then prove the restricted container as an option; do not ship the current safety claims.**
