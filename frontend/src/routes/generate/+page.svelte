<script lang="ts">
  import { listVoices, uploadVoice, generateAudio, getTask, subscribeTaskUpdates, listEngines } from '$lib/api';
  import type { VoiceAsset, GenerateResponse, GenerationTask, Subscription, WsConnectionStatus, EngineDetail } from '$lib/api';
  import { Play, Upload, ChevronDown, Loader2, Check, X, Wand2, Pause, Music, Smile, Frown, Hash, Scissors, RotateCcw, Star, Download, Send } from 'lucide-svelte';

  // 文本
  let text = $state('');
  let engineId = $state('indextts');
  let voiceId = $state('');

  // 引擎列表
  let engines = $state<EngineDetail[]>([]);
  let selectedEngine = $derived(engines.find(e => e.manifest.engine_id === engineId) ?? null);
  let engineCapabilities = $derived(selectedEngine?.manifest.capabilities ?? []);
  let showEmotionPanel = $derived(engineCapabilities.includes('emotion_control'));
  let showVoiceDesign = $derived(engineCapabilities.includes('voice_design'));
  let engineVersion = $derived(engineId);
  let language = $state('zh');

  // 情绪
  let emotionMode = $state('follow_reference');
  let emoAlpha = $state(0.6);
  let emotionValues = $state<Record<string, number>>({happy:0,angry:0,sad:0,afraid:0,disgusted:0,melancholic:0,surprised:0,calm:0});
  let emotionText = $state('');
  const emotionLabels: Record<string, string> = {happy:'高兴',angry:'愤怒',sad:'悲伤',afraid:'恐惧',disgusted:'反感',melancholic:'低落',surprised:'惊讶',calm:'自然'};

  // OmniVoice
  let voiceMode = $state('auto');

  // 基础参数
  let speed = $state(1.0);
  let temperature = $state(0.8);
  let topP = $state(0.8);
  let topK = $state(30);
  let repetitionPenalty = $state(10.0);
  let seedStr = $state('');
  // 高级
  let maxMelTokens = $state(600);
  let maxTextTokensPerSegment = $state(120);
  let intervalSilence = $state(200);
  let segmentOverlapMs = $state(50);
  // v2
  let diffusionSteps = $state(25);
  let cfgRate = $state(0.7);

  let showAdvanced = $state(false);
  let generating = $state(false);
  let results = $state<GenerationTask[]>([]);
  let voices = $state<VoiceAsset[]>([]);
  let wsStatus = $state<WsConnectionStatus | 'idle'>('idle');
  let wsFallbackMessage = $state('');
  let wsSub: Subscription | null = null;
  let directAudioFile: File | null = $state(null);
  let directAudioId = $state('');

  // 从 URL 参数获取预选声音
  $effect(() => {
    const params = new URLSearchParams(window.location.search);
    const v = params.get('voice');
    if (v) voiceId = v;
  });
  $effect(() => { listVoices().then(d => voices = d).catch(() => {}); });

  // 加载引擎列表
  $effect(() => {
    listEngines().then(d => {
      engines = d;
      if (d.length > 0 && !d.find(e => e.manifest.engine_id === engineId)) {
        engineId = d[0].manifest.engine_id;
      }
    }).catch(() => {});
  });

  // 文本增强工具
  function insertTag(tag: string) { text += tag; }
  function insertPause() { text += '，'; }
  function insertPinyin() { text += ' pinyin()'; }
  function insertLaughter() { text += ' [laughter]'; }
  function insertSigh() { text += ' [sigh]'; }

  async function generate() {
    if (!text.trim()) return;
    generating = true;
    wsStatus = 'connecting';
    wsFallbackMessage = '';
    // Close any existing subscription
    wsSub?.close();
    wsSub = null;
    try {
      // voice_id is passed directly; backend resolves the file path via voice store
      // (reference_audio_path is NOT set here — backend's _find_reference_audio handles it)

      const body: any = {
        text, engine_id: engineId, engine_version: engineVersion,
        voice_id: voiceId || undefined,
        language, emotion_mode: emotionMode, emo_alpha: emoAlpha,
        speed, temperature, top_p: topP, top_k: topK,
        repetition_penalty: repetitionPenalty,
        seed: seedStr ? parseInt(seedStr) : null,
        max_mel_tokens: maxMelTokens, max_text_tokens_per_segment: maxTextTokensPerSegment,
        interval_silence: intervalSilence, segment_overlap_ms: segmentOverlapMs,
        diffusion_steps: diffusionSteps, cfg_rate: cfgRate,
      };
      if (emotionMode === 'emotion_vector') body.emotion_values = emotionValues;
      else if (emotionMode === 'emotion_text') body.emotion_text = emotionText;
      if (showVoiceDesign) body.voice_mode = voiceMode;

      const res = await generateAudio(body as any);

      // Subscribe to task progress over WebSocket
      wsSub = subscribeTaskUpdates(
        res.task_id,
        (event): void => {
          if (event.type === 'done' || event.type === 'error') {
            results = [event.data, ...results];
            generating = false;
            wsSub?.close();
            wsSub = null;
            wsStatus = 'idle';
          }
        },
        {
          onStatusChange: (status): void => {
            wsStatus = status;
          },
          onFallback: (): void => {
            // WS failed after 3 retries — fall back to polling
            wsSub = null;
            startPolling(res.task_id);
          },
        }
      );
    } catch (e) {
      console.error(e);
      generating = false;
      wsStatus = 'idle';
    }
  }

  /** Fallback polling when WebSocket is unavailable. */
  async function startPolling(taskId: string) {
    wsFallbackMessage = 'WebSocket 不可用，切换轮询';
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const task = await getTask(taskId);
        if (task.status === 'success' || task.status === 'failed') {
          results = [task, ...results];
          generating = false;
          wsFallbackMessage = '';
          return;
        }
      } catch {
        // Poll failure — continue retrying
      }
    }
    generating = false;
    wsFallbackMessage = '';
  }

  // Clean up WebSocket on unmount
  $effect(() => {
    return () => {
      wsSub?.close();
      wsSub = null;
    };
  });

  async function toggleFav(idx: number) {
    results[idx] = {...results[idx], favorite: !results[idx].favorite};
    results = results; // trigger reactivity
  }

  function reuseParams(task: GenerationTask) {
    text = task.input_text;
    engineId = task.engine_id;
    voiceId = task.voice_id || '';
  }

  // ── WS status label ──
  const wsStatusLabel = $derived.by((): string => {
    switch (wsStatus) {
      case 'connecting': return '正在连接…';
      case 'connected': return '已连接';
      case 'reconnecting': return '重连中…';
      case 'fallback': return '已切换至轮询';
      default: return '';
    }
  });
</script>

<svelte:head><title>单句合成 - Voice Studio</title></svelte:head>

<section class="gen-page">
  <h1>单句合成</h1>

  <div class="workspace">
    <!-- 左：文本 + 工具条 + 结果 -->
    <div class="left-col">
      <!-- 文本增强工具条 -->
      <div class="text-toolbar">
        <button class="tool-btn" onclick={insertPause} title="插入停顿"><Pause size={14} /> 停顿</button>
        <button class="tool-btn" onclick={insertPinyin} title="插入拼音"><Music size={14} /> 拼音</button>
        <button class="tool-btn" onclick={insertLaughter} title="插入笑声"><Smile size={14} /> 笑声</button>
        <button class="tool-btn" onclick={insertSigh} title="插入叹气"><Frown size={14} /> 叹气</button>
        <button class="tool-btn" onclick={() => text = text.replace(/\d+/g, m => `[num:${m}]`)} title="数字规范化"><Hash size={14} /> 数字</button>
        <button class="tool-btn" onclick={() => { text = text.replace(/([。！？；\n])/g, '$1\n').replace(/\n+/g, '\n'); }} title="分句预览"><Scissors size={14} /> 分句</button>
      </div>

      <textarea class="text-input" placeholder="输入要合成的文本..." bind:value={text} rows={8}></textarea>
      <div class="text-footer">
        <span class="char-count">{text.length} 字</span>
      </div>

      <!-- 结果列表 -->
      {#if results.length > 0}
        <div class="results-section">
          <h3>生成结果（{results.length}）</h3>
          {#each results as r, i}
            <div class="result-card" class:ok={r.status === 'success'} class:fail={r.status === 'failed'}>
              <div class="result-header">
                <div class="result-info">
                  {#if r.status === 'success'}<Check size={14} class="ico-ok" />
                    {:else if r.status === 'failed'}<X size={14} class="ico-fail" />
                    {:else}<Loader2 size={14} class="spin" />
                  {/if}
                  <span class="result-text">{r.input_text.slice(0, 40)}{r.input_text.length > 40 ? '...' : ''}</span>
                </div>
                <div class="result-meta">
                  {r.engine_id} · {r.generation_time_ms ? (r.generation_time_ms/1000).toFixed(1)+'s' : ''} · {r.result_duration_ms ? (r.result_duration_ms/1000).toFixed(1)+'s' : ''}
                </div>
                <div class="result-actions">
                  <button class="icon-btn" class:active={r.favorite} onclick={() => toggleFav(i)}><Star size={13} /></button>
                  <button class="icon-btn" onclick={() => reuseParams(r)} title="复用参数"><RotateCcw size={13} /></button>
                </div>
              </div>
              {#if r.status === 'success' && r.result_audio_id}
                <audio controls src="/api/history/{r.result_audio_id}/audio" class="result-audio"></audio>
              {/if}
              {#if r.status === 'failed' && r.error_message}
                <div class="result-error">{r.error_message}</div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- 右：参数面板 -->
    <div class="param-panel">
      <div class="param-group">
        <label>引擎</label>
        <select bind:value={engineId}>
          {#each engines as eng}
            <option value={eng.manifest.engine_id}>{eng.manifest.display_name}</option>
          {/each}
        </select>
      </div>
      <div class="param-group">
        <label>声音</label>
        <select bind:value={voiceId}>
          <option value="">-- 选择声音 --</option>
          {#each voices as v}
            <option value={v.voice_id}>{v.name}{v.reference_audio_ids?.length ? '' : ' (无参考音频)'}</option>
          {/each}
        </select>
      </div>
      <div class="param-group">
        <label>直接上传参考音频（可选）</label>
        <input type="file" accept="audio/*" onchange={(e) => directAudioFile = (e.target as HTMLInputElement).files?.[0] || null} />
        {#if directAudioFile}<span class="dim">{directAudioFile.name}</span>{/if}
      </div>
      <div class="param-group">
        <label>语言</label>
        <select bind:value={language}><option value="zh">中文</option><option value="en">英文</option></select>
      </div>

      {#if showEmotionPanel}
        <div class="divider">情绪控制</div>
        <div class="param-group"><label>情绪模式</label>
          <select bind:value={emotionMode}>
            <option value="follow_reference">跟随参考音频</option>
            <option value="emotion_vector">自定义情绪向量</option>
            <option value="emotion_text">情绪文本描述</option>
          </select>
        </div>
        {#if emotionMode === 'emotion_vector'}
          <div class="emo-grid">
            {#each Object.entries(emotionLabels) as [key, label]}
              <div class="emo-row">
                <span class="emo-l">{label}</span>
                <input type="range" min="-1" max="1" step="0.1" value={emotionValues[key]}
                  oninput={(e) => emotionValues[key] = parseFloat((e.target as HTMLInputElement).value)} />
                <span class="emo-v">{emotionValues[key]?.toFixed(1) ?? '0.0'}</span>
              </div>
            {/each}
          </div>
          <div class="param-group"><label>情绪强度 {emoAlpha.toFixed(1)}</label><input type="range" min="0" max="1" step="0.1" bind:value={emoAlpha} /></div>
        {/if}
        {#if emotionMode === 'emotion_text'}
          <div class="param-group"><label>情绪描述</label><input type="text" bind:value={emotionText} placeholder="温柔地说话" /></div>
          <div class="param-group"><label>强度 {emoAlpha.toFixed(1)}</label><input type="range" min="0" max="1" step="0.1" bind:value={emoAlpha} /></div>
        {/if}
      {/if}

      {#if showVoiceDesign}
        <div class="divider">语音模式</div>
        <div class="param-group"><label>语音模式</label>
          <select bind:value={voiceMode}>
            <option value="auto">自动</option>
            <option value="clone">克隆</option>
            <option value="design">设计</option>
          </select>
        </div>
      {/if}

      <div class="divider">基础参数</div>
      <div class="param-group"><label>语速 {speed.toFixed(2)}</label><input type="range" min="0.5" max="2" step="0.05" bind:value={speed} /></div>
      <div class="param-group"><label>Temperature {temperature.toFixed(2)}</label><input type="range" min="0.1" max="2" step="0.05" bind:value={temperature} /></div>
      <div class="param-group"><label>Top-P {topP.toFixed(2)}</label><input type="range" min="0.1" max="1" step="0.05" bind:value={topP} /></div>
      <div class="param-group"><label>Top-K {topK}</label><input type="range" min="1" max="100" step="1" bind:value={topK} /></div>
      <div class="param-group"><label>重复惩罚 {repetitionPenalty.toFixed(1)}</label><input type="range" min="1" max="20" step="0.5" bind:value={repetitionPenalty} /></div>
      <div class="param-group"><label>Seed</label><input type="text" bind:value={seedStr} placeholder="随机" /></div>

      {#if engineId === 'indextts'}
        <div class="divider">v2 专属</div>
        <div class="param-group"><label>Diffusion Steps {diffusionSteps}</label><input type="range" min="5" max="50" step="1" bind:value={diffusionSteps} /></div>
        <div class="param-group"><label>CFG Rate {cfgRate.toFixed(2)}</label><input type="range" min="0" max="1" step="0.05" bind:value={cfgRate} /></div>
      {/if}

      <button class="adv-toggle" onclick={() => showAdvanced = !showAdvanced}>高级参数 {showAdvanced ? '▲' : '▼'}</button>
      {#if showAdvanced}
        <div class="param-group"><label>Max Mel Tokens {maxMelTokens}</label><input type="range" min="100" max="2000" step="50" bind:value={maxMelTokens} /></div>
        <div class="param-group"><label>分段 Token {maxTextTokensPerSegment}</label><input type="range" min="20" max="200" step="10" bind:value={maxTextTokensPerSegment} /></div>
        <div class="param-group"><label>段间静默 {intervalSilence}ms</label><input type="range" min="0" max="1000" step="50" bind:value={intervalSilence} /></div>
        <div class="param-group"><label>段重叠 {segmentOverlapMs}ms</label><input type="range" min="0" max="200" step="10" bind:value={segmentOverlapMs} /></div>
      {/if}

      <button class="gen-btn" onclick={generate} disabled={generating || !text.trim()}>
        {#if generating}<Loader2 size={16} class="spin" /> 生成中...{:else}<Wand2 size={16} /> 生成语音{/if}
      </button>
      {#if wsStatus !== 'idle' && wsStatusLabel}
        <div class="ws-status" class:ws-fallback={wsStatus === 'fallback'}>{wsStatusLabel}</div>
      {/if}
      {#if wsFallbackMessage}
        <div class="ws-fallback-msg">{wsFallbackMessage}</div>
      {/if}
    </div>
  </div>
</section>

<style>
  h1{font-size:1.5rem;font-weight:700;margin:0 0 1.5rem}
  .workspace{display:grid;grid-template-columns:1fr 360px;gap:1.5rem}
  .left-col{display:flex;flex-direction:column;gap:0.75rem}
  .text-toolbar{display:flex;gap:0.35rem;flex-wrap:wrap}
  .tool-btn{display:inline-flex;align-items:center;gap:0.3rem;padding:0.3rem 0.6rem;background:var(--color-surface);border:1px solid var(--color-border);border-radius:6px;color:var(--color-text-dim);font-size:0.75rem;cursor:pointer}
  .tool-btn:hover{border-color:var(--color-accent);color:var(--text)}
  .text-input{width:100%;padding:1rem;background:var(--color-surface);border:1px solid var(--color-border);border-radius:12px;color:var(--color-text);font-size:1rem;resize:vertical;outline:none;line-height:1.6;min-height:180px}
  .text-input:focus{border-color:var(--color-accent)}
  .text-footer{display:flex;justify-content:flex-end}
  .char-count{font-size:0.75rem;color:var(--color-text-dim)}

  .results-section h3{font-size:0.85rem;font-weight:600;color:var(--color-text-dim);margin:0 0 0.75rem}
  .result-card{padding:0.75rem;background:var(--color-surface);border:1px solid var(--color-border);border-radius:10px;margin-bottom:0.5rem}
  .result-header{display:flex;align-items:center;gap:0.5rem}
  .result-info{display:flex;align-items:center;gap:0.4rem;flex:1;min-width:0}
  .result-text{font-size:0.85rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .result-meta{font-size:0.7rem;color:var(--color-text-dim);white-space:nowrap}
  .result-actions{display:flex;gap:0.25rem}
  .icon-btn{background:none;border:none;color:var(--color-text-dim);cursor:pointer;padding:0.2rem;display:flex}
  .icon-btn:hover,.icon-btn.active{color:var(--color-warning)}
  .ico-ok{color:var(--color-success)}.ico-fail{color:var(--color-danger)}
  .result-audio{width:100%;height:36px;margin-top:0.5rem;border-radius:6px}
  .result-error{font-size:0.78rem;color:var(--color-danger);margin-top:0.35rem}

  .param-panel{background:var(--color-surface);border:1px solid var(--color-border);border-radius:12px;padding:1.25rem;display:flex;flex-direction:column;gap:0.55rem;height:fit-content;max-height:calc(100vh - var(--player-h) - 6rem);overflow-y:auto}
  .param-group{display:flex;flex-direction:column;gap:0.25rem}
  .param-group label{font-size:0.76rem;color:var(--color-text-dim)}
  .param-group select,.param-group input[type="text"]{padding:0.35rem 0.6rem;background:var(--color-surface-2);border:1px solid var(--color-border);border-radius:6px;color:var(--color-text);font-size:0.82rem;outline:none;width:100%}
  .param-group input[type="range"]{width:100%;accent-color:var(--color-accent)}
  .param-group input[type="file"]{font-size:0.78rem;color:var(--color-text-dim)}
  .param-group select:focus,.param-group input:focus{border-color:var(--color-accent)}
  .dim{font-size:0.75rem;color:var(--color-text-dim)}
  .version-toggle{display:flex;border-radius:6px;overflow:hidden;border:1px solid var(--color-border)}
  .toggle-btn{flex:1;padding:0.35rem;border:none;background:var(--color-surface-2);color:var(--color-text-dim);font-size:0.78rem;cursor:pointer}
  .toggle-btn.active{background:var(--color-accent);color:white}
  .divider{font-size:0.68rem;text-transform:uppercase;letter-spacing:0.06em;color:var(--color-text-dim);padding:0.4rem 0 0.1rem;border-top:1px solid var(--color-border);margin-top:0.2rem}
  .emo-grid{display:flex;flex-direction:column;gap:0.3rem}
  .emo-row{display:grid;grid-template-columns:2.8rem 1fr 2.2rem;align-items:center;gap:0.35rem}
  .emo-l{font-size:0.76rem;color:var(--color-text-dim)}.emo-v{font-size:0.68rem;color:var(--color-text-dim);text-align:right;font-variant-numeric:tabular-nums}
  .adv-toggle{background:none;border:none;color:var(--color-text-dim);font-size:0.76rem;cursor:pointer;padding:0.2rem 0;text-align:left}
  .gen-btn{display:flex;align-items:center;justify-content:center;gap:0.5rem;padding:0.6rem;border-radius:8px;border:none;font-size:0.88rem;font-weight:600;cursor:pointer;background:var(--color-accent);color:white;margin-top:0.4rem}
  .gen-btn:hover{opacity:0.9}.gen-btn:disabled{opacity:0.5;cursor:not-allowed}
  .spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}

  /* WS connection status */
  .ws-status{font-size:0.7rem;text-align:center;padding:0.2rem 0.4rem;border-radius:4px;color:var(--color-text-dim)}
  .ws-status.ws-fallback{color:var(--color-warning);font-weight:600}
  .ws-fallback-msg{font-size:0.72rem;text-align:center;padding:0.25rem 0.5rem;background:var(--color-warning);color:white;border-radius:6px;margin-top:0.25rem}
</style>
