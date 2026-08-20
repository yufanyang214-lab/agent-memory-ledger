from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .models import IndexRecord, MemoryDraft, SearchHit, SessionBundle


@runtime_checkable
class Sanitizer(Protocol):
    def sanitize(self, session: SessionBundle) -> SessionBundle:
        """Return a safe copy of a session before it is archived or indexed."""


@runtime_checkable
class MemoryExtractor(Protocol):
    def extract(self, session: SessionBundle, library: str) -> list[MemoryDraft]:
        """Convert a sanitized session into structured memory candidates."""


@runtime_checkable
class SemanticIndex(Protocol):
    """Optional adapter around a vector database and its embedding model.

    The core passes text records and query text. The adapter owns embeddings,
    persistence, filtering, and the concrete vector-store client.
    """

    def upsert(self, records: list[IndexRecord]) -> None:
        ...

    def delete(self, object_ids: list[str]) -> None:
        ...

    def search(self, query: str, top_k: int = 10) -> list[SearchHit]:
        ...

    def rebuild(self, records: list[IndexRecord]) -> None:
        ...

    def health(self) -> dict[str, Any]:
        ...


@runtime_checkable
class FilteredSemanticIndex(Protocol):
    """Optional extension for indexes that can filter before vector ranking.

    Keeping this separate from :class:`SemanticIndex` preserves compatibility
    with existing adapters. The core currently uses it for bright-ledger recall
    by passing ``{"promoted": True}``.
    """

    def search_filtered(
        self,
        query: str,
        top_k: int = 10,
        *,
        metadata: dict[str, Any],
    ) -> list[SearchHit]:
        ...


def load_plugin(spec: str, workspace: Path) -> Any:
    """Load ``module:factory`` or ``path/to/plugin.py:factory``.

    The factory is called with the resolved memory-workspace path. Supporting a
    local file keeps small private adapters useful without requiring packaging.
    """
    if ":" not in spec:
        raise ValueError(
            "plugin must use 'module:factory' or 'path/to/plugin.py:factory' syntax"
        )
    target, factory_name = spec.rsplit(":", 1)
    if not target or not factory_name:
        raise ValueError("plugin target and factory name must both be non-empty")

    candidate = Path(target).expanduser()
    is_file_target = candidate.suffix.lower() == ".py" or any(
        separator in target for separator in ("/", "\\")
    )
    if is_file_target:
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise ValueError(f"plugin file not found: {candidate}")
        module_name = f"_agent_memory_ledger_plugin_{abs(hash(str(candidate)))}"
        module_spec = importlib.util.spec_from_file_location(module_name, candidate)
        if module_spec is None or module_spec.loader is None:
            raise ValueError(f"cannot load plugin file: {candidate}")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_name] = module
        try:
            module_spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
    else:
        module = importlib.import_module(target)

    factory = getattr(module, factory_name, None)
    if factory is None or not callable(factory):
        raise ValueError(f"plugin factory not found or not callable: {spec}")
    return factory(workspace)
