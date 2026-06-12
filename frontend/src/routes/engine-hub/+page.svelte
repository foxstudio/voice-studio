<script lang="ts">
	import { Api } from '$lib/api';
	import { ApiError } from '$lib/api/client';
	import type { EngineAudioDiagnosis, EngineDetail, VoiceAsset } from '$lib/api/types';
	import { Activity, Play, RotateCcw, Search, Square, Volume2 } from 'lucide-svelte';
	import { capabilityLabel, engineStatusLabel } from '$lib/labels';

	type EngineCheckCard = {
		status: 'running' | 'passed' | 'failed';
		title: string;
		detail: string;
	};

	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let message = $state('');
	let voiceId = $state('');
	let diagnosis = $state<Record<string, EngineAudioDiagnosis>>({});
	let diagnosisErrors = $state<Record<string, string>>({});
	let diagnosing = $state<Record<string, boolean>>({});
	let healthChecks = $state<Record<string, EngineCheckCard>>({});
	let checking = $state<Record<string, boolean>>({});
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

	const engineDescriptionExtras: Record<string, string> = {
		'indextts-v2': '当前主力中文口播引擎：8 种情绪、长文本、S2Mel/BigVGAN2。',
		'omnivoice': 'Apache 2.0 开源，581k 小时训练数据，覆盖 646 种语言。',
		'emotivoice': '适合短句试听和音色筛选，16000 Hz 采样。',
		'f5-tts': '非自回归架构，推理效率高，支持整本书连续生成。',
		'cosyvoice-sft': 'CosyVoice-300M-SFT 模型，开箱即用的官方预训练音色。',
		'cosyvoice-zero-shot': '提供参考音频 + 对应台词，即可跨语言复刻。',
		'mimo-v2.5-tts-preset': 'Token Plan 计费，支持唱歌标签和自然语言风格指令。',
		'mimo-v2.5-tts-voicedesign': '用文字描述音色特征，如"温柔略带沙哑的女性"。',
		'mimo-v2.5-tts-voiceclone': '云端零样本克隆，支持 wav/mp3 参考音频上传。',
		'mimo-v2.5-asr': '自动语言检测，适合会议录音和素材转写。',
		'qwen3-asr-mlx': '纯 MLX 推理无需 PyTorch，数据不离设备，云端 ASR 的离线备选。'
	};

	const redundantCapabilities = new Set(['local_inference', 'cloud_api']);

	function engineDescription(engine: EngineDetail) {
		return [engine.manifest.description, engineDescriptionExtras[engine.manifest.engine_id]]
			.filter(Boolean)
			.join(' · ');
	}

	function engineTags(engine: EngineDetail) {
		const tags = [engine.manifest.engine_type === 'cloud' ? '云端' : '本地'];
		if (engine.manifest.sample_rate) tags.push(`${engine.manifest.sample_rate} Hz`);
		tags.push(
			...engine.manifest.capabilities
				.filter((cap) => !redundantCapabilities.has(cap))
				.filter((cap) => !(cap === 'transcription' && engine.manifest.capabilities.includes('speech_recognition')))
				.map(capabilityLabel)
		);
		return tags;
	}

	function descriptionTooltip(node: HTMLElement, text: string) {
		let description = text;
		let frame = 0;
		let observer: ResizeObserver | null = null;

		const updateTooltipState = () => {
			const textNode = node.querySelector<HTMLElement>('.clamp-text');
			const overflows =
				!!textNode &&
				(textNode.scrollHeight > textNode.clientHeight + 1 ||
					textNode.scrollWidth > textNode.clientWidth + 1);

			node.classList.toggle('has-tooltip', overflows);
			if (overflows) {
				node.dataset.text = description;
				node.tabIndex = 0;
				node.setAttribute('aria-label', `完整引擎描述：${description}`);
			} else {
				delete node.dataset.text;
				node.removeAttribute('tabindex');
				node.removeAttribute('aria-label');
			}
		};

		const schedule = () => {
			if (frame) cancelAnimationFrame(frame);
			frame = requestAnimationFrame(updateTooltipState);
		};

		if (typeof ResizeObserver !== 'undefined') {
			observer = new ResizeObserver(schedule);
			observer.observe(node);
			const textNode = node.querySelector<HTMLElement>('.clamp-text');
			if (textNode) observer.observe(textNode);
		}
		schedule();

		return {
			update(nextText: string) {
				description = nextText;
				schedule();
			},
			destroy() {
				if (frame) cancelAnimationFrame(frame);
				observer?.disconnect();
			}
		};
	}

	function compactJson(value: unknown) {
		return JSON.stringify(value, null, 0);
	}

	function errorText(error: unknown) {
		if (error instanceof ApiError) {
			if (error.code === 'REFERENCE_AUDIO_REQUIRED') return 'IndexTTS v2 需要先选择一个参考音色，再生成试听。';
			return `${error.message}${error.code ? `（${error.code}）` : ''}`;
		}
		return error instanceof Error ? error.message : String(error);
	}

	function formatHealthCheck(result: Record<string, unknown>): EngineCheckCard {
		const healthy = result.healthy === true;
		const status = String(result.status ?? (healthy ? 'ok' : 'unknown'));
		const detailParts = [
			result.detail,
			Array.isArray(result.missing) && result.missing.length ? `缺少文件：${result.missing.join('、')}` : null,
			result.model_path ? `模型路径：${result.model_path}` : null,
			result.base_url ? `服务地址：${result.base_url}` : null
		].filter(Boolean);

		return {
			status: healthy ? 'passed' : 'failed',
			title: healthy ? `环境可用 · ${status}` : `环境不可用 · ${status}`,
			detail: detailParts.join('；') || compactJson(result)
		};
	}

	function diagnosticAudioUrl(engineId: string, item: EngineAudioDiagnosis) {
		return `/api/engines/${engineId}/diagnostic-audio?t=${item.generation_time_ms ?? Date.now()}`;
	}

	async function refresh() {
		[engines, voices] = await Promise.all([Api.engines(), Api.voices({ offset: 0, limit: 2000 })]);
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
		checking = { ...checking, [id]: true };
		healthChecks = { ...healthChecks, [id]: { status: 'running', title: '正在检查环境', detail: '正在确认模型文件、依赖包或云端配置。' } };
		try {
			const result = await Api.healthEngine(id);
			healthChecks = { ...healthChecks, [id]: formatHealthCheck(result) };
		} catch (error) {
			healthChecks = { ...healthChecks, [id]: { status: 'failed', title: '环境检查失败', detail: errorText(error) } };
		} finally {
			checking = { ...checking, [id]: false };
		}
	}
	async function diagnose(id: string) {
		diagnosing = { ...diagnosing, [id]: true };
		diagnosisErrors = { ...diagnosisErrors, [id]: '' };
		try {
			const result = await Api.diagnoseEngineAudio(id, { voice_id: voiceId || null });
			diagnosis = { ...diagnosis, [id]: result };
		} catch (error) {
			diagnosisErrors = { ...diagnosisErrors, [id]: errorText(error) };
		} finally {
			diagnosing = { ...diagnosing, [id]: false };
		}
	}
</script>

<svelte:head><title>引擎管理 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head"><div><h1>引擎管理</h1><p class="muted">本地引擎生命周期、能力标签、版本差异和试听诊断</p></div><button class="btn" onclick={refresh}><RotateCcw size={16} /> 刷新</button></div>
	{#if message}<div class="panel muted">{message}</div>{/if}
	<section class="panel stack engine-toolbar-panel">
		<div class="toolbar-grid">
			<label class="field">
				<span>试听参考音色</span>
				<select bind:value={voiceId} aria-label="生成试听参考音色">
					<option value="">未选择，OmniVoice 可无参考试听</option>
					{#each voices as voice}<option value={voice.voice_id}>{voice.name}</option>{/each}
				</select>
			</label>
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
				<div class="description-pop" use:descriptionTooltip={engineDescription(engine)}>
					<p class="muted clamp-text">{engineDescription(engine)}</p>
				</div>
				<div class="row compact-tags feature-tags">
					{#each engineTags(engine) as tag, index}<span class="badge" class:badge-kind={index === 0}>{tag}</span>{/each}
				</div>
					<div class="row compact-actions">
						{#if engine.state.status === 'loaded'}<button class="btn mini-btn" onclick={() => stop(engine.manifest.engine_id)}><Square size={13} /> 停止</button>{:else}<button class="btn primary mini-btn" onclick={() => start(engine.manifest.engine_id)}><Play size={13} /> 启动</button>{/if}
						<button class="btn mini-btn" disabled={checking[engine.manifest.engine_id]} onclick={() => check(engine.manifest.engine_id)}><Activity size={13} /> {checking[engine.manifest.engine_id] ? '检查中' : '环境检查'}</button>
						{#if !engine.manifest.capabilities.includes('speech_recognition')}<button class="btn mini-btn" disabled={diagnosing[engine.manifest.engine_id]} onclick={() => diagnose(engine.manifest.engine_id)}><Volume2 size={13} /> {diagnosing[engine.manifest.engine_id] ? '试听中' : '生成试听'}</button>{/if}
					</div>
				{#if healthChecks[engine.manifest.engine_id]}
					<div class="status-box">
						<span class="badge" class:ok={healthChecks[engine.manifest.engine_id].status === 'passed'} class:fail={healthChecks[engine.manifest.engine_id].status === 'failed'}>{healthChecks[engine.manifest.engine_id].title}</span>
						<p class="muted">{healthChecks[engine.manifest.engine_id].detail}</p>
					</div>
				{/if}
				{#if diagnosing[engine.manifest.engine_id]}
					<div class="status-box running">
						<span class="badge">正在生成试听</span>
						<p class="muted">正在启动引擎并生成一段短音频，首次加载模型可能会比较久。</p>
					</div>
				{/if}
				{#if diagnosisErrors[engine.manifest.engine_id]}
					<div class="status-box">
						<span class="badge fail">生成试听失败</span>
						<p class="muted">{diagnosisErrors[engine.manifest.engine_id]}</p>
					</div>
				{/if}
				{#if diagnosis[engine.manifest.engine_id]}
					<div class="diagnosis-box">
						<span class="badge" class:ok={diagnosis[engine.manifest.engine_id].status === 'passed'} class:fail={diagnosis[engine.manifest.engine_id].status === 'failed'}>{diagnosis[engine.manifest.engine_id].status === 'passed' ? '可听门槛通过' : '试听失败/需复核'}</span>
						<p class="muted">RMS {diagnosis[engine.manifest.engine_id].quality.rms ?? '-'} · 峰值 {diagnosis[engine.manifest.engine_id].quality.peak ?? '-'} · 时长 {diagnosis[engine.manifest.engine_id].quality.duration_ms ?? '-'}ms</p>
						{#if diagnosis[engine.manifest.engine_id].output_path}
							<audio class="audio diagnosis-audio" controls preload="metadata" src={diagnosticAudioUrl(engine.manifest.engine_id, diagnosis[engine.manifest.engine_id])}></audio>
							<a class="btn mini-btn diagnosis-download" href={diagnosticAudioUrl(engine.manifest.engine_id, diagnosis[engine.manifest.engine_id])}>下载试听</a>
						{/if}
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
	.engine-toolbar-panel {
		margin-bottom: 14px;
		padding: 10px;
	}

	.toolbar-grid {
		display: grid;
		grid-template-columns: minmax(200px, 1fr) minmax(200px, 1.15fr) minmax(100px, 0.4fr) minmax(260px, 0.9fr);
		gap: 10px;
		align-items: end;
	}

	.search-field {
		display: flex;
		align-items: center;
		gap: 8px;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 0 10px;
		background: #0f1216;
		height: 34px;
		min-height: 34px;
		overflow: hidden;
	}

	.search-field input {
		border: 0;
		background: transparent;
		width: 100%;
		height: 30px;
		min-height: 30px;
		padding: 0;
		color: inherit;
		outline: none;
	}

	.summary-box {
		display: flex;
		align-items: center;
		align-content: center;
		gap: 6px;
		flex-wrap: wrap;
		padding: 5px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
		height: 34px;
		min-height: 34px;
		overflow: hidden;
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

	.description-pop {
		position: relative;
		width: 100%;
		cursor: default;
	}

	.description-pop.has-tooltip {
		cursor: help;
	}

	.description-pop.has-tooltip:focus-visible {
		outline: 1px solid rgba(79, 156, 249, 0.46);
		outline-offset: 3px;
		border-radius: 4px;
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

	.status-box {
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 8px;
		background: rgba(255, 255, 255, 0.025);
		display: grid;
		gap: 6px;
	}

	.status-box.running {
		border-color: rgba(79, 156, 249, 0.22);
		background: rgba(79, 156, 249, 0.07);
	}

	.status-box p {
		margin: 0;
		font-size: 11px;
		line-height: 1.45;
		overflow-wrap: anywhere;
	}

	.diagnosis-box p {
		margin: 0;
	}

	.diagnosis-audio {
		height: 30px;
	}

	.diagnosis-download {
		justify-self: start;
		text-decoration: none;
	}

	@media (max-width: 960px) {
		.toolbar-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
