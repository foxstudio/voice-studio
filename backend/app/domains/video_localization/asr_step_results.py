from __future__ import annotations

import re

from app.domains.video_localization.schemas import VideoLocalizationDraft

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

    asr_samples = [
        {
            "title": f"片段 {index}",
            "text": _compact_text(segment.raw_text),
            "meta": _time_range(segment.start_ms, segment.end_ms),
        }
        for index, segment in enumerate(segments[:3], start=1)
        if segment.raw_text.strip()
    ]
    asr_result = {
        "status": "success" if segments else "failed",
        "summary": f"识别到 {len(segments)} 个原始语音片段。" if segments else "没有识别到有效语音文本。",
        "metrics": _metrics(
            ("识别引擎", transcription.engine_id),
            ("语言", transcription.language),
            ("原始片段", len(segments)),
        ),
        "sections": _sections(("识别样例", asr_samples)),
    }

    research_queries = [
        {
            "title": _compact_text(item.query, 180),
            "text": _compact_text(item.reason),
            "meta": f"{item.category} · {', '.join(item.target_terms[:4])}".strip(" ·"),
        }
        for item in research.queries[:5]
    ]
    research_sources = [
        {
            "title": _compact_text(item.title or item.url, 180),
            "text": _compact_text(item.snippet),
            "meta": item.provider,
            "url": _compact_text(item.url, 600),
        }
        for item in research.sources[:5]
    ]
    research_result = {
        "status": _result_status(research.status),
        "summary": _research_summary(research.status, len(research.queries), len(research.sources), research.reason),
        "metrics": _metrics(
            ("判断结果", _status_label(research.status)),
            ("搜索查询", len(research.queries)),
            ("资料来源", len(research.sources)),
            ("缓存命中", research.cache_hits),
        ),
        "sections": _sections(("搜索问题", research_queries), ("参考来源", research_sources)),
        "notes": _notes(research.error),
    }

    changed_segments = [
        segment
        for segment in segments
        if segment.corrected_text is not None and segment.corrected_text.strip() != segment.raw_text.strip()
    ]
    accepted_edits = sum(
        operation.status == "accepted"
        for segment in segments
        for operation in segment.review_operations
    )
    rejected_edits = sum(
        operation.status == "rejected"
        for segment in segments
        for operation in segment.review_operations
    )
    correction_samples = [
        {
            "title": f"片段 {index}",
            "before": _compact_text(segment.raw_text),
            "after": _compact_text(segment.corrected_text or segment.raw_text),
            "meta": _time_range(segment.start_ms, segment.end_ms),
        }
        for index, segment in enumerate(changed_segments[:5], start=1)
    ]
    review_result = {
        "status": _result_status(transcription.review_status),
        "summary": _review_summary(transcription.review_status, len(changed_segments), len(segments)),
        "metrics": _metrics(
            ("语义模型", transcription.review_model_id),
            ("处理批次", stage("text_review").get("batch_count")),
            ("发生修正", len(changed_segments)),
            ("采纳修改", accepted_edits),
            ("拒绝修改", rejected_edits),
        ),
        "sections": _sections(("修正对照", correction_samples)),
        "notes": _notes(transcription.review_error),
    }

    confidence_counts = {level: sum(word.timing_confidence == level for word in words) for level in ("high", "medium", "low")}
    alignment_result = {
        "status": _result_status(transcription.alignment_status),
        "summary": (
            f"为 {len(words)} 个词生成时间码，整体可信度为{_confidence_label(transcription.timing_confidence)}。"
            if words else "没有生成可用的逐词时间码。"
        ),
        "metrics": _metrics(
            ("对齐引擎", transcription.alignment_engine_id),
            ("逐词时间码", len(words)),
            ("整体可信度", _confidence_label(transcription.timing_confidence)),
            ("高可信", confidence_counts["high"]),
            ("中可信", confidence_counts["medium"]),
            ("低可信", confidence_counts["low"]),
        ),
        "notes": _notes(transcription.alignment_error),
    }

    boundary_counts = {
        level: sum(item.confidence == level for item in transcription.audio_boundary_features)
        for level in ("high", "medium", "low", "none")
    }
    boundary_samples = [
        {
            "title": f"{word_by_id.get(item.left_word_id).text if word_by_id.get(item.left_word_id) else item.left_word_id} / "
            f"{word_by_id.get(item.right_word_id).text if word_by_id.get(item.right_word_id) else item.right_word_id}",
            "text": f"停顿 {item.gap_ms} ms，低能量区间 {item.low_energy_ms} ms",
            "meta": f"{_confidence_label(item.confidence)} · 能量下降 {item.energy_drop_db:.1f} dB",
        }
        for item in sorted(transcription.audio_boundary_features, key=lambda value: value.gap_ms, reverse=True)[:3]
    ]
    audio_boundary_result = {
        "status": _result_status(transcription.audio_boundary_status),
        "summary": f"分析到 {len(transcription.audio_boundary_features)} 个相邻词边界。",
        "metrics": _metrics(
            ("分析版本", transcription.audio_boundary_analysis_version),
            ("边界数量", len(transcription.audio_boundary_features)),
            ("修正入点", len(transcription.speech_onset_by_word_id)),
            ("高可信边界", boundary_counts["high"]),
            ("中可信边界", boundary_counts["medium"]),
            ("低可信边界", boundary_counts["low"]),
        ),
        "sections": _sections(("显著停顿样例", boundary_samples)),
        "notes": _notes(transcription.audio_boundary_error),
    }

    decision_counts = {
        decision: sum(item.decision == decision for item in transcription.boundary_reviews)
        for decision in ("prefer", "allow", "avoid")
    }
    review_samples = []
    for item in transcription.boundary_reviews[:5]:
        left = word_by_id.get(item.left_word_id)
        right = word_by_id.get(item.right_word_id)
        review_samples.append(
            {
                "title": f"{left.text if left else item.left_word_id} / {right.text if right else item.right_word_id}",
                "text": _compact_text(item.reason),
                "meta": f"{_decision_label(item.decision)} · 可信度 {round(item.confidence * 100)}%",
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
            ("候选边界", review_timing.get("candidate_count")),
            ("复核结果", len(transcription.boundary_reviews)),
            ("建议断开", decision_counts["prefer"]),
            ("允许断开", decision_counts["allow"]),
            ("避免断开", decision_counts["avoid"]),
            ("复核轮数", review_timing.get("round_count")),
            ("请求批次", review_timing.get("batch_count")),
        ),
        "sections": _sections(("断句判断样例", review_samples)),
        "notes": _notes(transcription.boundary_review_error),
    }

    asr_cues = [cue for cue in draft.cues if "generated_by_asr" in cue.quality_flags]
    if not asr_cues:
        asr_cues = [cue for cue in draft.cues if cue.en_subtitle_text and cue.start_ms is not None and cue.end_ms is not None]
    ordered_cues = sorted(asr_cues, key=lambda cue: (cue.start_ms or 0, cue.end_ms or 0, cue.cue_id))
    overlaps = sum(
        previous.end_ms is not None
        and current.start_ms is not None
        and previous.end_ms > current.start_ms
        for previous, current in zip(ordered_cues, ordered_cues[1:])
    )
    empty_cues = sum(not (cue.en_subtitle_text or "").strip() for cue in ordered_cues)
    cue_samples = [
        {
            "title": f"字幕 {index}",
            "text": _compact_text(cue.en_subtitle_text or ""),
            "meta": _time_range(cue.start_ms, cue.end_ms),
        }
        for index, cue in enumerate(ordered_cues[:3], start=1)
    ]
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
            ("时间范围", _cue_time_range(ordered_cues)),
        ),
        "sections": _sections(("最终字幕样例", cue_samples)),
    }

    return {
        "asr": asr_result,
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
    return {"prefer": "建议断开", "allow": "允许断开", "avoid": "避免断开"}.get(value, value)


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

