from __future__ import annotations

import errno
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_memory_ledger import MemoryLedger, SessionBundle
from agent_memory_ledger.store import WORKSPACE_SCHEMA_VERSION, WorkspaceStore


class RecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "memory"
        self.ledger = MemoryLedger(self.workspace)
        self.candidates = [
            {"kind": "knowledge", "title": "Durable fact", "content": "Orchid deployment uses blue routing.", "promote": True},
            {"kind": "procedure", "title": "Retired procedure", "content": "Use the retired amber workflow.", "promote": True},
        ]
        for index in range(2):
            self.ledger.ingest(SessionBundle.from_dict({
                "session_id": f"source-{index}", "source": f"agent-{index}",
                "messages": [{"role": "user", "content": "Archive this synthetic session."}],
                "memory_candidates": self.candidates,
            }))
        self.fact_id = self.ledger.list_objects(kind="knowledge")[0]["object_id"]
        self.retired_id = self.ledger.list_objects(kind="procedure")[0]["object_id"]
        self.ledger.retract(self.retired_id, reason="Superseded by the current workflow")

    def files(self, root: Path | None = None) -> dict[str, bytes]:
        directory = root or self.workspace
        return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file() and not p.name.endswith(("-wal", "-shm"))}

    def test_files_only_restore_preserves_state_and_all_sources(self) -> None:
        restored = self.root / "restored"
        shutil.copytree(self.workspace, restored, ignore=shutil.ignore_patterns(
            "catalog.sqlite3*", "bright.jsonl", "dark.jsonl", "journal.jsonl",
        ))
        canonical_before = self.files(restored)
        ledger = MemoryLedger(restored)
        self.assertTrue(ledger.validate()["ok"], ledger.validate())
        self.assertEqual(ledger.search("orchid")[0]["object_id"], self.fact_id)
        self.assertEqual(ledger.search("retired amber"), [])
        self.assertEqual([obj["object_id"] for obj in ledger.list_objects(promoted=True)], [self.fact_id])
        self.assertEqual(ledger.store.require_object(self.retired_id).status, "retracted")
        with ledger.store.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM object_sources WHERE object_id = ?", (self.fact_id,)).fetchone()[0], 2)
        for name, content in canonical_before.items():
            self.assertEqual((restored / name).read_bytes(), content)

    def test_explicit_rebuild_repairs_corrupt_database_and_preserves_backup(self) -> None:
        corrupt = b"synthetic broken sqlite database"
        self.ledger.store.db_path.write_bytes(corrupt)
        repair = MemoryLedger(self.workspace, initialize=False)
        self.assertFalse(repair.validate()["ok"])
        result = repair.reindex()
        self.assertEqual(result["status"], "completed", result)
        backup = self.workspace / result["catalog"]["backup"] / "catalog.sqlite3"
        self.assertEqual(backup.read_bytes(), corrupt)
        self.assertTrue(repair.validate()["ok"], repair.validate())
        self.assertEqual(repair.search("orchid")[0]["object_id"], self.fact_id)

    def test_validate_detects_missing_index_without_repairing_it(self) -> None:
        self.ledger.store.db_path.unlink()
        before = self.files()
        report = MemoryLedger(self.workspace, initialize=False).validate()
        self.assertFalse(report["ok"])
        self.assertGreater(report["canonical_objects"], 0)
        self.assertEqual(before, self.files())

    def test_validate_detects_equal_count_fts_drift(self) -> None:
        with self.ledger.store.connect() as con:
            con.execute("UPDATE objects_fts SET content = 'wrong indexed content' WHERE object_id = ?", (self.fact_id,))
        report = self.ledger.validate()
        self.assertFalse(report["ok"])
        self.assertTrue(any("FTS contents" in error for error in report["errors"]))
        self.assertEqual(self.ledger.reindex()["status"], "completed")
        self.assertTrue(self.ledger.validate()["ok"])

    def test_validate_detects_object_catalog_drift(self) -> None:
        with self.ledger.store.connect() as con:
            con.execute("UPDATE objects SET confidence = 0.1 WHERE object_id = ?", (self.fact_id,))
        report = self.ledger.validate()
        self.assertFalse(report["ok"])
        self.assertTrue(any("file/catalog content mismatch" in error for error in report["errors"]))

    def test_validation_does_not_rewrite_corrupt_ledgers(self) -> None:
        for name in ("bright.jsonl", "dark.jsonl"):
            path = self.workspace / "ledgers" / name
            path.write_text("{bad json}\n", encoding="utf-8")
        before = self.files()
        reopened = MemoryLedger(self.workspace)
        self.assertFalse(reopened.validate()["ok"])
        self.assertEqual(before, self.files())
        self.assertEqual(reopened.reindex()["status"], "completed")
        self.assertTrue(reopened.validate()["ok"])

    def test_evidence_tampering_blocks_rebuild_without_overwriting_state(self) -> None:
        for filename in ("session.json", "transcript.md", "manifest.json"):
            with self.subTest(filename=filename):
                path = next(self.workspace.glob(f"evidence/*/*/{filename}"))
                original = path.read_bytes()
                if filename == "session.json":
                    data = json.loads(original); data["messages"][0]["content"] = "Altered evidence."
                    path.write_text(json.dumps(data), encoding="utf-8")
                elif filename == "manifest.json":
                    data = json.loads(original); data["message_count"] += 1
                    path.write_text(json.dumps(data), encoding="utf-8")
                else:
                    path.write_text("Altered transcript.", encoding="utf-8")
                before = self.files()
                self.assertFalse(self.ledger.validate()["ok"])
                self.assertEqual(self.ledger.reindex()["status"], "failed")
                self.assertEqual(before, self.files())
                path.write_bytes(original)

    def test_object_body_tampering_is_not_accepted_as_rebuild_input(self) -> None:
        path = self.workspace / "objects" / "knowledge" / f"{self.fact_id}.json"
        data = json.loads(path.read_text()); data["content"] = "A different fact under the old ID."
        path.write_text(json.dumps(data), encoding="utf-8")
        before = self.files()
        self.assertFalse(self.ledger.validate()["ok"])
        self.assertEqual(self.ledger.reindex()["status"], "failed")
        self.assertEqual(before, self.files())

    def test_newer_workspace_is_rejected_before_any_mutation(self) -> None:
        path = self.workspace / "workspace.json"
        config = json.loads(path.read_text()); config["schema_version"] = WORKSPACE_SCHEMA_VERSION + 1
        config["memory_kinds"].append("relationship")
        path.write_text(json.dumps(config), encoding="utf-8")
        before = self.files()
        with self.assertRaisesRegex(ValueError, "unsupported workspace schema"):
            MemoryLedger(self.workspace)
        with self.assertRaisesRegex(ValueError, "unsupported workspace schema"):
            self.ledger.reindex()
        self.assertFalse(MemoryLedger(self.workspace, initialize=False).validate()["ok"])
        self.assertEqual(before, self.files())

    def test_cannot_promote_a_retracted_object(self) -> None:
        before = self.files()
        with self.assertRaisesRegex(ValueError, "retracted"):
            self.ledger.promote(self.retired_id)
        self.assertEqual(before, self.files())

    def test_legacy_secondary_sources_recover_from_journal(self) -> None:
        # Reproduce objects written before secondary provenance was file-backed.
        for path in self.workspace.glob("objects/*/*.json"):
            data = json.loads(path.read_text())
            data["metadata"].pop("_aml_source_archive_ids", None)
            path.write_text(json.dumps(data), encoding="utf-8")
        self.ledger.store.db_path.unlink()
        restored = MemoryLedger(self.workspace)
        self.assertTrue(restored.validate()["ok"], restored.validate())
        with restored.store.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM object_sources WHERE object_id = ?", (self.fact_id,)).fetchone()[0], 2)


class WindowsLockTests(unittest.TestCase):
    def test_waits_through_repeated_contention(self) -> None:
        api = SimpleNamespace(LK_NBLCK=2, locking=Mock(side_effect=[OSError(errno.EACCES, "busy")] * 12 + [None]))
        with tempfile.TemporaryFile() as handle, patch("agent_memory_ledger.store.time.sleep") as sleep:
            WorkspaceStore._acquire_windows_lock(handle, api)
        self.assertEqual(api.locking.call_count, 13)
        self.assertEqual(sleep.call_count, 12)

    def test_permanent_lock_errors_are_not_retried(self) -> None:
        api = SimpleNamespace(LK_NBLCK=2, locking=Mock(side_effect=OSError(errno.EBADF, "bad handle")))
        with tempfile.TemporaryFile() as handle, patch("agent_memory_ledger.store.time.sleep") as sleep:
            with self.assertRaises(OSError):
                WorkspaceStore._acquire_windows_lock(handle, api)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
