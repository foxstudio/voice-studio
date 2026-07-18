# Speaker diarization architecture

## Decision

MOSS-Transcribe-Diarize is an optional diarization sidecar. It does not replace Qwen3-ASR, text review, Qwen forced alignment, or the existing audio-boundary subtitle segmentation pipeline.

The production order is:

1. Primary ASR produces the transcript.
2. MOSS produces anonymous speaker ranges (`S01`, `S02`, ...).
3. CAM++ compares representative clean ranges and conservatively merges likely false splits.
4. Speaker ranges are mapped to forced-aligned words by time overlap.
5. Subtitle segmentation may not cross a confirmed speaker-cluster boundary.
6. Anonymous acoustic clusters are bound to stable project speakers (`speaker_01`, ...).
7. Localization context and web research may propose identity candidates, topics, and background evidence, but may not silently turn a candidate into a confirmed identity.

MOSS and CAM++ run in independent external Python environments under `~/VoiceStudio/engines`. Their models live under `~/VoiceStudio/models`. The main application environment does not import either runtime.

Managed locations:

- MOSS runtime: `~/VoiceStudio/engines/moss-transcribe-diarize`
- MOSS model: `~/VoiceStudio/models/moss-transcribe-diarize-8bit`
- CAM++ runtime: `~/VoiceStudio/engines/campplus-speaker-verifier`
- CAM++ model: `~/VoiceStudio/models/campplus-speaker-verifier`

The managed virtual environments use the stable UV Python installation under
`~/.local/share/uv/python`; they must not point at a POC directory under `/tmp`.

## POC evidence

Tested on 2026-07-18 with `vanch007/mlx-MOSS-Transcribe-Diarize-8bit` and `iic/speech_campplus_sv_zh-cn_16k-common`.

- 345 seconds of audio: warm aggregate RTF `0.0444`.
- Alternating two-speaker sample: 12 of 12 turns retained stable labels.
- Three expected single-speaker samples: two remained single-speaker; the intro was falsely split at a scene/acoustic change.
- CAM++ centroid cosine for the two false-split intro clusters: `0.8146` in the POC and `0.7671` in the managed-runtime smoke test.
- Different synthetic speakers (Samantha and Daniel) centroid cosine: `0.5207`.
- Overlapping speech is not reliable: the added secondary voice was omitted entirely.

Managed-runtime end-to-end checks:

- Alternating two-speaker sample: 12 segments remained as 2 clusters. CAM++ cosine
  `0.6119` was intentionally kept in the review band rather than auto-merged.
- Single-speaker intro: MOSS split `S01/S02`; CAM++ cosine `0.7671` merged all
  24 segments into 1 cluster.
- The first run after rebuilding the MLX environment took `125.4s`; the following
  75-second sample took `13.4s`. The task UI should expose diarization as a distinct
  stage so model/Metal cold start does not look like a frozen ASR task.

Initial project thresholds are deliberately conservative:

- `>= 0.75`: auto-merge acoustic clusters.
- `0.60-0.75`: keep separate and require review.
- `< 0.60`: keep separate.

These are POC calibration values, not universal speaker-verification thresholds. Store similarity evidence and keep the thresholds configurable.

## Download provenance

- No verified ModelScope mirror was found for the MLX 8bit MOSS model.
- `hf-mirror` downloaded the 1,258,427,152-byte main weight blob but failed on metadata requests.
- The official Hugging Face endpoint reused that blob and filled only the missing small files; the blob hash and inode did not change.
- CAM++ was downloaded successfully from ModelScope.

Mirror failure must be visible. Do not silently switch sources or report a partial snapshot as installed.

## Data boundaries

- `speaker_cluster_id`: anonymous acoustic grouping for this transcription run.
- `speaker_id`: stable project-level person used by localization and voice routing.
- identity candidate: a researched name with confidence and evidence source IDs.
- confirmed identity: a human-confirmed business fact; neither MOSS nor CAM++ can produce it.

Knowledge enrichment remains non-blocking evidence. Search failure must not fail ASR or erase speaker timing.

## Extension contract

New primary ASR engines implement `AsrProvider`; new diarization engines implement
`DiarizationProvider`. Provider outputs use milliseconds and anonymous
`speaker_cluster` labels. Verification, project-level speaker binding, forced
alignment, subtitle segmentation, and identity research stay outside provider
adapters, so replacing a model does not replace the rest of the workflow.
