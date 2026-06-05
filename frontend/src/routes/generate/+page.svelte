<script lang="ts">
  import { api } from '$lib/api';
  import type { VoiceAsset, GenerateResponse, GenerationTask } from '$lib/api';
  import { Play, Wand2, ChevronDown, Loader2, Check, X } from 'lucide-svelte';

  let text = $state('');
  let engineId = $state('indextts');
  let voiceId = $state('');
  let language = $state('zh');
  let speed = $state(1.0);
  let temperature = $state(0.8);
  let topP = $state(0.8);
  let showAdvanced = $state(false);
  let generating = $state(false);
  let currentTask = $state<GenerationTask | null>(null);
  let voices = $state<VoiceAsset[]>([]);

  $effect(() => {
    api.get<VoiceAsset[]>('/voices').then(d => voices = d).catch(() => {});
  });

  async function generate() {
    if (!text.trim()) return;
    generating = true;
    try {
      const res = await api.post<GenerateResponse>('/generate', {
        text, engine_id: engineId, voice_id: voiceId || undefined,
        language, speed, temperature, top_p: topP,
      });
      // 轮询任务状态
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 1000));
        const task = await api.get<GenerationTask>(`/tasks/${res.task_id}`);
        currentTask = task;
        if (task.status === 'success' || task.status === 'failed') break;
      }
    } catch (e) {
      console.error(e);
    } finally {
      generating = false;
    }
  }
</script>

<svelte:head><title>单句合成 - Voice Studio</title></svelte:head>

<section class="generate-page">
  <h1>单句合成</h1>

  <div class="workspace">
    <!-- 左：文本输入 -->
    <div class="input-panel">
      <textarea
        class="text-input"
        placeholder="输入要合成的文本..."
        bind:value={text}
        rows={6}
      ></textarea>
      <div class="toolbar">
        <span class="char-count">{text.length} 字</span>
      </div>
    </div>

    <!-- 右：参数面板 -->
    <div class="param-panel">
      <div class="param-group">
        <label>引擎</label>
        <select bind:value={engineId}>
          <option value="indextts">IndexTTS</option>
          <option value="omnivoice">OmniVoice</option>
        </select>
      </div>

      <div class="param-group">
        <label>声音</label>
        <select bind:value={voiceId}>
          <option value="">默认</option>
          {#each voices as v}
            <option value={v.voice_id}>{v.name}</option>
          {/each}
        </select>
      </div>

      <div class="param-group">
        <label>语言</label>
        <select bind:value={language}>
          <option value="zh">中文</option>
          <option value="en">英文</option>
          <option value="ja">日文</option>
        </select>
      </div>

      <div class="param-group">
        <label>语速 {speed.toFixed(2)}</label>
        <input type="range" min="0.5" max="2" step="0.05" bind:value={speed} />
      </div>

      <button class="adv-toggle" onclick={() => showAdvanced = !showAdvanced}>
        高级参数 <span class:rotated={showAdvanced}><ChevronDown size={14} /></span>
      </button>

      {#if showAdvanced}
        <div class="param-group">
          <label>Temperature {temperature.toFixed(2)}</label>
          <input type="range" min="0.1" max="2" step="0.05" bind:value={temperature} />
        </div>
        <div class="param-group">
          <label>Top-P {topP.toFixed(2)}</label>
          <input type="range" min="0.1" max="1" step="0.05" bind:value={topP} />
        </div>
      {/if}

      <button class="btn generate-btn" onclick={generate} disabled={generating || !text.trim()}>
        {#if generating}
          <Loader2 size={16} class="spin" /> 生成中...
        {:else}
          <Wand2 size={16} /> 生成语音
        {/if}
      </button>

      {#if currentTask}
        <div class="result-card" class:success={currentTask.status === 'success'} class:failed={currentTask.status === 'failed'}>
          <div class="result-status">
            {#if currentTask.status === 'success'}
              <Check size={16} /> 生成完成 · {currentTask.generation_time_ms}ms
            {:else if currentTask.status === 'failed'}
              <X size={16} /> 生成失败 · {currentTask.error_message}
            {:else}
              <Loader2 size={16} class="spin" /> {currentTask.status}...
            {/if}
          </div>
          {#if currentTask.status === 'success' && currentTask.result_audio_id}
            <audio controls src="/api/history/{currentTask.result_audio_id}/audio" class="audio-player"></audio>
          {/if}
        </div>
      {/if}
    </div>
  </div>
</section>

<style>
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 1.5rem; }
  .workspace { display: grid; grid-template-columns: 1fr 360px; gap: 1.5rem; }
  .text-input {
    width: 100%; padding: 1rem; background: var(--color-surface);
    border: 1px solid var(--color-border); border-radius: 12px;
    color: var(--color-text); font-size: 1rem; resize: vertical;
    outline: none; line-height: 1.6; min-height: 200px;
  }
  .text-input:focus { border-color: var(--color-accent); }
  .toolbar { display: flex; justify-content: flex-end; margin-top: 0.5rem; }
  .char-count { font-size: 0.75rem; color: var(--color-text-dim); }
  .param-panel {
    background: var(--color-surface); border: 1px solid var(--color-border);
    border-radius: 12px; padding: 1.25rem; display: flex; flex-direction: column; gap: 0.75rem;
    height: fit-content;
  }
  .param-group { display: flex; flex-direction: column; gap: 0.35rem; }
  .param-group label { font-size: 0.8rem; color: var(--color-text-dim); }
  .param-group select, .param-group input[type="range"] {
    width: 100%; padding: 0.4rem 0.6rem; background: var(--color-surface-2);
    border: 1px solid var(--color-border); border-radius: 6px;
    color: var(--color-text); font-size: 0.85rem; outline: none;
  }
  .param-group select:focus { border-color: var(--color-accent); }
  .adv-toggle {
    display: flex; align-items: center; gap: 0.35rem; background: none;
    border: none; color: var(--color-text-dim); font-size: 0.8rem;
    cursor: pointer; padding: 0.25rem 0;
  }
  .rotated { transform: rotate(180deg); }
  .btn { display: flex; align-items: center; justify-content: center; gap: 0.5rem;
    padding: 0.65rem 1rem; border-radius: 8px; border: none; font-size: 0.9rem;
    font-weight: 600; cursor: pointer; transition: all 0.15s; }
  .generate-btn { background: var(--color-accent); color: white; margin-top: 0.5rem; }
  .generate-btn:hover { opacity: 0.9; }
  .generate-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .spin { animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .result-card { padding: 0.75rem; background: var(--color-surface-2); border-radius: 8px; }
  .result-status { display: flex; align-items: center; gap: 0.5rem; font-size: 0.8rem; margin-bottom: 0.5rem; }
  .result-card.success .result-status { color: var(--color-success); }
  .result-card.failed .result-status { color: var(--color-danger); }
  .audio-player { width: 100%; height: 36px; border-radius: 6px; }
</style>
