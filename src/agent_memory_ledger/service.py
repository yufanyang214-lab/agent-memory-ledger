from __future__ import annotations

from pathlib import Path
from typing import Any

from .extractors import BasicSanitizer, HeuristicExtractor, classify_session
from .models import IndexRecord, Library, MemoryObject, SearchHit, SessionBundle
from .ports import MemoryExtractor, Sanitizer, SemanticIndex
from .store import WorkspaceStore


class MemoryLedger:
    """Portable archive + structured memory + bright/dark ledger service."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        sanitizer: Sanitizer | None = None,
        extractor: MemoryExtractor | None = None,
        semantic_index: SemanticIndex | None = None,
    ):
        self.store = WorkspaceStore(workspace)
        self.sanitizer = sanitizer or BasicSanitizer()
        self.extractor = extractor or HeuristicExtractor()
        self.semantic_index = semantic_index
        self.store.initialize()

    @property
    def workspace(self) -> Path:
        return self.store.root

    def ingest(
        self,
        session: SessionBundle,
        *,
        library: str | Library | None = None,
    ) -> dict[str, Any]:
        clean_session = self.sanitizer.sanitize(session)
        selected_library = self._resolve_library(clean_session, library)
        if selected_library is Library.IGNORED:
            self.store.append_journal(
                "session.ignored",
                {
                    "session_id": clean_session.session_id,
                    "source": clean_session.source,
                    "reason": "empty, noise-only, or explicitly ignored",
                },
            )
            return {
                "status": "ignored",
                "session_id": clean_session.session_id,
                "library": selected_library.value,
                "archive": None,
                "objects": [],
                "index_status": "not_requested",
            }

        archive = self.store.archive_session(clean_session, selected_library.value)
        drafts = self.extractor.extract(clean_session, selected_library.value)
        objects: list[MemoryObject] = []
        for draft in drafts:
            memory_object = self.store.put_object(
                draft,
                archive_id=str(archive["archive_id"]),
                session_id=clean_session.session_id,
                source_platform=clean_session.source,
            )
            objects.append(memory_object)

        index_status, index_error = self._index_objects(objects)
        return {
            "status": "completed",
            "session_id": clean_session.session_id,
            "library": selected_library.value,
            "archive": archive,
            "objects": [memory_object.to_dict() for memory_object in objects],
            "promoted_object_ids": [
                memory_object.object_id for memory_object in objects if memory_object.promoted
            ],
            "index_status": index_status,
            "index_error": index_error,
        }

    def search(self, query: str, *, top_k: int = 10) -> list[dict[str, Any]]:
        limit = max(1, int(top_k))
        ranked: dict[str, dict[str, Any]] = {}
        fts_hits = self.store.search_fts(query, top_k=limit * 2)
        self._merge_ranked(ranked, fts_hits)

        if self.semantic_index is not None:
            try:
                semantic_hits = self.semantic_index.search(query, top_k=limit * 2)
            except Exception as exc:  # adapters must not take down core recall
                self.store.append_journal(
                    "semantic_index.search_failed",
                    {"query": query, "error": f"{type(exc).__name__}: {exc}"},
                )
            else:
                self._merge_ranked(ranked, semantic_hits)

        ordered = sorted(
            ranked.values(),
            key=lambda item: (-float(item["score"]), str(item["object_id"])),
        )[:limit]
        results: list[dict[str, Any]] = []
        for item in ordered:
            memory_object = self.store.get_object(str(item["object_id"]))
            if memory_object is None or memory_object.status != "active":
                continue
            results.append(
                {
                    "object_id": memory_object.object_id,
                    "kind": memory_object.kind.value,
                    "title": memory_object.title,
                    "summary": memory_object.summary,
                    "content": memory_object.content,
                    "importance": memory_object.importance,
                    "confidence": memory_object.confidence,
                    "promoted": memory_object.promoted,
                    "tags": memory_object.tags,
                    "aliases": memory_object.aliases,
                    "source_archive_id": memory_object.source_archive_id,
                    "source_session_id": memory_object.source_session_id,
                    "source_platform": memory_object.source_platform,
                    "score": item["score"],
                    "retrieval_sources": sorted(item["sources"]),
                }
            )
        return results

    def promote(self, object_id: str, *, reason: str = "manual") -> dict[str, Any]:
        memory_object = self.store.promote(object_id, reason=reason)
        if self.semantic_index is not None:
            try:
                self.semantic_index.upsert([self._to_index_record(memory_object)])
            except Exception as exc:
                self.store.append_journal(
                    "semantic_index.promote_sync_failed",
                    {
                        "object_id": object_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        return memory_object.to_dict()

    def retract(self, object_id: str, *, reason: str) -> dict[str, Any]:
        memory_object = self.store.retract(object_id, reason=reason)
        if self.semantic_index is not None:
            try:
                self.semantic_index.delete([object_id])
            except Exception as exc:
                self.store.append_journal(
                    "semantic_index.retract_sync_failed",
                    {
                        "object_id": object_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        return memory_object.to_dict()

    def list_objects(
        self, *, kind: str | None = None, promoted: bool | None = None
    ) -> list[dict[str, Any]]:
        return [
            memory_object.to_dict()
            for memory_object in self.store.list_objects(kind=kind, promoted=promoted)
        ]

    def rebuild_semantic_index(self) -> dict[str, Any]:
        if self.semantic_index is None:
            return {"status": "disabled", "records": 0}
        records = self.store.index_records()
        try:
            self.semantic_index.rebuild(records)
        except Exception as exc:
            self.store.append_journal(
                "semantic_index.rebuild_failed",
                {"error": f"{type(exc).__name__}: {exc}", "records": len(records)},
            )
            return {
                "status": "failed",
                "records": len(records),
                "error": f"{type(exc).__name__}: {exc}",
            }
        self.store.append_journal(
            "semantic_index.rebuilt", {"records": len(records)}
        )
        return {"status": "completed", "records": len(records)}

    def validate(self) -> dict[str, Any]:
        result = self.store.validate()
        if self.semantic_index is None:
            result["semantic_index"] = {"status": "disabled"}
            return result
        try:
            health = self.semantic_index.health()
        except Exception as exc:
            result["semantic_index"] = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
            result["warnings"].append("optional semantic index health check failed")
        else:
            result["semantic_index"] = health
        return result

    @staticmethod
    def _resolve_library(
        session: SessionBundle, library: str | Library | None
    ) -> Library:
        if isinstance(library, Library):
            return library
        if isinstance(library, str) and library != "auto":
            return Library(library)
        return classify_session(session)

    def _index_objects(self, objects: list[MemoryObject]) -> tuple[str, str | None]:
        if self.semantic_index is None:
            return "disabled", None
        active_objects = [item for item in objects if item.status == "active"]
        if not active_objects:
            return "skipped", None
        records = [self._to_index_record(memory_object) for memory_object in active_objects]
        try:
            self.semantic_index.upsert(records)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.store.append_journal(
                "semantic_index.upsert_failed",
                {
                    "object_ids": [memory_object.object_id for memory_object in active_objects],
                    "error": message,
                },
            )
            return "pending", message
        return "completed", None

    @staticmethod
    def _to_index_record(memory_object: MemoryObject) -> IndexRecord:
        return IndexRecord(
            object_id=memory_object.object_id,
            title=memory_object.title,
            text=f"{memory_object.summary}\n\n{memory_object.content}",
            kind=memory_object.kind.value,
            tags=memory_object.tags,
            metadata={
                "importance": memory_object.importance,
                "promoted": memory_object.promoted,
                "source_platform": memory_object.source_platform,
            },
        )

    @staticmethod
    def _merge_ranked(
        ranked: dict[str, dict[str, Any]], hits: list[SearchHit]
    ) -> None:
        for rank, hit in enumerate(hits, 1):
            entry = ranked.setdefault(
                hit.object_id,
                {"object_id": hit.object_id, "score": 0.0, "sources": set()},
            )
            # Reciprocal-rank fusion keeps adapters comparable without assuming
            # their raw score ranges use the same scale.
            entry["score"] += 1.0 / (60.0 + rank)
            entry["sources"].add(hit.source)
