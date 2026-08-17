from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_memory_ledger import MemoryLedger, SessionBundle
from agent_memory_ledger.models import (
    IndexRecord,
    MemoryDraft,
    MemoryKind,
    SearchHit,
)
from agent_memory_ledger.ports import load_plugin


class CustomExtractor:
    def extract(self, session: SessionBundle, library: str) -> list[MemoryDraft]:
        return [
            MemoryDraft(
                kind=MemoryKind.SEMANTIC,
                title="Plugin-produced memory",
                content="The extractor interface is independent of the storage layer.",
                summary="Extractor and storage are decoupled.",
                importance=88,
                confidence=0.93,
                tags=["plugin"],
            )
        ]


class FakeSemanticIndex:
    def __init__(self) -> None:
        self.records: dict[str, IndexRecord] = {}
        self.deleted: list[str] = []
        self.rebuild_calls = 0
        self.upsert_calls = 0

    def upsert(self, records: list[IndexRecord]) -> None:
        self.upsert_calls += 1
        for record in records:
            self.records[record.object_id] = record

    def delete(self, object_ids: list[str]) -> None:
        self.deleted.extend(object_ids)
        for object_id in object_ids:
            self.records.pop(object_id, None)

    def search(self, query: str, top_k: int = 10) -> list[SearchHit]:
        return [
            SearchHit(object_id=object_id, score=0.99, source="fake-semantic")
            for object_id in list(self.records)[:top_k]
        ]

    def rebuild(self, records: list[IndexRecord]) -> None:
        self.rebuild_calls += 1
        self.records = {record.object_id: record for record in records}

    def health(self) -> dict[str, object]:
        return {"status": "ok", "records": len(self.records)}


class FailingSemanticIndex:
    def upsert(self, records: list[IndexRecord]) -> None:
        raise RuntimeError("simulated adapter outage")

    def delete(self, object_ids: list[str]) -> None:
        raise RuntimeError("simulated adapter outage")

    def search(self, query: str, top_k: int = 10) -> list[SearchHit]:
        raise RuntimeError("simulated adapter outage")

    def rebuild(self, records: list[IndexRecord]) -> None:
        raise RuntimeError("simulated adapter outage")

    def health(self) -> dict[str, object]:
        return {"status": "degraded"}


class PluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name) / "memory"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_plugin_can_load_from_a_plain_python_file(self) -> None:
        plugin_path = Path(self.tempdir.name) / "plain_plugin.py"
        plugin_path.write_text(
            "from pathlib import Path\n"
            "class Plugin:\n"
            "    def __init__(self, workspace: Path):\n"
            "        self.workspace = workspace\n"
            "def create(workspace: Path):\n"
            "    return Plugin(workspace)\n",
            encoding="utf-8",
        )
        loaded = load_plugin(f"{plugin_path}:create", self.workspace.resolve())
        self.assertEqual(loaded.workspace, self.workspace.resolve())

    def test_custom_extractor_and_semantic_index(self) -> None:
        semantic_index = FakeSemanticIndex()
        ledger = MemoryLedger(
            self.workspace,
            extractor=CustomExtractor(),
            semantic_index=semantic_index,
        )
        session = SessionBundle.from_dict(
            {
                "session_id": "plugin-session",
                "source": "custom-agent",
                "messages": [{"role": "user", "content": "A neutral input."}],
            }
        )

        result = ledger.ingest(session)
        self.assertEqual(result["index_status"], "completed")
        self.assertEqual(len(result["objects"]), 1)
        object_id = result["objects"][0]["object_id"]
        self.assertIn(object_id, semantic_index.records)

        hits = ledger.search("words absent from FTS", top_k=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["object_id"], object_id)
        self.assertEqual(hits[0]["retrieval_sources"], ["fake-semantic"])

        ledger.promote(object_id, reason="plugin test")
        self.assertGreaterEqual(semantic_index.upsert_calls, 2)
        ledger.retract(object_id, reason="plugin test complete")
        self.assertIn(object_id, semantic_index.deleted)
        self.assertEqual(ledger.search("anything"), [])

    def test_rebuild_uses_canonical_active_objects(self) -> None:
        semantic_index = FakeSemanticIndex()
        ledger = MemoryLedger(
            self.workspace,
            extractor=CustomExtractor(),
            semantic_index=semantic_index,
        )
        session = SessionBundle.from_dict(
            {
                "session_id": "rebuild-session",
                "source": "custom-agent",
                "messages": [{"role": "user", "content": "Archive this."}],
            }
        )
        result = ledger.ingest(session)
        object_id = result["objects"][0]["object_id"]
        semantic_index.records.clear()

        rebuilt = ledger.rebuild_semantic_index()
        self.assertEqual(rebuilt, {"status": "completed", "records": 1})
        self.assertEqual(semantic_index.rebuild_calls, 1)
        self.assertIn(object_id, semantic_index.records)

    def test_optional_index_failure_never_blocks_archiving_or_fts(self) -> None:
        ledger = MemoryLedger(
            self.workspace,
            extractor=CustomExtractor(),
            semantic_index=FailingSemanticIndex(),
        )
        session = SessionBundle.from_dict(
            {
                "session_id": "outage-session",
                "source": "custom-agent",
                "messages": [{"role": "user", "content": "Archive despite outage."}],
            }
        )

        result = ledger.ingest(session)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["index_status"], "pending")
        self.assertIn("simulated adapter outage", result["index_error"])
        self.assertTrue(result["archive"])

        # Semantic search fails, but the built-in FTS result still returns.
        hits = ledger.search("extractor interface storage", top_k=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["retrieval_sources"], ["fts"])

        validation = ledger.validate()
        self.assertTrue(validation["ok"])
        self.assertEqual(validation["semantic_index"], {"status": "degraded"})

        rebuild = ledger.rebuild_semantic_index()
        self.assertEqual(rebuild["status"], "failed")
        self.assertEqual(rebuild["records"], 1)


if __name__ == "__main__":
    unittest.main()
