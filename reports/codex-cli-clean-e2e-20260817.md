# Codex CLI Clean Cross-Session E2E — 2026-08-17

## Verdict

**PASS** on Codex CLI `0.144.1`.

The tested contract was:

```text
fresh thread A
  -> repository-local Agent Memory Ledger Skill
  -> standalone evidence / objects / bright-dark ledgers
  -> no-tool negative-control thread
  -> fresh Skill-enabled recall thread
```

## Isolation

The final run used:

- a disposable Git repository under `/tmp`;
- a fresh `HOME` with no user-level Skills;
- the real `CODEX_HOME` only for existing Codex authentication and rollout storage;
- `--ignore-user-config`;
- `features.memories=false`;
- one repository-local `.agents/skills/agent-memory-ledger` Skill;
- a repository-local core virtual environment and `.portable-memory` ledger.

The strict run confirmed that no global `using-superpowers`, RTK, or other user
Skill was invoked. The only memory command in write/recall trajectories was the
Agent Memory Ledger bridge.

## Test memory

```text
Fact: The project codename is Argent-Codex-10-Sable.
Rule: Before a public release:
  1. run unit tests;
  2. build the wheel;
  3. run the platform-free clean-room smoke test.
```

## Thread A: archive

Codex explicitly invoked `$agent-memory-ledger`, executed the bridge, and
reported a completed archive.

Created objects:

- semantic: `mem_421f90ec1ad4cb075c63`;
- procedural: `mem_c6b59318114f7d0eda82`;
- decision event: `mem_8747e6a9f28000ea7077`;
- stable archive event: `mem_180b501d8b529198c3a0`.

The semantic and procedural objects were promoted into the bright ledger.

## Negative control

A separate read-only thread was forbidden from using tools, files, Skills,
startup files, previous threads, or memory systems. It returned exactly:

```text
UNKNOWN
```

It made zero command calls and did not contain the test marker.

## Fresh-thread recall

A third thread was allowed to use only Agent Memory Ledger. It returned:

- the exact codename;
- the three exact steps in the correct order;
- both supporting object IDs.

Trajectory inspection showed one runtime command:

```text
.agent-memory-ledger-adapter/codex_bridge.py recall ...
```

No other memory command was invoked.

## Incremental session snapshots

Archiving while thread A was still running captured two visible messages.
Archiving after the thread completed captured three visible messages. The
system correctly retained two evidence versions while keeping only one stable
archive-event object.

Final state:

- evidence archive versions: `2`;
- active structured objects: `4`;
- archive-event objects: `1`;
- bright entries: `2`;
- dark entries: `4`;
- validation: `ok: true`.

Repeating the completed snapshot produced the same archive ID, creation time,
and object IDs.

## Native Codex plugin distribution

The repository was also tested as a native Codex marketplace:

```bash
codex plugin marketplace add /path/to/agent-memory-ledger
codex plugin add agent-memory-ledger@agent-memory-ledger
```

The plugin was discovered, installed, and enabled in an isolated `CODEX_HOME`.
The installed cache contained:

- `.codex-plugin/plugin.json`;
- `skills/agent-memory-ledger/SKILL.md`;
- `scripts/install.py`;
- `scripts/codex_bridge.py`.

The Codex-specific installer now copies the bridge into the target repository
as `.agent-memory-ledger-adapter/codex_bridge.py`, so runtime commands are
stable whether the Skill came from `.agents/skills` or the plugin cache.

## Non-blocking observation

Codex emitted stale model-cache schema warnings during some runs. They did not
change command execution, final answers, or ledger validation.
