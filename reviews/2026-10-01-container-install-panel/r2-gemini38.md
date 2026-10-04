# Round 2 — gemini38 (model echoed: gemini-3.8-flash-high, effort: None, pane 80a92e056d03)

### 1. Cross-Examination

#### Reviewer: Grok (grok-4.7)
* **Most Agree With:** **Finding 2 (The Claude-Only Side Door).** Grok astutely catches an architectural flaw both the plan and other reviewers largely glossed over: hooks in `.claude/settings.json` constrain *only* Claude Code. Codex, Grok, and Antigravity panes share the same UID, container workspace, and bind mounts, completely bypassing Claude’s PreToolUse memory guard. Relying on client hooks for filesystem security when four disparate CLIs share the mount is security theater.
* **Wrong / Overstated:** **Finding 5 (Case-folding APFS Collision).** Grok claims Docker Desktop on macOS maps case-insensitive APFS directly such that `Notes.md` and `notes.md` will collide and destroy files. In reality, Docker Desktop on macOS runs an optimized Linux VM (Hypervisor.framework / virtiofs); the host APFS volume handles case insensitivity natively (lookup collisions fail at OS create time with `EEXIST` rather than silently destroying files). While case-folding quirks exist, claiming silent file destruction inside the container is overstated.

#### Reviewer: ChatGPT / Codex (sol-6.1)
* **Most Agree With:** **Finding 2 (Healthcheck Decoupling).** Codex correctly identifies that Docker `HEALTHCHECK` and Compose `restart: unless-stopped` decouple container lifecycle from health. Marking a container "unhealthy" does *not* stop the process or deny traffic. Unless the entrypoint hard-fails and exits process execution synchronously, an unhealthy container will sit running on `:8098`, continuing to accept traffic and execute agent commands with disabled guards.
* **Wrong / Overstated:** **Finding 6 (Criticizing `/Users/<name>` as an Inherent Failure).** Codex claims setting `$HOME` to `/Users/<name>` breaks simply because of directory structure and ownership initialization. The path string `/Users/...` is completely legal on Linux; the actual issue is `getpwuid` / NSS lookups when running as arbitrary `--user ${UID}:${GID}` without an `/etc/passwd` record. Stating that the path itself causes login state to scatter misidentifies standard POSIX file path support for missing glibc user database mappings.

#### Verdict Revisions
* **P3 (Hook fail-open fix): RESHAPE $\rightarrow$ KILL.** Both reviewers demonstrated conclusively that the plan's hook check fails on two lethal fronts: (1) no-op checks certify stubs/always-exit-0 scripts, and (2) PreToolUse hooks enforce *nothing* against the Codex, Grok, and Antigravity panes sharing the container. This cannot be built as designed.

---

### 2. Convergence (Must-Fix Before Phase 0)

1. **Deny-by-Default Hook Verification:** Replace the no-op interpreter check with an active, negative blocking test; start must hard-abort if a prohibited write succeeds or hooks are absent.
2. **Cross-Pane Memory Protection:** Move write protection to the filesystem layer (e.g., read-only mounts) because Claude hooks do not govern Codex, Grok, or Antigravity.
3. **`~/notes` Read-Only Default:** Bind mount `~/notes` as `:ro` by default; Syncthing replication turns any rogue write or prompt injection into an immediate fleet-wide data disaster.
4. **Credential Isolation:** Remove `CLAUDE_CODE_OAUTH_TOKEN` from the global container environment; scope it strictly to the Claude CLI process via file descriptor or private secret mount.
5. **Entrypoint User/NSS Synthetic Identity:** Generate container-internal `/etc/passwd` entries mapping `${UID}` to `$HOME` so POSIX identity queries (`getpwuid`) succeed on macOS-style `/Users/<name>` paths.
6. **Synchronous Boot Gating:** Enforce health and hook validation in the container `entrypoint.sh` before spawning `hub.py`; never rely on asynchronous Docker healthchecks to block traffic.
7. **Pinned, Explicit Model Defaults:** Remove automatic "newest model" defaults on CI rebuilds; preserve pinned models in version-controlled config to prevent unexpected cost and behavioral drift.

---

### 3. The Fork: Architectural Recommendation

**Recommendation: A Hybrid Split.**
* **For the operator (Primary Mac Workstation / the Mac host): Non-Container.** Deploy a host-native, `uv`-pinned Python 3.12 runtime with prebuilt adapter bundles and platform binaries. The operator requires low-latency macOS Keychain integration, IC-7300 USB serial, desktop notifications, and zero VM overhead.
* **For Novices & Remote Fleet (Linux iMac, Linux laptop): Container.** Ship the hardened container image (with read-only `~/notes`, unprivileged credentials, and fixed gateway handling) where a sandboxed "appliances-style" zero-dependency install protects novice hosts from arbitrary local file compromise.

---

### 4. The Hook Guard: Absolute Startup Gate

```bash
# In entrypoint.sh before starting hub.py:
PAYLOAD='{"tool":"Write","path":"/opt/aios-seed/MEMORY_GUARD_TEST","content":"probe"}'
OUTPUT=$(/opt/aios-seed/venv/bin/python3 "$WORKSPACE/.claude/hooks/guard.py" <<< "$PAYLOAD" 2>&1)
EXIT_CODE=$?
# Guard must exit non-zero (DENY) and block the forbidden test write
if [ $EXIT_CODE -eq 0 ] || [ -f "/opt/aios-seed/MEMORY_GUARD_TEST" ]; then
  echo "FATAL: Memory write guard failed to block forbidden probe. Refusing hub start." >&2
  exit 1
fi
```

---

### 5. One-Line Bottom Line for the operator

Do not containerize your daily Mac seat just to fix dependency drift: pin your native environment with `uv`, lock `~/notes` to read-only, and reserve the container as a sandboxed distribution for non-Mac secondary nodes.
