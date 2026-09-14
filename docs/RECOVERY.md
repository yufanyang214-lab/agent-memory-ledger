# Recovering and validating a workspace

AML treats evidence and object files as canonical. SQLite, FTS, and the two
ledger snapshots are derived views. Version 0.1.1 adds an explicit recovery
path and validation that no longer rewrites ledger files before checking them.

## Inspect before repairing

```bash
aml --workspace ./memory validate
```

The command returns a JSON report and exit code 2 for inconsistent state. It
does not create a fresh memory workspace or repair an existing one. It may
open the workspace lock and SQLite's normal read-side bookkeeping files.

Checks include canonical evidence hashes, manifest metadata, transcript
rendering, object filename/content identity, supported schema, source
references, exact catalog records, ledger snapshots, and active FTS contents.
Missing files and equal-count index drift are failures, not successful empty
workspaces. A healthy optional index is not evidence that the core is healthy.

## Rebuild the derived state

```bash
aml --workspace ./memory reindex
aml --workspace ./memory validate
```

The rebuild verifies canonical inputs before replacing anything. It creates a
fresh SQLite database, retains the previous database and any sidecar files
under `state/catalog-backups/<recovery-id>/`, replaces the catalog, and
regenerates both ledgers. Cooperating AML readers and writers use the same
workspace lock. Direct file or SQLite writes outside AML are not coordinated.

Objects are loaded as stored, including retractions, promotion flags,
timestamps, and stable IDs. Extraction is never rerun. With a configured
semantic plugin, the optional external index is rebuilt after the core; an
external failure is reported separately and does not undo core recovery.

Normal opening automatically rebuilds a missing catalog from available files.
Use explicit `reindex` for an existing corrupt catalog. If canonical files are
invalid, recovery refuses to replace the index and reports the files needing
attention. Restore those files from a trusted backup before retrying.

## Backup and provenance compatibility

Keep `workspace.json`, `evidence/`, `objects/`, and `state/journal.jsonl` together.
New writes persist every source archive ID in object metadata under the
core-owned `_aml_source_archive_ids` key. Extractor plugins must not use that
reserved key. Older objects retain their primary source in the object and may
need the append-only journal for secondary sources. Missing legacy journal
coverage is reported as a warning; it cannot be reconstructed with certainty.

Back up a quiescent workspace, or coordinate a snapshot using AML's workspace
lock. A plain recursive copy taken concurrently with mutations may span more
than one consistent state. Rebuild backups contain old private data and are
never suitable for a public bug report.

The workspace schema remains 2. Newer unsupported schemas are rejected before
any migration; pre-0.1 semantic/procedural aliases remain supported, including
their original object IDs.

## Boundaries

Integrity checks detect corruption and inconsistency; they cannot authenticate
files against an adversary who controls every copy and its hashes. They also
cannot recover deleted canonical evidence. Keep independent backups.

Base retrieval still uses SQLite FTS tokenization. Chinese substring matching,
automatic memory expiry, and semantic conflict resolution remain outside this
release. The historical runtime-model E2E reports remain dated evidence; the
new regression suite and wheel checks do not revalidate live model accounts.
