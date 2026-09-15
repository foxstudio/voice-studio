from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from fastapi.openapi.utils import get_openapi
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app
from app.openapi_docs import SWAGGER_UI_VERSION, TAG_METADATA


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
HAS_CHINESE = re.compile(r"[\u4e00-\u9fff]")


def _operations(schema: dict[str, Any]):
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS:
                yield path, method.lower(), operation


def _property_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                names.update(properties)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return names


def test_openapi_schema_is_localized_without_changing_api_contract() -> None:
    baseline = get_openapi(
        title="baseline",
        version=app.version,
        routes=app.routes,
    )
    app.openapi_schema = None
    localized = app.openapi()

    baseline_operations = {(path, method): operation for path, method, operation in _operations(baseline)}
    localized_operations = {(path, method): operation for path, method, operation in _operations(localized)}

    assert localized["info"]["title"] == "Voice Studio 接口中心"
    assert "自动化工具和 Agent" in localized["info"]["description"]
    assert len(localized_operations) == len(baseline_operations)
    assert localized_operations.keys() == baseline_operations.keys()
    assert _property_names(localized) == _property_names(baseline)

    for key, operation in localized_operations.items():
        assert operation["operationId"] == baseline_operations[key]["operationId"]
        assert HAS_CHINESE.search(operation["summary"]), key


def test_all_tags_use_reader_friendly_chinese_names() -> None:
    schema = app.openapi()
    expected_names = {localized for localized, _ in TAG_METADATA.values()}
    documented_tags = {tag["name"] for tag in schema["tags"]}
    operation_tags = {tag for _, _, operation in _operations(schema) for tag in operation.get("tags", [])}

    assert len(documented_tags) == 21
    assert documented_tags == expected_names
    assert operation_tags == expected_names
    assert all(HAS_CHINESE.search(name) for name in documented_tags)


def test_video_localization_core_operations_explain_async_usage() -> None:
    schema = app.openapi()
    submit = schema["paths"]["/api/projects/{project_id}/video-localization/operations"]["post"]
    feed_v2 = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/feed-v2"
    ]["get"]
    get_operation = schema["paths"]["/api/projects/{project_id}/video-localization/operations/{operation_id}"]["get"]
    get_development_asr_result = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-asr-result"
    ]["get"]
    get_development_diarization_result = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-diarization-result"
    ]["get"]
    get_document_understanding_result = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-document-understanding-result"
    ]["get"]
    get_visual_evidence_result = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-visual-evidence-result"
    ]["get"]
    get_visual_evidence_frame = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-visual-evidence-frames/{frame_id}"
    ]["get"]
    get_review_decisions_result = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-review-decisions-result"
    ]["get"]
    get_whole_recheck_result = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-whole-recheck-result"
    ]["get"]
    get_transcript_quality_gate_result = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/{operation_id}/development-transcript-quality-gate-result"
    ]["get"]
    stem_audio = schema["paths"]["/api/projects/{project_id}/video-localization/stems/{kind}/audio"]["get"]
    typed_asr = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/english-asr"
    ]["post"]
    asr_workflow = schema["paths"][
        "/api/projects/{project_id}/video-localization/workflows/asr"
    ]["get"]
    typed_localization = schema["paths"][
        "/api/projects/{project_id}/video-localization/operations/localization"
    ]["post"]
    localization_workflow = schema["paths"][
        "/api/projects/{project_id}/video-localization/workflows/localization"
    ]["get"]
    assert submit["summary"] == "提交视频本土化后台任务"
    assert "`source_audio`" in submit["description"]
    assert "`stems`" in submit["description"]
    assert "`/operations/english-asr`" in submit["description"]
    assert "`/operations/localization`" in submit["description"]
    assert "不接受通用 `kind` 请求" in submit["description"]
    assert "`speaker_diarization`" in submit["description"]
    assert feed_v2["summary"] == "分页同步视频本土化后台任务"
    assert "opaque keyset cursor" in feed_v2["description"]
    assert feed_v2["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith(
        "/PublicVideoLocalizationOperationFeedV2"
    )
    assert "`operation_id`" in get_operation["description"]
    assert "`success`" in get_operation["description"]
    assert "`cancelled`" in get_operation["description"]
    assert (
        "`semantic-tts-grouping-workflow-v2`"
        in get_operation["description"]
    )
    assert (
        "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED"
        in get_operation["description"]
    )
    assert "不会静默退回" in get_operation["description"]
    assert get_development_asr_result["summary"] == "获取开发单步的完整原始听写结果"
    assert "`asr-raw-v2`" in get_development_asr_result["description"]
    assert "不能指定或读取任意本地文件" in get_development_asr_result["description"]
    assert get_development_diarization_result["summary"] == "获取开发单步的完整说话人区分结果"
    assert "`speaker-diarization-v1`" in get_development_diarization_result["description"]
    assert "不需要 ASR 文字或时间戳" in get_development_diarization_result["description"]
    assert get_document_understanding_result["summary"] == "查看全文理解开发结果"
    assert "不接受本地" in get_document_understanding_result["description"]
    assert "不会联网查询" in get_document_understanding_result["description"]
    assert get_visual_evidence_result["summary"] == "查看画面取证开发结果"
    assert "不会根据长相识别人" in get_visual_evidence_result["description"]
    assert "安全跳过" in get_visual_evidence_result["description"]
    assert get_visual_evidence_frame["summary"] == "查看画面取证截图"
    assert "任意本地文件" in get_visual_evidence_frame["description"]
    assert "`vocals`" in stem_audio["description"]
    assert "`background`" in stem_audio["description"]
    assert typed_asr["summary"] == "启动听写工作流"
    request_schema = typed_asr["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"].endswith("/VideoLocalizationAsrOperationRequest")
    typed_request = schema["components"]["schemas"][
        "VideoLocalizationAsrOperationRequest"
    ]
    assert typed_request["properties"]["execution_mode"]["enum"] == [
        "full",
        "development_target",
    ]
    target_steps = typed_request["properties"][
        "development_target_step_id"
    ]["anyOf"][0]["enum"]
    assert "review_decisions_r1" in target_steps
    assert "whole_recheck_r1" in target_steps
    assert "transcript_quality_gate" in target_steps
    assert not any(step_id.endswith("_r2") for step_id in target_steps)
    assert "stop_after_step" not in typed_request["properties"]
    assert "input_whole_recheck_operation_id" not in typed_request["properties"]
    assert "input_section_review_operation_id" not in typed_request["properties"]
    assert (
        get_review_decisions_result["summary"]
        == "查看第 1 轮修改汇总开发结果"
    )
    assert "完整字幕快照" in get_review_decisions_result["description"]
    assert get_review_decisions_result["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/AsrReviewDecisionsResult")
    assert (
        get_whole_recheck_result["summary"]
        == "查看第 1 轮全文复核开发结果"
    )
    assert "只读" in get_whole_recheck_result["description"]
    assert get_whole_recheck_result["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/AsrWholeRecheckResult")
    assert (
        get_transcript_quality_gate_result["summary"]
        == "查看进入校时前检查开发结果"
    )
    assert "不调用语言模型" in get_transcript_quality_gate_result["description"]
    assert "不阻断正式流程" in get_transcript_quality_gate_result["description"]
    assert "技术或结构" in get_transcript_quality_gate_result["description"]
    assert get_transcript_quality_gate_result["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/AsrTranscriptQualityGateResult")
    assert asr_workflow["summary"] == "查看听写工作流结构"
    assert typed_localization["summary"] == "启动本土化工作流"
    assert "全文本土化" in typed_localization["description"]
    assert "development_target" in typed_localization["description"]
    localization_request = schema["components"]["schemas"][
        "VideoLocalizationLocalizationOperationRequest"
    ]
    assert "execution_mode" in localization_request["properties"]
    assert (
        "development_target_step_id"
        in localization_request["properties"]
    )
    assert "development_session_id" in localization_request["properties"]
    assert "stop_after_step" not in localization_request["properties"]
    assert {
        "source_language",
        "target_language",
        "profile_id",
        "localization_requirements_id",
    }.issubset(localization_request["properties"])
    assert localization_workflow["summary"] == "查看本土化工作流结构"
    assert "当前本土化工作流" in localization_workflow["description"]
    assert "原子子任务" in asr_workflow["description"]
    atomic_task_schema = schema["components"]["schemas"][
        "WorkflowAtomicTaskDefinition"
    ]
    dependency_mode = atomic_task_schema["properties"]["dependency_mode"]
    assert dependency_mode["enum"] == ["all", "latest_completed"]
    assert "最后一个实际完成" in dependency_mode["description"]
    optional = atomic_task_schema["properties"]["optional"]
    assert optional["default"] is False
    assert "按需执行" in optional["description"]


def test_docs_page_is_chinese_localized_and_uses_pinned_assets() -> None:
    response = TestClient(app).get("/docs")

    assert response.status_code == 200
    assert '<html lang="zh-CN">' in response.text
    assert "<title>Voice Studio 接口文档</title>" in response.text
    assert f"swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui-bundle.js" in response.text
    assert f"swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css" in response.text
    assert '"docExpansion": "none"' in response.text
    assert '"filter": true' in response.text
    assert '["Try it out", "调试接口"]' in response.text
    assert '["Execute", "发送请求"]' in response.text
    assert '["Expand operation", "展开接口"]' in response.text
    assert 'record.type === "attributes"' in response.text
    assert 'attributeFilter: ["aria-label", "title", "placeholder"]' in response.text
    assert '["Filter by tag", "按功能筛选"]' in response.text
    assert '".opblock-summary-path"' in response.text


def test_preview_cache_openapi_uses_versioned_path_safe_status_contract() -> None:
    schema = app.openapi()
    endpoint = schema["paths"][
        "/api/projects/{project_id}/video-localization/source-media/preview-cache"
    ]["get"]
    response = endpoint["responses"]["200"]["content"]["application/json"]["schema"]
    status = schema["components"]["schemas"]["PreviewCacheStatus"]

    assert response["$ref"].endswith("/PreviewCacheStatus")
    assert status["properties"]["contract_version"]["const"] == "video-preview-cache-status-v1"
    assert "failed" in status["properties"]["state"]["enum"]
    assert "preparing_proxy" not in status["properties"]["phase"]["enum"]
    assert "active_chunk" in status["properties"]
    assert "retryable" in status["properties"]
    assert "cache_path" not in status["properties"]


def test_video_localization_has_only_one_media_render_command() -> None:
    schema = app.openapi()
    render_path = (
        "/api/projects/{project_id}/video-localization/export/render"
    )
    assert set(schema["paths"][render_path]) == {"post"}
    assert (
        "/api/projects/{project_id}/video-localization/"
        "export/timeline/audio-package"
    ) not in schema["paths"]
    assert (
        "/api/projects/{project_id}/video-localization/"
        "export/timeline/video"
    ) not in schema["paths"]


def test_video_localization_storage_repair_is_an_explicit_command() -> None:
    schema = app.openapi()
    path = (
        "/api/projects/{project_id}/video-localization/repair-storage"
    )
    operations = schema["paths"][path]

    assert set(operations) == {"post"}
    assert operations["post"]["summary"] == "修复视频本土化项目存储"
    assert "普通 GET 不会执行" in operations["post"]["description"]


def test_video_localization_has_only_current_heavy_operation_routes() -> None:
    schema = app.openapi()
    removed_paths = (
        "/api/projects/{project_id}/video-localization/source-audio",
        "/api/projects/{project_id}/video-localization/stems",
        "/api/projects/{project_id}/video-localization/asr/en",
        "/api/projects/{project_id}/video-localization/reference-clips",
        "/api/projects/{project_id}/video-localization/reference-clips/from-selection",
        "/api/projects/{project_id}/video-localization/reference-clips/{reference_clip_id}",
        "/api/projects/{project_id}/video-localization/reference-clips/{reference_clip_id}/cover",
        "/api/projects/{project_id}/video-localization/operations/feed",
        "/api/projects/{project_id}/video-localization/operations/summaries",
    )

    assert all(path not in schema["paths"] for path in removed_paths)


def test_docs_route_does_not_pollute_openapi_contract() -> None:
    assert "/docs" not in app.openapi()["paths"]
