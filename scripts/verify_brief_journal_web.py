#!/usr/bin/env python3
"""Real brief-development Web path with a fixed provider and owned temporary data."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from verify_generation_queue_web import ROOT, available_port, isolated_environment, stop_process_group


class FixedBriefProvider:
    """Deterministic outline -> refused detail -> successful recovery fixture.

    Injectable factories keep pure tests independent of application imports.
    There is no network fallback, and an unexpected extra provider call fails.
    """

    def __init__(self, candidate, *, candidate_factory, trace_factory, error_factory):
        self.candidate = candidate
        self.candidate_factory = candidate_factory
        self.trace_factory = trace_factory
        self.error_factory = error_factory
        self.initial_release = threading.Event()
        self.recovery_release = threading.Event()
        self.model_calls = 0
        self.outline_calls = 0
        self.detail_calls = 0
        self.call_order = []

    def release(self, phase):
        if phase not in {"initial", "recovery"}:
            raise ValueError("Unknown fixed-provider release phase")
        (self.initial_release if phase == "initial" else self.recovery_release).set()

    def snapshot(self):
        return {"model_calls": self.model_calls, "outline_calls": self.outline_calls,
                "detail_calls": self.detail_calls, "call_order": list(self.call_order)}

    def __call__(self, prompt, payload, *, trace_sink, **kwargs):
        if kwargs.get("profile_id") != "fixture-profile":
            raise RuntimeError("Unexpected model profile in fixed acceptance")
        is_detail = "core_source_cues" in payload
        self.model_calls += 1
        if is_detail:
            self.detail_calls += 1
        else:
            self.outline_calls += 1
        self.call_order.append("details" if is_detail else "outline")
        if self.call_order not in (["outline"], ["outline", "details"], ["outline", "details", "details"]):
            raise RuntimeError("Unexpected extra provider call instead of journal replay")
        rejected = is_detail and self.detail_calls == 1
        gate = self.initial_release if not is_detail else self.recovery_release
        if not rejected and not gate.wait(timeout=45):
            raise RuntimeError("Browser did not release the fixed candidate")
        trace_sink(self.trace_factory(profile_id="fixture-profile", model_id="fixture-model",
            provider_host="fixture", request_chars=100, request_body_bytes=120, max_tokens=6000,
            timeout_seconds=600, reasoning_effort_requested="low", reasoning_control_applied=True,
            duration_ms=1, finish_reason=None if rejected else "stop",
            error_code="codex_cli_rate_limited" if rejected else None))
        if rejected:
            raise self.error_factory("固定验收：模拟额度拒绝，请通过新开发执行恢复。",
                code="codex_cli_rate_limited", status_code=429)
        return self.candidate_factory(self.candidate, payload)


class FixedEvidenceProvider(FixedBriefProvider):
    """Real image inputs; fixed second-batch refusal with no network fallback."""

    TEXT_ORDER = ["outline", "details"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.text_order = []
        self.image_order = []
        self.image_hashes = []

    def snapshot(self):
        return {"text_calls": len(self.text_order), "image_calls": len(self.image_order),
                "image_order": list(self.image_order), "image_hashes": list(self.image_hashes)}

    def _trace(self, trace_sink, rejected=False):
        trace_sink(self.trace_factory(profile_id="fixture-profile", model_id="fixture-model",
            provider_host="fixture", request_chars=100, request_body_bytes=120, max_tokens=6000,
            timeout_seconds=600, reasoning_effort_requested="low", reasoning_control_applied=True,
            duration_ms=1, finish_reason=None if rejected else "stop",
            error_code="codex_cli_rate_limited" if rejected else None))

    def __call__(self, prompt, payload, *, trace_sink, **kwargs):
        if kwargs.get("profile_id") != "fixture-profile":
            raise RuntimeError("Unexpected fixed evidence profile")
        self.text_order.append("details" if "core_source_cues" in payload else "outline")
        if self.text_order != self.TEXT_ORDER[:len(self.text_order)]:
            raise RuntimeError("Unexpected text call instead of upstream reuse")
        self._trace(trace_sink)
        return self.candidate_factory(self.candidate, payload)

    def complete_images(self, prompt, payload, images, *, trace_sink, **kwargs):
        if kwargs.get("profile_id") != "fixture-profile" or len(images) != 3:
            raise RuntimeError("Expected one complete three-image question batch")
        question_ids = [question["question_id"] for question in payload["questions"]]
        if len(question_ids) != 1 or any(not image.data.startswith(b"\xff\xd8") for image in images):
            raise RuntimeError("Expected actual JPEG frames for one question")
        self.image_order.append(question_ids[0])
        if self.image_order not in (["question_0001"], ["question_0001", "question_0002"],
                                    ["question_0001", "question_0002", "question_0002"]):
            raise RuntimeError("Unexpected image call instead of candidate replay")
        self.image_hashes.append([hashlib.sha256(image.data).hexdigest() for image in images])
        rejected = len(self.image_order) == 2
        gate = self.initial_release if len(self.image_order) == 1 else self.recovery_release
        if not rejected and not gate.wait(timeout=45):
            raise RuntimeError("Browser did not release the fixed image candidate")
        self._trace(trace_sink, rejected)
        if rejected:
            raise self.error_factory("固定验收：第二图片批模拟额度拒绝。",
                code="codex_cli_rate_limited", status_code=429)
        return {"answers": [{"question_id": question_ids[0], "evidence_image_positions": [1, 2, 3],
            "status": "supported", "conclusion_zh": f"固定画面证据已核对：{question_ids[0]}",
            "constraint_zh": "只采用画面直接支持的信息。", "source_urls": []}]}


class FixedGenerationProvider(FixedEvidenceProvider):
    """Two real source chunks with a refused second generation candidate."""

    TEXT_ORDER = ["outline", "details", "details"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.generation_order = []
        self.generation_inputs = []

    def snapshot(self):
        return {"text_calls": len(self.text_order), "generation_calls": len(self.generation_order),
                "generation_order": list(self.generation_order), "generation_inputs": list(self.generation_inputs)}

    def __call__(self, prompt, payload, *, trace_sink, **kwargs):
        if "editable_source_cues" not in payload:
            return super().__call__(prompt, payload, trace_sink=trace_sink, **kwargs)
        if kwargs.get("profile_id") != "fixture-profile":
            raise RuntimeError("Unexpected fixed generation profile")
        chunk = payload["chunk_id"]
        self.generation_order.append(chunk)
        if self.generation_order not in (["chunk_0001"], ["chunk_0001", "chunk_0002"],
                                         ["chunk_0001", "chunk_0002", "chunk_0002"]):
            raise RuntimeError("Unexpected generation call instead of candidate replay")
        self.generation_inputs.append(hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
        rejected = len(self.generation_order) == 2
        gate = self.initial_release if len(self.generation_order) == 1 else self.recovery_release
        if not rejected and not gate.wait(timeout=45):
            raise RuntimeError("Browser did not release the fixed generated candidate")
        self._trace(trace_sink, rejected)
        if rejected:
            raise self.error_factory("固定验收：第二生成批模拟额度拒绝。",
                code="codex_cli_rate_limited", status_code=429)
        return {"chunk_id": chunk, "suggested_title": "问候与告别" if chunk == "chunk_0001" else None,
                "paragraphs": ["你好，世界。" if chunk == "chunk_0001" else "再见，世界。"]}


def prepare_generation_fixture(draft, candidate):
    """Two source sentences and macro sections, no fake model materialization."""
    draft.cues.append(draft.cues[0].model_copy(update={"cue_id": "cue_0002", "start_ms": 1000,
        "end_ms": 1900, "en_subtitle_text": "Goodbye world.", "source_word_ids": ["word_0003", "word_0004"]}))
    draft.transcription.words.extend([word.model_copy(update={"word_id": f"word_{index + 3:04d}",
        "segment_id": "segment_0002", "start_ms": word.start_ms + 1000, "end_ms": word.end_ms + 1000,
        "text": "Goodbye" if index == 0 else "world."}) for index, word in enumerate(draft.transcription.words[:2])])
    draft.transcription.corrected_text = "Hello world. Goodbye world."
    candidate["structure"].append({"section_id": "section_0002", "title": "告别", "function_zh": "结束问候。",
                                    "source_cue_ids": ["cue_0002"]})
    candidate["emotional_arc"].append({"section_id": "section_0002", "emotion_zh": "平静", "intensity": 2,
                                        "speech_acts": ["告别"]})
    candidate["immutable_facts"].append({"fact_id": "fact_0002", "statement_zh": "说了 Goodbye world。",
                                          "source_cue_ids": ["cue_0002"]})
    candidate["evidence_questions"] = []


def journal_summary(checkpoint_root: Path) -> list[dict]:
    records = []
    for path in checkpoint_root.rglob("checkpoint.json"):
        raw = path.read_bytes()
        result = json.loads(raw).get("result", {})
        if result.get("contract_version") == "localization-development-llm-batch-v1":
            records.append({"batch_id": result["batch_id"], "status": result["status"],
                "validation_count": len(result["validations"]),
                "recovery_ordinal": result.get("recovery_ordinal", 0),
                "recovery_execution_id": result.get("recovery_execution_id"),
                "call_error_code": result.get("call_error_code"),
                "input_fingerprint": result["input_fingerprint"],
                "candidate_fingerprint": result.get("candidate_fingerprint"),
                "recorded_call_count": len(result.get("llm_calls", [])),
                "recovered_candidate": result.get("recovered_candidate"),
                "file_sha256": hashlib.sha256(raw).hexdigest()})
    return sorted(records, key=lambda record: (record["batch_id"], record["recovery_ordinal"] or 0))


def import_fixture_candidates(checkpoint_root: Path, *, project_id: str,
                              source_session: str, target_session: str) -> list[dict]:
    """Import complete fixed-run evidence through the product receipt boundary."""
    from app.domains.video_localization.development_candidate_recovery import DevelopmentCandidateReceiptStore
    from app.domains.video_localization.development_checkpoints import load_development_checkpoint
    from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchCheckpoint
    from app.domains.video_localization.llm_candidate_provenance import DevelopmentCandidateImport

    if source_session == target_session:
        raise ValueError("Acceptance recovery requires a separate session")
    evidence = {}
    for path in (checkpoint_root / source_session).glob("*/checkpoint.json"):
        raw = path.read_bytes()
        envelope = json.loads(raw)
        if envelope.get("result_contract_version") != "localization-development-llm-batch-v1":
            continue
        record = load_development_checkpoint(checkpoint_root, project_id=project_id,
            workflow_operation_id=source_session, step_id=path.parent.name,
            result_model=DevelopmentLlmBatchCheckpoint)
        if record is None:
            raise ValueError("Invalid source checkpoint; refusing candidate import")
        if record.status != "validation_passed":
            continue  # The immutable original HTTP 429 is not a candidate.
        if record.batch_id in evidence:
            raise ValueError("Ambiguous successful fixture batch")
        evidence[record.batch_id] = DevelopmentCandidateImport(
            completeness="complete", batch_id=record.batch_id, attempt=record.attempt,
            candidate=record.raw_json_candidate,
            baseline={key: getattr(record, key) for key in (
                "batch_id", "attempt", "input_fingerprint", "candidate_fingerprint")},
            source_evidence_fingerprint=hashlib.sha256(raw).hexdigest())
    if set(evidence) != {"brief-outline", "brief-details-chunk_0001"}:
        raise ValueError("Both complete fixed outline/detail candidates are required")
    store = DevelopmentCandidateReceiptStore(root=checkpoint_root / "candidate-recovery",
        project_id=project_id, development_session_id=target_session, development_mode=True)
    return [store.import_candidate(evidence[batch_id]).provenance.model_dump(mode="json")
            for batch_id in sorted(evidence)]


def import_fixture_video(root: Path, project_id: str):
    """Create one tiny local video and use the public managed-media import."""
    from fastapi import UploadFile
    from app.domains.video_localization import service

    video = root / "fixed-evidence.mp4"
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=10:d=1", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(video)], check=True, timeout=15)
    with video.open("rb") as file:
        imported = asyncio.run(service.import_source_media(project_id,
            UploadFile(file=file, filename=video.name)))
    return imported.source_media


def project_content_snapshot(draft):
    """Exclude only public operation_state/draft_store lifecycle bookkeeping.

    Real API operations persist history and their own status; that is not a
    formal localization content commit. All media locators and content remain.
    """
    return draft.model_dump(mode="json", exclude={
        "operations": True, "updated_at": True, "quality_gate": {"checked_at"},
        "source_media": {"metadata": {"localization_draft_status", "localization_draft_error_code",
                                       "localization_draft_error"}},
    })


def serve(root: Path, port: int, scenario: str = "brief") -> None:
    if not (root / "brief-web-owner").is_file():
        raise RuntimeError("An owned acceptance root is required")
    os.environ.update(isolated_environment(root))
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
    from app.main import app
    from app.domains.video_localization import operation_queue, service
    from app.services import frontend_distribution, llm_runtime, project_store, settings_store
    from app.schemas.voice_studio import AppSettingsPatch, LlmProviderProfileUpsert, ProjectCreate
    from tests.test_video_localization_localization_context_intent import _draft
    from tests.test_video_localization_localization_spoken_script import _source_and_brief
    from tests.brief_stage_fixtures import stage_candidate

    operation_queue.DEVELOPMENT_LOCALIZATION_WORKFLOW_CHECKPOINT_ROOT.resolve().relative_to(root.resolve())
    settings_store.patch(AppSettingsPatch(video_localization_development_step_control_enabled=True))
    settings_store.update_llm_profile("fixture-profile", LlmProviderProfileUpsert(
        name="固定验收模型", protocol="openai_compatible", base_url="http://127.0.0.1:9/v1",
        model_id="fixture-model", reasoning_effort="low", enabled=True,
    ))
    names = {"brief": "全文理解重放网页验收", "evidence": "图片证据重放网页验收", "generation": "中文初稿重放网页验收"}
    project = project_store.create_project(ProjectCreate(name=names[scenario]))
    draft = _draft(include_brief=scenario == "brief")
    _, brief = _source_and_brief()
    candidate = brief.content.model_dump(mode="json")
    candidate["purpose"] = "固定验收：理解一句问候，验证候选重放。"
    if scenario == "generation":
        prepare_generation_fixture(draft, candidate)
    if scenario == "evidence":
        draft.source_media = import_fixture_video(root, project.project_id)
        candidate["evidence_questions"] = [{"question_id": f"question_{index:04d}", "kind": "visual",
            "question_zh": f"固定画面问题 {index}：画面提供什么证据？", "source_cue_ids": ["cue_0001"],
            "reason_zh": "验证多批图片证据的独立恢复。"} for index in (1, 2)]
        # Three real frames per question remain together; two questions form two batches.
        llm_runtime.MAX_IMAGE_COUNT = 3
    service.save_video_localization(project.project_id, draft)
    draft_before = project_content_snapshot(service.get_video_localization(project.project_id))
    state = {"project_id": project.project_id, "profile_id": "fixture-profile",
             "session_id": "brief-web-session", "receipt_session_id": "brief-receipt-web-session",
             "provider": "fixed-json-no-model", "scenario": scenario}
    checkpoint_root = operation_queue.DEVELOPMENT_LOCALIZATION_WORKFLOW_CHECKPOINT_ROOT
    providers = {"brief": FixedBriefProvider, "evidence": FixedEvidenceProvider, "generation": FixedGenerationProvider}
    provider = providers[scenario](candidate, candidate_factory=stage_candidate,
        trace_factory=llm_runtime.LlmCompletionTrace, error_factory=llm_runtime.LlmRuntimeError)
    llm_runtime.complete_json = provider
    if scenario == "evidence":
        llm_runtime.complete_multimodal_json = provider.complete_images

    @app.get("/api/__brief_acceptance/state", include_in_schema=False)
    def get_state():
        records = journal_summary(checkpoint_root / state["session_id"])
        current_draft = project_content_snapshot(service.get_video_localization(project.project_id))
        return {**state, **provider.snapshot(),
                "project_content_unchanged": current_draft == draft_before,
                "draft_changed_fields": [key for key in current_draft if current_draft[key] != draft_before[key]],
                "journal": [record for record in records if scenario == "brief" or record["batch_id"].startswith(f"{scenario}-")],
                "receipt_journal": journal_summary(checkpoint_root / state["receipt_session_id"])}

    @app.post("/api/__brief_acceptance/import-candidates", include_in_schema=False)
    def import_candidates():
        return {"receipts": import_fixture_candidates(checkpoint_root, project_id=project.project_id,
            source_session=state["session_id"], target_session=state["receipt_session_id"])}

    @app.post("/api/__brief_acceptance/release", include_in_schema=False)
    def release(payload: dict):
        provider.release(payload.get("phase", "initial"))
        return {"released": True}

    frontend_distribution.install_frontend_distribution(app, environ={
        "VOICE_STUDIO_SERVE_FRONTEND": "1",
        "VOICE_STUDIO_FRONTEND_DIST": str(ROOT / "frontend" / "build"),
    })
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=3)


def run(inspect_seconds: int, scenario: str = "brief") -> None:
    if not (ROOT / "frontend/build/index.html").is_file():
        raise RuntimeError("An existing production frontend build is required")
    with tempfile.TemporaryDirectory(prefix="voice-studio-brief-web-") as temporary:
        root = Path(temporary)
        (root / "brief-web-owner").touch()
        (root / "tmp").mkdir()
        port = available_port()
        if port in {5173, 18000, 51992}:
            raise RuntimeError("Refusing a current application service port")
        base = f"http://127.0.0.1:{port}"
        env = isolated_environment(root)
        server = browser = None
        with (root / "service.log").open("w+") as log:
            try:
                server = subprocess.Popen([sys.executable, __file__, "--serve-root", str(root), "--port", str(port), "--scenario", scenario],
                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 40
                while True:
                    try:
                        with urllib.request.urlopen(base + "/api/health", timeout=1):
                            break
                    except OSError:
                        if server.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Isolated brief service failed to start")
                        time.sleep(0.15)
                browser = subprocess.Popen([shutil.which("node") or "node", str(ROOT / "scripts/verify_brief_journal_browser.mjs")],
                    cwd=ROOT, env={**env, "BRIEF_JOURNAL_E2E_URL": base, "BRIEF_JOURNAL_E2E_ARTIFACT_DIR": str(root)},
                    start_new_session=True)
                if browser.wait(timeout=130):
                    raise RuntimeError("Brief journal browser acceptance failed")
                if inspect_seconds:
                    print(json.dumps({"screenshots": str(root), "cleanup_after_seconds": inspect_seconds}), flush=True)
                    time.sleep(inspect_seconds)
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-5000:], file=sys.stderr)
                raise
            finally:
                stop_process_group(browser)
                stop_process_group(server)
    assert not root.exists()
    print(json.dumps({"cleanup": "complete", "temporary_root_removed": True, "service_port": port}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--inspect-seconds", type=int, choices=range(0, 61), default=0)
    parser.add_argument("--scenario", choices=("brief", "evidence", "generation"), default="brief")
    args = parser.parse_args()
    serve(args.serve_root, args.port, args.scenario) if args.serve_root else run(args.inspect_seconds, args.scenario)
