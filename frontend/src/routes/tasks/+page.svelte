<script lang="ts">
  import { listTasks, cancelTask, retryTask } from '$lib/api/tasks';
  import type { GenerationTask, TaskStatusValue } from '$lib/api/types';
  import { ApiError, NetworkError } from '$lib/api/client';
  import { X, RotateCw } from 'lucide-svelte';

  let items = $state<GenerationTask[]>([]);
  let loading = $state(true);
  let errorMsg = $state<string | null>(null);

  async function fetchTasks() {
    try {
      errorMsg = null;
      items = await listTasks();
    } catch (e: unknown) {
      const msg = e instanceof ApiError || e instanceof NetworkError ? e.message : '加载失败';
      errorMsg = msg;
      console.error('[tasks] listTasks failed:', e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    fetchTasks();
    const timer = setInterval(fetchTasks, 3000);
    return () => clearInterval(timer);
  });

  async function handleCancel(taskId: string) {
    try {
      await cancelTask(taskId);
      items = items.map(t => t.task_id === taskId ? { ...t, status: 'cancelled' as TaskStatusValue } : t);
    } catch (e: unknown) {
      const msg = e instanceof ApiError || e instanceof NetworkError ? e.message : '取消失败';
      errorMsg = msg;
      console.error('[tasks] cancelTask failed:', e);
    }
  }

  async function handleRetry(taskId: string) {
    try {
      await retryTask(taskId);
      await fetchTasks();
    } catch (e: unknown) {
      const msg = e instanceof ApiError || e instanceof NetworkError ? e.message : '重试失败';
      errorMsg = msg;
      console.error('[tasks] retryTask failed:', e);
    }
  }

  const STATUS_LABELS: Record<TaskStatusValue, string> = {
    pending: '等待中',
    queued: '排队中',
    running: '运行中',
    postprocessing: '处理中',
    success: '已完成',
    failed: '失败',
    cancelled: '已取消',
    retrying: '重试中',
  };

  function statusClass(s: TaskStatusValue): string {
    if (s === 'success') return 'status-ok';
    if (s === 'failed') return 'status-err';
    if (s === 'cancelled') return 'status-dim';
    if (s === 'running' || s === 'postprocessing' || s === 'retrying') return 'status-run';
    return 'status-wait';
  }

  function isActive(s: TaskStatusValue): boolean {
    return s === 'pending' || s === 'queued' || s === 'running' || s === 'postprocessing' || s === 'retrying';
  }
</script>

<svelte:head><title>任务 - Voice Studio</title></svelte:head>

<section>
  <h1>任务</h1>
  <p class="desc">查看所有语音生成任务的实时状态</p>

  {#if loading}
    <div class="loading"><p>加载中...</p></div>
  {:else if errorMsg}
    <div class="error-banner"><p>加载失败：{errorMsg}</p></div>
  {:else if items.length === 0}
    <div class="empty">
      <p>还没有任务</p>
      <p class="dim">提交生成请求后将显示在这里</p>
    </div>
  {:else}
    <div class="task-list">
      {#each items as item (item.task_id)}
        <div class="task-item">
          <div class="item-left">
            <div class="item-top">
              <span class="item-text">{item.input_text}</span>
              <span class="status-badge {statusClass(item.status)}">{STATUS_LABELS[item.status]}</span>
            </div>
            <div class="item-meta">
              <span>引擎：{item.engine_id} {item.engine_version}</span>
              <span>音色：{item.voice_id ?? '—'}</span>
              <span>创建：{new Date(item.created_at).toLocaleString('zh-CN')}</span>
              {#if isActive(item.status) && item.progress > 0}
                <span>进度：{(item.progress * 100).toFixed(0)}%</span>
              {/if}
              {#if item.status === 'failed' && item.error_message}
                <span class="error-note" title={item.error_message}>错误：{item.error_message}</span>
              {/if}
            </div>
            {#if isActive(item.status)}
              <div class="progress-track">
                <div class="progress-fill" style="width: {Math.max(item.progress * 100, 2)}%"></div>
              </div>
            {/if}
          </div>
          <div class="item-actions">
            {#if isActive(item.status)}
              <button class="btn-icon cancel" onclick={() => handleCancel(item.task_id)} title="取消任务">
                <X size={16} />
              </button>
            {/if}
            {#if item.status === 'failed'}
              <button class="btn-icon retry" onclick={() => handleRetry(item.task_id)} title="重试">
                <RotateCw size={16} />
              </button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 0.25rem; }
  .desc { color: var(--color-text-dim); font-size: 0.9rem; margin: 0 0 1.5rem; }
  .empty { text-align: center; padding: 4rem; color: var(--color-text-dim); }
  .dim { font-size: 0.85rem; margin-top: 0.5rem; }
  .loading { text-align: center; padding: 4rem; color: var(--color-text-dim); }
  .error-banner { background: var(--color-danger); color: #fff; padding: 1rem 1.25rem; border-radius: 10px; margin-bottom: 1rem; }
  .error-note { color: var(--color-danger); font-size: 0.75rem; max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .task-list { display: flex; flex-direction: column; gap: 0.75rem; }
  .task-item {
    display: flex; justify-content: space-between; align-items: flex-start;
    padding: 1rem 1.25rem; background: var(--color-surface);
    border: 1px solid var(--color-border); border-radius: 10px;
  }
  .item-left { flex: 1; min-width: 0; }
  .item-top { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.35rem; }
  .item-text { font-size: 0.9rem; flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .item-meta { display: flex; flex-wrap: wrap; gap: 0.75rem; font-size: 0.75rem; color: var(--color-text-dim); margin-bottom: 0.35rem; }
  .item-actions { display: flex; align-items: center; gap: 0.5rem; margin-left: 1rem; }

  .status-badge {
    display: inline-block; font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 999px; white-space: nowrap; flex-shrink: 0;
  }
  .status-ok { background: color-mix(in srgb, var(--color-success) 20%, transparent); color: var(--color-success); }
  .status-err { background: color-mix(in srgb, var(--color-danger) 20%, transparent); color: var(--color-danger); }
  .status-run { background: color-mix(in srgb, var(--color-accent) 20%, transparent); color: var(--color-accent); }
  .status-wait { background: color-mix(in srgb, var(--color-text-dim) 20%, transparent); color: var(--color-text-dim); }
  .status-dim { background: color-mix(in srgb, var(--color-text-dim) 10%, transparent); color: var(--color-text-dim); opacity: 0.7; }

  .progress-track { height: 4px; background: var(--color-surface-2); border-radius: 2px; margin-top: 0.4rem; overflow: hidden; }
  .progress-fill { height: 100%; background: var(--color-accent); border-radius: 2px; transition: width 0.5s ease; }

  .btn-icon { background: none; border: none; cursor: pointer; padding: 0.4rem; border-radius: 6px; display: inline-flex; align-items: center; color: var(--color-text-dim); }
  .btn-icon.cancel:hover { background: color-mix(in srgb, var(--color-danger) 15%, transparent); color: var(--color-danger); }
  .btn-icon.retry:hover { background: color-mix(in srgb, var(--color-accent) 15%, transparent); color: var(--color-accent); }
</style>
