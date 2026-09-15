from __future__ import annotations

from app.services import database as db


_HISTORY_AUDIO_COUNTER = "history-audio"


def next_history_audio_sequence() -> int:
    """Allocate one global sequence when an audio download is accepted."""

    with db.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO download_counters (counter_key, value)
            VALUES (?, 1)
            ON CONFLICT(counter_key) DO UPDATE SET value = value + 1
            """,
            (_HISTORY_AUDIO_COUNTER,),
        )
        row = connection.execute(
            "SELECT value FROM download_counters WHERE counter_key = ?",
            (_HISTORY_AUDIO_COUNTER,),
        ).fetchone()
    return int(row["value"])
