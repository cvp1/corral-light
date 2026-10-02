**1. Cross-examination**

Labels below distinguish **Git knowledge**, **design judgment**, and **unverified adapter behavior**.

- **Gemini — strongest agreement:** Stop writing snapshot objects on summary ticks. Snapshot-on-open preserves immutable review without continuous object churn. **Wrong claim:** `safe.directory=*` is an acceptable ownership fix. It disables the ownership check globally. Also, unchanged blobs are deduplicated: twenty snapshots of the same 10 MiB file do not inherently create 200 MiB.
- **Grok — strongest agreement:** Start snapshots from the worktree’s actual index so force-added ignored files enter review. **Wrong design:** Unconditionally running `read-tree HEAD` afterward safely fixes the real index. It can erase newly staged content, including content no longer present in working files. The hub’s Python lock does not exclude external Git.
- **My revisions:** I support plumbing commits instead of my earlier hook-and-re-review approach, provided index reconciliation becomes recoverable. Review UI moves from **RESHAPE** to **BUILD**, with explicit incomplete-review warnings. Lifecycle and tests still need reshaping. Claims about exact lane trust, sandbox and subprocess behavior remain **unverified** until measured.

**2. Re-verdict**

- **(a) Approach — BUILD:** The reduced scope and recoverable disposal are sound.
- **(b) `worktrees.py` — RESHAPE:** Index reconciliation, snapshot retention and publication destinations remain unsafe.
- **(c) Lifecycle — RESHAPE:** Queue draining and detached subprocesses undermine disposal.
- **(d) Review UI — BUILD:** Show omitted content and never retry an action automatically after refreshing.
- **(e) Test plan — RESHAPE:** Several contradictory assertions can reward unsafe implementations.

**3. Remaining lost-work paths, ranked**

These are **design judgments grounded in Git behavior**.

1. **New: post-commit index overwrite.** Trigger: external Git stages content after review; CAS succeeds; `read-tree <new>` replaces that index. A crash between CAS and reconciliation also leaves an unfinished commit operation. **Fix:** Acquire the actual index lock, validate the captured index identity, preserve its bytes and staged objects durably, prepare the replacement index, and journal ref advancement separately from index installation. If identity differs, preserve it and report reconciliation required.

2. **New: stale or vanished snapshot.** Trigger: a background edit preserves filenames and numstat totals; summary still “agrees.” Separately, aggressive GC can delete an unreferenced reviewed tree. **Fix:** Counts never validate content. Rebuild and compare the proposed tree for actions requiring freshness; pin open snapshots and prepared commits under recovery refs until explicitly retired. Exact-tree commits remain safe against later file writes; promising universally stale-write refusal does not.

3. **New: disposal resumes a writer.** Trigger: successful Discard reaches the universal “clear held and drain queue” path, dispatching messages to an archived owner. A detached `bg-write` child can also survive process-group termination. **Fix:** Successful Discard leaves dispatch disabled; queued messages remain visibly retained for explicit restore/resume. Supervise descendants with a containment mechanism that survives process-group escape, or refuse disposal when writer termination cannot be established. Journal move and restore source/destination registrations.

4. **New: journal guesses completion.** Trigger: the ref advances, then another writer advances or resets it before restart. Its current value alone cannot reconstruct the operation. Concurrent CLI and hub writers can also overwrite registry updates. **Fix:** Persist operation IDs, prepared OIDs, stages and recovery refs; serialize registry mutations across processes. Preserve “unknown” when evidence is insufficient, and require resolution before resume or retry.

5. **Publication reaches a different destination.** Trigger: the shown fetch URL differs from `pushurl`, multiple push URLs exist, URL rewriting applies, or configuration changes after validation. **Fix:** Confirm the effective push destination, reject multiple destinations in v1, push using the pinned destination, and verify that same endpoint.

6. **New: pre-trust expands execution authority.** Trigger: trusting the cloned path permits project-controlled commands beyond what Craig understood “Own branch” to authorize. Cancellation after an edit cannot restore overwritten external files. **Fix:** Explain the trust grant in that opt-in, preserve the pane’s command approvals, scope trust to the exact canonical path, and refuse any adapter whose trust behavior remains unverified. A passing ordinary-task matrix proves compatibility, not confinement.

**4. Git fact check**

All items here are **Git knowledge**.

1. **Invalid configuration placement:** Use `git -c diff.renameLimit=1000 diff-tree …`; the shown `diff-tree … -c diff.renameLimit=1000` does not set global configuration. `diff-tree -c` means combined diff.
2. **`git diff <commit>`:** Correctly compares working content against that commit, using the index for trackedness. It does not inventory staged-only content or provide a content digest. Disable external diff/textconv on summary too.
3. **Hooks:** `commit-tree` skips porcelain commit hooks, but `update-ref` can invoke `reference-transaction` hooks. Pre-commit and commit-msg hooks do not run retroactively when that commit is pushed or merged.
4. **Filesystem restriction:** Linked worktrees may reside on another filesystem from the repository. For trash rename, the relevant requirement is that the worktree directory and trash destination share a filesystem. Ignored files move with the directory; forced removal deletes them.
5. **CAS scope:** `update-ref <ref> <new> <old>` checks that ref’s old value. It does not atomically validate symbolic HEAD, the index, working files or registry.
6. **Remote URL:** Checking a remote’s ordinary URL does not establish its effective push destination. Inspect push URLs and rewriting.

The stated `merge-tree` rc 0/1 handling is correct. Its NUL output contains sections and structured messages; parse section boundaries, not every NUL field as a filename.

**5. §7 recommendations**

**Q1 — Location.** Keep the configurable state-directory default. Sibling placement does not generally preserve project-relative assumptions. Remove the requirement that the repository share its filesystem; require the worktree and trash to share one. If the default cannot satisfy that, offer a configured root with a clear reason.

**Q2 — Commit.** Keep exact-tree plumbing commits with prominent disclosure that commit validation hooks are skipped. Hooks can be an explicit validation step before a fresh snapshot, followed by renewed review if content changes. Fix index reconciliation before shipping; an unconditional `read-tree` is unacceptable.

**Q3 — Snapshot.** Yes, snapshot-on-open is worth the object writes. It gives actions an immutable target. Pin it while actionable, bound snapshot resources, preserve excluded-content warnings, and make retirement explicit. Ordinary GC is housekeeping, not the retention contract.

**Q4 — Integration.** Copy merge command plus Push & PR is enough for v1. Conflicts can be resolved through an explicit user-directed agent turn in its worktree, then reviewed again. A separate automated sync action adds another mutation protocol without being necessary for launch.

**6. Ship gate**

**§5.4 is insufficient. Add three requirements:**

1. Demonstrate recoverable ref/index reconciliation and pinned snapshots, including concurrent external staging and crashes at every reconciliation boundary.
2. Demonstrate discard/restore with detached writers, queued sends and interrupted moves; nothing dispatches while trashed or unresolved.
3. Reconcile contradictory tests before counting them green: T-LIF-5 versus T-LIF-16; T-RMV-11 versus detached-child supervision; T-SAFE-5’s main-checkout assertion versus intentional shared Git administration. Require passing minimum-version fixtures and effective-push-destination checks.

**7. Craig’s bottom line**

Build v2 after fixing index reconciliation and disposal dispatch; its new recovery machinery must preserve Craig’s staged work and stop writers before moving their checkout.