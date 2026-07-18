from __future__ import annotations

import re

from app.domains.video_localization.schemas import VideoLocalizationDraft


ASR_SAMPLE_LIMIT = 15
DETAIL_LIMIT = 50


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

    sampled_segments = _sample_evenly(segments, ASR_SAMPLE_LIMIT)
    asr_samples = [
        {
            "title": f"片段 {index}",
            "text": _compact_text(segment.raw_text),
            "meta": _time_range(segment.start_ms, segment.end_ms),
        }
        for index, segment in enumerate(sampled_segments, start=1)
        if segment.raw_text.strip()
    ]
    asr_result = {
        "status": "success" if segments else "failed",
        "summary": f"识别到 {len(segments)} 个原始语音片段。" if segments else "没有识别到有效语音文本。",
        "metrics": _metrics(
            ("识别引擎", transcription.engine_id),
            ("语言", _language_label(transcription.language)),
            ("原始片段", len(segments)),
            ("抽查样例", len(asr_samples)),
        ),
        "sections": _sections(("识别样例", asr_samples)),
    }

    speaker_clusters = transcription.speaker_clusters
    review_clusters = [item for item in speaker_clusters if item.merge_status == "needs_review"]
    auto_merged_clusters = [item for item in speaker_clusters if item.merge_status == "auto_merged"]
    overlap_segments = [item for item in segments if item.has_speaker_overlap]
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
    diarization_result = {
        "status": _result_status(transcription.diarization_status),
        "summary": diarization_summary,
        "metrics": _metrics(
            ("区分引擎", transcription.diarization_engine_id),
            ("匿名说话人", len(speaker_clusters)),
            ("自动合并", len(auto_merged_clusters)),
            ("需要复核", len(review_clusters)),
            ("重叠片段", len(overlap_segments)),
        ),
        "sections": _sections(("说话人声纹簇", cluster_items)),
        "notes": _notes(transcription.diarization_error),
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
    if len(review_operations) > DETAIL_LIMIT:
        correction_notes.append(
            f"修改记录共 {len(review_operations)} 项，当前展示前 {DETAIL_LIMIT} 项；完整结果仍保留在项目转录数据中。"
        )
    review_result = {
        "status": _result_status(transcription.review_status),
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
        "sections": _sections(("逐项校对记录", correction_items)),
        "notes": _notes(*correction_notes),
    }

    confidence_counts = {
        level: sum(word.timing_confidence == level for word in words) for level in ("high", "medium", "low")
    }
    confidence_order = {"low": 0, "medium": 1, "high": 2}
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
        for word in sorted(words, key=lambda item: (confidence_order.get(item.timing_confidence, 3), item.start_ms))[
            :12
        ]
    ]
    alignment_result = {
        "status": _result_status(transcription.alignment_status),
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
        "sections": _sections(("需要重点看的词", alignment_samples)),
        "notes": _notes(transcription.alignment_error),
    }

    boundary_counts = {
        level: sum(item.confidence == level for item in transcription.audio_boundary_features)
        for level in ("high", "medium", "low", "none")
    }
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
        for item in sorted(transcription.audio_boundary_features, key=lambda value: value.gap_ms, reverse=True)[:12]
    ]
    audio_boundary_result = {
        "status": _result_status(transcription.audio_boundary_status),
        "summary": f"检查了 {len(transcription.audio_boundary_features)} 个相邻词之间的声音变化，找出可用于断句的停顿。",
        "metrics": _metrics(
            ("检查位置", len(transcription.audio_boundary_features)),
            ("校准语音开头", len(transcription.speech_onset_by_word_id)),
            ("明显停顿", boundary_counts["high"]),
            ("可能停顿", boundary_counts["medium"]),
            ("较弱停顿", boundary_counts["low"]),
        ),
        "sections": _sections(("显著停顿样例", boundary_samples)),
        "notes": _notes(transcription.audio_boundary_error),
    }

    decision_counts = {
        decision: sum(item.decision == decision for item in transcription.boundary_reviews)
        for decision in ("prefer", "allow", "avoid")
    }
    review_samples = []
    word_index = {word.word_id: index for index, word in enumerate(words)}
    for item in transcription.boundary_reviews[:30]:
        left = word_by_id.get(item.left_word_id)
        right = word_by_id.get(item.right_word_id)
        continuous, split = _boundary_context(item.left_word_id, item.right_word_id, words, word_index)
        review_samples.append(
            {
                "title": f"在“{left.text if left else item.left_word_id} / {right.text if right else item.right_word_id}”之间",
                "text": _compact_text(item.reason),
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
        "summary": _boundary_review_summary(
            transcription.boundary_review_status,
            len(transcription.boundary_reviews),
            int(review_timing.get("candidate_count") or 0),
        ),
        "metrics": _metrics(
            ("语义模型", transcription.boundary_review_model_id),
            ("待判断位置", review_timing.get("candidate_count")),
            ("已完成判断", len(transcription.boundary_reviews)),
            ("建议断开", decision_counts["prefer"]),
            ("可断可不断", decision_counts["allow"]),
            ("建议连着", decision_counts["avoid"]),
            ("复核轮数", review_timing.get("round_count")),
            ("请求批次", review_timing.get("batch_count")),
        ),
        "sections": _sections(("断句判断样例", review_samples)),
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
    subtitle_result = {
        "status": "failed" if not ordered_cues else "warning" if overlaps or empty_cues else "success",
        "summary": (
            "没有找到本次生成的 ASR 字幕。"
            if not ordered_cues
            else f"已写入 {len(ordered_cues)} 条 ASR 字幕，发现 {overlaps} 处时间重叠、{empty_cues} 条空文本。"
            if overlaps or empty_cues
            else f"已写入 {len(ordered_cues)} 条 ASR 字幕，基础完整性检查通过。"
        ),
        "metrics": _metrics(
            ("字幕数量", len(ordered_cues)),
            ("时间重叠", overlaps),
            ("空文本", empty_cues),
            ("覆盖时段", _cue_time_range(ordered_cues)),
        ),
        "sections": [],
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
            for source in sources[:12]
        ]
        items.append(
            {
                "title": f"问题 {index} · {_compact_text(query.query, 160)}",
                "text": conclusion,
                "meta": _research_category_label(query.category),
                "facts": [
                    {"label": "为什么要查", "value": _compact_text(query.reason, 320) or "需要确认相关名称或背景"},
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
    for index, (segment, operation) in enumerate(review_operations[:DETAIL_LIMIT], start=1):
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
                "title": f"{_compact_text(operation.source_text, 80)} → {_compact_text(operation.replacement_text, 80)}",
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
                    for source in cited_sources[:6]
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
        for index, segment in enumerate(changed_segments[:DETAIL_LIMIT], start=1)
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
    return reason


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
