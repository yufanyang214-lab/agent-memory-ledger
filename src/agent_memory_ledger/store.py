from __future__ import annotations

import errno
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
import threading
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .extractors import BasicSanitizer
from .integrity import (
    SOURCE_IDS_KEY, fts_row, ledger_entries, object_row, read_snapshot, render_transcript,
)
from .models import (
    IndexRecord,
    MEMORY_KIND_ALIASES,
    MemoryDraft,
    MemoryKind,
    MemoryObject,
    SearchHit,
    SessionBundle,
    utc_now,
)


WORKSPACE_SCHEMA_VERSION = 2


class WorkspaceStore:
    """File-first source of truth with a rebuildable SQLite catalog."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.evidence_dir = self.root / "evidence"
        self.objects_dir = self.root / "objects"
        self.ledgers_dir = self.root / "ledgers"
        self.state_dir = self.root / "state"
        self.db_path = self.state_dir / "catalog.sqlite3"
        self.journal_path = self.state_dir / "journal.jsonl"
        self.lock_path = self.state_dir / "workspace.lock"
        self.config_path = self.root / "workspace.json"
        self._thread_lock = threading.RLock()
        self._lock_depth = 0
        self._lock_handle: BinaryIO | None = None

    def initialize(self) -> None:
        self._check_workspace_version()
        with self.write_lock():
            self._check_workspace_version()
            self._initialize_unlocked()

    def _check_workspace_version(self) -> None:
        if not self.config_path.exists():
            return
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("workspace.json must contain a JSON object")
        version = config.get("schema_version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("invalid workspace schema version")
        if version > WORKSPACE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported workspace schema {version}; maximum is {WORKSPACE_SCHEMA_VERSION}"
            )

    @staticmethod
    def _create_schema(con: sqlite3.Connection) -> None:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS archives (
                archive_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                source TEXT NOT NULL,
                library TEXT NOT NULL,
                title TEXT,
                content_hash TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS objects (
                object_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                content TEXT NOT NULL,
                importance INTEGER NOT NULL,
                confidence REAL NOT NULL,
                tags_json TEXT NOT NULL,
                aliases_json TEXT NOT NULL,
                status TEXT NOT NULL,
                promoted INTEGER NOT NULL DEFAULT 0,
                relative_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS object_sources (
                object_id TEXT NOT NULL,
                archive_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                source_platform TEXT NOT NULL,
                PRIMARY KEY (object_id, archive_id),
                FOREIGN KEY (object_id) REFERENCES objects(object_id),
                FOREIGN KEY (archive_id) REFERENCES archives(archive_id)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS objects_fts USING fts5(
                object_id UNINDEXED,
                title,
                summary,
                content,
                tags
            );
            """
        )


    def _ensure_directories_unlocked(self) -> None:
        for library in ("primary", "auxiliary"):
            (self.evidence_dir / library).mkdir(parents=True, exist_ok=True)
        for kind in MemoryKind:
            (self.objects_dir / kind.value).mkdir(parents=True, exist_ok=True)
        self.ledgers_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _initialize_unlocked(self) -> None:
        self._ensure_directories_unlocked()
        if not self.config_path.exists():
            self._write_json_atomic(
                self.config_path,
                {
                    "schema_version": WORKSPACE_SCHEMA_VERSION,
                    "created_at": utc_now(),
                    "storage": "filesystem+sqlite",
                    "memory_kinds": [kind.value for kind in MemoryKind],
                    "bright_ledger": "ledgers/bright.jsonl",
                    "dark_ledger": "ledgers/dark.jsonl",
                },
            )
        if not self.db_path.exists() and (
            any(self.objects_dir.glob("*/*.json"))
            or any(self.evidence_dir.glob("*/*"))
        ):
            result = self._rebuild_catalog_unlocked()
            if result["status"] != "completed":
                raise RuntimeError("catalog recovery refused: " + "; ".join(result["errors"]))
            return
        new_catalog = not self.db_path.exists()
        with self.connect() as con:
            self._create_schema(con)
        migrated = self._migrate_legacy_kinds_unlocked()
        self._upgrade_workspace_config_unlocked()
        if new_catalog or migrated:
            self.sync_ledgers()

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        """Serialize workspace mutations across threads and agent processes."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            outermost = self._lock_depth == 0
            if outermost:
                handle = self.lock_path.open("a+b")
                try:
                    self._acquire_file_lock(handle)
                except Exception:
                    handle.close()
                    raise
                self._lock_handle = handle
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
                if outermost:
                    handle = self._lock_handle
                    self._lock_handle = None
                    if handle is not None:
                        try:
                            self._release_file_lock(handle)
                        finally:
                            handle.close()

    @staticmethod
    def _acquire_file_lock(handle: BinaryIO) -> None:
        if os.name == "nt":
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            WorkspaceStore._acquire_windows_lock(handle, msvcrt)
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _acquire_windows_lock(handle: BinaryIO, locking_api: Any) -> None:
        # LK_LOCK gives up after about ten seconds. Retry only contention;
        # invalid handles and other permanent errors must still reach callers.
        while True:
            handle.seek(0)
            try:
                locking_api.locking(handle.fileno(), locking_api.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                time.sleep(0.05)

    @staticmethod
    def _release_file_lock(handle: BinaryIO) -> None:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, timeout=30.0)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA journal_mode = WAL")
            with con:
                yield con
        finally:
            con.close()

    def _migrate_legacy_kinds_unlocked(self, *, record_journal: bool = True) -> bool:
        """Move pre-0.1 object files to canonical kind directories without changing IDs."""
        migrated: list[dict[str, str]] = []
        for legacy_kind, canonical_kind in MEMORY_KIND_ALIASES.items():
            legacy_dir = self.objects_dir / legacy_kind
            if not legacy_dir.is_dir():
                continue
            for legacy_path in sorted(legacy_dir.glob("*.json")):
                payload = json.loads(legacy_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise RuntimeError(f"invalid legacy memory object: {legacy_path}")
                object_id = str(payload.get("object_id") or legacy_path.stem)
                if object_id != legacy_path.stem:
                    raise RuntimeError(
                        f"legacy object filename/id mismatch: {legacy_path}"
                    )
                try:
                    payload_kind = MemoryKind(payload.get("kind", legacy_kind)).value
                except ValueError as exc:
                    raise RuntimeError(
                        f"invalid legacy object kind in {legacy_path}: {payload.get('kind')}"
                    ) from exc
                if payload_kind != canonical_kind:
                    raise RuntimeError(
                        f"legacy directory/object kind mismatch: {legacy_path}"
                    )
                payload["kind"] = canonical_kind
                try:
                    object_schema = int(payload.get("schema_version", 1))
                except (TypeError, ValueError):
                    object_schema = 1
                payload["schema_version"] = max(2, object_schema)

                canonical_path = self.objects_dir / canonical_kind / legacy_path.name
                if canonical_path.is_file():
                    existing = json.loads(canonical_path.read_text(encoding="utf-8"))
                    if not isinstance(existing, dict):
                        raise RuntimeError(
                            f"invalid canonical memory object: {canonical_path}"
                        )
                    try:
                        existing["kind"] = MemoryKind(
                            existing.get("kind", canonical_kind)
                        ).value
                    except ValueError as exc:
                        raise RuntimeError(
                            f"invalid canonical object kind in {canonical_path}"
                        ) from exc
                    try:
                        existing_schema = int(existing.get("schema_version", 1))
                    except (TypeError, ValueError):
                        existing_schema = 1
                    existing["schema_version"] = max(2, existing_schema)
                    if existing != payload:
                        raise RuntimeError(
                            "legacy/canonical memory object conflict: "
                            f"{legacy_path.relative_to(self.root)}"
                        )
                else:
                    self._write_json_atomic(canonical_path, payload)

                relative_path = canonical_path.relative_to(self.root).as_posix()
                with self.connect() as con:
                    con.execute(
                        """
                        UPDATE objects
                        SET kind = ?, relative_path = ?, schema_version = ?
                        WHERE object_id = ?
                        """,
                        (
                            canonical_kind,
                            relative_path,
                            payload["schema_version"],
                            object_id,
                        ),
                    )
                legacy_path.unlink()
                migrated.append(
                    {
                        "object_id": object_id,
                        "from": legacy_kind,
                        "to": canonical_kind,
                    }
                )
        if migrated and record_journal:
            self._append_journal_unlocked(
                "workspace.memory_kinds_migrated",
                {"objects": migrated, "schema_version": WORKSPACE_SCHEMA_VERSION},
            )

        return bool(migrated)

    def _upgrade_workspace_config_unlocked(self) -> None:
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise RuntimeError("workspace.json must contain a JSON object")
        changed = False
        try:
            current_schema = int(config.get("schema_version", 1))
        except (TypeError, ValueError):
            current_schema = 1
        if current_schema < WORKSPACE_SCHEMA_VERSION:
            config["schema_version"] = WORKSPACE_SCHEMA_VERSION
            changed = True
        canonical_kinds = [kind.value for kind in MemoryKind]
        if config.get("memory_kinds") != canonical_kinds:
            config["memory_kinds"] = canonical_kinds
            changed = True
        if changed:
            self._write_json_atomic(self.config_path, config)

    def archive_session(self, session: SessionBundle, library: str) -> dict[str, Any]:
        with self.write_lock():
            return self._archive_session_unlocked(session, library)

    def _archive_session_unlocked(
        self, session: SessionBundle, library: str
    ) -> dict[str, Any]:
        self.initialize()
        payload = session.to_dict()
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content_hash = sha256(canonical.encode("utf-8")).hexdigest()
        archive_id = sha256(
            f"{session.source}\0{session.session_id}\0{library}\0{content_hash}".encode(
                "utf-8"
            )
        ).hexdigest()[:20]
        archive_dir = self.evidence_dir / library / archive_id
        relative_path = archive_dir.relative_to(self.root).as_posix()
        manifest_path = archive_dir / "manifest.json"
        archive_dir.mkdir(parents=True, exist_ok=True)
        is_new = not manifest_path.is_file()
        if is_new:
            created_at = utc_now()
            manifest = {
                "archive_id": archive_id,
                "session_id": session.session_id,
                "source": session.source,
                "session_type": session.session_type,
                "library": library,
                "message_count": len(session.messages),
                "content_hash": content_hash,
                "created_at": created_at,
                "files": ["session.json", "transcript.md", "manifest.json"],
            }
            self._write_json_atomic(archive_dir / "session.json", payload)
            self._write_text_atomic(
                archive_dir / "transcript.md",
                self._render_transcript(session, library),
            )
            self._write_json_atomic(manifest_path, manifest)
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("content_hash") != content_hash:
                raise RuntimeError(f"archive id collision: {archive_id}")
            created_at = str(manifest["created_at"])
            if not (archive_dir / "session.json").is_file():
                self._write_json_atomic(archive_dir / "session.json", payload)
            if not (archive_dir / "transcript.md").is_file():
                self._write_text_atomic(
                    archive_dir / "transcript.md",
                    self._render_transcript(session, library),
                )

        with self.connect() as con:
            con.execute(
                """
                INSERT INTO archives (
                    archive_id, session_id, source, library, title,
                    content_hash, relative_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(archive_id) DO NOTHING
                """,
                (
                    archive_id,
                    session.session_id,
                    session.source,
                    library,
                    session.title,
                    content_hash,
                    relative_path,
                    created_at,
                ),
            )
        if is_new:
            self.append_journal(
                "session.archived",
                {
                    "archive_id": archive_id,
                    "session_id": session.session_id,
                    "library": library,
                    "content_hash": content_hash,
                },
            )
        return manifest

    def put_object(
        self,
        draft: MemoryDraft,
        *,
        archive_id: str,
        session_id: str,
        source_platform: str,
    ) -> MemoryObject:
        with self.write_lock():
            return self._put_object_unlocked(
                draft,
                archive_id=archive_id,
                session_id=session_id,
                source_platform=source_platform,
            )

    def _put_object_unlocked(
        self,
        draft: MemoryDraft,
        *,
        archive_id: str,
        session_id: str,
        source_platform: str,
    ) -> MemoryObject:
        self.initialize()
        normalized = " ".join(draft.content.split())
        object_id = self._object_id(
            draft.kind.value, draft.title.strip(), normalized
        )
        existing = self.get_object(object_id)
        if existing is None:
            # A migrated pre-0.1 object keeps its published ID. Re-ingesting the
            # same content through a legacy alias must find that object instead
            # of creating a canonical-ID duplicate.
            for legacy_kind, canonical_kind in MEMORY_KIND_ALIASES.items():
                if canonical_kind != draft.kind.value:
                    continue
                legacy_id = self._object_id(
                    legacy_kind, draft.title.strip(), normalized
                )
                legacy_object = self.get_object(legacy_id)
                if legacy_object is not None:
                    object_id = legacy_id
                    existing = legacy_object
                    break
        now = utc_now()
        relative_path = f"objects/{draft.kind.value}/{object_id}.json"
        path = self.root / relative_path

        status = existing.status if existing else "active"
        promoted = bool(draft.promote or (existing.promoted if existing else False))
        if status != "active":
            promoted = False
        created_at = existing.created_at if existing else now
        source_archive_id = existing.source_archive_id if existing else archive_id
        source_session_id = existing.source_session_id if existing else session_id
        source_source_platform = existing.source_platform if existing else source_platform
        importance = max(
            max(0, min(100, int(draft.importance))),
            existing.importance if existing else 0,
        )
        confidence = max(
            max(0.0, min(1.0, float(draft.confidence))),
            existing.confidence if existing else 0.0,
        )
        tags = sorted(set(draft.tags) | (set(existing.tags) if existing else set()))
        aliases = sorted(
            set(draft.aliases) | (set(existing.aliases) if existing else set())
        )
        metadata = {
            **(existing.metadata if existing else {}),
            **dict(draft.metadata),
        }
        source_ids = {archive_id, source_archive_id}
        if existing:
            source_ids.update(existing.metadata.get(SOURCE_IDS_KEY, []))
            with self.connect() as con:
                source_ids.update(row["archive_id"] for row in con.execute(
                    "SELECT archive_id FROM object_sources WHERE object_id = ?", (object_id,)
                ))
        metadata[SOURCE_IDS_KEY] = sorted(source_ids)
        memory_object = MemoryObject(
            object_id=object_id,
            kind=draft.kind,
            title=draft.title.strip(),
            content=draft.content.strip(),
            summary=draft.summary.strip(),
            importance=importance,
            confidence=confidence,
            tags=tags,
            aliases=aliases,
            source_archive_id=source_archive_id,
            source_session_id=source_session_id,
            source_platform=source_source_platform,
            status=status,
            promoted=promoted,
            created_at=created_at,
            updated_at=now,
            metadata=metadata,
        )
        self._write_json_atomic(path, memory_object.to_dict())

        tags_json = json.dumps(memory_object.tags, ensure_ascii=False)
        aliases_json = json.dumps(memory_object.aliases, ensure_ascii=False)
        metadata_json = json.dumps(memory_object.metadata, ensure_ascii=False, sort_keys=True)
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO objects (
                    object_id, kind, title, summary, content, importance, confidence,
                    tags_json, aliases_json, status, promoted, relative_path,
                    created_at, updated_at, schema_version, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(object_id) DO UPDATE SET
                    kind=excluded.kind,
                    title=excluded.title,
                    summary=excluded.summary,
                    content=excluded.content,
                    importance=MAX(objects.importance, excluded.importance),
                    confidence=MAX(objects.confidence, excluded.confidence),
                    tags_json=excluded.tags_json,
                    aliases_json=excluded.aliases_json,
                    status=excluded.status,
                    promoted=MAX(objects.promoted, excluded.promoted),
                    relative_path=excluded.relative_path,
                    updated_at=excluded.updated_at,
                    schema_version=MAX(objects.schema_version, excluded.schema_version),
                    metadata_json=excluded.metadata_json
                """,
                (
                    object_id,
                    memory_object.kind.value,
                    memory_object.title,
                    memory_object.summary,
                    memory_object.content,
                    memory_object.importance,
                    memory_object.confidence,
                    tags_json,
                    aliases_json,
                    memory_object.status,
                    int(memory_object.promoted),
                    relative_path,
                    memory_object.created_at,
                    memory_object.updated_at,
                    memory_object.schema_version,
                    metadata_json,
                ),
            )
            con.execute(
                """
                INSERT INTO object_sources (
                    object_id, archive_id, session_id, source_platform
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(object_id, archive_id) DO NOTHING
                """,
                (object_id, archive_id, session_id, source_platform),
            )
            con.execute("DELETE FROM objects_fts WHERE object_id = ?", (object_id,))
            if memory_object.status == "active":
                con.execute(
                    "INSERT INTO objects_fts (object_id, title, summary, content, tags) VALUES (?, ?, ?, ?, ?)",
                    (
                        object_id,
                        memory_object.title,
                        memory_object.summary,
                        memory_object.content,
                        " ".join(memory_object.tags + memory_object.aliases),
                    ),
                )
        self.append_journal(
            "memory.upserted",
            {
                "object_id": object_id,
                "kind": memory_object.kind.value,
                "archive_id": archive_id,
                "promoted": memory_object.promoted,
            },
        )
        self.sync_ledgers()
        return memory_object

    @staticmethod
    def _object_id(kind: str, title: str, normalized_content: str) -> str:
        digest = sha256(
            f"{kind}\0{title}\0{normalized_content}".encode("utf-8")
        ).hexdigest()[:20]
        return "mem_" + digest

    def promote(self, object_id: str, reason: str = "manual") -> MemoryObject:
        with self.write_lock():
            return self._promote_unlocked(object_id, reason=reason)

    def _promote_unlocked(
        self, object_id: str, reason: str = "manual"
    ) -> MemoryObject:
        memory_object = self.require_object(object_id)
        if memory_object.status != "active":
            raise ValueError("cannot promote a retracted object")
        memory_object.promoted = True
        memory_object.updated_at = utc_now()
        self._write_json_atomic(
            self.root / f"objects/{memory_object.kind.value}/{object_id}.json",
            memory_object.to_dict(),
        )
        with self.connect() as con:
            con.execute(
                "UPDATE objects SET promoted = 1, updated_at = ? WHERE object_id = ?",
                (memory_object.updated_at, object_id),
            )
        self.append_journal(
            "memory.promoted", {"object_id": object_id, "reason": reason}
        )
        self.sync_ledgers()
        return memory_object

    def retract(self, object_id: str, reason: str) -> MemoryObject:
        with self.write_lock():
            return self._retract_unlocked(object_id, reason=reason)

    def _retract_unlocked(self, object_id: str, reason: str) -> MemoryObject:
        reason = BasicSanitizer._sanitize_text(reason)
        memory_object = self.require_object(object_id)
        memory_object.status = "retracted"
        memory_object.promoted = False
        memory_object.updated_at = utc_now()
        memory_object.metadata = {**memory_object.metadata, "retraction_reason": reason}
        self._write_json_atomic(
            self.root / f"objects/{memory_object.kind.value}/{object_id}.json",
            memory_object.to_dict(),
        )
        with self.connect() as con:
            con.execute(
                """
                UPDATE objects
                SET status = 'retracted', promoted = 0, updated_at = ?, metadata_json = ?
                WHERE object_id = ?
                """,
                (
                    memory_object.updated_at,
                    json.dumps(memory_object.metadata, ensure_ascii=False, sort_keys=True),
                    object_id,
                ),
            )
            con.execute("DELETE FROM objects_fts WHERE object_id = ?", (object_id,))
        self.append_journal(
            "memory.retracted", {"object_id": object_id, "reason": reason}
        )
        self.sync_ledgers()
        return memory_object

    def get_object(self, object_id: str) -> MemoryObject | None:
        with self.write_lock():
            return self._get_object_unlocked(object_id)

    def _get_object_unlocked(self, object_id: str) -> MemoryObject | None:
        if not self.db_path.exists():
            return None
        with self.connect() as con:
            row = con.execute(
                "SELECT relative_path FROM objects WHERE object_id = ?", (object_id,)
            ).fetchone()
        if row is None:
            return None
        path = self.root / str(row["relative_path"])
        if not path.is_file():
            return None
        return MemoryObject.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def require_object(self, object_id: str) -> MemoryObject:
        memory_object = self.get_object(object_id)
        if memory_object is None:
            raise KeyError(f"memory object not found: {object_id}")
        return memory_object

    def list_objects(self, *, kind: str | None = None, promoted: bool | None = None) -> list[MemoryObject]:
        with self.write_lock():
            return self._list_objects_unlocked(kind=kind, promoted=promoted)

    def _list_objects_unlocked(
        self, *, kind: str | None = None, promoted: bool | None = None
    ) -> list[MemoryObject]:
        self.initialize()
        clauses = ["status = 'active'"]
        params: list[Any] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(MemoryKind(kind).value)
        if promoted is not None:
            clauses.append("promoted = ?")
            params.append(int(promoted))
        query = "SELECT object_id FROM objects WHERE " + " AND ".join(clauses)
        query += " ORDER BY importance DESC, updated_at DESC, object_id"
        with self.connect() as con:
            rows = con.execute(query, params).fetchall()
        return [self.require_object(str(row["object_id"])) for row in rows]

    def search_fts(self, query: str, top_k: int = 10) -> list[SearchHit]:
        with self.write_lock():
            return self._search_fts_unlocked(query, top_k=top_k)

    def _search_fts_unlocked(self, query: str, top_k: int = 10) -> list[SearchHit]:
        self.initialize()
        tokens = re.findall(r"[\w-]+", query, flags=re.UNICODE)
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT f.object_id, bm25(objects_fts) AS rank,
                       o.title, o.summary, o.kind
                FROM objects_fts AS f
                JOIN objects AS o ON o.object_id = f.object_id
                WHERE objects_fts MATCH ? AND o.status = 'active'
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, max(1, int(top_k))),
            ).fetchall()
        return [
            SearchHit(
                object_id=str(row["object_id"]),
                score=1.0 / (1.0 + abs(float(row["rank"]))),
                source="fts",
                title=str(row["title"]),
                summary=str(row["summary"]),
                kind=str(row["kind"]),
            )
            for row in rows
        ]

    def index_records(self) -> list[IndexRecord]:
        records: list[IndexRecord] = []
        for memory_object in self.list_objects():
            records.append(
                IndexRecord(
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
            )
        return records

    def sync_ledgers(self) -> None:
        with self.write_lock():
            self._sync_ledgers_unlocked()

    def _sync_ledgers_unlocked(self) -> None:
        if not self.db_path.exists():
            return
        dark_entries: list[dict[str, Any]] = []
        bright_entries: list[dict[str, Any]] = []
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT * FROM objects
                ORDER BY importance DESC, kind, title, object_id
                """
            ).fetchall()
            for row in rows:
                source_rows = con.execute(
                    "SELECT archive_id FROM object_sources WHERE object_id = ? ORDER BY archive_id",
                    (row["object_id"],),
                ).fetchall()
                entry = {
                    "object_id": row["object_id"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "importance": row["importance"],
                    "status": row["status"],
                    "promoted": bool(row["promoted"]),
                    "tags": json.loads(row["tags_json"]),
                    "aliases": json.loads(row["aliases_json"]),
                    "canonical_path": row["relative_path"],
                    "source_archive_ids": [item["archive_id"] for item in source_rows],
                    "updated_at": row["updated_at"],
                }
                dark_entries.append(entry)
                if row["status"] == "active" and row["promoted"]:
                    bright_entries.append(
                        {
                            "object_id": entry["object_id"],
                            "kind": entry["kind"],
                            "title": entry["title"],
                            "summary": entry["summary"],
                            "aliases": entry["aliases"],
                            "tags": entry["tags"],
                            "canonical_path": entry["canonical_path"],
                            "importance": entry["importance"],
                            "updated_at": entry["updated_at"],
                        }
                    )
        self._write_jsonl_atomic(self.ledgers_dir / "dark.jsonl", dark_entries)
        self._write_jsonl_atomic(self.ledgers_dir / "bright.jsonl", bright_entries)

    def rebuild_catalog(self) -> dict[str, Any]:
        self._check_workspace_version()
        if not self.config_path.is_file():
            return {"status": "failed", "errors": ["missing workspace.json; initialize a new workspace first"]}
        with self.write_lock():
            self._check_workspace_version()
            return self._rebuild_catalog_unlocked()

    def _rebuild_catalog_unlocked(self) -> dict[str, Any]:
        snapshot = read_snapshot(self.root, allow_legacy=True, allow_damaged_journal=True)
        if snapshot.errors:
            return {"status": "failed", "errors": snapshot.errors, "warnings": snapshot.warnings}
        # Explicit reindex bypasses initialize, including for empty legacy kinds.
        self._ensure_directories_unlocked()
        descriptor, name = tempfile.mkstemp(prefix=".catalog-rebuild-", suffix=".sqlite3", dir=self.state_dir)
        os.close(descriptor)
        temporary = Path(name)
        backup = None
        try:
            con = sqlite3.connect(temporary)
            try:
                con.execute("PRAGMA foreign_keys = ON")
                self._create_schema(con)
                with con:
                    for record in snapshot.archives.values():
                        columns = ", ".join(record)
                        placeholders = ", ".join("?" for _ in record)
                        con.execute(f"INSERT INTO archives ({columns}) VALUES ({placeholders})", tuple(record.values()))
                    for obj, relative_path in snapshot.objects.values():
                        record = object_row(obj, relative_path)
                        columns = ", ".join(record)
                        placeholders = ", ".join("?" for _ in record)
                        con.execute(f"INSERT INTO objects ({columns}) VALUES ({placeholders})", tuple(record.values()))
                        if obj.status == "active":
                            con.execute("INSERT INTO objects_fts (object_id, title, summary, content, tags) VALUES (?, ?, ?, ?, ?)", fts_row(obj))
                    con.executemany("INSERT INTO object_sources (object_id, archive_id, session_id, source_platform) VALUES (?, ?, ?, ?)", sorted(snapshot.sources))
            finally:
                con.close()
            # All cooperating readers/writers hold workspace.lock. Keep the old
            # database and its WAL together, including when the old DB is corrupt.
            previous = [self.state_dir / ("catalog.sqlite3" + suffix) for suffix in ("", "-wal", "-shm", "-journal")]
            moved: list[tuple[Path, Path]] = []
            try:
                if any(path.exists() for path in previous):
                    backup = self.state_dir / "catalog-backups" / uuid.uuid4().hex
                    backup.mkdir(parents=True)
                    for path in previous:
                        if path.exists():
                            saved = backup / path.name
                            path.replace(saved)
                            moved.append((path, saved))
                temporary.replace(self.db_path)
            except BaseException:
                for original, saved in reversed(moved):
                    saved.replace(original)
                raise
            self._migrate_legacy_kinds_unlocked(record_journal=not snapshot.journal_errors)
            self._upgrade_workspace_config_unlocked()
            self.sync_ledgers()
            # Keep damaged audit evidence byte-for-byte; appending would merge
            # a new event into a partial JSON record. Validation still flags it.
            if not snapshot.journal_errors:
                self.append_journal("catalog.rebuilt", {
                    "archives": len(snapshot.archives), "objects": len(snapshot.objects),
                    "backup": backup.relative_to(self.root).as_posix() if backup else None,
                })
            return {
                "status": "completed", "archives": len(snapshot.archives),
                "objects": len(snapshot.objects), "sources": len(snapshot.sources),
                "warnings": snapshot.warnings,
                "backup": backup.relative_to(self.root).as_posix() if backup else None,
            }
        finally:
            temporary.unlink(missing_ok=True)

    def validate(self) -> dict[str, Any]:
        # Do not initialize or synchronize: validation must expose existing drift.
        if not self.state_dir.is_dir():
            return self._validate_unlocked()
        with self.write_lock():
            return self._validate_unlocked()

    def _validate_unlocked(self) -> dict[str, Any]:
        snapshot = read_snapshot(self.root)
        errors = list(snapshot.errors)
        warnings = list(snapshot.warnings)
        try:
            if not self.config_path.is_file():
                errors.append("missing workspace.json")
            else:
                self._check_workspace_version()
                config = json.loads(self.config_path.read_text(encoding="utf-8"))
                if config.get("memory_kinds") != [kind.value for kind in MemoryKind]:
                    errors.append("workspace memory kinds differ from supported schema")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        for path in [self.evidence_dir / "primary", self.evidence_dir / "auxiliary",
                     *(self.objects_dir / kind.value for kind in MemoryKind), self.ledgers_dir, self.state_dir]:
            if not path.is_dir():
                errors.append(f"missing directory: {path.relative_to(self.root)}")

        dark, bright = ledger_entries(snapshot)
        for name, expected in (("dark.jsonl", dark), ("bright.jsonl", bright)):
            path = self.ledgers_dir / name
            try:
                actual = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if actual != expected:
                    errors.append(f"{name} differs from canonical objects or provenance")
            except (OSError, ValueError) as exc:
                errors.append(f"invalid or missing {name}: {exc}")

        db_objects, db_archives, db_fts = [], [], []
        if not self.db_path.is_file():
            errors.append("missing SQLite catalog; run aml reindex to rebuild from canonical files")
        else:
            try:
                con = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
                try:
                    if [row[0] for row in con.execute("PRAGMA quick_check")] != ["ok"]:
                        errors.append("SQLite integrity check failed")
                    db_objects = [dict(row) for row in con.execute("SELECT * FROM objects")]
                    db_archives = [dict(row) for row in con.execute("SELECT * FROM archives")]
                    db_sources = {tuple(row) for row in con.execute("SELECT object_id, archive_id, session_id, source_platform FROM object_sources")}
                    db_fts = [tuple(row) for row in con.execute("SELECT object_id, title, summary, content, tags FROM objects_fts")]
                finally:
                    con.close()
                expected_objects = {key: object_row(obj, path) for key, (obj, path) in snapshot.objects.items()}
                actual_objects = {row["object_id"]: row for row in db_objects}
                for object_id in sorted(set(expected_objects) | set(actual_objects)):
                    expected, actual = expected_objects.get(object_id), actual_objects.get(object_id)
                    if expected is None or actual is None:
                        errors.append(f"object file/catalog membership mismatch: {object_id}")
                        continue
                    for key in ("tags_json", "aliases_json", "metadata_json"):
                        expected[key] = json.loads(expected[key])
                        actual[key] = json.loads(actual[key])
                    if expected != actual:
                        errors.append(f"object file/catalog content mismatch: {object_id}")
                if {row["archive_id"]: row for row in db_archives} != snapshot.archives:
                    errors.append("archive file/catalog mismatch")
                if db_sources != snapshot.sources:
                    errors.append("source provenance file/catalog mismatch")
                expected_fts = [fts_row(obj) for obj, _ in snapshot.objects.values() if obj.status == "active"]
                if sorted(db_fts) != sorted(expected_fts):
                    errors.append("FTS contents differ from active canonical objects")
            except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"invalid SQLite catalog: {exc}")
        return {
            "ok": not errors, "root": str(self.root),
            "archives": len(db_archives), "objects": len(db_objects),
            "active_objects": sum(obj.status == "active" for obj, _ in snapshot.objects.values()),
            "canonical_archives": len(snapshot.archives), "canonical_objects": len(snapshot.objects),
            "fts_rows": len(db_fts), "errors": errors, "warnings": warnings,
        }

    def append_journal(self, event_type: str, payload: dict[str, Any]) -> None:
        with self.write_lock():
            self._append_journal_unlocked(event_type, payload)

    def _append_journal_unlocked(
        self, event_type: str, payload: dict[str, Any]
    ) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "event_type": event_type,
            "timestamp": utc_now(),
            "payload": BasicSanitizer()._sanitize_mapping(payload),
        }
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    _render_transcript = staticmethod(render_transcript)

    @staticmethod
    def _write_json_atomic(path: Path, value: Any) -> None:
        WorkspaceStore._write_text_atomic(
            path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    @staticmethod
    def _write_jsonl_atomic(path: Path, entries: Iterable[dict[str, Any]]) -> None:
        content = "".join(
            json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
            for entry in entries
        )
        WorkspaceStore._write_text_atomic(path, content)

    @staticmethod
    def _write_text_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
