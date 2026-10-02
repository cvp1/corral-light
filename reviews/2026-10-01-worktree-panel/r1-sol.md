**(a) Overall — BUILD:** Worktrees fit parallel agents, provided isolation is described as separate checkouts, not enforced filesystem confinement.  
**(b) worktrees.py — RESHAPE:** Its digest and Python lock cannot make mutations atomic against agents, editors, hooks, or external git.  
**(c) Lifecycle — RESHAPE:** Ownership transfer and crash recovery lack durable transactions and process quiescence.  
**(d) Review UI — RESHAPE:** Actions need explicit reviewed revisions, incomplete-review warnings, and recoverable failure states.  
**(e) Test plan — RESHAPE:** Broad happy-path coverage misses the concurrent writes and partial failures most likely to destroy work.

## Lost work

- **Agent writes after preview:** Digest validation succeeds, then `add -A`, merge, or removal consumes newer work. **Fix:** Block new turns, stop and await tool subprocesses, serialize hub actions, revalidate immediately before mutation. External editors remain uncontrolled; use immutable commit/tree targets and recoverable disposal rather than promising atomicity.
- **Discard deletes ignored files:** A “clean” worktree can contain `.env`, datasets, generated artifacts, and ignored agent output; normal worktree removal can delete these. **Fix:** Inventory ignored files and nested repositories explicitly; archive the complete directory before destructive disposal, or refuse pending manual cleanup.
- **Reviewed branch changes identity:** Agent checkout, reset, or branch rename leaves metadata pointing at another ref. Merge/push/delete may affect different work than the preview. **Fix:** Verify registered worktree, symbolic HEAD, branch identity, and expected OIDs on every action; quarantine mismatches.
- **Hooks change the commit:** A hook edits/stages additional content after review, or failure leaves the real index changed. **Fix:** Record pre-operation HEAD/index, show actual resulting commit and residual changes, and require renewed review before merge/publish. Never silently reset staged work.
- **Main checkout changes during merge:** Clean-check passes, then the operator edits or runs git. Automatic abort can interfere with his edits or an unrelated merge. **Fix:** Cut main-checkout merge from v1; later require explicit exclusive-use acknowledgment and transaction-specific recovery.
- **Timeout/crash after mutation:** Commit, merge, push, or PR creation can succeed before the response disappears. Retrying can duplicate actions; hook children may survive. **Fix:** Durable operation journal, process-group termination, postcondition reconciliation, and “outcome unknown” states; never infer rollback from an exception.
- **Creation before pane persistence:** A crash leaves a branch/worktree with no record. `reconcile(records)` cannot discover a previously unknown repository. **Fix:** Persist a creation intent and registry containing common directory, path, branch, and creation OID before git runs; enumerate registry plus root after restart.
- **Port transfers ownership too early:** Starting the target before stopping the source permits simultaneous writes; failed handshake or crash strands ownership. **Fix:** Durable handoff state machine: quiesce source, reserve ownership, start target, commit transfer; recover failures without allowing two writers. Enforce lane eligibility on port too.
- **Wrong checkout/base:** Selecting a repository subdirectory loses the relative cwd; selecting a linked worktree does not identify the main checkout. Detached/unborn HEAD also lacks the proposed merge target. **Fix:** Preserve relative cwd, resolve checkout registrations explicitly, reject unborn HEAD, and require an explicit target for detached starts.
- **Publish misplaces work:** Push excludes uncommitted edits even though review includes them; changing remote configuration can redirect publication. Deleted local branches leave remote branches/PRs alive. **Fix:** Require clean, reviewed commits; bind confirmation to destination URL, ref and OID; report local/remote outcomes separately.
- **Cache hides edits:** `(HEAD, index mtime)` stays unchanged during ordinary unstaged edits, so review readiness can disappear. **Fix:** Never use that cache for action validation; refresh working content and label summaries stale.

## Git correctness — ranked

These are findings from git internals knowledge, not code inspection.

1. **Temporary-index digest is not a byte snapshot.** Clean filters, CRLF normalization, ignored files and staged-only content break T-DIF-5. Fix: define the digest as the proposed Git tree plus HEAD, separately track real-index state and excluded content, and bind each action to its own additional inputs.
2. **Temporary index is not read-only.** `add` runs clean filters and writes objects; `merge-tree --write-tree` writes objects and can invoke configured merge drivers. Fix: document these effects, supervise subprocesses, and avoid calling either a harmless probe. `GIT_OPTIONAL_LOCKS=0` suppresses optional locks only.
3. **Prune cannot select paths.** `git worktree prune` operates repository-wide; “only hub paths” is not an available filter. Fix: omit automatic prune; retain stale registrations for explicit repair.
4. **`branch -d` proves the wrong thing.** With an upstream, it checks integration into upstream, which may merely be the published agent branch. Fix: explicitly test ancestry against the recorded target; delete only after verifying the expected branch OID and retaining a recovery ref.
5. **No transaction spans check and merge.** Git’s internal locks do not lock working files throughout preflight; hooks/timeouts complicate abort. Fix: remove automatic checkout merge initially; never abort a merge without proving operation ownership.
6. **Environment and config redirect behavior.** Inherited `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, etc. can target another repository. Fix: sanitize routing environment variables; explicitly set required temporary-index values. Respect `safe.directory` errors without installing a wildcard exemption.
7. **Registration and ownership exceed prefixes.** `corral/*` plus containment does not prove hub creation; symlink checks have replacement races. Fix: durable ownership registry, canonical common-directory identity, registration/ref verification, controlled root permissions, and revalidation before deletion.
8. **Diff machinery needs stronger controls.** `--no-ext-diff` does not disable textconv; newline filenames cannot safely identify files through patch headers. Fix: add `--no-textconv`, derive identities from NUL-delimited raw metadata, and attach hunks to those identities.
9. **Sparse checkout, submodules and LFS need explicit support boundaries.** Generic temporary-index `add -A` is insufficient for every sparse/index state; submodules contain independently dirty repositories. Fix: reject unsupported sparse/submodule cases in v1; detect filters and fail clearly when required tools are absent.
10. **Version/output and resource contracts are incomplete.** `merge-tree` conflict output needs structured parsing; 2.55 fixtures do not establish 2.38 compatibility. `capture_output=True` is unbounded before truncation. Fix: test minimum versions, request structured output where supported, stream bounded output, and cap the encoded JSON response.

## Agent realities

- **Claude:** Git understands linked worktrees; tracked instructions appear normally. Trust behavior and ACP prompt handling are **unverifiable from the text**. Without filesystem confinement, Claude can access the main checkout by absolute path. Committing depends on adapter permissions and hooks.
- **Codex:** Given the stated cwd sandbox, shared Git administrative paths may block staging/committing; exact protected-path behavior is version-specific and **unverifiable from the text**. Prefer hub commits. Making the entire common directory writable also exposes other branches’ refs and administrative state.
- **Grok:** Linked-worktree detection, trust and commit behavior are **unverifiable from the text**. Require the lane spike before enabling it; correct `git status` alone does not prove all tools stay confined.
- **Gemini/Antigravity:** Same uncertainty; adapter-specific tool roots and trust handling need measurement. Successful edits do not demonstrate sandbox isolation.
- **host:** Given the verified cwd behavior, refuse creation **and handoff**.
- **Ollama:** Given no tools, refuse ownership. Chat feedback can remain available without granting a checkout.

## §7 recommendations

**1. Location:** Keep the state-directory default, with configurable root. Persist ownership independently of pane metadata and preserve selected subdirectory cwd. Location does not establish containment.

**2. Merge:** Cut automatic main-checkout merge from v1. Updating an unchecked-out base ref later is viable using an expected-old-OID compare-and-swap, but only after checking all worktree registrations and defining crash recovery.

**3. Untracked files:** Use a temporary index; never `add -N` into the agent’s real index. Preserve/report staged state separately and acknowledge filter execution and ignored-file exclusion.

**4. Ownership:** One writing owner. Defer additional read-only agent panes: an instruction to avoid writes is not enforcement. Browser review requires no agent ownership.

**5. Missing tests:** Prioritize concurrent writers, operation uncertainty, ignored-file disposal, crash-safe ownership, and mutable branch/remote identities over more parser fixtures.

## Additional tests

- **T-SAFE-1:** Agent writes between validation and mutation; no unreviewed commit/publication or irreversible disposal.
- **T-SAFE-2:** Clean worktree with ignored secrets, nested repo and dirty submodule survives refused disposal.
- **T-SAFE-3:** External checkout/reset/ref replacement makes merge, publish and deletion refuse.
- **T-SAFE-4:** Hook stages extra content or fails; resulting commit/index state is surfaced accurately.
- **T-SAFE-5:** Kill hub at every journal boundary; recover creation, commit, push and PR without duplicate effects.
- **T-SAFE-6:** Port failure/restart leaves exactly one recoverable owner and no surviving source writer.
- **T-SAFE-7:** Published upstream with no base integration never qualifies for ordinary branch cleanup.
- **T-SAFE-8:** Remote URL changes after confirmation; publication refuses.
- **T-SAFE-9:** Unstaged edit with unchanged index mtime invalidates actionable review state.
- **T-SAFE-10:** Malicious inherited Git routing variables and unrelated stale registrations remain untouched.

**Listed tests needing repair:** T-DIF-5 is false as written; T-CRT-9 cannot guarantee rollback after timeout; T-REC-5 requires unavailable selective-prune semantics; T-ISO-1 cannot prove absence of arbitrary external writes from a real-root snapshot. T-LIF-16 needs a real subprocess crash harness, not its listed in-process level.

## Scope

**Cut:** Automatic main-checkout merge; automatic pruning; port handoff until transactional; unverified lanes; mandatory PR creation alongside push.

**Cannot ship without:** Durable ownership/operation registry; quiescence and action-specific revision binding; recoverable disposal including ignored files; identity checks on every action; truthful partial-failure and incomplete-review UI.

## One sentence

The operator will discard an apparently clean agent worktree and discover that its ignored configuration or generated output disappeared with it.