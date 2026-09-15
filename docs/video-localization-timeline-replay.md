# 本土化时间轴开发重放工具

`scripts/debug_video_localization_timeline.py` 用于开发阶段单独调试“中文字幕断句与英文逐词时间轴映射”子流程。

## 使用边界

- 快照默认保存在系统临时目录，不写入 Voice Studio 正式项目数据。
- 正式本土化任务不会读取这些开发快照。
- 修改时间轴子流程内部逻辑时，可以复用已有输入快照，只重放时间轴及其后置校验。
- 修改前序本土化逻辑、草稿结构或时间轴输入后，需要重新捕获快照。
- 修复完成后，仍需运行一次不读取开发快照的完整本土化流程。

## 常用命令

查看全部子命令：

```bash
.venv/bin/python scripts/debug_video_localization_timeline.py --help
```

捕获时间轴入口输入：

```bash
.venv/bin/python scripts/debug_video_localization_timeline.py capture --help
```

只重放时间轴和后置结构校验：

```bash
.venv/bin/python scripts/debug_video_localization_timeline.py replay --help
```

只检查局部边界：

```bash
.venv/bin/python scripts/debug_video_localization_timeline.py focus --help
```

`probe`、`window`、`recursive`、`sectioned`、`refine` 和 `merge` 用于边界算法 POC 与人工核验，不属于产品运行入口。

## 缓存约束

正式重放断点会记录输入快照、模型配置、分段计划和时间轴实现指纹。实现或输入不兼容时，工具会拒绝复用旧断点；只有开发者明确确认代码变更不影响已完成模型步骤时，才可接受代码变化继续重放。
