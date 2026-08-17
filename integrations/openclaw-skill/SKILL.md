---
name: "agent-memory-ledger"
description: "Archive an OpenClaw session into a standalone evidence/object/dual-ledger memory workspace, or recall prior sessions without using OpenClaw built-in memory."
---

# Agent Memory Ledger bridge

Use this skill when the user asks to remember, archive, or recall durable facts,
procedures, decisions, or events across OpenClaw sessions.

## Boundary

This bridge must not write `MEMORY.md`, `memory/`, OpenClaw Memory SQLite,
MemPalace, or any platform-owned memory store. The standalone ledger directory
is the only write target.

Install the core package into a workspace-local virtual environment once:

```bash
python skills/agent-memory-ledger/scripts/install.py \
  --source /path/to/agent_memory_ledger-0.1.0-py3-none-any.whl
```

Then use these local paths:

```bash
PY="$PWD/.agent-memory-ledger-venv/bin/python"
BRIDGE="$PWD/skills/agent-memory-ledger/scripts/openclaw_bridge.py"
LEDGER="$PWD/.portable-memory"
```

## Archive the current session

Resolve the current agent id and exact session key from runtime context. Then:

```bash
"$PY" "$BRIDGE" archive \
  --agent <agent-id> \
  --session-key <exact-session-key> \
  --ledger "$LEDGER" \
  --promote-markers
```

`--promote-markers` is appropriate only when the user explicitly supplies
stable lines such as `Fact:`, `Rule:`, `Procedure:`, `Decision:`, `事实：`,
`规则：`, or `流程：`. It promotes extracted semantic/procedural objects into
the bright ledger; ordinary session events remain dark-ledger entries.

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

## Validation

```bash
"$PY" "$BRIDGE" validate --ledger "$LEDGER"
```

Completion requires `ok: true`, at least one evidence manifest, structured
objects, both ledgers, and successful cross-process recall.
