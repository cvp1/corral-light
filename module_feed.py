"""module_feed — what modules may learn about Light (`module-feed/v1`).

docs/finops-module-plan.md §4.4. The hub writes, under
<state>/module-feed/v1/, atomically (temp file and rename; dirs 0700, files
0600):

- panes.json   per pane, open or closed within KEEP_DAYS: ids, lane, model,
               title, times, origin, and the usage figures from its
               `turn_end` events. No prompt text, no tool payloads.
- quota.json   per account (login fingerprint), per window, the newest
               vendor quota observation, written on arrival (note_quota).
- logins.json  sanitized login facts per lane: never a token, never a raw id.
- host.json    platform, timezone, sandbox availability, Light version.

Public API: tick(mgr) from the hub's observer loop (cheap when nothing
changed: per-file (size, mtime) caches, incremental reads, write only on a
change); feed_dir(); note_quota(pane, windows) from sessions.py, with
quota_windows(rate_limit_info) to split a Claude notice into windows and
merge_window(old, new) to fold a newer observation in per field.
Nothing here computes or rescales a vendor figure.
"""
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "corral-light.module-feed/1"
KEEP_DAYS = 35
LOGIN_TTL_S = 60          # login facts are re-read at most this often
HOST_TTL_S = 600
SALT_BYTES = 32
# A reset time over this is in milliseconds (docs/finops-module-plan.md §6.5).
MS_THRESHOLD = 1e11
ORIGINS = ("consult", "challenge", "rig")        # else "human"
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_PANE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_FIELD_CHARS = 200

# Tests point the feed at a private dir; None = the hub's state dir.
STATE_OVERRIDE = None

_write_lock = threading.Lock()
_last = {}                # file name -> the content last written (sans generated_at)
_quota_lock = threading.Lock()
_quota = None             # loaded from quota.json on first use, then kept
_pane_cache = {}          # pane id -> cached reads (see _pane_row)
_ttl = {}                 # key -> (read at, value)


# ── where ──────────────────────────────────────────────────────────────────

def state_dir():
    """The hub's state dir: the one sessions.py uses when it is loaded (tests
    repoint it), else CORRAL_LIGHT_STATE like every other module."""
    if STATE_OVERRIDE:
        return Path(STATE_OVERRIDE)
    s = sys.modules.get("sessions")
    if s is not None and getattr(s, "STATE", None):
        return Path(s.STATE)
    return Path(os.environ.get("CORRAL_LIGHT_STATE",
                               Path.home() / ".local/share/corral-light"))


def feed_dir():
    return state_dir() / "module-feed" / "v1"


def _private_dir(d):
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass


def _iso(s):
    if s is None:
        return None
    return (datetime.fromtimestamp(s, timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


def _write(name, obj, force=False):
    """Write <feed>/<name> atomically when its content changed. The stamp
    `generated_at` is not part of the comparison."""
    body = json.dumps(obj, sort_keys=True, indent=1)
    d = feed_dir()
    with _write_lock:
        key = (str(d), name)
        if not force and _last.get(key) == body and (d / name).is_file():
            return False
        _private_dir(d.parent)
        _private_dir(d)
        out = dict(obj, generated_at=_iso(time.time()))
        fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=str(d))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(out, fh, sort_keys=True, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, d / name)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _last[key] = body
        return True


# ── fingerprints ───────────────────────────────────────────────────────────

def _salt():
    """32 random bytes in the state dir (0600), created once. Outside the
    feed dir: a module never sees it. None when it cannot be read."""
    f = state_dir() / "module-feed.salt"
    try:
        fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    except OSError:
        return None
    else:
        with os.fdopen(fd, "wb") as fh:
            fh.write(os.urandom(SALT_BYTES))
    try:
        raw = f.read_bytes()
    except OSError:
        return None
    return raw if len(raw) == SALT_BYTES else None


def fingerprint(lane, account_id):
    """Salted SHA-256 of a stable account id (never a token). None when there
    is no id or no salt."""
    if not isinstance(account_id, str) or not account_id.strip():
        return None
    salt = _salt()
    if salt is None:
        return None
    h = hashlib.sha256(salt + lane.encode() + b"\0" + account_id.strip().encode())
    return h.hexdigest()


# ── quota ──────────────────────────────────────────────────────────────────

# The Claude SDK's SDKRateLimitInfo fields (agent SDK 0.3.x), plus the
# per-window `utilization`/`resetsAt` of `unifiedWindows`. Validated: only
# these names, only scalar values; anything else the notice carries is not
# copied (it could be anything).
NOTICE_FIELDS = ("status", "resetsAt", "rateLimitType", "utilization",
                 "overageStatus", "overageResetsAt", "overageDisabledReason",
                 "isUsingOverage", "overageInUse", "surpassedThreshold",
                 "limitScope", "errorCode")


def _scalar_fields(d):
    """The known notice fields, as sent; nested or unknown values are dropped."""
    out = {}
    for k, v in d.items():
        if k not in NOTICE_FIELDS:
            continue
        if v is None or isinstance(v, bool) or isinstance(v, int):
            out[k] = v
        elif isinstance(v, float) and math.isfinite(v):
            out[k] = v
        elif isinstance(v, str) and len(v) <= MAX_FIELD_CHARS:
            out[k] = v
    return out


def _window_key(v):
    """The vendor's window name when it is a plain key; a missing name is
    "_unknown"; any other name keeps a sanitised form of itself plus a short
    hash, so two odd names never share a window."""
    if not isinstance(v, str) or not v:
        return "_unknown"
    if _KEY_RE.match(v):
        return v
    tag = hashlib.sha256(v.encode("utf-8", "replace")).hexdigest()[:8]
    return "_" + re.sub(r"[^A-Za-z0-9_.:-]", "_", v)[:48] + "-" + tag


def _reset(v):
    """(epoch seconds, unit the vendor used) or (None, None)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or \
            not math.isfinite(v) or v <= 0:
        return None, None
    return (v / 1000.0, "ms") if v > MS_THRESHOLD else (float(v), "s")


def _observation(fields, window, source, now):
    obs = dict(fields)
    obs["resets_at_s"], obs["resets_at_unit"] = _reset(fields.get("resetsAt"))
    obs.update(window=window, source=source, observed_at=round(now, 3))
    return obs


def quota_windows(info, observed_at=None):
    """Split one Claude `rate_limit_info` into {window: observation}.

    The notice's own window is keyed by `rateLimitType` ("_unknown" when
    missing); each `unifiedWindows` entry becomes its own window, merged with
    the notice's fields when it is the same window. Fields are passed through
    exactly as sent (`utilization` stays a fraction); each observation adds
    `observed_at` (hub clock), `resets_at_s` and `resets_at_unit`.
    """
    now = time.time() if observed_at is None else observed_at
    if not isinstance(info, dict):
        return {}
    top = _scalar_fields(info)
    key = _window_key(info.get("rateLimitType"))
    out = {key: _observation(top, key, "notice", now)}
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        for name, w in unified.items():
            if not isinstance(w, dict):
                continue
            wk = _window_key(name)
            fields = _scalar_fields(w)
            fields.setdefault("rateLimitType", name if wk == name else None)
            if wk == key:
                out[wk] = _observation(dict(top, **fields), wk, "notice", now)
            else:
                out[wk] = _observation(fields, wk, "unifiedWindows", now)
    return out


def _load_quota():
    global _quota
    if _quota is None:
        try:
            doc = json.loads((feed_dir() / "quota.json").read_text(encoding="utf-8"))
            accounts = doc.get("accounts") if isinstance(doc, dict) else None
            _quota = {"accounts": accounts if isinstance(accounts, dict) else {}}
        except (OSError, ValueError):
            _quota = {"accounts": {}}
    return _quota


def merge_window(old, new):
    """A newer notice updates the fields it carries; a vendor field it lacks
    keeps the last value, and `carried` records when that value was
    observed (round three, finding 5). Nothing is computed."""
    if not isinstance(old, dict):
        return new
    out = dict(new)
    carried = {}
    old_carried = old.get("carried") if isinstance(old.get("carried"), dict) else {}
    for f in NOTICE_FIELDS:
        if f in new or f not in old:
            continue
        out[f] = old[f]
        carried[f] = old_carried.get(f, old.get("observed_at"))
        if f == "resetsAt":
            out["resets_at_s"] = old.get("resets_at_s")
            out["resets_at_unit"] = old.get("resets_at_unit")
    if carried:
        out["carried"] = carried
    else:
        out.pop("carried", None)
    return out


def note_quota(pane, windows, lane="claude"):
    """Called by sessions.py the moment a quota notice arrives. Keeps the
    newest observation per (account, window) and writes quota.json now."""
    if not windows:
        return
    fp = (login_facts(lanes=(lane,)).get(lane) or {}).get("fingerprint")
    acct = fp or "unknown"
    pid = getattr(pane, "id", None)
    with _quota_lock:
        q = _load_quota()
        a = q["accounts"].setdefault(acct, {"lane": lane, "windows": {}})
        ws = a.setdefault("windows", {})
        for k, obs in windows.items():
            old = ws.get(k)
            if isinstance(old, dict) and (old.get("observed_at") or 0) > obs["observed_at"]:
                continue
            ws[k] = merge_window(old, dict(obs, pane=pid if isinstance(pid, str) else None))
        _write("quota.json", {"schema": SCHEMA, "accounts": q["accounts"]}, force=True)


def reset_for_tests():
    """Forget every in-memory cache (a 'hub restart')."""
    global _quota
    with _quota_lock:
        _quota = None
    _last.clear()
    _pane_cache.clear()
    _ttl.clear()


# ── logins and host ────────────────────────────────────────────────────────

def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:                          # noqa: BLE001
        return {"present": None, "error": type(e).__name__}


def _lane_facts(lane):
    if lane == "claude":
        import claude_auth
        return claude_auth.login_facts(lambda a: fingerprint("claude", a))
    if lane == "codex":
        import codex_launcher
        return codex_launcher.login_facts(lambda a: fingerprint("codex", a))
    import grok_launcher
    return grok_launcher.login_facts()


def login_facts(force=False, lanes=("claude", "codex", "grok")):
    """{lane: facts}, each lane cached LOGIN_TTL_S. A lane whose read fails
    says so instead of failing the feed."""
    now = time.time()
    out = {}
    for lane in lanes:
        at, val = _ttl.get(("login", lane), (0.0, None))
        if val is None or force or now - at >= LOGIN_TTL_S:
            val = _safe(_lane_facts, lane)
            _ttl[("login", lane)] = (now, val)
        out[lane] = val
    return out


def _timezone():
    tz = os.environ.get("TZ")
    if not tz:
        try:
            target = os.readlink("/etc/localtime")
            tz = target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else None
        except OSError:
            tz = None
    return {"name": tz if tz and _KEY_RE.match(tz.replace("/", ".")) else None,
            "utc_offset_s": -time.altzone if time.localtime().tm_isdst > 0 else -time.timezone}


def _light_version():
    hub = sys.modules.get("hub")
    commit = getattr(hub, "BOOT_COMMIT", None) if hub is not None else None
    if commit is None:
        import subprocess
        try:
            r = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent),
                                "rev-parse", "HEAD"], capture_output=True, text=True,
                               timeout=5)
            commit = r.stdout.strip() or None if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            commit = None
    return {"commit": commit}


def host_facts(force=False):
    at, val = _ttl.get("host", (0.0, None))
    if val is not None and not force and time.time() - at < HOST_TTL_S:
        return val
    try:
        import module_sandbox
        ok, why = module_sandbox.available()
    except Exception as e:                          # noqa: BLE001
        ok, why = False, f"sandbox check failed: {type(e).__name__}"
    val = {"platform": sys.platform, "timezone": _timezone(),
           "sandbox": {"available": bool(ok), "why": None if ok else str(why)[:200]},
           "light": _light_version()}
    _ttl["host"] = (time.time(), val)
    return val


# ── panes ──────────────────────────────────────────────────────────────────

_WANT = (b'"kind": "turn_end"', b'"kind": "user"', b'"kind": "ready"',
         b'"kind": "resumed"', b'"kind": "cleared"')


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and \
        math.isfinite(v) else None


def _cost(c):
    if isinstance(c, dict):
        return {"amount": _num(c.get("amount")),
                "currency": c.get("currency") if isinstance(c.get("currency"), str)
                and len(c["currency"]) <= 8 else None}
    return None


def _scan_lines(raw, acc):
    """Fold complete event lines into `acc`; only five kinds are parsed, and
    only their numeric or enum fields are kept."""
    for line in raw.split(b"\n"):
        if not any(w in line for w in _WANT):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        kind, data = ev.get("kind"), ev.get("data")
        data = data if isinstance(data, dict) else {}
        at = ev.get("at") if isinstance(ev.get("at"), str) else None
        if kind == "turn_end":
            u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            stop = data.get("stopReason")
            acc["usage"].append({
                "turn": data.get("turn") if isinstance(data.get("turn"), str) else None,
                "at": at, "used": _num(u.get("used")), "size": _num(u.get("size")),
                "cost": _cost(u.get("cost")),
                "stop": stop if isinstance(stop, str) and len(stop) <= 40 else None})
        elif kind == "user":
            if acc["via"] is None:
                via = data.get("via")
                acc["via"] = via if via in ORIGINS else "human"
        elif kind in ("ready", "resumed"):
            m = data.get("model")
            if isinstance(m, str) and len(m) <= 120:
                acc["model"] = m
            if kind == "resumed":
                acc["segments"].append({"at": at, "kind": "resumed"})
        elif kind == "cleared":
            acc["segments"].append({"at": at, "kind": "cleared"})


def _read_log(path, cache):
    """Incremental read of one events file; `cache` holds the identity,
    offset and what was folded so far. Returns the cache (fresh on a swap)."""
    try:
        st = path.stat()
    except OSError:
        return None
    sig = (st.st_ino, st.st_size, st.st_mtime_ns)
    if cache is not None and cache["sig"] == sig:
        return cache                                   # unchanged: no read at all
    if cache is None or cache["sig"][0] != st.st_ino or st.st_size < cache["off"]:
        cache = {"sig": None, "off": 0,
                 "acc": {"usage": [], "via": None, "model": None, "segments": []}}
    try:
        with path.open("rb") as fh:
            fh.seek(cache["off"])
            raw = fh.read(max(0, st.st_size - cache["off"]))
    except OSError:
        return cache
    end = raw.rfind(b"\n")
    if end >= 0:
        _scan_lines(raw[:end], cache["acc"])
        cache["off"] += end + 1
    # A partial last line is read again next time.
    cache["sig"] = sig if end == len(raw) - 1 or not raw else (st.st_ino, -1, -1)
    return cache


def _pane_row(d, live, now):
    pid = d.name
    mf = d / "meta.json"
    try:
        mst = mf.stat()
    except OSError:
        return None
    c = _pane_cache.setdefault(pid, {"meta_sig": None, "meta": None, "logs": {}})
    msig = (mst.st_size, mst.st_mtime_ns)
    if c["meta_sig"] != msig:
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        c["meta"], c["meta_sig"] = (m if isinstance(m, dict) else None), msig
    m = c["meta"]
    if not m:
        return None
    closed = bool(m.get("closed")) and pid not in live
    if closed and now - mst.st_mtime > KEEP_DAYS * 86400:
        return None
    usage, via, model, segments = [], None, None, []
    for name in ("events.jsonl.1", "events.jsonl"):
        lc = _read_log(d / name, c["logs"].get(name))
        if lc is None:
            c["logs"].pop(name, None)
            continue
        c["logs"][name] = lc
        acc = lc["acc"]
        usage += acc["usage"]
        segments += acc["segments"]
        via = via or acc["via"]
        model = acc["model"] or model
    p = live.get(pid)
    model = (getattr(p, "model", None) if p is not None else None) or model \
        or m.get("want_model")

    def s(v, n=200):
        return v[:n] if isinstance(v, str) else None
    challenge_of = s(m.get("challenge_of"), 64)
    origin = "challenge" if challenge_of else (via if via in ORIGINS else "human")
    return {"id": pid, "agent": s(m.get("agent"), 40), "model": s(model, 120),
            "title": s(_published_title(p, m)),
            "created": s(m.get("created"), 40),
            "closed": _iso(mst.st_mtime) if closed else None,
            "acp_session": s(m.get("acp_session"), 120),
            "worktree_id": s(m.get("worktree_id"), 64),
            "role": s(m.get("role"), 80), "origin": origin,
            "challenge_of": challenge_of, "usage": usage, "segments": segments}


def _lane_label(agent):
    sess = sys.modules.get("sessions")
    spec = (getattr(sess, "AGENTS", None) or {}).get(agent) if sess is not None else None
    label = spec.get("label") if isinstance(spec, dict) else None
    return label if isinstance(label, str) else (agent if isinstance(agent, str) else None)


def _published_title(p, m):
    """A title the operator typed, else the lane's label. An untitled pane
    is named after its first prompt and a port copies that name, so neither
    the title nor title_locked says the text is safe to publish."""
    named = getattr(p, "title_named", False) if p is not None else m.get("title_named")
    if named is True:
        return getattr(p, "title", None) if p is not None else m.get("title")
    return _lane_label(m.get("agent"))


def panes_doc(mgr=None):
    root = state_dir() / "panes"
    live = dict(getattr(mgr, "panes", None) or {})
    now = time.time()
    rows, seen = [], set()
    try:
        dirs = sorted(root.iterdir())
    except OSError:
        dirs = []
    for d in dirs:
        if not _PANE_ID_RE.match(d.name) or not d.is_dir():
            continue
        seen.add(d.name)
        row = _pane_row(d, live, now)
        if row is not None:
            rows.append(row)
    for gone in set(_pane_cache) - seen:
        _pane_cache.pop(gone, None)
    rows.sort(key=lambda r: (r.get("created") or "", r["id"]))
    return {"schema": SCHEMA, "keep_days": KEEP_DAYS, "panes": rows}


# ── the tick ───────────────────────────────────────────────────────────────

def tick(mgr=None):
    """One observer-loop pass: rewrite whatever changed. Never raises."""
    wrote = []
    for name, build in (("panes.json", lambda: panes_doc(mgr)),
                        ("logins.json", lambda: {"schema": SCHEMA,
                                                 "lanes": login_facts()}),
                        ("host.json", lambda: dict(host_facts(), schema=SCHEMA))):
        try:
            if _write(name, build()):
                wrote.append(name)
        except Exception as e:                      # noqa: BLE001
            print(f"corral-light: module feed {name} not written: "
                  f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr, flush=True)
    try:
        if not (feed_dir() / "quota.json").is_file():
            with _quota_lock:
                q = _load_quota()
                _write("quota.json", {"schema": SCHEMA, "accounts": q["accounts"]})
                wrote.append("quota.json")
    except Exception as e:                          # noqa: BLE001
        print(f"corral-light: module feed quota.json not written: "
              f"{type(e).__name__}", file=sys.stderr, flush=True)
    return wrote
