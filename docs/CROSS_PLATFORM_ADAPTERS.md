# Cross-Platform Agent Adapters

Agent Memory Ledger keeps storage and governance platform-neutral. Platform
adapters are deliberately thin: they convert a runtime session into the generic
`SessionBundle`, then call the same core archive and recall API.

## Support matrix

| Runtime | Installation surface | Session source | Current validation |
|---|---|---|---|
| Generic CLI | Python wheel | JSON / JSONL | Full clean-room PASS |
| OpenClaw | workspace Skill | trajectory export | Full model E2E PASS |
| Codex CLI | `.agents/skills` or native plugin | rollout JSONL | Full model E2E PASS |
| Kimi Code CLI | native plugin | local `context.jsonl` or export ZIP | Plugin/parser/ledger PASS; live model E2E blocked by account quota |

## Codex CLI

### Option A: repository-local Skill

```bash
mkdir -p .agents/skills
cp -a /path/to/agent-memory-ledger/integrations/codex-skill \
  .agents/skills/agent-memory-ledger
```

Start a new Codex session and invoke:

```text
$agent-memory-ledger
```

The Skill resolves its own directory, installs the core wheel, and copies the
bridge into:

```text
.agent-memory-ledger-adapter/codex_bridge.py
```

Runtime state is then independent of the Skill's original installation path.

### Option B: native Codex plugin

The repository root is a Codex marketplace because it contains:

```text
.agents/plugins/marketplace.json
```

Install with:

```bash
codex plugin marketplace add /path/to/agent-memory-ledger
codex plugin add agent-memory-ledger@agent-memory-ledger
```

Start a new Codex session after installation. Invoke `$agent-memory-ledger` and
provide the core wheel path when setup is requested.

### Session boundary

`codex_bridge.py` locates the current rollout using `CODEX_THREAD_ID` and reads
only visible `event_msg` records of type:

```text
user_message
agent_message
```

It excludes developer/system response items, hidden reasoning, function calls,
command output, plugin recommendations, and model metadata.

## Kimi Code CLI

Install the native plugin:

```bash
kimi plugin install /path/to/agent-memory-ledger/integrations/kimi-plugin
```

Install the core into a repository:

```bash
KIMI_HOME="${KIMI_SHARE_DIR:-$HOME/.kimi}"
python "$KIMI_HOME/plugins/agent-memory-ledger/scripts/install.py" \
  --workspace "$PWD" \
  --source /path/to/agent_memory_ledger-0.1.0-py3-none-any.whl
```

The plugin root contains `SKILL.md`, so Kimi discovers it as a Skill. Archive
can use either the current local session or an explicit export ZIP:

```bash
KIMI_HOME="${KIMI_SHARE_DIR:-$HOME/.kimi}"
PY="$PWD/.agent-memory-ledger-venv/bin/python"
BRIDGE="$KIMI_HOME/plugins/agent-memory-ledger/scripts/kimi_bridge.py"

"$PY" "$BRIDGE" archive \
  --workdir "$PWD" \
  --ledger "$PWD/.portable-memory" \
  --promote-markers

"$PY" "$BRIDGE" archive-zip \
  --export-zip /path/to/session.zip \
  --ledger "$PWD/.portable-memory" \
  --promote-markers
```

### Session boundary

The Kimi adapter reads only visible `user` and `assistant` text from
`context.jsonl`. It does not ingest `wire.jsonl`, logs, system prompts,
checkpoints, usage records, thinking blocks, tool calls, or tool results.

## Shared recall contract

Every runtime ultimately calls the same interface:

```bash
aml --workspace .portable-memory search "<query>"
```

or its thin bridge equivalent. Final agent answers should include returned
`object_id` values so cross-session recall remains auditable.

## Test reports

- [`cross-platform-validation-20260817.md`](../reports/cross-platform-validation-20260817.md)
- [`openclaw-clean-agent-e2e-20260817.md`](../reports/openclaw-clean-agent-e2e-20260817.md)
- [`codex-cli-clean-e2e-20260817.md`](../reports/codex-cli-clean-e2e-20260817.md)
- [`kimi-code-cli-compatibility-20260817.md`](../reports/kimi-code-cli-compatibility-20260817.md)
