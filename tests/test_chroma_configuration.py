from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_memory_ledger.chroma_index import (
    DEFAULT_EMBEDDING_MODEL,
    FastEmbedTextEmbedder,
    create_index,
)


class RecordingModel:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def passage_embed(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        self.calls.append(texts)
        return [[3.0, 4.0] for _ in texts]

    def query_embed(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        self.calls.append(texts)
        return [[3.0, 4.0] for _ in texts]


class ChromaConfigurationTests(unittest.TestCase):
    def test_fastembed_uses_query_and_passage_paths_and_normalizes(self) -> None:
        model = RecordingModel()
        embedder = FastEmbedTextEmbedder()
        embedder._model = model

        self.assertEqual(embedder.embed_documents(["项目代号是星尘"]), [[0.6, 0.8]])
        self.assertEqual(embedder.embed_query("会话存档的名字"), [0.6, 0.8])
        self.assertEqual(
            model.calls,
            [["项目代号是星尘"], ["会话存档的名字"]],
        )

    def test_factory_is_lazy_and_uses_portable_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir) / "memory"
            with patch.dict(os.environ, {}, clear=True):
                index = create_index(workspace)

            self.assertEqual(index.mode, "embedded")
            self.assertEqual(index.model_id, DEFAULT_EMBEDDING_MODEL)
            self.assertEqual(index.path, (workspace / "state" / "chroma").resolve())
            self.assertFalse(index.embedder.loaded)

    def test_factory_accepts_http_configuration_without_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch.dict(
                os.environ,
                {
                    "AML_CHROMA_MODE": "http",
                    "AML_CHROMA_HOST": "vector.internal",
                    "AML_CHROMA_PORT": "8443",
                    "AML_CHROMA_SSL": "true",
                    "AML_CHROMA_HEADERS_JSON": '{"Authorization":"Bearer test"}',
                },
                clear=True,
            ):
                index = create_index(Path(tempdir) / "memory")

            self.assertEqual(index.mode, "http")
            self.assertEqual(index.host, "vector.internal")
            self.assertEqual(index.port, 8443)
            self.assertTrue(index.ssl)
            self.assertEqual(index.headers, {"Authorization": "Bearer test"})


if __name__ == "__main__":
    unittest.main()
