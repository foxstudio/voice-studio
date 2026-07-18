from __future__ import annotations

import re

from app.domains.video_localization.schemas import VideoLocalizationDraft


HUMAN_DETAIL_LIMIT = 250
FOCUSED_DETAIL_LIMIT = 120

STEP_PURPOSES = {
    "asr": "把音轨中的讲话转成原始文字，先保留模型实际听到的内容，不在这一步改写。",
    "diarization": "根据声音特征区分匿名说话人，并用声纹相似度合并可能被误拆的同一人。",
    "web_research": "结合全文判断哪些名称、人物或背景需要查证，并记录资料是否真的影响了文本。",
    "text_review": "对照全文、项目术语和查证资料校对转写，只采纳有充分依据且不改变原意的修改。",
    "alignment": "用校对后的完整文本重新贴合音频，为每个词确定出现时间，供后续字幕切分使用。",
    "audio_boundaries": "检查相邻词之间的静音和能量变化，找出自然停顿，而不是只按字数切字幕。",
    "boundary_review": "把声音停顿与上下文语义放在一起判断，决定字幕应该断开、可断或保持连读。",
    "subtitle_track": "按最终文字、说话人边界和断句结论生成字幕条，并检查空文本和时间重叠。",
}


def build_asr_step_results(draft: VideoLocalizationDraft, stages: dict) -> dict[str, dict]:
    transcription = draft.transcription
    if transcription is None:
        return {}

    segments = transcription.segments
    words = transcription.words
    research = transcription.research

    def stage(name: str) -> dict:
        value = stages.get(name)
        return value if isinstance(value, dict) else {}

    word_by_id = {word.word_id: word for word in words}

    visible_segments = _bounded_evenly(segments, HUMAN_DETAIL_LIMIT)
    asr_items = [
        {
            "title": f"片段 {index} · {segment.segment_id}",
            "text": _compact_text(segment.raw_text),
            "meta": _time_range(segment.start_ms, segment.end_ms),
        }
        for index, segment in enumerate(visible_segments, start=1)
        if segment.raw_text.strip()
    ]
    asr_result = {
        "status": "success" if segments else "failed",
        "purpose": STEP_PURPOSES["asr"],
        "summary": f"识别到 {len(segments)} 个原始语音片段。" if segments else "没有识别到有效语音文本。",
        "metrics": _metrics(
            ("识别引擎", transcription.engine_id),
            ("语言", _language_label(transcription.language)),
            ("原始片段", len(segments)),
            ("详情已展示", f"{len(asr_items)} / {len(segments)} 条"),
        ),
        "coverage": _coverage(len(asr_items), len(segments), "个原始片段"),
        "sections": _sections(("完整识别片段" if len(asr_items) == len(segments) else "识别片段重点展示", asr_items)),
        "notes": ["这里的片段时间是 ASR 的粗略定位；最终字幕时间以第 5 步“对齐逐词时间”的结果为准。"],
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
            "text": (
                "、".join(cluster.merged_source_labels)
                if cluster.merged_source_labels
                else cluster.source_label
            ),
            "meta": _time_range(cluster.start_ms, cluster.end_ms),
            "facts": [
                {"label": "语音片段", "value": str(cluster.segment_count)},
                {"label": "有效时长", "value": _duration_label(cluster.duration_ms)},
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
            "meta": _time_range(segment.start_ms, segment.end_ms),
            "facts": [
                {"label": "当前说话人", "value": segment.speaker_cluster_id or "尚未确定"},
                {"label": "处理建议", "value": "人工试听确认是否漏掉副声部"},
            ],
            "tone": "warning",
        }
        for segment in overlap_segments[:HUMAN_DETAIL_LIMIT]
    ]
    diarization_result = {
        "status": "warning" if unassigned_words else _result_status(transcription.diarization_status),
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

    research_source_by_id = {item.source_id: item for item in research.sources}
    review_operations = [(segment, operation) for segment in segments for operation in segment.review_operations]
    research_edits = [
        operation
        for _segment, operation in review_operations
        if operation.status == "accepted" and operation.evidence_source_ids
    ]
    research_questions = _research_question_items(research, review_operations)
    research_summary = _research_summary(research.status, len(research.queries), len(research.sources), research.reason)
    if research_edits:
        research_summary = f"{research_summary} 其中 {len(research_edits)} 项文本修正引用了联网资料。"
    research_result = {
        "status": _result_status(research.status),
        "purpose": STEP_PURPOSES["web_research"],
        "summary": research_summary,
        "metrics": _metrics(
            ("判断结果", _status_label(research.status)),
            ("查证问题", len(research.queries)),
            ("可用来源", len(research.sources)),
            ("支持修改", len(research_edits)),
        ),
        "sections": _sections(
            ("逐项查证结果", research_questions),
        ),
        "coverage": _coverage(len(research_questions), len(research.queries), "个查证问题"),
        "notes": _notes(research.error),
    }

    changed_segments = [
        segment
        for segment in segments
        if segment.corrected_text is not None and segment.corrected_text.strip() != segment.raw_text.strip()
    ]
    accepted_edits = sum(operation.status == "accepted" for _segment, operation in review_operations)
    rejected_edits = sum(operation.status == "rejected" for _segment, operation in review_operations)
    correction_items = _correction_items(segments, review_operations, research_source_by_id)
    title_resolution = stage("text_review").get("research_title_resolution") or {}
    correction_notes = [transcription.review_error]
    if len(review_operations) > HUMAN_DETAIL_LIMIT:
        correction_notes.append(
            f"修改记录共 {len(review_operations)} 项，详情按时间均匀展示 {HUMAN_DETAIL_LIMIT} 项；完整机器记录仍保留在项目转录数据中。"
        )
    review_result = {
        "status": _result_status(transcription.review_status),
        "purpose": STEP_PURPOSES["text_review"],
        "summary": _review_summary(transcription.review_status, len(changed_segments), len(segments)),
        "metrics": _metrics(
            ("语义模型", transcription.review_model_id),
            ("处理批次", stage("text_review").get("batch_count")),
            ("发生修正", len(changed_segments)),
            ("采纳修改", accepted_edits),
            ("拒绝修改", rejected_edits),
            ("标题专名候选", title_resolution.get("candidate_count")),
            ("标题专名修正", title_resolution.get("applied_count")),
            ("专名复核耗时", _duration_label(title_resolution.get("duration_ms")) if title_resolution else None),
        ),
        "coverage": _coverage(
            len(correction_items),
            len(review_operations) if review_operations else len(changed_segments),
            "项校对记录",
        ),
        "sections": _sections(("逐项校对记录", correction_items)),
        "notes": _notes(*correction_notes),
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
            "text": f"这个词从 {_timecode(word.start_ms)} 开始，到 {_timecode(word.end_ms)} 结束。",
            "meta": f"时间可信度：{_confidence_label(word.timing_confidence)}",
            "facts": [
                {"label": "持续时间", "value": _duration_label(word.end_ms - word.start_ms)},
                {"label": "定位方式", "value": _timing_source_label(word.timing_source)},
            ],
            "tone": "warning" if word.timing_confidence == "low" else "neutral",
        }
        for word in sorted(
            visible_alignment_words,
            key=lambda item: (confidence_order.get(item.timing_confidence, 3), item.start_ms),
        )
    ]
    alignment_result = {
        "status": _result_status(transcription.alignment_status),
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
        "notes": _notes(transcription.alignment_error),
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
            "text": _boundary_plain_summary(item.gap_ms, item.low_energy_ms, item.confidence),
            "meta": f"停顿判断：{_confidence_label(item.confidence)}可信",
            "facts": [
                {"label": "两词间隔", "value": _duration_label(item.gap_ms)},
                {"label": "安静部分", "value": _duration_label(item.low_energy_ms)},
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
        "status": _result_status(transcription.audio_boundary_status),
        "purpose": STEP_PURPOSES["audio_boundaries"],
        "summary": f"检查了 {len(transcription.audio_boundary_features)} 个相邻词之间的声音变化，找出可用于断句的停顿。",
        "metrics": _metrics(
            ("检查位置", len(transcription.audio_boundary_features)),
            ("校准语音开头", len(transcription.speech_onset_by_word_id)),
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
    boundary_review_result = {
        "status": _result_status(transcription.boundary_review_status),
        "purpose": STEP_PURPOSES["boundary_review"],
        "summary": _boundary_review_summary(
            transcription.boundary_review_status,
            len(transcription.boundary_reviews),
            int(review_timing.get("candidate_count") or 0),
        ),
        "metrics": _metrics(
            ("语义模型", transcription.boundary_review_model_id),
            ("本轮模型候选", review_timing.get("candidate_count")),
            ("复用既有判断", review_timing.get("reused_review_count")),
            ("全部可用判断", len(transcription.boundary_reviews)),
            ("建议断开", decision_counts["prefer"]),
            ("可断可不断", decision_counts["allow"]),
            ("建议连着", decision_counts["avoid"]),
            ("复核轮数", review_timing.get("round_count")),
            ("请求批次", review_timing.get("batch_count")),
        ),
        "coverage": _coverage(len(review_samples), len(transcription.boundary_reviews), "个语义断句判断"),
        "sections": _sections(("逐项断句判断", review_samples)),
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
            "title": f"字幕 {index} · {cue.cue_id}",
            "text": _compact_text(cue.en_subtitle_text),
            "meta": _time_range(cue.start_ms, cue.end_ms),
            "facts": [
                {"label": "时长", "value": _duration_label((cue.end_ms or 0) - (cue.start_ms or 0))},
                {"label": "说话人", "value": cue.speaker_id or cue.speaker_cluster_id or "尚未指定"},
                {"label": "时间可信度", "value": _confidence_label(cue.timing_confidence or "none")},
            ],
            "tone": "warning" if not (cue.en_subtitle_text or "").strip() else "neutral",
        }
        for index, cue in enumerate(visible_cues, start=1)
    ]
    subtitle_result = {
        "status": "failed" if not ordered_cues else "warning" if overlaps or empty_cues or upstream_review_needed else "success",
        "purpose": STEP_PURPOSES["subtitle_track"],
        "summary": (
            "没有找到本次生成的 ASR 字幕。"
            if not ordered_cues
            else f"已写入 {len(ordered_cues)} 条 ASR 字幕，发现 {overlaps} 处时间重叠、{empty_cues} 条空文本。"
            if overlaps or empty_cues
            else f"已写入 {len(ordered_cues)} 条 ASR 字幕；字幕结构完整，但仍有上游判断需要人工复核。"
            if upstream_review_needed
            else f"已写入 {len(ordered_cues)} 条 ASR 字幕，基础完整性检查通过。"
        ),
        "metrics": _metrics(
            ("字幕数量", len(ordered_cues)),
            ("时间重叠", overlaps),
            ("空文本", empty_cues),
            ("说话人复核", "需要" if speaker_review_needed else "不需要"),
            ("断句复核", _status_label(transcription.boundary_review_status)),
            ("覆盖时段", _cue_time_range(ordered_cues)),
        ),
        "coverage": _coverage(len(cue_items), len(ordered_cues), "条最终字幕"),
        "sections": _sections(("最终写入的 ASR 字幕", cue_items)),
        "notes": _notes(
            "断句语义复核没有全部完成，最终字幕已使用声音停顿和安全规则补齐；发布前建议人工快速通读。"
            if boundary_review_needed
            else None,
            "说话人结果仍有待确认项，涉及人物归属的字幕需要人工试听。"
            if speaker_review_needed
            else None,
        ),
    }

    return {
        "asr": asr_result,
        "diarization": diarization_result,
        "web_research": research_result,
        "text_review": review_result,
        "alignment": alignment_result,
        "audio_boundaries": audio_boundary_result,
        "boundary_review": boundary_review_result,
        "subtitle_track": subtitle_result,
    }


def _compact_text(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


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


def _research_question_items(research, review_operations: list[tuple[object, object]]) -> list[dict]:
    sources_by_query: dict[str, list] = {}
    for source in research.sources:
        sources_by_query.setdefault(source.query_id, []).append(source)

    items = []
    for index, query in enumerate(research.queries, start=1):
        sources = sources_by_query.get(query.query_id, [])
        source_ids = {source.source_id for source in sources}
        related_operations = [
            operation
            for _segment, operation in review_operations
            if source_ids.intersection(operation.evidence_source_ids)
        ]
        accepted = [operation for operation in related_operations if operation.status == "accepted"]
        rejected = [operation for operation in related_operations if operation.status == "rejected"]
        if accepted:
            changes = "；".join(
                f"{_compact_text(operation.source_text, 60)} → {_compact_text(operation.replacement_text, 60)}"
                for operation in accepted[:6]
            )
            conclusion = f"查到的资料支持了 {len(accepted)} 项文本修正：{changes}"
            impact = "已用于修正识别文本"
            tone = "positive"
        elif rejected:
            conclusion = f"资料参与了 {len(rejected)} 项校对判断，但相关修改最终未采用。"
            impact = "参与判断，未改变文本"
            tone = "neutral"
        elif sources:
            conclusion = f"找到 {len(sources)} 条可用资料，作为名称或背景参考；没有因此直接改动识别文本。"
            impact = "仅作为背景参考"
            tone = "neutral"
        else:
            conclusion = "本次搜索没有找到足够可靠的资料，因此没有据此修改识别文本。"
            impact = "未影响文本"
            tone = "warning"
        terms = "、".join(_compact_text(term, 80) for term in query.target_terms[:8]) or "未单独指定"
        return_sources = [
            {
                "title": _compact_text(source.title or source.url, 180),
                "url": _compact_text(source.url, 600),
                "meta": _compact_text(source.provider, 80),
                "text": _compact_text(source.snippet, 320),
            }
            for source in sources
        ]
        items.append(
            {
                "title": f"问题 {index} · {_compact_text(query.query, 160)}",
                "text": conclusion,
                "meta": _research_category_label(query.category),
                "facts": [
                    {"label": "为什么要查", "value": _plain_research_reason(query.reason, query.category)},
                    {"label": "重点查什么", "value": terms},
                    {"label": "产生的作用", "value": impact},
                ],
                "links": return_sources,
                "tone": tone,
            }
        )
    return items


def _correction_items(segments, review_operations, research_source_by_id: dict[str, object]) -> list[dict]:
    items = []
    visible_operations = _bounded_evenly(review_operations, HUMAN_DETAIL_LIMIT)
    for index, (segment, operation) in enumerate(visible_operations, start=1):
        accepted = operation.status == "accepted"
        cited_sources = [
            research_source_by_id[source_id]
            for source_id in operation.evidence_source_ids
            if source_id in research_source_by_id
        ]
        reason = _plain_review_reason(operation.reason)
        if not accepted:
            rejection = _rejection_reason_label(operation.rejection_reason)
            reason = f"建议理由：{reason} 未采用原因：{rejection}。"
        items.append(
            {
                "title": (
                    f"{_compact_text(operation.source_text, 80)} → "
                    f"{_compact_text(operation.replacement_text, 80) or '删除重复内容'}"
                ),
                "before": _compact_text(operation.source_text, 240),
                "after": _compact_text(operation.replacement_text, 240),
                "before_label": "识别原词",
                "after_label": "校对建议",
                "text": reason,
                "meta": "已采纳" if accepted else "未采纳",
                "facts": [
                    {"label": "所在时间", "value": _time_range(segment.start_ms, segment.end_ms)},
                    {"label": "判断把握", "value": f"{round(operation.confidence * 100)}%"},
                    {"label": "处理结果", "value": "已写入校对文本" if accepted else "保留原识别文本"},
                ],
                "links": [
                    {
                        "title": _compact_text(source.title or source.url, 180),
                        "url": _compact_text(source.url, 600),
                        "meta": _compact_text(source.provider, 80),
                    }
                    for source in cited_sources
                ],
                "tone": "positive" if accepted else "muted",
            }
        )

    if items:
        return items

    changed_segments = [
        segment
        for segment in segments
        if segment.corrected_text is not None and segment.corrected_text.strip() != segment.raw_text.strip()
    ]
    return [
        {
            "title": f"片段 {index}",
            "before": _compact_text(segment.raw_text),
            "after": _compact_text(segment.corrected_text or segment.raw_text),
            "before_label": "识别原文",
            "after_label": "校对结果",
            "meta": _time_range(segment.start_ms, segment.end_ms),
            "tone": "positive",
        }
        for index, segment in enumerate(_bounded_evenly(changed_segments, HUMAN_DETAIL_LIMIT), start=1)
    ]


def _duration_label(value_ms: int) -> str:
    value = max(0, int(value_ms))
    if value < 1_000:
        return f"{value} 毫秒"
    seconds = value / 1_000
    number = f"{seconds:.2f}".rstrip("0").rstrip(".")
    return f"{number} 秒"


def _timing_source_label(value: str) -> str:
    return {
        "forced_aligner": "按声音重新定位",
        "asr_segment_interpolation": "按识别片段估算",
    }.get(value, value)


def _boundary_plain_summary(gap_ms: int, low_energy_ms: int, confidence: str) -> str:
    if confidence == "high":
        return f"这里有 {_duration_label(gap_ms)} 的词间空隙，其中 {_duration_label(low_energy_ms)} 比较安静，是明显的停顿位置。"
    if confidence == "medium":
        return f"这里检测到 {_duration_label(gap_ms)} 的词间空隙，可能适合作为字幕边界，还需要结合语义判断。"
    return f"两词之间相隔 {_duration_label(gap_ms)}，声音停顿不够明显，不能只靠音频决定是否断句。"


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


def _plain_review_reason(value: object) -> str:
    reason = _compact_text(value, 320)
    if not reason:
        return "模型根据原文上下文提出了这项校对建议。"
    normalized = reason.lower()
    if normalized.startswith("project_glossary:"):
        return "与项目术语表中的标准写法一致。"
    if "product name correction" in normalized and "video title" in normalized:
        return "根据视频标题核对并修正产品名称。"
    if "proper noun correction" in normalized:
        return "结合视频标题、上下文和术语表核对专有名称。"
    if "near-homophone" in normalized or "near homophone" in normalized:
        return "结合上下文和术语表修正近音误识别。"
    if "number correction" in normalized:
        return "结合上下文核对数字或型号写法。"
    if "asr misrecognized brand name" in normalized or (
        "asr misrecognition" in normalized and ("proper noun" in normalized or "brand" in normalized)
    ):
        return "结合场景上下文、视频标题和查证资料，确认 ASR 把专有名称听错了。"
    if "incorrectly split version number" in normalized or (
        "version number" in normalized and "split" in normalized
    ):
        return "版本号被分段识别，需要合并为上下文中的完整写法。"
    if ("redundant word" in normalized and "asr split" in normalized) or (
        "redundant" in normalized and "version number" in normalized
    ):
        return "相邻片段已经包含完整版本号，这里是分段识别产生的重复内容，因此删除。"
    if _mostly_ascii_text(reason):
        return "模型结合全文上下文、标题和查证资料提出了这项校对建议。"
    return reason


def _plain_research_reason(value: object, category: str) -> str:
    reason = _compact_text(value, 320)
    if not reason:
        return "需要确认相关名称或背景。"
    normalized = reason.lower()
    if "verify correct product name" in normalized or "verify product name" in normalized:
        return "核对产品名称和版本写法是否准确。"
    if "source file" in normalized and "title" in normalized and "misheard" in normalized:
        return "源文件标题出现了与疑似误听专名相近的词，需要独立核对完整名称和版本。"
    if _mostly_ascii_text(reason):
        return {
            "proper_noun": "核对专有名称、产品名称或版本写法是否准确。",
            "persona": "核对人物身份、称呼或表达背景是否准确。",
            "background": "补充理解当前话题所需的背景资料。",
            "culture": "核对文化语境，避免按字面误解原意。",
        }.get(category, "核对相关名称或背景是否准确。")
    return reason


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


def _rejection_reason_label(value: object) -> str:
    reason = _compact_text(value, 240)
    labels = {
        "llm_review_rejected:empty": "建议内容为空",
        "llm_review_rejected:empty_text": "建议内容为空",
        "llm_review_rejected:language_changed": "建议改变了原文语言",
        "llm_review_rejected:numbers_changed": "建议会改变数字或型号",
        "llm_review_rejected:negation_changed": "建议会改变否定关系",
        "llm_review_rejected:rewrite_too_large": "建议改写幅度过大",
        "llm_review_rejected:too_different": "建议改写幅度过大",
        "llm_review_rejected:invalid_word_range": "建议定位的词语范围无效",
        "llm_review_rejected:content_deletion": "建议会删除原文信息",
        "llm_review_rejected:unsupported_proper_noun": "专有名称缺少足够依据",
    }
    return labels.get(reason, reason or "证据或声音依据不足")


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


def _research_category_label(value: str) -> str:
    return {
        "proper_noun": "专名核验",
        "background": "背景资料",
        "culture": "文化背景",
        "persona": "人物表达",
    }.get(value, value)


def _research_summary(status: str, query_count: int, source_count: int, reason: str) -> str:
    if status == "completed":
        return f"完成 {query_count} 个搜索问题，获得 {source_count} 条参考来源。"
    if status == "partial":
        return f"联网核验部分完成，获得 {source_count} 条可用来源。"
    if status == "not_needed":
        return _compact_text(reason) or "判断当前文本不需要联网核验。"
    if status in {"disabled", "not_configured"}:
        return "联网核验未启用，本步骤没有修改识别文本。"
    return "联网核验失败，后续步骤使用已有文本继续处理。"


def _review_summary(status: str, changed_count: int, segment_count: int) -> str:
    if status == "completed":
        return f"复核 {segment_count} 个片段，其中 {changed_count} 个片段发生修正。"
    if status == "partial":
        return f"文本校对部分完成，已产生 {changed_count} 个片段修正。"
    if status == "not_configured":
        return "未配置语义模型，仅应用了可确定的术语表修正。"
    if status == "skipped":
        return "没有可供校对的识别文本，本步骤已跳过。"
    return "文本校对失败，后续步骤保留原始识别文本。"


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


def _time_range(start_ms: int | None, end_ms: int | None) -> str:
    if start_ms is None or end_ms is None:
        return "未记录时间"
    return f"{_timecode(start_ms)} - {_timecode(end_ms)}"


def _timecode(value_ms: int) -> str:
    total_ms = max(0, int(value_ms))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    prefix = f"{hours:02d}:" if hours else ""
    return f"{prefix}{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _cue_time_range(cues: list) -> str:
    if not cues or cues[0].start_ms is None or cues[-1].end_ms is None:
        return "未记录"
    return _time_range(cues[0].start_ms, cues[-1].end_ms)
