#!/usr/bin/python3
"""consult — ask another assistant through the running Corral Light hub.

A client of the hub's own API: it opens ordinary panes on lanes already signed
in, sends, waits, and prints one JSON document. Panes keep their permission
rail; on timeout the turn is cancelled and returned with complete=false.

VERBS
    lanes                      which lanes are live right now (no spend)
    ask       --lane L         open a pane on lane L, send, wait, print JSON
    send      --pane ID        send to an existing pane, wait, print JSON
    fanout    --lane a --lane b ...   one prompt, N new panes, wait for all
              --pane x --pane y ...   ...or N existing panes
    crossfeed --pane a --pane b ...   every pane gets every other pane's last
                               answer under one preamble (quotes clipped at
                               the hub's QUOTE_CHARS)
    close     --pane ID ...    close panes this script opened

Panes it opens start minimized and restore themselves when one needs the
operator (a permission or a question); --foreground shows them on the wall.

The prompt comes from --prompt, --prompt-file, or stdin. Output is one JSON
document on stdout; progress and warnings go to stderr.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_URL = os.environ.get("CORRAL_LIGHT_URL", "http://127.0.0.1:8098")
COOKIE_NAME = "corral_light"          # hub.COOKIE
# One paired session shared by every scripted client on this account.
CFG = Path(os.environ.get("CORRAL_LIGHT_CONSULT_CFG",
                          Path.home() / ".config/corral-light/consult-session.json"))
LOCAL_TTL = 11 * 3600

CANCEL_REASONS = {"cancelled", "canceled", "interrupted", "aborted"}
DEFAULT_TIMEOUT_S = 2400
POLL_S = 2.0
MAX_TEXT = 300_000              # chars of one answer kept; the pane log holds the rest
MAX_LANES = 6
PAIR_WAIT_S = 120               # wait for a human `corral-light pair` elsewhere
# auth.approve's answer when the code is not in this process's pairing store;
# on the hub's own host that means a mismatched CORRAL_LIGHT_STATE, so fail fast.
NOT_IN_OUR_STORE = "unknown or expired code"
HTTP_TIMEOUT_S = 30
HANDSHAKE_S = 200               # /api/session/new blocks on the ACP handshake (180 s)

# Friendly lane names -> lane keys the hub knows.
LANE_ALIASES = {"gpt": "codex", "chatgpt": "codex", "openai": "codex",
                "antigravity": "gemini", "google": "gemini",
                "xai": "grok", "claude-code": "claude"}


class ConsultError(Exception):
    pass


# ---------------------------------------------------------------- HTTP client
class Hub:
    def __init__(self, base_url=DEFAULT_URL):
        self.url = base_url
        u = urllib.parse.urlsplit(base_url)
        self.https = u.scheme == "https"
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or (443 if self.https else 80)
        self.token = None

    def _conn(self, timeout=HTTP_TIMEOUT_S):
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=timeout)

    def _headers(self, extra=None):
        h = dict(extra or {})
        if self.token:
            h["Cookie"] = f"{COOKIE_NAME}={self.token}"
        return h

    def _do(self, method, path, body=None, timeout=HTTP_TIMEOUT_S):
        conn = self._conn(timeout)
        try:
            headers = self._headers()
            raw = None
            if body is not None:
                raw = json.dumps(body).encode()
                headers.update({"Content-Type": "application/json",
                                "Content-Length": str(len(raw))})
            try:
                conn.request(method, path, body=raw, headers=headers)
                r = conn.getresponse()
                data = r.read()
            except (ConnectionRefusedError, OSError, http.client.HTTPException) as e:
                # Refused/timed-out sockets and dropped keep-alives alike.
                raise ConsultError(f"corral hub unreachable at {self.host}:{self.port} "
                                   f"({type(e).__name__}: {e}). Is corral-light serve running?")
            try:
                obj = json.loads(data or b"{}")
            except ValueError:
                obj = {"raw": data[:200].decode("utf-8", "replace")}
            return r.status, r, obj
        finally:
            conn.close()

    def get(self, path, timeout=HTTP_TIMEOUT_S):
        status, r, obj = self._do("GET", path, timeout=timeout)
        if status == 401:
            raise ConsultError("not paired")
        if status != 200:
            raise ConsultError(f"GET {path} -> {status}: {obj.get('error') or obj}")
        return obj

    def post(self, path, body, timeout=HTTP_TIMEOUT_S):
        status, r, obj = self._do("POST", path, body, timeout=timeout)
        if status == 401:
            raise ConsultError("not paired")
        if status != 200 or obj.get("error"):
            raise ConsultError(f"POST {path} -> {status}: {obj.get('error') or obj}")
        return obj


# ------------------------------------------------------------------- pairing
def _origin(url):
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}"


def _load_token(url):
    """The cached cookie, only for the origin that minted it (no `url` field
    means the default hub)."""
    try:
        d = json.loads(CFG.read_text())
        if d.get("exp", 0) <= time.time():
            return None
        if _origin(d.get("url") or DEFAULT_URL) != _origin(url):
            return None
        return d.get("token")
    except (OSError, ValueError):
        return None


def _save_token(token, url):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"token": token, "exp": time.time() + LOCAL_TTL,
                       "url": _origin(url)}).encode()
    # Created 0600 atomically: the bearer is never world-readable.
    tmp = CFG.with_name(CFG.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CFG)


def _approve_locally(code):
    """Do the `corral-light pair` step in-process (works only as the hub's
    account on the hub host). Returns (ok, msg, pairing store path or None)."""
    try:
        sys.path.insert(0, str(HERE))
        import auth                                     # noqa: WPS433 (in-repo)
        ok, msg = auth.approve(code)
        return bool(ok), msg, str(auth.STATE)
    except Exception as e:                              # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", None


def _local_host_id():
    try:
        sys.path.insert(0, str(HERE))
        import auth                                     # noqa: WPS433 (in-repo)
        return auth.host_id()
    except Exception:                                   # noqa: BLE001
        return None


def pair(hub):
    status, _r, obj = hub._do("GET", "/api/pair/new")
    if status != 200 or not obj.get("code"):
        raise ConsultError(f"could not mint a pairing code: {obj}")
    code = obj["code"]
    ok, msg, store = _approve_locally(code)
    same_host = bool(obj.get("host")) and obj.get("host") == _local_host_id()
    if not ok and msg == NOT_IN_OUR_STORE and same_host:
        raise ConsultError(
            f"pairing code {code} is not in this client's pairing store ({store}), "
            f"so this shell's CORRAL_LIGHT_STATE is most likely not the hub's. Re-run with "
            f"the hub's CORRAL_LIGHT_STATE; the step this replaces, on the hub host:  "
            f"corral-light pair {code}")
    if not ok:
        print(f"consult: pairing needs you — on the hub host run:  corral-light pair {code}"
              f"  ({msg})", file=sys.stderr, flush=True)
    deadline = time.time() + (PAIR_WAIT_S if not ok else 15)
    while time.time() < deadline:
        status, r, obj = hub._do("GET", f"/api/pair/claim?code={urllib.parse.quote(code)}")
        if obj.get("status") == "ok":
            token = None
            for part in (r.getheader("Set-Cookie") or "").split(";"):
                part = part.strip()
                if part.startswith(COOKIE_NAME + "="):
                    token = part[len(COOKIE_NAME) + 1:]
            if not token:
                raise ConsultError("pairing claimed but no session cookie came back")
            hub.token = token
            _save_token(token, hub.url)
            return
        if obj.get("status") == "expired":
            raise ConsultError("pairing code expired before it was approved")
        time.sleep(1.5)
    raise ConsultError("pairing was not approved in time")


def connect(url=DEFAULT_URL):
    hub = Hub(url)
    hub.token = _load_token(url)
    try:
        hub.get("/api/state")
        return hub
    except ConsultError as e:
        if str(e) != "not paired":
            raise
    hub.token = None
    pair(hub)
    return hub


# ------------------------------------------------------------------ helpers
def lane_key(name):
    name = (name or "").strip().lower()
    return LANE_ALIASES.get(name, name)


def lanes(hub):
    st = hub.get("/api/state")
    return st.get("agents") or []


def _state(hub, since, timeout=HTTP_TIMEOUT_S):
    q = urllib.parse.quote(json.dumps(since)) if since else ""
    return hub.get("/api/state" + (f"?since={q}" if q else ""), timeout=timeout)


def _pane_in(st, pid):
    for p in st.get("panes") or []:
        if p["id"] == pid:
            return p
    return None


def _all_seqs(st):
    return {p["id"]: int(p.get("seq") or 0) for p in st.get("panes") or []}


def read_prompt(args):
    if getattr(args, "prompt", None):
        return args.prompt
    if getattr(args, "prompt_file", None):
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if sys.stdin.isatty():
        raise ConsultError("no prompt: pass --prompt, --prompt-file, or pipe stdin")
    return sys.stdin.read()


def open_pane(hub, lane, cwd, title=None, model=None, effort=None, posture=None,
              config=None, background=True):
    """Open a pane on `lane`; `posture=None` posts no key so the hub default applies.
    `background`: minimized on the wall until it needs the operator."""
    key = lane_key(lane)
    live = {a["key"]: a for a in lanes(hub)}
    a = live.get(key)
    if a is None:
        raise ConsultError(f"no lane {key!r} — live lanes: {', '.join(sorted(live))}")
    if not a.get("available", False):
        raise ConsultError(f"lane {key!r} ({a.get('label')}) is unavailable: "
                           f"{a.get('why') or a.get('needs') or 'no reason given'}")
    # Resolve locally: the hub checks is_dir() relative to its own cwd.
    body = {"agent": key, "cwd": str(Path(cwd).expanduser().resolve())}
    if posture:
        body["posture"] = posture
    if model:
        body["model"] = model
    if effort:
        body["effort"] = effort
    if background:
        body["background"] = True
    pane = hub.post("/api/session/new", body, timeout=HANDSHAKE_S)["pane"]
    # Extra lane config (e.g. codex `mode=read-only`); a refusal is fatal.
    applied = []
    for item in config or []:
        cid, _, val = item.partition("=")
        try:
            hub.post("/api/session/config", {"pane": pane["id"], "configId": cid.strip(),
                                             "value": val.strip()})
            applied.append(f"{cid.strip()}={val.strip()}")
        except ConsultError as e:
            # Never run the prompt without a control the caller asked for.
            try:
                hub.post("/api/session/close", {"pane": pane["id"]})
            except ConsultError:
                pass
            raise ConsultError(f"{lane}: config {cid}={val} refused by the lane ({e}); "
                               f"pane closed, prompt not sent")
    pane["config_applied"] = applied
    if title:
        try:
            hub.post("/api/session/rename", {"pane": pane["id"], "title": title[:60]})
        except ConsultError as e:
            print(f"consult: rename failed ({e})", file=sys.stderr, flush=True)
    return pane


def _is_ours(ev_text, own):
    """Is this `user` event our prompt? Exact match (whitespace-normalised),
    or a preamble prefix for hub-composed prompts; no `own` matches any."""
    if own is None:
        return True
    a, b = " ".join((ev_text or "").split()), " ".join(own.split())
    return a == b or (len(b) >= 40 and a.startswith(b))


def wait_turn(hub, pid, seq0, timeout_s, label="", own=None, prefix=False):
    """Collect the answer to our prompt among the events after `seq0`.

    Returns {text, complete, state, wall_s, needs_you_s, stop_reason,
    cancelled, timed_out}. `complete` only on a real turn_end after our own
    user event (matched by text); death, cancel or timeout are reported as such."""
    t0 = time.time()
    since = {pid: seq0}
    chunks, size, dropped = [], 0, False
    seen_user, complete, stop_reason = False, False, None
    needs_you_since, needs_you_s, warned = None, 0.0, False
    state, dead_why, transport_err = "", None, None
    own_key = None
    if own is not None:
        own_key = " ".join(own.split())
        if prefix:
            own_key = own_key[:200]
    while True:
        try:
            left = timeout_s - (time.time() - t0)
            st = _state(hub, since, timeout=min(HTTP_TIMEOUT_S, max(2.0, left)))
        except ConsultError as e:
            # Fall through to the cancel path rather than raising past it.
            transport_err = str(e)
            break
        p = _pane_in(st, pid)
        if p is None:
            dead_why = "pane vanished (closed or forgotten)"
            break
        state = p.get("state") or ""
        for ev in p.get("events") or []:
            k = ev.get("kind")
            d = ev.get("data") or {}
            if k == "user":
                if seen_user:
                    # A later prompt ends what we may attribute to ourselves.
                    stop_reason = "another prompt was sent to this pane"
                    dead_why = stop_reason
                    break
                text = d.get("text") or ""
                if own_key is None or (
                        " ".join(text.split()).startswith(own_key) if prefix
                        else " ".join(text.split()) == own_key):
                    seen_user = True
                # else: another prompt queued ahead of ours; keep reading.
            elif k == "text" and seen_user and not complete:
                t = d.get("text") or ""
                if size < MAX_TEXT:
                    keep = t[:MAX_TEXT - size]
                    chunks.append(keep)
                    dropped = dropped or len(keep) < len(t)
                else:
                    dropped = dropped or bool(t)
                size += len(t)
            elif k == "turn_end" and seen_user:
                stop_reason = d.get("stopReason") or "end_turn"
                if str(stop_reason).lower() in CANCEL_REASONS:
                    # A cancelled turn's trailing turn_end must not mark it complete.
                    dead_why = dead_why or "turn cancelled on the wall"
                    break
                complete = True
                break
            elif k == "cancelled" and seen_user:
                dead_why = "turn cancelled on the wall"
                break
            elif k in ("error", "exit") and seen_user:
                dead_why = f"{k}: {json.dumps(d)[:200]}"
                break
        if dead_why or complete:
            break
        # Keep every pane's `since` current so the next poll is a delta.
        since = _all_seqs(st)
        since[pid] = max(since.get(pid, 0), p.get("seq") or 0)
        if state == "needs-you":
            if needs_you_since is None:
                needs_you_since = time.time()
                if not warned:
                    print(f"consult: {label or pid} is waiting on a permission "
                          f"answer on the wall (needs-you) — the clock keeps running",
                          file=sys.stderr, flush=True)
                    warned = True
        elif needs_you_since is not None:
            needs_you_s += time.time() - needs_you_since
            needs_you_since = None
        if state == "dead":
            dead_why = f"pane died: {p.get('error') or 'no error recorded'}"
            break
        if time.time() - t0 > timeout_s:
            break
        time.sleep(POLL_S)
    if needs_you_since is not None:
        needs_you_s += time.time() - needs_you_since
    timed_out = (not complete and dead_why is None and transport_err is None
                 and time.time() - t0 > timeout_s)
    cancelled = False
    if timed_out or (transport_err and not complete):
        try:
            cancelled = bool(hub.post("/api/session/cancel", {"pane": pid}).get("ok"))
        except ConsultError as e:
            print(f"consult: cancel failed ({e})", file=sys.stderr, flush=True)
    if transport_err and not complete:
        dead_why = f"hub stopped answering mid-turn ({transport_err}); " + \
                   ("turn cancelled" if cancelled else "cancel also failed")
    return {"text": "".join(chunks).strip(), "complete": complete,
            "truncated": dropped, "state": state,
            "stop_reason": stop_reason, "why": dead_why,
            "wall_s": round(time.time() - t0, 1),
            "needs_you_s": round(needs_you_s, 1),
            "timed_out": timed_out, "cancelled": cancelled}


def _pane_record(hub, pid):
    p = _pane_in(hub.get("/api/state?since=" + urllib.parse.quote(json.dumps({pid: 1 << 40}))), pid)
    if p is None:
        return {"pane": pid}
    return {"pane": pid, "lane": p.get("agent"), "label": p.get("label"),
            "model": p.get("model"), "effort": p.get("effort"),
            "title": p.get("title"), "cwd": p.get("cwd"),
            "posture": p.get("posture"), "posture_enforced": p.get("postureEnforced")}


def _await_ready(hub, pid, timeout_s, label=""):
    """Wait until the pane has no turn in flight (else the previous turn's
    output would read as our answer). Never cancels; refuses on expiry.
    Returns the pane snapshot to sequence from."""
    t0, warned = time.time(), False
    while True:
        p = _pane_in(_state(hub, {pid: 1 << 40}), pid)
        if p is None:
            raise ConsultError(f"no pane {pid}")
        state = p.get("state") or ""
        if state == "dead":
            raise ConsultError(f"pane {pid} is dead: {p.get('error') or 'no error recorded'}")
        if state in ("ready", "detached", "uncertain") and not p.get("pending"):
            return p
        if not warned:
            print(f"consult: {label or pid} is {state} — waiting for its current turn "
                  f"to end before sending", file=sys.stderr, flush=True)
            warned = True
        if time.time() - t0 > timeout_s:
            raise ConsultError(f"pane {pid} stayed {state} for {int(timeout_s)}s; "
                               f"not sending onto a busy pane")
        time.sleep(POLL_S)


def send_and_wait(hub, pid, text, timeout_s, label=""):
    t0 = time.time()
    p = _await_ready(hub, pid, timeout_s, label)
    seq0 = int(p.get("seq") or 0)
    # `via` marks the turn as script-originated; older hubs ignore it.
    hub.post("/api/session/send", {"pane": pid, "text": text, "via": "consult"})
    rec = _pane_record(hub, pid)
    remaining = max(1, int(timeout_s - (time.time() - t0)))
    try:
        rec.update(wait_turn(hub, pid, seq0, remaining, label or rec.get("title") or pid,
                             own=text))
    except KeyboardInterrupt:
        # Ctrl+C must not leave the lane generating.
        try:
            hub.post("/api/session/cancel", {"pane": pid})
        except ConsultError:
            pass
        raise
    rec["prompt_chars"] = len(text)
    return rec


def _parallel(items, fn):
    out, threads, errs = {}, [], {}

    def run(k, *a):
        try:
            out[k] = fn(*a)
        except Exception as e:                          # noqa: BLE001
            errs[k] = f"{type(e).__name__}: {e}"

    for k, *a in items:
        t = threading.Thread(target=run, args=(k, *a), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        while t.is_alive():
            t.join(0.5)                 # interruptible: Ctrl+C reaches the caller
    for k, e in errs.items():
        out[k] = {"pane": k, "ok": False, "why": e, "complete": False, "text": ""}
    return out


# -------------------------------------------------------------------- verbs
def cmd_lanes(args):
    hub = connect(args.url)
    rows = lanes(hub)
    if not args.table:
        print(json.dumps(rows, indent=2), flush=True)
    else:
        for a in rows:
            flag = "ok      " if a.get("available") else "UNAVAIL "
            why = "" if a.get("available") else f"  {a.get('why') or a.get('needs') or ''}"
            print(f"  {flag}{a['key']:<12} {a.get('label','')}{why}", flush=True)
    return 0 if any(a.get("available") for a in rows) else 1


def cmd_ask(args):
    hub = connect(args.url)
    text = read_prompt(args)
    pane = open_pane(hub, args.lane, args.cwd, args.title, args.model, args.effort,
                     args.posture, args.config, background=not args.foreground)
    try:
        rec = send_and_wait(hub, pane["id"], text, args.timeout, args.title or args.lane)
    except BaseException:
        # Cancel and close a pane we opened but could not use.
        for verb in ("cancel", "close"):
            try:
                hub.post(f"/api/session/{verb}", {"pane": pane["id"]})
            except ConsultError:
                pass
        raise
    rec["config_applied"] = pane.get("config_applied", [])
    rec["ok"] = bool(rec["complete"] and rec["text"])
    if args.close:
        hub.post("/api/session/close", {"pane": pane["id"]})
        rec["closed"] = True
    print(json.dumps(rec, indent=2), flush=True)
    return 0 if rec["ok"] else 1


def cmd_send(args):
    hub = connect(args.url)
    text = read_prompt(args)
    rec = send_and_wait(hub, args.pane, text, args.timeout)
    rec["ok"] = bool(rec["complete"] and rec["text"])
    print(json.dumps(rec, indent=2), flush=True)
    return 0 if rec["ok"] else 1


def cmd_fanout(args):
    hub = connect(args.url)
    text = read_prompt(args)
    lanes_ = [lane_key(x) for x in (args.lane or [])]
    pids = list(args.pane or [])
    if bool(lanes_) == bool(pids):
        raise ConsultError("fanout takes --lane ... (new panes) OR --pane ... (existing), not both")
    if len(lanes_) + len(pids) > MAX_LANES:
        raise ConsultError(f"at most {MAX_LANES} arms per fan-out")
    opened = {}
    for i, lane in enumerate(lanes_):
        title = (args.title + f" · {lane}") if args.title else None
        try:
            pane = open_pane(hub, lane, args.cwd, title, None, None, args.posture,
                             background=not args.foreground)
            opened[pane["id"]] = lane
        except ConsultError as e:
            # One lane refusing does not stop the others; report it by name.
            opened[f"refused:{lane}"] = str(e)
    pids += [k for k in opened if not k.startswith("refused:")]
    try:
        results = _parallel([(pid, hub, pid, text, args.timeout, opened.get(pid, pid))
                             for pid in pids], send_and_wait)
    except KeyboardInterrupt:
        for pid in pids:
            try:
                hub.post("/api/session/cancel", {"pane": pid})
            except ConsultError:
                pass
        raise
    for k, v in opened.items():
        if k.startswith("refused:"):
            results[k] = {"pane": None, "lane": k.split(":", 1)[1], "ok": False,
                          "why": v, "complete": False, "text": ""}
    for r in results.values():
        r.setdefault("ok", bool(r.get("complete") and r.get("text")))
    print(json.dumps({"arms": list(results.values())}, indent=2), flush=True)
    return 0 if sum(1 for r in results.values() if r["ok"]) >= 1 else 1


def cmd_crossfeed(args):
    hub = connect(args.url)
    preamble = read_prompt(args)
    pids = list(args.pane or [])
    if len(pids) < 2:
        raise ConsultError("crossfeed needs at least two --pane ids")
    st = _state(hub, {pid: 1 << 40 for pid in pids})
    seqs = {}
    for pid in pids:
        p = _pane_in(st, pid)
        if p is None:
            raise ConsultError(f"no pane {pid}")
        seqs[pid] = int(p.get("seq") or 0)
    r = hub.post("/api/session/crossfeed", {"panes": pids, "text": preamble})
    results = _parallel([(pid, hub, pid, seqs[pid], args.timeout, pid, preamble.strip(), True)
                         for pid in pids if r["results"].get(pid) is None], wait_turn)
    out = []
    for pid in pids:
        rec = _pane_record(hub, pid)
        if r["results"].get(pid):
            rec.update({"ok": False, "why": r["results"][pid], "complete": False, "text": ""})
        else:
            rec.update(results[pid])
            rec["ok"] = bool(rec["complete"] and rec["text"])
        out.append(rec)
    print(json.dumps({"arms": out}, indent=2), flush=True)
    return 0 if sum(1 for x in out if x["ok"]) >= 1 else 1


# No `wait <pane>` verb: without a hub turn id a client cannot tell which
# turn is current, so callers only wait on turns they sent.


def cmd_close(args):
    hub = connect(args.url)
    out = {}
    for pid in args.pane:
        try:
            hub.post("/api/session/close", {"pane": pid})
            out[pid] = "closed"
        except ConsultError as e:
            out[pid] = str(e)
    print(json.dumps(out, indent=2), flush=True)
    return 0 if any(v == "closed" for v in out.values()) else 1


def _prompt_args(p):
    p.add_argument("--prompt")
    p.add_argument("--prompt-file")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                   help=f"wall budget per arm, seconds (default {DEFAULT_TIMEOUT_S})")


def _foreground_arg(p):
    p.add_argument("--foreground", action="store_true",
                   help="show new panes on the wall (default: minimized until "
                        "one needs you)")


def build_parser():
    """The CLI parser, separate from main() so tests can inspect defaults."""
    ap = argparse.ArgumentParser(prog="corral-light consult",
                                 description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=DEFAULT_URL)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("lanes", help="which lanes are live (no spend); JSON unless --table")
    p.add_argument("--table", action="store_true", help="human rows instead of JSON")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)   # the default
    p.set_defaults(fn=cmd_lanes)

    p = sub.add_parser("ask", help="new pane on a lane, one prompt, wait")
    p.add_argument("--lane", required=True)
    p.add_argument("--cwd", default=str(Path.home()))
    p.add_argument("--title")
    p.add_argument("--model")
    p.add_argument("--effort")
    # No default: an unset posture lets the hub apply its DEFAULT_POSTURE.
    p.add_argument("--posture", default=None)
    p.add_argument("--config", action="append", metavar="ID=VALUE",
                   help="extra lane config, e.g. mode=read-only (repeatable)")
    p.add_argument("--close", action="store_true", help="close the pane afterwards")
    _foreground_arg(p)
    _prompt_args(p)
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("send", help="send to an existing pane, wait")
    p.add_argument("--pane", required=True)
    _prompt_args(p)
    p.set_defaults(fn=cmd_send)

    p = sub.add_parser("fanout", help="one prompt to several lanes or panes")
    p.add_argument("--lane", action="append")
    p.add_argument("--pane", action="append")
    p.add_argument("--cwd", default=str(Path.home()))
    p.add_argument("--title")
    # No default: an unset posture lets the hub apply its DEFAULT_POSTURE.
    p.add_argument("--posture", default=None)
    _foreground_arg(p)
    _prompt_args(p)
    p.set_defaults(fn=cmd_fanout)

    p = sub.add_parser("crossfeed", help="the hub's round-two verb over existing panes")
    p.add_argument("--pane", action="append", required=True)
    _prompt_args(p)
    p.set_defaults(fn=cmd_crossfeed)

    p = sub.add_parser("close", help="close panes")
    p.add_argument("--pane", action="append", required=True)
    p.set_defaults(fn=cmd_close)

    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ConsultError as e:
        print(f"consult: {e}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
