"""SQLite operations run on a worker thread; one connection per operation."""

from __future__ import annotations

import bisect
import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .errors import KnowledgeError
from .types import Citation, IndexResult, KnowledgeLimits, SearchHit, SearchResult

_APPLICATION_ID = 0x504B5231
_TOKENS = re.compile(
    r"[\u3400-\u9fff\U00020000-\U0002fa1f]+|[^\W_\u3400-\u9fff\U00020000-\U0002fa1f]+",
    re.UNICODE,
)
_STOP = set(
    "a an and are as at be by can do does for from how i in is it of on or that the to was what when where which who why will with would you your".split()
)
_STOP.update(
    "什么 怎么 如何 为何 为什么 是否 哪个 哪些 多少 请问 可以 一个 我们 你们 这个 那个 的 了 吗 呢".split()
)


def tokens(text: str) -> list[str]:
    result = []
    for match in _TOKENS.finditer(unicodedata.normalize("NFKC", text).casefold()):
        word = match.group()
        if "\u3400" <= word[0] <= "\u9fff" or "\U00020000" <= word[0] <= "\U0002fa1f":
            result.extend(word[i : i + 2] for i in range(max(1, len(word) - 1)))
        else:
            result.append(word)
    return [word for word in result if word not in _STOP]


def query_expression(question: str, limits: KnowledgeLimits) -> str:
    if not isinstance(question, str) or not question.strip():
        raise KnowledgeError("invalid_query")
    if len(question) > limits.max_query_chars:
        raise KnowledgeError("query_limit_exceeded")
    terms = list(dict.fromkeys(tokens(question)))
    if len(terms) > limits.max_query_terms:
        raise KnowledgeError("query_limit_exceeded")
    # Literal terms only: never pass the caller's FTS syntax through to MATCH.
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _check(stop: threading.Event) -> None:
    if stop.is_set():
        raise KnowledgeError("operation_cancelled")


def _chunks(project_id, source, limits, stop):
    # Chunk configuration is part of the revision so changed settings rebuild it.
    revision = _digest([asdict(source), limits.chunk_chars, limits.overlap_chars])
    chunks = []
    for part_index, part in enumerate(source.parts):
        line_starts = [0] + [m.end() for m in re.finditer(r"\r\n|[\r\n]", part.text)]
        start = 0
        while start < len(part.text):
            _check(stop)
            end = min(start + limits.chunk_chars, len(part.text))
            if end < len(part.text):
                boundary = part.text.rfind("\n", start + limits.chunk_chars // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            text = part.text[start:end]
            if text.strip():
                citation_id = _digest(
                    [project_id, source.source_id, revision, part_index, start, end]
                )
                citation = Citation(
                    citation_id,
                    project_id,
                    source.source_id,
                    revision,
                    source.title,
                    source.kind,
                    source.uri,
                    part_index,
                    part.content_type,
                    start,
                    end,
                    bisect.bisect_right(line_starts, start),
                    bisect.bisect_right(line_starts, end - 1),
                    part.page,
                    part.start_seconds,
                    part.end_seconds,
                    part.origin_part,
                    source.backend,
                    source.warnings,
                )
                chunks.append(SearchHit(text, citation))
                if len(chunks) > limits.max_chunks:
                    raise KnowledgeError("index_limit_exceeded")
            if end == len(part.text):
                break
            start = max(start + 1, end - limits.overlap_chars)
    return revision, chunks


class Index:
    def __init__(self, path: Path, limits: KnowledgeLimits):
        self.path, self.limits = path, limits

    @contextmanager
    def connection(self, stop):
        _check(stop)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.1)
        try:
            connection.row_factory = sqlite3.Row
            connection.set_progress_handler(lambda: int(stop.is_set()), 1000)
            # Serialize initialization with other services opening the same file.
            connection.execute("BEGIN IMMEDIATE")
            app = connection.execute("PRAGMA application_id").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if app == 0 and version == 0:
                if connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
                    raise KnowledgeError("index_format_mismatch")
                connection.execute(
                    "CREATE TABLE sources (project_id TEXT, source_id TEXT, "
                    "revision TEXT, PRIMARY KEY(project_id, source_id))"
                )
                connection.execute(
                    "CREATE TABLE chunks (citation_id TEXT UNIQUE, project_id TEXT, "
                    "source_id TEXT, payload TEXT)"
                )
                connection.execute("CREATE INDEX chunks_source ON chunks(project_id, source_id)")
                connection.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(title, body)")
                connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                connection.execute("PRAGMA user_version = 1")
            elif app != _APPLICATION_ID or version != 1:
                raise KnowledgeError("index_format_mismatch")
            _check(stop)
            connection.commit()
            yield connection
        finally:
            # Disable interruption so rollback/close still completes after cancellation.
            connection.set_progress_handler(None, 0)
            connection.close()

    @staticmethod
    def _delete(connection, project_id, source_id):
        connection.execute(
            "DELETE FROM chunks_fts WHERE rowid IN "
            "(SELECT rowid FROM chunks WHERE project_id=? AND source_id=?)",
            (project_id, source_id),
        )
        connection.execute(
            "DELETE FROM chunks WHERE project_id=? AND source_id=?", (project_id, source_id)
        )
        return connection.execute(
            "DELETE FROM sources WHERE project_id=? AND source_id=?", (project_id, source_id)
        ).rowcount

    def upsert(self, project_id, sources, stop) -> IndexResult:
        prepared, count = [], 0
        for source in sources:
            revision, chunks = _chunks(project_id, source, self.limits, stop)
            count += len(chunks)
            if count > self.limits.max_chunks:
                raise KnowledgeError("index_limit_exceeded")
            prepared.append((revision, chunks))
        updated = unchanged = 0
        with self.connection(stop) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for source, (revision, chunks) in zip(sources, prepared, strict=True):
                _check(stop)
                current = connection.execute(
                    "SELECT revision FROM sources WHERE project_id=? AND source_id=?",
                    (project_id, source.source_id),
                ).fetchone()
                if current and current[0] == revision:
                    unchanged += 1
                    continue
                self._delete(connection, project_id, source.source_id)
                connection.execute(
                    "INSERT INTO sources VALUES (?, ?, ?)", (project_id, source.source_id, revision)
                )
                title_tokens = " ".join(tokens(source.title))
                for hit in chunks:
                    _check(stop)
                    cursor = connection.execute(
                        "INSERT INTO chunks VALUES (?, ?, ?, ?)",
                        (
                            hit.citation.citation_id,
                            project_id,
                            source.source_id,
                            _json(hit.as_dict()),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO chunks_fts(rowid,title,body) VALUES (?, ?, ?)",
                        (cursor.lastrowid, title_tokens, " ".join(tokens(hit.text))),
                    )
                updated += 1
            _check(stop)
            connection.commit()
        return IndexResult(updated, unchanged, count)

    def remove(self, project_id, source_id, stop) -> bool:
        with self.connection(stop) as connection:
            connection.execute("BEGIN IMMEDIATE")
            deleted = self._delete(connection, project_id, source_id)
            _check(stop)
            connection.commit()
            return bool(deleted)

    @staticmethod
    def _allow(connection, scope):
        connection.execute("CREATE TEMP TABLE allowed (source_id TEXT PRIMARY KEY)")
        connection.executemany(
            "INSERT OR IGNORE INTO allowed VALUES (?)", ((s,) for s in scope.source_ids)
        )

    @staticmethod
    def _hit(row) -> SearchHit:
        data = json.loads(row["payload"])
        data["citation"]["warnings"] = tuple(data["citation"]["warnings"])
        return SearchHit(
            data["text"],
            Citation(**data["citation"]),
            -row["rank"] if "rank" in row.keys() else None,
        )

    def search(self, expression, scope, limit, stop) -> SearchResult:
        if not expression or not scope.source_ids:
            return SearchResult(())
        with self.connection(stop) as connection:
            self._allow(connection, scope)
            rows = connection.execute(
                "SELECT c.payload, bm25(chunks_fts, 2.0, 1.0) AS rank FROM chunks_fts "
                "JOIN chunks c ON c.rowid=chunks_fts.rowid "
                "JOIN allowed a ON a.source_id=c.source_id "
                "WHERE chunks_fts MATCH ? AND c.project_id=? "
                "ORDER BY rank, c.citation_id LIMIT ?",
                (expression, scope.project_id, limit + 1),
            ).fetchall()
            hits, size = [], 0
            for row in rows[:limit]:
                _check(stop)
                hit = self._hit(row)
                if size + len(hit.text) > self.limits.max_output_chars:
                    break
                hits.append(hit)
                size += len(hit.text)
            return SearchResult(tuple(hits), len(hits) < len(rows))

    def fetch(self, citation_id, scope, stop) -> SearchHit:
        with self.connection(stop) as connection:
            self._allow(connection, scope)
            row = connection.execute(
                "SELECT c.payload FROM chunks c JOIN allowed a ON a.source_id=c.source_id "
                "WHERE c.citation_id=? AND c.project_id=?",
                (citation_id, scope.project_id),
            ).fetchone()
            _check(stop)
            if row is None:
                # Unknown, removed, stale and unauthorized citations are indistinguishable.
                raise KnowledgeError("citation_unavailable")
            hit = self._hit(row)
            if len(hit.text) > self.limits.max_output_chars:
                raise KnowledgeError("output_limit_exceeded")
            return hit
