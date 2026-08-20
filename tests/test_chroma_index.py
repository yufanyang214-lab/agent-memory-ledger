from __future__ import annotations

import importlib.util
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from agent_memory_ledger.chroma_index import (
    ChromaSemanticIndex,
    EmbeddedChromaChangedError,
)
from agent_memory_ledger.models import IndexRecord

CHROMA_INSTALLED = importlib.util.find_spec("chromadb") is not None


class ConceptEmbedder:
    model_id = "test-multilingual-concepts-v1"
    loaded = True

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        if any(term in text for term in ("项目代号", "会话存档", "星尘")):
            return [1.0, 0.0, 0.0]
        if any(term in text for term in ("并发", "工作区锁", "互斥")):
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


def _record(
    object_id: str,
    title: str,
    text: str,
    *,
    promoted: bool = False,
) -> IndexRecord:
    return IndexRecord(
        object_id=object_id,
        title=title,
        text=text,
        kind="semantic",
        tags=["test"],
        metadata={
            "importance": 80,
            "promoted": promoted,
            "source_platform": "chroma-test",
        },
    )


def _upsert_in_fresh_process(workspace: str, index: int) -> None:
    adapter = ChromaSemanticIndex(workspace, embedder=ConceptEmbedder())
    adapter.upsert(
        [
            _record(
                f"mem_parallel_{index:02d}",
                f"并发记录 {index:02d}",
                f"工作区锁保护并发向量 {index:02d}",
            )
        ]
    )


@unittest.skipUnless(CHROMA_INSTALLED, "install chromadb to run integration tests")
class ChromaIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name) / "memory"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_single_collection_supports_bright_filter_and_metadata_updates(
        self,
    ) -> None:
        adapter = ChromaSemanticIndex(self.workspace, embedder=ConceptEmbedder())
        project = _record(
            "mem_project",
            "项目代号",
            "本次会话的项目代号是星尘。",
            promoted=True,
        )
        lock = _record(
            "mem_lock",
            "并发规则",
            "多个 Agent 写入前必须持有工作区锁。",
        )
        adapter.upsert([project, lock])

        semantic = adapter.search("会话存档的名字", top_k=2)
        bright = adapter.search_filtered(
            "会话存档的名字",
            top_k=2,
            metadata={"promoted": True},
        )

        self.assertEqual(semantic[0].object_id, "mem_project")
        self.assertEqual([hit.object_id for hit in bright], ["mem_project"])
        self.assertEqual(adapter.health()["records"], 2)

        lock.metadata["promoted"] = True
        adapter.upsert([lock])
        bright_lock = adapter.search_filtered(
            "如何避免并发冲突",
            top_k=1,
            metadata={"promoted": True},
        )
        self.assertEqual(bright_lock[0].object_id, "mem_lock")

    def test_persistence_delete_and_rebuild(self) -> None:
        adapter = ChromaSemanticIndex(self.workspace, embedder=ConceptEmbedder())
        adapter.upsert([_record("mem_old", "旧记录", "这是稍后会被重建移除的内容。")])

        restarted = ChromaSemanticIndex(self.workspace, embedder=ConceptEmbedder())
        self.assertEqual(restarted.health()["records"], 1)
        restarted.delete(["mem_old"])
        self.assertEqual(restarted.health()["records"], 0)

        restarted.rebuild(
            [
                _record(
                    "mem_new",
                    "项目代号",
                    "项目代号是星尘。",
                    promoted=True,
                )
            ]
        )
        self.assertEqual(restarted.health()["records"], 1)
        self.assertEqual(
            restarted.search("会话存档的名字", top_k=1)[0].object_id,
            "mem_new",
        )

    def test_parallel_fresh_process_writers_are_serialized(self) -> None:
        context = multiprocessing.get_context("spawn")
        processes = [
            context.Process(
                target=_upsert_in_fresh_process,
                args=(str(self.workspace), index),
            )
            for index in range(8)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=60)

        failures = [process.exitcode for process in processes if process.exitcode != 0]
        self.assertEqual(failures, [])
        adapter = ChromaSemanticIndex(self.workspace, embedder=ConceptEmbedder())
        self.assertEqual(adapter.health()["records"], 8)
        self.assertEqual(
            (self.workspace / "state" / "chroma.generation")
            .read_text(encoding="utf-8")
            .strip(),
            "8",
        )

    def test_long_lived_embedded_client_rejects_stale_cross_process_query(self) -> None:
        adapter = ChromaSemanticIndex(self.workspace, embedder=ConceptEmbedder())
        adapter.upsert([_record("mem_parent", "父进程", "父进程中的初始记录。")])

        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_upsert_in_fresh_process,
            args=(str(self.workspace), 99),
        )
        process.start()
        process.join(timeout=60)
        self.assertEqual(process.exitcode, 0)

        with self.assertRaisesRegex(
            EmbeddedChromaChangedError, "potentially stale vector operation"
        ):
            adapter.search("工作区锁", top_k=5)


if __name__ == "__main__":
    unittest.main()
