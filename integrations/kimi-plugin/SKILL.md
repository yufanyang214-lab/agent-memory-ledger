---
name: agent-memory-ledger
description: Archive or recall durable facts, procedures, decisions, and events across Kimi Code CLI sessions using a standalone evidence/object/bright-dark-ledger workspace.
---

# Agent Memory Ledger for Kimi Code CLI

Use this skill when the user asks Kimi Code to remember, archive, or recall
durable information across separate sessions.

## Boundary

The standalone ledger is the canonical memory write target. Do not store memory
bodies in `AGENTS.md` or another startup file. When the operator explicitly
configures external-memory mode, a separate provider may receive only sanitized
evidence or derived objects after successful AML archival and must retain AML
archive/object provenance. The adapter reads only visible `user` and `assistant`
content from Kimi's `context.jsonl` or an exported session ZIP. It ignores
`_system_prompt`, checkpoints, usage records, encrypted thinking, tool calls,
tool results, `wire.jsonl`, and logs.

Install this plugin with:

```bash
kimi plugin install /path/to/integrations/kimi-plugin
```

Then install the core package once into the current repository:

```bash
KIMI_HOME="${KIMI_SHARE_DIR:-$HOME/.kimi}"
python "$KIMI_HOME/plugins/agent-memory-ledger/scripts/install.py" \
  --workspace "$PWD" \
  --source /path/to/agent_memory_ledger-0.1.1-py3-none-any.whl
```

Use these paths:

```bash
KIMI_HOME="${KIMI_SHARE_DIR:-$HOME/.kimi}"
PY="$PWD/.agent-memory-ledger-venv/bin/python"
BRIDGE="$KIMI_HOME/plugins/agent-memory-ledger/scripts/kimi_bridge.py"
LEDGER="$PWD/.portable-memory"
```

## Archive the current session

The bridge locates the newest Kimi session for the current working directory.
Pass `--session-id` when an exact session is known.

Archive only when the user affirmatively asks to archive the current session
with the configured trigger or an equivalent such as "archive", "archive this
session", “归档本会话”, or “按 AML 归档”. Questions, explanations, quotations,
hypotheticals, and negations about archiving do not trigger the workflow. First
extract durable `knowledge`, `procedure`, and `event` candidates into a JSON
file as described in `docs/AGENT_INSTRUCTIONS.md`. Use tags for finer subtypes
and canonical kind names only. Set `promote: true` only for the small set of
stable, high-value items that should be discoverable at startup.

```bash
"$PY" "$BRIDGE" archive \
  --workdir "$PWD" \
  --ledger "$LEDGER" \
  --candidate-file /path/to/memory-candidates.json
```

Every accepted active candidate is represented in the dark ledger. The bridge
uses candidate `promote` values to build the bright ledger. Add
`--promote-markers` only when the user explicitly supplied stable lines such as
`Fact:`, `Rule:`, `Procedure:`, `Decision:`, `事实：`, `规则：`, or `流程：`.
Never edit evidence, objects, ledgers, SQLite, or the journal directly. Run
validation after archival and report the archive, object, and promoted IDs.

An exported ZIP can also be archived deterministically:

```bash
"$PY" "$BRIDGE" archive-zip \
  --export-zip /path/to/session.zip \
  --ledger "$LEDGER" \
  --candidate-file /path/to/memory-candidates.json
```

## Recall

```bash
"$PY" "$BRIDGE" recall \
  --ledger "$LEDGER" \
  --query "<user question>" \
  --top-k 6
```

Answer only from returned active objects and include relevant `object_id` values.
Do not silently use previous Kimi sessions or another memory source. An
explicitly configured external provider may be used for deeper recall, but its
result must retain or resolve to AML archive/object provenance.

## Validate

```bash
"$PY" "$BRIDGE" validate --ledger "$LEDGER"
```
