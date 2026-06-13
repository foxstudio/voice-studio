# _process 拆分执行计划（AB 共识版 v2）

## 当前状态
- 恢复点: `be4aa99` (RECOVERY_POINT.md)
- 核心文件: `backend/app/services/task_queue.py`
- 核心函数: `_process()` (718-830行, 112行)

## 拆分目标
把 `_process` 从"一个函数做所有事"变成"流水线 + 统一状态管理"。

## AB 共识要点
- A 方案（我）：从 _process 里长出 State Owner，不空降设计
- B 方案（ChatGPT）：统一状态主权，加 decide_task_state 规则层
- **合并结论**：渐进式收敛，统一写入口 + 统一决策点

---

## Step 1: 抽纯函数（不动主流程）

### 1.1 抽音频后处理
**从 _process 里抽出** (781-786行):
```python
def _postprocess_audio(task, req, result, audio_id):
    final_path = Path(result["output_path"])
    if req.output_format != "wav":
        converted = settings_store.output_dir() / f"{audio_id}.{req.output_format}"
        final_path = audio_tools.copy_or_convert(final_path, converted, req.output_format)
    if not final_path.exists() or final_path.stat().st_size <= 0:
        raise RuntimeError(f"生成完成但结果音频不存在：{final_path}")
    return final_path
```

### 1.2 抽历史记录写入
**从 _process 里抽出** (793-810行):
```python
def _save_history(task, req, final_path, audio_id, result):
    voice = voice_store.get_voice(req.voice_id) if req.voice_id else None
    hist = history_store.add(HistoryItem(
        task_id=task.task_id,
        engine_id=req.engine_id,
        voice_id=req.voice_id,
        voice_name=voice.name if voice else None,
        project_id=task.project_id,
        segment_id=task.segment_id,
        longform_task_id=task.longform_task_id,
        longform_segment_index=task.longform_segment_index,
        longform_segment_count=task.longform_segment_count,
        input_text=req.text,
        output_audio_id=audio_id,
        output_path=str(final_path),
        duration_ms=result.get("duration_ms"),
        generation_time_ms=result.get("generation_time_ms"),
        parameter_snapshot=task.parameters,
    ))
    return hist
```

### 1.3 抽项目段落更新
**从 _process 里抽出** (811-812行和824-825行):
```python
def _update_project_segment(task, audio_id, hist_result_id, status, error=None):
    if task.project_id and task.segment_id:
        project_store.update_segment_result(task.project_id, task.segment_id, audio_id, hist_result_id, status, error)
```

### 验证方式
- 跑 `uv run python -m pytest tests/ -q`
- _process 行为完全不变，只是调用新函数

---

## Step 2: 加 _update_status()（统一状态写入）

### 新增函数（ChatGPT 确认：统一用 async broadcast）
```python
async def _update_status(task, **kwargs):
    """唯一状态写入口：写 DB + 广播。不做任何业务逻辑。"""
    for key, value in kwargs.items():
        if value is not None and hasattr(task, key):
            setattr(task, key, value)
    _save(task)
    await _broadcast(task)
```

### 改造 _process
把所有直接的 `task.status = ...`、`task.progress = ...`、`_save(task)`、`_broadcast(task)` 替换为 `await _update_status(task, ...)` 调用。

### ChatGPT 技术确认
- broadcast 统一用 async `await _broadcast()`，不用 `_broadcast_from_thread`
- 避免线程/事件循环边界混乱

### 验证方式
- 跑全部测试
- 检查 WebSocket 推送是否正常

---

## Step 3: 加 decide_task_state()（统一状态决策）

### 新增函数
```python
def decide_task_state(task, *, engine_result=None, engine_error=None, cancelled=False):
    """统一状态决策：把各种结果翻译成状态。只返回决策，不写状态。"""
    if cancelled:
        return TaskStatus.cancelled, "已取消"
    if engine_error:
        if _task_is_protected_by_state(task):
            return None, None  # 不改状态，交给 DB 同步
        return TaskStatus.failed, str(engine_error)
    if engine_result:
        return TaskStatus.postprocessing, None
    return None, None
```

### ChatGPT 技术确认
- **不要在 try 和 except 都调用 decide_task_state**，会导致状态"覆盖竞争"
- 改为：try 里只产生"中间结果"，最终在单一 exit point 统一调用
- 建议用 finally 或函数末尾统一处理

### 改造 _process
把散落在 except/elif/else 里的状态判断逻辑替换为 `decide_task_state()` 调用，且只在单一出口调用。

### 验证方式
- 跑全部测试
- 测试各种失败场景（引擎超时、取消、ASR 失败）

---

## Step 4: 抽引擎调用

### 从 _process 里抽出 (738-770行)
```python
async def _execute_engine(task_id, engine_id, kwargs, wav_path):
    """纯引擎调用，不碰状态。返回原始结果。"""
    progress_state = {"last_sent_at": 0.0, "last_value": 0.24}
    
    def progress_tick(elapsed_seconds):
        # 原有进度计算逻辑，只更新 progress_state
        ...
    
    timeout_seconds = _timeout_seconds_for(engine_id)
    result = await asyncio.to_thread(
        engine_registry.run_isolated,
        engine_id,
        kwargs,
        timeout_seconds,
        lambda: task_id in _cancelled,
        progress_tick,
    )
    return result, progress_state
```

### ChatGPT 技术确认
- 不传 task 对象（避免耦合状态），传 task_id（只用于关联）+ progress_state（纯数据）
- progress_tick 闭包只依赖 progress_state，不依赖 task
- 返回 result + progress_state 两个值

### 验证方式
- 跑全部测试
- 测试各引擎的进度推送

---

## Step 5: _process 改流水线

### ChatGPT 技术确认
- 阶段之间要有明确数据契约（DTO），避免重新耦合
- 状态决策只在单一 exit point 调用，不在 try/except 都调用
- 建议用 finally 或函数末尾统一处理

### 改造后结构（单一 exit point 版）
```python
async def _process(task):
    if _task_is_protected_by_state(task):
        _sync_task_status_from_db(task)
        return
    
    final_status = None
    final_error = None
    hist = None
    
    try:
        req = GenerateRequest(**task.parameters)
        await _update_status(task, status=TaskStatus.running, started_at=now_iso(), progress=0.12)
        engine_registry.ensure_loaded(req.engine_id)
        await _update_status(task, progress=0.24)
        settings_store.ensure_directories()
        
        audio_id = task.task_id
        wav_path = settings_store.output_dir() / f"{audio_id}.wav"
        
        # Stage 2: 引擎执行（只传 task_id，不传 task）
        kwargs = _kwargs(req, str(wav_path))
        result, progress_state = await _execute_engine(task.task_id, req.engine_id, kwargs, wav_path)
        
        if task.task_id in _cancelled:
            final_status, final_error = TaskStatus.cancelled, "已取消"
        elif _task_is_protected_by_state(task):
            _sync_task_status_from_db(task)
            return
        else:
            # Stage 3: 音频后处理
            await _update_status(task, status=TaskStatus.postprocessing, progress=0.96)
            final_path = _postprocess_audio(task, req, result, audio_id)
            
            # Stage 4: 资产登记 + 历史
            await _update_status(task, status=TaskStatus.success, progress=1.0, 
                           result_audio_id=audio_id,
                           result_duration_ms=result.get("duration_ms"),
                           generation_time_ms=result.get("generation_time_ms"))
            hist = _save_history(task, req, final_path, audio_id, result)
            task.result_id = hist.result_id
            _update_project_segment(task, audio_id, hist.result_id, SegmentStatus.completed)
            
    except Exception as exc:
        if _task_is_protected_by_state(task):
            _sync_task_status_from_db(task)
            return
        elif task.task_id in _cancelled or str(exc) == "Generation cancelled":
            final_status, final_error = TaskStatus.cancelled, "已取消"
        else:
            final_status, final_error = TaskStatus.failed, str(exc)
    
    # 单一 exit point：统一状态决策
    if final_status:
        await _update_status(task, status=final_status, error_message=final_error, completed_at=now_iso())
        if final_status == TaskStatus.failed:
            _update_project_segment(task, None, None, SegmentStatus.failed, final_error)
    else:
        await _update_status(task, completed_at=now_iso())
    
    if task.status == TaskStatus.success and task.result_id:
        schedule_auto_verification(task.task_id)
```

### 验证方式
- 跑全部测试
- 完整流程测试：正常生成、取消、失败、重试
- 检查 WebSocket 推送
- 检查历史记录

---

## 风险控制（AB 共识版）
1. 每一步都跑完全部测试再继续
2. 旧 _process 保留为 _process_v1，新版本为 _process_v2
3. 灰度方案：runtime config + per-task flag（ChatGPT 建议，比环境变量更精细）
4. 前端是轮询，改后端状态机影响小
5. 状态决策只在单一 exit point 调用，避免覆盖竞争（ChatGPT 确认）
6. broadcast 统一用 async，不用 _broadcast_from_thread（ChatGPT 确认）

## 工作量估算
- Step 1: 2-3 小时
- Step 2: 2-3 小时
- Step 3: 1-2 小时
- Step 4: 2-3 小时
- Step 5: 2-3 小时
- 总计: 1-1.5 天
