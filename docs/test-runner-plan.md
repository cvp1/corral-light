# Test runner on the review card — plan

Status: PLAN, v1, 2026-10-16. Nothing built. Reviewed in one round by a
three-vendor panel (Codex, Grok, Gemini). The charge and the three answers
are in `reviews/2026-10-16-test-runner-panel/`. All three said "build with
changes". This version folds those changes in. §6 lists the decisions the
operator must make before Phase 0 starts.

**What this adds.** When an agent's branch reaches a review card, the
operator can run the repository's test command against the exact frozen tree
the card shows. The card gets a badge for that tree: passed, failed, timed
out, or one of the states that say why there is no result.

**What it is not.** A green badge means "the operator's command exited 0 on
this tree in the sandbox". It does not mean the change is safe. A hostile
test suite can exit 0 on purpose. The badge never blocks merge or push.

**Where it came from.** An outside feature spec (Epic 1) proposed
auto-detecting a runner, reading `.corral.json` from the worktree, and
running `eval "$TEST_CMD"` in the worktree. That puts code the agent wrote
in charge of what runs on the operator's machine, outside any sandbox, with
the operator's keys. This plan keeps the goal and drops that mechanism.

## 1. The rule above all others

The sandbox is the security boundary, not the command. The operator picks
`pytest`, but pytest imports the agent's `conftest.py`. The operator picks
`npm test`, but npm runs the agent's `package.json` script. Every design
choice below assumes the code under test is hostile. When the sandbox is
unavailable, nothing runs. There is no unsandboxed fallback.

## 2. Design

**D1. Where the command comes from.** Only the hub's own config, per
registered repository (keyed by the repository's path on the hub, never by a
remote URL the agent can edit). It is an argv list, never a shell string.
`argv[0]` must be an absolute path outside the scratch dir; a relative
`argv[0]` such as `./gradlew` is refused. The hub sets `PATH`, and it never
includes the scratch dir. Nothing in the agent's tree is read to choose or
change the command. Detection may later suggest a command in the UI, which
the operator confirms into config; detection never runs anything.

**D2. The sandbox.** A new **test profile** in `module_sandbox.py`, separate
from the collector profile. It shares `available()` and `build_argv()`.

- Linux: the existing bubblewrap allowlist (empty root, no network, own pid
  namespace, die-with-parent, cleared env). Children are fine there: the pid
  namespace ends them all. Add a size-capped tmpfs or a quota for scratch.
- macOS: the collector profile denies `process-fork` on purpose, because a
  `setsid()` child escapes the process group and macOS has no pid namespace
  to end it. Test runners fork, so the test profile must allow it. That is
  safe only with a proven reaping method. Phase 0 decides between three
  candidates: polling the run's descendants and killing them by pid; a
  dedicated unprivileged user whose processes are all killed by uid; or a
  throwaway VM. Until one passes falsifier F1, macOS reports the sandbox as
  unavailable for tests.
- Both: `TMPDIR` and `HOME` are inside scratch. A minimal `mach-lookup`
  allowlist on macOS. `RLIMIT_AS` is dropped from the test profile, because
  V8, Go and the JVM reserve far more address space than they use. Memory
  is bounded by cgroups on Linux where available; on macOS it is bounded
  only by concurrency 1 and the wall-clock limit, and the plan says so.
  `RLIMIT_CPU` and `RLIMIT_FSIZE` stay.

**D3. Dependencies.** No network during a test run. Real suites need
installed dependencies, which are gitignored and absent from the frozen
tree. Two sources only:

- Operator-declared read-only toolchain prefixes, such as the Homebrew
  prefix or a Python install. Each path is canonicalised. A path under
  `$HOME` is refused unless the operator names it, and the known secret
  locations stay refused even then. `~/.cargo` holds a registry token in
  `credentials.toml`, so a home cache is never bound whole.
- A per-repository dependency dir the hub builds itself, keyed by the
  digest of the lockfile in the frozen tree. The build is a separate,
  operator-started step that runs in its own sandbox with install scripts
  off (`npm ci --ignore-scripts` and the like) and registry access only.
  The test run mounts the result read-only, or clones it into scratch with
  APFS copy-on-write.

The agent's own `node_modules` or `.venv` is never used. A missing
dependency dir yields **error: dependencies not built**, never **failed**.

**D4. The tree under test.** Reuse `sessions._challenge_export`: it streams
`git archive` of the frozen tree, drops members that escape the dir through
`tarfile.data_filter`, and refuses trees over the size cap. No
`git worktree add` and no `checkout-index`, since both can run filters or
expose `.git`. Known gaps, stated on the badge: submodules and LFS content
are not present, and `export-ignore` in the tree's own `.gitattributes`
leaves files out. When that attribute appears in the diff, the card says so.

**D5. What a result is bound to.** The key is (repository, tree oid,
command digest, dependency digest, sandbox-profile version). A change to any
part makes the badge **stale**. Two worktrees with the same tree and the
same key share one result. The result tests the branch as committed, not
merged with the current base; the badge says that in words. A trial merge
with `git merge-tree` is out of v1.

**D6. When it runs.** v1: the operator presses **Run tests** on the card.
No automatic run. One active attempt per key; global concurrency 1; a
per-pane queue cap; a hard ceiling on the operator's timeout (default 180s,
cap 600s). A per-repository opt-in for automatic runs can follow once v1
has been used for real.

**D7. Output and the log.** The hub reads only the run's stdout and stderr
pipes. It never reads anything the run wrote into scratch, so there is no
junit or coverage read-back in v1. The log keeps the first 64 KiB and the
last 192 KiB with a truncation marker, stored in hub state, never in the
tree. The wall renders it as plain text through `textContent`. The ANSI
colour viewer is cut from v1. On timeout, the hub closes its pipe ends after
the kill, so a surviving child holding the pipe cannot hang the reader.

**D8. States.** `unconfigured`, `unavailable` (the sandbox cannot run
here), `queued`, `running`, `passed` (shown as "command exited 0"),
`failed`, `timed_out`, `error` (export, dependency or sandbox setup failed),
and `stale`. Unconfigured and unavailable must not look like a pass or a
fail. Each attempt is kept, so a re-run that passes does not erase an
earlier failure; the card shows "passed on retry 2".

**D9. Lifecycle.** A hub restart turns every `running` attempt into `error`.
Scratch is deleted only after every process of the run is gone, with the
existing walk-chmod-rmtree that does not follow links. Results and logs
expire with the card.

## 3. Phase 0: falsifiers

Each is a test that can fail. If one fails, that part of the design stops.

| ID | Test | Fails when |
|---|---|---|
| F1 | A test that calls `setsid()` and forks an endless child, then the run times out | The child is alive 5 seconds after the timeout, on either OS |
| F2 | A test that tries to read canaries in `~/.ssh`, the hub's `session.key`, and `~/.cargo/credentials.toml` | Any canary byte appears in the log, the result, or the network |
| F3 | A config whose `argv[0]` is `./run` | The hub accepts it |
| F4 | This repository's own unittest suite, and a small npm project with a hub-built dependency dir | Either fails in the profile while passing outside it |
| F5 | `node -e 1` and `go version` in the test profile | Either fails to start |
| F6 | A test that prints 1 GiB | Hub memory grows with it, or the log exceeds its cap |
| F7 | A tree with a symlink to a file outside it | The export or the cleanup follows it |
| F8 | A tree that fills scratch with many files under the per-file cap | The host volume fills past the scratch quota |

## 4. Changes from the outside spec

| Spec said | Plan says | Why |
|---|---|---|
| Auto-detect and run | Operator config only | Detection runs code the agent controls |
| `.corral.json` in the worktree | Hub config, keyed by local path | The agent can write any file in its worktree |
| `eval "$TEST_CMD"` in the worktree | argv, absolute `argv[0]`, in a sandbox on the frozen tree | No shell parsing; the live worktree can change after review |
| Log in `worktree/.test.log` | Head and tail of the pipes, in hub state | A log in the tree is a file the agent can pre-plant |
| Run on agent completion | Operator presses Run in v1 | Unique trees are cheap; automatic runs are a resource drain |
| ANSI log viewer | Plain text | Terminal escapes are an injection surface |

## 5. Where the panel disagreed

- **Automatic runs.** Grok: none in v1. Codex: automatic with rate limits.
  Gemini: did not object. The plan takes Grok's line for v1.
- **Home caches.** Gemini would bind `~/.cargo` and `~/go/pkg/mod`
  read-only. Codex warned that caches can hold credentials, and `~/.cargo`
  does. The plan takes Codex's line.
- **macOS containment.** Codex prefers a VM for real assurance. Grok
  proposes a fork-enabled profile with a kill-and-reap sweep. Phase 0 F1
  settles it on evidence.
- **Base drift.** Gemini would key on (base, tree) or run a trial merge.
  Grok and Codex would label it. The plan labels it in v1.

## 6. Decisions for the operator before Phase 0

1. **A macOS test profile that allows `process-fork`.** This reverses, for
   this workload only, a choice the collector profile made on purpose. It
   ships only if F1 passes.
2. **Manual Run only in v1**, with no automatic runs on card creation.
3. **No home caches.** Dependencies come only from toolchain prefixes and
   hub-built dependency dirs.
