<script lang="ts">
	import { Api } from '$lib/api';
	import type { EngineAudioDiagnosis, EngineDetail, VoiceAsset } from '$lib/api/types';
	import { Activity, Play, RotateCcw, Square, Volume2 } from 'lucide-svelte';
	import { capabilityLabel, engineStatusLabel } from '$lib/labels';

	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let message = $state('');
	let voiceId = $state('');
	let diagnosis = $state<Record<string, EngineAudioDiagnosis>>({});

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

<svelte:head><title>引擎中心 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head"><div><h1>引擎中心</h1><p class="muted">本地引擎生命周期、能力标签、版本差异和音频诊断</p></div><button class="btn" onclick={refresh}><RotateCcw size={16} /> 刷新</button></div>
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
	<section class="grid">
			{#each engines as engine}
				<article class="card stack">
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
					<span class="badge">{engine.manifest.engine_type === 'local' ? '本地' : '云端'}</span>
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
		{/each}
	</section>
</main>

<style>
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
</style>
