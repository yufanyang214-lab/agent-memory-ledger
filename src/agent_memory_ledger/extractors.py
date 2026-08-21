from __future__ import annotations

import copy
import re
from hashlib import sha256

from .models import Library, MemoryDraft, MemoryKind, SessionBundle


_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.S,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/=-]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{12,}|AKIA[A-Z0-9]{16})\b"
        ),
        "[REDACTED_TOKEN]",
    ),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|"
            r"passwd|secret)\s*[:=]\s*)[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r'(?i)("(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|'
            r'secret)"\s*:\s*")[^"]+("?)'
        ),
        r"\1[REDACTED]\2",
    ),
)


class BasicSanitizer:
    """Conservative secret redaction with no external dependencies."""

    def sanitize(self, session: SessionBundle) -> SessionBundle:
        clean = copy.deepcopy(session)
        for message in clean.messages:
            message.content = self._sanitize_text(message.content)
            message.metadata = self._sanitize_mapping(message.metadata)
            if message.name:
                message.name = self._sanitize_text(message.name)
        if clean.title:
            clean.title = self._sanitize_text(clean.title)
        clean.tags = [self._sanitize_text(tag) for tag in clean.tags]
        clean.metadata = self._sanitize_mapping(clean.metadata)
        clean.memory_candidates = self._sanitize_mapping(clean.memory_candidates)
        return clean

    @staticmethod
    def _sanitize_text(value: str) -> str:
        text = value
        for pattern, replacement in _SECRET_PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def _sanitize_mapping(self, value: object) -> object:
        if isinstance(value, dict):
            result: dict[str, object] = {}
            for key, item in value.items():
                if re.search(r"(?i)(password|secret|token|api[_-]?key)", str(key)):
                    result[str(key)] = "[REDACTED]"
                else:
                    result[str(key)] = self._sanitize_mapping(item)
            return result
        if isinstance(value, list):
            return [self._sanitize_mapping(item) for item in value]
        if isinstance(value, str):
            return self._sanitize_text(value)
        return value


_NOISE = {"ping", "test", "testing", "hello", "hi", "测试", "测试一下", "在吗"}
_AUXILIARY_TYPES = {
    "cron",
    "heartbeat",
    "executor",
    "validator",
    "tool",
    "subagent",
    "system",
    "background",
}


def classify_session(session: SessionBundle) -> Library:
    if session.library is not None:
        return session.library
    meaningful = [message.content.strip() for message in session.messages if message.content.strip()]
    if not meaningful:
        return Library.IGNORED
    if len(meaningful) <= 2 and all(text.lower() in _NOISE for text in meaningful):
        return Library.IGNORED
    if session.session_type.lower() in _AUXILIARY_TYPES:
        return Library.AUXILIARY
    non_human = sum(
        message.role.lower() in {"tool", "toolresult", "system"}
        for message in session.messages
    )
    if session.messages and non_human / len(session.messages) >= 0.6:
        return Library.AUXILIARY
    return Library.PRIMARY


_MARKERS: tuple[tuple[re.Pattern[str], MemoryKind], ...] = (
    (
        re.compile(
            r"^(?:knowledge|semantic|fact|decision|conclusion|constraint|preference|"
            r"知识|事实|决定|结论|约束|偏好)\s*[:：-]\s*(.+)$",
            re.I,
        ),
        MemoryKind.KNOWLEDGE,
    ),
    (
        re.compile(
            r"^(?:procedure|procedural|workflow|rule|step|步骤|流程|规则|做法)"
            r"\s*[:：-]\s*(.+)$",
            re.I,
        ),
        MemoryKind.PROCEDURE,
    ),
    (
        re.compile(
            r"^(?:event|result|completed|milestone|incident|state[ -]?change|"
            r"事件|结果|完成|里程碑|事故|状态变化|状态变更)\s*[:：-]\s*(.+)$",
            re.I,
        ),
        MemoryKind.EVENT,
    ),
)


class HeuristicExtractor:
    """Small default extractor; production users can replace it with an LLM plugin."""

    def extract(self, session: SessionBundle, library: str) -> list[MemoryDraft]:
        drafts: list[MemoryDraft] = []
        for candidate in session.memory_candidates:
            draft = self._candidate_to_draft(candidate)
            if draft is not None:
                drafts.append(draft)

        for message in session.messages:
            for raw_line in message.content.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                for pattern, kind in _MARKERS:
                    match = pattern.match(line)
                    if not match:
                        continue
                    content = match.group(1).strip()
                    if content:
                        importance = 75 if kind is not MemoryKind.EVENT else 55
                        drafts.append(
                            MemoryDraft(
                                kind=kind,
                                title=self._title(content, kind.value.title()),
                                content=content,
                                summary=content[:240],
                                importance=importance,
                                confidence=0.72,
                                tags=["extracted-marker", f"source:{session.source}"],
                            )
                        )
                    break

        event_title = session.title or f"Session {session.session_id} archived"
        message_count = len(session.messages)
        drafts.append(
            MemoryDraft(
                kind=MemoryKind.EVENT,
                title=event_title,
                content=(
                    f"Session evidence archived from {session.source} as {library}. "
                    "Raw conversation text remains in the evidence layer."
                ),
                summary=(
                    f"{message_count} visible messages archived from "
                    f"{session.source} ({library})."
                ),
                importance=35,
                confidence=1.0,
                tags=["session-archive", f"source:{session.source}", f"library:{library}"],
                metadata={
                    "message_count": message_count,
                    "session_type": session.session_type,
                },
            )
        )
        return self._deduplicate(drafts)

    def _candidate_to_draft(self, value: dict[str, object]) -> MemoryDraft | None:
        try:
            kind = MemoryKind(str(value.get("kind", "event")))
        except ValueError:
            return None
        content = str(value.get("content") or "").strip()
        if not content:
            return None
        title = str(value.get("title") or self._title(content, kind.value.title()))
        summary = str(value.get("summary") or content[:240])
        importance = max(0, min(100, int(value.get("importance", 50))))
        confidence = max(0.0, min(1.0, float(value.get("confidence", 0.8))))
        raw_tags = value.get("tags")
        raw_aliases = value.get("aliases")
        raw_metadata = value.get("metadata")
        return MemoryDraft(
            kind=kind,
            title=title,
            content=content,
            summary=summary,
            importance=importance,
            confidence=confidence,
            tags=[str(item) for item in raw_tags] if isinstance(raw_tags, list) else [],
            aliases=[str(item) for item in raw_aliases]
            if isinstance(raw_aliases, list)
            else [],
            promote=bool(value.get("promote", False)),
            metadata=dict(raw_metadata) if isinstance(raw_metadata, dict) else {},
        )

    @staticmethod
    def _title(content: str, prefix: str) -> str:
        compact = " ".join(content.split())
        return f"{prefix}: {compact[:72]}" if compact else prefix

    @staticmethod
    def _deduplicate(drafts: list[MemoryDraft]) -> list[MemoryDraft]:
        seen: set[str] = set()
        result: list[MemoryDraft] = []
        for draft in drafts:
            key = sha256(
                f"{draft.kind.value}\0{draft.title}\0{draft.content}".encode()
            ).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            result.append(draft)
        return result
