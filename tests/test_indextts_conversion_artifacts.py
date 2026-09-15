from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mlx_indextts import convert_v2  # noqa: E402
from mlx_indextts.model_artifacts import (  # noqa: E402
    INDEXTTS_WAV_PREPROCESSING_ARTIFACTS,
)


def test_conversion_installs_pinned_wav_assets_inside_model_directory(tmp_path):
    downloads = tmp_path / "downloads"
    calls: list[tuple[str, str, str]] = []

    def downloader(repo_id: str, *, filename: str, revision: str) -> str:
        calls.append((repo_id, filename, revision))
        source = downloads / repo_id.replace("/", "--") / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"{repo_id}:{filename}:{revision}".encode())
        return str(source)

    output = tmp_path / "mlx-indexTTS-2.0"
    installed = convert_v2.install_wav_preprocessing_artifacts(
        output,
        downloader=downloader,
    )

    assert calls == [
        (artifact.repo_id, artifact.filename, artifact.revision)
        for artifact in INDEXTTS_WAV_PREPROCESSING_ARTIFACTS
    ]
    assert installed == [
        output / artifact.managed_relative_path
        for artifact in INDEXTTS_WAV_PREPROCESSING_ARTIFACTS
    ]
    assert all(path.is_file() for path in installed)
