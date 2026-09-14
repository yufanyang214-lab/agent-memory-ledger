# Changelog

## 0.1.1 — release candidate, 2026-09-14

- Rebuild SQLite, FTS, and ledger snapshots from verified canonical files.
- Preserve complete object provenance on disk, with journal-based compatibility
  for older objects; preserve retraction, promotion, IDs, and timestamps.
- Validate evidence hashes, transcripts, object contents, provenance, ledgers,
  and exact FTS contents without silently repairing them.
- Retain replaced databases and sidecars in a private recovery backup.
- Recover file-backed provenance despite a damaged journal, preserving the
  original audit bytes and validation errors; fail if legacy provenance needs it.
- Create empty canonical kind directories during explicit legacy reindex.
- Reject newer workspace schemas before modifying their metadata.
- Keep waiting for contended Windows locks, while surfacing permanent errors.
- Redact identifier fields, nested metadata keys, retraction reasons, and audit
  payloads; ignore the default `.agent-memory-ledger/` workspace in Git.
- Make runtime-adapter validation use the same non-repairing behavior.
- Align package, plugin manifests, and installation examples at version 0.1.1.

Historical 0.1.0 release assets are unchanged and do not contain these fixes.
The workspace schema remains version 2; existing object IDs stay valid.

## 0.1.0 — 2026-08-17

Initial standalone archive, memory-object, dual-ledger, FTS, plugin, and runtime
adapter implementation. Later main-branch commits added concurrent writer
locking and canonical knowledge/procedure/event names before the 0.1.1 fixes.
