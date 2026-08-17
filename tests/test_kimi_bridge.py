from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


BRIDGE_PATH = (
    Path(__file__).resolve().parents[1]
    / "integrations/kimi-plugin/scripts/kimi_bridge.py"
)
SPEC = importlib.util.spec_from_file_location("aml_kimi_bridge", BRIDGE_PATH)
assert SPEC is not None and SPEC.loader is not None
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class KimiBridgeTests(unittest.TestCase):
    def _context_records(self) -> list[dict[str, object]]:
        return [
            {"role": "_system_prompt", "content": "private system prompt"},
            {"role": "_checkpoint", "id": "cp-1"},
            {
                "role": "user",
                "content": "<system-reminder>synthetic reminder</system-reminder>",
            },
            {"role": "user", "content": "Fact: The codename is Moon-Kimi-7."},
            {
                "role": "assistant",
                "content": [
                    {"type": "think", "think": "hidden reasoning", "encrypted": "cipher"},
                    {"type": "text", "text": "Procedure: test, build, validate."},
                ],
                "tool_calls": [{"name": "shell", "arguments": "private arguments"}],
            },
            {
                "role": "tool",
                "content": [{"type": "text", "text": "private tool output"}],
                "tool_call_id": "call-1",
            },
            {"role": "_usage", "token_count": 1234},
        ]

    def test_context_conversion_keeps_only_visible_user_assistant_text(self) -> None:
        text = "\n".join(json.dumps(item) for item in self._context_records()) + "\n"
        bundle = BRIDGE.context_to_bundle(text, session_id="kimi-session-1")
        self.assertEqual(bundle["session_id"], "kimi-session-1")
        self.assertEqual(bundle["source"], "kimi-code-cli")
        self.assertEqual(
            [(item["role"], item["content"]) for item in bundle["messages"]],
            [
                ("user", "Fact: The codename is Moon-Kimi-7."),
                ("assistant", "Procedure: test, build, validate."),
            ],
        )
        serialized = json.dumps(bundle, ensure_ascii=False)
        for forbidden in (
            "private system prompt",
            "synthetic reminder",
            "hidden reasoning",
            "cipher",
            "private arguments",
            "private tool output",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_export_zip_conversion_uses_manifest_and_context_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "kimi-session.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "session_id": "kimi-export-123",
                            "kimi_cli_version": "1.31.0",
                            "exported_at": "2026-08-17T00:00:00Z",
                        }
                    ),
                )
                archive.writestr(
                    "context.jsonl",
                    "\n".join(json.dumps(item) for item in self._context_records()) + "\n",
                )
                archive.writestr("wire.jsonl", json.dumps({"private": "wire data"}))
                archive.writestr("logs/kimi.log", "private logs")
            bundle = BRIDGE.convert_export_zip(archive_path)
            self.assertEqual(bundle["session_id"], "kimi-export-123")
            self.assertEqual(bundle["metadata"]["kimi_cli_version"], "1.31.0")
            serialized = json.dumps(bundle, ensure_ascii=False)
            self.assertNotIn("wire data", serialized)
            self.assertNotIn("private logs", serialized)

    def test_locate_latest_context_by_workdir_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workdir = root / "workspace"
            share = root / "kimi-share"
            workdir.mkdir()
            digest = hashlib.md5(
                str(workdir.resolve()).encode("utf-8"), usedforsecurity=False
            ).hexdigest()
            older = share / "sessions" / digest / "older" / "context.jsonl"
            newer = share / "sessions" / digest / "newer" / "context.jsonl"
            older.parent.mkdir(parents=True)
            newer.parent.mkdir(parents=True)
            older.write_text(json.dumps({"role": "user", "content": "old"}) + "\n")
            newer.write_text(json.dumps({"role": "user", "content": "new"}) + "\n")
            older.touch()
            newer.touch()
            # Force deterministic ordering independent of filesystem timestamp resolution.
            import os

            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            self.assertEqual(
                BRIDGE.locate_context_file(workdir, share_path=str(share)),
                newer.resolve(),
            )
            self.assertEqual(
                BRIDGE.locate_context_file(
                    workdir, session_id="older", share_path=str(share)
                ),
                older.resolve(),
            )


if __name__ == "__main__":
    unittest.main()
