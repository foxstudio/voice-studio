"""Explicit development-only evidence admission into the existing batch replay."""
import hashlib
import json
from pathlib import Path
import re

from app.domains.video_localization.development_checkpoints import (
    LocalizationDevelopmentCheckpointWriter,
    development_checkpoint_exists,
    load_development_checkpoint,
)
from app.domains.video_localization.llm_candidate_provenance import (
    DevelopmentCandidateImport,
    DevelopmentCandidateReceipt,
    RecoveredLlmCandidateProvenance,
)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


class DevelopmentCandidateReceiptStore:
    """One target project/session's explicitly imported, immutable receipts.

    The caller owns the external temporary root. The public import method
    records evidence only; actual call-input matching and domain validation
    still happen in the ordinary batch execution path.
    """

    def __init__(self, *, root: Path, project_id: str, development_session_id: str,
                 development_mode: bool) -> None:
        if development_mode is not True:
            raise ValueError("候选恢复收据只允许显式开发模式。")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", project_id):
            raise ValueError("候选恢复项目标识不合法。")
        self.root = Path(root) / project_id
        self.project_id = project_id
        self.session_id = development_session_id
        self.writer = LocalizationDevelopmentCheckpointWriter(
            self.root, project_id=project_id, workflow_operation_id=development_session_id,
        )

    def _step_id(self, batch_id: str, attempt: int) -> str:
        return f"candidate-receipt.{hashlib.sha256(batch_id.encode()).hexdigest()}.a{attempt}"

    def _receipt(self, evidence: DevelopmentCandidateImport) -> DevelopmentCandidateReceipt:
        baseline = evidence.baseline
        if evidence.batch_id != baseline.batch_id or evidence.attempt != baseline.attempt:
            raise ValueError("恢复候选与旧基线的批次或尝试不一致。")
        if _fingerprint(evidence.candidate) != baseline.candidate_fingerprint:
            raise ValueError("恢复候选 SHA 与旧基线不一致。")
        identity = {"target_project_id": self.project_id, "target_session_id": self.session_id,
                    "evidence": evidence.model_dump(mode="json")}
        return DevelopmentCandidateReceipt(
            **identity,
            provenance=RecoveredLlmCandidateProvenance(
                batch_id=evidence.batch_id, attempt=evidence.attempt,
                input_fingerprint=baseline.input_fingerprint,
                candidate_fingerprint=baseline.candidate_fingerprint,
                receipt_fingerprint=_fingerprint(identity),
                source_evidence_fingerprint=evidence.source_evidence_fingerprint,
            ),
        )

    def _load(self, batch_id: str, attempt: int) -> DevelopmentCandidateReceipt | None:
        step = self._step_id(batch_id, attempt)
        receipt = load_development_checkpoint(
            self.root, project_id=self.project_id, workflow_operation_id=self.session_id,
            step_id=step, result_model=DevelopmentCandidateReceipt,
        )
        if receipt is None:
            if development_checkpoint_exists(self.root, workflow_operation_id=self.session_id, step_id=step):
                raise ValueError("恢复收据缺损或身份不符；拒绝调用模型。")
            return None
        if (receipt.target_project_id != self.project_id or receipt.target_session_id != self.session_id
                or receipt.evidence.batch_id != batch_id or receipt.evidence.attempt != attempt
                or receipt != self._receipt(receipt.evidence)):
            raise ValueError("恢复收据身份或指纹不一致；拒绝调用模型。")
        return receipt

    def import_candidate(self, evidence: DevelopmentCandidateImport) -> DevelopmentCandidateReceipt:
        """Verify and register a complete candidate; never reconstruct a call."""
        # Revalidate instances too, so model_copy/model_construct cannot bypass admission.
        evidence = DevelopmentCandidateImport.model_validate(evidence.model_dump(mode="json"))
        receipt = self._receipt(evidence)
        existing = self._load(evidence.batch_id, evidence.attempt)
        if existing is not None:
            if existing != receipt:
                raise ValueError("同批恢复收据已存在且内容不同；禁止覆盖。")
            return existing
        self.writer(self._step_id(evidence.batch_id, evidence.attempt), receipt)
        return receipt

    def match(self, *, batch_id: str, attempt: int, input_fingerprint: str) -> DevelopmentCandidateReceipt | None:
        receipt = self._load(batch_id, attempt)
        if receipt is not None and receipt.provenance.input_fingerprint != input_fingerprint:
            raise ValueError("当前完整模型输入 SHA 与恢复基线不一致；拒绝调用模型。")
        return receipt
