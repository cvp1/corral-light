# Coral Lite Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed Coral Lite lifecycle, search, attachment, synchronization, bounds, SSH teardown, and mobile usability bugs without expanding Coral Lite into full Corral.

**Architecture:** Keep the existing Manager/SSE and browser palette architecture. Add narrowly-scoped lifecycle guards and layout events, make attachment routing lane-aware at both server and client boundaries, and preserve the existing command-runner contract for SSH.

**Tech Stack:** Python 3.9+, `unittest`, stdlib HTTP/SSE server, vanilla JavaScript/CSS, SQLite FTS5.

---

### Task 1: Protect intentional pause state

**Files:**
- Modify: `sessions.py:2083-2118`
- Test: `test_corral_light.py` (new lifecycle regression class near the session tests)

- [ ] **Step 1: Write the failing test**

Create a minimal pane-shaped object with a detached state, an expected exit, and a client whose process has already exited. Assert `Pane.snapshot()` reports `detached` and does not emit a dead transition.

```python
def test_expected_pause_exit_stays_detached(self):
    pane = sessions.Pane.__new__(sessions.Pane)
    pane.client = types.SimpleNamespace(
        alive=True, p=types.SimpleNamespace(poll=lambda: 0))
    pane.state = "detached"
    pane._expect_exit = True
    pane.last_activity = time.time()
    pane.events = []
    pane._seq = 0
    pane._lock = threading.Lock()
    pane._replaying = False
    pane._log = None
    result = pane.snapshot()
    self.assertEqual(result["state"], "detached")
    self.assertFalse(any(e["kind"] == "state" for e in pane.events))
```

- [ ] **Step 2: Run the regression test and verify it fails**

Run: `python3 -m unittest test_corral_light.IntentionalPauseLifecycle -v`

Expected: FAIL because `snapshot()` currently assigns `state_override = "dead"` when `poll()` is non-`None`.

- [ ] **Step 3: Implement the minimal lifecycle guard**

In `snapshot()`, only set the process-exit override when the pane is not detached and the shutdown is not expected:

```python
if self.client.p.poll() is not None:
    alive = False
    if self.state != "detached" and not self._expect_exit:
        state_override = "dead"
```

Keep the existing final detached-state exemption and pause/resume transitions unchanged.

- [ ] **Step 4: Run the focused test and the full suite**

Run: `python3 -m unittest test_corral_light.IntentionalPauseLifecycle -v`

Then run: `python3 -m unittest test_corral_light -v`

Expected: the new test and all existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add sessions.py test_corral_light.py
git commit -m "fix: preserve paused Coral Lite panes"
```

### Task 2: Make search attachment lane-aware and race-free

**Files:**
- Modify: `hub.py:353-373`, `static/app.js:2106-2252`
- Test: `test_corral_light.py` (`AttachSemantics`, plus static behavior assertions)

- [ ] **Step 1: Write failing backend and source-contract tests**

Add a test that identifies SSH agents as non-attachable targets and asserts the hub refuses them before returning an excerpt. Add source assertions for detached/dead/minimized/SSH filtering and query invalidation before the short-query return.

```python
def test_ssh_target_is_rejected_for_content_attachment(self):
    self.assertIn("host:", sessions.AGENTS)
    hub = (ROOT / "hub.py").read_text(encoding="utf-8")
    self.assertIn("SSH panes cannot receive note attachments", hub)
```

- [ ] **Step 2: Run the focused tests and verify the new contract assertions fail**

Run: `python3 -m unittest test_corral_light.AttachSemantics -v`

Expected: FAIL because the server currently treats every no-tools pane as an excerpt target and the browser target filter omits SSH/detached states.

- [ ] **Step 3: Implement server-side SSH refusal**

After resolving `pane` in `/api/content/attach`, reject host lanes before deciding between path reference and excerpt:

```python
if pane and pane.agent.startswith("host:"):
    raise ValueError("SSH panes cannot receive note attachments")
```

- [ ] **Step 4: Implement client-side target filtering and recomputation**

Update `attachTarget()` to reject `detached` and `agent.startsWith('host:')`. In `activatePalette()`, compute `attachTarget()` immediately before calling `attachContent()` rather than relying on the target captured during debounce. Keep Shift+Enter’s explicit new-pane behavior.

- [ ] **Step 5: Invalidate palette requests on every query transition**

At the start of `paletteResults()`, increment `PAL.seq` and clear `PAL.t` before rendering local rows. Return for short queries only after invalidation. Keep the response guard `if (seq !== PAL.seq) return`.

- [ ] **Step 6: Run focused tests and full suite**

Run: `python3 -m unittest test_corral_light.AttachSemantics -v`

Then: `python3 -m unittest test_corral_light -v`

- [ ] **Step 7: Commit**

```bash
git add hub.py static/app.js test_corral_light.py
git commit -m "fix: route Coral Lite search attachments safely"
```

### Task 3: Broadcast shared layout changes across tabs

**Files:**
- Modify: `sessions.py:1993-2003`, `sessions.py:2540-2566`, `static/app.js:1780-1860`
- Test: `test_corral_light.py` (Manager layout event tests and source contract)

- [ ] **Step 1: Write the failing event tests**

Use a test Manager with a subscriber queue and a pane stub. Assert minimize, pin, and reorder enqueue a layout event containing the affected pane’s authoritative layout fields.

```python
def test_minimize_broadcasts_authoritative_layout(self):
    q = queue.Queue()
    manager.subscribe(q)
    manager.get(pane_id).set_minimized(True)
    event = q.get_nowait()
    self.assertEqual(event["kind"], "layout")
    self.assertTrue(event["data"]["minimized"])
```

- [ ] **Step 2: Run and verify the tests fail**

Run the new layout test class. Expected: queue is empty because the current methods only save metadata.

- [ ] **Step 3: Implement layout event publication**

Add a Manager helper that broadcasts a non-transcript layout event with the pane ID and current `minimized`, `pinned`, and `order` values. Call it after each successful layout mutation. Do not use `Pane.emit()` so layout changes do not consume transcript-ring capacity.

- [ ] **Step 4: Apply layout events in the browser**

In the SSE handler, process `layout` before the normal unknown-pane fallback. Update the matching pane’s layout fields, rebuild the pane map order if necessary, and call `render()`.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m unittest test_corral_light -v`

```bash
git add sessions.py static/app.js test_corral_light.py
git commit -m "fix: synchronize Coral Lite layout across tabs"
```

### Task 4: Enforce content read bounds and quiet SSH teardown

**Files:**
- Modify: `content.py:169-220`, `ssh_acp.py:109-140`
- Test: `test_corral_light.py` (`ContentIndex`, `SshShellIsBounded`)

- [ ] **Step 1: Write failing bounds and reader tests**

Create a file larger than `MAX_FILE` and assert the indexed body is no longer than the configured limit. Feed `_reader()` a fake stdout that raises `ValueError` after closure and assert it places exactly one EOF marker instead of raising.

```python
def test_index_reads_only_the_configured_file_bound(self):
    large = "x" * (self.content.MAX_FILE + 1000)
    (self.notes / "large.txt").write_text(large)
    self.content.refresh(force=True)
    row = self.content.get("notes:large.txt")
    self.assertLessEqual(len(row["body"]), self.content.MAX_FILE)
```

- [ ] **Step 2: Run and verify both tests fail**

Run the two focused tests. Expected: the content body is full-sized and the fake reader raises on the closed pipe.

- [ ] **Step 3: Implement bounded file reads**

Open each changed file in binary mode, read `MAX_FILE` bytes plus one byte to determine clipping, decode with `errors="replace"`, and store only the bounded decoded text. Preserve file metadata from `stat()`.

- [ ] **Step 4: Implement exception-safe SSH reader shutdown**

Wrap `readline()` in `try/except (OSError, ValueError)`, treat those exceptions as EOF, and put the sentinel in a `finally` block. Ensure only the reader owns the sentinel and that normal EOF remains unchanged.

- [ ] **Step 5: Run focused and full tests, then commit**

Run: `python3 -m unittest test_corral_light.ContentIndex test_corral_light.SshShellIsBounded -v`

Then: `python3 -m unittest test_corral_light -v`

```bash
git add content.py ssh_acp.py test_corral_light.py
git commit -m "fix: bound Coral Lite content reads and SSH teardown"
```

### Task 5: Restore mobile entry points

**Files:**
- Modify: `static/style.css`, optionally `static/index.html` and `static/app.js` if compact controls need a wrapper
- Test: `test_corral_light.py` (`AttachSemantics` or a new `MobileSurface` source-contract class)

- [ ] **Step 1: Write the failing mobile source-contract test**

Assert that the narrow-screen rules retain visible New Conversation and Search controls and provide a visible pane-navigation surface.

```python
def test_mobile_keeps_new_search_and_navigation_controls(self):
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    self.assertIn("mobile-actions", css)
    self.assertIn("#new", css)
    self.assertIn("#search-trigger", css)
```

- [ ] **Step 2: Run and verify the test fails**

Run: `python3 -m unittest test_corral_light.MobileSurface -v`

Expected: FAIL because the current mobile rules hide the left-rail controls and roster.

- [ ] **Step 3: Implement compact phone chrome**

Add a small mobile action wrapper or make the existing controls fixed/visible under 820px. Keep the needs-you rail accessible. Ensure controls have labels/ARIA text and do not overlap the status bar or transcript.

- [ ] **Step 4: Run the mobile contract and full suite**

Run: `python3 -m unittest test_corral_light.MobileSurface -v`

Then: `python3 -m unittest test_corral_light -v`

- [ ] **Step 5: Commit**

```bash
git add static/style.css static/index.html static/app.js test_corral_light.py
git commit -m "fix: keep Coral Lite controls reachable on mobile"
```

### Task 6: Final verification and diff review

**Files:**
- Review: all modified files and commits from Tasks 1–5

- [ ] **Step 1: Run the complete verification set**

```bash
python3 -m unittest test_corral_light -v
node --check static/app.js
PYTHONPYCACHEPREFIX=/tmp/corral-light-pyc python3 -m compileall -q *.py
git diff --check master...HEAD
```

- [ ] **Step 2: Confirm no unintended files or scope expansion**

Run: `git status --short --branch` and verify only the design, plan, tests, and the targeted Coral Lite files changed. Confirm no full-Corral imports or routes were introduced.

- [ ] **Step 3: Review the final diff and report evidence**

Summarize the test count, any remaining warnings, branch/worktree path, and the exact behavior still intentionally unsupported (SSH PTY/interactive programs).
