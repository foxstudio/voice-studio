<script lang="ts">
	import { Api } from '$lib/api';
		import type { AppSettings, EngineDetail, GenerationTask, GenerateRequest, PresetTemplate, VoiceAsset } from '$lib/api/types';
	import { engineStatusLabel, taskStatusLabel } from '$lib/labels';
	import { Download, Play, RotateCcw, Send, Wand2 } from 'lucide-svelte';

	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let presets = $state<PresetTemplate[]>([]);
	let settings = $state<AppSettings | null>(null);
	let text = $state('');
	let engineId = $state('indextts-v2');
	let voiceId = $state('');
	let language = $state('zh');
	let emotion = $state('calm');
	let voiceDesign = $state('女，青年，中音调');
	let voiceDesignPrompt = $state('中年男性，声线沉稳偏正式，吐字工整，语速适中。');
	let styleInstruction = $state('');
	let mimoVoice = $state('mimo_default');
	let emoAlpha = $state(0.6);
	let speed = $state(1.0);
	let temperature = $state(0.8);
	let topP = $state(0.8);
	let topK = $state(30);
	let maxTextTokensPerSegment = $state(120);
	let intervalSilence = $state(200);
	let diffusionSteps = $state(25);
	let cfgRate = $state(0.7);
	let outputFormat = $state<'wav' | 'mp3' | 'flac'>('wav');
	let tasks = $state<GenerationTask[]>([]);
	let busy = $state(false);
	let error = $state('');
	let initialized = $state(false);
	let lastEngineId = $state('indextts-v2');

	const selected = $derived(engines.find((e) => e.manifest.engine_id === engineId));
	const ttsEngines = $derived(engines.filter((e) => !e.manifest.capabilities.includes('speech_recognition')));
	const selectedVoice = $derived(voices.find((v) => v.voice_id === voiceId) ?? null);
	const supportsEmotion = $derived(Boolean(selected?.manifest.capabilities.includes('emotion_control')));
	const isIndexTTS = $derived(engineId === 'indextts-v2');
	const isOmniVoice = $derived(engineId === 'omnivoice');
	const isMimoPreset = $derived(engineId === 'mimo-v2.5-tts-preset');
	const isMimoDesign = $derived(engineId === 'mimo-v2.5-tts-voicedesign');
	const isMimoClone = $derived(engineId === 'mimo-v2.5-tts-voiceclone');
	const isMimo = $derived(engineId.startsWith('mimo-v2.5'));
	const mimoVoiceOptions = $derived(selected?.manifest.parameter_schema.find((p) => p.key === 'mimo_voice')?.options ?? []);
	const voiceChoices = $derived(
		isMimoClone
			? voices.filter((voice) => voice.engine_bindings?.some((binding) => binding.engine_id === 'mimo-v2.5-tts-voiceclone' && binding.available))
			: voices
	);

	$effect(() => {
		Promise.all([Api.engines(), Api.voices(), Api.tasks(), Api.presets(), Api.settings()]).then(([e, v, t, p, s]) => {
			engines = e;
			voices = v;
			tasks = t.slice(0, 8);
			presets = p;
			settings = s;
			const params = new URLSearchParams(location.search);
			const vId = params.get('voice');
			if (!initialized) {
				const defaultEngine = e.find((engine) => engine.manifest.engine_id === s.default_engine_id && !engine.manifest.capabilities.includes('speech_recognition'));
				engineId = defaultEngine?.manifest.engine_id || engineId;
				voiceId = vId || s.default_voice_id || '';
				language = s.default_language || language;
				initialized = true;
			} else if (vId) {
				voiceId = vId;
			}
		});
	});

	$effect(() => {
		if (engineId !== lastEngineId) {
			if (engineId.startsWith('mimo-v2.5')) {
				temperature = 0.6;
				topP = 0.95;
			} else {
				temperature = 0.8;
				topP = 0.8;
			}
			if (!isMimoClone && !isIndexTTS && !isOmniVoice) voiceId = '';
			lastEngineId = engineId;
		}
	});

	function requestBody(): GenerateRequest {
		return {
			text,
			engine_id: engineId,
			voice_id: voiceId || null,
			ref_text: selectedVoice?.reference_text || null,
			language,
				emotion_mode: supportsEmotion ? 'emotion_vector' : 'follow_reference',
				emotion: supportsEmotion ? emotion : null,
				emotion_text: isOmniVoice && !voiceId ? voiceDesign : null,
				style_instruction: isMimo ? styleInstruction || null : null,
				voice_design_prompt: isMimoDesign ? voiceDesignPrompt : null,
				mimo_voice: isMimoPreset ? mimoVoice : null,
				emo_alpha: emoAlpha,
				speed,
			temperature,
			top_p: topP,
			top_k: topK,
			repetition_penalty: 10,
			max_mel_tokens: 1500,
			max_text_tokens_per_segment: maxTextTokensPerSegment,
			interval_silence: intervalSilence,
			segment_overlap_ms: 50,
			diffusion_steps: diffusionSteps,
			cfg_rate: cfgRate,
			output_format: outputFormat
		};
	}

	function applyPreset(preset: PresetTemplate) {
		const params = preset.parameters;
		text = preset.sample_text;
		engineId = preset.engine_id;
		emotion = String(params.emotion ?? emotion);
		voiceDesign = String(params.emotion_text ?? voiceDesign);
		emoAlpha = Number(params.emo_alpha ?? emoAlpha);
		speed = Number(params.speed ?? speed);
		temperature = Number(params.temperature ?? temperature);
		topP = Number(params.top_p ?? topP);
		topK = Number(params.top_k ?? topK);
		maxTextTokensPerSegment = Number(params.max_text_tokens_per_segment ?? maxTextTokensPerSegment);
		intervalSilence = Number(params.interval_silence ?? intervalSilence);
		diffusionSteps = Number(params.diffusion_steps ?? diffusionSteps);
		cfgRate = Number(params.cfg_rate ?? cfgRate);
		outputFormat = (params.output_format as 'wav' | 'mp3' | 'flac') ?? outputFormat;
	}

	async function poll(taskId: string) {
		for (let i = 0; i < 900; i++) {
			const task = await Api.task(taskId);
			tasks = [task, ...tasks.filter((x) => x.task_id !== taskId)].slice(0, 12);
			if (['success', 'failed', 'cancelled'].includes(task.status)) return;
			await new Promise((r) => setTimeout(r, 1000));
		}
	}

		async function generate() {
			if (!text.trim()) return;
			error = '';
			busy = true;
			try {
				if (isMimoClone && settings?.mimo_voiceclone_confirm_upload) {
					const name = selectedVoice?.name ?? '当前参考音色';
					const ok = window.confirm(`MiMo 音色复刻会把「${name}」的本次参考音频发送到小米云端用于生成。继续吗？`);
					if (!ok) return;
				}
				const eng = engines.find((e) => e.manifest.engine_id === engineId);
				if (eng && eng.state.status !== 'loaded') await Api.startEngine(engineId);
				const res = await Api.generate(requestBody());
			await poll(res.task_id);
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	function reuse(task: GenerationTask) {
		text = task.input_text;
		engineId = task.engine_id;
		voiceId = task.voice_id ?? '';
	}

	async function clean() {
		text = (await Api.cleanText(text)).text;
	}
</script>

<svelte:head><title>单条生成 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head"><div><h1>单条生成</h1><p class="muted">短文本快速合成、试听、参数复用和导出</p></div><button class="btn" onclick={clean}><Wand2 size={15} /> 清洗文本</button></div>
	<div class="workbench">
		<section class="panel stack">
			<h2>参数模板</h2>
			<div class="preset-grid">
				{#each presets as preset}
					<button class="preset-card" type="button" onclick={() => applyPreset(preset)}>
						<strong>{preset.name}</strong>
						<span>{preset.scene}</span>
						<small>{preset.description}</small>
					</button>
				{/each}
			</div>
			<textarea bind:value={text} placeholder="输入要合成的文本"></textarea>
			<div class="row" style="justify-content:space-between"><span class="muted">{text.length} 字</span><button class="btn primary" disabled={busy || !text.trim()} onclick={generate}><Send size={15} /> {busy ? '生成中' : '生成'}</button></div>
			{#if error}<div class="badge fail">{error}</div>{/if}
			<h2>结果</h2>
			<div class="stack">
				{#each tasks as task}
					<div class="card stack">
						<div class="row" style="justify-content:space-between"><strong>{task.input_text.slice(0, 48)}{task.input_text.length > 48 ? '...' : ''}</strong><span class="badge" class:ok={task.status === 'success'} class:fail={task.status === 'failed'}>{taskStatusLabel(task.status)}</span></div>
						<p class="muted">{task.engine_id} · {task.generation_time_ms ? (task.generation_time_ms / 1000).toFixed(1) + 's' : ''}</p>
						{#if task.result_id}
							<audio class="audio" controls src={`/api/history/${task.result_id}/audio`}></audio>
						{/if}
						<div class="row">
							<button class="btn" onclick={() => reuse(task)}><RotateCcw size={15} /> 复用</button>
							{#if task.result_id}<a class="btn" href={`/api/history/${task.result_id}/audio`}><Download size={15} /> 下载</a>{/if}
						</div>
						{#if task.error_message}<p class="muted">{task.error_message}</p>{/if}
					</div>
				{/each}
			</div>
		</section>
		<aside class="panel stack">
			<h2><Play size={16} /> 参数</h2>
				<div class="field"><label for="engine">引擎</label><select id="engine" bind:value={engineId}>{#each ttsEngines as e}<option value={e.manifest.engine_id}>{e.manifest.display_name} · {engineStatusLabel(e.state.status)}</option>{/each}</select></div>
				{#if !isMimoPreset && !isMimoDesign}
					<div class="field">
						<label for="voice">声音</label>
						<select id="voice" bind:value={voiceId}>
							<option value="">未选择</option>
							{#each voiceChoices as v}<option value={v.voice_id}>{v.name}</option>{/each}
						</select>
						{#if isMimoClone}<small>只显示已授权且可上传云端复刻的本地参考音色。</small>{/if}
					</div>
				{/if}
				{#if isMimoPreset}
					<div class="field">
						<label for="mimo-voice">MiMo 官方音色</label>
						<select id="mimo-voice" bind:value={mimoVoice}>{#each mimoVoiceOptions as option}<option value={option.value}>{option.label}</option>{/each}</select>
					</div>
				{/if}
				<div class="field"><label for="language">语言</label><select id="language" bind:value={language}><option value="zh">中文</option><option value="en">英文</option><option value="auto">自动</option></select></div>
				{#if isMimo}
					{#if isMimoDesign}
						<div class="field"><label for="voice-design-prompt">音色描述</label><textarea id="voice-design-prompt" bind:value={voiceDesignPrompt}></textarea><small>描述这副声音本身，例如年龄、性别、质感、语速和情绪底色。</small></div>
					{:else}
						<div class="field"><label for="style-instruction">风格指令</label><textarea id="style-instruction" bind:value={styleInstruction} placeholder="例如：语速稍慢，语气温柔，像知识视频旁白。"></textarea></div>
					{/if}
					{#if isMimoClone && settings?.mimo_voiceclone_confirm_upload}<small>生成前会再次提醒：本次参考音频将发送到 MiMo 云端。</small>{/if}
				{/if}
				{#if supportsEmotion}
				<div class="field"><label for="emotion">情绪</label><select id="emotion" bind:value={emotion}><option value="calm">自然 calm</option><option value="happy">高兴 happy</option><option value="sad">悲伤 sad</option><option value="angry">愤怒 angry</option><option value="afraid">恐惧 afraid</option><option value="disgusted">反感 disgusted</option><option value="melancholic">低落 melancholic</option><option value="surprised">惊讶 surprised</option></select><small>控制语气倾向；正式产出建议先用自然或低强度情绪。</small></div>
				{#if isIndexTTS}<div class="field"><label for="emo-alpha">情绪强度 {emoAlpha.toFixed(2)}</label><input id="emo-alpha" type="range" min="0" max="1" step="0.05" bind:value={emoAlpha} /><small>数值越高，表演感越强；长文本通常不宜过高。</small></div>{/if}
			{/if}
			{#if isOmniVoice && !voiceId}
				<div class="field">
					<label for="voice-design">声音设计标签</label>
					<select id="voice-design" bind:value={voiceDesign}>
						<option value="女，青年，中音调">女，青年，中音调</option>
						<option value="男，青年，中音调">男，青年，中音调</option>
						<option value="女，中年，高音调">女，中年，高音调</option>
						<option value="男，中年，低音调">男，中年，低音调</option>
						<option value="女，青年，耳语">女，青年，耳语</option>
					</select>
				</div>
			{/if}
				{#if !isMimo}<div class="field"><label for="speed">语速 {speed.toFixed(2)}</label><input id="speed" type="range" min="0.5" max="2" step="0.05" bind:value={speed} /><small>低于 1 更稳更慢，高于 1 更适合短视频快讲。</small></div>{/if}
			<div class="field"><label for="temp">随机性 Temperature {temperature.toFixed(2)}</label><input id="temp" type="range" min="0.1" max="2" step="0.05" bind:value={temperature} /><small>越低越稳定，越高变化越多，也更可能口齿漂移。</small></div>
			<div class="field"><label for="top-p">采样范围 Top-P {topP.toFixed(2)}</label><input id="top-p" type="range" min="0" max="1" step="0.05" bind:value={topP} /><small>限制模型从多大概率范围里选声音片段；默认 0.8 较稳。</small></div>
			<div class="field"><label for="top-k">候选数量 Top-K {topK}</label><input id="top-k" type="range" min="1" max="100" step="1" bind:value={topK} /><small>每一步最多保留多少候选；过大更自由，过小更保守。</small></div>
			<div class="field"><label for="segment">分段长度 Token {maxTextTokensPerSegment}</label><input id="segment" type="range" min="20" max="500" step="10" bind:value={maxTextTokensPerSegment} /><small>长文本会被拆段生成；短分段更利于剪辑和稳定停顿。</small></div>
			<div class="field"><label for="silence">段间静默 {intervalSilence}ms</label><input id="silence" type="range" min="0" max="2000" step="50" bind:value={intervalSilence} /><small>控制分段之间的留白，便于字幕和剪辑卡点。</small></div>
			{#if isIndexTTS}
				<div class="field"><label for="cfg">引导强度 CFG Rate {cfgRate.toFixed(2)}</label><input id="cfg" type="range" min="0" max="1" step="0.05" bind:value={cfgRate} /><small>控制生成时贴合条件的力度；默认 0.7 适合大多数旁白。</small></div>
				<div class="field"><label for="diffusion">扩散步数 Diffusion Steps {diffusionSteps}</label><input id="diffusion" type="range" min="5" max="60" step="1" bind:value={diffusionSteps} /><small>步数越多越细致但更慢；25 是当前主力基线。</small></div>
			{/if}
			<div class="field"><label for="format">输出格式</label><select id="format" bind:value={outputFormat}><option value="wav">WAV</option><option value="mp3">MP3</option><option value="flac">FLAC</option></select></div>
		</aside>
	</div>
</main>

<style>
	.preset-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 10px;
	}

	.preset-card {
		text-align: left;
		display: grid;
		gap: 5px;
		border: 1px solid var(--line);
		background: #121519;
		color: var(--text);
		border-radius: 7px;
		padding: 11px;
	}

	.preset-card span,
	.preset-card small {
		color: var(--muted);
		line-height: 1.4;
	}

	.field small {
		color: var(--muted);
		font-size: 12px;
		line-height: 1.45;
	}
</style>
