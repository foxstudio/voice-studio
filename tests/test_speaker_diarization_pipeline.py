from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (
    asr_step_results,
    localization,
    operation_queue,
    operation_state,
    speakers,
    subtitle_segmentation,
)
from app.models.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSpeaker,
    VideoLocalizationSpeakerCluster,
    VideoLocalizationTranscriptionState,
    VideoLocalizationTranscriptSegment,
)
from app.services import speaker_diarization_service


def _word(index: int, speaker: str) -> VideoLocalizationAlignedWord:
    return VideoLocalizationAlignedWord(
        word_id=f"word_{index:06d}",
        segment_id="asr_0001",
        text=f"word{index}",
        start_ms=(index - 1) * 500,
        end_ms=index * 500,
        speaker_cluster_id=speaker,
    )


def test_subtitle_boundaries_never_cross_speaker_clusters():
    words = [_word(1, "cluster_01"), _word(2, "cluster_01"), _word(3, "cluster_02"), _word(4, "cluster_02")]

    boundaries = subtitle_segmentation._optimal_boundaries(words)

    assert 2 in boundaries
    start = 0
    for end in boundaries:
        assert len({word.speaker_cluster_id for word in words[start:end]}) == 1
        start = end


def test_word_assignment_marks_overlapping_speakers_for_review():
    word = _word(1, "cluster_old")
    mapped = speaker_diarization_service.assign_words(
        [word],
        [
            {"start_ms": 0, "end_ms": 500, "speaker": "cluster_01"},
            {"start_ms": 300, "end_ms": 700, "speaker": "cluster_02"},
        ],
    )[0]

    assert mapped.speaker_cluster_id == "cluster_01"
    assert mapped.has_speaker_overlap is True
    assert mapped.speaker_confidence == 1.0


def test_diarization_clusters_bind_to_stable_business_speakers():
    draft = VideoLocalizationDraft()
    cluster = VideoLocalizationSpeakerCluster(
        cluster_id="cluster_01",
        source_label="S01",
        source_engine_id="moss-transcribe-diarize-mlx",
        start_ms=0,
        end_ms=1000,
        duration_ms=1000,
        segment_count=1,
    )
    cue = VideoLocalizationCue(cue_id="cue_0001", speaker_cluster_id="cluster_01", start_ms=0, end_ms=1000)

    created, bound_cues, bound_clusters = speakers.bind_diarization_clusters(draft, [cue], [cluster])
    reused, rebound_cues, _ = speakers.bind_diarization_clusters(
        draft.model_copy(update={"speakers": created}),
        [cue],
        [cluster],
    )

    assert len(created) == 1
    assert created[0].speaker_id == "speaker_01"
    assert created[0].acoustic_cluster_ids == ["cluster_01"]
    assert bound_cues[0].speaker_id == "speaker_01"
    assert bound_clusters[0].business_speaker_id == "speaker_01"
    assert len(reused) == 1
    assert rebound_cues[0].speaker_id == "speaker_01"


def test_asr_operation_defaults_to_auto_diarization_and_reports_review():
    cluster = VideoLocalizationSpeakerCluster(
        cluster_id="cluster_01",
        source_label="S01",
        source_engine_id="moss-transcribe-diarize-mlx",
        start_ms=0,
        end_ms=1000,
        duration_ms=1000,
        segment_count=1,
        merge_status="needs_review",
    )
    draft = VideoLocalizationDraft(
        transcription=VideoLocalizationTranscriptionState(
            diarization_status="partial",
            diarization_engine_id="moss-transcribe-diarize-mlx",
            speaker_clusters=[cluster],
            quality_flags=["speaker_cluster_review_required"],
        )
    )

    parameters = operation_queue._normalized_operation_parameters("english_asr", {}, draft)
    summary = operation_state.english_asr_summary(draft)

    assert parameters["diarization_engine_id"] == "auto"
    assert summary["diarization_status"] == "partial"
    assert summary["speaker_count"] == 1
    assert summary["speaker_review_required"] is True


def test_asr_task_detail_exposes_speaker_clusters_and_overlap_review():
    cluster = VideoLocalizationSpeakerCluster(
        cluster_id="cluster_01",
        source_label="S01",
        source_engine_id="moss-transcribe-diarize-mlx",
        start_ms=0,
        end_ms=1000,
        duration_ms=1000,
        segment_count=1,
        merge_status="needs_review",
    )
    segment = VideoLocalizationTranscriptSegment(
        segment_id="asr_0001",
        start_ms=0,
        end_ms=1000,
        raw_text="Hello.",
        speaker_cluster_id="cluster_01",
        has_speaker_overlap=True,
    )
    draft = VideoLocalizationDraft(
        transcription=VideoLocalizationTranscriptionState(
            engine_id="qwen3-asr-mlx",
            segments=[segment],
            diarization_status="partial",
            diarization_engine_id="moss-transcribe-diarize-mlx",
            speaker_clusters=[cluster],
            quality_flags=["speaker_overlap_review_required"],
        )
    )

    result = asr_step_results.build_asr_step_results(
        draft,
        {"diarization": {"cluster_count": 1}},
    )["diarization"]

    assert result["status"] == "warning"
    assert "1 位匿名说话人" in result["summary"]
    assert {"label": "重叠片段", "value": "1"} in result["metrics"]
    assert result["sections"][0]["items"][0]["facts"][2]["value"] == "需要人工复核"


def test_knowledge_enrichment_requires_evidence_and_keeps_identity_as_candidate(monkeypatch):
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "entities": [
                {"name": "Seedance 2.0", "evidence_source_ids": ["source_01"]},
                {"name": "Unsupported", "evidence_source_ids": ["missing"]},
            ],
            "claims": [{"text": "A supported claim", "evidence_source_ids": ["source_01"]}],
            "speaker_identity_candidates": [
                {
                    "speaker_id": "speaker_01",
                    "name": "Example Person",
                    "confidence": 0.99,
                    "reason": "The source names the presenter",
                    "evidence_source_ids": ["source_01"],
                    "status": "confirmed",
                },
                {
                    "speaker_id": "speaker_01",
                    "name": "No Evidence",
                    "confidence": 1.0,
                    "evidence_source_ids": ["missing"],
                },
            ],
        },
    )
    context = {"overview": "Demo", "topics": ["VFX"], "speakers": [{"speaker_id": "speaker_01"}]}
    research = {
        "questions": [
            {
                "sources": [
                    {
                        "source_id": "source_01",
                        "title": "Evidence",
                        "url": "https://example.com/evidence",
                        "snippet": "Example Person presents Seedance 2.0",
                    }
                ]
            }
        ]
    }

    enriched = localization._enrich_context_with_research(
        context,
        research,
        profile_id="review",
        is_cancelled=None,
    )
    next_draft = localization._with_localized_track(
        VideoLocalizationDraft(speakers=[VideoLocalizationSpeaker(speaker_id="speaker_01")]),
        [],
        fingerprint="fingerprint",
        context=enriched,
        research=research,
        source_language="en",
        target_language="zh-Hans",
        profile_id="review",
        model_id="model",
        localization_level="L1",
        worldview_permeability="W0",
    )
    context_result = localization._context_step_result(enriched, next_draft)

    assert [item["name"] for item in enriched["knowledge"]["entities"]] == ["Seedance 2.0"]
    assert len(enriched["knowledge"]["speaker_identity_candidates"]) == 1
    candidate = next_draft.speakers[0].identity_candidates[0]
    assert candidate.name == "Example Person"
    assert candidate.confidence == 0.95
    assert candidate.status == "candidate"
    assert candidate.evidence_source_ids == ["source_01"]
    assert {"label": "身份候选", "value": "1"} in context_result["metrics"]
    assert context_result["sections"][1]["title"] == "身份候选（待人工确认）"
    assert context_result["sections"][1]["items"][0]["facts"][1]["value"] == "95%"


def test_knowledge_enrichment_failure_does_not_block_localization(monkeypatch):
    monkeypatch.setattr(
        localization.llm_runtime,
        "complete_json",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("knowledge unavailable")),
    )

    enriched = localization._enrich_context_with_research(
        {"overview": "Demo"},
        {"questions": [{"sources": [{"source_id": "source_01"}]}]},
        profile_id="review",
        is_cancelled=None,
    )

    assert enriched["knowledge"]["status"] == "failed"
    assert enriched["knowledge"]["speaker_identity_candidates"] == []
