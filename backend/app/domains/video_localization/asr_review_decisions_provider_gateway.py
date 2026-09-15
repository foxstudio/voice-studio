"""Durable LLM gateway for managed ASR review decisions."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_review_decisions_managed_contracts as managed_contracts,
    managed_artifact_files,
    review_decisions,
)
from app.errors import AppException
from app.schemas.video_localization_asr_review_decisions_step import (
    ASR_REVIEW_DECISIONS_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
    AsrReviewDecisionsCallArtifactV1,
    parse_review_decisions_call_artifact,
    review_decisions_call_artifact_bytes,
    review_decisions_call_input,
    review_decisions_call_input_fingerprint,
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
KnownAttemptSink = Callable[["KnownReviewDecisionsAttempt"], None]
_ARTIFACT_KIND = "step-result"
_ARTIFACT_KEY = "primary"


@dataclass(frozen=True)
class KnownReviewDecisionsAttempt:
    reference: (
        managed_contracts.AsrReviewDecisionsAttemptReferenceV1
    )
    artifact: AsrReviewDecisionsCallArtifactV1 | None = None


@dataclass
class ManagedReviewDecisionsGateway:
    execution_fence: ExecutionFence
    resolved_profile: llm_runtime.ResolvedProfile
    expected_profile_configuration_fingerprint: str
    behavior_fingerprint: str
    expected_issue_ids: tuple[str, ...]
    file_backend: ManagedArtifactFileBackend = managed_artifact_files
    complete_json: CompleteJson = llm_runtime.complete_json
    on_known_attempt: KnownAttemptSink | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _primary_artifact_fingerprint: str | None = field(
        default=None,
        init=False,
        repr=False,
    )

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
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_PROFILE_CHANGED",
                "复查结论汇总使用的模型配置已经变化，请重新提交任务。",
            )
        if re.fullmatch(
            r"[0-9a-f]{64}",
            self.behavior_fingerprint,
        ) is None:
            raise ValueError(
                "review-decisions behavior fingerprint is invalid"
            )
        if not self.expected_issue_ids or len(
            set(self.expected_issue_ids)
        ) != len(self.expected_issue_ids):
            raise ValueError(
                "review-decisions issue identity is invalid"
            )

    def __call__(
        self,
        request: review_decisions.AsrReviewDecisionsCompletionRequest,
    ) -> dict:
        if not isinstance(
            request,
            review_decisions.AsrReviewDecisionsCompletionRequest,
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_CALL_INVALID",
                "复查结论汇总产生了未知模型调用。",
            )
        if (
            request.contract_version
            != "asr-review-decisions-call-input-v1"
            or request.behavior_version
            != review_decisions.PROMPT_VERSION
            or request.profile_id
            != self.resolved_profile.profile_id
            or not set(request.issue_ids)
            <= set(self.expected_issue_ids)
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_INPUT_CHANGED",
                "复查结论汇总调用与已锁定任务不一致。",
            )
        if (
            request.call_group == "primary"
            and request.issue_ids != self.expected_issue_ids
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_INPUT_CHANGED",
                "复查结论汇总的首轮问题范围已经变化。",
            )
        primary_fingerprint = (
            self._primary_artifact_fingerprint
            if request.call_group == "coverage"
            else None
        )
        if request.call_group == "coverage" and not primary_fingerprint:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_LINEAGE_MISSING",
                "补充判断缺少已确认的首轮调用记录。",
            )
        provider = provider_execution.classify_provider_profile(
            self.resolved_profile
        )
        call_input = review_decisions_call_input(
            behavior_version=request.behavior_version,
            behavior_fingerprint=self.behavior_fingerprint,
            call_group=request.call_group,
            attempt=request.attempt,
            round_index=request.round_index,
            issue_ids=request.issue_ids,
            primary_artifact_fingerprint=primary_fingerprint,
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
            review_decisions_call_input_fingerprint(call_input)
        )
        step_id = _step_id(request)
        idempotency_key = provider_execution.provider_idempotency_key(
            self.execution_fence,
            key_prefix="vsl_asr_dec_",
            step_id=step_id,
            input_fingerprint=input_fingerprint,
        )
        plan = provider_lifecycle.ProviderStepPlan(
            step_id=step_id,
            workflow_version=(
                ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
            ),
            input_fingerprint=input_fingerprint,
            cost_class=provider.cost_class,
            provider_name=provider.provider_name,
            provider_idempotency_key=idempotency_key,
            artifact_kind=_ARTIFACT_KIND,
            artifact_key=_ARTIFACT_KEY,
            payload_schema_version=(
                ASR_REVIEW_DECISIONS_CALL_ARTIFACT_SCHEMA_VERSION
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
                    primary_artifact_fingerprint=primary_fingerprint,
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
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_RESULT_UNKNOWN",
                "复查结论汇总的模型请求可能已经执行，但结果暂时无法确认；系统没有自动重复请求。",
            ) from exc
        except provider_lifecycle.ProviderStepExecutionFailed as exc:
            self._record_failed(
                request,
                step=exc.step,
                primary_artifact_fingerprint=primary_fingerprint,
            )
            raise _runtime_error_from_failed_step(exc) from exc
        except provider_lifecycle.ProviderStepIntegrityError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_CALL_ARTIFACT_INVALID",
                "复查结论汇总的模型调用记录缺失或损坏，请先运行任务详情审计。",
            ) from exc
        try:
            artifact = parse_review_decisions_call_artifact(
                executed.content
            )
        except (TypeError, ValueError) as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_CALL_ARTIFACT_INVALID",
                "复查结论汇总的模型调用记录无法通过完整性校验。",
            ) from exc
        if (
            artifact.call_input_fingerprint != input_fingerprint
            or artifact.call_group != request.call_group
            or artifact.attempt != request.attempt
            or artifact.round_index != request.round_index
            or artifact.issue_ids != request.issue_ids
            or artifact.primary_artifact_fingerprint
            != primary_fingerprint
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_CALL_ARTIFACT_INVALID",
                "复查结论汇总的模型调用记录与已锁定输入不一致。",
            )
        if executed.outcome == "reused" and request.trace_sink is not None:
            request.trace_sink(_runtime_trace(artifact.llm_call))
        reference = (
            managed_contracts.AsrReviewDecisionsAttemptReferenceV1(
                call_group=request.call_group,
                attempt=request.attempt,
                round_index=request.round_index,
                issue_ids=request.issue_ids,
                primary_artifact_fingerprint=primary_fingerprint,
                step_id=executed.step.step_id,
                input_fingerprint=executed.step.input_fingerprint,
                status="success",
                artifact_fingerprint=(
                    executed.artifact.content_fingerprint
                ),
            )
        )
        if request.call_group == "primary":
            self._primary_artifact_fingerprint = (
                reference.artifact_fingerprint
            )
        self._record(
            KnownReviewDecisionsAttempt(
                reference=reference,
                artifact=artifact,
            )
        )
        return dict(artifact.response)

    def _submit(
        self,
        request: review_decisions.AsrReviewDecisionsCompletionRequest,
        *,
        primary_artifact_fingerprint: str | None,
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
            call_id=(
                f"review-decisions-r{request.round_index}-"
                f"{request.call_group}-a{request.attempt:02d}"
            ),
            purpose="review_decisions",
            round_index=request.round_index,
            candidate_ids=list(request.issue_ids),
        )
        artifact = AsrReviewDecisionsCallArtifactV1(
            call_input_fingerprint=input_fingerprint,
            call_group=request.call_group,
            attempt=request.attempt,
            round_index=request.round_index,
            issue_ids=request.issue_ids,
            primary_artifact_fingerprint=(
                primary_artifact_fingerprint
            ),
            response=raw,
            llm_call=call,
        )
        return provider_lifecycle.ProviderResponse(
            content=review_decisions_call_artifact_bytes(artifact),
            provider_request_id=call.response_id,
        )

    def _record_failed(
        self,
        request: review_decisions.AsrReviewDecisionsCompletionRequest,
        *,
        step,
        primary_artifact_fingerprint: str | None,
    ) -> None:
        self._record(
            KnownReviewDecisionsAttempt(
                reference=(
                    managed_contracts
                    .AsrReviewDecisionsAttemptReferenceV1(
                        call_group=request.call_group,
                        attempt=request.attempt,
                        round_index=request.round_index,
                        issue_ids=request.issue_ids,
                        primary_artifact_fingerprint=(
                            primary_artifact_fingerprint
                        ),
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

    def _record(self, attempt: KnownReviewDecisionsAttempt) -> None:
        if self.on_known_attempt is not None:
            self.on_known_attempt(attempt)


def _step_id(
    request: review_decisions.AsrReviewDecisionsCompletionRequest,
) -> str:
    return (
        f"decision_call_{request.call_group}_"
        f"attempt_{request.attempt}"
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
    message = (
        "语言模型服务余额不足，请充值或改用其他模型配置。"
        if public_code.endswith("LLM_INSUFFICIENT_CREDITS")
        else "语言模型未能完成复查结论汇总，请检查模型配置后重试。"
    )
    return llm_runtime.LlmRuntimeError(
        message,
        code=normalized,
        status_code=502,
    )


__all__ = [
    "KnownReviewDecisionsAttempt",
    "ManagedReviewDecisionsGateway",
]
