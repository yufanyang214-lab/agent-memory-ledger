from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ConcurrentWorkspaceTests(unittest.TestCase):
    def test_parallel_cli_ingests_share_one_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace = root / "memory"
            session_paths: list[Path] = []
            for index in range(32):
                session_path = root / f"session-{index:02d}.json"
                session_path.write_text(
                    json.dumps(
                        {
                            "session_id": f"parallel-{index:02d}",
                            "source": "concurrency-test-agent",
                            "title": f"Concurrent session {index:02d}",
                            "messages": [
                                {
                                    "role": "assistant",
                                    "content": f"Store concurrent item {index:02d}.",
                                }
                            ],
                            "memory_candidates": [
                                {
                                    "kind": "semantic",
                                    "title": f"Concurrent fact {index:02d}",
                                    "content": f"Concurrent value {index:02d} is durable.",
                                    "importance": 60,
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                session_paths.append(session_path)

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                completed = list(
                    executor.map(self._ingest, session_paths, [workspace] * 32)
                )

            failures = [
                {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
                for result in completed
                if result.returncode != 0
            ]
            self.assertEqual(failures, [])

            validation = self._run_cli(workspace, "validate")
            self.assertEqual(
                validation.returncode, 0, validation.stdout + validation.stderr
            )
            report = json.loads(validation.stdout)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["archives"], 32)
            self.assertEqual(report["active_objects"], 64)
            self.assertEqual(report["fts_rows"], 64)
            self.assertTrue((workspace / "state" / "workspace.lock").is_file())
            self.assertEqual(list(workspace.rglob("*.tmp")), [])

            journal = [
                json.loads(line)
                for line in (workspace / "state" / "journal.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(
                sum(item["event_type"] == "session.archived" for item in journal),
                32,
            )
            self.assertEqual(
                sum(item["event_type"] == "memory.upserted" for item in journal),
                64,
            )

    @classmethod
    def _ingest(
        cls, session_path: Path, workspace: Path
    ) -> subprocess.CompletedProcess[str]:
        return cls._run_cli(workspace, "ingest", str(session_path))

    @staticmethod
    def _run_cli(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
        project_root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        python_path = str(project_root / "src")
        if env.get("PYTHONPATH"):
            python_path += os.pathsep + env["PYTHONPATH"]
        env["PYTHONPATH"] = python_path
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_memory_ledger.cli",
                "--workspace",
                str(workspace),
                *args,
            ],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
