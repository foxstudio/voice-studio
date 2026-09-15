"""Durable LLM gateway for managed transcript section review."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_section_review_managed_contracts as managed_contracts,
    managed_artifact_files,
    section_review,
)
from app.errors import AppException
from app.schemas.video_localization_asr_section_review_step import (
    ASR_SECTION_REVIEW_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
    AsrSectionReviewCallArtifactV1,
    parse_section_review_call_artifact,
    section_review_call_artifact_bytes,
    section_review_call_input,
    section_review_call_input_fingerprint,
)
from app.schemas.video_localization_llm_observability import (
    VideoLocalizationLlmCallRecord,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.services import llm_runtime
from app.services import (
    video_localization_llm_provider_execution as provider_execution,
)
from app.services import (
    video_localization_provider_step_lifecycle as provider_lifecycle,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
)


CompleteJson = Callable[..., dict | list]
KnownAttemptSink = Callable[["KnownSectionReviewAttempt"], None]
_ARTIFACT_KIND = "step-result"
_ARTIFACT_KEY = "primary"


@dataclass(frozen=True)
class KnownSectionReviewAttempt:
    reference: (
        managed_contracts.AsrSectionReviewAttemptReferenceV1
    )
    artifact: AsrSectionReviewCallArtifactV1 | None = None


@dataclass(frozen=True)
class ManagedSectionReviewGateway:
    execution_fence: ExecutionFence
    resolved_profile: llm_runtime.ResolvedProfile
    expected_profile_configuration_fingerprint: str
    behavior_fingerprint: str
    file_backend: ManagedArtifactFileBackend = managed_artifact_files
    complete_json: CompleteJson = llm_runtime.complete_json
    on_known_attempt: KnownAttemptSink | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def __post_init__(self) -> None:
        provider = provider_execution.classify_provider_profile(
            self.resolved_profile
        )
        if (
            provider.configuration_fingerprint
            != self.expected_profile_configuration_fingerprint
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SECTION_REVIEW_PROFILE_CHANGED",
                "分段复查使用的模型配置已经变化，请重新提交任务。",
            )
        if re.fullmatch(
            r"[0-9a-f]{64}",
            self.behavior_fingerprint,
        ) is None:
            raise ValueError(
                "section-review behavior fingerprint is invalid"
            )

    def __call__(
        self,
        request: section_review.AsrSectionReviewCompletionRequest,
    ) -> dict:
        if not isinstance(
            request,
            section_review.AsrSectionReviewCompletionRequest,
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SECTION_REVIEW_CALL_INVALID",
                "分段复查产生了未知模型调用。",
            )
        if (
            request.contract_version
            != "asr-section-review-call-input-v1"
            or request.behavior_version
            != section_review.PROMPT_VERSION
            or request.profile_id
            != self.resolved_profile.profile_id
            or request.round_index != 1
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SECTION_REVIEW_INPUT_CHANGED",
                "分段复查调用与已锁定任务不一致。",
            )
        provider = provider_execution.classify_provider_profile(
            self.resolved_profile
        )
        call_input = section_review_call_input(
            behavior_version=request.behavior_version,
            behavior_fingerprint=self.behavior_fingerprint,
            call_id=request.call_id,
            section_id=request.section_id,
            section_start_ordinal=request.section_start_ordinal,
            core_segment_ids=request.core_segment_ids,
            attempt=request.attempt,
            round_index=request.round_index,
            system_prompt=request.system_prompt,
            user_payload=request.user_payload,
            profile_id=self.resolved_profile.profile_id,
            model_id=self.resolved_profile.model_id,
            provider_protocol=self.resolved_profile.protocol,
            provider_endpoint_fingerprint=(
                provider.endpoint_fingerprint
            ),
            profile_configuration_fingerprint=(
                provider.configuration_fingerprint
            ),
            max_tokens=request.max_tokens,
            timeout=request.timeout,
            disable_reasoning=request.disable_reasoning,
        )
        input_fingerprint = (
            section_review_call_input_fingerprint(call_input)
        )
        step_id = _step_id(request)
        idempotency_key = provider_execution.provider_idempotency_key(
            self.execution_fence,
            key_prefix="vsl_asr_sec_",
            step_id=step_id,
            input_fingerprint=input_fingerprint,
        )
        plan = provider_lifecycle.ProviderStepPlan(
            step_id=step_id,
            workflow_version=(
                ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
            ),
            input_fingerprint=input_fingerprint,
            cost_class=provider.cost_class,
            provider_name=provider.provider_name,
            provider_idempotency_key=idempotency_key,
            artifact_kind=_ARTIFACT_KIND,
            artifact_key=_ARTIFACT_KEY,
            payload_schema_version=(
                ASR_SECTION_REVIEW_CALL_ARTIFACT_SCHEMA_VERSION
            ),
            media_type="application/json",
        )
        try:
            executed = provider_lifecycle.run_provider_step(
                self.execution_fence,
                file_backend=self.file_backend,
                plan=plan,
                submit=lambda key: self._submit(
                    request,
                    idempotency_key=key,
                    input_fingerprint=input_fingerprint,
                ),
                clock=self.clock,
            )
        except (
            provider_lifecycle.ProviderReplayBlocked,
            provider_lifecycle.ProviderStepResultUnknown,
        ) as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SECTION_REVIEW_RESULT_UNKNOWN",
                "分段复查的模型请求可能已经执行，但结果暂时无法确认；系统没有自动重复请求。",
            ) from exc
        except provider_lifecycle.ProviderStepExecutionFailed as exc:
            self._record_failed(
                request,
                step=exc.step,
            )
            raise _runtime_error_from_failed_step(exc) from exc
        except provider_lifecycle.ProviderStepIntegrityError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SECTION_REVIEW_CALL_ARTIFACT_INVALID",
                "分段复查的模型调用记录缺失或损坏，请先运行任务详情审计。",
            ) from exc
        try:
            artifact = parse_section_review_call_artifact(
                executed.content
            )
        except (TypeError, ValueError) as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SECTION_REVIEW_CALL_ARTIFACT_INVALID",
                "分段复查的模型调用记录无法通过完整性校验。",
            ) from exc
        if (
            artifact.call_input_fingerprint != input_fingerprint
            or artifact.call_id != request.call_id
            or artifact.section_id != request.section_id
            or artifact.section_start_ordinal
            != request.section_start_ordinal
            or artifact.core_segment_ids
            != request.core_segment_ids
            or artifact.attempt != request.attempt
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SECTION_REVIEW_CALL_ARTIFACT_INVALID",
                "分段复查的模型调用记录与已锁定输入不一致。",
            )
        if executed.outcome == "reused" and request.trace_sink is not None:
            request.trace_sink(_runtime_trace(artifact.llm_call))
        self._record(
            KnownSectionReviewAttempt(
                reference=(
                    managed_contracts
                    .AsrSectionReviewAttemptReferenceV1(
                        call_id=request.call_id,
                        section_id=request.section_id,
                        section_start_ordinal=(
                            request.section_start_ordinal
                        ),
                        core_segment_ids=request.core_segment_ids,
                        attempt=request.attempt,
                        step_id=executed.step.step_id,
                        input_fingerprint=(
                            executed.step.input_fingerprint
                        ),
                        status="success",
                        artifact_fingerprint=(
                            executed.artifact.content_fingerprint
                        ),
                    )
                ),
                artifact=artifact,
            )
        )
        return dict(artifact.response)

    def _submit(
        self,
        request: section_review.AsrSectionReviewCompletionRequest,
        *,
        idempotency_key: str,
        input_fingerprint: str,
    ) -> provider_lifecycle.ProviderResponse:
        traces: list[llm_runtime.LlmCompletionTrace] = []

        def record(trace: llm_runtime.LlmCompletionTrace) -> None:
            traces.append(trace)
            if request.trace_sink is not None:
                request.trace_sink(trace)

        try:
            raw = self.complete_json(
                system_prompt=request.system_prompt,
                user_payload=request.user_payload,
                profile_id=self.resolved_profile.profile_id,
                max_tokens=request.max_tokens,
                timeout=request.timeout,
                disable_reasoning=request.disable_reasoning,
                trace_sink=record,
                idempotency_key=idempotency_key,
                resolved_profile=self.resolved_profile,
            )
        except llm_runtime.LlmRuntimeError as exc:
            if provider_execution.is_uncertain_llm_error_code(exc.code):
                raise provider_lifecycle.ProviderResultUncertain(
                    "VIDEO_LOCALIZATION_LLM_RESULT_UNKNOWN",
                    provider_request_id=(
                        provider_execution.trace_response_id(traces)
                    ),
                ) from None
            raise provider_lifecycle.ProviderRequestRejected(
                provider_execution.known_provider_error_code(exc.code)
            ) from None
        if not isinstance(raw, dict) or len(traces) != 1:
            raise provider_lifecycle.ProviderRequestRejected(
                "VIDEO_LOCALIZATION_LLM_TRACE_INVALID"
            )
        call = VideoLocalizationLlmCallRecord.from_runtime(
            traces[0],
            call_id=f"{request.call_id}-a{request.attempt:02d}",
            purpose="section_review",
            round_index=1,
            candidate_ids=list(request.core_segment_ids),
        )
        artifact = AsrSectionReviewCallArtifactV1(
            call_input_fingerprint=input_fingerprint,
            call_id=request.call_id,
            section_id=request.section_id,
            section_start_ordinal=request.section_start_ordinal,
            core_segment_ids=request.core_segment_ids,
            attempt=request.attempt,
            response=raw,
            llm_call=call,
        )
        return provider_lifecycle.ProviderResponse(
            content=section_review_call_artifact_bytes(artifact),
            provider_request_id=call.response_id,
        )

    def _record_failed(
        self,
        request: section_review.AsrSectionReviewCompletionRequest,
        *,
        step,
    ) -> None:
        self._record(
            KnownSectionReviewAttempt(
                reference=(
                    managed_contracts
                    .AsrSectionReviewAttemptReferenceV1(
                        call_id=request.call_id,
                        section_id=request.section_id,
                        section_start_ordinal=(
                            request.section_start_ordinal
                        ),
                        core_segment_ids=request.core_segment_ids,
                        attempt=request.attempt,
                        step_id=step.step_id,
                        input_fingerprint=step.input_fingerprint,
                        status="failed",
                        error_code=(
                            step.error_code
                            or "VIDEO_LOCALIZATION_LLM_REQUEST_FAILED"
                        ),
                    )
                )
            )
        )

    def _record(self, attempt: KnownSectionReviewAttempt) -> None:
        if self.on_known_attempt is not None:
            self.on_known_attempt(attempt)


def _step_id(
    request: section_review.AsrSectionReviewCompletionRequest,
) -> str:
    section_hash = hashlib.sha256(
        request.section_id.encode("utf-8")
    ).hexdigest()[:12]
    return (
        f"section_call_{request.section_start_ordinal:04d}_"
        f"{section_hash}_attempt_{request.attempt}"
    )


def _runtime_trace(
    call: VideoLocalizationLlmCallRecord,
) -> llm_runtime.LlmCompletionTrace:
    payload = call.model_dump(
        exclude={
            "call_id",
            "purpose",
            "round_index",
            "candidate_ids",
            "question_id",
        }
    )
    return llm_runtime.LlmCompletionTrace(**payload)


def _runtime_error_from_failed_step(
    exc: provider_lifecycle.ProviderStepExecutionFailed,
) -> llm_runtime.LlmRuntimeError:
    public_code = str(exc.step.error_code or "").strip()
    prefix = "VIDEO_LOCALIZATION_"
    normalized = (
        public_code[len(prefix) :].casefold()
        if public_code.startswith(prefix)
        else "llm_request_failed"
    )
    return llm_runtime.LlmRuntimeError(
        "语言模型未能完成该段复查，请检查模型配置后重试。",
        code=normalized,
        status_code=502,
    )


__all__ = [
    "KnownSectionReviewAttempt",
    "ManagedSectionReviewGateway",
]
