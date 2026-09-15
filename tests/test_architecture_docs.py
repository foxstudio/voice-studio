from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_agent_architecture_entrypoints_exist_and_point_to_live_sources():
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    index = (ROOT / "docs/architecture/README.md").read_text(encoding="utf-8")
    system = (
        ROOT / "docs/architecture/SYSTEM_ARCHITECTURE.md"
    ).read_text(encoding="utf-8")

    assert "[AGENTS.md](AGENTS.md)" in root_readme
    assert "[架构文档导航](docs/architecture/README.md)" in root_readme
    assert "docs/architecture/README.md" in agents
    assert "docs/architecture/SYSTEM_ARCHITECTURE.md" in agents
    assert "能在 Web 端呈现或触发" in agents
    assert "不得只用单元测试、API、数据库或文件结果代替" in agents
    assert "../../backend/app/domains/video_localization/README.md" in index
    assert "../domains/video_localization/domain-contract.md" in index
    assert "| `SETTINGS_SYSTEM_RFC.md` | proposal |" in index
    assert "| `ENGINE_PROVIDER_POLICY_RFC.md` | in progress |" in index
    assert "backend/app/domains/video_localization/asr_pipeline.py" in system
    assert "backend/app/domains/video_localization/workflow_contracts.py" in system
    assert "`backend/app/schemas`" in system
    assert "`backend/app/errors`" in system
    assert "兼容过渡存在" in system

    for path in (
        ROOT / "docs/architecture/README.md",
        ROOT / "docs/architecture/SYSTEM_ARCHITECTURE.md",
        ROOT / "backend/app/domains/video_localization/README.md",
        ROOT / "docs/domains/video_localization/domain-contract.md",
        ROOT / "backend/app/domains/video_localization/asr_pipeline.py",
        ROOT / "backend/app/domains/video_localization/workflow_contracts.py",
    ):
        assert path.exists(), path


def test_system_architecture_maps_every_top_level_product_page_to_real_backend_owners():
    system = (
        ROOT / "docs/architecture/SYSTEM_ARCHITECTURE.md"
    ).read_text(encoding="utf-8")
    page_routes = (
        "generate",
        "script-studio",
        "voice-library",
        "engine-hub",
        "audio-tools",
        "eval-reference",
        "video-localization",
        "settings",
    )
    backend_entries = (
        "backend/app/api/generate.py",
        "backend/app/api/longform.py",
        "backend/app/api/batches.py",
        "backend/app/api/voices.py",
        "backend/app/api/engines.py",
        "backend/app/api/audio_tools.py",
        "backend/app/api/asr.py",
        "backend/app/api/evaluations.py",
        "backend/app/api/video_localization.py",
        "backend/app/api/settings.py",
    )

    for route in page_routes:
        assert f"`/{route}`" in system
        assert (ROOT / "frontend/src/routes" / route / "+page.svelte").exists()
    for path in backend_entries:
        assert path.removeprefix("backend/app/") in system
        assert (ROOT / path).exists()
