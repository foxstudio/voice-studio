"""Durable Provider execution for semantic TTS grouping."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone

from app.domains.video_localization import (
    managed_artifact_files,
    semantic_tts_grouping,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
)
from app.errors import AppException
from app.schemas.video_localization_llm_observability import (
    VideoLocalizationLlmCallRecord,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_semantic_tts_grouping_step import (
    SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION,
    SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
    SemanticTtsGroupingInputV2,
    SemanticTtsGroupingItemV1,
    SemanticTtsGroupingRoundArtifactV1,
    SemanticTtsGroupingRoundInputV1,
    parse_semantic_tts_grouping_round_artifact,
    semantic_tts_grouping_round_artifact_bytes,
    semantic_tts_grouping_round_input_fingerprint,
)
from app.services import llm_runtime
from app.services import (
    video_localization_llm_provider_execution as llm_provider_execution,
)
from app.services import (
    video_localization_provider_step_lifecycle as provider_lifecycle,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
)


_ARTIFACT_KIND = "step-result"
_ARTIFACT_KEY = "primary"
CompleteJson = Callable[..., dict | list]


SemanticGroupingProviderProfile = (
    llm_provider_execution.VideoLocalizationLlmProviderProfile
)
classify_provider_profile = llm_provider_execution.classify_provider_profile
provider_configuration_fingerprint = (
    llm_provider_execution.provider_configuration_fingerprint
)


def local_step_fingerprint(value: object) -> str:
    """Canonical local-step fingerprint without retaining the payload."""

    return _canonical_fingerprint(value)


def execute_semantic_tts_grouping(
    execution_fence: ExecutionFence,
    draft: VideoLocalizationDraft,
    *,
    profile_id: str | None = None,
    resolved_profile: llm_runtime.ResolvedProfile | None = None,
    expected_profile_configuration_fingerprint: str | None = None,
    target_chars: int = semantic_tts_grouping.DEFAULT_TARGET_CHARS,
    max_chars: int = semantic_tts_grouping.DEFAULT_MAX_CHARS,
    on_progress: Callable[[float, str], None] | None = None,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    complete_json: CompleteJson | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Execute up to two durable grouping rounds and return final groups."""

    actual_clock = clock or _utc_now
    preflight_fingerprint = _canonical_fingerprint(
        {
            "step_id": "prepare",
            "profile_id": str(profile_id or "").strip() or None,
            "expected_profile_configuration_fingerprint": (
                str(
                    expected_profile_configuration_fingerprint
                    or ""
                ).strip()
                or None
            ),
            "target_chars": target_chars,
            "max_chars": max_chars,
            "localized_subtitle_ids": [
                subtitle.subtitle_id
                for subtitle in draft.localized_subtitles
            ],
        }
    )
    try:
        items = semantic_tts_grouping.build_items(draft)
        if not items:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLES_MISSING",
                "请先生成本土化字幕。",
            )
        normalized_target, normalized_max = (
            semantic_tts_grouping.normalize_limits(
                target_chars,
                max_chars,
            )
        )
        resolved = resolved_profile or llm_runtime.resolve_profile(
            profile_id
        )
        if (
            profile_id
            and resolved.profile_id != str(profile_id).strip()
        ):
            raise ValueError(
                "resolved LLM profile differs from requested profile"
            )
        provider = classify_provider_profile(resolved)
        expected_profile_fingerprint = str(
            expected_profile_configuration_fingerprint or ""
        ).strip()
        if (
            expected_profile_fingerprint
            and provider.configuration_fingerprint
            != expected_profile_fingerprint
        ):
            raise AppException(
                409,
                (
                    "VIDEO_LOCALIZATION_"
                    "SEMANTIC_GROUPING_PROFILE_CHANGED"
                ),
                (
                    "语义分组使用的模型配置在任务提交后发生了变化，"
                    "请重新提交任务。"
                ),
            )
        workflow_input = SemanticTtsGroupingInputV2(
            profile_id=resolved.profile_id,
            model_id=resolved.model_id,
            provider_protocol=resolved.protocol,
            provider_endpoint_fingerprint=(
                provider.endpoint_fingerprint
            ),
            target_chars=normalized_target,
            max_chars=normalized_max,
            subtitles=[
                SemanticTtsGroupingItemV1.model_validate(item)
                for item in items
            ],
        )
    except AppException as exc:
        fail_local_step(
            execution_fence,
            step_id="prepare",
            input_fingerprint=_canonical_fingerprint(
                {
                    "preflight": preflight_fingerprint,
                    "error_code": exc.code,
                }
            ),
            error_code=exc.code,
            clock=actual_clock,
        )
        raise
    except llm_runtime.LlmRuntimeError as exc:
        fail_local_step(
            execution_fence,
            step_id="prepare",
            input_fingerprint=_canonical_fingerprint(
                {
                    "preflight": preflight_fingerprint,
                    "error_code": exc.code,
                }
            ),
            error_code=exc.code,
            clock=actual_clock,
        )
        raise
    except Exception:
        fail_local_step(
            execution_fence,
            step_id="prepare",
            input_fingerprint=_canonical_fingerprint(
                {
                    "preflight": preflight_fingerprint,
                    "error_code": (
                        "VIDEO_LOCALIZATION_"
                        "SEMANTIC_GROUPING_PREPARE_FAILED"
                    ),
                }
            ),
            error_code=(
                "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PREPARE_FAILED"
            ),
            clock=actual_clock,
        )
        raise
    actual_complete_json = complete_json or llm_runtime.complete_json
    prepare_fingerprint = _canonical_fingerprint(
        {
            "step_id": "prepare",
            "workflow_input": workflow_input.model_dump(mode="json"),
        }
    )
    prepare_local_step(
        execution_fence,
        step_id="prepare",
        input_fingerprint=prepare_fingerprint,
        output_fingerprint=prepare_fingerprint,
        clock=actual_clock,
    )
    if on_progress:
        on_progress(0.20, "整理字幕和说话人")
    group_step = _prepare_local_step_attempt(
        execution_fence,
        step_id="group",
        input_fingerprint=_canonical_fingerprint(
            {
                "step_id": "group",
                "workflow_input": workflow_input.model_dump(
                    mode="json"
                ),
            }
        ),
        clock=actual_clock,
    )
    last_error: str | None = None
    calls: list[VideoLocalizationLlmCallRecord] = []
    groups: list[list[str]] | None = None
    last_output_fingerprint: str | None = None
    for round_index in (1, 2):
        if on_progress:
            on_progress(
                0.35 if round_index == 1 else 0.60,
                (
                    "判断语义和场景"
                    if round_index == 1
                    else "重新核对分组"
                ),
            )
        round_input = SemanticTtsGroupingRoundInputV1(
            workflow_input=workflow_input,
            round_index=round_index,
            previous_validation_error=last_error,
        )
        input_fingerprint = (
            semantic_tts_grouping_round_input_fingerprint(
                round_input
            )
        )
        step_id = f"semantic_grouping_round_{round_index}"
        idempotency_key = llm_provider_execution.provider_idempotency_key(
            execution_fence,
            key_prefix="vsl_semgrp_",
            step_id=step_id,
            input_fingerprint=input_fingerprint,
        )
        plan = provider_lifecycle.ProviderStepPlan(
            step_id=step_id,
            workflow_version=(
                SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION
            ),
            input_fingerprint=input_fingerprint,
            cost_class=provider.cost_class,
            provider_name=provider.provider_name,
            provider_idempotency_key=idempotency_key,
            artifact_kind=_ARTIFACT_KIND,
            artifact_key=_ARTIFACT_KEY,
            payload_schema_version=(
                SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION
            ),
            media_type="application/json",
        )
        try:
            executed = provider_lifecycle.run_provider_step(
                execution_fence,
                file_backend=file_backend,
                plan=plan,
                submit=lambda key, current=round_input: (
                    _submit_round(
                        current,
                        resolved=resolved,
                        idempotency_key=key,
                        complete_json=actual_complete_json,
                    )
                ),
                clock=actual_clock,
            )
        except (
            provider_lifecycle.ProviderReplayBlocked,
            provider_lifecycle.ProviderStepResultUnknown,
        ) as exc:
            _finish_prepared_local_step(
                execution_fence,
                group_step,
                status="failed",
                error_code=(
                    "VIDEO_LOCALIZATION_"
                    "SEMANTIC_GROUPING_RESULT_UNKNOWN"
                ),
                clock=actual_clock,
            )
            raise _result_unknown_error() from exc
        except provider_lifecycle.ProviderStepExecutionFailed as exc:
            _finish_prepared_local_step(
                execution_fence,
                group_step,
                status="failed",
                error_code=(
                    "VIDEO_LOCALIZATION_"
                    "SEMANTIC_GROUPING_PROVIDER_FAILED"
                ),
                clock=actual_clock,
            )
            raise AppException(
                502,
                "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROVIDER_FAILED",
                "语言模型未能完成语义分组，请检查模型配置后重试。",
                {
                    "provider_error_code": (
                        exc.step.error_code
                        or "VIDEO_LOCALIZATION_LLM_REQUEST_FAILED"
                    )
                },
            ) from exc
        except provider_lifecycle.ProviderStepIntegrityError as exc:
            _finish_prepared_local_step(
                execution_fence,
                group_step,
                status="failed",
                error_code=(
                    "VIDEO_LOCALIZATION_"
                    "SEMANTIC_GROUPING_ARTIFACT_INVALID"
                ),
                clock=actual_clock,
            )
            raise AppException(
                500,
                "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_ARTIFACT_INVALID",
                "已保存的语义分组结果无法通过完整性校验。",
            ) from exc

        try:
            artifact = parse_semantic_tts_grouping_round_artifact(
                executed.content
            )
        except (TypeError, ValueError) as exc:
            _finish_prepared_local_step(
                execution_fence,
                group_step,
                status="failed",
                error_code=(
                    "VIDEO_LOCALIZATION_"
                    "SEMANTIC_GROUPING_ARTIFACT_INVALID"
                ),
                clock=actual_clock,
            )
            raise AppException(
                500,
                "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_ARTIFACT_INVALID",
                "已保存的语义分组结果契约无效。",
            ) from exc
        if artifact.round_index != round_index:
            _finish_prepared_local_step(
                execution_fence,
                group_step,
                status="failed",
                error_code=(
                    "VIDEO_LOCALIZATION_"
                    "SEMANTIC_GROUPING_ARTIFACT_INVALID"
                ),
                clock=actual_clock,
            )
            raise AppException(
                500,
                "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_ARTIFACT_INVALID",
                "已保存的语义分组轮次与当前步骤不一致。",
            )
        calls.append(artifact.llm_call)
        last_output_fingerprint = executed.step.output_fingerprint
        try:
            groups = semantic_tts_grouping.validate_groups(
                artifact.groups,
                items,
                normalized_max,
            )
            break
        except ValueError as exc:
            last_error = str(exc)
    if last_output_fingerprint is None:
        _finish_prepared_local_step(
            execution_fence,
            group_step,
            status="failed",
            error_code=(
                "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_ARTIFACT_INVALID"
            ),
            clock=actual_clock,
        )
        raise AppException(
            500,
            "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_ARTIFACT_INVALID",
            "语义分组没有形成可验证的持久结果。",
        )
    _finish_prepared_local_step(
        execution_fence,
        group_step,
        status="success",
        output_fingerprint=last_output_fingerprint,
        clock=actual_clock,
    )
    if groups is None:
        validate_input_fingerprint = _canonical_fingerprint(
            {
                "step_id": "validate",
                "round_output_fingerprint": (
                    last_output_fingerprint
                ),
            }
        )
        fail_local_step(
            execution_fence,
            step_id="validate",
            input_fingerprint=validate_input_fingerprint,
            error_code=(
                "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_INVALID"
            ),
            clock=actual_clock,
        )
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_INVALID",
            f"语义分组结果无法使用：{last_error or '结果无效'}",
        )
    if on_progress:
        on_progress(0.85, "检查分组完整性")
    result = semantic_tts_grouping.build_result(
        items,
        groups,
        target_chars=normalized_target,
        max_chars=normalized_max,
        llm_calls=calls,
    )
    validate_input_fingerprint = _canonical_fingerprint(
        {
            "step_id": "validate",
            "round_output_fingerprint": (
                last_output_fingerprint
            ),
        }
    )
    prepare_local_step(
        execution_fence,
        step_id="validate",
        input_fingerprint=validate_input_fingerprint,
        output_fingerprint=_canonical_fingerprint(
            {
                "source_fingerprint": result[
                    "source_fingerprint"
                ],
                "groups": result["groups"],
            }
        ),
        clock=actual_clock,
    )
    return result


def _prepare_local_step_attempt(
    execution_fence: ExecutionFence,
    *,
    step_id: str,
    input_fingerprint: str,
    clock: Callable[[], datetime],
) -> step_store.OperationStepAttempt:
    return step_store.prepare_step(
        execution_fence,
        step_id=step_id,
        workflow_version=SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
        input_fingerprint=input_fingerprint,
        cost_class="local_free",
        observed_at=clock(),
    ).step


def _finish_prepared_local_step(
    execution_fence: ExecutionFence,
    step: step_store.OperationStepAttempt,
    *,
    status: step_store.TerminalStepStatus,
    clock: Callable[[], datetime],
    output_fingerprint: str | None = None,
    error_code: str | None = None,
) -> step_store.OperationStepAttempt:
    if step.status == "success" and status == "failed":
        # A recovered attempt may reuse a previously completed aggregate
        # group step before discovering later storage corruption. Preserve
        # the immutable success row and let the original integrity error
        # surface instead of replacing it with a transition conflict.
        return step
    if step.status == status:
        if (
            step.output_fingerprint != output_fingerprint
            or step.error_code != error_code
        ):
            raise step_store.StepIdentityConflict(
                "reused local step result differs"
            )
        return step
    if step.status != "prepared":
        raise step_store.StepTransitionConflict(
            "local step is already terminal"
        )
    return step_store.finish_step(
        step.step_attempt_id,
        execution_fence=execution_fence,
        status=status,
        observed_at=clock(),
        output_fingerprint=output_fingerprint,
        error_code=error_code,
    )


def prepare_local_step(
    execution_fence: ExecutionFence,
    *,
    step_id: str,
    input_fingerprint: str,
    output_fingerprint: str,
    clock: Callable[[], datetime] | None = None,
) -> step_store.OperationStepAttempt:
    """Idempotently record one successful local workflow step."""

    actual_clock = clock or _utc_now
    prepared = step_store.prepare_step(
        execution_fence,
        step_id=step_id,
        workflow_version=SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
        input_fingerprint=input_fingerprint,
        cost_class="local_free",
        observed_at=actual_clock(),
    ).step
    if prepared.status == "success":
        if prepared.output_fingerprint != output_fingerprint:
            raise step_store.StepIdentityConflict(
                "reused local step output differs"
            )
        return prepared
    return step_store.finish_step(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        status="success",
        observed_at=actual_clock(),
        output_fingerprint=output_fingerprint,
    )


def fail_local_step(
    execution_fence: ExecutionFence,
    *,
    step_id: str,
    input_fingerprint: str,
    error_code: str,
    clock: Callable[[], datetime] | None = None,
) -> step_store.OperationStepAttempt:
    """Idempotently record a failed local workflow step."""

    actual_clock = clock or _utc_now
    prepared = step_store.prepare_step(
        execution_fence,
        step_id=step_id,
        workflow_version=SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
        input_fingerprint=input_fingerprint,
        cost_class="local_free",
        observed_at=actual_clock(),
    ).step
    if prepared.status == "failed":
        if prepared.error_code != error_code:
            raise step_store.StepIdentityConflict(
                "reused local step error differs"
            )
        return prepared
    return step_store.finish_step(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        status="failed",
        observed_at=actual_clock(),
        error_code=error_code,
    )


def run_local_step(
    execution_fence: ExecutionFence,
    *,
    step_id: str,
    input_fingerprint: str,
    output_fingerprint: str,
    action: Callable[[], object],
    fallback_error_code: str,
    clock: Callable[[], datetime] | None = None,
) -> object:
    """Run a local side effect before durably marking its step successful."""

    actual_clock = clock or _utc_now
    prepared = step_store.prepare_step(
        execution_fence,
        step_id=step_id,
        workflow_version=SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
        input_fingerprint=input_fingerprint,
        cost_class="local_free",
        observed_at=actual_clock(),
    ).step
    if prepared.status == "failed":
        raise AppException(
            409,
            prepared.error_code or fallback_error_code,
            "该本地步骤此前已失败，请重新提交任务。",
        )
    try:
        value = action()
    except AppException as exc:
        if prepared.status == "prepared":
            step_store.finish_step(
                prepared.step_attempt_id,
                execution_fence=execution_fence,
                status="failed",
                observed_at=actual_clock(),
                error_code=exc.code,
            )
        raise
    except Exception:
        if prepared.status == "prepared":
            step_store.finish_step(
                prepared.step_attempt_id,
                execution_fence=execution_fence,
                status="failed",
                observed_at=actual_clock(),
                error_code=fallback_error_code,
            )
        raise
    if prepared.status == "success":
        if prepared.output_fingerprint != output_fingerprint:
            raise step_store.StepIdentityConflict(
                "reused local step output differs"
            )
        return value
    step_store.finish_step(
        prepared.step_attempt_id,
        execution_fence=execution_fence,
        status="success",
        observed_at=actual_clock(),
        output_fingerprint=output_fingerprint,
    )
    return value


def _submit_round(
    round_input: SemanticTtsGroupingRoundInputV1,
    *,
    resolved: llm_runtime.ResolvedProfile,
    idempotency_key: str,
    complete_json: CompleteJson,
) -> provider_lifecycle.ProviderResponse:
    traces: list[llm_runtime.LlmCompletionTrace] = []
    payload = {
        "target_chars": round_input.workflow_input.target_chars,
        "max_chars": round_input.workflow_input.max_chars,
        "subtitles": [
            item.model_dump(mode="json")
            for item in round_input.workflow_input.subtitles
        ],
    }
    if round_input.previous_validation_error:
        payload["previous_error"] = (
            round_input.previous_validation_error
        )
    try:
        raw = complete_json(
            system_prompt=semantic_tts_grouping.SYSTEM_PROMPT,
            user_payload=payload,
            profile_id=resolved.profile_id,
            resolved_profile=resolved,
            temperature=0.0,
            max_tokens=max(
                1200,
                min(
                    8000,
                    len(round_input.workflow_input.subtitles) * 32,
                ),
            ),
            timeout=180,
            disable_reasoning=True,
            trace_sink=traces.append,
            idempotency_key=idempotency_key,
        )
    except llm_runtime.LlmRuntimeError as exc:
        if llm_provider_execution.is_uncertain_llm_error_code(exc.code):
            raise provider_lifecycle.ProviderResultUncertain(
                "VIDEO_LOCALIZATION_LLM_RESULT_UNKNOWN",
                provider_request_id=(
                    llm_provider_execution.trace_response_id(traces)
                ),
            ) from None
        raise provider_lifecycle.ProviderRequestRejected(
            llm_provider_execution.known_provider_error_code(exc.code)
        ) from None
    if len(traces) != 1:
        raise provider_lifecycle.ProviderRequestRejected(
            "VIDEO_LOCALIZATION_LLM_TRACE_INVALID"
        )
    call = VideoLocalizationLlmCallRecord.from_runtime(
        traces[0],
        call_id=(
            "semantic-tts-grouping-"
            f"{round_input.round_index}"
        ),
        purpose="semantic_tts_grouping",
        round_index=round_input.round_index,
    )
    response_shape, groups = _normalized_provider_groups(raw)
    artifact = SemanticTtsGroupingRoundArtifactV1(
        round_index=round_input.round_index,
        response_shape=response_shape,
        groups=groups,
        llm_call=call,
    )
    return provider_lifecycle.ProviderResponse(
        content=semantic_tts_grouping_round_artifact_bytes(
            artifact
        ),
        provider_request_id=call.response_id,
    )


def _canonical_fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _normalized_provider_groups(
    raw: object,
) -> tuple[str, list[list[str]]]:
    if not isinstance(raw, dict) or "groups" not in raw:
        return "groups_missing", []
    raw_groups = raw.get("groups")
    if not isinstance(raw_groups, list):
        return "groups_not_array", []
    if any(not isinstance(group, list) for group in raw_groups):
        return "group_not_array", []
    if any(
        not isinstance(value, str)
        for group in raw_groups
        for value in group
    ):
        return "subtitle_id_not_string", []
    return (
        "groups_array",
        [
            [value.strip() for value in group]
            for group in raw_groups
        ],
    )


def _result_unknown_error() -> AppException:
    return AppException(
        409,
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_RESULT_UNKNOWN",
        "语言模型请求可能已经执行，但结果暂时无法确认；"
        "系统没有自动重复请求，请先检查模型服务记录。",
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "SemanticGroupingProviderProfile",
    "classify_provider_profile",
    "execute_semantic_tts_grouping",
    "provider_configuration_fingerprint",
]
