from __future__ import annotations

from pydantic import BaseModel, Field


class ManagedModelInstallRequest(BaseModel):
    accepted_license_id: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "The exact model-license identifier accepted by the user. "
            "Required only for downloads whose catalog entry says so."
        ),
    )
