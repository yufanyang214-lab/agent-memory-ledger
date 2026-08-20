"""First-party Chroma semantic index with multilingual retrieval defaults.

Chroma and FastEmbed are optional imports. Install them with
``pip install 'agent-memory-ledger[chroma]'``. Explicit embeddings are always
supplied, so Chroma's English-only default embedding function is never used.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import IndexRecord, SearchHit
from .store import WorkspaceStore

# The ledger contains user memory. Keep both optional local runtimes quiet by
# default, while respecting an explicit environment choice made by the caller.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_COLLECTION = "agent_memory_ledger_v1"
COLLECTION_SCHEMA_VERSION = 1


class TextEmbedder(Protocol):
    """Small injectable boundary used by the Chroma adapter and its tests."""

    model_id: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedTextEmbedder:
    """Lightweight ONNX embedding wrapper with a Chinese retrieval default."""

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        *,
        cache_dir: str | None = None,
        batch_size: int = 32,
        threads: int | None = None,
    ):
        self.model_id = model_name
        self.cache_dir = cache_dir
        self.batch_size = max(1, int(batch_size))
        self.threads = threads
        self._model: Any | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        values = self._load_model().passage_embed(
            texts,
            batch_size=self.batch_size,
        )
        return [self._normalize(row) for row in values]

    def embed_query(self, text: str) -> list[float]:
        values = self._load_model().query_embed(
            [text],
            batch_size=self.batch_size,
        )
        return self._normalize(next(iter(values)))

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "FastEmbed is not installed; run "
                "pip install 'agent-memory-ledger[chroma]'"
            ) from exc
        self._model = TextEmbedding(
            model_name=self.model_id,
            cache_dir=self.cache_dir,
            threads=self.threads,
            providers=["CPUExecutionProvider"],
        )
        return self._model

    @staticmethod
    def _normalize(values: Any) -> list[float]:
        vector = [float(value) for value in values]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            raise ValueError("embedding model returned a zero vector")
        return [value / norm for value in vector]


class EmbeddedChromaChangedError(RuntimeError):
    """Raised instead of returning a known-stale embedded vector index."""


@dataclass
class _EmbeddedClientState:
    client: Any
    generation: int
    pid: int


_EMBEDDED_CLIENTS: dict[str, _EmbeddedClientState] = {}
_EMBEDDED_CLIENTS_LOCK = threading.RLock()


class ChromaSemanticIndex:
    """Chroma-backed derived index for all active memory objects.

    A single collection stores both bright and non-promoted objects. Bright
    recall uses a Chroma metadata filter before vector ranking.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        embedder: TextEmbedder | None = None,
        mode: str = "embedded",
        path: str | Path | None = None,
        collection_name: str = DEFAULT_COLLECTION,
        host: str = "localhost",
        port: int = 8000,
        ssl: bool = False,
        headers: dict[str, str] | None = None,
        tenant: str = "default_tenant",
        database: str = "default_database",
    ):
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"embedded", "http"}:
            raise ValueError("Chroma mode must be 'embedded' or 'http'")
        if not collection_name.strip():
            raise ValueError("Chroma collection name must not be empty")
        self.store = WorkspaceStore(workspace)
        self.embedder = embedder or FastEmbedTextEmbedder()
        self.mode = normalized_mode
        self.path = self._resolve_path(path)
        self.collection_name = collection_name.strip()
        self.host = host
        self.port = int(port)
        self.ssl = bool(ssl)
        self.headers = dict(headers or {})
        self.tenant = tenant
        self.database = database
        self._client: Any | None = None
        self._observed_generation: int | None = None
        self._generation_path = self.store.state_dir / "chroma.generation"

    @property
    def model_id(self) -> str:
        model_id = str(getattr(self.embedder, "model_id", "")).strip()
        if not model_id:
            raise ValueError("embedding adapter must expose a non-empty model_id")
        return model_id

    def upsert(self, records: list[IndexRecord]) -> None:
        if not records:
            return
        with self._operation(mutating=True) as client:
            refreshed, delete_ids = self._refresh_from_canonical(records)
            collection = self._get_collection(client)
            if delete_ids:
                collection.delete(ids=delete_ids)
            if refreshed:
                self._upsert_collection(collection, refreshed)

    def delete(self, object_ids: list[str]) -> None:
        ids = sorted({str(object_id) for object_id in object_ids if object_id})
        if not ids:
            return
        with self._operation(mutating=True) as client:
            collection = self._get_collection(client)
            collection.delete(ids=ids)

    def search(self, query: str, top_k: int = 10) -> list[SearchHit]:
        return self.search_filtered(query, top_k=top_k, metadata={})

    def search_filtered(
        self,
        query: str,
        top_k: int = 10,
        *,
        metadata: dict[str, Any],
    ) -> list[SearchHit]:
        if not query.strip():
            return []
        limit = max(1, int(top_k))
        where = self._validate_filter(metadata)
        with self._operation(mutating=False) as client:
            collection = self._get_collection(client)
            count = int(collection.count())
            if count == 0:
                return []
            query_embedding = self._validate_query_embedding(
                self.embedder.embed_query(query)
            )
            result = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(limit, count),
                where=where or None,
                include=["distances"],
            )
        ids = self._first_result_row(result.get("ids"))
        distances = self._first_result_row(result.get("distances"))
        hits: list[SearchHit] = []
        for index, object_id in enumerate(ids):
            distance = distances[index] if index < len(distances) else None
            score = 0.0 if distance is None else 1.0 / (1.0 + max(0.0, float(distance)))
            hits.append(
                SearchHit(
                    object_id=str(object_id),
                    score=score,
                    source="chroma",
                )
            )
        return hits

    def rebuild(self, records: list[IndexRecord]) -> None:
        with self._operation(mutating=True) as client:
            self._delete_collection_if_exists(client)
            collection = self._create_collection(client)
            refreshed, _ = self._refresh_from_canonical(records)
            if refreshed:
                self._upsert_collection(collection, refreshed)

    def health(self) -> dict[str, Any]:
        with self._operation(mutating=False) as client:
            collection = self._get_collection(client)
            records = int(collection.count())
        return {
            "status": "ok",
            "provider": "chroma",
            "mode": self.mode,
            "collection": self.collection_name,
            "records": records,
            "embedding_model": self.model_id,
            "embedding_loaded": bool(getattr(self.embedder, "loaded", True)),
            "generation": (
                self._read_generation() if self.mode == "embedded" else None
            ),
        }

    @contextmanager
    def _operation(self, *, mutating: bool) -> Iterator[Any]:
        # Refreshing canonical objects under the workspace lock prevents a delayed
        # upsert from overwriting newer promotion or retraction metadata.
        with self.store.write_lock():
            current_generation = self._read_generation()
            client = self._get_client(current_generation)
            if self.mode == "embedded":
                observed = self._observed_generation
                if observed != current_generation:
                    raise EmbeddedChromaChangedError(
                        "embedded Chroma was updated by another process; refusing "
                        "a potentially stale vector operation. Restart this process "
                        "or use AML_CHROMA_MODE=http for long-lived multi-agent access."
                    )
                if mutating:
                    current_generation += 1
                    WorkspaceStore._write_text_atomic(
                        self._generation_path, f"{current_generation}\n"
                    )
                    self._set_observed_generation(current_generation)
            yield client

    def _get_client(self, current_generation: int) -> Any:
        if self.mode == "http":
            if self._client is None:
                self._client = self._build_client()
            return self._client

        key = self._embedded_client_key()
        with _EMBEDDED_CLIENTS_LOCK:
            state = _EMBEDDED_CLIENTS.get(key)
            if state is not None:
                if state.pid != os.getpid():
                    raise EmbeddedChromaChangedError(
                        "embedded Chroma was loaded before this process was forked; "
                        "start workers before loading Chroma or use HTTP mode"
                    )
                self._client = state.client
                self._observed_generation = state.generation
                return state.client
            client = self._build_client()
            state = _EmbeddedClientState(
                client=client,
                generation=current_generation,
                pid=os.getpid(),
            )
            _EMBEDDED_CLIENTS[key] = state
            self._client = client
            self._observed_generation = current_generation
            return client

    def _build_client(self) -> Any:
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:
            raise RuntimeError(
                "Chroma support is not installed; run "
                "pip install 'agent-memory-ledger[chroma]'"
            ) from exc
        settings = Settings(anonymized_telemetry=False)
        if self.mode == "embedded":
            self.path.mkdir(parents=True, exist_ok=True)
            return chromadb.PersistentClient(
                path=str(self.path),
                settings=settings,
                tenant=self.tenant,
                database=self.database,
            )
        return chromadb.HttpClient(
            host=self.host,
            port=self.port,
            ssl=self.ssl,
            headers=self.headers or None,
            settings=settings,
            tenant=self.tenant,
            database=self.database,
        )

    def _set_observed_generation(self, generation: int) -> None:
        self._observed_generation = generation
        if self.mode != "embedded":
            return
        with _EMBEDDED_CLIENTS_LOCK:
            state = _EMBEDDED_CLIENTS.get(self._embedded_client_key())
            if state is not None and state.pid == os.getpid():
                state.generation = generation

    def _read_generation(self) -> int:
        if not self._generation_path.is_file():
            return 0
        try:
            return int(self._generation_path.read_text(encoding="utf-8").strip() or "0")
        except ValueError as exc:
            raise RuntimeError("invalid Chroma generation marker") from exc

    def _embedded_client_key(self) -> str:
        return f"{self.path}\0{self.tenant}\0{self.database}"

    def _get_collection(self, client: Any) -> Any:
        collection = client.get_or_create_collection(
            name=self.collection_name,
            metadata=self._collection_metadata(),
            embedding_function=None,
        )
        metadata = dict(collection.metadata or {})
        if (
            metadata.get("aml_embedding_model") != self.model_id
            or int(metadata.get("aml_schema_version", 0)) != COLLECTION_SCHEMA_VERSION
        ):
            raise RuntimeError(
                "Chroma collection schema or embedding model differs from this "
                "configuration; run 'aml reindex --semantic-provider chroma'"
            )
        return collection

    def _create_collection(self, client: Any) -> Any:
        return client.create_collection(
            name=self.collection_name,
            metadata=self._collection_metadata(),
            embedding_function=None,
        )

    def _delete_collection_if_exists(self, client: Any) -> None:
        names = {
            str(item.name if hasattr(item, "name") else item)
            for item in client.list_collections()
        }
        if self.collection_name in names:
            client.delete_collection(name=self.collection_name)

    def _collection_metadata(self) -> dict[str, Any]:
        return {
            "aml_schema_version": COLLECTION_SCHEMA_VERSION,
            "aml_embedding_model": self.model_id,
            "aml_source_of_truth": "filesystem",
        }

    def _upsert_collection(self, collection: Any, records: list[IndexRecord]) -> None:
        documents = [self._record_text(record) for record in records]
        embeddings = self._validate_document_embeddings(
            self.embedder.embed_documents(documents), len(records)
        )
        collection.upsert(
            ids=[record.object_id for record in records],
            documents=documents,
            embeddings=embeddings,
            metadatas=[self._record_metadata(record) for record in records],
        )

    def _refresh_from_canonical(
        self, records: list[IndexRecord]
    ) -> tuple[list[IndexRecord], list[str]]:
        refreshed: list[IndexRecord] = []
        delete_ids: list[str] = []
        for record in records:
            memory_object = self.store.get_object(record.object_id)
            if memory_object is None:
                refreshed.append(record)
                continue
            if memory_object.status != "active":
                delete_ids.append(record.object_id)
                continue
            refreshed.append(
                IndexRecord(
                    object_id=memory_object.object_id,
                    title=memory_object.title,
                    text=f"{memory_object.summary}\n\n{memory_object.content}",
                    kind=memory_object.kind.value,
                    tags=memory_object.tags,
                    metadata={
                        "importance": memory_object.importance,
                        "promoted": memory_object.promoted,
                        "source_platform": memory_object.source_platform,
                    },
                )
            )
        return refreshed, delete_ids

    @staticmethod
    def _record_text(record: IndexRecord) -> str:
        text = f"{record.title}\n\n{record.text}".strip()
        return text or record.object_id

    @staticmethod
    def _record_metadata(record: IndexRecord) -> dict[str, Any]:
        return {
            "kind": str(record.kind),
            "promoted": bool(record.metadata.get("promoted", False)),
            "importance": int(record.metadata.get("importance", 0)),
            "source_platform": str(record.metadata.get("source_platform", "")),
            "tags_json": json.dumps(record.tags, ensure_ascii=False, sort_keys=True),
        }

    @staticmethod
    def _validate_filter(metadata: dict[str, Any]) -> dict[str, Any]:
        supported = {"kind", "promoted", "importance", "source_platform"}
        unsupported = set(metadata) - supported
        if unsupported:
            raise ValueError(
                "unsupported Chroma metadata filters: " + ", ".join(sorted(unsupported))
            )
        for key, value in metadata.items():
            if not isinstance(value, (str, int, float, bool)):
                raise TypeError(f"Chroma metadata filter {key!r} must be scalar")
        return dict(metadata)

    @classmethod
    def _validate_document_embeddings(
        cls, embeddings: list[list[float]], expected: int
    ) -> list[list[float]]:
        if len(embeddings) != expected:
            raise ValueError(
                "embedding adapter returned "
                f"{len(embeddings)} vectors for {expected} records"
            )
        validated = [cls._validate_query_embedding(vector) for vector in embeddings]
        dimensions = {len(vector) for vector in validated}
        if len(dimensions) > 1:
            raise ValueError("embedding adapter returned inconsistent dimensions")
        return validated

    @staticmethod
    def _validate_query_embedding(embedding: list[float]) -> list[float]:
        vector = [float(value) for value in embedding]
        if not vector:
            raise ValueError("embedding adapter returned an empty vector")
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding adapter returned a non-finite value")
        return vector

    @staticmethod
    def _first_result_row(value: Any) -> list[Any]:
        if not value:
            return []
        first = value[0]
        return list(first) if isinstance(first, (list, tuple)) else list(value)

    def _resolve_path(self, path: str | Path | None) -> Path:
        if path is None:
            return (self.store.state_dir / "chroma").resolve()
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.store.root / candidate
        return candidate.resolve()


def create_index(workspace: Path) -> ChromaSemanticIndex:
    """Create the first-party adapter from portable ``AML_*`` settings."""
    model_name = os.environ.get("AML_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    batch_size = _positive_int(
        os.environ.get("AML_EMBEDDING_BATCH_SIZE", "32"),
        "batch size",
    )
    threads_value = os.environ.get("AML_EMBEDDING_THREADS")
    threads = (
        _positive_int(threads_value, "embedding threads") if threads_value else None
    )
    embedder = FastEmbedTextEmbedder(
        model_name=model_name,
        cache_dir=os.environ.get("AML_EMBEDDING_CACHE") or None,
        batch_size=batch_size,
        threads=threads,
    )
    headers = _headers_from_environment()
    return ChromaSemanticIndex(
        workspace,
        embedder=embedder,
        mode=os.environ.get("AML_CHROMA_MODE", "embedded"),
        path=os.environ.get("AML_CHROMA_PATH") or None,
        collection_name=os.environ.get("AML_CHROMA_COLLECTION", DEFAULT_COLLECTION),
        host=os.environ.get("AML_CHROMA_HOST", "localhost"),
        port=_positive_int(
            os.environ.get("AML_CHROMA_PORT", "8000"),
            "Chroma port",
        ),
        ssl=_environment_bool("AML_CHROMA_SSL", False),
        headers=headers,
        tenant=os.environ.get("AML_CHROMA_TENANT", "default_tenant"),
        database=os.environ.get("AML_CHROMA_DATABASE", "default_database"),
    )


def _positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{label} must be positive")
    return parsed


def _environment_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _headers_from_environment() -> dict[str, str]:
    raw = os.environ.get("AML_CHROMA_HEADERS_JSON")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AML_CHROMA_HEADERS_JSON is invalid JSON: {exc}") from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("AML_CHROMA_HEADERS_JSON must be an object of string values")
    return dict(value)
