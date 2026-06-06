"""Engine adapters -- PRD 8 compliant engine abstraction layer."""

try:
    from .omnivoice_adapter import OmniVoiceAdapter  # noqa: F401
except ImportError:
    OmniVoiceAdapter = None  # type: ignore[assignment]
try:
    from .v1_adapter import V1Adapter  # noqa: F401
except ImportError:
    V1Adapter = None  # type: ignore[assignment]


__all__ = [
    name for name in ["OmniVoiceAdapter", "V1Adapter"]
    if globals().get(name) is not None
]
