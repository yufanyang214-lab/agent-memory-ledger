# Cross-Platform Validation Summary — 2026-08-17

## Overall conclusion

Agent Memory Ledger's platform-neutral core and thin-adapter design is validated
across three agent runtimes.

| Runtime | Native installation | Real session conversion | No-tool negative control | Fresh-session model recall | Verdict |
|---|---:|---:|---:|---:|---|
| OpenClaw | PASS | PASS | PASS | PASS | Full E2E PASS |
| Codex CLI 0.144.1 | PASS | PASS | PASS | PASS | Full E2E PASS |
| Kimi Code CLI 1.31.0 | PASS | PASS | Not run | Blocked before assistant turn | Adapter PASS; live model E2E blocked by account quota |

## Shared core

- deterministic Python tests: `17/17` PASS;
- platform-free wheel clean room: PASS;
- no platform-specific directories created by the core;
- evidence, structured objects, bright/dark ledgers, SQLite, audit journal, and
  cross-process recall: PASS;
- raw transcript text remains in evidence rather than archive-event objects;
- runtime dependencies: none.

## Codex CLI

Codex was tested with a fresh `HOME`, `--ignore-user-config`, and experimental
memories disabled. A unique fact and procedure were written in thread A. A
separate no-tool thread returned exactly `UNKNOWN`. A third thread invoked only
Agent Memory Ledger and returned the exact fact, ordered procedure, and source
object IDs.

The repository also passed native marketplace installation from an extracted
sdist:

```bash
codex plugin marketplace add /path/to/agent-memory-ledger
codex plugin add agent-memory-ledger@agent-memory-ledger
```

## Kimi Code CLI

Kimi native plugin installation, runtime metadata, isolated Skill discovery,
real `context.jsonl` location, real export ZIP conversion, and deterministic
archive/recall all passed.

The live model comparison could not proceed because Kimi returned an HTTP 403
usage-limit response for the current billing cycle before producing an
assistant turn. The session was still exportable and validated against the
adapter. This result is recorded as an external blocker, not a live E2E pass.

## Release-artifact validation

From the built sdist rather than the working tree:

- Codex marketplace add: PASS;
- Codex plugin add and enable: PASS;
- Kimi plugin install/list/info: PASS;
- Kimi core installer from the built wheel: PASS;
- required Skill, bridge, installer, plugin manifest, marketplace manifest, and
  reports present: PASS.

The remaining test is to repeat Kimi's model-driven three-session protocol after
its account allowance refreshes.
