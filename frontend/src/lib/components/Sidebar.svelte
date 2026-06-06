<script lang="ts">
  import { page } from '$app/stores';
  import {
    Home, Cpu, Mic, Play, FileText, Clock, Settings
  } from 'lucide-svelte';
  import { listEngines } from '$lib/api/engines';
  import type { EngineDetail } from '$lib/api/types';
  import { ApiError, NetworkError } from '$lib/api/client';

  const navItems = [
    { href: '/', label: 'Dashboard', icon: Home },
    { href: '/engine-hub', label: '引擎中心', icon: Cpu },
    { href: '/voice-library', label: '声音资产', icon: Mic },
    { href: '/generate', label: '单句合成', icon: Play },
    { href: '/script-studio', label: '脚本工作台', icon: FileText },
    { href: '/history', label: '历史记录', icon: Clock },
    { href: '/settings', label: '设置', icon: Settings },
  ];

  let engines = $state<EngineDetail[]>([]);
  let errorMsg = $state<string | null>(null);

  $effect(() => {
    listEngines()
      .then(data => {
        engines = data;
        errorMsg = null;
      })
      .catch((e: ApiError | NetworkError) => {
        errorMsg = e.message;
        engines = [];
        console.error('[sidebar] listEngines failed:', e);
      });
  });

  function statusClass(status: string): string {
    if (status === 'loaded' || status === 'running') return 'running';
    if (status === 'loading' || status === 'starting') return 'loading';
    if (status === 'error') return 'error';
    return 'idle';
  }
</script>

<nav class="sidebar">
  <div class="logo">
    <span class="logo-icon">🎙️</span>
    <span class="logo-text">Voice Studio</span>
  </div>

  <ul class="nav-list">
    {#each navItems as item}
      <li>
        <a
          href={item.href}
          class="nav-item"
          class:active={$page.url.pathname === item.href}
        >
          <item.icon size={18} />
          <span>{item.label}</span>
        </a>
      </li>
    {/each}
  </ul>

  <div class="sidebar-footer">
    {#if errorMsg}
      <div class="engine-status" title={errorMsg}>
        <span class="dot error"></span>
        <span>引擎离线</span>
      </div>
    {:else if engines.length === 0}
      <div class="engine-status">
        <span class="dot idle"></span>
        <span>无引擎</span>
      </div>
    {:else}
      {#each engines as engine}
        <div class="engine-status">
          <span class="dot {statusClass(engine.state.status)}"></span>
          <span>{engine.manifest.display_name}</span>
        </div>
      {/each}
    {/if}
  </div>
</nav>

<style>
  .sidebar {
    grid-column: 1;
    grid-row: 1 / -1;
    background: var(--color-surface);
    border-right: 1px solid var(--color-border);
    display: flex;
    flex-direction: column;
    padding: 0;
  }
  .logo {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 1.25rem 1.5rem;
    border-bottom: 1px solid var(--color-border);
  }
  .logo-icon { font-size: 1.5rem; }
  .logo-text {
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .nav-list {
    list-style: none;
    padding: 0.5rem;
    margin: 0;
    flex: 1;
  }
  .nav-item {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.65rem 1rem;
    border-radius: 8px;
    color: var(--color-text-dim);
    text-decoration: none;
    font-size: 0.875rem;
    transition: all 0.15s;
  }
  .nav-item:hover { background: var(--color-surface-2); color: var(--color-text); }
  .nav-item.active {
    background: var(--color-accent-dim);
    color: var(--color-accent);
    font-weight: 600;
  }
  .sidebar-footer {
    padding: 1rem 1.5rem;
    border-top: 1px solid var(--color-border);
  }
  .engine-status {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.8rem;
    color: var(--color-text-dim);
  }
  .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--color-text-dim);
  }
  .dot.running { background: var(--color-success); }
  .dot.loading { background: var(--color-warning, #eab308); }
  .dot.error { background: var(--color-error, #ef4444); }
  .dot.idle { background: var(--color-text-dim); }
</style>
