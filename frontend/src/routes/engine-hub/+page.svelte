<script lang="ts">
  import { listEngines, startEngine, stopEngine, reloadEngine, healthCheckEngine, ApiError } from '$lib/api';
  import type { EngineDetail } from '$lib/api';
  import { Play, Square, RefreshCw, HardDrive, Globe, RotateCcw } from 'lucide-svelte';

  let engines = $state<EngineDetail[]>([]);
  let errorMsg = $state<string | null>(null);
  let activeTab = $state<'all' | 'v1' | 'v2'>('all');
  let loading = $state(true);

  $effect(() => {
    loading = true;
    errorMsg = null;
    listEngines()
      .then(d => { engines = d; loading = false; })
      .catch((e: Error | ApiError) => {
        errorMsg = e.message || '加载引擎列表失败';
        loading = false;
        console.error('[engine-hub] listEngines failed:', e);
      });
  });

  function getFilteredEngines(): EngineDetail[] {
    if (activeTab === 'all') return engines;
    return engines.filter(e => e.manifest.version === activeTab);
  }

  async function toggleEngine(id: string, running: boolean) {
    const action = running ? 'stop' : 'start';
    try {
      if (action === 'start') { await startEngine(id); } else { await stopEngine(id); }
      engines = await listEngines();
    } catch (e: unknown) {
      errorMsg = `操作失败: ${(e as Error).message}`;
    }
  }

  async function checkHealth(id: string) {
    try {
      await healthCheckEngine(id);
    } catch (e: unknown) {
      errorMsg = `健康检查失败: ${(e as Error).message}`;
    }
  }

  async function handleReload(id: string) {
    errorMsg = null;
    try {
      await reloadEngine(id);
      engines = await listEngines();
    } catch (e: unknown) {
      errorMsg = `重新加载失败: ${(e as Error).message}`;
    }
  }

  function statusClass(status: string): string {
    if (status === 'running' || status === 'loaded') return 'badge-running';
    if (status === 'stopped' || status === 'not_installed') return 'badge-stopped';
    if (status === 'error') return 'badge-error';
    if (status === 'loading' || status === 'starting') return 'badge-loading';
    return '';
  }

  function isEngineRunning(status: string): boolean {
    return status === 'running' || status === 'loaded';
  }

  function getEmotions(capabilities: string[]): string[] {
    const emotionLabels: Record<string, string> = {
      happy: '高兴', sad: '悲伤', angry: '愤怒', afraid: '恐惧',
      disgusted: '反感', melancholic: '低落', surprised: '惊讶', calm: '自然',
    };
    return capabilities
      .filter(c => c.startsWith('emotion_') && c !== 'emotion_control'
        && c !== 'emotion_reference' && c !== 'emotion_vector' && c !== 'emotion_text')
      .map(c => c.replace('emotion_', ''))
      .filter(e => e in emotionLabels)
      .map(e => emotionLabels[e]);
  }

  const EMOTION_COLORS: Record<string, string> = {
    '高兴': '#fbbf24', '悲伤': '#818cf8', '愤怒': '#f87171', '恐惧': '#c084fc',
    '反感': '#a78bfa', '低落': '#94a3b8', '惊讶': '#f472b6', '自然': '#34d399',
  };

  const TAB_LABELS: Record<string, string> = {
    all: '全部引擎',
    v1: 'IndexTTS v1',
    v2: 'IndexTTS v2',
  };
</script>

<svelte:head><title>引擎中心 - Voice Studio</title></svelte:head>

<section>
  <h1>引擎中心</h1>
  <p class="desc">管理本地与云端语音引擎</p>

  {#if errorMsg}
    <div class="error-banner" role="alert">
      <span>{errorMsg}</span>
      <button class="dismiss-btn" onclick={() => errorMsg = null}>✕</button>
    </div>
  {/if}

  <div class="tabs" role="tablist">
    {#each ['all', 'v1', 'v2'] as tab}
      <button
        class="tab"
        class:active={activeTab === tab}
        role="tab"
        aria-selected={activeTab === tab}
        onclick={() => activeTab = tab as 'all' | 'v1' | 'v2'}
      >
        {TAB_LABELS[tab]}
      </button>
    {/each}
  </div>

  {#if loading}
    <div class="loading-state">加载中...</div>
  {:else}
    {@const filtered = getFilteredEngines()}
    {#if filtered.length === 0}
      <div class="empty-state">暂无匹配的引擎</div>
    {:else}
      <div class="engine-grid" role="tabpanel">
        {#each filtered as engine}
          {@const running = isEngineRunning(engine.state.status)}
          {@const isLocal = engine.manifest.engine_type === 'local'}
          {@const emotions = getEmotions(engine.manifest.capabilities)}
          {@const hasEmotionControl = engine.manifest.capabilities.includes('emotion_control')}
          <div class="engine-card">
            <div class="card-header">
              <div class="engine-icon">
                {#if isLocal}<HardDrive size={20} />{:else}<Globe size={20} />{/if}
              </div>
              <div class="badge-group">
                {#if engine.manifest.version}
                  <span class="version-badge">v{engine.manifest.version}</span>
                {/if}
                <span class="status-badge {statusClass(engine.state.status)}">
                  {engine.state.status}
                </span>
              </div>
            </div>
            <h3>{engine.manifest.display_name}</h3>
            <p class="engine-desc">{engine.manifest.description}</p>

            {#if engine.manifest.sample_rate || engine.manifest.max_tokens}
              <div class="specs">
                {#if engine.manifest.sample_rate}
                  <span class="spec-tag">采样率: {engine.manifest.sample_rate} Hz</span>
                {/if}
                {#if engine.manifest.max_tokens}
                  <span class="spec-tag">最大 Token: {engine.manifest.max_tokens}</span>
                {/if}
              </div>
            {/if}

            {#if hasEmotionControl && emotions.length > 0}
              <div class="emotions-section">
                <span class="label">支持情绪:</span>
                <div class="emotion-tags">
                  {#each emotions as emotion}
                    <span
                      class="emotion-tag"
                      style="--emotion-color: {EMOTION_COLORS[emotion] || '#94a3b8'}"
                    >
                      {emotion}
                    </span>
                  {/each}
                </div>
              </div>
            {/if}

            <div class="caps">
              {#each engine.manifest.capabilities.filter(c => !c.startsWith('emotion_') || c === 'emotion_control') as cap}
                <span class="cap-tag">{cap.replace(/_/g, ' ')}</span>
              {/each}
            </div>

            <div class="card-actions">
              <button class="btn {running ? '' : 'primary'}" onclick={() => toggleEngine(engine.manifest.engine_id, running)}>
                {#if running}<Square size={14} /> 停止{:else}<Play size={14} /> 启动{/if}
              </button>
              <button class="btn ghost" onclick={() => checkHealth(engine.manifest.engine_id)}>
                <RefreshCw size={14} /> 检查
              </button>
              <button class="btn ghost" onclick={() => handleReload(engine.manifest.engine_id)}>
                <RotateCcw size={14} /> 重新加载
              </button>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</section>

<style>
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 0.25rem; }
  .desc { color: var(--color-text-dim); font-size: 0.9rem; margin: 0 0 1.5rem; }

  .error-banner {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0.6rem 1rem; margin-bottom: 1rem;
    background: oklch(30% 0.1 25); border: 1px solid oklch(45% 0.15 25);
    border-radius: 8px; color: var(--color-error);
    font-size: 0.85rem;
  }
  .dismiss-btn {
    background: none; border: none; color: var(--color-error);
    cursor: pointer; font-size: 1rem; padding: 0 0.25rem;
    opacity: 0.7;
  }
  .dismiss-btn:hover { opacity: 1; }

  .loading-state, .empty-state {
    text-align: center; padding: 3rem 1rem;
    color: var(--color-text-dim); font-size: 0.9rem;
  }

  .tabs { display: flex; gap: 0.25rem; margin-bottom: 1.5rem; background: var(--color-surface); border-radius: 8px; padding: 0.25rem; width: fit-content; }
  .tab {
    padding: 0.4rem 1rem; border: none; background: none;
    color: var(--color-text-dim); font-size: 0.85rem;
    border-radius: 6px; cursor: pointer; transition: all 0.15s;
  }
  .tab.active { background: var(--color-surface-2); color: var(--color-text); }
  .tab:hover:not(.active) { color: var(--color-text); }

  .engine-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 1rem;
  }
  .engine-card { padding: 1.5rem; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 12px; }
  .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
  .engine-icon { color: var(--color-accent); }
  .badge-group { display: flex; gap: 0.35rem; align-items: center; }
  .version-badge {
    font-size: 0.65rem; padding: 0.15rem 0.5rem; border-radius: 4px;
    background: var(--color-accent-dim); color: var(--color-accent);
    font-weight: 700;
  }
  .status-badge { font-size: 0.7rem; padding: 0.2rem 0.6rem; border-radius: 20px; background: var(--color-surface-2); color: var(--color-text-dim); }
  .badge-running { background: oklch(30% 0.08 150); color: var(--color-success); }
  .badge-stopped { background: oklch(30% 0.06 85); color: var(--color-warning); }
  .badge-error { background: oklch(30% 0.1 25); color: var(--color-error); }
  .badge-loading { background: oklch(30% 0.06 240); color: var(--color-info); }

  h3 { font-size: 1.1rem; font-weight: 700; margin: 0 0 0.5rem; }
  .engine-desc {
    font-size: 0.8rem; color: var(--color-text-dim);
    margin: 0 0 0.75rem; line-height: 1.5;
  }

  .specs { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .spec-tag {
    font-size: 0.7rem; padding: 0.2rem 0.5rem;
    background: var(--color-surface-2); border-radius: 4px;
    color: var(--color-text-dim); font-weight: 600;
  }

  .emotions-section { margin-bottom: 0.75rem; }
  .label {
    font-size: 0.7rem; color: var(--color-text-dim);
    display: block; margin-bottom: 0.3rem;
  }
  .emotion-tags { display: flex; gap: 0.3rem; flex-wrap: wrap; }
  .emotion-tag {
    font-size: 0.7rem; padding: 0.15rem 0.5rem;
    background: color-mix(in srgb, var(--emotion-color) 20%, transparent);
    color: var(--emotion-color); border-radius: 4px; font-weight: 600;
  }

  .caps { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .cap-tag { font-size: 0.7rem; padding: 0.15rem 0.5rem; background: var(--color-surface-2); border-radius: 4px; color: var(--color-text-dim); }

  .card-actions { display: flex; gap: 0.5rem; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--color-border); }
  .btn { display: flex; align-items: center; gap: 0.4rem; padding: 0.45rem 1rem; border-radius: 6px; border: 1px solid var(--color-border); background: var(--color-surface-2); color: var(--color-text); font-size: 0.8rem; cursor: pointer; transition: all 0.15s; }
  .btn.primary { background: var(--color-accent); color: white; border-color: var(--color-accent); }
  .btn:hover { opacity: 0.85; }
  .btn.ghost { background: none; border-color: transparent; }
  .btn.ghost:hover { background: var(--color-surface-2); }
</style>
