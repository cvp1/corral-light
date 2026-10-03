FIX FIRST — a repository-scoped `url.<base>.pushInsteadOf` whose target `<base>` contains a space bypasses `rewrite_rule`, causing Publish to push commits to an unconfirmed destination while leaving the pane in an unsettled `unknown` state.

## BLOCKING FINDINGS

### 1. High — PROVEN: Spaces in `url.<base>.pushInsteadOf` bypass destination verification
- **Severity:** High
- **Status:** PROVEN
- **File:line:** [`worktrees.py:1411-1417`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r7/gemini38/corral-light/worktrees.py#L1411-L1417) (`rewrite_rule`), called from [`worktrees.py:1495`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r7/gemini38/corral-light/worktrees.py#L1495) (`push`).
- **Trigger:** A repository-scoped git config sets `url.<base>.pushInsteadOf = <confirmed_url>` where `<base>` contains whitespace (e.g. `url.https://attacker.invalid/repo path/.pushInsteadOf`), and the remote has an explicit push URL configured (`remote.<name>.pushurl`).
- **What goes wrong:** 
  1. `push_urls(entry, remote)` queries `git remote get-url --push -- <remote>`. Git's documentation explicitly specifies: *"If a remote has an explicit pushurl, Git will ignore this setting [pushInsteadOf] for that remote."* Therefore, `push_urls` returns the confirmed URL, satisfying `urls[0] == push_url`.
  2. `rewrite_rule` executes `git config --get-regexp "^url\..*\.(insteadof|pushinsteadof)$"`. Without `--null`, git outputs `<key> <prefix>`. Because the key contains a space within `<base>`, `line.partition(" ")` splits at the first space inside the key name. `prefix` is misparsed as `"path/.pushinsteadof <confirmed_url>"`, causing `url.startswith(prefix)` to evaluate to `False`.
  3. `git ls-remote --get-url -- <url>` only applies `insteadOf`, never `pushInsteadOf`, returning the confirmed URL unaltered. `rewrite_rule` returns `None`.
  4. `transport_override` checks only `core.sshcommand`, `core.gitproxy`, `http.*`, and `ssh.*`, so it returns `None`.
  5. `push()` executes `git push --porcelain -- <push_url> <oid>:<ref>`. Because git push is invoked with a literal URL rather than a named remote with an explicit pushurl, git applies `pushInsteadOf` and pushes the branch and commits to `<base>`.
  6. The subsequent `git ls-remote -- <push_url> <ref>` checks the confirmed repository, where the ref does not exist. The op is journalled as `unknown` and `Refused("unknown")` is raised, but the commits have already been pushed to the unconfirmed remote.
- **Minimal fix:** In `rewrite_rule`, query git config with `--null` and split on `\0` and `\n` so spaces in key subsection names do not corrupt parsing. Additionally, include `url\..+` in `_TRANSPORT_KEYS` in [`worktrees.py:1431`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r7/gemini38/corral-light/worktrees.py#L1431) so repository-scoped URL rewrite rules are rejected by `transport_override`.
- **Repro path:** [`/home/cvande/.cache/corral-bugbash-2026-10-03-r7/gemini38/scratch/repro_pushinsteadof_space.py`](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r7/gemini38/scratch/repro_pushinsteadof_space.py).

---

## NON-BLOCKING

1. All 325 tests in the test suite pass cleanly (98.5s).
2. The Round 6 `http.curloptResolve` blocker holds: `transport_override` successfully catches URL-scoped and host-scoped `http.*` and `ssh.*` settings.
3. The generation-bound drain, parked peer message notifications, and branch switch lockouts hold under test.
4. `transport_override` fails closed when git config output exceeds 8 MiB (`r.truncated`).
5. Legitimate repository-scoped `http.*` settings (e.g. `http.postBuffer`) refuse Publish until moved to global config, as intended.
6. A remote with multiple push URLs refuses Publish with `remote_changed`.
7. `remote.<name>.proxy` does not affect pushes to literal URLs.
8. Process scans in Discard continue to ignore processes with unreadable `/proc` cwd and fds.

---

## ONE SENTENCE

Craig will review and confirm a push to his team's repository, only to find the operation marked `unknown` while his branch was silently pushed to an attacker-controlled remote path rewritten by an agent's git config.
