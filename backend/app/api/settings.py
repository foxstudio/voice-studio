from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.errors import AppException
from app.schemas.voice_studio import (
    AppSettings,
    AppSettingsPatch,
    CloudConnectionTestResponse,
    CodexCliAuthActionResponse,
    CodexCliStatusResponse,
    DoubaoSecretUpdate,
    LlmConnectionTestResponse,
    LlmModelInfo,
    LlmModelListResponse,
    LlmProviderListResponse,
    LlmProviderProfileUpsert,
    WebSearchSettings,
    WebSearchSettingsUpdate,
    WebSearchTestResponse,
    MimoSecretUpdate,
    VolcengineDirectorySecretUpdate,
)
from app.services import (
    cloud_connection_tests,
    codex_cli_provider,
    llm_provider,
    llm_runtime,
    settings_store,
    web_search,
)

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
    try:
        return settings_store.patch(settings)
    except ValueError as exc:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_LLM_PROFILE_NOT_FOUND",
            str(exc),
        ) from exc


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


def _raise_codex_cli_error(exc: codex_cli_provider.CodexCliError) -> None:
    raise AppException(
        exc.status_code,
        exc.code.upper(),
        str(exc),
    ) from exc


def _require_local_codex_request(request: Request) -> None:
    host = request.client.host if request.client is not None else None
    if not codex_cli_provider.is_local_client(host):
        raise AppException(
            403,
            "CODEX_CLI_LOCAL_ONLY",
            "本机 Codex 状态、登录与订阅测试只允许从当前电脑访问",
        )


@router.get("/llm-profiles", response_model=LlmProviderListResponse)
async def get_llm_profiles():
    return settings_store.llm_profiles()


@router.put("/llm-profiles/{profile_id}", response_model=LlmProviderListResponse)
async def save_llm_profile(profile_id: str, data: LlmProviderProfileUpsert):
    try:
        if data.protocol == "openai_compatible":
            normalized = llm_provider.normalize_base_url(data.base_url)
            data = data.model_copy(update={"base_url": normalized})
        return settings_store.update_llm_profile(profile_id, data)
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
async def get_llm_models(profile_id: str, request: Request):
    profile = _llm_profile_or_404(profile_id)
    if profile.protocol == "codex_cli":
        _require_local_codex_request(request)
        try:
            models = codex_cli_provider.list_models()
        except codex_cli_provider.CodexCliError as exc:
            _raise_codex_cli_error(exc)
        return LlmModelListResponse(
            profile_id=profile_id,
            models=[
                LlmModelInfo(model_id=item["id"], owned_by=item.get("owned_by"))
                for item in models
            ],
        )
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
async def test_llm_profile(profile_id: str, request: Request):
    profile = _llm_profile_or_404(profile_id)
    if profile.protocol == "codex_cli":
        _require_local_codex_request(request)
    if profile.protocol == "openai_compatible" and not profile.model_id.strip():
        raise AppException(400, "LLM_MODEL_NOT_CONFIGURED", "请先填写或选择要测试的模型 ID")
    was_default = settings_store.llm_profiles().default_profile_id == profile_id
    settings_store.clear_llm_profile_verification(profile_id)
    traces: list[llm_runtime.LlmCompletionTrace] = []
    try:
        result = llm_runtime.complete_json(
            '这是模型连接测试。无论收到什么内容，只返回 JSON：{"ok":true}，不要添加其他字段。',
            {"ping": "pong"},
            profile_id=profile_id,
            temperature=0.0,
            max_tokens=256,
            timeout=45,
            trace_sink=traces.append,
        )
    except llm_runtime.LlmRuntimeError as exc:
        raise AppException(exc.status_code, "LLM_MODEL_TEST_FAILED", str(exc)) from exc
    if result.get("ok") is not True:
        raise AppException(502, "LLM_MODEL_TEST_INVALID_RESPONSE", "模型已响应，但没有按测试要求返回正确结果")
    settings_store.mark_llm_profile_verified(profile_id)
    if was_default:
        settings_store.set_default_llm_profile(profile_id)
    tested_model_id = traces[-1].model_id if traces else profile.model_id or None
    return LlmConnectionTestResponse(
        profile_id=profile_id,
        models_count=None,
        selected_model_available=True,
        tested_model_id=tested_model_id,
        response_verified=True,
        billing_effect="minimal",
        message=(
            (
                f"本机 Codex 已用 {tested_model_id or '默认模型'} 返回正确内容；"
                "本次测试已消耗少量 ChatGPT/Codex 订阅额度"
            )
            if profile.protocol == "codex_cli"
            else f"模型 {tested_model_id or profile.model_id} 已返回正确内容；本次为最小生成测试，已产生少量用量"
        ),
    )


@router.get(
    "/codex-cli/status",
    response_model=CodexCliStatusResponse,
    summary="查看本机 Codex CLI 状态",
    description=(
        "仅允许从当前电脑访问。通过 Codex CLI 自身的版本和登录状态命令检查安装、"
        "ChatGPT 登录及图片输入能力；不读取认证文件、令牌或浏览器 Cookie。"
    ),
)
async def get_codex_cli_status(request: Request):
    _require_local_codex_request(request)
    return codex_cli_provider.status()


@router.post(
    "/codex-cli/login",
    response_model=CodexCliAuthActionResponse,
    summary="登录本机 Codex CLI",
    description=(
        "仅允许从当前电脑访问。异步打开 Codex CLI 的 ChatGPT 登录流程；登录结果可通过状态接口轮询。"
        "此操作会改变这台电脑上其他 Codex 客户端共享的登录状态，但 Voice Studio 不读取认证文件。"
    ),
)
async def login_codex_cli(request: Request):
    _require_local_codex_request(request)
    try:
        codex_cli_provider.start_login()
    except codex_cli_provider.CodexCliError as exc:
        _raise_codex_cli_error(exc)
    return CodexCliAuthActionResponse(
        status="started",
        message="已启动 ChatGPT 登录；系统浏览器将打开登录页面，请按页面提示完成登录",
    )


@router.post(
    "/codex-cli/relogin",
    response_model=CodexCliAuthActionResponse,
    summary="更换本机 Codex CLI 账号",
    description=(
        "仅允许从当前电脑访问。先退出当前 Codex CLI 账号，再异步打开新的 ChatGPT 登录流程。"
        "此操作会改变这台电脑上其他 Codex 客户端共享的登录状态，但 Voice Studio 不读取认证文件。"
    ),
)
async def relogin_codex_cli(request: Request):
    _require_local_codex_request(request)
    try:
        codex_cli_provider.start_login(relogin=True)
    except codex_cli_provider.CodexCliError as exc:
        _raise_codex_cli_error(exc)
    return CodexCliAuthActionResponse(
        status="started",
        message="正在退出旧账号并启动新的 ChatGPT 登录；请在系统浏览器中完成登录",
    )


@router.post(
    "/codex-cli/logout",
    response_model=CodexCliAuthActionResponse,
    summary="退出本机 Codex CLI",
    description=(
        "仅允许从当前电脑访问。调用 Codex CLI 自身的退出命令，不读取认证文件。"
        "退出会同时影响这台电脑上其他共享同一 Codex 登录的客户端。"
    ),
)
async def logout_codex_cli(request: Request):
    _require_local_codex_request(request)
    try:
        codex_cli_provider.logout()
    except codex_cli_provider.CodexCliError as exc:
        _raise_codex_cli_error(exc)
    return CodexCliAuthActionResponse(
        status="logged_out",
        message="已退出 Codex CLI 登录",
    )


@router.get("/web-search", response_model=WebSearchSettings)
async def get_web_search_settings():
    return settings_store.web_search_settings()


@router.put("/web-search", response_model=WebSearchSettings)
async def save_web_search_settings(data: WebSearchSettingsUpdate):
    return settings_store.update_web_search_settings(data)


@router.post("/web-search/test", response_model=WebSearchTestResponse)
async def test_web_search_settings():
    settings = settings_store.web_search_settings()
    results = web_search.search(settings, "Voice Studio subtitle localization", api_key=settings_store.web_search_api_key())
    return WebSearchTestResponse(
        provider=settings.provider,
        result_count=len(results),
        message=f"搜索连接正常，返回 {len(results)} 条结果",
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
