"""Typed node registry for the canonical localization workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.domains.video_localization import asr_uncertainty
from app.domains.video_localization import localization_alignment_adjudication
from app.domains.video_localization import localization_alignment_request
from app.domains.video_localization import localization_context_intent
from app.domains.video_localization import localization_creation_context
from app.domains.video_localization import localization_document_brief
from app.domains.video_localization import localization_brief_contracts
from app.domains.video_localization import localization_brief_stages
from app.domains.video_localization import localization_document_evidence
from app.domains.video_localization import localization_display_adjudication
from app.domains.video_localization import localization_dual_tracks
from app.domains.video_localization import localization_generation_request
from app.domains.video_localization import localization_generation_chunks
from app.domains.video_localization import localization_review_request
from app.domains.video_localization import localization_semantic_alignment
from app.domains.video_localization import localization_source
from app.domains.video_localization import localization_spoken_script
from app.domains.video_localization import localization_edit_spans
from app.domains.video_localization import localization_tracks
from app.domains.video_localization import quality_gate
from app.domains.video_localization import subtitle_punctuation
from app.domains.video_localization.workflow_contracts import (
    LOCALIZATION_WORKFLOW_V3_DEFINITION,
)
from app.domains.video_localization.workflow_graph import WorkflowGraph
from app.domains.video_localization.workflow_behavior import (
    source_behavior_fingerprint,
)


@dataclass(frozen=True)
class LocalizationWorkflowNodeSpec:
    step_id: str
    result_model: type[BaseModel]
    implementation_modules: tuple[object, ...]
    implementation_objects: tuple[object, ...] = ()
    has_formal_side_effect: bool = False


LOCALIZATION_WORKFLOW_NODE_SPECS: dict[
    str,
    LocalizationWorkflowNodeSpec,
] = {
    "lock_localization_source": LocalizationWorkflowNodeSpec(
        "lock_localization_source",
        localization_source.LocalizationSourceLockResult,
        (localization_source, asr_uncertainty),
    ),
    "lock_localization_context_intent": LocalizationWorkflowNodeSpec(
        "lock_localization_context_intent",
        localization_context_intent.LocalizationContextIntentResult,
        (localization_context_intent,),
    ),
    "analyze_localization_document": LocalizationWorkflowNodeSpec(
        "analyze_localization_document",
        localization_document_brief.LocalizationDocumentBriefResult,
        (
            localization_document_brief,
            localization_brief_contracts,
            localization_brief_stages,
            localization_generation_chunks,
        ),
    ),
    "collect_localization_research_evidence_v3": (
        LocalizationWorkflowNodeSpec(
            "collect_localization_research_evidence_v3",
            localization_document_evidence.LocalizationDocumentResearchResult,
            (),
            (
                localization_document_evidence
                .collect_localization_document_research,
            ),
        )
    ),
    "collect_localization_visual_evidence_v3": LocalizationWorkflowNodeSpec(
        "collect_localization_visual_evidence_v3",
        localization_document_evidence.LocalizationDocumentVisualResult,
        (),
        (
            localization_document_evidence
            .collect_localization_document_visuals,
            localization_document_evidence._sample_timestamps,
        ),
    ),
    "adjudicate_localization_evidence_v3": LocalizationWorkflowNodeSpec(
        "adjudicate_localization_evidence_v3",
        (
            localization_document_evidence
            .LocalizationDocumentEvidenceAdjudicationResult
        ),
        (),
        (
            localization_document_evidence
            .adjudicate_localization_document_evidence,
            localization_document_evidence._complete_text_evidence_batch,
        ),
    ),
    "lock_localization_creation_context": LocalizationWorkflowNodeSpec(
        "lock_localization_creation_context",
        (
            localization_creation_context
            .LocalizationCreationContextResult
        ),
        (localization_creation_context,),
    ),
    "generate_localization_spoken_script": LocalizationWorkflowNodeSpec(
        "generate_localization_spoken_script",
        localization_spoken_script.LocalizationSpokenScriptResult,
        (
            localization_generation_request,
            localization_generation_chunks,
        ),
        (
            localization_spoken_script
            .generate_localization_spoken_script,
            localization_spoken_script.build_spoken_script_prompt,
            localization_spoken_script.generation_route_fingerprint,
            localization_spoken_script.parse_spoken_script_text,
            localization_spoken_script._validate_lineage,
            localization_spoken_script._validate_generation_checkpoint,
            localization_spoken_script._validate_script_content,
            localization_spoken_script._validate_chunk_output,
            localization_generation_chunks.plan_localization_generation_chunks,
        ),
    ),
    "review_localization_fidelity": LocalizationWorkflowNodeSpec(
        "review_localization_fidelity",
        localization_spoken_script.LocalizationSpokenScriptReviewResult,
        (localization_review_request, localization_edit_spans),
        (
            localization_spoken_script
            .review_localization_spoken_script_fidelity,
            localization_spoken_script._run_structured_review,
            localization_spoken_script._build_review,
            localization_spoken_script._review_request_summary,
            localization_spoken_script._normalize_review_issue_fields,
            localization_spoken_script.script_review_text,
            localization_spoken_script._sentence_fragment_spans,
            localization_review_request
            .build_localization_fidelity_review_request,
        ),
    ),
    "review_localization_naturalness": LocalizationWorkflowNodeSpec(
        "review_localization_naturalness",
        localization_spoken_script.LocalizationSpokenScriptReviewResult,
        (localization_review_request, localization_edit_spans),
        (
            localization_spoken_script
            .review_localization_spoken_script_naturalness,
            localization_spoken_script._run_structured_review,
            localization_spoken_script._build_review,
            localization_spoken_script._review_request_summary,
            localization_spoken_script._normalize_review_issue_fields,
            localization_spoken_script.script_review_text,
            localization_spoken_script._sentence_fragment_spans,
            localization_review_request
            .build_localization_naturalness_review_request,
        ),
    ),
    "finalize_localization_spoken_script": LocalizationWorkflowNodeSpec(
        "finalize_localization_spoken_script",
        localization_spoken_script.LocalizationSpokenScriptFinalResult,
        (
            localization_spoken_script,
            localization_review_request,
            localization_edit_spans,
        ),
    ),
    "align_localization_semantics": LocalizationWorkflowNodeSpec(
        "align_localization_semantics",
        localization_semantic_alignment.LocalizationSemanticAlignmentResult,
        (localization_semantic_alignment,),
    ),
    "adjudicate_localization_alignment": LocalizationWorkflowNodeSpec(
        "adjudicate_localization_alignment",
        (
            localization_alignment_adjudication
            .LocalizationAlignmentAdjudicationResult
        ),
        (
            localization_alignment_adjudication,
            localization_alignment_request,
        ),
    ),
    "build_localization_dual_tracks": LocalizationWorkflowNodeSpec(
        "build_localization_dual_tracks",
        localization_dual_tracks.LocalizationDualTrackResult,
        (localization_dual_tracks, subtitle_punctuation),
    ),
    "adjudicate_localization_display_boundaries": LocalizationWorkflowNodeSpec(
        "adjudicate_localization_display_boundaries",
        localization_display_adjudication.LocalizationDisplayAdjudicationResult,
        (
            localization_display_adjudication,
            localization_alignment_adjudication,
            localization_alignment_request,
        ),
    ),
    "validate_localization_tracks": LocalizationWorkflowNodeSpec(
        "validate_localization_tracks",
        localization_tracks.LocalizationTracksQualityGateResult,
        (
            localization_tracks,
            quality_gate,
        ),
    ),
    "commit_localization_tracks": LocalizationWorkflowNodeSpec(
        "commit_localization_tracks",
        localization_tracks.LocalizationFormalDualTrackWriteResult,
        (localization_tracks,),
        has_formal_side_effect=True,
    ),
}


def validate_localization_node_registry() -> None:
    graph = WorkflowGraph(LOCALIZATION_WORKFLOW_V3_DEFINITION)
    defined = set(graph.ordered_step_ids)
    registered = set(LOCALIZATION_WORKFLOW_NODE_SPECS)
    if defined != registered:
        raise ValueError(
            "本土化节点注册表与工作流定义不一致："
            f"缺少 {sorted(defined - registered)}，"
            f"多出 {sorted(registered - defined)}。"
        )
    for step_id, spec in LOCALIZATION_WORKFLOW_NODE_SPECS.items():
        if spec.step_id != step_id:
            raise ValueError(f"节点注册键与节点 ID 不一致：{step_id}")


def node_behavior_fingerprint(
    step_id: str,
    behavior_context: dict[str, Any] | None = None,
) -> str:
    spec = LOCALIZATION_WORKFLOW_NODE_SPECS[step_id]
    return source_behavior_fingerprint(
        workflow_schema_version=(
            LOCALIZATION_WORKFLOW_V3_DEFINITION.schema_version
        ),
        workflow_id=LOCALIZATION_WORKFLOW_V3_DEFINITION.workflow_id,
        step_id=step_id,
        output_contract_version=next(
            task.output_contract_version
            for stage in LOCALIZATION_WORKFLOW_V3_DEFINITION.stages
            for task in stage.atomic_tasks
            if task.id == step_id
        ),
        implementation_modules=spec.implementation_modules,
        implementation_objects=spec.implementation_objects,
        behavior_context=behavior_context,
    )


def checkpoint_behavior_fingerprint(step_id: str) -> str | None:
    """Resolve a nested trace checkpoint to its owning workflow node."""

    candidates = [
        candidate
        for candidate in LOCALIZATION_WORKFLOW_NODE_SPECS
        if step_id == candidate or step_id.startswith(f"{candidate}.")
    ]
    if not candidates:
        return None
    return node_behavior_fingerprint(max(candidates, key=len))


validate_localization_node_registry()


__all__ = [
    "LOCALIZATION_WORKFLOW_NODE_SPECS",
    "LocalizationWorkflowNodeSpec",
    "checkpoint_behavior_fingerprint",
    "node_behavior_fingerprint",
    "validate_localization_node_registry",
]
