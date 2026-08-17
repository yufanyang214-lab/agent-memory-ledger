from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text is not None:
                    parts.append(str(text))
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False))
    return str(value)


class Library(str, Enum):
    PRIMARY = "primary"
    AUXILIARY = "auxiliary"
    IGNORED = "ignored"


class MemoryKind(str, Enum):
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    EVENT = "event"


@dataclass(slots=True)
class SessionMessage:
    role: str
    content: str
    timestamp: str | None = None
    message_id: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any], index: int = 0) -> "SessionMessage":
        role = str(value.get("role") or value.get("type") or "unknown")
        content = _content_to_text(value.get("content", value.get("text", "")))
        message_id = value.get("message_id") or value.get("id") or f"message-{index:04d}"
        timestamp = value.get("timestamp") or value.get("created_at") or value.get("time")
        name = value.get("name") or value.get("agent")
        reserved = {
            "role",
            "type",
            "content",
            "text",
            "message_id",
            "id",
            "timestamp",
            "created_at",
            "time",
            "name",
            "agent",
        }
        metadata = {key: val for key, val in value.items() if key not in reserved}
        return cls(
            role=role,
            content=content,
            timestamp=str(timestamp) if timestamp is not None else None,
            message_id=str(message_id),
            name=str(name) if name is not None else None,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SessionBundle:
    session_id: str
    source: str
    messages: list[SessionMessage]
    session_type: str = "conversation"
    title: str | None = None
    library: Library | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    memory_candidates: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any], fallback_id: str = "session") -> "SessionBundle":
        raw_messages = value.get("messages") or value.get("events") or []
        if not isinstance(raw_messages, list):
            raise ValueError("session 'messages' or 'events' must be a list")
        messages = [
            SessionMessage.from_dict(item, index)
            for index, item in enumerate(raw_messages)
            if isinstance(item, dict)
        ]
        raw_library = value.get("library")
        library = Library(raw_library) if raw_library in {item.value for item in Library} else None
        session_id = str(value.get("session_id") or value.get("id") or fallback_id)
        source = str(value.get("source") or value.get("platform") or "generic")
        candidates = value.get("memory_candidates") or []
        if not isinstance(candidates, list):
            raise ValueError("memory_candidates must be a list")
        reserved = {
            "session_id",
            "id",
            "source",
            "platform",
            "messages",
            "events",
            "session_type",
            "type",
            "title",
            "library",
            "tags",
            "metadata",
            "memory_candidates",
        }
        metadata = dict(value.get("metadata") or {})
        metadata.update({key: val for key, val in value.items() if key not in reserved})
        return cls(
            session_id=session_id,
            source=source,
            messages=messages,
            session_type=str(value.get("session_type") or value.get("type") or "conversation"),
            title=str(value["title"]) if value.get("title") else None,
            library=library,
            tags=[str(tag) for tag in value.get("tags", [])],
            metadata=metadata,
            memory_candidates=[item for item in candidates if isinstance(item, dict)],
        )

    @classmethod
    def from_path(cls, path: str | Path) -> "SessionBundle":
        file_path = Path(path)
        text = file_path.read_text(encoding="utf-8")
        fallback_id = file_path.stem
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            records: list[dict[str, Any]] = []
            for lineno, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at line {lineno}: {exc}") from exc
                if isinstance(item, dict):
                    records.append(item)
            return cls(
                session_id=fallback_id,
                source="generic-jsonl",
                messages=[SessionMessage.from_dict(item, index) for index, item in enumerate(records)],
            )
        if isinstance(parsed, list):
            return cls(
                session_id=fallback_id,
                source="generic-json",
                messages=[
                    SessionMessage.from_dict(item, index)
                    for index, item in enumerate(parsed)
                    if isinstance(item, dict)
                ],
            )
        if not isinstance(parsed, dict):
            raise ValueError("session file must contain a JSON object, JSON array, or JSONL records")
        return cls.from_dict(parsed, fallback_id=fallback_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source": self.source,
            "session_type": self.session_type,
            "title": self.title,
            "library": self.library.value if self.library else None,
            "tags": self.tags,
            "metadata": self.metadata,
            "messages": [message.to_dict() for message in self.messages],
            "memory_candidates": self.memory_candidates,
        }


@dataclass(slots=True)
class MemoryDraft:
    kind: MemoryKind
    title: str
    content: str
    summary: str
    importance: int = 50
    confidence: float = 0.7
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    promote: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryObject:
    object_id: str
    kind: MemoryKind
    title: str
    content: str
    summary: str
    importance: int
    confidence: float
    tags: list[str]
    aliases: list[str]
    source_archive_id: str
    source_session_id: str
    source_platform: str
    status: str = "active"
    promoted: bool = False
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    schema_version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MemoryObject":
        data = dict(value)
        data["kind"] = MemoryKind(data["kind"])
        return cls(**data)


@dataclass(slots=True)
class IndexRecord:
    object_id: str
    title: str
    text: str
    kind: str
    tags: list[str]
    metadata: dict[str, Any]


@dataclass(slots=True)
class SearchHit:
    object_id: str
    score: float
    source: str
    title: str = ""
    summary: str = ""
    kind: str = ""
