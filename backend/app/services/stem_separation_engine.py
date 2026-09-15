from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import librosa
import numpy as np
import soundfile as sf

from app.errors import AppException
from app.services import settings_store, stem_separation_model


ENGINE_ID = "bs-roformer-viperx-1297:residual-v1"
OUTPUT_SAMPLE_RATE = 48_000
_CONTEXT_SECONDS = 2.0


def separate(
    audio_path: Path,
    vocals_output_path: Path,
    background_output_path: Path,
    *,
    overlap: int,
    chunk_duration_seconds: int,
) -> dict[str, Any]:
    stem_separation_model.require_model_files()
    source = Path(audio_path)
    if not source.is_file():
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING",
            "没有找到待分离的源音频",
        )
    cache_root = settings_store.cache_dir() / "stem-separation"
    cache_root.mkdir(parents=True, exist_ok=True)
    vocals_output_path.parent.mkdir(parents=True, exist_ok=True)
    background_output_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix="run-",
        dir=cache_root,
    ) as temporary:
        temporary_dir = Path(temporary)
        separator = _build_separator(
            temporary_dir,
            overlap=overlap,
        )
        separator.load_model(stem_separation_model.MODEL_FILENAME)
        try:
            _separate_streamed(
                separator,
                source,
                temporary_dir,
                vocals_output_path,
                background_output_path,
                chunk_duration_seconds=chunk_duration_seconds,
            )
        except AppException:
            raise
        except Exception as exc:
            raise AppException(
                500,
                "VIDEO_LOCALIZATION_SEPARATION_FAILED",
                "人声与背景声分离失败",
                {"reason": str(exc)[:400]},
            ) from exc
    return {
        "engine_id": ENGINE_ID,
        "model_id": stem_separation_model.ENGINE_ID,
        "sample_rate": OUTPUT_SAMPLE_RATE,
        "overlap": overlap,
        "chunk_duration_seconds": chunk_duration_seconds,
    }


def residual_background(
    original_mix: np.ndarray,
    estimated_vocals: np.ndarray,
) -> np.ndarray:
    if original_mix.shape != estimated_vocals.shape:
        raise ValueError("mix and vocals must have identical shapes")
    return original_mix.astype(np.float32) - estimated_vocals.astype(np.float32)


def _separator_class():
    try:
        from audio_separator.separator import Separator
    except ImportError as exc:
        raise AppException(
            500,
            "STEM_SEPARATION_RUNTIME_MISSING",
            "缺少 BS-RoFormer 运行环境，请重新安装 Voice Studio 视频本土化组件",
        ) from exc
    return Separator


def _build_separator(output_dir: Path, *, overlap: int):
    separator = _separator_class()(
        log_level=logging.INFO,
        model_file_dir=str(stem_separation_model.model_dir()),
        output_dir=str(output_dir),
        output_format="WAV",
        normalization_threshold=1.0,
        amplification_threshold=0.0,
        output_single_stem="Vocals",
        sample_rate=OUTPUT_SAMPLE_RATE,
        use_soundfile=True,
        chunk_duration=None,
        mdxc_params={
            "segment_size": 256,
            "override_model_segment_size": False,
            "batch_size": 1,
            "overlap": overlap,
            "pitch_shift": 0,
        },
    )
    _apply_device_setting(separator)
    return separator


def _apply_device_setting(separator) -> None:
    requested = settings_store.get().device
    if requested == "auto":
        return
    try:
        import torch
    except ImportError as exc:
        raise AppException(
            500,
            "STEM_SEPARATION_RUNTIME_MISSING",
            "缺少 PyTorch，无法运行 BS-RoFormer",
        ) from exc
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise AppException(
                409,
                "STEM_SEPARATION_CUDA_UNAVAILABLE",
                "当前电脑不可用 CUDA，请改为自动选择或 CPU",
            )
        separator.torch_device = torch.device("cuda")
        separator.torch_device_mps = None
        return
    if requested == "mps":
        if not (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            raise AppException(
                409,
                "STEM_SEPARATION_MPS_UNAVAILABLE",
                "当前电脑不可用 Apple MPS，请改为自动选择或 CPU",
            )
        separator.torch_device_mps = torch.device("mps")
        separator.torch_device = separator.torch_device_mps
        return
    separator.torch_device_cpu = torch.device("cpu")
    separator.torch_device = separator.torch_device_cpu
    separator.torch_device_mps = None
    separator.onnx_execution_provider = ["CPUExecutionProvider"]


def _separate_streamed(
    separator,
    source: Path,
    temporary_dir: Path,
    vocals_output: Path,
    background_output: Path,
    *,
    chunk_duration_seconds: int,
) -> None:
    with sf.SoundFile(source) as source_file:
        source_rate = int(source_file.samplerate)
        source_channels = min(2, int(source_file.channels))
        total_frames = int(source_file.frames)
        if total_frames <= 0:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_SOURCE_AUDIO_EMPTY",
                "源音频为空，无法分离人声和背景声",
            )
        core_frames = max(
            source_rate,
            int(chunk_duration_seconds * source_rate),
        )
        context_frames = int(_CONTEXT_SECONDS * source_rate)
        vocals_output.unlink(missing_ok=True)
        background_output.unlink(missing_ok=True)
        with sf.SoundFile(
            vocals_output,
            mode="w",
            samplerate=OUTPUT_SAMPLE_RATE,
            channels=source_channels,
            format="WAV",
            subtype="FLOAT",
        ) as vocals_writer, sf.SoundFile(
            background_output,
            mode="w",
            samplerate=OUTPUT_SAMPLE_RATE,
            channels=source_channels,
            format="WAV",
            subtype="FLOAT",
        ) as background_writer:
            core_start = 0
            chunk_index = 0
            while core_start < total_frames:
                core_end = min(total_frames, core_start + core_frames)
                read_start = max(0, core_start - context_frames)
                read_end = min(total_frames, core_end + context_frames)
                source_file.seek(read_start)
                padded_mix = source_file.read(
                    read_end - read_start,
                    dtype="float32",
                    always_2d=True,
                )[:, :source_channels]
                chunk_path = temporary_dir / f"source-{chunk_index:04d}.wav"
                sf.write(
                    chunk_path,
                    padded_mix,
                    source_rate,
                    subtype="FLOAT",
                )
                output_names = {
                    "Vocals": f"estimated-vocals-{chunk_index:04d}"
                }
                output_files = separator.separate(
                    str(chunk_path),
                    output_names,
                )
                vocals_chunk = _read_vocals_output(
                    output_files,
                    temporary_dir,
                    source_channels,
                )
                crop_start = _scaled_frame(
                    core_start - read_start,
                    source_rate,
                )
                expected_frames = _scaled_frame(
                    core_end - core_start,
                    source_rate,
                )
                vocals_core = _fit_frames(
                    vocals_chunk[crop_start:crop_start + expected_frames],
                    expected_frames,
                    source_channels,
                )
                mix_core = padded_mix[
                    core_start - read_start:core_end - read_start
                ]
                mix_core = _resample_audio(
                    mix_core,
                    source_rate,
                    OUTPUT_SAMPLE_RATE,
                )
                mix_core = _fit_frames(
                    mix_core,
                    expected_frames,
                    source_channels,
                )
                vocals_writer.write(vocals_core)
                background_writer.write(
                    residual_background(mix_core, vocals_core)
                )
                core_start = core_end
                chunk_index += 1


def _read_vocals_output(
    output_files: list[str],
    output_dir: Path,
    channels: int,
) -> np.ndarray:
    candidates = []
    for value in output_files:
        path = Path(value)
        candidates.append(path if path.is_absolute() else output_dir / path)
    candidates.extend(output_dir.glob("*Vocals*.wav"))
    candidates.extend(output_dir.glob("estimated-vocals-*.wav"))
    existing = next((path for path in candidates if path.is_file()), None)
    if existing is None:
        raise RuntimeError("分离模型没有返回人声音轨")
    audio, sample_rate = sf.read(
        existing,
        always_2d=True,
        dtype="float32",
    )
    audio = _match_channels(audio, channels)
    return _resample_audio(audio, int(sample_rate), OUTPUT_SAMPLE_RATE)


def _match_channels(audio: np.ndarray, channels: int) -> np.ndarray:
    if audio.shape[1] == channels:
        return audio
    if channels == 1:
        return audio.mean(axis=1, keepdims=True)
    if audio.shape[1] == 1:
        return np.repeat(audio, channels, axis=1)
    return audio[:, :channels]


def _resample_audio(
    audio: np.ndarray,
    source_rate: int,
    target_rate: int,
) -> np.ndarray:
    if source_rate == target_rate:
        return audio.astype(np.float32, copy=False)
    resampled = librosa.resample(
        audio.T,
        orig_sr=source_rate,
        target_sr=target_rate,
        axis=-1,
    ).T
    return resampled.astype(np.float32, copy=False)


def _scaled_frame(frames: int, source_rate: int) -> int:
    return int(round(frames * OUTPUT_SAMPLE_RATE / source_rate))


def _fit_frames(
    audio: np.ndarray,
    frames: int,
    channels: int,
) -> np.ndarray:
    audio = _match_channels(audio, channels)
    if len(audio) >= frames:
        return audio[:frames].astype(np.float32, copy=False)
    missing = frames - len(audio)
    return np.pad(audio, ((0, missing), (0, 0))).astype(np.float32)
