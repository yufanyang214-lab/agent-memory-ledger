from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_memory_ledger import MemoryLedger, SessionBundle
from agent_memory_ledger.models import MemoryKind
from agent_memory_ledger.store import WORKSPACE_SCHEMA_VERSION, WorkspaceStore


class MemoryKindTests(unittest.TestCase):
    def test_legacy_input_aliases_are_normalized_at_the_boundary(self) -> None:
        self.assertIs(MemoryKind("semantic"), MemoryKind.KNOWLEDGE)
        self.assertIs(MemoryKind("procedural"), MemoryKind.PROCEDURE)
        self.assertIs(MemoryKind.SEMANTIC, MemoryKind.KNOWLEDGE)
        self.assertIs(MemoryKind.PROCEDURAL, MemoryKind.PROCEDURE)

        with tempfile.TemporaryDirectory() as tempdir:
            ledger = MemoryLedger(Path(tempdir) / "memory")
            session = SessionBundle.from_dict(
                {
                    "session_id": "legacy-alias-input",
                    "source": "compatibility-test",
                    "messages": [{"role": "user", "content": "Archive aliases."}],
                    "memory_candidates": [
                        {
                            "kind": "semantic",
                            "title": "Legacy fact alias",
                            "content": "Semantic input is stored as AML knowledge.",
                        },
                        {
                            "kind": "procedural",
                            "title": "Legacy workflow alias",
                            "content": "Procedural input is stored as an AML procedure.",
                        },
                    ],
                }
            )
            result = ledger.ingest(session)
            by_title = {item["title"]: item for item in result["objects"]}
            self.assertEqual(by_title["Legacy fact alias"]["kind"], "knowledge")
            self.assertEqual(by_title["Legacy workflow alias"]["kind"], "procedure")
            self.assertEqual(
                [item["kind"] for item in ledger.list_objects(kind="semantic")],
                ["knowledge"],
            )
            self.assertEqual(
                [item["kind"] for item in ledger.list_objects(kind="procedural")],
                ["procedure"],
            )

    def test_initialize_migrates_legacy_paths_without_changing_published_id(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir) / "memory"
            ledger = MemoryLedger(workspace)
            candidate = {
                "kind": "knowledge",
                "title": "Stable legacy identifier",
                "content": "A migrated object keeps its existing public pointer.",
                "summary": "The public pointer remains stable.",
                "importance": 85,
                "promote": True,
            }
            session = SessionBundle.from_dict(
                {
                    "session_id": "legacy-workspace",
                    "source": "compatibility-test",
                    "messages": [{"role": "user", "content": "Archive this."}],
                    "memory_candidates": [candidate],
                }
            )
            result = ledger.ingest(session)
            current = next(
                item
                for item in result["objects"]
                if item["title"] == candidate["title"]
            )
            canonical_id = current["object_id"]
            normalized = " ".join(candidate["content"].split())
            legacy_id = WorkspaceStore._object_id(
                "semantic", candidate["title"], normalized
            )
            self.assertNotEqual(canonical_id, legacy_id)

            canonical_path = workspace / "objects" / "knowledge" / f"{canonical_id}.json"
            payload = json.loads(canonical_path.read_text(encoding="utf-8"))
            payload.update(
                {
                    "object_id": legacy_id,
                    "kind": "semantic",
                    "schema_version": 1,
                }
            )
            legacy_path = workspace / "objects" / "semantic" / f"{legacy_id}.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            canonical_path.unlink()

            with closing(sqlite3.connect(ledger.store.db_path)) as con, con:
                con.execute(
                    "UPDATE objects SET object_id = ?, kind = 'semantic', "
                    "relative_path = ?, schema_version = 1 WHERE object_id = ?",
                    (
                        legacy_id,
                        f"objects/semantic/{legacy_id}.json",
                        canonical_id,
                    ),
                )
                con.execute(
                    "UPDATE object_sources SET object_id = ? WHERE object_id = ?",
                    (legacy_id, canonical_id),
                )
                con.execute("DELETE FROM objects_fts WHERE object_id = ?", (canonical_id,))
                con.execute(
                    "INSERT INTO objects_fts "
                    "(object_id, title, summary, content, tags) VALUES (?, ?, ?, ?, ?)",
                    (
                        legacy_id,
                        payload["title"],
                        payload["summary"],
                        payload["content"],
                        " ".join(payload["tags"] + payload["aliases"]),
                    ),
                )

            config = json.loads((workspace / "workspace.json").read_text(encoding="utf-8"))
            config["schema_version"] = 1
            config.pop("memory_kinds", None)
            (workspace / "workspace.json").write_text(
                json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            migrated = MemoryLedger(workspace)
            migrated_path = workspace / "objects" / "knowledge" / f"{legacy_id}.json"
            self.assertTrue(migrated_path.is_file())
            self.assertFalse(legacy_path.exists())
            memory_object = migrated.store.require_object(legacy_id)
            self.assertIs(memory_object.kind, MemoryKind.KNOWLEDGE)
            self.assertEqual(memory_object.schema_version, 2)

            migrated_config = json.loads(
                (workspace / "workspace.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                migrated_config["schema_version"], WORKSPACE_SCHEMA_VERSION
            )
            self.assertEqual(
                migrated_config["memory_kinds"],
                ["knowledge", "procedure", "event"],
            )
            dark = [
                json.loads(line)
                for line in (workspace / "ledgers" / "dark.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            migrated_entry = next(item for item in dark if item["object_id"] == legacy_id)
            self.assertEqual(migrated_entry["kind"], "knowledge")
            self.assertEqual(
                migrated_entry["canonical_path"],
                f"objects/knowledge/{legacy_id}.json",
            )

            repeated = migrated.ingest(session)
            repeated_id = next(
                item["object_id"]
                for item in repeated["objects"]
                if item["title"] == candidate["title"]
            )
            self.assertEqual(repeated_id, legacy_id)
            self.assertTrue(migrated.validate()["ok"])
            MemoryLedger(workspace)
            journal = [
                json.loads(line)
                for line in (workspace / "state" / "journal.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(
                sum(
                    item["event_type"] == "workspace.memory_kinds_migrated"
                    for item in journal
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
