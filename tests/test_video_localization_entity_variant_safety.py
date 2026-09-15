from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import entity_variant_safety  # noqa: E402


def test_rejects_price_and_measurement_phrases_as_name_variants():
    assert not entity_variant_safety.is_safe_proper_name_replacement(
        "Kimi K3",
        "$3 per million tokens",
    )
    assert not entity_variant_safety.is_safe_proper_name_replacement(
        "Kimi K3",
        "2.8 trillion parameters",
    )
    assert not entity_variant_safety.is_safe_proper_name_replacement(
        "Seedance",
        "C-ends should only replace the environment around me.",
    )
    assert not entity_variant_safety.is_safe_proper_name_replacement(
        "Seedance",
        "Hidens actually understands physics.",
    )


def test_accepts_name_like_asr_variant_without_changing_version_numbers():
    assert entity_variant_safety.is_safe_proper_name_replacement(
        "JoAnne Feeney",
        "Duan Feeny",
    )
    assert entity_variant_safety.is_safe_proper_name_replacement(
        "GPT-4",
        "GPT 4",
    )
    assert entity_variant_safety.is_safe_proper_name_replacement(
        "Seedance 2.0",
        "CineSense 2",
    )
    assert entity_variant_safety.is_safe_proper_name_replacement(
        "Seedance 2.0",
        "C-ends",
    )
    assert not entity_variant_safety.is_safe_proper_name_replacement(
        "Kimi K3",
        "Kimi K2",
    )
