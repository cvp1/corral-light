"""Modules: features installed beside Light, never inside it (docs/finops-module-plan.md §4, §5).

A module is its own git repository with a `module.json` manifest. The hub
runs its collector as a fresh, time-boxed subprocess (M1) inside the module
sandbox (module_sandbox.py), reads one JSON snapshot from stdout, checks it
against the snapshot contract (§4.5) and caches it. The browser renders the
snapshot's typed blocks with textContent only (M2); no module code reaches
the page.

Layout (§4.3):
  <state>/modules/<name>/<commit>/     one generation, `.git` removed
  <state>/modules/<name>/current       the active commit
  <state>/modules/<name>/snapshot.json the last snapshot, hub-only
  <state>/module-data/<name>/          the module's one writable dir
  ~/.config/corral-light/modules.json  pins: source, commit, digest,
                                       enabled, unsandboxed_ack
  ~/.config/corral-light/modules/<name>/  operator config (config.toml)

Trust (§3): a module runs as the operator's user. On Linux with
bubblewrap it sees only its declared reads, the feed, its own code and
config read-only, and its data dir; no network. Elsewhere it runs only
after the operator typed `unsandboxed`, and says so on its tile.
The digest is integrity, not security: it stops a half-finished update or
a stray file from running.
"""
import contextlib
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import select
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import module_sandbox

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get("CORRAL_LIGHT_STATE",
                            Path.home() / ".local/share/corral-light"))
CONFIG = Path(os.environ.get("CORRAL_LIGHT_CONFIG_DIR",
                             Path.home() / ".config/corral-light"))
INDEX = ROOT / "modules" / "index.json"

CORE_API = 1
MANIFEST_SCHEMA = "corral-light.manifest/1"
SNAPSHOT_SCHEMA = "corral-light.module/1"

# Verbs of the corral-light wrapper; a module may never take one (M7).
CORE_VERBS = frozenset((
    "pair", "key", "serve", "doctor", "worktrees", "launch", "install-service",
    "diagnose", "consult", "watch", "panes", "open", "say", "pending", "ok", "no",
    "cancel", "pause", "resume", "close", "forget", "reopen", "rename", "seat",
    "config", "attach", "quote", "later", "search", "digest", "port", "rig",
    "lanes", "update", "cli", "module", "modules", "help", "version", "hubs",
    "session"))
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")

MANIFEST_KEYS = {"schema", "name", "title", "version", "core_api", "summary",
                 "collector", "cli", "doctor", "reads", "vendor_reports", "network",
                 "notices", "fetcher"}
ENTRY_KEYS = {"collector": {"script", "args", "every_s", "budget_s", "timeout_s"},
              "cli": {"script", "args"},
              "doctor": {"script", "args"},
              "fetcher": {"script", "args", "every_s", "timeout_s", "vendors"}}
# Vendor billing APIs a fetcher may reach (plan §6.7), by exact host. Fixed
# here, never chosen by a module; a grant names one vendor, and that run
# reaches only that vendor's hosts.
FETCH_VENDORS = {
    "anthropic": ("api.anthropic.com",),
    "openai": ("api.openai.com",),
    "xai": ("management-api.x.ai",),
    # Google's token host is the core's own (fetch_proxy), never the module's.
    "gcp": ("bigquery.googleapis.com",),
}
FETCH_EVERY_S = (21600, 3600, 7 * 86400)      # default, least, most
FETCH_TIMEOUT_S = (60, 5, 120)
READS = ("claude-projects", "codex-sessions", "gemini-store", "light-feed")
VENDOR_REPORTS = ("grok-usage",)
# Python runs these on its own, in any compiled form: refused by stem.
FORBIDDEN_STEMS = ("sitecustomize", "usercustomize")
# Inside the sandbox the run copy of the code sits here, read-only.
SANDBOX_CODE = "/module"
# What `module add` says on a host with no sandbox. A test pins it to the
# behaviour: with no pid namespace, a child that calls setsid outlives a
# timeout kill of the run's process group.
UNSANDBOXED_SURVIVORS = "processes it starts may outlive a timeout"

# Run caps (§4.5).
STDOUT_CAP = 1 << 20
STDERR_CAP = 64 << 10
DEFAULT_EVERY_S, MIN_EVERY_S = 300, 60
DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S = 45, 300
REFRESH_MIN_GAP_S = 30


class ModuleError(Exception):
    """A refusal, with the reason the operator sees."""


# ---------------------------------------------------------------------------
# small helpers

def _say(*a, **k):
    """print, flushed: under a service manager stdout is block-buffered."""
    print(*a, file=k.get("file"), flush=True)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_json(path, obj):
    _atomic_write(path, json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def check_name(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ModuleError(f"module name {name!r} must match [a-z][a-z0-9-]{{0,31}}")
    if name in CORE_VERBS:
        raise ModuleError(f"{name!r} is a corral-light verb; a module cannot take it")
    return name


def module_dir(name):
    return STATE / "modules" / check_name(name)


def data_dir(name):
    return STATE / "module-data" / check_name(name)


def config_dir(name):
    return CONFIG / "modules" / check_name(name)


def pins_path():
    return CONFIG / "modules.json"


class _Lock:
    """One lock per module: update, remove and run never overlap (§5.3)."""

    def __init__(self, name, blocking=True):
        self.path = STATE / "modules" / f".{check_name(name)}.lock"
        self.blocking = blocking
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB))
        except OSError as e:
            os.close(self.fd)
            self.fd = None
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise ModuleError("the module is busy (a run, update or removal "
                                  "holds it); try again shortly") from None
            raise
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


_PINS_LOCK = threading.Lock()


@contextlib.contextmanager
def pins_lock():
    """Every read-modify-write of modules.json, across threads and across
    processes (the hub disables on tamper while a CLI updates). Not
    re-entrant."""
    path = CONFIG / ".modules.json.lock"
    with _PINS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)                  # closing releases the flock


def load_pins():
    d = _read_json(pins_path(), {})
    mods = d.get("modules") if isinstance(d, dict) else None
    return mods if isinstance(mods, dict) else {}


def save_pins(mods):
    _write_json(pins_path(), {"schema": "corral-light.module-pins/1", "modules": mods})


def update_pin(name, **fields):
    with pins_lock():
        mods = load_pins()
        pin = dict(mods.get(name) or {})
        pin.update(fields)
        mods[name] = pin
        save_pins(mods)
        return pin


# ---------------------------------------------------------------------------
# manifest (§4.2)

def _check_script(root, rel, where):
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        raise ModuleError(f"{where}.script must be a relative path inside the module")
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ModuleError(f"{where}.script {rel!r} may not contain '..', '.' or empty parts")
    if rel.startswith("-"):
        raise ModuleError(f"{where}.script may not look like an interpreter option")
    if not rel.endswith(".py"):
        raise ModuleError(f"{where}.script must be a .py file run by the core's Python")
    if root is not None:
        cur = Path(root)
        for p in parts:
            cur = cur / p
            if cur.is_symlink():
                raise ModuleError(f"{where}.script {rel!r} passes through a symlink")
        if not cur.is_file():
            raise ModuleError(f"{where}.script {rel!r} is not a regular file in the module")
    return rel


def _check_args(args, where):
    if args is None:
        return []
    if not isinstance(args, list) or len(args) > 16 or \
            not all(isinstance(a, str) and len(a) <= 200 for a in args):
        raise ModuleError(f"{where}.args must be a list of at most 16 short strings")
    return list(args)


def _int_field(entry, key, default, lo, hi, where):
    v = entry.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ModuleError(f"{where}.{key} must be an integer from {lo} to {hi}")
    return v


def validate_manifest(obj, root=None):
    """-> the manifest, normalised. Raises ModuleError with the reason.
    `root`: the module's files, to check scripts exist and are plain files."""
    if not isinstance(obj, dict):
        raise ModuleError("module.json must hold one JSON object")
    unknown = sorted(set(obj) - MANIFEST_KEYS)
    if unknown:
        raise ModuleError(f"module.json has unknown keys: {', '.join(unknown)}")
    if obj.get("schema") != MANIFEST_SCHEMA:
        raise ModuleError(f"module.json schema must be {MANIFEST_SCHEMA!r}")
    if obj.get("core_api") != CORE_API:
        raise ModuleError(f"module.json core_api {obj.get('core_api')!r} is not one this "
                          f"Corral Light speaks ({CORE_API})")
    name = check_name(obj.get("name"))
    out = {"schema": MANIFEST_SCHEMA, "name": name, "core_api": CORE_API}
    for key, cap in (("title", 60), ("version", 40), ("summary", 200)):
        v = obj.get(key, name if key == "title" else "")
        if not isinstance(v, str) or len(v) > cap:
            raise ModuleError(f"module.json {key} must be text of at most {cap} characters")
        out[key] = v
    if obj.get("network", "none") != "none":
        raise ModuleError("module.json network must be \"none\": a collector never "
                          "reaches the network")
    out["network"] = "none"
    # Rail notices (§4.7) are opt-in, and shown at install like the reads.
    if not isinstance(obj.get("notices", False), bool):
        raise ModuleError("module.json notices must be true or false")
    out["notices"] = obj.get("notices", False)
    reads = obj.get("reads", [])
    if not isinstance(reads, list) or not all(isinstance(r, str) for r in reads):
        raise ModuleError("module.json reads must be a list of names")
    bad = [r for r in reads if r not in READS]
    if bad:
        raise ModuleError(f"module.json reads {bad[0]!r} is not one of: {', '.join(READS)}")
    out["reads"] = sorted(set(reads))
    vrep = obj.get("vendor_reports", [])
    if not isinstance(vrep, list) or not all(isinstance(r, str) for r in vrep):
        raise ModuleError("module.json vendor_reports must be a list of names")
    bad = [r for r in vrep if r not in VENDOR_REPORTS]
    if bad:
        raise ModuleError(f"module.json vendor_reports {bad[0]!r} is not one of: "
                          f"{', '.join(VENDOR_REPORTS)}")
    out["vendor_reports"] = sorted(set(vrep))
    if "collector" not in obj:
        raise ModuleError("module.json needs a collector")
    for where in ("collector", "cli", "doctor", "fetcher"):
        if where not in obj:
            continue
        entry = obj[where]
        if not isinstance(entry, dict):
            raise ModuleError(f"module.json {where} must be an object")
        extra = sorted(set(entry) - ENTRY_KEYS[where])
        if extra:
            raise ModuleError(f"module.json {where} has unknown keys: {', '.join(extra)}")
        e = {"script": _check_script(root, entry.get("script"), where),
             "args": _check_args(entry.get("args"), where)}
        if where == "fetcher":
            vendors = entry.get("vendors")
            if not isinstance(vendors, list) or not vendors or \
                    not all(isinstance(v, str) for v in vendors):
                raise ModuleError("module.json fetcher vendors must be a list of names")
            bad = [v for v in vendors if v not in FETCH_VENDORS]
            if bad:
                raise ModuleError(f"module.json fetcher vendor {bad[0]!r} is not one of: "
                                  f"{', '.join(sorted(FETCH_VENDORS))}")
            e["vendors"] = sorted(set(vendors))
            e["every_s"] = _int_field(entry, "every_s", FETCH_EVERY_S[0], FETCH_EVERY_S[1],
                                      FETCH_EVERY_S[2], where)
            e["timeout_s"] = _int_field(entry, "timeout_s", FETCH_TIMEOUT_S[0],
                                        FETCH_TIMEOUT_S[1], FETCH_TIMEOUT_S[2], where)
        if where == "collector":
            e["every_s"] = _int_field(entry, "every_s", DEFAULT_EVERY_S, MIN_EVERY_S, 86400, where)
            e["timeout_s"] = _int_field(entry, "timeout_s", DEFAULT_TIMEOUT_S, 5,
                                        MAX_TIMEOUT_S, where)
            e["budget_s"] = _int_field(entry, "budget_s", min(30, e["timeout_s"]), 1,
                                       e["timeout_s"], where)
        out[where] = e
    return out


def read_manifest(root):
    p = Path(root) / "module.json"
    if p.is_symlink() or not p.is_file():
        raise ModuleError("the module has no module.json (or it is a symlink)")
    if p.stat().st_size > 64 << 10:
        raise ModuleError("module.json is larger than 64 KiB")
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ModuleError(f"module.json is not valid JSON: {e}") from None
    return validate_manifest(obj, root)


# ---------------------------------------------------------------------------
# tree checks and the digest (§5.3)

def scan_tree(root):
    """-> sorted [(relpath, mode, sha256)] of every file under root. Raises
    on a symlink, a special file, a `.pth`, a site hook in any compiled form,
    or any `.git` (install removes it; one that appears later is a change)."""
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        for d in dirnames + filenames:
            if d == ".git":
                raise ModuleError(f"the module contains "
                                  f"{os.path.normpath(os.path.join(rel_dir, d))}; refused")
        for d in dirnames:
            if os.path.islink(os.path.join(dirpath, d)):
                raise ModuleError(f"the module contains a symlink: "
                                  f"{os.path.normpath(os.path.join(rel_dir, d))}")
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.normpath(os.path.join(rel_dir, fn))
            st = os.lstat(full)
            if os.path.islink(full):
                raise ModuleError(f"the module contains a symlink: {rel}")
            if not (st.st_mode & 0o170000 == 0o100000):
                raise ModuleError(f"the module contains a special file: {rel}")
            if fn.endswith(".pth") or fn.split(".", 1)[0] in FORBIDDEN_STEMS:
                raise ModuleError(f"the module contains {rel}, which Python would run "
                                  f"on its own; refused")
            h = hashlib.sha256()
            with open(full, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
            mode = "100755" if st.st_mode & 0o111 else "100644"
            out.append((rel.replace(os.sep, "/"), mode, h.hexdigest()))
    out.sort()
    return out


def tree_digest(root):
    h = hashlib.sha256()
    for rel, mode, sha in scan_tree(root):
        h.update(f"{mode} {sha} {rel}\0".encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# sources and git

def _git_env():
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "HOME", "TMPDIR")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false",
                "GIT_LFS_SKIP_SMUDGE": "1"})
    return env


def _git(args, cwd=None, timeout=300):
    argv = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "protocol.file.allow=always",
            "-c", "core.symlinks=true", "-c", "submodule.recurse=false",
            "-c", "core.fsmonitor=false"] + list(args)
    r = subprocess.run(argv, cwd=cwd, env=_git_env(), capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [f"exit {r.returncode}"]
        raise ModuleError(f"git {args[0]} failed: {msg[0][:300]}")
    return r.stdout


def load_index():
    d = _read_json(INDEX, {})
    mods = d.get("modules") if isinstance(d, dict) else None
    return mods if isinstance(mods, dict) else {}


def resolve_source(source):
    """-> (name_hint or None, git source string). A short name resolves
    through the first-party index; a path or URL is used as given."""
    if NAME_RE.match(source or "") and not os.path.exists(source):
        entry = load_index().get(source)
        if not entry or not isinstance(entry.get("url"), str):
            raise ModuleError(f"{source!r} is not in the module index "
                              f"(modules/index.json); give a path or a git URL")
        return source, entry["url"]
    u = urlparse(source)
    if u.scheme in ("https", "file", "ssh"):
        if u.scheme == "https" and (u.username or u.password):
            raise ModuleError("a module URL may not carry credentials")
        return None, source
    if u.scheme:
        raise ModuleError(f"unsupported source scheme {u.scheme!r}; use https, file, "
                          f"ssh or a local path")
    p = Path(source).expanduser()
    if not p.exists():
        raise ModuleError(f"no such module source: {source}")
    p = p.resolve()
    if (p / ".git").exists():
        dirty = _git(["status", "--porcelain", "--untracked-files=all"], cwd=p)
        if dirty.strip():
            raise ModuleError(f"{p} has uncommitted or untracked files; commit them "
                              f"first, so what is installed is exactly one commit")
    return None, str(p)


def _stage(name, src, ref=None):
    """Clone `src` at `ref` (default: its HEAD) into a staging dir. -> (dir, commit)."""
    base = STATE / "modules" / name
    base.mkdir(parents=True, exist_ok=True)
    stage = base / f".staging-{secrets.token_hex(6)}"
    try:
        _git(["clone", "--quiet", "--no-checkout", "--no-recurse-submodules",
              "--", src, str(stage)])
        commit = _git(["rev-parse", "--verify", (ref or "HEAD") + "^{commit}"],
                      cwd=stage).strip()
        if not COMMIT_RE.match(commit):
            raise ModuleError(f"could not resolve {ref or 'HEAD'} to a commit")
        _git(["checkout", "--quiet", "--detach", commit], cwd=stage)
        head = _git(["rev-parse", "HEAD"], cwd=stage).strip()
        if head != commit:
            raise ModuleError("the checkout's HEAD is not the pinned commit")
        if _git(["status", "--porcelain", "--untracked-files=all", "--ignored"],
                cwd=stage).strip():
            raise ModuleError("the checkout is not clean")
        if (stage / ".gitmodules").exists():
            raise ModuleError("the module uses git submodules; refused")
        tracked = set(_git(["ls-files", "-z"], cwd=stage).split("\0")) - {""}
        shutil.rmtree(stage / ".git")
        on_disk = {rel for rel, _m, _h in scan_tree(stage)}
        if on_disk != tracked:
            raise ModuleError("the checkout holds files git does not track")
        return stage, commit
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def describe(manifest, sandboxed, src, commit, digest):
    """What `module add` shows before the operator confirms (§5.1)."""
    lines = [f"Module   {manifest['name']} — {manifest['title']} {manifest['version']}",
             f"Source   {src}", f"Commit   {commit}", f"Digest   {digest}",
             f"Reads    {', '.join(manifest['reads']) or 'nothing'}"]
    if manifest["vendor_reports"]:
        lines.append(f"Reports  {', '.join(manifest['vendor_reports'])} "
                     f"(run by Light, not by the module)")
    lines.append("Network  none: its collector makes no network calls")
    if manifest.get("notices"):
        lines.append("Notices  yes: it can show short notices in your rail")
    if manifest.get("fetcher"):
        hosts = "; ".join(f"{v}: {', '.join(FETCH_VENDORS[v])}"
                          for v in manifest["fetcher"]["vendors"])
        lines.append(f"Fetcher  may call vendor billing APIs, only with a key you grant, "
                     f"only to: {hosts}")
    if sandboxed:
        lines.append("Sandbox  yes: it sees only the above, its own files and its data dir")
    else:
        lines.append("Sandbox  NO: this module will run unsandboxed as your user and "
                     "can read anything you can;")
        lines.append(f"         {UNSANDBOXED_SURVIVORS}")
    return "\n".join(lines)


def add(source, *, ref=None, confirm=None, ack_unsandboxed=None, out=_say, ask=None):
    """Install a module (§5.1). The operator types the module's name to
    confirm; on a host without the sandbox, also `unsandboxed`. `confirm`
    and `ack_unsandboxed` carry those typed answers for scripts; `ask` reads
    them interactively."""
    hint, src = resolve_source(source)
    if hint:
        check_name(hint)
    stage, commit = _stage(hint or ".incoming", src, ref)
    try:
        manifest = read_manifest(stage)
        name = manifest["name"]
        if hint and name != hint:
            raise ModuleError(f"the index names {hint!r} but its manifest says {name!r}")
        if load_pins().get(name):
            raise ModuleError(f"{name} is already installed; use `module update {name}`")
        digest = tree_digest(stage)
        sandboxed, why = module_sandbox.available()
        out(describe(manifest, sandboxed, src, commit, digest))
        if not sandboxed:
            out(f"         ({why})")
        typed = confirm if confirm is not None else (ask(f"Type {name} to install: ")
                                                    if ask else None)
        if typed != name:
            raise ModuleError("not confirmed; nothing installed")
        ack = False
        if not sandboxed:
            t2 = ack_unsandboxed if ack_unsandboxed is not None else (
                ask("Type unsandboxed to let it run without a sandbox: ") if ask else None)
            if t2 != "unsandboxed":
                raise ModuleError("the unsandboxed run was not acknowledged; nothing "
                                  "installed")
            ack = True
        with _Lock(name):
            base = module_dir(name)
            base.mkdir(parents=True, exist_ok=True)
            gen = base / commit
            if gen.exists():
                shutil.rmtree(gen)
            os.replace(stage, gen)
            stage = None
            _atomic_write(base / "current", commit + "\n")
            data_dir(name).mkdir(parents=True, exist_ok=True, mode=0o700)
            config_dir(name).mkdir(parents=True, exist_ok=True, mode=0o700)
            update_pin(name, source=src, commit=commit, digest=digest, enabled=True,
                       unsandboxed_ack=ack, installed_at=_now_iso(), previous=None,
                       title=manifest["title"], summary=manifest["summary"])
        out(f"Installed {name} at {commit[:12]}.")
        return name
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def update(name, *, ref=None, confirm=None, out=_say, ask=None):
    """Stage a new generation, verify it, wait for the module to be idle,
    switch atomically, keep the previous one for rollback (M5)."""
    pin = _pin(name)
    stage, commit = _stage(name, pin["source"], ref)
    try:
        manifest = read_manifest(stage)
        if manifest["name"] != name:
            raise ModuleError(f"the new manifest names {manifest['name']!r}, not {name!r}")
        digest = tree_digest(stage)
        if commit == pin.get("commit") and digest == pin.get("digest"):
            out(f"{name} is already at {commit[:12]}.")
            return False
        sandboxed, _why = module_sandbox.available()
        old = _read_json(module_dir(name) / pin["commit"] / "module.json", {}) or {}
        out(describe(manifest, sandboxed, pin["source"], commit, digest))
        if set(manifest["reads"]) != set(old.get("reads") or []) or \
                set(manifest["vendor_reports"]) != set(old.get("vendor_reports") or []) or \
                bool(manifest["notices"]) != (old.get("notices") is True) or \
                _fetch_vendors(manifest) != _fetch_vendors(old):
            out("Changed  what it reads, its notices or its billing APIs; confirm again.")
            typed = confirm if confirm is not None else (ask(f"Type {name} to update: ")
                                                        if ask else None)
            if typed != name:
                raise ModuleError("not confirmed; the current generation stays active")
        with _Lock(name):                     # waits for an in-flight run
            _unchanged_since(name, pin)
            base = module_dir(name)
            gen = base / commit
            if gen.exists():
                shutil.rmtree(gen)
            os.replace(stage, gen)
            stage = None
            prev = {"commit": pin["commit"], "digest": pin["digest"]}
            # The pin moves first; `current` follows. A crash between them is
            # caught by verify (digest of `current` != pin) and repaired by
            # rollback or a re-run of update.
            update_pin(name, commit=commit, digest=digest, previous=prev,
                       updated_at=_now_iso(), title=manifest["title"])
            _atomic_write(base / "current", commit + "\n")
            _prune(name, keep={commit, prev["commit"]})
        out(f"Updated {name} to {commit[:12]} (rollback: {prev['commit'][:12]}).")
        return True
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def _fetch_vendors(m):
    f = (m or {}).get("fetcher")
    v = f.get("vendors") if isinstance(f, dict) else None
    return sorted(set(v)) if isinstance(v, list) else []


def _unchanged_since(name, pin):
    """Under the module lock: the pin this operation started from is still
    the pin. Another update or a rollback in between would otherwise be
    overwritten, and its generation pruned."""
    now = _pin(name)
    if now.get("commit") != pin.get("commit") or now.get("digest") != pin.get("digest"):
        raise ModuleError(f"{name} changed while this was staged (another update or a "
                          f"rollback); nothing switched, run it again")


def rollback(name, out=_say):
    with _Lock(name):
        pin = _pin(name)
        prev = pin.get("previous")
        if not prev:
            raise ModuleError(f"{name} has no previous generation to roll back to")
        base = module_dir(name)
        gen = base / prev["commit"]
        if not gen.is_dir() or tree_digest(gen) != prev["digest"]:
            raise ModuleError("the previous generation is missing or changed on disk")
        update_pin(name, commit=prev["commit"], digest=prev["digest"],
                   previous={"commit": pin["commit"], "digest": pin["digest"]},
                   updated_at=_now_iso())
        _atomic_write(base / "current", prev["commit"] + "\n")
    out(f"Rolled {name} back to {prev['commit'][:12]}.")


def _prune(name, keep):
    base = module_dir(name)
    for child in base.iterdir():
        if child.is_dir() and not child.name.startswith(".") and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)


def remove(name, *, purge=False, out=_say):
    _pin(name)
    with _Lock(name):
        with pins_lock():
            mods = load_pins()
            mods.pop(name, None)
            save_pins(mods)
        shutil.rmtree(module_dir(name), ignore_errors=True)
        # Fetch results and fetcher work dirs go with the module: they hold
        # what a granted key fetched, and the grants went with the pin.
        shutil.rmtree(fetch_dir(name), ignore_errors=True)
        shutil.rmtree(STATE / "module-fetchwork" / name, ignore_errors=True)
        if purge:
            shutil.rmtree(data_dir(name), ignore_errors=True)
            shutil.rmtree(config_dir(name), ignore_errors=True)
    out(f"Removed {name}" + (" with its data and config." if purge else
                             "; its data and config stay (use --purge to delete them)."))


def set_enabled(name, enabled, out=_say):
    _pin(name)
    update_pin(name, enabled=bool(enabled), disabled_reason=None)
    out(f"{name} {'enabled' if enabled else 'disabled'}.")


def _pin(name):
    check_name(name)
    pin = load_pins().get(name)
    if not pin:
        raise ModuleError(f"no module named {name!r} is installed")
    return pin


# ---------------------------------------------------------------------------
# verification before every execution path (§5.3)

def _copy_generation(gen, dest):
    """Plain files only, modes kept; scan_tree on the copy refuses anything
    else that slipped in."""
    for rel, _mode, _sha in scan_tree(gen):
        src, dst = gen / rel, dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(src, dst, follow_symlinks=False)


def verify(name, run_copy=None):
    """-> (code_dir, manifest, pin). Raises ModuleError, and disables the
    module, when what is on disk is not what was pinned.

    run_copy: a fresh path. The generation is copied there and the COPY is
    checked, so what runs is exactly what was digested: a writer that
    swaps a file in the generation after the check changes nothing about
    this run (round three, finding 3). The caller deletes it."""
    pin = _pin(name)
    if not pin.get("enabled"):
        raise ModuleError(pin.get("disabled_reason") or f"{name} is disabled")
    base = module_dir(name)
    try:
        current = (base / "current").read_text(encoding="utf-8").strip()
    except OSError:
        current = ""
    try:
        if current != pin.get("commit") or not COMMIT_RE.match(current):
            raise ModuleError("its active generation is not the pinned commit")
        gen = base / current
        if gen.is_symlink() or not gen.is_dir():
            raise ModuleError("its pinned generation is missing")
        code = gen
        if run_copy is not None:
            code = Path(run_copy)
            code.mkdir(mode=0o700)
            _copy_generation(gen, code)
        if tree_digest(code) != pin.get("digest"):
            raise ModuleError("its files changed on disk")
        manifest = read_manifest(code)
    except ModuleError as e:
        reason = f"disabled: changed on disk ({e})"
        update_pin(name, enabled=False, disabled_reason=reason)
        raise ModuleError(reason) from None
    return code, manifest, pin


# ---------------------------------------------------------------------------
# running (§5.2)

def _interpreter():
    """A Python that exists inside the sandbox: the core's own when it lives
    under /usr, else the system's."""
    exe = os.path.realpath(sys.executable)
    if exe.startswith("/usr/") or sys.platform == "darwin":
        # macOS: the hub's own interpreter, never the /usr/bin/python3 stub,
        # which hands off to xcrun and the developer tools.
        return exe
    for c in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if os.path.exists(c):
            return c
    return exe


def resolve_reads(reads):
    """{vocabulary name: [existing absolute paths]}. None holds a login."""
    home = Path.home()
    out = {}
    for r in reads:
        paths = []
        if r == "claude-projects":
            paths.append(home / ".claude" / "projects")
            panes = STATE / "panes"
            if panes.is_dir():
                for pd in sorted(panes.iterdir()):
                    paths.append(pd / "config" / "projects")
        elif r == "codex-sessions":
            ch = Path(os.environ.get("CORRAL_CODEX_HOME",
                                     home / ".config/corral-light/codex-home"))
            paths += [ch / "sessions", ch / "archived_sessions"]
        elif r == "gemini-store":
            paths.append(home / ".gemini" / "antigravity-acp" / "conversations")
        out[r] = [str(p) for p in paths
                  if p.is_dir() and not p.is_symlink()]
    return out


def feed_dir():
    return STATE / "module-feed" / "v1"


def fetch_dir(name):
    """Fetcher results the core accepted (plan §6.7): one JSON per granted
    key, written by the core only, read-only to the module's collector."""
    return STATE / "module-fetch" / check_name(name)


class RunSpec(tuple):
    """(argv, env, cwd, sandboxed), and `.manifest` of the verified copy."""
    manifest = None


def _spec(argv, env, cwd, sandboxed, manifest):
    r = RunSpec((argv, env, cwd, sandboxed))
    r.manifest = manifest
    return r


@contextlib.contextmanager
def prepared(name, entry_key, extra_args=(), *, interactive=False):
    """Verify into a private run copy and yield (argv, env, cwd, sandboxed)
    to run it; the copy is deleted afterwards. The caller holds the
    module's lock, so any run copy already there is a crash's leftover."""
    base = module_dir(name)
    for old in base.glob(".run-*") if base.is_dir() else ():
        shutil.rmtree(old, ignore_errors=True)
    run_dir = base / f".run-{secrets.token_hex(8)}"
    try:
        yield build_run(name, entry_key, extra_args, interactive=interactive,
                        run_dir=run_dir)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def build_run(name, entry_key, extra_args=(), *, interactive=False, run_dir):
    """-> (argv, env, cwd, sandboxed) for one execution path, verified into
    run_dir. Inside the sandbox the code sits read-only at SANDBOX_CODE."""
    if entry_key == "fetcher":
        raise ModuleError("a fetcher runs only through module_fetch, with one granted key")
    code, manifest, pin = verify(name, run_copy=run_dir)
    entry = manifest.get(entry_key)
    if not entry:
        raise ModuleError(f"{name} has no {entry_key}")
    sandboxed, why = module_sandbox.available()
    if not sandboxed and not pin.get("unsandboxed_ack"):
        raise ModuleError(f"this host cannot sandbox modules ({why}); {name} was "
                          f"installed where it could, so it does not run here unsandboxed. "
                          f"Remove it and add it again to acknowledge that")
    data = data_dir(name)
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    cfg = config_dir(name)
    cfg.mkdir(parents=True, exist_ok=True, mode=0o700)
    reads = resolve_reads(manifest["reads"])
    fd = feed_dir()
    use_feed = "light-feed" in manifest["reads"]
    if use_feed:
        fd.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {"LANG": "C.UTF-8", "TZ": _host_tz(), "CORRAL_MODULE_API": str(CORE_API),
           "CORRAL_MODULE_NAME": name,
           "CORRAL_MODULE_CONFIG": str(cfg / "config.toml"),
           "CORRAL_MODULE_DATA": str(data),
           "CORRAL_MODULE_FEED": str(fd) if use_feed else "",
           "CORRAL_MODULE_SANDBOXED": "1" if sandboxed else "0"}
    # A module with a fetcher reads what the core accepted from it, read-only.
    fetched = fetch_dir(name) if manifest.get("fetcher") else None
    if fetched is not None:
        fetched.mkdir(parents=True, exist_ok=True, mode=0o700)
        env["CORRAL_MODULE_FETCHED"] = str(fetched)
    for r, paths in reads.items():
        if r == "light-feed":
            continue
        env["CORRAL_READ_" + r.upper().replace("-", "_")] = os.pathsep.join(paths)
    if interactive and os.environ.get("TERM"):
        env["TERM"] = os.environ["TERM"]
    tail = list(entry["args"]) + [str(a) for a in extra_args]
    if sandboxed:
        argv = [_interpreter(), "-I", "-B", f"{SANDBOX_CODE}/{entry['script']}"] + tail
        ro = [p for r, ps in reads.items() for p in ps]
        if fetched is not None:
            ro.append(str(fetched))
        binds = [(str(code), SANDBOX_CODE)]
        # The collector reads its config; only the CLI (setup) may write it.
        if interactive:
            argv = module_sandbox.build_argv(argv, read_only=ro, data_dir=str(data),
                                             feed_dir=str(fd) if use_feed else None,
                                             file_binds=binds, env=env,
                                             writable_extra=[str(cfg)])
        else:
            ro.append(str(cfg))
            argv = module_sandbox.build_argv(argv, read_only=ro, data_dir=str(data),
                                             feed_dir=str(fd) if use_feed else None,
                                             file_binds=binds, env=env)
        return _spec(argv, None, str(data), True, manifest)
    argv = [_interpreter(), "-I", "-B", str(code / entry["script"])] + tail
    full = dict(env, PATH=module_sandbox.SANDBOX_PATH, HOME=str(data))
    return _spec(argv, full, str(data), False, manifest)


def _host_tz():
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _exited(pid):
    """True once `pid` has exited; it is NOT reaped, so its process group
    id stays reserved until we kill the group and wait. Linux only (waitid);
    see _ExitWatch for macOS."""
    try:
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return True


class _ExitWatch:
    """Has a child exited, without reaping it? waitid(WNOWAIT) where Python
    has it (Linux); a kqueue NOTE_EXIT event on macOS, which has no waitid.
    A child that exited before the watch was set still reports at once
    (measured on macOS 27)."""

    def __init__(self, pid):
        self.pid, self.kq, self.done = pid, None, False
        if not hasattr(os, "waitid") and hasattr(select, "kqueue"):
            self.kq = select.kqueue()
            try:
                if self.kq.control([select.kevent(pid, select.KQ_FILTER_PROC,
                                                  select.KQ_EV_ADD, select.KQ_NOTE_EXIT)], 1, 0):
                    self.done = True
            except OSError:
                self.done = True                  # gone before we could watch it

    def exited(self):
        if self.done:
            return True
        if self.kq is not None:
            if self.kq.control(None, 1, 0):
                self.done = True
            return self.done
        return _exited(self.pid)

    def close(self):
        if self.kq is not None:
            self.kq.close()


def run_capped(argv, env, cwd, timeout_s, *, stdout_cap=STDOUT_CAP, stderr_cap=STDERR_CAP):
    """Run in a new session; read both pipes with caps in one loop driven by
    a single deadline; kill the group on timeout or overflow, and once more
    when it ends (anything left in it). -> (rc or None, stdout, stderr, why).

    Raw descriptors and no reader threads: a child that left the group and
    still holds a pipe cannot hold this call past its deadline (round three,
    finding 4). In the sandbox the pid namespace ends such a child; with no
    sandbox it survives, which `module add` says (UNSANDBOXED_SURVIVORS)."""
    proc = subprocess.Popen(argv, env=env if env is not None else {}, cwd=cwd,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True,
                            preexec_fn=module_sandbox.limits_fn(), close_fds=True)
    bufs = {"out": bytearray(), "err": bytearray()}
    caps = {"out": stdout_cap, "err": stderr_cap}
    why = None
    deadline = time.monotonic() + timeout_s
    late = f"it ran past its {timeout_s} s timeout"
    sel = selectors.DefaultSelector()
    watch = _ExitWatch(proc.pid)
    try:
        sel.register(proc.stdout.fileno(), selectors.EVENT_READ, "out")
        sel.register(proc.stderr.fileno(), selectors.EVENT_READ, "err")
        open_pipes = 2
        while open_pipes and why is None:
            left = deadline - time.monotonic()
            if left <= 0:
                why = late
                break
            for key, _ev in sel.select(min(left, 0.5)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    sel.unregister(key.fd)
                    open_pipes -= 1
                    continue
                k = key.data
                room = caps[k] - len(bufs[k])
                if len(chunk) > room:
                    bufs[k] += chunk[:max(room, 0)]
                    why = (f"its {'output' if k == 'out' else 'error output'} passed the "
                           f"{caps[k] // 1024} KiB cap")
                    break
                bufs[k] += chunk
        while why is None and not watch.exited():
            if time.monotonic() >= deadline:
                why = late
                break
            time.sleep(0.02)
    finally:
        _kill_group(proc)             # before reaping: the pgid is still ours
        sel.close()
        watch.close()
        for f in (proc.stdout, proc.stderr):
            try:
                f.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return proc.returncode, bytes(bufs["out"]), bytes(bufs["err"]), why


# ---------------------------------------------------------------------------
# the snapshot contract (§4.5)

KINDS = ("billed", "vendor", "declared", "list", "estimate", "unknown")
LEVELS = ("ok", "info", "warn", "bad")
BLOCK_TYPES = ("tiles", "meter", "table", "note", "link")
MAX_BLOCKS, MAX_TILES, MAX_ROWS, MAX_COLS = 50, 24, 200, 12
LABEL_CAP, CELL_CAP = 200, 500


def _text(v, cap, dropped=None):
    if v is None:
        return ""
    if isinstance(v, bool):
        v = "yes" if v else "no"
    if isinstance(v, (int, float)):
        v = str(v) if not isinstance(v, float) or math.isfinite(v) else ""
    if not isinstance(v, str):
        return ""
    v = v.replace("\x00", "")
    if len(v) > cap:
        if dropped is not None:
            dropped["chars"] = dropped.get("chars", 0) + (len(v) - cap)
        v = v[:cap - 1] + "…"
    return v


def safe_https_url(url):
    if not isinstance(url, str) or len(url) > 2000:
        return None
    try:
        u = urlparse(url)
    except ValueError:
        return None
    if u.scheme != "https" or not u.hostname or u.username or u.password or \
            any(c in url for c in "\r\n\t \\"):
        return None
    return url


def _validate_block(b):
    if not isinstance(b, dict):
        return {"type": "unsupported", "was": type(b).__name__}
    t = b.get("type")
    if t not in BLOCK_TYPES:
        return {"type": "unsupported", "was": _text(t, 40)}
    d = {}
    if t == "tiles":
        items = b.get("items") if isinstance(b.get("items"), list) else []
        out = []
        for it in items[:MAX_TILES]:
            if not isinstance(it, dict):
                continue
            out.append({"label": _text(it.get("label"), LABEL_CAP, d),
                        "value": _text(it.get("value"), LABEL_CAP, d),
                        "kind": it.get("kind") if it.get("kind") in KINDS else "unknown",
                        "level": it.get("level") if it.get("level") in LEVELS else "info",
                        "note": _text(it.get("note"), CELL_CAP, d),
                        "fresh_at": _text(it.get("fresh_at"), 40, d)})
        blk = {"type": "tiles", "items": out}
        if len(items) > MAX_TILES:
            d["items"] = len(items) - MAX_TILES
    elif t == "meter":
        pct = b.get("pct")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct):
            pct = 0
        blk = {"type": "meter", "label": _text(b.get("label"), LABEL_CAP, d),
               "pct": max(0, min(100, pct)),
               "kind": b.get("kind") if b.get("kind") in KINDS else "unknown",
               "level": b.get("level") if b.get("level") in LEVELS else "info",
               "note": _text(b.get("note"), CELL_CAP, d)}
    elif t == "table":
        cols = b.get("columns") if isinstance(b.get("columns"), list) else []
        rows = b.get("rows") if isinstance(b.get("rows"), list) else []
        ncol = min(len(cols), MAX_COLS)
        out_rows = []
        for r in rows[:MAX_ROWS]:
            if not isinstance(r, list):
                continue
            cells = [_text(c, CELL_CAP, d) for c in r[:MAX_COLS]]
            out_rows.append(cells)
        blk = {"type": "table", "title": _text(b.get("title"), LABEL_CAP, d),
               "columns": [_text(c, LABEL_CAP, d) for c in cols[:ncol]], "rows": out_rows}
        if len(rows) > MAX_ROWS:
            d["rows"] = len(rows) - MAX_ROWS
        if len(cols) > MAX_COLS:
            d["columns"] = len(cols) - MAX_COLS
    elif t == "note":
        blk = {"type": "note", "text": _text(b.get("text"), CELL_CAP, d),
               "level": b.get("level") if b.get("level") in LEVELS else "info"}
    else:  # link
        url = safe_https_url(b.get("url"))
        blk = {"type": "link", "label": _text(b.get("label"), LABEL_CAP, d),
               "url": url if url else _text(b.get("url"), LABEL_CAP, d),
               "safe": bool(url)}
    if d:
        blk["dropped"] = d
    return blk


NOTICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
NOTICE_LEVELS = ("info", "warn", "bad")
NOTICE_TITLE_CAP, NOTICE_TEXT_CAP = 80, 300
MAX_NOTICES = 5                  # kept per snapshot
NOTICES_PER_MODULE = 3          # per module, to the page
NOTICES_SENT = 24               # in all, to the page; the rail draws 8 after Not now
NOTICE_MAX_AGE_S = 24 * 3600
_LEVEL_RANK = {"bad": 0, "warn": 1, "info": 2}


def _parse_iso(v):
    """ISO time -> epoch seconds, or None. A time with no zone is UTC."""
    if not isinstance(v, str) or not v or len(v) > 40:
        return None
    try:
        t = datetime.fromisoformat(v[:-1] + "+00:00" if v.endswith("Z") else v)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    try:
        ts = t.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    return ts if math.isfinite(ts) else None


def _age_text(seconds):
    s = max(0, int(seconds))
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h"
    return f"{s // 86400} d"


def _validate_notices(raw):
    """A snapshot's `notices` (§4.7) -> (kept, dropped count)."""
    if not isinstance(raw, list):
        return [], 0
    seen, out, dropped = set(), [], 0
    for n in raw[:200]:
        nid = n.get("id") if isinstance(n, dict) else None
        title = _text(n.get("title"), NOTICE_TITLE_CAP) if isinstance(n, dict) else ""
        if not isinstance(nid, str) or not NOTICE_ID_RE.match(nid) or nid in seen \
                or not title.strip():
            dropped += 1
            continue
        seen.add(nid)
        exp = _parse_iso(n.get("expires_at"))
        out.append({"id": nid,
                    "level": n.get("level") if n.get("level") in NOTICE_LEVELS else "info",
                    "title": title, "text": _text(n.get("text"), NOTICE_TEXT_CAP),
                    "expires_at": (datetime.fromtimestamp(exp, timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ")
                                   if exp is not None else None)})
    dropped += max(0, len(raw) - 200)
    out.sort(key=lambda x: (_LEVEL_RANK[x["level"]], x["id"]))
    dropped += max(0, len(out) - MAX_NOTICES)
    return out[:MAX_NOTICES], dropped


def validate_snapshot(raw, notices=False):
    """bytes or str -> (snapshot, None) or (None, error). Every string is
    text, every enum is mapped, every bound enforced (§4.5). `notices`: the
    verified manifest opted in (§4.7); otherwise the field is ignored."""
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > STDOUT_CAP:
            return None, "the snapshot is larger than 1 MiB"
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, "the snapshot is not UTF-8"
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        return None, "the collector's output is not JSON"
    if not isinstance(obj, dict):
        return None, "the snapshot is not a JSON object"
    if obj.get("schema") != SNAPSHOT_SCHEMA:
        return None, f"the snapshot's schema is not {SNAPSHOT_SCHEMA!r}"
    view = obj.get("view") if isinstance(obj.get("view"), list) else []
    snap = {"schema": SNAPSHOT_SCHEMA, "ok": obj.get("ok") is True,
            "generated_at": _text(obj.get("generated_at"), 40),
            "error": _text(obj.get("error"), CELL_CAP) or None,
            "progress": None,
            "view": [_validate_block(b) for b in view[:MAX_BLOCKS]]}
    pr = obj.get("progress")
    if isinstance(pr, dict):
        pct = pr.get("done_pct")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct):
            pct = None
        snap["progress"] = {"phase": _text(pr.get("phase"), 40),
                            "done_pct": None if pct is None else max(0, min(100, pct)),
                            "note": _text(pr.get("note"), LABEL_CAP)}
    if len(view) > MAX_BLOCKS:
        snap["truncated"] = {"blocks": len(view) - MAX_BLOCKS}
    if notices:
        snap["notices"], nd = _validate_notices(obj.get("notices"))
        if nd:
            snap["notices_dropped"] = nd
    return snap, None


# ---------------------------------------------------------------------------
# one run, and the hub's runner

def snapshot_path(name):
    return module_dir(name) / "snapshot.json"


def status_path(name):
    return module_dir(name) / "status.json"


def load_status(name):
    return _read_json(status_path(name), {}) or {}


def run_collector(name, *, before_run=None):
    """Verify, run, validate, cache. A failed run keeps the last good
    snapshot and records the error. -> status dict. Never raises."""
    st = load_status(name)
    st["last_run_at"] = _now_iso()
    try:
        with _Lock(name, blocking=False):
            with prepared(name, "collector") as run:
                argv, env, cwd, sandboxed = run
                manifest = run.manifest           # of the verified copy
                if before_run:
                    try:
                        before_run(name, manifest)
                    except Exception as e:  # noqa: BLE001 — a report failure is not fatal
                        st["report_error"] = f"{type(e).__name__}: {str(e)[:200]}"
                t0 = time.monotonic()
                rc, out, err, why = run_capped(argv, env, cwd,
                                               manifest["collector"]["timeout_s"])
            st["duration_s"] = round(time.monotonic() - t0, 3)
            st["sandboxed"] = sandboxed
            if why:
                raise ModuleError(why)
            if rc != 0:
                tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
                raise ModuleError(f"the collector exited {rc}: {tail[0][:300]}")
            snap, verr = validate_snapshot(out, notices=manifest.get("notices") is True)
            if verr:
                raise ModuleError(verr)
            _write_json(snapshot_path(name), snap)
            # What the verified copy said, for the read-time notice rules:
            # never re-read from the installed files, which can change.
            st["verified"] = {"notices": manifest.get("notices") is True,
                              "every_s": int(manifest["collector"]["every_s"])}
            st.update(state="ok", error=None, fresh_at=_now_iso(), runs_ok=st.get("runs_ok", 0) + 1)
    except ModuleError as e:
        st.update(state="failing", error=str(e)[:500])
    except Exception as e:  # noqa: BLE001 — the runner thread must survive anything
        st.update(state="failing", error=f"{type(e).__name__}: {str(e)[:300]}")
    try:
        if module_dir(name).is_dir():
            _write_json(status_path(name), st)
    except OSError:
        pass
    return st


def summary(name, pin=None):
    """The public face of one module for routes, doctor and the palette."""
    pin = pin if pin is not None else (load_pins().get(name) or {})
    st = load_status(name)
    sandboxed, _ = module_sandbox.available()
    if not pin.get("enabled"):
        state = "disabled"
    elif not sandboxed and not pin.get("unsandboxed_ack"):
        state = "unacknowledged"
    else:
        state = st.get("state") or "never-run"
    if RUNNER and RUNNER.is_running(name):
        state = "running"
    return {"name": name, "title": pin.get("title") or name,
            "summary": pin.get("summary", ""), "enabled": bool(pin.get("enabled")),
            "state": state, "sandboxed": bool(sandboxed),
            "error": pin.get("disabled_reason") if not pin.get("enabled") else st.get("error"),
            "last_run_at": st.get("last_run_at"), "fresh_at": st.get("fresh_at"),
            "fetch": _fetch_summary(name, pin)}


def _fetch_summary(name, pin):
    if not pin.get("grants"):
        return {}
    import module_fetch
    return module_fetch.summary(name, pin)


def notices(now=None):
    """The rail's module notices (§4.7): live notices of every enabled,
    runnable, opted-in module, at most NOTICES_PER_MODULE each and
    NOTICES_SENT in all (the page draws 8 after Not now). The opt-in and
    period come from the verified copy of the run that made the snapshot
    (status `verified`), never from installed files. -> {"items": [...],
    "more": n}. Never raises."""
    now = time.time() if now is None else now
    items, more = [], 0
    try:
        pins = load_pins()
        sandboxed, _ = module_sandbox.available()
    except Exception:  # noqa: BLE001 — the state route must not fail on a module
        return {"items": [], "more": 0}
    for name in sorted(pins):
        try:
            pin = pins[name] or {}
            if not pin.get("enabled") or (not sandboxed and not pin.get("unsandboxed_ack")):
                continue
            st = load_status(name)
            ver = st.get("verified") if isinstance(st.get("verified"), dict) else {}
            if ver.get("notices") is not True:
                continue
            fresh = _parse_iso(st.get("fresh_at"))
            if fresh is None:
                continue
            every = ver.get("every_s")
            if isinstance(every, bool) or not isinstance(every, int):
                continue
            every = max(every, MIN_EVERY_S)
            if now >= fresh + NOTICE_MAX_AGE_S:
                continue
            # A failing module keeps the `bad` notices of its last good
            # snapshot until NOTICE_MAX_AGE_S, marked with that snapshot's age
            # (Delegates plan Q7): a broken collector must not drop its own
            # alarm from the rail. Everything else still goes at 2 x every_s.
            last_good = now >= fresh + 2 * every
            if last_good and st.get("state") != "failing":
                continue
            snap = _read_json(snapshot_path(name), {}) or {}
            live = []
            for n in snap.get("notices") or []:
                if last_good and n.get("level") != "bad":
                    continue
                # Checked again at read: the stored file is not trusted either.
                if not isinstance(n, dict) or n.get("level") not in NOTICE_LEVELS or \
                        not isinstance(n.get("id"), str) or not NOTICE_ID_RE.match(n["id"]) or \
                        not _text(n.get("title"), NOTICE_TITLE_CAP).strip():
                    continue
                exp = _parse_iso(n.get("expires_at"))
                if exp is not None and now >= exp:
                    continue
                text = _text(n.get("text"), NOTICE_TEXT_CAP)
                if last_good:
                    suffix = f"last good {_age_text(now - fresh)} ago"
                    text = _text(text, NOTICE_TEXT_CAP - len(suffix) - 2)
                    text = (text + "; " if text else "") + suffix
                live.append({"module": name, "moduleTitle": pin.get("title") or name,
                             "id": _text(n.get("id"), 64), "level": n["level"],
                             "title": _text(n.get("title"), NOTICE_TITLE_CAP),
                             "text": text})
            live.sort(key=lambda x: (_LEVEL_RANK[x["level"]], x["id"]))
            more += max(0, len(live) - NOTICES_PER_MODULE)
            items.extend(live[:NOTICES_PER_MODULE])
        except Exception:  # noqa: BLE001 — one broken module costs only itself
            continue
    items.sort(key=lambda x: (_LEVEL_RANK[x["level"]], x["module"], x["id"]))
    more += max(0, len(items) - NOTICES_SENT)
    return {"items": items[:NOTICES_SENT], "more": more}


def detail(name):
    pins = load_pins()
    if name not in pins:
        return None
    d = summary(name, pins[name])
    d["snapshot"] = _read_json(snapshot_path(name))
    return d


class Runner:
    """The hub's runner thread: runs each enabled module when due, one at a
    time per module, and honours rate-limited refresh requests."""

    def __init__(self, before_run=None, tick_s=5, grace_s=15):
        self.before_run = before_run
        self.grace_s = grace_s            # serve first; collectors start after
        self.tick_s = tick_s
        self._due = {}
        self._queued = set()
        self._running = set()
        self._last_refresh = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self.errors = 0

    def start(self):
        threading.Thread(target=self._loop, name="module-runner", daemon=True).start()
        return self

    def stop(self):
        self._stop = True
        self._wake.set()

    def is_running(self, name):
        with self._lock:
            return name in self._running

    def refresh(self, name):
        """-> (ok, why). One queued run; at most one per REFRESH_MIN_GAP_S."""
        pins = load_pins()
        if name not in pins or not pins[name].get("enabled"):
            return False, "no such enabled module"
        now = time.monotonic()
        with self._lock:
            if name in self._queued:
                return True, "already queued"
            last = self._last_refresh.get(name)
            if last is not None and now - last < REFRESH_MIN_GAP_S:
                return False, f"refreshed {int(now - last)} s ago; wait a little"
            self._last_refresh[name] = now
            self._queued.add(name)
        self._wake.set()
        return True, "queued"

    def _loop(self):
        while not self._stop:
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                self.errors += 1
            self._wake.wait(self.tick_s)
            self._wake.clear()

    def tick(self):
        now = time.monotonic()
        for name, pin in sorted(load_pins().items()):
            if not pin.get("enabled"):
                continue
            with self._lock:
                queued = name in self._queued
                if name not in self._due:
                    self._due[name] = now + self.grace_s
                due = self._due[name] <= now
                if not (queued or due) or name in self._running:
                    continue
                self._queued.discard(name)
                self._running.add(name)
            every = DEFAULT_EVERY_S
            try:
                m = _read_json(module_dir(name) / pin.get("commit", "-") / "module.json", {})
                every = int(((m or {}).get("collector") or {}).get("every_s") or every)
            except (TypeError, ValueError):
                pass
            try:
                run_collector(name, before_run=self.before_run)
            finally:
                with self._lock:
                    self._running.discard(name)
                    self._due[name] = time.monotonic() + max(every, MIN_EVERY_S)
        self.fetch_tick()

    def fetch_tick(self, now=None):
        """Granted fetchers that are due, one at a time (plan §6.7). A fetch
        that stores a result queues its module's collector."""
        import module_fetch
        for name, pin in sorted(load_pins().items()):
            if not pin.get("enabled") or not pin.get("grants"):
                continue
            for key in module_fetch.due(name, pin, now):
                with self._lock:
                    if name in self._running:
                        break
                    self._running.add(name)
                try:
                    st = module_fetch.run_fetch(name, key)
                finally:
                    with self._lock:
                        self._running.discard(name)
                if st.get("state") == "ok":
                    with self._lock:
                        self._queued.add(name)


RUNNER = None


def counts():
    """For /health: numbers only."""
    pins = load_pins()
    failing = 0
    for name, pin in pins.items():
        if pin.get("enabled") and load_status(name).get("state") == "failing":
            failing += 1
    return {"modules": len(pins), "modules_failing": failing}


# ---------------------------------------------------------------------------
# CLI (corral-light module ..., and the enabled-module fall-through)

USAGE = """usage: corral-light module <verb>
  add <name|path|url> [--ref REF] [--confirm NAME] [--ack unsandboxed]
  list
  show <name>
  run <name>                  run the collector once, now
  enable <name> | disable <name>
  update <name> [--ref REF] [--confirm NAME]
  rollback <name>
  remove <name> [--purge]
  doctor <name>               the module's own doctor
  key add <key> | key list | key remove <key>
                              billing API keys, kept in the key directory (mode 0600)
  grant <name> <key> <vendor> [--param name=value]...
                              let a module's fetcher use one key for one vendor
  revoke <name> <key>         take a grant back; its fetched results are deleted
  fetch <name> [<key>]        run its granted fetchers now
An enabled module is also a verb: corral-light <name> ..."""


def _opt(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            raise ModuleError(f"{flag} needs a value")
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return None


def _ask(prompt):
    if not sys.stdin.isatty():
        return None
    try:
        return input(prompt).strip()
    except EOFError:
        return None


def run_interactive(name, entry_key, args):
    """CLI and doctor runs: verified, sandboxed, the terminal passed through.
    The module's lock is held until the process ends, so an update cannot
    prune the generation under a long `setup` (round three, finding 2)."""
    with _Lock(name):
        with prepared(name, entry_key, args, interactive=True) as (argv, env, cwd, _sb):
            proc = subprocess.Popen(argv, env=env if env is not None else {}, cwd=cwd,
                                    preexec_fn=module_sandbox.limits_fn())
            try:
                return proc.wait()
            except KeyboardInterrupt:
                proc.send_signal(signal.SIGINT)
                return proc.wait()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args or args[0] in ("-h", "--help", "help"):
            print(USAGE, flush=True)
            return 0 if args else 2
        verb, rest = args[0], args[1:]
        if verb == "add":
            ref, confirm, ack = _opt(rest, "--ref"), _opt(rest, "--confirm"), _opt(rest, "--ack")
            if len(rest) != 1:
                raise ModuleError("usage: module add <name|path|url>")
            add(rest[0], ref=ref, confirm=confirm, ack_unsandboxed=ack, ask=_ask)
        elif verb == "list":
            pins = load_pins()
            if not pins:
                print("No modules installed. Try: corral-light module add finops", flush=True)
            for name in sorted(pins):
                s = summary(name, pins[name])
                print(f"{name:16} {s['state']:14} {pins[name].get('commit', '')[:12]}  "
                      f"{'sandboxed' if s['sandboxed'] else 'UNSANDBOXED'}"
                      + (f"  {s['error']}" if s.get("error") else ""), flush=True)
        elif verb == "show" and len(rest) == 1:
            d = detail(rest[0])
            if d is None:
                raise ModuleError(f"no module named {rest[0]!r} is installed")
            print(json.dumps(d, indent=2), flush=True)
        elif verb == "run" and len(rest) == 1:
            _pin(rest[0])
            st = run_collector(rest[0])
            print(f"{rest[0]}: {st.get('state')}" + (f" — {st['error']}" if st.get("error") else ""),
                  flush=True)
            return 0 if st.get("state") == "ok" else 1
        elif verb in ("enable", "disable") and len(rest) == 1:
            set_enabled(rest[0], verb == "enable")
        elif verb == "update":
            ref, confirm = _opt(rest, "--ref"), _opt(rest, "--confirm")
            if len(rest) != 1:
                raise ModuleError("usage: module update <name>")
            update(rest[0], ref=ref, confirm=confirm, ask=_ask)
        elif verb == "rollback" and len(rest) == 1:
            rollback(rest[0])
        elif verb == "remove":
            purge = "--purge" in rest
            rest = [a for a in rest if a != "--purge"]
            if len(rest) != 1:
                raise ModuleError("usage: module remove <name> [--purge]")
            remove(rest[0], purge=purge)
        elif verb == "doctor" and len(rest) == 1:
            return run_interactive(rest[0], "doctor", [])
        elif verb in ("key", "keys", "grant", "revoke", "fetch"):
            import module_fetch
            return _fetch_cli(module_fetch, verb, rest)
        else:
            print(USAGE, file=sys.stderr, flush=True)
            return 2
        return 0
    except ModuleError as e:
        print(f"corral-light module: {e}", file=sys.stderr, flush=True)
        return 1


def _fetch_cli(mf, verb, rest):
    """`module key add|list|remove`, `module grant|revoke`, `module fetch`."""
    if verb == "keys" or (verb == "key" and rest[:1] == ["list"] and len(rest) == 1):
        rows = mf.key_list()
        if not rows:
            print(f"No keys. Add one with: corral-light module key add <name>  "
                  f"(stored in {mf.keys_dir()}, mode 0600)", flush=True)
        for name, ok, why, users in rows:
            print(f"{name:24} {'ok' if ok else 'REFUSED: ' + why}"
                  + (f"  granted to {', '.join(users)}" if users else ""), flush=True)
        return 0
    if verb == "key" and len(rest) == 2 and rest[0] == "add":
        import getpass
        if sys.stdin.isatty():
            secret = getpass.getpass(f"Paste the key for {rest[1]!r} (not shown): ")
        else:
            secret = sys.stdin.read(mf.MAX_KEY_BYTES + 1)
        p = mf.key_add(rest[1], secret)
        print(f"Stored key {rest[1]!r} in {p} (mode 0600). Grant it with: "
              f"corral-light module grant <module> {rest[1]} <vendor>", flush=True)
        return 0
    if verb == "key" and len(rest) == 2 and rest[0] == "remove":
        mf.key_remove(rest[1])
        print(f"Removed key {rest[1]!r}.", flush=True)
        return 0
    if verb == "grant":
        params = {}
        while "--param" in rest:
            kv = _opt(rest, "--param")
            k, eq, v = kv.partition("=")
            if not eq:
                raise ModuleError("--param takes name=value")
            params[k] = v
        if len(rest) != 3:
            raise ModuleError("usage: module grant <name> <key> <vendor> [--param name=value]...")
        hosts = mf.grant(rest[0], rest[1], rest[2], params)
        print(f"{rest[0]} may now use key {rest[1]!r} for {rest[2]}, reaching only "
              f"{', '.join(hosts)}. It runs at the next due time, or now with: "
              f"corral-light module fetch {rest[0]}", flush=True)
        return 0
    if verb == "revoke" and len(rest) == 2:
        mf.revoke(rest[0], rest[1])
        print(f"Revoked key {rest[1]!r} from {rest[0]}; its fetched results are deleted.",
              flush=True)
        return 0
    if verb == "fetch" and len(rest) in (1, 2):
        pin = _pin(rest[0])
        keys = rest[1:] or sorted(pin.get("grants") or {})
        if not keys:
            raise ModuleError(f"{rest[0]} has no granted keys")
        bad = 0
        for k in keys:
            st = mf.run_fetch(rest[0], k)
            print(f"{rest[0]} {k}: {st.get('state')}"
                  + (f" — {st['error']}" if st.get("error") else "")
                  + (f"  [{'; '.join(st['hosts'])}]" if st.get("hosts") else ""), flush=True)
            bad += st.get("state") != "ok"
        if not bad:
            run_collector(rest[0])
        return 1 if bad else 0
    print(USAGE, file=sys.stderr, flush=True)
    return 2


def dispatch(argv=None):
    """`corral-light <verb> ...` for a verb the wrapper does not know: run it
    only if it is the exact name of an enabled module with a CLI (M7)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    name, rest = args[0], args[1:]
    if not NAME_RE.match(name) or name in CORE_VERBS:
        return 2
    pin = load_pins().get(name)
    if not pin or not pin.get("enabled"):
        return 2
    try:
        return run_interactive(name, "cli", rest)
    except ModuleError as e:
        print(f"corral-light {name}: {e}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--dispatch":
        sys.exit(dispatch(sys.argv[2:]))
    sys.exit(main())
