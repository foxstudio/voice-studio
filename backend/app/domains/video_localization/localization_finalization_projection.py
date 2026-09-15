"""Reader wording for advisory finalization, including proven legacy saves."""

from copy import deepcopy


FINALIZATION_WARNING_DECISION = "终审仍有待抽查项，带提醒继续后续流程"
FINALIZATION_WARNING_NOTE = "终审提醒不阻断后续流程；正式保存仍需通过来源与时间完整性检查。"


def project_saved_finalization_warning(summary: dict) -> dict:
    """Adapt old blocking copy only when this operation proves it saved tracks.

    Do not infer a historical review pass from current project state or rewrite
    saved task evidence. Failed/development operations retain their own semantics.
    """
    steps = summary.get("task_step_results")
    if not isinstance(steps, dict):
        return summary
    final = steps.get("finalize_localization_spoken_script")
    commit = steps.get("commit_localization_tracks")
    if not (
        isinstance(final, dict)
        and isinstance(commit, dict)
        and commit.get("status") == "success"
        and final.get("status") == "needs_review"
    ):
        return summary
    projected = deepcopy(summary)
    step = projected["task_step_results"]["finalize_localization_spoken_script"]
    step["status"] = "warning"
    replacements = {
        "终审后仍有问题，停止进入后续流程": FINALIZATION_WARNING_DECISION,
        "终审后的复核仍有问题，结果不会写入正式字幕轨。": FINALIZATION_WARNING_NOTE,
    }
    # Only exact known legacy presentation fields; never alter issue excerpts,
    # documents, review scores, or historical diagnostics.
    step["notes"] = [replacements.get(note, note) for note in step.get("notes", [])]
    for metric in step.get("metrics", []):
        if metric.get("label") == "处理结论":
            metric["value"] = replacements.get(metric.get("value"), metric.get("value"))
    for section in step.get("sections", []):
        if section.get("title") == "终审结果":
            for item in section.get("items", []):
                item["title"] = replacements.get(item.get("title"), item.get("title"))
    return projected
