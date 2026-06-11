"""Voice Studio request/response schemas.

Compatibility facade for the Phase 4 directory governance migration. New code
may import from here while existing ``app.models.schemas`` imports continue to
work unchanged.
"""

from app.models.schemas import *  # noqa: F403
