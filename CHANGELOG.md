# Changelog

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
