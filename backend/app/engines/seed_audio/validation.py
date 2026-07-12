from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import SeedAudioReference


MAX_REFERENCE_BYTES = 10 * 1024 * 1024
MAX_REFERENCE_AUDIO_SECONDS = 30.0
SUPPORTED_REFERENCE_AUDIO_FORMATS = frozenset({"wav", "mp3", "pcm", "ogg_opus"})
SUPPORTED_REFERENCE_IMAGE_FORMATS = frozenset({"jpeg", "png", "webp"})
PROMPT_REFERENCE_PATTERN = re.compile(r"@音频(\d+)")


class SeedAudioValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PromptReferenceValidation:
    used: tuple[int, ...]
    unused: tuple[int, ...]
    invalid: tuple[int, ...]

    def raise_for_invalid(self) -> None:
        if not self.invalid:
            return
        labels = "、".join(f"@音频{number}" for number in self.invalid)
        raise SeedAudioValidationError(f"Prompt 引用了不存在的参考声音：{labels}")


def validate_prompt_references(text_prompt: str, *, reference_count: int) -> PromptReferenceValidation:
    if reference_count < 0 or reference_count > 3:
        raise SeedAudioValidationError("参考声音数量必须在 0 到 3 之间")
    mentioned = {int(match.group(1)) for match in PROMPT_REFERENCE_PATTERN.finditer(text_prompt)}
    valid = {number for number in mentioned if 1 <= number <= reference_count}
    invalid = mentioned - valid
    available = set(range(1, reference_count + 1))
    return PromptReferenceValidation(
        used=tuple(sorted(valid)),
        unused=tuple(sorted(available - valid)),
        invalid=tuple(sorted(invalid)),
    )


def validate_reference_constraints(references: list[SeedAudioReference]) -> None:
    """Validate facts known locally without guessing remote-source metadata.

    Mixed audio source kinds in separate slots remain allowed. The official
    documentation does not currently guarantee all mixed combinations, so this
    layer deliberately avoids turning that unknown behavior into a rejection.
    """

    if len(references) > 3:
        raise SeedAudioValidationError("最多支持 3 条参考声音")

    for index, reference in enumerate(references, start=1):
        if reference.size_bytes is not None and reference.size_bytes > MAX_REFERENCE_BYTES:
            label = "参考音频" if reference.is_audio else "参考图片"
            raise SeedAudioValidationError(f"第 {index} 条{label}不能超过 10 MB")
        if reference.is_audio:
            if reference.duration_seconds is not None and reference.duration_seconds > MAX_REFERENCE_AUDIO_SECONDS:
                raise SeedAudioValidationError(f"第 {index} 条参考音频不能超过 30 秒")
            if reference.media_format and reference.media_format.lower() not in SUPPORTED_REFERENCE_AUDIO_FORMATS:
                raise SeedAudioValidationError(f"不支持的参考音频格式：{reference.media_format}")
        elif reference.media_format and reference.media_format.lower() not in SUPPORTED_REFERENCE_IMAGE_FORMATS:
            raise SeedAudioValidationError(f"不支持的参考图片格式：{reference.media_format}")
