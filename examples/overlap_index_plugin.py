"""Tiny persistent semantic-index example using only the standard library.

This is not a vector database. It demonstrates the adapter lifecycle and can be
replaced by a Chroma, Qdrant, pgvector, or other implementation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agent_memory_ledger.models import IndexRecord, SearchHit


def _terms(text: str) -> set[str]:
    return {item.lower() for item in re.findall(r"[\w-]+", text, flags=re.UNICODE)}


class OverlapIndex:
    def __init__(self, workspace: Path):
        self.path = workspace / "state" / "example-overlap-index.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def upsert(self, records: list[IndexRecord]) -> None:
        data = self._load()
        for record in records:
            data[record.object_id] = {
                "title": record.title,
                "text": record.text,
                "kind": record.kind,
                "tags": record.tags,
                "metadata": record.metadata,
            }
        self._save(data)

    def delete(self, object_ids: list[str]) -> None:
        data = self._load()
        for object_id in object_ids:
            data.pop(object_id, None)
        self._save(data)

    def search(self, query: str, top_k: int = 10) -> list[SearchHit]:
        query_terms = _terms(query)
        if not query_terms:
            return []
        scored: list[tuple[float, str]] = []
        for object_id, record in self._load().items():
            record_terms = _terms(
                f"{record.get('title', '')} {record.get('text', '')} "
                f"{' '.join(record.get('tags', []))}"
            )
            union = query_terms | record_terms
            score = len(query_terms & record_terms) / len(union) if union else 0.0
            if score > 0:
                scored.append((score, object_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            SearchHit(object_id=object_id, score=score, source="overlap-example")
            for score, object_id in scored[:top_k]
        ]

    def rebuild(self, records: list[IndexRecord]) -> None:
        self._save({})
        self.upsert(records)

    def health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "provider": "overlap-example",
            "records": len(self._load()),
        }

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.path.is_file():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    def _save(self, value: dict[str, dict[str, object]]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def create_index(workspace: Path) -> OverlapIndex:
    return OverlapIndex(workspace)
