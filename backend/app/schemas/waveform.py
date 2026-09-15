from pydantic import BaseModel


class WaveformPeaksResponse(BaseModel):
    peaks: list[float]
    duration: float
    bins: int
    window_start_ms: int | None = None
    window_end_ms: int | None = None
