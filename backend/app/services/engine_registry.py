from __future__ import annotations

import threading
import time
from functools import lru_cache
from typing import Any, Callable

from app.schemas.voice_studio import EngineDetail, EngineSpeaker, EngineStatus
from app.services import cosyvoice_worker, doubao_speaker_catalog_store, engine_health, engine_manifests, engine_provider, engine_runner, f5_worker

_engine_state_lock = threading.Lock()


def _speaker_option_to_detail(option: dict[str, str]) -> EngineSpeaker:
    value = str(option.get("value") or "")
    label = str(option.get("label") or value)
    name = label.replace(value, "", 1).strip(" -·")
    return EngineSpeaker(speaker_id=value, name=name or value, label=label)


def _doubao_speaker_catalog() -> list[EngineSpeaker]:
    return doubao_speaker_catalog_store.list_speakers()


def _filter_speakers(
    speakers: list[EngineSpeaker],
    *,
    query: str,
    gender: str,
    limit: int,
) -> list[EngineSpeaker]:
    if gender in {"F", "M"}:
        speakers = [speaker for speaker in speakers if speaker.gender.upper() == gender]
    if query:
        speakers = [
            speaker
            for speaker in speakers
            if query
            in " ".join(
                [
                    speaker.speaker_id,
                    speaker.name,
                    speaker.gender,
                    speaker.description,
                    speaker.label,
                    *speaker.languages,
                    *speaker.emotions,
                    *speaker.categories,
                    *speaker.normal_labels,
                    *speaker.special_labels,
                ]
            ).lower()
        ]
    return speakers[:limit]


@lru_cache(maxsize=1)
def _emotivoice_speaker_catalog() -> list[EngineSpeaker]:
    try:
        readme = _external_engine_root("emotivoice") / "data" / "youdao" / "text" / "README.md"
    except RuntimeError:
        return [_speaker_option_to_detail(option) for option in engine_manifests.EMOTIVOICE_SPEAKERS]
    if not readme.exists():
        return [_speaker_option_to_detail(option) for option in engine_manifests.EMOTIVOICE_SPEAKERS]

    speakers: list[EngineSpeaker] = []
    for line in readme.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        speaker_id, name, gender, description = cells[:4]
        gender_label = {"F": "女声", "M": "男声"}.get(gender, gender)
        label_parts = [speaker_id, name]
        if gender_label:
            label_parts.append(gender_label)
        if description:
            label_parts.append(f"· {description}")
        speakers.append(
            EngineSpeaker(
                speaker_id=speaker_id,
                name=name,
                gender=gender,
                description=description,
                label=" ".join(label_parts),
            )
        )
    return speakers or [_speaker_option_to_detail(option) for option in engine_manifests.EMOTIVOICE_SPEAKERS]


def list_speakers(engine_id: str, query: str = "", gender: str = "all", limit: int = 80) -> list[EngineSpeaker]:
    engine_id = _resolve_engine_id(engine_id)
    limit = max(1, min(limit, 5000))
    normalized_query = query.strip().lower()
    normalized_gender = gender.strip().upper()

    if engine_id == "emotivoice":
        return _filter_speakers(
            _emotivoice_speaker_catalog(),
            query=normalized_query,
            gender=normalized_gender,
            limit=limit,
        )

    if engine_id == "doubao-tts-preset":
        return _filter_speakers(
            _doubao_speaker_catalog(),
            query=normalized_query,
            gender=normalized_gender,
            limit=limit,
        )

    detail = get_engine(engine_id)
    if not detail:
        return []
    speaker_param = next((param for param in detail.manifest.parameter_schema if param.key == "speaker_id"), None)
    return [_speaker_option_to_detail(option) for option in (speaker_param.options if speaker_param else [])][:limit]


_ENGINES = engine_manifests.ENGINES
_external_engine_root = engine_health.external_engine_root


def _resolve_engine_id(engine_id: str) -> str:
    return engine_provider.resolve_engine_id(engine_id)


def list_engines() -> list[EngineDetail]:
    return engine_provider.list_engine_details()


def get_engine(engine_id: str) -> EngineDetail | None:
    return engine_provider.get_engine_detail(engine_id)


def health_check(engine_id: str) -> dict[str, Any]:
    provider = engine_provider.get_provider(engine_id)
    if not provider:
        return {"healthy": False, "status": "not_found"}
    return provider.health_check()


def start_engine(engine_id: str) -> EngineDetail:
    engine_id = _resolve_engine_id(engine_id)
    with _engine_state_lock:
        detail = _ENGINES[engine_id]
        detail.state.status = EngineStatus.loading
    hc = health_check(engine_id)
    with _engine_state_lock:
        if not hc.get("healthy"):
            detail.state.status = EngineStatus.error
            detail.state.error_message = str(hc)
            return detail
        detail.state.status = EngineStatus.loaded
        detail.state.model_path = hc.get("model_path") or hc.get("base_url")
        detail.state.error_message = None
        detail.state.loaded_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        return detail


def stop_engine(engine_id: str) -> EngineDetail:
    engine_id = _resolve_engine_id(engine_id)
    engine_runner.stop_persistent_worker(engine_id)
    with _engine_state_lock:
        detail = _ENGINES[engine_id]
        detail.state.status = EngineStatus.stopped
        detail.state.error_message = None
        return detail


def ensure_loaded(engine_id: str) -> None:
    engine_id = _resolve_engine_id(engine_id)
    detail = _ENGINES.get(engine_id)
    if not detail:
        raise ValueError(f"Unknown engine: {engine_id}")
    if detail.state.status != EngineStatus.loaded:
        start_engine(engine_id)
    if detail.state.status != EngineStatus.loaded:
        raise RuntimeError(detail.state.error_message or f"Engine {engine_id} is not available")


def run_isolated(
    engine_id: str,
    kwargs: dict[str, Any],
    timeout: int = 900,
    cancel_check: Callable[[], bool] | None = None,
    on_tick: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    return engine_runner.run_isolated(
        engine_id,
        kwargs,
        timeout=timeout,
        cancel_check=cancel_check,
        on_tick=on_tick,
    )


def shutdown_workers() -> None:
    engine_runner.shutdown_workers()
