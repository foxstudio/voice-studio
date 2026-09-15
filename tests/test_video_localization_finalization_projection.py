from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.video_localization.localization_finalization_projection import (
    project_saved_finalization_warning,
)


def test_saved_warning_is_projected_without_rewriting_review_evidence():
    summary = {"task_step_results": {
        "commit_localization_tracks": {"status": "success"},
        "finalize_localization_spoken_script": {
            "status": "needs_review",
            "notes": ["终审后的复核仍有问题，结果不会写入正式字幕轨。"],
            "metrics": [{"label": "处理结论", "value": "终审后仍有问题，停止进入后续流程"}],
            "sections": [
                {"title": "终审结果", "items": [{"title": "终审后仍有问题，停止进入后续流程"}]},
                {"title": "仍需处理", "items": [{"title": "original excerpt", "text": "review finding"}]},
            ],
            "document": {"content": "original script"},
        },
    }}
    original = deepcopy(summary)
    result = project_saved_finalization_warning(summary)
    step = result["task_step_results"]["finalize_localization_spoken_script"]
    assert step["status"] == "warning"
    assert "带提醒继续" in step["metrics"][0]["value"]
    assert "不阻断" in step["notes"][0]
    assert step["sections"][1] == original["task_step_results"]["finalize_localization_spoken_script"]["sections"][1]
    assert step["document"] == {"content": "original script"}
    assert summary == original
    assert project_saved_finalization_warning(result) == result


def test_uncommitted_historical_result_is_not_reinterpreted():
    for status in ("failed", "todo", "cancelled", "running"):
        summary = {"task_step_results": {
            "commit_localization_tracks": {"status": status},
            "finalize_localization_spoken_script": {"status": "needs_review"},
        }}
        assert project_saved_finalization_warning(summary) is summary
    assert project_saved_finalization_warning({}) == {}
