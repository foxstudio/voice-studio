from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndexTTSPreprocessingArtifact:
    artifact_id: str
    repo_id: str
    revision: str
    filename: str
    managed_relative_path: str
    license_spdx: str
    commercial_use: bool


INDEXTTS_BIGVGAN_REPO_ID = "nvidia/bigvgan_v2_22khz_80band_256x"
INDEXTTS_BIGVGAN_REVISION = "633ff708ed5b74903e86ff1298cf4a98e921c513"
INDEXTTS_BIGVGAN_FILENAME = "bigvgan_generator.pt"


INDEXTTS_WAV_PREPROCESSING_ARTIFACTS = (
    IndexTTSPreprocessingArtifact(
        artifact_id="maskgct-semantic-codec",
        repo_id="amphion/MaskGCT",
        revision="265c6cef07625665d0c28d2faafb1415562379dc",
        filename="semantic_codec/model.safetensors",
        managed_relative_path=(
            "preprocessing/amphion-maskgct/semantic_codec/model.safetensors"
        ),
        license_spdx="CC-BY-NC-4.0",
        commercial_use=False,
    ),
    IndexTTSPreprocessingArtifact(
        artifact_id="w2v-bert-config",
        repo_id="facebook/w2v-bert-2.0",
        revision="da985ba0987f70aaeb84a80f2851cfac8c697a7b",
        filename="config.json",
        managed_relative_path="preprocessing/facebook-w2v-bert-2.0/config.json",
        license_spdx="MIT",
        commercial_use=True,
    ),
    IndexTTSPreprocessingArtifact(
        artifact_id="w2v-bert-weights",
        repo_id="facebook/w2v-bert-2.0",
        revision="da985ba0987f70aaeb84a80f2851cfac8c697a7b",
        filename="model.safetensors",
        managed_relative_path=(
            "preprocessing/facebook-w2v-bert-2.0/model.safetensors"
        ),
        license_spdx="MIT",
        commercial_use=True,
    ),
    IndexTTSPreprocessingArtifact(
        artifact_id="w2v-bert-feature-extractor",
        repo_id="facebook/w2v-bert-2.0",
        revision="da985ba0987f70aaeb84a80f2851cfac8c697a7b",
        filename="preprocessor_config.json",
        managed_relative_path=(
            "preprocessing/facebook-w2v-bert-2.0/preprocessor_config.json"
        ),
        license_spdx="MIT",
        commercial_use=True,
    ),
    IndexTTSPreprocessingArtifact(
        artifact_id="campplus-speaker-encoder",
        repo_id="funasr/campplus",
        revision="e4b6ede7ce16997aff4ae69fbca1f0175e2afede",
        filename="campplus_cn_common.bin",
        managed_relative_path=(
            "preprocessing/funasr-campplus/campplus_cn_common.bin"
        ),
        license_spdx="Apache-2.0",
        commercial_use=True,
    ),
)

INDEXTTS_WAV_PREPROCESSING_BY_ID = {
    artifact.artifact_id: artifact
    for artifact in INDEXTTS_WAV_PREPROCESSING_ARTIFACTS
}

INDEXTTS_W2V_BERT_ARTIFACTS = tuple(
    artifact
    for artifact in INDEXTTS_WAV_PREPROCESSING_ARTIFACTS
    if artifact.repo_id == "facebook/w2v-bert-2.0"
)
