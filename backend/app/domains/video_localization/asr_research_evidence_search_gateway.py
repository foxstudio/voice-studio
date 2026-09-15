"""Durable search gateway for managed ASR research evidence."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_research_evidence_managed_contracts as managed_contracts,
    managed_artifact_files,
)
from app.errors import AppException
from app.schemas.video_localization_asr_research_evidence_step import (
    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RESEARCH_SEARCH_ARTIFACT_SCHEMA_VERSION,
    AsrResearchSearchArtifactV1,
    AsrResearchSearchInputV1,
    AsrResearchSearchResultV1,
    parse_research_search_artifact,
    research_search_artifact_bytes,
    research_search_input_fingerprint,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.voice_studio import WebSearchSettings
from app.services import web_search
from app.services import (
    video_localization_llm_provider_execution as provider_execution,
)
from app.services import (
    video_localization_provider_step_lifecycle as provider_lifecycle,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
)


CommittedSearchSink = Callable[["CommittedResearchSearch"], None]
_ARTIFACT_KIND = "step-result"
_ARTIFACT_KEY = "primary"
_UNCERTAIN_SEARCH_CODES = frozenset({"WEB_SEARCH_UNAVAILABLE"})


@dataclass(frozen=True)
class CommittedResearchSearch:
    reference: managed_contracts.AsrResearchSearchReferenceV1
    artifact: AsrResearchSearchArtifactV1


def search_configuration_fingerprint(
    settings: WebSearchSettings,
) -> str:
    """Hash execution-affecting search settings without API secrets."""

    payload = {
        "enabled": settings.enabled,
        "provider": settings.provider,
        "base_url": settings.base_url,
        "max_results_per_query": settings.max_results_per_query,
        "fallback_provider": "duckduckgo",
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def search_endpoint_fingerprint(
    provider: str,
    base_url: str,
) -> str:
    endpoint = {
        "wikipedia": "https://*.wikipedia.org/w/api.php",
        "tavily": "https://api.tavily.com/search",
        "duckduckgo": "https://lite.duckduckgo.com/lite/",
    }.get(provider, base_url)
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ManagedResearchSearchGateway:
    execution_fence: ExecutionFence
    round_index: int
    candidate_id: str
    settings: WebSearchSettings
    expected_search_configuration_fingerprint: str
    behavior_fingerprint: str
    file_backend: ManagedArtifactFileBackend = managed_artifact_files
    on_committed_search: CommittedSearchSink | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def __post_init__(self) -> None:
        if (
            search_configuration_fingerprint(self.settings)
            != self.expected_search_configuration_fingerprint
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_RESEARCH_SEARCH_CONFIG_CHANGED",
                "资料查询的搜索配置已经变化，请重新提交任务。",
            )
        if re.fullmatch(r"[0-9a-f]{64}", self.behavior_fingerprint) is None:
            raise ValueError("research behavior fingerprint is invalid")

    def search(
        self,
        settings: WebSearchSettings,
        query: str,
        *,
        api_key: str | None,
        attempt: int,
    ) -> list[web_search.SearchResult]:
        if settings != self.settings:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_RESEARCH_SEARCH_CONFIG_CHANGED",
                "资料查询使用的搜索配置与已锁定输入不一致，请重新提交任务。",
            )
        return self._run(
            provider=settings.provider,
            query=query,
            limit=settings.max_results_per_query,
            attempt=attempt,
            search_kind="primary",
            submit=lambda: web_search.search(
                settings,
                query,
                api_key=api_key,
            ),
        )

    def search_general_web(
        self,
        query: str,
        *,
        limit: int,
        attempt: int,
    ) -> list[web_search.SearchResult]:
        return self._run(
            provider="duckduckgo",
            query=query,
            limit=limit,
            attempt=attempt,
            search_kind="general_fallback",
            submit=lambda: web_search.search_general_web(
                query,
                limit=limit,
            ),
        )

    def _run(
        self,
        *,
        provider: str,
        query: str,
        limit: int,
        attempt: int,
        search_kind: str,
        submit: Callable[[], list[web_search.SearchResult]],
    ) -> list[web_search.SearchResult]:
        query_digest = hashlib.sha256(
            query.encode("utf-8")
        ).hexdigest()[:12]
        request_id = (
            f"research-r{self.round_index:02d}-"
            f"{self.candidate_id}-{search_kind}-"
            f"{query_digest}-a{attempt}"
        )
        call_input = AsrResearchSearchInputV1(
            request_id=request_id,
            round_index=self.round_index,
            candidate_id=self.candidate_id,
            search_kind=search_kind,
            attempt=attempt,
            provider=provider,
            query=query,
            limit=limit,
            provider_endpoint_fingerprint=(
                search_endpoint_fingerprint(
                    provider,
                    self.settings.base_url,
                )
            ),
            search_configuration_fingerprint=(
                self.expected_search_configuration_fingerprint
            ),
            behavior_fingerprint=self.behavior_fingerprint,
        )
        input_fingerprint = research_search_input_fingerprint(
            call_input
        )
        step_id = _step_id(request_id)
        cost_class = (
            "external_paid"
            if provider == "tavily"
            else "external_free"
        )
        idempotency_key = provider_execution.provider_idempotency_key(
            self.execution_fence,
            key_prefix="vsl_asr_res_search_",
            step_id=step_id,
            input_fingerprint=input_fingerprint,
        )
        plan = provider_lifecycle.ProviderStepPlan(
            step_id=step_id,
            workflow_version=(
                ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
            ),
            input_fingerprint=input_fingerprint,
            cost_class=cost_class,
            provider_name=f"web_search_{provider}",
            provider_idempotency_key=idempotency_key,
            artifact_kind=_ARTIFACT_KIND,
            artifact_key=_ARTIFACT_KEY,
            payload_schema_version=(
                ASR_RESEARCH_SEARCH_ARTIFACT_SCHEMA_VERSION
            ),
            media_type="application/json",
        )
        try:
            executed = provider_lifecycle.run_provider_step(
                self.execution_fence,
                file_backend=self.file_backend,
                plan=plan,
                submit=lambda _key: self._submit(
                    call_input,
                    submit=submit,
                    cost_class=cost_class,
                    input_fingerprint=input_fingerprint,
                ),
                clock=self.clock,
            )
        except (
            provider_lifecycle.ProviderReplayBlocked,
            provider_lifecycle.ProviderStepResultUnknown,
        ) as exc:
            code = (
                "VIDEO_LOCALIZATION_RESEARCH_SEARCH_RESULT_UNKNOWN"
            )
            if cost_class == "external_free":
                code = "WEB_SEARCH_UNAVAILABLE"
            raise AppException(
                409 if cost_class == "external_paid" else 502,
                code,
                (
                    "付费搜索请求可能已经执行，但结果暂时无法确认；"
                    "系统没有自动重复请求。"
                    if cost_class == "external_paid"
                    else "暂时无法确认搜索结果。"
                ),
            ) from exc
        except provider_lifecycle.ProviderStepExecutionFailed as exc:
            error_code = str(
                exc.step.error_code or "WEB_SEARCH_FAILED"
            )
            if cost_class == "external_paid":
                error_code = (
                    "VIDEO_LOCALIZATION_RESEARCH_SEARCH_FAILED"
                )
            raise AppException(
                502,
                error_code,
                "搜索服务未能完成资料查询。",
            ) from exc
        except provider_lifecycle.ProviderStepIntegrityError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_RESEARCH_SEARCH_ARTIFACT_INVALID",
                "资料查询的搜索记录缺失或损坏，请先运行任务详情审计。",
            ) from exc
        try:
            artifact = parse_research_search_artifact(
                executed.content
            )
        except (TypeError, ValueError) as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_RESEARCH_SEARCH_ARTIFACT_INVALID",
                "资料查询的搜索记录无法通过完整性校验。",
            ) from exc
        if (
            artifact.search_input_fingerprint
            != input_fingerprint
            or artifact.request_id != request_id
            or artifact.round_index != self.round_index
            or artifact.candidate_id != self.candidate_id
            or artifact.search_kind != search_kind
            or artifact.attempt != attempt
            or artifact.provider != provider
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_RESEARCH_SEARCH_ARTIFACT_INVALID",
                "资料查询的搜索记录与已锁定输入不一致。",
            )
        if self.on_committed_search is not None:
            self.on_committed_search(
                CommittedResearchSearch(
                    reference=(
                        managed_contracts
                        .AsrResearchSearchReferenceV1(
                            request_id=request_id,
                            round_index=self.round_index,
                            candidate_id=self.candidate_id,
                            search_kind=search_kind,
                            attempt=attempt,
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
        return [
            web_search.SearchResult(
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                retrieved_at=item.retrieved_at,
            )
            for item in artifact.results
        ]

    def _submit(
        self,
        call_input: AsrResearchSearchInputV1,
        *,
        submit: Callable[[], list[web_search.SearchResult]],
        cost_class: str,
        input_fingerprint: str,
    ) -> provider_lifecycle.ProviderResponse:
        started_at = time.perf_counter()
        try:
            results = submit()
        except AppException as exc:
            if (
                cost_class == "external_paid"
                and exc.code in _UNCERTAIN_SEARCH_CODES
            ):
                raise provider_lifecycle.ProviderResultUncertain(
                    "VIDEO_LOCALIZATION_RESEARCH_SEARCH_RESULT_UNKNOWN"
                ) from None
            raise provider_lifecycle.ProviderRequestRejected(
                exc.code
            ) from None
        except (TimeoutError, OSError):
            if cost_class == "external_paid":
                raise provider_lifecycle.ProviderResultUncertain(
                    "VIDEO_LOCALIZATION_RESEARCH_SEARCH_RESULT_UNKNOWN"
                ) from None
            raise provider_lifecycle.ProviderRequestRejected(
                "WEB_SEARCH_UNAVAILABLE"
            ) from None
        retrieved_at = self.clock().isoformat()
        artifact = AsrResearchSearchArtifactV1(
            search_input_fingerprint=input_fingerprint,
            request_id=call_input.request_id,
            round_index=call_input.round_index,
            candidate_id=call_input.candidate_id,
            search_kind=call_input.search_kind,
            attempt=call_input.attempt,
            provider=call_input.provider,
            duration_ms=max(
                0,
                int(round((time.perf_counter() - started_at) * 1_000)),
            ),
            results=tuple(
                AsrResearchSearchResultV1(
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    retrieved_at=(
                        item.retrieved_at or retrieved_at
                    ),
                )
                for item in results[: call_input.limit]
            ),
        )
        return provider_lifecycle.ProviderResponse(
            content=research_search_artifact_bytes(artifact)
        )


def _step_id(request_id: str) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        request_id.casefold(),
    ).strip("_")
    return f"research_search_{normalized}"[:256]


__all__ = [
    "CommittedResearchSearch",
    "ManagedResearchSearchGateway",
    "search_configuration_fingerprint",
    "search_endpoint_fingerprint",
]
