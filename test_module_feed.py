#!/usr/bin/python3
"""The module feed (module_feed.py), Claude quota capture (sessions.py) and
the Claude adapter patch (adapter_patches.py).

docs/finops-module-plan.md §4.4, §4.6 and §8.1 ("Feed", "Quota capture, in
the adapter's real shapes", "Login facts"). Rate-limit updates are produced
by running the adapter's own `rate_limit_event` code (0.85.1, patched with
adapter_patches.py) under node, fed the notice shapes in
testkit/fixtures/module-feed/; no real agent is started.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from testkit.scratch import default_state, tmpdir  # noqa: E402
default_state("corral-light-test-")

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "testkit" / "fixtures" / "module-feed"
ADAPTER_PKG = "@agentclientprotocol/claude-agent-acp"
INFOS = json.loads((FIX / "rate_limit_info.json").read_text(encoding="utf-8"))
RESULT = json.loads((FIX / "result_usage_update.json").read_text(encoding="utf-8"))

SENTINEL_PROMPT = "SENTINEL-PROMPT-7f3a9c please never leak me"
SENTINEL_TOKEN = "sk-SENTINEL-TOKEN-91b2e4"
SENTINEL_ACCOUNT = "acct-SENTINEL-5d6e7f"

import adapter_patches  # noqa: E402
import module_feed  # noqa: E402

_HARNESS = r"""
const fs = require("fs");
const [file, infoJson, lastJson, model] = process.argv.slice(1);
const text = fs.readFileSync(file, "utf8");
const start = text.indexOf('case "rate_limit_event": {');
let end = text.indexOf('\n                    case "', start + 1);
if (end < 0) end = text.lastIndexOf("}");
const body = "switch (message.type) {" + text.slice(start, end) + "}";
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const run = new AsyncFunction("message", "lastAssistantTotalUsage", "session", "params",
                              "sendUpdate", "attachUsageModel", body);
const out = [];
const attach = (u) => !model ? u : {...u, _meta: {...(u._meta ?? {}), "_claude/model": model}};
run({type: "rate_limit_event", rate_limit_info: JSON.parse(infoJson)},
    JSON.parse(lastJson), {contextWindowSize: 200000}, {sessionId: "s1"},
    async (n) => { out.push(n.update); }, attach)
  .then(() => process.stdout.write(JSON.stringify(out)));
"""


def _quiet(fn, *a):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


def _node():
    return shutil.which("node") or os.environ.get("CORRAL_TEST_NODE")


def fake_spike(owner, source=None):
    """A spike/ tree holding the 0.85.1 adapter's rate-limit code (the
    fixture excerpt, or a copy of an installed file). Never the live tree."""
    spike = Path(tmpdir(owner, "spike-"))
    pkg = spike / "node_modules" / ADAPTER_PKG
    (pkg / "dist").mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"version": "0.85.1"}))
    shutil.copyfile(source or FIX / "acp-agent-0.85.1-rle.js", pkg / "dist" / "acp-agent.js")
    return spike


def adapter_rle(test, spike, info, last_usage, model="claude-sonnet-x"):
    """The usage_update(s) the adapter's own code sends for one notice."""
    node = _node()
    if not node:
        test.fail("node is required: the quota tests run the adapter's own code")
    f = spike / "node_modules" / ADAPTER_PKG / "dist" / "acp-agent.js"
    r = subprocess.run([node, "-e", _HARNESS, str(f), json.dumps(info),
                        json.dumps(last_usage), model or ""],
                       capture_output=True, text=True, timeout=30)
    test.assertEqual(r.returncode, 0, r.stderr[-500:])
    return json.loads(r.stdout)


class FeedCase(unittest.TestCase):
    """A private feed dir; the login stores are temp dirs with sentinels."""

    def setUp(self):
        self.state = Path(tmpdir(self, "feed-state-"))
        patcher = mock.patch.object(module_feed, "STATE_OVERRIDE", str(self.state))
        patcher.start()
        self.addCleanup(patcher.stop)
        module_feed.reset_for_tests()
        self.addCleanup(module_feed.reset_for_tests)
        # Sandbox probe: a fixed answer (its own tests live in test_modules).
        sb = mock.patch("module_sandbox.available", return_value=(True, ""))
        sb.start()
        self.addCleanup(sb.stop)
        self.homes = Path(tmpdir(self, "feed-homes-"))
        self.claude = self.homes / "claude"
        self.claude.mkdir()
        self.write_claude_login(SENTINEL_TOKEN, SENTINEL_ACCOUNT)
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.claude)})
        env.start()
        self.addCleanup(env.stop)
        import codex_launcher
        import grok_launcher
        self.codex = self.homes / "codex"
        (self.codex / "sessions" / "2026" / "10" / "07").mkdir(parents=True)
        (self.codex / "auth.json").write_text(json.dumps(
            {"tokens": {"access_token": SENTINEL_TOKEN, "account_id": SENTINEL_ACCOUNT}}))
        self.write_rollout("rollout-a.jsonl", SENTINEL_ACCOUNT)
        self.grok = self.homes / "grok"
        self.grok.mkdir()
        (self.grok / "auth.json").write_text(json.dumps(
            {"x": {"key": SENTINEL_TOKEN, "auth_mode": "oauth", "user_id": SENTINEL_ACCOUNT}}))
        for mod, attr, val in ((codex_launcher, "CODEX_HOME", self.codex),
                               (grok_launcher, "GROK_HOME", self.grok)):
            p = mock.patch.object(mod, attr, val)
            p.start()
            self.addCleanup(p.stop)

    # ── fixtures ──

    def write_claude_login(self, token, account, plan="max", tier="default_claude_max_5x"):
        (self.claude / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
            "accessToken": token, "refreshToken": token + "-r",
            "expiresAt": int(time.time() * 1000) + 3600_000,
            "refreshTokenExpiresAt": int(time.time() * 1000) + 86400_000,
            "subscriptionType": plan, "rateLimitTier": tier, "scopes": ["user:inference"]}}))
        (self.claude / ".claude.json").write_text(json.dumps({"oauthAccount": {
            "accountUuid": account, "emailAddress": "sentinel@example.invalid"}}))

    def write_rollout(self, name, account, plan="plus"):
        f = self.codex / "sessions" / "2026" / "10" / "07" / name
        lines = [{"type": "session_meta", "payload": {
                    "id": "t1", "creator_account_id": account,
                    "base_instructions": {"text": SENTINEL_PROMPT}}},
                 {"type": "response_item", "payload": {"text": SENTINEL_PROMPT}},
                 {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                     "plan_type": plan, "primary": {"used_percent": 12.0}}}}]
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))
        t = time.time() + len(list(f.parent.iterdir()))
        os.utime(f, (t, t))
        return f

    def write_pane(self, pid, events, meta=None):
        d = self.state / "panes" / pid
        d.mkdir(parents=True, exist_ok=True)
        m = {"id": pid, "agent": "claude", "cwd": "/srv/x", "title": "t", "created":
             "2026-10-07T10:00:00Z", "acp_session": "acp-1", "role": None,
             "worktree_id": None, "closed": False}
        m.update(meta or {})
        (d / "meta.json").write_text(json.dumps(m))
        with (d / "events.jsonl").open("a", encoding="utf-8") as fh:
            for i, (kind, data) in enumerate(events):
                fh.write(json.dumps({"seq": i + 1, "at": "2026-10-07T10:0%d:00Z" % (i % 10),
                                     "pane": pid, "kind": kind, "data": data}) + "\n")
        return d

    def pane(self, pid="p-quota"):
        import sessions
        p = sessions.Pane.__new__(sessions.Pane)
        p.id, p.agent = pid, "claude"
        p._init_runtime()
        p.mgr = mock.Mock()
        return p

    def feed(self, name):
        return json.loads((module_feed.feed_dir() / name).read_text(encoding="utf-8"))

    def windows(self):
        accts = self.feed("quota.json")["accounts"]
        self.assertEqual(len(accts), 1, accts.keys())
        return next(iter(accts.values()))["windows"]


class FeedFiles(FeedCase):

    def test_feed_is_written_atomically_with_private_modes(self):
        wrote = module_feed.tick(None)
        self.assertEqual(set(wrote), {"panes.json", "logins.json", "host.json", "quota.json"})
        d = module_feed.feed_dir()
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(d.parent.stat().st_mode), 0o700)
        for f in d.iterdir():
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600, f.name)
            self.assertFalse(f.name.startswith("."), f"temp file left: {f.name}")
        # A failed rename leaves the previous file whole and no temp behind.
        self.write_pane("p1", [("turn_end", {"turn": "t1", "usage": {"used": 5}})])
        before = (d / "panes.json").read_bytes()
        with mock.patch("module_feed.os.replace", side_effect=OSError("disk")):
            module_feed.tick(None)
        self.assertEqual((d / "panes.json").read_bytes(), before)
        self.assertEqual(sorted(f.name for f in d.iterdir()),
                         ["host.json", "logins.json", "panes.json", "quota.json"])
        module_feed.tick(None)
        self.assertEqual(len(self.feed("panes.json")["panes"]), 1)
        host = self.feed("host.json")
        self.assertEqual(host["platform"], sys.platform)
        self.assertTrue(host["sandbox"]["available"])

    def test_usage_comes_from_turn_end_events_incrementally(self):
        self.write_pane("p1", [
            ("user", {"text": SENTINEL_PROMPT, "via": "consult", "turn": "t1"}),
            ("ready", {"model": "claude-sonnet-x"}),
            ("turn_end", {"stopReason": "end_turn", "turn": "t1",
                          "usage": {"used": 100, "size": 200000,
                                    "cost": {"amount": 0.5, "currency": "USD"}}})])
        self.write_pane("p2", [("user", {"text": "x"})], {"challenge_of": "p1"})
        module_feed.tick(None)
        rows = {r["id"]: r for r in self.feed("panes.json")["panes"]}
        self.assertEqual(rows["p1"]["usage"], [{
            "turn": "t1", "at": "2026-10-07T10:02:00Z", "used": 100, "size": 200000,
            "cost": {"amount": 0.5, "currency": "USD"}, "stop": "end_turn"}])
        self.assertEqual(rows["p1"]["origin"], "consult")
        self.assertEqual(rows["p1"]["model"], "claude-sonnet-x")
        self.assertEqual(rows["p2"]["origin"], "challenge")
        self.assertEqual(rows["p2"]["challenge_of"], "p1")
        # Nothing changed: nothing rewritten.
        self.assertEqual(module_feed.tick(None), [])
        # Appended: only the new bytes are read.
        log = self.state / "panes" / "p1" / "events.jsonl"
        with mock.patch.object(module_feed, "_scan_lines",
                               wraps=module_feed._scan_lines) as scan:
            with log.open("a") as fh:
                fh.write(json.dumps({"seq": 9, "at": "2026-10-07T11:00:00Z", "pane": "p1",
                                     "kind": "turn_end", "data": {
                                         "turn": "t2", "usage": {"used": 300}}}) + "\n")
            self.assertEqual(module_feed.tick(None), ["panes.json"])
            self.assertEqual(scan.call_count, 1)
            self.assertNotIn(b"SENTINEL", scan.call_args[0][0])
        rows = {r["id"]: r for r in self.feed("panes.json")["panes"]}
        self.assertEqual([u["turn"] for u in rows["p1"]["usage"]], ["t1", "t2"])

    def test_closed_panes_older_than_the_window_are_left_out(self):
        d = self.write_pane("old", [("turn_end", {"usage": {"used": 1}})], {"closed": True})
        self.write_pane("new", [("turn_end", {"usage": {"used": 1}})], {"closed": True})
        old = time.time() - (module_feed.KEEP_DAYS + 1) * 86400
        os.utime(d / "meta.json", (old, old))
        module_feed.tick(None)
        rows = self.feed("panes.json")["panes"]
        self.assertEqual([r["id"] for r in rows], ["new"])
        self.assertIsNotNone(rows[0]["closed"])

    def test_no_prompt_and_no_token_ever_reaches_the_feed(self):
        self.write_pane("p1", [
            ("user", {"text": SENTINEL_PROMPT}),
            ("tool", {"title": SENTINEL_PROMPT, "rawInput": {"command": SENTINEL_TOKEN}}),
            ("text", {"text": SENTINEL_PROMPT}),
            ("turn_end", {"turn": "t1", "usage": {"used": 1, "_meta": {"x": SENTINEL_TOKEN}}})])
        p = self.pane()
        p._on_event("usage_update", dict(RESULT, _meta={"_claude/rateLimit": dict(
            INFOS["phase0"], note=SENTINEL_TOKEN)}))
        module_feed.tick(None)
        logins = self.feed("logins.json")["lanes"]
        self.assertTrue(logins["claude"]["present"])
        self.assertEqual(logins["claude"]["plan"], "max")
        self.assertEqual(logins["codex"]["plan"], "plus")
        self.assertEqual(logins["grok"]["auth_mode"], "present")
        self.assertTrue(logins["claude"]["fingerprint"])
        self.assertTrue(logins["codex"]["fingerprint"])
        for f in module_feed.feed_dir().iterdir():
            text = f.read_text(encoding="utf-8")
            for s in (SENTINEL_PROMPT, SENTINEL_TOKEN, SENTINEL_ACCOUNT, "SENTINEL",
                      "sentinel@example"):
                self.assertNotIn(s, text, f"{f.name} carries {s!r}")


class QuotaCapture(FeedCase):
    """Rate-limit updates from the adapter's own code, through Pane._on_event."""

    def setUp(self):
        super().setUp()
        self.spike = fake_spike(self)
        rows = adapter_patches.apply(self.spike)
        self.assertEqual(rows[0]["status"], "patched")
        self.p = self.pane()

    def rle(self, name, last_usage=12000):
        (u,) = adapter_rle(self, self.spike, INFOS[name], last_usage)
        return u

    def test_rate_limit_then_result_keeps_cost_and_window(self):
        self.p._on_event("usage_update", self.rle("phase0"))
        self.p._on_event("usage_update", RESULT)
        self.assertEqual(self.p.usage["cost"], RESULT["cost"])
        self.assertEqual(self.p.usage["used"], RESULT["used"])
        self.assertEqual(set(self.p.quota), {"five_hour", "seven_day"})
        self.assertEqual(set(self.windows()), {"five_hour", "seven_day"})
        self.assertNotIn("_claude/rateLimit", self.p.usage.get("_meta", {}))

    def test_result_then_rate_limit_keeps_both(self):
        self.p._on_event("usage_update", RESULT)
        self.p._on_event("usage_update", self.rle("phase0", RESULT["used"]))
        self.assertEqual(self.p.usage["cost"], RESULT["cost"])
        self.assertEqual(self.p.quota["seven_day"]["utilization"], 0.07)
        self.p._on_event("usage_update", {"sessionUpdate": "usage_update", "used": 7})
        self.assertEqual(self.p.usage["cost"], RESULT["cost"])
        self.assertIn("five_hour", self.p.quota)

    def test_rate_limit_before_first_usage_reaches_the_feed_only_with_the_patch(self):
        unpatched = fake_spike(self)
        self.assertEqual(adapter_rle(self, unpatched, INFOS["phase0"], None), [],
                         "the unpatched adapter drops it (the bug being patched)")
        self.assertTrue(adapter_patches.drops_early_rate_limits(unpatched))
        (u,) = adapter_rle(self, self.spike, INFOS["phase0"], None)
        self.assertNotIn("used", u)
        self.p._on_event("usage_update", u)          # no `used`: accepted
        self.assertNotIn("used", self.p.usage)
        w = self.windows()
        self.assertEqual(w["five_hour"]["utilization"], 0.16)
        self.assertEqual(w["five_hour"]["status"], "allowed")
        self.assertEqual(w["five_hour"]["pane"], self.p.id)

    def test_opus_window_and_overage_fields_survive(self):
        self.p._on_event("usage_update", self.rle("opus_overage"))
        w = self.windows()["seven_day_opus"]
        for k in ("status", "utilization", "surpassedThreshold", "overageStatus",
                  "overageResetsAt", "isUsingOverage", "overageInUse", "resetsAt"):
            self.assertEqual(w[k], INFOS["opus_overage"][k], k)
        self.assertEqual(self.windows()["seven_day_opus"]["overageResetsAt"], 1792000000)

    def test_missing_type_is_kept_under_its_own_key_and_ms_resets_are_normalised(self):
        self.p._on_event("usage_update", self.rle("no_type"))
        w = self.windows()
        self.assertEqual(set(w), {"_unknown"})
        self.assertEqual(w["_unknown"]["resets_at_s"], 1791403800.0)
        self.assertEqual(w["_unknown"]["resets_at_unit"], "ms")
        self.assertEqual(w["_unknown"]["resetsAt"], INFOS["no_type"]["resetsAt"])

    def test_missing_resets_at_is_recorded_as_null_so_it_reads_stale(self):
        self.p._on_event("usage_update", self.rle("no_resets"))
        w = self.windows()["seven_day"]
        self.assertIsNone(w["resets_at_s"])
        self.assertIsNone(w["resets_at_unit"])
        self.assertNotIn("resetsAt", w)
        self.assertEqual(w["status"], "rejected")
        self.assertIsInstance(w["observed_at"], float)

    def test_utilization_is_passed_through_unscaled(self):
        self.p._on_event("usage_update", self.rle("phase0"))
        w = self.windows()
        self.assertEqual(w["five_hour"]["utilization"], 0.16)
        self.assertEqual(w["seven_day"]["utilization"], 0.07)
        self.assertEqual(w["five_hour"]["resets_at_s"], 1791403800.0)
        self.assertEqual(w["five_hour"]["resets_at_unit"], "s")

    def test_a_hub_restart_mid_turn_keeps_the_last_observation(self):
        self.p._on_event("usage_update", self.rle("phase0"))      # no turn_end follows
        self.assertTrue((module_feed.feed_dir() / "quota.json").is_file())
        module_feed.reset_for_tests()                              # the hub restarts
        module_feed.tick(None)
        self.assertEqual(self.windows()["seven_day"]["utilization"], 0.07)
        other = self.pane("p-after")
        other._on_event("usage_update", self.rle("opus_overage"))
        self.assertEqual(set(self.windows()), {"five_hour", "seven_day", "seven_day_opus"})
        other._on_event("usage_update", self.rle("phase0_later"))
        self.assertEqual(self.windows()["five_hour"]["utilization"], 0.21)

    def test_a_replayed_history_does_not_count_as_an_observation(self):
        self.p._replaying = True
        self.p._on_event("usage_update", self.rle("phase0"))
        self.assertFalse((module_feed.feed_dir() / "quota.json").exists())


class QuotaMerge(FeedCase):
    """Round three, findings 5 and 6: per-field merge, distinct window keys."""

    def note(self, info, at):
        module_feed.note_quota(self.pane(), module_feed.quota_windows(info, observed_at=at))

    def test_a_later_notice_without_a_figure_keeps_the_last_one_and_says_when(self):
        self.note({"rateLimitType": "seven_day", "status": "allowed", "utilization": 0.07,
                   "resetsAt": 1791403800}, 1000.0)
        self.note({"rateLimitType": "seven_day", "status": "rejected"}, 2000.0)
        w = self.windows()["seven_day"]
        self.assertEqual(w["status"], "rejected")
        self.assertEqual(w["observed_at"], 2000.0)
        self.assertEqual(w["utilization"], 0.07)
        self.assertEqual(w["resets_at_s"], 1791403800.0)
        self.assertEqual(w["carried"], {"utilization": 1000.0, "resetsAt": 1000.0})
        self.note({"rateLimitType": "seven_day", "status": "rejected"}, 3000.0)
        self.assertEqual(self.windows()["seven_day"]["carried"]["utilization"], 1000.0,
                         "a carried field keeps the time it was observed")
        self.note({"rateLimitType": "seven_day", "status": "allowed", "utilization": 0.5},
                  4000.0)
        w = self.windows()["seven_day"]
        self.assertEqual(w["utilization"], 0.5)
        self.assertEqual(w["carried"], {"resetsAt": 1000.0})
        self.note({"rateLimitType": "seven_day", "status": "allowed", "utilization": 0.6,
                   "resetsAt": 1791500000}, 5000.0)
        self.assertNotIn("carried", self.windows()["seven_day"])
        # An older observation arriving late changes nothing.
        self.note({"rateLimitType": "seven_day", "status": "rejected"}, 1500.0)
        self.assertEqual(self.windows()["seven_day"]["status"], "allowed")

    def test_unknown_window_names_keep_their_own_keys(self):
        info = {"rateLimitType": "weekly limit", "status": "allowed", "utilization": 0.1,
                "unifiedWindows": {"five hour!": {"utilization": 0.2},
                                   "bad/name": {"utilization": 0.3}}}
        ws = module_feed.quota_windows(info, observed_at=1.0)
        self.assertEqual(len(ws), 3, sorted(ws))
        self.assertNotIn("_unknown", ws)
        for k in ws:
            self.assertRegex(k, module_feed._KEY_RE)
        self.assertEqual(set(ws), set(module_feed.quota_windows(info, observed_at=2.0)),
                         "keys must be stable across notices")
        self.assertEqual(set(module_feed.quota_windows({"status": "allowed"})), {"_unknown"})
        long_a, long_b = "x" * 80 + " a", "x" * 80 + " b"
        self.assertNotEqual(module_feed._window_key(long_a), module_feed._window_key(long_b))


class PaneTitles(FeedCase):
    """Round three, finding 1: a pane named after its first prompt must not
    carry that prompt into the feed. Only a name the operator typed is
    published; otherwise the lane's label."""

    def test_a_title_from_the_first_prompt_never_reaches_the_feed(self):
        import sessions
        from test_resilience import FakeLaneCase, wait_for
        lane = FakeLaneCase("run")
        lane.setUp()
        self.addCleanup(lane.doCleanups)
        p = lane.mgr.create("fake", lane.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        p.send(SENTINEL_PROMPT + " with enough words to pass the cut")
        self.assertTrue(wait_for(lambda: lane.turn_ends(p) >= 1))
        self.assertIn("SENTINEL", p.title, "the real path did not name the pane")
        # A port locks a title it copied; locked is not the same as typed.
        p.title_locked = True
        p.save_meta()

        def row(mgr):
            module_feed._pane_cache.clear()
            with mock.patch.object(module_feed, "STATE_OVERRIDE", str(sessions.STATE)):
                doc = module_feed.panes_doc(mgr)
            self.assertNotIn("SENTINEL", json.dumps(doc))
            return next(r for r in doc["panes"] if r["id"] == p.id)
        self.assertEqual(row(lane.mgr)["title"], "Fake")
        self.assertEqual(row(None)["title"], "Fake")          # from meta.json alone
        p.rename("Budget review")
        self.assertEqual(row(lane.mgr)["title"], "Budget review")
        self.assertEqual(row(None)["title"], "Budget review")


class TurnEndCarriesQuota(FeedCase):
    """End to end with a real process: the fake agent replays recorded
    adapter updates; the pane's turn_end carries the merged usage and the
    windows, and quota.json was written on arrival."""

    def test_turn_end_writes_merged_usage_and_windows(self):
        import sessions
        from test_resilience import FakeLaneCase, wait_for
        spike = fake_spike(self)
        adapter_patches.apply(spike)
        replay = Path(tmpdir(self, "replay-"))
        (rle,) = adapter_rle(self, spike, INFOS["phase0"], None)
        (replay / "turn.jsonl").write_text(json.dumps(rle) + "\n" + json.dumps(RESULT) + "\n")
        lane = FakeLaneCase("run")
        lane.setUp()
        self.addCleanup(lane.doCleanups)
        sessions.AGENTS["fake"]["env"]["FAKE_ACP_REPLAY"] = str(replay)
        p = lane.mgr.create("fake", lane.agent_dir)
        self.assertEqual(p.state, "ready", p.error)
        p.send("replay turn")
        self.assertTrue(wait_for(lambda: lane.turn_ends(p) >= 1))
        end = [e for e in p.events if e["kind"] == "turn_end"][-1]["data"]
        self.assertEqual(end["usage"]["cost"], RESULT["cost"])
        self.assertEqual(end["quota"]["five_hour"]["utilization"], 0.16)
        self.assertEqual(set(self.windows()), {"five_hour", "seven_day"})


class AdapterPatch(unittest.TestCase):

    def test_apply_is_idempotent_and_check_reports(self):
        spike = fake_spike(self)
        self.assertEqual(adapter_patches.check(spike)[0]["status"], "unpatched")
        self.assertTrue(adapter_patches.drops_early_rate_limits(spike))
        self.assertEqual(adapter_patches.apply(spike)[0]["status"], "patched")
        f = spike / "node_modules" / ADAPTER_PKG / "dist" / "acp-agent.js"
        once = f.read_text()
        self.assertEqual(adapter_patches.apply(spike)[0]["status"], "patched")
        self.assertEqual(f.read_text(), once)
        self.assertFalse(adapter_patches.drops_early_rate_limits(spike))
        self.assertEqual(_quiet(adapter_patches.main, ["check", str(spike)]), 0)

    def test_other_versions_and_drift_are_not_written(self):
        spike = fake_spike(self)
        pkg = spike / "node_modules" / ADAPTER_PKG
        (pkg / "package.json").write_text(json.dumps({"version": "9.9.9"}))
        self.assertEqual(adapter_patches.apply(spike)[0]["status"], "other-version")
        self.assertTrue(adapter_patches.drops_early_rate_limits(spike))
        self.assertEqual(_quiet(adapter_patches.main, ["check", str(spike)]), 1)
        (pkg / "package.json").write_text(json.dumps({"version": "0.85.1"}))
        (pkg / "dist" / "acp-agent.js").write_text("// changed upstream\n")
        self.assertEqual(adapter_patches.apply(spike)[0]["status"], "drift")
        self.assertEqual((pkg / "dist" / "acp-agent.js").read_text(), "// changed upstream\n")
        self.assertEqual(adapter_patches.check(Path(tmpdir(self)))[0]["status"], "absent")

    def test_the_patch_matches_the_installed_adapter(self):
        """Against a COPY of the installed adapter, when one is installed."""
        live = adapter_patches.SPIKE / "node_modules" / ADAPTER_PKG
        try:
            version = json.loads((live / "package.json").read_text())["version"]
        except (OSError, ValueError, KeyError):
            self.skipTest("no Claude adapter installed in spike/")
        if version != adapter_patches.PATCHES[0]["version"]:
            self.skipTest(f"installed {version}; the patch is pinned to "
                          f"{adapter_patches.PATCHES[0]['version']}")
        spike = fake_spike(self, source=live / "dist" / "acp-agent.js")
        self.assertIn(adapter_patches.apply(spike)[0]["status"], ("patched",))
        (u,) = adapter_rle(self, spike, INFOS["phase0"], None)
        self.assertEqual(u["_meta"]["_claude/rateLimit"], INFOS["phase0"])

    def test_install_and_lanes_update_apply_it(self):
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('adapter_patches.py" apply', sh)
        lanes = (ROOT / "lanes.py").read_text(encoding="utf-8")
        self.assertIn("adapter_patches.apply(", lanes)


class LoginFacts(FeedCase):

    def fps(self):
        module_feed.reset_for_tests()
        f = module_feed.login_facts(force=True)
        return f["claude"]["fingerprint"], f["codex"]["fingerprint"]

    def test_a_token_refresh_keeps_fingerprints_and_a_new_login_changes_them(self):
        c0, x0 = self.fps()
        self.assertTrue(c0 and x0 and c0 != x0)
        self.write_claude_login("sk-refreshed-token", SENTINEL_ACCOUNT)
        (self.codex / "auth.json").write_text(json.dumps({"tokens": {"access_token": "new"}}))
        self.write_rollout("rollout-b.jsonl", SENTINEL_ACCOUNT)
        self.assertEqual(self.fps(), (c0, x0))
        self.write_claude_login("sk-other", "acct-other-login")
        self.write_rollout("rollout-c.jsonl", "acct-other-codex")
        c1, x1 = self.fps()
        self.assertNotEqual(c1, c0)
        self.assertNotEqual(x1, x0)
        salt = self.state / "module-feed.salt"
        self.assertEqual(stat.S_IMODE(salt.stat().st_mode), 0o600)
        self.assertEqual(len(salt.read_bytes()), 32)

    def test_codex_facts_never_open_auth_json(self):
        import codex_launcher
        (self.codex / "auth.json").chmod(0)
        self.addCleanup((self.codex / "auth.json").chmod, 0o600)
        real_open = Path.open

        def guarded(path, *a, **k):
            if Path(path).name == "auth.json":
                raise AssertionError("auth.json was opened")
            return real_open(path, *a, **k)
        with mock.patch.object(Path, "open", guarded):
            f = codex_launcher.login_facts(lambda a: "h")
        self.assertEqual((f["present"], f["plan"], f["fingerprint"]), (True, "plus", "h"))

    def test_macos_does_not_touch_the_keychain(self):
        import claude_auth
        with mock.patch("subprocess.run", side_effect=AssertionError("security called")):
            f = claude_auth.login_facts(lambda a: "h", platform="darwin")
        self.assertEqual((f["plan"], f["tier"], f["source"]), (None, None, "keychain-unread"))

    def test_no_account_id_means_no_fingerprint(self):
        (self.claude / ".claude.json").write_text("{}")
        self.assertIsNone(self.fps()[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
