<script lang="ts">
  import { api } from '$lib/api';
  import type { VoiceAsset, GenerateResponse, GenerationTask } from '$lib/api';
  import { Play, Upload, ChevronDown, Loader2, Check, X, Wand2 } from 'lucide-svelte';

  let text = $state('');
  let engineId = $state('indextts');
  let engineVersion = $state('v2');
  let voiceId = $state('');
  let language = $state('zh');

  // 情绪控制
  let emotionMode = $state('follow_reference');
  let emoAlpha = $state(0.6);
  let emotionValues = $state<Record<string, number>>({
    happy: 0, angry: 0, sad: 0, afraid: 0,
    disgusted: 0, melancholic: 0, surprised: 0, calm: 0,
  });
  let emotionText = $state('');

  const emotionLabels: Record<string, string> = {
    happy: '高兴', angry: '愤怒', sad: '悲伤', afraid: '恐惧',
    disgusted: '反感', melancholic: '低落', surprised: '惊讶', calm: '自然',
  };

  // 基础参数
  let speed = $state(1.0);
  let temperature = $state(0.8);
  let topP = $state(0.8);
  let topK = $state(30);
  let repetitionPenalty = $state(10.0);
  let seed = $state<string>('');

  // 高级参数
  let maxMelTokens = $state(600);
  let maxTextTokensPerSegment = $state(120);
  let intervalSilence = $state(200);
  let segmentOverlapMs = $state(50);

  // v2 专属
  let diffusionSteps = $state(25);
  let cfgRate = $state(0.7);

  let showAdvanced = $state(false);
  let generating = $state(false);
  let currentTask = $state<GenerationTask | null>(null);
  let voices = $state<VoiceAsset[]>([]);

  let isV2 = $derived(engineVersion === 'v2');

  $effect(() => {
    api.get<VoiceAsset[]>('/voices').then(d => voices = d).catch(() => {});
  });

  async function generate() {
    if (!text.trim()) return;
    generating = true;
    currentTask = null;
    try {
      const body: any = {
        text, engine_id: engineId, engine_version: engineVersion,
        voice_id: voiceId || undefined, language,
        emotion_mode: emotionMode,
        emo_alpha: emoAlpha,
        speed, temperature, top_p: topP, top_k: topK,
        repetition_penalty: repetitionPenalty,
        seed: seed ? parseInt(seed) : null,
        max_mel_tokens: maxMelTokens,
        max_text_tokens_per_segment: maxTextTokensPerSegment,
        interval_silence: intervalSilence,
        segment_overlap_ms: segmentOverlapMs,
        diffusion_steps: diffusionSteps,
        cfg_rate: cfgRate,
      };
      if (emotionMode === 'emotion_vector') {
        body.emotion_values = emotionValues;
      } else if (emotionMode === 'emotion_text') {
        body.emotion_text = emotionText;
      }
      const res = await api.post<GenerateResponse>('/generate', body);
      for (let i = 0; i < 120; i++) {
        await new Promise(r => setTimeout(r, 1000));
        const task = await api.get<GenerationTask>(`/tasks/${res.task_id}`);
        currentTask = task;
        if (task.status === 'success' || task.status === 'failed') break;
      }
    } catch (e) { console.error(e); }
    finally { generating = false; }
  }
</script>

<svelte:head><title>单句合成 - Voice Studio</title></svelte:head>

<section class="gen-page">
  <h1>单句合成</h1>

  <div class="workspace">
    <!-- 左：文本 + 结果 -->
    <div class="left-col">
      <textarea class="text-input" placeholder="输入要合成的文本..." bind:value={text} rows={8}></textarea>
      <div class="toolbar">
        <span class="char-count">{text.length} 字</span>
        {#if currentTask?.status === 'success' && currentTask.result_audio_id}
          <audio controls src="/api/history/{currentTask.result_id || currentTask.task_id}/audio" class="result-audio"></audio>
        {/if}
      </div>

      {#if currentTask}
        <div class="result-card" class:ok={currentTask.status === 'success'} class:fail={currentTask.status === 'failed'}>
          {#if currentTask.status === 'success'}
            <Check size={16} /> 完成 · {currentTask.generation_time_ms}ms · {currentTask.result_duration_ms ? (currentTask.result_duration_ms/1000).toFixed(1) + 's' : ''}
          {:else if currentTask.status === 'failed'}
            <X size={16} /> 失败 · {currentTask.error_message}
          {:else}
            <Loader2 size={16} class="spin" /> {currentTask.status}...
          {/if}
        </div>
      {/if}
    </div>

    <!-- 右：参数面板 -->
    <div class="param-panel">
      <!-- 引擎 -->
      <div class="param-group">
        <label>引擎版本</label>
        <div class="version-toggle">
          <button class="toggle-btn" class:active={engineVersion === 'v1'} onclick={() => engineVersion = 'v1'}>v1</button>
          <button class="toggle-btn" class:active={engineVersion === 'v2'} onclick={() => engineVersion = 'v2'}>v2 情绪</button>
        </div>
      </div>

      <div class="param-group">
        <label>声音</label>
        <select bind:value={voiceId}>
          <option value="">-- 选择声音 --</option>
          {#each voices as v}<option value={v.voice_id}>{v.name}</option>{/each}
        </select>
      </div>

      <div class="param-group">
        <label>语言</label>
        <select bind:value={language}>
          <option value="zh">中文</option><option value="en">英文</option><option value="ja">日文</option>
        </select>
      </div>

      <!-- 情绪控制（v2） -->
      {#if isV2}
        <div class="section-divider">情绪控制</div>
        <div class="param-group">
          <label>情绪模式</label>
          <select bind:value={emotionMode}>
            <option value="follow_reference">跟随参考音频</option>
            <option value="emotion_vector">自定义情绪向量</option>
            <option value="emotion_text">情绪文本描述</option>
          </select>
        </div>

        {#if emotionMode === 'emotion_vector'}
          <div class="emotion-grid">
            {#each Object.entries(emotionLabels) as [key, label]}
              <div class="emo-row">
                <span class="emo-label">{label}</span>
                <input type="range" min="-1" max="1" step="0.1"
                  value={emotionValues[key]}
                  oninput={(e) => emotionValues[key] = parseFloat(e.target.value)} />
                <span class="emo-val">{emotionValues[key]?.toFixed(1) ?? '0.0'}</span>
              </div>
            {/each}
          </div>
          <div class="param-group">
            <label>情绪强度 (emo_alpha) {emoAlpha.toFixed(1)}</label>
            <input type="range" min="0" max="1" step="0.1" bind:value={emoAlpha} />
          </div>
        {/if}

        {#if emotionMode === 'emotion_text'}
          <div class="param-group">
            <label>情绪描述</label>
            <input type="text" placeholder="例：温柔地说话" bind:value={emotionText} />
          </div>
          <div class="param-group">
            <label>情绪强度 (emo_alpha) {emoAlpha.toFixed(1)}</label>
            <input type="range" min="0" max="1" step="0.1" bind:value={emoAlpha} />
          </div>
        {/if}
      {/if}

      <!-- 基础参数 -->
      <div class="section-divider">基础参数</div>
      <div class="param-group">
        <label>语速 {speed.toFixed(2)}</label>
        <input type="range" min="0.5" max="2" step="0.05" bind:value={speed} />
      </div>
      <div class="param-group">
        <label>Temperature {temperature.toFixed(2)}</label>
        <input type="range" min="0.1" max="2" step="0.05" bind:value={temperature} />
      </div>
      <div class="param-group">
        <label>Top-P {topP.toFixed(2)}</label>
        <input type="range" min="0.1" max="1" step="0.05" bind:value={topP} />
      </div>
      <div class="param-group">
        <label>Top-K {topK}</label>
        <input type="range" min="1" max="100" step="1" bind:value={topK} />
      </div>
      <div class="param-group">
        <label>重复惩罚 {repetitionPenalty.toFixed(1)}</label>
        <input type="range" min="1" max="20" step="0.5" bind:value={repetitionPenalty} />
      </div>
      <div class="param-group">
        <label>Seed（留空随机）</label>
        <input type="text" bind:value={seed} placeholder="随机" />
      </div>

      <!-- v2 专属 -->
      {#if isV2}
        <div class="section-divider">v2 专属</div>
        <div class="param-group">
          <label>Diffusion Steps {diffusionSteps}</label>
          <input type="range" min="5" max="50" step="1" bind:value={diffusionSteps} />
        </div>
        <div class="param-group">
          <label>CFG Rate {cfgRate.toFixed(2)}</label>
          <input type="range" min="0" max="1" step="0.05" bind:value={cfgRate} />
        </div>
      {/if}

      <!-- 高级参数 -->
      <button class="adv-toggle" onclick={() => showAdvanced = !showAdvanced}>
        高级参数 {#if showAdvanced}▲{:else}▼{/if}
      </button>

      {#if showAdvanced}
        <div class="param-group">
          <label>Max Mel Tokens {maxMelTokens}</label>
          <input type="range" min="100" max="2000" step="50" bind:value={maxMelTokens} />
        </div>
        <div class="param-group">
          <label>分段 Token 数 {maxTextTokensPerSegment}</label>
          <input type="range" min="20" max="200" step="10" bind:value={maxTextTokensPerSegment} />
        </div>
        <div class="param-group">
          <label>段间静默 {intervalSilence}ms</label>
          <input type="range" min="0" max="1000" step="50" bind:value={intervalSilence} />
        </div>
        <div class="param-group">
          <label>段重叠 {segmentOverlapMs}ms</label>
          <input type="range" min="0" max="200" step="10" bind:value={segmentOverlapMs} />
        </div>
      {/if}

      <button class="gen-btn" onclick={generate} disabled={generating || !text.trim()}>
        {#if generating}<Loader2 size={16} class="spin" /> 生成中...{:else}<Wand2 size={16} /> 生成语音{/if}
      </button>
    </div>
  </div>
</section>

<style>
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 1.5rem; }
  .workspace { display: grid; grid-template-columns: 1fr 360px; gap: 1.5rem; }
  .left-col { display: flex; flex-direction: column; gap: 0.75rem; }
  .text-input {
    width: 100%; padding: 1rem; background: var(--color-surface);
    border: 1px solid var(--color-border); border-radius: 12px;
    color: var(--color-text); font-size: 1rem; resize: vertical;
    outline: none; line-height: 1.6; min-height: 200px;
  }
  .text-input:focus { border-color: var(--color-accent); }
  .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
  .char-count { font-size: 0.75rem; color: var(--color-text-dim); }
  .result-audio { height: 40px; flex: 1; border-radius: 6px; }
  .result-card {
    padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.85rem;
    display: flex; align-items: center; gap: 0.5rem;
    background: var(--color-surface-2);
  }
  .result-card.ok { color: var(--color-success); }
  .result-card.fail { color: var(--color-danger); }
  .result-audio { width: 100%; }

  /* 参数面板 */
  .param-panel {
    background: var(--color-surface); border: 1px solid var(--color-border);
    border-radius: 12px; padding: 1.25rem; display: flex; flex-direction: column; gap: 0.6rem;
    height: fit-content; max-height: calc(100vh - var(--player-h) - 6rem); overflow-y: auto;
  }
  .param-group { display: flex; flex-direction: column; gap: 0.3rem; }
  .param-group label { font-size: 0.78rem; color: var(--color-text-dim); }
  .param-group select, .param-group input[type="text"] {
    padding: 0.4rem 0.6rem; background: var(--color-surface-2);
    border: 1px solid var(--color-border); border-radius: 6px;
    color: var(--color-text); font-size: 0.85rem; outline: none; width: 100%;
  }
  .param-group input[type="range"] { width: 100%; accent-color: var(--color-accent); }
  .param-group select:focus, .param-group input:focus { border-color: var(--color-accent); }

  .version-toggle { display: flex; gap: 0; border-radius: 6px; overflow: hidden; border: 1px solid var(--color-border); }
  .toggle-btn {
    flex: 1; padding: 0.4rem; border: none; background: var(--color-surface-2);
    color: var(--color-text-dim); font-size: 0.8rem; cursor: pointer;
  }
  .toggle-btn.active { background: var(--color-accent); color: white; }

  .section-divider {
    font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--color-text-dim); padding: 0.5rem 0 0.1rem;
    border-top: 1px solid var(--color-border); margin-top: 0.25rem;
  }

  /* 情绪向量 */
  .emotion-grid { display: flex; flex-direction: column; gap: 0.35rem; }
  .emo-row { display: grid; grid-template-columns: 3rem 1fr 2.5rem; align-items: center; gap: 0.4rem; }
  .emo-label { font-size: 0.78rem; color: var(--color-text-dim); }
  .emo-val { font-size: 0.7rem; color: var(--color-text-dim); text-align: right; font-variant-numeric: tabular-nums; }
  .emo-row input[type="range"] { accent-color: var(--color-accent); }

  .adv-toggle {
    background: none; border: none; color: var(--color-text-dim); font-size: 0.78rem;
    cursor: pointer; padding: 0.3rem 0; text-align: left;
  }

  .gen-btn {
    display: flex; align-items: center; justify-content: center; gap: 0.5rem;
    padding: 0.65rem 1rem; border-radius: 8px; border: none; font-size: 0.9rem;
    font-weight: 600; cursor: pointer; background: var(--color-accent); color: white; margin-top: 0.5rem;
  }
  .gen-btn:hover { opacity: 0.9; }
  .gen-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .spin { animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
