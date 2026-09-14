from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .models import Library, MemoryKind, SessionBundle
from .ports import load_plugin
from .service import MemoryLedger


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _add_plugin_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--semantic-plugin",
        help="optional semantic-index adapter as module:factory or plugin.py:factory",
    )
    parser.add_argument(
        "--extractor-plugin",
        help="optional session-to-memory extractor as module:factory or plugin.py:factory",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-memory-ledger",
        description="Archive agent sessions into structured memory and bright/dark ledgers.",
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("AML_WORKSPACE", ".agent-memory-ledger"),
        help="memory workspace directory (default: .agent-memory-ledger)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="initialize a memory workspace")

    ingest = subparsers.add_parser("ingest", help="archive a JSON/JSONL session")
    ingest.add_argument("session_file")
    ingest.add_argument(
        "--library",
        choices=["auto", *(item.value for item in Library)],
        default="auto",
    )
    _add_plugin_args(ingest)

    search = subparsers.add_parser("search", help="search memory objects")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=10)
    _add_plugin_args(search)

    list_parser = subparsers.add_parser("list", help="list active memory objects")
    list_parser.add_argument(
        "--kind",
        choices=MemoryKind.accepted_values(),
        help=(
            "filter by knowledge, procedure, or event "
            "(legacy semantic/procedural aliases are accepted)"
        ),
    )
    list_parser.add_argument("--bright", action="store_true", help="only promoted objects")

    show = subparsers.add_parser("show", help="show one memory object")
    show.add_argument("object_id")

    promote = subparsers.add_parser("promote", help="add an object to the bright ledger")
    promote.add_argument("object_id")
    promote.add_argument("--reason", default="manual")

    retract = subparsers.add_parser("retract", help="retract an object without deleting evidence")
    retract.add_argument("object_id")
    retract.add_argument("--reason", required=True)

    validate = subparsers.add_parser("validate", help="validate workspace consistency")
    _add_plugin_args(validate)

    reindex = subparsers.add_parser("reindex", help="rebuild SQLite, ledgers, and an optional semantic index")
    _add_plugin_args(reindex)

    return parser


def _make_ledger(args: argparse.Namespace) -> MemoryLedger:
    workspace = Path(args.workspace)
    semantic_index = None
    extractor = None
    semantic_spec = getattr(args, "semantic_plugin", None)
    extractor_spec = getattr(args, "extractor_plugin", None)
    if semantic_spec:
        semantic_index = load_plugin(semantic_spec, workspace.resolve())
    if extractor_spec:
        extractor = load_plugin(extractor_spec, workspace.resolve())
    return MemoryLedger(
        workspace,
        extractor=extractor,
        semantic_index=semantic_index,
        initialize=args.command not in {"validate", "reindex"},
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ledger = _make_ledger(args)
        if args.command == "init":
            ledger.store.initialize()
            _print({"status": "initialized", "workspace": str(ledger.workspace)})
            return 0
        if args.command == "ingest":
            session = SessionBundle.from_path(args.session_file)
            _print(ledger.ingest(session, library=args.library))
            return 0
        if args.command == "search":
            _print(ledger.search(args.query, top_k=args.top_k))
            return 0
        if args.command == "list":
            promoted = True if args.bright else None
            _print(ledger.list_objects(kind=args.kind, promoted=promoted))
            return 0
        if args.command == "show":
            _print(ledger.store.require_object(args.object_id).to_dict())
            return 0
        if args.command == "promote":
            _print(ledger.promote(args.object_id, reason=args.reason))
            return 0
        if args.command == "retract":
            _print(ledger.retract(args.object_id, reason=args.reason))
            return 0
        if args.command == "validate":
            result = ledger.validate()
            _print(result)
            return 0 if result.get("ok") else 2
        if args.command == "reindex":
            result = ledger.reindex()
            _print(result)
            return 0 if result.get("status") in {"completed", "disabled"} else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _print({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 2
    except Exception as exc:  # keep CLI failures machine-readable
        _print({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
