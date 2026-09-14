from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_memory_ledger import MemoryLedger, SessionBundle


class MemoryLedgerEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name) / "memory"
        self.ledger = MemoryLedger(self.workspace)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_session_to_evidence_objects_ledgers_and_search(self) -> None:
        fake_value = "sk-" + ("A" * 24)
        session = SessionBundle.from_dict(
            {
                "session_id": "session-001",
                "source": "test-agent",
                "title": "Portable memory design",
                "metadata": {"access_token": fake_value},
                "messages": [
                    {
                        "role": "user",
                        "content": f"Build portable memory. Temporary value: {fake_value}",
                        "metadata": {"password": "example-only"},
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "结论：The filesystem is the canonical source of truth.\n"
                            "流程：Preserve evidence before updating the ledgers.\n"
                            "完成：The design was approved."
                        ),
                    },
                ],
                "memory_candidates": [
                    {
                        "kind": "knowledge",
                        "title": "Canonical storage invariant",
                        "content": (
                            "Filesystem objects are canonical; derived indexes can be rebuilt. "
                            f"Do not persist {fake_value}."
                        ),
                        "summary": "Filesystem objects are canonical.",
                        "importance": 95,
                        "confidence": 0.98,
                        "promote": True,
                        "aliases": ["source of truth"],
                        "tags": ["architecture", "storage"],
                    }
                ],
            }
        )

        result = self.ledger.ingest(session)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["library"], "primary")
        kinds = {item["kind"] for item in result["objects"]}
        self.assertTrue({"knowledge", "procedure", "event"}.issubset(kinds))
        archive_event = next(
            item for item in result["objects"] if "session-archive" in item["tags"]
        )
        self.assertNotIn("Build portable memory", archive_event["content"])
        self.assertNotIn("Last context", archive_event["content"])
        self.assertIn("evidence layer", archive_event["content"])
        self.assertEqual(archive_event["metadata"]["message_count"], 2)

        archive_id = result["archive"]["archive_id"]
        archive_dir = self.workspace / "evidence" / "primary" / archive_id
        self.assertTrue((archive_dir / "manifest.json").is_file())
        self.assertTrue((archive_dir / "transcript.md").is_file())
        persisted_session = (archive_dir / "session.json").read_text(encoding="utf-8")
        self.assertNotIn(fake_value, persisted_session)
        self.assertIn("[REDACTED", persisted_session)

        object_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (self.workspace / "objects").glob("*/*.json")
        )
        self.assertNotIn(fake_value, object_text)

        dark_entries = self._read_jsonl(self.workspace / "ledgers" / "dark.jsonl")
        bright_entries = self._read_jsonl(self.workspace / "ledgers" / "bright.jsonl")
        self.assertEqual(len(dark_entries), len(result["objects"]))
        self.assertTrue(
            any(item["title"] == "Canonical storage invariant" for item in bright_entries)
        )
        self.assertLess(len(bright_entries), len(dark_entries))

        hits = self.ledger.search("filesystem canonical source truth", top_k=5)
        self.assertTrue(hits)
        target_hit = next(
            item for item in hits if item["title"] == "Canonical storage invariant"
        )
        self.assertIn("fts", target_hit["retrieval_sources"])
        self.assertEqual(target_hit["source_session_id"], "session-001")

        validation = self.ledger.validate()
        self.assertTrue(validation["ok"], validation)
        self.assertEqual(validation["archives"], 1)
        self.assertEqual(validation["active_objects"], len(result["objects"]))

        first_created_at = result["archive"]["created_at"]
        second = self.ledger.ingest(session)
        self.assertEqual(second["archive"]["archive_id"], archive_id)
        self.assertEqual(second["archive"]["created_at"], first_created_at)
        second_validation = self.ledger.validate()
        self.assertEqual(second_validation["archives"], 1)
        self.assertEqual(second_validation["objects"], validation["objects"])

    def test_duplicate_object_merges_metadata_and_provenance(self) -> None:
        base_candidate = {
            "kind": "knowledge",
            "title": "Shared invariant",
            "content": "Indexes are derived from canonical memory objects.",
            "summary": "Indexes are derived.",
            "importance": 40,
            "confidence": 0.5,
            "tags": ["first"],
            "aliases": ["derived index"],
        }
        first = SessionBundle.from_dict(
            {
                "session_id": "source-one",
                "source": "agent-a",
                "messages": [{"role": "user", "content": "Discuss memory."}],
                "memory_candidates": [base_candidate],
            }
        )
        first_result = self.ledger.ingest(first)
        object_id = next(
            item["object_id"]
            for item in first_result["objects"]
            if item["title"] == "Shared invariant"
        )

        second_candidate = dict(base_candidate)
        second_candidate.update(
            {
                "importance": 92,
                "confidence": 0.97,
                "tags": ["second"],
                "aliases": ["rebuildable index"],
                "promote": True,
            }
        )
        second = SessionBundle.from_dict(
            {
                "session_id": "source-two",
                "source": "agent-b",
                "messages": [{"role": "user", "content": "Confirm memory."}],
                "memory_candidates": [second_candidate],
            }
        )
        self.ledger.ingest(second)

        merged = self.ledger.store.require_object(object_id)
        self.assertEqual(merged.importance, 92)
        self.assertEqual(merged.confidence, 0.97)
        self.assertEqual(set(merged.tags), {"first", "second"})
        self.assertEqual(
            set(merged.aliases), {"derived index", "rebuildable index"}
        )
        self.assertTrue(merged.promoted)

        with closing(sqlite3.connect(self.ledger.store.db_path)) as con, con:
            count = con.execute(
                "SELECT COUNT(*) FROM object_sources WHERE object_id = ?", (object_id,)
            ).fetchone()[0]
            row = con.execute(
                "SELECT importance, confidence, tags_json FROM objects WHERE object_id = ?",
                (object_id,),
            ).fetchone()
        self.assertEqual(count, 2)
        self.assertEqual(row[0], merged.importance)
        self.assertEqual(row[1], merged.confidence)
        self.assertEqual(set(json.loads(row[2])), set(merged.tags))
        self.assertTrue(self.ledger.validate()["ok"])

    def test_auxiliary_ignored_promotion_and_retraction(self) -> None:
        ignored = SessionBundle.from_dict(
            {
                "session_id": "ping-only",
                "source": "agent",
                "messages": [{"role": "user", "content": "ping"}],
            }
        )
        ignored_result = self.ledger.ingest(ignored)
        self.assertEqual(ignored_result["status"], "ignored")

        auxiliary = SessionBundle.from_dict(
            {
                "session_id": "cron-001",
                "source": "scheduler",
                "session_type": "cron",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Event: nightly validation completed successfully.",
                    }
                ],
                "memory_candidates": [
                    {
                        "kind": "procedure",
                        "title": "Nightly validation",
                        "content": "Run validation before publishing a release.",
                        "importance": 60,
                    }
                ],
            }
        )
        result = self.ledger.ingest(auxiliary)
        self.assertEqual(result["library"], "auxiliary")
        archive_id = result["archive"]["archive_id"]
        self.assertTrue(
            (self.workspace / "evidence" / "auxiliary" / archive_id).is_dir()
        )

        target_id = next(
            item["object_id"]
            for item in result["objects"]
            if item["title"] == "Nightly validation"
        )
        self.assertFalse(self.ledger.store.require_object(target_id).promoted)
        self.ledger.promote(target_id, reason="useful release rule")
        self.assertTrue(self.ledger.store.require_object(target_id).promoted)
        bright_ids = {
            item["object_id"]
            for item in self._read_jsonl(self.workspace / "ledgers" / "bright.jsonl")
        }
        self.assertIn(target_id, bright_ids)

        self.assertTrue(self.ledger.search("publishing release"))
        self.ledger.retract(target_id, reason="superseded example")
        self.assertEqual(self.ledger.store.require_object(target_id).status, "retracted")
        self.assertNotIn(
            target_id,
            {item["object_id"] for item in self.ledger.search("publishing release")},
        )
        bright_ids = {
            item["object_id"]
            for item in self._read_jsonl(self.workspace / "ledgers" / "bright.jsonl")
        }
        self.assertNotIn(target_id, bright_ids)

        self.ledger.ingest(auxiliary)
        still_retracted = self.ledger.store.require_object(target_id)
        self.assertEqual(still_retracted.status, "retracted")
        self.assertFalse(still_retracted.promoted)
        self.assertTrue(self.ledger.validate()["ok"])

    def test_incremental_session_snapshot_reuses_archive_event_object(self) -> None:
        first = SessionBundle.from_dict(
            {
                "session_id": "incremental-thread",
                "source": "codex-cli",
                "title": "Codex thread incremental-thread",
                "messages": [
                    {"role": "user", "content": "Fact: The codename is Incremental-One."}
                ],
            }
        )
        first_result = self.ledger.ingest(first)
        first_event = next(
            item for item in first_result["objects"] if "session-archive" in item["tags"]
        )

        second = SessionBundle.from_dict(
            {
                "session_id": "incremental-thread",
                "source": "codex-cli",
                "title": "Codex thread incremental-thread",
                "messages": [
                    {"role": "user", "content": "Fact: The codename is Incremental-One."},
                    {"role": "assistant", "content": "Rule: Run validation after building."},
                ],
            }
        )
        second_result = self.ledger.ingest(second)
        second_event = next(
            item for item in second_result["objects"] if "session-archive" in item["tags"]
        )
        self.assertNotEqual(
            first_result["archive"]["archive_id"], second_result["archive"]["archive_id"]
        )
        self.assertEqual(first_event["object_id"], second_event["object_id"])
        self.assertEqual(second_event["metadata"]["message_count"], 2)
        validation = self.ledger.validate()
        self.assertEqual(validation["archives"], 2)
        archive_events = [
            item
            for item in self.ledger.list_objects(kind="event")
            if "session-archive" in item["tags"]
        ]
        self.assertEqual(len(archive_events), 1)

    def test_json_array_and_jsonl_inputs(self) -> None:
        array_path = Path(self.tempdir.name) / "array.json"
        array_path.write_text(
            json.dumps(
                [
                    {"role": "user", "content": "Question"},
                    {"role": "assistant", "content": "Answer"},
                ]
            ),
            encoding="utf-8",
        )
        array_bundle = SessionBundle.from_path(array_path)
        self.assertEqual(array_bundle.source, "generic-json")
        self.assertEqual(len(array_bundle.messages), 2)

        jsonl_path = Path(self.tempdir.name) / "messages.jsonl"
        jsonl_path.write_text(
            "\n".join(
                [
                    json.dumps({"role": "user", "content": "One"}),
                    json.dumps({"role": "assistant", "content": "Two"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        jsonl_bundle = SessionBundle.from_path(jsonl_path)
        self.assertEqual(jsonl_bundle.source, "generic-jsonl")
        self.assertEqual(len(jsonl_bundle.messages), 2)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


if __name__ == "__main__":
    unittest.main()
