<script lang="ts">
  import { getSettings, updateSettings } from '$lib/api';
  import { ApiError, NetworkError } from '$lib/api';
  import type { AppSettings } from '$lib/api';
  import { Save } from 'lucide-svelte';

  let settings = $state<AppSettings | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let saved = $state(false);
  let saveError = $state<string | null>(null);

  const EMOTION_LABELS: Record<string, string> = {
    happy: '高兴', sad: '悲伤', angry: '愤怒', afraid: '恐惧',
    disgusted: '反感', melancholic: '低落', surprised: '惊讶', calm: '自然',
  };

  const THEME_LABELS: Record<string, string> = {
    light: '浅色', dark: '深色', system: '跟随系统',
  };

  async function load() {
    loading = true;
    error = null;
    try {
      settings = await getSettings();
    } catch (e: unknown) {
      if (e instanceof ApiError || e instanceof NetworkError) {
        error = e.message;
      } else {
        error = '加载设置失败';
      }
      console.error('[settings] getSettings failed:', e);
    } finally {
      loading = false;
    }
  }

  // Load on mount
  $effect(() => { load(); });

  async function save() {
    if (!settings) return;
    saveError = null;
    try {
      const result = await updateSettings(settings);
      settings = result;
      saved = true;
      setTimeout(() => saved = false, 2000);
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        saveError = `保存失败: ${e.message}`;
      } else if (e instanceof NetworkError) {
        saveError = `网络错误: ${e.message}`;
      } else {
        saveError = '保存设置失败';
      }
      console.error('[settings] updateSettings failed:', e);
    }
  }
</script>

<section>
  <h1>设置</h1>

  {#if loading}
    <div class="loading">加载中…</div>
  {:else if error}
    <div class="error-card">
      <p class="error-msg">加载失败: {error}</p>
      <button class="btn" onclick={load}>重试</button>
    </div>
  {:else if settings}
    <div class="settings-grid">
      <div class="setting-group">
        <h2>通用</h2>
        <label>引擎版本
          <select bind:value={settings.default_engine_version}>
            <option value="v1">IndexTTS v1</option>
            <option value="v2">IndexTTS v2</option>
          </select>
        </label>
        <label>默认语言
          <select bind:value={settings.default_language}>
            <option value="zh">中文</option>
            <option value="en">英文</option>
          </select>
        </label>
        <label>默认情感
          <select bind:value={settings.default_emotion}>
            {#each Object.entries(EMOTION_LABELS) as [val, label]}
              <option value={val}>{label}</option>
            {/each}
          </select>
        </label>
        <label>情感强度
          <div class="range-row">
            <input type="range" min="0" max="0.8" step="0.05" bind:value={settings.default_emo_alpha} />
            <span class="range-value">{settings.default_emo_alpha.toFixed(2)}</span>
          </div>
        </label>
        <label>主题
          <select bind:value={settings.theme}>
            {#each Object.entries(THEME_LABELS) as [val, label]}
              <option value={val}>{label}</option>
            {/each}
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
        <label>项目目录 <input type="text" bind:value={settings.project_dir} /></label>
      </div>

      <div class="setting-group">
        <h2>其他</h2>
        <label class="toggle-label">
          <span>启用云端引擎</span>
          <input type="checkbox" bind:checked={settings.cloud_enabled} />
        </label>
        <p class="dim">启用后，部分文本和音频将发送到第三方服务</p>
      </div>
    </div>

    <div class="actions">
      {#if saveError}
        <div class="toast toast-error">{saveError}</div>
      {/if}
      {#if saved}
        <div class="toast toast-success">已保存</div>
      {/if}
      <button class="btn primary" onclick={save}>
        <Save size={14} /> 保存设置
      </button>
    </div>
  {/if}
</section>

<style>
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 2rem; }

  .loading { color: var(--color-text-dim); padding: 2rem 0; }
  .error-card { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 12px; padding: 2rem; max-width: 400px; }
  .error-msg { color: #ef4444; margin: 0 0 1rem; font-size: 0.9rem; }

  .settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; max-width: 900px; }

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

  .range-row { display: flex; align-items: center; gap: 0.75rem; margin-top: 0.35rem; }
  .range-row input[type="range"] { flex: 1; accent-color: var(--color-accent); }
  .range-value { min-width: 2.5rem; text-align: right; font-size: 0.85rem; color: var(--color-text); }

  .toggle-label { display: flex !important; justify-content: space-between; align-items: center; }
  .dim { font-size: 0.8rem; color: var(--color-text-dim); margin: -0.5rem 0 0; }

  .actions { margin-top: 2rem; max-width: 900px; display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; }

  .toast { padding: 0.5rem 1rem; border-radius: 8px; font-size: 0.85rem; font-weight: 500; }
  .toast-success { background: #14532d; color: #86efac; border: 1px solid #22c55e; }
  .toast-error { background: #450a0a; color: #fca5a5; border: 1px solid #ef4444; }

  .btn { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.5rem 1.25rem;
    border-radius: 8px; border: none; font-size: 0.85rem; font-weight: 600; cursor: pointer; }
  .btn.primary { background: var(--color-accent); color: white; }
  .btn.primary:hover { filter: brightness(1.1); }
</style>
