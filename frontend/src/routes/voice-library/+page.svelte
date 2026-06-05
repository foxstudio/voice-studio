<script lang="ts">
  import { api } from '$lib/api';
  import type { VoiceAsset } from '$lib/api';
  import { Plus, Search, Play, Trash2, Upload, Music, Check, X } from 'lucide-svelte';

  let voices = $state<VoiceAsset[]>([]);
  let searchQuery = $state('');
  let showAddModal = $state(false);

  // 新建声音表单
  let newName = $state('');
  let newDesc = $state('');
  let newLang = $state('zh');
  let newAudioFile: File | null = $state(null);
  let uploading = $state(false);
  let uploadError = $state('');

  $effect(() => {
    api.get<VoiceAsset[]>('/voices').then(d => voices = d).catch(() => {});
  });

  let filtered = $derived(
    voices.filter(v => !searchQuery || v.name.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  async function addVoice() {
    if (!newName.trim()) return;
    uploading = true;
    uploadError = '';
    try {
      let audioIds: string[] = [];
      // 先上传音频
      if (newAudioFile) {
        const uploadRes = await api.upload('/voices/upload', newAudioFile) as { file_id: string };
        audioIds = [uploadRes.file_id];
      }
      // 创建声音，关联音频
      await api.post('/voices', {
        name: newName,
        description: newDesc,
        default_language: newLang,
        reference_audio_ids: audioIds,
      });
      voices = await api.get<VoiceAsset[]>('/voices');
      showAddModal = false;
      newName = ''; newDesc = ''; newAudioFile = null;
    } catch (e: any) {
      uploadError = e.message || '创建失败';
    } finally {
      uploading = false;
    }
  }

  async function removeVoice(id: string) {
    await api.delete(`/voices/${id}`);
    voices = await api.get<VoiceAsset[]>('/voices');
  }

  function onFileChange(e: Event) {
    const input = e.target as HTMLInputElement;
    newAudioFile = input.files?.[0] || null;
  }

  function formatFileSize(bytes: number): string {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
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
      <Music size={48} class="empty-icon" />
      <p>还没有声音资产</p>
      <p class="dim">上传参考音频（3-10秒 wav/mp3），用于声音克隆</p>
    </div>
  {:else}
    <div class="voice-grid">
      {#each filtered as voice}
        <div class="voice-card">
          <div class="card-top">
            <div class="voice-avatar">{voice.name.charAt(0).toUpperCase()}</div>
            <div class="voice-info">
              <div class="voice-name">{voice.name}</div>
              <div class="voice-meta">
                {voice.voice_type} · {voice.default_language.toUpperCase()}
                · {voice.reference_audio_ids.length} 个参考音频
              </div>
            </div>
          </div>
          <p class="voice-desc">{voice.description || '暂无描述'}</p>

          {#if voice.reference_audio_ids.length > 0}
            <div class="audio-badge">
              <Music size={12} /> 已关联参考音频
            </div>
          {:else}
            <div class="audio-badge no-audio">
              <Upload size={12} /> 未上传参考音频
            </div>
          {/if}

          <div class="tags">
            {#each voice.tags as tag}
              <span class="tag">{tag}</span>
            {/each}
          </div>

          <div class="card-actions">
            {#if voice.reference_audio_ids.length > 0}
              <button class="btn sm"><Play size={12} /> 试听</button>
            {/if}
            <button class="btn sm ghost danger" onclick={() => removeVoice(voice.voice_id)}>
              <Trash2 size={12} />
            </button>
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <!-- 新增声音弹窗 -->
  {#if showAddModal}
    <div class="modal-overlay" onclick={() => showAddModal = false}>
      <div class="modal" onclick={(e) => e.stopPropagation()}>
        <h2>新增声音</h2>

        <label>
          声音名称 *
          <input type="text" bind:value={newName} placeholder="例：小美" />
        </label>

        <label>
          描述
          <input type="text" bind:value={newDesc} placeholder="声音特点描述" />
        </label>

        <label>
          语言
          <select bind:value={newLang}>
            <option value="zh">中文</option>
            <option value="en">英文</option>
            <option value="ja">日文</option>
          </select>
        </label>

        <div class="upload-area">
          <label class="upload-label">
            <div class="upload-content">
              <Upload size={24} />
              {#if newAudioFile}
                <span class="upload-file-selected">
                  <Check size={14} /> {newAudioFile.name} ({formatFileSize(newAudioFile.size)})
                </span>
              {:else}
                <span>点击上传参考音频</span>
                <span class="dim">支持 wav / mp3 / flac，建议 3-10 秒</span>
              {/if}
            </div>
            <input type="file" accept="audio/*" onchange={onFileChange} class="hidden-input" />
          </label>
        </div>

        {#if uploadError}
          <div class="error-msg"><X size={14} /> {uploadError}</div>
        {/if}

        <div class="modal-actions">
          <button class="btn" onclick={() => showAddModal = false} disabled={uploading}>取消</button>
          <button class="btn primary" onclick={addVoice} disabled={!newName.trim() || uploading}>
            {#if uploading}上传中...{:else}保存{/if}
          </button>
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
  .btn.ghost.danger:hover { color: var(--color-danger); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .empty { text-align: center; padding: 4rem; color: var(--color-text-dim); }
  .empty-icon { opacity: 0.3; margin-bottom: 1rem; }
  .dim { font-size: 0.8rem; opacity: 0.7; }
  .voice-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; }
  .voice-card { padding: 1.25rem; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 12px; }
  .card-top { display: flex; gap: 0.75rem; margin-bottom: 0.75rem; }
  .voice-avatar { width: 40px; height: 40px; border-radius: 10px; background: var(--color-accent-dim); color: var(--color-accent); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1rem; flex-shrink: 0; }
  .voice-name { font-weight: 600; font-size: 0.95rem; }
  .voice-meta { font-size: 0.75rem; color: var(--color-text-dim); }
  .voice-desc { font-size: 0.8rem; color: var(--color-text-dim); margin: 0 0 0.75rem; line-height: 1.4; }
  .audio-badge { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.75rem; padding: 0.2rem 0.6rem; border-radius: 4px; margin-bottom: 0.75rem; background: oklch(30% 0.08 150); color: var(--color-success); }
  .audio-badge.no-audio { background: var(--color-surface-2); color: var(--color-text-dim); }
  .tags { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .tag { font-size: 0.7rem; padding: 0.15rem 0.5rem; background: var(--color-surface-2); border-radius: 4px; color: var(--color-text-dim); }
  .card-actions { display: flex; gap: 0.5rem; }

  /* Modal */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
  .modal { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 16px; padding: 2rem; min-width: 440px; max-width: 500px; }
  .modal h2 { margin: 0 0 1.5rem; font-size: 1.2rem; }
  .modal label { display: block; margin-bottom: 1rem; font-size: 0.85rem; color: var(--color-text-dim); }
  .modal input[type="text"], .modal select { display: block; width: 100%; margin-top: 0.35rem; padding: 0.5rem 0.75rem; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: 6px; color: var(--color-text); font-size: 0.9rem; outline: none; }
  .modal input:focus, .modal select:focus { border-color: var(--color-accent); }

  /* Upload */
  .upload-area { margin-bottom: 1rem; }
  .upload-label { display: block; cursor: pointer; }
  .upload-content {
    display: flex; flex-direction: column; align-items: center; gap: 0.5rem;
    padding: 1.5rem; border: 2px dashed var(--color-border); border-radius: 10px;
    color: var(--color-text-dim); font-size: 0.9rem; transition: border-color 0.15s;
  }
  .upload-content:hover { border-color: var(--color-accent); color: var(--color-text); }
  .upload-file-selected { color: var(--color-success); font-size: 0.85rem; display: flex; align-items: center; gap: 0.3rem; }
  .hidden-input { display: none; }

  .error-msg { display: flex; align-items: center; gap: 0.35rem; color: var(--color-danger); font-size: 0.85rem; margin-bottom: 0.75rem; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.5rem; }
</style>
