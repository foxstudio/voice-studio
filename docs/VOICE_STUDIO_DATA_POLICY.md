# Voice Studio 本地数据与模型规则

仓库只保存代码、文档、测试和“去哪里下载模型”的清单。模型权重、音色、生成结果、项目文件、数据库、API Key 都不进入 Git。

## `~/VoiceStudio` 目录

| 目录 | 用途 | 清理规则 |
|---|---|---|
| `config/` | 设置和 SQLite 数据库 | 核心数据，不自动删除 |
| `voices/` | 已保存到音色库的长期参考音 | 长期保留，只能由用户明确删除 |
| `assets/reference-audio/custom/` | 生成页临时上传的参考音 | 有任务/历史/预设等引用时保留；最后一个引用删除后回收，未使用上传 7 天后回收 |
| `assets/seed-audio/images/` | Seed Audio 等模型使用的图片输入 | 有任务/历史引用时保留；孤立上传 7 天后回收，内置预设长期保留 |
| `projects/` | 视频本土化等项目及其素材、轨道和导出 | 删除项目时再联动清理 |
| `outputs/` | 合成结果 | 删除对应生成记录时删除 |
| `exports/` | 用户明确导出的交付文件 | 不自动删除 |
| `cache/` | 波形、目录缓存、对齐日志等可重建数据 | TTL + 容量上限 + LRU 自动维护 |
| `models/` | 模型权重 | 不自动删除，不提交 Git |
| `engines/` | 外部引擎运行时或指向它们的软链接 | 不自动删除，不提交 Git |
| `reports/audits/` | 人工运行数据审计脚本时生成的报告 | 可重建，可人工清理 |
| `backups/` | 迁移或维护前留下的保护副本 | 确认不再回滚后人工清理 |

旧版 `seed_audio/assets/` 继续兼容读取，但新文件统一写入 `assets/seed-audio/images/`。
旧版 `manifests/` 只作为迁移兼容目录；新的审计报告统一写入 `reports/audits/`。

## 模型和引擎为什么分开

- `models/` 是权重，通常几个 GB，可以重新下载。
- `engines/` 是运行这些权重的代码和 Python 环境，也可以是指向其他位置的软链接。
- 二者用途不同，不合并。一个引擎可能共用多个模型，一个模型也可能由不同运行时调用。

新安装默认把模型放在 `~/VoiceStudio/models/`，不放进代码仓库。外部引擎路径按以下顺序查找：环境变量、`~/VoiceStudio/engines/<engine>`、仓库同级的 `tts-engine-lab/<engine>`。已有模型不必复制，使用环境变量或软链接即可。

## 自动清理边界

自动清理只处理可重建缓存。音色库、ASR 原始上传、项目、生成结果、导出、模型和引擎运行时永远不进入自动缓存清理范围。

默认缓存策略为 30 天 TTL、可重建缓存合计最多 1 GB；按最后访问时间优先淘汰旧文件。可用 `VOICE_STUDIO_CACHE_TTL_DAYS`、`VOICE_STUDIO_CACHE_MAX_BYTES` 和 `VOICE_STUDIO_ORPHAN_ASSET_TTL_DAYS` 调整。
