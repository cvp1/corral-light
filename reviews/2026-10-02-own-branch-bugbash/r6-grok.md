I'll review the round-5 fixes against the ship bar, starting with the synthesis, the spec, and the commit that claims those fixes.The round-5 diff is small. I'll read the drain, push checks, and the new tests before trying to break them.`http.curloptResolve` is not in the transport denylist. I'll check whether a repo-local setting can send a confirmed push somewhere else.`http.curloptResolve` sends `confirmed.invalid` to loopback. Next I’ll run that through `push()` and check whether Publish still reports the confirmed URL.FIX FIRST — a repo-local `http.curloptResolve` sends a confirmed `http://` push to another address, and Publish reports success for the URL the user confirmed.

## BLOCKING FINDINGS

### 1. High — PROVEN: `http.curloptResolve` bypasses the transport denylist
- **File:line:** `worktrees.py:1427` (`_TRANSPORT_KEYS`), matched in `transport_override` at `1449`, called from `push` at `1493`.
- **Trigger:** The confirmed URL is `http://host:port/repo.git`, and the repository config sets `http.curloptResolve` to `host:port:other-address`. The same value under `http.http://host:port/.curloptResolve` or `http.http://host:port/repo.git.curloptResolve` is the same hole. The denylist matches none of those three keys. Scope in the repro was `local`; the check uses the same scope set as `core.sshCommand` (`local`, `worktree`, `command`).
- **What goes wrong:** `transport_override` and `rewrite_rule` both return None. `wt.push` returns `{"pushed": "e6b20864622909cb61fe779b88f673956c93a59e", "url": "http://confirmed.invalid:40283/repo.git"}`. That host does not resolve. The commit is stored on `127.0.0.2`, the address in the config. The follow-up `ls-remote` of the confirmed URL uses the same override, so the "remote shows the commit" check passes.
- **Minimal fix:** Treat every key matching `http\..*curloptresolve` as a transport override.
- **Repro:** `scratch/probe_r6_curlopt.py` (full `wt.push`). A second spelling alone, `http.http://confirmed.invalid:40855/repo.git.curloptResolve`, made `git push` of that URL create `refs/heads/scoped-proof` (`8e8cb28fa7575cf98b5d7369d73d919bd96485ae`) in the server repo. On `https://` the same key selects the address, and the push completes when certificate verification accepts that address. A repo-local `http.sslVerify=false` is already refused, so the success above is the `http://` case (any port the agent can bind, including 8080).

## NON-BLOCKING

1. The round-5 checks held: the generation-bound drain, newline-split remote names, and repo-local `core.sshCommand` including through `include.path` (scope comes back `local`). Isolated suite: 325 tests, OK, 101s.
2. `rewrite_rule` ignores a truncated `insteadOf` listing; `transport_override` then reads the whole config with the same 8 MiB cap and refuses when that read is truncated, so a padded `pushInsteadOf` fails closed.
3. `http.followRedirects` is outside the denylist. The default is already `initial`, which follows the first redirect and uses that URL for the pack; the key names no destination of its own.
4. `remote.<name>.proxy` is used for `git ls-remote origin` and unused for a URL argument. `push()` passes the confirmed URL; a proxy of `127.0.0.1:1` failed the name form while the URL form still listed the server.
5. Global and system git config, the hub environment, and `~/.ssh/config` can still redirect a push. That boundary is unchanged.
6. An agent that switches branches blocks the pane, Discard included, until the branch is switched back.
7. Discard still ignores a process whose `/proc` cwd and fds are both unreadable, and its settle check can refuse once while files are still changing.
8. `BugBash6Publish` covers `core.sshCommand`, `core.gitProxy`, `http.proxy`, and one `sslVerify` spelling. It never sets `http.curloptResolve`.

The first Publish to an `http://` remote can report the URL the operator confirmed while the commit is stored at the address an agent wrote into `http.curloptResolve`, a line the review diff never shows.
