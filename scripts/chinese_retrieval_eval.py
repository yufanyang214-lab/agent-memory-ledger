"""Small reproducible Chinese semantic-retrieval evaluation for Chroma."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from agent_memory_ledger.chroma_index import (
    ChromaSemanticIndex,
    FastEmbedTextEmbedder,
)
from agent_memory_ledger.models import IndexRecord

CASES = [
    (
        "项目代号",
        "项目代号是星尘，所有会话归档使用这个名称。",
        "会话存档的名字是什么？",
    ),
    (
        "并发写入规则",
        "多个 Agent 并发写入时，必须先获取工作区文件锁。",
        "几个智能体同时保存记忆如何避免冲突？",
    ),
    (
        "存储权威",
        "文件系统中的 JSON 对象是唯一事实来源，SQLite 和向量索引均可重建。",
        "哪个存储才是最终权威？",
    ),
    (
        "明账提升门槛",
        "只有经过提升的高价值记忆才能进入明账，其他活跃对象留在暗账。",
        "哪些内容会出现在启动时优先看的记忆里？",
    ),
    (
        "撤回保留证据",
        "撤回记忆时保留原始证据和审计日志，但不得继续参与召回。",
        "删除一条错误记忆后还保留什么？",
    ),
    (
        "可选向量安装",
        "ChromaDB 是可选依赖，通过 pip extra 安装，基础包不强制携带向量库。",
        "没有向量数据库的用户怎么启用语义搜索？",
    ),
    (
        "归档前脱敏",
        "归档前需要删除访问令牌、密码和其他敏感信息。",
        "写入历史之前怎样处理密钥？",
    ),
    (
        "常驻 Agent 向量服务",
        "长期运行的多个 Agent 应连接同一个 Chroma HTTP 服务，避免本地客户端陈旧索引。",
        "常驻进程共享向量索引应该用什么连接方式？",
    ),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--cache-dir")
    parser.add_argument("--min-top1", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    workspace = args.workspace or Path(tempfile.mkdtemp(prefix="aml-chinese-eval-"))
    embedder = FastEmbedTextEmbedder(cache_dir=args.cache_dir)
    index = ChromaSemanticIndex(workspace, embedder=embedder)
    records = [
        IndexRecord(
            object_id=f"mem_eval_{position:02d}",
            title=title,
            text=text,
            kind="semantic",
            tags=["chinese-eval"],
            metadata={
                "importance": 80,
                "promoted": position % 2 == 1,
                "source_platform": "evaluation",
            },
        )
        for position, (title, text, _) in enumerate(CASES, 1)
    ]

    started = time.perf_counter()
    index.rebuild(records)
    index_seconds = time.perf_counter() - started
    results: list[dict[str, object]] = []
    latencies: list[float] = []
    for position, (_, _, query) in enumerate(CASES, 1):
        started = time.perf_counter()
        hits = index.search(query, top_k=1)
        latencies.append((time.perf_counter() - started) * 1000.0)
        predicted = hits[0].object_id if hits else None
        expected = f"mem_eval_{position:02d}"
        results.append(
            {
                "query": query,
                "expected": expected,
                "predicted": predicted,
                "pass": predicted == expected,
            }
        )

    bright_hits = index.search_filtered(
        CASES[0][2],
        top_k=len(CASES),
        metadata={"promoted": True},
    )
    promoted_ids = {
        record.object_id for record in records if record.metadata["promoted"]
    }
    bright_filter_pass = bool(bright_hits) and all(
        hit.object_id in promoted_ids for hit in bright_hits
    )
    passed = sum(bool(item["pass"]) for item in results)
    report = {
        "status": (
            "passed"
            if passed >= max(0, args.min_top1) and bright_filter_pass
            else "failed"
        ),
        "model": index.model_id,
        "top1": f"{passed}/{len(CASES)}",
        "bright_filter": bright_filter_pass,
        "records": index.health()["records"],
        "index_seconds": round(index_seconds, 3),
        "query_ms_median": round(statistics.median(latencies), 2),
        "workspace": str(workspace.resolve()),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
