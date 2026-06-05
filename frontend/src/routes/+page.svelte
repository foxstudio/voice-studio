<script lang="ts">
  import { Cpu, Mic, Play, Plus, ArrowRight } from 'lucide-svelte';
  import { api } from '$lib/api';
  import type { EngineDetail } from '$lib/api';

  let engines = $state<EngineDetail[]>([]);

  $effect(() => {
    api.get<EngineDetail[]>('/engines').then(d => engines = d).catch(() => {});
  });

  const quickActions = [
    { label: '配置引擎', desc: '设置 IndexTTS / OmniVoice', href: '/engine-hub', icon: Cpu },
    { label: '导入声音', desc: '上传参考音频', href: '/voice-library', icon: Mic },
    { label: '开始合成', desc: '生成第一条语音', href: '/generate', icon: Play },
  ];
</script>

<svelte:head><title>Voice Studio</title></svelte:head>

<section class="dashboard">
  <div class="hero">
    <h1>Voice Studio</h1>
    <p class="subtitle">本地优先的语音生产工作站</p>
  </div>

  <div class="section">
    <h2>快速开始</h2>
    <div class="card-grid">
      {#each quickActions as action}
        <a href={action.href} class="action-card">
          <div class="action-icon"><action.icon size={22} /></div>
          <div>
            <div class="action-label">{action.label}</div>
            <div class="action-desc">{action.desc}</div>
          </div>
          <ArrowRight size={16} class="action-arrow" />
        </a>
      {/each}
    </div>
  </div>

  <div class="section">
    <h2>引擎状态</h2>
    <div class="card-grid">
      {#each engines as engine}
        <div class="engine-card">
          <div class="engine-name">{engine.manifest.display_name}</div>
          <div class="engine-desc">{engine.manifest.description}</div>
          <div class="engine-status">
            <span class="dot" class:running={engine.state.status === 'running'}
              class:stopped={engine.state.status === 'stopped'}></span>
            <span>{engine.state.status}</span>
          </div>
          <div class="engine-caps">
            {#each engine.manifest.capabilities.slice(0, 3) as cap}
              <span class="cap-tag">{cap}</span>
            {/each}
          </div>
        </div>
      {/each}
    </div>
  </div>
</section>

<style>
  .dashboard { max-width: 960px; margin: 0 auto; }
  .hero { margin-bottom: 2.5rem; }
  .hero h1 { font-size: 2rem; font-weight: 800; letter-spacing: -0.03em; margin: 0; }
  .subtitle { color: var(--color-text-dim); margin: 0.25rem 0 0; font-size: 1rem; }
  .section { margin-bottom: 2rem; }
  .section h2 { font-size: 1rem; font-weight: 600; margin: 0 0 1rem; color: var(--color-text-dim); text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.8rem; }
  .card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }
  .action-card {
    display: flex; align-items: center; gap: 1rem;
    padding: 1.25rem; background: var(--color-surface);
    border: 1px solid var(--color-border); border-radius: 12px;
    text-decoration: none; color: var(--color-text); transition: all 0.15s;
  }
  .action-card:hover { border-color: var(--color-accent); transform: translateY(-1px); }
  .action-icon { color: var(--color-accent); }
  .action-label { font-weight: 600; font-size: 0.95rem; }
  .action-desc { font-size: 0.8rem; color: var(--color-text-dim); }
  .action-arrow { margin-left: auto; color: var(--color-text-dim); }
  .engine-card {
    padding: 1.25rem; background: var(--color-surface);
    border: 1px solid var(--color-border); border-radius: 12px;
  }
  .engine-name { font-weight: 700; font-size: 1rem; margin-bottom: 0.25rem; }
  .engine-desc { font-size: 0.8rem; color: var(--color-text-dim); margin-bottom: 0.75rem; }
  .engine-status { display: flex; align-items: center; gap: 0.5rem; font-size: 0.8rem; margin-bottom: 0.5rem; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-text-dim); }
  .dot.running { background: var(--color-success); }
  .dot.stopped { background: var(--color-warning); }
  .engine-caps { display: flex; gap: 0.35rem; flex-wrap: wrap; }
  .cap-tag {
    font-size: 0.7rem; padding: 0.15rem 0.5rem;
    background: var(--color-surface-2); border-radius: 4px; color: var(--color-text-dim);
  }
</style>
