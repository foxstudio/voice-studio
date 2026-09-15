<script lang="ts">
	import { Api } from '$lib/api';
	import { ApiError } from '$lib/api/client';
	import type { EngineAudioDiagnosis, EngineDetail, EngineInstallation, VoiceAsset } from '$lib/api/types';
	import { Activity, Download, ExternalLink, FolderSymlink, Play, RotateCcw, Search, Square, Volume2 } from 'lucide-svelte';
	import { capabilityLabel } from '$lib/labels';
	import { modelInstallationGuidance } from '$lib/model-installation-presentation';
	import { onMount } from 'svelte';
	import ModelResourceCard from './ModelResourceCard.svelte';
	import EnginePopover from './EnginePopover.svelte';
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
		resourceGroups,
		type AvailabilityFilter,
		type EngineTask,
		type HubView
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
	let diagnosis = $state<Record<string, EngineAudioDiagnosis>>({});
	let diagnosisErrors = $state<Record<string, string>>({});
	let diagnosing = $state<Record<string, boolean>>({});
	let healthChecks = $state<Record<string, EngineCheckCard>>({});
	let checking = $state<Record<string, boolean>>({});
	let managingInstallations = $state<Record<string, boolean>>({});
	let loadingEngines = $state(true);
	let engineLoadError = $state('');
	let installationLoadError = $state('');
	let voiceLoadError = $state('');
	let query = $state('');
	let hubView = $state<HubView>('engines');
	let taskFilter = $state<'all' | EngineTask>('all');
	let locationFilter = $state<'all' | 'local' | 'cloud'>('all');
	let availabilityFilter = $state<AvailabilityFilter>('all');

	function installationRuntimeEngineId(item: EngineInstallation) {
		if (item.runtime_engine_id) return item.runtime_engine_id;
		if (item.family_id === 'vibevoice-asr' && ['4bit', '8bit'].includes(item.variant_id ?? '')) {
			return `vibevoice-asr-mlx-${item.variant_id}`;
		}
		return item.reference_only ? null : item.engine_id;
	}

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
		return installations.filter((installation) => {
			const linkedEngine = linkedEngineForInstallation(installation);
			const group = resourceGroup(installation, linkedEngine);
			if (taskFilter === 'tts' && group !== 'tts') return false;
			if (taskFilter === 'asr' && group !== 'asr') return false;
			if (taskFilter === 'audio_generation') return false;
			if (!availabilityMatches(availabilityFilter, resourceAvailability(installation).key)) return false;
			if (!q) return true;
			return [installation.display_name, installation.engine_id, installation.architecture, installation.recommended_for]
				.filter(Boolean).join(' ').toLowerCase().includes(q);
		});
	});

	const groupedEngineSections = $derived.by(() => engineTaskGroups.map((task) => {
		const taskEngines = visibleEngines.filter((engine) => engineTask(engine) === task.id);
		const families = new Map<string, EngineDetail[]>();
		for (const engine of taskEngines) {
			const id = engineFamilyId(engine.manifest.engine_id);
			families.set(id, [...(families.get(id) ?? []), engine]);
		}
		return {
			...task,
			families: [...families.entries()].map(([id, items]) => ({ id, label: engineFamilyLabel(items[0]), items }))
		};
	}).filter((section) => section.families.length));

	const groupedResourceSections = $derived.by(() => resourceGroups.map((group) => {
		const groupItems = visibleInstallations.filter((installation) => {
			const linkedEngine = linkedEngineForInstallation(installation);
			return resourceGroup(installation, linkedEngine) === group.id;
		});
		const families = new Map<string, EngineInstallation[]>();
		for (const installation of groupItems) {
			const id = installation.family_id || engineFamilyId(installationRuntimeEngineId(installation) || installation.engine_id);
			families.set(id, [...(families.get(id) ?? []), installation]);
		}
		return {
			...group,
			families: [...families.entries()].map(([id, items]) => ({
				id,
				label: resourceFamilyLabel(id, items[0]),
				items
			}))
		};
	}).filter((section) => section.families.length));

	const engineCounts = $derived.by(() => ({
		visible: hubView === 'engines' ? visibleEngines.length : visibleInstallations.length,
		total: hubView === 'engines' ? engines.length : installations.length,
		local: engines.filter((engine) => engine.manifest.engine_type === 'local').length,
		cloud: engines.filter((engine) => engine.manifest.engine_type === 'cloud').length,
		ready: hubView === 'engines'
			? engines.filter((engine) => engineAvailability(engine, installationMap.get(engine.manifest.engine_id)).key === 'ready').length
			: installations.filter((installation) => resourceAvailability(installation).key === 'ready').length
	}));

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

	function primaryDownloadSource(installation: EngineInstallation | undefined) {
		return installation?.download_sources.find((source) => source.preferred)
			?? installation?.download_sources[0];
	}

	function secondaryDownloadSources(installation: EngineInstallation | undefined) {
		const primary = primaryDownloadSource(installation);
		return installation?.download_sources.filter((source) => source.url !== primary?.url) ?? [];
	}

	function showModelIntroduction(installation: EngineInstallation | undefined) {
		return Boolean(installation && installation.source_url !== primaryDownloadSource(installation)?.url);
	}

	function showMoreSources(installation: EngineInstallation | undefined) {
		if (!installation) return false;
		return secondaryDownloadSources(installation).length > 0
			|| Boolean(installation.runtime_url && installation.runtime_url !== installation.source_url);
	}

	function canRunEngine(engine: EngineDetail, installation: EngineInstallation | undefined) {
		if (!isEngineCompatible(engine)) return false;
		if (engine.manifest.engine_type !== 'local' || !installation) return true;
		return installation.runtime_ready === true;
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
		return `/api/engines/${engineId}/diagnostic-audio?t=${item.generation_time_ms ?? Date.now()}`;
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
				.then((items) => { installations = items; })
				.catch((error) => { installationLoadError = `本机模型读取失败：${errorText(error)}`; }),
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

<svelte:head><title>引擎与模型 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head"><div><h1>引擎与模型</h1><p class="muted">分别查看可调用的引擎服务，以及本机保存的模型文件</p></div><button class="btn" disabled={loadingEngines} onclick={refreshPage}><RotateCcw size={16} /> {loadingEngines ? '读取中' : '刷新'}</button></div>
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
	<nav class="hub-tabs" aria-label="页面视图">
		<button class:active={hubView === 'engines'} aria-pressed={hubView === 'engines'} onclick={() => hubView = 'engines'}><strong>引擎服务</strong><span>可启动或连接的能力</span></button>
		<button class:active={hubView === 'models'} aria-pressed={hubView === 'models'} onclick={() => { hubView = 'models'; locationFilter = 'all'; }}><strong>本机模型</strong><span>已下载和可下载的资源</span></button>
	</nav>
	<section class="panel stack engine-toolbar-panel">
		<div class="toolbar-grid">
			{#if hubView === 'engines'}<label class="field">
				<span>试听参考音色</span>
				<select bind:value={voiceId} aria-label="生成试听参考音色">
					<option value="">未选择，OmniVoice 可无参考试听</option>
					{#each voices as voice}<option value={voice.voice_id}>{voice.name}</option>{/each}
				</select>
			</label>{/if}
			<label class="field">
				<span>搜索{hubView === 'engines' ? '引擎' : '模型'}</span>
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
					{#if hubView === 'engines'}<option value="audio_generation">音频生成</option>{/if}
				</select>
			</label>
			{#if hubView === 'engines'}<label class="field"><span>位置</span><select bind:value={locationFilter}><option value="all">全部位置</option><option value="local">本地</option><option value="cloud">云端</option></select></label>{/if}
			<label class="field"><span>可用状态</span><select bind:value={availabilityFilter}><option value="all">全部状态</option><option value="ready">可用 / 已下载</option><option value="needs_install">需安装 / 未下载</option><option value="needs_setup">需配置 / 未检查</option><option value="error">异常</option></select></label>
			<div class="summary-box" aria-label="引擎概览">
				<span class="summary-chip strong">可见 {engineCounts.visible}/{engineCounts.total}</span>
				{#if hubView === 'engines'}<span class="summary-chip">本地 {engineCounts.local}</span><span class="summary-chip">云端 {engineCounts.cloud}</span>{/if}
				<span class="summary-chip ok">{hubView === 'engines' ? '可用' : '已下载'} {engineCounts.ready}</span>
			</div>
		</div>
	</section>
	<div class="engine-color-legend" aria-label="卡片左侧色条说明">
		<span class="legend-label">左侧色条</span>
		{#if hubView === 'engines'}<span><i class="legend-swatch local"></i>本地引擎</span><span><i class="legend-swatch cloud"></i>云端引擎</span>{:else}<span><i class="legend-swatch resource"></i>模型资源</span><span class="legend-note">下载状态以文字标签为准</span>{/if}
	</div>

	{#if hubView === 'engines'}
	{#each groupedEngineSections as section}
		<section class="hub-section" aria-labelledby={`engine-${section.id}`}>
			<div class="section-title"><div><h2 id={`engine-${section.id}`}>{section.label}</h2><p>{section.description}</p></div><span class="section-count">{section.families.reduce((sum, family) => sum + family.items.length, 0)} 个模式</span></div>
			{#each section.families as family}
			<div class="family-block">
				<div class="family-head"><h3>{family.label}</h3>{#if family.items.length > 1}<span>{family.items.length} 个模式</span>{/if}</div>
				<div class="engine-list">
		{#each family.items as engine}
			{@const installation = installationMap.get(engine.manifest.engine_id)}
			{@const downloadSource = primaryDownloadSource(installation)}
			{@const secondarySources = secondaryDownloadSources(installation)}
			{@const engineRunnable = canRunEngine(engine, installation)}
			{@const availability = engineAvailability(engine, installation)}
			<article class={`card engine-surface ${engine.manifest.engine_type === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
				<div class="engine-overview">
					<div class="engine-card-head">
						<h3>{engineVariantLabel(engine)}</h3>
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
						<span class="installation-path" title={installation.preferred_path ?? ''}><FolderSymlink size={13} /> {installation.preferred_path || '未设置模型目录'}</span>
						{#if installation.installation_status === 'installing'}
							<div class="download-progress"><div style={`width: ${Math.max(1, Math.round((installation.progress ?? 0) * 100))}%`}></div></div>
							<small>{Math.round((installation.progress ?? 0) * 100)}% · {formatBytes(installation.downloaded_bytes)} / {formatBytes(installation.total_bytes)}</small>
						{:else if modelInstallationGuidance(installation)}
							<small>{modelInstallationGuidance(installation)}</small>
						{:else}
							<small>{installation.reuse_note}</small>
						{/if}
					{:else if engine.manifest.engine_type === 'cloud'}
						<div class="resource-status-line"><strong>{availability.detail}</strong><span>{engine.manifest.version}</span></div>
						<small>无需下载本地权重；环境检查会验证连接、凭据和服务配置。</small>
					{:else}
						<div class="resource-status-line"><span class="badge fail">缺少模型资料</span></div>
						<small>目录中尚未登记这个本地引擎的模型或运行时来源。</small>
					{/if}
				</div>

				<div class="compact-actions">
					{#if installation?.automatic_download_supported && !installation.installed}<button class="btn primary mini-btn" disabled={managingInstallations[installation.engine_id] || installation.installation_status === 'installing'} onclick={() => installEngineModel(installation)}><Download size={13} /> {installation.installation_status === 'installing' ? '安装中' : '下载模型'}</button>{/if}
					{#if engine.manifest.engine_type === 'local'}
						{#if engine.state.status === 'loaded'}<button class="btn mini-btn" onclick={() => stop(engine.manifest.engine_id)}><Square size={13} /> 停止</button>{:else}<button class="btn primary mini-btn" disabled={!engineRunnable} title={!engineRunnable ? '先安装并通过环境检查后才能启动' : undefined} onclick={() => start(engine.manifest.engine_id)}><Play size={13} /> 启动</button>{/if}
					{/if}
					<button class="btn mini-btn" disabled={checking[engine.manifest.engine_id]} onclick={() => check(engine.manifest.engine_id)}><Activity size={13} /> {checking[engine.manifest.engine_id] ? '检查中' : engine.manifest.engine_type === 'cloud' ? '连接检查' : '环境检查'}</button>
					{#if !engine.manifest.capabilities.includes('speech_recognition')}<button class="btn mini-btn" disabled={!engineRunnable || diagnosing[engine.manifest.engine_id]} title={!engineRunnable ? '先安装并通过环境检查后才能试听' : undefined} onclick={() => diagnose(engine.manifest.engine_id)}><Volume2 size={13} /> {diagnosing[engine.manifest.engine_id] ? '试听中' : '生成试听'}</button>{/if}
					{#if downloadSource}<a class="btn mini-btn" href={downloadSource.url} target="_blank" rel="noreferrer"><Download size={13} /> {downloadSource.region === 'cn' ? '国内模型' : '模型权重'}</a>{/if}
					{#if showModelIntroduction(installation)}<a class="btn mini-btn" href={installation?.source_url} target="_blank" rel="noreferrer"><ExternalLink size={13} /> 模型介绍</a>{/if}
					{#if engine.manifest.documentation_url}<a class="btn mini-btn" href={engine.manifest.documentation_url} target="_blank" rel="noreferrer"><ExternalLink size={13} /> 官方说明</a>{/if}
					{#if installation && showMoreSources(installation)}
						<EnginePopover id={`sources-${engine.manifest.engine_id}`} label="更多来源">
							<div class="source-menu">
								{#each secondarySources as source}<a href={source.url} target="_blank" rel="noreferrer"><Download size={12} /> {source.label}</a>{/each}
								{#if installation.runtime_url && installation.runtime_url !== installation.source_url}<a href={installation.runtime_url} target="_blank" rel="noreferrer"><ExternalLink size={12} /> {installation.runtime_label ?? '运行时'}</a>{/if}
							</div>
						</EnginePopover>
					{/if}
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
						{/if}
						{#if diagnosis[engine.manifest.engine_id].quality.warnings?.length}
							<p class="muted">{diagnosis[engine.manifest.engine_id].quality.warnings?.join('；')}</p>
						{/if}
					</div>
				{/if}
				{#if engine.state.error_message}<p class="muted engine-wide">{engine.state.error_message}</p>{/if}
			</article>
		{/each}
				</div>
			</div>
			{/each}
		</section>
	{/each}
	{#if !groupedEngineSections.length}<div class="empty">{loadingEngines ? '正在读取引擎…' : '当前筛选下没有引擎'}</div>{/if}
	{:else}
	{#each groupedResourceSections as section}
		<section class="hub-section" aria-labelledby={`resource-${section.id}`}>
			<div class="section-title"><div><h2 id={`resource-${section.id}`}>{section.label}</h2><p>{section.description}</p></div><span class="section-count">{section.families.reduce((sum, family) => sum + family.items.length, 0)} 个资源</span></div>
			{#each section.families as family}
				<div class="family-block">
					<div class="family-head"><h3>{family.label}</h3>{#if family.items.length > 1}<span>{family.items.length} 个版本</span>{/if}</div>
					<div class="resource-list">{#each family.items as installation}<ModelResourceCard {installation} displayName={resourceDisplayName(installation)} busy={Boolean(managingInstallations[installation.engine_id])} onInstall={installEngineModel} onUninstall={installation.category === 'media_tool' ? uninstallModel : undefined} />{/each}</div>
				</div>
			{/each}
		</section>
	{/each}
	{#if !groupedResourceSections.length}<div class="empty">{loadingEngines ? '正在读取模型…' : installationLoadError ? '模型清单读取失败，请点击上方“重新读取”' : '当前筛选下没有模型资源'}</div>{/if}
	{/if}
</main>

<style>
	.page { --engine-card-columns: minmax(260px, 1fr) minmax(290px, 1.05fr) 360px; --engine-card-gap: 12px; --hub-local-color: #4f9cf9; --hub-cloud-color: #a78bfa; --hub-resource-color: #64748b; }
	.page-notice { margin-bottom: 12px; }
	.load-issue { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 12px; margin-bottom: 14px; padding: 10px 12px; border-color: rgba(245, 184, 78, .34); background: rgba(245, 184, 78, .06); color: #c5a968; font-size: 10px; }
	.load-issue strong { color: #edcf8a; }
	.load-issue .mini-btn { margin-left: auto; }
	.hub-tabs { display: inline-grid; grid-template-columns: repeat(2, minmax(220px, 1fr)); gap: 7px; width: min(560px, 100%); margin: 0 0 14px; }
	.hub-tabs button { display: grid; gap: 2px; min-height: 52px; padding: 9px 12px; border: 1px solid var(--line); border-radius: 8px; background: #111419; color: var(--muted); text-align: left; cursor: pointer; }
	.hub-tabs button strong { color: #dce3eb; font-size: 12px; }
	.hub-tabs button span { font-size: 9px; }
	.hub-tabs button:hover, .hub-tabs button:focus-visible { border-color: #435164; background: #171c23; }
	.hub-tabs button.active { border-color: rgba(79, 156, 249, .55); background: rgba(79, 156, 249, .1); box-shadow: inset 0 -2px 0 var(--hub-local-color); }
	.engine-toolbar-panel { margin-bottom: 14px; padding: 10px; }
	.toolbar-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(138px, 1fr)); gap: 10px; align-items: end; }
	.toolbar-grid .summary-box { grid-column: span 2; }
	.search-field { display: flex; align-items: center; gap: 8px; height: 34px; min-height: 34px; overflow: hidden; padding: 0 10px; border: 1px solid var(--line); border-radius: 7px; background: #0f1216; }
	.search-field input { width: 100%; height: 30px; min-height: 30px; padding: 0; border: 0; outline: none; background: transparent; color: inherit; }
	.summary-box { display: flex; align-items: center; align-content: center; gap: 6px; flex-wrap: wrap; height: 34px; min-height: 34px; overflow: hidden; padding: 5px; border: 1px solid var(--line); border-radius: 7px; background: #101215; }
	.summary-chip { display: inline-flex; align-items: center; min-height: 22px; padding: 2px 7px; border: 1px solid rgba(255, 255, 255, .07); border-radius: 999px; background: rgba(255, 255, 255, .025); color: var(--muted); font-size: 10px; line-height: 1.2; white-space: nowrap; }
	.summary-chip.strong { border-color: rgba(79, 156, 249, .28); background: rgba(79, 156, 249, .09); color: #d9e2ef; }
	.summary-chip.ok { border-color: rgba(66, 196, 155, .28); background: rgba(66, 196, 155, .08); color: #9ee6c8; }
	.engine-color-legend { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 12px; min-height: 24px; margin: 0 3px 10px; color: #7f8996; font-size: 9px; }
	.engine-color-legend > span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
	.engine-color-legend .legend-label { color: #a7b0bb; font-weight: 650; }
	.legend-swatch { width: 3px; height: 12px; border-radius: 999px; background: var(--hub-resource-color); }
	.legend-swatch.local { background: var(--hub-local-color); }
	.legend-swatch.cloud { background: var(--hub-cloud-color); }
	.legend-swatch.resource { background: var(--hub-resource-color); }
	.legend-note { color: #687483; }

	.hub-section { display: grid; gap: 8px; margin-bottom: 18px; }
	.section-title { display: flex; align-items: end; justify-content: space-between; padding: 0 3px; }
	.section-title h2, .section-title p { margin: 0; }
	.section-title h2 { color: #e9eef4; font-size: 14px; }
	.section-title p { margin-top: 2px; color: var(--muted); font-size: 10px; }
	.section-count { color: #778391; font-size: 9px; }
	.family-block { display: grid; gap: 5px; }
	.family-head { display: flex; align-items: center; gap: 7px; padding: 0 5px; }
	.family-head h3 { margin: 0; color: #cbd5e1; font-size: 11px; font-weight: 650; }
	.family-head span { color: #687483; font-size: 9px; }
	.resource-list { display: grid; gap: 7px; }

	.engine-list { display: grid; gap: 7px; }
	.engine-surface { display: grid; grid-template-columns: var(--engine-card-columns); align-items: center; gap: var(--engine-card-gap); min-height: 98px; padding: 10px 12px; border-left: 3px solid var(--hub-local-color); }
	.engine-surface.engine-local { border-left-color: var(--hub-local-color); }
	.engine-surface.engine-cloud { border-left-color: var(--hub-cloud-color); }
	.engine-overview, .engine-resource { display: grid; gap: 6px; min-width: 0; }
	.engine-card-head { display: flex; align-items: center; justify-content: flex-start; gap: 7px; min-width: 0; }
	.engine-card-head h3 { min-width: 0; margin: 0; overflow: hidden; color: #eef2f6; font-size: 13px; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
	.engine-card-head .badge { flex: none; }
	.badge.warning { border-color: rgba(245, 184, 78, .32); background: rgba(245, 184, 78, .09); color: #eac884; }
	.clamp-text { display: -webkit-box; width: 100%; margin: 0; overflow: hidden; color: var(--muted); font-size: 10px; line-height: 1.45; line-clamp: 1; -webkit-line-clamp: 1; -webkit-box-orient: vertical; }
	.description-pop { position: relative; width: 100%; cursor: default; }
	.description-pop.has-tooltip { cursor: help; }
	.description-pop.has-tooltip:focus-visible { border-radius: 4px; outline: 1px solid rgba(79, 156, 249, .46); outline-offset: 3px; }
	.compact-tags { display: flex; align-items: flex-start; flex-wrap: nowrap; gap: 4px; max-height: 22px; overflow: hidden; }
	.compact-tags .badge { padding: 1px 6px; font-size: 9px; line-height: 1.35; }
	.resource-status-line { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
	.resource-status-line strong { color: #c9d3df; font-size: 10px; }
	.installation-path { display: flex; align-items: center; gap: 5px; min-width: 0; overflow: hidden; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
	.engine-resource small { margin: 0; overflow: hidden; color: #798695; font-size: 9px; line-height: 1.4; text-overflow: ellipsis; white-space: nowrap; }
	.download-progress { height: 4px; overflow: hidden; border-radius: 999px; background: rgba(255, 255, 255, .08); }
	.download-progress div { height: 100%; border-radius: inherit; background: #4f9cf9; transition: width 180ms ease; }
	.compact-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 5px; }
	.mini-btn { min-height: 26px; padding: 3px 7px; gap: 4px; border-radius: 6px; font-size: 10px; line-height: 1.1; text-decoration: none; }
	.source-menu { display: grid; gap: 5px; }
	.source-menu a { display: flex; align-items: center; gap: 7px; min-height: 30px; padding: 6px 8px; border: 1px solid transparent; border-radius: 6px; background: #20242a; color: #c1cad5; font-size: 10px; text-decoration: none; }
	.source-menu a:hover, .source-menu a:focus-visible { border-color: #444d59; background: #272c33; color: #edf2f7; }
	.status-box, .diagnosis-box { grid-column: 1 / -1; display: grid; gap: 6px; padding: 8px; border: 1px solid var(--line); border-radius: 7px; background: rgba(255, 255, 255, .025); }
	.status-box.running { border-color: rgba(79, 156, 249, .22); background: rgba(79, 156, 249, .07); }
	.status-box p, .diagnosis-box p { margin: 0; overflow-wrap: anywhere; font-size: 10px; line-height: 1.45; }
	.diagnosis-audio { height: 30px; }
	.diagnosis-download { justify-self: start; text-decoration: none; }
	.engine-wide { grid-column: 1 / -1; margin: 0; }

	@media (max-width: 1120px) {
		.toolbar-grid { grid-template-columns: 1fr 1fr; }
		.engine-surface { grid-template-columns: minmax(230px, .8fr) minmax(250px, 1fr); }
		.compact-actions { grid-column: 1 / -1; justify-content: flex-start; }
	}
	@media (max-width: 760px) {
		.hub-tabs, .toolbar-grid, .engine-surface { grid-template-columns: 1fr; }
		.toolbar-grid .summary-box { grid-column: 1; }
		.engine-surface { min-height: 0; padding: 12px; }
		.compact-actions { grid-column: 1; justify-content: flex-start; }
		.engine-resource small { white-space: normal; }
		.engine-color-legend { justify-content: flex-start; gap: 8px 12px; }
	}
	@media (prefers-reduced-motion: reduce) { .download-progress div { transition: none; } }
</style>
