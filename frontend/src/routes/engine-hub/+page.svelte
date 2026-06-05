<script lang="ts">
  import { listEngines, startEngine, stopEngine, healthCheckEngine } from '$lib/api';
  import type { EngineDetail } from '$lib/api';
  import { Play, Square, RefreshCw, HardDrive, Globe } from 'lucide-svelte';

  let engines = $state<EngineDetail[]>([]);

  $effect(() => {
    listEngines().then(d => engines = d).catch(() => {});
  });

  async function toggleEngine(id: string, running: boolean) {
    const action = running ? 'stop' : 'start';
    if (action === 'start') { await startEngine(id); } else { await stopEngine(id); }
    engines = await listEngines();
  }

  async function checkHealth(id: string) {
    await healthCheckEngine(id);
  }

  function statusClass(status: string): string {
    if (status === 'running') return 'badge-running';
    if (status === 'stopped') return 'badge-stopped';
    return '';
  }
</script>

<svelte:head><title>引擎中心 - Voice Studio</title></svelte:head>

<section>
  <h1>引擎中心</h1>
  <p class="desc">管理本地与云端语音引擎</p>

  <div class="tabs">
    <button class="tab active">全部</button>
    <button class="tab">本地</button>
    <button class="tab">云端</button>
  </div>

  <div class="engine-grid">
    {#each engines as engine}
      {@const running = engine.state.status === 'running'}
      {@const isLocal = engine.manifest.engine_type === 'local'}
      <div class="engine-card">
        <div class="card-header">
          <div class="engine-icon">
            {#if isLocal}<HardDrive size={20} />{:else}<Globe size={20} />{/if}
          </div>
          <span class="status-badge {statusClass(engine.state.status)}">
            {engine.state.status}
          </span>
        </div>
        <h3>{engine.manifest.display_name}</h3>
        <p class="engine-desc">{engine.manifest.description}</p>

        <div class="lang-tags">
          {#each engine.manifest.supported_languages.slice(0, 4) as lang}
            <span class="lang-tag">{lang.toUpperCase()}</span>
          {/each}
        </div>

        <div class="caps">
          {#each engine.manifest.capabilities.slice(0, 4) as cap}
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
        </div>
      </div>
    {/each}
  </div>
</section>

<style>
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 0.25rem; }
  .desc { color: var(--color-text-dim); font-size: 0.9rem; margin: 0 0 1.5rem; }
  .tabs { display: flex; gap: 0.25rem; margin-bottom: 1.5rem; background: var(--color-surface); border-radius: 8px; padding: 0.25rem; width: fit-content; }
  .tab { padding: 0.4rem 1rem; border: none; background: none; color: var(--color-text-dim); font-size: 0.85rem; border-radius: 6px; cursor: pointer; }
  .tab.active { background: var(--color-surface-2); color: var(--color-text); }
  .engine-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 1rem; }
  .engine-card { padding: 1.5rem; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 12px; }
  .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
  .engine-icon { color: var(--color-accent); }
  .status-badge { font-size: 0.7rem; padding: 0.2rem 0.6rem; border-radius: 20px; background: var(--color-surface-2); color: var(--color-text-dim); }
  .badge-running { background: oklch(30% 0.08 150); color: var(--color-success); }
  .badge-stopped { background: oklch(30% 0.06 85); color: var(--color-warning); }
  h3 { font-size: 1.1rem; font-weight: 700; margin: 0 0 0.5rem; }
  .engine-desc { font-size: 0.8rem; color: var(--color-text-dim); margin: 0 0 1rem; line-height: 1.5; }
  .lang-tags, .caps { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .lang-tag { font-size: 0.7rem; padding: 0.15rem 0.5rem; background: var(--color-accent-dim); color: var(--color-accent); border-radius: 4px; font-weight: 600; }
  .cap-tag { font-size: 0.7rem; padding: 0.15rem 0.5rem; background: var(--color-surface-2); border-radius: 4px; color: var(--color-text-dim); }
  .card-actions { display: flex; gap: 0.5rem; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--color-border); }
  .btn { display: flex; align-items: center; gap: 0.4rem; padding: 0.45rem 1rem; border-radius: 6px; border: 1px solid var(--color-border); background: var(--color-surface-2); color: var(--color-text); font-size: 0.8rem; cursor: pointer; transition: all 0.15s; }
  .btn.primary { background: var(--color-accent); color: white; border-color: var(--color-accent); }
  .btn:hover { opacity: 0.85; }
  .btn.ghost { background: none; border-color: transparent; }
  .btn.ghost:hover { background: var(--color-surface-2); }
</style>
