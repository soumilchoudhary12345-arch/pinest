"""Secrets must never reach the agent monitor. Run: python3 -m unittest pihub/client/test_report_event.py"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report_event  # noqa: E402
from report_event import redact, summarize  # noqa: E402


class Redact(unittest.TestCase):
    def test_header_token(self):
        out = redact('curl -H "X-Token: FAKE-token-for-tests-0000000000" http://pi:8899/api')
        self.assertNotIn("FAKE-token", out)
        self.assertIn("http://pi:8899/api", out)

    def test_key_value_and_bearer(self):
        for s, secret in (("export API_KEY=abc123", "abc123"), ("password=hunter2 login", "hunter2"),
                          ("Authorization: Bearer eyJhbGciOi", "eyJhbGciOi")):
            self.assertNotIn(secret, redact(s), s)

    def test_long_random_strings(self):
        self.assertNotIn("ghp_0123456789abcdefABCDEF0123", redact("echo ghp_0123456789abcdefABCDEF0123"))

    def test_ordinary_commands_survive(self):
        for s in ("python3 -m unittest pihub/test_app.py", "ls -la ~/Documents", "npm run build"):
            self.assertEqual(redact(s), s)

    def test_summarize_uses_redaction(self):
        self.assertNotIn("hunter2", summarize("Bash", {"command": "mysql --password=hunter2"}))



def posted(argv, payload):
    """Run report_event.main() with a payload and return the body it would POST to PiHub."""
    sent = []

    class Resp:
        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return Resp()

    cfg = {"device": "soumil_linux", "hub_url": "http://pi:8899", "token": "t"}
    with mock.patch.object(sys, "argv", ["report_event.py"] + argv), \
         mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
         mock.patch("urllib.request.urlopen", fake_urlopen), \
         mock.patch("pihub_client.load_config", return_value=cfg):
        report_event.main()
    return sent[0] if sent else None


class EveryAgent(unittest.TestCase):
    """One real-shaped payload per agent must come out in PiHub's (Claude Code's) terms."""

    def check(self, argv, payload, event, tool, summary, agent):
        b = posted(argv, payload)
        self.assertEqual((b["event"], b["tool"], b["summary"], b["agent"]), (event, tool, summary, agent))
        self.assertTrue(b["session"])

    def test_claude_code(self):
        self.check(["--agent", "claude-code"], {"hook_event_name": "PreToolUse", "session_id": "s1", "cwd": "/w/pihub",
                   "tool_name": "Edit", "tool_input": {"file_path": "/w/pihub/app.py"}}, "PreToolUse", "Edit", "app.py", "claude-code")

    def test_codex_apply_patch(self):
        patch = "*** Begin Patch\n*** Update File: src/main.rs\n@@\n-a\n+b\n*** End Patch"
        self.check(["--agent", "codex"], {"hook_event_name": "PreToolUse", "session_id": "s2", "cwd": "/w",
                   "tool_name": "apply_patch", "tool_input": {"input": patch}}, "PreToolUse", "Edit", "main.rs", "codex")

    def test_copilot_camel_case_and_string_args(self):
        self.check(["--agent", "copilot", "--event", "preToolUse"], {"sessionId": "s3", "cwd": "/w", "toolName": "bash",
                   "toolArgs": json.dumps({"command": "npm test", "description": "Run the tests"})},
                   "PreToolUse", "Bash", "Run the tests", "copilot")

    def test_copilot_agent_stop(self):
        b = posted(["--agent", "copilot", "--event", "agentStop"], {"sessionId": "s3", "cwd": "/w"})
        self.assertEqual(b["event"], "Stop")

    def test_crush(self):
        self.check(["--agent", "crush"], {"event": "PreToolUse", "hook_event_name": "PreToolUse", "session_id": "s4",
                   "cwd": "/w", "tool_name": "view", "tool_input": {"file_path": "/w/README.md"}}, "PreToolUse", "Read", "README.md", "crush")

    def test_opencode_plugin_payload(self):
        self.check(["--agent", "opencode"], {"hook_event_name": "PreToolUse", "session_id": "ses_5", "cwd": "/w",
                   "tool_name": "read", "tool_input": {"filePath": "/w/package.json"}}, "PreToolUse", "Read", "package.json", "opencode")

    def test_gemini_before_tool_and_after_agent(self):
        self.check(["--agent", "gemini"], {"hook_event_name": "BeforeTool", "session_id": "s6", "cwd": "/w",
                   "tool_name": "run_shell_command", "tool_input": {"command": "ls", "description": "List the files"}},
                   "PreToolUse", "Bash", "List the files", "gemini")
        self.assertEqual(posted(["--agent", "gemini"], {"hook_event_name": "AfterAgent", "session_id": "s6"})["event"], "Stop")

    def test_permission_request_reads_as_waiting(self):
        b = posted(["--agent", "codex"], {"hook_event_name": "PermissionRequest", "session_id": "s7", "tool_name": "Bash"})
        self.assertEqual((b["event"], b["summary"]), ("Notification", "Asking permission to use Bash"))

    def test_gemini_family_without_agy_parent_is_gemini(self):
        self.assertEqual(report_event.gemini_or_antigravity(), "gemini")


if __name__ == "__main__":
    unittest.main()
