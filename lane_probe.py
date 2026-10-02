#!/usr/bin/python3
"""lane_probe — ask a lane, for real, whether it works here and what it offers.

Runs the real adapter through initialize + session/new, which answers both
"can it authenticate" and "which models/efforts does it offer". One
short-lived subprocess per lane; results cached for CACHE_S.
"""
import threading
import time
from pathlib import Path

import acp

CACHE_S = 120
_cache = {}             # key -> (at, result)
_lock = threading.Lock()
_refreshing = set()     # keys with a background re-probe in flight


def probe(key, cwd=None, force=False):
    """Run one lane's handshake. Returns, and never raises:

        {"ok": bool, "config": {id: {...}}, "error": str}

    `config` is the agent's own configOptions, verbatim.

    Cached CACHE_S. A stale entry is served as is while one background thread
    re-probes, so the handshake (about two seconds of subprocess) never runs
    inside the /api/state request that happened to find the cache expired.
    """
    with _lock:
        hit = _cache.get(key)
        if hit and not force:
            if time.time() - hit[0] < CACHE_S:
                return hit[1]
            if key not in _refreshing:
                _refreshing.add(key)
                threading.Thread(target=_refresh, args=(key, cwd),
                                 daemon=True, name=f"lane-probe-{key}").start()
            return hit[1]
    return _probe_now(key, cwd)


def _refresh(key, cwd):
    try:
        _probe_now(key, cwd)
    finally:
        with _lock:
            _refreshing.discard(key)


def _probe_now(key, cwd):
    import sessions                      # local: sessions imports this module
    result = {"ok": False, "config": {}, "error": ""}
    spec = sessions.AGENTS.get(key)
    if not spec:
        result["error"] = f"no such lane {key!r}"
    else:
        missing = [p for p in spec.get("requires", ()) if not Path(p).exists()]
        if missing:
            result["error"] = f"not installed: {missing[0]}"
        else:
            result = _handshake(spec, cwd or str(Path.home()))
    with _lock:
        _cache[key] = (time.time(), result)
    return result


def _handshake(spec, cwd):
    client = None
    try:
        import sessions
        client = acp.AcpClient(spec["argv"], cwd, env=sessions_env(spec),
                               strip_env=sessions.strip_prefixes())
        client.initialize()
        # session/new surfaces auth failures and carries the model catalog.
        new = client.new_session_full(cwd, []) or {}
        config = {}
        for co in new.get("configOptions") or []:
            config[co.get("id")] = {
                "value": co.get("currentValue"),
                "name": co.get("name"),
                "realId": co.get("id"),
                "options": [{"value": o.get("value"), "name": o.get("name"),
                             "description": (o.get("description") or "")[:120]}
                            for o in (co.get("options") or [])][:20],
            }
        return {"ok": True, "config": config, "error": ""}
    except acp.AgentError as e:
        # The vendor's own words, not a paraphrase.
        return {"ok": False, "config": {}, "error": str(e)[:300]}
    except Exception as e:                      # noqa: BLE001 — never a blocker
        return {"ok": False, "config": {}, "error": f"{type(e).__name__}: {e}"[:300]}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:                   # noqa: BLE001
                pass


def sessions_env(spec):
    """The environment a pane would get, private config dir included, so the
    probe tests what a pane actually runs."""
    import sessions
    config_dir = None
    if spec.get("posture_via_config_dir"):
        config_dir = sessions.seed_config_dir(
            sessions.STATE / "probe-config", sessions.DEFAULT_POSTURE)
    return sessions.spawn_env(spec, config_dir)


def catalog_probe(key):
    """A `catalog_probe`-shaped adapter: (values, default) for the model, or
    None. Used to fill the new-pane dialog BEFORE any pane has run."""
    r = probe(key)
    model = (r.get("config") or {}).get("model") or {}
    values = [o["value"] for o in model.get("options") or [] if o.get("value")]
    if not values:
        return None
    default = model.get("value")
    return (values, default if default in values else values[0])


def full_config(key):
    """The whole configOptions dict from the probe — model and effort."""
    return (probe(key).get("config") or {})
