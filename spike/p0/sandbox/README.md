# Phase 0: collector sandbox profile (prototype)

**Verdict: works on this host.** The allowlist profile in
`spike/p0/sandbox/collector_sandbox.py` (`build_argv(argv, read_only_paths, feed_dir, data_dir)`)
enforces everything plan §3 promises: real bwrap 0.12.0, 9/9 tests pass, and
it adds about 5 ms to Python's startup. The plan's grok-usage idea (bind
only the resolved binary file) also works as written. The binary is a
static-pie ELF, so it needs no interpreter or package dir.

Run: `cd spike/p0/sandbox && python3 -m unittest test_collector_sandbox -v`

## Profile

The root is an empty tmpfs, remounted read-only after the binds go in. Inside:
`/usr` is read-only. `/bin /sbin /lib /lib64` are recreated as the host's
symlinks into `/usr` (a real directory would be bound read-only). Seven
non-secret `/etc` entries are bound (ld.so.cache/conf/conf.d, localtime,
passwd, group, nsswitch.conf). Declared read paths and the feed are
read-only, the data dir is writable, and the sandbox gets a fresh `/dev`,
its own `/proc` and a private `/tmp`. Flags: `--unshare-all --unshare-user
--disable-userns` with no `--share-net`, plus `--die-with-parent
--new-session --cap-drop ALL`. `--clearenv` then sets only PATH, HOME
(the data dir) and LANG. The cwd is the data dir. Paths are resolved with
realpath before binding. The function refuses a relative path, a missing
path, `/`, or a read path that overlaps the data dir.

Reused from `review_sandbox.py`: the `BWRAP` setting (`CORRAL_BWRAP`), and
in the tests `SECRET_DIRS`, `SECRET_FILES` and `lane_logins()` as the list
of paths that must be absent. `wrap()` itself is not reused: it starts
from `--ro-bind / /` and per-directory overlays of home.

## Test results

| # | Check | Result |
|---|---|---|
| 1 | `session.key` absent: a fake `<state>/session.key` that sits next to the bound `module-data/finops`, and the real `~/.local/share/corral-light/session.key` and its dir (tested only with `os.path.exists`) | PASS. Only `module-data` and `module-feed` show under the fake state dir |
| 2 | No network: 127.0.0.1:8098 (the live hub, confirmed listening outside), ::1:8098, 1.1.1.1:443 | PASS. Loopback: ECONNREFUSED (the sandbox has its own empty `lo`). External: ENETUNREACH. The only interface is `lo` |
| 3 | Reads a declared path and the feed, writes the data dir; HOME and cwd are the data dir | PASS |
| 4 | Writes to a declared read path (new file and existing file), the feed, `/usr`, `/etc`, `/`, `$HOME` and the state dir all fail | PASS (EROFS). The first run caught `/etc/x` being writable on the tmpfs root; the profile now ends with `--remount-ro /`. The host was never affected |
| 5 | `~/.ssh`, `~/.claude`, `~/.codex`, `~/.grok`, `~/.gemini`, `~/.config`, `~/.gnupg`, `aios/keyvault`, every `review_sandbox` secret dir and file, and every lane login are absent | PASS. `$HOME` contains only `.cache`, the path down to the test fixtures |
| extra | `/proc` shows only the sandbox's pids; no `/run`; `/tmp` is empty; env is exactly HOME, LANG, PATH (+ PWD); no `/etc/shadow` | PASS |
| extra | Bad paths are refused | PASS |
| 6 | python3 runs | PASS |

## Startup timing (`python3 -I -c pass`, median of 10)

| | ms |
|---|---|
| outside | 14.3 to 14.9 |
| inside sandbox | 20.2 to 20.3 |
| overhead | about 5 to 6 |

At `every_s: 300` this cost does not matter.

## grok single-file binding

- `which grok` points to the mise node install, `.../node_modules/@xai-official/grok/bin/grok`.
  `readlink -f` resolves it to `grok-native`, an ELF 64-bit **static-pie**
  binary. It is not a node script; `grok-bootstrap.js` sits next to it but
  is not on the exec path.
- `~/.grok/bin` lists `grok -> grok-1.0.46`, `grok-1.0.41` and `grok-1.0.46`.
  The file names were the only thing looked at. `grok-1.0.46` is the same
  static-pie build as the mise copy (same BuildID).
- Bound alone with `--ro-bind` (the resolved file, under this profile, with
  a scratch HOME): `grok --version` exits 0 and prints `grok 1.0.46 (2765805b9442)`.
  Inside, the binary's dir lists only the binary itself, so `auth.json`
  does not exist there.
- **The minimal binding set is the one resolved file.** No interpreter, no
  package dir and no shared libraries are needed beyond what the profile
  already has. Bind the resolved target rather than the `grok` symlink:
  bwrap follows a source symlink anyway, but resolving first pins the
  version that gets recorded. If xAI ever ships the npm wrapper as a real
  node script, the set grows to the node binary, the package dir and
  `/usr` libraries. The core should check `file`/ELF magic and refuse
  anything else rather than guess.

## Caveats

- This prototype ran only `--version`. The follow-up in docs/finops-phase0.md ran `grok usage <id>` with a session dir bound
  read-only under the scratch HOME was not exercised here (the plan says it
  was tested before). It may also want to write under HOME, which is
  writable scratch.
- No memory, CPU or process limits yet. Plan §4.2 wants them for grok, and
  the collector should have them too (`prlimit`/`setrlimit` in a preexec,
  or a systemd-run scope). Timeouts and kill-by-group live in the hub, not
  in this argv.
- `/etc/passwd` and `group` are bound for libc's sake. They are not
  secret, but they do reveal user names; drop them if nothing needs them.
- Loopback is "refused", not "unreachable", because the sandbox has its
  own `lo`. The hub's listener is not in that namespace.
- The collector still reads everything under its declared paths (Claude
  transcripts include prompts). Confidentiality rests on there being no
  network and the data dir being the only writable place, as §3 says.
  Bound read paths are ro bind mounts, so writes can't go through them,
  but the collector can still see atime and mtime.
- Linux and bwrap only; the unsandboxed path for macOS is unchanged.
- The fixtures live under `~/.cache/collector-sbx-*`, not `/tmp`, and are
  removed after each run.
