# Longform TTS Planning and Verification RFC

更新时间：2026-06-08

## 背景

当前 Voice Studio 已经有单条生成、批量生成、ASR 转写和导出合并能力，但它们还是分散能力：

- `POST /api/generate`：单条 TTS 任务。
- `POST /api/batches/generate`：批量 TTS 任务。
- `POST /api/asr/transcribe` 与 `/api/asr/tasks`：音频转写。
- `POST /api/exports`：合并或转码音频。

长文本生成需要的是一条编排流程：文本规划、分段生成、ASR 校对、失败重试、最终合并。这个流程不应塞进前端按钮逻辑里，也不应让 agent 自己拼接多个 API 后假装是统一任务。

## 外部实践摘要

- NVIDIA NeMo Magpie-TTS Longform 采用 sentence-level chunks 处理长文本，以绕开上下文限制并尽量保持连续性。
- Deepgram TTS 文档建议按最大字符数、从句和句子边界做 chunking，以降低长文本请求延迟。
- Inworld 长文本 TTS 文档采用 chunk and stitch：先按段落、换行、句末和空格切分，再合成并拼接。
- NVIDIA Riva TTS 评估文档使用 ASR 反转写合成音频，再用 CER/WER 和编辑距离与原文比较，用于发现 TTS 内容不一致。

参考链接：

- https://docs.nvidia.com/nemo-framework/user-guide/latest/speech_ai/magpietts-longform.html
- https://developers.deepgram.com/docs/text-chunking-for-tts-optimization
- https://dev.docs.inworld.ai/docs/tts/capabilities/long-text-input
- https://docs.nvidia.com/deeplearning/riva/user-guide/docs/tutorials/tts-evaluate.html

## 产品目标

1. 短文本保持轻量：用户点击生成后直接进入单条生成。
2. 长文本先计划：系统判断长度和模型风险，必要时提示用户选择分段策略。
3. 分段后可校对：每段生成后通过 ASR 转写，检查内容是否基本覆盖原文。
4. 失败可重试：默认失败段落最多重试 2 次；第二次可尝试更短分段。
5. 通过后再合并：校对失败时默认停止合并，避免把缺句音频交付为最终结果。
6. LLM 可插拔：第一阶段使用规则 planner/judge，后续可以接 LLM 只增强规划与解释。

## 非目标

- 第一阶段不接入 LLM provider、API key、base URL。
- 第一阶段不把 ASR 结果逐字当作唯一真相；错别字不直接判失败。
- 第一阶段不重构现有 `task_queue`、`batch_queue`、`asr_service`、`export_store`。
- 第一阶段不强制所有长文本都分段，用户仍可选择单条生成。

## 阈值策略

阈值是软提示，不是后端硬限制。

| 引擎 | 推荐单段 | 提示阈值 | 强提醒阈值 | 默认建议 |
| --- | ---: | ---: | ---: | --- |
| `omnivoice` | 50-90 中文字 | 120 | 220 | 分段生成 |
| `indextts-v2` | 120-220 中文字 | 300 | 600 | 分段生成并合并 |
| `mimo-v2.5-tts-preset` | 200-400 中文字 | 600 | 1200 | 分段生成并合并 |
| `mimo-v2.5-tts-voiceclone` | 120-250 中文字 | 400 | 800 | 分段生成并合并 |
| `mimo-v2.5-tts-voicedesign` | 120-250 中文字 | 400 | 800 | 先短样本确认声线 |

补充：

- IndexTTS v2 内部已有 `max_text_tokens_per_segment`，但这不等价于产品层面的长文本任务。产品层仍需要可见分段、校对和合并结果。
- OmniVoice 更适合短句确认音色和风格；长文本应优先分段。
- MiMo 云端可以承受更长文本，但为了降低重试成本和便于校对，仍建议分段。

## 分段规则

第一阶段使用 `RuleTextPlanner`：

1. 保留自然段优先。
2. 优先在中文/英文句末切：`。！？!?；;…`。
3. 单句过长时，再按逗号、顿号、冒号等弱停顿切。
4. 仍过长时按字数兜底切，但尽量避免切断英文单词和数字。
5. 合并过短段，避免每段太碎导致语气漂移。

返回结果需要保留：

- `planner = "rules"`
- `llm_available = false`
- `segment_reason`
- `warnings`
- `requires_user_confirmation`
- `privacy_notice`

未来 LLM 接入时，可以新增 `LLMTextPlanner`，但 API 形状不变。

## 校对规则

第一阶段使用 `RuleVerificationJudge`：

1. ASR 转写生成音频。
2. 原文和转写文本都做规范化：去标点、去多余空白、数字规范、大小写归一。
3. 按句子计算覆盖率，而不是逐字完全一致。
4. 错别字、同音字、小范围 ASR 错误只降置信度。
5. 整句缺失、顺序明显错乱、内容跑偏标记为失败。

建议状态：

- `passed`：覆盖完整。
- `warning`：有小差异，但不阻塞合并。
- `failed`：缺句、漏段或内容明显不对。
- `skipped`：ASR 不可用，按用户设置决定是否继续。

## API 规划

### `POST /api/generate/plan`

只做规划，不提交生成任务。前端和 agent 都应该先用它判断是否需要分段。

请求：

```json
{
  "text": "要合成的文本",
  "engine_id": "indextts-v2",
  "planner_mode": "auto",
  "target_format": "mp3"
}
```

响应：

```json
{
  "planner": "rules",
  "llm_available": false,
  "mode": "longform_recommended",
  "recommended_action": "split_verify_merge",
  "requires_user_confirmation": true,
  "text_length": 680,
  "threshold": 300,
  "hard_threshold": 600,
  "warnings": ["当前文本较长，建议分段生成并校对。"],
  "privacy_notice": "规则规划不会离开本机。启用云端 ASR 或 MiMo voiceclone 时会另行提示。",
  "segments": [
    {
      "index": 1,
      "text": "第一段文本。",
      "char_count": 6,
      "segment_reason": "sentence_boundary"
    }
  ]
}
```

推荐 action：

- `direct_generate`
- `direct_generate_with_verification`
- `split_generate`
- `split_verify_merge`

### `POST /api/longform/generate`

第二阶段实现。执行分段生成、校对、重试和合并。

关键参数：

- `plan_id` 或内联 `segments`
- `generate_request`
- `verify_enabled`
- `merge_enabled`
- `max_retries`
- `stop_merge_on_verification_failed`

### `GET /api/longform/{task_id}`

查询父任务、段落状态、校对报告和最终合并音频。

### `POST /api/longform/{task_id}/retry-failed`

只重试失败段落。

## 前端交互

生成页新增四层提示：

1. `输入要合成的文本` 旁增加 info 图标，hover 说明长文本分段与校对。
2. 标题下方副标题：
   - 短文本：`短文本会直接生成，完成后可自动校对内容完整性。`
   - 长文本：`当前文本较长，建议分段生成以减少漏句、截断和长时间等待。`
3. 字数旁增加状态标签：
   - `适合直接生成`
   - `建议分段`
   - `强烈建议分段`
4. 点击生成时，如果 `requires_user_confirmation=true`，弹窗展示分段计划和选择：
   - `分段生成并自动合并`
   - `只分段生成，不合并`
   - `仍然单条生成`

## Agent 规则

Agent 调用 Voice Studio 时必须遵守：

1. 先调 `GET /api/engines`，确认目标引擎有效参数。
2. 文本超过目标引擎提示阈值时，先调 `POST /api/generate/plan`。
3. 如果 `requires_user_confirmation=true`，必须向用户确认再执行长文本策略。
4. MiMo voiceclone 涉及上传参考音频，仍然必须按现有设置确认。
5. 不允许把明显长文本硬塞进 `/api/generate` 后直接报告成功。
6. 长文本只有全部必需段落生成成功，并且校对通过或用户接受风险后，才能报告最终成功。
7. 校对失败时必须报告失败段落、缺失片段和建议，而不是只返回“生成成功”。

## 实施顺序

### Phase 1：规划能力

- 新增 `text_planner.py`
- 新增 `POST /api/generate/plan`
- 前端生成页展示长度状态和长文本弹窗
- 更新 agent 文档

### Phase 2：校对能力

- 已新增 `text_verifier.py`
- 已新增 `POST /api/evaluations/tts-verification`
- 已支持单条结果手动 ASR 校对
- 已支持 `expected_text` + `transcript_text` 的离线比较
- 已在结果卡片显示校对状态

### Phase 3：长文本编排

- 新增 `longform_queue.py`
- 分段生成、逐段校对、自动重试、通过后合并
- 结果记录支持父任务与子段落展开

### Phase 4：LLM 增强

- 设置页新增 LLM provider：`base_url`、`api_key`、`model`
- 新增 `LLMTextPlanner` 与 `LLMVerificationJudge`
- LLM 只负责规划、解释和疑难判断，不接管任务状态机

## 风险

- 分段太碎会造成音色和情绪漂移。
- ASR 可能误判，所以第一阶段校对不做逐字强一致。
- 云端 ASR、MiMo voiceclone 和未来 LLM 都涉及数据出本机，必须有明确提示。
- 长文本父任务需要可恢复；服务重启后不能丢失已生成段落。
- 自动重试必须有上限，默认最多 2 次。
