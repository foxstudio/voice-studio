# Video Localization ASR and LLM Design Draft

Status: source-language subtitle pipeline implemented and validated; Chinese
localization remains a downstream stage.

## Product Goal

Build a video-localization workflow that produces accurate source subtitles and
natural Chinese localization. The localized dialogue should feel as if the
original speaker speaks Chinese directly, while preserving personality,
register, habitual phrasing, humor, restraint, and other speaking traits.

## Stable Decisions

1. Qwen3-ASR is the local transcription engine.
2. Qwen3-ForcedAligner is the source of word/character timing truth.
3. Word-level timing must remain available through the subtitle pipeline. Do
   not collapse it into coarse ASR segments before segmentation.
4. The production boundary path combines acoustic pauses, punctuation, ASR
   segment transitions, subtitle constraints, and optional LLM semantic
   review. Speaker changes and shot cuts remain future evidence sources.
5. Cue selection should use deterministic global optimization rather than
   splitting at every pause or delegating all decisions to an LLM.
6. The core workflow remains fully usable offline without an LLM.
7. An LLM is an optional semantic quality layer. It never invents timestamps.
8. TTS, voice cloning, and dubbing synthesis are downstream production stages.
   They are not loaded or required while producing source-language subtitles.
9. `segment-any-text/sat-3l-sm` remains an evaluated offline fallback, not a
   production dependency. It must not be downloaded or loaded implicitly.
10. When clean vocals and the original mix are both available, ASR uses clean
    vocals for recognition and acoustic pause analysis, while forced alignment
    uses the original mix. Store independent track IDs and SHA-256 fingerprints
    for both inputs; never imply they are the same source.

## Processing Layers

```text
source audio/video
  -> Qwen3-ASR draft transcript
  -> conservative transcript review
  -> Qwen3-ForcedAligner word/character timeline
  -> candidate boundaries from acoustic pauses, punctuation, and ASR segments
  -> optional LLM review of ambiguous semantic boundaries
  -> constrained global cue segmentation
  -> platform/language timing and layout QC
  -> source-language subtitles
  -> Chinese localization preserving speaker persona
```

The source-subtitle stage ends after source-language subtitle QC and export.
Only an explicit transition into dubbing production may load IndexTTS,
Qwen3-TTS, F5-TTS, or another speech-synthesis engine.

## LLM Responsibilities

The LLM may:

- review low-confidence ASR words using surrounding context and a glossary;
- restore punctuation and identify likely transcription errors;
- rank ambiguous semantic cue boundaries;
- produce Chinese localization that preserves meaning and speaker persona;
- flag uncertain passages for review.

The LLM must not:

- create or directly edit timestamps;
- rewrite text that was not requested or supported by the source;
- silently add, remove, summarize, or translate source content during ASR
  correction;
- bypass overlap, duration, reading-speed, or line-length constraints.

LLM boundary decisions reference stable word IDs. Transcript changes are
validated as explicit edits and then re-aligned to audio.

## Prompt Families

### transcript-review-v1

Input: source-language tokens, low-confidence spans, nearby context, glossary.

Output: JSON edit operations containing word-ID ranges, replacement text,
reason, and confidence. No translation, summarization, or timestamp fields.

### boundary-review-v1

Input: aligned word IDs, candidate boundary features, pause durations, source
segment changes, punctuation, and nearby context.

Output: selected boundary word IDs, reasons, and confidence. Text and timing
remain unchanged.

### localization-zh-v1

Input: verified source cue groups, surrounding context, speaker profile,
terminology, scene context, and target subtitle constraints.

Output: Chinese localization plus structured notes for intentional adaptation.
The result preserves factual meaning, speaker identity, attitude, recurring
verbal habits, social relationship, humor, and cultural intent. It should sound
spoken rather than translated, without inventing Chinese cultural references
that are absent from the source.

## Generic LLM Provider Configuration

Support multiple OpenAI-compatible provider profiles. Do not hard-code a
DeepSeek URL or model ID.

Each profile stores:

- profile name;
- protocol (`openai_compatible` initially);
- Base URL;
- API Key, stored only in the backend settings database;
- model ID, manually editable;
- enabled/default state.

The UI may retrieve available model IDs from `GET {Base URL}/models` after a
Base URL and key are configured. Manual model entry always remains available
because some compatible providers do not expose a model catalog.

## Model Capability Requirements

- text-only is sufficient for the ASR and localization workflow;
- reliable structured JSON output;
- strong English and Chinese understanding;
- at least 16K context, with 32K preferred;
- stable low-temperature behavior;
- image input, streaming, and function calling are not required.

Shot detection should use deterministic video analysis. A vision LLM may be
added later for optional on-screen text, character identity, or visual-context
disambiguation, but it is not part of the core subtitle workflow.

## Local Model Download Policy

- Prefer an official publisher mirror on ModelScope for users in mainland
  China. Qwen Forced Aligner currently uses
  [`Qwen/Qwen3-ForcedAligner-0.6B`](https://modelscope.cn/models/Qwen/Qwen3-ForcedAligner-0.6B)
  from ModelScope by default. Qwen3-ASR upstream models also prefer the
  publisher's ModelScope repository when the selected runtime uses that exact
  checkpoint format.
- A mirror is only a valid alternative when its model format is compatible
  with the active runtime. The upstream PyTorch checkpoint must not be offered
  as a direct replacement for an MLX-converted checkpoint.
- Keep Hugging Face official and community mirror endpoints as explicit manual
  alternatives. Never silently fall back to an international endpoint.
- Resume only when the mirror serves the byte-identical file with HTTP Range
  support. Otherwise download into a temporary path and replace atomically.
- Verify the expected byte size and SHA-256 before a model becomes available
  to the ASR pipeline. A partial or corrupt checkpoint remains unavailable.
- ASR execution must never start a multi-gigabyte model download implicitly.
  Installation state and runtime health are separate user-visible states.
- SaT currently has no verified publisher-operated ModelScope mirror. The
  community INT8 upload must not become the default until its tokenizer,
  upstream revision, license, and checksums are reproducibly verified. Use the
  pinned upstream ONNX release or a project-maintained verified mirror.
- Speaker diarization should use WeSpeaker CAM++ with Silero VAD in a separate
  runtime when enabled. Prefer the Apache-2.0 CAM++ ModelScope checkpoint; do
  not rerun a second ASR pipeline merely to obtain speaker labels.

## Evaluation

The production pipeline does not require a human SRT. A small validation set is
only used to prove quality and tune weights. Each full timing sample consists
of the original audio/video and a human-reviewed SRT for the same media. SRT
alone can validate formatting but cannot validate audio timing.

Evaluate transcript error rate, cue boundary error, overlap, duration and
reading-speed violations, semantic segmentation quality, and human editing
time. Store rule-profile and prompt versions with evaluation results.

## Implementation Status

Completed for source-language subtitles:

1. Generic LLM provider settings, secret handling, and model discovery.
2. Qwen3-ASR transcription from the clean-vocals track.
3. Conservative LLM review with auditable, glossary-backed edit operations.
4. Qwen3 Forced Aligner word timelines against the original mix.
5. Acoustic pause analysis, LLM boundary review, and deterministic global cue
   segmentation.
6. Timing, overlap, word-coverage, boundary-review, and export quality gates.

Downstream work remains separate:

1. Persona-aware Chinese localization and its review workflow.
2. Optional speaker-change and shot-cut evidence.
3. Broader platform profiles and a representative human-reviewed evaluation
   set.

## Production Validation

On 2026-07-15, the source-subtitle pipeline was validated against an 11:05
real video project:

- 2,067 recognized words, all timed by Qwen3 Forced Aligner;
- 305 exported cues with no overlap, negative duration, media overflow,
  missing word, duplicated word, or ordering error;
- separate fingerprints for the clean-vocals recognition source and original
  mix alignment source;
- six project-glossary corrections accepted with auditable word ranges;
- transcript review, alignment, acoustic boundary analysis, and semantic
  boundary review all completed;
- zero quality-gate blockers; the remaining warning requires human playback
  review of generated cues;
- no TTS engine was started or required.
