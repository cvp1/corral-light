# Round three: quick code review of the module seam (2026-10-07)

Arms, cold, a read-only clone of `modules-seam` at 31d576c, via
`corral-light consult ask` (charge: `charge-r3-seam.md`):

| Arm | Model | Verdict | File |
|---|---|---|---|
| Codex | gpt-6-astra (read-only mode) | FIX-FIRST | r3-astra.md |
| Grok | grok-4.7 | FIX-FIRST | r3-grok.md |
| Gemini | gemini-pro-agent | FIX-FIRST | r3-gemini.md |

Each arm found the snapshot-to-page path clean (Grok said so explicitly:
no route from snapshot text to markup, class, style or link).

## Converged, checked by the author against the code

1. **Prompt text reaches the feed through pane titles (all three).**
   Checked: `sessions.py` names an untitled pane after its first 42
   characters of prompt; `module_feed._pane_row` copies `title`. The
   feed test passed because it never drove the real title path. Fix:
   publish a title only when the operator set it (`title_locked`),
   otherwise the lane label; add a test through the real prompt path.
2. **Interactive runs drop the module lock before the process ends (all
   three).** Checked: `run_interactive` leaves `with _Lock` after
   `Popen`. A long `setup` can have its generation pruned by two updates.
   Fix: hold the lock until `wait()` returns.
3. **Verify, then exec by path, is a time-of-check gap (Astra, Grok;
   Gemini's closing sentence points the same way).** A same-user writer
   can swap `collector.py` between the digest and the exec. The sandbox
   stops the collector itself; other processes are the gap. Fix: copy
   the generation into a private run tree, verify the copy, bind it at
   a fixed path inside the sandbox, run that, delete it.
4. **Unsandboxed timeout leaves `setsid` descendants alive (Astra,
   Grok).** True by construction: only the sandboxed path has a pid
   namespace. Astra adds that closing a buffered pipe can block behind a
   pump thread still reading a stray child's pipe. Fix: a single
   deadline-driven `selectors` loop on raw descriptors; on hosts with no
   sandbox, the acknowledgement text says processes it starts may
   outlive a timeout, and a test pins that wording to the behaviour.

## Single-arm findings, checked

5. **Quota: a newer notice replaces the whole window (Grok).** Checked:
   `note_quota` assigns the new observation; a `rejected` notice with no
   `utilization` erases the last figure. Fix: merge per field; a carried
   field records the time it was observed.
6. **Quota: unknown window names collide on `_unknown` (Gemini).**
   Checked: `_window_key`. Fix: keep a sanitised form of the vendor's
   own name.
7. **Pins file lost update across processes (Astra).** `_PINS_LOCK` is
   per process; the hub (which disables on tamper) and a CLI both
   rewrite `modules.json`. Fix: an interprocess lock file around every
   read-modify-write.
8. **Stale pin in racing updates (Astra).** `update` reads the pin before
   staging and writes it after the lock. Fix: re-read inside the lock
   and refuse if the starting commit moved.
9. **`.git` is skipped by runtime verification (Astra).** Install
   removes `.git`, but verify ignores one that appears later. Fix:
   runtime verification refuses any `.git`; the run-copy (3) also
   closes it.
10. **`sitecustomize.pyc` and `.so` pass the install check (Grok).**
    Fix: refuse any file whose stem is `sitecustomize` or
    `usercustomize`.
11. **`os.listdir` inside `preexec_fn` in a threaded hub (Gemini).**
    Python documents `preexec_fn` as unsafe with threads. Fix: count
    the user's threads before `Popen`; the child only calls `setrlimit`.

## Not adopted

- Gemini 5, relative `file_binds` destinations: refused by design; the
  error is the intended behaviour.
- Gemini's test claim on `test_real_elf_runs_in_sandbox_and_bad_output_is_stale`
  is fair as far as it goes: a sandbox build failure also yields
  "stale". Adopted as a small strengthening (assert the ELF actually
  executed), not as a defect.

## Applied (2026-10-07)

All eleven, on `modules-seam`, each with a test that failed before the
fix (findings 2, 3, 7 to 11 failed on behaviour; 1, 5, 6 on the feed
output; 4 on the wording first, then on behaviour against the old
reader-thread loop, which held a run for 60 s on an escaped child).

| # | Fix | Test |
|---|---|---|
| 1 | The feed publishes a title only when the operator typed it (`title_named`, set by rename, kept in pane meta); otherwise the lane label. Stricter than the finding: a port copies and locks a prompt-derived title, so `title_locked` alone leaks | `PaneTitles` (real prompt path, live and meta-only, port-style lock, then a rename) |
| 2 | `run_interactive` holds the module lock until `wait()` returns | `Locks.test_an_interactive_run_holds_the_lock_until_it_exits` |
| 3 | Each run copies the generation into `.run-<random>`, verifies the copy, runs it (bound read-only at `/module` in the sandbox), deletes it; the collector path uses the copy's manifest, so there is one check | `Tamper.test_a_swap_after_the_check_does_not_run`, `test_the_code_sits_at_a_fixed_path_inside_the_sandbox` |
| 4 | `run_capped` is one `selectors` loop on raw descriptors with one deadline; the group is killed before the leader is reaped. `module add` on an unsandboxed host says "processes it starts may outlive a timeout" | `Unsandboxed.test_a_timeout_returns_on_time_and_the_ack_names_survivors` |
| 5 | Quota observations merge per field; a carried field records its own `observed_at` in `carried`. Applied to the feed and to the pane's own copy | `QuotaMerge.test_a_later_notice_without_a_figure_keeps_the_last_one_and_says_when` |
| 6 | An odd window name keeps a sanitised form plus a short hash; only a missing name is `_unknown` | `QuotaMerge.test_unknown_window_names_keep_their_own_keys` |
| 7 | `pins_lock()`: a lock file under the config dir around every pins read-modify-write | `Locks.test_pin_writes_wait_for_another_process` |
| 8 | Update and rollback re-read the pin under the module lock and refuse if it moved | `UpdateAndRollback.test_a_racing_update_is_refused_not_overwritten` |
| 9 | `scan_tree` refuses any `.git`, so runtime verification does too | `Tamper.test_a_git_dir_that_appears_later_is_refused` |
| 10 | Refused by stem: `sitecustomize` or `usercustomize` in any form | `InstallAndPin.test_compiled_site_hooks_are_refused` |
| 11 | `module_sandbox.limits_fn()` counts tasks and reads limits in the parent; the child only calls `setrlimit`. `vendor_reports` uses it | `Limits.test_the_child_side_only_calls_setrlimit` |

Also adopted: the real-ELF vendor-report test now asserts the binary ran
and exited 0 (stale for its output, not for a sandbox failure).

Residual, by design: the run copy narrows finding 3 to a fresh random
path that exists only for one run; a same-user process that finds it in
that window can still change it, as it could ptrace the hub. The sandbox
remains the boundary for the collector itself.
