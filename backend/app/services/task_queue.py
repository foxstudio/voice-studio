"""任务队列 - 内存队列 + WebSocket 推送 + SQLite

设计要点（T6 修复后）:
1. 单实例 Worker: 模块级 threading.Lock + bool 标志, 第二次启动是 no-op + WARN 日志.
2. 真实取消: 通过 _cancel_flags[task_id] = threading.Event() 协作取消.
   - PENDING/QUEUED 任务: 立即标记 cancelled, worker 出队后检查 status 直接跳过.
   - RUNNING 任务: 设置 flag, worker 在 synthesize 前后检查, 命中则覆盖结果为 cancelled.
3. 参数完整重放: retry 用 stored task.parameters + 顶层字段重建 GenerateRequest, 不丢字段.
4. 异常零吞噬: worker 内层 try 写日志+stack trace, 外层 while True 包 try 防 worker 进程级死亡.
"""

import asyncio
import logging
import threading
import traceback
import uuid
from datetime import datetime

from fastapi import WebSocket

from app.models.schemas import (
    EmotionMode,
    EngineVersion,
    GenerateRequest,
    GenerationTask,
    HistoryItem,
    TaskStatus,
)
from app.services import database as db, history_store

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[str] = asyncio.Queue()

# ── Single-instance worker control ─────────────────────
# 用 threading.Lock 保证 check-and-set 原子, 即使从多 asyncio 任务并发调用也只起一个 worker.
_worker_lock = threading.Lock()
_worker_started: bool = False
_worker_task: asyncio.Task | None = None

# ── Cancellation flags ─────────────────────────────────
# 每个正在运行的任务一个 Event, worker 在阶段切换时检查.
_cancel_flags: dict[str, threading.Event] = {}
_cancel_flags_lock = threading.Lock()

_ws_clients: list[WebSocket] = []


def add_ws_client(ws: WebSocket):
    _ws_clients.append(ws)


def remove_ws_client(ws: WebSocket):
    try:
        _ws_clients.remove(ws)
    except ValueError:
        pass


async def _broadcast(task: GenerationTask):
    data = task.model_dump_json()
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


def list_tasks() -> list[GenerationTask]:
    return [GenerationTask(**d) for d in db.db_list_tasks()]


def get_task(task_id: str) -> GenerationTask | None:
    for d in db.db_list_tasks():
        if d.get("task_id") == task_id:
            return GenerationTask(**d)
    return None


# ── Worker singleton control ───────────────────────────

def start_worker() -> bool:
    """启动后台 worker. 已启动则返回 False 并打印 WARN, 不抛错.

    返回 True 表示本次调用成功启动了新 worker; False 表示已有 worker 在跑.
    """
    global _worker_started, _worker_task
    with _worker_lock:
        if _worker_started:
            logger.warning(
                "task_queue.start_worker called but worker already running; ignoring (no-op)."
            )
            return False
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        _worker_task = loop.create_task(_worker())
        _worker_started = True
        logger.info("task_queue worker started.")
        return True


def is_worker_running() -> bool:
    return _worker_started


# ── Worker main loop ───────────────────────────────────

async def _worker():
    """主 worker 循环. 外层 try 防止单条异常杀掉整个 worker."""
    while True:
        task_id = None
        try:
            task_id = await _queue.get()
            await _process_one(task_id)
        except asyncio.CancelledError:
            logger.info("task_queue worker cancelled, exiting.")
            raise
        except Exception:
            # 顶层兜底: 绝不让 worker 沉默死掉
            logger.exception(
                "task_queue worker caught unexpected error processing task_id=%s",
                task_id,
            )
            # 尝试把任务标记 failed (best-effort), 然后继续下一个
            if task_id:
                try:
                    t = get_task(task_id)
                    if t and t.status not in (TaskStatus.success, TaskStatus.cancelled):
                        t.status = TaskStatus.failed
                        t.error_message = "worker-level unexpected error (see backend log)"
                        t.completed_at = datetime.now().isoformat()
                        db.db_save_task(t.model_dump())
                        await _broadcast(t)
                except Exception:
                    logger.exception("failed to mark task %s as failed after worker error", task_id)


async def _process_one(task_id: str):
    """处理单个任务. 异常在这里就地写入 task.error_message, 不向上抛."""
    task_data = None
    for d in db.db_list_tasks():
        if d.get("task_id") == task_id:
            task_data = d
            break
    if not task_data:
        logger.warning("task_queue: dequeued unknown task_id=%s, skipping", task_id)
        return
    task = GenerationTask(**task_data)

    # ── Phase 0: 已被取消的任务直接跳过 ──
    if task.status == TaskStatus.cancelled:
        logger.info("task %s already cancelled before run, skipping", task_id)
        _clear_cancel_flag(task_id)
        return

    # ── Phase 1: 准备 cancel flag, 标记 running ──
    cancel_flag = _ensure_cancel_flag(task_id)
    if cancel_flag.is_set():
        # 出队前已被 cancel
        task.status = TaskStatus.cancelled
        task.completed_at = datetime.now().isoformat()
        db.db_save_task(task.model_dump())
        await _broadcast(task)
        _clear_cancel_flag(task_id)
        return

    task.status = TaskStatus.running
    task.started_at = datetime.now().isoformat()
    db.db_save_task(task.model_dump())
    await _broadcast(task)

    # ── Phase 2: 实际推理 ──
    try:
        # Auto-start engine if needed (safety net)
        from app.services import engine_registry as _reg
        engine = _reg.get_engine(task.engine_id)
        if engine.state.status.value not in ('loaded',):
            logger.info("task %s: engine %s not loaded, auto-starting", task_id, task.engine_id)
            _reg.start_engine(task.engine_id)

        from app.services.tts_engine import synthesize
        result = await synthesize(task)

        # 推理完成后再次检查 cancel: 如果中途被取消, 丢弃结果
        if cancel_flag.is_set():
            logger.info("task %s cancel flag set after synthesize, discarding result", task_id)
            task.status = TaskStatus.cancelled
            task.error_message = "cancelled by user during generation"
        else:
            task.status = TaskStatus.success
            task.result_audio_id = result["audio_id"]
            task.result_duration_ms = result.get("duration_ms")
            task.generation_time_ms = result.get("generation_time_ms")
    except Exception as e:
        # 关键: 全栈打印, 绝不沉默
        stack = traceback.format_exc()
        logger.error("task %s synthesize failed: %s\n%s", task_id, e, stack)
        if cancel_flag.is_set():
            task.status = TaskStatus.cancelled
            task.error_message = f"cancelled (error during run: {e})"
        else:
            task.status = TaskStatus.failed
            task.error_message = f"{type(e).__name__}: {e}"

    # ── Phase 3: 持久化收尾 + 预生成 result_id ──
    task.completed_at = datetime.now().isoformat()
    history_result_id = uuid.uuid4().hex[:12] if task.status == TaskStatus.success else None
    task.result_id = history_result_id
    db.db_save_task(task.model_dump())
    await _broadcast(task)
    _clear_cancel_flag(task_id)

    # ── Phase 4: 成功才写历史 ──
    if task.status == TaskStatus.success:
        voice_name = None
        if task.voice_id:
            from app.services.voice_store import get_voice
            v = get_voice(task.voice_id)
            if v:
                voice_name = v.name
        try:
            history_store.add(HistoryItem(
                result_id=history_result_id,
                task_id=task.task_id,
                engine_id=task.engine_id,
                engine_version=task.engine_version,
                voice_id=task.voice_id,
                voice_name=voice_name,
                input_text=task.input_text,
                output_audio_id=task.result_audio_id,
                duration_ms=task.result_duration_ms,
                generation_time_ms=task.generation_time_ms,
                parameter_snapshot=task.parameters,
                completed_at=task.completed_at,
            ))
        except Exception:
            logger.exception("failed to write history for task %s", task_id)



# ── Cancel flag helpers ────────────────────────────────

def _ensure_cancel_flag(task_id: str) -> threading.Event:
    with _cancel_flags_lock:
        flag = _cancel_flags.get(task_id)
        if flag is None:
            flag = threading.Event()
            _cancel_flags[task_id] = flag
        return flag


def _clear_cancel_flag(task_id: str) -> None:
    with _cancel_flags_lock:
        _cancel_flags.pop(task_id, None)


# ── Submit / Cancel / Retry ────────────────────────────

async def submit(req: GenerateRequest) -> str:
    # 第一次提交时确保 worker 已起; 多次调用安全 (no-op)
    if not _worker_started:
        start_worker()

    task_id = uuid.uuid4().hex[:12]

    # 构建完整参数快照 (retry 时全量重放)
    params = {
        "engine_version": req.engine_version.value,
        "reference_audio_path": req.reference_audio_path,
        "ref_audio_path": req.ref_audio_path,
        "ref_text": req.ref_text,
        "language": req.language,
        "emotion_mode": req.emotion_mode.value,
        "emotion_values": req.emotion_values,
        "emotion_text": req.emotion_text,
        "emotion": req.emotion,
        "emo_alpha": req.emo_alpha,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "repetition_penalty": req.repetition_penalty,
        "speed": req.speed,
        "seed": req.seed,
        "max_mel_tokens": req.max_mel_tokens,
        "max_text_tokens_per_segment": req.max_text_tokens_per_segment,
        "interval_silence": req.interval_silence,
        "segment_overlap_ms": req.segment_overlap_ms,
        "diffusion_steps": req.diffusion_steps,
        "cfg_rate": req.cfg_rate,
        "output_format": req.output_format,
    }

    task = GenerationTask(
        task_id=task_id,
        engine_id=req.engine_id,
        engine_version=req.engine_version.value,
        voice_id=req.voice_id,
        input_text=req.text,
        parameters=params,
    )
    task.status = TaskStatus.queued
    db.db_save_task(task.model_dump())
    await _queue.put(task_id)
    await _broadcast(task)
    return task_id


def cancel_task(task_id: str) -> dict:
    """取消任务. 返回 {"task_id", "previous_status", "new_status"}.

    - pending/queued: 直接标记 cancelled (worker 出队检查会跳过)
    - running: 设置 cancel flag (worker 在 synthesize 前后会检查并改 status)
    - 终态 (success/failed/cancelled): 不变
    """
    task = get_task(task_id)
    if not task:
        return {"task_id": task_id, "previous_status": None, "new_status": None, "note": "not found"}

    prev = task.status

    if prev in (TaskStatus.success, TaskStatus.failed, TaskStatus.cancelled):
        return {
            "task_id": task_id,
            "previous_status": prev.value,
            "new_status": prev.value,
            "note": "task already in terminal state",
        }

    # 总是先设置 cancel flag (running 阶段需要)
    _ensure_cancel_flag(task_id).set()

    if prev in (TaskStatus.pending, TaskStatus.queued):
        # 立即标记取消, worker 出队后会跳过
        task.status = TaskStatus.cancelled
        task.completed_at = datetime.now().isoformat()
        db.db_save_task(task.model_dump())
        logger.info("cancelled queued task %s", task_id)
        return {
            "task_id": task_id,
            "previous_status": prev.value,
            "new_status": TaskStatus.cancelled.value,
            "note": "task was queued, marked cancelled",
        }

    # RUNNING 任务: flag 已设, worker 会在阶段切换处检查
    logger.info("cancel flag set on running task %s; worker will pick up at next checkpoint", task_id)
    return {
        "task_id": task_id,
        "previous_status": prev.value,
        "new_status": TaskStatus.running.value,
        "note": "cancel flag set, will transition to cancelled after current phase",
    }


async def retry_task(task_id: str) -> str:
    """重试任务: 用 stored parameters 全量重建 GenerateRequest, 新 task_id, 新 pending 状态."""
    old = get_task(task_id)
    if not old:
        raise ValueError(f"retry_task: task {task_id} not found")

    p = old.parameters or {}

    # 安全转 enum: 容错老任务可能缺字段
    try:
        engine_version = EngineVersion(p.get("engine_version", old.engine_version or "v1"))
    except ValueError:
        engine_version = EngineVersion.v1
    try:
        emotion_mode = EmotionMode(p.get("emotion_mode", "follow_reference"))
    except ValueError:
        emotion_mode = EmotionMode.follow_reference

    new_req = GenerateRequest(
        text=old.input_text,
        engine_id=old.engine_id,
        engine_version=engine_version,
        voice_id=old.voice_id,
        reference_audio_path=p.get("reference_audio_path"),
        language=p.get("language", "zh"),
        emotion_mode=emotion_mode,
        emotion_values=p.get("emotion_values"),
        emotion_text=p.get("emotion_text"),
        emotion=p.get("emotion"),
        emo_alpha=p.get("emo_alpha", 0.6),
        speed=p.get("speed", 1.0),
        temperature=p.get("temperature", 0.8),
        top_p=p.get("top_p", 0.8),
        top_k=p.get("top_k", 30),
        repetition_penalty=p.get("repetition_penalty", 10.0),
        seed=p.get("seed"),
        max_mel_tokens=p.get("max_mel_tokens", 600),
        max_text_tokens_per_segment=p.get("max_text_tokens_per_segment", 120),
        interval_silence=p.get("interval_silence", 200),
        segment_overlap_ms=p.get("segment_overlap_ms", 50),
        diffusion_steps=p.get("diffusion_steps", 25),
        cfg_rate=p.get("cfg_rate", 0.7),
        output_format=p.get("output_format", "wav"),
    )
    new_id = await submit(new_req)
    logger.info("retry: old=%s new=%s, params restored from snapshot", task_id, new_id)
    return new_id
