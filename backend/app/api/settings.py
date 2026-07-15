from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.errors import AppException
from app.schemas.voice_studio import (
    AppSettings,
    AppSettingsPatch,
    CloudConnectionTestResponse,
    DoubaoSecretUpdate,
    LlmConnectionTestResponse,
    LlmModelInfo,
    LlmModelListResponse,
    LlmProviderListResponse,
    LlmProviderProfileUpsert,
    MimoSecretUpdate,
    VolcengineDirectorySecretUpdate,
)
from app.services import cloud_connection_tests, llm_provider, llm_runtime, settings_store

router = APIRouter()


class StorageLocation(BaseModel):
    key: str
    label: str
    path: str
    category: str
    description: str
    exists: bool
    size_bytes: int
    file_count: int
    truncated: bool
    cleanup_key: str | None = None
    cleanup_label: str | None = None
    cleanup_risk: str | None = None


class StorageFlow(BaseModel):
    name: str
    path: str
    description: str


class StorageAuditResponse(BaseModel):
    locations: list[StorageLocation]
    flows: list[StorageFlow]
    total_bytes: int


class StorageCleanupRequest(BaseModel):
    targets: list[str]


class StorageCleanupItem(BaseModel):
    target: str
    path: str
    before_bytes: int
    after_bytes: int
    removed_bytes: int
    before_files: int
    after_files: int


class StorageCleanupResponse(BaseModel):
    cleaned: list[StorageCleanupItem]
    skipped: list[str]
    removed_bytes: int


class StorageOpenRequest(BaseModel):
    key: str


class StorageOpenResponse(BaseModel):
    status: str
    key: str
    path: str


@router.get("", response_model=AppSettings)
async def get_settings():
    return settings_store.get()


@router.patch("", response_model=AppSettings)
async def update_settings(settings: AppSettingsPatch):
    return settings_store.patch(settings)


@router.patch("/mimo-secret", response_model=AppSettings)
async def update_mimo_secret(data: MimoSecretUpdate):
    return settings_store.update_mimo_api_key(data.api_key, data.clear)


@router.patch("/doubao-secret", response_model=AppSettings)
async def update_doubao_secret(data: DoubaoSecretUpdate):
    return settings_store.update_doubao_api_key(data.api_key, data.clear)


@router.patch("/volcengine-directory-secret", response_model=AppSettings)
async def update_volcengine_directory_secret(data: VolcengineDirectorySecretUpdate):
    return settings_store.update_volcengine_directory_credentials(
        data.access_key_id,
        data.secret_access_key,
        clear_access_key_id=data.clear_access_key_id,
        clear_secret_access_key=data.clear_secret_access_key,
    )


@router.post(
    "/cloud-connections/{provider}/test",
    response_model=CloudConnectionTestResponse,
)
async def test_cloud_connection(
    provider: Literal["mimo", "doubao", "volcengine_directory"],
):
    try:
        return cloud_connection_tests.test_connection(provider)
    except cloud_connection_tests.CloudConnectionTestError as exc:
        raise AppException(
            exc.status_code,
            exc.code,
            str(exc),
            exc.safe_detail() or None,
        ) from exc


def _llm_profile_or_404(profile_id: str):
    profile = settings_store.llm_profile(profile_id)
    if profile is None:
        raise AppException(404, "LLM_PROFILE_NOT_FOUND", "未找到这个语言模型配置")
    return profile


def _raise_llm_provider_error(exc: llm_provider.LLMProviderError) -> None:
    upstream_status = exc.status_code
    status_code = 400 if upstream_status in {400, 401, 403, 404, 422} else 502
    raise AppException(
        status_code,
        "LLM_PROVIDER_REQUEST_FAILED",
        str(exc),
        {"upstream_status": upstream_status} if upstream_status is not None else None,
    ) from exc


@router.get("/llm-profiles", response_model=LlmProviderListResponse)
async def get_llm_profiles():
    return settings_store.llm_profiles()


@router.put("/llm-profiles/{profile_id}", response_model=LlmProviderListResponse)
async def save_llm_profile(profile_id: str, data: LlmProviderProfileUpsert):
    try:
        normalized = llm_provider.normalize_base_url(data.base_url)
        return settings_store.update_llm_profile(profile_id, data.model_copy(update={"base_url": normalized}))
    except llm_provider.LLMProviderError as exc:
        raise AppException(400, "LLM_PROFILE_INVALID", str(exc)) from exc
    except ValueError as exc:
        raise AppException(400, "LLM_PROFILE_INVALID", str(exc)) from exc


@router.delete("/llm-profiles/{profile_id}", response_model=LlmProviderListResponse)
async def remove_llm_profile(profile_id: str):
    _llm_profile_or_404(profile_id)
    return settings_store.delete_llm_profile(profile_id)


@router.post("/llm-profiles/{profile_id}/default", response_model=LlmProviderListResponse)
async def set_default_llm_profile(profile_id: str):
    _llm_profile_or_404(profile_id)
    try:
        return settings_store.set_default_llm_profile(profile_id)
    except ValueError as exc:
        raise AppException(409, "LLM_PROFILE_NOT_VERIFIED", str(exc)) from exc


@router.post("/llm-profiles/{profile_id}/models", response_model=LlmModelListResponse)
async def get_llm_models(profile_id: str):
    profile = _llm_profile_or_404(profile_id)
    try:
        models = llm_provider.list_models(
            base_url=profile.base_url,
            api_key=settings_store.llm_api_key(profile_id),
        )
    except llm_provider.LLMProviderError as exc:
        _raise_llm_provider_error(exc)
    return LlmModelListResponse(
        profile_id=profile_id,
        models=[LlmModelInfo(model_id=item["id"], owned_by=item.get("owned_by")) for item in models],
    )


@router.post("/llm-profiles/{profile_id}/test", response_model=LlmConnectionTestResponse)
async def test_llm_profile(profile_id: str):
    profile = _llm_profile_or_404(profile_id)
    if not profile.model_id.strip():
        raise AppException(400, "LLM_MODEL_NOT_CONFIGURED", "请先填写或选择要测试的模型 ID")
    was_default = settings_store.llm_profiles().default_profile_id == profile_id
    settings_store.clear_llm_profile_verification(profile_id)
    try:
        result = llm_runtime.complete_json(
            '这是模型连接测试。无论收到什么内容，只返回 JSON：{"ok":true}，不要添加其他字段。',
            {"ping": "pong"},
            profile_id=profile_id,
            temperature=0.0,
            max_tokens=256,
            timeout=45,
        )
    except llm_runtime.LlmRuntimeError as exc:
        raise AppException(exc.status_code, "LLM_MODEL_TEST_FAILED", str(exc)) from exc
    if result.get("ok") is not True:
        raise AppException(502, "LLM_MODEL_TEST_INVALID_RESPONSE", "模型已响应，但没有按测试要求返回正确结果")
    settings_store.mark_llm_profile_verified(profile_id)
    if was_default:
        settings_store.set_default_llm_profile(profile_id)
    return LlmConnectionTestResponse(
        profile_id=profile_id,
        models_count=None,
        selected_model_available=True,
        tested_model_id=profile.model_id,
        response_verified=True,
        billing_effect="minimal",
        message=f"模型 {profile.model_id} 响应正常；本次为最小生成测试，已产生少量用量",
    )


@router.get("/storage", response_model=StorageAuditResponse)
async def get_storage_audit():
    return settings_store.storage_audit()


@router.post("/storage/cleanup", response_model=StorageCleanupResponse)
async def cleanup_storage(data: StorageCleanupRequest):
    return settings_store.cleanup_storage(data.targets)


@router.post("/storage/open", response_model=StorageOpenResponse)
async def open_storage_location(data: StorageOpenRequest):
    try:
        return settings_store.open_storage_location(data.key)
    except ValueError as exc:
        raise AppException(404, "STORAGE_LOCATION_NOT_FOUND", str(exc)) from exc
