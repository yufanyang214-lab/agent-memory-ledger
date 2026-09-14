#!/usr/bin/python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from agent_memory_ledger import MemoryLedger, SessionBundle


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def visible_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"text", "input_text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def load_candidates(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("memory_candidates", [])
    if not isinstance(value, list):
        raise ValueError("candidate file must be a list or contain memory_candidates")
    return [item for item in value if isinstance(item, dict)]


def infer_type(session_key: str) -> str:
    lowered = session_key.lower()
    for name in ("cron", "heartbeat", "subagent", "executor", "validator", "background"):
        if f":{name}:" in lowered:
            return name
    return "conversation"


def convert_export(export_dir: Path, *, agent: str, session_key: str,
                   candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    messages: list[dict[str, Any]] = []
    for raw in (export_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        event = json.loads(raw)
        if event.get("source") != "transcript":
            continue
        if event.get("type") not in {"user.message", "assistant.message"}:
            continue
        message = (event.get("data") or {}).get("message") or {}
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = visible_text(message.get("content"))
        if not text:
            continue
        messages.append({
            "role": role,
            "content": text,
            "timestamp": event.get("ts"),
            "message_id": event.get("entryId"),
        })
    if not messages:
        raise ValueError("trajectory export contains no visible user/assistant text")
    return {
        "session_id": str(manifest.get("sessionId") or session_key),
        "source": "openclaw",
        "session_type": infer_type(session_key),
        "title": session_key.rsplit(":", 1)[-1],
        "tags": ["adapter:openclaw", f"agent:{agent}"],
        "metadata": {
            "source_session_key": session_key,
            "source_agent": agent,
            "trajectory_schema": manifest.get("schemaVersion"),
            "visible_message_count": len(messages),
        },
        "messages": messages,
        "memory_candidates": candidates or [],
    }


def process_id() -> int:
    import os
    return os.getpid()


def export_trajectory(agent: str, session_key: str, workspace: Path) -> Path:
    suffix = sha256(session_key.encode("utf-8")).hexdigest()[:12]
    output_name = f"aml-{suffix}-{process_id()}"
    command = [
        "openclaw", "sessions", "export-trajectory", "--agent", agent,
        "--session-key", session_key, "--output", output_name,
        "--workspace", str(workspace), "--json",
    ]
    completed = subprocess.run(command, cwd=workspace, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"OpenClaw trajectory export failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    result = json.loads(completed.stdout)
    output_dir = Path(result["outputDir"])
    if not output_dir.is_dir():
        raise RuntimeError(f"trajectory export directory missing: {output_dir}")
    return output_dir


def clean_export(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    for candidate in (path.parent, path.parent.parent):
        try:
            candidate.rmdir()
        except OSError:
            pass


def archive(args: argparse.Namespace) -> dict[str, Any]:
    workdir = Path(args.workdir).resolve()
    ledger = MemoryLedger(Path(args.ledger).resolve())
    export_dir = export_trajectory(args.agent, args.session_key, workdir)
    try:
        payload = convert_export(
            export_dir,
            agent=args.agent,
            session_key=args.session_key,
            candidates=load_candidates(Path(args.candidate_file)) if args.candidate_file else [],
        )
        result = ledger.ingest(
            SessionBundle.from_dict(payload),
            library=None if args.library == "auto" else args.library,
        )
        promoted: list[str] = list(result.get("promoted_object_ids", []))
        if args.promote_markers:
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
                ledger.promote(object_id, reason="explicit stable marker in archived session")
                if object_id not in promoted:
                    promoted.append(object_id)
        return {
            "status": result.get("status"),
            "session_id": result.get("session_id"),
            "library": result.get("library"),
            "archive": result.get("archive"),
            "object_ids": [item["object_id"] for item in result.get("objects", [])],
            "promoted_object_ids": promoted,
            "visible_messages": len(payload["messages"]),
            "ledger": str(ledger.workspace),
        }
    finally:
        if not args.keep_export:
            clean_export(export_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin OpenClaw adapter for Agent Memory Ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    ap = sub.add_parser("archive")
    ap.add_argument("--agent", required=True)
    ap.add_argument("--session-key", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--candidate-file")
    ap.add_argument("--library", choices=["auto", "primary", "auxiliary", "ignored"], default="auto")
    ap.add_argument("--promote-markers", action="store_true")
    ap.add_argument("--keep-export", action="store_true")
    rp = sub.add_parser("recall")
    rp.add_argument("--ledger", required=True)
    rp.add_argument("--query", required=True)
    rp.add_argument("--top-k", type=int, default=6)
    vp = sub.add_parser("validate")
    vp.add_argument("--ledger", required=True)
    cp = sub.add_parser("convert")
    cp.add_argument("--export-dir", required=True)
    cp.add_argument("--agent", required=True)
    cp.add_argument("--session-key", required=True)
    cp.add_argument("--candidate-file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "archive":
            emit(archive(args))
        elif args.command == "recall":
            emit(MemoryLedger(Path(args.ledger).resolve()).search(args.query, top_k=args.top_k))
        elif args.command == "validate":
            result = MemoryLedger(Path(args.ledger).resolve(), initialize=False).validate()
            emit(result)
            return 0 if result.get("ok") else 2
        elif args.command == "convert":
            emit(convert_export(
                Path(args.export_dir), agent=args.agent, session_key=args.session_key,
                candidates=load_candidates(Path(args.candidate_file)) if args.candidate_file else [],
            ))
    except Exception as exc:
        emit({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
