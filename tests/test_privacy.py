from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_memory_ledger import MemoryLedger, SessionBundle


class PrivacyTests(unittest.TestCase):
    def test_identifiers_nested_keys_and_audit_reasons_are_sanitized(self) -> None:
        fake = "ghp_" + "SYNTHETICFIXTUREONLY" * 2
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); ledger = MemoryLedger(root)
            session = SessionBundle.from_dict({
                "session_id": "session-" + fake, "source": "agent-" + fake,
                "session_type": "conversation-" + fake, "title": fake,
                "tags": [fake], "metadata": {fake: {"nested": fake}},
                "messages": [{"role": "user", "content": "Archive a synthetic fact.",
                              "id": fake, "timestamp": fake, "name": fake,
                              "extra": {fake: fake}}],
                "memory_candidates": [{"kind": "knowledge", "title": "A safe fact",
                                       "content": "Orchid routing is stable.", "promote": True}],
            })
            result = ledger.ingest(session)
            self.assertNotIn(fake, str(result))
            # Sanitization does not mutate the caller's input.
            self.assertIn(fake, session.session_id)
            ledger.retract(result["objects"][0]["object_id"], reason="Retraction with " + fake)
            ledger.store.append_journal("synthetic.error", {"query": fake, "error": fake})
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertNotIn(fake.encode(), path.read_bytes(), str(path))
            self.assertTrue(ledger.validate()["ok"], ledger.validate())


if __name__ == "__main__":
    unittest.main()
