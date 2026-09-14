#!/usr/bin/python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def call(cmd: list[str], cwd: Path, child_env: dict[str, str] | None = None, parse: bool = False):
    result = subprocess.run(cmd, cwd=cwd, env=child_env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(
            f"failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return json.loads(result.stdout) if parse else result.stdout


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    root = Path(tempfile.mkdtemp(prefix="aml-clean-room-"))
    keep = "--keep" in sys.argv[1:]
    try:
        home = root / "home"
        agent = root / "agent-workspace"
        memory = agent / "portable-memory"
        dist = root / "dist"
        venv = root / "venv"
        for path in (home, agent, dist):
            path.mkdir(parents=True, exist_ok=True)

        call(
            [sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
             "--wheel-dir", str(dist)],
            repo,
        )
        wheels = list(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise AssertionError(f"expected one wheel, got {wheels}")
        call([sys.executable, "-m", "venv", str(venv)], root)

        bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        aml = bin_dir / ("aml.exe" if os.name == "nt" else "aml")
        child_env = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "PYTHONNOUSERSITE": "1",
            "PATH": os.pathsep.join([str(bin_dir), "/usr/local/bin", "/usr/bin", "/bin"]),
        }
        if os.name == "nt":
            for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
                if key in os.environ:
                    child_env[key] = os.environ[key]
        call([str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])], agent, child_env)

        session = {
            "session_id": "clean-agent-session-001",
            "source": "generic-clean-agent",
            "messages": [
                {"role": "user", "content": "Remember release checks before publishing."},
                {"role": "assistant", "content": "Decision: filesystem is the canonical memory source."},
                {"role": "assistant", "content": "Procedure: test, build, then validate artifacts."},
            ],
            "memory_candidates": [
                {
                    "kind": "knowledge", "title": "Canonical memory source",
                    "content": "The filesystem is the canonical memory source.",
                    "summary": "Canonical memory stays outside replaceable indexes.",
                    "importance": 92, "confidence": 0.98,
                    "tags": ["storage", "memory"], "promote": True,
                },
                {
                    "kind": "procedure", "title": "Release verification",
                    "content": "Run tests, build, and validate artifacts before publishing.",
                    "summary": "Reusable release verification procedure.",
                    "importance": 84, "confidence": 0.95,
                    "tags": ["release", "verification"], "promote": True,
                },
            ],
        }
        (agent / "session.json").write_text(json.dumps(session, indent=2), encoding="utf-8")
        plugin = agent / "semantic_index_plugin.py"
        shutil.copy2(repo / "examples/overlap_index_plugin.py", plugin)
        base = [str(aml), "--workspace", str(memory)]

        initialized = call(base + ["init"], agent, child_env, True)
        ingested = call(
            base + ["ingest", "session.json", "--semantic-plugin", f"{plugin}:create_index"],
            agent, child_env, True,
        )
        searched = call(
            base + ["search", "canonical filesystem memory", "--top-k", "5",
                    "--semantic-plugin", f"{plugin}:create_index"],
            agent, child_env, True,
        )
        bright = call(base + ["list", "--bright"], agent, child_env, True)
        checked = call(
            base + ["validate", "--semantic-plugin", f"{plugin}:create_index"],
            agent, child_env, True,
        )

        # Exercise the installed wheel's recovery path, including persisted
        # promotion/retraction. No source checkout or extractor is used here.
        retired_id = bright[0]["object_id"]
        call(base + ["retract", retired_id, "--reason", "synthetic recovery check"], agent, child_env, True)
        restored = agent / "restored-memory"
        shutil.copytree(memory, restored, ignore=shutil.ignore_patterns(
            "catalog.sqlite3*", "bright.jsonl", "dark.jsonl",
        ))
        restored_base = [str(aml), "--workspace", str(restored)]
        missing = subprocess.run(restored_base + ["validate"], cwd=agent, env=child_env, text=True, capture_output=True)
        rebuilt = call(restored_base + ["reindex"], agent, child_env, True)
        restored_check = call(restored_base + ["validate"], agent, child_env, True)
        retired = call(restored_base + ["show", retired_id], agent, child_env, True)
        restored_bright = call(restored_base + ["list", "--bright"], agent, child_env, True)
        restored_hits = call(restored_base + ["search", "filesystem verification memory"], agent, child_env, True)
        # Continue the original assertions against the untouched pre-retraction
        # results, then check the explicit recovery outcomes separately.

        count = lambda path: sum(bool(line.strip()) for line in path.read_text().splitlines())
        bright_path = memory / "ledgers/bright.jsonl"
        dark_path = memory / "ledgers/dark.jsonl"
        objects = list(memory.glob("objects/*/*.json"))
        object_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in objects]
        archive_event = next(
            item for item in object_payloads if "session-archive" in item.get("tags", [])
        )
        checks = {
            "initialized": initialized.get("status") == "initialized",
            "ingested": ingested.get("status") == "completed",
            "validated": checked.get("ok") is True,
            "evidence": len(list(memory.glob("evidence/*/*/manifest.json"))) == 1,
            "objects": len(objects) >= 3,
            "canonical_kinds": (
                {item.get("kind") for item in object_payloads}
                <= {"knowledge", "procedure", "event"}
                and (memory / "objects/knowledge").is_dir()
                and (memory / "objects/procedure").is_dir()
                and (memory / "objects/event").is_dir()
                and not (memory / "objects/semantic").exists()
                and not (memory / "objects/procedural").exists()
            ),
            "bright_ledger": bright_path.is_file() and count(bright_path) >= 1,
            "dark_ledger": dark_path.is_file() and count(dark_path) >= 3,
            "sqlite": (memory / "state/catalog.sqlite3").is_file(),
            "restart_recall": bool(searched),
            "fts": any("fts" in item.get("retrieval_sources", []) for item in searched),
            "plugin": any("overlap-example" in item.get("retrieval_sources", []) for item in searched),
            "promoted": len(bright) >= 2,
            "missing_index_detected": missing.returncode == 2 and json.loads(missing.stdout).get("ok") is False,
            "catalog_rebuilt": rebuilt.get("status") == "completed" and restored_check.get("ok") is True,
            "restored_recall": bool(restored_hits),
            "retraction_preserved": retired.get("status") == "retracted" and not retired.get("promoted"),
            "promotion_preserved": len(restored_bright) == len(bright) - 1,
            "raw_text_only_in_evidence": (
                "Remember release checks" not in archive_event.get("content", "")
                and "Last context" not in archive_event.get("content", "")
                and "evidence layer" in archive_event.get("content", "")
            ),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise AssertionError(f"clean-room checks failed: {failed}")
        for forbidden in (".openclaw", ".claude", ".codex", "mempalace"):
            if list(home.rglob(forbidden)) or list(agent.rglob(forbidden)):
                raise AssertionError(f"platform-specific directory created: {forbidden}")

        print(json.dumps({
            "status": "passed", "wheel": wheels[0].name,
            "objects": len(objects), "bright_entries": count(bright_path),
            "dark_entries": count(dark_path), "search_hits": len(searched),
            "retrieval_sources": sorted({s for item in searched for s in item.get("retrieval_sources", [])}),
            "platform_specific_dirs_created": False, "checks": checks,
        }, indent=2))
        return 0
    finally:
        if keep:
            print(f"clean room kept at: {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
