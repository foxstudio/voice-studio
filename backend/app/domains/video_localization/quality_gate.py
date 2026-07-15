from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from threading import Lock

from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationQualityGate,
    VideoLocalizationQualityIssue,
    VideoLocalizationReferenceClip,
    VideoLocalizationSubtitleCue,
    now_iso,
)


LOCALIZED_SUBTITLE_TARGET_CPS = 9
LOCALIZED_SUBTITLE_HARD_MAX_CPS = 12
LOCALIZED_SUBTITLE_MIN_DURATION_MS = 800
LOCALIZED_SUBTITLE_SOFT_MAX_DURATION_MS = 6000
LOCALIZED_SUBTITLE_HARD_MAX_DURATION_MS = 7000
LOCALIZED_SUBTITLE_MAX_LINES = 2
LOCALIZED_SUBTITLE_MAX_LINE_UNITS = 14
LOCALIZED_SUBTITLE_MAX_TOTAL_UNITS = 28
REPETITIVE_ISSUE_THRESHOLD = 12
REPETITIVE_ISSUE_SAMPLE_SIZE = 3
FILE_SHA256_CACHE_LIMIT = 64
_FILE_SHA256_CACHE: dict[tuple[str, int, int], str] = {}
_FILE_SHA256_CACHE_LOCK = Lock()


def evaluate_quality_gate(draft: VideoLocalizationDraft) -> VideoLocalizationQualityGate:
    blockers: list[VideoLocalizationQualityIssue] = []
    warnings: list[VideoLocalizationQualityIssue] = []
    reference_by_id = {clip.reference_clip_id: clip for clip in draft.reference_clips}
    speaker_ids = {speaker.speaker_id for speaker in draft.speakers}
    has_localization_work = _has_localization_work(draft)
    has_dubbing_work = _has_dubbing_work(draft)
    _check_asr_timing_quality(draft, blockers)
    _check_word_provenance(draft, blockers)
    _check_boundary_review_quality(draft, blockers, warnings)
    _check_localized_subtitles(draft.localized_subtitles, blockers, warnings)
    _check_media_duration_bounds(draft, blockers)

    if not draft.cues:
        blockers = _finalize_issues(blockers)
        warnings = _finalize_issues(warnings)
        status = "blocked" if blockers else "warning" if warnings else "unknown"
        return VideoLocalizationQualityGate(
            status=status,
            pending_issues=len(blockers) + len(warnings),
            blockers=blockers,
            warnings=warnings,
            checked_at=now_iso(),
        )

    if not (draft.source_media.filename or draft.source_media.video_path):
        warnings.append(_issue("SOURCE_MEDIA_MISSING", "尚未记录源视频素材", "warning"))

    if draft.stems.separation_status != "completed":
        warnings.append(_issue("STEMS_NOT_READY", "人声/背景声分离尚未完成", "warning"))

    _check_cue_timeline(draft.cues, blockers)
    _check_duplicate_cue_ids(draft.cues, blockers)

    for cue in draft.cues:
        _check_cue_basics(
            cue,
            speaker_ids,
            blockers,
            warnings,
            check_localization=has_localization_work or has_dubbing_work,
            check_dubbing=has_dubbing_work,
        )
        if has_localization_work or has_dubbing_work:
            _check_cue_localized_subtitle(cue, blockers, warnings)
        if has_dubbing_work:
            _check_cue_reference(cue, reference_by_id, blockers, warnings)
        _check_cue_duration(cue, warnings)

    blockers = _finalize_issues(blockers)
    warnings = _finalize_issues(warnings)
    status = "blocked" if blockers else "warning" if warnings else "pass"
    return VideoLocalizationQualityGate(
        status=status,
        pending_issues=len(blockers) + len(warnings),
        blockers=blockers,
        warnings=warnings,
        checked_at=now_iso(),
    )


def _has_localization_work(draft: VideoLocalizationDraft) -> bool:
    return bool(draft.localized_subtitles or any(_has_text(cue.zh_localized_subtitle_text) for cue in draft.cues))


def _has_dubbing_work(draft: VideoLocalizationDraft) -> bool:
    if draft.reference_clips or draft.voice_recipes or draft.generated_candidates:
        return True
    if any(dict(item).get("track_id") == "dub" for item in draft.timeline_clips):
        return True
    return any(
        cue.audio_route in {"clone_from_source", "preset_tts"}
        or bool(cue.tts_result_id or cue.tts_audio_path or cue.tts_batch_task_id)
        for cue in draft.cues
    )


def subtitle_export_blockers(draft: VideoLocalizationDraft, kind: str) -> list[VideoLocalizationQualityIssue]:
    blockers: list[VideoLocalizationQualityIssue] = []
    warnings: list[VideoLocalizationQualityIssue] = []
    if kind in {"en", "bilingual"}:
        if not draft.cues:
            blockers.append(_issue("SUBTITLE_TRACK_EMPTY", "字幕轨为空，没有可导出的字幕", "blocker"))
        _check_asr_timing_quality(draft, blockers)
        _check_word_provenance(draft, blockers)
        _check_boundary_review_quality(draft, blockers, warnings)
        _check_cue_timeline(draft.cues, blockers)
        _check_duplicate_cue_ids(draft.cues, blockers)
        for cue in draft.cues:
            _check_export_cue(cue, kind, blockers)
            if kind == "bilingual":
                _check_cue_localized_subtitle(cue, blockers, warnings)
    elif kind == "zh":
        if draft.localized_subtitles:
            _check_localized_subtitles(draft.localized_subtitles, blockers, warnings)
        else:
            if not draft.cues:
                blockers.append(_issue("SUBTITLE_TRACK_EMPTY", "字幕轨为空，没有可导出的字幕", "blocker"))
            _check_cue_timeline(draft.cues, blockers)
            _check_duplicate_cue_ids(draft.cues, blockers)
            for cue in draft.cues:
                _check_export_cue(cue, kind, blockers)
                _check_cue_localized_subtitle(cue, blockers, warnings)
    _check_media_duration_bounds(
        draft,
        blockers,
        check_cues=kind in {"en", "bilingual"} or (kind == "zh" and not draft.localized_subtitles),
        check_localized=kind in {"zh", "bilingual"} and bool(draft.localized_subtitles),
    )
    return _finalize_issues(blockers)


def _check_export_cue(
    cue: VideoLocalizationCue,
    kind: str,
    blockers: list[VideoLocalizationQualityIssue],
) -> None:
    if cue.start_ms is None or cue.end_ms is None:
        blockers.append(_issue("CUE_TIMECODE_MISSING", "字幕缺少入点或出点，无法导出", "blocker", cue_id=cue.cue_id))
    elif cue.end_ms <= cue.start_ms:
        blockers.append(_issue("CUE_DURATION_INVALID", "字幕出点必须晚于入点", "blocker", cue_id=cue.cue_id))
    if kind in {"en", "bilingual"} and not _has_text(cue.en_subtitle_text):
        blockers.append(_issue("EN_SUBTITLE_MISSING", "字幕缺少原文/ASR 文本，无法导出", "blocker", cue_id=cue.cue_id))
    if kind in {"zh", "bilingual"} and not _has_text(cue.zh_localized_subtitle_text):
        blockers.append(_issue("ZH_SUBTITLE_MISSING", "字幕缺少本土化中文文本，无法导出", "blocker", cue_id=cue.cue_id))


def _check_asr_timing_quality(
    draft: VideoLocalizationDraft,
    blockers: list[VideoLocalizationQualityIssue],
) -> None:
    transcription = draft.transcription
    if transcription:
        hash_cache: dict[Path, str] = {}
        current_fingerprint = _current_track_fingerprint(
            draft,
            transcription.source_track_id,
            hash_cache=hash_cache,
        )
        if (
            current_fingerprint
            and transcription.source_audio_sha256
            and current_fingerprint != transcription.source_audio_sha256
        ):
            blockers.append(_issue("ASR_SOURCE_CHANGED", "当前音轨与字幕听写时使用的音轨不一致，请重新听写", "blocker"))

        if transcription.source_track_id == "dub":
            expected_state = draft.source_media.metadata.get("english_asr_source_state_sha256")
            try:
                from app.domains.video_localization import source_pipeline

                current_state = source_pipeline.dub_asr_source_state_sha256(draft)
            except Exception:
                current_state = None
            if not expected_state or current_state != expected_state:
                blockers.append(
                    _issue(
                        "ASR_SOURCE_CHANGED",
                        "合成配音轨与字幕听写时使用的内容不一致，请重新听写",
                        "blocker",
                    )
                )

        alignment_source_track_id = getattr(transcription, "alignment_source_track_id", None)
        alignment_audio_sha256 = getattr(transcription, "alignment_audio_sha256", None)
        if alignment_source_track_id and alignment_audio_sha256:
            current_alignment_fingerprint = _current_track_fingerprint(
                draft,
                alignment_source_track_id,
                hash_cache=hash_cache,
            )
            if current_alignment_fingerprint and current_alignment_fingerprint != alignment_audio_sha256:
                blockers.append(
                    _issue(
                        "ASR_ALIGNMENT_SOURCE_CHANGED",
                        "当前对齐音轨与生成词级时间戳时使用的音轨不一致，请重新对齐",
                        "blocker",
                    )
                )
    all_cues_manually_verified = bool(draft.cues) and all(
        cue_tools.manual_timing_confirmation_is_current(cue) for cue in draft.cues
    )
    if transcription and transcription.alignment_status == "failed" and not all_cues_manually_verified:
        blockers.append(_issue("ASR_ALIGNMENT_FAILED", "ASR 字幕时间对齐失败，需重新对齐后方可进入生产", "blocker"))

    interpolated_word_ids = (
        {word.word_id for word in transcription.words if word.timing_source == "asr_segment_interpolation"}
        if transcription
        else set()
    )
    unverified_interpolated_cues = [
        cue
        for cue in draft.cues
        if (
            "segment_timing_interpolated" in cue.quality_flags
            or bool(interpolated_word_ids.intersection(cue.source_word_ids))
        )
        and not cue_tools.manual_timing_confirmation_is_current(cue)
    ]
    has_interpolated_timing = bool(unverified_interpolated_cues) or bool(interpolated_word_ids and not draft.cues)
    if has_interpolated_timing:
        cue_examples = "、".join(cue.cue_id for cue in unverified_interpolated_cues[:REPETITIVE_ISSUE_SAMPLE_SIZE])
        cue_detail = f"（示例：{cue_examples}）" if cue_examples else ""
        blockers.append(
            _issue(
                "ASR_TIMING_INTERPOLATED",
                f"{len(interpolated_word_ids)} 个词的时间仍由分段插值生成，涉及 {len(unverified_interpolated_cues)} 条未校准字幕{cue_detail}",
                "blocker",
            )
        )

    for cue in draft.cues:
        if "segmentation_review_required" in cue.quality_flags:
            blockers.append(
                _issue(
                    "ASR_CUE_SEGMENTATION_LIMIT_EXCEEDED",
                    "字幕为保留语义完整性而超出断句限制，请人工复核该片段",
                    "blocker",
                    cue_id=cue.cue_id,
                )
            )
        if transcription and cue.source_word_ids and cue.transcription_revision_id != transcription.revision_id:
            blockers.append(
                _issue(
                    "ASR_CUE_REVISION_STALE",
                    "字幕片段来自旧的听写版本，请重新生成或校准",
                    "blocker",
                    cue_id=cue.cue_id,
                )
            )
        if (
            cue.timing_confidence == "low"
            and not cue_tools.manual_timing_confirmation_is_current(cue)
            and not (transcription and (transcription.alignment_status == "failed" or has_interpolated_timing))
        ):
            blockers.append(
                _issue(
                    "ASR_CUE_TIMING_LOW_CONFIDENCE",
                    "ASR cue 时间置信度低，需人工校准后方可进入生产",
                    "blocker",
                    cue_id=cue.cue_id,
                )
            )


def _current_track_fingerprint(
    draft: VideoLocalizationDraft,
    source_track_id: str | None,
    *,
    hash_cache: dict[Path, str],
) -> str | None:
    path = _current_track_audio_path(draft, source_track_id)
    if path is not None:
        try:
            if path not in hash_cache:
                hash_cache[path] = _file_sha256(path)
            return hash_cache[path]
        except OSError:
            # A concurrently replaced or temporarily unreadable file cannot be
            # treated as authoritative; retain compatibility with old drafts.
            pass
    return _cached_track_fingerprint(draft, source_track_id)


def _current_track_audio_path(
    draft: VideoLocalizationDraft,
    source_track_id: str | None,
) -> Path | None:
    candidates: tuple[str | None, ...]
    if source_track_id == "vocals":
        candidates = (draft.stems.vocals_clean_path,)
    elif source_track_id == "original":
        candidates = (draft.source_media.audio_path, draft.stems.original_audio_path)
    elif source_track_id == "background":
        candidates = (draft.stems.background_path,)
    else:
        return None
    for value in candidates:
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_file():
            return path.resolve()
    return None


def _cached_track_fingerprint(draft: VideoLocalizationDraft, source_track_id: str | None) -> str | None:
    if source_track_id == "vocals":
        return draft.stems.vocals_clean_sha256
    if source_track_id == "original":
        return draft.source_media.audio_sha256 or draft.stems.original_audio_sha256
    if source_track_id == "background":
        return draft.stems.background_sha256
    return None


def _file_sha256(path: Path) -> str:
    stat = path.stat()
    cache_key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _FILE_SHA256_CACHE_LOCK:
        cached = _FILE_SHA256_CACHE.get(cache_key)
    if cached is not None:
        return cached

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    fingerprint = digest.hexdigest()
    with _FILE_SHA256_CACHE_LOCK:
        if len(_FILE_SHA256_CACHE) >= FILE_SHA256_CACHE_LIMIT:
            _FILE_SHA256_CACHE.clear()
        _FILE_SHA256_CACHE[cache_key] = fingerprint
    return fingerprint


def _check_boundary_review_quality(
    draft: VideoLocalizationDraft,
    blockers: list[VideoLocalizationQualityIssue],
    warnings: list[VideoLocalizationQualityIssue],
) -> None:
    transcription = draft.transcription
    if not transcription:
        return
    if transcription.audio_boundary_status == "failed":
        warnings.append(
            _issue("ASR_AUDIO_BOUNDARY_ANALYSIS_FAILED", "声学停顿分析失败，断句已降级为词级时间与标点规则", "warning")
        )

    review_by_pair = {(item.left_word_id, item.right_word_id): item for item in transcription.boundary_reviews}
    selected_pairs = _selected_asr_boundary_pairs(draft)
    avoided_pairs = [
        pair
        for pair in selected_pairs
        if (review := review_by_pair.get(pair)) is not None and review.decision == "avoid" and review.confidence >= 0.75
    ]
    if avoided_pairs:
        examples = _format_pair_examples(avoided_pairs)
        blockers.append(
            _issue(
                "ASR_SELECTED_BOUNDARY_REVIEW_AVOIDED",
                f"最终字幕采用了 {len(avoided_pairs)} 处高置信度不应切分的语义边界{examples}，需重新断句",
                "blocker",
            )
        )

    review_required_pairs = _review_required_selected_pairs(draft, selected_pairs)
    unreviewed_pairs = [pair for pair in review_required_pairs if pair not in review_by_pair]
    if unreviewed_pairs:
        examples = _format_pair_examples(unreviewed_pairs)
        warnings.append(
            _issue(
                "ASR_SELECTED_BOUNDARY_UNREVIEWED",
                f"最终字幕仍有 {len(unreviewed_pairs)} 处实际断句边界未完成语义复核{examples}",
                "warning",
            )
        )


def _selected_asr_boundary_pairs(draft: VideoLocalizationDraft) -> list[tuple[str, str]]:
    transcription = draft.transcription
    if not transcription or not transcription.words:
        return []
    word_index = {word.word_id: index for index, word in enumerate(transcription.words)}
    asr_cues = _current_asr_cues(draft)
    pairs: list[tuple[str, str]] = []
    for left_cue, right_cue in zip(asr_cues, asr_cues[1:]):
        if not left_cue.source_word_ids or not right_cue.source_word_ids:
            continue
        pair = (left_cue.source_word_ids[-1], right_cue.source_word_ids[0])
        left_index = word_index.get(pair[0])
        right_index = word_index.get(pair[1])
        if left_index is not None and right_index == left_index + 1:
            pairs.append(pair)
    return pairs


def _review_required_selected_pairs(
    draft: VideoLocalizationDraft,
    selected_pairs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    transcription = draft.transcription
    if not transcription:
        return []
    word_by_id = {word.word_id: word for word in transcription.words}
    evidence_by_pair = {(item.left_word_id, item.right_word_id): item for item in transcription.audio_boundary_features}
    required: list[tuple[str, str]] = []
    for pair in selected_pairs:
        left = word_by_id.get(pair[0])
        right = word_by_id.get(pair[1])
        if left is None or right is None:
            continue
        evidence = evidence_by_pair.get(pair)
        deterministic_sentence_end = _has_terminal_punctuation(left.text) and (
            left.segment_id != right.segment_id or (evidence is not None and evidence.confidence in {"medium", "high"})
        )
        if not deterministic_sentence_end:
            required.append(pair)
    return required


def _has_terminal_punctuation(text: str) -> bool:
    stripped = text.rstrip().rstrip("\"'”’)]")
    return bool(stripped and stripped[-1] in ".!?。！？")


def _format_pair_examples(pairs: list[tuple[str, str]]) -> str:
    examples = "、".join(f"{left}/{right}" for left, right in pairs[:REPETITIVE_ISSUE_SAMPLE_SIZE])
    return f"（示例：{examples}）" if examples else ""


def _check_word_provenance(
    draft: VideoLocalizationDraft,
    blockers: list[VideoLocalizationQualityIssue],
) -> None:
    transcription = draft.transcription
    if not transcription or not transcription.words:
        return

    transcription_word_ids = [word.word_id for word in transcription.words]
    duplicate_transcription_ids = {word_id for word_id, count in Counter(transcription_word_ids).items() if count > 1}
    if duplicate_transcription_ids:
        blockers.append(
            _issue(
                "ASR_WORD_IDS_DUPLICATED",
                f"词级对齐结果包含 {len(duplicate_transcription_ids)} 个重复 word ID，无法可靠追踪字幕来源",
                "blocker",
            )
        )
        return

    word_by_id = {word.word_id: word for word in transcription.words}
    word_index = {word_id: index for index, word_id in enumerate(transcription_word_ids)}
    asr_cues = _current_asr_cues(draft)
    flattened_claims: list[str] = []
    empty_claim_cue_ids: list[str] = []
    unknown_claims: list[tuple[str, str]] = []
    duplicated_within_cue: list[str] = []
    out_of_order_cues: list[str] = []
    text_mismatch_cues: list[str] = []

    for cue in asr_cues:
        ids = cue.source_word_ids
        if not ids:
            empty_claim_cue_ids.append(cue.cue_id)
            continue
        flattened_claims.extend(ids)
        if len(ids) != len(set(ids)):
            duplicated_within_cue.append(cue.cue_id)
        unknown = [word_id for word_id in ids if word_id not in word_by_id]
        if unknown:
            unknown_claims.extend((cue.cue_id, word_id) for word_id in unknown)
            continue

        indices = [word_index[word_id] for word_id in ids]
        if indices != sorted(indices):
            out_of_order_cues.append(cue.cue_id)

        referenced_words = [word_by_id[word_id] for word_id in ids]
        referenced_text = " ".join(word.text for word in referenced_words)
        if _normalize_provenance_text(cue.en_subtitle_text) != _normalize_provenance_text(referenced_text):
            text_mismatch_cues.append(cue.cue_id)

        if cue.start_ms is None or cue.end_ms is None:
            continue
        if cue_tools.manual_timing_confirmation_is_current(cue):
            # Keep word IDs as source provenance while allowing an auditioned
            # human correction to supersede an imperfect aligner edge.
            continue
        effective_onsets = transcription.speech_onset_by_word_id
        first_word = referenced_words[0]
        referenced_start_ms = effective_onsets.get(first_word.word_id, first_word.start_ms)
        referenced_end_ms = max(word.end_ms for word in referenced_words)
        if cue.start_ms > referenced_start_ms or cue.end_ms < referenced_end_ms:
            blockers.append(
                _issue(
                    "ASR_CUE_EXCLUDES_REFERENCED_WORDS",
                    "字幕时间窗裁掉了其引用的词级时间范围，请重新校准出入点",
                    "blocker",
                    cue_id=cue.cue_id,
                )
            )

    if empty_claim_cue_ids:
        blockers.append(
            _issue(
                "ASR_CUE_WORD_IDS_EMPTY",
                _counted_cue_message("ASR 字幕没有记录词级来源", empty_claim_cue_ids),
                "blocker",
            )
        )
    if duplicated_within_cue:
        blockers.append(
            _issue(
                "ASR_CUE_WORD_IDS_DUPLICATED",
                _counted_cue_message("ASR 字幕重复引用同一个词级时间点", duplicated_within_cue),
                "blocker",
            )
        )
    if unknown_claims:
        examples = "、".join(f"{cue_id}:{word_id}" for cue_id, word_id in unknown_claims[:REPETITIVE_ISSUE_SAMPLE_SIZE])
        blockers.append(
            _issue(
                "ASR_CUE_WORD_IDS_MISSING",
                f"ASR 字幕引用了 {len(unknown_claims)} 个不存在的词级时间点（示例：{examples}）",
                "blocker",
            )
        )
    if out_of_order_cues:
        blockers.append(
            _issue(
                "ASR_CUE_WORD_IDS_OUT_OF_ORDER",
                _counted_cue_message("ASR 字幕内部的词级来源顺序错误", out_of_order_cues),
                "blocker",
            )
        )
    if text_mismatch_cues:
        blockers.append(
            _issue(
                "ASR_CUE_TEXT_WORD_MISMATCH",
                _counted_cue_message("ASR 字幕原文与其引用的词级文本不一致", text_mismatch_cues),
                "blocker",
            )
        )

    claim_counts = Counter(word_id for word_id in flattened_claims if word_id in word_by_id)
    shared_word_ids = [word_id for word_id, count in claim_counts.items() if count > 1]
    if shared_word_ids:
        examples = "、".join(shared_word_ids[:REPETITIVE_ISSUE_SAMPLE_SIZE])
        blockers.append(
            _issue(
                "ASR_WORD_ID_SHARED_BY_CUES",
                f"有 {len(shared_word_ids)} 个词级时间点被 ASR 字幕重复覆盖（示例：{examples}）",
                "blocker",
            )
        )

    uncovered_word_ids = [word_id for word_id in transcription_word_ids if claim_counts[word_id] == 0]
    if uncovered_word_ids:
        examples = "、".join(uncovered_word_ids[:REPETITIVE_ISSUE_SAMPLE_SIZE])
        blockers.append(
            _issue(
                "ASR_WORD_IDS_UNCOVERED",
                f"有 {len(uncovered_word_ids)} 个词级时间点未被任何 ASR 字幕覆盖（示例：{examples}）",
                "blocker",
            )
        )

    known_claims = [word_id for word_id in flattened_claims if word_id in word_by_id]
    coverage_is_exact = not shared_word_ids and not uncovered_word_ids and not unknown_claims
    if coverage_is_exact and known_claims != transcription_word_ids:
        blockers.append(
            _issue(
                "ASR_CUE_WORD_SEQUENCE_MISMATCH",
                "ASR 字幕对词级时间点的整体覆盖顺序与听写结果不一致",
                "blocker",
            )
        )


def _current_asr_cues(draft: VideoLocalizationDraft) -> list[VideoLocalizationCue]:
    transcription = draft.transcription
    if not transcription:
        return []
    cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        if cue.transcription_revision_id is not None:
            if cue.transcription_revision_id == transcription.revision_id:
                cues.append(cue)
            continue
        if cue.source_word_ids or "generated_by_asr" in cue.quality_flags:
            cues.append(cue)
    return cues


def _normalize_provenance_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(character for character in normalized if unicodedata.category(character)[0] in {"L", "N"})


def _counted_cue_message(label: str, cue_ids: list[str]) -> str:
    examples = "、".join(cue_ids[:REPETITIVE_ISSUE_SAMPLE_SIZE])
    return f"{label}，共 {len(cue_ids)} 条（示例：{examples}）"


def _check_cue_timeline(
    cues: list[VideoLocalizationCue],
    blockers: list[VideoLocalizationQualityIssue],
) -> None:
    previous_timing: tuple[int, int] | None = None
    for cue in cues:
        if cue.start_ms is None or cue.end_ms is None:
            continue
        if previous_timing is not None:
            previous_start_ms, previous_end_ms = previous_timing
            if cue.start_ms < previous_start_ms:
                blockers.append(
                    _issue(
                        "ASR_CUE_TIMELINE_OUT_OF_ORDER",
                        "ASR cue 时间顺序倒置，需校准轨道顺序",
                        "blocker",
                        cue_id=cue.cue_id,
                    )
                )
            elif cue.start_ms < previous_end_ms:
                blockers.append(
                    _issue(
                        "ASR_CUE_TIMELINE_OVERLAP",
                        "相邻 ASR cue 时间重叠，需校准入点和出点",
                        "blocker",
                        cue_id=cue.cue_id,
                    )
                )
        previous_timing = (cue.start_ms, cue.end_ms)


def _check_media_duration_bounds(
    draft: VideoLocalizationDraft,
    blockers: list[VideoLocalizationQualityIssue],
    *,
    check_cues: bool = True,
    check_localized: bool = True,
) -> None:
    duration_ms = draft.source_media.duration_ms
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms <= 0:
        return

    cue_ids = (
        [cue.cue_id for cue in draft.cues if cue.end_ms is not None and cue.end_ms > duration_ms] if check_cues else []
    )
    if cue_ids:
        blockers.append(
            _issue(
                "ASR_CUE_EXCEEDS_MEDIA_DURATION",
                _counted_cue_message(
                    f"ASR 字幕出点超过媒体总时长 {duration_ms} 毫秒",
                    cue_ids,
                ),
                "blocker",
            )
        )

    subtitle_ids = (
        [subtitle.subtitle_id for subtitle in draft.localized_subtitles if subtitle.end_ms > duration_ms]
        if check_localized
        else []
    )
    if subtitle_ids:
        blockers.append(
            _issue(
                "LOCALIZED_SUBTITLE_EXCEEDS_MEDIA_DURATION",
                _counted_cue_message(
                    f"本土化字幕出点超过媒体总时长 {duration_ms} 毫秒",
                    subtitle_ids,
                ),
                "blocker",
            )
        )


def _check_duplicate_cue_ids(
    cues: list[VideoLocalizationCue],
    blockers: list[VideoLocalizationQualityIssue],
) -> None:
    seen: set[str] = set()
    duplicated: set[str] = set()
    for cue in cues:
        if cue.cue_id in seen:
            duplicated.add(cue.cue_id)
        seen.add(cue.cue_id)
    for cue_id in sorted(duplicated):
        blockers.append(
            _issue(
                "CUE_ID_DUPLICATED",
                "字幕轨包含重复 cue ID，无法可靠导出",
                "blocker",
                cue_id=cue_id,
            )
        )


def _check_localized_subtitles(
    subtitles: list[VideoLocalizationSubtitleCue],
    blockers: list[VideoLocalizationQualityIssue],
    warnings: list[VideoLocalizationQualityIssue],
) -> None:
    seen_ids: set[str] = set()
    duplicated_ids: set[str] = set()
    previous: VideoLocalizationSubtitleCue | None = None
    for subtitle in subtitles:
        if subtitle.subtitle_id in seen_ids:
            duplicated_ids.add(subtitle.subtitle_id)
        seen_ids.add(subtitle.subtitle_id)

        text = subtitle.text.strip()
        if not text:
            blockers.append(
                _issue(
                    "LOCALIZED_SUBTITLE_TEXT_MISSING",
                    "本土化字幕包含空文本，无法导出",
                    "blocker",
                    cue_id=subtitle.linked_cue_id,
                )
            )
        if subtitle.end_ms <= subtitle.start_ms:
            blockers.append(
                _issue(
                    "LOCALIZED_SUBTITLE_DURATION_INVALID",
                    "本土化字幕出点必须晚于入点",
                    "blocker",
                    cue_id=subtitle.linked_cue_id,
                )
            )

        if previous is not None:
            if subtitle.start_ms < previous.start_ms:
                blockers.append(
                    _issue(
                        "LOCALIZED_SUBTITLE_TIMELINE_OUT_OF_ORDER",
                        "本土化字幕时间顺序倒置，需校准轨道顺序",
                        "blocker",
                        cue_id=subtitle.linked_cue_id,
                    )
                )
            elif subtitle.start_ms < previous.end_ms:
                blockers.append(
                    _issue(
                        "LOCALIZED_SUBTITLE_TIMELINE_OVERLAP",
                        "相邻本土化字幕时间重叠，需校准入点和出点",
                        "blocker",
                        cue_id=subtitle.linked_cue_id,
                    )
                )
        previous = subtitle

        if text and subtitle.end_ms > subtitle.start_ms:
            _check_localized_text(
                text=text,
                start_ms=subtitle.start_ms,
                end_ms=subtitle.end_ms,
                blockers=blockers,
                warnings=warnings,
                cue_id=subtitle.linked_cue_id,
            )

    for subtitle_id in sorted(duplicated_ids):
        blockers.append(
            _issue(
                "LOCALIZED_SUBTITLE_ID_DUPLICATED",
                f"本土化字幕轨包含重复字幕 ID：{subtitle_id}",
                "blocker",
            )
        )


def _check_cue_localized_subtitle(
    cue: VideoLocalizationCue,
    blockers: list[VideoLocalizationQualityIssue],
    warnings: list[VideoLocalizationQualityIssue],
) -> None:
    text = (cue.zh_localized_subtitle_text or "").strip()
    if not text or _is_localization_placeholder(text) or cue.start_ms is None or cue.end_ms is None:
        return
    _check_localized_text(
        text=text,
        start_ms=cue.start_ms,
        end_ms=cue.end_ms,
        blockers=blockers,
        warnings=warnings,
        cue_id=cue.cue_id,
    )


def _check_localized_text(
    *,
    text: str,
    start_ms: int,
    end_ms: int,
    blockers: list[VideoLocalizationQualityIssue],
    warnings: list[VideoLocalizationQualityIssue],
    cue_id: str | None,
) -> None:
    duration_ms = end_ms - start_ms
    if duration_ms < LOCALIZED_SUBTITLE_MIN_DURATION_MS:
        blockers.append(
            _issue(
                "LOCALIZED_SUBTITLE_DURATION_TOO_SHORT",
                "本土化字幕时长短于 800 毫秒，未达到导出硬门槛",
                "blocker",
                cue_id=cue_id,
            )
        )
    elif duration_ms > LOCALIZED_SUBTITLE_HARD_MAX_DURATION_MS:
        blockers.append(
            _issue(
                "LOCALIZED_SUBTITLE_DURATION_TOO_LONG",
                "本土化字幕时长超过 7 秒，未达到导出硬门槛",
                "blocker",
                cue_id=cue_id,
            )
        )
    elif duration_ms > LOCALIZED_SUBTITLE_SOFT_MAX_DURATION_MS:
        warnings.append(
            _issue(
                "LOCALIZED_SUBTITLE_DURATION_ABOVE_TARGET",
                "本土化字幕时长超过 6 秒，建议按语义拆分",
                "warning",
                cue_id=cue_id,
            )
        )

    visible_units = _visible_units(text)
    if duration_ms > 0 and visible_units * 1000 > LOCALIZED_SUBTITLE_HARD_MAX_CPS * duration_ms:
        blockers.append(
            _issue(
                "LOCALIZED_SUBTITLE_CPS_HARD_LIMIT",
                "本土化字幕阅读速度超过 12 字/秒，需调整后再导出",
                "blocker",
                cue_id=cue_id,
            )
        )
    elif duration_ms > 0 and visible_units * 1000 > LOCALIZED_SUBTITLE_TARGET_CPS * duration_ms:
        warnings.append(
            _issue(
                "LOCALIZED_SUBTITLE_CPS_HIGH",
                "本土化字幕阅读速度超过 9 字/秒目标，建议精简或调整时间",
                "warning",
                cue_id=cue_id,
            )
        )

    lines = text.splitlines() or [text]
    if len(lines) > LOCALIZED_SUBTITLE_MAX_LINES:
        warnings.append(
            _issue(
                "LOCALIZED_SUBTITLE_TOO_MANY_LINES",
                "本土化字幕超过两行，需按语义重新排版",
                "warning",
                cue_id=cue_id,
            )
        )
    if any(_visible_units(line) > LOCALIZED_SUBTITLE_MAX_LINE_UNITS for line in lines):
        warnings.append(
            _issue(
                "LOCALIZED_SUBTITLE_LINE_TOO_LONG",
                "本土化字幕单行超过约 14 个可视汉字，需检查安全区或重新断行",
                "warning",
                cue_id=cue_id,
            )
        )
    if visible_units > LOCALIZED_SUBTITLE_MAX_TOTAL_UNITS:
        warnings.append(
            _issue(
                "LOCALIZED_SUBTITLE_TOTAL_TOO_LONG",
                "本土化字幕总长度超过约 28 个可视汉字，需优先按语义拆分",
                "warning",
                cue_id=cue_id,
            )
        )


def _visible_units(text: str) -> float:
    units = 0.0
    for character in text:
        if character.isspace():
            continue
        units += 1.0 if unicodedata.east_asian_width(character) in {"W", "F", "A"} else 0.5
    return units


def _deduplicate_issues(
    issues: list[VideoLocalizationQualityIssue],
) -> list[VideoLocalizationQualityIssue]:
    unique: dict[tuple[str, str | None, str | None, str | None], VideoLocalizationQualityIssue] = {}
    for issue in issues:
        key = (issue.code, issue.cue_id, issue.speaker_id, issue.reference_clip_id)
        unique.setdefault(key, issue)
    return list(unique.values())


def _finalize_issues(
    issues: list[VideoLocalizationQualityIssue],
) -> list[VideoLocalizationQualityIssue]:
    deduplicated = _deduplicate_issues(issues)
    grouped: dict[tuple[str, str], list[VideoLocalizationQualityIssue]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for issue in deduplicated:
        key = (issue.code, issue.severity)
        if key not in grouped:
            order.append(key)
        grouped[key].append(issue)

    finalized: list[VideoLocalizationQualityIssue] = []
    for key in order:
        group = grouped[key]
        if len(group) < REPETITIVE_ISSUE_THRESHOLD:
            finalized.extend(group)
            continue
        cue_ids = [item.cue_id for item in group if item.cue_id]
        examples = "、".join(cue_ids[:REPETITIVE_ISSUE_SAMPLE_SIZE])
        suffix = f"，示例：{examples}" if examples else ""
        finalized.append(
            _issue(
                group[0].code,
                f"{group[0].message}（同类问题共 {len(group)} 项{suffix}）",
                group[0].severity,
            )
        )
    return finalized


def _check_cue_basics(
    cue: VideoLocalizationCue,
    speaker_ids: set[str],
    blockers: list[VideoLocalizationQualityIssue],
    warnings: list[VideoLocalizationQualityIssue],
    *,
    check_localization: bool,
    check_dubbing: bool,
) -> None:
    if cue.start_ms is None or cue.end_ms is None:
        blockers.append(_issue("CUE_TIMECODE_MISSING", "cue 缺少入点或出点", "blocker", cue_id=cue.cue_id))
    elif cue.end_ms <= cue.start_ms:
        blockers.append(_issue("CUE_DURATION_INVALID", "cue 出点必须晚于入点", "blocker", cue_id=cue.cue_id))
    if check_dubbing:
        if not cue.speaker_id:
            blockers.append(_issue("CUE_SPEAKER_MISSING", "cue 缺少说话人", "blocker", cue_id=cue.cue_id))
        elif cue.speaker_id != "mixed" and cue.speaker_id not in speaker_ids:
            blockers.append(
                _issue(
                    "CUE_SPEAKER_NOT_FOUND",
                    "cue 绑定的说话人不存在",
                    "blocker",
                    cue_id=cue.cue_id,
                    speaker_id=cue.speaker_id,
                )
            )
        if cue.speaker_id == "mixed" and cue.audio_route != "preserve_original_audio":
            blockers.append(
                _issue("MIXED_SPEAKER_NEEDS_SPLIT", "混合说话需拆分或标记保留原声", "blocker", cue_id=cue.cue_id)
            )
    if not _has_text(cue.en_subtitle_text):
        blockers.append(_issue("EN_SUBTITLE_MISSING", "cue 缺少英文字幕", "blocker", cue_id=cue.cue_id))
    if check_localization:
        if not _has_text(cue.zh_localized_subtitle_text):
            blockers.append(_issue("ZH_SUBTITLE_MISSING", "cue 缺少中文字幕", "blocker", cue_id=cue.cue_id))
        elif _is_localization_placeholder(cue.zh_localized_subtitle_text):
            blockers.append(
                _issue("ZH_SUBTITLE_PLACEHOLDER", "中文字幕仍是待本土化占位稿", "blocker", cue_id=cue.cue_id)
            )
    if check_dubbing:
        if not _has_text(cue.tts_recommended_text):
            blockers.append(_issue("TTS_TEXT_MISSING", "cue 缺少 TTS 台词", "blocker", cue_id=cue.cue_id))
        elif _is_localization_placeholder(cue.tts_recommended_text):
            blockers.append(_issue("TTS_TEXT_PLACEHOLDER", "TTS 台词仍是待本土化占位稿", "blocker", cue_id=cue.cue_id))
        elif cue.tts_recommended_text.strip() == (cue.zh_localized_subtitle_text or "").strip():
            warnings.append(
                _issue(
                    "TTS_TEXT_NOT_NORMALIZED",
                    "TTS 台词与中文字幕相同，可能未做口播规范化",
                    "warning",
                    cue_id=cue.cue_id,
                )
            )
    if cue.review_status == "blocked":
        blockers.append(_issue("CUE_REVIEW_BLOCKED", "cue 被人工标记为阻断", "blocker", cue_id=cue.cue_id))
    elif cue.review_status == "needs_review":
        warnings.append(_issue("CUE_NEEDS_REVIEW", "cue 仍待人工校对", "warning", cue_id=cue.cue_id))


def _check_cue_reference(
    cue: VideoLocalizationCue,
    reference_by_id: dict[str, VideoLocalizationReferenceClip],
    blockers: list[VideoLocalizationQualityIssue],
    warnings: list[VideoLocalizationQualityIssue],
) -> None:
    if cue.audio_route != "clone_from_source":
        if cue.audio_route == "manual_review":
            warnings.append(_issue("AUDIO_ROUTE_NEEDS_REVIEW", "音频路线仍待人工确认", "warning", cue_id=cue.cue_id))
        return

    if not cue.reference_clip_id:
        blockers.append(_issue("REFERENCE_CLIP_MISSING", "克隆路线缺少参考音色", "blocker", cue_id=cue.cue_id))
        return

    reference = reference_by_id.get(cue.reference_clip_id)
    if not reference:
        blockers.append(
            _issue(
                "REFERENCE_CLIP_NOT_FOUND",
                "cue 绑定的参考音不存在",
                "blocker",
                cue_id=cue.cue_id,
                reference_clip_id=cue.reference_clip_id,
            )
        )
        return

    if reference.source_stem != "vocals_clean":
        blockers.append(
            _issue(
                "REFERENCE_NOT_FROM_CLEAN_VOCALS",
                "参考音必须来自分离后的干净人声",
                "blocker",
                cue_id=cue.cue_id,
                reference_clip_id=reference.reference_clip_id,
            )
        )
    if reference.cleanliness != "clean":
        blockers.append(
            _issue(
                "REFERENCE_NOT_CLEAN",
                "参考音未标记为干净人声",
                "blocker",
                cue_id=cue.cue_id,
                reference_clip_id=reference.reference_clip_id,
            )
        )
    if reference.asr_status != "verified":
        blockers.append(
            _issue(
                "REFERENCE_ASR_NOT_VERIFIED",
                "参考音尚未完成独立 ASR 校验",
                "blocker",
                cue_id=cue.cue_id,
                reference_clip_id=reference.reference_clip_id,
            )
        )
    if reference.speaker_id and cue.speaker_id and reference.speaker_id != cue.speaker_id:
        warnings.append(
            _issue(
                "REFERENCE_SPEAKER_MISMATCH",
                "参考音说话人与 cue 说话人不一致",
                "warning",
                cue_id=cue.cue_id,
                reference_clip_id=reference.reference_clip_id,
            )
        )


def _check_cue_duration(cue: VideoLocalizationCue, warnings: list[VideoLocalizationQualityIssue]) -> None:
    if not cue.source_duration_ms or not cue.generated_duration_ms:
        return
    diff = abs(cue.generated_duration_ms - cue.source_duration_ms)
    if diff / max(cue.source_duration_ms, 1) > 0.2:
        warnings.append(
            _issue("TTS_DURATION_MISMATCH", "生成音频与原时间窗时长差超过 20%", "warning", cue_id=cue.cue_id)
        )


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _is_localization_placeholder(value: str | None) -> bool:
    return bool(value and value.strip().startswith("【待本土化】"))


def _issue(
    code: str,
    message: str,
    severity: str,
    cue_id: str | None = None,
    speaker_id: str | None = None,
    reference_clip_id: str | None = None,
) -> VideoLocalizationQualityIssue:
    return VideoLocalizationQualityIssue(
        code=code,
        message=message,
        severity=severity,  # type: ignore[arg-type]
        cue_id=cue_id,
        speaker_id=speaker_id,
        reference_clip_id=reference_clip_id,
    )
