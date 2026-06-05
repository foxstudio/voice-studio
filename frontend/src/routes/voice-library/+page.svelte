<script lang="ts">
  import { api } from '$lib/api';
  import type { VoiceAsset } from '$lib/api';
  import { Plus, Search, Play, Trash2, Upload, Music, Check, X, Edit3, Send, Filter } from 'lucide-svelte';

  let voices = $state<VoiceAsset[]>([]);
  let searchQuery = $state('');
  let filterTag = $state('');
  let filterAuth = $state('');
  let showAddModal = $state(false);
  let showEditModal = $state(false);

  // 新建表单
  let newName = $state('');
  let newDesc = $state('');
  let newLang = $state('zh');
  let newType = $state('test_sample');
  let newTags = $state('');
  let newAuth = $state('unknown');
  let newEngine = $state('');
  let newRefText = $state('');
  let newAudioFile: File | null = $state(null);
  let uploadQuality: any = $state(null);
  let uploading = $state(false);
  let uploadError = $state('');

  // 编辑表单
  let editVoice: VoiceAsset | null = $state(null);

  $effect(() => { api.get<VoiceAsset[]>('/voices').then(d => voices = d).catch(() => {}); });

  const voiceTypes = [
    { value: 'real_person', label: '真人' },
    { value: 'virtual_character', label: '虚拟角色' },
    { value: 'host', label: '主播' },
    { value: 'narrator', label: '旁白' },
    { value: 'test_sample', label: '测试样本' },
  ];
  const authStatuses = [
    { value: 'self_voice', label: '自己的声音' },
    { value: 'company_authorized', label: '公司授权' },
    { value: 'authorized', label: '已授权' },
    { value: 'test_only', label: '仅测试' },
    { value: 'unknown', label: '未知' },
  ];

  const allTags = $derived([...new Set(voices.flatMap(v => v.tags))]);

  let filtered = $derived(voices.filter(v => {
    if (searchQuery && !v.name.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    if (filterTag && !v.tags.includes(filterTag)) return false;
    if (filterAuth && v.license_status !== filterAuth) return false;
    return true;
  }));

  async function addVoice() {
    if (!newName.trim()) return;
    uploading = true; uploadError = '';
    try {
      let audioIds: string[] = [];
      if (newAudioFile) {
        const res = await api.upload('/voices/upload', newAudioFile) as any;
        audioIds = [res.file_id];
        uploadQuality = res.quality;
      }
      await api.post('/voices', {
        name: newName, description: newDesc, default_language: newLang,
        voice_type: newType, tags: newTags ? newTags.split(',').map(t => t.trim()) : [],
        license_status: newAuth, recommended_engine_id: newEngine || null,
        reference_text: newRefText, reference_audio_ids: audioIds,
      });
      voices = await api.get<VoiceAsset[]>('/voices');
      closeAddModal();
    } catch (e: any) { uploadError = e.message || '创建失败'; }
    finally { uploading = false; }
  }

  async function saveEdit() {
    if (!editVoice) return;
    await api.patch(`/voices/${editVoice.voice_id}`, {
      name: editVoice.name, description: editVoice.description,
      default_language: editVoice.default_language,
      voice_type: editVoice.voice_type,
      tags: editVoice.tags,
      license_status: editVoice.license_status,
      recommended_engine_id: editVoice.recommended_engine_id,
      reference_text: editVoice.reference_text,
    });
    voices = await api.get<VoiceAsset[]>('/voices');
    showEditModal = false; editVoice = null;
  }

  async function removeVoice(id: string) {
    await api.delete(`/voices/${id}`);
    voices = await api.get<VoiceAsset[]>('/voices');
  }

  async function testGenerate(id: string) {
    await api.post(`/voices/${id}/test-generate`, {});
  }

  function closeAddModal() {
    showAddModal = false; newName = ''; newDesc = ''; newAudioFile = null;
    uploadQuality = null; uploadError = '';
  }

  function openEdit(v: VoiceAsset) { editVoice = { ...v }; showEditModal = true; }
  function onFileChange(e: Event) { newAudioFile = (e.target as HTMLInputElement).files?.[0] || null; }
  function fmtSize(b: number) { return b < 1024 ? b+'B' : b < 1048576 ? (b/1024).toFixed(1)+'KB' : (b/1048576).toFixed(1)+'MB'; }
  function voiceTypeLabel(t: string) { return voiceTypes.find(v => v.value === t)?.label || t; }
  function authLabel(a: string) { return authStatuses.find(s => s.value === a)?.label || a; }

  let gotoGenerateWithVoice = (voiceId: string) => { window.location.href = `/generate?voice=${voiceId}`; };
</script>

<svelte:head><title>声音资产库 - Voice Studio</title></svelte:head>

<section>
  <div class="header">
    <div>
      <h1>声音资产库</h1>
      <p class="desc">管理参考音频与声音资产（{voices.length} 个声音）</p>
    </div>
    <button class="btn primary" onclick={() => showAddModal = true}>
      <Plus size={16} /> 新增声音
    </button>
  </div>

  <div class="toolbar">
    <div class="search-box">
      <Search size={16} />
      <input type="text" placeholder="搜索声音名称..." bind:value={searchQuery} />
    </div>
    <div class="filters">
      {#if allTags.length > 0}
        <select bind:value={filterTag}>
          <option value="">全部标签</option>
          {#each allTags as tag}<option value={tag}>{tag}</option>{/each}
        </select>
      {/if}
      <select bind:value={filterAuth}>
        <option value="">全部授权</option>
        {#each authStatuses as s}<option value={s.value}>{s.label}</option>{/each}
      </select>
    </div>
  </div>

  {#if filtered.length === 0}
    <div class="empty">
      <Music size={48} class="empty-icon" />
      <p>{voices.length === 0 ? '还没有声音资产' : '没有匹配的声音'}</p>
      <p class="dim">上传参考音频（3-10秒 wav/mp3），用于声音克隆</p>
    </div>
  {:else}
    <div class="voice-grid">
      {#each filtered as voice}
        <div class="voice-card" onclick={() => openEdit(voice)}>
          <div class="card-top">
            <div class="voice-avatar">{voice.name.charAt(0).toUpperCase()}</div>
            <div class="voice-info">
              <div class="voice-name">{voice.name}</div>
              <div class="voice-meta">
                {voiceTypeLabel(voice.voice_type)} · {voice.default_language.toUpperCase()}
              </div>
            </div>
            <span class="auth-badge" class:ok={voice.license_status === 'self_voice'}>
              {authLabel(voice.license_status)}
            </span>
          </div>
          <p class="voice-desc">{voice.description || '暂无描述'}</p>

          {#if voice.reference_audio_ids.length > 0}
            <div class="audio-badge has"><Music size={12} /> {voice.reference_audio_ids.length} 个参考音频</div>
          {:else}
            <div class="audio-badge none"><Upload size={12} /> 未上传参考音频</div>
          {/if}

          {#if voice.tags.length > 0}
            <div class="tags">{#each voice.tags as tag}<span class="tag">{tag}</span>{/each}</div>
          {/if}

          <div class="card-actions" onclick={(e) => e.stopPropagation()}>
            {#if voice.reference_audio_ids.length > 0}
              <button class="btn sm" title="试听"><Play size={12} /> 试听</button>
              <button class="btn sm primary" title="用于生成" onclick={() => gotoGenerateWithVoice(voice.voice_id)}>
                <Send size={12} /> 使用
              </button>
            {/if}
            <button class="btn sm ghost" title="编辑" onclick={() => openEdit(voice)}><Edit3 size={12} /></button>
            <button class="btn sm ghost danger" title="删除" onclick={() => removeVoice(voice.voice_id)}><Trash2 size={12} /></button>
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <!-- 新增声音弹窗 -->
  {#if showAddModal}
    <div class="modal-overlay" onclick={closeAddModal}>
      <div class="modal" onclick={(e) => e.stopPropagation()}>
        <h2>新增声音</h2>
        <label>声音名称 * <input type="text" bind:value={newName} placeholder="例：小美" /></label>
        <label>声音类型
          <select bind:value={newType}>{#each voiceTypes as t}<option value={t.value}>{t.label}</option>{/each}</select>
        </label>
        <label>描述 <input type="text" bind:value={newDesc} placeholder="声音特点描述" /></label>
        <label>语言 <select bind:value={newLang}><option value="zh">中文</option><option value="en">英文</option><option value="ja">日文</option></select></label>
        <label>标签（逗号分隔）<input type="text" bind:value={newTags} placeholder="温柔, 女声" /></label>
        <label>授权状态
          <select bind:value={newAuth}>{#each authStatuses as s}<option value={s.value}>{s.label}</option>{/each}</select>
        </label>
        <label>推荐引擎
          <select bind:value={newEngine}><option value="">自动</option><option value="indextts">IndexTTS</option><option value="omnivoice">OmniVoice</option></select>
        </label>
        <label>参考文本（可选）<input type="text" bind:value={newRefText} placeholder="参考音频对应的文字" /></label>

        <div class="upload-area">
          <label class="upload-label">
            <div class="upload-content">
              <Upload size={24} />
              {#if newAudioFile}
                <span class="has-file"><Check size={14} /> {newAudioFile.name} ({fmtSize(newAudioFile.size)})</span>
              {:else}
                <span>点击上传参考音频</span>
                <span class="dim">wav / mp3 / flac，建议 3-10 秒</span>
              {/if}
            </div>
            <input type="file" accept="audio/*" onchange={onFileChange} class="hidden-input" />
          </label>
        </div>

        {#if uploadQuality && !uploadQuality.passed}
          <div class="quality-warn">
            <X size={14} /> 质量检测未通过：
            {#each uploadQuality.warnings as w}<span>{w}</span>{/each}
          </div>
        {:else if uploadQuality?.warnings?.length > 0}
          <div class="quality-info">
            {#each uploadQuality.warnings as w}<span>⚠ {w}</span>{/each}
          </div>
        {/if}

        {#if uploadError}<div class="error-msg"><X size={14} /> {uploadError}</div>{/if}

        <div class="modal-actions">
          <button class="btn" onclick={closeAddModal} disabled={uploading}>取消</button>
          <button class="btn primary" onclick={addVoice} disabled={!newName.trim() || uploading}>
            {uploading ? '上传中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  {/if}

  <!-- 编辑声音弹窗 -->
  {#if showEditModal && editVoice}
    <div class="modal-overlay" onclick={() => showEditModal = false}>
      <div class="modal" onclick={(e) => e.stopPropagation()}>
        <h2>编辑声音：{editVoice.name}</h2>
        <label>声音名称 * <input type="text" bind:value={editVoice.name} /></label>
        <label>声音类型
          <select bind:value={editVoice.voice_type}>{#each voiceTypes as t}<option value={t.value}>{t.label}</option>{/each}</select>
        </label>
        <label>描述 <input type="text" bind:value={editVoice.description} /></label>
        <label>语言 <select bind:value={editVoice.default_language}><option value="zh">中文</option><option value="en">英文</option></select></label>
        <label>标签（逗号分隔）<input type="text" value={editVoice.tags.join(', ')} oninput={(e) => editVoice.tags = (e.target as HTMLInputElement).value.split(',').map(t => t.trim()).filter(Boolean)} /></label>
        <label>授权状态
          <select bind:value={editVoice.license_status}>{#each authStatuses as s}<option value={s.value}>{s.label}</option>{/each}</select>
        </label>
        <label>推荐引擎
          <select bind:value={editVoice.recommended_engine_id}><option value={null}>自动</option><option value="indextts">IndexTTS</option><option value="omnivoice">OmniVoice</option></select>
        </label>
        <label>参考文本 <input type="text" bind:value={editVoice.reference_text} /></label>

        {#if editVoice.reference_audio_ids.length > 0}
          <div class="audio-badge has"><Music size={12} /> 已关联 {editVoice.reference_audio_ids.length} 个参考音频</div>
          <button class="btn" onclick={() => testGenerate(editVoice!.voice_id)}><Send size={14} /> 测试生成</button>
        {:else}
          <div class="audio-badge none"><Upload size={12} /> 未上传参考音频</div>
        {/if}

        <div class="modal-actions">
          <button class="btn" onclick={() => showEditModal = false}>取消</button>
          <button class="btn primary" onclick={saveEdit}>保存</button>
        </div>
      </div>
    </div>
  {/if}
</section>

<style>
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.5rem; }
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 0.25rem; }
  .desc { color: var(--color-text-dim); font-size: 0.9rem; margin: 0; }
  .toolbar { display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
  .search-box { display: flex; align-items: center; gap: 0.5rem; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 8px; padding: 0.5rem 1rem; flex: 1; max-width: 320px; }
  .search-box input { background: none; border: none; color: var(--color-text); outline: none; font-size: 0.9rem; width: 100%; }
  .filters { display: flex; gap: 0.5rem; }
  .filters select { padding: 0.4rem 0.6rem; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 6px; color: var(--color-text); font-size: 0.8rem; }
  .btn { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.45rem 1rem; border-radius: 6px; border: 1px solid var(--color-border); background: var(--color-surface-2); color: var(--color-text); font-size: 0.85rem; cursor: pointer; }
  .btn.primary { background: var(--color-accent); color: white; border-color: var(--color-accent); }
  .btn.sm { padding: 0.3rem 0.6rem; font-size: 0.75rem; }
  .btn.ghost { background: none; border-color: transparent; }
  .btn.ghost.danger:hover { color: var(--color-danger); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .empty { text-align: center; padding: 4rem; color: var(--color-text-dim); }
  .empty-icon { opacity: 0.3; margin-bottom: 1rem; }
  .dim { font-size: 0.8rem; opacity: 0.7; }
  .voice-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 1rem; }
  .voice-card {
    padding: 1.25rem; background: var(--color-surface); border: 1px solid var(--color-border);
    border-radius: 12px; cursor: pointer; transition: border-color 0.15s;
  }
  .voice-card:hover { border-color: var(--color-accent); }
  .card-top { display: flex; gap: 0.75rem; margin-bottom: 0.75rem; align-items: center; }
  .voice-avatar { width: 36px; height: 36px; border-radius: 8px; background: var(--color-accent-dim); color: var(--color-accent); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 0.9rem; flex-shrink: 0; }
  .voice-info { flex: 1; min-width: 0; }
  .voice-name { font-weight: 600; font-size: 0.95rem; }
  .voice-meta { font-size: 0.72rem; color: var(--color-text-dim); }
  .auth-badge { font-size: 0.65rem; padding: 0.15rem 0.5rem; border-radius: 4px; background: var(--color-surface-2); color: var(--color-text-dim); white-space: nowrap; }
  .auth-badge.ok { background: oklch(30% 0.06 150); color: var(--color-success); }
  .voice-desc { font-size: 0.8rem; color: var(--color-text-dim); margin: 0 0 0.6rem; line-height: 1.4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .audio-badge { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.72rem; padding: 0.15rem 0.5rem; border-radius: 4px; margin-bottom: 0.5rem; }
  .audio-badge.has { background: oklch(30% 0.06 150); color: var(--color-success); }
  .audio-badge.none { background: var(--color-surface-2); color: var(--color-text-dim); }
  .tags { display: flex; gap: 0.25rem; flex-wrap: wrap; margin-bottom: 0.6rem; }
  .tag { font-size: 0.68rem; padding: 0.1rem 0.4rem; background: var(--color-surface-2); border-radius: 3px; color: var(--color-text-dim); }
  .card-actions { display: flex; gap: 0.4rem; flex-wrap: wrap; }

  /* Modal */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
  .modal { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 16px; padding: 2rem; min-width: 440px; max-width: 520px; max-height: 90vh; overflow-y: auto; }
  .modal h2 { margin: 0 0 1.25rem; font-size: 1.15rem; }
  .modal label { display: block; margin-bottom: 0.75rem; font-size: 0.82rem; color: var(--color-text-dim); }
  .modal input[type="text"], .modal select { display: block; width: 100%; margin-top: 0.3rem; padding: 0.45rem 0.7rem; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: 6px; color: var(--color-text); font-size: 0.88rem; outline: none; }
  .modal input:focus, .modal select:focus { border-color: var(--color-accent); }
  .upload-area { margin-bottom: 0.75rem; }
  .upload-label { display: block; cursor: pointer; }
  .upload-content { display: flex; flex-direction: column; align-items: center; gap: 0.4rem; padding: 1.25rem; border: 2px dashed var(--color-border); border-radius: 10px; color: var(--color-text-dim); font-size: 0.88rem; transition: border-color 0.15s; }
  .upload-content:hover { border-color: var(--color-accent); }
  .has-file { color: var(--color-success); font-size: 0.85rem; display: flex; align-items: center; gap: 0.3rem; }
  .hidden-input { display: none; }
  .quality-warn { background: oklch(30% 0.06 25); color: var(--color-danger); padding: 0.5rem 0.75rem; border-radius: 6px; font-size: 0.8rem; margin-bottom: 0.75rem; display: flex; flex-direction: column; gap: 0.2rem; }
  .quality-info { background: oklch(30% 0.06 85); color: var(--color-warning); padding: 0.5rem 0.75rem; border-radius: 6px; font-size: 0.8rem; margin-bottom: 0.75rem; display: flex; flex-direction: column; gap: 0.2rem; }
  .error-msg { display: flex; align-items: center; gap: 0.3rem; color: var(--color-danger); font-size: 0.85rem; margin-bottom: 0.75rem; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.5rem; }
</style>
