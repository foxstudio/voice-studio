"""Behavior registry for model-driven ASR development snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.video_localization import asr_flow
from app.domains.video_localization import asr_uncertainty
from app.domains.video_localization import entity_normalization
from app.domains.video_localization import research_evidence
from app.domains.video_localization import review_decisions
from app.domains.video_localization import section_review
from app.domains.video_localization import visual_evidence
from app.domains.video_localization import whole_recheck
from app.domains.video_localization.workflow_behavior import (
    source_behavior_fingerprint,
)


@dataclass(frozen=True)
class AsrDevelopmentNodeBehaviorSpec:
    artifact_kind: str
    output_contract_version: str
    implementation_modules: tuple[object, ...] = ()
    implementation_objects: tuple[object, ...] = ()


ASR_DEVELOPMENT_NODE_BEHAVIOR_SPECS = {
    "document_understanding": AsrDevelopmentNodeBehaviorSpec(
        artifact_kind="document_understanding",
        output_contract_version="asr-document-understanding-v1",
        implementation_objects=(
            asr_flow.understand_document,
            asr_flow._understand_document,
            asr_flow._document_payload,
        ),
    ),
    "visual_evidence": AsrDevelopmentNodeBehaviorSpec(
        artifact_kind="visual_evidence",
        output_contract_version="asr-visual-evidence-v1",
        implementation_modules=(visual_evidence,),
    ),
    "research_evidence": AsrDevelopmentNodeBehaviorSpec(
        artifact_kind="research_evidence",
        output_contract_version="asr-research-evidence-v2",
        implementation_modules=(research_evidence,),
    ),
    "entity_normalization": AsrDevelopmentNodeBehaviorSpec(
        artifact_kind="entity_normalization",
        output_contract_version="asr-entity-normalization-v1",
        implementation_modules=(entity_normalization,),
    ),
    "section_review": AsrDevelopmentNodeBehaviorSpec(
        artifact_kind="section_review",
        output_contract_version="asr-section-review-v4",
        implementation_modules=(section_review,),
    ),
    "review_decisions": AsrDevelopmentNodeBehaviorSpec(
        artifact_kind="review_decisions",
        output_contract_version="asr-review-decisions-v4",
        implementation_modules=(review_decisions, asr_uncertainty),
    ),
    "whole_recheck": AsrDevelopmentNodeBehaviorSpec(
        artifact_kind="whole_recheck",
        output_contract_version="asr-whole-recheck-v3",
        implementation_modules=(whole_recheck,),
    ),
}


def asr_development_behavior_fingerprint(artifact_kind: str) -> str:
    spec = ASR_DEVELOPMENT_NODE_BEHAVIOR_SPECS[artifact_kind]
    return source_behavior_fingerprint(
        workflow_id="english-asr-development",
        workflow_schema_version="asr-development-behavior-v1",
        step_id=spec.artifact_kind,
        output_contract_version=spec.output_contract_version,
        implementation_modules=spec.implementation_modules,
        implementation_objects=spec.implementation_objects,
    )


__all__ = [
    "ASR_DEVELOPMENT_NODE_BEHAVIOR_SPECS",
    "AsrDevelopmentNodeBehaviorSpec",
    "asr_development_behavior_fingerprint",
]
