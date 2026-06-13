# ChatGPT 对 Voice Studio 优化点的评估与建议

## 一、优先级重新排序

**先说结论**：你的优化点整体判断是靠谱的，但优先级有点"安全洁癖偏高、工程稳定性偏低"。Voice Studio 现在是单用户本地部署，真正要优先解决的不是"代码够不够优雅"，而是：换机器能不能跑、任务会不会丢、引擎挂了会不会拖死全局、生成结果能不能追溯、前端大页面还能不能改。

### 优化点重新排序

| 优化点 | 原级别 | 建议级别 | 判断 |
|--------|--------|----------|------|
| 硬编码个人路径 | P0 | **P0** | 对，换机器必炸，必须先修 |
| SQL 表名无白名单 | P0 | **P0/P1** | 如果 API 间接可控就是 P0；纯内部调用可降 P1，但很容易修，建议顺手 P0 修掉 |
| 异常处理宽泛 | P1 | **P0.5/P1** | 生成任务、Worker、文件写入、DB 写入处必须升 P0.5 |
| 前端测试覆盖低 | P1 | **P1/P2** | 不是全量补，而是优先覆盖核心流程 |
| 大文件拆分 | P2 | **P1.5** | 不是为了好看，是为了后续多引擎扩展不崩 |
| 通配符导入 | P2 | **P3** | 代码卫生问题，别抢主线资源 |
| 版本号重复 | P2 | **P2** | 会影响诊断、更新提示、打包发布，值得修 |
| 空目录遗留 | P2 | **P3** | 删除即可，别上纲上线 |
| DB 连接管理 | P3 | **P3** | 单机 SQLite 不急，先保证事务和 busy timeout |
| 模型缓存无驱逐 | P3 | **P1.5/P2** | Apple Silicon 内存有限，多模型 TTS 会真的顶爆内存 |
| CORS 硬编码 | P3 | **P2** | 很容易修，改成配置项即可 |

### 更重要的遗漏点

| 新增问题 | 建议级别 | 原因 |
|----------|----------|------|
| 任务队列状态持久化、取消、重试、恢复 | **P0.5** | TTS 长任务最怕中途失败后状态乱掉 |
| 多引擎隔离与能力注册表 | **P1** | 8 个引擎塞在一个 runner 里，后面会变成祖传汤锅 |
| 音频文件生命周期管理 | **P1** | 临时文件、导出文件、参考音频、失败产物都要有归属和清理策略 |
| 云端 API 密钥管理 | **P1** | MiMo TTS 云端 API 不应散落在代码或配置死角 |
| WebSocket 断线重连与任务状态恢复 | **P1** | 长篇合成时前端刷新/断线很常见 |
| 数据库 schema migration | **P1/P2** | v1.2 之后表结构变化会越来越频繁，没有迁移会很痛 |

---

## 二、当前阶段哪些是过度设计？

| 项目 | 判断 | 说明 |
|------|------|------|
| 数据库连接池 | **过度** | SQLite 官方支持多线程模式；先做清晰 context manager + busy_timeout + WAL + 事务边界 |
| 前端全量测试 | **过度** | 23 个组件不需要全测，优先测"用户真实操作、失败成本高、状态复杂"的路径 |
| 完整插件市场式引擎架构 | **偏过度** | 但"Engine Adapter + Registry"不算过度，这是多引擎平台的最低工程秩序 |
| 复杂 LRU 模型缓存 | **不急** | 但要有最小可用策略：最大缓存数、手动 unload、任务完成后可释放、健康页可查看内存占用 |
| CORS 安全体系 | **别搞复杂** | 只需要把 localhost 端口从硬编码改成配置项，别直接 * |

---

## 三、针对每个优化点的实施建议

### P0-1：硬编码个人路径

**必须马上修。**

建议做法：
```python
# settings.py
from pathlib import Path
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    project_root: Path = Path(__file__).resolve().parents[3]
    engines_root: Path | None = None
    indextts_path: Path | None = None
    f5_worker_path: Path | None = None

    class Config:
        env_file = ".env"
```

注意点：
- fallback 不要再写 `/Users/foxmacstudio/...`
- 缺失路径时不要假装能跑，应该在 health check 里明确返回 `not_configured`
- `.env.example` 必须提供
- 前端健康页应该展示"已配置 / 未配置 / 路径不存在 / 可执行文件不可用"

推荐状态：
```json
{
  "engine": "f5-tts",
  "status": "not_configured",
  "message": "F5 worker path is not configured"
}
```

### P0-2：SQL 表名无白名单

建议做法：
```python
ALLOWED_TABLES = {
    "voices": {"id", "name", "engine", "created_at", "updated_at"},
    "projects": {"id", "title", "created_at", "updated_at"},
    "tasks": {"id", "status", "engine", "created_at", "updated_at"},
}

def validate_identifier(table: str, field: str | None = None):
    if table not in ALLOWED_TABLES:
        raise ValueError(f"Invalid table: {table}")
    if field is not None and field not in ALLOWED_TABLES[table]:
        raise ValueError(f"Invalid field: {field}")
```

注意点：
- 不要只校验 table，key_field、排序字段、更新字段也要校验
- data.keys() 也要和字段集合比对
- 不建议做成复杂 ORM，当前阶段保持轻量即可

### P1-3：异常处理过于宽泛

建议分三层处理：
```python
try:
    result = await engine.synthesize(request)
except EngineConfigError as exc:
    logger.warning("Engine config error: %s", exc)
    raise HTTPException(status_code=400, detail=str(exc))
except EngineRuntimeError as exc:
    logger.exception("Engine runtime failed")
    raise HTTPException(status_code=500, detail="TTS engine failed")
except Exception as exc:
    logger.exception("Unexpected synthesis error")
    raise
```

优先整改位置：
- 引擎推理调用
- 外部子进程启动/停止
- 音频文件写入/转换
- 数据库写入
- WebSocket 广播
- 自动校对/ASR 流程

**关键点：任务失败必须落库，不能只在日志里闪现一下就消失。否则用户看到的是"卡住了"，开发看到的是"复现不了"。**

### P1-4：前端测试覆盖低

建议：

| 测试类型 | 工具 | 优先级 |
|----------|------|--------|
| 工具函数 / store / 状态机 | Vitest | P0 |
| 表单组件 / 上传组件 / 任务状态组件 | Vitest + Svelte component test | P1 |
| 真实用户流程 | Playwright | P1 |
| 视觉快照 | 暂不建议 | P3 |

先补 8～12 个高价值测试，不要一上来给 23 个组件都写。

### P2-5：大文件拆分

**task_queue.py** 建议拆成：
```
task_queue/
  service.py          # submit/cancel/retry
  worker.py           # consume and execute
  progress.py         # WebSocket progress events
  longform.py         # long text splitting/export
  autocorrect.py      # ASR + correction
  models.py           # task state models
```

**inference_runner.py** 建议改成：
```
engines/
  base.py
  registry.py
  indextts.py
  omnivoice.py
  emotivoice.py
  f5.py
  cosyvoice.py
  mimo.py
```

统一接口：
```python
class TTSEngine(Protocol):
    id: str

    async def health_check(self) -> EngineHealth:
        ...

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        ...
```

再加能力描述：
```python
@dataclass
class EngineCapabilities:
    voice_clone: bool
    emotion_control: bool
    batch: bool
    longform: bool
    cloud: bool
    streaming_progress: bool
```

### P2-7：版本号重复定义

建议确定一个唯一来源：
- 后端版本以 `pyproject.toml` 为准
- `main.py` 运行时读取 package metadata
- 前端通过 `/api/system/info` 获取后端版本
- `frontend/package.json` 只表示前端包版本，不强行等于后端

API 示例：
```json
{
  "app": "Voice Studio",
  "backend_version": "1.2.0",
  "frontend_version": "1.2.0",
  "api_version": "v1"
}
```

### P3-10：模型缓存无驱逐策略（建议提升到 P1.5/P2）

建议先做轻量版：
```python
MAX_CACHED_MODELS = 2
_model_cache: OrderedDict[str, LoadedModel] = OrderedDict()
```

策略：
- 超过数量，驱逐最久未使用模型
- 支持手动 unload
- 合成任务运行中不能驱逐
- health 页面显示当前加载模型
- 每个模型记录 loaded_at / last_used_at / memory_hint

---

## 四、更好的架构建议

Voice Studio 下一步不要直接"大重构"，而是做一层稳定的内部平台骨架：

```
Frontend
  ↓
API Layer
  ↓
Application Services
  - SynthesisService
  - VoiceLibraryService
  - ProjectService
  - AudioAssetService
  - TaskService
  ↓
Task Queue / Worker
  ↓
Engine Registry
  - IndexTTSAdapter
  - OmniVoiceAdapter
  - EmotiVoiceAdapter
  - F5Adapter
  - CosyVoiceAdapter
  - MiMoAdapter
  ↓
Storage
  - SQLite
  - Audio Files
  - Logs
```

### 核心原则

**第一，API 不直接知道具体引擎细节**

**第二，任务队列是中心，不是附属功能**

TTS 平台不是普通 CRUD 项目，核心资产是任务：
- 谁提交的
- 用哪个引擎
- 输入是什么
- 输出在哪里
- 成功还是失败
- 失败原因是什么
- 是否可重试
- 是否可复现

建议任务表里加：id, type, engine, status, input_payload, output_asset_id, error_code, error_message, progress, created_at, started_at, finished_at, retry_count

**第三，音频文件必须资产化**

建议做 audio_assets：
- id, kind (generated/reference/upload/export), path, format, duration, sample_rate, size, hash, created_at, source_task_id

**第四，前后端共享类型**

当前 SvelteKit + FastAPI 很适合走 OpenAPI 生成类型：
```
FastAPI OpenAPI schema → 生成 TypeScript client/types → 前端调用 API 不再手写 any
```

---

## 五、前端测试策略

### 第一优先级：合成主流程
输入文本 → 选择引擎 → 选择音色 → 提交任务 → WebSocket/轮询显示进度 → 完成后出现音频播放器 → 下载/导出

**这是 Voice Studio 的生命线。这个流程挂了，别的功能再漂亮都是 UI 手办。**

### 第二优先级：声音库
- 创建音色、上传参考音频、授权状态展示、删除音色二次确认
- 尤其要测：某个引擎不支持 voice clone 时，前端不能还给用户展示克隆入口

### 第三优先级：脚本工作室
- 多角色段落排序、角色绑定音色、批量提交、某一段失败后的状态展示、导出合并结果

### 第四优先级：音频工具
先拆组件：AudioUploadPanel, FormatConvertPanel, QualityCheckPanel, AudioPreview, ResultList

### 第五优先级：WebSocket 状态恢复
任务运行中刷新页面 → WebSocket 断开 → 重新进入任务详情页 → 能看到当前任务状态

---

## 六、落地顺序建议

### 第一轮：稳定性修复
- 去掉硬编码路径
- SQL 白名单
- 关键异常日志与任务失败落库
- CORS / API Key / 引擎路径统一配置
- 任务状态增加失败、取消、重试字段

### 第二轮：架构整理
- 抽 EngineAdapter
- 建 EngineRegistry
- 拆 inference_runner.py
- 拆 task_queue.py
- 音频文件资产化

### 第三轮：测试补强
- Vitest 测 store、helpers、表单校验
- 组件测试覆盖声音库、任务状态、上传组件
- Playwright 覆盖合成主流程、脚本工作室、任务恢复
- CI 里至少跑 lint、typecheck、backend tests、frontend unit tests

---

## 最后的直白判断

> 这项目现在最危险的不是"代码不够高级"，而是已经有明显的平台型项目特征：多引擎、多任务、多状态、多文件、多前端页面。
>
> 所以优化方向别变成"哪里脏修哪里"。更好的打法是：
>
> **先补配置和任务稳定性，再抽引擎边界，最后补测试。**
>
> 一句话：先让它换机器能跑、失败能查、任务能恢复；再让代码变漂亮。
