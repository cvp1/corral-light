FIX-FIRST for the seam, with the strongest reason: The CLI/doctor race condition drops the lock during execution, allowing concurrent updates to delete the running module's files out from under it.

### Findings

**1. High, PROVEN: Interactive modules drop their execution lock, risking concurrent deletion (modules.py:1176)**  
The `run_interactive` function drops `_Lock(name)` by dedenting `try: return proc.wait()` to outside the `with` block.  
*Concrete failure:* An operator starts `corral-light module <name>` (the CLI). While that interactive session is running, they or another process run `corral-light module update <name>`. Because the lock is free, `update` proceeds and deletes the running module's generation script directory, crashing the running CLI process upon subsequent lazy imports.  
*Fix:* Indent `try: return proc.wait()` and its exception handler to fall tightly inside the `with _Lock(name):` block.

**2. High, PROVEN: Hub deadlock due to `os.listdir` in `bwrap` preexec fork (module_sandbox.py:164)**  
`set_limits` is evaluated via `subprocess.Popen(..., preexec_fn=module_sandbox.set_limits)`, executing `_user_procs` (which calls `os.listdir("/proc")`) in the fork child BEFORE `exec` runs. The core hub uses multithreading for runners and reports; invoking `os.listdir` in a child of a multithreaded process is not async-signal-safe and risks deadlocking on the malloc lock or GIL.  
*Concrete failure:* A collector module launches while a background thread is allocating memory or logging. The fork child hangs forever trying to iterate `/proc`, blocking the module run indefinitely and disrupting the hub's thread.  
*Fix:* Call `procs = _user_procs()` in `run_capped` *before* `Popen`, and pass it to a closure `lambda: _set_rlimits(procs)`.

**3. Medium, PROVEN: Unrecognized quota windows overwrite one another in the feed (module_feed.py:203)**  
`quota_windows()` splits Claude quota entries using `_window_key(name)`. Any `unifiedWindows` keys failing the regex fallback to the static `"_unknown"`. If multiple unrecognized windows come in a single notice, they share the identical `"_unknown"` key.  
*Concrete failure:* The notice provides `unifiedWindows` containing `{"new-window@1": {...}, "new-window@2": {...}}`. Both fall back to `"_unknown"`. The loop overwrites the first entry with the second in `out["_unknown"]`, silently losing the first quota window.  
*Fix:* Return the sanitized raw name or append a hash, e.g. `else "_unknown-" + hashlib.sha1(v.encode()).hexdigest()[:8]`.

**4. Medium, SUSPECTED: Feed leaks user prompts via `title` in pane rows (module_feed.py:125)**  
`_pane_row` exports the session `title` straight into the `panes.json` feed document, intended solely for financial and architectural usage observation.  
*Concrete failure:* An operator uses `corral say "secret internal password"`. The system auto-generates the session title from the first prompt. `panes.json` picks up the title, permanently leaking the prompt text to the finops plugin feed against the "No prompt text" requirement.  
*Fix:* Remove `"title"` from `_pane_row` entirely; pane feeds only need `id` and `model`.

**5. Low, PROVEN: `file_binds` with relative paths trigger a `SandboxError` (module_sandbox.py:101)**  
While currently insulated by `vendor_reports.py` appending absolute `scratch` directories, `build_argv`'s destination validation is rigidly absolute.
*Concrete failure:* If a future module or core feature requests an actual relative path string (`.grok/sessions/...`) for a bind mount using `file_binds`, `build_argv` crashes with `SandboxError` instead of resolving it inside the generated tmpfs root.  
*Fix:* Join relative destinations with `"/"` inside `build_argv`, or document that `dst` must strictly map to an absolute container-side URI.

### TESTS

- **Test that would still pass with its property broken:** `test_real_elf_runs_in_sandbox_and_bad_output_is_stale`. It would fully pass even if the sandbox file bindings completely failed (e.g., throwing a `SandboxError`), because it strictly evaluates that the run tripped a failure and marked the session stale (`ran=1, failed=1, stale=[id]`), which the `SandboxError` fallback path coincidentally satisfies without having actually executed the ELF correctly.  
- **Missing test that matters most:** A concurrency test for `modules.py` that mocks a slow running, interactive `run_interactive` process, intentionally firing off an asynchronous `module update` or `remove` script and asserting that the lock is held (and the update blocks) until `proc.wait()` finishes.

### ONE SENTENCE

Adopt a containerized filesystem layout where a generation directory `gen` is statically bound into a fixed, immutable read-only path (e.g., `/app`) inside the container rather than binding directly aligned host folders, which structurally closes host-side path resolution and TOCTOU directory swap races.