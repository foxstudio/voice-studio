# Voice Studio PRD v2.0｜完整开发执行版

> 版本：v2.0-dev  
> 日期：2026-06-05  
> 文档定位：可交付给产品、设计、前端、后端、模型适配与测试执行的开发需求文档  
> 产品定位：本地优先、多引擎、可扩展的语音生产工作站  
> 当前首期引擎：IndexTTS / OmniVoice  
> 未来扩展：MiMo TTS、CosyVoice、F5-TTS、GPT-SoVITS、ElevenLabs、OpenAI TTS、Azure Speech、Amazon Polly 等  
> 运行形态：本地浏览器访问 + 本地后端服务  
> 目标设备：Apple Silicon Mac 优先，兼容 M1 系列；具体性能需 PoC 验证  
> 云端策略：默认关闭，作为未来可选扩展  

---

## 0. 文档说明

### 0.1 本文档目标

本文档用于指导 Voice Studio 的完整产品开发，包含产品定位、用户场景、产品架构、页面与交互、数据对象、引擎适配规范、API 草案、本地文件目录规范、测试验收方案和最终交付清单。

本文档不是单纯 UI 描述，也不是模型 Demo 说明，而是面向开发落地的完整 PRD。

### 0.2 信息来源与准确性原则

本文档基于以下信息来源：

1. 用户提供的旧版 Voice Studio PRD v1.0，作为功能素材与初始需求参考。
2. IndexTTS 官方仓库，用于核验 IndexTTS / IndexTTS2 能力。
3. OmniVoice 官方仓库，用于核验多语言、声音克隆、声音设计、Apple Silicon 安装说明等能力。
4. Xiaomi MiMo-Skills 官方仓库，用于核验 MiMo TTS 属于 API/Agent Skill 方向，不作为首期本地模型实现。
5. MLX 官方仓库，用于核验其 Apple Silicon 机器学习框架定位。
6. F5-TTS、GPT-SoVITS、CosyVoice、ComfyUI TTS Audio Suite 等成熟项目，用于产品与架构参考。

### 0.3 已核验官方信息摘要

| 项目 | 已核验信息 | 对 PRD 的影响 |
|---|---|---|
| IndexTTS2 | 官方仓库描述其支持高度表现力的情绪化语音合成、多输入模态情绪控制，音色与情绪解耦；精准时长控制能力被描述为模型贡献，但官方 release 说明中标注“当前 release 尚未启用”。 | 情绪控制进入首期；精准时长控制作为预留能力，不作为首期强制验收。 |
| OmniVoice | 官方仓库定位为 600+ 语言 zero-shot TTS，支持 voice cloning、voice design、非语言符号、pinyin/phoneme 发音修正；安装说明包含 Apple Silicon 的 PyTorch 安装方式。 | 作为首期本地多语言/声音设计引擎；Apple Silicon 需要 PoC 验证性能。 |
| Xiaomi MiMo-Skills | 官方仓库中的 `mimo-v2-5-tts` 支持预设音色、声音设计、声音克隆、情绪风格、方言、唱歌、自然语言控制、Director Mode、音频标签控制；需要 `MIMO_API_KEY`。 | 归类为未来云端/API 引擎，不混入首期本地核心闭环。 |
| MLX | 官方定位为 Apple Silicon 机器学习 array framework，支持 CPU/GPU、统一内存、动态图、惰性计算等。 | 技术方案阶段可作为 Apple Silicon 本地推理候选，不在 PRD 阶段写死。 |
| F5-TTS | 官方说明包含 chunk inference、多风格/多说话人生成、Voice Chat、自定义推理。 | 用于参考脚本分段、多角色配音、长文本生成设计。 |
| GPT-SoVITS | 官方说明包含 5 秒 zero-shot、1 分钟 few-shot、跨语言推理，并有数据处理工具链。 | 用于参考声音资产库、声音素材处理、未来训练/微调工作流。 |
| CosyVoice | 官方说明其面向 zero-shot multilingual speech synthesis，CosyVoice 2/3 方向包含流式、多语言、自然韵律等。 | 用于未来 V2 流式、本地/服务化、多语言扩展参考。 |
| ComfyUI TTS Audio Suite | 项目强调多引擎音频扩展、universal streaming architecture、adapter implementation 等思路。 | 用于参考引擎 Adapter 架构，不照搬节点式 UI。 |

### 0.4 待确认事项

以下内容不是阻塞项，但会影响后续技术方案与交付标准：

| 待确认项 | 默认假设 | 影响 |
|---|---|---|
| Mac 型号与内存 | Apple Silicon Mac，M1 系列，16GB 起步，32GB+ 推荐 | 决定模型并发、任务队列、是否允许多模型常驻 |
| 是否完全离线 | 本地优先，云端默认关闭 | 决定 MiMo/ElevenLabs/OpenAI TTS 是否进入实际可用范围 |
| “小米本地模型”具体指代 | 当前未发现 MiMo TTS 作为纯本地模型的官方接入方式；MiMo-Skills 需 API Key | 首期本地只做 OmniVoice；MiMo 作为云端预留 |
| 开发技术栈 | 前端 Web UI，后端 Python FastAPI，本地 SQLite + 文件目录 | 开发执行建议，不强制 |
| 首期是否要桌面 App | 默认先本地 Web，后续 Tauri/Electron 包装 | 决定安装包交付方式 |

---

## 1. 产品定位

Voice Studio 是一个本地优先、多引擎、可扩展的语音生产工作站。

它不是单模型 Demo，也不是简单 TTS 参数面板，而是面向内容创作者、音频制作者、短视频配音人员和内部内容团队的语音生产系统。

核心能力包括：

- 声音资产管理
- 单句语音合成
- 脚本级多段落配音
- 多角色声音绑定
- 多引擎切换
- 文本增强与发音控制
- 批量生成与任务队列
- 生成历史与参数复用
- 音频导出与基础后处理
- 本地/云端模型扩展接口

---

## 2. 产品目标

### 2.1 当前开发目标

完整开发版本需要实现以下闭环：

```text
引擎配置
→ 声音资产导入
→ 文本输入/脚本导入
→ 引擎与声音选择
→ 参数调节
→ 生成任务执行
→ 播放试听
→ 历史记录
→ 导出音频
```

### 2.2 长期目标

Voice Studio 需要支持未来扩展：

- 新增本地 TTS 模型
- 新增云端 TTS API
- 新增声音训练/微调工作流
- 新增语音转换模型
- 新增音频后处理工具
- 新增脚本/字幕时间轴能力
- 新增桌面客户端打包
- 新增团队级声音资产授权与管理

### 2.3 设计原则

#### 原则一：架构一步到位，功能分层交付

产品架构从第一版开始按多引擎、多项目、多任务设计。实际开发按照 P0 / P1 / P2 分层交付。

#### 原则二：页面不绑定模型

错误方式：

```text
IndexTTS 页面
OmniVoice 页面
CosyVoice 页面
```

正确方式：

```text
单句合成页
脚本工作台页
声音资产库
引擎中心
```

模型作为引擎选择项存在，页面根据引擎能力动态展示参数。

#### 原则三：能力标签驱动 UI

系统不直接判断模型名称，而是判断能力标签。

```text
支持 emotion_vector → 显示情绪向量面板
支持 voice_design → 显示声音设计面板
支持 nonverbal_tags → 显示非语言标签工具栏
支持 pinyin_control → 显示拼音标注工具
```

#### 原则四：本地优先，云端可选

默认所有文本、参考音频、生成结果保存在本地。云端模型必须由用户主动开启，并明确提示数据会发送到第三方服务。

---

## 3. 目标用户与核心场景

### 3.1 内容创作者

典型需求：生成短视频旁白、生成角色台词、调整语气/语速/情绪、快速导出音频给剪辑软件。

### 3.2 播客 / 有声内容制作者

典型需求：长文本分段、多角色声音、旁白与对话区分、批量生成、合并导出。

### 3.3 公司内部内容团队

典型需求：管理主播/歌手/虚拟角色声音资产、标记授权状态、统一参数模板、批量生产内容音频、留存生成历史。

### 3.4 技术/模型使用者

典型需求：配置本地模型、切换推理设备、查看模型状态、测试不同引擎参数、接入新模型。

---

## 4. 产品边界

### 4.1 必须交付 P0

| 模块 | 说明 |
|---|---|
| 引擎中心 | 管理 IndexTTS、OmniVoice，预留未来引擎 |
| 声音资产库 | 导入、管理、试听、授权标记参考声音 |
| 单句合成 | 快速生成短文本语音 |
| 脚本工作台基础版 | 支持基础多段落、多角色、批量生成 |
| 文本增强基础版 | 支持术语、发音、拼音/音素、非语言标签基础能力 |
| 任务队列 | 支持生成任务状态、失败重试、取消 |
| 生成历史 | 保存音频、文本、参数快照 |
| 音频导出 | 支持 WAV/MP3，合并导出 |
| 设置中心 | 模型目录、输出目录、默认引擎、本地/云端开关 |
| 测试验收 | 包含完整验收用例与交付清单 |

### 4.2 建议交付 P1

| 模块 | 说明 |
|---|---|
| 基础音频后处理 | 音量标准化、去静音、句间静音、格式转换 |
| 参数模板 | 标准、稳定、多样、旁白、情绪化等 |
| 项目 JSON 导入导出 | 便于工程迁移 |
| 错误日志面板 | 便于本地模型调试 |
| 健康检查脚本 | 一键检查模型与环境状态 |

### 4.3 预留接口 P2

| 模块 | 说明 |
|---|---|
| MiMo TTS | 云端/API 引擎预留 |
| ElevenLabs/OpenAI/Azure/Polly | 云端引擎预留 |
| CosyVoice/F5-TTS/GPT-SoVITS | 未来本地引擎预留 |
| 声音训练/微调 | 未来能力 |
| 流式生成 | 未来能力 |
| SRT 时间轴对齐 | 未来能力 |
| 团队协作/权限系统 | 未来能力 |
| DAW 级多轨编辑 | 暂不做 |


---

## 5. 成熟项目参考与设计原则

### 5.1 商业语音工作台参考

| 参考对象 | 可借鉴点 | Voice Studio 落地方式 |
|---|---|---|
| ElevenLabs | 声音库、声音克隆、声音设计、API、项目化 | 声音资产库、引擎中心、历史记录 |
| Murf | 配音编辑器、语速/音高/韵律控制、项目化 | 脚本工作台、段落级参数 |
| Azure Speech | SSML、phoneme、custom lexicon、prosody | 文本增强、发音词典、未来 SSML |
| Amazon Polly | pronunciation lexicon、SSML、say-as | 术语词典、读法控制 |

### 5.2 开源语音项目参考

| 参考对象 | 可借鉴点 | Voice Studio 落地方式 |
|---|---|---|
| GPT-SoVITS | 声音素材处理、zero-shot、few-shot | 声音资产库、未来训练/微调 |
| F5-TTS | chunk inference、多说话人 | 脚本分段、多角色配音 |
| CosyVoice | zero-shot multilingual、流式、服务化 | 未来本地服务化与流式生成 |
| ComfyUI TTS Audio Suite | 多引擎 Adapter、统一处理架构 | 引擎适配器，不照搬节点 UI |

---

## 6. 总体产品架构

```text
Voice Studio
├── Dashboard 工作台首页
├── Engine Hub 引擎中心
├── Voice Library 声音资产库
├── Single Generate 单句合成
├── Script Studio 脚本工作台
├── Text Tools 文本增强
├── Job Queue 任务队列
├── Audio Tools 音频后处理
├── History 生成历史
└── Settings 设置中心
```

### 6.1 推荐工作台布局

```text
顶部导航栏：
Logo / 当前项目 / 引擎状态 / 任务队列 / 历史 / 设置

左侧侧边栏：
项目列表 / 声音资产 / 角色列表 / 最近使用

中间主工作区：
文本输入 / 脚本段落 / 生成结果 / 资产列表

右侧 Inspector：
当前对象参数 / 引擎参数 / 情绪 / 语言 / 高级参数

底部音频栏：
播放器 / 波形 / 当前结果 / 导出
```

### 6.2 响应式要求

| 屏幕宽度 | 布局 |
|---|---|
| >= 1280px | 完整工作台：左侧栏 + 主工作区 + 右侧 Inspector + 底部播放器 |
| 1024px-1279px | 压缩侧边栏，Inspector 可折叠 |
| 768px-1023px | 双栏布局，底部播放器固定 |
| <768px | 单栏堆叠，仅保留核心生成能力 |

---

## 7. 页面与导航结构

### 7.1 页面列表

| 页面 | 路由建议 | 优先级 | 说明 |
|---|---|---|---|
| Dashboard | `/` | P0 | 首页与状态总览 |
| Engine Hub | `/engines` | P0 | 引擎配置与状态 |
| Voice Library | `/voices` | P0 | 声音资产管理 |
| Single Generate | `/generate` | P0 | 单句合成 |
| Script Studio | `/projects/:id` | P0/P1 | 脚本项目 |
| Job Queue | `/tasks` | P0 | 任务队列 |
| History | `/history` | P0 | 历史记录 |
| Text Tools | `/text-tools` | P1 | 文本增强、词典 |
| Audio Tools | `/audio-tools` | P1 | 基础音频后处理 |
| Settings | `/settings` | P0 | 系统设置 |

### 7.2 Dashboard 首页

目标：快速查看模型可用性、最近项目、最近声音、最近生成与快速入口。

核心区域：

- 模型状态卡片
- 快速开始卡片
- 最近项目
- 最近声音
- 最近生成
- 存储空间状态

空状态：

```text
欢迎使用 Voice Studio
建议先完成：
1. 配置一个本地引擎
2. 导入一个参考声音
3. 生成第一条语音
```

### 7.3 Engine Hub 引擎中心

目标：统一管理本地/云端语音引擎。

核心区域：

- 引擎分类 Tabs：全部 / 本地 / 云端 / 未安装
- 引擎卡片列表
- 引擎详情 Inspector
- 健康检查
- 日志面板

引擎卡片字段：

- 引擎名称
- 类型：本地 / 云端
- 推荐用途
- 安装状态
- 运行状态
- 支持语言
- 能力标签
- 操作：启动 / 停止 / 健康检查 / 配置 / 查看日志

### 7.4 Voice Library 声音资产库

目标：把参考音频管理成可复用声音资产。

核心区域：

- 新增声音
- 批量导入
- 搜索
- 标签筛选
- 授权状态筛选
- 声音卡片
- 声音详情 Inspector

声音卡片字段：

- 声音名称
- 声音类型
- 默认语言
- 标签
- 授权状态
- 推荐引擎
- 最近使用时间
- 试听按钮
- 使用按钮

### 7.5 Single Generate 单句合成页

目标：快速生成一句或一小段语音。

核心区域：

- 文本输入
- 文本增强工具条
- 引擎选择
- 声音选择
- 情绪/风格参数
- 专业参数折叠
- 生成按钮
- 结果列表
- 底部播放器

主流程：

```text
输入文本
→ 选择引擎
→ 选择声音
→ 设置参数
→ 点击生成
→ 进入任务队列
→ 生成成功
→ 播放试听
→ 保存历史
→ 导出音频
```

### 7.6 Script Studio 脚本工作台

目标：支持长文本、多段落、多角色、批量配音。

核心区域：

- 项目信息
- 角色列表
- 脚本段落列表
- 段落 Inspector
- 批量生成按钮
- 任务队列
- 合并导出

批量生成前检查：

- 是否存在空段落
- 是否存在未绑定声音
- 是否存在不可用引擎
- 是否存在授权未确认声音
- 是否存在过长文本
- 是否存在参数异常

### 7.7 Text Tools 文本增强

功能范围：

- 自动分句
- 术语词典
- 发音词典
- 中文拼音标注
- 英文音素标注
- 数字读法
- 日期读法
- 停顿标记
- 非语言标签
- 文本清洗

### 7.8 Job Queue 任务队列

任务状态：

```text
pending
queued
running
postprocessing
success
failed
cancelled
retrying
```

支持操作：

- 暂停
- 继续
- 取消
- 重试
- 查看日志
- 打开结果
- 批量导出

### 7.9 Audio Tools 音频后处理

基础能力：

- 音量标准化
- 去静音
- 句间静音调整
- 淡入淡出
- 音频合并
- WAV / MP3 / FLAC 格式转换

暂不做：

- 多轨 DAW 编辑
- 复杂混音
- 专业效果器链
- 实时录音棚能力

### 7.10 Settings 设置中心

设置项：

- 默认引擎
- 默认语言
- 默认输出格式
- 模型目录
- 声音目录
- 项目目录
- 输出目录
- 缓存目录
- 日志目录
- 设备偏好
- 云端模型开关
- API Key 管理
- 隐私模式


---

## 8. 引擎中心与插件化接入规范

### 8.1 Engine Adapter 设计

每个模型通过 Engine Adapter 接入。

```text
Engine Adapter
├── manifest.json
├── parameter_schema.json
├── runtime_config
├── request_mapper
├── response_mapper
├── health_check
├── generate
├── cancel
├── logs
└── error_mapper
```

### 8.2 Manifest 字段

```json
{
  "engine_id": "indextts",
  "name": "IndexTTS",
  "display_name": "IndexTTS",
  "engine_type": "local",
  "provider": "Index Team",
  "version": "adapter_version",
  "model_version": "model_version",
  "description": "中文/英文情绪化语音合成引擎",
  "supported_languages": ["zh", "en"],
  "supported_devices": ["auto", "cpu", "apple_silicon", "cuda"],
  "capabilities": ["local_inference", "voice_clone"],
  "default_use_case": "中文/英文情绪化配音",
  "privacy_level": "local_only"
}
```

### 8.3 能力标签

```text
local_inference
cloud_api
voice_clone
voice_design
auto_voice
emotion_control
emotion_reference
emotion_vector
emotion_text
multilingual
dialect
accent
nonverbal_tags
pinyin_control
phoneme_control
long_text
batch_generate
streaming
duration_control
speaker_role
srt_timing
voice_training
voice_conversion
audio_postprocess
```

### 8.4 参数 Schema

参数必须声明：

```json
{
  "key": "speed",
  "label": "语速 Speed",
  "type": "slider",
  "level": "basic",
  "default": 1.0,
  "min": 0.5,
  "max": 2.0,
  "step": 0.05,
  "tooltip": "控制输出语音速度，1.0 为正常速度",
  "required": false,
  "visible_when": null,
  "engine_mapping": "speed"
}
```

### 8.5 参数层级

| 层级 | 说明 |
|---|---|
| basic | 普通用户常用参数 |
| advanced | 专业调参，默认折叠 |
| developer | 开发/调试参数，默认隐藏 |

### 8.6 标准生成请求

```json
{
  "task_id": "task_xxx",
  "engine_id": "indextts",
  "project_id": null,
  "segment_id": null,
  "input": {
    "text": "这里是要合成的文本",
    "language": "zh",
    "voice_id": "voice_xxx",
    "reference_audio_ids": ["file_xxx"],
    "reference_text": "参考音频对应文本"
  },
  "controls": {
    "speed": 1.0,
    "emotion_mode": "reference",
    "emotion_values": null,
    "style": null,
    "target_duration_ms": null
  },
  "parameters": {
    "temperature": 0.8,
    "top_p": 0.8,
    "seed": 0
  },
  "output": {
    "format": "wav",
    "sample_rate": 44100,
    "save_to_history": true
  }
}
```

### 8.7 标准生成响应

```json
{
  "task_id": "task_xxx",
  "result_id": "result_xxx",
  "status": "success",
  "engine_id": "indextts",
  "output": {
    "audio_file_id": "file_xxx",
    "duration_ms": 12000,
    "format": "wav",
    "sample_rate": 44100
  },
  "metrics": {
    "generation_time_ms": 8500,
    "rtf": 0.7
  },
  "snapshot": {
    "input_text": "这里是要合成的文本",
    "parameters": {}
  },
  "warnings": [],
  "error": null
}
```

---

## 9. IndexTTS 接入需求

### 9.1 引擎定位

IndexTTS 用于中文/英文情绪化语音生成、声音克隆和角色配音。

### 9.2 必须支持能力

```text
voice_clone
emotion_reference
emotion_vector
emotion_text
pinyin_control
long_text
```

### 9.3 UI 展示

选择 IndexTTS 后，参数区显示：

- 文本输入
- 声音选择
- 参考音频
- 情感模式
- 情绪向量
- 情绪文本描述
- 情感强度
- 拼音标注
- 高级参数

### 9.4 情感模式

| 模式 | 说明 |
|---|---|
| follow_reference | 跟随参考音频 |
| emotion_reference | 独立情感参考音频 |
| emotion_vector | 自定义情绪向量 |
| emotion_text | 情绪文本描述 |

### 9.5 情绪向量

默认 8 维：

```text
高兴
愤怒
悲伤
恐惧
反感
低落
惊讶
自然
```

如果实际引擎字段与此不同，由 Adapter 做映射。

### 9.6 高级参数

```text
temperature
top_p
top_k
repetition_penalty
diffusion_steps
cfg_rate
segment_tokens
interval_silence
seed
```

### 9.7 特别说明

- 精准时长控制仅作为预留能力，不进入首期强制验收。
- 拼音控制需在 UI 中提示：仅支持有效拼音，不保证所有异常文本都可修正。
- 情绪文本、情感参考、情绪向量同时存在时，必须由 Adapter 定义优先级。

建议优先级：

```text
段落级情绪设置
> 用户指定情绪模式
> 声音默认情绪
> 引擎默认值
```

---

## 10. OmniVoice 接入需求

### 10.1 引擎定位

OmniVoice 用于多语言语音生成、声音克隆、声音设计和非语言标签表达。

### 10.2 必须支持能力

```text
voice_clone
voice_design
auto_voice
multilingual
dialect
accent
nonverbal_tags
pinyin_control
phoneme_control
```

### 10.3 UI 展示

选择 OmniVoice 后，参数区显示：

- 文本输入
- 语言选择
- 声音模式
- Voice Clone 上传区
- Voice Design 属性区
- Auto Voice 随机音色
- 非语言标签工具
- 拼音/音素控制
- 高级参数

### 10.4 声音模式

| 模式 | 说明 |
|---|---|
| clone | 声音克隆 |
| design | 声音设计 |
| auto | 自动音色 |

### 10.5 Voice Design 属性

```text
gender
age
pitch
style
accent
dialect
```

### 10.6 非语言标签

首期内置：

```text
[laughter]
[sigh]
[question]
[surprise]
```

后续可由 Adapter 配置更多标签。

### 10.7 高级参数

```text
speed
duration
steps
guidance_scale
denoise
preprocess
postprocess
```

### 10.8 特别说明

- OmniVoice 官方支持 600+ 语言，但 UI 不建议一次展示全部语言。
- 语言选择应提供常用语言快捷项 + 搜索完整语言列表。
- Voice Design 在不同语言上的稳定性可能不同，UI 需要展示实验性提示。
- Apple Silicon 可安装运行依赖，但实际生成速度与内存占用需 PoC 验证。

---

## 11. MiMo / 云端模型预留策略

### 11.1 MiMo 定位

MiMo TTS 不作为首期本地模型实现。

原因：

- 官方 MiMo-Skills 需要 `MIMO_API_KEY`。
- 它更像 Agent Skill / 云端 API 能力。
- 与本地模型架构不同。

### 11.2 预留能力

未来作为 Cloud Engine 接入。

能力标签可包括：

```text
cloud_api
voice_clone
voice_design
emotion_control
dialect
singing
natural_language_control
director_mode
audio_tags
```

### 11.3 云端调用规则

首次启用云端引擎时必须提示：

```text
你将使用云端语音服务。
输入文本、参考音频或生成参数可能会发送到第三方服务。
请确认你有权上传相关声音与文本内容。
```


---

## 12. 声音资产库需求

### 12.1 VoiceAsset 字段

```text
voice_id
name
voice_type
description
default_language
tags
reference_audio_ids
reference_text
recommended_engine_id
recommended_preset_id
license_id
quality_status
quality_notes
favorite
created_at
updated_at
last_used_at
```

### 12.2 声音类型

```text
real_person
virtual_character
host
singer
narrator
emotion_reference
test_sample
```

### 12.3 授权状态

```text
self_voice
company_authorized
authorized
test_only
unknown
commercial_forbidden
```

### 12.4 新增声音流程

```text
上传参考音频
→ 基础质量检测
→ 可选填写/识别参考文本
→ 设置声音名称
→ 选择声音类型
→ 设置语言与标签
→ 设置授权状态
→ 选择推荐引擎
→ 保存入库
```

### 12.5 质量检测

P0 检测：

- 文件格式
- 时长
- 文件大小
- 采样率
- 声道数
- 是否存在超长静音

P1 检测：

- 噪声风险
- 音量过低/过高
- 多人声风险
- 推荐裁剪区间

---

## 13. 单句合成需求

### 13.1 功能范围

- 输入文本
- 选择声音
- 选择引擎
- 设置语言
- 设置情绪/风格
- 设置基础参数
- 展开高级参数
- 生成音频
- 播放结果
- 收藏结果
- 复用参数
- 导出音频
- 加入脚本项目

### 13.2 文本增强工具条

```text
插入停顿
插入拼音
插入音素
插入笑声
插入叹气
数字规范化
分句预览
```

### 13.3 生成按钮状态

```text
default: 生成语音
hover: 高亮
loading: 生成中...
success: 生成完成
error: 生成失败，查看原因
```

### 13.4 结果卡字段

```text
result_id
audio_player
engine
voice
language
parameter_summary
generation_time
duration
favorite
export
reuse_params
add_to_project
```

---

## 14. 脚本工作台需求

### 14.1 项目流程

```text
新建脚本项目
→ 导入/粘贴脚本
→ 自动分段
→ 创建角色
→ 绑定声音
→ 设置段落参数
→ 批量生成
→ 单段重生成
→ 锁定满意版本
→ 合并导出
```

### 14.2 Project 字段

```text
project_id
name
project_type
description
default_engine_id
default_voice_id
default_language
roles
segments
status
export_settings
created_at
updated_at
last_opened_at
```

### 14.3 Role 字段

```text
role_id
project_id
name
color
default_voice_id
default_engine_id
default_language
default_emotion
default_speed
preset_id
description
```

### 14.4 ScriptSegment 字段

```text
segment_id
project_id
index
text
role_id
voice_id
engine_id
language
emotion_mode
emotion_values
style
speed
target_duration_ms
pause_before_ms
pause_after_ms
parameter_snapshot
status
current_result_id
locked
notes
```

### 14.5 段落状态

```text
empty
ready
queued
generating
completed
failed
locked
need_regenerate
```

### 14.6 导出类型

```text
单段音频
合并音频
按角色导出
按段落导出
SRT 字幕
CSV 表格
JSON 工程文件
参数快照
生成日志
```

---

## 15. 文本增强与发音词典需求

### 15.1 术语词典

字段：

```text
entry_id
source_text
target_text
language
engine_id
scope
project_id
enabled
notes
created_at
updated_at
```

### 15.2 发音词典

字段：

```text
pronunciation_id
source_text
pronunciation
pronunciation_type
language
engine_id
example_sentence
enabled
created_at
updated_at
```

### 15.3 发音类型

```text
pinyin
phoneme
ssml
custom
```

### 15.4 应用规则

- IndexTTS 优先使用 pinyin。
- OmniVoice 可使用 pinyin / phoneme。
- 云端模型未来可使用 SSML / phoneme。
- 同一词同时存在术语替换与发音词典时，优先执行术语替换，再执行发音规则。

---

## 16. 任务队列需求

### 16.1 GenerationTask 字段

```text
task_id
task_type
project_id
segment_id
engine_id
voice_id
input_text
input_files
parameters
status
progress
priority
retry_count
error_code
error_message
result_id
created_at
started_at
completed_at
```

### 16.2 任务状态

```text
pending
queued
running
postprocessing
success
failed
cancelled
retrying
```

### 16.3 任务操作

- 取消
- 重试
- 查看日志
- 打开结果
- 复制错误
- 批量导出

### 16.4 队列规则

- 默认单任务顺序执行，避免本地模型内存压力。
- 允许未来配置并发数。
- 不同引擎是否可并发由 RuntimeStatus 决定。
- 批量生成时，失败段落不影响其他段落继续执行。

---

## 17. 音频后处理需求

### 17.1 P0 基础能力

- 音频合并
- WAV / MP3 导出
- 句间静音插入
- 音量标准化基础版

### 17.2 P1 增强能力

- 去静音
- 淡入淡出
- FLAC 导出
- 批量格式转换
- 波形预览

### 17.3 暂不做

- 多轨剪辑
- 混音台
- VST 插件
- 专业母带处理
- 实时音频录制

---

## 18. 历史记录与导出需求

### 18.1 GenerationResult 字段

```text
result_id
task_id
project_id
segment_id
engine_id
voice_id
output_audio_id
input_text
parameter_snapshot
duration_ms
generation_time_ms
status
quality_rating
favorite
notes
created_at
```

### 18.2 历史记录字段

```text
history_id
result_id
text_summary
engine_name
voice_name
created_at
project_name
favorite
```

### 18.3 导出要求

导出时必须支持：

- 单条音频导出
- 合并音频导出
- 生成文件命名规则
- 输出目录选择
- 导出失败提示
- 导出日志

### 18.4 文件命名建议

```text
{date}_{engine}_{voice}_{summary}_{result_id}.wav
{project_name}_{date}_merged.wav
{project_name}_{date}_segments.zip
```


---

## 19. 数据对象与字段总览

### 19.1 核心对象

```text
Engine
EngineCapability
EngineParameterSchema
VoiceAsset
FileAsset
LicenseRecord
Project
Role
ScriptSegment
GenerationTask
GenerationResult
History
TextDictionary
PronunciationEntry
PresetTemplate
ExportJob
AppSettings
RuntimeStatus
```

### 19.2 最小数据闭环

```text
用户配置 Engine
→ 上传 FileAsset
→ 创建 VoiceAsset
→ 输入文本
→ 创建 GenerationTask
→ 生成 GenerationResult
→ 写入 History
→ 导出音频文件
```

---

## 20. API 接口草案

说明：以下为接口语义草案。开发阶段可使用 REST、WebSocket、本地 RPC 或内部服务调用实现，但语义需要保持一致。

### 20.1 引擎接口

```http
GET /api/engines
GET /api/engines/{engine_id}
POST /api/engines/{engine_id}/start
POST /api/engines/{engine_id}/stop
POST /api/engines/{engine_id}/health-check
GET /api/engines/{engine_id}/logs
GET /api/engines/{engine_id}/parameter-schema
```

### 20.2 声音资产接口

```http
GET /api/voices
POST /api/voices
GET /api/voices/{voice_id}
PATCH /api/voices/{voice_id}
DELETE /api/voices/{voice_id}
POST /api/voices/{voice_id}/test-generate
```

### 20.3 文件接口

```http
POST /api/files/upload
GET /api/files/{file_id}
DELETE /api/files/{file_id}
GET /api/files/{file_id}/download
```

### 20.4 生成接口

```http
POST /api/generate
GET /api/tasks
GET /api/tasks/{task_id}
POST /api/tasks/{task_id}/cancel
POST /api/tasks/{task_id}/retry
GET /api/results/{result_id}
```

### 20.5 项目接口

```http
GET /api/projects
POST /api/projects
GET /api/projects/{project_id}
PATCH /api/projects/{project_id}
DELETE /api/projects/{project_id}
POST /api/projects/{project_id}/segments
PATCH /api/projects/{project_id}/segments/{segment_id}
POST /api/projects/{project_id}/generate
```

### 20.6 历史与导出接口

```http
GET /api/history
DELETE /api/history/{history_id}
POST /api/export
GET /api/export/{export_id}
```

### 20.7 设置接口

```http
GET /api/settings
PATCH /api/settings
POST /api/settings/health-check
```

---

## 21. 本地文件目录规范

### 21.1 默认根目录

```text
~/VoiceStudio
├── config
├── models
├── voices
├── projects
├── outputs
├── exports
├── cache
├── logs
└── temp
```

### 21.2 目录说明

| 目录 | 用途 |
|---|---|
| config | 应用配置、引擎配置 |
| models | 本地模型文件 |
| voices | 参考音频与声音资产 |
| projects | 项目工程文件 |
| outputs | 生成音频 |
| exports | 用户主动导出的文件 |
| cache | 临时缓存 |
| logs | 应用和模型日志 |
| temp | 临时处理文件 |

### 21.3 工程文件

项目应支持 JSON 工程文件导入导出。

最小结构：

```json
{
  "project_id": "project_xxx",
  "name": "项目名称",
  "roles": [],
  "segments": [],
  "export_settings": {},
  "created_at": "",
  "updated_at": ""
}
```

---

## 22. 设置中心与运行环境

### 22.1 运行环境要求

默认目标：

```text
Apple Silicon Mac
本地浏览器访问
本地 Python 服务
本地数据存储
本地模型推理
```

### 22.2 设备选项

```text
auto
cpu
apple_silicon
cuda
other
```

具体后端：

- MLX
- PyTorch MPS
- ONNX Runtime
- 模型官方推理脚本

由技术方案阶段验证，不在 PRD 写死。

### 22.3 技术 PoC 必测项

```text
IndexTTS 是否能在目标 Mac 上跑通
OmniVoice 是否能在目标 Mac 上跑通
模型启动耗时
首条音频生成耗时
连续生成稳定性
长文本分段生成稳定性
内存占用
硬盘占用
模型文件下载/加载方式
```

---

## 23. 隐私、授权与安全限制

### 23.1 本地隐私原则

默认不上传：

- 用户文本
- 参考音频
- 生成音频
- 声音资产
- 项目文件

### 23.2 声音授权

所有声音资产必须设置授权状态。

授权未确认的声音：

- 可以测试生成
- 导出时弹出提示
- 项目中显示风险标记

禁止商用的声音：

- 允许本地测试
- 导出时强提示
- 可在导出文件 metadata 中预留风险标记

### 23.3 云端调用提示

云端模型默认关闭。

开启时提示：

```text
启用云端模型后，输入文本、参考音频或生成参数可能发送到第三方服务。
请确认你拥有相关文本和声音的使用权。
```

---

## 24. 非功能需求

### 24.1 性能

| 项目 | 要求 |
|---|---|
| 页面响应 | 常规操作 200ms 内反馈 |
| 生成任务 | 进入队列后立即显示状态 |
| 长文本 | 必须分段处理，不允许页面卡死 |
| 音频播放 | 生成成功后可立即播放 |
| 历史记录 | 最近 100 条快速加载，更多可分页 |

### 24.2 稳定性

- 本地模型异常不能导致前端崩溃。
- 任务失败必须记录错误。
- 页面刷新后任务历史不丢失。
- 应用重启后声音资产、项目、历史仍可恢复。

### 24.3 可维护性

- 新增模型不得改动主页面结构。
- 模型参数由 schema 驱动。
- 错误码统一管理。
- 数据对象保持可迁移。

### 24.4 可用性

- 普通用户默认只看到基础参数。
- 高级参数默认折叠。
- 开发参数默认隐藏。
- 模型不可用时给出可理解提示。

---

## 25. 错误码与异常状态

### 25.1 错误码

```text
E_ENGINE_NOT_FOUND
E_ENGINE_NOT_READY
E_MODEL_FILE_MISSING
E_RUNTIME_ERROR
E_DEVICE_UNAVAILABLE
E_INPUT_TEXT_EMPTY
E_REFERENCE_AUDIO_MISSING
E_REFERENCE_AUDIO_INVALID
E_REFERENCE_AUDIO_TOO_SHORT
E_REFERENCE_AUDIO_TOO_LONG
E_LANGUAGE_UNSUPPORTED
E_PARAMETER_INVALID
E_LICENSE_WARNING
E_CLOUD_API_KEY_MISSING
E_CLOUD_QUOTA_EXCEEDED
E_OUTPUT_WRITE_FAILED
E_TASK_CANCELLED
```

### 25.2 错误展示规则

必须分两层：

用户提示：

```text
参考音频过短，建议上传 3 秒以上的清晰人声音频。
```

技术详情：

```text
E_REFERENCE_AUDIO_TOO_SHORT: duration_ms=1200, min_required_ms=3000
```


---

## 26. 测试验收方案

### 26.1 验收范围

必须覆盖：

- 页面功能
- 引擎接入
- 声音资产
- 单句合成
- 脚本工作台
- 任务队列
- 历史记录
- 导出
- 错误状态
- 本地文件持久化

### 26.2 测试环境

默认测试环境：

```text
Apple Silicon Mac
本地浏览器
本地后端服务
IndexTTS 可用
OmniVoice 可用
至少 2 条参考音频
至少 5 条测试文本
```

### 26.3 P0 验收用例

#### TC-001 应用启动

步骤：

```text
启动后端服务
打开本地浏览器地址
进入 Dashboard
```

预期：

```text
页面正常打开
能看到模型状态
能进入设置页
无前端报错
```

#### TC-002 引擎状态检查

步骤：

```text
进入 Engine Hub
查看 IndexTTS
执行健康检查
查看 OmniVoice
执行健康检查
```

预期：

```text
两个引擎状态可见
健康检查有明确成功/失败结果
失败时展示错误详情
```

#### TC-003 声音资产导入

步骤：

```text
进入 Voice Library
上传参考音频
填写参考文本
设置授权状态
保存
```

预期：

```text
声音资产创建成功
音频可试听
资产出现在列表
刷新页面后仍存在
```

#### TC-004 IndexTTS 单句生成

步骤：

```text
进入 Single Generate
选择 IndexTTS
选择声音资产
输入中文文本
选择情绪模式
点击生成
```

预期：

```text
任务进入队列
生成成功
音频可播放
历史记录出现
参数快照可查看
音频可导出
```

#### TC-005 OmniVoice Voice Clone 生成

步骤：

```text
选择 OmniVoice
选择 Voice Clone
选择参考声音
输入文本
选择语言
点击生成
```

预期：

```text
生成成功
音频可播放
历史记录出现
导出成功
```

#### TC-006 OmniVoice Voice Design 生成

步骤：

```text
选择 OmniVoice
选择 Voice Design
设置性别/年龄/音调/口音/方言
输入文本
点击生成
```

预期：

```text
生成成功或失败提示明确
参数快照保存
结果可播放或错误可追踪
```

#### TC-007 脚本项目批量生成

步骤：

```text
新建脚本项目
导入 5 段文本
创建 2 个角色
绑定不同声音
批量生成
```

预期：

```text
每段进入任务队列
成功段落可播放
失败段落可重试
项目可保存
可合并导出
```

#### TC-008 生成历史复用

步骤：

```text
进入 History
选择一条记录
点击复用参数
回到 Single Generate
重新生成
```

预期：

```text
文本、引擎、声音、参数正确回填
可以再次生成
```

#### TC-009 导出文件

步骤：

```text
选择生成结果
导出 WAV
导出 MP3
打开导出目录
```

预期：

```text
文件存在
文件可播放
命名符合规范
导出失败时有错误提示
```

#### TC-010 应用重启持久化

步骤：

```text
关闭应用
重新启动
进入声音库/项目/历史
```

预期：

```text
声音资产仍存在
项目仍存在
历史仍存在
设置仍存在
```

### 26.4 异常测试

| 用例 | 操作 | 预期 |
|---|---|---|
| 空文本生成 | 不输入文本点击生成 | 提示输入文本为空 |
| 缺少参考音频 | 需要参考音频时未选择 | 提示缺少参考音频 |
| 引擎未启动 | 停止引擎后生成 | 提示引擎不可用 |
| 文件格式错误 | 上传非音频文件 | 拒绝上传并提示 |
| 输出目录不可写 | 设置无权限目录 | 导出失败并提示 |
| 授权未确认 | 使用 unknown 声音导出 | 弹出风险提示 |
| 长文本过长 | 输入超长文本 | 自动分段或提示进入脚本工作台 |

### 26.5 验收通过标准

项目视为可交付，需要满足：

```text
P0 功能全部通过
P1 功能无阻塞性缺陷
所有失败状态有明确提示
生成音频可播放、可导出
数据重启后不丢失
至少 IndexTTS 和 OmniVoice 各完成 3 条成功生成
脚本工作台完成一次 5 段以上批量生成
交付包包含启动说明和测试报告
```

---

## 27. 最终交付清单

开发完成后必须交付：

### 27.1 代码与配置

```text
项目源码
前端源码
后端源码
引擎 Adapter 源码
配置文件示例
环境变量示例
依赖清单
```

### 27.2 运行与部署

```text
本地启动说明
模型安装说明
一键启动脚本
一键健康检查脚本
常见问题说明
日志查看说明
缓存清理说明
```

### 27.3 测试材料

```text
测试文本样本
测试音频样本
测试项目样本
测试报告
已知问题列表
验收记录
```

### 27.4 用户资料

```text
基础使用说明
声音资产导入说明
单句合成说明
脚本项目说明
导出说明
授权风险提示
```

### 27.5 可运行版本

至少交付一种：

```text
本地源码运行版
或
本地可执行包
或
Docker / uv / conda 环境脚本
```

如果暂不交付桌面 App，需要明确说明。

---

## 28. 开发阶段建议

### 28.1 推荐阶段

```text
阶段 1：技术 PoC
- 跑通 IndexTTS
- 跑通 OmniVoice
- 验证 Mac M1 性能
- 产出模型运行报告

阶段 2：应用骨架
- 前端工作台
- 后端服务
- 本地存储
- 文件目录
- 引擎中心

阶段 3：核心闭环
- 声音资产库
- 单句合成
- 任务队列
- 播放器
- 历史记录
- 导出

阶段 4：生产能力
- 脚本工作台
- 多角色
- 批量生成
- 合并导出
- 文本增强

阶段 5：验收与打包
- 全量测试
- 修复异常
- 完成文档
- 交付可运行版本
```

### 28.2 不建议事项

```text
不建议先做复杂 UI 动效
不建议先做桌面端打包
不建议首期做云端计费
不建议首期做声音训练
不建议一开始支持太多模型
不建议把模型参数写死在前端
```

---

## 29. 待开发确认清单

开发前需要确认：

```text
目标 Mac 具体配置
是否允许联网下载模型
模型文件来源：Hugging Face / ModelScope / 手动下载
是否使用 Python FastAPI
是否使用 SQLite
是否需要桌面 App 包装
是否需要 Docker
首期是否必须完全离线
是否提供测试参考音频
是否有内部声音授权规则
```

---

## 30. 最终结论

Voice Studio 应按「本地优先、多引擎、可扩展语音生产工作站」开发。

正确路线是：

```text
先跑通本地双引擎
再沉淀声音资产
再做脚本级生产
再扩展云端/更多模型
```

首期不要做成单模型 Demo，也不要做成 ComfyUI 节点工具。

最终产品形态应更接近：

```text
本地版 ElevenLabs Studio
+ 多引擎 Adapter 架构
+ 声音资产库
+ 脚本级配音生产台
```

这样后续无论接入 IndexTTS、OmniVoice、MiMo、F5-TTS、CosyVoice、GPT-SoVITS，还是云端 TTS，都能在统一架构下扩展。
