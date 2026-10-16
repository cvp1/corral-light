"""The driving side of drive.py: panes that run on a paired hub, shown and
worked on this wall (docs/remote-panes-plan.md).

A mirror holds no process. It is the remote pane's last snapshot plus a ring
of its events, fed by the channel, and it sits in this wall's state next to
local panes. Commands on it go up the channel. The peer holds the truth: a
restart here keeps only which panes were mirrored, and the channel's `ready`
refills each from the peer's own ring.
"""
import json
import os
import threading
import time
from pathlib import Path

RING = 2000                # events a mirror keeps; the peer keeps the rest
OPEN_WAIT_S = 10           # how long `open` waits for a channel that is coming up
REFRESH_S = 60             # how often unlinked peers are asked what they allow
# Wall routes a mirror answers itself, and the channel command each one runs.
ROUTES = {"/api/session/send": "send", "/api/session/permission": "answer",
          "/api/session/cancel": "cancel", "/api/session/close": "close",
          "/api/session/resume": "resume"}


class Mirror:
    def __init__(self, peer, pane_id):
        self.peer, self.id = peer, pane_id
        self.snap = {"id": pane_id, "state": "starting", "pending": [], "title": "…",
                     "agent": "?", "label": "?", "events": [], "seq": 0}
        self.events = []
        self.lock = threading.Lock()
        self.minimized = False

    @property
    def last_seq(self):
        with self.lock:
            return self.events[-1]["seq"] if self.events else 0

    def apply_snapshot(self, s):
        """A whole snapshot (open, resume, resync). Returns True when it left
        a hole the wall must refetch around."""
        with self.lock:
            evs = s.get("events") or []
            last = self.events[-1]["seq"] if self.events else 0
            hole = bool(s.get("gap"))
            if evs and last and evs[0]["seq"] > last + 1:
                hole = True
            if hole:
                self.events = []
                last = 0
            self.events.extend(e for e in evs if e["seq"] > last)
            del self.events[:-RING]
            self.snap = {k: v for k, v in s.items() if k not in ("events", "gap")}
            return hole

    def apply_state(self, s):
        with self.lock:
            self.snap.update({k: v for k, v in s.items() if k not in ("events", "gap")})

    def apply_events(self, evs):
        """-> the events that are new, in order; [] for what we already have."""
        with self.lock:
            last = self.events[-1]["seq"] if self.events else 0
            new = [e for e in evs if e["seq"] > last]
            self.events.extend(new)
            del self.events[:-RING]
            return new

    def view(self, since, remote):
        with self.lock:
            d = dict(self.snap)
            d["events"] = [e for e in self.events if e["seq"] > since]
            d["seq"] = self.events[-1]["seq"] if self.events else d.get("seq", 0)
        d["minimized"] = self.minimized
        # The pane's real state stays; the badge says when the link is down.
        d["remote"] = remote
        return d


class Remote:
    """Every mirror on this hub, and the links that feed them."""

    def __init__(self, hubs, mgr, state_dir):
        self.hubs, self.mgr = hubs, mgr
        self.file = Path(state_dir) / "drive" / "mirrors.json"
        self.lock = threading.RLock()
        self.mirrors = {}                 # pane id -> Mirror
        self.peers = {}                   # peer id -> {name, lanes, home, ...}
        self.opened = {}                  # pane id -> threading.Event (open waits)

    # ── life ─────────────────────────────────────────────────────────────
    def start(self):
        for rec in self._load():
            pid, pane = rec.get("peer"), rec.get("pane")
            if pid and pane and pane not in self.mirrors:
                m = self.mirrors[pane] = Mirror(pid, pane)
                m.minimized = bool(rec.get("minimized"))
        self.ensure_links()
        threading.Thread(target=self._refresh_loop, name="drive-refresh",
                         daemon=True).start()
        return self

    def _refresh_loop(self):
        """Once a minute, ask each paired hub with no live link what it lets
        this one do (any signed answer carries it), so a `drive` granted over
        there shows up here without waiting for other traffic."""
        while True:
            time.sleep(REFRESH_S)
            try:
                links = self.hubs.drive_links()
                for p in self.hubs.status().get("peers") or []:
                    link = links.get(p["id"])
                    if link is not None and link.up.is_set():
                        continue
                    try:
                        self.hubs.call(p["id"], "GET", "/fed/v1/hello")
                    except Exception:                    # noqa: BLE001
                        pass
                self.ensure_links()
            except Exception:                            # noqa: BLE001
                pass

    def ensure_links(self):
        """A link to every peer that lets this hub drive it, and to every peer
        a mirror lives on. Cheap when they are already up."""
        try:
            peers = self.hubs.status().get("peers") or []
        except Exception:                                # noqa: BLE001
            return
        want = {p["id"] for p in peers if "drive" in (p.get("weMay") or [])}
        with self.lock:
            want |= {m.peer for m in self.mirrors.values()}
        known = {p["id"] for p in peers}
        for pid in want & known:
            self.link(pid)

    def link(self, pid):
        return self.hubs.drive_link(pid, self._on_frame,
                                    cursors=lambda pid=pid: self._cursors(pid))

    def _cursors(self, pid):
        with self.lock:
            return {m.id: m.last_seq for m in self.mirrors.values() if m.peer == pid}

    def _load(self):
        try:
            d = json.loads(self.file.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _save(self):
        with self.lock:
            rows = [{"peer": m.peer, "pane": m.id, "minimized": m.minimized}
                    for m in self.mirrors.values()]
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
            os.replace(tmp, self.file)
        except OSError:
            pass

    # ── frames from a link ───────────────────────────────────────────────
    def _remote(self, pid):
        link = self.hubs.drive_links().get(pid)
        info = self.peers.get(pid) or {}
        st = link.status() if link else {"up": False, "error": "not linked"}
        return {"peer": pid, "hub": info.get("name") or st.get("hub") or pid[:8],
                "up": bool(st.get("up")), "error": st.get("error")}

    def _poke(self, pane):
        """Make every browser re-read this pane (a layout event: an unknown
        pane makes the wall refetch; a known one takes the fields)."""
        m = self.mirrors.get(pane)
        data = {"remote": self._remote(m.peer)} if m else {}
        self.mgr.broadcast({"seq": 0, "pane": pane, "kind": "layout", "data": data,
                            "at": time.time()})

    def _on_frame(self, link, f):
        t, pid = f.get("t"), link.pid
        if t == "ready":
            with self.lock:
                self.peers[pid] = {"name": f.get("hub"), "lanes": f.get("lanes") or {},
                                   "home": f.get("home") or {}, "at": time.time()}
            live = set()
            for s in f.get("panes") or []:
                live.add(s["id"])
                self._take_snapshot(pid, s)
            # Mirrors the peer no longer has (closed there while we were away).
            with self.lock:
                stale = [k for k, m in self.mirrors.items() if m.peer == pid and k not in live]
                for k in stale:
                    del self.mirrors[k]
            for k in stale:
                self._poke(k)
            self._save()
            with self.lock:
                mine = [k for k, m in self.mirrors.items() if m.peer == pid]
            for k in mine:
                self._poke(k)
        elif t == "panes":
            for s in f.get("panes") or []:
                self._take_snapshot(pid, s)
        elif t == "ev":
            for pane, s in (f.get("states") or {}).items():
                m = self.mirrors.get(pane)
                if m and not s.get("gone"):
                    m.apply_state(s)
            for e in f.get("evs") or []:
                m = self.mirrors.get(e.get("pane"))
                if m is None or m.peer != pid:
                    continue
                for ev in m.apply_events([e]):
                    self.mgr.broadcast(ev)          # browsers and the notifier
        elif t == "lanes":
            with self.lock:
                self.peers.setdefault(pid, {})["lanes"] = f.get("lanes") or {}
        elif t == "down":
            with self.lock:
                mine = [k for k, m in self.mirrors.items() if m.peer == pid]
            for k in mine:
                self._poke(k)

    def _take_snapshot(self, pid, s):
        pane = s.get("id")
        if not pane:
            return
        if s.get("gone"):
            with self.lock:
                m = self.mirrors.pop(pane, None)
            if m:
                self._save()
                self._poke(pane)
            return
        with self.lock:
            m = self.mirrors.get(pane)
            new = m is None
            if new:
                if pane in getattr(self.mgr, "panes", {}):
                    return                  # an id this hub already uses locally
                m = self.mirrors[pane] = Mirror(pid, pane)
        hole = m.apply_snapshot(s)
        if new:
            self._save()
        ev = self.opened.get(pane)
        if ev:
            ev.set()
        self._poke(pane)
        if hole:
            self.mgr.broadcast({"seq": 0, "pane": None, "kind": "resync",
                                "data": {"reason": "a pane from another hub skipped ahead"},
                                "at": time.time()})

    # ── what the wall reads ──────────────────────────────────────────────
    def views(self, since=None):
        since = since or {}
        with self.lock:
            ms = list(self.mirrors.values())
        return [m.view(since.get(m.id, 0), self._remote(m.peer)) for m in ms]

    def hosts(self):
        """For the picker: each peer this hub may drive, with its lanes."""
        self.ensure_links()
        out = []
        for pid, link in self.hubs.drive_links().items():
            info = self.peers.get(pid) or {}
            st = link.status()
            out.append({"peer": pid, "name": info.get("name") or st.get("hub") or pid[:8],
                        "up": bool(st.get("up")), "error": st.get("error"),
                        "lanes": info.get("lanes") or {}, "home": info.get("home") or {}})
        return sorted(out, key=lambda h: h["name"] or "")

    def has(self, pane):
        return pane in self.mirrors

    def get(self, pane):
        m = self.mirrors.get(pane)
        if m is None:
            raise ValueError(f"no pane {pane!r} from another hub")
        return m

    # ── commands from the wall ───────────────────────────────────────────
    def _up_link(self, pid):
        link = self.link(pid)
        if not link.up.wait(OPEN_WAIT_S):
            raise ValueError(f"the other hub is not reachable: {link.error or 'no answer'}")
        return link

    def open(self, peer_ref, args):
        import drive
        pid, _p = self.hubs.find_peer(peer_ref)
        link = self._up_link(pid)
        try:
            res = link.call("open", {k: args.get(k) for k in
                                     ("lane", "cwd", "model", "effort", "posture", "title",
                                      "text")})
        except drive.DriveError as e:
            raise ValueError(e.why)
        pane = res["pane"]
        ev = self.opened.setdefault(pane, threading.Event())
        try:
            if pane not in self.mirrors:
                ev.wait(5)
        finally:
            self.opened.pop(pane, None)
        with self.lock:
            m = self.mirrors.get(pane)
            if m is None:                       # the snapshot is late: start bare
                m = self.mirrors[pane] = Mirror(pid, pane)
        self._save()
        return m.view(0, self._remote(pid))

    def call(self, pane, op, args, timeout=None):
        import drive
        m = self.get(pane)
        link = self.link(m.peer)
        kw = {"timeout": timeout} if timeout else {}
        try:
            res = link.call(op, {**args, "pane": pane}, **kw)
        except drive.DriveError as e:
            raise ValueError(e.why)
        if op == "close":
            with self.lock:
                self.mirrors.pop(pane, None)
            self._save()
        return res

    def route(self, path, b):
        """A wall POST for a mirror -> (status, body)."""
        pane = b.get("pane", "")
        op = ROUTES[path]
        if op == "send":
            r = self.call(pane, "send", {"text": b.get("text", "")})
            return {"ok": True, "turn": r.get("turn")}
        if op == "answer":
            r = self.call(pane, "answer", {"requestId": b.get("requestId", ""),
                                           "optionId": b.get("optionId", ""),
                                           "digest": b.get("digest") or "",
                                           "card": b.get("card") or ""})
            return {"ok": True, "delivered": r.get("delivered")}
        if op == "cancel":
            r = self.call(pane, "cancel", {})
            return {"ok": bool(r.get("ok"))}
        if op == "close":
            self.call(pane, "close", {})
            return {"ok": True}
        if op == "resume":
            self.call(pane, "resume", {})
            return {"ok": True, "pane": self.get(pane).view(0, self._remote(self.get(pane).peer))}
        raise ValueError("not supported on a pane from another hub")

    def pending(self, pane):
        return self.call(pane, "pending", {})

    def history(self, pane, before, n):
        return self.call(pane, "history", {"before": before, "n": n}).get("events") or []

    def set_minimized(self, pane, flag):
        m = self.get(pane)
        m.minimized = bool(flag)
        self._save()
        self._poke(pane)
        return m.minimized

    def forget(self, pane):
        """Drop the mirror here; the pane keeps running on its own hub."""
        with self.lock:
            m = self.mirrors.pop(pane, None)
        if m:
            self._save()
            self._poke(pane)
        return bool(m)

    def notice_target(self, pane):
        """(title, pending ids, state) for the notifier, or None."""
        m = self.mirrors.get(pane)
        if m is None:
            return None
        with m.lock:
            s = dict(m.snap)
        hub = (self.peers.get(m.peer) or {}).get("name") or m.peer[:8]
        return {"title": f"{s.get('title') or 'pane'} (on {hub})",
                "pending": list(s.get("pending") or []), "state": s.get("state")}
