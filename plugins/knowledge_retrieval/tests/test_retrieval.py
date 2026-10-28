import json
import sqlite3
from dataclasses import replace

import pytest

from penhin_knowledge import (
    AccessScope,
    KnowledgeError,
    KnowledgeLimits,
    KnowledgeService,
    KnowledgeSource,
    SourcePart,
)


async def test_question_returns_exact_text_with_source_and_reopens(service, source, scope):
    report = await service.upsert("project", [source])
    assert (report.updated, report.unchanged, report.chunks) == (1, 0, 1)
    result = await service.search("数据库备份什么时候执行？", scope=scope)
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.text == source.parts[0].text
    assert hit.citation.source_id == "guide"
    assert hit.citation.uri == "docs/deploy.md"
    assert hit.citation.line_start == 1 and hit.citation.line_end == 3
    assert hit.citation.char_start == 0 and hit.citation.char_end == len(hit.text)
    assert hit.score > 0
    assert (await service.fetch(hit.citation.citation_id, scope=scope)).text == hit.text
    assert json.loads(result.as_text_block()["text"])["trust"] == "untrusted"
    path = service._index.path
    await service.aclose()
    async with KnowledgeService(path) as reopened:
        assert (await reopened.fetch(hit.citation.citation_id, scope=scope)).text == hit.text


@pytest.mark.parametrize("question", ["如何配置API？", "api", "数据库", '"备份" OR * NEAR(x)'])
async def test_chinese_mixed_text_and_literal_fts_query(service, source, scope, question):
    await service.upsert("project", [source])
    assert (await service.search(question, scope=scope)).hits


@pytest.mark.parametrize("question", ["quantum entanglement", "???", "the and what"])
async def test_no_evidence_returns_no_hits(service, source, scope, question):
    await service.upsert("project", [source])
    result = await service.search(question, scope=scope)
    assert result.hits == () and not result.truncated


async def test_source_updates_are_idempotent_and_invalidate_old_citations(service, source, scope):
    await service.upsert("project", [source])
    old = (await service.search("备份", scope=scope)).hits[0]
    report = await service.upsert("project", [source])
    assert (report.updated, report.unchanged) == (0, 1)
    assert (await service.search("备份", scope=scope)).hits[0] == old
    changed = replace(source, parts=(SourcePart("更新：存储使用对象服务。"),))
    await service.upsert("project", [changed])
    assert (await service.search("备份", scope=scope)).hits == ()
    new = (await service.search("存储", scope=scope)).hits[0]
    assert old.citation.revision != new.citation.revision
    with pytest.raises(KnowledgeError, match="^citation_unavailable$"):
        await service.fetch(old.citation.citation_id, scope=scope)
    assert await service.remove("project", "guide")
    assert not await service.remove("project", "guide")
    assert not (await service.search("存储", scope=scope)).hits
    with pytest.raises(KnowledgeError, match="citation_unavailable"):
        await service.fetch(new.citation.citation_id, scope=scope)


async def test_project_and_source_scope_filter_before_limit(service, source, scope):
    await service.upsert("project", [source])
    await service.upsert("other", [replace(source, parts=(SourcePart("备份秘密信息"),))])
    await service.upsert(
        "project", [replace(source, source_id="private", title="备份", parts=(SourcePart("备份"),))]
    )
    hits = (await service.search("备份", scope=scope, limit=1)).hits
    assert len(hits) == 1 and hits[0].citation.source_id == "guide"
    citation_id = hits[0].citation.citation_id
    for denied in (
        AccessScope("other", ("guide",)),
        AccessScope("project", ()),
        AccessScope("project", ("private",)),
    ):
        with pytest.raises(KnowledgeError, match="^citation_unavailable$"):
            await service.fetch(citation_id, scope=denied)
    assert not (await service.search("备份", scope=AccessScope("project", ()))).hits


async def test_chunks_preserve_unicode_whitespace_and_crlf_offsets(tmp_path):
    text = "# 说明\r\n" + "  数据库备份 🐈 café\r\n" * 20
    source = KnowledgeSource.from_text("guide", "说明", text)
    scope = AccessScope("project", ("guide",))
    limits = KnowledgeLimits(chunk_chars=50, overlap_chars=10, max_results=50)
    async with KnowledgeService(tmp_path / "index.db", limits=limits) as service:
        await service.upsert("project", [source])
        hits = (await service.search("备份", scope=scope, limit=50)).hits
        assert len(hits) > 1
        covered = set()
        for hit in hits:
            citation = hit.citation
            assert hit.text == text[citation.char_start : citation.char_end]
            assert citation.line_start == text[: citation.char_start].count("\n") + 1
            assert len(hit.text) <= 50
            covered.update(range(citation.char_start, citation.char_end))
        assert covered == set(range(len(text)))


async def test_atomic_batch_rolls_back_old_index_on_database_failure(service, source, scope):
    await service.upsert("project", [source])
    with sqlite3.connect(service._index.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON sources "
            "WHEN NEW.source_id='broken' BEGIN SELECT RAISE(ABORT,'private'); END"
        )
    with pytest.raises(KnowledgeError, match="^index_unavailable$"):
        await service.upsert(
            "project",
            [replace(source, parts=(SourcePart("新文本"),)), replace(source, source_id="broken")],
        )
    assert (await service.search("备份", scope=scope)).hits[0].text == source.parts[0].text
    assert not (await service.search("新文本", scope=scope)).hits


async def test_result_budget_and_limit_are_explicit(tmp_path):
    limits = KnowledgeLimits(chunk_chars=40, overlap_chars=0, max_output_chars=60)
    async with KnowledgeService(tmp_path / "index.db", limits=limits) as service:
        source = KnowledgeSource.from_text("s", "index", "backup " * 40)
        await service.upsert("p", [source])
        result = await service.search("backup", scope=AccessScope("p", ("s",)), limit=5)
        assert result.truncated and len(result.hits) == 1
        assert len(result.hits[0].text) == 40


async def test_metadata_changes_invalidate_citations(service, source, scope):
    await service.upsert("project", [source])
    old = (await service.search("备份", scope=scope)).hits[0]
    await service.upsert("project", [replace(source, uri="docs/renamed.md")])
    with pytest.raises(KnowledgeError, match="citation_unavailable"):
        await service.fetch(old.citation.citation_id, scope=scope)


@pytest.mark.parametrize(
    "source",
    [
        KnowledgeSource.from_text("", "title", "text"),
        KnowledgeSource.from_text("s", "title", "  \n"),
        KnowledgeSource.from_text("s", "title", "text", kind="unknown"),
        KnowledgeSource(
            "s", "title", "transcript", (SourcePart("t", start_seconds=4, end_seconds=2),)
        ),
        KnowledgeSource("s", "title", "media", (SourcePart("t", page=0),)),
        KnowledgeSource("s", "title", "media", (SourcePart("t", start_seconds=float("nan")),)),
    ],
)
async def test_invalid_source_rejected_before_database_write(service, source):
    with pytest.raises(KnowledgeError):
        await service.upsert("project", [source])
    assert not service._index.path.exists()


async def test_limits_and_duplicate_ids_fail_before_write(service, source, scope):
    with pytest.raises(KnowledgeError, match="duplicate_source"):
        await service.upsert("project", [source, source])
    with pytest.raises(KnowledgeError, match="index_limit_exceeded"):
        await service.upsert("project", [replace(source, parts=(SourcePart("x" * 1_000_001),))])
    with pytest.raises(KnowledgeError, match="query_limit_exceeded"):
        await service.search("x" * 1001, scope=scope)
    with pytest.raises(KnowledgeError, match="invalid_limit"):
        await service.search("x", scope=scope, limit=0)
    with pytest.raises(KnowledgeError, match="invalid_scope"):
        await service.search("x", scope=AccessScope("project", "guide"))
    with pytest.raises(KnowledgeError, match="invalid_scope"):
        await service.search("x", scope=None)
    assert not service._index.path.exists()


async def test_fetch_respects_lower_output_budget_after_reopen(service, scope):
    source = KnowledgeSource.from_text("guide", "title", "backup " * 100)
    await service.upsert("project", [source])
    citation_id = (await service.search("backup", scope=scope)).hits[0].citation.citation_id
    limits = KnowledgeLimits(chunk_chars=50, overlap_chars=10, max_output_chars=50)
    async with KnowledgeService(service._index.path, limits=limits) as restricted:
        with pytest.raises(KnowledgeError, match="output_limit_exceeded"):
            await restricted.fetch(citation_id, scope=scope)
        report = await restricted.upsert("project", [source])
        assert report.updated == 1
        with pytest.raises(KnowledgeError, match="citation_unavailable"):
            await restricted.fetch(citation_id, scope=scope)


async def test_foreign_database_is_not_adopted_or_modified(tmp_path):
    path = tmp_path / "foreign.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE important (value TEXT)")
        connection.execute("INSERT INTO important VALUES ('keep')")
    async with KnowledgeService(path) as service:
        with pytest.raises(KnowledgeError, match="index_format_mismatch"):
            await service.upsert("p", [KnowledgeSource.from_text("s", "title", "text")])
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM important").fetchall() == [("keep",)]
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == [("important",)]
