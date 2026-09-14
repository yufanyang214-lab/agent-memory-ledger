#!/usr/bin/python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

from agent_memory_ledger import MemoryLedger, SessionBundle


_REMINDER_PREFIXES = ("<system-reminder>", "<system-reminder ")


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def load_candidates(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("memory_candidates", [])
    if not isinstance(value, list):
        raise ValueError("candidate file must be a list or contain memory_candidates")
    return [item for item in value if isinstance(item, dict)]


def visible_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def is_synthetic_user_message(text: str) -> bool:
    return text.lstrip().lower().startswith(_REMINDER_PREFIXES)


def context_to_bundle(
    context_text: str,
    *,
    session_id: str,
    source_metadata: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for index, raw in enumerate(context_text.splitlines()):
        if not raw.strip():
            continue
        record = json.loads(raw)
        role = record.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = visible_text(record.get("content"))
        if not text:
            continue
        if role == "user" and is_synthetic_user_message(text):
            continue
        messages.append(
            {
                "role": role,
                "content": text,
                "message_id": str(record.get("id") or f"message-{index:04d}"),
            }
        )
    if not messages:
        raise ValueError("Kimi session contains no visible user/assistant text")
    metadata = dict(source_metadata or {})
    metadata["visible_message_count"] = len(messages)
    return {
        "session_id": session_id,
        "source": "kimi-code-cli",
        "session_type": "conversation",
        "title": f"Kimi session {session_id}",
        "tags": ["adapter:kimi-code-cli"],
        "metadata": metadata,
        "messages": messages,
        "memory_candidates": candidates or [],
    }


def convert_context_file(
    context_file: Path,
    *,
    session_id: str | None = None,
    workdir: Path | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved = context_file.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Kimi context file not found: {resolved}")
    resolved_session = session_id or resolved.parent.name
    return context_to_bundle(
        resolved.read_text(encoding="utf-8"),
        session_id=resolved_session,
        source_metadata={
            "context_filename": resolved.name,
            "workdir": str(workdir.resolve()) if workdir else None,
        },
        candidates=candidates,
    )


def convert_export_zip(
    export_zip: Path,
    *,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved = export_zip.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Kimi export ZIP not found: {resolved}")
    with zipfile.ZipFile(resolved) as archive:
        names = set(archive.namelist())
        if "manifest.json" not in names or "context.jsonl" not in names:
            raise ValueError("Kimi export ZIP must contain manifest.json and context.jsonl")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        context_text = archive.read("context.jsonl").decode("utf-8")
    session_id = str(manifest.get("session_id") or resolved.stem)
    allowed_manifest = {
        key: manifest.get(key)
        for key in (
            "session_id",
            "exported_at",
            "kimi_cli_version",
            "python_version",
            "os",
            "platform",
            "session_first_activity",
            "session_last_activity",
        )
        if key in manifest
    }
    allowed_manifest["export_filename"] = resolved.name
    return context_to_bundle(
        context_text,
        session_id=session_id,
        source_metadata=allowed_manifest,
        candidates=candidates,
    )


def share_dir(path: str | None = None) -> Path:
    return Path(path or os.environ.get("KIMI_SHARE_DIR") or Path.home() / ".kimi").expanduser().resolve()


def sessions_dir_for_workdir(workdir: Path, share: Path) -> Path:
    canonical = str(workdir.expanduser().resolve())
    digest = hashlib.md5(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()
    return share / "sessions" / digest


def locate_context_file(
    workdir: Path,
    *,
    session_id: str | None = None,
    share_path: str | None = None,
) -> Path:
    root = sessions_dir_for_workdir(workdir, share_dir(share_path))
    if session_id:
        exact = root / session_id / "context.jsonl"
        legacy = root / f"{session_id}.jsonl"
        for candidate in (exact, legacy):
            if candidate.is_file():
                return candidate.resolve()
        raise FileNotFoundError(f"Kimi session not found: {session_id} under {root}")
    candidates = list(root.glob("*/context.jsonl")) + list(root.glob("*.jsonl"))
    if not candidates:
        raise FileNotFoundError(f"No Kimi sessions found for workdir: {workdir.resolve()}")
    return max(candidates, key=lambda item: item.stat().st_mtime_ns).resolve()


def promote_marker_objects(ledger: MemoryLedger, result: dict[str, Any]) -> list[str]:
    promoted = list(result.get("promoted_object_ids", []))
    for item in result.get("objects", []):
        if item.get("kind") not in {
            "knowledge",
            "procedure",
            "semantic",
            "procedural",
        }:
            continue
        if "extracted-marker" not in item.get("tags", []):
            continue
        object_id = str(item["object_id"])
        ledger.promote(object_id, reason="explicit stable marker in archived Kimi session")
        if object_id not in promoted:
            promoted.append(object_id)
    return promoted


def ingest_payload(
    payload: dict[str, Any],
    *,
    ledger_path: str,
    library: str,
    promote_markers: bool,
    source_locator: str,
) -> dict[str, Any]:
    ledger = MemoryLedger(Path(ledger_path).expanduser().resolve())
    result = ledger.ingest(
        SessionBundle.from_dict(payload),
        library=None if library == "auto" else library,
    )
    promoted = (
        promote_marker_objects(ledger, result)
        if promote_markers
        else list(result.get("promoted_object_ids", []))
    )
    return {
        "status": result.get("status"),
        "session_id": result.get("session_id"),
        "library": result.get("library"),
        "archive": result.get("archive"),
        "object_ids": [item["object_id"] for item in result.get("objects", [])],
        "promoted_object_ids": promoted,
        "visible_messages": len(payload["messages"]),
        "source_locator": source_locator,
        "ledger": str(ledger.workspace),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin Kimi Code CLI adapter for Agent Memory Ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    archive = sub.add_parser("archive")
    archive.add_argument("--workdir", default=".")
    archive.add_argument("--session-id")
    archive.add_argument("--share-dir")
    archive.add_argument("--ledger", required=True)
    archive.add_argument("--candidate-file")
    archive.add_argument("--library", choices=["auto", "primary", "auxiliary", "ignored"], default="auto")
    archive.add_argument("--promote-markers", action="store_true")
    archive_zip = sub.add_parser("archive-zip")
    archive_zip.add_argument("--export-zip", required=True)
    archive_zip.add_argument("--ledger", required=True)
    archive_zip.add_argument("--candidate-file")
    archive_zip.add_argument("--library", choices=["auto", "primary", "auxiliary", "ignored"], default="auto")
    archive_zip.add_argument("--promote-markers", action="store_true")
    recall = sub.add_parser("recall")
    recall.add_argument("--ledger", required=True)
    recall.add_argument("--query", required=True)
    recall.add_argument("--top-k", type=int, default=6)
    validate = sub.add_parser("validate")
    validate.add_argument("--ledger", required=True)
    convert = sub.add_parser("convert")
    source = convert.add_mutually_exclusive_group(required=True)
    source.add_argument("--context-file")
    source.add_argument("--export-zip")
    convert.add_argument("--session-id")
    convert.add_argument("--workdir")
    convert.add_argument("--candidate-file")
    locate = sub.add_parser("locate")
    locate.add_argument("--workdir", default=".")
    locate.add_argument("--session-id")
    locate.add_argument("--share-dir")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "archive":
            workdir = Path(args.workdir).resolve()
            context = locate_context_file(
                workdir,
                session_id=args.session_id,
                share_path=args.share_dir,
            )
            payload = convert_context_file(
                context,
                session_id=args.session_id,
                workdir=workdir,
                candidates=load_candidates(Path(args.candidate_file)) if args.candidate_file else [],
            )
            emit(
                ingest_payload(
                    payload,
                    ledger_path=args.ledger,
                    library=args.library,
                    promote_markers=args.promote_markers,
                    source_locator=str(context),
                )
            )
        elif args.command == "archive-zip":
            export_zip = Path(args.export_zip).resolve()
            payload = convert_export_zip(
                export_zip,
                candidates=load_candidates(Path(args.candidate_file)) if args.candidate_file else [],
            )
            emit(
                ingest_payload(
                    payload,
                    ledger_path=args.ledger,
                    library=args.library,
                    promote_markers=args.promote_markers,
                    source_locator=str(export_zip),
                )
            )
        elif args.command == "recall":
            emit(MemoryLedger(Path(args.ledger).resolve()).search(args.query, top_k=args.top_k))
        elif args.command == "validate":
            result = MemoryLedger(Path(args.ledger).resolve(), initialize=False).validate()
            emit(result)
            return 0 if result.get("ok") else 2
        elif args.command == "convert":
            candidates = load_candidates(Path(args.candidate_file)) if args.candidate_file else []
            if args.export_zip:
                emit(convert_export_zip(Path(args.export_zip), candidates=candidates))
            else:
                emit(
                    convert_context_file(
                        Path(args.context_file),
                        session_id=args.session_id,
                        workdir=Path(args.workdir).resolve() if args.workdir else None,
                        candidates=candidates,
                    )
                )
        elif args.command == "locate":
            emit(
                {
                    "context_file": str(
                        locate_context_file(
                            Path(args.workdir).resolve(),
                            session_id=args.session_id,
                            share_path=args.share_dir,
                        )
                    )
                }
            )
    except Exception as exc:
        emit({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
