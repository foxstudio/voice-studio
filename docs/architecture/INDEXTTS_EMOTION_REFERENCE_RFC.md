# IndexTTS 独立情绪参考架构契约

**状态**：Implemented

**适用范围**：IndexTTS v2 单条、批量、长文本和生成历史

**设计目标**：在不改变默认音色克隆行为的前提下，让音色参考和情绪参考可以来自不同音频，并为后续多参考、逐段情绪和自动标签保留稳定扩展点。

## 1. 不可破坏的不变量

1. `follow_reference` 是默认模式。同一条主参考音频同时提供音色和情绪，结果路径不得因本功能发生变化。
2. 主参考音频始终负责 speaker、prompt、mel 和 style；独立情绪音频只负责 emotion embedding，不能替换主音色条件。
3. `emotion_vector` 与 `emotion_reference` 互斥。调用方传入矛盾参数时必须明确失败，不能静默选一个。
4. 独立情绪插值固定为 `base + alpha * (target - base)`；`alpha=0` 等于原音色情绪，`alpha=1` 等于完整目标情绪。
5. 上传或裁切后的受管文件，只能在任务、批次、长文本、历史、预设和音色库都不再引用时回收。

## 2. 分层边界

### 2.1 前端状态层

目录：`frontend/src/routes/generate/engine-ui/indextts-v2/`

- `state.ts`：只管理 IndexTTS 情绪参考草稿，不耦合任务 API。
- `request.ts`：负责 UI 状态和稳定请求字段之间的双向转换。
- `validation.ts`：负责“是否可以提交”的用户侧校验。
- `EmotionReferencePanel.svelte`：只编排来源选择、强度和通用范围编辑器。
- `ReferenceAudioRangeEditor.svelte`：共享音频范围选择能力，不包含 ASR、音色注册或模型规则。

生成页只负责挂载面板和在提交前调用校验，不能重新实现上述业务规则。

### 2.2 后端策略层

模块：`backend/app/services/emotion_reference.py`

这是独立情绪参考的唯一策略入口，集中负责：

- 模式规范化和互斥校验；
- 显式音频路径优先于音色库 ID；
- 音色库参考文件解析和存在性检查；
- 单条、批量公共参数及逐段覆盖的有效值计算；
- 从独立模式切回 `follow_reference` 时清除继承参数。

`task_queue.py`、`batch_queue.py` 和 `longform_queue.py` 只负责调用策略模块与任务编排，不得复制路径优先级或模式判断。

### 2.3 推理运行层

- `engine_request_builder.py` 把产品字段收敛成唯一运行时字段 `emotion_reference_audio`。
- `generate_v2.py` 只提取情绪参考的 16 kHz semantic embedding。
- speaker cache 与 emotion cache 相互独立；同一路径可以复用 speaker embedding。
- `gpt_v2.py` 持有官方插值公式，API 层和队列层不重复实现数值混合。

### 2.4 素材层

- `POST /api/voices/files/{file_id}/clip` 只创建受管 WAV 片段，不运行 ASR。
- 请求快照同时保存最终片段路径、原始来源路径、来源时长及 IN/OUT，支持历史恢复和安全清理。
- 通用素材生命周期继续由 `custom_reference_store.py` 递归发现引用，不为情绪参考另建第二套清理系统。

## 3. 稳定请求合同

`emotion_mode` 支持：

- `follow_reference`
- `emotion_vector`
- `emotion_reference`
- `emotion_text`（保留给既有兼容路径，IndexTTS 不消费自由文本）

独立情绪字段：

```text
emotion_reference_voice_id
emotion_reference_audio_path
emotion_reference_source_audio_path
emotion_reference_source_duration_ms
emotion_reference_trim_start_ms
emotion_reference_trim_end_ms
emo_alpha
```

显式 `emotion_reference_audio_path` 和 `emotion_reference_voice_id` 同时存在时，显式路径优先。字段存在但模式不是 `emotion_reference` 时视为调用错误，避免隐藏状态悄悄生效或悄悄失效。

## 4. 扩展规则

未来能力应沿现有层次扩展：

- 新增逐段情绪：扩展批次/长文本的 effective reference 计算，不改模型公式。
- 新增多情绪片段混合：在策略层定义有序引用，在运行层增加独立聚合器，不让 UI 直接拼模型张量。
- 新增自动情绪标签：标签属于素材元数据，不改变生成请求的事实来源。
- 新增其他支持情绪音频的引擎：复用前端编辑器和素材层，但创建各自策略适配器；不要把 IndexTTS 专属规则写成全局规则。
- 新增情绪强度曲线：先扩展稳定合同，再由长文本编排按段生成标量；禁止在队列里解析 UI 私有状态。

## 5. 验收门槛

每次修改至少验证：

1. 默认请求不携带独立情绪字段。
2. `alpha=0` 与默认 conditioning 等价。
3. 单条、批量公共、批量逐段覆盖、长文本、重试和历史恢复一致。
4. 音色库与上传来源都能裁切，且裁切不触发 ASR。
5. 删除最后一个引用前，原始素材和片段都不会被清理。
6. 前端检查与构建、后端全量测试、Ruff、真实短音频 A/B 均通过。
