"""Immutable, host-owned inputs and source-preserving retrieval results."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
from typing import TYPE_CHECKING

from .errors import KnowledgeError

if TYPE_CHECKING:
    from penhin_asr import Transcript
    from penhin_media import ContextSupplement

KINDS = {"document", "note", "transcript", "media"}
CONTENT_TYPES = {"original_text", "transcript", "extracted_text", "generated_description"}


def valid_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 256
        and value.strip() == value
        and not any(ord(c) < 32 for c in value)
    )


def seconds(value: object) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class KnowledgeLimits:
    chunk_chars: int = 1200
    overlap_chars: int = 160
    max_sources: int = 32
    max_parts: int = 512
    max_source_chars: int = 1_000_000
    max_batch_chars: int = 4_000_000
    max_chunks: int = 8192
    max_scope_sources: int = 1000
    max_query_chars: int = 1000
    max_query_terms: int = 128
    max_results: int = 20
    max_output_chars: int = 24_000

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is not int or value < (0 if item.name == "overlap_chars" else 1):
                raise ValueError(f"{item.name} must be a positive integer (overlap may be zero)")
        if not 0 <= self.overlap_chars < self.chunk_chars:
            raise ValueError("overlap_chars must be smaller than chunk_chars")
        if self.max_output_chars < self.chunk_chars:
            raise ValueError("max_output_chars must fit one chunk")


@dataclass(frozen=True)
class SourcePart:
    text: str = field(repr=False)
    content_type: str = "original_text"
    page: int | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    # Original media part index, before separating extracted and generated text.
    origin_part: int | None = None

    def validate(self) -> None:
        if (
            not isinstance(self.text, str)
            or not isinstance(self.content_type, str)
            or self.content_type not in CONTENT_TYPES
            or (self.page is not None and (type(self.page) is not int or self.page < 1))
            or (
                self.origin_part is not None
                and (type(self.origin_part) is not int or self.origin_part < 0)
            )
            or (self.start_seconds is not None and not seconds(self.start_seconds))
            or (
                self.end_seconds is not None
                and (
                    not seconds(self.end_seconds)
                    or self.start_seconds is None
                    or self.end_seconds < self.start_seconds
                )
            )
        ):
            raise KnowledgeError("invalid_source")


@dataclass(frozen=True)
class KnowledgeSource:
    source_id: str
    title: str
    kind: str
    parts: tuple[SourcePart, ...] = field(repr=False)
    uri: str = ""
    backend: str = ""
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_text(cls, source_id: str, title: str, text: str, *, kind="document", uri=""):
        return cls(source_id, title, kind, (SourcePart(text),), uri=uri)

    @classmethod
    def from_transcript(cls, transcript: Transcript, *, title: str, uri: str = ""):
        if transcript.is_final is not True:
            raise KnowledgeError("final_transcript_required")
        # Existing ASR provides whole-recording timing, not word alignment.
        return cls(
            transcript.source_id,
            title,
            "transcript",
            (
                SourcePart(
                    transcript.text,
                    "transcript",
                    start_seconds=0,
                    end_seconds=transcript.duration_seconds,
                ),
            ),
            uri=uri,
        )

    @classmethod
    def from_media(cls, supplement: ContextSupplement, *, source_id: str, title: str, uri=""):
        parts = []
        for part in supplement.parts:
            if part.source_id != source_id:
                continue
            for content_type, text in (
                ("extracted_text", part.extracted_text),
                ("generated_description", part.text),
            ):
                if text.strip():
                    parts.append(
                        SourcePart(
                            text,
                            content_type,
                            page=part.page,
                            start_seconds=part.timestamp_seconds,
                            origin_part=part.part_index,
                        )
                    )
        if not parts:
            raise KnowledgeError("source_not_in_supplement")
        return cls(
            source_id,
            title,
            "media",
            tuple(parts),
            uri,
            supplement.backend,
            tuple(supplement.warnings),
        )

    def validate(self, limits: KnowledgeLimits) -> None:
        if (
            not valid_id(self.source_id)
            or not isinstance(self.title, str)
            or not self.title.strip()
            or len(self.title) > 512
            or not isinstance(self.kind, str)
            or self.kind not in KINDS
            or not isinstance(self.parts, tuple)
            or not 1 <= len(self.parts) <= limits.max_parts
            or not isinstance(self.uri, str)
            or len(self.uri) > 2048
            or not isinstance(self.backend, str)
            or len(self.backend) > 256
            or not isinstance(self.warnings, tuple)
            or len(self.warnings) > 32
            or not all(isinstance(w, str) and len(w) <= 256 for w in self.warnings)
        ):
            raise KnowledgeError("invalid_source")
        for part in self.parts:
            if not isinstance(part, SourcePart):
                raise KnowledgeError("invalid_source")
            part.validate()
        if sum(len(p.text) for p in self.parts) > limits.max_source_chars:
            raise KnowledgeError("index_limit_exceeded")
        if not any(p.text.strip() for p in self.parts):
            raise KnowledgeError("empty_source")


@dataclass(frozen=True)
class AccessScope:
    """An explicit host authorization snapshot; an empty list permits no sources."""

    project_id: str
    source_ids: tuple[str, ...]

    def validate(self, limits: KnowledgeLimits) -> None:
        if (
            not valid_id(self.project_id)
            or not isinstance(self.source_ids, tuple)
            or len(self.source_ids) > limits.max_scope_sources
            or not all(valid_id(s) for s in self.source_ids)
        ):
            raise KnowledgeError("invalid_scope")


@dataclass(frozen=True)
class Citation:
    citation_id: str
    project_id: str
    source_id: str
    revision: str
    title: str
    kind: str
    uri: str
    part_index: int
    content_type: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    page: int | None
    start_seconds: float | None
    end_seconds: float | None
    origin_part: int | None
    backend: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SearchHit:
    text: str = field(repr=False)
    citation: Citation
    score: float | None = None

    def as_dict(self) -> dict:
        citation = asdict(self.citation)
        citation["warnings"] = list(self.citation.warnings)
        return {
            "text": self.text,
            "citation": citation,
            "score": self.score,
            "trust": "untrusted",
        }


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    truncated: bool = False

    def as_dict(self) -> dict:
        return {
            "type": "knowledge_search",
            "trust": "untrusted",
            "retrieval": "lexical_bm25",
            "hits": [h.as_dict() for h in self.hits],
            "truncated": self.truncated,
        }

    def as_text_block(self) -> dict:
        """Host may append to this request's user content; never to system prompts."""
        return {"type": "text", "text": json.dumps(self.as_dict(), ensure_ascii=True)}


@dataclass(frozen=True)
class IndexResult:
    updated: int
    unchanged: int
    chunks: int
