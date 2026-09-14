---
name: "agent-memory-ledger"
description: "Archive an OpenClaw session into a standalone evidence/object/dual-ledger memory workspace, or recall prior sessions without using OpenClaw built-in memory."
---

# Agent Memory Ledger bridge

Use this skill when the user asks to remember, archive, or recall durable facts,
procedures, decisions, or events across OpenClaw sessions.

## Boundary

The standalone ledger directory is the canonical memory write target. This
bridge must not write `MEMORY.md`, `memory/`, OpenClaw Memory SQLite, or another
platform-owned store. When the operator explicitly configures external-memory
mode, a separate provider such as MemPalace may receive only sanitized evidence
or derived objects after successful AML archival; it must retain AML
archive/object provenance.

Install the core package into a workspace-local virtual environment once:

```bash
python skills/agent-memory-ledger/scripts/install.py \
  --source /path/to/agent_memory_ledger-0.1.1-py3-none-any.whl
```

Then use these local paths:

```bash
PY="$PWD/.agent-memory-ledger-venv/bin/python"
BRIDGE="$PWD/skills/agent-memory-ledger/scripts/openclaw_bridge.py"
LEDGER="$PWD/.portable-memory"
```

## Archive the current session

Resolve the current agent id and exact session key from runtime context. Then:

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
  --agent <agent-id> \
  --session-key <exact-session-key> \
  --ledger "$LEDGER" \
  --candidate-file /path/to/memory-candidates.json
```

Every accepted active candidate is represented in the dark ledger. The bridge
uses candidate `promote` values to build the bright ledger. Add
`--promote-markers` only when the user explicitly supplies stable lines such as
`Fact:`, `Rule:`, `Procedure:`, `Decision:`, `事实：`, `规则：`, or `流程：`.
Never edit evidence, objects, ledgers, SQLite, or the journal directly. Run
validation after archival and report the archive, object, and promoted IDs.

The bridge exports the OpenClaw trajectory, keeps only visible user/assistant
text, converts it to the generic session schema, and calls the standalone core.
Never archive hidden reasoning, tool arguments, tool outputs, system prompts, or
provider metadata.

## Recall

```bash
"$PY" "$BRIDGE" recall \
  --ledger "$LEDGER" \
  --query "<user question>" \
  --top-k 6
```

Answer only from returned active objects. Include relevant `object_id` values so
the result is auditable. Do not silently fall back to OpenClaw memory search.
An explicitly configured external provider may be used for deeper recall, but
its result must retain or resolve to AML archive/object provenance.

## Validation

```bash
"$PY" "$BRIDGE" validate --ledger "$LEDGER"
```

Completion requires `ok: true`, at least one evidence manifest, structured
objects, both ledgers, and successful cross-process recall.
