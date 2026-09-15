from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_open_source_release import audit_release_files, tracked_files  # noqa: E402


def test_release_gate_rejects_models_user_data_secrets_and_personal_paths(tmp_path):
    files = {
        "models/private/model.safetensors": "weights",
        "config/voice_studio.db": "database",
        ".env": "API_KEY=secret",
        "backend/runtime.py": "ROOT = '/Users/private/VoiceStudio'",
        "frontend/token.ts": "const token = 'sk-abcdefghijklmnopqrstuvwxyz123456'",
    }
    paths = []
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        paths.append(path)

    issues = audit_release_files(paths, root=tmp_path)

    codes = {issue.code for issue in issues}
    assert "forbidden_artifact" in codes
    assert "personal_absolute_path" in codes
    assert "openai_style_secret" in codes


def test_release_gate_allows_examples_code_and_documentation(tmp_path):
    files = {
        ".env.example": "API_KEY=your-api-key",
        "backend/app.py": "DATA_DIR = settings.data_dir",
        "docs/assets/screenshot.png": "not decoded as text",
        "docs/setup.md": "Example only: /Users/example/VoiceStudio",
        "backend/models/catalog.py": "MODEL_ID = 'official/model'",
    }
    paths = []
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        paths.append(path)

    assert audit_release_files(paths, root=tmp_path) == []


def test_release_gate_rejects_personal_paths_in_published_documentation(tmp_path):
    private_doc = tmp_path / "docs" / "internal-notes.md"
    private_doc.parent.mkdir(parents=True)
    private_doc.write_text(
        "Acceptance evidence: /Users/alice/Projects/voice-studio/output/result.png",
        encoding="utf-8",
    )

    issues = audit_release_files([private_doc], root=tmp_path)

    assert [(issue.code, issue.path) for issue in issues] == [
        ("personal_absolute_path", "docs/internal-notes.md")
    ]


def test_release_gate_rejects_personal_paths_in_published_tests(tmp_path):
    published_test = tmp_path / "tests" / "test_local_setup.py"
    published_test.parent.mkdir(parents=True)
    published_test.write_text(
        "LOCAL_MODEL = '/Users/bob/Models/private-model'",
        encoding="utf-8",
    )

    issues = audit_release_files([published_test], root=tmp_path)

    assert [(issue.code, issue.path) for issue in issues] == [
        ("personal_absolute_path", "tests/test_local_setup.py")
    ]


def test_release_gate_requires_legal_security_and_contribution_files(tmp_path):
    license_file = tmp_path / "LICENSE"
    license_file.write_text("project license", encoding="utf-8")

    issues = audit_release_files(
        [license_file],
        root=tmp_path,
        require_governance=True,
    )

    missing = {issue.path for issue in issues if issue.code == "missing_release_governance"}
    assert "THIRD_PARTY_NOTICES.md" in missing
    assert "SECURITY.md" in missing
    assert "CONTRIBUTING.md" in missing
    assert "third_party_licenses/IndexTTS2-bilibili-model-use-license.txt" in missing


def test_release_gate_requires_mlx_indextts_license_and_provenance_record(tmp_path):
    existing_governance = {
        "CONTRIBUTING.md",
        "LICENSE",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "third_party_licenses/3D-Speaker-Apache-2.0.txt",
        "third_party_licenses/Amphion-MIT.txt",
        "third_party_licenses/BigVGAN-MIT.txt",
        "third_party_licenses/IndexTTS2-bilibili-model-use-license.txt",
    }
    files = []
    for relative in existing_governance:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("release governance", encoding="utf-8")
        files.append(path)

    issues = audit_release_files(files, root=tmp_path, require_governance=True)

    missing = {issue.path for issue in issues if issue.code == "missing_release_governance"}
    assert "third_party_licenses/MLX-IndexTTS-MIT.txt" in missing
    assert "docs/engines/mlx-indextts-source-provenance.md" in missing


def test_current_repository_passes_open_source_release_gate():
    assert audit_release_files(tracked_files(), require_governance=True) == []


def test_recovery_guide_does_not_prescribe_destructive_reset_or_stale_current_commit():
    guide = (ROOT / "RECOVERY_POINT.md").read_text(encoding="utf-8")

    assert "git reset --hard" not in guide
    assert "当前恢复点" not in guide
    assert "固定恢复点" not in guide


def test_release_gate_cli_emits_utf8_under_non_unicode_console():
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_open_source_release.py")],
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert result.stdout.decode("utf-8").startswith("\u5f00\u6e90\u53d1\u5e03\u68c0\u67e5\u901a\u8fc7")
