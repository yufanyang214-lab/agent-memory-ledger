from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_memory_ledger import MemoryLedger, SessionBundle
from agent_memory_ledger.models import IndexRecord, MemoryDraft, MemoryKind, SearchHit


class ScopedExtractor:
    def extract(self, session: SessionBundle, library: str) -> list[MemoryDraft]:
        return [
            MemoryDraft(
                kind=MemoryKind.SEMANTIC,
                title="Dark deployment note",
                content="The deployment codename is shadow-only.",
                summary="An unpromoted deployment note.",
                importance=50,
            ),
            MemoryDraft(
                kind=MemoryKind.SEMANTIC,
                title="Bright deployment rule",
                content="The deployment codename is bluebird.",
                summary="A promoted deployment rule.",
                importance=90,
                promote=True,
            ),
        ]


class FilteredIndex:
    def __init__(self) -> None:
        self.records: dict[str, IndexRecord] = {}
        self.filters: list[dict[str, object]] = []

    def upsert(self, records: list[IndexRecord]) -> None:
        self.records.update({record.object_id: record for record in records})

    def delete(self, object_ids: list[str]) -> None:
        for object_id in object_ids:
            self.records.pop(object_id, None)

    def search(self, query: str, top_k: int = 10) -> list[SearchHit]:
        return self._hits(list(self.records.values()), top_k)

    def search_filtered(
        self,
        query: str,
        top_k: int = 10,
        *,
        metadata: dict[str, object],
    ) -> list[SearchHit]:
        self.filters.append(metadata)
        records = [
            record
            for record in self.records.values()
            if all(record.metadata.get(key) == value for key, value in metadata.items())
        ]
        return self._hits(records, top_k)

    def rebuild(self, records: list[IndexRecord]) -> None:
        self.records = {record.object_id: record for record in records}

    def health(self) -> dict[str, object]:
        return {"status": "ok"}

    @staticmethod
    def _hits(records: list[IndexRecord], top_k: int) -> list[SearchHit]:
        return [
            SearchHit(record.object_id, 1.0, "filtered-test")
            for record in records[:top_k]
        ]


class LegacyIndex(FilteredIndex):
    search_filtered = None  # type: ignore[assignment]


class SearchScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name) / "memory"
        self.session = SessionBundle.from_dict(
            {
                "session_id": "scoped-session",
                "source": "scope-test",
                "messages": [{"role": "user", "content": "Store both notes."}],
            }
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_filtered_adapter_applies_bright_scope_before_ranking(self) -> None:
        index = FilteredIndex()
        ledger = MemoryLedger(
            self.workspace,
            extractor=ScopedExtractor(),
            semantic_index=index,
        )
        ledger.ingest(self.session)

        active = ledger.search("words absent from fts", scope="active", top_k=10)
        bright = ledger.search("words absent from fts", scope="bright", top_k=10)

        self.assertEqual(len(active), 2)
        self.assertEqual([item["title"] for item in bright], ["Bright deployment rule"])
        self.assertEqual(index.filters, [{"promoted": True}])

    def test_legacy_adapter_remains_compatible_with_bright_scope(self) -> None:
        index = LegacyIndex()
        ledger = MemoryLedger(
            self.workspace,
            extractor=ScopedExtractor(),
            semantic_index=index,
        )
        ledger.ingest(self.session)

        bright = ledger.search("words absent from fts", scope="bright", top_k=10)

        self.assertEqual([item["title"] for item in bright], ["Bright deployment rule"])

    def test_fts_can_filter_to_bright_objects(self) -> None:
        ledger = MemoryLedger(self.workspace, extractor=ScopedExtractor())
        ledger.ingest(self.session)

        bright = ledger.search("deployment codename", scope="bright", top_k=10)

        self.assertEqual([item["title"] for item in bright], ["Bright deployment rule"])

    def test_invalid_scope_is_rejected(self) -> None:
        ledger = MemoryLedger(self.workspace)
        with self.assertRaisesRegex(ValueError, "search scope"):
            ledger.search("anything", scope="retracted")


if __name__ == "__main__":
    unittest.main()
