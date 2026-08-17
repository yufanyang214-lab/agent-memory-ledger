---
name: agent-memory-ledger
description: Archive or recall durable facts, procedures, decisions, and events across Codex CLI sessions using a standalone evidence/object/bright-dark-ledger workspace rather than Codex memories.
---

# Agent Memory Ledger for Codex CLI

Use this skill when the user asks Codex to remember, archive, or recall durable
information across separate Codex sessions.

## Boundary

The standalone ledger is the only memory write target. Do not enable or call
Codex experimental memories, and do not copy memory into `AGENTS.md` or other
startup instructions. The adapter reads only visible user and assistant events
from the current rollout. It excludes developer instructions, hidden reasoning,
tool calls, command output, plugin recommendations, and model metadata.

Resolve `SKILL_DIR` as the absolute directory containing this loaded
`SKILL.md`. Codex exposes the skill location in its skill metadata; do not
assume that the skill came from `.agents/skills`, because a plugin installation
uses the Codex plugin cache.

Install the core package and stable repository-local bridge once:

```bash
python "$SKILL_DIR/scripts/install.py" \
  --workspace "$PWD" \
  --source /path/to/agent_memory_ledger-0.1.0-py3-none-any.whl
```

Use these paths from the repository root:

```bash
PY="$PWD/.agent-memory-ledger-venv/bin/python"
BRIDGE="$PWD/.agent-memory-ledger-adapter/codex_bridge.py"
LEDGER="$PWD/.portable-memory"
```

## Archive the current Codex session

`CODEX_THREAD_ID` is provided to shell commands by Codex CLI, so the bridge can
locate the current rollout without asking the user for a session path.

```bash
"$PY" "$BRIDGE" archive \
  --ledger "$LEDGER" \
  --promote-markers
```

Use `--promote-markers` only when the user explicitly supplied stable lines such
as `Fact:`, `Rule:`, `Procedure:`, `Decision:`, `事实：`, `规则：`, or `流程：`.
It promotes extracted semantic/procedural objects; ordinary session events stay
in the dark ledger.

## Recall

```bash
"$PY" "$BRIDGE" recall \
  --ledger "$LEDGER" \
  --query "<user question>" \
  --top-k 6
```

Answer only from returned active objects and include relevant `object_id` values.
Do not silently fall back to session history, Codex memories, or startup files.

## Validate

```bash
"$PY" "$BRIDGE" validate --ledger "$LEDGER"
```

Completion requires `ok: true`, evidence, structured objects, both ledgers, and
successful recall from a fresh Codex thread.
