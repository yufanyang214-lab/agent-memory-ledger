#!/usr/bin/python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_memory_ledger import MemoryLedger, SessionBundle


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


def codex_sessions_root(codex_home: str | None = None) -> Path:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return home.expanduser().resolve() / "sessions"


def rollout_thread_id(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("type") != "session_meta":
                    continue
                payload = event.get("payload") or {}
                value = payload.get("id") or payload.get("session_id")
                return str(value) if value else None
    except (OSError, ValueError, TypeError):
        return None
    return None


def locate_rollout(thread_id: str, codex_home: str | None = None) -> Path:
    root = codex_sessions_root(codex_home)
    if not root.is_dir():
        raise FileNotFoundError(f"Codex sessions directory not found: {root}")
    candidates = list(root.rglob(f"*{thread_id}*.jsonl"))
    if not candidates:
        for path in root.rglob("*.jsonl"):
            if rollout_thread_id(path) == thread_id:
                candidates.append(path)
    exact = [path for path in candidates if rollout_thread_id(path) == thread_id]
    selected = exact or candidates
    if not selected:
        raise FileNotFoundError(f"Codex rollout not found for thread: {thread_id}")
    return max(selected, key=lambda path: path.stat().st_mtime_ns)


def convert_rollout(
    rollout_file: Path,
    *,
    thread_id: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    messages: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for raw in rollout_file.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        event = json.loads(raw)
        event_type = event.get("type")
        payload = event.get("payload") or {}
        if event_type == "session_meta":
            metadata = {
                "thread_id": payload.get("id") or payload.get("session_id"),
                "cli_version": payload.get("cli_version"),
                "originator": payload.get("originator"),
                "source_mode": payload.get("source"),
                "cwd": payload.get("cwd"),
                "model_provider": payload.get("model_provider"),
            }
            continue
        if event_type != "event_msg":
            continue
        message_type = payload.get("type")
        if message_type == "user_message":
            role = "user"
        elif message_type == "agent_message":
            role = "assistant"
        else:
            continue
        text = payload.get("message")
        if not isinstance(text, str) or not text.strip():
            continue
        timestamp = str(event.get("timestamp") or "")
        key = (role, timestamp, text.strip())
        if key in seen:
            continue
        seen.add(key)
        messages.append(
            {
                "role": role,
                "content": text.strip(),
                "timestamp": timestamp or None,
                "metadata": {"phase": payload.get("phase")} if role == "assistant" else {},
            }
        )

    resolved_thread = str(thread_id or metadata.get("thread_id") or rollout_file.stem)
    if not messages:
        raise ValueError("Codex rollout contains no visible user/assistant events")
    return {
        "session_id": resolved_thread,
        "source": "codex-cli",
        "session_type": "conversation",
        "title": f"Codex thread {resolved_thread}",
        "tags": ["adapter:codex-cli"],
        "metadata": {
            **metadata,
            "rollout_filename": rollout_file.name,
            "visible_message_count": len(messages),
        },
        "messages": messages,
        "memory_candidates": candidates or [],
    }


def promote_marker_objects(ledger: MemoryLedger, result: dict[str, Any]) -> list[str]:
    promoted = list(result.get("promoted_object_ids", []))
    for item in result.get("objects", []):
        if item.get("kind") not in {"semantic", "procedural"}:
            continue
        if "extracted-marker" not in item.get("tags", []):
            continue
        object_id = str(item["object_id"])
        ledger.promote(object_id, reason="explicit stable marker in archived Codex session")
        if object_id not in promoted:
            promoted.append(object_id)
    return promoted


def archive(args: argparse.Namespace) -> dict[str, Any]:
    thread_id = args.thread_id or os.environ.get("CODEX_THREAD_ID")
    if args.rollout_file:
        rollout = Path(args.rollout_file).expanduser().resolve()
        thread_id = thread_id or rollout_thread_id(rollout)
    else:
        if not thread_id:
            raise ValueError("thread id missing; run inside Codex or pass --thread-id")
        rollout = locate_rollout(thread_id, args.codex_home)
    if not rollout.is_file():
        raise FileNotFoundError(f"rollout file not found: {rollout}")

    payload = convert_rollout(
        rollout,
        thread_id=thread_id,
        candidates=load_candidates(Path(args.candidate_file)) if args.candidate_file else [],
    )
    ledger = MemoryLedger(Path(args.ledger).expanduser().resolve())
    result = ledger.ingest(
        SessionBundle.from_dict(payload),
        library=None if args.library == "auto" else args.library,
    )
    promoted = (
        promote_marker_objects(ledger, result)
        if args.promote_markers
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
        "rollout_file": str(rollout),
        "ledger": str(ledger.workspace),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin Codex CLI adapter for Agent Memory Ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    ap = sub.add_parser("archive")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--thread-id")
    ap.add_argument("--rollout-file")
    ap.add_argument("--codex-home")
    ap.add_argument("--candidate-file")
    ap.add_argument("--library", choices=["auto", "primary", "auxiliary", "ignored"], default="auto")
    ap.add_argument("--promote-markers", action="store_true")
    rp = sub.add_parser("recall")
    rp.add_argument("--ledger", required=True)
    rp.add_argument("--query", required=True)
    rp.add_argument("--top-k", type=int, default=6)
    vp = sub.add_parser("validate")
    vp.add_argument("--ledger", required=True)
    cp = sub.add_parser("convert")
    cp.add_argument("--rollout-file", required=True)
    cp.add_argument("--thread-id")
    cp.add_argument("--candidate-file")
    lp = sub.add_parser("locate")
    lp.add_argument("--thread-id")
    lp.add_argument("--codex-home")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "archive":
            emit(archive(args))
        elif args.command == "recall":
            emit(MemoryLedger(Path(args.ledger).resolve()).search(args.query, top_k=args.top_k))
        elif args.command == "validate":
            result = MemoryLedger(Path(args.ledger).resolve()).validate()
            emit(result)
            return 0 if result.get("ok") else 2
        elif args.command == "convert":
            emit(
                convert_rollout(
                    Path(args.rollout_file).resolve(),
                    thread_id=args.thread_id,
                    candidates=load_candidates(Path(args.candidate_file)) if args.candidate_file else [],
                )
            )
        elif args.command == "locate":
            thread_id = args.thread_id or os.environ.get("CODEX_THREAD_ID")
            if not thread_id:
                raise ValueError("thread id missing; run inside Codex or pass --thread-id")
            emit({"thread_id": thread_id, "rollout_file": str(locate_rollout(thread_id, args.codex_home))})
    except Exception as exc:
        emit({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
