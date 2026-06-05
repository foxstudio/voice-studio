<script lang="ts">
  import { api } from '$lib/api';
  import type { VoiceAsset } from '$lib/api';
  import { Plus, Search, Play, Trash2, Upload } from 'lucide-svelte';

  let voices = $state<VoiceAsset[]>([]);
  let searchQuery = $state('');
  let showAddModal = $state(false);
  let newName = $state('');
  let newDesc = $state('');
  let newLang = $state('zh');

  $effect(() => {
    api.get<VoiceAsset[]>('/voices').then(d => voices = d).catch(() => {});
  });

  let filtered = $derived(
    voices.filter(v => !searchQuery || v.name.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  async function addVoice() {
    await api.post('/voices', { name: newName, description: newDesc, default_language: newLang });
    voices = await api.get<VoiceAsset[]>('/voices');
    showAddModal = false; newName = ''; newDesc = '';
  }

  async function removeVoice(id: string) {
    await api.delete(`/voices/${id}`);
    voices = await api.get<VoiceAsset[]>('/voices');
  }
</script>

<svelte:head><title>声音资产库 - Voice Studio</title></svelte:head>

<section>
  <div class="header">
    <div>
      <h1>声音资产库</h1>
      <p class="desc">管理参考音频与声音资产</p>
    </div>
    <button class="btn primary" onclick={() => showAddModal = true}>
      <Plus size={16} /> 新增声音
    </button>
  </div>

  <div class="toolbar">
    <div class="search-box">
      <Search size={16} />
      <input type="text" placeholder="搜索声音..." bind:value={searchQuery} />
    </div>
  </div>

  {#if filtered.length === 0}
    <div class="empty">
      <p>还没有声音资产</p>
      <p class="dim">点击"新增声音"添加第一个参考音频</p>
    </div>
  {:else}
    <div class="voice-grid">
      {#each filtered as voice}
        <div class="voice-card">
          <div class="card-top">
            <div class="voice-avatar">{voice.name.charAt(0).toUpperCase()}</div>
            <div class="voice-info">
              <div class="voice-name">{voice.name}</div>
              <div class="voice-meta">{voice.voice_type} · {voice.default_language.toUpperCase()}</div>
            </div>
          </div>
          <p class="voice-desc">{voice.description || '暂无描述'}</p>
          <div class="tags">
            {#each voice.tags as tag}
              <span class="tag">{tag}</span>
            {/each}
          </div>
          <div class="card-actions">
            <button class="btn sm"><Play size={12} /> 试听</button>
            <button class="btn sm ghost" onclick={() => removeVoice(voice.voice_id)}><Trash2 size={12} /></button>
          </div>
        </div>
      {/each}
    </div>
  {/if}

  {#if showAddModal}
    <div class="modal-overlay" onclick={() => showAddModal = false}>
      <div class="modal" onclick={(e) => e.stopPropagation()}>
        <h2>新增声音</h2>
        <label>名称 <input type="text" bind:value={newName} /></label>
        <label>描述 <input type="text" bind:value={newDesc} /></label>
        <label>语言
          <select bind:value={newLang}>
            <option value="zh">中文</option>
            <option value="en">英文</option>
            <option value="ja">日文</option>
          </select>
        </label>
        <div class="modal-actions">
          <button class="btn" onclick={() => showAddModal = false}>取消</button>
          <button class="btn primary" onclick={addVoice}>保存</button>
        </div>
      </div>
    </div>
  {/if}
</section>

<style>
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.5rem; }
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 0.25rem; }
  .desc { color: var(--color-text-dim); font-size: 0.9rem; margin: 0; }
  .toolbar { margin-bottom: 1.5rem; }
  .search-box { display: flex; align-items: center; gap: 0.5rem; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 8px; padding: 0.5rem 1rem; max-width: 320px; }
  .search-box input { background: none; border: none; color: var(--color-text); outline: none; font-size: 0.9rem; width: 100%; }
  .btn { display: flex; align-items: center; gap: 0.4rem; padding: 0.45rem 1rem; border-radius: 6px; border: 1px solid var(--color-border); background: var(--color-surface-2); color: var(--color-text); font-size: 0.85rem; cursor: pointer; }
  .btn.primary { background: var(--color-accent); color: white; border-color: var(--color-accent); }
  .btn.sm { padding: 0.3rem 0.6rem; font-size: 0.75rem; }
  .btn.ghost { background: none; border-color: transparent; }
  .empty { text-align: center; padding: 4rem; color: var(--color-text-dim); }
  .dim { font-size: 0.85rem; }
  .voice-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; }
  .voice-card { padding: 1.25rem; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 12px; }
  .card-top { display: flex; gap: 0.75rem; margin-bottom: 0.75rem; }
  .voice-avatar { width: 40px; height: 40px; border-radius: 10px; background: var(--color-accent-dim); color: var(--color-accent); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1rem; }
  .voice-name { font-weight: 600; font-size: 0.95rem; }
  .voice-meta { font-size: 0.75rem; color: var(--color-text-dim); }
  .voice-desc { font-size: 0.8rem; color: var(--color-text-dim); margin: 0 0 0.75rem; }
  .tags { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .tag { font-size: 0.7rem; padding: 0.15rem 0.5rem; background: var(--color-surface-2); border-radius: 4px; color: var(--color-text-dim); }
  .card-actions { display: flex; gap: 0.5rem; }
  /* Modal */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
  .modal { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 16px; padding: 2rem; min-width: 400px; }
  .modal h2 { margin: 0 0 1.5rem; font-size: 1.2rem; }
  .modal label { display: block; margin-bottom: 1rem; font-size: 0.85rem; color: var(--color-text-dim); }
  .modal input, .modal select { display: block; width: 100%; margin-top: 0.35rem; padding: 0.5rem 0.75rem; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: 6px; color: var(--color-text); font-size: 0.9rem; outline: none; }
  .modal input:focus, .modal select:focus { border-color: var(--color-accent); }
  .modal-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.5rem; }
</style>
