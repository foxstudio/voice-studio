from __future__ import annotations

from copy import deepcopy
import importlib
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
SCRIPT = ROOT / "scripts" / "audit_video_localization_architecture.py"
POLICY = ROOT / "scripts" / "architecture" / "video_localization_architecture_policy.json"


def _load_audit_module():
    spec = importlib.util.spec_from_file_location(
        "audit_video_localization_architecture",
        SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_reference_inventory_resolves_local_aliases_and_concatenation(
    tmp_path: Path,
):
    module = _load_audit_module()
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_routes.py").write_text(
        """
def test_routes(client, project_id):
    base = f"/api/projects/{project_id}"
    path = base + "/video-localization/export/render"
    client.post(path)
    client.post(
        f"/api/projects/{project_id}/video-localization/"
        "source-media/preview-cache"
    )
""",
        encoding="utf-8",
    )

    assert module._test_http_references(tests_root) == [
        {
            "method": "POST",
            "path": ("/api/projects/{}/video-localization/export/render"),
            "file": "tests/test_routes.py",
            "line": 5,
        },
        {
            "method": "POST",
            "path": ("/api/projects/{}/video-localization/source-media/preview-cache"),
            "file": "tests/test_routes.py",
            "line": 6,
        },
    ]


def test_shared_tts_entrypoints_use_the_handoff_application_boundary():
    module = _load_audit_module()
    modules = module._python_modules(BACKEND)
    known_modules = set(modules)

    for module_name in (
        "app.api.generate",
        "app.services.longform_queue",
        "app.services.task_queue",
    ):
        imports = module._python_imports(
            module_name,
            modules[module_name],
            known_modules=known_modules,
        )
        assert (
            "app.services.video_localization_tts_handoff"
            in imports
        )
        assert (
            "app.domains.video_localization.service"
            not in imports
        )


def test_architecture_baseline_report_is_internally_consistent():
    module = _load_audit_module()

    report = module.build_report(ROOT)

    assert report["schema_version"] == ("video-localization-architecture-baseline-v1")
    backend = report["backend"]
    assert backend["domain_file_count"] == len(backend["modules"])
    assert backend["domain_line_count"] == sum(item["line_count"] for item in backend["modules"])
    assert backend["api"]["route_count"] == len(backend["api"]["routes"])
    assert set(backend["api"]["files"]) == {
        "video_localization",
        "projects",
        "history",
        "tasks",
        "batches",
        "longform",
    }
    assert backend["api"]["async_route_count"] == sum(bool(item["async"]) for item in backend["api"]["routes"])
    assert backend["api"]["async_without_await_count"] == len(backend["api"]["async_without_await"])
    assert backend["api"]["async_unprotected_app_call_count"] == len(backend["api"]["async_unprotected_app_calls"])
    assert backend["api"]["async_without_await"] == []
    assert backend["api"]["async_unprotected_app_calls"] == []
    assert sum(backend["api"]["execution_class_counts"].values()) == (backend["api"]["route_count"])
    assert backend["api"]["route_test_reference_count"] == sum(
        bool(item["test_references"]) for item in backend["api"]["routes"]
    )
    assert backend["api"]["route_test_reference_count"] > 0
    assert set(backend["api"]["routes_without_test_references"]) == {
        item["qualified_name"] for item in backend["api"]["routes"] if not item["test_references"]
    }
    assert all(
        {
            "qualified_name",
            "full_paths",
            "execution_class",
            "local_helper_dependencies",
            "business_owners",
            "route_level_authorization",
            "declared_errors",
            "test_references",
        }.issubset(item)
        for item in backend["api"]["routes"]
    )
    project_update = next(
        item for item in backend["api"]["routes"] if item["qualified_name"] == "projects:update_project"
    )
    assert project_update["full_paths"] == ["/api/projects/{project_id}"]
    assert project_update["business_owners"] == [
        "app.domains.video_localization.service",
        "app.services.project_store",
    ]
    assert project_update["route_level_authorization"] == {
        "declared": False,
        "dependencies": [],
        "scope": "route-level-static-only",
    }
    assert project_update["declared_errors"] == [
        {"status": 404, "code": "PROJECT_NOT_FOUND"},
        {"status": 409, "code": "PROJECT_REVISION_CONFLICT"},
    ]
    assert project_update["test_references"]
    history_waveform = next(
        item for item in backend["api"]["routes"] if item["qualified_name"] == "history:get_waveform"
    )
    assert history_waveform["local_helper_dependencies"] == ["_history_waveform"]
    assert history_waveform["business_owners"] == [
        "app.services.history_store",
        "app.services.waveform_cache",
    ]
    assert history_waveform["declared_errors"] == [
        {"status": 404, "code": "AUDIO_NOT_FOUND"},
    ]
    assert all("__init__" not in cycle for cycle in backend["runtime_cycles"])
    asr_cycle_modules = {
        "asr_flow",
        "entity_normalization",
        "section_review",
    }
    assert all(not asr_cycle_modules.intersection(cycle) for cycle in backend["runtime_cycles"])
    assert backend["runtime_cycles"] == []
    assert backend["package_runtime_cycles"] == []

    frontend = report["frontend"]
    assert frontend["production_file_count"] == len(frontend["production_files"])
    assert set(frontend["unreachable_svelte_components"]).issubset(frontend["unreachable_from_page"])
    assert frontend["source_string_test_count"] == len(frontend["source_string_tests"])
    assert frontend["component_behavior_test_count"] == len(frontend["component_behavior_tests"])
    assert "Backend domain:" in module.format_text(report)


def test_domain_package_keeps_public_exports_lazy_and_compatible():
    package = importlib.import_module("app.domains.video_localization")

    assert "evaluate_quality_gate" not in vars(package)
    assert "build_production_readiness_audit" not in vars(package)
    assert callable(package.evaluate_quality_gate)
    assert callable(package.build_production_readiness_audit)


def test_asr_data_contracts_are_runtime_neutral_and_use_canonical_modules():
    asr_pipeline = importlib.import_module("app.domains.video_localization.asr_pipeline")
    document_contracts = importlib.import_module("app.domains.video_localization.document_understanding_contracts")
    llm_contracts = importlib.import_module("app.domains.video_localization.llm_contracts")
    llm_observability = importlib.import_module("app.domains.video_localization.llm_observability")

    assert not hasattr(
        asr_pipeline,
        "AsrDocumentUnderstandingResult",
    )
    assert llm_observability.AsrLlmCallRecord is llm_contracts.AsrLlmCallRecord

    document_source = (
        ROOT / "backend" / "app" / "domains" / "video_localization" / "document_understanding_contracts.py"
    ).read_text(encoding="utf-8")
    llm_source = (ROOT / "backend" / "app" / "domains" / "video_localization" / "llm_contracts.py").read_text(
        encoding="utf-8"
    )
    assert "asr_pipeline" not in document_source
    assert "app.services" not in document_source
    assert "app.services" not in llm_source

    for module_name in (
        "research_evidence.py",
        "section_review.py",
        "visual_evidence.py",
        "whole_recheck.py",
    ):
        source = (ROOT / "backend" / "app" / "domains" / "video_localization" / module_name).read_text(encoding="utf-8")
        assert "asr_pipeline" not in source


def test_current_architecture_satisfies_reviewed_policy():
    module = _load_audit_module()

    report = module.build_report(ROOT)
    policy = module.load_policy(POLICY)

    assert module.policy_violations(report, policy) == []
    assert "PASS" in module.format_policy_result([])


def test_architecture_policy_rejects_each_new_debt_shape():
    module = _load_audit_module()
    report = deepcopy(module.build_report(ROOT))
    policy = module.load_policy(POLICY)

    report["backend"]["runtime_cycles"].append(["new_domain_a", "new_domain_b"])
    report["backend"]["package_runtime_cycles"].append(["__init__", "new_package_cycle"])
    report["backend"]["cross_layer_importers"]["app.api.new_route"] = ["service"]
    report["backend"]["api"]["async_without_await"].append("new_blocking_async_route")
    report["backend"]["api"]["async_unprotected_app_calls"].append(
        "new_async_route:video_localization_service.blocking_call"
    )
    report["frontend"]["unreachable_svelte_components"].append("NewDeadPanel.svelte")
    report["frontend"]["source_string_tests"].append("new-source-string.test.ts")

    violations = module.policy_violations(report, policy)

    assert {item["code"] for item in violations} == {
        "new-runtime-cycle",
        "new-package-runtime-cycle",
        "new-cross-layer-importer",
        "new-async-without-await",
        "new-async-unprotected-app-call",
        "new-unreachable-svelte-component",
        "new-source-string-test",
    }
    assert "FAIL" in module.format_policy_result(violations)


def test_architecture_policy_allows_existing_debt_to_be_removed():
    module = _load_audit_module()
    report = deepcopy(module.build_report(ROOT))
    policy = module.load_policy(POLICY)

    report["backend"]["runtime_cycles"] = []
    report["backend"]["package_runtime_cycles"] = []
    report["backend"]["cross_layer_importers"] = {}
    report["backend"]["api"]["async_without_await"] = []
    report["backend"]["api"]["async_unprotected_app_calls"] = []
    report["frontend"]["unreachable_svelte_components"] = []
    report["frontend"]["source_string_tests"] = []

    assert module.policy_violations(report, policy) == []
