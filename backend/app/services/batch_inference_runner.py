from __future__ import annotations

import json
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _audio_meta(path: str, sample_rate: int) -> dict[str, Any]:
    try:
        import soundfile as sf

        info = sf.info(path)
        return {"duration_ms": int(info.frames / info.samplerate * 1000), "sample_rate": info.samplerate}
    except Exception:
        return {"duration_ms": None, "sample_rate": sample_rate}


def _target_path(output_path: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _finalize_wav(wav_path: Path, output_path: Path, sample_rate: int) -> dict[str, Any]:
    if output_path.suffix.lower() == ".wav":
        final = wav_path
    else:
        from app.services import audio_tools

        audio_tools.copy_or_convert(wav_path, output_path, output_path.suffix.lstrip(".") or "mp3")
        final = output_path
        try:
            wav_path.unlink()
        except OSError:
            pass
    meta = _audio_meta(str(final), sample_rate)
    meta["output_path"] = str(final)
    return meta


def run_indextts_v2(payload: dict[str, Any]) -> list[dict[str, Any]]:
    from mlx_indextts.generate_v2 import IndexTTSv2

    allowed_keys = {
        "reference_audio",
        "emotion_reference_audio",
        "max_mel_tokens",
        "max_text_tokens_per_segment",
        "interval_silence",
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "diffusion_steps",
        "cfg_rate",
        "emotion",
        "emo_alpha",
        "seed",
        "verbose",
        "segment_overlap_ms",
        "speed",
    }
    common = dict(payload["common"])
    model_dir = common.pop("model_dir")
    common = {key: value for key, value in common.items() if key in allowed_keys and value is not None}
    model = IndexTTSv2(model_dir, device=common.pop("device", "mps"))
    results = []
    for segment in payload["segments"]:
        started = time.perf_counter()
        out = _target_path(segment["output_path"])
        wav_out = out if out.suffix.lower() == ".wav" else out.with_suffix(".batch-tmp.wav")
        kwargs = dict(common)
        for key, value in segment.get("parameters", {}).items():
            if key not in allowed_keys:
                continue
            if value is None:
                kwargs.pop(key, None)
            else:
                kwargs[key] = value
        kwargs["text"] = segment["text"]
        try:
            model.generate(output_path=str(wav_out), **kwargs)
            meta = _finalize_wav(wav_out, out, 22050)
            meta["generation_time_ms"] = int((time.perf_counter() - started) * 1000)
            results.append({"segment_id": segment["segment_id"], "status": "success", **meta})
        except Exception as exc:
            results.append({"segment_id": segment["segment_id"], "status": "failed", "error_message": str(exc)})
    return results


def run_omnivoice(payload: dict[str, Any]) -> list[dict[str, Any]]:
    import numpy as np
    import soundfile as sf
    from app.services.inference_runner import omnivoice_local_snapshot
    from omnivoice import OmniVoice
    from omnivoice.models.omnivoice import OmniVoiceGenerationConfig

    common = dict(payload["common"])
    model = OmniVoice.from_pretrained(str(omnivoice_local_snapshot()), device_map=common.pop("device", "mps"))
    results = []
    for segment in payload["segments"]:
        started = time.perf_counter()
        out = _target_path(segment["output_path"])
        wav_out = out if out.suffix.lower() == ".wav" else out.with_suffix(".batch-tmp.wav")
        kwargs = dict(common)
        kwargs.update({k: v for k, v in segment.get("parameters", {}).items() if v is not None})
        gen_kwargs = {"text": segment["text"]}
        if kwargs.get("language") and kwargs["language"] != "auto":
            gen_kwargs["language"] = kwargs["language"]
        if kwargs.get("reference_audio"):
            gen_kwargs["ref_audio"] = kwargs["reference_audio"]
            if kwargs.get("ref_text") is not None:
                gen_kwargs["ref_text"] = kwargs["ref_text"]
        elif kwargs.get("emotion_text") or kwargs.get("emotion"):
            gen_kwargs["instruct"] = kwargs.get("emotion_text") or kwargs.get("emotion")
        target_duration = float(kwargs["duration"]) if kwargs.get("duration") and float(kwargs["duration"]) > 0 else None
        postprocess_speed = 1.0 if target_duration else float(kwargs.get("speed") or 1.0)
        generation_config = OmniVoiceGenerationConfig.from_dict(
            {
                key: value
                for key, value in {
                    "num_step": kwargs.get("diffusion_steps"),
                    "guidance_scale": kwargs.get("guidance_scale"),
                    "audio_chunk_duration": kwargs.get("audio_chunk_duration"),
                    "audio_chunk_threshold": kwargs.get("audio_chunk_threshold"),
                }.items()
                if value is not None
            }
        )
        gen_kwargs["generation_config"] = generation_config
        try:
            result = model.generate(**gen_kwargs)
            if isinstance(result, (str, Path)):
                shutil.copy2(str(result), wav_out)
            else:
                audio = np.concatenate([np.asarray(x).reshape(-1) for x in result]).astype(np.float32)
                sf.write(wav_out, np.clip(audio, -1, 1), getattr(model, "sampling_rate", 24000), subtype="PCM_16")
            if target_duration:
                from app.services import audio_tools

                current = audio_tools.probe_audio(wav_out)
                current_seconds = (current.get("duration_ms") or 0) / 1000
                if current_seconds > 0:
                    audio_tools.time_stretch_file(wav_out, current_seconds / target_duration)
            elif postprocess_speed != 1.0:
                from app.services import audio_tools

                audio_tools.time_stretch_file(wav_out, postprocess_speed)
            meta = _finalize_wav(wav_out, out, getattr(model, "sampling_rate", 24000))
            meta["generation_time_ms"] = int((time.perf_counter() - started) * 1000)
            results.append({"segment_id": segment["segment_id"], "status": "success", **meta})
        except Exception as exc:
            results.append({"segment_id": segment["segment_id"], "status": "failed", "error_message": str(exc)})
    return results


def run_mimo_tts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    from app.services import mimo_client

    common = dict(payload["common"])
    results = []
    for segment in payload["segments"]:
        started = time.perf_counter()
        out = _target_path(segment["output_path"])
        kwargs = dict(common)
        kwargs.update({k: v for k, v in segment.get("parameters", {}).items() if v is not None})
        try:
            mimo_client.generate_tts(
                base_url=kwargs["base_url"],
                api_key=kwargs["api_key"],
                text=segment["text"],
                output_path=str(out),
                model=kwargs.get("model", "mimo-v2.5-tts"),
                voice=kwargs.get("mimo_voice") or kwargs.get("voice") or "mimo_default",
                instruction=kwargs.get("style_instruction") or kwargs.get("emotion_text") or kwargs.get("emotion"),
                voice_design_prompt=kwargs.get("voice_design_prompt"),
                optimize_text_preview=bool(kwargs.get("optimize_text_preview")),
                reference_audio_path=kwargs.get("reference_audio") or kwargs.get("reference_audio_path"),
                temperature=kwargs.get("temperature"),
                top_p=kwargs.get("top_p"),
                audio_format=out.suffix.lstrip(".") or "mp3",
            )
            meta = _audio_meta(str(out), 24000)
            meta.update({"output_path": str(out), "generation_time_ms": int((time.perf_counter() - started) * 1000)})
            results.append({"segment_id": segment["segment_id"], "status": "success", **meta})
        except Exception as exc:
            results.append({"segment_id": segment["segment_id"], "status": "failed", "error_message": str(exc)})
    return results


def run_doubao_tts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    from app.services import doubao_client

    common = dict(payload["common"])
    results = []
    for segment in payload["segments"]:
        started = time.perf_counter()
        out = _target_path(segment["output_path"])
        kwargs = dict(common)
        kwargs.update({k: v for k, v in segment.get("parameters", {}).items() if v is not None})
        requested_format = out.suffix.lower().lstrip(".") or "mp3"
        provider_format = requested_format if requested_format in {"wav", "mp3", "pcm", "ogg_opus"} else "wav"
        provider_out = out if provider_format == requested_format else out.with_suffix(".doubao-tmp.wav")
        try:
            doubao_client.generate_tts_unidirectional_http(
                base_url=kwargs["base_url"],
                api_key=kwargs["api_key"],
                text=segment["text"],
                output_path=str(provider_out),
                speaker=kwargs.get("speaker") or kwargs.get("speaker_id") or "zh_female_vv_uranus_bigtts",
                resource_id=kwargs.get("resource_id") or "seed-tts-2.0",
                style_instruction=kwargs.get("style_instruction"),
                # Same provider field as the single-task runner.  Batch jobs
                # must not silently fall back to automatic language detection.
                explicit_language=kwargs.get("explicit_language"),
                speed=kwargs.get("speed"),
                sample_rate=kwargs.get("sample_rate") or doubao_client.DEFAULT_TTS_SAMPLE_RATE,
                bit_rate=kwargs.get("bit_rate") or doubao_client.DEFAULT_TTS_BIT_RATE,
                loudness_rate=kwargs.get("loudness_rate"),
                pitch_rate=kwargs.get("pitch_rate"),
                enable_subtitle=bool(kwargs.get("enable_subtitle")),
                silence_duration=kwargs.get("silence_duration") or 0,
                aigc_watermark=bool(kwargs.get("aigc_watermark")),
                max_length_to_filter_parenthesis=kwargs.get("max_length_to_filter_parenthesis"),
                disable_markdown_filter=bool(kwargs.get("disable_markdown_filter")),
                latex_parser_mode=kwargs.get("latex_parser_mode"),
                aigc_metadata_enable=bool(kwargs.get("aigc_metadata_enable")),
                content_producer=kwargs.get("content_producer"),
                produce_id=kwargs.get("produce_id"),
                content_propagator=kwargs.get("content_propagator"),
                propagate_id=kwargs.get("propagate_id"),
                tone_fidelity=bool(kwargs.get("tone_fidelity")),
                audio_format=provider_format,
            )
            if provider_out != out:
                from app.services import audio_tools

                audio_tools.copy_or_convert(provider_out, out, requested_format)
            meta = _audio_meta(str(out), int(kwargs.get("sample_rate") or doubao_client.DEFAULT_TTS_SAMPLE_RATE))
            meta.update({"output_path": str(out), "generation_time_ms": int((time.perf_counter() - started) * 1000)})
            results.append({"segment_id": segment["segment_id"], "status": "success", **meta})
        except Exception as exc:
            results.append({"segment_id": segment["segment_id"], "status": "failed", "error_message": str(exc)})
        finally:
            if provider_out != out:
                try:
                    provider_out.unlink()
                except OSError:
                    pass
    return results


def run_external_tts(payload: dict[str, Any], runner_name: str, sample_rate: int) -> list[dict[str, Any]]:
    from app.services import inference_runner

    runner = getattr(inference_runner, runner_name)
    common = dict(payload["common"])
    results = []
    for segment in payload["segments"]:
        started = time.perf_counter()
        out = _target_path(segment["output_path"])
        kwargs = dict(common)
        kwargs.update({k: v for k, v in segment.get("parameters", {}).items() if v is not None})
        kwargs["text"] = segment["text"]
        kwargs["output_path"] = str(out if out.suffix.lower() == ".wav" else out.with_suffix(".batch-tmp.wav"))
        try:
            meta = runner(**kwargs)
            final = Path(meta["output_path"])
            if out.suffix.lower() != ".wav":
                meta = _finalize_wav(final, out, sample_rate)
            meta["generation_time_ms"] = int((time.perf_counter() - started) * 1000)
            results.append({"segment_id": segment["segment_id"], "status": "success", **meta})
        except Exception as exc:
            results.append({"segment_id": segment["segment_id"], "status": "failed", "error_message": str(exc)})
    return results


def run_emotivoice(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return run_external_tts(payload, "run_emotivoice", 16000)


def run_confucius4_mlx(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return run_external_tts(payload, "run_confucius4_mlx", 22050)


def run_qwen3_tts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return run_external_tts(payload, "run_qwen3_tts", 24000)


def run_f5_tts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return run_external_tts(payload, "run_f5_tts", 24000)


def run_cosyvoice_sft(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return run_external_tts(payload, "run_cosyvoice_sft", 22050)


def run_cosyvoice_zero_shot(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return run_external_tts(payload, "run_cosyvoice_zero_shot", 22050)


RUNNERS = {
    "indextts-v2": run_indextts_v2,
    "omnivoice": run_omnivoice,
    "emotivoice": run_emotivoice,
    "confucius4-mlx-int8": run_confucius4_mlx,
    "qwen3-tts-mlx-0.6b": run_qwen3_tts,
    "f5-tts": run_f5_tts,
    "cosyvoice-sft": run_cosyvoice_sft,
    "cosyvoice-zero-shot": run_cosyvoice_zero_shot,
    "mimo-v2.5-tts": run_mimo_tts,
    "mimo-v2.5-tts-preset": run_mimo_tts,
    "mimo-v2.5-tts-voicedesign": run_mimo_tts,
    "mimo-v2.5-tts-voiceclone": run_mimo_tts,
    "doubao-tts-preset": run_doubao_tts,
    "doubao-tts-voiceclone": run_doubao_tts,
}


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        results = RUNNERS[payload["engine_id"]](payload)
        print(json.dumps({"results": results}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "traceback": traceback.format_exc()[-3000:]}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
