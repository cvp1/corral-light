"""Core-run vendor reports for modules (docs/finops-module-plan.md §4.2, §4.4).

A module never sees or runs a vendor binary. For `grok-usage` the core
itself runs `grok usage <id>`, one sandboxed call per changed session, and
writes a reduced, validated report into the module feed:

    refresh_grok(out_dir) -> {"ran", "failed", "stale", "skipped", ...}

Per call (module_sandbox's allowlist profile):

- the resolved binary file only, bound read-only into a fresh scratch home;
  its real folder (which holds the login) is never bound;
- that one session's usage.json and summary.json, bound read-only under
  <scratch>/.grok/sessions/s/<id>/ (a neutral folder name: no host path
  reaches the sandbox); the chat history beside them is never in view;
- no network, resource limits, a timeout, killed by process group;
- stdout must be one JSON object under 1 MiB with the expected keys.

Only numeric usage fields (integers; a float anywhere refuses the report),
the session id, turn numbers, timestamps and model ids are kept, plus
parent_session_id and forked_at from summary.json and the binary's version.
A failed or hung call marks that session stale; the others go on.
"""
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

import module_sandbox

SCHEMA = "corral-light.grok-usage/1"
MAX_OUT = 1 << 20                 # stdout cap for one call
MAX_INPUT = 64 << 20              # usage.json / summary.json size we will bind
MAX_SUMMARY_READ = 4 << 20        # summary.json we parse ourselves
MAX_TURNS = 20000
MAX_SCAN = 10000                  # session dirs looked at per refresh
RETRY_FAILED = 3                  # attempts on an unchanged usage.json
ENC_DIR = "s"                     # the neutral cwd folder name inside the sandbox

SESSION_ID = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
NUM_KEY = re.compile(r"\A[A-Za-z][A-Za-z0-9_]{0,63}\Z")
MODEL_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,127}\Z")
STAMP = re.compile(r"\A[0-9][0-9TZ:.+ -]{0,63}\Z")
REF = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}\Z")
VERSION = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9 ._()\[\]+-]{0,127}\Z")
MAX_INT = (1 << 63) - 1

ELF_MAGIC = b"\x7fELF"


def is_elf(path):
    """True when `path` is a regular file starting with the ELF magic."""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            return False
        with open(path, "rb") as f:
            return f.read(4) == ELF_MAGIC
    except OSError:
        return False


# The binary check. Tests swap in a check that also takes their script
# stand-ins; the shipped check accepts ELF executables only.
BINARY_CHECK = is_elf

_LOCK = threading.Lock()


class BadReport(ValueError):
    """Output that is not a report we keep."""


# ---------------------------------------------------------------- locating

def grok_home(home=None):
    if home:
        return os.path.abspath(str(home))
    env = os.environ.get("CORRAL_GROK_HOME")
    return os.path.abspath(env) if env else os.path.join(os.path.expanduser("~"), ".grok")


def find_binary(ghome=None):
    """The resolved Grok binary file, or None. `which grok`, then
    <grok home>/bin/grok; the symlink is resolved so only the file is bound."""
    for cand in (shutil.which("grok"), os.path.join(grok_home(ghome), "bin", "grok")):
        if cand and os.path.exists(cand):
            return os.path.realpath(cand)
    return None


def _plain_file(path, expect_real):
    """Regular file whose resolved path is exactly `expect_real` (no
    symlink anywhere below the sessions root); its stat or None."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_INPUT:
        return None
    if os.path.realpath(path) != expect_real:
        return None
    return st


def scan_sessions(ghome):
    """[(id, session_dir, usage_stat)] for sessions with a usage.json and a
    summary.json, newest usage first. Ids that are not uuid-shaped, symlinked
    dirs or files, and duplicates (older copy) are left out."""
    root = os.path.join(ghome, "sessions")
    try:
        real_root = os.path.realpath(root)
        encs = list(os.scandir(real_root))
    except OSError:
        return []
    found = {}
    seen = 0
    for enc in encs:
        if not enc.is_dir(follow_symlinks=False):
            continue
        try:
            ids = list(os.scandir(enc.path))
        except OSError:
            continue
        for e in ids:
            seen += 1
            if seen > MAX_SCAN:
                break
            if not SESSION_ID.match(e.name) or not e.is_dir(follow_symlinks=False):
                continue
            d = os.path.join(real_root, enc.name, e.name)
            u = _plain_file(os.path.join(d, "usage.json"), os.path.join(d, "usage.json"))
            s = _plain_file(os.path.join(d, "summary.json"), os.path.join(d, "summary.json"))
            if u is None or s is None:
                continue
            old = found.get(e.name)
            if old is None or u.st_mtime_ns > old[2].st_mtime_ns:
                found[e.name] = (e.name, d, u)
    return sorted(found.values(), key=lambda t: t[2].st_mtime_ns, reverse=True)


# ---------------------------------------------------------------- running

def _uid_tasks():
    """Tasks (threads) this uid runs now; see module_sandbox.user_tasks."""
    return module_sandbox.user_tasks() or 0


def _limits_fn(nproc_base):
    """The sandbox's limits, counted in the parent (the child only calls
    setrlimit; preexec_fn is unsafe in a threaded process)."""
    return module_sandbox.limits_fn(nproc_base)


def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.kill()
    except OSError:
        pass


def _run(argv, timeout_s, preexec):
    """-> (status, stdout bytes). status: "ok", "exit N", "timeout",
    "too large", "spawn". stderr is drained and dropped."""
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True,
                             preexec_fn=preexec, close_fds=True)
    except OSError:
        return "spawn", b""
    out = bytearray()
    status = None
    sel = selectors.DefaultSelector()
    sel.register(p.stdout, selectors.EVENT_READ, "out")
    sel.register(p.stderr, selectors.EVENT_READ, "err")
    deadline = time.monotonic() + timeout_s
    try:
        open_pipes = 2
        while open_pipes:
            left = deadline - time.monotonic()
            if left <= 0:
                status = "timeout"
                break
            for key, _ in sel.select(min(left, 0.5)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    sel.unregister(key.fileobj)
                    open_pipes -= 1
                elif key.data == "out":
                    out += chunk
                    if len(out) > MAX_OUT:
                        status = "too large"
                        break
            if status:
                break
        if status:
            _kill_group(p)
        try:
            rc = p.wait(timeout=max(0.1, deadline - time.monotonic()) if not status else 5)
        except subprocess.TimeoutExpired:
            _kill_group(p)
            rc = p.wait(timeout=5)
            status = status or "timeout"
    finally:
        sel.close()
        for f in (p.stdout, p.stderr):
            try:
                f.close()
            except OSError:
                pass
    if status:
        return status, b""
    return ("ok" if rc == 0 else f"exit {rc}"), bytes(out)


def _sandboxed(binary, args, scratch_parent, timeout_s, preexec, files=()):
    """Run the bound binary with `args` in a fresh scratch home; files are
    (host source, path under the scratch home). The scratch is removed."""
    scratch = tempfile.mkdtemp(prefix="run-", dir=scratch_parent)
    try:
        inner_bin = os.path.join(scratch, ".grok", "bin", "grok")
        binds = [(binary, inner_bin)] + [(src, os.path.join(scratch, rel)) for src, rel in files]
        argv = module_sandbox.build_argv([inner_bin] + list(args), data_dir=scratch,
                                         file_binds=binds, env={})
        return _run(argv, timeout_s, preexec)
    except module_sandbox.SandboxError:
        return "sandbox", b""
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------- validating

class _Float(object):
    pass


def _reject_const(_):
    raise BadReport("non-finite number")


def _num(v):
    if isinstance(v, _Float):
        raise BadReport("float in a numeric field")
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    if v < 0 or v > MAX_INT:
        raise BadReport("integer out of range")
    return v


def _usage_block(d):
    """Numeric fields, primaryModelId, modelUsage {model: numeric}."""
    if not isinstance(d, dict):
        raise BadReport("usage block is not an object")
    out = {}
    for k, v in d.items():
        if k == "primaryModelId":
            if isinstance(v, str) and MODEL_ID.match(v):
                out[k] = v
        elif k == "modelUsage":
            if not isinstance(v, dict):
                continue
            mu = {}
            for model, block in list(v.items())[:64]:
                if not (isinstance(model, str) and MODEL_ID.match(model)) or not isinstance(block, dict):
                    continue
                mu[model] = {nk: n for nk, n in ((nk, _num(nv)) for nk, nv in block.items()
                                                 if NUM_KEY.match(str(nk)))
                             if n is not None}
            out[k] = mu
        elif k in ("endedAt", "startedAt", "updatedAt"):
            if isinstance(v, str) and STAMP.match(v):
                out[k] = v
            elif _num(v) is not None:
                out[k] = v
        elif isinstance(k, str) and NUM_KEY.match(k):
            n = _num(v)
            if n is not None:
                out[k] = n
    if "costUsdTicks" in d and "costUsdTicks" not in out:
        raise BadReport("costUsdTicks is not an integer")
    return out


def reduce_report(raw, sid):
    """bytes from `grok usage <id>` -> the kept fields. Raises BadReport."""
    if len(raw) > MAX_OUT:
        raise BadReport("too large")
    try:
        doc = json.loads(raw.decode("utf-8"), parse_float=lambda s: _Float(),
                         parse_constant=_reject_const)
    except (UnicodeDecodeError, ValueError) as e:
        if isinstance(e, BadReport):
            raise
        raise BadReport("not json")
    if not isinstance(doc, dict):
        raise BadReport("not an object")
    if doc.get("sessionId") != sid:
        raise BadReport("sessionId does not match")
    if not isinstance(doc.get("session"), dict) or not isinstance(doc.get("turns"), list):
        raise BadReport("missing session or turns")
    if len(doc["turns"]) > MAX_TURNS:
        raise BadReport("too many turns")
    rep = {"sessionId": sid, "session": _usage_block(doc["session"]), "turns": []}
    u = doc.get("updatedAt")
    if isinstance(u, str) and STAMP.match(u):
        rep["updatedAt"] = u
    for t in doc["turns"]:
        block = _usage_block(t)
        if not isinstance(block.get("turnNumber"), int):
            raise BadReport("turn without turnNumber")
        rep["turns"].append(block)
    return rep


def summary_refs(path):
    """(parent_session_id, forked_at) from summary.json, each a bounded
    plain string or None."""
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_SUMMARY_READ + 1)
        if len(raw) > MAX_SUMMARY_READ:
            return None, None
        doc = json.loads(raw.decode("utf-8"), parse_float=lambda s: None,
                         parse_constant=lambda s: None)
    except (OSError, UnicodeDecodeError, ValueError):
        return None, None
    if not isinstance(doc, dict):
        return None, None
    p, fk = doc.get("parent_session_id"), doc.get("forked_at")
    p = p if isinstance(p, str) and REF.match(p) else None
    fk = fk if isinstance(fk, str) and STAMP.match(fk) else None
    return p, fk


def _version(raw):
    try:
        line = raw.decode("utf-8").strip().splitlines()[0].strip()
    except (UnicodeDecodeError, IndexError):
        return None
    return line if VERSION.match(line) else None


# ---------------------------------------------------------------- files

def _write_json(path, obj):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, separators=(",", ":"), sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_index(out_dir):
    try:
        with open(os.path.join(out_dir, ".index.json")) as f:
            idx = json.load(f)
        return idx if isinstance(idx, dict) else {}
    except (OSError, ValueError):
        return {}


def _mark_stale(out_dir, sid, why):
    """Keep the last good report, flagged, so a module knows it is old."""
    path = os.path.join(out_dir, sid + ".json")
    try:
        with open(path) as f:
            rep = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(rep, dict):
        rep["stale"] = True
        rep["stale_reason"] = why
        _write_json(path, rep)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- refresh

def refresh_grok(out_dir, *, grok_home=None, binary=None, limit=20, timeout_s=10,
                 scratch_dir=None):
    """Run `grok usage` for sessions whose usage.json changed since their
    last report, newest first, at most `limit`; write <out_dir>/<id>.json.

    -> {"ran": calls made, "failed": of those, "stale": [ids that failed],
        "skipped": changed sessions not run this time, "reason": why
        nothing ran or None, "version": the binary's version or None}"""
    with _LOCK:
        return _refresh(out_dir, grok_home, binary, limit, timeout_s, scratch_dir)


def _refresh(out_dir, ghome_arg, binary, limit, timeout_s, scratch_dir):
    res = {"ran": 0, "failed": 0, "stale": [], "skipped": 0, "reason": None, "version": None}
    out_dir = os.path.abspath(str(out_dir))
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    ghome = grok_home(ghome_arg)
    idx = _load_index(out_dir)

    sessions = scan_sessions(ghome)
    todo = []
    for sid, d, st in sessions:
        prev = idx.get(sid) if isinstance(idx.get(sid), dict) else {}
        same = prev.get("m") == st.st_mtime_ns and prev.get("s") == st.st_size
        if same and prev.get("ok") and os.path.exists(os.path.join(out_dir, sid + ".json")):
            continue
        if same and not prev.get("ok") and int(prev.get("fails") or 0) >= RETRY_FAILED:
            continue
        todo.append((sid, d, st))

    def skip_all(reason):
        res["skipped"] = len(todo)
        res["reason"] = reason
        return res

    ok, why = module_sandbox.available()
    if not ok:
        return skip_all(f"module sandbox unavailable: {why}")
    if not todo:
        return res
    binary = os.path.realpath(str(binary)) if binary else find_binary(ghome)
    if not binary or not os.path.isfile(binary):
        return skip_all("grok binary not found")
    if not BINARY_CHECK(binary):
        return skip_all("grok binary is not an ELF executable; refused")

    res["skipped"] = max(0, len(todo) - limit)
    todo = todo[:limit]
    scratch_parent = os.path.abspath(scratch_dir) if scratch_dir else os.path.join(out_dir, ".scratch")
    os.makedirs(scratch_parent, mode=0o700, exist_ok=True)
    preexec = _limits_fn(_uid_tasks())
    try:
        status, raw = _sandboxed(binary, ["--version"], scratch_parent, timeout_s, preexec)
        version = _version(raw) if status == "ok" else None
        res["version"] = version
        for sid, d, st in todo:
            res["ran"] += 1
            base = os.path.join(".grok", "sessions", ENC_DIR, sid)
            status, raw = _sandboxed(
                binary, ["usage", sid], scratch_parent, timeout_s, preexec,
                files=[(os.path.join(d, "usage.json"), os.path.join(base, "usage.json")),
                       (os.path.join(d, "summary.json"), os.path.join(base, "summary.json"))])
            why = status
            rep = None
            if status == "ok":
                try:
                    rep = reduce_report(raw, sid)
                except BadReport as e:
                    why = str(e)
            prev = idx.get(sid) if isinstance(idx.get(sid), dict) else {}
            if rep is None:
                res["failed"] += 1
                res["stale"].append(sid)
                same = prev.get("m") == st.st_mtime_ns and prev.get("s") == st.st_size
                fails = (int(prev.get("fails") or 0) if same and not prev.get("ok") else 0) + 1
                idx[sid] = {"m": st.st_mtime_ns, "s": st.st_size, "ok": False,
                            "fails": fails, "why": why}
                _mark_stale(out_dir, sid, why)
                continue
            parent, forked = summary_refs(os.path.join(d, "summary.json"))
            rep.update({"schema": SCHEMA, "parent_session_id": parent, "forked_at": forked,
                        "grok_version": version, "generated_at": _now(), "stale": False})
            _write_json(os.path.join(out_dir, sid + ".json"), rep)
            idx[sid] = {"m": st.st_mtime_ns, "s": st.st_size, "ok": True}
    finally:
        _write_json(os.path.join(out_dir, ".index.json"), idx)
        if not scratch_dir:
            shutil.rmtree(scratch_parent, ignore_errors=True)
    return res
