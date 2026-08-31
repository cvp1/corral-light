#!/usr/bin/python3
"""lane_probe — ask a lane, for real, whether it works here and what it offers.

WHY (dogma-2, 2026-08-31)
    Craig's Claude pane died with `Authentication required`, and the
    new-conversation dialog offered him no model and no effort to pick. Those
    look like two bugs. They are one:

      * a lane's model/effort lists come ONLY from a completed `session/new`
        (never a hardcoded list — a hardcoded one goes stale the day a vendor
        ships a model, and then the picker offers something the agent rejects);
      * so a lane that cannot authenticate can never populate its own pickers;
      * and the first thing an operator does on a new box is open that dialog,
        which is precisely when nothing has completed yet.

    The picker's honest empty state ("Default — agent decides", disabled) is
    correct and useless on day one.

    Worse, the availability check could not tell him either. Every other lane
    is gated on a credential FILE, which is a guess about where a vendor keeps
    its secret: for Claude on macOS that guess is wrong (the credential can
    live in the Keychain), and a check that refuses a working lane is the same
    class of error as one that offers a broken lane. So this build left the
    Claude lane optimistic, and the refusal arrived as a dead pane instead.

    The way out is not a better guess. It is to stop guessing: run the real
    adapter, do the real handshake, and report what actually happened
    (PRINCIPLES 13 — validate live; 1 — distrust green). One short-lived
    process answers both questions at once, on every platform, with no
    knowledge of where any vendor stores anything.

COST AND BOUNDS (P8)
    One subprocess per lane, killed as soon as it answers. Results are cached
    for CACHE_S so `doctor` and the picker do not spawn a process per call.
    The handshake carries acp.py's own HANDSHAKE_TIMEOUT — a pre-session call
    has no working space to protect, so failing is how the operator learns it
    did not attach.
"""
import threading
import time
from pathlib import Path

import acp

CACHE_S = 120           # a login does not change second to second
_cache = {}             # key -> (at, result)
_lock = threading.Lock()


def probe(key, cwd=None, force=False):
    """Run one lane's handshake. Returns, and never raises:

        {"ok": bool, "config": {id: {...}}, "error": str}

    `config` is the agent's OWN configOptions, verbatim — the same response a
    real pane absorbs, so a model seeded from here is one the lane will
    actually accept.
    """
    import sessions                      # local: sessions imports this module
    with _lock:
        hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < CACHE_S:
        return hit[1]

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
        # session/new is where auth actually surfaces for most adapters, and
        # it is also the ONLY place the model catalog comes from. The same
        # call answers both questions, which is why the probe goes this far
        # and not one step less.
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
        # The vendor's own words, not a paraphrase. "Authentication required"
        # is a sentence an operator can act on; "lane unavailable" is not.
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
    """The environment a PANE would get — private config dir included.

    The first version of this deliberately passed config_dir=None, reasoning
    that "the probe asks whether the lane works as the user has it, not under
    a pane's private posture directory". That reasoning produced a probe that
    could not see the exact failure it was written to catch: on dogma-2 it
    reported `ok Claude Code` while every pane died at its first prompt with
    `Authentication required`, because the probe ran under ~/.claude (which
    works) and the pane ran under a private CLAUDE_CONFIG_DIR holding no
    credential (which does not).

    A probe that does not run the way the real thing runs is not evidence
    about the real thing. It is a second, easier question that happens to
    have a nicer answer — the definition of a green light you cannot trust.
    """
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
    """The whole configOptions dict from the probe — model AND effort.

    catalog_probe only carries a model, because that is the shape the seeding
    path already spoke. Effort is the other half of what the dialog is missing
    on a fresh box, and it comes from the same handshake for free.
    """
    return (probe(key).get("config") or {})
