### Adversarial Design Review

#### 1. [Blocker] Dependency Starvation & The Apple Silicon Virtual Memory Trap (Questions A, B)
* **Scenario:** On an isolated scratch checkout with no `$HOME` and no network, `pytest`, `npm test`, `cargo test`, and `go test` fail immediately: `node_modules` and `.venv` are gitignored, and `~/.cargo`/`~/go/pkg/mod` are unreachable. Furthermore, if you mount `module_sandbox` as-is on macOS (Apple Silicon), its 2 GiB address-space `rlimit` (`RLIMIT_AS`) will instantly crash Node.js/V8, JVM, Go, and Rust, which reserve tens of gigabytes of virtual address space for cages/arenas without committing physical RAM.
* **Fix:** 
  1. Never enforce `RLIMIT_AS` on macOS (or set it ≥64 GiB for V8/Go); limit physical memory or rely on wall-clock + file-size caps.
  2. Provide dependencies via **two mechanisms**: (a) **operator-declared read-only host paths** passed to `module_sandbox(..., read_only=[...])` for global toolchains/caches (`~/.cargo`, `~/go/pkg/mod`, `/opt/homebrew`), and (b) a **hub-managed warm seed directory** per repo (e.g., a clean `node_modules` or `.venv` built from the trusted base branch) cloned into the scratch dir before running tests via APFS `cp -cR` (zero-cost copy-on-write clone on macOS) or Linux overlayfs/reflink. Do **not** mount the agent worktree's `node_modules` (an agent could have poisoned it) and do **not** allow network access during test runs.

#### 2. [Blocker] Unsafe Tree Extraction & Host-side Symlink Traversal (Questions A, C)
* **Scenario:** An agent commits a symlink `evil -> /Users/operator/.ssh/id_rsa` or a `.gitattributes` `filter`/`smudge` driver into the frozen tree. 
  * If the hub uses `git worktree add` or `git checkout-index` on the host before entering the sandbox, Git executes smudge/clean filters on the host with operator privileges, or creates symlinks that the hub later follows when reading or cleaning up the scratch dir.
  * Additionally, `git worktree add` mounts a `.git` file pointing back to the host's main `.git/worktrees/...`, requiring the sandbox to expose `.git`—leaking repo config, other branches, or hooks.
* **Fix:** Extract using `git archive --format=tar <tree_oid> | tar -x -C <scratch>` **inside** `module_sandbox` (or via Python `tarfile` with `filter='data'` on the host, rejecting `SYMTYPE`/`LNKTYPE` targets that escape `<scratch>` and stripping device/FIFO nodes). `git archive` ignores `.git` internals and does not run smudge filters unless `--worktree-attributes` is passed (pass `--no-worktree-attributes` or extract raw tree objects). Never use `git worktree add` for untrusted test execution. Note: submodules and LFS objects won't populate via `git archive`; document this limitation or resolve LFS pointers read-only from the host's `.git/lfs/objects`.

#### 3. [Major] macOS Seatbelt Escape Surfaces, Process Orphans & IPC (Question A)
* **Scenario:** Config-only `argv` (`pytest`, `npm test`) still executes 100% agent-controlled code (`conftest.py`, `setup.py`, test files). `module_sandbox` **is** the sole security boundary. On macOS:
  * **Process Group Escape:** A test calls `os.setsid()` or `setpgid(0, 0)` and forks a background daemon. When the 180s timer fires and the hub runs `os.killpg(pgid, SIGKILL)`, the detached child survives, holding the stdout/stderr pipe FDs open (hanging the hub's reader thread forever) or burning CPU indefinitely because macOS lacks PID namespaces and `PR_SET_PDEATHSIG`.
  * **Seatbelt Mach/IPC & `/tmp`:** If the Seatbelt profile allows broad `mach-lookup` or access to `/tmp` / `/var/folders` (where tmux, launchd, or SSH agent sockets live), hostile tests can talk to system services or local sockets.
* **Fix:** 
  1. Ensure the Seatbelt profile denies all `network*` (including UNIX domain sockets outside `<scratch>`), restricts `mach-lookup` to a minimal system allowlist, and sets `TMPDIR=<scratch>/tmp`.
  2. Do not wait on pipe EOF to finish timeout handling: close the parent's pipe ends immediately after `killpg`, and sweep any remaining processes whose `PWD` or open files reference `<scratch>` (or run inside a dedicated unprivileged UID/container if possible) before `shutil.rmtree(scratch)`.
  3. Keep `env` strictly minimal (`PATH`, `HOME=<scratch>`, `TMPDIR=<scratch>/tmp`, `LANG=C.UTF-8`).

#### 4. [Major] Log-Tail Truncation Blindness & Terminal/UI Injection (Question A)
* **Scenario:** 
  1. An agent test fails on line 10, then prints 256 KiB of spaces. Keeping only the *last* 256 KiB erases the failure traceback.
  2. The test outputs crafted ANSI OSC sequences (e.g., OSC 52 clipboard write, OSC 8 malicious hyperlinks, or raw HTML/script tags if rendered in a webview) into `.test.log`.
* **Fix:** Keep a **split buffer**: first 64 KiB (setup/early failures) + last 192 KiB (summary), with a clear `[... X bytes truncated ...]` marker. In the UI log viewer, strictly strip all control characters except SGR color codes (`\x1b[[0-9;]*m`) and HTML-escape before rendering.

#### 5. [Major] Tree-OID Binding Misses Base-Branch Drift (Question D)
* **Scenario:** The review card shows `diff(base, tree_oid)`, which is a function of *both* `base` and `tree_oid`. However, the test runner executes against `tree_oid` alone. If `base` advances on `main`, a merge may cleanly succeed via Git's 3-way merge while breaking tests due to a semantic conflict with new commits on `base`—yet the card still shows a green `PASSED` badge bound to `tree_oid`. (Conversely, identical `tree_oid` across two worktrees sharing a test result is completely fine and desirable.)
* **Fix:** Either run tests against an ephemeral trial merge tree (`git merge-tree --write-tree <base_oid> <tree_oid>`) and bind the result to `(repo, base_oid, tree_oid)`, **or** explicitly badge the test result as `STALE (base moved)` if `base_oid` changes before merge.

#### 6. [Minor] Silent Sandbox Degradation & Missing Badge States (Questions E, F)
* **Scenario:** Advisory-only is right, and cutting `.corral.json` and `eval` is right. However, if `module_sandbox.available()` returns `False` (e.g., missing `bwrap` on Linux), or if no test command is configured, the operator might confuse a missing/error badge with a clean bill of health, or worse, the hub might fall back to running unsandboxed.
* **Fix:** **Never fall back to unsandboxed execution.** Use distinct, unambiguous badge states: `UNCONFIGURED` (neutral gray, prompts to set config), `SANDBOX_UNAVAILABLE` (warning amber), `RUNNING`, `PASSED`, `FAILED`, `TIMED_OUT`, `ERROR` (setup/extraction failure). For flaky tests, record a `run_count` on `(repo, tree_oid)` and display `PASSED (retry #2)` so the operator sees the flake.

---

### Verdict: **Build with changes**

1. **Dependencies & macOS rlimits:** Drop/raise `RLIMIT_AS` on macOS; add config for read-only toolchain paths (`read_only`) and APFS copy-on-write (`cp -cR`) seeding of a clean base dependency dir (`node_modules`/`.venv`) into `<scratch>`.
2. **Safe extraction:** Use `git archive <tree_oid>` extracted safely without following external symlinks or running `.gitattributes` filters; never `git worktree add`.
3. **Process & log hardening:** Close pipes on timeout so detached `setsid()` children cannot hang the hub; split-cap logs (64 KiB head + 192 KiB tail); sanitize ANSI to SGR-only.
4. **Base-aware binding:** Key results to `(repo, base_oid, tree_oid)` (or trial-merge tree) and add explicit `UNCONFIGURED` / `SANDBOX_UNAVAILABLE` states with hard fail-closed behavior if the sandbox is missing.
