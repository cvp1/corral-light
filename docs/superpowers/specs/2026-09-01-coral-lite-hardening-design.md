# Coral Lite Hardening Design

**Date:** 2026-09-01

## Goal

Harden Coral Lite for its deliberately narrow job: local live conversation panes, SSH command lanes, and search over configured notes/text files. Preserve its current permission, isolation, bounded-output, and full-Corral independence guarantees.

## Design

### Lifecycle authority

An intentional pause owns the pane state. The observer and agent-exit paths must not convert an expected process shutdown into `dead`; resume must remain available after teardown completes. A regression test will cover the timing window where the child has exited while the pane is still marked detached.

### Search and attachment routing

Palette request invalidation will happen for every query transition, including clearing and closing. Attachment targets will be recomputed at activation time and will exclude minimized, detached, dead, and SSH host panes. The server will reject direct attachment to SSH panes so a note excerpt cannot become a remote shell command.

### Shared browser state

Minimize, pin, and reorder will publish a state/layout event through the existing SSE broadcaster. Other tabs will apply the event or refresh the authoritative state; single-tab behavior remains unchanged.

### Content bounds

Index refresh will read at most `MAX_FILE` bytes from each configured file, decode with replacement, and retain the existing title/search/excerpt behavior. The test will use a file larger than the limit and assert the stored body is bounded.

### SSH teardown

The reader thread will treat pipe closure during intentional shell teardown as normal EOF and will always publish one EOF marker. The existing kill/reconnect and output-cap behavior remains intact, with a regression test asserting no reader exception escapes during teardown.

### Mobile controls

At narrow widths, Coral Lite will retain reachable New Conversation, Search, and pane navigation controls through compact phone chrome. The permission/needs-you surface remains prominent.

## Error handling and compatibility

All changes are local to Coral Lite. Existing API routes and persisted pane metadata remain compatible. Unsupported SSH interactions continue to be represented as command-runner limitations rather than silently upgraded to a PTY contract.

## Verification

Use test-first regression cases for each backend behavior, static/source assertions for browser behavior where no browser harness exists, then run:

```text
python3 -m unittest test_corral_light -v
node --check static/app.js
PYTHONPYCACHEPREFIX=/tmp/corral-light-pyc python3 -m compileall -q *.py
```
