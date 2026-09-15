from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit


MANAGED_INSTALL_KINDS = frozenset(
    {
        "managed_model_download",
        "managed_model_snapshot",
    }
)


@dataclass(frozen=True)
class AutomaticDownloadDecision:
    allowed: bool
    blockers: tuple[str, ...]


def automatic_download_decision(
    source: Mapping[str, Any] | None,
) -> AutomaticDownloadDecision:
    blockers: list[str] = []
    if not source:
        return AutomaticDownloadDecision(False, ("catalog_entry_missing",))

    install_kind = str(source.get("install_kind") or "")
    if install_kind not in MANAGED_INSTALL_KINDS:
        blockers.append("managed_install_not_supported")
    if source.get("model_license_status") != "verified":
        blockers.append("model_license_unverified")
    if not str(source.get("model_license") or "").strip():
        blockers.append("model_license_missing")
    if not _is_https_url(source.get("source_url")):
        blockers.append("official_source_not_https")

    download_sources = source.get("download_sources") or []
    if not download_sources:
        blockers.append("download_source_missing")
    elif any(
        not isinstance(item, Mapping) or not _is_https_url(item.get("url"))
        for item in download_sources
    ):
        blockers.append("download_source_not_https")

    if install_kind == "managed_model_snapshot" and not str(
        source.get("model_revision") or ""
    ).strip():
        blockers.append("model_revision_not_pinned")
    if install_kind == "managed_model_download" and not str(
        source.get("model_sha256") or ""
    ).strip():
        blockers.append("model_checksum_missing")
    if source.get("license_acceptance_required") and not str(
        source.get("license_acceptance_id") or ""
    ).strip():
        blockers.append("license_acceptance_id_missing")
    if source.get("reference_only"):
        blockers.append("reference_only_resource")

    return AutomaticDownloadDecision(not blockers, tuple(blockers))


def _is_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)
