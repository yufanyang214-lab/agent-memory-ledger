# Kimi Code CLI Compatibility Report — 2026-08-17

## Verdict

**Adapter and native-plugin compatibility: PASS.**

**Live model-driven cross-session E2E: BLOCKED by account quota, not by the
adapter.**

Runtime tested: Kimi Code CLI `1.31.0`.

## Native plugin installation

The plugin was installed into an isolated `KIMI_SHARE_DIR` with:

```bash
kimi plugin install /path/to/integrations/kimi-plugin
```

Kimi reported:

```text
Installed plugin 'agent-memory-ledger' v0.1.0
runtime: host=kimi-code, version=1.31.0
```

`kimi plugin list` and `kimi plugin info` both returned the installed plugin.
With a fresh `HOME`, Kimi's Skill resolver found exactly one external Skill
root—the isolated plugin directory—and discovered `agent-memory-ledger` from its
root `SKILL.md`.

## Real Kimi export format

A real Kimi invocation created a disposable local session. The model call was
rejected before an assistant response because the account had reached its usage
allowance for the current billing cycle. The local session identifier is omitted
from the public report.

Kimi still exported the session successfully. The ZIP contained:

```text
manifest.json
context.jsonl
wire.jsonl
logs/kimi.log
```

The adapter converted the real ZIP and direct local session path correctly:

- detected Kimi CLI version `1.31.0`;
- retained one visible user message;
- ignored Kimi internal records;
- resolved the session from the working-directory hash and session ID.

## Parser boundary

The adapter reads only:

- `manifest.json` metadata from an export ZIP;
- visible `user` and `assistant` content from `context.jsonl`.

It excludes:

- `_system_prompt`;
- `_checkpoint` and `_usage` records;
- synthetic `<system-reminder>` user messages;
- encrypted or plaintext `think` blocks;
- assistant tool-call structures;
- `tool` role results;
- `wire.jsonl`;
- diagnostic logs.

The deterministic tests for these exclusions passed.

## Platform-compatible archive and recall

A realistic synthetic Kimi `1.31.0` export ZIP was used because the live model
could not complete a response under the current quota.

Test memory:

```text
Fact: The project codename is Luna-Kimi-8-Oriole.
Rule: Before a public release:
  1. run unit tests;
  2. build the wheel;
  3. run the platform-free clean-room smoke test.
```

Results:

- archive status: `completed`;
- visible messages: `2`;
- active objects: `4`;
- promoted objects: `2`;
- validation: `ok: true`;
- semantic object: `mem_416461c1ae40d170ab99`;
- procedural object: `mem_c6b59318114f7d0eda82`;
- recall returned the exact codename and ordered procedure;
- internal system context, reasoning, tool output, wire data, and logs were absent
  from evidence and structured objects.

## What remains unverified

The missing test is specifically Kimi's model autonomously reading the installed
Skill, invoking `kimi_bridge.py`, and answering from the returned object IDs in a
fresh session. That requires a successful model call. The attempt failed with an
HTTP 403 usage-limit response before an assistant turn existed.

After the Kimi allowance refreshes, rerun the same three-session protocol used
for Codex:

```text
session A archive
  -> no-tool negative control
  -> fresh Skill-enabled recall
```

No code change is currently indicated by the quota failure.
