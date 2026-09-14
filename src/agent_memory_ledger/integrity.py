"""Read canonical files without repairing them or depending on SQLite."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from .models import MEMORY_KIND_ALIASES, MemoryKind, MemoryObject, SessionBundle


SOURCE_IDS_KEY = "_aml_source_archive_ids"


def content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def archive_id(source: str, session_id: str, library: str, digest: str) -> str:
    return sha256(f"{source}\0{session_id}\0{library}\0{digest}".encode("utf-8")).hexdigest()[:20]


def render_transcript(session: SessionBundle, library: str) -> str:
    lines = [
        f"# {session.title or session.session_id}", "",
        f"- Session ID: `{session.session_id}`", f"- Source: `{session.source}`",
        f"- Session type: `{session.session_type}`", f"- Library: `{library}`",
        f"- Messages: `{len(session.messages)}`", "", "## Transcript", "",
    ]
    for index, message in enumerate(session.messages, 1):
        heading = f"### {index}. {message.role}"
        if message.name:
            heading += f" ({message.name})"
        if message.timestamp:
            heading += f" — {message.timestamp}"
        lines.extend([heading, "", message.content.strip() or "_(empty)_", ""])
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class Snapshot:
    archives: dict[str, dict[str, Any]] = field(default_factory=dict)
    objects: dict[str, tuple[MemoryObject, str]] = field(default_factory=dict)
    sources: set[tuple[str, str, str, str]] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    journal_errors: list[str] = field(default_factory=list)


def read_snapshot(
    root: Path, *, allow_legacy: bool = False, allow_damaged_journal: bool = False,
) -> Snapshot:
    """Journal damage is recoverable only when all provenance is file-backed."""
    result = Snapshot()
    for directory in (root / "objects", root / "evidence"):
        if not directory.is_dir():
            result.errors.append(f"missing canonical directory: {directory.name}")
    known_object_ids: set[str] = set()

    def read_json(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("canonical files must remain inside the workspace")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    for directory in sorted((root / "evidence").glob("*/*")):
        relative = directory.relative_to(root).as_posix()
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("expected an evidence directory")
            library = directory.parent.name
            if library not in {"primary", "auxiliary"}:
                raise ValueError("unknown evidence library")
            manifest = read_json(directory / "manifest.json")
            payload = read_json(directory / "session.json")
            session = SessionBundle.from_dict(payload)
            digest = content_hash(payload)
            expected_id = archive_id(session.source, session.session_id, library, digest)
            if manifest.get("content_hash") != digest or directory.name != expected_id:
                raise ValueError("session content hash or archive ID mismatch")
            expected = {
                "archive_id": expected_id, "session_id": session.session_id,
                "source": session.source, "session_type": session.session_type,
                "library": library, "message_count": len(session.messages),
                "files": ["session.json", "transcript.md", "manifest.json"],
            }
            if any(manifest.get(key) != value for key, value in expected.items()):
                raise ValueError("manifest/session metadata mismatch")
            if not isinstance(manifest.get("created_at"), str):
                raise ValueError("missing archive creation time")
            transcript = directory / "transcript.md"
            if transcript.is_symlink() or transcript.read_text(encoding="utf-8") != render_transcript(session, library):
                raise ValueError("transcript/session content mismatch")
            if expected_id in result.archives:
                raise ValueError("duplicate archive ID")
            result.archives[expected_id] = {
                "archive_id": expected_id, "session_id": session.session_id,
                "source": session.source, "library": library, "title": session.title,
                "content_hash": digest, "relative_path": relative,
                "created_at": manifest["created_at"],
            }
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result.errors.append(f"invalid evidence {relative}: {exc}")

    for path in sorted((root / "objects").rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        try:
            payload = read_json(path)
            if len(path.relative_to(root / "objects").parts) != 2:
                raise ValueError("object is outside its kind directory")
            obj = MemoryObject.from_dict(payload)
            if not isinstance(obj.schema_version, int) or not 1 <= obj.schema_version <= 2:
                raise ValueError("unsupported object schema version")
            directory_kind = path.parent.name
            allowed = {obj.kind.value}
            if allow_legacy:
                allowed.update(key for key, value in MEMORY_KIND_ALIASES.items() if value == obj.kind.value)
            if directory_kind not in allowed or payload.get("kind") != directory_kind:
                raise ValueError("object kind/path mismatch")
            if not re.fullmatch(r"mem_[0-9a-f]{20}", obj.object_id) or path.stem != obj.object_id:
                raise ValueError("object ID/filename mismatch")
            if not all(isinstance(value, str) for value in (
                obj.title, obj.summary, obj.content, obj.created_at, obj.updated_at,
                obj.source_archive_id, obj.source_session_id, obj.source_platform,
            )):
                raise ValueError("invalid object text fields")
            id_kinds = {obj.kind.value} | {key for key, value in MEMORY_KIND_ALIASES.items() if value == obj.kind.value}
            valid_ids = {
                "mem_" + sha256(f"{kind}\0{obj.title.strip()}\0{' '.join(obj.content.split())}".encode("utf-8")).hexdigest()[:20]
                for kind in id_kinds
            }
            if obj.object_id not in valid_ids:
                raise ValueError("object content/ID mismatch")
            known_object_ids.update(valid_ids)
            if obj.status not in {"active", "retracted"} or not isinstance(obj.promoted, bool):
                raise ValueError("invalid object recall state")
            if obj.status == "retracted" and obj.promoted:
                raise ValueError("retracted object cannot be promoted")
            if not isinstance(obj.importance, int) or not 0 <= obj.importance <= 100:
                raise ValueError("invalid importance")
            if not isinstance(obj.confidence, (int, float)) or not math.isfinite(obj.confidence) or not 0 <= obj.confidence <= 1:
                raise ValueError("invalid confidence")
            if not all(isinstance(values, list) and all(isinstance(item, str) for item in values) for values in (obj.tags, obj.aliases)):
                raise ValueError("invalid tags or aliases")
            if not isinstance(obj.metadata, dict):
                raise ValueError("invalid object metadata")
            if obj.object_id in result.objects:
                raise ValueError("duplicate object ID")
            result.objects[obj.object_id] = (obj, relative)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result.errors.append(f"invalid object {relative}: {exc}")

    # Older objects kept secondary sources only in SQLite and the append-only
    # journal. Read the latter for compatibility; never rerun an extractor.
    legacy_sources: dict[str, set[str]] = {}
    journal = root / "state" / "journal.jsonl"
    if journal.is_file():
        try:
            if journal.is_symlink():
                raise ValueError("journal must not be a symlink")
            for line_number, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise ValueError(f"invalid journal record at line {line_number}")
                data = entry.get("payload", {})
                if entry.get("event_type") == "memory.upserted" and isinstance(data, dict):
                    object_id, source_id = data.get("object_id"), data.get("archive_id")
                    if isinstance(object_id, str) and isinstance(source_id, str):
                        legacy_sources.setdefault(object_id, set()).add(source_id)
        except (OSError, ValueError) as exc:
            result.journal_errors.append(f"invalid audit journal: {exc}")

    needs_journal = any(SOURCE_IDS_KEY not in obj.metadata for obj, _ in result.objects.values())
    if allow_damaged_journal and not needs_journal:
        result.warnings.extend(
            f"{error}; rebuilding from object provenance and leaving the journal unchanged"
            for error in result.journal_errors
        )
    else:
        result.errors.extend(result.journal_errors)

    for object_id in sorted(set(legacy_sources) - known_object_ids):
        result.errors.append(f"journal references a missing or invalid canonical object: {object_id}")

    for object_id, (obj, _) in result.objects.items():
        if SOURCE_IDS_KEY in obj.metadata:
            ids = obj.metadata[SOURCE_IDS_KEY]
            if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
                result.errors.append(f"invalid source archive list: {object_id}")
                continue
            if obj.source_archive_id not in ids:
                result.errors.append(f"primary source absent from source archive list: {object_id}")
            source_ids = set(ids)
        else:
            source_ids = {obj.source_archive_id} | legacy_sources.get(object_id, set())
            if not journal.is_file():
                result.warnings.append(f"legacy secondary provenance cannot be verified without journal: {object_id}")
        for source_id in sorted(source_ids):
            archive = result.archives.get(source_id)
            if archive is None:
                result.errors.append(f"object {object_id} points to missing or invalid archive {source_id}")
                continue
            if source_id == obj.source_archive_id and (obj.source_session_id, obj.source_platform) != (archive["session_id"], archive["source"]):
                result.errors.append(f"primary source metadata mismatch: {object_id}")
            result.sources.add((object_id, source_id, archive["session_id"], archive["source"]))
    return result


def object_row(obj: MemoryObject, relative_path: str) -> dict[str, Any]:
    return {
        "object_id": obj.object_id, "kind": obj.kind.value, "title": obj.title,
        "summary": obj.summary, "content": obj.content, "importance": obj.importance,
        "confidence": obj.confidence, "tags_json": json.dumps(obj.tags, ensure_ascii=False),
        "aliases_json": json.dumps(obj.aliases, ensure_ascii=False), "status": obj.status,
        "promoted": int(obj.promoted), "relative_path": relative_path,
        "created_at": obj.created_at, "updated_at": obj.updated_at,
        "schema_version": obj.schema_version,
        "metadata_json": json.dumps(obj.metadata, ensure_ascii=False, sort_keys=True),
    }


def fts_row(obj: MemoryObject) -> tuple[str, ...]:
    return (obj.object_id, obj.title, obj.summary, obj.content, " ".join(obj.tags + obj.aliases))


def ledger_entries(snapshot: Snapshot) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dark, bright = [], []
    sources: dict[str, list[str]] = {}
    for object_id, source_id, _, _ in sorted(snapshot.sources):
        sources.setdefault(object_id, []).append(source_id)
    ordered = sorted(snapshot.objects.values(), key=lambda item: (-item[0].importance, item[0].kind.value, item[0].title, item[0].object_id))
    for obj, path in ordered:
        entry = {
            "object_id": obj.object_id, "kind": obj.kind.value, "title": obj.title,
            "summary": obj.summary, "importance": obj.importance, "status": obj.status,
            "promoted": obj.promoted, "tags": obj.tags, "aliases": obj.aliases,
            "canonical_path": path, "source_archive_ids": sources.get(obj.object_id, []),
            "updated_at": obj.updated_at,
        }
        dark.append(entry)
        if obj.status == "active" and obj.promoted:
            bright.append({key: entry[key] for key in (
                "object_id", "kind", "title", "summary", "aliases", "tags",
                "canonical_path", "importance", "updated_at",
            )})
    return dark, bright
