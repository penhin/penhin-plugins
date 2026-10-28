"""Run after installing the plugin: python examples/local_search.py [question]."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from penhin_knowledge import AccessScope, KnowledgeService, KnowledgeSource, SourcePart


async def main():
    sources = [
        KnowledgeSource.from_text("docs/deploy.md", "部署文档", "发布前必须完成数据库备份。"),
        KnowledgeSource.from_text(
            "notes/review.md", "复盘笔记", "备份演练每周执行一次。", kind="note"
        ),
        KnowledgeSource(
            "meeting",
            "会议转写",
            "transcript",
            (SourcePart("发布安排在周五下午。", "transcript", start_seconds=10, end_seconds=15),),
        ),
        KnowledgeSource(
            "roadmap.pdf",
            "路线图",
            "media",
            (
                SourcePart("发布计划：周五。", "extracted_text", page=2),
                SourcePart("图中用箭头连接测试和发布。", "generated_description", page=2),
            ),
            backend="example-vision",
        ),
    ]
    with tempfile.TemporaryDirectory(prefix="penhin-knowledge-") as directory:
        async with KnowledgeService(Path(directory) / "index.db") as service:
            await service.upsert("demo", sources)
            scope = AccessScope("demo", tuple(s.source_id for s in sources))
            result = await service.search(
                sys.argv[1] if len(sys.argv) > 1 else "发布前要做什么？", scope=scope
            )
            print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
            if result.hits:
                # Resolve the exact persisted original excerpt, rechecking the scope.
                original = await service.fetch(result.hits[0].citation.citation_id, scope=scope)
                assert original.text == result.hits[0].text


if __name__ == "__main__":
    asyncio.run(main())
