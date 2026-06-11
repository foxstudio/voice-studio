# Phase 3 Batch 0 参数 schema 漂移审计

**目的**：在不改实现逻辑的前提下，审计单次生成 (`task_queue._kwargs`)、
批量生成 (`batch_queue._common_kwargs` + `_runner_segments`) 与外部 runner/worker
参数路径（`inference_runner` 与 `f5_worker` / `cosyvoice_worker`）的映射一致性。

## 当前一致项

- **F5**
  - 单次与批量都输出 `speed`、`nfe_step`、`cfg_strength`、`target_rms`、`cross_fade_duration`、`remove_silence`、`seed`，并在批量构造时通过 `common + segment` 透传。
  - 单次与批量 payload 均被 `inference_runner._build_f5_tts_kwargs` 解析为同等值，且 worker 使用的关键字段匹配。

- **CosyVoice zero-shot**
  - 单次与批量都输出 `reference_audio`、`ref_text`、`speed`。
  - 两侧 payload 与 `inference_runner._build_cosyvoice_zero_shot_kwargs` 对齐。

- **IndexTTS v2（基础路径）**
  - 在 `emotion_mode=emotion_vector` / 普通参数场景下，单次与批量对齐了
    `temperature`、`top_p`、`top_k`、`repetition_penalty`、
    `max_text_tokens_per_segment`、`interval_silence`、`segment_overlap_ms`、`speed`、`seed`、`diffusion_steps`、`cfg_rate`、`emotion`、`emo_alpha`。
  - 关键参数可被 `inference_runner._build_indextts_v2_kwargs` 正常接收。

- **MiMo 三个 Profile**
  - `mimo-v2.5-tts-preset`、`mimo-v2.5-tts-voicedesign`、`mimo-v2.5-tts-voiceclone` 在单次/批量中均保留各自 `model` 映射。
  - 两类路径都能带出 `mimo_voice` / `voice` 的入口数据，未出现运行期直接报错。
- **MiMo instruction 映射**
  - 单次路径 (`task_queue._kwargs`) 与批量路径 (`batch_queue._common_kwargs`) 都会基于 `style_instruction` / `emotion_text` / `emotion` 形成统一的 `instruction`。
- **IndexTTS v2 emotion_text 归一化**
  - 在 `emotion_mode=emotion_text` 下，批量路径会将 `emotion_text` 归一为 `emotion`。

## 漂移项（本批未修）

1. **参数命名平面仍不完全统一（Low）**
   - MiMo 在单次侧使用 `voice`、批量侧使用 `mimo_voice`（外部 runner 兼容了这两套，但不属于纯契约一致）。
   - 该差异为历史兼容与迁移窗口问题，建议放在后续 normalization 统一层修复。

## 建议修复顺序

1. 后续如继续扩大引擎能力治理，建立 `Phase 3/4` 统一参数 normalization 层。
2. `MiMo instruction` 与 `IndexTTS emotion_mode=emotion_text` 已完成小步修复，并由契约测试锁住。
3. 再进行参数命名统一（`voice` / `mimo_voice`、`instruction` / `style_instruction` / `emotion_text`）。

## 测试覆盖

- `tests/test_engine_parameter_contract.py::test_f5_single_batch_worker_payload_contract`
  - 覆盖：F5 单次/批量 + `inference_runner._build_f5_tts_kwargs`
- `tests/test_engine_parameter_contract.py::test_cosyvoice_zero_shot_single_batch_worker_contract`
  - 覆盖：CosyVoice zero-shot 单次/批量 + worker/inference builder
- `tests/test_engine_parameter_contract.py::test_indextts_v2_single_batch_contract_for_required_parameters`
  - 覆盖：IndexTTS v2 基础参数集合（用户优先清单内）
- `tests/test_engine_parameter_contract.py::test_mimo_profiles_preserve_independent_profiles`
  - 覆盖：MiMo 三 profile 的模型入口独立性
- `tests/test_engine_parameter_contract.py::test_mimo_top_level_style_instruction_is_normalized_between_single_and_batch`
  - 覆盖：MiMo top-level 风格指令映射一致性
- `tests/test_engine_parameter_contract.py::test_indextts_emotion_text_mode_is_normalized_between_single_and_batch`
  - 覆盖：IndexTTS v2 `emotion_mode=emotion_text` 下的 emotion 归一
