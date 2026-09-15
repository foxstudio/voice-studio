"""A continuous voiced take does not need a fabricated silence interval."""
import sys
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.video_localization.dubbing_production_run import _processed_candidate_projection_is_current


def test_continuous_take_is_complete_without_silence_but_not_without_words():
    audio = {'aligned_words': [SimpleNamespace(word_id='w1', text='你好', start_ms=0, end_ms=1000)],
             'gap_evidence': [], 'speech_start_ms': 0, 'speech_end_ms': 1000}
    report = {'overall_status': 'warning', 'audio_evidence': audio}
    clips = [{'clip_id': 'clip', 'start_ms': 0, 'end_ms': 1000, 'source_start_ms': 0, 'source_end_ms': 1000}]
    assert _processed_candidate_projection_is_current(report=report, candidate_clips=clips)
    cropped = [{**clips[0], 'source_end_ms': 500, 'end_ms': 500}]
    assert not _processed_candidate_projection_is_current(report=report, candidate_clips=cropped)
    audio['aligned_words'] = []
    assert not _processed_candidate_projection_is_current(report=report, candidate_clips=clips)
