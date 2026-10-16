I'll review this as an adversarial eval of the proposed test-runner design. First I'll load the eval-pass doctrine and the actual sandbox implementations so the findings are grounded in how `module_sandbox` really behaves.Eval-pass and the live sandbox code next — I need those to attack the Seatbelt/bwrap claims against what actually ships.Eval-pass requires re-deriving the sandbox claims from the live code, not the spec. Searching the fleet for `module_sandbox` and the Seatbelt profile.The macOS profile looks like it forbids fork. I'll check the Seatbelt docs and how tests actually get spawned.**Claim checked against live code:** `module_sandbox.py` + `docs/finops-macos-sandbox.md` §6. The Darwin profile allows `process-exec` and **forbids `process-fork` on purpose**. Collectors exec-only. `RLIMIT_AS` is unproven on macOS. Frozen-tree export already exists: `sessions._challenge_export` (`git archive` + `tarfile.data_filter`).

---

**1. Blocker — macOS profile cannot run a test suite.**  
`npm test` / `pytest` / `cargo test` / `make test` all `fork`. Current Seatbelt denies fork so a `setsid()` child cannot outlive the group (no pid ns, no `--die-with-parent`). Scenario: card auto-fires `["pytest"]` on the Mac hub; pytest dies on first `Popen`; badge `error` on every card.  
**Fix:** a **test profile**, separate from the collector profile. Allow `process-fork`. Kill story: `setsid()` + new session, `killpg` then `SIGKILL` on the recorded sid, then `pgrep`/`kill` leftover children of that sandbox; treat survivors as `error` (never `passed`). Cap CPU (`RLIMIT_CPU`) and concurrent runs at **1** on the hub host. Keep `file-map-executable` **off** the scratch (already true on Darwin); on Linux mount scratch `noexec`. In-tree Mach-O does not run.

**2. Blocker — argv provenance is one layer, not the boundary.**  
Operator `["npm","test"]` still runs agent `package.json` / `conftest.py` / `Makefile`. Relative `["./gradlew","test"]` is an agent binary.  
**Fix:** sandbox is the boundary. `argv[0]` must be an **absolute path outside the scratch**, resolved on a hub PATH that does not include the tree (`/usr/bin/npm` after an explicit RO bind, never `./`). Refuse relative argv[0].

**3. Major — Seatbelt leftover paths once fork is on.**  
Scratch is writable and is HOME. Agent tree can write `~/.npmrc`, plant a FIFO, fill the volume with many files under the 1 GiB *per-file* `fsize` (no quota). Hub `rmtree` of a hostile tree is the remaining host-touch. Log is hostile: ANSI/HTML in `log_tail` into the wall.  
**Fix:** reuse `_challenge_export` (skip escaping links, byte cap), chmod u+w **after** extract; never `worktree add` / `checkout-index` (hooks + smudge). Capture stdout/stderr **in the parent only**; never read scratch after the run; delete with the existing walk+chmod+`rmtree`. Render log with `textContent` only. Put scratch on a dedicated volume or `quota`. Cleared env stays; no `writable_extra`.

**4. Major — no-network + no-HOME makes the badge a lie.**  
Apple Silicon PATH in the sandbox is `/usr/local/bin:/usr/bin:/bin`. Homebrew `npm`/`cargo`/`pytest` live under `/opt/homebrew` or a venv, which are invisible. Suites fail `ENOENT` or missing `node_modules`. Operator learns to ignore the pill. Live-worktree `node_modules` as a RO bind is a race: agent mutates gitignored deps after freeze. Registry network is DNS + POST + `postinstall`.  
**Fix:** **operator-declared, hub-owned RO cache dirs**, keyed by lockfile hash from the frozen tree. Populate with a **separate** sandboxed install (`npm ci --ignore-scripts`, etc.), optional registry proxy only for that install. Never bind the agent worktree. Missing cache → state `error` (“deps not mounted”), not `failed`. No network in the test run.

**5. Major — auto-run is a DoS on the Ollama box.**  
Unique trees are cheap (touch one file). Each run unpacks a tree and can take 180s × 2 GiB AS.  
**Fix:** no auto-run in v1. Operator clicks Run. One run per `(repo, tree)`; global concurrency 1; queue cap per pane; hard timeout cap 600s.

**6. Major — checkout gaps.**  
`git archive` omits submodules, LFS blobs (pointer files only), untracked, gitignored fixtures.  
**Fix:** that is the v1 contract. Badge subtitle: “frozen tree only”. Submodules/LFS/untracked stay out; do not pull them from the live worktree.

**7. Minor — tree-oid binding is right for content, silent on merge.**  
Same tree in two worktrees sharing one result is correct. Base moving does not change the tree oid: tests pass on the branch while `merge-tree` with current main fails.  
**Fix:** bind to tree; show “tested as committed tree, not merged with current base”. Card staleness vs base stays a review problem, not a test-key problem.

**8. Minor — advisory is right; the words are not.**  
Blocking merge on a red pill bricks Publish when deps are missing. Habituation is the residual risk.  
**Fix:** states: `unconfigured` | `unavailable` | `running` | `passed` | `failed` | `timed_out` | `error`. Unconfigured and unavailable must not look like pass or fail. Flakes: Re-run only; do not average.

**Cut:** ANSI log viewer; auto-detect that one-click-enables; auto-run; any read-back of scratch (junit.xml, coverage).

---

**A.** Argv is necessary and insufficient. Sandbox is the boundary. Collector Seatbelt is **not** strong enough for this workload until fork+kill+noexec+quota above. Remaining: setsid orphans, disk fill, log injection, dep-cache integrity.  
**B.** Hub-owned RO caches from lockfile, ignore-scripts install. No live worktree, no test-time network.  
**C.** `git archive` + existing `data_filter` only.  
**D.** Tree oid is the right content key. Call out merge-with-base separately.  
**E.** Advisory. Honest empty states.  
**F.** Kill/reap, noexec, quota, log trust, argv[0] policy, dep cache, auto-run off.

**Falsifier:** fixture A `setsid` + infinite child still alive 5s after timeout → kill the feature. Fixture B `package.json` `test` reads `~/.ssh` or hub `session.key` and those bytes appear in `log_tail` → kill. Fixture C relative `./run` as argv[0] accepted → kill.

**Verdict: build with those changes.** Command-from-operator-config is the right cut. Reusing collector `module_sandbox` as-is on the Mac hub is a category error.
