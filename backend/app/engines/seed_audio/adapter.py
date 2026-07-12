from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .schemas import (
    SeedAudioAIGCMetadata,
    SeedAudioAudioConfig,
    SeedAudioReference,
    SeedAudioRequest,
    SeedAudioWatermark,
)
from .assets import SeedAudioAssetError, SeedAudioAssetResolver
from .validation import SeedAudioValidationError, validate_prompt_references, validate_reference_constraints


_CLOUD_ALLOWED_LICENSES = frozenset({"self_voice", "authorized", "company_authorized"})


class SeedAudioAdapter:
    engine_id = "doubao-seed-audio-1.0"

    def validate_request(self, request: SeedAudioRequest) -> None:
        validate_reference_constraints(request.references)
        if request.input_mode == "audio":
            validate_prompt_references(
                request.text_prompt,
                reference_count=len(request.references),
            ).raise_for_invalid()

    def build_payload(self, request: SeedAudioRequest) -> dict[str, Any]:
        self.validate_request(request)
        payload: dict[str, Any] = {
            "model": request.model,
            "text_prompt": request.text_prompt,
            "audio_config": request.audio_config.model_dump(),
        }
        if request.references:
            payload["references"] = [reference.to_api_reference() for reference in request.references]
        if request.watermark and request.watermark.enabled:
            payload["watermark"] = request.watermark.model_dump(exclude_none=True)
        return payload

    def from_generate_request(self, request: Any) -> SeedAudioRequest:
        seed_request, _summaries = self.resolve_generate_request(request)
        return seed_request

    def resolve_generate_request(
        self,
        request: Any,
        *,
        asset_resolver: SeedAudioAssetResolver | None = None,
        upload_confirmation_required: bool = True,
    ) -> tuple[SeedAudioRequest, list[dict[str, Any]]]:
        if getattr(request, "engine_id", None) != self.engine_id:
            raise SeedAudioValidationError("请求引擎与 Seed Audio Adapter 不匹配")

        mode = getattr(request, "input_mode", None) or "text"
        assets = list(getattr(request, "input_assets", None) or [])
        if mode == "text" and assets:
            raise SeedAudioValidationError("文字描述模式不能包含参考资源")
        if mode == "audio":
            if not assets or len(assets) > 3:
                raise SeedAudioValidationError("参考声音模式需要 1 到 3 条音频参考")
            if any(getattr(asset, "type", None) not in {"audio", "speaker"} for asset in assets):
                raise SeedAudioValidationError("参考声音模式只能包含音频参考")
        if mode == "image" and (len(assets) != 1 or getattr(assets[0], "type", None) != "image"):
            raise SeedAudioValidationError("参考图片模式只能包含一张图片")

        parameters = dict(getattr(request, "engine_parameters", None) or {})
        confirm_upload = parameters.pop("confirm_upload", False)
        if not isinstance(confirm_upload, bool):
            raise SeedAudioValidationError("confirm_upload 必须是布尔值")
        resolver = asset_resolver or SeedAudioAssetResolver()
        references: list[SeedAudioReference] = []
        summaries: list[dict[str, Any]] = []
        for index, asset in enumerate(assets, start=1):
            asset_type = getattr(asset, "type", None)
            source = _enum_value(getattr(asset, "source", None))
            asset_id = str(getattr(asset, "asset_id", "") or "")
            if asset_type == "speaker":
                if source != "cloud_speaker" or not getattr(asset, "speaker_id", None):
                    raise SeedAudioAssetError("INVALID_CLOUD_SPEAKER", "云端声音素材必须提供有效 speaker_id")
                references.append(SeedAudioReference(speaker=asset.speaker_id))
                summaries.append(
                    {
                        "asset_id": asset_id,
                        "source": "cloud_speaker",
                        "media_kind": "speaker",
                        "file_id": None,
                        "voice_id": None,
                        "speaker_id": asset.speaker_id,
                        "name": getattr(asset, "display_name", None) or asset.speaker_id,
                        "reference_index": index,
                        "reference_token": f"@音频{index}",
                    }
                )
                continue

            if upload_confirmation_required and not confirm_upload:
                raise SeedAudioAssetError("UPLOAD_CONFIRM_REQUIRED", "请确认上传参考素材到豆包云端")

            file_id = _managed_file_id(asset)
            if asset_type == "audio" and source == "voice_library":
                voice_id = str(getattr(asset, "voice_id", "") or "")
                if not voice_id:
                    raise SeedAudioAssetError("VOICE_ID_REQUIRED", "音色库参考声音必须提供 voice_id")
                managed = resolver.resolve_voice_audio(voice_id=voice_id, file_id=file_id)
            elif asset_type == "audio" and source == "upload":
                _validate_asset_license(asset)
                managed = resolver.resolve_upload(
                    file_id=file_id,
                    media_kind="audio",
                    authorized=True,
                    source="upload",
                )
            elif asset_type == "image" and source in {"upload", "preset"}:
                _validate_asset_license(asset)
                managed = resolver.resolve_upload(
                    file_id=file_id,
                    media_kind="image",
                    authorized=True,
                    source=source,
                )
            else:
                raise SeedAudioAssetError("UNSUPPORTED_ASSET_SOURCE", "当前模式不支持该素材来源")

            references.append(managed.build_reference())
            summary = managed.history_summary()
            summary.update(
                {
                    "asset_id": asset_id,
                    "reference_index": index,
                    "reference_token": f"@音频{index}" if mode == "audio" else None,
                }
            )
            summaries.append(summary)

        watermark_keys = {
            "aigc_watermark",
            "aigc_metadata_enable",
            "content_producer",
            "produce_id",
            "content_propagator",
            "propagate_id",
        }
        watermark_values = {key: parameters.pop(key) for key in list(parameters) if key in watermark_keys}
        if isinstance(parameters.get("sample_rate"), str) and parameters["sample_rate"].isdigit():
            parameters["sample_rate"] = int(parameters["sample_rate"])
        audio_config = SeedAudioAudioConfig(**parameters)

        metadata_fields = {
            key: watermark_values.get(key)
            for key in ("content_producer", "produce_id", "content_propagator", "propagate_id")
            if watermark_values.get(key) not in (None, "")
        }
        metadata_enabled = bool(watermark_values.get("aigc_metadata_enable"))
        watermark = None
        if watermark_values.get("aigc_watermark") or metadata_enabled:
            watermark = SeedAudioWatermark(
                aigc_watermark=bool(watermark_values.get("aigc_watermark")),
                aigc_metadata=SeedAudioAIGCMetadata(enable=metadata_enabled, **metadata_fields),
            )

        seed_request = SeedAudioRequest(
            input_mode=mode,
            text_prompt=getattr(request, "text", ""),
            references=references,
            audio_config=audio_config,
            watermark=watermark,
        )
        self.validate_request(seed_request)
        return seed_request, summaries

    def execute(self, request: Any, **context: Any) -> dict[str, Any]:
        from .client import SeedAudioClient, urllib_json_transport

        started = time.monotonic()
        seed_request = context.get("prepared_request")
        asset_summaries = context.get("asset_summaries")
        if not isinstance(seed_request, SeedAudioRequest) or not isinstance(asset_summaries, list):
            seed_request, asset_summaries = self.resolve_generate_request(
                request,
                asset_resolver=context.get("asset_resolver"),
                upload_confirmation_required=bool(context.get("upload_confirmation_required", True)),
            )
        transport = context.get("transport") or urllib_json_transport
        client = SeedAudioClient(
            api_key=str(context.get("api_key") or ""),
            base_url=str(context.get("base_url") or ""),
            transport=transport,
            adapter=self,
            allow_test_host=bool(context.get("allow_test_host", False)),
        )
        result = client.create_and_save(
            seed_request,
            output_dir=Path(context["output_dir"]),
            output_name=str(context["output_name"]),
            request_id=context.get("request_id"),
            timeout=float(context.get("timeout") or 300),
            cancel_check=context.get("cancel_check"),
        )
        duration_ms = int(round(result.duration * 1000)) if result.duration is not None else None
        original_duration_ms = (
            int(round(result.original_duration * 1000)) if result.original_duration is not None else None
        )
        return {
            "output_path": result.output_path,
            "duration_ms": duration_ms,
            "generation_time_ms": max(0, int(round((time.monotonic() - started) * 1000))),
            "provider_request_id": result.request_id,
            "provider_log_id": result.logid,
            "original_duration_ms": original_duration_ms,
            "subtitle": result.subtitle,
            "audio_bytes": result.audio_bytes,
            "response_source": result.source,
            "asset_summaries": asset_summaries,
        }


def _managed_file_id(asset: Any) -> str:
    value = (
        getattr(asset, "clip_file_id", None)
        or getattr(asset, "file_id", None)
        or getattr(asset, "source_file_id", None)
    )
    if not value:
        raise SeedAudioAssetError("FILE_ID_REQUIRED", "参考素材必须提供受管理的 file_id")
    return str(value)


def _validate_asset_license(asset: Any) -> None:
    license_status = _enum_value(getattr(asset, "license_status", None))
    if license_status not in _CLOUD_ALLOWED_LICENSES:
        raise SeedAudioAssetError("ASSET_UPLOAD_NOT_AUTHORIZED", "该素材未授权上传到云端")


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")
