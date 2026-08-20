# Chroma semantic index

The bundled Chroma adapter is a derived recall index, not a new source of
truth. Evidence, structured objects, ledgers, and the audit journal remain
readable without Chroma and can rebuild the collection at any time.

## Install and enable

The base package has no third-party runtime dependencies. Install the optional
profile only when semantic retrieval is wanted:

```bash
pip install "agent-memory-ledger[chroma]"
aml --workspace .portable-memory init --semantic-provider chroma
```

The selection is stored in `workspace.json`. It is then applied automatically
to `ingest`, `search`, `promote`, `retract`, `reindex`, and `validate`.

```bash
aml --workspace .portable-memory ingest session.json
aml --workspace .portable-memory search "会话存档的名字" --scope active
aml --workspace .portable-memory search "启动时优先加载什么" --scope bright
aml --workspace .portable-memory reindex
```

Use `aml init --semantic-provider none` to remove the persisted selection. Use
`--semantic-provider none` on another command for a one-command override. A
custom `--semantic-plugin` also overrides the persisted provider, so an Agent
with Qdrant, pgvector, or a platform-owned memory index does not need Chroma.

## Chinese embedding default

The adapter sends explicit vectors to Chroma and never invokes Chroma's default
English `all-MiniLM-L6-v2` embedding. Its default is
`BAAI/bge-small-zh-v1.5` through FastEmbed:

- Chinese retrieval model;
- 512-dimensional normalized vectors;
- ONNX CPU inference;
- approximately 54.6 MB compressed and 90 MB installed;
- no PyTorch, CUDA, or GPU requirement.

The model is downloaded lazily on the first upsert or query. Override its cache
or model with:

```bash
export AML_EMBEDDING_CACHE=/var/cache/agent-memory-ledger
export AML_EMBEDDING_MODEL=jinaai/jina-embeddings-v2-base-zh
aml --workspace .portable-memory reindex
```

The replacement must be a model supported by the installed FastEmbed version.
Changing models requires `reindex`; the adapter rejects a collection whose
recorded model/schema differs instead of mixing incompatible vectors.

## Bright and active recall

One collection stores every active object. Scalar metadata includes `kind`,
`importance`, `promoted`, and `source_platform`.

- `--scope active` searches all active structured objects. This is normal
  dark-ledger recall; retracted objects are never returned.
- `--scope bright` applies `promoted=true` inside Chroma before vector ranking
  and applies the same filter to SQLite FTS.

SQLite and Chroma results are combined with reciprocal-rank fusion. Final hits
are hydrated from canonical object files, so stale or deleted vector payloads
cannot replace memory content.

## Embedded and HTTP modes

Embedded mode is the zero-configuration default:

```bash
export AML_CHROMA_MODE=embedded
# Default path: WORKSPACE/state/chroma
```

Every local operation takes `state/workspace.lock`. This safely serializes
independent CLI processes, and canonical records are refreshed while holding
the lock so a delayed upsert cannot overwrite newer promotion metadata.

Chroma's `PersistentClient` is not process-safe for multiple long-lived
clients. In particular, one process can observe a new row count while its
loaded vector segment still returns old rankings. The adapter tracks
`state/chroma.generation`; if another process changes embedded Chroma, a
long-lived client refuses the vector operation and the core falls back to
SQLite rather than returning a known-stale result.

For multiple long-lived Agents, run one Chroma server and use HTTP clients:

```bash
chroma run --path /var/lib/chroma --host 127.0.0.1 --port 8000

export AML_CHROMA_MODE=http
export AML_CHROMA_HOST=127.0.0.1
export AML_CHROMA_PORT=8000
aml --workspace .portable-memory validate
```

Optional HTTP settings are:

```text
AML_CHROMA_SSL=true|false
AML_CHROMA_HEADERS_JSON={"Authorization":"Bearer ..."}
AML_CHROMA_TENANT=default_tenant
AML_CHROMA_DATABASE=default_database
AML_CHROMA_COLLECTION=agent_memory_ledger_v1
```

Do not persist credentials in `workspace.json`; keep them in the Agent's secret
environment. Chroma telemetry and ONNX Runtime telemetry are disabled by
default by this adapter.

## Failure behavior

Chroma is optional at runtime. A missing package, unavailable model download,
server outage, schema mismatch, or stale embedded client is journaled as a
semantic-index failure. Ingest still archives and stores canonical objects,
and search still uses SQLite FTS. Run `reindex` after the optional service or
dependency is restored.

## Reproduce the Chinese check

```bash
python scripts/chinese_retrieval_eval.py \
  --cache-dir /var/cache/agent-memory-ledger
```

The fixed eight-query check reports per-query predictions, Top-1 accuracy,
bright-filter correctness, index time, and median query latency. It is a smoke
evaluation, not a general benchmark; model quality should be evaluated again
on each deployment's own memory corpus.
