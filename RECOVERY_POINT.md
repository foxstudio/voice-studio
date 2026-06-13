# 恢复点记录

## 当前恢复点

- **Commit**: `be4aa99`
- **分支**: `main`
- **日期**: 2026-06-13
- **说明**: 架构优化前的基线版本。包含本土化授权状态、批处理错误处理改进、ChatGPT 优化评估文档。

## 恢复命令

```bash
git reset --hard be4aa99
```

## 状态快照

- 后端: FastAPI + SQLite, 38 个服务文件
- 前端: SvelteKit 5, 23 个组件
- 测试: 174 个用例 (170 passed, 4 skipped)
- 引擎: 8 个 TTS 引擎 (IndexTTS v2, OmniVoice, EmotiVoice, F5-TTS, CosyVoice, MiMo)

## 已知问题 (来自 ChatGPT 评估)

1. task_queue.py 830 行, 职责过多
2. inference_runner.py 522 行, 8 个引擎逻辑混杂
3. 硬编码个人路径
4. 模型缓存无驱逐策略
5. 前端测试覆盖极低
