from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_IMMUTABLE_PREFIX = "_app/immutable/"


def _default_distribution_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "frontend" / "build"


def _is_enabled(environ: Mapping[str, str]) -> bool:
    return environ.get("VOICE_STUDIO_SERVE_FRONTEND", "").strip().lower() in _TRUE_VALUES


def _resolve_distribution_dir(environ: Mapping[str, str]) -> Path:
    configured = environ.get("VOICE_STUDIO_FRONTEND_DIST", "").strip()
    return Path(configured).expanduser().resolve() if configured else _default_distribution_dir().resolve()


def install_frontend_distribution(
    app: FastAPI,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Serve the production SPA after all API routes have been registered.

    Development remains unchanged unless ``VOICE_STUDIO_SERVE_FRONTEND`` is
    explicitly enabled. The distribution must already exist so a broken
    release fails during startup instead of returning a partially working UI.
    """

    active_environ = os.environ if environ is None else environ
    if not _is_enabled(active_environ):
        return None

    distribution_dir = _resolve_distribution_dir(active_environ)
    index_file = distribution_dir / "index.html"
    if not index_file.is_file():
        raise RuntimeError(
            "Voice Studio frontend distribution is missing. "
            f"Expected {index_file}. Run the frontend production build first."
        )

    @app.api_route("/{frontend_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def serve_frontend(frontend_path: str):
        normalized_path = frontend_path.lstrip("/")
        if normalized_path == "api" or normalized_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")

        candidate = (distribution_dir / normalized_path).resolve()
        try:
            candidate.relative_to(distribution_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Frontend asset not found") from exc

        if normalized_path and candidate.is_file():
            cache_control = (
                "public, max-age=31536000, immutable"
                if normalized_path.startswith(_IMMUTABLE_PREFIX)
                else "no-cache"
            )
            return FileResponse(candidate, headers={"Cache-Control": cache_control})

        # Asset requests must fail plainly. Returning the SPA shell for a
        # missing script would surface as a confusing browser MIME error.
        if Path(normalized_path).suffix:
            raise HTTPException(status_code=404, detail="Frontend asset not found")

        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    return distribution_dir
