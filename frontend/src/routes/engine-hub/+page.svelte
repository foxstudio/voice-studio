<script lang="ts">
	import { Api } from '$lib/api';
	import { ApiError } from '$lib/api/client';
	import type { EngineAudioDiagnosis, EngineDetail, EngineInstallation, VoiceAsset } from '$lib/api/types';
	import { Activity, Download, ExternalLink, Play, RotateCcw, Search, Square, Volume2 } from 'lucide-svelte';
	import { capabilityLabel } from '$lib/labels';
	import { modelInstallationGuidance } from '$lib/model-installation-presentation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import { onMount } from 'svelte';
	import ModelResourceCard from './ModelResourceCard.svelte';
	import ModelDetails from './ModelDetails.svelte';
	import {
		availabilityMatches,
		engineAvailability,
		engineFamilyId,
		engineFamilyLabel,
		engineTask,
		engineTaskGroups,
		engineVariantLabel,
		resourceAvailability,
		resourceGroup,
		installationRuntimeEngineId,
		standaloneResources,
		type AvailabilityFilter,
		type EngineTask
	} from './engine-hub-presentation';

	type EngineCheckCard = {
		status: 'running' | 'passed' | 'failed';
		title: string;
		detail: string;
	};

	let engines = $state<EngineDetail[]>([]);
	let installations = $state<EngineInstallation[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let message = $state('');
	let voiceId = $state('');
	let referenceAudio = $state<HTMLAudioElement | null>(null);
	let referencePlaying = $state(false);
	let referenceError = $state('');
	const selectedReference = $derived(voices.find(voice => voice.voice_id === voiceId));
	const referencePreviewUrl = $derived(selectedReference?.reference_audio_ids[0]
		? `/api/voices/${encodeURIComponent(selectedReference.voice_id)}/audio/${encodeURIComponent(selectedReference.reference_audio_ids[0])}` : '');
	$effect(() => {
		const url = referencePreviewUrl;
		const audio = referenceAudio;
		audio?.pause();
		referencePlaying = false;
		referenceError = '';
		return () => { audio?.pause(); };
	});
	async function previewReference() {
		const audio = referenceAudio;
		const url = referencePreviewUrl;
		if (!audio || !url) return;
		if (!audio.paused) { audio.pause(); return; }
		referenceError = '';
		try { await audio.play(); }
		catch (error) {
			if (url === referencePreviewUrl && !(error instanceof DOMException && error.name === 'AbortError')) {
				referenceError = '原音暂时无法播放，请检查参考音频是否存在。';
			}
		}
	}

	let diagnosis = $state<Record<string, EngineAudioDiagnosis>>({});
	let diagnosisErrors = $state<Record<string, string>>({});
	let diagnosing = $state<Record<string, boolean>>({});
	let healthChecks = $state<Record<string, EngineCheckCard>>({});
	let checking = $state<Record<string, boolean>>({});
	let managingInstallations = $state<Record<string, boolean>>({});
	let loadingEngines = $state(true);
	let installationsLoaded = $state(false);
	let engineLoadError = $state('');
	let installationLoadError = $state('');
	let voiceLoadError = $state('');
	let query = $state('');
	let taskFilter = $state<'all' | EngineTask | 'workflow'>('all');
	let locationFilter = $state<'all' | 'local' | 'cloud'>('all');
	let availabilityFilter = $state<AvailabilityFilter>('all');


	const installationMap = $derived(new Map(
		installations
			.map((item) => [installationRuntimeEngineId(item), item] as const)
			.filter((item): item is readonly [string, EngineInstallation] => Boolean(item[0]))
	));

	function linkedEngineForInstallation(installation: EngineInstallation) {
		const runtimeId = installationRuntimeEngineId(installation);
		return runtimeId ? engines.find((engine) => engine.manifest.engine_id === runtimeId) : undefined;
	}

	function resourceDisplayName(installation: EngineInstallation) {
		if (installation.display_name) return installation.display_name;
		const linkedEngine = linkedEngineForInstallation(installation);
		if (linkedEngine) return linkedEngine.manifest.display_name;
		if (installation.engine_id === 'moss-transcribe-diarize-mlx') return 'MOSS 说话人分离';
		if (installation.engine_id === 'campplus-modelscope') return 'CAM++ 声纹复核';
		if (installation.engine_id === 'qwen3-forced-aligner') return 'Qwen3 字幕时间对齐';
		if (installation.engine_id === 'semantic-alignment-labse') return 'LaBSE 跨语言语义对齐';
		return installation.engine_id;
	}

	const visibleEngines = $derived.by(() => {
		const q = query.trim().toLowerCase();
		return engines.filter((engine) => {
			const installation = installationMap.get(engine.manifest.engine_id);
			if (locationFilter !== 'all' && engine.manifest.engine_type !== locationFilter) return false;
			if (taskFilter !== 'all' && engineTask(engine) !== taskFilter) return false;
			if (!availabilityMatches(availabilityFilter, engineAvailability(engine, installation).key)) return false;
			if (!q) return true;
			return (
				engine.manifest.display_name.toLowerCase().includes(q) ||
				engine.manifest.engine_id.toLowerCase().includes(q) ||
				engine.manifest.description.toLowerCase().includes(q) ||
				engine.manifest.capabilities.join(' ').toLowerCase().includes(q)
			);
		});
	});

	const visibleInstallations = $derived.by(() => {
		const q = query.trim().toLowerCase();
		return standaloneResources(engines, installations).filter((installation) => {
			if (locationFilter === 'cloud') return false;
			const linkedEngine = linkedEngineForInstallation(installation);
			const group = resourceGroup(installation, linkedEngine);
			if (taskFilter !== 'all' && taskFilter !== group) return false;
			if (!availabilityMatches(availabilityFilter, resourceAvailability(installation).key)) return false;
			if (!q) return true;
			return [installation.display_name, installation.engine_id, installation.architecture, installation.recommended_for]
				.filter(Boolean).join(' ').toLowerCase().includes(q);
		});
	});

	const groupedEngineSections = $derived.by(() => [...engineTaskGroups,
		{ id: 'workflow', label: '流程辅助', description: '供音视频处理使用的人声分离、时间对齐与声纹模型。' }
	].map((task) => {
		const families = new Map<string, { id: string; label: string; items: EngineDetail[]; resources: EngineInstallation[] }>();
		for (const engine of visibleEngines.filter(engine => engineTask(engine) === task.id)) {
			const id = engineFamilyId(engine.manifest.engine_id);
			if (!families.has(id)) families.set(id, { id, label: engineFamilyLabel(engine), items: [], resources: [] });
			families.get(id)!.items.push(engine);
		}
		for (const item of visibleInstallations.filter(item => resourceGroup(item, linkedEngineForInstallation(item)) === task.id)) {
			const id = item.family_id || engineFamilyId(installationRuntimeEngineId(item) || item.engine_id);
			if (!families.has(id)) families.set(id, { id, label: resourceFamilyLabel(id, item), items: [], resources: [] });
			families.get(id)!.resources.push(item);
		}
		return { ...task, families: [...families.values()] };
	}).filter(section => section.families.length));

	const engineCounts = $derived({
		visible: visibleEngines.length + visibleInstallations.length,
		total: engines.length + standaloneResources(engines, installations).length,
		ready: engines.filter(engine => engineAvailability(engine, installationMap.get(engine.manifest.engine_id)).key === 'ready').length
	});

	const engineDescriptionExtras: Record<string, string> = {
		'indextts-v2': '当前主力中文口播引擎：8 种情绪、长文本、S2Mel/BigVGAN2。',
		'omnivoice': '代码为 Apache-2.0；预训练权重为 CC-BY-NC，仅限非商业用途，覆盖 646 种语言。',
		'emotivoice': '适合短句试听和音色筛选，16000 Hz 采样。',
		'f5-tts': '非自回归架构，推理效率高，支持整本书连续生成。',
		'cosyvoice-sft': 'CosyVoice-300M-SFT 模型，开箱即用的官方预训练音色。',
		'cosyvoice-zero-shot': '提供参考音频 + 对应台词，即可跨语言复刻。',
		'qwen3-tts-mlx-0.6b': '0.6B MLX 实验接入，支持预置音色和本地参考音色克隆。',
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

	function resourceFamilyLabel(id: string, installation: EngineInstallation) {
		if (id === 'vibevoice-asr') return 'VibeVoice ASR';
		if (id === 'cosyvoice') return 'CosyVoice';
		if (id === 'mimo-v2.5') return 'MiMo V2.5';
		if (id === 'doubao') return '豆包语音';
		return resourceDisplayName(installation);
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

	function isEngineCompatible(engine: EngineDetail) {
		return engine.compatibility?.compatible !== false;
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

	function formatBytes(value: number | undefined) {
		const bytes = Number(value ?? 0);
		if (!bytes) return '0 MB';
		if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
		return `${Math.round(bytes / 1_000_000)} MB`;
	}


	function canRunEngine(engine: EngineDetail, installation: EngineInstallation | undefined) {
		if (!isEngineCompatible(engine)) return false;
		if (engine.manifest.engine_type !== 'local') return true;
		return installation?.runtime_ready === true;
	}

	async function installEngineModel(installation: EngineInstallation) {
		const id = installation.engine_id;
		let acceptedLicenseId: string | undefined;
		if (installation.license_acceptance_required) {
			const accepted = confirm(
				`下载 ${installation.model_license ?? '受限许可'} 模型权重？\n\n${installation.license_note}\n\n仅在你确认用途符合许可时继续。`
			);
			if (!accepted) return;
			acceptedLicenseId = installation.license_acceptance_id;
		}
		managingInstallations = { ...managingInstallations, [id]: true };
		message = '模型开始下载；关闭页面不会中断后台安装。';
		try {
			await Api.installEngineModel(id, acceptedLicenseId);
			for (let attempt = 0; attempt < 1200; attempt += 1) {
				installations = await Api.engineInstallations();
				const current = installations.find((item) => item.engine_id === id);
				if (!current || current.installation_status !== 'installing') {
					if (current?.installed) message = `${current.display_name ?? id} 已安装并完成完整性校验。`;
					else if (current?.error) message = `模型安装失败：${current.error}`;
					break;
				}
				await new Promise((resolve) => setTimeout(resolve, 750));
			}
		} catch (error) {
			message = `模型安装失败：${errorText(error)}`;
		} finally {
			managingInstallations = { ...managingInstallations, [id]: false };
		}
	}

	async function uninstallModel(id: string) {
		if (!confirm('删除这份分离模型？已有项目的音轨不会删除，但下次分离前需要重新安装。')) return;
		managingInstallations = { ...managingInstallations, [id]: true };
		try {
			await Api.uninstallEngineModel(id);
			installations = await Api.engineInstallations();
			message = '分离模型已从本机模型目录删除。';
		} catch (error) {
			message = `删除失败：${errorText(error)}`;
		} finally {
			managingInstallations = { ...managingInstallations, [id]: false };
		}
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
		return item.audio_url || `/api/engines/${engineId}/diagnostic-audio?t=${item.generation_time_ms ?? Date.now()}`;
	}

	async function refresh() {
		engineLoadError = '';
		installationLoadError = '';
		voiceLoadError = '';
		await Promise.all([
			Api.engines()
				.then((items) => { engines = items; })
				.catch((error) => { engineLoadError = `引擎服务读取失败：${errorText(error)}`; }),
			Api.engineInstallations()
				.then((items) => { installations = items; installationsLoaded = true; })
				.catch((error) => { installationLoadError = `本机模型读取失败：${errorText(error)}`; installationsLoaded = true; }),
			Api.voices({ offset: 0, limit: 2000 })
				.then((items) => { voices = items; })
				.catch((error) => { voiceLoadError = `试听音色读取失败：${errorText(error)}`; })
		]);
	}
	async function refreshPage() {
		loadingEngines = true;
		message = '';
		await refresh();
		loadingEngines = false;
	}
	onMount(() => {
		const requestedType = new URLSearchParams(window.location.search).get('type');
		if (requestedType === 'local' || requestedType === 'cloud') locationFilter = requestedType;
		if (requestedType === 'asr' || requestedType === 'tts') taskFilter = requestedType;
		void refreshPage();
	});

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
	/** 试听记录的台词带上引擎名，方便从合成历史里精确定位到这一条。 */
	function diagnosisText(engineId: string) {
		const label = engines.find((engine) => engine.manifest.engine_id === engineId)?.manifest.display_name ?? engineId;
		return `引擎试听：${label}`;
	}

	async function diagnose(id: string) {
		diagnosing = { ...diagnosing, [id]: true };
		diagnosisErrors = { ...diagnosisErrors, [id]: '' };
		try {
			const result = await Api.diagnoseEngineAudio(id, { voice_id: voiceId || null, text: diagnosisText(id) });
			diagnosis = { ...diagnosis, [id]: result };
		} catch (error) {
			diagnosisErrors = { ...diagnosisErrors, [id]: errorText(error) };
		} finally {
			diagnosing = { ...diagnosing, [id]: false };
		}
	}
</script>

<svelte:head><title>引擎与模型 - 声音工作台</title></svelte:head>

<main class="page">
	<PageHeader title="引擎与模型" subtitle="下载模型、启动引擎、生成试听，都在这里。">
		{#snippet actions()}
			<button class="btn" disabled={loadingEngines} onclick={refreshPage}><RotateCcw size={16} /> {loadingEngines ? '读取中' : '刷新'}</button>
		{/snippet}
	</PageHeader>
	{#if message}<div class="panel muted page-notice">{message}</div>{/if}
	{#if engineLoadError || installationLoadError || voiceLoadError}
		<div class="panel load-issue" role="alert">
			<strong>部分信息暂时没有读取成功</strong>
			{#if engineLoadError}<span>{engineLoadError}</span>{/if}
			{#if installationLoadError}<span>{installationLoadError}</span>{/if}
			{#if voiceLoadError}<span>{voiceLoadError}</span>{/if}
			<button class="btn mini-btn" disabled={loadingEngines} onclick={refreshPage}><RotateCcw size={13} /> 重新读取</button>
		</div>
	{/if}
	<section class="panel stack engine-toolbar-panel">
		<div class="toolbar-grid">
			<div class="field audition-field">
				<label for="hub-reference-voice">试听参考音色</label>
				<select id="hub-reference-voice" bind:value={voiceId} aria-label="生成试听参考音色">
					<option value="">未选择，OmniVoice 可无参考试听</option>
					{#each voices as voice}<option value={voice.voice_id}>{voice.name}</option>{/each}
				</select>
			<button class="btn mini-btn reference-preview" type="button" disabled={!referencePreviewUrl}
					aria-label={referencePlaying ? '暂停原音' : '试听原音'} title={referencePreviewUrl ? `播放 ${selectedReference?.name ?? ''} 的参考原音` : '先选择带参考音频的音色'}
					onclick={previewReference}>{#if referencePlaying}<Square size={13} /> 暂停{:else}<Volume2 size={13} /> 原音{/if}</button>
				<audio bind:this={referenceAudio} src={referencePreviewUrl || undefined} preload="none"
					onplay={() => referencePlaying = true} onpause={() => referencePlaying = false} onended={() => referencePlaying = false}
					onerror={() => { if (referencePreviewUrl) referenceError = '原音暂时无法播放，请检查参考音频是否存在。'; }}></audio>
				{#if referenceError}<span class="reference-error" role="alert">{referenceError}</span>{/if}
			</div>
			<label class="field">
				<span>搜索</span>
				<div class="search-field">
					<Search size={15} />
					<input bind:value={query} placeholder="名称、描述、能力" />
				</div>
			</label>
			<label class="field">
				<span>任务</span>
				<select bind:value={taskFilter}>
					<option value="all">全部任务</option>
					<option value="tts">语音合成</option>
					<option value="asr">语音识别</option>
					<option value="audio_generation">音频生成</option><option value="workflow">流程辅助</option>
				</select>
			</label>
			<label class="field"><span>位置</span><select bind:value={locationFilter}><option value="all">全部位置</option><option value="local">本地</option><option value="cloud">云端</option></select></label>
			<label class="field"><span>可用状态</span><select bind:value={availabilityFilter}><option value="all">全部状态</option><option value="ready">可用 / 已下载</option><option value="needs_install">需安装 / 未下载</option><option value="needs_setup">需配置 / 未检查</option><option value="error">异常</option></select></label>
			<div class="summary-box" aria-label="引擎概览">
				<span class="summary-chip strong">可见 {engineCounts.visible}/{engineCounts.total}</span>
				<span class="summary-chip ok" title="全部引擎中状态就绪的数量，不受筛选影响">全库已就绪 {engineCounts.ready}</span>
			</div>
		</div>
	</section>
	{#each groupedEngineSections as section}
		<section class="hub-section" aria-labelledby={`engine-${section.id}`}>
			<div class="section-title"><div><h2 id={`engine-${section.id}`}>{section.label}</h2><p>{section.description}</p></div><span class="section-count">{section.families.reduce((sum, family) => sum + family.items.length + family.resources.length, 0)} 项</span></div>
			{#each section.families as family}
			<div class="family-block">
				{#if family.items.length + family.resources.length > 1}<div class="family-head"><h3>{family.label}</h3><span>{family.items.length + family.resources.length} 个版本 / 模式</span></div>{/if}
				<div class="engine-list">
		{#each family.items as engine}
			{@const installation = installationMap.get(engine.manifest.engine_id)}
			{@const engineRunnable = canRunEngine(engine, installation)}
			{@const availability = engineAvailability(engine, installation)}
			<article class={`card engine-surface ${engine.manifest.engine_type === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
				<div class="engine-overview">
					<div class="engine-card-head">
						<h3>{family.items.length + family.resources.length > 1 ? engineVariantLabel(engine) : engine.manifest.display_name}</h3>
						<span class="badge" class:ok={availability.tone === 'ok'} class:fail={availability.tone === 'fail'} class:warning={availability.tone === 'warning'}>{availability.label}</span>
					</div>
					<div class="description-pop" use:descriptionTooltip={engineDescription(engine)}><p class="muted clamp-text">{engineDescription(engine)}</p></div>
					<div class="compact-tags feature-tags">{#each engineTags(engine).slice(0, 5) as tag, index}<span class="badge" class:badge-kind={index === 0}>{tag}</span>{/each}</div>
				</div>

				<div class="engine-resource">
					{#if installation}
						<div class="resource-status-line">
							<strong>{availability.detail}</strong>
							{#if installation.size_bytes || installation.total_bytes}<strong>{formatBytes(installation.size_bytes || installation.total_bytes)}</strong>{/if}
						</div>

						{#if installation.installation_status === 'installing'}
							<div class="download-progress" role="progressbar" aria-label="模型下载进度" aria-valuenow={Math.round((installation.progress ?? 0) * 100)} aria-valuemin="0" aria-valuemax="100"><div style={`width: ${Math.max(1, Math.round((installation.progress ?? 0) * 100))}%`}></div></div>
							<small>{Math.round((installation.progress ?? 0) * 100)}% · {formatBytes(installation.downloaded_bytes)} / {formatBytes(installation.total_bytes)}</small>
						{:else if modelInstallationGuidance(installation)}
							<small>{modelInstallationGuidance(installation)}</small>
						{:else if installation.license_acceptance_required}
							<small>{installation.license_note}</small>
						{/if}
					{:else if engine.manifest.engine_type === 'cloud'}
						<div class="resource-status-line"><strong>{availability.detail}</strong><span>{engine.manifest.version}</span></div>
						<small>无需下载本地权重；环境检查会验证连接、凭据和服务配置。</small>
					{:else if !installationsLoaded}
						<div class="resource-status-line"><span class="badge">正在读取模型资料…</span></div>
					{:else}
						<div class="resource-status-line"><span class="badge fail">缺少模型资料</span></div>
						<small>目录中尚未登记这个本地引擎的模型或运行时来源。</small>
					{/if}
				</div>

				<div class="compact-actions">
					{#if installation?.automatic_download_supported && !installation.installed}<button class="btn primary mini-btn" disabled={managingInstallations[installation.engine_id] || installation.installation_status === 'installing'} onclick={() => installEngineModel(installation)}><Download size={13} /> {installation.installation_status === 'installing' ? '安装中' : '下载模型'}</button>{/if}
					{#if engine.manifest.engine_type === 'local' && (engineRunnable || engine.state.status === 'loaded')}
					{#if engine.state.status === 'loaded'}<button class="btn mini-btn" onclick={() => stop(engine.manifest.engine_id)}><Square size={13} /> 停止</button>{:else}<button class="btn primary mini-btn" onclick={() => start(engine.manifest.engine_id)}><Play size={13} /> 启动</button>{/if}
					{/if}
					<button class="btn mini-btn" disabled={checking[engine.manifest.engine_id]} onclick={() => check(engine.manifest.engine_id)}><Activity size={13} /> {checking[engine.manifest.engine_id] ? '检查中' : engine.manifest.engine_type === 'cloud' ? '连接检查' : '环境检查'}</button>
					{#if engineRunnable && !engine.manifest.capabilities.includes('speech_recognition')}<button class="btn mini-btn" disabled={diagnosing[engine.manifest.engine_id]} onclick={() => diagnose(engine.manifest.engine_id)}><Volume2 size={13} /> {diagnosing[engine.manifest.engine_id] ? '试听中' : '生成试听'}</button>{/if}
					{#if installation}<ModelDetails {installation} documentationUrl={engine.manifest.documentation_url ?? undefined} description={engineDescription(engine)} label={!installation.installed && !installation.automatic_download_supported ? '安装说明' : '详情与来源'} />
					{:else if engine.manifest.documentation_url}<a class="btn mini-btn" href={engine.manifest.documentation_url} target="_blank" rel="noreferrer"><ExternalLink size={13} /> 官方说明</a>{/if}
				</div>
				{#if !isEngineCompatible(engine)}
					<div class="status-box">
						<span class="badge fail">无法在当前系统运行</span>
						<p class="muted">{engine.compatibility?.message ?? '当前系统与此引擎不兼容。'}</p>
					</div>
				{/if}
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
							{#if diagnosis[engine.manifest.engine_id].result_id}<a class="btn mini-btn diagnosis-download" href={`/generate?q=${encodeURIComponent(diagnosisText(engine.manifest.engine_id))}`} title="到合成历史里只看这次试听的记录">已保存到合成历史 · 查看</a>{/if}
						{/if}
						{#if diagnosis[engine.manifest.engine_id].quality.warnings?.length}
							<p class="muted">{diagnosis[engine.manifest.engine_id].quality.warnings?.join('；')}</p>
						{/if}
					</div>
				{/if}
				{#if engine.state.error_message}<p class="muted engine-wide">{engine.state.error_message}</p>{/if}
			</article>
		{/each}
		{#each family.resources as installation}<ModelResourceCard {installation} displayName={resourceDisplayName(installation)} busy={Boolean(managingInstallations[installation.engine_id])} onInstall={installEngineModel} onUninstall={installation.category === 'media_tool' ? uninstallModel : undefined} />{/each}
				</div>
			</div>
			{/each}
		</section>
	{/each}
	{#if !groupedEngineSections.length}<div class="empty">{loadingEngines ? '正在读取引擎与模型…' : '没有符合筛选条件的引擎或模型'}{#if !loadingEngines}<button class="btn mini-btn" onclick={() => { query = ''; taskFilter = 'all'; locationFilter = 'all'; availabilityFilter = 'all'; }}>重置筛选</button>{/if}</div>{/if}
</main>

<style>
	.page { --engine-card-columns: minmax(260px, 1.35fr) minmax(190px, .8fr) 350px; --engine-card-gap: 12px; --hub-local-color: #4f9cf9; --hub-cloud-color: #a78bfa; --hub-resource-color: #64748b; }
	.page-notice { margin-bottom: 12px; }
	.load-issue { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 12px; margin-bottom: 14px; padding: 10px 12px; border-color: rgba(245, 184, 78, .34); background: rgba(245, 184, 78, .06); color: #c5a968; font-size: 12px; }
	.load-issue strong { color: #edcf8a; }
	.load-issue .mini-btn { margin-left: auto; }
	.engine-toolbar-panel { margin-bottom: 14px; padding: 10px; }
	.toolbar-grid { display: grid; grid-template-columns: minmax(200px, 1.6fr) repeat(3, minmax(120px, 1fr)); gap: 10px; align-items: end; }
	.toolbar-grid .summary-box { order: 5; grid-column: span 2; height: auto; border: 0; background: transparent; padding: 0; }
	.audition-field { order: 6; grid-column: span 2; display: flex; align-items: center; justify-content: flex-end; gap: 10px; }
	.toolbar-grid select { height: 34px; min-height: 34px; font-size: 12px; line-height: 1.2; padding: 0 10px; }
	.audition-field select { width: min(260px, 55%); min-width: 0; }
	.audition-field .reference-preview { flex: none; height: 34px; min-height: 34px; padding: 0 10px; border-radius: 7px; }
	.reference-error { color: #ff9b9b; font-size: 12px; width: 100%; }
	.audition-field { flex-wrap: wrap; }
	.search-field { display: flex; align-items: center; gap: 8px; height: 34px; min-height: 34px; overflow: hidden; padding: 0 10px; border: 1px solid var(--line); border-radius: 7px; background: #0f1216; }
	.search-field:focus-within { outline: 2px solid var(--hub-local-color); outline-offset: 2px; }
	.search-field input { width: 100%; height: 30px; min-height: 30px; padding: 0; border: 0; outline: none; background: transparent; color: inherit; }
	.summary-box { display: flex; align-items: center; align-content: center; gap: 6px; flex-wrap: wrap; height: 34px; min-height: 34px; overflow: hidden; padding: 5px; border: 1px solid var(--line); border-radius: 7px; background: #101215; }
	.summary-chip { display: inline-flex; align-items: center; min-height: 22px; padding: 2px 7px; border: 1px solid rgba(255, 255, 255, .07); border-radius: 999px; background: rgba(255, 255, 255, .025); color: var(--muted); font-size: 12px; line-height: 1.2; white-space: nowrap; }
	.summary-chip.strong { border-color: rgba(79, 156, 249, .28); background: rgba(79, 156, 249, .09); color: #d9e2ef; }
	.summary-chip.ok { border-color: rgba(66, 196, 155, .28); background: rgba(66, 196, 155, .08); color: #9ee6c8; }

	.empty { display: grid; gap: 10px; justify-items: center; }
	.hub-section { display: grid; gap: 8px; margin-bottom: 18px; }
	.section-title { display: flex; align-items: end; justify-content: space-between; padding: 0 3px; }
	.section-title h2, .section-title p { margin: 0; }
	.section-title h2 { color: #e9eef4; font-size: 14px; }
	.section-title p { margin-top: 2px; color: var(--muted); font-size: 12px; }
	.section-count { color: #778391; font-size: 11px; }
	.family-block { display: grid; gap: 5px; }
	.family-head { display: flex; align-items: center; gap: 7px; padding: 0 5px; }
	.family-head h3 { margin: 0; color: #cbd5e1; font-size: 11px; font-weight: 650; }
	.family-head span { color: #687483; font-size: 11px; }

	.engine-list { display: grid; gap: 7px; }
	.engine-surface { display: grid; grid-template-columns: var(--engine-card-columns); align-items: center; gap: var(--engine-card-gap); min-height: 112px; padding: 10px 12px; border-left: 3px solid var(--hub-local-color); }
	.engine-surface.engine-local { border-left-color: var(--hub-local-color); }
	.engine-surface.engine-cloud { border-left-color: var(--hub-cloud-color); }
	.engine-overview, .engine-resource { display: grid; gap: 6px; min-width: 0; }
	.engine-card-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-start; gap: 7px; min-width: 0; }
	.engine-card-head h3 { min-width: 0; margin: 0; overflow: hidden; color: #eef2f6; font-size: 13px; line-height: 1.3; text-overflow: ellipsis; white-space: normal; }
	.engine-card-head .badge { flex: none; }
	.badge.warning { border-color: rgba(245, 184, 78, .32); background: rgba(245, 184, 78, .09); color: #eac884; }
	.clamp-text { display: -webkit-box; width: 100%; margin: 0; overflow: hidden; color: var(--muted); font-size: 12px; line-height: 1.45; line-clamp: 2; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
	.description-pop { position: relative; width: 100%; cursor: default; }
	.description-pop.has-tooltip { cursor: help; }
	.description-pop.has-tooltip:focus-visible { border-radius: 4px; outline: 1px solid rgba(79, 156, 249, .46); outline-offset: 3px; }
	.compact-tags { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 4px; }
	.compact-tags .badge { padding: 1px 6px; font-size: 11px; line-height: 1.35; }
	.resource-status-line { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
	.resource-status-line strong { color: #c9d3df; font-size: 12px; }
	.engine-resource small { margin: 0; overflow: hidden; color: #798695; font-size: 11px; line-height: 1.4; text-overflow: ellipsis; white-space: nowrap; }
	.download-progress { height: 4px; overflow: hidden; border-radius: 999px; background: rgba(255, 255, 255, .08); }
	.download-progress div { height: 100%; border-radius: inherit; background: #4f9cf9; transition: width 180ms ease; }
	.compact-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 5px; }
	.mini-btn { min-height: 32px; padding: 3px 7px; gap: 4px; border-radius: 6px; font-size: 12px; line-height: 1.1; text-decoration: none; }
	.status-box, .diagnosis-box { grid-column: 1 / -1; display: grid; gap: 6px; padding: 8px; border: 1px solid var(--line); border-radius: 7px; background: rgba(255, 255, 255, .025); }
	.status-box.running { border-color: rgba(79, 156, 249, .22); background: rgba(79, 156, 249, .07); }
	.status-box p, .diagnosis-box p { margin: 0; overflow-wrap: anywhere; font-size: 12px; line-height: 1.45; }
	.diagnosis-audio { height: 30px; }
	.diagnosis-download { justify-self: start; text-decoration: none; }
	.engine-wide { grid-column: 1 / -1; margin: 0; }

	@media (max-width: 1120px) {
		.toolbar-grid { grid-template-columns: 1fr 1fr; }
		.engine-surface { grid-template-columns: minmax(230px, .8fr) minmax(250px, 1fr); }
		.compact-actions { grid-column: 1 / -1; justify-content: flex-start; }
	}
	@media (max-width: 760px) {
		.toolbar-grid, .engine-surface { grid-template-columns: 1fr; }
		.toolbar-grid .summary-box, .audition-field { grid-column: 1; }
		.audition-field { justify-content: flex-start; flex-wrap: wrap; }
		.audition-field label { width: 100%; }
		.toolbar-grid select { height: 34px; min-height: 34px; font-size: 12px; line-height: 1.2; padding: 0 10px; }
	.audition-field select { width: auto; flex: 1; }
		.engine-surface { min-height: 0; padding: 12px; }
		.compact-actions { grid-column: 1; justify-content: flex-start; }
		.engine-resource small { white-space: normal; }
	}
	@media (prefers-reduced-motion: reduce) { .download-progress div { transition: none; } }
</style>
