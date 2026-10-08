"""Module fetchers: opt-in vendor billing API readers (docs/finops-module-plan.md §6.7).

A fetcher is a module's second, separate entry. It never runs on its own:
the operator stores a key with `module key add`, then grants that one key
to one module for one vendor with `module grant`. Each run then gets:

- the verified run copy of the module's code, read-only;
- that one key file, read-only, at KEY_IN_SANDBOX, and nothing else from
  the config dir: no other key, no collector data, no usage `reads`, no
  feed;
- a writable dir of its own (not the collector's data dir);
- egress only through a CONNECT proxy the hub runs for that run, which
  allows the vendor's exact hosts (modules.FETCH_VENDORS) and never a lane's
  sign-in host. The sandbox has its own network namespace, so the proxy's
  unix socket is the only way out.

Fetchers need the sandbox: there is no unsandboxed acknowledgement for a
process that holds a key and reaches the network. The result is one JSON
object under 1 MiB on stdout. The core refuses it if it contains the key's
bytes, scrubs errors of keys, credentials headers and URLs, and stores it in
modules.fetch_dir(<module>), which the collector reads read-only.
"""
import base64
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
from pathlib import Path

import module_sandbox
import modules
import review_egress
from modules import ModuleError

KEY_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
MAX_KEY_BYTES = 16 << 10
MIN_KEY_BYTES = 16              # shorter is no vendor key, and too short to find in output
RESULT_CAP = 1 << 20
RESULT_SCHEMA = "corral-light.fetch-result/1"
KEY_IN_SANDBOX = "/run/corral/key"
EGRESS_IN_SANDBOX = "/run/corral/egress.py"
SOCK_DIR_IN_SANDBOX = "/run/corral/sock"
EGRESS_PY = Path(review_egress.__file__).resolve()
# Public CA roots for TLS, nothing secret. Bound only where they exist.
CA_DIRS = ("/etc/ssl", "/etc/ca-certificates", "/etc/pki")
BACKOFF_MIN_S = 3600                  # retries at most hourly per grant
MAX_ERROR = 300
# Non-secret settings a grant may carry (a billing export table, a region):
# a few short printable values, passed as CORRAL_FETCH_PARAM_<NAME>.
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PARAM_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,200}$")
MAX_PARAMS = 8


# ── keys ────────────────────────────────────────────────────────────────

def keys_dir():
    return modules.CONFIG / "keys"


def check_key_name(name):
    if not isinstance(name, str) or not KEY_NAME_RE.match(name):
        raise ModuleError(f"key name {name!r} must match [a-z0-9][a-z0-9-]{{0,47}}")
    return name


def key_path(name):
    """-> the key file's path, after every rule in §6.7. Raises ModuleError."""
    check_key_name(name)
    d = keys_dir()
    try:
        dst = os.lstat(d)
    except OSError:
        raise ModuleError("there is no key directory yet; add a key with "
                          "`corral-light module key add <name>`") from None
    if not stat.S_ISDIR(dst.st_mode) or dst.st_uid != os.getuid():
        raise ModuleError(f"{d} must be a directory you own (not a symlink)")
    if dst.st_mode & 0o077:
        raise ModuleError(f"{d} must not be readable by others (mode 0700)")
    p = d / name
    try:
        st = os.lstat(p)
    except OSError:
        raise ModuleError(f"no key named {name!r}") from None
    if stat.S_ISLNK(st.st_mode):
        raise ModuleError(f"key {name!r} is a symlink; keys must be plain files")
    if not stat.S_ISREG(st.st_mode):
        raise ModuleError(f"key {name!r} is not a regular file")
    if st.st_uid != os.getuid():
        raise ModuleError(f"key {name!r} is not owned by you")
    if st.st_mode & 0o077:
        raise ModuleError(f"key {name!r} is readable by others; it must be mode 0600")
    if st.st_size == 0 or st.st_size > MAX_KEY_BYTES:
        raise ModuleError(f"key {name!r} is empty or larger than 16 KiB")
    if os.path.dirname(os.path.realpath(p)) != os.path.realpath(d):
        raise ModuleError(f"key {name!r} resolves outside the key directory")
    return p


def read_key(name):
    """The key's bytes, read through a descriptor that refuses a symlink and
    checked on that same descriptor, so a swap after key_path() changes
    nothing. Raises ModuleError."""
    p = key_path(name)
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise ModuleError(f"key {name!r} could not be opened safely") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or \
                st.st_mode & 0o077 or st.st_nlink != 1 or st.st_size > MAX_KEY_BYTES:
            raise ModuleError(f"key {name!r} changed or is hard-linked; refused")
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(MAX_KEY_BYTES + 1)
    finally:
        os.close(fd)
    if len(data.strip()) < MIN_KEY_BYTES:
        raise ModuleError(f"key {name!r} is shorter than {MIN_KEY_BYTES} characters")
    return data


def key_add(name, secret):
    """Store a key: one file, mode 0600, in a 0700 directory. `secret` is
    the key's text; it is never echoed or logged."""
    check_key_name(name)
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    secret = secret.strip()
    if not secret:
        raise ModuleError("the key is empty; nothing stored")
    if len(secret) > MAX_KEY_BYTES:
        raise ModuleError("the key is larger than 16 KiB; nothing stored")
    if len(secret) < MIN_KEY_BYTES:
        raise ModuleError(f"the key is shorter than {MIN_KEY_BYTES} characters; no vendor key "
                          f"is, so nothing was stored")
    d = keys_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:                                   # never follow a swapped-in link
        dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise ModuleError(f"{d} is not a plain directory; refused") from None
    try:
        os.fchmod(dfd, 0o700)
    finally:
        os.close(dfd)
    p = d / name
    if os.path.lexists(p):
        raise ModuleError(f"a key named {name!r} exists; remove it first")
    tmp = d / f".{name}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(secret + b"\n")
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, p)                       # never replaces an existing key
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    key_path(name)
    return p


def key_list():
    """-> [(name, ok, why, granted_to)] for every file in the key directory."""
    d = keys_dir()
    if not d.is_dir():
        return []
    pins = modules.load_pins()
    out = []
    for entry in sorted(os.listdir(d)):
        if entry.startswith("."):
            continue
        try:
            key_path(entry)
            ok, why = True, ""
        except ModuleError as e:
            ok, why = False, str(e)
        users = sorted(f"{m}:{g['vendor']}" for m, pin in pins.items()
                       for k, g in (pin.get("grants") or {}).items() if k == entry)
        out.append((entry, ok, why, users))
    return out


def key_remove(name):
    check_key_name(name)
    users = [m for m, pin in modules.load_pins().items() if name in (pin.get("grants") or {})]
    if users:
        raise ModuleError(f"key {name!r} is granted to {', '.join(users)}; revoke it first")
    p = keys_dir() / name
    if not os.path.lexists(p):
        raise ModuleError(f"no key named {name!r}")
    os.unlink(p)


# ── grants ──────────────────────────────────────────────────────────────

def _fetcher(name):
    pin = modules._pin(name)
    m = modules._read_json(modules.module_dir(name) / pin.get("commit", "-") / "module.json",
                           {}) or {}
    f = m.get("fetcher")
    if not isinstance(f, dict):
        raise ModuleError(f"{name} has no fetcher")
    return pin, f


def check_params(params):
    params = dict(params or {})
    if len(params) > MAX_PARAMS:
        raise ModuleError(f"at most {MAX_PARAMS} parameters per grant")
    for k, v in params.items():
        if not isinstance(k, str) or not PARAM_NAME_RE.match(k):
            raise ModuleError(f"parameter name {k!r} must match [a-z][a-z0-9_]{{0,31}}")
        if not isinstance(v, str) or not PARAM_VALUE_RE.match(v):
            raise ModuleError(f"parameter {k} must be 1 to 200 characters of letters, digits "
                              f"and ._:/@+-")
    return params


def grant(name, key, vendor, params=None):
    params = check_params(params)
    pin, f = _fetcher(name)
    if vendor not in modules.FETCH_VENDORS:
        raise ModuleError(f"vendor {vendor!r} is not one of: "
                          f"{', '.join(sorted(modules.FETCH_VENDORS))}")
    if vendor not in (f.get("vendors") or []):
        raise ModuleError(f"{name} does not declare a {vendor} fetcher")
    key_path(key)
    with modules._Lock(name):               # never mid-fetch
        grants = dict(modules._pin(name).get("grants") or {})
        grants[key] = {"vendor": vendor, "granted_at": modules._now_iso(), "params": params}
        modules.update_pin(name, grants=grants)
    return modules.FETCH_VENDORS[vendor]


def revoke(name, key):
    """Waits for a fetch in flight, so its result cannot land after this."""
    with modules._Lock(name):
        grants = dict(modules._pin(name).get("grants") or {})
        if key not in grants:
            raise ModuleError(f"{name} has no grant for key {key!r}")
        grants.pop(key)
        modules.update_pin(name, grants=grants)
        _drop_results(name, key)


def _drop_results(name, key):
    for p in (modules.fetch_dir(name) / f"{key}.json",):
        try:
            p.unlink()
        except OSError:
            pass
    st = load_status(name)
    st.pop(key, None)
    _save_status(name, st)
    shutil.rmtree(work_dir(name, key), ignore_errors=True)


def work_dir(name, key):
    return modules.STATE / "module-fetchwork" / modules.check_name(name) / check_key_name(key)


def status_path(name):
    return modules.STATE / "modules" / modules.check_name(name) / "fetch-status.json"


def load_status(name):
    st = modules._read_json(status_path(name), {})
    return st if isinstance(st, dict) else {}


def _save_status(name, st):
    if modules.module_dir(name).is_dir():
        modules._write_json(status_path(name), st)


# ── secrets in output ───────────────────────────────────────────────────

def needles(key_bytes):
    """Byte strings whose presence in a result means the key leaked: the
    key itself, its long lines (a PEM body), the secret strings of a JSON
    key, and each of those base64-encoded."""
    raw = key_bytes.strip()
    found = {raw}
    for line in raw.splitlines():
        line = line.strip()
        if len(line) >= 24 and not line.startswith(b"-----"):
            found.add(line)
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        for k in ("private_key", "private_key_id", "client_secret", "refresh_token"):
            v = doc.get(k)
            if isinstance(v, str) and len(v) >= 16:
                found.add(v.encode())
                for line in v.encode().splitlines():
                    if len(line.strip()) >= 24 and not line.startswith(b"-----"):
                        found.add(line.strip())
    out = {raw} if raw else set()               # the key itself, whatever its length
    for n in found:
        if len(n) >= 12 or n == raw:
            out.add(n)
            out.add(base64.b64encode(n))
            out.add(base64.urlsafe_b64encode(n))
            out.add(n.hex().encode())
            out.add(json.dumps(n.decode("utf-8", "replace"))[1:-1].encode())
    return sorted(out, key=len, reverse=True)


_HEADER = re.compile(r"(?im)^\s*(authorization|x-api-key|proxy-authorization|cookie)\s*:.*$")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL = re.compile(r"https?://\S+")


def scrub(text, ns=()):
    """Error text safe to show: no key bytes, no credential headers, no URLs."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    for n in ns:
        text = text.replace(n.decode("utf-8", "replace"), "[key]")
    text = _HEADER.sub(r"\1: [redacted]", text)
    text = _BEARER.sub(r"\1 [redacted]", text)
    text = _URL.sub("[url]", text)
    return text[:MAX_ERROR]


# ── one run ─────────────────────────────────────────────────────────────

def _ca_dirs():
    return [d for d in CA_DIRS if os.path.isdir(d)]


DARWIN = sys.platform == "darwin"


def build_fetch(name, key, run_dir, sock_dir, egress_port=None):
    """-> (argv, cwd, timeout_s, every_s, vendor, key_bytes) for one granted
    key, from the verified run copy. Raises ModuleError on anything not
    allowed. Linux reaches the proxy through a shim and the unix socket in
    sock_dir; macOS through `egress_port` on loopback, which its profile
    allows and nothing else."""
    code, manifest, pin = modules.verify(name, run_copy=run_dir)
    entry = manifest.get("fetcher")
    if not entry:
        raise ModuleError(f"{name} has no fetcher")
    g = (pin.get("grants") or {}).get(key)
    if not g:
        raise ModuleError(f"{name} has no grant for key {key!r}")
    vendor = g.get("vendor")
    if vendor not in entry["vendors"] or vendor not in modules.FETCH_VENDORS:
        raise ModuleError(f"{name}'s fetcher no longer declares {vendor!r}; grant again")
    sandboxed, why = module_sandbox.available()
    if not sandboxed:
        raise ModuleError(f"fetchers run only in the module sandbox, and this host has "
                          f"none ({why})")
    key_bytes = read_key(key)
    # The run binds a private copy in its own 0700 dir: what was checked is
    # what is bound, whatever happens to the key file meanwhile.
    kp = Path(sock_dir) / "key"
    kfd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(kfd, "wb") as f:
        f.write(key_bytes)
    wd = work_dir(name, key)
    wd.mkdir(parents=True, exist_ok=True, mode=0o700)
    hosts = modules.FETCH_VENDORS[vendor]
    env = {"LANG": "C.UTF-8", "TZ": modules._host_tz(), "CORRAL_MODULE_API": str(modules.CORE_API),
           "CORRAL_MODULE_NAME": name, "CORRAL_MODULE_DATA": str(wd),
           "CORRAL_MODULE_SANDBOXED": "1", "CORRAL_FETCH_VENDOR": vendor,
           "CORRAL_FETCH_KEY": KEY_IN_SANDBOX, "CORRAL_FETCH_KEY_NAME": key,
           "CORRAL_FETCH_HOSTS": ",".join(hosts)}
    for k, v in check_params(g.get("params")).items():
        env["CORRAL_FETCH_PARAM_" + k.upper()] = v
    interp = modules._interpreter()
    script = [interp, "-I", "-B", f"{modules.SANDBOX_CODE}/{entry['script']}"] + list(entry["args"])
    binds = [(str(code), modules.SANDBOX_CODE), (str(kp), KEY_IN_SANDBOX)]
    if DARWIN:
        if not egress_port:
            raise ModuleError("no egress port for the fetch")
        proxy = f"http://127.0.0.1:{int(egress_port)}"
        env.update({k: proxy for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                                       "http_proxy", "ALL_PROXY", "all_proxy")})
        env["NO_PROXY"] = env["no_proxy"] = ""
        argv = module_sandbox.build_argv(script, read_only=_ca_dirs(), data_dir=str(wd),
                                         env=env, file_binds=binds, proxy_port=int(egress_port))
    else:
        inner = [interp, "-I", "-B", EGRESS_IN_SANDBOX, f"{SOCK_DIR_IN_SANDBOX}/egress.sock",
                 "--"] + script
        argv = module_sandbox.build_argv(
            inner, read_only=_ca_dirs(), data_dir=str(wd), env=env,
            file_binds=binds + [(str(EGRESS_PY), EGRESS_IN_SANDBOX),
                                (str(sock_dir), SOCK_DIR_IN_SANDBOX)])
    return argv, str(wd), entry["timeout_s"], entry["every_s"], vendor, key_bytes


def _check_result(out, ns):
    if len(out) > RESULT_CAP:
        raise ModuleError("the fetcher's result is larger than 1 MiB")
    for n in ns:
        if n in out:
            raise ModuleError("the fetcher's result contains the key; refused and not stored")
    try:
        obj = json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ModuleError("the fetcher's output is not one JSON document") from None
    if not isinstance(obj, dict):
        raise ModuleError("the fetcher's result is not a JSON object")
    # Again after parsing: \uXXXX escapes rebuild the key in the object that
    # is stored. Encodings past these are not caught here; the fetcher is
    # pinned, verified code the operator chose to grant a key to.
    for canon in (json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                  json.dumps(obj).encode("utf-8")):
        for n in ns:
            if n in canon:
                raise ModuleError("the fetcher's result contains the key; refused and not stored")
    return obj


def run_fetch(name, key, now=None):
    """Verify, run in the fetch profile behind the exact-host proxy, check,
    store. -> this grant's status dict. Never raises."""
    now = time.time() if now is None else now
    st_all = load_status(name)
    st = dict(st_all.get(key) or {})
    st["last_run_at"] = modules._now_iso()
    seen = []
    ns = ()
    every = FETCH_DEFAULT_EVERY
    egress = None
    sock_dir = None
    try:
        check_key_name(key)
        with modules._Lock(name, blocking=False):
            base = modules.module_dir(name)
            run_dir = base / f".fetch-{secrets.token_hex(8)}"
            egress_root = modules.STATE / "module-egress"
            egress_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            sock_dir = egress_root / f"{name}-{secrets.token_hex(6)}"
            sock_dir.mkdir(mode=0o700)
            try:
                granted = ((modules._pin(name).get("grants") or {}).get(key) or {})
                hosts = modules.FETCH_VENDORS.get(granted.get("vendor"), ())
                # The proxy first: on macOS the command needs its port.
                egress = review_egress.Egress(
                    sock_dir / "egress.sock", None,
                    allow=lambda h: review_egress.exact_allowed(h, hosts),
                    on_host=lambda h, ok: seen.append((str(h).lower().rstrip(".") if ok
                                                       else None, ok)), tcp=DARWIN)
                argv, cwd, timeout_s, every, vendor, key_bytes = build_fetch(
                    name, key, run_dir, sock_dir, egress_port=egress.port)
                ns = needles(key_bytes)
                if modules.FETCH_VENDORS[vendor] != hosts:
                    raise ModuleError("the grant changed while the fetch was prepared")
                st["vendor"] = vendor
                t0 = time.monotonic()
                rc, out, err, why = modules.run_capped(argv, None, cwd, timeout_s,
                                                       stdout_cap=RESULT_CAP)
                st["duration_s"] = round(time.monotonic() - t0, 3)
            finally:
                shutil.rmtree(run_dir, ignore_errors=True)
            if why:
                raise ModuleError(why)
            if rc != 0:
                tail = scrub(err, ns).strip().splitlines()[-1:] or [""]
                raise ModuleError(f"the fetcher exited {rc}: {tail[0]}")
            obj = _check_result(out, ns)
            fd = modules.fetch_dir(name)
            fd.mkdir(parents=True, exist_ok=True, mode=0o700)
            modules._write_json(fd / f"{key}.json",
                                {"schema": RESULT_SCHEMA, "module": name, "key": key,
                                 "vendor": vendor, "fetched_at": modules._now_iso(),
                                 "result": obj})
            st.update(state="ok", error=None, failures=0, fresh_at=modules._now_iso(),
                      next_due_at=now + every)
    except ModuleError as e:
        _failed(st, scrub(str(e), ns), now, every)
    except Exception as e:  # noqa: BLE001 — the runner thread must survive anything
        _failed(st, scrub(f"{type(e).__name__}: {e}", ns), now, every)
    finally:
        if egress is not None:
            egress.close()
        if sock_dir is not None:
            shutil.rmtree(sock_dir, ignore_errors=True)
    # Only names from the grant's own list; a refused target is the
    # fetcher's text, so it is counted, never kept (it could carry the key).
    allowed = sorted({h for h, ok in seen if ok and h in set(modules.FETCH_VENDORS.get(
        st.get("vendor"), ()))})
    refused = sum(1 for _h, ok in seen if not ok)
    st["hosts"] = [f"{h} allowed" for h in allowed] + (
        [f"{refused} other host(s) REFUSED"] if refused else [])
    try:
        cur = load_status(name)
        cur[key] = st
        _save_status(name, cur)
    except OSError:
        pass
    return st


FETCH_DEFAULT_EVERY = modules.FETCH_EVERY_S[0]


def _failed(st, error, now, every):
    n = int(st.get("failures") or 0) + 1
    st.update(state="failing", error=error, failures=n,
              next_due_at=now + min(max(BACKOFF_MIN_S, BACKOFF_MIN_S * 2 ** (n - 1)),
                                    max(every, BACKOFF_MIN_S)))


def due(name, pin, now=None):
    """Granted keys whose next run is due, oldest first."""
    now = time.time() if now is None else now
    st = load_status(name)
    out = []
    for key in sorted(pin.get("grants") or {}):
        nd = (st.get(key) or {}).get("next_due_at")
        if not isinstance(nd, (int, float)) or nd <= now:
            out.append(key)
    return out


def summary(name, pin):
    st = load_status(name)
    return {k: {"vendor": g.get("vendor"), "params": g.get("params") or {},
                "state": (st.get(k) or {}).get("state") or "never-run",
                "last_run_at": (st.get(k) or {}).get("last_run_at"),
                "error": (st.get(k) or {}).get("error")}
            for k, g in sorted((pin.get("grants") or {}).items())}
