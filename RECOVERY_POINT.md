# 本地版本恢复说明

Git 历史是 Voice Studio 唯一的版本依据。本文件不记录某个长期不变的恢复提交：分支继续
开发后，固定提交、测试数量和引擎数量会立即过期，也不应被开源使用者误认为最新状态。

## 开工前保存进度

先查看当前分支和未提交改动，只暂存本次任务相关文件：

```bash
git status --short --branch
git diff
git add <本次任务文件>
git commit -m "<说明这次改动>"
```

需要隔离一项新工作时，先从已确认的提交创建本地分支。Codex 创建的开发分支默认使用
`codex/` 前缀：

```bash
git switch -c codex/<task-name>
```

## 查找和恢复历史版本

先查提交记录和目标提交的实际内容：

```bash
git log --oneline --decorate -20
git show --stat <commit>
```

如果要从旧提交继续工作，为它创建一个新的恢复分支；这样不会改写现有分支，也不会删除
工作区里的文件：

```bash
git switch -c codex/recovery-<name> <commit>
```

如果只是查看旧版本，可在另一个 worktree 或干净的临时克隆中打开。不要把会丢弃未提交
改动的强制重置命令写成通用恢复步骤。遇到脏工作区时，先确认改动归属并提交到本地分支，
不要覆盖或批量清理来源不明的文件。

## 远端边界

本地提交、分支和恢复操作不代表已经同步到 GitHub。只有得到明确授权后，才执行 push、
pull、fetch、创建远端分支或 PR 等远端操作。
