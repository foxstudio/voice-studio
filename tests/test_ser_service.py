"""情绪识别（SER）服务的健康检查、标签映射和子进程协议测试。

这些用例都用假 worker 和临时模型目录，不加载真实模型，也不会联网。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import ser_service


def _model_dir(tmp_path: Path, *, complete: bool = True) -> Path:
    root = tmp_path / "emotion2vec-plus-large"
    root.mkdir()
    for name in ser_service.REQUIRED_MODEL_FILES:
        (root / name).write_text("{}", encoding="utf-8")
    if not complete:
        (root / "model.pt").unlink()
    return root


def _stub_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    python = tmp_path / "runtime-python"
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(ser_service, "runtime_python", lambda: python)
    monkeypatch.setattr(ser_service, "runtime_root", lambda: tmp_path)
    return python


def _stub_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str) -> Path:
    worker = tmp_path / "fake_ser_worker.py"
    worker.write_text(source, encoding="utf-8")
    monkeypatch.setattr(ser_service, "worker_script", lambda: worker)
    monkeypatch.setattr(ser_service, "runtime_python", lambda: Path(sys.executable))
    return worker


def _audio(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"RIFF")
    return path


def test_health_check_ok_when_runtime_and_model_present(tmp_path, monkeypatch):
    model_root = _model_dir(tmp_path)
    _stub_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(ser_service, "model_path", lambda: model_root)

    health = ser_service.health_check()

    assert health["healthy"] is True
    assert health["status"] == "ok"
    assert health["missing"] == []


def test_health_check_reports_missing_model_files(tmp_path, monkeypatch):
    model_root = _model_dir(tmp_path, complete=False)
    _stub_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(ser_service, "model_path", lambda: model_root)

    health = ser_service.health_check()

    assert health["healthy"] is False
    assert health["status"] == "model_missing"
    assert health["missing"] == ["model.pt"]
    assert "model.pt" in ser_service.unavailable_detail(health)


def test_health_check_reports_missing_runtime(tmp_path, monkeypatch):
    model_root = _model_dir(tmp_path)
    monkeypatch.setattr(ser_service, "model_path", lambda: model_root)
    monkeypatch.setattr(ser_service, "runtime_root", lambda: tmp_path / "absent-runtime")
    monkeypatch.setattr(ser_service, "runtime_python", lambda: tmp_path / "absent-runtime" / "python")

    health = ser_service.health_check()

    assert health["healthy"] is False
    assert health["status"] == "runtime_missing"
    assert ser_service.unavailable_detail(health)


def test_map_scores_accepts_chinese_slash_labels():
    """emotion2vec+ 实测返回「生气/angry」这类标签，不能再被当成未知丢弃。"""
    top, raw_top, mapped = ser_service._map_scores({"生气/angry": 0.9, "中立/neutral": 0.1})

    assert raw_top == "生气/angry"
    assert top == "angry"
    assert mapped["angry"] == pytest.approx(0.9)
    assert mapped["calm"] == pytest.approx(0.1)


def test_map_scores_accepts_plain_english_labels():
    top, raw_top, mapped = ser_service._map_scores({"happy": 0.7, "sad": 0.3})

    assert raw_top == "happy"
    assert top == "happy"
    assert set(mapped) == {"happy", "sad"}


def test_map_scores_merges_labels_that_share_a_target_and_drops_unknown_tokens():
    top, _raw_top, mapped = ser_service._map_scores(
        {"中立/neutral": 0.4, "其他/other": 0.3, "未知/unknown": 0.2, "<unk>": 0.1}
    )

    assert top == "calm"
    assert mapped["calm"] == pytest.approx(0.9)
    assert "<unk>" not in mapped
    assert len(mapped) == 1


def test_predict_emotions_parses_worker_output(tmp_path, monkeypatch):
    audio = _audio(tmp_path, "clip.wav")
    _stub_worker(
        monkeypatch,
        tmp_path,
        "import json\nprint(json.dumps({'results': [{'raw_scores': {'生气/angry': 0.8}}]}))\n",
    )
    monkeypatch.setattr(ser_service, "model_path", lambda: tmp_path)

    results = ser_service.predict_emotions([audio])

    assert len(results) == 1
    assert results[0]["top_emotion"] == "angry"
    assert results[0]["raw_top_emotion"] == "生气/angry"


def test_predict_emotions_keeps_per_clip_errors(tmp_path, monkeypatch):
    ok_clip = _audio(tmp_path, "ok.wav")
    missing_clip = tmp_path / "missing.wav"
    _stub_worker(
        monkeypatch,
        tmp_path,
        "import json\nprint(json.dumps({'results': [{'raw_scores': {'开心/happy': 1.0}}, {'error': 'boom'}]}))\n",
    )
    monkeypatch.setattr(ser_service, "model_path", lambda: tmp_path)

    results = ser_service.predict_emotions([ok_clip, missing_clip])

    assert results[0]["top_emotion"] == "happy"
    assert "error" in results[1]


def test_predict_emotions_reports_worker_failure_for_every_clip(tmp_path, monkeypatch):
    clip = _audio(tmp_path, "clip.wav")
    _stub_worker(
        monkeypatch,
        tmp_path,
        "import sys\nsys.stderr.write('ModuleNotFoundError: No module named funasr')\nsys.exit(3)\n",
    )
    monkeypatch.setattr(ser_service, "model_path", lambda: tmp_path)

    results = ser_service.predict_emotions([clip])

    assert "error" in results[0]
    assert "funasr" in results[0]["error"]


def test_predict_emotions_pads_short_worker_output(tmp_path, monkeypatch):
    clips = [_audio(tmp_path, f"clip{index}.wav") for index in range(2)]
    _stub_worker(
        monkeypatch,
        tmp_path,
        "import json\nprint(json.dumps({'results': [{'raw_scores': {'开心/happy': 1.0}}]}))\n",
    )
    monkeypatch.setattr(ser_service, "model_path", lambda: tmp_path)

    results = ser_service.predict_emotions(clips)

    assert len(results) == len(clips)
    assert results[0]["top_emotion"] == "happy"
    assert "error" in results[1]


def test_predict_emotion_reports_missing_audio_without_starting_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(ser_service, "model_path", lambda: tmp_path)
    monkeypatch.setattr(
        ser_service,
        "runtime_python",
        lambda: pytest.fail("缺少音频时不应启动 worker"),
    )

    result = ser_service.predict_emotion(tmp_path / "absent.wav")

    assert "error" in result


def test_model_path_honours_environment_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-emotion-model"
    monkeypatch.setenv("VOICE_STUDIO_EMOTION2VEC_MODEL", str(override))

    assert ser_service.model_path() == override
