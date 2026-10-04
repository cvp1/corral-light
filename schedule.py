#!/usr/bin/python3
"""later — run a conversation later, or daily/weekly, without you present.

A job opens a pane and sends a prompt (or lands a nudge/resume on an existing
pane) at its time; it never answers permission gates and never retries.
Missed runs older than CATCHUP_S are skipped, not stampeded.
"""
from __future__ import annotations

import json
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_JOBS = 40
MAX_FAILED = 10             # failed/missed one-shots kept as a record
CATCHUP_S = 3 * 3600        # a missed run older than this is skipped
TICK_S = 20
MAX_PROMPT = 8000
ACTIONS = ("start", "nudge", "resume")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_when(s):
    """Parse a UTC stamp (…Z) or a naive local wall-clock time into UTC."""
    s = (s or "").strip()
    if not s:
        raise ValueError("when is required")
    for fmt, utc in (("%Y-%m-%dT%H:%M:%SZ", True), ("%Y-%m-%dT%H:%MZ", True),
                     ("%Y-%m-%dT%H:%M:%S", False), ("%Y-%m-%dT%H:%M", False),
                     ("%Y-%m-%d %H:%M", False)):
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=timezone.utc) if utc else \
            dt.astimezone().astimezone(timezone.utc)
    raise ValueError(f"could not read a time from {s!r} (YYYY-MM-DDTHH:MM)")


class Scheduler:
    """One background ticker over a JSON file. Owned by the Manager."""

    def __init__(self, mgr, path):
        self.mgr = mgr
        self.path = Path(path)
        self._lock = threading.Lock()
        self.jobs = self._load()
        self._stop = threading.Event()
        self._thread = None

    # ── storage ──────────────────────────────────────────────────────────
    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return [j for j in d.get("jobs", []) if isinstance(j, dict)][:MAX_JOBS]
        except (OSError, ValueError):
            return []

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"jobs": self.jobs}, indent=1), encoding="utf-8")
            tmp.replace(self.path)          # atomic
        except OSError as e:
            print(f"corral-light: could not save {self.path}: {e}",
                  file=sys.stderr, flush=True)

    # ── api ──────────────────────────────────────────────────────────────
    def add(self, agent, cwd, prompt, when, repeat="", posture=None,
            model=None, effort=None, title="", action="start", pane_id=None,
            role=None):
        import sessions
        prompt = (prompt or "").strip()
        ask = prompt
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r} — one of {', '.join(ACTIONS)}"
                             + (" (Light has no attention queue to remind into)"
                                if action == "remind" else ""))
        role_sha, role_notes = None, []
        if role:
            if action != "start":
                raise ValueError(f"a role starts a new conversation; it cannot "
                                 f"be assigned to a scheduled {action}")
            import roles
            try:
                r = roles.resolve(role, lane=agent or None, posture=posture,
                                  effort=effort)
                prompt = roles.compose(r.preamble, prompt)
            except roles.RoleError as e:
                raise ValueError(str(e)) from None
            agent, posture, effort = r.agent, r.posture, r.effort
            role_sha, role_notes = r.sha256, list(r.notes or [])
        if action in ("nudge", "resume"):
            # Validate the target at arm time, not fire time.
            if not pane_id:
                raise ValueError(f"a scheduled {action} needs a pane to land on")
            pane = self.mgr.panes.get(pane_id)
            if pane is None:
                raise ValueError(f"no pane {pane_id} — it may have been closed")
            if action == "nudge" and not prompt:
                raise ValueError("a nudge needs a message")
            agent, cwd = pane.agent, pane.cwd
            title = title or pane.title or ""
        elif not prompt:
            raise ValueError("a scheduled run needs a prompt — otherwise it "
                             "opens an agent and asks it nothing")
        if len(prompt) > MAX_PROMPT:
            raise ValueError(f"prompt exceeds {MAX_PROMPT} chars")
        if repeat not in ("", "daily", "weekly"):
            raise ValueError(f"unknown repeat {repeat!r}")
        if posture and posture not in sessions.POSTURES:
            raise ValueError(f"unknown posture {posture!r} — one of "
                             f"{', '.join(sorted(sessions.POSTURES))}")
        spec = sessions.AGENTS.get(agent)
        if action == "start" and spec is None:
            raise ValueError(f"unknown agent {agent!r}")
        if spec and spec.get("unavailable"):
            raise ValueError(f"{agent}: {spec['unavailable']}")
        at = parse_when(when)
        if at < _now() - timedelta(seconds=CATCHUP_S):
            raise ValueError("that time is already well past")
        if action == "start" and not Path(cwd or "").expanduser().is_dir():
            raise ValueError(f"not a directory: {cwd}")
        with self._lock:
            if len(self.jobs) >= MAX_JOBS:
                raise ValueError(f"{MAX_JOBS} scheduled jobs is the cap")
            job = {"id": f"s{uuid.uuid4().hex[:12]}", "agent": agent,
                   "role": role or None, "role_sha": role_sha,
                   "role_notes": role_notes,
                   "cwd": str(Path(cwd).expanduser()) if cwd else "",
                   "prompt": prompt, "at": _iso(at), "repeat": repeat,
                   "posture": posture, "model": model, "effort": effort,
                   "action": action, "pane_id": pane_id,
                   "title": (title or ask or prompt)[:60],
                   "created": _iso(_now()), "last": None, "last_error": None}
            self.jobs.append(job)
            self._save()
        return job

    def remove(self, job_id):
        with self._lock:
            before = len(self.jobs)
            self.jobs = [j for j in self.jobs if j.get("id") != job_id]
            if len(self.jobs) == before:
                raise ValueError(f"no scheduled job {job_id}")
            self._save()
        return job_id

    def list(self):
        with self._lock:
            return sorted((dict(j) for j in self.jobs), key=lambda j: j.get("at") or "")

    # ── the ticker ───────────────────────────────────────────────────────
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="later-ticker")
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(TICK_S):
            try:
                self.tick()
            except Exception as e:           # noqa: BLE001
                print(f"corral-light: scheduler tick failed: {e}",
                      file=sys.stderr, flush=True)

    def tick(self, now=None):
        """Fire everything due. Returns the jobs it acted on."""
        now = now or _now()
        with self._lock:
            due = [j for j in self.jobs
                   if not j.get("failed") and parse_when(j["at"]) <= now]
        for job in due:
            if (now - parse_when(job["at"])).total_seconds() > CATCHUP_S:
                self._settle(job, error="missed while Corral Light was down — "
                                        "not run, the moment for it had passed")
                continue
            try:
                self._fire(job)
                self._settle(job)
            except Exception as e:           # noqa: BLE001
                self._settle(job, error=str(e)[:200])
        return due

    def _fire_target(self, job):
        """Land a nudge/resume on an existing pane; refuse if it is gone or
        blocked at a permission gate."""
        pane = self.mgr.panes.get(job.get("pane_id") or "")
        if pane is None:
            raise RuntimeError("its pane is gone — nothing was resumed, and "
                               "no substitute conversation was opened")
        if pane.pending:
            raise RuntimeError("its pane is blocked at a permission gate — the "
                               "message was NOT queued behind the gate")
        if job.get("action") == "resume" and not (job.get("prompt") or "").strip():
            if pane.state in ("detached", "dead"):
                pane.resume()
                if pane.state == "dead":
                    raise RuntimeError(pane.error or "could not resume")
        else:
            pane.send(job["prompt"])      # detached/dead -> resumes first
        job["pane"] = pane.id

    def _fire(self, job):
        if job.get("action") in ("nudge", "resume"):
            return self._fire_target(job)
        import sessions
        pane = self.mgr.create(job["agent"], job["cwd"],
                               job.get("posture") or sessions.DEFAULT_POSTURE,
                               job.get("model"), job.get("effort"),
                               role=job.get("role"), role_sha=job.get("role_sha"),
                               background=True)   # unattended: minimized until it needs you
        if pane.state == "dead":
            raise RuntimeError(f"the agent did not start: {pane.error}")
        pane.rename(job["title"] or job["prompt"][:60])
        pane.emit("note", {"text": f"started by a scheduled job ({job['id']}, "
                                   f"{job.get('repeat') or 'once'})"})
        pane.send(job["prompt"])
        job["pane"] = pane.id

    def _settle(self, job, error=None):
        """Advance a repeat, or retire a one-shot. Always records the outcome."""
        with self._lock:
            job["last"] = _iso(_now())
            job["last_error"] = error
            step = {"daily": timedelta(days=1),
                    "weekly": timedelta(weeks=1)}.get(job.get("repeat"))
            if step:
                nxt, now = parse_when(job["at"]), _now()
                while nxt <= now:            # next future slot, not +1 step
                    nxt += step
                job["at"] = _iso(nxt)
            elif error:
                job["failed"] = True         # kept as a record until dismissed
                dead = [j for j in self.jobs if j.get("failed")]
                for old in sorted(dead, key=lambda j: j.get("last") or "")[:-MAX_FAILED]:
                    self.jobs = [j for j in self.jobs if j is not old]
            else:
                self.jobs = [j for j in self.jobs if j.get("id") != job["id"]]
            self._save()
