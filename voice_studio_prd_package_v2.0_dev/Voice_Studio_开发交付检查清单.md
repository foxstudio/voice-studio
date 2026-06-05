# Voice Studio｜开发交付检查清单

## 1. 必须先做技术 PoC

- [ ] IndexTTS 在目标 Mac 上跑通
- [ ] OmniVoice 在目标 Mac 上跑通
- [ ] 记录模型启动耗时
- [ ] 记录首条音频生成耗时
- [ ] 记录连续生成稳定性
- [ ] 记录内存占用
- [ ] 记录硬盘占用
- [ ] 明确模型文件下载/安装方式

## 2. P0 功能完成

- [ ] Dashboard
- [ ] Engine Hub
- [ ] Voice Library
- [ ] Single Generate
- [ ] Job Queue
- [ ] History
- [ ] Settings
- [ ] 基础导出
- [ ] 本地文件持久化

## 3. P1 功能完成

- [ ] Script Studio 基础版
- [ ] 多角色
- [ ] 批量生成
- [ ] 单段重生成
- [ ] 合并导出
- [ ] Text Tools 基础版
- [ ] 基础音频后处理

## 4. 验收条件

- [ ] IndexTTS 至少 3 条成功生成
- [ ] OmniVoice 至少 3 条成功生成
- [ ] 脚本项目至少 5 段批量生成成功
- [ ] 声音资产重启后不丢失
- [ ] 生成历史重启后不丢失
- [ ] 导出 WAV/MP3 可播放
- [ ] 错误状态有清晰提示
- [ ] 交付启动说明、模型安装说明、测试报告、已知问题列表
