# 运行能力契约

状态：current

`app.services.runtime_capabilities` 是操作系统、CPU 架构、可用计算设备、推理框架和
外部媒体工具的唯一运行时探测入口。调用方不得各自重新实现 CUDA、MPS、CPU 或
平台判断。

## 公共健康契约

`GET /api/health` 保留 `runtime_ready` 和 `runtime_capabilities` 兼容字段，并新增：

- `runtime_ready`：只表示核心 HTTP 服务已经可以工作；可选组件缺失不能把它设为
  `false`。
- `optional_runtime_ready`：所有已声明的可选运行能力是否齐全。
- `runtime_capabilities`：可选能力的扁平布尔映射，供旧客户端展示降级原因。
- `platform_capabilities`：版本化的运行能力快照，当前 `schema_version` 为 `1`。
- `data_dir`：为兼容旧客户端保留，但固定返回 `<redacted>`，避免健康检查泄露
  本机用户名和绝对路径；受信任的本机设置页通过设置接口读取真实目录。

快照包含规范化操作系统、CPU 架构、Python 版本、可用设备、首选设备、已安装推理
框架、各框架实际可用设备和可选组件状态。设备优先级为 CUDA、MPS、CPU；CPU
始终可表达，但这不代表每个模型或每个框架都支持 CPU。

## 边界

- `scripts/voice_studio_doctor.py` 是安装前的纯标准库启动检查，只判断系统、
  Python 和启动命令是否齐全；服务启动后的真实设备和引擎能力仍以
  `app.services.runtime_capabilities` 为唯一权威来源。
- Windows 检查必须区分“原生 MLX 不可用”和“可经 WSL 2 启动”。PowerShell 入口只负责
  确认 WSL 可调用和转换项目路径，不得要求 Windows 本机安装 Python；Python、uv、pnpm
  等真实运行依赖由 WSL 内的 `start.sh --doctor` 检查。缺少 WSL、WSL 发行版或其中的运行
  依赖时，启动器必须非零退出并给出可读提示。
- 运行能力快照描述当前机器，不替代引擎兼容性检查。
- `app.services.engine_compatibility` 把机器快照与引擎平台边界合并为公开的
  `EngineCompatibility`。引擎列表、详情和环境检查复用这一结果；不兼容时必须在
  模型健康检查之前停止，并给出可读替代建议。
- `app.services.execution_plan` 为支持设备选择的引擎生成 `schema_version=1` 的可序列化
  执行计划。任务提交时保存用户请求的设备，实际执行前会按当前机器重新校验；显式
  选择不可用设备时失败并提示可选项，只有 `auto` 可以自动选择。
- IndexTTS v2 是 MLX + PyTorch 混合运行时，设备设置控制其 PyTorch 参考音频预处理；
  OmniVoice 和 F5-TTS 是 PyTorch 引擎，不得按 MLX 平台边界误判，支持的设备由 PyTorch
  探测结果决定。F5-TTS 的单条、批量、诊断和备用进程必须传递同一执行计划；常驻 worker
  只在运行时路径、Python 和设备都相同时复用，设备变化时重启以免沿用旧模型实例。
- Qwen3-TTS 默认通过外部虚拟环境的常驻 worker 执行；同一模型种类连续请求复用已加载
  实例，切换 CustomVoice、Base 或 VoiceDesign 时只保留当前模型。关闭常驻模式后的一次性
  备用进程必须复用同一推理实现、参数归一化和音频后处理，不能形成第二套算法。
- VibeVoice ASR 4bit/8bit 通过 Apple Silicon 的 MLX/Metal 路径执行，一次推理同时生成
  文字、时间戳和匿名说话人标签。原生 Windows 不声明 MLX 兼容；模型目录、自动选择、
  内存门槛和本机基准见 [VibeVoice ASR 模型选择](../guides/VIBEVOICE_ASR_MODELS.md)。
- 引擎最终是否可运行仍需继续结合模型完整性和 provider 执行计划。
- 探测结果在服务进程内缓存；安装或移除运行时依赖后应重启服务。
- 外部虚拟环境 Python 路径统一通过 `app.services.python_runtime` 解析。Windows 使用
  `Scripts/python.exe`，POSIX 平台使用 `bin/python`。
- 模型权重统一由 `ModelStore` 解析：单模型环境变量优先，其次是
  `VOICE_STUDIO_MODELS_DIR` 或当前 `model_dir`，最后才是已登记的旧目录与只读缓存。解析器先
  检查对应模型的最小完整文件集合，残缺目录不得遮住完整回退。引擎代码和虚拟环境放在当前
  `data_dir/engines`；Qwen Forced Aligner 的仓库内旧虚拟环境只作兼容回退。不得用模块导入时
  冻结的目录常量驱动健康检查、模型页或实际推理。
- 可选组件缺失时应用继续启动，前端显示可读的降级提示。

- `PersistentWorker` uses newline-delimited JSON encoded as UTF-8 on both sides of its
  subprocess pipes. The parent sets an explicit pipe encoding and the child receives
  `PYTHONIOENCODING=utf-8`; host locale must not change Chinese or other Unicode payloads.
