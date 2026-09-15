from __future__ import annotations

import asyncio
import logging
import signal
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, cast

from app.domains.video_localization import (
    asr_pipeline,
    asr_timing_contracts,
    entity_normalization,
    operation_detail_reader,
    operation_summary_legacy,
    operation_summary_projection,
    operation_queue,
    operation_state,
    research_evidence,
    review_decisions,
    section_review,
    transcript_quality_gate,
    transcription,
    visual_evidence,
    whole_recheck,
)
from app.domains.video_localization.document_understanding_contracts import (
    AsrDocumentUnderstandingResult,
)
from app.domains.video_localization.draft_store import DRAFT_WRITE_LOCK
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
    VideoLocalizationOperationSummary,
    VideoLocalizationTranscriptSegment,
)
from app.errors import AppException
from app.schemas.video_localization_operation_feed import (
    VideoLocalizationOperationFeedV2,
)
from app.schemas.video_localization_asr_raw_step import (
    AsrRawResultV2,
)
from app.schemas.video_localization_operation_detail import (
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
)
from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_WORKFLOW_VERSION,
)
from app.schemas.video_localization_stem_separation_step import (
    STEM_SEPARATION_WORKFLOW_VERSION,
)
from app.schemas.video_localization_reference_candidates_step import (
    REFERENCE_CANDIDATES_WORKFLOW_VERSION,
)
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_WORKFLOW_VERSION,
)
from app.services import database, settings_store
from app.services import (
    video_localization_operation_summary_migration,
)
from app.services import video_localization_operation_summary_store


logger = logging.getLogger(__name__)


def run_external_subtitle_evidence(
    *,
    audio_path: str,
    audio_sha256: str,
    source_track_id: str,
    segments: list[VideoLocalizationTranscriptSegment],
    language: str,
    duration_ms: int,
    video_frame_rate: float | None,
):
    """Application port shared by the public ASR adapter and project flow."""

    pipeline = asr_pipeline.AsrPipeline()
    alignment = pipeline.run_strict_alignment(
        asr_timing_contracts.AsrAlignmentInput(
            alignment_audio_path=audio_path,
            alignment_audio_sha256=audio_sha256,
            alignment_source_track_id=source_track_id,
            segments=segments,
            language=language,
            duration_ms=duration_ms,
        )
    )
    delivery_words, delivery_metadata = (
        transcription.coalesce_zero_width_alignment_words(
            alignment.words
        )
    )
    if delivery_metadata["coalesced_group_count"]:
        quality_flags = list(
            alignment.metadata.get("quality_flags") or []
        )
        if delivery_metadata["zero_width_token_count"]:
            quality_flags.append(
                "alignment_zero_width_tokens_coalesced"
            )
        else:
            quality_flags.append(
                "alignment_overlapping_tokens_coalesced"
            )
        alignment = alignment.model_copy(
            update={
                "words": delivery_words,
                "metadata": {
                    **alignment.metadata,
                    **delivery_metadata,
                    "quality_flags": list(dict.fromkeys(quality_flags)),
                },
            }
        )
    boundaries = pipeline.run_audio_boundaries(
        asr_timing_contracts.AsrAudioBoundariesInput(
            audio_path=audio_path,
            audio_sha256=audio_sha256,
            source_track_id=source_track_id,
            words=delivery_words,
            video_frame_rate=video_frame_rate,
        )
    )
    return alignment, boundaries


FeedCacheKey = tuple[object, ...]
OperationSummaryBackfillReport = (
    video_localization_operation_summary_migration
    .OperationSummaryBackfillReport
)
OperationSummaryReconciliationReport = (
    video_localization_operation_summary_migration
    .OperationSummaryReconciliationReport
)
OperationSummaryPromotionReport = (
    video_localization_operation_summary_migration
    .OperationSummaryPromotionReport
)
OperationSummaryAuthorityCloseReport = (
    video_localization_operation_summary_migration
    .OperationSummaryAuthorityCloseReport
)


@dataclass(frozen=True)
class _CachedFeed:
    revision: int
    feed: VideoLocalizationOperationFeedV2


class OperationFeedReader:
    """Bounded single-flight cache for immutable operation feed revisions.

    Project/ledger persistence remains authoritative. Every read first checks
    the durable projection revision; the cache only avoids rebuilding the same
    immutable summary projection for concurrent callers.
    """

    def __init__(self, *, max_entries: int = 32) -> None:
        if max_entries < 1:
            raise ValueError("operation feed cache must retain an entry")
        self._max_entries = max_entries
        self._lock = threading.RLock()
        self._cache: OrderedDict[
            FeedCacheKey,
            _CachedFeed,
        ] = OrderedDict()
        self._inflight: dict[FeedCacheKey, threading.Event] = {}

    def read_v2_page(
        self,
        project_id: str,
        *,
        descriptor: (
            video_localization_operation_summary_store
            .OperationSummaryFeedDescriptor
        ),
        cursor: (
            video_localization_operation_summary_store
            .OperationSummaryHistoryCursor
            | None
        ),
        cursor_token: str | None,
        history_limit: int,
    ) -> VideoLocalizationOperationFeedV2 | None:
        if descriptor.summary_status == "repair_required":
            self._raise_projection_error()
        if not descriptor.repository_ready:
            self._raise_authority_not_closed()
        key = (
            *self._cache_key(project_id),
            "v2",
            descriptor.history_revision,
            history_limit,
            cursor_token or "",
        )

        def build() -> VideoLocalizationOperationFeedV2 | None:
            page = (
                video_localization_operation_summary_store
                .read_verified_project_summary_page(
                    project_id,
                    history_limit=history_limit,
                    cursor=cursor,
                )
            )
            return (
                _repository_page_feed(page)
                if page is not None
                else None
            )

        return cast(
            VideoLocalizationOperationFeedV2 | None,
            self._read_cached(
                key,
                revision=descriptor.projection_revision,
                build=build,
            ),
        )

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _read_cached(
        self,
        key: FeedCacheKey,
        *,
        revision: int,
        build: Callable[[], VideoLocalizationOperationFeedV2 | None],
    ) -> VideoLocalizationOperationFeedV2 | None:
        while True:
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None and cached.revision == revision:
                    self._cache.move_to_end(key)
                    return self._response_copy(cached.feed)
                waiter = self._inflight.get(key)
                if waiter is None:
                    waiter = threading.Event()
                    self._inflight[key] = waiter
                    leader = True
                else:
                    leader = False
            if not leader:
                waiter.wait()
                continue
            try:
                feed = build()
                if feed is None:
                    return None
                if not feed.changed:
                    raise RuntimeError(
                        "full operation feed build returned unchanged"
                    )
                self._store(key, feed)
                return self._response_copy(feed)
            finally:
                with self._lock:
                    completed = self._inflight.pop(key, None)
                    if completed is not None:
                        completed.set()

    @staticmethod
    def _feed_descriptor(
        project_id: str,
    ) -> (
        video_localization_operation_summary_store
        .OperationSummaryFeedDescriptor
        | None
    ):
        try:
            return (
                video_localization_operation_summary_store
                .read_feed_descriptor(project_id)
            )
        except (
            video_localization_operation_summary_store
            .OperationSummaryProjectionConflict
        ):
            OperationFeedReader._raise_projection_error()

    @staticmethod
    def _raise_projection_error() -> None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_SUMMARY_REPAIR_REQUIRED",
            "任务历史摘要需要修复，请先运行本土化任务摘要审计和修复。",
        )

    @staticmethod
    def _raise_authority_not_closed() -> None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_SUMMARY_AUTHORITY_NOT_CLOSED",
            "任务历史摘要迁移尚未完成，请先完成全库对账和权威切换。",
        )

    def _store(
        self,
        key: FeedCacheKey,
        feed: VideoLocalizationOperationFeedV2,
    ) -> None:
        with self._lock:
            self._cache[key] = _CachedFeed(
                revision=feed.revision,
                feed=feed.model_copy(deep=True),
            )
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)

    @staticmethod
    def _response_copy(
        feed: VideoLocalizationOperationFeedV2,
    ) -> VideoLocalizationOperationFeedV2:
        """Isolate the response list while reusing immutable summaries."""

        return VideoLocalizationOperationFeedV2(
            revision=feed.revision,
            history_revision=feed.history_revision,
            changed=feed.changed,
            active_operations=list(feed.active_operations),
            history=list(feed.history),
            history_total=feed.history_total,
            next_cursor=feed.next_cursor,
        )

    @staticmethod
    def _cache_key(
        project_id: str,
    ) -> FeedCacheKey:
        return (
            database.runtime_identity(),
            project_id,
            "repository",
        )


_operation_feed_reader = OperationFeedReader()


def read_operation_feed_v2(
    project_id: str,
    *,
    after_revision: int | None = None,
    cursor: str | None = None,
    history_limit: int = 50,
) -> VideoLocalizationOperationFeedV2 | None:
    if cursor is not None and after_revision is not None:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_OPERATION_FEED_QUERY_INVALID",
            "历史游标请求不能同时携带 after_revision。",
        )
    if (
        history_limit < 1
        or history_limit
        > video_localization_operation_summary_store
        .MAX_HISTORY_PAGE_SIZE
    ):
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_OPERATION_HISTORY_LIMIT_INVALID",
            "任务历史每页数量必须在 1 到 100 之间。",
        )
    decoded_cursor = _decode_history_cursor(cursor)
    for _attempt in range(2):
        descriptor = OperationFeedReader._feed_descriptor(project_id)
        if descriptor is None:
            return None
        if descriptor.summary_status == "repair_required":
            OperationFeedReader._raise_projection_error()
        if not descriptor.repository_ready:
            OperationFeedReader._raise_authority_not_closed()
        if (
            cursor is None
            and after_revision is not None
            and after_revision == descriptor.projection_revision
        ):
            return VideoLocalizationOperationFeedV2(
                revision=descriptor.projection_revision,
                history_revision=descriptor.history_revision,
                changed=False,
            )
        try:
            return _operation_feed_reader.read_v2_page(
                project_id,
                descriptor=descriptor,
                cursor=decoded_cursor,
                cursor_token=cursor,
                history_limit=history_limit,
            )
        except (
            video_localization_operation_summary_store
            .OperationSummaryProjectionSourceChanged
        ):
            continue
        except (
            video_localization_operation_summary_store
            .OperationSummaryHistoryCursorStale
        ):
            _raise_history_cursor_stale()
        except (
            video_localization_operation_summary_store
            .OperationSummaryProjectionConflict
        ):
            OperationFeedReader._raise_projection_error()
    OperationFeedReader._raise_projection_error()


def _repository_page_feed(
    page: (
        video_localization_operation_summary_store
        .OperationSummaryProjectionPage
    ),
) -> VideoLocalizationOperationFeedV2:
    return VideoLocalizationOperationFeedV2(
        revision=page.descriptor.projection_revision,
        history_revision=page.descriptor.history_revision,
        changed=True,
        active_operations=[
            _repository_record_summary(record)
            for record in page.active_records
        ],
        history=[
            _repository_record_summary(record)
            for record in page.history_records
        ],
        history_total=page.history_total,
        next_cursor=page.next_cursor,
    )


def _repository_record_summary(
    record: (
        video_localization_operation_summary_store
        .OperationSummaryProjectionRecord
    ),
) -> VideoLocalizationOperationSummary:
    return operation_queue.project_repository_operation_summary(
        operation_summary_projection.assemble_operation_summary(
            record.core,
            kind=record.kind,
            status=record.status,
            cancel_requested=record.cancel_requested,
            created_at=record.created_at,
            completed_at=record.completed_at,
        )
    )


def _decode_history_cursor(
    cursor: str | None,
) -> (
    video_localization_operation_summary_store
    .OperationSummaryHistoryCursor
    | None
):
    if cursor is None:
        return None
    try:
        return (
            video_localization_operation_summary_store
            .decode_history_cursor(cursor)
        )
    except (
        video_localization_operation_summary_store
        .OperationSummaryHistoryCursorInvalid
    ) as exc:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_OPERATION_HISTORY_CURSOR_INVALID",
            "任务历史游标无效，请重新读取第一页。",
        ) from exc


def _raise_history_cursor_stale() -> None:
    raise AppException(
        409,
        "VIDEO_LOCALIZATION_OPERATION_HISTORY_CURSOR_STALE",
        "任务历史在翻页期间发生变化，请重新读取第一页。",
        {"restart": "head"},
    )


def backfill_operation_summaries(
    *,
    after_project_id: str | None = None,
    limit: int = 100,
) -> OperationSummaryBackfillReport:
    return (
        video_localization_operation_summary_migration
        .backfill_operation_summaries(
            operation_summary_legacy
            .decode_legacy_summary_source,
            after_project_id=after_project_id,
            limit=limit,
        )
    )


def reconcile_operation_summaries(
    *,
    limit: int = 1_000,
) -> OperationSummaryReconciliationReport:
    return (
        video_localization_operation_summary_migration
        .reconcile_operation_summaries(
            operation_summary_legacy
            .decode_legacy_summary_source,
            limit=limit,
        )
    )


def promote_operation_summaries(
    *,
    after_project_id: str | None = None,
    limit: int = 100,
) -> OperationSummaryPromotionReport:
    return (
        video_localization_operation_summary_migration
        .promote_operation_summaries(
            operation_summary_legacy
            .decode_legacy_summary_source,
            after_project_id=after_project_id,
            limit=limit,
        )
    )


def close_operation_summary_authority(
    *,
    limit: int = 1_000,
) -> OperationSummaryAuthorityCloseReport:
    return (
        video_localization_operation_summary_migration
        .close_operation_summary_authority(
            operation_summary_legacy.decode_legacy_summary_source,
            limit=limit,
        )
    )


def submit_operation(
    project_id: str,
    kind: operation_state.OperationKind,
    parameters: dict | None = None,
) -> VideoLocalizationOperation | None:
    """Submit one operation through the project-scoped persistence boundary."""

    with DRAFT_WRITE_LOCK:
        return operation_queue.submit(project_id, kind, parameters)


def list_operations(
    project_id: str,
) -> list[VideoLocalizationOperation] | None:
    return operation_queue.list_operations(project_id)


def get_operation(
    project_id: str,
    operation_id: str,
) -> VideoLocalizationOperation | None:
    return operation_queue.get_operation(project_id, operation_id)


def get_operation_detail(
    project_id: str,
    operation_id: str,
) -> VideoLocalizationOperation | None:
    try:
        detail = operation_detail_reader.read_operation_detail(
            project_id,
            operation_id,
        )
    except (
        operation_detail_reader.OperationDetailRepairRequired
    ) as exc:
        _raise_operation_detail_repair_required(exc)
    if detail.authority == "managed":
        return detail.operation
    legacy = operation_queue.get_operation(project_id, operation_id)
    if (
        detail.authority == "missing"
        and legacy is not None
        and str(
            legacy.result_summary.get(
                "workflow_schema_version"
            )
            or ""
        ).strip()
        in {
            SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
            SOURCE_AUDIO_WORKFLOW_VERSION,
            STEM_SEPARATION_WORKFLOW_VERSION,
            REFERENCE_CANDIDATES_WORKFLOW_VERSION,
            SPEAKER_DIARIZATION_WORKFLOW_VERSION,
            ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
            ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
            ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
            ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
        }
    ):
        _raise_operation_detail_repair_required(
            operation_detail_reader.OperationDetailRepairRequired(
                "ledger_operation_missing"
            )
        )
    return legacy


def _raise_operation_detail_repair_required(
    exc: operation_detail_reader.OperationDetailRepairRequired,
) -> None:
    raise AppException(
        409,
        "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
        str(exc),
        {"issue_codes": list(exc.issue_codes)},
    ) from exc


def cancel_operation(
    project_id: str,
    operation_id: str,
) -> VideoLocalizationOperation | None:
    return operation_queue.cancel(project_id, operation_id)


def retry_operation(
    project_id: str,
    operation_id: str,
) -> VideoLocalizationOperation | None:
    return operation_queue.retry(project_id, operation_id)


def get_development_asr_result(
    project_id: str,
    operation_id: str,
) -> transcription.TranscribeRawOutput | AsrRawResultV2 | None:
    return operation_queue.get_development_asr_result(
        project_id,
        operation_id,
    )


def get_development_initial_analysis_result(
    project_id: str,
    operation_id: str,
) -> asr_pipeline.AsrInitialAnalysisSnapshot | None:
    return operation_queue.get_development_initial_analysis_result(
        project_id,
        operation_id,
    )


def get_development_document_understanding_result(
    project_id: str,
    operation_id: str,
) -> AsrDocumentUnderstandingResult | None:
    return operation_queue.get_development_document_understanding_result(
        project_id,
        operation_id,
    )


def get_development_visual_evidence_result(
    project_id: str,
    operation_id: str,
) -> visual_evidence.AsrVisualEvidenceResult | None:
    return operation_queue.get_development_visual_evidence_result(
        project_id,
        operation_id,
    )


def get_development_visual_evidence_frame(
    project_id: str,
    operation_id: str,
    frame_id: str,
) -> Path | None:
    return operation_queue.get_development_visual_evidence_frame(
        project_id,
        operation_id,
        frame_id,
    )


def get_formal_visual_evidence_frame(
    project_id: str,
    operation_id: str,
    frame_id: str,
) -> Path | None:
    return operation_queue.get_formal_visual_evidence_frame(
        project_id,
        operation_id,
        frame_id,
    )


def get_development_research_evidence_result(
    project_id: str,
    operation_id: str,
) -> research_evidence.AsrResearchEvidenceResult | None:
    return operation_queue.get_development_research_evidence_result(
        project_id,
        operation_id,
    )


def get_development_entity_normalization_result(
    project_id: str,
    operation_id: str,
) -> entity_normalization.AsrEntityNormalizationResult | None:
    return operation_queue.get_development_entity_normalization_result(
        project_id,
        operation_id,
    )


def get_development_section_review_result(
    project_id: str,
    operation_id: str,
) -> section_review.AsrSectionReviewResult | None:
    return operation_queue.get_development_section_review_result(
        project_id,
        operation_id,
    )


def get_development_review_decisions_result(
    project_id: str,
    operation_id: str,
) -> review_decisions.AsrReviewDecisionsResult | None:
    return operation_queue.get_development_review_decisions_result(
        project_id,
        operation_id,
    )


def get_development_whole_recheck_result(
    project_id: str,
    operation_id: str,
) -> whole_recheck.AsrWholeRecheckResult | None:
    return operation_queue.get_development_whole_recheck_result(
        project_id,
        operation_id,
    )


def get_development_transcript_quality_gate_result(
    project_id: str,
    operation_id: str,
) -> transcript_quality_gate.AsrTranscriptQualityGateResult | None:
    return operation_queue.get_development_transcript_quality_gate_result(
        project_id,
        operation_id,
    )


def get_development_diarization_result(
    project_id: str,
    operation_id: str,
) -> object | None:
    return operation_queue.get_development_diarization_result(
        project_id,
        operation_id,
    )


async def run_worker(
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the durable operation worker without an HTTP server."""

    settings_store.ensure_directories()
    owned_stop_event = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    if stop_event is None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    signum,
                    owned_stop_event.set,
                )
            except NotImplementedError:
                continue
            installed_signals.append(signum)

    operation_queue.start_worker()
    logger.info(
        "video-localization operation worker started"
    )
    try:
        await owned_stop_event.wait()
    finally:
        await operation_queue.shutdown()
        for signum in installed_signals:
            loop.remove_signal_handler(signum)
        logger.info(
            "video-localization operation worker stopped"
        )


def run_worker_main() -> None:
    asyncio.run(run_worker())
