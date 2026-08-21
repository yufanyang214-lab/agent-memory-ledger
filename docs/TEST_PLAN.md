# Test Plan

Agent Memory Ledger is tested at three levels. The goal is not to benchmark a
hosted memory product; it is to prove that the portable archiving and dual-ledger
design works without an agent platform's official memory implementation.

## Level 1: deterministic core tests

Run:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The suite covers:

- JSON, JSONL, and generic session input;
- evidence sanitization;
- AML-defined knowledge, procedure, and event objects;
- pre-0.1 semantic/procedural input aliases and workspace migration with stable
  object IDs;
- primary, auxiliary, and ignored routing;
- bright-ledger promotion and dark-ledger registration;
- duplicate ingestion and multi-source provenance;
- manual promotion and retraction;
- retracted objects staying retracted after re-ingestion;
- SQLite FTS recall;
- optional semantic-index adapters and failure fallback;
- OpenClaw trajectory conversion that excludes runtime prompts, hidden thinking,
  tool calls, and tool results;
- raw conversation text remaining in evidence rather than archive-event objects.

## Level 2: platform-free clean room

Run:

```bash
python scripts/clean_room_smoke.py
```

The script builds a wheel, installs it into a fresh virtual environment with a
fresh home directory, runs outside the source checkout, and verifies:

- no OpenClaw, Claude, Codex, or MemPalace directory is created;
- evidence, structured objects, both ledgers, SQLite, and audit state exist;
- process-restart recall works;
- FTS and a user-supplied semantic-index plugin can be merged;
- optional indexes are derived, not canonical;
- raw transcript text is not duplicated into the generated archive event.

This test runs in GitHub Actions on Python 3.11 and 3.12.

## Level 3: clean Agent integration

The OpenClaw adapter is tested in a disposable agent workspace:

1. Create a new agent workspace with no `memory/`, `MEMORY.md`, or `DREAMS.md`.
2. Confirm the unique test marker is absent from OpenClaw built-in memory.
3. Install the standalone core into `.agent-memory-ledger-venv`.
4. Install `integrations/openclaw-skill` as the only workspace-local memory
   integration.
5. In session A, provide a unique fact and procedure and ask the agent to archive
   the current session through the skill.
6. In a new negative-control session, forbid tools and confirm the agent returns
   `UNKNOWN`.
7. In session B, allow only the skill, recall the fact and procedure, and require
   supporting object IDs.
8. Audit the trajectory to confirm the bridge CLI was called and official memory
   search was not called.
9. Re-run the archive command and confirm archive ID, creation time, and object IDs
   are unchanged.
10. Re-query built-in memory and confirm the unique marker is still absent.

### Important OpenClaw baseline detail

A newly created OpenClaw workspace contains a template `USER.md`. After the first
agent turn, OpenClaw may index that template automatically. Therefore the correct
independence gate is not "the built-in database must forever contain zero chunks."
The correct gates are:

- no test fact is present in built-in memory search;
- no official memory file receives the test fact;
- the negative-control session cannot answer;
- the skill-enabled session can answer with standalone object IDs;
- trajectory audit shows the standalone bridge, not built-in memory search.

## Parity with the private CAP workflow

The private system follows:

```text
sanitized evidence
  -> structured knowledge/procedure/event material
  -> routing/promotion gate
  -> bright/dark lookup discipline
  -> retrieval acceptance
```

The standalone module keeps that ordering while replacing private components:

| Private workflow component | Standalone equivalent |
|---|---|
| MemPalace raw archive | `evidence/<library>/<archive_id>/` |
| legacy semantic/procedural/event files | canonical `objects/knowledge`, `objects/procedure`, and `objects/event` files |
| pointer registry + root bright index | `ledgers/bright.jsonl` |
| full dark memory corpus | `ledgers/dark.jsonl` |
| OpenClaw memory index | bundled SQLite FTS, optional plugin index |
| memory facade/eval | `aml validate` plus recall assertions |
| Neat Freak consistency pass | intentionally omitted from v0.1 |

The v0.1 scope intentionally leaves LLM extraction, vector databases, and
cross-document consistency checking as plugins or later work.

## Cross-platform runtime tests

The same negative-control protocol is applied to agent runtimes:

```text
session A writes a unique fact and procedure
  -> standalone archive
  -> fresh session without tools returns UNKNOWN
  -> fresh Skill-enabled session recalls with object IDs
```

Current results:

- OpenClaw: full model E2E PASS;
- Codex CLI: full model E2E PASS, including native marketplace/plugin install;
- Kimi Code CLI: native plugin, Skill discovery, real export parsing, and
  deterministic ledger E2E PASS; live model E2E BLOCKED by the test account's
  current billing-cycle usage limit.

Detailed installation and source-boundary rules are in
[`CROSS_PLATFORM_ADAPTERS.md`](CROSS_PLATFORM_ADAPTERS.md).
