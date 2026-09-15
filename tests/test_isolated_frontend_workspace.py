from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from isolated_frontend_workspace import prepare_frontend_workspace


def test_workspace_does_not_reuse_generated_config_or_private_environment(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("node_modules", ".svelte-kit", "build", "src"):
        (source / name).mkdir()
        (source / name / "sentinel").write_text(name)
    (source / ".env").write_text("PRIVATE=value")
    (source / "tsconfig.json").write_text('{"extends":"./.svelte-kit/tsconfig.json"}')
    workspace = prepare_frontend_workspace(source, tmp_path / "validation")
    assert (workspace / "src/sentinel").read_text() == "src"
    assert not (workspace / "node_modules").is_symlink()
    (workspace / "node_modules/sentinel").write_text("isolated")
    assert (source / "node_modules/sentinel").read_text() == "node_modules"
    assert not (workspace / ".env").exists()
    assert not (workspace / ".svelte-kit").exists()
    assert not (workspace / "build").exists()
    (workspace / ".svelte-kit").mkdir()
    (workspace / ".svelte-kit/tsconfig.json").write_text("{}")
    assert not (source / ".svelte-kit/tsconfig.json").exists()
    assert (source / ".svelte-kit/sentinel").read_text() == ".svelte-kit"


def test_workspace_refuses_existing_or_source_directories(tmp_path):
    source = tmp_path / "source"
    (source / "node_modules").mkdir(parents=True)
    with pytest.raises(ValueError):
        prepare_frontend_workspace(source, source / "validation")
    with pytest.raises(FileExistsError):
        prepare_frontend_workspace(source, tmp_path)
