"""Engine adapters -- PRD 8 compliant engine abstraction layer."""

# This package intentionally does NOT import adapters at module level.
# Each adapter module is lazy-imported only when needed (inside functions),
# to avoid triggering heavy model loading (e.g., whisper) on package import.
