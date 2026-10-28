from types import SimpleNamespace

import pytest

from penhin_knowledge import AccessScope, KnowledgeError, KnowledgeSource, SourcePart


async def test_final_asr_preserves_original_text_and_whole_recording_timing(service):
    transcript = SimpleNamespace(
        source_id="voice", text="  明天发布新版本。", is_final=True, duration_seconds=12.5
    )
    source = KnowledgeSource.from_transcript(transcript, title="会议录音")
    await service.upsert("p", [source])
    hit = (await service.search("何时发布版本？", scope=AccessScope("p", ("voice",)))).hits[0]
    assert hit.text == transcript.text
    assert hit.citation.content_type == "transcript"
    assert (hit.citation.start_seconds, hit.citation.end_seconds) == (0, 12.5)
    transcript.is_final = False
    with pytest.raises(KnowledgeError, match="final_transcript_required"):
        KnowledgeSource.from_transcript(transcript, title="会议录音")


async def test_segmented_transcript_keeps_segment_times(service):
    source = KnowledgeSource(
        "voice",
        "会议",
        "transcript",
        (
            SourcePart("预算计划", "transcript", start_seconds=2, end_seconds=5),
            SourcePart("下周发布版本", "transcript", start_seconds=20, end_seconds=25),
        ),
    )
    await service.upsert("p", [source])
    hit = (await service.search("发布", scope=AccessScope("p", ("voice",)))).hits[0]
    assert (hit.citation.start_seconds, hit.citation.end_seconds) == (20, 25)


async def test_media_separates_original_extraction_from_generated_description(service):
    supplement = SimpleNamespace(
        backend="local-vision",
        warnings=("video_frames_sampled",),
        parts=(
            SimpleNamespace(
                source_id="pdf",
                text="图表展示增长。",
                extracted_text="原文：季度收入增长。",
                page=3,
                timestamp_seconds=None,
                part_index=2,
            ),
            SimpleNamespace(
                source_id="video",
                text="蓝色汽车经过。",
                extracted_text="",
                page=None,
                timestamp_seconds=15,
                part_index=3,
            ),
        ),
    )
    pdf = KnowledgeSource.from_media(supplement, source_id="pdf", title="财报")
    video = KnowledgeSource.from_media(supplement, source_id="video", title="录像")
    await service.upsert("p", [pdf, video])
    scope = AccessScope("p", ("pdf", "video"))
    hits = (await service.search("收入图表增长", scope=scope)).hits
    assert {h.citation.content_type for h in hits} == {"extracted_text", "generated_description"}
    assert all(h.citation.page == 3 and h.citation.origin_part == 2 for h in hits)
    frame = (await service.search("汽车", scope=scope)).hits[0]
    assert frame.citation.start_seconds == 15 and frame.citation.end_seconds is None
    assert frame.citation.backend == "local-vision"
    assert frame.citation.warnings == ("video_frames_sampled",)
    with pytest.raises(KnowledgeError, match="source_not_in_supplement"):
        KnowledgeSource.from_media(supplement, source_id="missing", title="x")


async def test_note_is_a_distinct_source_kind(service):
    note = KnowledgeSource.from_text("note", "随手记", "检索需要保留引用。", kind="note")
    await service.upsert("p", [note])
    hit = (await service.search("引用", scope=AccessScope("p", ("note",)))).hits[0]
    assert hit.citation.kind == "note"
