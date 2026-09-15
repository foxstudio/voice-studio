"""MLX-IndexTTS public package.

The heavyweight MLX runtime is imported lazily so packaging tools, help
commands, and Windows/WSL preflight can inspect the package without first
loading an accelerator backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mlx_indextts.version import __version__


if TYPE_CHECKING:
    from mlx_indextts.generate import IndexTTS as IndexTTS

__all__ = ["IndexTTS", "__version__"]


def __getattr__(name: str) -> Any:
    if name == "IndexTTS":
        from mlx_indextts.generate import IndexTTS

        return IndexTTS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
