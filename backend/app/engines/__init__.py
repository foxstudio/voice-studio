"""Opt-in engine adapters for model-specific request contracts.

This package is intentionally separate from the legacy task queue.  Adapters
are not active until a caller explicitly registers and invokes them.
"""

from .base import EngineAdapter
from .registry import AdapterRegistry

__all__ = ["AdapterRegistry", "EngineAdapter"]
