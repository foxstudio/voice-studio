"""Stable, lazy public exports for the video-localization domain."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["build_production_readiness_audit", "evaluate_quality_gate"]

_PUBLIC_EXPORT_MODULES = {
    "build_production_readiness_audit": (
        "app.domains.video_localization.readiness"
    ),
    "evaluate_quality_gate": (
        "app.domains.video_localization.quality_gate"
    ),
}


def __getattr__(name: str) -> Any:
    module_name = _PUBLIC_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_name), name)
