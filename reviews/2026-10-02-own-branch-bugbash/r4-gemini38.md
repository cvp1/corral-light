FIX FIRST — a failed agent respawn on a dead worktree pane (D14 retry) leaves the pane permanently frozen in `starting`, blocking Resume, Discard, and Forget forever.

## BLOCKING FINDINGS

### 1. High — PROVEN: Failed respawn on a dead worktree pane or during `/clear` wedges the pane in `starting` forever
- **File:line**: [sessions.py:867-874](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/sessions.py#L867-L874) (and [sessions.py:1050-1052](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/sessions.py#L1050-L1052))
- **Trigger**: An own-branch pane whose initial agent start failed (the D14 dead-pane path where the pane stays dead owning its worktree) is resumed by clicking Resume, or a pane receives `/clear`, and `pane.start()` encounters an `OSError` (e.g. `FileNotFoundError` if the agent executable is missing/not in `PATH`, `PermissionError`, or resource exhaustion).
- **What goes wrong**:
  1. `resume()` calls `self.mgr._reserve_live(self)`, transitioning `pane.state` from `"dead"` to `"starting"`.
  2. In `resume()`, the D14 branch (`if not self.acp_session and self.worktree_id:`) calls `self.start()` without an exception handler.
  3. `Pane.start()` only catches `acp.AgentError`. When `AcpClient` raises an `OSError` (such as `FileNotFoundError`), the exception bubbles unhandled out of `resume()`.
  4. The pane is left in `state = "starting"`.
  5. Commit `e8a24eb` added `"starting"` to `_worktree_action`'s busy check ([sessions.py:2515](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/sessions.py#L2515)). Consequently:
     - All review actions (`worktree_discard`, `worktree_commit`, `worktree_publish`, `worktree_snapshot`) refuse with `Refused("busy", "the agent is still working; wait for its turn to end")`.
     - Subsequent calls to `resume()` refuse with `ValueError("pane is starting, not detached or dead")`.
     - Subsequent `/clear` calls refuse with `ValueError("this pane is still starting")`.
     - `forget(keep_branch=False)` refuses with `Refused("own_branch", "open review to Discard it")` — but Discard is refused.
  6. The pane cannot be resumed, discarded, sent to, or forgotten; it is stuck forever until the entire hub process is killed.
- **Minimal fix**:
  In [sessions.py](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/sessions.py#L1230-L1236), make `Pane.start()` handle `Exception` (or `(acp.AgentError, OSError)`) by marking `self._dead(str(e))`, and ensure the D14 block in `_resume()` resets `self.state = "dead"` if `start()` fails:
  ```python
  if not self.acp_session and self.worktree_id:
      self.mgr._reserve_live(self)
      if self._log is None:
          self._log = (self.dir / "events.jsonl").open("a", encoding="utf-8")
      self.error = None
      self._preamble_due = True
      try:
          self.start()
      except Exception as e:
          self.state, self.error = "dead", f"could not start: {e}"
          self.save_meta()
          raise
      return self
  ```
- **Repro path**:
  In `<SEATDIR>/scratch/test_d14_stuck.py`: create a worktree pane where `Pane.start` raises `FileNotFoundError("agent binary not found")`; call `pane.resume()`; observe `pane.state == "starting"`; attempt `pane.resume()`, `mgr.worktree_discard(p.id, None)`, and `mgr.forget(p.id)` — all fail with permanent refusals.

---

## NON-BLOCKING FINDINGS

1. [sessions.py:2436](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/sessions.py#L2436): `_worktree_blocked_reason` synchronously runs three git subprocesses (`worktree list`, `symbolic-ref`, and `rev-parse`) on every user keystroke, dispatch, and resume; while ~15ms locally, high load or networked disks will add noticeable dispatch latency.
2. [sessions.py:2407](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/sessions.py#L2407): An entry left in `phase="intent"` (e.g. process killed during `git worktree add`) returns `None` from `_worktree_blocked_reason` until restart because its `ops` list is empty and only `active`/`trashed`/`missing`/`tampered`/`purged` are handled.
3. [worktrees.py:1762](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/worktrees.py#L1762): Discard's preflight settle check can intentionally refuse once with `Refused("changed")` while an agent is actively modifying files (T-RMV-11 design choice), requiring the user to trigger Discard a second time.
4. [worktrees.py:1712](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/worktrees.py#L1712): Non-dumpable processes under `/proc` (where `cwd` and `fd` cannot be inspected due to `EACCES`) are skipped by the discard process scan rather than failing closed.
5. [worktrees.py:814](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/worktrees.py#L814): Repositories located on a different filesystem mount from `CORRAL_LIGHT_WORKTREES` are permitted without warning, relying on `git worktree move` remaining within the worktree root.
6. [worktrees.py:1199](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/worktrees.py#L1199): `fit_review` trims `diff["files"]`, `too_big`, and `ignored["sample"]`, but does not bound top-level commit metadata or diff headers if an extreme number of files are modified.
7. [worktrees_cli.py:182](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/worktrees_cli.py#L182): The CLI recovery hint for interrupted commits (`git reset -q`) drops index-only staged changes if the user runs it without checking the diff.
8. [worktrees.py:1712](file:///home/USER/.cache/corral-bugbash-2026-10-03-r4/gemini38/corral-light/worktrees.py#L1712): The process scan iterates through all `/proc` PIDs linearly on Linux during discard preflight, taking 50–100ms on hosts running many background containers or processes.

---

## FIRST WEEK PREDICTION

If an agent binary is temporarily missing or misconfigured in `PATH`, clicking Resume on the failed pane locks it permanently in `starting`, preventing the operator from either restarting the agent or discarding the branch without terminating the hub.
