from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workspace = self.root / "memory"
        self.session_path = self.root / "session.json"
        self.session_path.write_text(
            json.dumps(
                {
                    "session_id": "cli-session",
                    "source": "cli-test-agent",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "Fact: CLI imports create durable memory objects.",
                        }
                    ],
                    "memory_candidates": [
                        {
                            "kind": "procedure",
                            "title": "CLI verification",
                            "content": "Run validate after importing a session.",
                            "importance": 80,
                            "promote": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_cli_round_trip(self) -> None:
        initialized = self._run("init")
        self.assertEqual(initialized["status"], "initialized")

        ingested = self._run("ingest", str(self.session_path))
        self.assertEqual(ingested["status"], "completed")
        self.assertEqual(ingested["library"], "primary")

        hits = self._run("search", "durable memory", "--top-k", "3")
        self.assertTrue(hits)
        self.assertTrue(any(item["kind"] == "knowledge" for item in hits))

        # Pre-0.1 CLI filters remain accepted but return canonical objects.
        legacy_filter = self._run("list", "--kind", "procedural")
        self.assertEqual([item["kind"] for item in legacy_filter], ["procedure"])

        bright = self._run("list", "--bright")
        self.assertEqual([item["title"] for item in bright], ["CLI verification"])

        validation = self._run("validate")
        self.assertTrue(validation["ok"], validation)

    def test_cli_returns_machine_readable_error(self) -> None:
        bad_path = self.root / "bad.json"
        bad_path.write_text("{not json}", encoding="utf-8")
        completed = self._run_raw("ingest", str(bad_path))
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "error")
        self.assertIn("ValueError", payload["error"])

    def test_validate_and_reindex_work_when_catalog_is_missing(self) -> None:
        self._run("ingest", str(self.session_path))
        index = self.workspace / "state" / "catalog.sqlite3"
        index.unlink()
        checked = self._run_raw("validate")
        self.assertEqual(checked.returncode, 2)
        self.assertFalse(json.loads(checked.stdout)["ok"])
        self.assertFalse(index.exists())
        rebuilt = self._run("reindex")
        self.assertEqual(rebuilt["status"], "completed")
        self.assertEqual(rebuilt["semantic_index"]["status"], "disabled")
        self.assertTrue(self._run("validate")["ok"])
        self.assertTrue(self._run("search", "durable memory"))

    def _run(self, *args: str) -> object:
        completed = self._run_raw(*args)
        if completed.returncode != 0:
            self.fail(
                f"CLI failed with {completed.returncode}:\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        return json.loads(completed.stdout)

    def _run_raw(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_memory_ledger.cli",
                "--workspace",
                str(self.workspace),
                *args,
            ],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
