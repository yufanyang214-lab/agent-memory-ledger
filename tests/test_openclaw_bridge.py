from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


BRIDGE_PATH = (
    Path(__file__).resolve().parents[1]
    / "integrations/openclaw-skill/scripts/openclaw_bridge.py"
)
SPEC = importlib.util.spec_from_file_location("aml_openclaw_bridge", BRIDGE_PATH)
assert SPEC is not None and SPEC.loader is not None
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class OpenClawBridgeTests(unittest.TestCase):
    def test_convert_export_keeps_only_visible_user_and_assistant_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(
                json.dumps({"sessionId": "session-123", "schemaVersion": 1}),
                encoding="utf-8",
            )
            events = [
                {
                    "seq": 1,
                    "source": "runtime",
                    "type": "prompt.submitted",
                    "data": {"prompt": "must not be archived"},
                },
                {
                    "seq": 2,
                    "source": "transcript",
                    "type": "user.message",
                    "entryId": "u1",
                    "ts": "2026-08-17T00:00:00Z",
                    "data": {"message": {"role": "user", "content": "Fact: Codename is Nacre."}},
                },
                {
                    "seq": 3,
                    "source": "transcript",
                    "type": "assistant.message",
                    "entryId": "a1",
                    "ts": "2026-08-17T00:00:01Z",
                    "data": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "hidden"},
                                {"type": "toolCall", "name": "bash", "arguments": {}},
                            ],
                        }
                    },
                },
                {
                    "seq": 4,
                    "source": "transcript",
                    "type": "tool.result",
                    "entryId": "t1",
                    "data": {"message": {"role": "toolResult", "content": "private output"}},
                },
                {
                    "seq": 5,
                    "source": "transcript",
                    "type": "assistant.message",
                    "entryId": "a2",
                    "ts": "2026-08-17T00:00:02Z",
                    "data": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "hidden"},
                                {"type": "text", "text": "Procedure: Test, build, validate."},
                            ],
                        }
                    },
                },
            ]
            (root / "events.jsonl").write_text(
                "\n".join(json.dumps(item) for item in events) + "\n",
                encoding="utf-8",
            )
            bundle = BRIDGE.convert_export(
                root,
                agent="clean-agent",
                session_key="agent:clean-agent:subagent:write",
                candidates=[
                    {
                        "kind": "semantic",
                        "title": "Explicit candidate",
                        "content": "Candidate survives conversion.",
                    }
                ],
            )
            self.assertEqual(bundle["session_id"], "session-123")
            self.assertEqual(bundle["session_type"], "subagent")
            self.assertEqual(
                [(item["role"], item["content"]) for item in bundle["messages"]],
                [
                    ("user", "Fact: Codename is Nacre."),
                    ("assistant", "Procedure: Test, build, validate."),
                ],
            )
            serialized = json.dumps(bundle, ensure_ascii=False)
            self.assertNotIn("must not be archived", serialized)
            self.assertNotIn("private output", serialized)
            self.assertNotIn("hidden", serialized)
            self.assertEqual(len(bundle["memory_candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
