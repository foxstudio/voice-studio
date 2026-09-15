"""Fixed provider responses for the public outline/detail brief workflow."""

from copy import deepcopy

def staged_candidates(content):
    """Project existing test content into model-stage contracts, not production logic."""
    from app.domains.video_localization.localization_brief_contracts import LocalizationDocumentBriefContent

    full = LocalizationDocumentBriefContent.model_validate(content).model_dump(mode="json")
    outline_fields = (
        "purpose", "audience", "structure", "speaker_profile", "emotional_arc",
        "cultural_adaptation_rules", "disfluency_policy_zh",
    )
    outline = {field: deepcopy(full[field]) for field in outline_fields}
    strategy = full["creative_strategy"]
    outline["creative_strategy"] = {
        field: strategy[field] or value
        for field, value in {
            "content_type_zh": "讲解", "register_zh": "自然",
            "audience_relationship_zh": "平等交流", "narrative_voice_zh": "直接讲述",
            "expression_strategy_zh": "准确自然地表达已有事实。",
        }.items()
    }
    details = {field: deepcopy(full[field]) for field in (
        "immutable_facts", "term_relations", "speech_qualification_candidates", "evidence_questions",
    )}
    details.update({field: deepcopy(strategy[field]) for field in ("terminology", "semantic_attention")})
    return outline, details


def stage_candidate(content, payload):
    outline, details = staged_candidates(content)
    if "core_source_cues" not in payload:
        return outline
    core_ids = {cue["cue_id"] for cue in payload["core_source_cues"]}
    return {field: [item for item in items if set(item["source_cue_ids"]) <= core_ids]
            for field, items in details.items()}
