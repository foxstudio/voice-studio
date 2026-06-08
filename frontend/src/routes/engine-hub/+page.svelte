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

	const engineCounts = $derived.by(() => ({
		visible: visibleEngines.length,
		total: engines.length,
		local: engines.filter((engine) => engine.manifest.engine_type === 'local').length,
		cloud: engines.filter((engine) => engine.manifest.engine_type === 'cloud').length,
		loaded: engines.filter((engine) => engine.state.status === 'loaded').length
	}));

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
			<span class="muted">IndexTTS v2 诊断默认跟随参考音色；OmniVoice 可用声音设计做无参考诊断。</span>
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
			<div class="summary-box" aria-label="引擎概览">
				<span class="summary-chip strong">可见 {engineCounts.visible}/{engineCounts.total}</span>
				<span class="summary-chip">本地 {engineCounts.local}</span>
				<span class="summary-chip">云端 {engineCounts.cloud}</span>
				<span class="summary-chip ok">已加载 {engineCounts.loaded}</span>
			</div>
		</div>
	</section>
	<section class="grid">
			{#each visibleEngines as engine}
				<article class={`card stack engine-surface ${engine.manifest.engine_type === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
				<div class="row engine-card-head">
					<h2>{engine.manifest.display_name}</h2>
					<span class="badge" class:ok={engine.state.status === 'loaded'} class:fail={engine.state.status === 'error'}>{engineStatusLabel(engine.state.status)}</span>
				</div>
				<p class="muted clamp-text text-pop" data-text={engine.manifest.description}>{engine.manifest.description}</p>
				{#if engine.manifest.engine_id === 'indextts-v2'}
					<p class="muted clamp-text text-pop" data-text="当前主力中文口播引擎：支持 8 种情绪、更长文本、S2Mel/BigVGAN2。">当前主力中文口播引擎：支持 8 种情绪、更长文本、S2Mel/BigVGAN2。</p>
				{/if}
				<div class="row compact-tags">
					<span class="badge">{engine.manifest.sample_rate ?? '-'} Hz</span>
					<span class="badge badge-kind">{engine.manifest.engine_type === 'local' ? '本地' : '云端'}</span>
					<span class="badge">{engine.manifest.privacy_level === 'local_only' ? '仅本地' : engine.manifest.privacy_level}</span>
				</div>
				<div class="row compact-tags capability-row">
					{#each engine.manifest.capabilities as cap}<span class="badge">{capabilityLabel(cap)}</span>{/each}
				</div>
					<div class="row compact-actions">
						{#if engine.state.status === 'loaded'}<button class="btn mini-btn" onclick={() => stop(engine.manifest.engine_id)}><Square size={13} /> 停止</button>{:else}<button class="btn primary mini-btn" onclick={() => start(engine.manifest.engine_id)}><Play size={13} /> 启动</button>{/if}
						<button class="btn mini-btn" onclick={() => check(engine.manifest.engine_id)}><Activity size={13} /> 检查</button>
						{#if !engine.manifest.capabilities.includes('speech_recognition')}<button class="btn mini-btn" onclick={() => diagnose(engine.manifest.engine_id)}><Volume2 size={13} /> 音频诊断</button>{/if}
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
		display: flex;
		align-items: center;
		align-content: center;
		gap: 6px;
		flex-wrap: wrap;
		padding: 6px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
		min-height: 34px;
	}

	.summary-chip {
		display: inline-flex;
		align-items: center;
		min-height: 22px;
		padding: 2px 7px;
		border: 1px solid rgba(255, 255, 255, 0.07);
		border-radius: 999px;
		color: var(--muted);
		background: rgba(255, 255, 255, 0.025);
		font-size: 11px;
		line-height: 1.2;
		white-space: nowrap;
	}

	.summary-chip.strong {
		color: #d9e2ef;
		border-color: rgba(79, 156, 249, 0.28);
		background: rgba(79, 156, 249, 0.09);
	}

	.summary-chip.ok {
		color: #9ee6c8;
		border-color: rgba(66, 196, 155, 0.28);
		background: rgba(66, 196, 155, 0.08);
	}

	.grid {
		align-items: stretch;
	}

	.card {
		gap: 9px;
	}

	.engine-card-head {
		justify-content: space-between;
		flex-wrap: nowrap;
		align-items: flex-start;
		gap: 10px;
	}

	.engine-card-head .badge {
		flex: 0 0 auto;
		white-space: nowrap;
	}

	.engine-card-head h2 {
		margin-bottom: 0;
		line-height: 1.25;
	}

	.clamp-text {
		display: -webkit-box;
		line-clamp: 2;
		-webkit-line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
		width: 100%;
		margin: 0;
		font-size: 12px;
		line-height: 1.4;
	}

	.text-pop.clamp-text {
		padding: 0;
		border: 0;
		border-radius: 0;
		background: transparent;
		backdrop-filter: none;
		box-shadow: none;
		cursor: help;
	}

	.text-pop.clamp-text:hover::after,
	.text-pop.clamp-text:focus-within::after {
		left: 0;
		bottom: calc(100% + 8px);
		width: min(320px, 76vw);
	}

	.compact-tags {
		gap: 5px;
		align-items: flex-start;
	}

	.compact-tags .badge {
		padding: 1px 6px;
		font-size: 11px;
		line-height: 1.4;
	}

	.capability-row {
		row-gap: 5px;
	}

	.compact-actions {
		gap: 6px;
		align-items: center;
		margin-top: 2px;
	}

	.mini-btn {
		min-height: 24px;
		padding: 3px 7px;
		gap: 4px;
		font-size: 11px;
		border-radius: 6px;
		line-height: 1.1;
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
