"""Pure validation of explicit recovery decisions; never invent semantic breaks."""

import re
import unicodedata

from app.errors import AppException
from app.schemas.video_localization_dubbing_recovery import DubbingRecoveryDecision


def validate_recovery_decision(draft, decision: DubbingRecoveryDecision):
    plan = draft.dubbing_production.active_plan
    group = next((g for g in plan.groups if g.group_id == decision.group_id), None) if plan else None
    if not group or plan.source_revision != decision.source_revision or plan.plan_revision != decision.plan_revision:
        raise AppException(409, "DUBBING_RECOVERY_PLAN_CHANGED", "恢复方案不属于当前配音计划，请重新读取分组。")

    def compact(text):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))

    if compact("".join(decision.phrases)) != compact(group.spoken_text):
        raise AppException(
            409, "DUBBING_RECOVERY_TEXT_CHANGED", "分段必须按原顺序完整覆盖当前台词；改词请先同步修改字幕。"
        )
    reference_range = None
    if decision.reference_cue_ids:
        selected = [cue for cue in draft.cues if cue.cue_id in decision.reference_cue_ids]
        if (
            len(selected) != len(decision.reference_cue_ids)
            or [c.cue_id for c in selected] != decision.reference_cue_ids
        ):
            raise AppException(409, "DUBBING_RECOVERY_REFERENCE_INVALID", "参考字幕不存在或顺序不正确。")
        if not group.speaker_id or any(c.speaker_id != group.speaker_id for c in selected):
            raise AppException(409, "DUBBING_RECOVERY_SPEAKER_CHANGED", "恢复参考必须来自当前说话人的分离人声。")
        start, end = selected[0].start_ms, selected[-1].end_ms
        covered = [c for c in draft.cues if c.start_ms < end and c.end_ms > start]
        if [c.cue_id for c in covered] != decision.reference_cue_ids:
            raise AppException(
                409, "DUBBING_RECOVERY_REFERENCE_GAP", "参考范围夹有未选择的台词，请选择连续且同一说话人的字幕。"
            )
        if not 3000 <= end - start <= 10000:
            raise AppException(409, "DUBBING_RECOVERY_REFERENCE_DURATION", "就近恢复参考请使用 3–10 秒的连续分离人声。")
        reference_range = (start, end)
    return group, reference_range
