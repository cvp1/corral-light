"""The flow progress tools (progress_mcp.py) and what the hub keeps of a tool
call for the wall's progress strip and agents panel (sessions.tool_event).
The wall's own derivation is pinned by selftest_agents.mjs.

Run: python3 test_progress_mcp.py
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from testkit.scratch import default_state
default_state("corral-light-test-")

import progress_mcp        # noqa: E402
import sessions            # noqa: E402

ROOT = Path(__file__).resolve().parent


def acp_tool(name, raw=None, parent=None, **extra):
    meta = {"claudeCode": {"toolName": name}}
    if parent:
        meta["claudeCode"]["parentToolUseId"] = parent
    d = {"sessionUpdate": "tool_call", "toolCallId": "toolu_1", "title": name,
         "kind": "other", "status": "pending", "_meta": meta}
    if raw is not None:
        d["rawInput"] = raw
    d.update(extra)
    return d


class KeptInput(unittest.TestCase):
    def test_an_agent_call_keeps_its_label_not_its_brief(self):
        kept = progress_mcp.kept_input("Agent", {
            "description": "Wire the strip", "subagent_type": "savvy-careful",
            "model": "opus", "run_in_background": True,
            "prompt": "the whole brief " * 400})
        self.assertEqual(kept, {"description": "Wire the strip",
                                "subagent_type": "savvy-careful",
                                "model": "opus", "background": True})

    def test_any_other_tool_keeps_nothing(self):
        for name in ("Bash", "Edit", "mcp__corral-seats__seat_send", None):
            self.assertIsNone(progress_mcp.kept_input(name, {"command": "rm -rf ~"}))

    def test_progress_is_bounded(self):
        kept = progress_mcp.kept_input(progress_mcp.PROGRESS, {
            "title": "x" * 500, "total": 10 ** 9, "done": -4, "phase": "lunch",
            "finished": "yes",
            "tasks": [{"title": f"t{i}", "tier": "savvy-Heavy", "after": [1, 0, True, 2.5, 3]}
                      for i in range(300)] + [{"title": "  "}, "junk"]})
        self.assertEqual(len(kept["title"]), progress_mcp.MAX_TITLE)
        self.assertEqual(kept["total"], progress_mcp.MAX_TASKS)
        self.assertEqual(kept["done"], 0)
        self.assertNotIn("phase", kept)
        self.assertNotIn("finished", kept)
        self.assertEqual(len(kept["tasks"]), progress_mcp.MAX_TASKS)
        self.assertEqual(kept["tasks"][0], {"title": "t0", "tier": "heavy", "after": [1, 3]})

    def test_step_keeps_counts_and_a_short_note(self):
        kept = progress_mcp.kept_input(progress_mcp.STEP,
                                       {"done": 2, "total": 5, "note": "n" * 300, "x": 1})
        self.assertEqual(set(kept), {"done", "total", "note"})
        self.assertEqual(len(kept["note"]), progress_mcp.MAX_TITLE)


class ToolEvent(unittest.TestCase):
    def test_the_transcript_fields_are_unchanged(self):
        ev = sessions.tool_event(acp_tool("Bash", {"command": "ls"},
                                          content=[{"type": "content"}] * 9,
                                          locations=[{"path": "/"}] * 12))
        self.assertEqual(ev["id"], "toolu_1")
        self.assertEqual(len(ev["content"]), 6)
        self.assertEqual(len(ev["locations"]), 8)
        self.assertEqual(ev["name"], "Bash")
        self.assertNotIn("input", ev)          # a shell command is not kept twice
        self.assertNotIn("parent", ev)

    def test_a_subagent_call_names_its_agent(self):
        ev = sessions.tool_event(acp_tool(progress_mcp.STEP, {"done": 1, "total": 3},
                                          parent="toolu_agent"))
        self.assertEqual(ev["parent"], "toolu_agent")
        self.assertEqual(ev["input"], {"done": 1, "total": 3})

    def test_an_update_without_meta_still_emits(self):
        ev = sessions.tool_event({"toolCallId": "t", "status": "completed"})
        self.assertEqual(ev["status"], "completed")
        self.assertNotIn("name", ev)


class Server(unittest.TestCase):
    def rpc(self, *msgs):
        r = subprocess.run([sys.executable, str(ROOT / "progress_mcp.py")],
                           input="".join(json.dumps(m) + "\n" for m in msgs),
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [json.loads(line) for line in r.stdout.splitlines()]

    def test_lists_the_mod_tool_names_and_answers_calls(self):
        out = self.rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "progress", "arguments": {"done": 2, "total": 5, "phase": "review"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "step", "arguments": {"note": "no count"}}})
        self.assertEqual([m["id"] for m in out], [1, 2, 3, 4])
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "savvy-progress")
        self.assertEqual([t["name"] for t in out[1]["result"]["tools"]], ["progress", "step"])
        self.assertEqual(out[2]["result"]["content"][0]["text"], "ok: 2/5, review")
        self.assertTrue(out[3]["result"]["isError"])


class Offered(unittest.TestCase):
    def pane(self, agent="claude", challenge_of=None):
        p = object.__new__(sessions.Pane)
        p.__dict__.update(agent=agent, challenge_of=challenge_of,
                          mgr=type("M", (), {"mcp": None})())
        return p

    def test_a_claude_pane_is_offered_the_tools(self):
        names = [d["name"] for d in self.pane()._mcp_servers()]
        self.assertIn("savvy-progress", names)

    def test_other_lanes_reviewers_and_the_opt_out_are_not(self):
        self.assertEqual(self.pane("codex")._mcp_servers(), [])
        self.assertEqual(self.pane(challenge_of="abc")._mcp_servers(), [])
        os.environ[progress_mcp.ENV] = "0"
        try:
            self.assertEqual(self.pane()._mcp_servers(), [])
        finally:
            del os.environ[progress_mcp.ENV]


class WallDerivation(unittest.TestCase):
    def test_selftest_agents(self):
        from test_corral_light import _run_node_selftest
        _run_node_selftest(self, "selftest_agents.mjs",
                           "the wall's progress strip and agents panel")


if __name__ == "__main__":
    unittest.main(verbosity=2)
