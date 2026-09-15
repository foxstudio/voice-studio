"""Durable LLM gateway for managed entity normalization."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_entity_normalization_managed_contracts as managed_contracts,
    asr_flow,
    managed_artifact_files,
)
from app.errors import AppException
from app.schemas.video_localization_asr_entity_normalization_step import (
    ASR_ENTITY_NORMALIZATION_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
    AsrEntityNormalizationCallArtifactV1,
    EntityCallPurpose,
    entity_normalization_call_artifact_bytes,
    entity_normalization_call_input,
    entity_normalization_call_input_fingerprint,
    parse_entity_normalization_call_artifact,
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
CommittedCallSink = Callable[
    ["CommittedEntityNormalizationCall"],
    None,
]
_ARTIFACT_KIND = "step-result"
_ARTIFACT_KEY = "primary"


@dataclass(frozen=True)
class CommittedEntityNormalizationCall:
    reference: (
        managed_contracts.AsrEntityNormalizationCallReferenceV1
    )
    artifact: AsrEntityNormalizationCallArtifactV1


@dataclass(frozen=True)
class ManagedEntityNormalizationGateway:
    execution_fence: ExecutionFence
    resolved_profile: llm_runtime.ResolvedProfile
    expected_profile_configuration_fingerprint: str
    behavior_fingerprint: str
    candidate_ids: tuple[str, ...]
    file_backend: ManagedArtifactFileBackend = managed_artifact_files
    complete_json: CompleteJson = llm_runtime.complete_json
    on_committed_call: CommittedCallSink | None = None
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
                "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_PROFILE_CHANGED",
                "名称与术语统一使用的模型配置已经变化，请重新提交任务。",
            )
        if re.fullmatch(
            r"[0-9a-f]{64}",
            self.behavior_fingerprint,
        ) is None:
            raise ValueError(
                "entity-normalization behavior fingerprint is invalid"
            )
        if not self.candidate_ids or len(set(self.candidate_ids)) != len(
            self.candidate_ids
        ):
            raise ValueError(
                "entity-normalization candidate identity is invalid"
            )

    def __call__(
        self,
        request: asr_flow.AsrDocumentUnderstandingCompletionRequest,
    ) -> dict:
        purpose = _purpose(request.call_id)
        if (
            request.contract_version
            != "asr-entity-normalization-call-input-v1"
            or request.behavior_version
            != "asr-entity-normalization-v1"
            or request.profile_id
            != self.resolved_profile.profile_id
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_INPUT_CHANGED",
                "名称与术语统一调用与已锁定任务不一致。",
            )
        provider = provider_execution.classify_provider_profile(
            self.resolved_profile
        )
        call_input = entity_normalization_call_input(
            behavior_version=request.behavior_version,
            behavior_fingerprint=self.behavior_fingerprint,
            call_id=request.call_id,
            purpose=purpose,
            attempt=request.attempt,
            candidate_ids=self.candidate_ids,
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
            entity_normalization_call_input_fingerprint(call_input)
        )
        step_id = _step_id(request)
        idempotency_key = provider_execution.provider_idempotency_key(
            self.execution_fence,
            key_prefix="vsl_asr_ent_",
            step_id=step_id,
            input_fingerprint=input_fingerprint,
        )
        plan = provider_lifecycle.ProviderStepPlan(
            step_id=step_id,
            workflow_version=(
                ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
            ),
            input_fingerprint=input_fingerprint,
            cost_class=provider.cost_class,
            provider_name=provider.provider_name,
            provider_idempotency_key=idempotency_key,
            artifact_kind=_ARTIFACT_KIND,
            artifact_key=_ARTIFACT_KEY,
            payload_schema_version=(
                ASR_ENTITY_NORMALIZATION_CALL_ARTIFACT_SCHEMA_VERSION
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
                    purpose=purpose,
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
                "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_RESULT_UNKNOWN",
                "名称与术语统一的模型请求可能已经执行，但结果暂时无法确认；系统没有自动重复请求。",
            ) from exc
        except provider_lifecycle.ProviderStepExecutionFailed as exc:
            raise _runtime_error_from_failed_step(exc) from exc
        except provider_lifecycle.ProviderStepIntegrityError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_CALL_ARTIFACT_INVALID",
                "名称与术语统一的模型调用记录缺失或损坏，请先运行任务详情审计。",
            ) from exc
        try:
            artifact = parse_entity_normalization_call_artifact(
                executed.content
            )
        except (TypeError, ValueError) as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_CALL_ARTIFACT_INVALID",
                "名称与术语统一的模型调用记录无法通过完整性校验。",
            ) from exc
        if (
            artifact.call_input_fingerprint != input_fingerprint
            or artifact.call_id != request.call_id
            or artifact.purpose != purpose
            or artifact.attempt != request.attempt
            or artifact.candidate_ids != self.candidate_ids
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_CALL_ARTIFACT_INVALID",
                "名称与术语统一的模型调用记录与已锁定输入不一致。",
            )
        if executed.outcome == "reused" and request.trace_sink is not None:
            request.trace_sink(_runtime_trace(artifact.llm_call))
        if self.on_committed_call is not None:
            self.on_committed_call(
                CommittedEntityNormalizationCall(
                    reference=(
                        managed_contracts
                        .AsrEntityNormalizationCallReferenceV1(
                            call_id=request.call_id,
                            purpose=purpose,
                            attempt=request.attempt,
                            candidate_ids=self.candidate_ids,
                            step_id=executed.step.step_id,
                            input_fingerprint=(
                                executed.step.input_fingerprint
                            ),
                            artifact_fingerprint=(
                                executed.artifact
                                .content_fingerprint
                            ),
                        )
                    ),
                    artifact=artifact,
                )
            )
        return dict(artifact.response)

    def _submit(
        self,
        request: asr_flow.AsrDocumentUnderstandingCompletionRequest,
        *,
        purpose: EntityCallPurpose,
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
            call_id=f"{request.call_id}:attempt_{request.attempt}",
            purpose=purpose,
            round_index=request.attempt,
            candidate_ids=list(self.candidate_ids),
        )
        artifact = AsrEntityNormalizationCallArtifactV1(
            call_input_fingerprint=input_fingerprint,
            call_id=request.call_id,
            purpose=purpose,
            attempt=request.attempt,
            candidate_ids=self.candidate_ids,
            response=raw,
            llm_call=call,
        )
        return provider_lifecycle.ProviderResponse(
            content=entity_normalization_call_artifact_bytes(
                artifact
            ),
            provider_request_id=call.response_id,
        )


def _purpose(call_id: str) -> EntityCallPurpose:
    if call_id == "entity_resolution":
        return "entity_resolution"
    if call_id == "entity_variant_mapping":
        return "entity_variant_mapping"
    raise AppException(
        409,
        "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_CALL_INVALID",
        "名称与术语统一产生了未知模型调用。",
    )


def _step_id(
    request: asr_flow.AsrDocumentUnderstandingCompletionRequest,
) -> str:
    return (
        f"entity_call_{request.call_id}_attempt_{request.attempt}"
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
        "语言模型未能完成名称与术语统一，请检查模型配置后重试。",
        code=normalized,
        status_code=502,
    )


__all__ = [
    "CommittedEntityNormalizationCall",
    "ManagedEntityNormalizationGateway",
]
