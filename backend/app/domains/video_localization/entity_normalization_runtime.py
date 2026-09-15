"""Composition adapter for the entity-normalization atomic service.

The service owns typed input/output and orchestration. The canonical-name
algorithm lives in the current ASR review module, and this adapter is the only
module allowed to connect those two boundaries.
"""

from __future__ import annotations

from app.domains.video_localization import asr_flow, entity_normalization


DEFAULT_ENTITY_NORMALIZATION_SERVICE = (
    entity_normalization.EntityNormalizationService(
        glossary_applier=lambda segments, glossary: (
            asr_flow.apply_explicit_glossary(segments, glossary)
        ),
        researched_entity_normalizer=lambda **kwargs: (
            asr_flow.normalize_researched_entities(**kwargs)
        ),
    )
)
