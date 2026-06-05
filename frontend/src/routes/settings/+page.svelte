<script lang="ts">
  import { api } from '$lib/api';
  import type { AppSettings } from '$lib/api';
  import { Save } from 'lucide-svelte';

  let settings = $state<AppSettings>({
    default_engine_id: 'indextts', default_language: 'zh', default_output_format: 'wav',
    model_dir: '~/VoiceStudio/models', voice_dir: '~/VoiceStudio/voices',
    output_dir: '~/VoiceStudio/outputs', export_dir: '~/VoiceStudio/exports',
    project_dir: '~/VoiceStudio/projects', device: 'auto', cloud_enabled: false,
  });
  let saved = $state(false);

  $effect(() => {
    api.get<AppSettings>('/settings').then(d => settings = d).catch(() => {});
  });

  async function save() {
    await api.patch<AppSettings>('/settings', settings);
    saved = true;
    setTimeout(() => saved = false, 2000);
  }
</script>

<svelte:head><title>设置 - Voice Studio</title></svelte:head>

<section>
  <h1>设置</h1>

  <div class="settings-grid">
    <div class="setting-group">
      <h2>通用</h2>
      <label>默认引擎
        <select bind:value={settings.default_engine_id}>
          <option value="indextts">IndexTTS</option>
          <option value="omnivoice">OmniVoice</option>
        </select>
      </label>
      <label>默认语言
        <select bind:value={settings.default_language}>
          <option value="zh">中文</option>
          <option value="en">英文</option>
        </select>
      </label>
      <label>默认输出格式
        <select bind:value={settings.default_output_format}>
          <option value="wav">WAV</option>
          <option value="mp3">MP3</option>
        </select>
      </label>
      <label>推理设备
        <select bind:value={settings.device}>
          <option value="auto">自动</option>
          <option value="gpu">GPU</option>
          <option value="cpu">CPU</option>
        </select>
      </label>
    </div>

    <div class="setting-group">
      <h2>目录</h2>
      <label>模型目录 <input type="text" bind:value={settings.model_dir} /></label>
      <label>声音目录 <input type="text" bind:value={settings.voice_dir} /></label>
      <label>输出目录 <input type="text" bind:value={settings.output_dir} /></label>
      <label>导出目录 <input type="text" bind:value={settings.export_dir} /></label>
    </div>

    <div class="setting-group">
      <h2>云端</h2>
      <label class="toggle-label">
        <span>启用云端引擎</span>
        <input type="checkbox" bind:checked={settings.cloud_enabled} />
      </label>
      <p class="dim">启用后，部分文本和音频将发送到第三方服务</p>
    </div>
  </div>

  <div class="actions">
    <button class="btn primary" onclick={save}>
      <Save size={14} /> {saved ? '已保存' : '保存设置'}
    </button>
  </div>
</section>

<style>
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 2rem; }
  .settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; max-width: 800px; }
  .setting-group {
    background: var(--color-surface); border: 1px solid var(--color-border);
    border-radius: 12px; padding: 1.5rem;
  }
  .setting-group h2 { font-size: 0.8rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--color-text-dim); margin: 0 0 1rem; }
  .setting-group label { display: block; margin-bottom: 1rem; font-size: 0.85rem; color: var(--color-text-dim); }
  .setting-group input[type="text"], .setting-group select {
    display: block; width: 100%; margin-top: 0.35rem; padding: 0.45rem 0.75rem;
    background: var(--color-surface-2); border: 1px solid var(--color-border);
    border-radius: 6px; color: var(--color-text); font-size: 0.85rem; outline: none;
  }
  .setting-group input:focus, .setting-group select:focus { border-color: var(--color-accent); }
  .toggle-label { display: flex !important; justify-content: space-between; align-items: center; }
  .dim { font-size: 0.8rem; color: var(--color-text-dim); margin: -0.5rem 0 0; }
  .actions { margin-top: 2rem; max-width: 800px; }
  .btn { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.5rem 1.25rem;
    border-radius: 8px; border: none; font-size: 0.85rem; font-weight: 600; cursor: pointer; }
  .btn.primary { background: var(--color-accent); color: white; }
</style>
