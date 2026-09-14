# Security

Agent Memory Ledger stores conversation-derived material on disk. Treat its workspace as private data unless you deliberately publish an export.

## Defaults

- Session content is sanitized before evidence, objects, or indexes are written.
- The built-in sanitizer is a safety net, not a complete data-loss-prevention system.
- Optional extractor and semantic-index plugins run with the permissions of the host process.
- Vector adapters receive only the sanitized object text supplied by the core.
- Original evidence is retained after a memory object is retracted.
- Identifier fields, nested metadata keys, and audit payloads also pass through
  built-in secret-pattern redaction. This does not anonymize all personal data.
- Validation detects mismatches between evidence hashes, transcripts, canonical
  objects, ledgers, and indexes. These local checks are not cryptographic proof
  against an attacker who can rewrite the entire workspace.

## Recommended deployment

- Keep the memory workspace outside public repositories.
- Restrict filesystem permissions to the intended user or service account.
- Review third-party plugins before installation.
- Use synthetic sessions in bug reports and tests.
- Add application-specific redaction rules before importing regulated or highly sensitive data.
- Keep recovery backups private too: `state/catalog-backups/` can contain older
  indexed data. Upgrading does not retroactively sanitize existing archives.

## Reporting

Please report security issues privately to the repository maintainer rather than opening a public issue containing session data or credentials.
