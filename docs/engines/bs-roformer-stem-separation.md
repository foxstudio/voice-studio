# BS-RoFormer 人声与背景声分离

> Current：视频本土化使用的唯一音轨分离方案，不是 TTS 引擎。

## 当前固定方案

| 项目 | 当前值 |
| --- | --- |
| 模型 | BS-RoFormer Viperx-1297 |
| 文件 | `model_bs_roformer_ep_317_sdr_12.9755.ckpt` |
| 架构 | MDXC / Band-Split RoFormer |
| 运行时 | `audio-separator==0.44.2` |
| 加速 | Apple Silicon 自动使用 MPS；设置页可固定 CPU |
| 输出 | 48 kHz、float WAV |
| 人声 | 模型估计的 Vocals |
| 背景声 | 原混音逐样本减去人声，不使用模型的伴奏输出 |

默认 `overlap=8`，这是本项目固定试听中采用的质量档。长视频默认每 300 秒作为一个
核心段，读取时额外带前后上下文，推理后裁掉上下文再连续写入；设置页允许在明确的
范围内调整这两个参数。

## 安装和文件管理

引擎管理页提供一键安装、进度、安装状态、空间占用和删除。权重保存到设置页的模型
目录下 `bs-roformer-viperx-1297/`，供所有项目共用。下载先写 `.part`，支持网络截断后
续传；最终文件只有通过固定大小和 SHA-256 校验才会启用。运行切片位于设置的缓存目录
`stem-separation/`，任务成功或失败都会清理。

模型权重不随 Voice Studio 代码仓库发布，也不会写入项目包。删除模型不会删除已有
项目生成的人声和背景声文件；下一次执行新分离前需要重新安装。

## 来源和许可边界

- [python-audio-separator 官方仓库](https://github.com/nomadkaraoke/python-audio-separator)：MIT 运行时及 Python API。
- [UVR/TRvlvr 模型发布仓库](https://github.com/TRvlvr/model_repo)：当前固定权重的下载源。
- [UVR/TRvlvr 配置与模型清单](https://github.com/TRvlvr/application_data)：模型 YAML 和下载清单。
- [BS-RoFormer 公开实现](https://github.com/lucidrains/BS-RoFormer)：架构背景；不是当前权重的下载源。

`python-audio-separator` 的代码许可为 MIT，并要求对 UVR 及其开发者保留致谢。当前
权重发布项没有随文件提供独立的模型卡或独立许可文本，因此产品只提供按需下载和来源
说明，不把权重再分发进代码仓库。Voice Studio 正式开源前必须再次核验该权重当时的
发布状态与再分发边界；如果许可仍不明确，保持“用户点击后从原发布源下载”，不得随
安装包或源码镜像捆绑。
