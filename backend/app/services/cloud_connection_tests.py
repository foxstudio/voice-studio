"""Safe, provider-specific connection probes for fixed cloud credentials.

The settings API exposes only narrowly scoped probes. It never accepts a URL
or secret in the test request, so credentials remain write-only and the MiMo
probe cannot be turned into a general-purpose server-side request primitive.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlsplit

from app.services import doubao_client, doubao_speaker_catalog_store, llm_provider, settings_store


CloudProvider = Literal["mimo", "doubao", "volcengine_directory"]


class CloudConnectionTestError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        upstream_status: int | None = None,
        request_id: str | None = None,
        logid: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.upstream_status = upstream_status
        self.request_id = request_id
        self.logid = logid

    def safe_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        if self.upstream_status is not None:
            detail["upstream_status"] = self.upstream_status
        if self.request_id:
            detail["request_id"] = self.request_id
        if self.logid:
            detail["logid"] = self.logid
        return detail


def _credentials_required(message: str) -> CloudConnectionTestError:
    return CloudConnectionTestError("CLOUD_CREDENTIALS_REQUIRED", message)


def _mimo_base_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise CloudConnectionTestError("CLOUD_BASE_URL_INVALID", "MiMo Base URL 格式无效") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    allowed_host = hostname == "api.xiaomimimo.com" or bool(
        re.fullmatch(r"token-plan-[a-z0-9-]+\.xiaomimimo\.com", hostname)
    )
    if (
        parsed.scheme != "https"
        or not allowed_host
        or port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.path.rstrip("/") != "/v1"
        or parsed.query
        or parsed.fragment
    ):
        raise CloudConnectionTestError(
            "CLOUD_BASE_URL_INVALID",
            "连接测试只支持 MiMo 官方 HTTPS Base URL；请使用按量付费或控制台显示的 Token Plan 地址",
        )
    return f"https://{hostname}/v1"


def _map_http_failure(provider_label: str, status_code: int | None) -> CloudConnectionTestError:
    if status_code == 401:
        return CloudConnectionTestError(
            "CLOUD_AUTH_FAILED",
            f"{provider_label} 鉴权失败，请检查凭据与接入地址是否匹配",
            upstream_status=status_code,
        )
    if status_code in {402, 429}:
        return CloudConnectionTestError(
            "CLOUD_QUOTA_EXHAUSTED",
            f"{provider_label} 账户余额、套餐额度或请求频率受限",
            upstream_status=status_code,
        )
    if status_code == 403:
        return CloudConnectionTestError(
            "CLOUD_PERMISSION_DENIED",
            f"{provider_label} 拒绝访问，请检查产品权限、地区或风控状态",
            upstream_status=status_code,
        )
    return CloudConnectionTestError(
        "CLOUD_UPSTREAM_FAILED",
        f"{provider_label} 暂时无法完成连接测试，请稍后重试",
        status_code=502,
        upstream_status=status_code,
    )


def test_mimo_connection() -> dict[str, Any]:
    settings = settings_store.get()
    api_key = settings_store.mimo_api_key()
    if not api_key:
        raise _credentials_required("请先保存 MiMo API Key")
    base_url = _mimo_base_url(settings.mimo_base_url)
    try:
        models = llm_provider.list_models(base_url=base_url, api_key=api_key, timeout=12)
    except llm_provider.LLMProviderError as exc:
        if exc.status_code is not None:
            raise _map_http_failure("MiMo", exc.status_code) from exc
        raise CloudConnectionTestError(
            "CLOUD_CONNECTION_FAILED",
            "无法连接 MiMo，请检查网络与 Base URL",
            status_code=502,
        ) from exc
    return {
        "provider": "mimo",
        "status": "connected",
        "message": f"MiMo 连接正常，获取到 {len(models)} 个可用模型",
        "verified_scopes": ["models"],
        "billing_effect": "none",
        "models_count": len(models),
    }


def test_doubao_connection() -> dict[str, Any]:
    settings = settings_store.get()
    api_key = settings_store.doubao_api_key()
    if not api_key:
        raise _credentials_required("请先保存豆包 API Key")
    try:
        result = doubao_client.probe_tts_connection(
            base_url=settings.doubao_base_url,
            api_key=api_key,
            resource_id=settings.doubao_default_tts_resource_id,
            timeout=15,
        )
    except doubao_client.DoubaoAPIError as exc:
        mapped = _map_http_failure("豆包 TTS", exc.status_code)
        mapped.logid = exc.logid
        raise mapped from exc
    return {
        "provider": "doubao",
        "status": "connected",
        "message": "豆包 TTS 连接正常；复刻、训练与 Seed Audio 权限未在本次测试中验证",
        "verified_scopes": ["tts"],
        "billing_effect": "minimal",
        "request_id": result["request_id"],
        "logid": result.get("logid"),
    }


def test_volcengine_directory_connection() -> dict[str, Any]:
    access_key = settings_store.volcengine_access_key_id()
    secret_key = settings_store.volcengine_secret_access_key()
    if not access_key or not secret_key:
        raise _credentials_required("请先完整保存火山引擎 Access Key ID 与 Secret Access Key")
    client = doubao_speaker_catalog_store.VolcengineOpenAPIClient(access_key, secret_key)
    try:
        payload = client.signed_request(
            "ListSpeakers",
            body={"ResourceIDs": ["seed-tts-2.0"], "Page": 1, "Limit": 1},
            timeout=12,
        )
    except doubao_speaker_catalog_store.DoubaoSpeakerCatalogError as exc:
        raise CloudConnectionTestError(
            "CLOUD_UPSTREAM_FAILED",
            "火山引擎音色目录连接失败，请检查 AK/SK 与 ListSpeakers 权限",
            status_code=502,
        ) from exc
    metadata = payload.get("ResponseMetadata") if isinstance(payload, dict) else None
    request_id = metadata.get("RequestId") if isinstance(metadata, dict) else None
    return {
        "provider": "volcengine_directory",
        "status": "connected",
        "message": "火山引擎 AK/SK 可用，音色目录权限正常",
        "verified_scopes": ["speaker_catalog"],
        "billing_effect": "none",
        "request_id": request_id,
    }


def test_connection(provider: CloudProvider) -> dict[str, Any]:
    if provider == "mimo":
        return test_mimo_connection()
    if provider == "doubao":
        return test_doubao_connection()
    if provider == "volcengine_directory":
        return test_volcengine_directory_connection()
    raise CloudConnectionTestError("CLOUD_PROVIDER_INVALID", "不支持这个云服务连接测试")
