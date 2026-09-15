"""Stable schema import surface for new backend code.

The implementation still lives in ``app.models.schemas`` during the
compatibility window. Keep class identity stable until imports are migrated.
"""

from app.models.schemas import *  # noqa: F403
