from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationSubtitleCue


@dataclass(frozen=True)
class SourceCueReplacement:
    """One stable source cue produced by a source-track mutation."""

    cue_id: str
    source_word_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceCueChange:
    """How one old source cue was deleted, merged, or split.

    ``replacements`` is empty for deletion, contains one item for a rename or
    merge, and may contain several word-partitioned items for a split.
    """

    cue_id: str
    source_word_ids: tuple[str, ...] = ()
    replacements: tuple[SourceCueReplacement, ...] = ()


@dataclass(frozen=True)
class SubtitleLinkageAudit:
    subtitle_id: str
    source_cue_ids: tuple[str, ...]
    codes: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.codes


@dataclass(frozen=True)
class SubtitleLinkageRemapResult:
    subtitles: tuple[VideoLocalizationSubtitleCue, ...]
    changed_subtitle_ids: tuple[str, ...]
    orphaned_subtitle_ids: tuple[str, ...]
    review_required_subtitle_ids: tuple[str, ...]


def localized_source_cue_ids(subtitle: VideoLocalizationSubtitleCue) -> tuple[str, ...]:
    """Return the canonical stable source IDs for a localized subtitle.

    ``source_cue_ids`` is authoritative once populated. ``linked_cue_id`` is a
    legacy fallback only; combining both would resurrect a stale primary link.
    """

    values: Iterable[str | None]
    if subtitle.source_cue_ids:
        values = subtitle.source_cue_ids
    else:
        values = (subtitle.linked_cue_id,)
    return tuple(dict.fromkeys(value for value in values if value))


def audit_subtitle_linkages(
    cues: Sequence[VideoLocalizationCue],
    subtitles: Sequence[VideoLocalizationSubtitleCue],
) -> tuple[SubtitleLinkageAudit, ...]:
    """Check persistent linkage integrity without inferring links from time."""

    cue_by_id = {cue.cue_id: cue for cue in cues}
    cue_position = {cue.cue_id: index for index, cue in enumerate(cues)}
    canonical_ids_by_subtitle = {
        subtitle.subtitle_id: localized_source_cue_ids(subtitle) for subtitle in subtitles
    }
    subtitle_ids_by_word: dict[str, set[str]] = {}
    subtitle_ids_by_cue: dict[str, set[str]] = {}
    for subtitle in subtitles:
        for word_id in subtitle.source_word_ids:
            subtitle_ids_by_word.setdefault(word_id, set()).add(subtitle.subtitle_id)
        for cue_id in canonical_ids_by_subtitle[subtitle.subtitle_id]:
            subtitle_ids_by_cue.setdefault(cue_id, set()).add(subtitle.subtitle_id)
    audits: list[SubtitleLinkageAudit] = []
    for subtitle in subtitles:
        source_ids = canonical_ids_by_subtitle[subtitle.subtitle_id]
        codes: list[str] = []
        if not source_ids:
            codes.append("source_cues_missing")
        existing_ids = [cue_id for cue_id in source_ids if cue_id in cue_by_id]
        if len(existing_ids) != len(source_ids):
            codes.append("source_cue_not_found")
        if subtitle.source_cue_ids and subtitle.linked_cue_id not in {None, subtitle.source_cue_ids[0]}:
            codes.append("legacy_primary_mismatch")

        positions = sorted(cue_position[cue_id] for cue_id in existing_ids)
        if len(positions) > 1 and any(right != left + 1 for left, right in zip(positions, positions[1:])):
            codes.append("source_cues_noncontiguous")

        speaker_ids = {cue_by_id[cue_id].speaker_id or "unknown" for cue_id in existing_ids}
        if len(speaker_ids) > 1:
            codes.append("source_cues_cross_speaker")

        linked_word_ids = {
            word_id
            for cue_id in existing_ids
            for word_id in cue_by_id[cue_id].source_word_ids
        }
        if subtitle.source_word_ids and linked_word_ids and any(
            word_id not in linked_word_ids for word_id in subtitle.source_word_ids
        ):
            codes.append("source_words_outside_linked_cues")
        if any(len(subtitle_ids_by_word[word_id]) > 1 for word_id in subtitle.source_word_ids):
            codes.append("source_words_multiply_assigned")
        if not subtitle.source_word_ids and any(
            len(subtitle_ids_by_cue[cue_id]) > 1 for cue_id in source_ids
        ):
            codes.append("shared_source_without_word_partition")

        audits.append(
            SubtitleLinkageAudit(
                subtitle_id=subtitle.subtitle_id,
                source_cue_ids=source_ids,
                codes=tuple(dict.fromkeys(codes)),
            )
        )
    return tuple(audits)


def remap_localized_source_links(
    subtitles: Sequence[VideoLocalizationSubtitleCue],
    changes: Sequence[SourceCueChange],
) -> SubtitleLinkageRemapResult:
    """Apply source cue mutations to localized subtitle links.

    Word IDs decide which children of a split remain linked. When word-level
    evidence is unavailable or inconsistent, all split children are retained
    and the subtitle is returned in ``review_required_subtitle_ids`` rather
    than guessing from mutable timeline positions.
    """

    change_by_id = _changes_by_id(changes)
    output: list[VideoLocalizationSubtitleCue] = []
    changed_ids: list[str] = []
    orphaned_ids: list[str] = []
    review_ids: list[str] = []

    for subtitle in subtitles:
        old_ids = localized_source_cue_ids(subtitle)
        subtitle_words = set(subtitle.source_word_ids)
        next_ids: list[str] = []
        selected_replacements_by_old_id: dict[str, tuple[SourceCueReplacement, ...]] = {}
        needs_review = False

        for cue_id in old_ids:
            change = change_by_id.get(cue_id)
            if change is None:
                next_ids.append(cue_id)
                continue
            selected, ambiguous = _select_replacements(change, subtitle_words)
            selected_replacements_by_old_id[cue_id] = selected
            next_ids.extend(item.cue_id for item in selected)
            needs_review = needs_review or ambiguous

        next_ids = list(dict.fromkeys(next_ids))
        next_word_ids = _remapped_word_ids(
            subtitle.source_word_ids,
            change_by_id,
            selected_replacements_by_old_id,
        )
        next_linked_cue_id = next_ids[0] if next_ids else None
        changed = (
            tuple(next_ids) != old_ids
            or next_word_ids != tuple(subtitle.source_word_ids)
            or subtitle.linked_cue_id != next_linked_cue_id
        )
        next_subtitle = subtitle.model_copy(
            update={
                "linked_cue_id": next_linked_cue_id,
                "source_cue_ids": next_ids,
                "source_word_ids": list(next_word_ids),
            }
        ) if changed else subtitle
        output.append(next_subtitle)
        if changed:
            changed_ids.append(subtitle.subtitle_id)
        if old_ids and not next_ids:
            orphaned_ids.append(subtitle.subtitle_id)
        if needs_review:
            review_ids.append(subtitle.subtitle_id)

    return SubtitleLinkageRemapResult(
        subtitles=tuple(output),
        changed_subtitle_ids=tuple(changed_ids),
        orphaned_subtitle_ids=tuple(orphaned_ids),
        review_required_subtitle_ids=tuple(review_ids),
    )


def _changes_by_id(changes: Sequence[SourceCueChange]) -> Mapping[str, SourceCueChange]:
    by_id: dict[str, SourceCueChange] = {}
    for change in changes:
        if not change.cue_id:
            raise ValueError("source cue change requires cue_id")
        if change.cue_id in by_id:
            raise ValueError(f"duplicate source cue change: {change.cue_id}")
        by_id[change.cue_id] = change
    return by_id


def _select_replacements(
    change: SourceCueChange,
    subtitle_word_ids: set[str],
) -> tuple[tuple[SourceCueReplacement, ...], bool]:
    replacements = tuple(item for item in change.replacements if item.cue_id)
    if len(replacements) <= 1:
        return replacements, False
    if not subtitle_word_ids:
        return replacements, True
    matched = tuple(
        item for item in replacements if subtitle_word_ids.intersection(item.source_word_ids)
    )
    if matched:
        return matched, False
    return replacements, True


def _remapped_word_ids(
    source_word_ids: Sequence[str],
    change_by_id: Mapping[str, SourceCueChange],
    selected_replacements_by_old_id: Mapping[str, tuple[SourceCueReplacement, ...]],
) -> tuple[str, ...]:
    retained: list[str] = []
    for word_id in source_word_ids:
        keep = True
        for old_cue_id, selected in selected_replacements_by_old_id.items():
            change = change_by_id[old_cue_id]
            if word_id not in change.source_word_ids:
                continue
            selected_word_ids = {value for item in selected for value in item.source_word_ids}
            if word_id not in selected_word_ids:
                keep = False
                break
        if keep:
            retained.append(word_id)
    return tuple(dict.fromkeys(retained))
