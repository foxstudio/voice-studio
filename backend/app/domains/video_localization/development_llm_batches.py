"""Opt-in development replay of returned JSON candidates, never a formal cache.

The provider's HTTP response and hidden reasoning are not available here. Input
identity describes the actual call, deliberately not the local validator version.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from app.domains.video_localization.development_checkpoints import (
    LocalizationDevelopmentCheckpointWriter,
    development_checkpoint_exists,
    load_development_checkpoint,
    load_development_checkpoints_by_prefix,
)
from app.domains.video_localization.development_candidate_recovery import DevelopmentCandidateReceiptStore
from app.domains.video_localization.llm_candidate_provenance import RecoveredLlmCandidateProvenance
from app.domains.video_localization.llm_observability import (
    VideoLocalizationLlmCallRecord,
    VideoLocalizationLlmTraceCollector,
)
from app.services import llm_runtime
from app.services.video_localization_llm_provider_execution import provider_configuration_fingerprint


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class DevelopmentLlmImageIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: int = Field(ge=0)


class DevelopmentLlmCallInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    user_payload: dict[str, Any]
    profile_id: str | None
    model_id: str
    profile_configuration_fingerprint: str = Field(min_length=64, max_length=64)
    temperature: float
    max_tokens: int
    timeout: float
    reasoning_effort: str | None
    # None is the original text protocol; [] is still a multimodal request.
    image_inputs: list[DevelopmentLlmImageIdentity] | None = None

    @model_serializer(mode="wrap")
    def _preserve_text_input_fingerprint(self, serialize):
        result = serialize(self)
        if self.image_inputs is None:
            result.pop("image_inputs", None)
        return result


class DevelopmentLlmValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validator_version: str
    status: Literal["passed", "failed"]
    error_type: str | None = None
    error_message: str | None = None


class DevelopmentLlmBatchCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-development-llm-batch-v1"] = "localization-development-llm-batch-v1"
    batch_id: str
    attempt: int = Field(ge=0)
    input_fingerprint: str = Field(min_length=64, max_length=64)
    call_input: DevelopmentLlmCallInput
    status: Literal["prepared", "response_received", "candidate_imported", "call_failed", "validation_passed", "validation_failed"]
    raw_json_candidate: dict[str, Any] | None = None
    candidate_fingerprint: str | None = None
    llm_calls: list[VideoLocalizationLlmCallRecord] = Field(default_factory=list)
    call_error_code: str | None = None
    call_error_message: str | None = None
    call_error_status_code: int | None = None
    validations: list[DevelopmentLlmValidation] = Field(default_factory=list)
    recovery_ordinal: int | None = Field(default=None, ge=0, le=2_147_483_647)
    recovery_execution_id: str | None = Field(default=None, min_length=1, max_length=200)
    recovery_parent_step_id: str | None = Field(default=None, min_length=1)
    recovered_candidate: RecoveredLlmCandidateProvenance | None = None
    unknown_retry_authorized_step_id: str | None = None

    @model_serializer(mode="wrap")
    def _preserve_legacy_fingerprint(self, serialize):
        # Legacy v1 envelope hashes include every original default field.
        # New absent metadata must not invalidate those already-paid responses.
        result = serialize(self)
        for field in ("recovery_ordinal", "recovery_execution_id", "recovery_parent_step_id", "recovered_candidate",
                      "unknown_retry_authorized_step_id"):
            if getattr(self, field) is None:
                result.pop(field, None)
        return result


class DevelopmentLlmBatchReplay:
    """One explicit development session; callers retain domain retry policy."""

    def __init__(
        self,
        *,
        root: Path | None,
        project_id: str,
        development_session_id: str,
        step_id_prefix: str,
        checkpoint_writer: Callable[[str, BaseModel], None] | None = None,
        retry_rejected_execution_id: str | None = None,
        candidate_recovery_store: DevelopmentCandidateReceiptStore | None = None,
        retry_unknown_batch_step_id: str | None = None,
    ) -> None:
        self.root = root
        self.project_id = project_id
        self.session_id = development_session_id
        self.prefix = step_id_prefix
        if candidate_recovery_store is not None and (
            root is None or candidate_recovery_store.project_id != project_id
            or candidate_recovery_store.session_id != development_session_id
        ):
            raise ValueError("候选恢复只允许同项目、同会话的显式开发 journal；正式流程禁止读取。")
        self.candidate_recovery_store = candidate_recovery_store
        if retry_rejected_execution_id is not None and (
            not retry_rejected_execution_id.strip() or len(retry_rejected_execution_id) > 200
        ):
            raise ValueError("限额拒绝恢复需要非空且有界的显式开发执行 ID。")
        # A write-only formal run must never inspect or recover development state.
        self.retry_rejected_execution_id = retry_rejected_execution_id if root is not None else None
        self.retry_unknown_batch_step_id = retry_unknown_batch_step_id if root is not None else None
        if self.retry_unknown_batch_step_id is not None and (
            not isinstance(self.retry_unknown_batch_step_id, str)
            or not re.fullmatch(re.escape(step_id_prefix) + r"\.[0-9a-f]{24}\.a\d+\.[0-9a-f]{64}(?:\.r[1-9]\d*)?",
                                self.retry_unknown_batch_step_id)
            or self.retry_rejected_execution_id is None
        ):
            raise ValueError("未知结果重试需要当前 journal 范围内的精确失败步骤和新的显式开发执行 ID。")
        if root is None and checkpoint_writer is None:
            raise ValueError("诊断 journal 需要显式 writer；开发重放需要独占快照根目录。")
        self.writer = checkpoint_writer or LocalizationDevelopmentCheckpointWriter(
            root,
            project_id=project_id,
            workflow_operation_id=development_session_id,
        )

    def attempt(
        self, *, batch_id: str, attempt: int, model_id: str, call_id: str, purpose: str, round_index: int
    ) -> DevelopmentLlmBatchAttempt:
        return DevelopmentLlmBatchAttempt(
            self,
            batch_id=batch_id,
            attempt=attempt,
            model_id=model_id,
            call_id=call_id,
            purpose=purpose,
            round_index=round_index,
        )


class DevelopmentLlmBatchAttempt:
    def __init__(
        self,
        journal: DevelopmentLlmBatchReplay,
        *,
        batch_id: str,
        attempt: int,
        model_id: str,
        call_id: str,
        purpose: str,
        round_index: int,
    ) -> None:
        self.journal = journal
        self.batch_id, self.attempt_index = batch_id, attempt
        self.model_id, self.call_id = model_id, call_id
        self.purpose, self.round_index = purpose, round_index
        self.checkpoint: DevelopmentLlmBatchCheckpoint | None = None
        self.step_id: str | None = None
        self.reused_calls: list[VideoLocalizationLlmCallRecord] = []
        self.recovered_candidate: RecoveredLlmCandidateProvenance | None = None

    def _save(self) -> None:
        assert self.step_id is not None and self.checkpoint is not None
        self.journal.writer(self.step_id, self.checkpoint)

    def complete_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        *,
        profile_id: str | None,
        temperature: float,
        max_tokens: int,
        timeout: float,
        reasoning_effort: str | None,
        trace_sink=None,
    ) -> dict[str, Any]:
        return self._complete(
            system_prompt, user_payload, images=None,
            profile_id=profile_id, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
            reasoning_effort=reasoning_effort, trace_sink=trace_sink,
        )

    def complete_multimodal_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        images: Sequence[llm_runtime.LlmImageInput],
        *,
        profile_id: str | None,
        temperature: float = 0.0,
        max_tokens: int,
        timeout: float,
        reasoning_effort: str | None,
        trace_sink=None,
    ) -> dict[str, Any]:
        # Freeze the exact bytes/order that both identity and provider consume.
        frozen_images = tuple(
            llm_runtime.LlmImageInput(data=bytes(image.data), media_type=image.media_type)
            for image in images
        )
        return self._complete(
            system_prompt, user_payload, images=frozen_images,
            profile_id=profile_id, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
            reasoning_effort=reasoning_effort, trace_sink=trace_sink,
        )

    def _complete(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        *,
        images: tuple[llm_runtime.LlmImageInput, ...] | None,
        profile_id: str | None,
        temperature: float,
        max_tokens: int,
        timeout: float,
        reasoning_effort: str | None,
        trace_sink=None,
    ) -> dict[str, Any]:
        resolved_profile = llm_runtime.resolve_profile(profile_id)
        if resolved_profile.model_id != self.model_id:
            raise llm_runtime.LlmRuntimeError(
                "终审模型配置已变化，与当前冻结路由不一致，请重新准备该步骤。",
                code="development_llm_profile_changed",
                status_code=409,
            )
        call_input = DevelopmentLlmCallInput(
            system_prompt=system_prompt,
            user_payload=user_payload,
            profile_id=resolved_profile.profile_id,
            model_id=resolved_profile.model_id,
            profile_configuration_fingerprint=provider_configuration_fingerprint(resolved_profile),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
            image_inputs=(
                [DevelopmentLlmImageIdentity(
                    sha256=hashlib.sha256(image.data).hexdigest(),
                    media_type=image.media_type, size_bytes=len(image.data),
                ) for image in images]
                if images is not None else None
            ),
        )
        fingerprint = _fingerprint(call_input.model_dump(mode="json"))
        batch_key = hashlib.sha256(self.batch_id.encode()).hexdigest()[:24]
        base_step_id = f"{self.journal.prefix}.{batch_key}.a{self.attempt_index}.{fingerprint}"
        authorized_step = self.journal.retry_unknown_batch_step_id
        if authorized_step is not None and authorized_step.startswith(
            f"{self.journal.prefix}.{batch_key}.a{self.attempt_index}."
        ) and authorized_step != base_step_id and not authorized_step.startswith(base_step_id + ".r"):
            raise ValueError("已授权失败批次的实际输入或模型配置已变化；不能使用同配置重试许可。")
        prior_records = (
            load_development_checkpoints_by_prefix(
                self.journal.root, project_id=self.journal.project_id,
                workflow_operation_id=self.journal.session_id, step_id_prefix=base_step_id,
                result_model=DevelopmentLlmBatchCheckpoint,
            ) if self.journal.root is not None else []
        )
        last_existing_ordinal = max((record.recovery_ordinal or 0 for record in prior_records), default=0)
        ordinal = 0
        parent_step_id = None
        unknown_authorized_parent = None
        seen_execution_ids: set[str] = set()
        self.reused_calls = []
        self.recovered_candidate = None
        while True:
            self.step_id = base_step_id if ordinal == 0 else f"{base_step_id}.r{ordinal}"
            loaded = (
                load_development_checkpoint(
                    self.journal.root, project_id=self.journal.project_id,
                    workflow_operation_id=self.journal.session_id, step_id=self.step_id,
                    result_model=DevelopmentLlmBatchCheckpoint,
                ) if self.journal.root is not None else None
            )
            if not isinstance(loaded, DevelopmentLlmBatchCheckpoint):
                if self.journal.root is not None and development_checkpoint_exists(
                    self.journal.root, workflow_operation_id=self.journal.session_id, step_id=self.step_id,
                ):
                    raise ValueError("开发 LLM 批次检查点不可读取；拒绝自动重复模型调用。")
                if ordinal < last_existing_ordinal:
                    raise ValueError("开发 LLM 限额恢复链缺少前序记录；拒绝重复模型调用。")
                if ordinal and (
                    not self.journal.retry_rejected_execution_id
                    or self.journal.retry_rejected_execution_id in seen_execution_ids
                ):
                    raise llm_runtime.LlmRuntimeError(
                        "限额拒绝需要新一次显式开发执行；本次不会自动重复模型调用。",
                        code="development_llm_candidate_unavailable", status_code=409,
                    )
                break
            if (
                loaded.input_fingerprint != fingerprint
                or loaded.call_input != call_input
                or loaded.batch_id != self.batch_id
                or loaded.attempt != self.attempt_index
                or (loaded.recovery_ordinal or 0) != ordinal
                or loaded.recovery_parent_step_id != parent_step_id
                or loaded.unknown_retry_authorized_step_id != unknown_authorized_parent
                or (ordinal and not loaded.recovery_execution_id)
                or (loaded.recovery_execution_id and loaded.recovery_execution_id in seen_execution_ids)
            ):
                raise ValueError("开发 LLM 批次输入证据不一致。")
            self.checkpoint = loaded
            self.reused_calls.extend(loaded.llm_calls)
            if loaded.recovery_execution_id:
                seen_execution_ids.add(loaded.recovery_execution_id)
            if loaded.raw_json_candidate is not None:
                if loaded.candidate_fingerprint != _fingerprint(loaded.raw_json_candidate):
                    raise ValueError("开发 LLM 批次候选证据不一致。")
                if loaded.recovered_candidate is not None:
                    provenance = loaded.recovered_candidate
                    if (provenance.batch_id != self.batch_id or provenance.attempt != self.attempt_index
                            or provenance.input_fingerprint != fingerprint
                            or provenance.candidate_fingerprint != loaded.candidate_fingerprint):
                        raise ValueError("开发恢复候选来源与当前批次不一致。")
                    self.recovered_candidate = provenance
                return loaded.raw_json_candidate
            # A refusal can use the existing explicit-execution recovery.
            # Unknown outcomes require separate authorization of this exact
            # saved failure; a historical authorized child only permits replay.
            rejected = (loaded.status == "call_failed"
                    and loaded.call_error_code in {"codex_cli_rate_limited", "llm_rate_limited"}
                    and loaded.call_error_status_code == 429)
            unknown_failure = (
                loaded.status == "call_failed"
                and loaded.call_error_code
                in {"codex_cli_execution_failed", "codex_cli_timeout"}
            )
            historical_authorized_child = unknown_failure and any(
                record.recovery_ordinal == ordinal + 1
                and record.recovery_parent_step_id == self.step_id
                and record.unknown_retry_authorized_step_id == self.step_id
                for record in prior_records
            )
            authorized_unknown = unknown_failure and (
                self.step_id == authorized_step or historical_authorized_child
            )
            if rejected or authorized_unknown:
                if ordinal >= 2_147_483_647:
                    raise ValueError("开发 LLM 限额恢复序号超出可表示范围。")
                unknown_authorized_parent = self.step_id if authorized_unknown else None
                parent_step_id = self.step_id
                ordinal += 1
                continue
            replayable_errors = {"llm_json_invalid", "llm_json_not_object"}
            if images is not None:
                # Replay the known refusal into the caller's existing fallback;
                # never turn it into a second image call or a new retry policy.
                replayable_errors.add("llm_image_input_unsupported")
            if loaded.status == "call_failed" and loaded.call_error_code in replayable_errors:
                raise llm_runtime.LlmRuntimeError(
                    loaded.call_error_message or "模型未返回 JSON 对象。",
                    code=loaded.call_error_code,
                    status_code=loaded.call_error_status_code or 502,
                )
            raise llm_runtime.LlmRuntimeError(
                "开发批次已发起但没有可恢复的 JSON 候选；不会自动重复模型调用。",
                code="development_llm_candidate_unavailable",
                status_code=409,
            )
        receipt = (self.journal.candidate_recovery_store.match(
            batch_id=self.batch_id, attempt=self.attempt_index, input_fingerprint=fingerprint,
        ) if self.journal.candidate_recovery_store is not None else None)
        if receipt is not None:
            self.recovered_candidate = receipt.provenance
            self.checkpoint = DevelopmentLlmBatchCheckpoint(
                batch_id=self.batch_id, attempt=self.attempt_index,
                input_fingerprint=fingerprint, call_input=call_input,
                status="candidate_imported", raw_json_candidate=receipt.evidence.candidate,
                candidate_fingerprint=receipt.provenance.candidate_fingerprint,
                recovered_candidate=receipt.provenance,
                recovery_ordinal=ordinal if self.journal.retry_rejected_execution_id else None,
                recovery_execution_id=self.journal.retry_rejected_execution_id,
                recovery_parent_step_id=parent_step_id,
                unknown_retry_authorized_step_id=unknown_authorized_parent,
            )
            self._save()  # Current-session evidence admission, not an old model call.
            return self.checkpoint.raw_json_candidate
        self.checkpoint = DevelopmentLlmBatchCheckpoint(
            batch_id=self.batch_id,
            attempt=self.attempt_index,
            input_fingerprint=fingerprint,
            call_input=call_input,
            status="prepared",
            recovery_ordinal=ordinal if self.journal.retry_rejected_execution_id else None,
            recovery_execution_id=self.journal.retry_rejected_execution_id,
            recovery_parent_step_id=parent_step_id,
            unknown_retry_authorized_step_id=unknown_authorized_parent,
        )
        self._save()  # Durable intent precedes the call.
        collector = VideoLocalizationLlmTraceCollector()
        # The caller owns call IDs in its trace sink. Keep both copies identical;
        # recovery attempt identity lives in the separate immutable checkpoint.
        collect = collector.sink(call_id=self.call_id, purpose=self.purpose, round_index=self.round_index)

        def trace(value):
            collect(value)
            if trace_sink is not None:
                trace_sink(value)

        try:
            complete = (
                llm_runtime.complete_json if images is None
                else llm_runtime.complete_multimodal_json
            )
            raw = complete(
                system_prompt,
                user_payload,
                **({"images": images} if images is not None else {}),
                profile_id=profile_id,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
                trace_sink=trace,
                resolved_profile=resolved_profile,
            )
        except llm_runtime.LlmRuntimeError as exc:
            self.checkpoint = self.checkpoint.model_copy(
                update={
                    "status": "call_failed",
                    "llm_calls": collector.records(),
                    "call_error_code": exc.code,
                    "call_error_message": str(exc),
                    "call_error_status_code": exc.status_code,
                }
            )
            self._save()
            raise
        except Exception as exc:
            self.checkpoint = self.checkpoint.model_copy(
                update={
                    "status": "call_failed",
                    "llm_calls": collector.records(),
                    "call_error_code": type(exc).__name__,
                    "call_error_message": "模型调用中断；没有可恢复的 JSON 候选。",
                }
            )
            self._save()
            raise
        self.checkpoint = self.checkpoint.model_copy(
            update={
                "status": "response_received",
                "raw_json_candidate": raw,
                "candidate_fingerprint": _fingerprint(raw),
                "llm_calls": collector.records(),
            }
        )
        self._save()  # Candidate is durable before any domain validation.
        return raw

    def record_validation(self, *, validator_version: str, error: Exception | None = None) -> None:
        if self.checkpoint is None or self.checkpoint.raw_json_candidate is None:
            return
        validation = DevelopmentLlmValidation(
            validator_version=validator_version,
            status="failed" if error else "passed",
            error_type=type(error).__name__ if error else None,
            error_message=str(error) if error else None,
        )
        self.checkpoint = self.checkpoint.model_copy(
            update={
                "status": "validation_failed" if error else "validation_passed",
                "validations": [*self.checkpoint.validations, validation],
            }
        )
        self._save()
