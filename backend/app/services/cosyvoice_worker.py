from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.services.persistent_worker import PersistentWorker


MODEL_DIRECTORY_NAMES = {
    "cosyvoice-sft": "CosyVoice-300M-SFT",
    "cosyvoice-zero-shot": "CosyVoice-300M",
}
_COMMON_REQUIRED_MODEL_FILES = (
    "cosyvoice.yaml",
    "llm.pt",
    "flow.pt",
    "hift.pt",
    "campplus.onnx",
    "speech_tokenizer_v1.onnx",
)
REQUIRED_MODEL_FILES = {
    "cosyvoice-sft": (*_COMMON_REQUIRED_MODEL_FILES, "spk2info.pt"),
    "cosyvoice-zero-shot": _COMMON_REQUIRED_MODEL_FILES,
}


def model_directory(root: Path, engine_id: str) -> Path:
    try:
        directory_name = MODEL_DIRECTORY_NAMES[engine_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported CosyVoice engine: {engine_id}") from exc
    return root / "pretrained_models" / directory_name


def required_model_files(engine_id: str) -> tuple[str, ...]:
    try:
        return REQUIRED_MODEL_FILES[engine_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported CosyVoice engine: {engine_id}") from exc


_worker = PersistentWorker(
    log_name="cosyvoice-worker.log",
    error_prefix="CosyVoice",
    pythonpath_from_root=lambda root: str(root),
    worker_script=r"""
import contextlib
import gc
import json
import sys
import traceback
from pathlib import Path

root = Path(sys.argv[1])
model_directory_names = {
    "cosyvoice-sft": "CosyVoice-300M-SFT",
    "cosyvoice-zero-shot": "CosyVoice-300M",
}

try:
    sys.path.insert(0, ".")
    sys.path.append("third_party/Matcha-TTS")
    import torch
    import torchaudio
    with contextlib.redirect_stdout(sys.stderr):
        from cosyvoice.cli.cosyvoice import AutoModel
    print(json.dumps({"ready": True}, ensure_ascii=False), flush=True)
except Exception as exc:
    print(json.dumps({"ready": False, "error": str(exc), "traceback": traceback.format_exc()[-2000:]}, ensure_ascii=False), flush=True)
    raise SystemExit(1)

model = None
active_engine_id = None
active_speakers = []

def load_model(engine_id, requested_model_dir):
    global model, active_engine_id, active_speakers
    if engine_id not in model_directory_names:
        raise RuntimeError(f"Unsupported CosyVoice engine: {engine_id}")
    expected_model_dir = root / "pretrained_models" / model_directory_names[engine_id]
    if Path(requested_model_dir).resolve() != expected_model_dir.resolve():
        raise RuntimeError(
            f"CosyVoice model path mismatch for {engine_id}: expected {expected_model_dir}, got {requested_model_dir}"
        )
    if active_engine_id == engine_id and model is not None:
        return model, active_speakers
    model = None
    active_engine_id = None
    active_speakers = []
    gc.collect()
    if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
        torch.mps.empty_cache()
    next_model = AutoModel(model_dir=str(expected_model_dir))
    next_speakers = next_model.list_available_spks() if engine_id == "cosyvoice-sft" else []
    model = next_model
    active_engine_id = engine_id
    active_speakers = next_speakers
    return model, active_speakers

def audio_meta(path, sample_rate):
    try:
        import soundfile as sf
        info = sf.info(path)
        return {"duration_ms": int(info.frames / info.samplerate * 1000), "sample_rate": info.samplerate}
    except Exception:
        return {"duration_ms": None, "sample_rate": sample_rate}

def collect_speech(generator):
    chunks = []
    for item in generator:
        speech = item["tts_speech"].detach().cpu()
        if speech.ndim == 1:
            speech = speech.unsqueeze(0)
        chunks.append(speech)
    if not chunks:
        raise RuntimeError("CosyVoice returned no audio")
    if len(chunks) == 1:
        return chunks[0], 1
    return torch.cat(chunks, dim=1), len(chunks)

def speaker_not_found_error(speaker_id, available_speakers):
    available = [str(item).strip() for item in available_speakers if str(item).strip()]
    preview = "、".join(available[:12]) or "无"
    if len(available) > 12:
        preview += " 等"
    return (
        f"COSYVOICE_SPEAKER_NOT_FOUND: 未找到官方预置音色“{speaker_id}”。"
        f"请从音色列表中重新选择；当前模型可用音色：{preview}。"
    )

for line in sys.stdin:
    try:
        payload = json.loads(line)
        engine_id = payload["engine_id"]
        output_path = payload["output_path"]
        text = payload["text"].strip()
        speed = float(payload.get("speed") or 1.0)
        if not text:
            raise RuntimeError("Text is empty")
        with contextlib.redirect_stdout(sys.stderr):
            model, speakers = load_model(engine_id, payload["model_dir"])
            if engine_id == "cosyvoice-sft":
                speaker_id = str(payload.get("speaker_id") or "中文女").strip() or "中文女"
                if speaker_id not in speakers:
                    raise RuntimeError(speaker_not_found_error(speaker_id, speakers))
                speech, chunk_count = collect_speech(model.inference_sft(text, speaker_id, stream=False, speed=speed))
            elif engine_id == "cosyvoice-zero-shot":
                reference_audio = payload.get("reference_audio")
                ref_text = (payload.get("ref_text") or "").strip()
                if not reference_audio:
                    raise RuntimeError("REFERENCE_AUDIO_REQUIRED")
                if not ref_text:
                    raise RuntimeError("REFERENCE_TEXT_REQUIRED")
                speech, chunk_count = collect_speech(model.inference_zero_shot(text, ref_text, reference_audio, stream=False, speed=speed))
            else:
                raise RuntimeError(f"Unsupported CosyVoice engine: {engine_id}")
            torchaudio.save(output_path, speech, model.sample_rate)
        result = {"output_path": output_path, "chunk_count": chunk_count, **audio_meta(output_path, model.sample_rate)}
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "traceback": traceback.format_exc()[-2000:]}, ensure_ascii=False), flush=True)
""",
)


def run(
    engine_id: str,
    kwargs: dict[str, Any],
    *,
    root: Path,
    python: str,
    timeout: int,
    cancel_check: Callable[[], bool] | None = None,
    on_tick: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    payload = {
        **kwargs,
        "engine_id": engine_id,
        "model_dir": str(model_directory(root, engine_id)),
    }
    return _worker.run(payload, root=root, python=python, timeout=timeout, cancel_check=cancel_check, on_tick=on_tick)


def shutdown() -> None:
    _worker.shutdown()
