# 开源发布边界

状态：current

Voice Studio 的完整应用发布物是 GitHub 源码仓库或源码归档。它包含 FastAPI 后端、
SvelteKit 前端、MLX IndexTTS 核心、macOS/Linux 启动脚本和 Windows PowerShell 入口。
模型权重、音色、项目、生成结果、数据库、缓存和密钥不属于发布物。

根 `LICENSE` 只覆盖贡献者有权以 MIT 授权的项目自有部分。内嵌或兼容实现的上游条款、
版权归属和模型权重许可见 `THIRD_PARTY_NOTICES.md` 与 `third_party_licenses/`。
`mlx_indextts` 的直接来源已经固定为 `solar2ain/mlx-indextts` 的公开 Git 历史，并保留
其 MIT 许可证；具体提交和路径见
[MLX-IndexTTS 来源记录](engines/mlx-indextts-source-provenance.md)。已记录与官方 v1/v2 的文件对应关系，且固定参照不等于未记录的历史 checkout。
公开分发前仍须按 [IndexTTS 分发条款核对表](engines/indextts-distribution-terms.md)
完成许可决定及相应义务；完整仓库和 wheel 不得对外宣称为无条件 MIT 发布物。

## 当前安装边界

| 环境 | 支持方式 | MLX 依赖 |
|---|---|---|
| macOS Apple Silicon | 原生启动 | `mlx` |
| Linux | 原生启动，默认 CPU | `mlx[cpu]`；CUDA 用户可按 MLX 官方说明切换后端 |
| Windows | PowerShell 检查后转交 WSL 2 | Windows 原生不安装 MLX；WSL 内按 Linux 处理 |

`uv build` 产生的源码包包含完整仓库应用；当前 Python wheel 只包含
`mlx_indextts` 推理核心，不能单独提供 WebUI 或 FastAPI 应用。因此，在完成独立的
应用打包器之前，不得把 wheel 描述成完整 Voice Studio 安装包。`voice-studio` 与
`mlx-indextts` console script 当前都属于 MLX IndexTTS 核心命令。

## 版本管理

发布版本只在根目录 `pyproject.toml` 的 `[project].version` 中维护。安装后的 Python
包、FastAPI/OpenAPI 和 `/api/health` 都从标准发行包元数据读取同一个值；直接从源码
运行时回读同一份 `pyproject.toml`。两者都不可用时必须显示 `0+unknown`，不得写死旧
版本作为兜底。`frontend/package.json` 是私有构建工作区，不维护独立产品版本。

每次发版还必须在 `CHANGELOG.md` 添加与 `[project].version` 相同的版本标题。跨平台
契约测试负责阻止版本来源重新分叉，发行包烟雾测试负责确认 wheel 内的元数据可读。

IndexTTS v2 的 Python 可选依赖组名为 `indextts2`。不使用 `v2` 作为 extra 名，
因为部分 Packaging/pip 组合会把 `extra == 'v2'` 误当成 PEP 440 版本比较，
导致包在解析其他 extras 时无法安装。发布门禁必须实际构建 sdist/wheel、
校验元数据，并在无依赖的隔离环境中执行 CLI `--help` 烟雾测试。

完整源码应用的生产入口是 `start-production.sh`；Windows 通过
`start.ps1 -Production` 转交 WSL 2。入口先用固定的 SvelteKit static adapter 构建
`frontend/build`，再由 FastAPI 同源提供 SPA 和 `/api`，生产运行不需要第二个 Node
服务。普通 `start.sh` 仍是双端口开发入口。`frontend/build` 是可重建产物，不提交 Git，
也不把它误描述为 Python wheel 的内容。

当前普通 TTS、批量和长文本队列由应用进程持有，因此同一数据库只允许一个后端实例。
服务生命周期会持有 `<voice_studio.db>.backend.lock` 的跨平台非阻塞锁；第二个进程会立即
给出“已有后端使用该数据目录”的错误，而不是等待或重复消费任务。不要使用
`uvicorn --workers 2`；需要并行开发实例时，为每个实例配置独立的
`VOICE_STUDIO_DATA_DIR` 和数据库。

## 模型分发规则

- Git 仓库和发布归档不包含任何模型权重。
- 自动下载只能使用目录中声明的固定来源和版本，并在使用前完成完整性校验。
- 代码许可与模型权重许可分别展示。需要额外确认的权重，未确认时不得开始下载。
- 许可不明确、来源无法固定或不能校验的模型，只提供官方页面，不开放自动安装。
- 模型统一写入 `VOICE_STUDIO_MODELS_DIR`，默认位于
  `VOICE_STUDIO_DATA_DIR/models`；运行时不允许静默联网补权重。
- IndexTTS 官方 PyTorch 权重需要在 macOS/Linux/WSL 内转换成 MLX；转换只执行一次，并把
  固定版本的 WAV 参考音频配套权重收进同一模型目录。原始权重和转换产物都不进入发布包。
- Windows 当前只承诺 WSL 2 兜底；原生 Windows 不宣称支持 MLX 模型转换或推理。

## 发布前门禁

后端 CI 使用 `uv sync --locked`，同时安装 `dev`、`server`、`convert`、
`indextts2`、`asr` 和 `video_localization` extras；不得仅安装 `dev` 后依赖其他
包偶然带入 Web 服务依赖。用户安装也必须使用锁文件，模型转换使用已安装环境，
不以另一条缩减 extras 的同步命令覆盖完整应用环境。

本土化回归脚本通过 `scripts/isolated_frontend_workspace.py` 在独占临时目录复制
前端源码，排除现有构建与环境文件，再生成该副本自身的 `.svelte-kit/tsconfig.json`。
测试、类型检查、构建和包体积检查均使用这份副本；不读取开发目录中的旧生成配置。
单独检查隔离构建体积时，给 `audit_video_localization_bundle.mjs` 传入
`--frontend-root <隔离前端目录>`。已安装依赖复制到工作区，避免缓存回写与原机器路径
进入构建注释；生成配置和构建输出不复用。

网页验收通过公共 `isolatedBrowserOptions` 禁止测试浏览器启动更新程序，
防止额外进程拖住退出；不修改用户浏览器的更新设置。完整门禁还覆盖 ASR 开发续接：
公共 API 触发后，真实网页检查运行状态、结果与刷新持久化，全程使用固定快照。

```bash
python scripts/verify_open_source_release.py
uv lock --check
uv build --out-dir <外部临时目录>
.venv/bin/python -m pytest -q
pnpm --dir frontend test
pnpm --dir frontend check
pnpm --dir frontend build
```

`verify_open_source_release.py` 只读取 Git 已跟踪文件，阻止模型、媒体、数据库、
本地运行目录、常见真实令牌和个人绝对路径进入发布；个人路径检查同时覆盖公开文档，
但允许 `/Users/example/...` 这类明确的教学占位符。门禁还检查必要的安全、贡献和第三方
声明文件、MLX 上游许可证和固定来源记录是否存在。它不替代人工版权来源和许可兼容性
判断；上述官方 IndexTTS 派生边界仍是明确的发布阻断项。构建与测试产物必须写到本轮
拥有的临时目录并在验证后清理。

## Git 历史与公开仓库

发布文件必须按清单选择：保留完整源码、测试、使用文档、构建配置和许可证；
排除运行报告与首次切换的内部审计记录。明确排除项见
`scripts/release_source_exclusions.json`。排除仅影响发布副本，不删除开发目录中的文件。
常规文件门禁不认识所有业务报告；JSON 后缀不代表文件一定可以公开。

维护者私人的音色导入脚本（`scripts/genshin_*.py`、`analyze_genshin_pack.py`、`voice_importer.py` 等）
针对本人素材与特定作品目录，只在本机保留，不随仓库分发；`.gitignore` 已登记这些路径。模型和声音素材的
使用权仍由使用者自行确认。

文件门禁只检查当前受跟踪文件，不证明 Git 历史可以公开。删除文件或加入
`.gitignore` 不会清除历史提交中的音频、内部日志或凭据。直接推送开发分支前，
必须单独审核将要推送的完整可达历史，不能把当前文件门禁通过当作历史通过。

若开发历史含有不应公开的内容，推荐保留原开发仓库，在独立目录创建经过审核的
源码快照作为首次公开版本；保留所有适用的版权声明、第三方许可证和固定来源记录。
快照必须包含已确认的完整实现，并从该快照验证构建与安装。不得直接复制整个工作
目录、模型或运行数据，也不得未确认就改写开发历史或强制推送。

公开仓库、组织归属和首次版本内容确认后，再统一检查 README、包元数据和安全
报告入口中的 GitHub 地址。源码快照发布不替代前述 IndexTTS 派生许可确认。
