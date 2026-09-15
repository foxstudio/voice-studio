from __future__ import annotations

import re

from app.domains.video_localization import workflow_contracts
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.domains.video_localization.timeline_timecode import (
    format_timeline_duration,
    format_timeline_position,
    format_timeline_range,
)


HUMAN_DETAIL_LIMIT = 250
FOCUSED_DETAIL_LIMIT = 120

STEP_PURPOSES = {
    "asr": "把音轨中的讲话转成原始文字，先保留模型实际听到的内容，不在这一步改写。",
    "diarization": "根据声音特征区分匿名说话人，并用声纹相似度合并可能被误拆的同一人。",
    "initial_analysis_join": "确认听写和说话人结果来自同一音轨，再整理成谁在什么时候说了什么。",
    "transcript_quality_gate": "确认全文和时间结构可以安全进入校时；低把握文字采用当前最高概率结果继续，并保留建议复听提示。",
    "alignment": "用校对后的完整文本重新贴合音频，为每个词确定出现时间，供后续字幕切分使用。",
    "audio_boundaries": "检查相邻词之间的静音和能量变化，找出自然停顿，而不是只按字数切字幕。",
    "boundary_review": "根据逐词时间码、标点、说话人变化、声音停顿和显示长度在本地确定字幕断句。",
    "subtitle_track": "按最终文字、说话人边界和断句结论生成字幕条，并检查空文本和时间重叠。",
}

ASR_FLOW_STEP_META = {
    "asr": ("生成原始听写", 10),
    "diarization": ("区分说话人", 20),
    "initial_analysis_join": ("汇合听写与说话人", 25),
    "alignment": ("对齐逐词时间", 300),
    "audio_boundaries": ("分析声音停顿", 310),
    "boundary_review": ("本地确定字幕断句", 320),
    "subtitle_track": ("生成并检查字幕轨", 330),
}

_ASR_WORKFLOW_TASKS = {
    task.id: task for stage in workflow_contracts.ASR_WORKFLOW_DEFINITION.stages for task in stage.atomic_tasks
}

_LLM_REVIEW_STEPS = {
    "understand_document",
    "visual_evidence",
    "research",
    "normalize_entities",
    "section_review_r1",
    "review_decisions_r1",
    "whole_recheck_r1",
}

_NON_ASR_QUALITY_PREFIXES = (
    "AUDIO_ROUTE_",
    "LOCALIZATION_",
    "LOCALIZED_",
    "REFERENCE_",
    "TTS_",
    "ZH_",
)


def build_asr_step_results(draft: VideoLocalizationDraft, stages: dict) -> dict[str, dict]:
    transcription = draft.transcription
    if transcription is None:
        return {}

    segments = transcription.segments
    words = transcription.words
    frame_rate = draft.source_media.frame_rate or 30.0
    segment_by_id = {segment.segment_id: segment for segment in segments}

    def stage(name: str) -> dict:
        value = stages.get(name)
        return value if isinstance(value, dict) else {}

    word_by_id = {word.word_id: word for word in words}

    visible_segments = _bounded_evenly(segments, HUMAN_DETAIL_LIMIT)
    asr_items = [
        {
            "title": f"片段 {index}",
            "text": _compact_text(segment.raw_text),
            "meta": _time_range(segment.start_ms, segment.end_ms, frame_rate=frame_rate),
        }
        for index, segment in enumerate(visible_segments, start=1)
        if segment.raw_text.strip()
    ]
    incomplete_range_items = [
        {
            "title": _time_range(
                item.start_ms,
                item.end_ms,
                frame_rate=frame_rate,
            ),
            "text": "这段没有生成可用的源语言听写，后续字幕和配音会跳过。",
            "meta": item.reason or "未返回可用结果",
            "tone": "warning",
        }
        for item in transcription.raw_asr_incomplete_ranges
    ]
    asr_result = {
        "status": (
            "warning"
            if segments and transcription.raw_asr_warning_codes
            else "success"
            if segments
            else "failed"
        ),
        "purpose": STEP_PURPOSES["asr"],
        "summary": (
            f"识别到 {len(segments)} 个原始语音片段；另有 {len(incomplete_range_items)} 段未识别。"
            if segments and incomplete_range_items
            else f"识别到 {len(segments)} 个原始语音片段。"
            if segments
            else "没有识别到有效语音文本。"
        ),
        "metrics": _metrics(
            ("识别引擎", transcription.engine_id),
            ("语言", _language_label(transcription.language)),
            ("原始片段", len(segments)),
            ("未识别范围", len(incomplete_range_items)),
            ("详情已展示", f"{len(asr_items)} / {len(segments)} 条"),
        ),
        "coverage": _coverage(len(asr_items), len(segments), "个原始片段"),
        "sections": _sections(
            (
                "完整识别片段"
                if len(asr_items) == len(segments)
                else "识别片段重点展示",
                asr_items,
            ),
            ("未识别范围", incomplete_range_items),
        ),
        "notes": [
            "这里的片段时间是 ASR 的粗略定位；最终字幕时间以后续“对齐逐词时间”的结果为准。",
            *(
                ["未识别范围会保留为任务提醒，不会让其余可用字幕整体失败。"]
                if incomplete_range_items
                else []
            ),
        ],
    }

    speaker_clusters = transcription.speaker_clusters
    review_clusters = [item for item in speaker_clusters if item.merge_status == "needs_review"]
    auto_merged_clusters = [item for item in speaker_clusters if item.merge_status == "auto_merged"]
    overlap_segments = [item for item in segments if item.has_speaker_overlap]
    unassigned_words = [
        word
        for word in words
        if not word.speaker_cluster_id and transcription.diarization_status in {"completed", "partial"}
    ]
    if transcription.diarization_status == "completed":
        diarization_summary = f"区分出 {len(speaker_clusters)} 位匿名说话人，声纹簇检查已完成。"
    elif transcription.diarization_status == "partial":
        diarization_summary = (
            f"区分出 {len(speaker_clusters)} 位匿名说话人，"
            f"其中 {len(review_clusters)} 个声纹簇或 {len(overlap_segments)} 个重叠片段需要复核。"
        )
    elif transcription.diarization_status == "failed":
        diarization_summary = "说话人区分失败；主转写已继续完成，请人工核对说话人。"
    else:
        diarization_summary = "本次没有启用说话人区分。"
    cluster_items = [
        {
            "title": cluster.cluster_id,
            "text": ("、".join(cluster.merged_source_labels) if cluster.merged_source_labels else cluster.source_label),
            "meta": _time_range(cluster.start_ms, cluster.end_ms, frame_rate=frame_rate),
            "facts": [
                {"label": "语音片段", "value": str(cluster.segment_count)},
                {"label": "有效时长", "value": _duration_label(cluster.duration_ms, frame_rate=frame_rate)},
                {
                    "label": "合并判断",
                    "value": {
                        "auto_merged": "已自动合并同一人",
                        "needs_review": "需要人工复核",
                        "original": "保持原始分组",
                    }[cluster.merge_status],
                },
            ],
            "tone": "warning" if cluster.merge_status == "needs_review" else "neutral",
        }
        for cluster in speaker_clusters
    ]
    overlap_items = [
        {
            "title": f"重叠讲话 · {segment.segment_id}",
            "text": _compact_text(segment.corrected_text or segment.raw_text),
            "meta": _time_range(segment.start_ms, segment.end_ms, frame_rate=frame_rate),
            "facts": [
                {"label": "当前说话人", "value": segment.speaker_cluster_id or "尚未确定"},
                {"label": "处理建议", "value": "人工试听确认是否漏掉副声部"},
            ],
            "tone": "warning",
        }
        for segment in overlap_segments[:HUMAN_DETAIL_LIMIT]
    ]
    diarization_result = {
        "status": (
            "warning"
            if (unassigned_words or (transcription.diarization_status == "failed" and segments))
            else _result_status(transcription.diarization_status)
        ),
        "purpose": STEP_PURPOSES["diarization"],
        "summary": diarization_summary,
        "metrics": _metrics(
            ("区分引擎", transcription.diarization_engine_id),
            ("匿名说话人", len(speaker_clusters)),
            ("自动合并", len(auto_merged_clusters)),
            ("需要复核", len(review_clusters)),
            ("重叠片段", len(overlap_segments)),
            ("未匹配词语", len(unassigned_words)),
        ),
        "coverage": _coverage(len(cluster_items), len(speaker_clusters), "个说话人声纹簇"),
        "sections": _sections(("说话人声纹簇", cluster_items), ("需要人工试听的重叠片段", overlap_items)),
        "notes": _notes(
            transcription.diarization_error,
            f"有 {len(unassigned_words)} 个已经对齐的词没有匹配到说话人，需要人工试听确认。"
            if unassigned_words
            else None,
        ),
    }
    matched_segments = [item for item in segments if item.speaker_cluster_id]
    unmatched_segments = [item for item in segments if not item.speaker_cluster_id]
    low_confidence_segments = [
        item for item in segments if item.speaker_confidence is not None and item.speaker_confidence < 0.6
    ]
    joined_samples = [
        {
            "title": segment.speaker_cluster_id or "说话人待确认",
            "text": _compact_text(segment.raw_text),
            "meta": _time_range(segment.start_ms, segment.end_ms, frame_rate=frame_rate),
            "facts": [
                {
                    "label": "匹配可信度",
                    "value": (
                        f"{round(segment.speaker_confidence * 100)}%"
                        if segment.speaker_confidence is not None
                        else "未提供"
                    ),
                },
                {
                    "label": "重叠讲话",
                    "value": "是" if segment.has_speaker_overlap else "否",
                },
            ],
            "tone": (
                "warning"
                if not segment.speaker_cluster_id
                or segment.has_speaker_overlap
                or (segment.speaker_confidence is not None and segment.speaker_confidence < 0.6)
                else "neutral"
            ),
        }
        for segment in _bounded_evenly(segments, 8)
    ]
    join_warning = bool(
        unmatched_segments
        or low_confidence_segments
        or overlap_segments
        or transcription.diarization_status in {"failed", "partial"}
    )
    join_result = {
        "status": "warning" if join_warning else "success",
        "purpose": STEP_PURPOSES["initial_analysis_join"],
        "summary": (f"已汇合 {len(segments)} 个听写片段，其中 {len(matched_segments)} 个已匹配匿名说话人。"),
        "metrics": _metrics(
            ("听写片段", len(segments)),
            ("匿名说话人", len(speaker_clusters)),
            ("已匹配", len(matched_segments)),
            ("未匹配", len(unmatched_segments)),
            ("低可信度", len(low_confidence_segments)),
            ("重叠讲话", len(overlap_segments)),
        ),
        "coverage": _coverage(
            len(joined_samples),
            len(segments),
            "个汇合后片段",
            focused_reason="从整篇结果中均匀展示样例；完整结果继续供后续全文理解和时间对齐使用。",
        ),
        "sections": _sections(("汇合后的讲话样例", joined_samples)),
        "notes": _notes(
            "有部分听写片段没有匹配到匿名说话人，需要结合原音复核。" if unmatched_segments else None,
            "有低可信度或重叠讲话片段，人物归属可能需要人工试听。"
            if low_confidence_segments or overlap_segments
            else None,
            transcription.diarization_error,
        ),
        "debug": {
            "description": "用于确认上游输入、输出契约和汇合完整性，不影响正常查看结果。",
            "metrics": _metrics(
                ("听写输入契约", "asr-raw-v2"),
                ("说话人输入契约", "speaker-diarization-v1"),
                ("汇合输出契约", "asr-joined-transcript-v1"),
                ("来源音轨", transcription.source_track_id),
                ("音频指纹", _short_fingerprint(transcription.source_audio_sha256)),
                ("输入片段", len(segments)),
                ("输出片段", len(segments)),
            ),
            "sections": [],
            "notes": [],
        },
    }

    confidence_counts = {
        level: sum(word.timing_confidence == level for word in words) for level in ("high", "medium", "low")
    }
    confidence_order = {"low": 0, "medium": 1, "high": 2}
    review_words = [word for word in words if word.timing_confidence in {"low", "medium"}]
    if not review_words:
        review_words = _sample_evenly(words, min(12, len(words)))
    visible_alignment_words = _bounded_evenly(review_words, FOCUSED_DETAIL_LIMIT)
    alignment_samples = [
        {
            "title": f"“{_compact_text(word.text, 80)}”的出现位置",
            "text": (
                "这个词从 "
                f"{_timecode(word.start_ms, frame_rate=frame_rate)}"
                " 开始，到 "
                f"{_timecode(word.end_ms, frame_rate=frame_rate)} 结束。"
            ),
            "meta": f"时间可信度：{_confidence_label(word.timing_confidence)}",
            "facts": [
                {"label": "持续时间", "value": _duration_label(word.end_ms - word.start_ms, frame_rate=frame_rate)},
                {"label": "定位方式", "value": _timing_source_label(word.timing_source)},
            ],
            "tone": "warning" if word.timing_confidence == "low" else "neutral",
        }
        for word in sorted(
            visible_alignment_words,
            key=lambda item: (confidence_order.get(item.timing_confidence, 3), item.start_ms),
        )
    ]
    low_confidence_words = sorted(
        (word for word in words if word.timing_confidence == "low"),
        key=lambda item: item.start_ms,
    )
    alignment_review_targets = _alignment_review_targets(
        low_confidence_words,
        segments,
        frame_rate=frame_rate,
    )
    alignment_notes = _alignment_notes(
        transcription.alignment_error,
        segment_by_id,
        frame_rate=frame_rate,
    )
    alignment_result = {
        "status": (
            "warning"
            if transcription.alignment_status == "failed" and words
            else _result_status(transcription.alignment_status)
        ),
        "purpose": STEP_PURPOSES["alignment"],
        "summary": (
            f"为 {len(words)} 个词生成时间码，整体可信度为{_confidence_label(transcription.timing_confidence)}。"
            if words
            else "没有生成可用的逐词时间码。"
        ),
        "metrics": _metrics(
            ("对齐引擎", transcription.alignment_engine_id),
            ("已定位词语", len(words)),
            ("整体可靠程度", _confidence_label(transcription.timing_confidence)),
            ("位置可靠", confidence_counts["high"]),
            ("建议抽查", confidence_counts["medium"]),
            ("需要复核", confidence_counts["low"]),
        ),
        "coverage": _coverage(
            len(alignment_samples),
            len(words),
            "个逐词时间码",
            focused_reason="逐词时间码数量较大，不适合逐条人工阅读；这里优先展示全部低、中可信度词，若没有异常则展示代表样例。",
        ),
        "sections": _sections(("需要重点看的词", alignment_samples)),
        "review_targets": alignment_review_targets,
        "notes": alignment_notes,
    }

    boundary_counts = {
        level: sum(item.confidence == level for item in transcription.audio_boundary_features)
        for level in ("high", "medium", "low", "none")
    }
    meaningful_boundaries = [
        item for item in transcription.audio_boundary_features if item.confidence in {"high", "medium"}
    ]
    if not meaningful_boundaries:
        meaningful_boundaries = sorted(
            transcription.audio_boundary_features,
            key=lambda value: value.gap_ms,
            reverse=True,
        )[:12]
    visible_boundaries = _bounded_evenly(meaningful_boundaries, FOCUSED_DETAIL_LIMIT)
    boundary_samples = [
        {
            "title": f"“{word_by_id.get(item.left_word_id).text if word_by_id.get(item.left_word_id) else item.left_word_id}”与“"
            f"{word_by_id.get(item.right_word_id).text if word_by_id.get(item.right_word_id) else item.right_word_id}”之间",
            "text": _boundary_plain_summary(
                item.gap_ms,
                item.low_energy_ms,
                item.confidence,
                frame_rate=frame_rate,
            ),
            "meta": f"停顿判断：{_confidence_label(item.confidence)}可信",
            "facts": [
                {"label": "两词间隔", "value": _duration_label(item.gap_ms, frame_rate=frame_rate)},
                {"label": "安静部分", "value": _duration_label(item.low_energy_ms, frame_rate=frame_rate)},
                {"label": "声音下降", "value": f"{item.energy_drop_db:.1f} dB"},
            ],
            "visual": {
                "label": "安静部分占两词间隔",
                "value": item.low_energy_ms,
                "max": max(1, item.gap_ms),
            },
            "tone": "positive" if item.confidence == "high" else "neutral",
        }
        for item in sorted(visible_boundaries, key=lambda value: value.start_ms)
    ]
    audio_boundary_result = {
        "status": (
            "warning"
            if (transcription.audio_boundary_status == "failed" and words)
            else _result_status(transcription.audio_boundary_status)
        ),
        "purpose": STEP_PURPOSES["audio_boundaries"],
        "summary": f"检查了 {len(transcription.audio_boundary_features)} 个相邻词之间的声音变化，找出可用于断句的停顿。",
        "metrics": _metrics(
            ("检查位置", len(transcription.audio_boundary_features)),
            ("校准字幕入点", len(transcription.subtitle_entry_by_word_id)),
            ("明显停顿", boundary_counts["high"]),
            ("可能停顿", boundary_counts["medium"]),
            ("较弱停顿", boundary_counts["low"]),
        ),
        "coverage": _coverage(
            len(boundary_samples),
            len(transcription.audio_boundary_features),
            "个相邻词边界",
            focused_reason="完整声学表包含大量没有明显停顿的位置；这里优先完整展示明显和可能停顿，其余数据继续参与字幕算法。",
        ),
        "sections": _sections(("可用于断句的声音停顿", boundary_samples)),
        "notes": _notes(transcription.audio_boundary_error),
    }

    decision_counts = {
        decision: sum(item.decision == decision for item in transcription.boundary_reviews)
        for decision in ("prefer", "allow", "avoid")
    }
    review_samples = []
    word_index = {word.word_id: index for index, word in enumerate(words)}
    visible_reviews = _bounded_evenly(transcription.boundary_reviews, HUMAN_DETAIL_LIMIT)
    for item in visible_reviews:
        left = word_by_id.get(item.left_word_id)
        right = word_by_id.get(item.right_word_id)
        continuous, split = _boundary_context(item.left_word_id, item.right_word_id, words, word_index)
        review_samples.append(
            {
                "title": f"在“{left.text if left else item.left_word_id} / {right.text if right else item.right_word_id}”之间",
                "text": _plain_boundary_reason(item.reason),
                "before": continuous,
                "after": split,
                "before_label": "连续阅读",
                "after_label": "断句预览",
                "meta": f"{_decision_label(item.decision)} · 把握 {round(item.confidence * 100)}%",
                "tone": {"prefer": "positive", "allow": "neutral", "avoid": "warning"}.get(item.decision, "neutral"),
            }
        )
    review_timing = stage("boundary_review")
    local_boundary_stage = (
        transcription.boundary_review_prompt_version == "boundary-review-local-v1"
        and not transcription.boundary_review_model_id
    )
    boundary_review_result = {
        "status": (
            "warning"
            if (transcription.boundary_review_status == "failed" and segments)
            else _result_status(transcription.boundary_review_status)
        ),
        "purpose": STEP_PURPOSES["boundary_review"],
        "summary": (
            f"本地确定 {int(review_timing.get('candidate_count') or 0)} 个字幕边界，没有额外请求语言模型。"
            if local_boundary_stage
            else _boundary_review_summary(
                transcription.boundary_review_status,
                len(transcription.boundary_reviews),
                int(review_timing.get("candidate_count") or 0),
            )
        ),
        "metrics": (
            _metrics(
                ("本地断句", review_timing.get("candidate_count")),
                ("模型请求", 0),
            )
            if local_boundary_stage
            else _metrics(
                ("语义模型", transcription.boundary_review_model_id),
                ("本轮模型候选", review_timing.get("candidate_count")),
                ("复用既有判断", review_timing.get("reused_review_count")),
                ("全部可用判断", len(transcription.boundary_reviews)),
                ("建议断开", decision_counts["prefer"]),
                ("可断可不断", decision_counts["allow"]),
                ("建议连着", decision_counts["avoid"]),
                ("复核轮数", review_timing.get("round_count")),
                ("请求批次", review_timing.get("batch_count")),
            )
        ),
        "coverage": (
            None
            if local_boundary_stage
            else _coverage(len(review_samples), len(transcription.boundary_reviews), "个语义断句判断")
        ),
        "sections": (
            [
                {
                    "title": "本地断句结果",
                    "items": [
                        {
                            "title": "断句依据",
                            "text": (
                                "综合逐词时间码、标点、说话人变化、声音停顿和显示长度，"
                                "本地确定字幕边界；结果已直接用于生成字幕轨。"
                            ),
                            "facts": _metrics(
                                (
                                    "检查边界",
                                    int(review_timing.get("candidate_count") or 0),
                                ),
                                ("额外模型判断", 0),
                            ),
                            "links": [],
                            "tone": "positive",
                        }
                    ],
                }
            ]
            if local_boundary_stage
            else _sections(("逐项断句判断", review_samples))
        ),
        "notes": _notes(transcription.boundary_review_error),
    }

    asr_cues = [cue for cue in draft.cues if "generated_by_asr" in cue.quality_flags]
    if not asr_cues:
        asr_cues = [
            cue for cue in draft.cues if cue.en_subtitle_text and cue.start_ms is not None and cue.end_ms is not None
        ]
    ordered_cues = sorted(asr_cues, key=lambda cue: (cue.start_ms or 0, cue.end_ms or 0, cue.cue_id))
    overlaps = sum(
        previous.end_ms is not None and current.start_ms is not None and previous.end_ms > current.start_ms
        for previous, current in zip(ordered_cues, ordered_cues[1:])
    )
    empty_cues = sum(not (cue.en_subtitle_text or "").strip() for cue in ordered_cues)
    speaker_review_needed = bool(review_clusters or unassigned_words or overlap_segments)
    boundary_review_needed = transcription.boundary_review_status in {"partial", "failed"}
    upstream_review_needed = speaker_review_needed or boundary_review_needed
    visible_cues = _bounded_evenly(ordered_cues, HUMAN_DETAIL_LIMIT)
    cue_items = [
        {
            "title": f"字幕 {index}",
            "text": _compact_text(cue.en_subtitle_text),
            "meta": _time_range(cue.start_ms, cue.end_ms, frame_rate=frame_rate),
            "facts": [
                {
                    "label": "时长",
                    "value": _duration_label((cue.end_ms or 0) - (cue.start_ms or 0), frame_rate=frame_rate),
                },
                {"label": "说话人", "value": cue.speaker_id or cue.speaker_cluster_id or "尚未指定"},
                {"label": "时间可信度", "value": _confidence_label(cue.timing_confidence or "none")},
            ],
            "tone": "warning" if not (cue.en_subtitle_text or "").strip() else "neutral",
        }
        for index, cue in enumerate(visible_cues, start=1)
    ]
    final_asr_gate = _final_asr_quality_gate(draft)
    final_blockers = final_asr_gate["blockers"]
    final_warnings = final_asr_gate["warnings"]
    final_issue_count = len(final_blockers) + len(final_warnings)
    subtitle_result = {
        "status": (
            "failed"
            if not ordered_cues
            else "warning"
            if overlaps or empty_cues or upstream_review_needed or final_issue_count
            else "success"
        ),
        "purpose": STEP_PURPOSES["subtitle_track"],
        "summary": (
            "没有找到本次生成的 ASR 字幕。"
            if not ordered_cues
            else (
                f"已写入 {len(ordered_cues)} 条 ASR 字幕；最终检查发现 "
                f"{len(final_blockers)} 项必须处理的问题、"
                f"{len(final_warnings)} 项建议复核。"
            )
            if final_issue_count
            else f"已写入 {len(ordered_cues)} 条 ASR 字幕，但发现 {overlaps} 处时间重叠、{empty_cues} 条空文本。"
            if overlaps or empty_cues
            else f"已写入 {len(ordered_cues)} 条 ASR 字幕，基础完整性检查通过。"
        ),
        "metrics": _metrics(
            ("字幕数量", len(ordered_cues)),
            ("时间重叠", overlaps),
            ("空文本", empty_cues),
            ("说话人复核", "需要" if speaker_review_needed else "不需要"),
            ("断句复核", _status_label(transcription.boundary_review_status)),
            ("最终硬问题", len(final_blockers)),
            ("最终提醒", len(final_warnings)),
            ("详情已展示", f"{len(cue_items)} / {len(ordered_cues)} 条"),
            ("覆盖时段", _cue_time_range(ordered_cues, frame_rate=frame_rate)),
        ),
        "coverage": _coverage(len(cue_items), len(ordered_cues), "条最终字幕"),
        "sections": _sections(("最终写入的 ASR 字幕", cue_items)),
        "review_targets": [_final_quality_issue_target(issue) for issue in [*final_blockers, *final_warnings]],
        "final_quality_gate": {
            "status": final_asr_gate["status"],
            "blocker_count": len(final_blockers),
            "warning_count": len(final_warnings),
        },
        "notes": [],
    }

    persisted_quality_cycle = (
        transcription.transcript_quality_cycle if isinstance(transcription.transcript_quality_cycle, dict) else {}
    )
    transcript_quality_result = _final_transcript_quality_result(
        transcription,
        persisted_quality_cycle,
        frame_rate=frame_rate,
    )
    if transcript_quality_result is None:
        raw_transcript_quality_result = persisted_quality_cycle.get("step_result")
        if not isinstance(raw_transcript_quality_result, dict):
            raw_transcript_quality_result = stage("transcript_quality_gate").get("step_result")
        if not isinstance(raw_transcript_quality_result, dict):
            raise ValueError("current ASR result is missing transcript quality gate output")
        transcript_quality_result = raw_transcript_quality_result

    base_results = {
        "asr": asr_result,
        "diarization": diarization_result,
        "initial_analysis_join": join_result,
        "transcript_quality_gate": transcript_quality_result,
        "alignment": alignment_result,
        "audio_boundaries": audio_boundary_result,
        "boundary_review": boundary_review_result,
        "subtitle_track": subtitle_result,
    }
    flow_results = persisted_quality_cycle.get("task_step_results")
    if not str(persisted_quality_cycle.get("prompt_version") or "").startswith("asr-flow-v") or not isinstance(
        flow_results, dict
    ):
        raise ValueError("current ASR result is missing typed review step outputs")

    static_results = {
        step_id: {
            **base_results[step_id],
            "label": ASR_FLOW_STEP_META[step_id][0],
            "order": ASR_FLOW_STEP_META[step_id][1],
        }
        for step_id in ASR_FLOW_STEP_META
    }
    persisted_flow_results = {
        str(step_id): _reader_ready_flow_result(str(step_id), result)
        for step_id, result in flow_results.items()
        if isinstance(result, dict)
    }
    results = {
        "asr": static_results["asr"],
        **persisted_flow_results,
        "transcript_quality_gate": {
            **_reader_ready_flow_result(
                "transcript_quality_gate",
                transcript_quality_result,
            ),
            "label": "进入校时前检查",
            "order": 290,
        },
        "alignment": static_results["alignment"],
        "audio_boundaries": static_results["audio_boundaries"],
        "boundary_review": static_results["boundary_review"],
        "subtitle_track": static_results["subtitle_track"],
    }
    if (
        transcription.diarization_status not in {"not_run", "skipped"}
        or transcription.diarization_engine_id
        or transcription.speaker_clusters
    ):
        results = {
            "asr": static_results["asr"],
            "diarization": {
                **diarization_result,
                "label": ASR_FLOW_STEP_META["diarization"][0],
                "order": ASR_FLOW_STEP_META["diarization"][1],
            },
            "initial_analysis_join": {
                **join_result,
                "label": ASR_FLOW_STEP_META["initial_analysis_join"][0],
                "order": ASR_FLOW_STEP_META["initial_analysis_join"][1],
            },
            **{
                step_id: result
                for step_id, result in results.items()
                if step_id != "asr"
            },
        }
    return _with_default_step_debug(results, transcription, stages)


def _final_asr_quality_gate(draft: VideoLocalizationDraft) -> dict:
    blockers = [issue for issue in draft.quality_gate.blockers if not issue.code.startswith(_NON_ASR_QUALITY_PREFIXES)]
    warnings = [issue for issue in draft.quality_gate.warnings if not issue.code.startswith(_NON_ASR_QUALITY_PREFIXES)]
    return {
        "status": ("blocked" if blockers else "warning" if warnings else "pass"),
        "blockers": blockers,
        "warnings": warnings,
    }


def _final_quality_issue_target(issue) -> dict:
    return {
        "title": "最终字幕检查",
        "detail": str(issue.message),
    }


def _with_default_step_debug(results: dict[str, dict], transcription, stages: dict) -> dict[str, dict]:
    """Keep one stable observability shape for completed formal ASR steps.

    Rich per-run debug data wins when it was persisted. Current deterministic
    local steps expose facts reconstructed from the final project snapshot.
    """

    enriched: dict[str, dict] = {}
    for step_id, raw_result in results.items():
        result = dict(raw_result)
        status = str(result.get("status") or "").strip().lower()
        if not isinstance(result.get("debug"), dict) and status not in {
            "todo",
            "running",
            "skipped",
        }:
            result["debug"] = _default_step_debug(
                step_id,
                result,
                transcription,
                stages,
            )
        enriched[step_id] = result
    return enriched


def _default_step_debug(step_id: str, result: dict, transcription, stages: dict) -> dict:
    definition = _ASR_WORKFLOW_TASKS.get(step_id)
    stage_timing = stages.get(step_id) if isinstance(stages.get(step_id), dict) else {}
    dependency_labels = []
    if definition is not None:
        for dependency_id in definition.depends_on:
            dependency = _ASR_WORKFLOW_TASKS.get(dependency_id)
            dependency_labels.append(dependency.label if dependency is not None else dependency_id)

    metrics = _metrics(
        (
            "输出契约",
            definition.output_contract_version if definition is not None else None,
        ),
        ("依赖步骤", "、".join(dependency_labels)),
        ("来源音轨", transcription.source_track_id),
        ("音频指纹", _short_fingerprint(transcription.source_audio_sha256)),
        (
            "处理耗时",
            _duration_label(stage_timing.get("duration_ms")) if stage_timing.get("duration_ms") is not None else None,
        ),
    )
    if step_id == "asr":
        metrics.extend(_metrics(("识别引擎", transcription.engine_id)))
    elif step_id == "diarization":
        metrics.extend(
            _metrics(
                ("区分引擎", transcription.diarization_engine_id),
                ("区分模型", transcription.diarization_model_id),
            )
        )
    elif step_id in _LLM_REVIEW_STEPS:
        metrics.extend(
            _metrics(
                ("模型配置", transcription.review_profile_id),
                ("实际模型", transcription.review_model_id),
                ("流程版本", transcription.review_prompt_version),
            )
        )
    elif step_id == "transcript_quality_gate":
        metrics.extend(
            _metrics(
                ("输入结果", "已完成的全文复核"),
                ("处理方式", "本地只读检查"),
                ("模型调用", "0"),
            )
        )
    elif step_id == "alignment":
        metrics.extend(
            _metrics(
                ("对齐引擎", transcription.alignment_engine_id),
                ("对齐状态", transcription.alignment_status),
                ("输出词数", len(transcription.words)),
            )
        )
    elif step_id == "audio_boundaries":
        metrics.extend(
            _metrics(
                ("分析版本", transcription.audio_boundary_analysis_version),
                ("分析状态", transcription.audio_boundary_status),
                ("停顿候选", len(transcription.audio_boundary_features)),
            )
        )
    elif step_id == "boundary_review":
        local_boundary_stage = (
            transcription.boundary_review_prompt_version == "boundary-review-local-v1"
            and not transcription.boundary_review_model_id
        )
        metrics.extend(
            _metrics(
                ("断句状态", transcription.boundary_review_status),
                ("处理规则", transcription.segmentation_profile_id),
                (
                    "本地检查边界" if local_boundary_stage else "语义断句判断",
                    stage_timing.get("candidate_count")
                    if local_boundary_stage
                    else len(transcription.boundary_reviews),
                ),
                ("额外模型判断", 0 if local_boundary_stage else None),
            )
        )
    elif step_id == "subtitle_track":
        subtitle_count = next(
            (
                metric.get("value")
                for metric in result.get("metrics", [])
                if isinstance(metric, dict) and metric.get("label") == "字幕数量"
            ),
            None,
        )
        metrics.extend(_metrics(("写入字幕", subtitle_count)))

    notes = ["这里展示最终任务快照中可追溯的运行信息。"]
    if step_id in _LLM_REVIEW_STEPS:
        notes.append("这条任务没有保存该步骤的调用次数、Token、费用和停止原因；这里不补写或估算历史用量。")
    elif step_id in {
        "transcript_quality_gate",
        "alignment",
        "audio_boundaries",
        "boundary_review",
        "subtitle_track",
    }:
        notes.append("本步骤使用本地处理，不调用通用语言模型。")
    return {
        "description": "用于核对输入来源、上游依赖、运行配置和输出结果。",
        "metrics": metrics,
        "sections": [],
        "notes": notes,
    }


def _reader_ready_flow_result(step_id: str, raw_result: dict) -> dict:
    """Normalize a current saved ASR step for reader display."""

    result = dict(raw_result)
    raw_sections = result.get("sections")
    sections = (
        [
        {
            **section,
                "items": [dict(item) for item in section.get("items", []) if isinstance(item, dict)],
        }
        for section in raw_sections
        if isinstance(section, dict) and isinstance(section.get("items"), list)
        ]
        if isinstance(raw_sections, list)
        else []
    )

    if step_id == "understand_document":
        for section in sections:
            for item in section["items"]:
                meta = str(item.get("meta") or "")
                if meta.startswith("字幕 "):
                    item["meta"] = f"听写片段 {meta.removeprefix('字幕 ')}"
                facts = item.get("facts")
                if isinstance(facts, list):
                    item["facts"] = [
                        {
                            **fact,
                            "label": ("待核对名称" if fact.get("label") == "重点名称" else fact.get("label")),
                        }
                        if isinstance(fact, dict)
                        else fact
                        for fact in facts
                    ]

    if not sections and step_id == "visual_evidence":
        metrics = {
            str(item.get("label") or ""): str(item.get("value") or "")
            for item in result.get("metrics", [])
            if isinstance(item, dict)
        }
        frame_count = metrics.get("截图", "0")
        observation_count = metrics.get("画面观察", "0")
        sections = [
            {
                "title": "画面取证说明",
                "items": [
                    {
                        "title": "这条历史任务只保存了数量",
                        "text": (
                            f"任务记录显示提取了 {frame_count} 张截图、得到 "
                            f"{observation_count} 项画面观察，但当时没有保存逐项观察内容。"
                            "这里不补写或猜测历史结果；新任务会保存可查看的逐项观察。"
                        ),
                        "facts": [],
                        "links": [],
                        "tone": "muted",
                    }
                ],
            }
        ]

    if not sections and step_id == "normalize_entities":
        sections = [
            {
                "title": "检查结论",
                "items": [
                    {
                        "title": "本次无需统一名称",
                        "text": ("已检查候选名称和术语，没有找到证据充分、需要修改的写法，因此保留原文。"),
                        "facts": [],
                        "links": [],
                        "tone": "positive",
                    }
                ],
            }
        ]

    if step_id in {"whole_recheck_r1", "transcript_quality_gate"}:
        warning_items = [item for section in sections for item in section["items"] if item.get("tone") == "warning"]
        if warning_items and all(not str(item.get("meta") or "").strip() for item in warning_items):
            notes = list(result.get("notes") or [])
            notes.append("这条历史任务没有保存这些提醒对应的片段位置；这里只展示当时保存的提醒原文，不补写时间。")
            result["notes"] = notes

    result["sections"] = sections
    return result


def _final_transcript_quality_result(
    transcription,
    quality_cycle: dict,
    *,
    frame_rate: float,
) -> dict | None:
    if not str(quality_cycle.get("prompt_version") or "").startswith("asr-flow-v"):
        return None
    task_step_results = quality_cycle.get("task_step_results")
    persisted_gate_result = (
        task_step_results.get("transcript_quality_gate") if isinstance(task_step_results, dict) else None
    )
    if isinstance(persisted_gate_result, dict):
        return persisted_gate_result

    changes = [item for item in quality_cycle.get("changes", []) if isinstance(item, dict)]
    warnings = [item for item in quality_cycle.get("warnings", []) if isinstance(item, dict)]
    segment_by_id = {segment.segment_id: segment for segment in transcription.segments}
    changed_segment_ids = {str(item.get("segment_id") or "") for item in changes}
    active_warnings = []
    warning_segment_ids: set[str] = set()
    for warning in warnings:
        segment_id = str(warning.get("segment_id") or "")
        message = str(warning.get("message") or "")
        if segment_id and segment_id in changed_segment_ids:
            continue
        if re.search(r"(?:已通过|已经通过|已确认|无需|不需要).{0,12}(?:验证|核对|修改)", message):
            continue
        if segment_id and segment_id in warning_segment_ids:
            continue
        if segment_id:
            warning_segment_ids.add(segment_id)
        active_warnings.append(warning)

    change_items = []
    for change in changes:
        segment = segment_by_id.get(str(change.get("segment_id") or ""))
        change_items.append(
            {
                "title": f"{_compact_text(change.get('before'), 90)} → {_compact_text(change.get('after'), 90)}",
                "before": _compact_text(change.get("before"), 320),
                "after": _compact_text(change.get("after"), 320),
                "before_label": "原来识别成",
                "after_label": "现在改成",
                "text": _plain_quality_change_reason(change.get("reason")),
                "meta": _time_range(
                    segment.start_ms,
                    segment.end_ms,
                    frame_rate=frame_rate,
                )
                if segment
                else "",
                "facts": _metrics(
                    ("处理结果", "已写入最终 ASR 字幕"),
                    ("判断把握", _percentage(change.get("confidence"))),
                ),
                "links": [],
                "tone": "positive",
            }
        )

    warning_items = []
    review_targets = []
    for warning in active_warnings:
        segment_id = str(warning.get("segment_id") or "")
        segment = segment_by_id.get(segment_id)
        excerpt = _compact_text(warning.get("excerpt"), 120)
        detail = _plain_quality_warning(warning.get("message"), excerpt=excerpt)
        location = (
            _time_range(
            segment.start_ms,
            segment.end_ms,
            frame_rate=frame_rate,
            )
            if segment
            else ""
        )
        current_text = _compact_text(segment.corrected_text or segment.raw_text, 320) if segment else ""
        title = f"听一下“{excerpt}”" if excerpt else "还有同类片段需要抽查"
        warning_items.append(
            {
                "title": title,
                "text": detail,
                "meta": location,
                "facts": _metrics(
                    ("当前字幕", current_text),
                    ("处理结果", "当前最高概率结果已保留"),
                ),
                "links": [],
                "tone": "warning",
            }
        )
        review_targets.append(
            {
                "title": title,
                "location": location,
                "detail": detail,
                "excerpt": current_text,
                "start_ms": segment.start_ms if segment else None,
                "end_ms": segment.end_ms if segment else None,
            }
        )

    rounds = int(quality_cycle.get("assessment_rounds") or len(quality_cycle.get("rounds") or []))
    status = "warning" if active_warnings else "success"
    summary = "整篇校对完成，已自动继续校时"
    summary += f"；另有 {len(active_warnings)} 处建议复听。" if active_warnings else "，没有建议复听项。"
    return {
        "status": status,
        "purpose": (
            "通读完整转写，结合前后文和查证资料修正明确的听写错误；"
            "低把握内容采用当前最高概率结果继续，并保留建议复听提示。"
        ),
        "summary": summary,
        "metrics": _metrics(
            ("校对轮次", rounds),
            ("已经改好", len(changes)),
            ("建议复听", len(active_warnings)),
        ),
        "sections": _sections(
            ("已经改好的内容", change_items),
            ("建议复听", warning_items),
        ),
        "review_targets": review_targets,
        "notes": [],
        "coverage": _coverage(
            len(active_warnings),
            len(active_warnings),
            "处建议复听",
        ),
    }


def _compact_text(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _short_fingerprint(value: object) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) <= 12:
        return normalized
    return f"{normalized[:8]}…{normalized[-4:]}"


def _alignment_notes(
    error: object,
    segment_by_id: dict[str, object],
    *,
    frame_rate: float,
) -> list[str]:
    message = _compact_text(error, 500)
    if not message:
        return []
    match = re.search(r"\b(asr_\d+)\b", message)
    segment = segment_by_id.get(match.group(1)) if match else None
    if segment is None:
        return _notes(message)
    text = _compact_text(
        getattr(segment, "corrected_text", None) or getattr(segment, "raw_text", None),
        120,
    )
    location = _time_range(
        int(getattr(segment, "start_ms", 0)),
        int(getattr(segment, "end_ms", 0)),
        frame_rate=frame_rate,
    )
    return [
        (f"{location}“{text}”：这一段没有生成可用的逐词对齐结果，当前时间按原始识别片段估算，建议抽查字幕入点和出点。")
    ]


def _alignment_review_targets(
    low_confidence_words: list,
    segments: list,
    *,
    frame_rate: float,
) -> list[dict]:
    """Keep separate interpolation regions separate in the review summary."""

    if not low_confidence_words:
        return []
    segment_order = {segment.segment_id: index for index, segment in enumerate(segments)}
    segment_by_id = {segment.segment_id: segment for segment in segments}
    affected_ids = sorted(
        {word.segment_id for word in low_confidence_words if word.segment_id in segment_order},
        key=segment_order.__getitem__,
    )
    if not affected_ids:
        return [
            {
                "title": (f"抽查 {len(low_confidence_words)} 个低可信度词"),
                "location": _time_range(
                    min(word.start_ms for word in low_confidence_words),
                    max(word.end_ms for word in low_confidence_words),
                    frame_rate=frame_rate,
                ),
                "detail": (
                    "这一区间的逐词时间主要按原始识别片段估算；"
                    "旧结果没有保存对应片段编号，建议确认字幕入点和出点"
                    "是否自然。"
                ),
            }
        ]
    groups: list[list[str]] = []
    for segment_id in affected_ids:
        if not groups or segment_order[segment_id] != segment_order[groups[-1][-1]] + 1:
            groups.append([segment_id])
        else:
            groups[-1].append(segment_id)

    words_by_segment: dict[str, list] = {}
    for word in low_confidence_words:
        words_by_segment.setdefault(word.segment_id, []).append(word)
    targets = []
    for group in groups:
        group_words = [word for segment_id in group for word in words_by_segment.get(segment_id, [])]
        if not group_words:
            continue
        excerpts = [
            (segment_by_id[segment_id].corrected_text or segment_by_id[segment_id].raw_text).strip()
            for segment_id in group
        ]
        targets.append(
            {
                "title": (f"抽查 {len(group_words)} 个低可信度词"),
                "location": _time_range(
                    min(word.start_ms for word in group_words),
                    max(word.end_ms for word in group_words),
                    frame_rate=frame_rate,
                ),
                "detail": (
                    "这一区间的逐词时间主要按原始识别片段估算；"
                    "建议确认字幕入点和出点是否自然。"
                    f" 当前原文：{_compact_text(' '.join(excerpts), 220)}"
                ),
            }
        )
    return targets


def _metrics(*items: tuple[str, object]) -> list[dict[str, str]]:
    return [{"label": label, "value": str(value)} for label, value in items if value not in {None, ""}]


def _sections(*items: tuple[str, list[dict]]) -> list[dict]:
    return [{"title": title, "items": values} for title, values in items if values]


def _coverage(
    shown_count: int,
    total_count: int,
    unit: str,
    *,
    focused_reason: str | None = None,
) -> dict[str, object]:
    is_complete = shown_count >= total_count and not focused_reason
    return {
        "mode": "complete" if is_complete else "focused",
        "shown_count": shown_count,
        "total_count": total_count,
        "unit": unit,
        **({"reason": focused_reason} if focused_reason else {}),
    }


def _notes(*items: object) -> list[str]:
    notes = []
    for item in items:
        if not item:
            continue
        note = _compact_text(item, 500)
        note = re.sub(r"(?i)(api[_ -]?key|authorization|bearer|token|secret)\s*[:=]\s*\S+", r"\1=[已隐藏]", note)
        note = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-[已隐藏]", note)
        notes.append(note)
    return notes


def _sample_evenly(items: list, limit: int) -> list:
    if len(items) <= limit:
        return list(items)
    if limit <= 1:
        return [items[0]]
    last_index = len(items) - 1
    indexes = [round(index * last_index / (limit - 1)) for index in range(limit)]
    return [items[index] for index in indexes]


def _bounded_evenly(items: list, limit: int) -> list:
    return list(items) if len(items) <= limit else _sample_evenly(items, limit)


def _duration_label(value_ms: int, *, frame_rate: float = 30.0) -> str:
    return format_timeline_duration(value_ms, frame_rate=frame_rate)


def _timing_source_label(value: str) -> str:
    return {
        "forced_aligner": "按声音重新定位",
        "asr_segment_interpolation": "按识别片段估算",
    }.get(value, value)


def _boundary_plain_summary(
    gap_ms: int,
    low_energy_ms: int,
    confidence: str,
    *,
    frame_rate: float = 30.0,
) -> str:
    if confidence == "high":
        return f"这里有 {_duration_label(gap_ms, frame_rate=frame_rate)} 的词间空隙，其中 {_duration_label(low_energy_ms, frame_rate=frame_rate)} 比较安静，是明显的停顿位置。"
    if confidence == "medium":
        return f"这里检测到 {_duration_label(gap_ms, frame_rate=frame_rate)} 的词间空隙，可能适合作为字幕边界，还需要结合语义判断。"
    return (
        f"两词之间相隔 {_duration_label(gap_ms, frame_rate=frame_rate)}，声音停顿不够明显，不能只靠音频决定是否断句。"
    )


def _boundary_context(left_word_id: str, right_word_id: str, words, word_index: dict[str, int]) -> tuple[str, str]:
    left_index = word_index.get(left_word_id)
    right_index = word_index.get(right_word_id)
    if left_index is None or right_index is None:
        return f"{left_word_id} {right_word_id}", f"{left_word_id} ｜ {right_word_id}"
    start = max(0, left_index - 4)
    end = min(len(words), right_index + 5)
    left_words = [word.text for word in words[start : left_index + 1]]
    right_words = [word.text for word in words[right_index:end]]
    continuous = " ".join([*left_words, *right_words])
    split = f"{' '.join(left_words)} ｜ {' '.join(right_words)}"
    return _compact_text(continuous, 320), _compact_text(split, 320)


def _plain_quality_change_reason(value: object) -> str:
    reason = _compact_text(value, 320)
    normalized = reason.lower()
    if "canonical" in normalized or "inconsistent name" in normalized:
        return "结合全文和查证资料，确认这是同一个名称的误识别，已统一成正确写法。"
    if "misheard" in normalized or "misrecogn" in normalized:
        return "结合前后文和查证资料，确认这里听错了名称，已改成正确写法。"
    if _mostly_ascii_text(reason):
        return "结合完整上下文和查证资料确认后，已修正这处听写错误。"
    return reason or "结合完整上下文确认后，已修正这处听写错误。"


def _plain_quality_warning(value: object, *, excerpt: str) -> str:
    message = _compact_text(value, 320)
    normalized = message.lower()
    if "low confidence" in normalized or "no strong evidence" in normalized:
        return f"模型怀疑“{excerpt}”可能听错，但没有足够证据，所以没有自动修改。请听一下原音。"
    if "likely misheard" in normalized:
        return f"前后文里有更常见的说法，模型怀疑“{excerpt}”听错了，但把握不够。请听一下原音。"
    if "pronoun likely misheard" in normalized:
        return "这里的人称和前后文可能对不上，但模型没有足够把握修改。请听一下原音。"
    if "数字或否定关系" in message:
        return f"模型怀疑“{excerpt}”是名称误听，但修改可能带入错误信息，所以保留了原文。请听一下原音。"
    if "无可靠来源确认规范拼写" in message:
        return f"没有查到足够资料确认“{excerpt}”的准确写法，所以没有自动修改。请听一下原音并核对名称。"
    if "孤立的数字" in message:
        return f"“{excerpt}”可能是单独听出来的数字，也可能属于前一句。请听一下这处断点。"
    if "无法确认" in message and "置信度低" in message:
        return f"模型怀疑“{excerpt}”可能听错，但把握很低，所以没有自动修改。请听一下原音。"
    if "同类问题" in message:
        return "还有几处相似的名称或词语没有足够把握自动修改，当前都保留原文。建议按前面的具体片段抽查原音。"
    if _mostly_ascii_text(message):
        return "模型发现这里可能听错，但没有足够把握自动修改。请结合原音确认。"
    return message or "模型发现这里可能听错，但没有足够把握自动修改。请结合原音确认。"


def _percentage(value: object) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return f"{round(max(0.0, min(number, 1.0)) * 100)}%"


def _plain_boundary_reason(value: object) -> str:
    reason = _compact_text(value, 320)
    normalized = reason.lower()
    if "incomplete_syntax" in normalized:
        prefix = "这个位置已被安全规则保护：" if normalized.startswith("protected:") else ""
        return f"{prefix}从这里断开会破坏完整语法或把紧密相连的词组拆开。"
    if "clause_end" in normalized:
        return "前后已经形成相对完整的分句，可以在这里停顿或换一条字幕。"
    if "sentence_end" in normalized:
        return "这里是完整句子的结尾，适合作为字幕边界。"
    if "speaker_change" in normalized:
        return "这里发生说话人变化，字幕应随人物切换断开。"
    if _mostly_ascii_text(reason):
        return "模型结合上下文判断了这个位置是否适合断句。"
    return reason or "模型结合上下文判断了这个位置是否适合断句。"


def _mostly_ascii_text(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return bool(letters) and sum(ord(char) < 128 for char in letters) / len(letters) >= 0.9


def _result_status(status: str) -> str:
    if status == "completed":
        return "success"
    if status in {"partial", "not_configured"}:
        return "warning"
    if status in {"disabled", "not_needed", "skipped", "not_run"}:
        return "skipped"
    return "failed"


def _status_label(status: str) -> str:
    return {
        "completed": "已完成",
        "partial": "部分完成",
        "not_needed": "无需联网",
        "not_configured": "未配置",
        "disabled": "已关闭",
        "skipped": "已跳过",
        "failed": "失败",
    }.get(status, status)


def _confidence_label(value: str) -> str:
    return {"high": "高", "medium": "中", "low": "低", "none": "无"}.get(value, value)


def _decision_label(value: str) -> str:
    return {"prefer": "建议断开", "allow": "可断可不断", "avoid": "建议连着"}.get(value, value)


def _language_label(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return {
        "en": "英语",
        "english": "英语",
        "zh": "中文",
        "zh-cn": "中文",
        "chinese": "中文",
        "ja": "日语",
        "japanese": "日语",
        "ko": "韩语",
        "korean": "韩语",
    }.get(normalized, value)


def _boundary_review_summary(status: str, review_count: int, candidate_count: int) -> str:
    if status == "completed":
        if candidate_count == 0 and review_count:
            return f"复用了 {review_count} 个已有断句判断，本轮不需要再次请求模型。"
        return f"从 {candidate_count} 个候选边界中完成 {review_count} 个语义断句判断。"
    if status == "partial":
        return f"断句复核部分完成，获得 {review_count} 个可用判断。"
    if status in {"not_configured", "skipped"}:
        return "语义断句复核未执行，字幕使用声学边界和规则结果。"
    return "语义断句复核失败，字幕使用声学边界和规则结果。"


def _time_range(
    start_ms: int | None,
    end_ms: int | None,
    *,
    frame_rate: float = 30.0,
) -> str:
    if start_ms is None or end_ms is None:
        return "未记录时间"
    return format_timeline_range(start_ms, end_ms, frame_rate=frame_rate)


def _timecode(value_ms: int, *, frame_rate: float = 30.0) -> str:
    return format_timeline_position(value_ms, frame_rate=frame_rate)


def _cue_time_range(cues: list, *, frame_rate: float = 30.0) -> str:
    if not cues or cues[0].start_ms is None or cues[-1].end_ms is None:
        return "未记录"
    return _time_range(
        cues[0].start_ms,
        cues[-1].end_ms,
        frame_rate=frame_rate,
    )
