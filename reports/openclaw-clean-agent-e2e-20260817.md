# OpenClaw Clean-Agent E2E Report — 2026-08-17

## Verdict

**PASS** for the v0.1 integration contract:

```text
session A -> standalone archive -> fresh session B -> audited recall
```

## Environment

- Disposable OpenClaw agent and workspace under `/tmp`.
- No workspace `memory/`, `MEMORY.md`, or `DREAMS.md` before or after the test.
- Core installed from a built wheel into a workspace-local virtual environment.
- Adapter installed as a workspace-local Skill.
- Production Gateway was not restarted; local embedded agent execution was used
  because the running Gateway did not hot-load the newly registered disposable
  agent.

## Test memory

- Unique codename: `Nacre-7Q-BlueHeron`.
- Required release order:
  1. run unit tests;
  2. build the wheel;
  3. run the clean-room smoke test.

## Results

### Baseline

- Built-in memory search for the unique codename: zero results.
- Workspace official memory paths: absent.

### Session A archive

- Agent read the installed Skill and executed the bridge archive command.
- One primary evidence archive was created.
- Four active objects were created:
  - one semantic fact;
  - one procedural rule;
  - one decision event;
  - one concise archive event.
- Semantic and procedural objects were promoted into the bright ledger.
- Standalone validation returned `ok: true`.

### Negative control

A different session was forbidden from using tools, files, skills, session
history, or memory. It returned exactly:

```text
UNKNOWN
```

It made zero tool calls and did not contain the unique codename.

### Skill recall

A third, fresh session was allowed to use only Agent Memory Ledger. It returned:

- the exact codename;
- the exact three-step order;
- the semantic and procedural object IDs.

All six content assertions passed.

### Independence audit

- Final built-in memory search for the unique codename: zero results.
- The only automatically indexed built-in file was the default template
  `USER.md`; it did not contain the test fact.
- Recall trajectory invoked `openclaw_bridge.py recall`.
- Recall trajectory did not invoke `memory_search` or `openclaw memory search`.

### Replay and idempotence

The embedded agent runtime replayed the archive/recall shell calls in its active
trajectory. The ledger remained stable:

- same archive ID on repeat;
- same archive creation timestamp;
- same object IDs;
- one archive total;
- four objects total;
- validation still passed.

This is useful evidence that deterministic IDs and idempotent upserts survive an
actual agent-runtime replay, not only a unit test.

### Evidence/structure separation fix

Initial E2E inspection found that the generated archive-event object copied a
recent-context preview. That violated the private CAP rule that raw conversation
text belongs only in evidence. The implementation was corrected and retested.
The final archive event now contains only:

```text
Archived 4 visible messages from openclaw as primary.
Raw conversation text remains in the evidence layer.
```

The unique codename and raw prompt are absent from that archive-event object.

## Automated coverage at closeout

- Unit/integration tests: 11 passed.
- Platform-free clean-room smoke: passed.
- Clean-room assertions include evidence, objects, both ledgers, SQLite,
  cross-process recall, semantic plugin recall, and raw-text separation.
- Workspace-local installer smoke: passed.
- Wheel installation: passed.

## Remaining non-blocking gaps

- The default extractor is deliberately conservative and marker-based; natural
  language extraction should use a plugin.
- Supersession/conflict resolution is manual (`retract` + replacement) in v0.1.
- No automatic session-end hook is provided yet; archive is explicit.
- The adapter E2E requires a configured OpenClaw model and therefore is not part
  of public unauthenticated CI. The deterministic bridge conversion test is in CI.
