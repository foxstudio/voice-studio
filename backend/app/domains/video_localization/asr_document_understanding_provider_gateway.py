"""Managed Provider gateway for ASR document-understanding calls."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_flow,
    asr_document_understanding_managed_contracts as managed_contracts,
    managed_artifact_files,
)
from app.errors import AppException
from app.schemas.video_localization_asr_document_understanding_step import (
    ASR_DOCUMENT_UNDERSTANDING_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
    AsrDocumentUnderstandingCallArtifactV1,
    asr_document_understanding_call_artifact_bytes,
    asr_document_understanding_call_input,
    asr_document_understanding_call_input_fingerprint,
    parse_asr_document_understanding_call_artifact,
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
    ["CommittedDocumentUnderstandingCall"],
    None,
]
_ARTIFACT_KIND = "step-result"
_ARTIFACT_KEY = "primary"
_BEHAVIOR_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CommittedDocumentUnderstandingCall:
    reference: (
        managed_contracts.AsrDocumentUnderstandingCallReferenceV1
    )
    artifact: AsrDocumentUnderstandingCallArtifactV1


@dataclass(frozen=True)
class ManagedDocumentUnderstandingGateway:
    execution_fence: ExecutionFence
    resolved_profile: llm_runtime.ResolvedProfile
    expected_profile_configuration_fingerprint: str
    behavior_fingerprint: str
    file_backend: ManagedArtifactFileBackend = managed_artifact_files
    complete_json: CompleteJson = llm_runtime.complete_json
    on_committed_call: CommittedCallSink | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def __post_init__(self) -> None:
        profile = provider_execution.classify_provider_profile(self.resolved_profile)
        if profile.configuration_fingerprint != self.expected_profile_configuration_fingerprint:
            raise AppException(
                409,
                ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_PROFILE_CHANGED"),
                "全文理解使用的模型配置在任务提交后发生了变化，请重新提交任务。",
            )
        if _BEHAVIOR_FINGERPRINT.fullmatch(self.behavior_fingerprint) is None:
            raise ValueError("document understanding behavior fingerprint is invalid")

    def __call__(
        self,
        request: (asr_flow.AsrDocumentUnderstandingCompletionRequest),
    ) -> dict:
        if request.profile_id != self.resolved_profile.profile_id:
            raise AppException(
                409,
                ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_PROFILE_CHANGED"),
                "全文理解调用的模型身份与已锁定任务不一致，请重新提交任务。",
            )
        provider = provider_execution.classify_provider_profile(self.resolved_profile)
        call_input = asr_document_understanding_call_input(
            behavior_version=request.behavior_version,
            behavior_fingerprint=self.behavior_fingerprint,
            call_id=request.call_id,
            attempt=request.attempt,
            system_prompt=request.system_prompt,
            user_payload=request.user_payload,
            profile_id=self.resolved_profile.profile_id,
            model_id=self.resolved_profile.model_id,
            provider_protocol=self.resolved_profile.protocol,
            provider_endpoint_fingerprint=(provider.endpoint_fingerprint),
            profile_configuration_fingerprint=(provider.configuration_fingerprint),
            max_tokens=request.max_tokens,
            timeout=request.timeout,
            disable_reasoning=request.disable_reasoning,
        )
        input_fingerprint = asr_document_understanding_call_input_fingerprint(call_input)
        step_id = _step_id(request)
        idempotency_key = provider_execution.provider_idempotency_key(
            self.execution_fence,
            key_prefix="vsl_asr_doc_",
            step_id=step_id,
            input_fingerprint=input_fingerprint,
        )
        plan = provider_lifecycle.ProviderStepPlan(
            step_id=step_id,
            workflow_version=(ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION),
            input_fingerprint=input_fingerprint,
            cost_class=provider.cost_class,
            provider_name=provider.provider_name,
            provider_idempotency_key=idempotency_key,
            artifact_kind=_ARTIFACT_KIND,
            artifact_key=_ARTIFACT_KEY,
            payload_schema_version=(ASR_DOCUMENT_UNDERSTANDING_CALL_ARTIFACT_SCHEMA_VERSION),
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
                ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_RESULT_UNKNOWN"),
                ("全文理解请求可能已经执行，但结果暂时无法确认；系统没有自动重复请求，请先检查模型服务记录。"),
            ) from exc
        except provider_lifecycle.ProviderStepExecutionFailed as exc:
            raise _runtime_error_from_failed_step(exc) from exc
        except provider_lifecycle.ProviderStepIntegrityError as exc:
            raise AppException(
                409,
                ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_ARTIFACT_INVALID"),
                "全文理解调用记录缺失或损坏，请先运行任务详情审计。",
            ) from exc
        try:
            artifact = parse_asr_document_understanding_call_artifact(executed.content)
        except (TypeError, ValueError) as exc:
            raise AppException(
                409,
                ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_ARTIFACT_INVALID"),
                "全文理解调用记录无法通过完整性校验，请先运行任务详情审计。",
            ) from exc
        if (
            artifact.call_input_fingerprint != input_fingerprint
            or artifact.call_id != request.call_id
            or artifact.attempt != request.attempt
        ):
            raise AppException(
                409,
                ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_ARTIFACT_INVALID"),
                "全文理解调用记录与已锁定输入不一致，请先运行任务详情审计。",
            )
        if executed.outcome == "reused" and request.trace_sink is not None:
            request.trace_sink(_runtime_trace(artifact.llm_call))
        if self.on_committed_call is not None:
            self.on_committed_call(
                CommittedDocumentUnderstandingCall(
                    reference=(
                        managed_contracts
                        .AsrDocumentUnderstandingCallReferenceV1(
                            call_id=request.call_id,
                            attempt=request.attempt,
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
        request: (asr_flow.AsrDocumentUnderstandingCompletionRequest),
        *,
        idempotency_key: str,
        input_fingerprint: str,
    ) -> provider_lifecycle.ProviderResponse:
        traces: list[llm_runtime.LlmCompletionTrace] = []

        def record(
            trace: llm_runtime.LlmCompletionTrace,
        ) -> None:
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
                    provider_request_id=(provider_execution.trace_response_id(traces)),
                ) from None
            raise provider_lifecycle.ProviderRequestRejected(
                provider_execution.known_provider_error_code(exc.code)
            ) from None
        if not isinstance(raw, dict) or len(traces) != 1:
            raise provider_lifecycle.ProviderRequestRejected("VIDEO_LOCALIZATION_LLM_TRACE_INVALID")
        call = VideoLocalizationLlmCallRecord.from_runtime(
            traces[0],
            call_id=(f"{request.call_id}:attempt_{request.attempt}"),
            purpose="document_understanding",
            round_index=1,
        )
        artifact = AsrDocumentUnderstandingCallArtifactV1(
            call_input_fingerprint=input_fingerprint,
            call_id=request.call_id,
            attempt=request.attempt,
            response=raw,
            llm_call=call,
        )
        return provider_lifecycle.ProviderResponse(
            content=(asr_document_understanding_call_artifact_bytes(artifact)),
            provider_request_id=call.response_id,
        )


def _step_id(
    request: asr_flow.AsrDocumentUnderstandingCompletionRequest,
) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        request.call_id.casefold(),
    ).strip("_")
    return f"document_call_{normalized}_attempt_{request.attempt}"


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
        "语言模型未能完成全文理解，请检查模型配置后重试。",
        code=normalized,
        status_code=502,
    )


__all__ = [
    "CommittedDocumentUnderstandingCall",
    "ManagedDocumentUnderstandingGateway",
]
