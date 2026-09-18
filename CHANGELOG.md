# Changelog

## 1.4.0 - 2026-09-18

### 语音与音色

- 修复情绪识别：此前它把 funasr 直接导进主进程，依赖未在任何位置声明、模型权重也未安装，调用必然返回 500 并只在浏览器控制台留下一条日志。现在复用 CAM++ 引擎的 funasr 运行时、推理改走独立子进程，批量识别只加载一次模型而不是每个音色重启一次。
- 情绪识别的标签映射不再假定纯英文键：模型实际返回「生气/angry」这类中英混写标签，此前会使正确识别出的情绪被静默降级为平静。
- 运行环境或模型不完整时，接口返回可读原因，不再透出 worker 堆栈；情绪识别模型也登记进引擎模型页。

### 存储与清理

- 设置页新增「自动清理」：按保留天数管理可重建缓存、过程产物和生成结果三类内容。缓存与过程产物默认保留 30 天，生成结果默认永不清理，可改为 7/16/30/90/365 天或自定义天数，也可单独手动清理。
- 所有清理改为移入系统废纸篓，误清后可以自行恢复；移入失败时保留数据库记录，废纸篓不可用时跳过而不退回直接删除。此前启动时的孤立素材清理需要显式环境变量开启，实际对所有人都是关闭状态。

### 开源边界

- 维护者私人的音色导入脚本不再随源码分发，相关路径已登记到忽略清单；同时移除两份未执行的脚本目录迁移提议文档。
- README 补充 IndexTTS 2.0 衍生作品所需的第 4.1(a) 条不背书声明。
- 音色标签分类器不再内置特定作品名称，保留通用类别。

源码、内嵌第三方代码和模型适用各自条款，详见 `THIRD_PARTY_NOTICES.md`；模型权重和用户数据不随源码分发。

## 1.3.0 - 2026-09-15

### 视频本土化

- 增加按步骤继续 ASR 开发任务的能力，并保留已完成的检查结果。
- 加强本土化结果写入、任务来源绑定和失败后继续处理的一致性。
- 明确配音字幕菜单中的“生成／更新”和“全部重做”入口。

### 安装与发布

- 统一完整应用的可选依赖安装命令，使用锁定依赖安装。
- 新安装缺少模型时显示安装指引，避免在生成记录中展示内部健康检查数据。
- 补充第三方来源、许可证、模型与用户数据不随源码分发的说明。
- 隔离前端检查环境，补充 ASR 续接网页验收并修正验收浏览器退出。

源码、内嵌第三方代码和模型适用各自条款，详见 `THIRD_PARTY_NOTICES.md`；模型权重和用户数据不随源码分发。

## 1.2.0 - 2026-06-11

### Backend stability

- Stabilized task orchestration semantics for single, longform, and batch generation.
- Added regression coverage for cancellation, retry, restart recovery, partial success, and cloud idempotency behavior.
- Split engine responsibilities into policy, manifests, health, request builder, runner, and provider facade modules while preserving API response shape.
- Hardened persistent worker error context and parameter consistency tests for supported TTS paths.
- Added dry-run Voice Studio data audit manifest tooling.

### WebUI

- Stabilized the generate results panel layout, scrolling, toolbar responsiveness, queue status hints, card headers, action placement, and audio controls.
- Constrained long error messages inside result cards with internal scrolling and fade overflow.
- Added delayed, unified tooltips for icon-only controls in the generate workflow.

### CI and verification

- Fixed CI dependency setup for async pytest usage.
- Fixed Ruff regex escape failures.
- Verified frontend checks/builds and GitHub Actions for the stabilization commits.

### Notes

- No real `~/VoiceStudio` data, local model weights, or generated audio assets are moved by this release.
- `frontend/README.md` remains untracked because it appears to be the default Svelte template README and has not been accepted as project documentation.

## 1.1.0 - 2026-06-11

- Baseline Voice Studio stabilization tag before the post-`v1.1.0` backend architecture and generate WebUI hardening batches.
