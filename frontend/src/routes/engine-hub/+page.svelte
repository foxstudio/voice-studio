<script lang="ts">
	import { Api } from '$lib/api';
	import type { EngineAudioDiagnosis, EngineDetail, VoiceAsset } from '$lib/api/types';
	import { Activity, Play, RotateCcw, Search, Square, Volume2 } from 'lucide-svelte';
	import { capabilityLabel, engineStatusLabel } from '$lib/labels';

	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let message = $state('');
	let voiceId = $state('');
	let diagnosis = $state<Record<string, EngineAudioDiagnosis>>({});
	let query = $state('');
	let engineFilter = $state<'all' | 'local' | 'cloud' | 'asr' | 'tts'>('all');

	const visibleEngines = $derived.by(() => {
		const q = query.trim().toLowerCase();
		return engines.filter((engine) => {
			if (engineFilter === 'local' && engine.manifest.engine_type !== 'local') return false;
			if (engineFilter === 'cloud' && engine.manifest.engine_type !== 'cloud') return false;
			if (engineFilter === 'asr' && !engine.manifest.capabilities.includes('speech_recognition')) return false;
			if (engineFilter === 'tts' && engine.manifest.capabilities.includes('speech_recognition')) return false;
			if (!q) return true;
			return (
				engine.manifest.display_name.toLowerCase().includes(q) ||
				engine.manifest.engine_id.toLowerCase().includes(q) ||
				engine.manifest.description.toLowerCase().includes(q) ||
				engine.manifest.capabilities.join(' ').toLowerCase().includes(q)
			);
		});
	});

	async function refresh() {
		[engines, voices] = await Promise.all([Api.engines(), Api.voices()]);
	}
	$effect(() => { refresh(); });

	async function start(id: string) {
		message = `正在启动 ${id}`;
		await Api.startEngine(id);
		await refresh();
		message = '';
	}
	async function stop(id: string) {
		await Api.stopEngine(id);
		await refresh();
	}
	async function check(id: string) {
		const result = await Api.healthEngine(id);
		message = JSON.stringify(result);
	}
	async function diagnose(id: string) {
		message = `正在诊断 ${id}`;
		const result = await Api.diagnoseEngineAudio(id, { voice_id: voiceId || null });
		diagnosis = { ...diagnosis, [id]: result };
		message = '';
	}
</script>

<svelte:head><title>引擎管理 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head"><div><h1>引擎管理</h1><p class="muted">本地引擎生命周期、能力标签、版本差异和音频诊断</p></div><button class="btn" onclick={refresh}><RotateCcw size={16} /> 刷新</button></div>
	{#if message}<div class="panel muted">{message}</div>{/if}
	<section class="panel stack" style="margin-bottom:16px">
		<h2>音频诊断参考音色</h2>
		<div class="row">
			<select bind:value={voiceId} aria-label="诊断参考音色">
				<option value="">未选择，OmniVoice 可无参考诊断</option>
				{#each voices as voice}<option value={voice.voice_id}>{voice.name}</option>{/each}
			</select>
			<span class="muted">IndexTTS v2 诊断需要参考音色；OmniVoice 可用声音设计做无参考诊断。</span>
		</div>
	</section>
	<section class="panel stack" style="margin-bottom:16px">
		<div class="toolbar-grid">
			<label class="field">
				<span>搜索引擎</span>
				<div class="search-field">
					<Search size={15} />
					<input bind:value={query} placeholder="名称、描述、能力" />
				</div>
			</label>
			<label class="field">
				<span>类型</span>
				<select bind:value={engineFilter}>
					<option value="all">全部</option>
					<option value="local">本地</option>
					<option value="cloud">云端</option>
					<option value="tts">TTS</option>
					<option value="asr">ASR</option>
				</select>
			</label>
			<div class="stack summary-box">
				<span class="muted">可见 {visibleEngines.length} / {engines.length}</span>
				<span class="badge">本地 {engines.filter((engine) => engine.manifest.engine_type === 'local').length}</span>
			</div>
		</div>
	</section>
	<section class="grid">
			{#each visibleEngines as engine}
				<article class={`card stack engine-surface ${engine.manifest.engine_type === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
				<div class="row" style="justify-content:space-between">
					<h2>{engine.manifest.display_name}</h2>
					<span class="badge" class:ok={engine.state.status === 'loaded'} class:fail={engine.state.status === 'error'}>{engineStatusLabel(engine.state.status)}</span>
				</div>
				<p class="muted">{engine.manifest.description}</p>
				{#if engine.manifest.engine_id === 'indextts-v2'}
					<p class="muted">当前主力中文口播引擎：支持 8 种情绪、更长文本、S2Mel/BigVGAN2。</p>
				{/if}
				<div class="row">
					<span class="badge">{engine.manifest.sample_rate ?? '-'} Hz</span>
					<span class="badge badge-kind">{engine.manifest.engine_type === 'local' ? '本地' : '云端'}</span>
					<span class="badge">{engine.manifest.privacy_level === 'local_only' ? '仅本地' : engine.manifest.privacy_level}</span>
				</div>
				<div class="row">
					{#each engine.manifest.capabilities as cap}<span class="badge">{capabilityLabel(cap)}</span>{/each}
				</div>
					<div class="row">
						{#if engine.state.status === 'loaded'}<button class="btn" onclick={() => stop(engine.manifest.engine_id)}><Square size={15} /> 停止</button>{:else}<button class="btn primary" onclick={() => start(engine.manifest.engine_id)}><Play size={15} /> 启动</button>{/if}
						<button class="btn" onclick={() => check(engine.manifest.engine_id)}><Activity size={15} /> 检查</button>
						{#if !engine.manifest.capabilities.includes('speech_recognition')}<button class="btn" onclick={() => diagnose(engine.manifest.engine_id)}><Volume2 size={15} /> 音频诊断</button>{/if}
					</div>
				{#if diagnosis[engine.manifest.engine_id]}
					<div class="diagnosis-box">
						<span class="badge" class:ok={diagnosis[engine.manifest.engine_id].status === 'passed'} class:fail={diagnosis[engine.manifest.engine_id].status === 'failed'}>{diagnosis[engine.manifest.engine_id].status === 'passed' ? '可听门槛通过' : '诊断失败/需复核'}</span>
						<p class="muted">RMS {diagnosis[engine.manifest.engine_id].quality.rms ?? '-'} · 峰值 {diagnosis[engine.manifest.engine_id].quality.peak ?? '-'} · 时长 {diagnosis[engine.manifest.engine_id].quality.duration_ms ?? '-'}ms</p>
						{#if diagnosis[engine.manifest.engine_id].quality.warnings?.length}
							<p class="muted">{diagnosis[engine.manifest.engine_id].quality.warnings?.join('；')}</p>
						{/if}
					</div>
				{/if}
				{#if engine.state.error_message}<p class="muted">{engine.state.error_message}</p>{/if}
			</article>
		{:else}
			<div class="empty">当前筛选下没有引擎</div>
		{/each}
	</section>
</main>

<style>
	.toolbar-grid {
		display: grid;
		grid-template-columns: minmax(0, 1.5fr) minmax(180px, 0.7fr) minmax(140px, 0.6fr);
		gap: 12px;
		align-items: end;
	}

	.search-field {
		display: flex;
		align-items: center;
		gap: 8px;
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 0 10px;
		background: #0f1216;
	}

	.search-field input {
		border: 0;
		background: transparent;
		width: 100%;
		min-height: 34px;
		color: inherit;
		outline: none;
	}

	.summary-box {
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
		min-height: 100%;
	}

	.diagnosis-box {
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 10px;
		background: #101215;
		display: grid;
		gap: 7px;
	}

	.diagnosis-box p {
		margin: 0;
	}

	@media (max-width: 960px) {
		.toolbar-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
