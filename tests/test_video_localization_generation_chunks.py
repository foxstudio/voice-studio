from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


from app.domains.video_localization.localization_document_brief import (  # noqa: E402
    LocalizationDocumentSection,
)
from app.domains.video_localization.localization_generation_chunks import (  # noqa: E402
    plan_localization_generation_chunks,
)
from app.domains.video_localization.localization_source import (  # noqa: E402
    LocalizationSourceCue,
    LocalizationSourcePause,
)


def _cue(index: int, *, characters: int = 500) -> LocalizationSourceCue:
    return LocalizationSourceCue(
        cue_id=f"cue_{index:04d}",
        start_ms=(index - 1) * 1_000,
        end_ms=index * 1_000,
        text=("word " * (characters // 5)).strip() + ".",
        speaker_id="speaker_1",
        source_word_ids=[f"word_{index:04d}"],
    )


def _section(
    index: int,
    cues: list[LocalizationSourceCue],
) -> LocalizationDocumentSection:
    return LocalizationDocumentSection(
        section_id=f"section_{index:04d}",
        title=f"Section {index}",
        function_zh="推进当前内容。",
        source_cue_ids=[cue.cue_id for cue in cues],
    )


def test_chunk_manifest_preserves_complete_order_without_crossing_sections():
    cues = [_cue(index) for index in range(1, 13)]
    sections = [_section(1, cues[:7]), _section(2, cues[7:])]

    result = plan_localization_generation_chunks(
        source_fingerprint="a" * 64,
        cues=cues,
        sections=sections,
    )

    assert [
        cue_id for chunk in result.chunks for cue_id in chunk.source_cue_ids
    ] == [cue.cue_id for cue in cues]
    assert [chunk.chunk_id for chunk in result.chunks] == [
        f"chunk_{index:04d}"
        for index in range(1, len(result.chunks) + 1)
    ]
    assert all(
        set(chunk.source_cue_ids).issubset(
            set(sections[int(chunk.section_id[-4:]) - 1].source_cue_ids)
        )
        for chunk in result.chunks
    )


def test_chunk_planner_prefers_strong_pause_near_target_size():
    cues = [_cue(index, characters=600) for index in range(1, 9)]
    pause = LocalizationSourcePause(
        boundary_id="pause_0001",
        left_word_id="word_0004",
        right_word_id="word_0005",
        start_ms=4_000,
        end_ms=5_200,
        gap_ms=1_200,
        low_energy_ms=1_100,
        low_energy_ratio=0.92,
        confidence="high",
        analysis_version="test-v1",
    )

    result = plan_localization_generation_chunks(
        source_fingerprint="b" * 64,
        cues=cues,
        sections=[_section(1, cues)],
        pauses=[pause],
    )

    assert result.chunks[0].source_cue_ids[-1] == "cue_0004"
    assert result.chunks[1].source_cue_ids[0] == "cue_0005"


def test_short_section_stays_in_one_chunk_and_context_is_read_only():
    cues = [_cue(index, characters=200) for index in range(1, 7)]
    result = plan_localization_generation_chunks(
        source_fingerprint="c" * 64,
        cues=cues,
        sections=[_section(1, cues[:2]), _section(2, cues[2:])],
    )

    assert len(result.chunks) == 2
    assert result.chunks[0].readonly_context_after == [
        cues[2].text,
        cues[3].text,
        cues[4].text,
    ]
    assert result.chunks[1].readonly_context_before == [
        cues[0].text,
        cues[1].text,
    ]


def test_chunk_plan_is_stable_for_the_same_source():
    cues = [_cue(index) for index in range(1, 10)]
    kwargs = {
        "source_fingerprint": "d" * 64,
        "cues": cues,
        "sections": [_section(1, cues)],
    }

    first = plan_localization_generation_chunks(**kwargs)
    second = plan_localization_generation_chunks(**kwargs)

    assert first == second
