---
name: agent-memory-ledger
description: Archive or recall durable facts, procedures, decisions, and events across Kimi Code CLI sessions using a standalone evidence/object/bright-dark-ledger workspace.
---

# Agent Memory Ledger for Kimi Code CLI

Use this skill when the user asks Kimi Code to remember, archive, or recall
durable information across separate sessions.

## Boundary

The standalone ledger is the only memory write target. Do not store the test
facts in `AGENTS.md`, another startup file, or another agent memory system. The
adapter reads only visible `user` and `assistant` content from Kimi's
`context.jsonl` or an exported session ZIP. It ignores `_system_prompt`,
checkpoints, usage records, encrypted thinking, tool calls, tool results,
`wire.jsonl`, and logs.

Install this plugin with:

```bash
kimi plugin install /path/to/integrations/kimi-plugin
```

Then install the core package once into the current repository:

```bash
KIMI_HOME="${KIMI_SHARE_DIR:-$HOME/.kimi}"
python "$KIMI_HOME/plugins/agent-memory-ledger/scripts/install.py" \
  --workspace "$PWD" \
  --source /path/to/agent_memory_ledger-0.1.0-py3-none-any.whl
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

```bash
"$PY" "$BRIDGE" archive \
  --workdir "$PWD" \
  --ledger "$LEDGER" \
  --promote-markers
```

Use `--promote-markers` only when the user explicitly supplied stable lines such
as `Fact:`, `Rule:`, `Procedure:`, `Decision:`, `事实：`, `规则：`, or `流程：`.

An exported ZIP can also be archived deterministically:

```bash
"$PY" "$BRIDGE" archive-zip \
  --export-zip /path/to/session.zip \
  --ledger "$LEDGER" \
  --promote-markers
```

## Recall

```bash
"$PY" "$BRIDGE" recall \
  --ledger "$LEDGER" \
  --query "<user question>" \
  --top-k 6
```

Answer only from returned active objects and include relevant `object_id` values.
Do not silently use previous Kimi sessions or another memory source.

## Validate

```bash
"$PY" "$BRIDGE" validate --ledger "$LEDGER"
```
