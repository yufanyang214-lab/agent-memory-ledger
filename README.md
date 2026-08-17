# Agent Memory Ledger

A small, file-first memory layer for AI agents.

It takes a session export, preserves a sanitized evidence copy, extracts structured memory objects, and maintains two simple ledgers:

- **Dark ledger**: every structured memory object and its provenance.
- **Bright ledger**: only the small set of promoted entries an agent should discover quickly.

The project is intentionally independent of agent runtimes and memory databases. It runs with the Python standard library, uses SQLite FTS for built-in search, and exposes optional plugins for LLM extraction and vector search.

## Why

Agent platforms often mix four different concerns:

1. raw conversation history;
2. durable knowledge and procedures;
3. startup-visible memory;
4. a particular vector database or vendor memory API.

Agent Memory Ledger separates them:

```text
Session JSON / JSONL
        │
        ▼
Sanitize + classify
        │
        ├── evidence/primary|auxiliary/    immutable session evidence
        │
        ▼
Extractor (built-in or plugin)
        │
        ├── objects/semantic/
        ├── objects/procedural/
        └── objects/event/
        │
        ├── ledgers/dark.jsonl             all objects
        └── ledgers/bright.jsonl           promoted entry points
        │
        ├── SQLite FTS                      built-in, rebuildable search
        └── SemanticIndex plugin            optional vector database
```

The filesystem is the source of truth. SQLite and vector indexes are derived and replaceable.

## Features

- Generic JSON and JSONL session input.
- Primary, auxiliary, and ignored session routing.
- Conservative secret redaction before persistence.
- `semantic`, `procedural`, and `event` memory objects.
- Deterministic object IDs and idempotent re-ingestion.
- Human-readable evidence manifests and transcripts.
- Bright/dark ledger snapshots plus an append-only audit journal.
- SQLite FTS5 search with no external service.
- Optional extractor and semantic-index plugins.
- Retraction without deleting original evidence.
- Workspace consistency validation.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .

aml --workspace .demo-memory init
aml --workspace .demo-memory ingest examples/session.json
aml --workspace .demo-memory search "filesystem source of truth"
aml --workspace .demo-memory list --bright
aml --workspace .demo-memory validate
```

## Agent runtime adapters

The core remains platform-neutral. Thin optional adapters live in
[`integrations`](integrations):

- OpenClaw workspace Skill;
- Codex CLI repository Skill and native plugin;
- Kimi Code CLI native plugin.

See [`docs/CROSS_PLATFORM_ADAPTERS.md`](docs/CROSS_PLATFORM_ADAPTERS.md) for
installation and session-boundary details.
The combined validation verdict is in
[`reports/cross-platform-validation-20260817.md`](reports/cross-platform-validation-20260817.md).

### OpenClaw

The OpenClaw adapter lives in
[`integrations/openclaw-skill`](integrations/openclaw-skill).

```bash
# Build or download the core wheel first.
openclaw skills install ./integrations/openclaw-skill \
  --as agent-memory-ledger

python skills/agent-memory-ledger/scripts/install.py \
  --source /path/to/agent_memory_ledger-0.1.0-py3-none-any.whl
```

The Skill exports a trajectory, retains only visible user/assistant text, and
calls the standalone ledger. It does not write platform-owned memory files or
query the built-in memory index. Archive and recall stay explicit and auditable.

The tested cross-session flow is documented in
[`docs/TEST_PLAN.md`](docs/TEST_PLAN.md) and
[`reports/openclaw-clean-agent-e2e-20260817.md`](reports/openclaw-clean-agent-e2e-20260817.md).

### Codex CLI

Install as a repository-local Skill:

```bash
mkdir -p .agents/skills
cp -a /path/to/agent-memory-ledger/integrations/codex-skill \
  .agents/skills/agent-memory-ledger
```

Or install through Codex's native plugin marketplace:

```bash
codex plugin marketplace add /path/to/agent-memory-ledger
codex plugin add agent-memory-ledger@agent-memory-ledger
```

The strict clean-thread E2E report is
[`reports/codex-cli-clean-e2e-20260817.md`](reports/codex-cli-clean-e2e-20260817.md).

### Kimi Code CLI

```bash
kimi plugin install /path/to/agent-memory-ledger/integrations/kimi-plugin
```

Native plugin installation, Skill discovery, real session export parsing, and
standalone archive/recall all pass. The current live model E2E is recorded as
blocked by the test account's billing-cycle usage limit, not as a module pass.
See
[`reports/kimi-code-cli-compatibility-20260817.md`](reports/kimi-code-cli-compatibility-20260817.md).

The example creates a workspace like this:

```text
.demo-memory/
├── evidence/
│   ├── primary/
│   └── auxiliary/
├── objects/
│   ├── semantic/
│   ├── procedural/
│   └── event/
├── ledgers/
│   ├── bright.jsonl
│   └── dark.jsonl
├── state/
│   ├── catalog.sqlite3
│   └── journal.jsonl
└── workspace.json
```

## Session format

A session is a JSON object with a stable ID, a source name, and messages:

```json
{
  "session_id": "demo-001",
  "source": "my-agent",
  "session_type": "conversation",
  "messages": [
    {"role": "user", "content": "Design a portable memory layer."},
    {"role": "assistant", "content": "Rule: the filesystem is the source of truth."}
  ]
}
```

A plain JSON array of messages and line-delimited JSON messages are also accepted.

For deterministic pipelines, a producer may attach explicit `memory_candidates`:

```json
{
  "memory_candidates": [
    {
      "kind": "semantic",
      "title": "Storage invariant",
      "content": "Memory objects on disk are canonical; indexes are rebuildable.",
      "importance": 90,
      "promote": true,
      "aliases": ["source of truth"]
    }
  ]
}
```

The built-in extractor also recognizes lines prefixed with markers such as `Fact:`, `Rule:`, `Event:`, `结论：`, `流程：`, and `完成：`. It is deliberately modest. A production deployment can replace it with an LLM extractor plugin without changing storage or ledgers.

## Commands

```text
aml init
aml ingest SESSION_FILE [--library auto|primary|auxiliary|ignored]
aml search QUERY [--top-k N]
aml list [--kind semantic|procedural|event] [--bright]
aml show OBJECT_ID
aml promote OBJECT_ID [--reason TEXT]
aml retract OBJECT_ID --reason TEXT
aml reindex --semantic-plugin module:factory
aml validate
```

All commands accept `--workspace PATH`. `AML_WORKSPACE` may also set the default workspace.

## Plugins

Two small interfaces are public:

- `MemoryExtractor`: sanitized session → structured memory candidates.
- `SemanticIndex`: text records and query text → optional semantic retrieval.

A semantic adapter owns its embedding model and vector database. The core only sends sanitized records, receives object IDs, and hydrates final results from the canonical filesystem objects.

```bash
# Installed adapter package
aml --workspace ./memory \
  search "deployment rollback" \
  --semantic-plugin my_package.adapter:create_index

# One local adapter file; no packaging required
aml --workspace ./memory \
  search "deployment rollback" \
  --semantic-plugin ./my_adapter.py:create_index
```

A runnable standard-library example is included at
[`examples/overlap_index_plugin.py`](examples/overlap_index_plugin.py).
See [`docs/PLUGIN_API.md`](docs/PLUGIN_API.md).

## Design rules

- Raw evidence is not the same thing as durable memory.
- Auxiliary machine sessions are preserved without polluting default recall.
- Structured objects never need the original agent platform to be useful.
- Bright ledger entries are pointers, not duplicate copies of memory content.
- A vector database is an optional derived index, never the source of truth.
- Optional index failures do not block archiving.
- Retraction changes recall state while preserving evidence and audit history.

## Scope

This is not a hosted memory platform, an agent framework, or a vector database. Version 0.1 focuses on one useful loop:

```text
session → evidence → structured objects → dual ledgers → recall
```

Adapters for individual agent platforms and databases can live in separate packages.

## Development and clean-room testing

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
python scripts/clean_room_smoke.py
```

The clean-room smoke test builds a wheel, installs it into a fresh virtual
environment with a new home directory, and runs from outside the repository. It
verifies evidence archiving, structured objects, both ledgers, SQLite recall,
process-restart persistence, and a local semantic-index plugin. It also fails if
platform-specific memory directories are created.

## Provenance

The design was distilled from a private Conversation Archiving Protocol and a bright/dark memory-ledger workflow. This repository is a clean standalone implementation: it does not import code or runtime state from any agent platform or third-party memory system.

## 中文说明

这是一个刻意保持轻量的 Agent 记忆模块：输入任意平台导出的 session，保存脱敏证据，提炼 `semantic / procedural / event` 三类对象，并生成明账与暗账。默认只依赖文件系统和 SQLite；向量库、Embedding 和 LLM 提炼器都通过插件接入，因此不会绑定某个 Agent 平台或记忆系统。

## License

MIT
