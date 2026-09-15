from __future__ import annotations

from app.services import vibevoice_model


def test_all_vibevoice_file_specs_use_complete_sha256_digests() -> None:
    specs = [item for variant in vibevoice_model.VARIANTS.values() for item in variant.files.values()] + list(
        vibevoice_model.TOKENIZER_FILES.values()
    )

    assert all(len(item.sha256) == 64 for item in specs)


def test_vibevoice_variants_share_one_managed_tokenizer_directory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        vibevoice_model,
        "family_root",
        lambda: tmp_path / "models" / "vibevoice-asr",
    )

    assert vibevoice_model.variant_dir("4bit") == (
        tmp_path / "models" / "vibevoice-asr" / "4bit"
    )
    assert vibevoice_model.variant_dir("8bit") == (
        tmp_path / "models" / "vibevoice-asr" / "8bit"
    )
    assert vibevoice_model.variant_dir("official") == (
        tmp_path / "models" / "vibevoice-asr" / "official"
    )
    assert vibevoice_model.tokenizer_dir() == (
        tmp_path / "models" / "vibevoice-asr" / "tokenizer"
    )


def test_modelscope_download_urls_are_pinned_for_mlx_variants() -> None:
    for variant_id in ("4bit", "8bit"):
        spec = vibevoice_model.VARIANTS[variant_id]
        url = vibevoice_model._modelscope_file_url(
            spec.repo_id,
            spec.revision,
            "config.json",
        )
        assert url.startswith("https://modelscope.cn/")
        assert f"Revision={spec.revision}" in url
        assert "FilePath=config.json" in url


def test_cleanup_removes_variant_and_shared_tokenizer_parts(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "models" / "vibevoice-asr"
    part_root = root / ".downloads"
    part_root.mkdir(parents=True)
    (part_root / "4bit-config.json.part").write_bytes(b"variant")
    (part_root / "tokenizer-tokenizer.json.part").write_bytes(b"shared")
    monkeypatch.setattr(vibevoice_model, "family_root", lambda: root)

    vibevoice_model._cleanup_download_parts("4bit")

    assert not part_root.exists()


def test_download_part_scope_excludes_other_variants() -> None:
    assert vibevoice_model._download_part_prefixes("4bit") == {
        "4bit-",
        "tokenizer-",
    }
    assert "8bit-" not in vibevoice_model._download_part_prefixes("4bit")
    assert vibevoice_model._download_part_prefixes("official") == {"official-"}
