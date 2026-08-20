# Plugin API

Agent Memory Ledger keeps plugins deliberately small. A plugin is loaded with either `module:factory` or `path/to/plugin.py:factory`. The factory receives the resolved memory-workspace path and returns an object implementing the required methods.

## Extractor plugin

An extractor converts a sanitized `SessionBundle` into `MemoryDraft` objects.

```python
from pathlib import Path

from agent_memory_ledger.models import MemoryDraft, MemoryKind, SessionBundle


class MyExtractor:
    def extract(self, session: SessionBundle, library: str) -> list[MemoryDraft]:
        # Call an LLM, a local model, or deterministic application logic here.
        return [
            MemoryDraft(
                kind=MemoryKind.SEMANTIC,
                title="Example fact",
                content="A durable statement extracted from the session.",
                summary="A durable statement.",
                importance=80,
                confidence=0.9,
                tags=["example"],
                promote=True,
            )
        ]


def create_extractor(workspace: Path) -> MyExtractor:
    return MyExtractor()
```

Use it with:

```bash
aml --workspace ./memory ingest session.json \
  --extractor-plugin my_package.extractor:create_extractor
```

The extractor must not write ledgers, evidence, object files, or SQLite directly. It returns candidates; the core performs deterministic storage and validation.

## Semantic-index plugin

A semantic-index adapter wraps both the embedding implementation and the vector database. This avoids forcing the core to coordinate incompatible embedding clients, dimensions, and database SDKs.

```python
from pathlib import Path

from agent_memory_ledger.models import IndexRecord, SearchHit


class MySemanticIndex:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def upsert(self, records: list[IndexRecord]) -> None:
        # Embed record.text and upsert vectors keyed by record.object_id.
        ...

    def delete(self, object_ids: list[str]) -> None:
        ...

    def search(self, query: str, top_k: int = 10) -> list[SearchHit]:
        # Embed query, search the vector store, and return object IDs.
        return [
            SearchHit(
                object_id="mem_example",
                score=0.91,
                source="my-vector-store",
            )
        ]

    def rebuild(self, records: list[IndexRecord]) -> None:
        # Replace the derived index from canonical object records.
        ...

    def health(self) -> dict[str, object]:
        return {"status": "ok", "provider": "my-vector-store"}


def create_index(workspace: Path) -> MySemanticIndex:
    return MySemanticIndex(workspace)
```

Use it with:

```bash
aml --workspace ./memory search "query" \
  --semantic-plugin my_package.adapter:create_index

aml --workspace ./memory reindex \
  --semantic-plugin my_package.adapter:create_index
```

For a local file that has not been packaged:

```bash
aml --workspace ./memory search "query" \
  --semantic-plugin ./my_adapter.py:create_index
```

The repository includes a runnable standard-library example at
`examples/overlap_index_plugin.py`.

### Optional metadata filtering

Existing adapters remain compatible with the original `SemanticIndex`
interface. An adapter that can filter before ranking may additionally expose:

```python
def search_filtered(
    self,
    query: str,
    top_k: int = 10,
    *,
    metadata: dict[str, object],
) -> list[SearchHit]:
    ...
```

The core currently calls this extension with `{"promoted": True}` for
`search --scope bright`. A legacy adapter is oversampled and filtered while
canonical results are hydrated, so adding this method is optional. The bundled
Chroma adapter performs the filter inside its single collection.

## Adapter contract

A semantic adapter should satisfy these behaviors:

1. `upsert` is idempotent by `object_id`.
2. Re-upserting an object replaces stale text and metadata.
3. Search results return canonical `object_id` values.
4. Workspaces or namespaces do not leak into each other.
5. `rebuild` can recreate the complete index from records supplied by the core.
6. Database or network failure raises an exception; the core records it and keeps the archive successful.
7. The adapter never treats vector-store text as the canonical memory object.
8. The adapter indexes only the sanitized text supplied by the core.

## Packaging suggestion

The base package remains dependency-free. The bundled Chroma adapter is
installed only through `agent-memory-ledger[chroma]`; other database adapters
can stay outside the core package:

```text
agent-memory-ledger                 core package
agent-memory-ledger-qdrant          optional adapter
agent-memory-ledger-pgvector        optional adapter
```

This keeps the base install dependency-free and lets each adapter declare its own SDK and version constraints.
