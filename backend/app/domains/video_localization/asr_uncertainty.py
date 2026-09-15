"""Pure shared projection of saved ASR uncertainty onto aligned word identities."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.domains.video_localization.schemas import VideoLocalizationTranscriptionState
from app.schemas.asr_uncertainty import AsrReviewDecisionWarning

UNRESOLVED_TEXT_FLAG = "asr_unresolved_text"
UNRESOLVED_SCOPE_FLAG = "asr_unresolved_scope_unknown"


def transcript_segment_text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AsrUncertaintyProjection:
    unresolved_word_ids: frozenset[str]
    scope_unknown_word_ids: frozenset[str]
    reviewed_word_ids: frozenset[str]

    def cue_flags(self, flags: list[str], word_ids: list[str]) -> list[str]:
        """Replace only uncertainty flags, preserving the other flags' order."""
        selected = set(word_ids)
        result = [flag for flag in flags if flag not in {UNRESOLVED_TEXT_FLAG, UNRESOLVED_SCOPE_FLAG}]
        if selected & self.unresolved_word_ids:
            result.append(UNRESOLVED_TEXT_FLAG)
        if selected & self.scope_unknown_word_ids or (
            {UNRESOLVED_TEXT_FLAG, UNRESOLVED_SCOPE_FLAG}.intersection(flags)
            and not selected.intersection(self.reviewed_word_ids)
        ):
            result.append(UNRESOLVED_SCOPE_FLAG)
        return result


def _tokens(text: str) -> list[str]:
    return [value.casefold().replace("’", "'") for value in re.findall(
        r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?|[\u3400-\u9fff\u3040-\u30ff]|[^\W_]+", text,
    )]


_NOTE_FIELD = re.compile(r'''\s*(\w+)=('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")''')


def _saved_warning_note(value: object) -> AsrReviewDecisionWarning | None:
    # Historical notes are Pydantic's string representation, not executable
    # Python. Accept a complete sequence of known string fields only. Parsing
    # an AST Constant never evaluates names, calls, expressions or attributes.
    if not isinstance(value, str) or len(value) > 32_768 or not value.startswith("code="):
        return None
    fields = {}
    cursor = 0
    for match in _NOTE_FIELD.finditer(value):
        if match.start() != cursor or match.group(1) in fields:
            return None
        try:
            node = ast.parse(match.group(2), mode="eval").body
        except (SyntaxError, ValueError):
            return None
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            return None
        fields[match.group(1)] = node.value
        cursor = match.end()
    if cursor != len(value) or set(fields) != {"code", "issue_id", "segment_id", "excerpt", "message"}:
        return None
    if any(marker in fields["excerpt"] or marker in fields["message"] for marker in ("…", "...", "[truncated]")):
        return None
    try:
        return AsrReviewDecisionWarning.model_validate(fields)
    except ValidationError:
        return None


def _legacy_warnings(transcription: VideoLocalizationTranscriptionState) -> tuple[list[AsrReviewDecisionWarning], set[str]]:
    cycle = transcription.transcript_quality_cycle
    if not isinstance(cycle, dict):
        return [], set()
    warnings = []
    invalid_segments: set[str] = set()
    raw_warnings = cycle.get("warnings", [])
    for raw in raw_warnings if isinstance(raw_warnings, list) else []:
        try:
            warning = AsrReviewDecisionWarning.model_validate(raw)
        except (ValidationError, TypeError):
            continue
        if warning.code == "needs_confirmation" and warning.segment_id and (
            not warning.excerpt.strip() or any(marker in warning.excerpt or marker in warning.message
                                             for marker in ("…", "...", "[truncated]"))
        ):
            invalid_segments.add(warning.segment_id)
        else:
            warnings.append(warning)
    steps = cycle.get("task_step_results", {})
    if isinstance(steps, dict):
        for step_id, step in steps.items():
            if not re.fullmatch(r"review_decisions_r\d+", step_id) or not isinstance(step, dict):
                continue
            debug = step.get("debug", {})
            if not isinstance(debug, dict):
                continue
            notes = debug.get("notes", [])
            for note in notes if isinstance(notes, list) else []:
                warning = _saved_warning_note(note)
                if warning is not None:
                    warnings.append(warning)
                elif isinstance(note, str) and note.startswith("code="):
                    # A damaged diagnostic cannot be used to clear coarse
                    # legacy uncertainty, even if another scope was recovered.
                    invalid_segments.update(segment.segment_id for segment in transcription.segments
                                            if UNRESOLVED_TEXT_FLAG in segment.review_flags)
    return warnings, invalid_segments


def project_asr_uncertainty(transcription: VideoLocalizationTranscriptionState) -> AsrUncertaintyProjection:
    """Resolve new spans and explicit legacy evidence without changing history."""
    words_by_segment = {}
    for word in transcription.words:
        words_by_segment.setdefault(word.segment_id, []).append(word)
    warnings_by_segment = {}
    legacy_warnings, invalid_segments = _legacy_warnings(transcription)
    for warning in legacy_warnings:
        if warning.code == "needs_confirmation" and warning.segment_id and warning.excerpt.strip():
            warnings_by_segment.setdefault(warning.segment_id, []).append(warning)
    unresolved, unknown, reviewed = set(), set(), set()
    for segment in transcription.segments:
        words = words_by_segment.get(segment.segment_id, [])
        word_ids = {word.word_id for word in words}
        flattened = [(token, word.word_id) for word in words for token in _tokens(word.text)]
        # Merge, never choose between, independently saved uncertainty sources.
        scopes: list[tuple[str, str, bool]] = []
        text_sha = transcript_segment_text_fingerprint(segment.corrected_text or segment.raw_text)
        for span in segment.unconfirmed_text_spans or []:
            scopes.append((span.excerpt, span.match_policy,
                span.segment_id == segment.segment_id and span.segment_text_sha256 == text_sha))
        scopes.extend((operation.source_text, "unique", True) for operation in segment.review_operations
                      if operation.status == "rejected" and operation.source_text.strip())
        scopes.extend((warning.excerpt, "unique", True) for warning in warnings_by_segment.get(segment.segment_id, []))
        managed = bool(scopes or segment.unconfirmed_text_spans is not None
                       or segment.segment_id in invalid_segments
                       or {UNRESOLVED_TEXT_FLAG, UNRESOLVED_SCOPE_FLAG}.intersection(segment.review_flags))
        if not managed:
            continue
        reviewed.update(word_ids)
        if segment.segment_id in invalid_segments:
            unknown.update(word_ids)
        if not scopes and {UNRESOLVED_TEXT_FLAG, UNRESOLVED_SCOPE_FLAG}.intersection(segment.review_flags):
            unknown.update(word_ids)
        for excerpt, policy, valid in set(scopes):
            target = _tokens(excerpt)
            if not valid or not target:
                unknown.update(word_ids)
                continue
            matches = [set(word_id for _, word_id in flattened[start:start + len(target)])
                for start in range(len(flattened) - len(target) + 1)
                if [token for token, _ in flattened[start:start + len(target)]] == target]
            if not matches:
                unknown.update(word_ids)
            elif len(matches) == 1 or policy == "all_occurrences":
                unresolved.update(set().union(*matches))
            else:
                unknown.update(set().union(*matches))
    return AsrUncertaintyProjection(frozenset(unresolved), frozenset(unknown), frozenset(reviewed))
