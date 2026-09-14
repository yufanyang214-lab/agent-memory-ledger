from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


BRIDGE_PATH = (
    Path(__file__).resolve().parents[1]
    / "integrations/codex-skill/scripts/codex_bridge.py"
)
SPEC = importlib.util.spec_from_file_location("aml_codex_bridge", BRIDGE_PATH)
assert SPEC is not None and SPEC.loader is not None
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class CodexBridgeTests(unittest.TestCase):
    def test_conversion_uses_only_visible_event_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout-thread-123.jsonl"
            events = [
                {
                    "timestamp": "2026-08-17T00:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "thread-123",
                        "cli_version": "0.144.1",
                        "cwd": "/tmp/clean",
                        "originator": "codex_exec",
                        "source": "exec",
                        "model_provider": "openai",
                    },
                },
                {
                    "timestamp": "2026-08-17T00:00:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "do not archive"}],
                    },
                },
                {
                    "timestamp": "2026-08-17T00:00:02Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "Fact: The codename is Argent-Codex-9.",
                    },
                },
                {
                    "timestamp": "2026-08-17T00:00:03Z",
                    "type": "response_item",
                    "payload": {"type": "function_call", "name": "shell", "arguments": "private"},
                },
                {
                    "timestamp": "2026-08-17T00:00:04Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": "Procedure: test, build, validate.",
                        "phase": "final_answer",
                    },
                },
            ]
            rollout.write_text(
                "\n".join(json.dumps(item) for item in events) + "\n",
                encoding="utf-8",
            )
            bundle = BRIDGE.convert_rollout(rollout)
            self.assertEqual(bundle["session_id"], "thread-123")
            self.assertEqual(bundle["source"], "codex-cli")
            self.assertEqual(
                [(item["role"], item["content"]) for item in bundle["messages"]],
                [
                    ("user", "Fact: The codename is Argent-Codex-9."),
                    ("assistant", "Procedure: test, build, validate."),
                ],
            )
            serialized = json.dumps(bundle, ensure_ascii=False)
            self.assertNotIn("do not archive", serialized)
            self.assertNotIn("private", serialized)
            self.assertEqual(bundle["metadata"]["cli_version"], "0.144.1")

    def test_locate_rollout_uses_thread_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions/2026/08/17"
            sessions.mkdir(parents=True)
            rollout = sessions / "rollout-without-id-in-name.jsonl"
            rollout.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "thread-hidden-name"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            located = BRIDGE.locate_rollout("thread-hidden-name", tmp)
            self.assertEqual(located, rollout.resolve())


if __name__ == "__main__":
    unittest.main()
