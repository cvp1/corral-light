# A module sandbox on macOS (Phase 4)

> Status: BUILT 2026-10-08 and proven on a Mac running macOS 27.0.1
> (Apple silicon, Python 3.9.6 from the Command Line Tools). §6 records
> what was measured and where the build differs from the design below,
> which was written first from the cited sources.

## 1. The question

On Linux a module's collector, CLI and doctor run under bubblewrap: an
empty root, `/usr` and a few `/etc` files read-only, a fixed list of read
paths, one writable data dir, no network (its own network namespace), no
view of `$HOME` beyond what is listed, resource limits, and a kill by
process group (`module_sandbox.py`). A fetcher runs in the same profile
with egress only through the hub's CONNECT proxy, reached over a bound
unix socket, to its vendor's exact hosts (`module_fetch.py`).

On macOS there is no sandbox today. A module runs only after the operator
types `unsandboxed` (plan §9.1), and fetchers do not run at all. What can
a stdlib Python program, unsigned and outside the App Store, use to give
a child process the same confinement on current macOS?

## 2. Options

| Option | Fits a CLI hub? | Notes |
|---|---|---|
| **Seatbelt profile via `/usr/bin/sandbox-exec`** | Yes | Deprecated in its man page and "not API" per Apple DTS, but shipped and working through macOS 26; used today by Codex CLI and Claude Code |
| App Sandbox entitlements | No | Cannot be enabled on a standalone command-line tool; needs an app bundle and container |
| Endpoint Security descendants client (macOS 27) | No | Needs an entitlement requested from Apple and a bundle; no auth event for TCP `connect` |
| A Linux VM (Apple `container`, Lima) | Opt-in later | Reuses the bubblewrap profile unchanged; costs a VM, an install with an admin password, macOS 26 and Apple silicon for `container` |

Sources: sandbox-exec(1) man page (https://leancrew.com/all-this/man/man1/sandbox-exec.html);
Apple DTS on SBPL (https://developer.apple.com/forums/thread/777509,
https://developer.apple.com/forums/thread/124284); App Sandbox for CLIs
(https://developer.apple.com/forums/thread/687532,
https://developer.apple.com/forums/thread/722044); ES descendants client
(https://developer.apple.com/forums/thread/832204,
https://developer.apple.com/tutorials/data/documentation/endpointsecurity/es_event_type_t.md);
Apple container (https://github.com/apple/container); Lima
(https://lima-vm.io/docs/config/vmtype/).

### How others use Seatbelt today

- **Codex CLI** runs only `/usr/bin/sandbox-exec` (never one found on
  PATH), starts from `(deny default)`, passes every path as a `-D`
  parameter rather than splicing strings, and allows network only to a
  local proxy port or listed unix-socket paths; with no proxy it allows no
  network
  (https://github.com/openai/codex/blob/main/codex-rs/sandboxing/src/seatbelt_base_policy.sbpl,
  https://github.com/openai/codex/blob/37eaae6eeb71c3bd3d165f52515086db8f5e7dee/codex-rs/sandboxing/src/seatbelt.rs).
- **Claude Code's sandbox-runtime** generates deny-default profiles,
  routes egress through local proxies, allows only listed unix sockets
  (https://github.com/anthropic-experimental/sandbox-runtime).
- **Nix** builds under `(deny default)` with the build dir as a parameter
  (https://github.com/NixOS/nix/blob/master/src/libstore/darwin/build/sandbox-defaults.sb).
- **Bazel** starts from `(allow default)` and only denies writes and
  network: it does not stop reads of secrets. Not a model for this.
  (https://github.com/bazelbuild/bazel/blob/3f20d8048e8a06663efe39a6624410466be95402/src/main/java/com/google/devtools/build/lib/sandbox/DarwinSandboxedSpawnRunner.java)

## 3. Gaps against bubblewrap

- **No namespaces.** The child sees the real filesystem and is limited
  path by path, so the read rules must be complete; `/var` and `/tmp`
  resolve under `/private`, so every path is passed resolved.
- **Mach services** are the main surface. Deny-default blocks every
  lookup not listed; allow only `com.apple.system.opendirectoryd.libinfo`,
  and not `com.apple.SecurityServer`, so the Keychain stays closed.
  Python's `ssl` reads a CA bundle file through OpenSSL; whether it needs
  `trustd` here is unverified and must be tested.
- **Unix sockets.** Allow exact paths only (the fetcher's proxy socket);
  an allow-all would expose ssh-agent and gpg-agent.
- **Kill by group** is weaker: a descendant that calls `setsid()` leaves
  the group, and there are no cgroups. Linux's `--die-with-parent` has no
  equivalent. A test must document this escape.
- **Resource limits.** `setrlimit` before exec is inherited; whether macOS
  enforces `RLIMIT_AS` is unverified.
- **Environment and /tmp.** `sandbox-exec` passes the environment through:
  the core builds it from scratch, as on Linux, and points `TMPDIR` into
  the data dir; `/private/tmp` stays unwritable.
- **Unsupported.** An OS update can break a profile (zsh 5.9 under a
  deny-default profile broke on macOS 26:
  https://forum.cursor.com/t/sandbox-fails-to-initialize-for-zsh-on-macos-26-tahoe-darwin25-zsh-5-9-reads-unwhitelisted-hw-sysctls/161023).
  The self-test must run at hub start, as `module_sandbox.available()`
  does on Linux, and modules must refuse to run when it fails.

## 4. Recommendation

Build a Seatbelt backend behind the same `available()` and `build_argv()`
interface as `module_sandbox.py`, modelled on Codex:

- run `/usr/bin/sandbox-exec -p <profile> -D NAME=/resolved/path ...` only;
- deny by default; read-only `/usr/lib`, `/usr/share`, the system
  frameworks, the Python prefix and each declared read; read-write the
  data dir only; no `network*` except, for a fetcher, outbound to the
  proxy's unix-socket directory;
- `(deny system-fcntl (fcntl-command 80 110))` as Codex does, so a
  read-only descriptor cannot be turned writable;
- a scrubbed environment, `TMPDIR` in the data dir, rlimits in the
  parent, a new session and a kill by group;
- keep the typed `unsandboxed` acknowledgement only for a Mac where the
  self-test fails; fetchers stay off there.

A starting collector profile, assembled from the Codex and Nix rules
above (paths arrive as parameters):

```scheme
(version 1)
(deny default)
(allow process-exec) (allow process-fork)
(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(deny file-write-setugid)
(allow file-write-data (require-all (path "/dev/null") (vnode-type CHARACTER-DEVICE)))
(allow sysctl-read)
(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo"))
(allow ipc-posix-sem)
(allow file-read* (subpath "/usr/lib") (subpath "/usr/share")
                  (subpath "/System/Library/Frameworks")
                  (subpath "/System/Library/PrivateFrameworks"))
(allow file-map-executable (subpath "/usr/lib") (subpath "/System/Library/Frameworks"))
(allow file-read* (subpath (param "PYTHON_PREFIX")) (subpath (param "RO_0")))
(allow file-read* file-write* (subpath (param "DATA_DIR")))
(deny system-fcntl (fcntl-command 80 110))
```

For a fetcher, add only:

```scheme
(allow system-socket (socket-domain AF_UNIX))
(allow network-outbound (remote unix-socket (subpath (param "PROXY_SOCK_DIR"))))
```

The proxy resolves names, so no DNS rules are needed. A TCP-port proxy is
the fallback; Seatbelt accepts only `localhost` or `*` as a host there,
and `localhost` does not match `::ffff:127.0.0.1`
(sandbox-runtime, `src/sandbox/macos-sandbox-utils.ts`).

## 5. What must pass before it ships

Run on macOS 14, 15, 26 and 27, in CI and by hand, each inside the profile:

- reading `~/.ssh/id_*`, `~/Library/Keychains/*`, Light's `session.key`
  and every lane's login file fails; `security find-generic-password` and
  `/usr/bin/open` fail;
- writing the data dir works; writing a read path, `$HOME` or
  `/private/tmp` fails;
- `connect` to `1.1.1.1:443`, `[2606:4700::1111]:443` and a local port
  fails; a DNS lookup fails; binding or listening fails; connecting to
  `$SSH_AUTH_SOCK` fails;
- in the fetcher profile the proxy socket works and the proxy refuses a
  host not on the grant's list;
- a grandchild started with `subprocess` is still confined;
- rlimits hold, and a memory-limit probe records what macOS enforces;
- a kill by group leaves no survivors, and the `setsid()` escape is
  recorded as a known gap;
- Python with `ssl` and `multiprocessing` works;
- the environment holds only the allowed variables.

Until then, the macOS answer stays as it is: an explicit, typed
acknowledgement for collectors, and no fetchers. (Superseded by §6.)

## 6. As built and measured (2026-10-08)

`module_sandbox.build_argv` and `available()` keep one interface; on
macOS they build a Seatbelt profile run by `/usr/bin/sandbox-exec -p`,
with every path passed as a `-D` parameter. Measured on the Mac:

- **What dyld and Python need, and nothing more:** `/usr` and `/System`
  read and map-executable; the running interpreter's own prefix (never the
  `/usr/bin/python3` stub, which hands off to the developer tools) and the
  library directories its extension modules link to outside that prefix
  (`otool -L` over `lib-dynload`: a Homebrew python's `_sqlite3`, `_ssl`,
  `_lzma`, `_zstd`, `_decimal` link sibling kegs, in both their `opt/`
  symlink and `Cellar/` spellings — found 2026-10-10 when the finops
  collector died with "blocked by sandbox" on `libsqlite3.dylib`); the
  root directory's own entry (data and metadata: dyld aborts every binary
  without it, even `/usr/bin/true`); metadata on the parent folders of each
  allowed path only (Python's `realpath` walks them); four `/dev` nodes.
  No global metadata: a module cannot stat arbitrary paths.
- **No child processes.** The profile allows `process-exec` but not
  `process-fork`. A child that calls `setsid()` leaves the process group
  and macOS has no pid namespace to end it (§3), so a sandboxed module
  starts none. Collectors and fetchers need none; the chain
  `sandbox-exec` → `env` → `python` is exec only.
- **No mounts.** A bind whose destination lies outside the data dir
  becomes a path alias: the source path is used, and allowed. A bind into
  the data dir is a copy. So the module's code runs from its verified run
  copy's real path rather than `/module`, and a fetcher finds its key
  through `CORRAL_FETCH_KEY`.
- **Fetchers** reach the hub's fetch proxy (plan §6.7.2: it holds the key
  and adds it; the fetcher has none) on a loopback TCP port the
  profile allows (`(remote tcp "localhost:PORT")`); every other local port
  and the internet are refused, and the proxy, not the module, resolves
  names. Linux keeps the unix-socket shim.
- **Exit without reaping.** macOS Python has no `os.waitid`; the runner
  watches for the child's exit with a kqueue `NOTE_EXIT` event, which also
  fires at once for a child that exited before the watch was set.
- **Environment.** Built from nothing with `/usr/bin/env -i`; `TMPDIR` is
  the data dir; the OS adds `LC_CTYPE` and `__CF_USER_TEXT_ENCODING`.

Probed inside the profile: reading `~/.ssh`, `~/.zshrc` and the login
keychain, listing the home folder, writing `/private/tmp` or the home
folder, connecting to `1.1.1.1:443` or any local port, DNS, binding a
port, `/usr/bin/security`, and a subprocess all fail; writing the data
dir works. Light's module and fetcher tests (116) pass on the Mac with the
real sandbox, and FinOps, installed from its repository into a scratch
Light state there, collected the Mac's own usage sandboxed and fetched
from Anthropic through the proxy (a fake key: a clean 401, and the key's
bytes nowhere in the state).

Still open: the tests ran by hand on one Mac and one macOS release; CI
now has a macOS job (the system Python and a current one) that fails if
the sandbox is not available, so the sandboxed tests cannot pass by
skipping. Resource limits other than open files and file
size are not shown to hold on macOS.
