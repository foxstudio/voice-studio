<script lang="ts">
	import { Api } from '$lib/api';
	import type { AppSettings, CloudConnectionTestResponse, CloudProviderId, EngineDetail, VoiceAsset } from '$lib/api/types';
	import { engineStatusLabel } from '$lib/labels';
	import {
		Bot,
		Activity,
		CheckCircle2,
		ChevronRight,
		Cloud,
		Cpu,
		FileOutput,
		FolderCog,
		FolderOpen,
		Gauge,
		HardDrive,
		Languages,
		Library,
		Mic2,
		MonitorCog,
		Save,
		Search,
		ShieldCheck,
		SlidersHorizontal,
		Volume2
	} from 'lucide-svelte';
	import { onMount, tick } from 'svelte';
	import LlmSettings from './components/LlmSettings.svelte';
	import SettingsCheck from './components/SettingsCheck.svelte';
	import SettingsField from './components/SettingsField.svelte';
	import SettingsRow from './components/SettingsRow.svelte';
	import StorageSettings from './components/StorageSettings.svelte';

	type SectionId = 'common' | 'cloud' | 'ai' | 'files' | 'advanced';
	type SaveState = 'saved' | 'dirty' | 'saving' | 'error';
	type MimoAccessMode = 'payg' | 'token_plan' | 'custom';
	type CloudTestResult = { tone: 'success' | 'error'; message: string; note?: string };

	const MIMO_PAYG_BASE_URL = 'https://api.xiaomimimo.com/v1';
	const MIMO_TOKEN_PLAN_CN_BASE_URL = 'https://token-plan-cn.xiaomimimo.com/v1';

	const sections: { id: SectionId; label: string; description: string }[] = [
		{ id: 'common', label: '常用设置', description: '生成时最常改的默认值' },
		{ id: 'cloud', label: '云服务', description: 'MiMo、豆包和云端凭据' },
		{ id: 'ai', label: 'AI 助手', description: '语言模型连接配置' },
		{ id: 'files', label: '文件与存储', description: '输出位置、空间占用与清理' },
		{ id: 'advanced', label: '高级设置', description: '模型、缓存和运行目录' }
	];

	const searchItems: { section: SectionId; target: string; title: string; description: string; keywords: string; content?: string }[] = [
		{ section: 'common', target: 'common-title', title: '新任务的默认设置', description: '创建新任务时自动带入，之后仍可单独修改', keywords: '常用设置 默认值 状态', content: '本机服务 运行正常 连接异常 默认引擎 默认音色 默认语言 输出格式 计算设备 云端引擎' },
		{ section: 'cloud', target: 'cloud-title', title: '按服务管理连接', description: '管理云端服务地址、密钥、上传提醒和连接测试', keywords: '云服务 云端 连接 密钥', content: 'Xiaomi MiMo API 豆包语音 官方音色目录凭据 Base URL API Key Resource ID AK SK 测试连接' },
		{ section: 'ai', target: 'ai-title', title: '语言模型连接', description: '管理 OpenAI Compatible 服务配置与连接测试', keywords: 'AI 助手 语言模型 LLM', content: '配置名称 接口协议 Base URL API Key 模型 ID 启用此服务 默认服务 清除已保存 Key 测试连接 保存配置' },
		{ section: 'files', target: 'files-title', title: '输出位置与空间占用', description: '修改常用文件位置，查看占用并清理允许删除的内容', keywords: '文件与存储 路径 空间 清理', content: '音色库目录 生成输出目录 导出目录 项目目录 存储概览 打开位置 快捷清理' },
		{ section: 'advanced', target: 'advanced-title', title: '运行目录', description: '管理模型、缓存和日志位置，数据根目录保持只读', keywords: '高级设置 目录 路径', content: '数据根目录 模型目录 缓存目录 日志目录 密钥只保存在本机' },
		{ section: 'common', target: 'default-engine', title: '默认引擎', description: '设置新任务使用的语音合成引擎', keywords: 'tts indextts 引擎 合成' },
		{ section: 'common', target: 'default-voice', title: '默认音色', description: '设置新任务预先选择的音色', keywords: '声音 speaker voice' },
		{ section: 'common', target: 'default-lang', title: '默认语言', description: '中文、英文或自动识别', keywords: 'language 中文 英文' },
		{ section: 'common', target: 'default-format', title: '输出格式', description: 'WAV、MP3 或 FLAC', keywords: 'audio wav mp3 flac 文件' },
		{ section: 'common', target: 'device', title: '计算设备', description: '自动、Apple MPS 或 CPU', keywords: '性能 芯片 gpu cpu mps' },
		{ section: 'common', target: 'cloud', title: '云端引擎', description: '总开关，不影响本地引擎', keywords: 'cloud 开关 服务' },
		{ section: 'cloud', target: 'mimo-access-mode', title: 'Xiaomi MiMo API', description: '按量付费、Token Plan、Base URL、默认音色和 API Key', keywords: '小米 key 密钥 复刻 上传 token plan 按量付费', content: '接入方式 MiMo Preset 默认音色 生成前提醒上传到云端 清除已保存的 MiMo API Key 测试连接' },
		{ section: 'cloud', target: 'doubao-base', title: '豆包语音', description: '管理语音服务地址、Resource ID、API Key 和上传提醒', keywords: '火山 字节 seed tts icl key 密钥', content: '默认 TTS Resource ID 默认复刻 Resource ID 音色训练 ASR 上传前提醒 清除已保存的豆包 API Key 测试豆包 TTS' },
		{ section: 'cloud', target: 'volcengine-access-key-id', title: '官方音色目录凭据', description: 'AK / SK 只用于同步官方音色目录', keywords: '火山引擎 access secret credential 音色目录', content: 'Access Key ID Secret Access Key 登录创建 官方说明 测试目录权限 ListSpeakers' },
		{ section: 'ai', target: 'llm-name', title: '语言模型服务配置', description: '管理 OpenAI Compatible 连接、模型与默认服务', keywords: 'llm ai model api key', content: '配置名称 接口协议 Base URL API Key 模型 ID 获取模型 启用此服务 设为默认服务 清除已保存 Key 测试连接 保存配置 删除服务' },
		{ section: 'files', target: 'voice-dir', title: '音色库目录', description: '保存持久音色参考音频', keywords: 'voice folder path 路径' },
		{ section: 'files', target: 'output-dir', title: '生成输出目录', description: '保存语音生成结果', keywords: 'output result path 路径' },
		{ section: 'files', target: 'export-dir', title: '导出目录', description: '保存合并和打包结果', keywords: 'export merge path 路径' },
		{ section: 'files', target: 'project-dir', title: '项目目录', description: '保存项目级资产', keywords: 'project asset path 路径' },
		{ section: 'files', target: 'storage-overview', title: '存储清理', description: '查看占用并清理允许删除的内容', keywords: 'cache clean delete 文件 磁盘 空间' },
		{ section: 'advanced', target: 'data-dir', title: '数据根目录', description: '应用统一管理的数据位置（只读）', keywords: 'data database root path 路径 数据库' },
		{ section: 'advanced', target: 'model-dir', title: '模型目录', description: '本地模型权重位置', keywords: 'model weights path 路径' },
		{ section: 'advanced', target: 'cache-dir', title: '缓存目录', description: 'ASR、波形和对齐缓存位置', keywords: 'cache asr waveform align path 路径' },
		{ section: 'advanced', target: 'log-dir', title: '日志目录', description: '应用日志保存位置', keywords: 'log debug path 路径' }
	];

	let settings = $state<AppSettings | null>(null);
	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let activeSection = $state<SectionId>('common');
	let searchQuery = $state('');
	let searchFocused = $state(false);
	let loading = $state(true);
	let loadError = $state('');
	let serviceOnline = $state(false);
	let saveState = $state<SaveState>('saved');
	let saveMessage = $state('已保存');
	let savedSettingsSnapshot = $state('');
	let mimoApiKey = $state('');
	let clearMimoKey = $state(false);
	let doubaoApiKey = $state('');
	let clearDoubaoKey = $state(false);
	let volcengineAccessKeyId = $state('');
	let volcengineSecretAccessKey = $state('');
	let clearVolcengineAccessKeyId = $state(false);
	let clearVolcengineSecretAccessKey = $state(false);
	let mimoAccessMode = $state<MimoAccessMode>('payg');
	let cloudTestBusy = $state<CloudProviderId | null>(null);
	let cloudTestResults = $state<Partial<Record<CloudProviderId, CloudTestResult>>>({});

	const ttsEngines = $derived(engines.filter((engine) => !engine.manifest.capabilities.includes('speech_recognition')));
	const mimoVoiceOptions = $derived(
		ttsEngines
			.find((engine) => engine.manifest.engine_id === 'mimo-v2.5-tts-preset')
			?.manifest.parameter_schema.find((parameter) => parameter.key === 'mimo_voice')
			?.options ?? []
	);
	const mimoVoiceOptionIds = $derived(new Set(mimoVoiceOptions.map((option) => option.value)));
	const defaultEngineLabel = $derived(
		ttsEngines.find((engine) => engine.manifest.engine_id === settings?.default_engine_id)?.manifest.display_name ?? settings?.default_engine_id ?? '未设置'
	);
	const defaultEngine = $derived(ttsEngines.find((engine) => engine.manifest.engine_id === settings?.default_engine_id));
	const defaultEngineReady = $derived(defaultEngine?.state.status === 'loaded' || defaultEngine?.state.status === 'running');
	const defaultEngineStatus = $derived(defaultEngine ? engineStatusLabel(defaultEngine.state.status) : '未找到');
	const normalizedQuery = $derived(searchQuery.trim().toLocaleLowerCase('zh-CN'));
	const searchResults = $derived.by(() => {
		if (!normalizedQuery) return [];
		return searchItems
			.filter((item) => {
				const section = sections.find((candidate) => candidate.id === item.section);
				return `${section?.label ?? ''} ${section?.description ?? ''} ${item.title} ${item.description} ${item.keywords} ${item.content ?? ''}`
					.toLocaleLowerCase('zh-CN')
					.includes(normalizedQuery);
			})
			.slice(0, 8);
	});

	onMount(load);

	async function load() {
		loading = true;
		loadError = '';
		const [settingsResult, enginesResult, voicesResult, healthResult] = await Promise.allSettled([
			Api.settings(),
			Api.engines(),
			Api.voices({ offset: 0, limit: 2000 }),
			Api.health()
		]);
		if (settingsResult.status === 'fulfilled') {
			settings = settingsResult.value;
			mimoAccessMode = detectMimoAccessMode(settingsResult.value.mimo_base_url);
			savedSettingsSnapshot = JSON.stringify(settingsResult.value);
			saveState = 'saved';
			saveMessage = '已保存';
		}
		else loadError = settingsResult.reason instanceof Error ? settingsResult.reason.message : '设置加载失败';
		if (enginesResult.status === 'fulfilled') engines = enginesResult.value;
		if (voicesResult.status === 'fulfilled') voices = voicesResult.value;
		serviceOnline = healthResult.status === 'fulfilled' && healthResult.value.status === 'ok';
		loading = false;
	}

	function markDirty() {
		queueMicrotask(() => {
			if (!settings || saveState === 'saving') return;
			const hasSecretChanges = Boolean(
				mimoApiKey.trim() || clearMimoKey ||
				doubaoApiKey.trim() || clearDoubaoKey ||
				volcengineAccessKeyId.trim() || volcengineSecretAccessKey.trim() ||
				clearVolcengineAccessKeyId || clearVolcengineSecretAccessKey
			);
			const dirty = JSON.stringify(settings) !== savedSettingsSnapshot || hasSecretChanges;
			saveState = dirty ? 'dirty' : 'saved';
			saveMessage = dirty ? '有未保存更改' : '已保存';
		});
	}

	async function save(): Promise<boolean> {
		if (!settings || saveState === 'saving') return false;
		saveState = 'saving';
		saveMessage = '正在保存';
		try {
			settings = await Api.saveSettings(settings);
			if (mimoApiKey.trim() || clearMimoKey) {
				settings = await Api.saveMimoSecret({ api_key: mimoApiKey.trim() || null, clear: clearMimoKey });
				mimoApiKey = '';
				clearMimoKey = false;
			}
			if (doubaoApiKey.trim() || clearDoubaoKey) {
				settings = await Api.saveDoubaoSecret({ api_key: doubaoApiKey.trim() || null, clear: clearDoubaoKey });
				doubaoApiKey = '';
				clearDoubaoKey = false;
			}
			if (volcengineAccessKeyId.trim() || volcengineSecretAccessKey.trim() || clearVolcengineAccessKeyId || clearVolcengineSecretAccessKey) {
				settings = await Api.saveVolcengineDirectorySecret({
					access_key_id: volcengineAccessKeyId.trim() || null,
					secret_access_key: volcengineSecretAccessKey.trim() || null,
					clear_access_key_id: clearVolcengineAccessKeyId,
					clear_secret_access_key: clearVolcengineSecretAccessKey
				});
				volcengineAccessKeyId = '';
				volcengineSecretAccessKey = '';
				clearVolcengineAccessKeyId = false;
				clearVolcengineSecretAccessKey = false;
			}
			saveState = 'saved';
			saveMessage = '已保存';
			savedSettingsSnapshot = JSON.stringify(settings);
			return true;
		} catch (error) {
			saveState = 'error';
			saveMessage = error instanceof Error ? error.message : '保存失败，请重试';
			return false;
		}
	}

	function detectMimoAccessMode(baseUrl: string): MimoAccessMode {
		if (baseUrl.replace(/\/$/, '') === MIMO_PAYG_BASE_URL) return 'payg';
		if (/^https:\/\/token-plan-[a-z0-9-]+\.xiaomimimo\.com\/v1\/?$/i.test(baseUrl)) return 'token_plan';
		return 'custom';
	}

	function changeMimoAccessMode(event: Event) {
		if (!settings) return;
		mimoAccessMode = (event.currentTarget as HTMLSelectElement).value as MimoAccessMode;
		if (mimoAccessMode === 'payg') settings.mimo_base_url = MIMO_PAYG_BASE_URL;
		else if (mimoAccessMode === 'token_plan' && detectMimoAccessMode(settings.mimo_base_url) !== 'token_plan') {
			settings.mimo_base_url = MIMO_TOKEN_PLAN_CN_BASE_URL;
		}
		markDirty();
	}

	function cloudTestAvailable(provider: CloudProviderId): boolean {
		if (!settings) return false;
		if (provider === 'mimo') return !clearMimoKey && Boolean(mimoApiKey.trim() || settings.mimo_api_key_configured);
		if (provider === 'doubao') return !clearDoubaoKey && Boolean(doubaoApiKey.trim() || settings.doubao_api_key_configured);
		const hasAccessKey = !clearVolcengineAccessKeyId && Boolean(volcengineAccessKeyId.trim() || settings.volcengine_access_key_id_configured);
		const hasSecretKey = !clearVolcengineSecretAccessKey && Boolean(volcengineSecretAccessKey.trim() || settings.volcengine_secret_access_key_configured);
		return hasAccessKey && hasSecretKey;
	}

	function cloudTestNote(result: CloudConnectionTestResponse): string {
		if (result.provider === 'mimo') return `已验证 Base URL、Key 和模型列表${result.models_count === null ? '' : `（${result.models_count} 个模型）`}；本次不产生生成用量。`;
		if (result.provider === 'doubao') return '已验证豆包 TTS Key、Resource ID 与官方音色合成；本次可能产生极少量用量。';
		return '已验证 AK/SK 签名与官方音色目录读取权限；不写入本地目录缓存。';
	}

	async function testCloudConnection(provider: CloudProviderId) {
		if (cloudTestBusy || !cloudTestAvailable(provider)) return;
		cloudTestBusy = provider;
		cloudTestResults = { ...cloudTestResults, [provider]: undefined };
		const saved = await save();
		if (!saved) {
			cloudTestResults = { ...cloudTestResults, [provider]: { tone: 'error', message: '当前配置保存失败，未发起连接测试。' } };
			cloudTestBusy = null;
			return;
		}
		try {
			const result = await Api.testCloudConnection(provider);
			cloudTestResults = {
				...cloudTestResults,
				[provider]: { tone: 'success', message: result.message, note: cloudTestNote(result) }
			};
		} catch (error) {
			cloudTestResults = {
				...cloudTestResults,
				[provider]: { tone: 'error', message: error instanceof Error ? error.message : '连接测试失败，请检查配置。' }
			};
		} finally {
			cloudTestBusy = null;
		}
	}

	async function goTo(sectionId: SectionId, target?: string) {
		activeSection = sectionId;
		searchFocused = false;
		searchQuery = '';
		await tick();
		if (!target) {
			document.querySelector('.settings-content')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
			return;
		}
		const element = document.getElementById(target);
		const details = element?.closest('details');
		if (details) details.open = true;
		await tick();
		element?.scrollIntoView({ behavior: 'smooth', block: 'center' });
		if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLButtonElement) element.focus();
	}

	function onMimoKeyInput(event: Event) {
		mimoApiKey = (event.currentTarget as HTMLInputElement).value;
		if (mimoApiKey) clearMimoKey = false;
		markDirty();
	}

	function onDoubaoKeyInput(event: Event) {
		doubaoApiKey = (event.currentTarget as HTMLInputElement).value;
		if (doubaoApiKey) clearDoubaoKey = false;
		markDirty();
	}

	function onVolcengineAccessKeyInput(event: Event) {
		volcengineAccessKeyId = (event.currentTarget as HTMLInputElement).value;
		if (volcengineAccessKeyId) clearVolcengineAccessKeyId = false;
		markDirty();
	}

	function onVolcengineSecretInput(event: Event) {
		volcengineSecretAccessKey = (event.currentTarget as HTMLInputElement).value;
		if (volcengineSecretAccessKey) clearVolcengineSecretAccessKey = false;
		markDirty();
	}

	function sectionLabel(id: SectionId) {
		return sections.find((section) => section.id === id)?.label ?? id;
	}
</script>

<svelte:head><title>设置 - 声音工作台</title></svelte:head>

<main class="settings-page">
	<div class="settings-frame">
		<header class="settings-header">
			<div class="title-row">
				<h1>设置</h1>
			</div>

			<div class="settings-toolbar">
				<div class="search-shell" class:focused={searchFocused}>
					<Search size={19} aria-hidden="true" />
					<input
						type="search"
						placeholder="搜索标题、选项或说明"
						aria-label="搜索标题、选项或说明"
						bind:value={searchQuery}
						onfocus={() => (searchFocused = true)}
						onblur={() => setTimeout(() => (searchFocused = false), 120)}
						onkeydown={(event) => {
							if (event.key === 'Escape') {
								searchFocused = false;
								(event.currentTarget as HTMLInputElement).blur();
							}
						}}
					/>
					{#if searchFocused && normalizedQuery}
						<div class="search-results" id="settings-search-results" role="listbox">
							{#each searchResults as result}
								<button type="button" role="option" aria-selected="false" onclick={() => goTo(result.section, result.target)}>
									<span><strong>{result.title}</strong><small>{result.description}</small></span>
									<em>{sectionLabel(result.section)}</em>
									<ChevronRight size={15} />
								</button>
							{:else}
								<div class="no-result">没有找到相关设置</div>
							{/each}
						</div>
					{/if}
				</div>

				<div class="save-actions">
					<span class:dirty={saveState === 'dirty'} class:error={saveState === 'error'} class="save-status" role={saveState === 'error' ? 'alert' : 'status'}>
						<CheckCircle2 size={17} /> {saveMessage}
					</span>
					<button class="save-button" type="button" onclick={save} disabled={!settings || saveState === 'saving' || saveState === 'saved'}>
						<Save size={16} /> {saveState === 'saving' ? '保存中' : '保存更改'}
					</button>
				</div>
			</div>

			<nav class="section-nav" aria-label="设置分区">
				{#each sections as section}
					<button class:active={activeSection === section.id} type="button" onclick={() => goTo(section.id)}>{section.label}</button>
				{/each}
			</nav>
		</header>

		{#if loading}
			<div class="loading-state"><span></span><strong>正在读取设置</strong><small>连接本机服务并加载可用引擎</small></div>
		{:else if loadError || !settings}
			<div class="error-state">
				<strong>设置未能加载</strong>
				<p>{loadError || '本机设置服务没有返回数据。'}</p>
				<button type="button" onclick={load}>重新加载</button>
			</div>
		{:else}
			<div class="settings-content">
				{#if activeSection === 'common'}
					<section class="content-section" aria-labelledby="common-title" oninput={markDirty} onchange={markDirty}>
						<div class="section-heading"><div><h2 id="common-title">新任务的默认设置</h2><span>这些选项会在创建新任务时自动带入，之后仍可单独修改。</span></div></div>
						<div class="common-status-strip" aria-label="服务状态">
							<div class:ok={serviceOnline} class="common-status-item"><Gauge size={18} /><span><small>本机服务</small><strong>{serviceOnline ? '运行正常' : '连接异常'}</strong></span></div>
							<a class:ok={defaultEngineReady} class="common-status-item common-status-link" href="/engine-hub"><Mic2 size={18} /><span><small>默认引擎 · {defaultEngineStatus}</small><strong>{defaultEngineLabel}</strong></span><ChevronRight size={16} /></a>
						</div>
						<div class="setting-list common-settings">
							<SettingsRow icon={SlidersHorizontal} title="默认引擎" description="设置新任务使用的语音合成引擎" controlId="default-engine">
								<select id="default-engine" aria-label="默认引擎" bind:value={settings.default_engine_id}>{#each ttsEngines as engine}<option value={engine.manifest.engine_id}>{engine.manifest.display_name}</option>{/each}</select>
							</SettingsRow>
							<SettingsRow icon={Mic2} title="默认音色" description="设置新任务预先选择的音色" controlId="default-voice">
								<select id="default-voice" aria-label="默认音色" bind:value={settings.default_voice_id}><option value={null}>不指定</option>{#each voices as voice}<option value={voice.voice_id}>{voice.name}</option>{/each}</select>
							</SettingsRow>
							<SettingsRow icon={Languages} title="默认语言" description="用于文本处理和语音生成的语言提示" controlId="default-lang">
								<select id="default-lang" aria-label="默认语言" bind:value={settings.default_language}><option value="zh">中文</option><option value="en">英文</option><option value="auto">自动识别</option></select>
							</SettingsRow>
							<SettingsRow icon={FileOutput} title="输出格式" description="新任务默认保存的音频文件格式" controlId="default-format">
								<select id="default-format" aria-label="输出格式" bind:value={settings.default_output_format}><option value="wav">WAV · 无损</option><option value="mp3">MP3 · 体积较小</option><option value="flac">FLAC · 无损压缩</option></select>
							</SettingsRow>
							<SettingsRow icon={Cpu} title="计算设备" description="自动选择通常最稳妥；排查问题时可固定设备" controlId="device">
								<select id="device" aria-label="计算设备" bind:value={settings.device}><option value="auto">自动选择</option><option value="mps">Apple 芯片 MPS</option><option value="cpu">CPU</option></select>
							</SettingsRow>
							<SettingsRow icon={Cloud} title="云端引擎" description="启用已配置的云端合成服务，不影响本地引擎" controlId="cloud">
								<SettingsCheck id="cloud" bind:checked={settings.cloud_enabled}>{settings.cloud_enabled ? '已开启' : '未开启'}</SettingsCheck>
							</SettingsRow>
						</div>
						<div class="security-note"><ShieldCheck size={18} /><div><strong>密钥只保存在本机，不会回显</strong><span>云端服务和 AI 助手会分别显示“是否已配置”，不会把密钥原文返回页面。</span></div></div>
					</section>
				{:else if activeSection === 'cloud'}
					<section class="content-section" aria-labelledby="cloud-title" oninput={markDirty} onchange={markDirty}>
						<div class="section-heading"><div><h2 id="cloud-title">按服务管理连接</h2><span>密钥只保存在本机，页面只显示是否已配置。</span></div><div class="cloud-master"><SettingsCheck id="cloud-master" bind:checked={settings.cloud_enabled}>{settings.cloud_enabled ? '云端引擎已开启' : '云端引擎未开启'}</SettingsCheck></div></div>

						<div class="provider-stack">
							<details class="provider-card" open>
								<summary><span class="provider-icon mimo"><Volume2 size={18} /></span><span class="provider-copy"><strong>Xiaomi MiMo API</strong><small>按量付费优先；Token Plan 须遵守官方场景限制</small></span><span class:configured={settings.mimo_api_key_configured} class="provider-status">{settings.mimo_api_key_configured ? 'Key 已配置' : 'Key 未配置'}</span><ChevronRight class="provider-chevron" size={17} /></summary>
								<div class="provider-form">
								<SettingsField label="接入方式" controlId="mimo-access-mode">
									<select id="mimo-access-mode" value={mimoAccessMode} onchange={changeMimoAccessMode}><option value="payg">按量付费 API（推荐）</option><option value="token_plan">Token Plan 专属地址</option><option value="custom">自定义 / 已保存地址</option></select>
									{#snippet description()}选择后会自动填写对应 Base URL。{/snippet}
								</SettingsField>
								<SettingsField label="Base URL" controlId="mimo-base">
									<input id="mimo-base" type="url" bind:value={settings.mimo_base_url} readonly={mimoAccessMode === 'payg'} />
									{#snippet description()}{mimoAccessMode === 'token_plan' ? '已填中国区示例；请以订阅管理页显示的专属地址为准。' : mimoAccessMode === 'payg' ? '按量付费官方地址，使用 sk- 类型 Key。' : '连接测试仅允许 MiMo 官方 HTTPS 地址。'}{/snippet}
								</SettingsField>
								<SettingsField label="MiMo Preset 默认音色" controlId="mimo-voice">
									<select id="mimo-voice" bind:value={settings.mimo_default_voice}>
											{#if settings.mimo_default_voice && !mimoVoiceOptionIds.has(settings.mimo_default_voice)}
												<option value={settings.mimo_default_voice}>{settings.mimo_default_voice}（已保存）</option>
											{/if}
										{#each mimoVoiceOptions as option}<option value={option.value}>{option.label}</option>{/each}
									</select>
									{#snippet description()}只用于 MiMo 官方预置音色模型；生成页没有单独改选时使用。不会影响本地音色库、音色设计或音色复刻。<a href="https://platform.xiaomimimo.com/docs/zh-CN/usage-guide/speech-synthesis-v2.5" target="_blank" rel="noreferrer">查看官方音色说明</a>{/snippet}
								</SettingsField>
								<SettingsField label="API Key" controlId="mimo-key">
									<input id="mimo-key" type="password" value={mimoApiKey} oninput={onMimoKeyInput} placeholder={settings.mimo_api_key_configured ? '已配置；填写新 Key 可覆盖' : '未配置'} autocomplete="new-password" />
									{#snippet description()}sk- 与 tp- Key 不能混用；保存后不会回显。{/snippet}
								</SettingsField>
									<div class="provider-footer wide">
										<div class="check-cluster">
										<SettingsCheck id="mimo-upload-confirm" bind:checked={settings.mimo_voiceclone_confirm_upload}>音色复刻每次生成前提醒上传到云端</SettingsCheck>
										{#if settings.mimo_api_key_configured}<SettingsCheck id="mimo-clear" danger bind:checked={clearMimoKey} onchange={() => { if (clearMimoKey) mimoApiKey = ''; }}>清除已保存的 MiMo API Key</SettingsCheck>{/if}
										</div>
										<div class="connection-actions"><span>无生成请求，不产生生成用量。</span><button type="button" class="test-button" onclick={() => testCloudConnection('mimo')} disabled={Boolean(cloudTestBusy) || !cloudTestAvailable('mimo')}><Activity size={15} /> {cloudTestBusy === 'mimo' ? '测试中' : '测试连接'}</button></div>
									</div>
									{#if cloudTestResults.mimo}<div class:failed={cloudTestResults.mimo.tone === 'error'} class="connection-result wide" role={cloudTestResults.mimo.tone === 'error' ? 'alert' : 'status'}><CheckCircle2 size={16} /><div><strong>{cloudTestResults.mimo.message}</strong>{#if cloudTestResults.mimo.note}<small>{cloudTestResults.mimo.note}</small>{/if}</div></div>{/if}
								</div>
							</details>

							<details class="provider-card">
								<summary><span class="provider-icon doubao"><Cloud size={18} /></span><span class="provider-copy"><strong>豆包语音</strong><small>官方音色、云端复刻、训练与 ASR</small></span><span class:configured={settings.doubao_api_key_configured} class="provider-status">{settings.doubao_api_key_configured ? 'Key 已配置' : 'Key 未配置'}</span><ChevronRight class="provider-chevron" size={17} /></summary>
								<div class="provider-form">
								<SettingsField label="Base URL" controlId="doubao-base" wide><input id="doubao-base" type="url" bind:value={settings.doubao_base_url} /></SettingsField>
								<SettingsField label="默认 TTS Resource ID" controlId="doubao-tts-resource"><input id="doubao-tts-resource" bind:value={settings.doubao_default_tts_resource_id} /></SettingsField>
								<SettingsField label="默认复刻 Resource ID" controlId="doubao-icl-resource"><input id="doubao-icl-resource" bind:value={settings.doubao_default_icl_resource_id} /></SettingsField>
								<SettingsField label="豆包 API Key" controlId="doubao-key" wide>
									<input id="doubao-key" type="password" value={doubaoApiKey} oninput={onDoubaoKeyInput} placeholder={settings.doubao_api_key_configured ? '已配置；填写新 Key 可覆盖' : '未配置'} autocomplete="new-password" />
									{#snippet description()}<a href="https://console.volcengine.com/speech/new/overview?projectName=default" target="_blank" rel="noreferrer">登录获取</a> · <a href="https://docs.volcengine.com/docs/6561/1167802?lang=zh" target="_blank" rel="noreferrer">官方说明</a>{/snippet}
								</SettingsField>
									<div class="provider-footer wide">
										<div class="check-cluster">
										<SettingsCheck id="doubao-upload-confirm" bind:checked={settings.doubao_upload_confirm}>音色训练或 ASR 上传前提醒</SettingsCheck>
										{#if settings.doubao_api_key_configured}<SettingsCheck id="doubao-clear" danger bind:checked={clearDoubaoKey} onchange={() => { if (clearDoubaoKey) doubaoApiKey = ''; }}>清除已保存的豆包 API Key</SettingsCheck>{/if}
										</div>
										<div class="connection-actions"><span>会合成 1 个字并丢弃音频，可能产生极少量用量；不验证复刻、训练或 Seed Audio。</span><button type="button" class="test-button" onclick={() => testCloudConnection('doubao')} disabled={Boolean(cloudTestBusy) || !cloudTestAvailable('doubao')}><Activity size={15} /> {cloudTestBusy === 'doubao' ? '测试中' : '测试豆包 TTS'}</button></div>
									</div>
									{#if cloudTestResults.doubao}<div class:failed={cloudTestResults.doubao.tone === 'error'} class="connection-result wide" role={cloudTestResults.doubao.tone === 'error' ? 'alert' : 'status'}><CheckCircle2 size={16} /><div><strong>{cloudTestResults.doubao.message}</strong>{#if cloudTestResults.doubao.note}<small>{cloudTestResults.doubao.note}</small>{/if}</div></div>{/if}

									<div class="subsection-title wide">
										<div>
											<span class="subsection-name"><strong>官方音色目录凭据</strong><span class:configured={settings.volcengine_access_key_id_configured && settings.volcengine_secret_access_key_configured} class="provider-status">{settings.volcengine_access_key_id_configured && settings.volcengine_secret_access_key_configured ? 'AK / SK 已配置' : 'AK / SK 未完整配置'}</span></span>
											<small>AK / SK 只用于同步音色目录，不替代上方豆包 API Key。<a href="https://console.volcengine.com/iam/keymanage/" target="_blank" rel="noreferrer">登录创建</a> · <a href="https://www.volcengine.com/docs/6291/65568?lang=zh" target="_blank" rel="noreferrer">官方说明</a></small>
										</div>
									</div>
								<SettingsField label="Access Key ID" controlId="volcengine-access-key-id"><input id="volcengine-access-key-id" type="password" value={volcengineAccessKeyId} oninput={onVolcengineAccessKeyInput} placeholder={settings.volcengine_access_key_id_configured ? '已配置；填写新值可覆盖' : '未配置'} autocomplete="new-password" /></SettingsField>
								<SettingsField label="Secret Access Key" controlId="volcengine-secret-access-key"><input id="volcengine-secret-access-key" type="password" value={volcengineSecretAccessKey} oninput={onVolcengineSecretInput} placeholder={settings.volcengine_secret_access_key_configured ? '已配置；填写新值可覆盖' : '未配置'} autocomplete="new-password" /></SettingsField>
									<div class="provider-footer wide">
										<div class="check-cluster">
										{#if settings.volcengine_access_key_id_configured}<SettingsCheck id="volcengine-clear-access-key-id" danger bind:checked={clearVolcengineAccessKeyId} onchange={() => { if (clearVolcengineAccessKeyId) volcengineAccessKeyId = ''; }}>清除 Access Key ID</SettingsCheck>{/if}
										{#if settings.volcengine_secret_access_key_configured}<SettingsCheck id="volcengine-clear-secret-access-key" danger bind:checked={clearVolcengineSecretAccessKey} onchange={() => { if (clearVolcengineSecretAccessKey) volcengineSecretAccessKey = ''; }}>清除 Secret Access Key</SettingsCheck>{/if}
										</div>
										<div class="connection-actions"><span>只读验证 ListSpeakers，不同步、不改写本地音色目录。</span><button type="button" class="test-button" onclick={() => testCloudConnection('volcengine_directory')} disabled={Boolean(cloudTestBusy) || !cloudTestAvailable('volcengine_directory')}><Activity size={15} /> {cloudTestBusy === 'volcengine_directory' ? '测试中' : '测试目录权限'}</button></div>
									</div>
									{#if cloudTestResults.volcengine_directory}<div class:failed={cloudTestResults.volcengine_directory.tone === 'error'} class="connection-result wide" role={cloudTestResults.volcengine_directory.tone === 'error' ? 'alert' : 'status'}><CheckCircle2 size={16} /><div><strong>{cloudTestResults.volcengine_directory.message}</strong>{#if cloudTestResults.volcengine_directory.note}<small>{cloudTestResults.volcengine_directory.note}</small>{/if}</div></div>{/if}
								</div>
							</details>
						</div>
					</section>
				{:else if activeSection === 'ai'}
					<section class="content-section" aria-labelledby="ai-title">
						<div class="section-heading"><div><h2 id="ai-title">语言模型连接</h2><span>这里只管理连接；配置成功不代表业务功能已经接入。</span></div></div>
						<LlmSettings />
					</section>
				{:else if activeSection === 'files'}
					<section class="content-section" aria-labelledby="files-title" oninput={markDirty} onchange={markDirty}>
						<div class="section-heading"><div><h2 id="files-title">输出位置与空间占用</h2><span>常用文件位置可以直接修改；清理操作只针对后端白名单。</span></div></div>
						<div class="setting-list path-list">
							<SettingsRow icon={Library} title="音色库目录" description="持久保存已注册的自定义音色" controlId="voice-dir"><input id="voice-dir" bind:value={settings.voice_dir} /></SettingsRow>
							<SettingsRow icon={FolderOpen} title="生成输出目录" description="保存单条、长文本和批量生成结果" controlId="output-dir"><input id="output-dir" bind:value={settings.output_dir} /></SettingsRow>
							<SettingsRow icon={FileOutput} title="导出目录" description="保存合并、打包和音频工具导出结果" controlId="export-dir"><input id="export-dir" bind:value={settings.export_dir} /></SettingsRow>
							<SettingsRow icon={FolderCog} title="项目目录" description="保存视频和长项目相关资产" controlId="project-dir"><input id="project-dir" bind:value={settings.project_dir} /></SettingsRow>
						</div>
						<div id="storage-overview"><StorageSettings /></div>
					</section>
				{:else if activeSection === 'advanced'}
					<section class="content-section" aria-labelledby="advanced-title" oninput={markDirty} onchange={markDirty}>
						<div class="section-heading"><div><h2 id="advanced-title">运行目录</h2><span>日常使用通常不需要修改。数据根目录只读，避免不完整迁移。</span></div></div>
						<div class="setting-list path-list">
							<SettingsRow icon={ShieldCheck} title="数据根目录" description="配置数据库和受管数据的根位置；迁移需使用专用向导" controlId="data-dir"><input id="data-dir" value={settings.data_dir} readonly /></SettingsRow>
							<SettingsRow icon={MonitorCog} title="模型目录" description="本地模型权重的保存位置" controlId="model-dir"><input id="model-dir" bind:value={settings.model_dir} /></SettingsRow>
							<SettingsRow icon={HardDrive} title="缓存目录" description="ASR 源音频、波形与对齐缓存" controlId="cache-dir"><input id="cache-dir" bind:value={settings.cache_dir} /></SettingsRow>
							<SettingsRow icon={FolderCog} title="日志目录" description="应用运行日志；外部引擎可能另有日志" controlId="log-dir"><input id="log-dir" bind:value={settings.log_dir} /></SettingsRow>
						</div>
						<div class="security-note"><ShieldCheck size={18} /><div><strong>密钥只保存在本机</strong><span>设置接口只返回“是否已配置”，不会返回密钥原文。</span></div></div>
					</section>
				{/if}
			</div>
		{/if}
	</div>
</main>

<style>
	:global(body) { background: #0b0f14; }
	.settings-page {
		--settings-control-height: 34px;
		--settings-control-touch-height: 44px;
		--settings-control-radius: 7px;
		--settings-control-font-size: 12px;
		min-height: 100%;
		background: #0b0f14;
		color: #edf1f5;
	}
	.settings-frame { width: min(100%, 1320px); margin: 0 auto; padding: 22px 30px 48px; }
	.settings-header { margin-bottom: 18px; }
	.title-row,
	.settings-toolbar,
	.save-actions,
	.save-status,
	.section-heading,
	.provider-card summary,
	.provider-status,
	.check-cluster,
	.subsection-title,
	.security-note,
	.common-status-item { display: flex; align-items: center; }
	.title-row { justify-content: space-between; gap: 14px; margin-bottom: 16px; }
	h1 { margin: 0; color: #f4f6f8; font-size: 27px; font-weight: 720; letter-spacing: -.035em; }

	.settings-toolbar { display: grid; grid-template-columns: minmax(320px, 1fr) auto; gap: 18px; }
	.search-shell { position: relative; display: grid; grid-template-columns: 22px minmax(0, 1fr); align-items: center; min-height: 42px; padding: 0 13px; border: 1px solid #2b3440; border-radius: 9px; background: rgba(22, 27, 35, .92); color: #8b96a5; box-shadow: inset 0 1px 0 rgba(255, 255, 255, .025); }
	.search-shell.focused { border-color: #397fca; box-shadow: 0 0 0 3px rgba(47, 133, 237, .11); }
	.search-shell input { min-height: 40px; padding: 0 3px; border: 0; background: transparent; color: #edf1f5; outline: 0; font-size: 13px; }
	.search-shell input::placeholder { color: #687483; }
	.search-results { position: absolute; z-index: 20; top: calc(100% + 8px); left: 0; right: 0; overflow: hidden; border: 1px solid #2a3441; border-radius: 11px; background: rgba(17, 22, 30, .98); box-shadow: 0 18px 48px rgba(0, 0, 0, .42); }
	.search-results button { display: grid; grid-template-columns: minmax(0, 1fr) auto 18px; align-items: center; gap: 10px; width: 100%; min-height: 58px; padding: 9px 13px; border: 0; border-bottom: 1px solid rgba(148, 163, 184, .1); background: transparent; color: #dfe5ec; text-align: left; }
	.search-results button:last-child { border-bottom: 0; }
	.search-results button:hover,
	.search-results button:focus-visible { background: rgba(47, 133, 237, .1); outline: 0; }
	.search-results button span { display: grid; gap: 3px; }
	.search-results strong { font-size: 12px; }
	.search-results small { color: #7f8a99; font-size: 10px; }
	.search-results em { color: #689fdc; font-size: 10px; font-style: normal; }
	.no-result { padding: 28px; color: #7e8997; font-size: 12px; text-align: center; }

	.save-actions { justify-content: flex-end; gap: 12px; }
	.save-status { gap: 6px; max-width: 280px; color: #57cd8b; font-size: 11px; }
	.save-status.dirty { color: #e7bc61; }
	.save-status.error { color: #ff9297; }
	.save-button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-width: 116px; min-height: 42px; padding: 0 14px; border: 1px solid #3389ee; border-radius: 8px; background: #277de2; color: white; font-size: 12px; font-weight: 670; box-shadow: inset 0 1px 0 rgba(255, 255, 255, .14), 0 6px 18px rgba(17, 79, 152, .18); }
	.save-button:hover:not(:disabled) { background: #348bf0; }
	.save-button:disabled { cursor: not-allowed; opacity: .66; }

	.section-nav { display: flex; gap: 6px; overflow-x: auto; margin-top: 16px; border-bottom: 1px solid rgba(148, 163, 184, .14); scrollbar-width: none; }
	.section-nav::-webkit-scrollbar { display: none; }
	.section-nav button { position: relative; flex: 0 0 auto; min-height: 40px; padding: 0 14px; border: 0; background: transparent; color: #8994a2; font-size: 12px; white-space: nowrap; }
	.section-nav button::after { content: ''; position: absolute; right: 15px; bottom: -1px; left: 15px; height: 2px; border-radius: 2px 2px 0 0; background: transparent; }
	.section-nav button:hover { color: #dce2e9; }
	.section-nav button.active { color: #f1f4f7; }
	.section-nav button.active::after { background: #2f85ed; }

	.settings-content { scroll-margin-top: 20px; }
	.content-section { display: grid; gap: 14px; }
	.section-heading { justify-content: space-between; gap: 18px; min-height: 48px; padding: 0 3px; }
	.section-heading > div { display: grid; gap: 3px; }
	.section-heading h2 { margin: 0; color: #f0f3f6; font-size: 18px; font-weight: 690; letter-spacing: -.02em; }
	.section-heading span { color: #7f8a98; font-size: 11px; line-height: 1.45; }
	.setting-list { overflow: hidden; border: 1px solid rgba(148, 163, 184, .16); border-radius: 11px; background: rgba(17, 22, 30, .82); }
	.setting-list :global(input:not([type='checkbox']):not([type='radio'])),
	.setting-list :global(select) { height: var(--settings-control-height); min-height: var(--settings-control-height); border-color: #2c3541; border-radius: var(--settings-control-radius); background: #0d1218; font-size: var(--settings-control-font-size); }
	.common-settings :global(select) { width: min(100%, 320px); height: var(--settings-control-height); min-height: var(--settings-control-height); padding-inline: 11px 30px; border-radius: var(--settings-control-radius); font-size: var(--settings-control-font-size); }
	.path-list :global(input) { max-width: 520px !important; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 11px; }
	.path-list :global(input[readonly]) { color: #7e8997; cursor: text; }
	.cloud-master { flex: 0 0 auto; }

	.common-status-strip { display: grid; grid-template-columns: minmax(180px, .56fr) minmax(0, 1fr); overflow: hidden; border: 1px solid rgba(148, 163, 184, .16); border-radius: 10px; background: rgba(17, 22, 30, .72); }
	.common-status-item { gap: 9px; min-width: 0; min-height: 50px; padding: 8px 12px; color: #7e8b9b; }
	.common-status-item:first-child { border-right: 1px solid rgba(148, 163, 184, .13); }
	.common-status-item.ok { color: #58ce8c; }
	.common-status-item span { display: grid; gap: 3px; min-width: 0; }
	.common-status-item small { color: #727f8e; font-size: 10px; }
	.common-status-item strong { overflow: hidden; color: #dce2e8; font-size: 12px; font-weight: 640; text-overflow: ellipsis; white-space: nowrap; }
	.common-status-link { transition: background 140ms ease; }
	.common-status-link:hover { background: rgba(28, 40, 55, .82); }
	.common-status-link > :global(svg:last-child) { margin-left: auto; color: #657488; }
	.provider-stack { display: grid; gap: 10px; }
	.provider-card { overflow: hidden; border: 1px solid rgba(148, 163, 184, .16); border-radius: 11px; background: rgba(17, 22, 30, .82); }
	.provider-card summary { min-height: 62px; gap: 11px; padding: 10px 14px; cursor: pointer; list-style: none; }
	.provider-card summary::-webkit-details-marker { display: none; }
	.provider-card summary :global(.provider-chevron) { color: #718090; transition: transform 150ms ease; }
	.provider-card[open] summary :global(.provider-chevron) { transform: rotate(90deg); }
	.provider-icon { display: grid; width: 32px; height: 32px; place-items: center; flex: none; border: 1px solid #315d78; border-radius: 8px; background: #142733; color: #8dd5f0; }
	.provider-icon.mimo { border-color: #39619a; background: #15233a; color: #8db9f4; }
	.provider-copy { display: grid; flex: 1; gap: 4px; }
	.provider-copy strong { color: #edf1f4; font-size: 13px; }
	.provider-copy small { color: #7b8796; font-size: 10px; }
	.provider-status { justify-content: center; min-height: 25px; padding: 0 8px; border: 1px solid #5b4920; border-radius: 999px; background: #251e0f; color: #e9c36d; font-size: 10px; white-space: nowrap; }
	.provider-status.configured { border-color: #276346; background: #11261d; color: #78dbaa; }
	.provider-form { display: grid; grid-template-columns: 1fr 1fr; gap: 11px 14px; padding: 14px; border-top: 1px solid rgba(148, 163, 184, .13); background: rgba(8, 12, 18, .22); }
	.subsection-title a { color: #75ade8; }
	.subsection-title a:hover { text-decoration: underline; text-underline-offset: 3px; }
	.wide { grid-column: 1 / -1; }
	.check-cluster { flex-wrap: wrap; gap: 7px; }
	.subsection-title { gap: 12px; margin-top: 2px; padding-top: 12px; border-top: 1px solid rgba(148, 163, 184, .12); }
	.subsection-title > div { display: grid; gap: 4px; }
	.subsection-name { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
	.subsection-title strong { color: #dfe5eb; font-size: 12px; }
	.subsection-title small { color: #758190; font-size: 10px; }
	.subsection-title .provider-status { min-height: 20px; padding-inline: 6px; font-size: 9px; }
	.provider-footer { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, auto); align-items: center; gap: 10px 16px; }
	.provider-footer .check-cluster { min-width: 0; }
	.connection-actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; min-width: 0; }
	.connection-actions > span { color: #758190; font-size: 10px; line-height: 1.5; text-align: right; }
	.test-button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 34px; padding: 0 11px; border: 1px solid #315b88; border-radius: 7px; background: #142438; color: #a9cef6; font-size: 11px; font-weight: 650; white-space: nowrap; }
	.test-button:hover:not(:disabled) { border-color: #3d79b8; background: #19304a; color: #d4e8ff; }
	.test-button:disabled { cursor: not-allowed; opacity: .46; }
	.connection-result { display: flex; align-items: flex-start; gap: 9px; padding: 11px 12px; border: 1px solid rgba(56, 158, 105, .28); border-radius: 9px; background: rgba(27, 91, 57, .1); color: #68d69a; }
	.connection-result.failed { border-color: rgba(222, 91, 98, .3); background: rgba(112, 34, 42, .12); color: #f18e96; }
	.connection-result > div { display: grid; gap: 3px; }
	.connection-result strong { color: #cfe9da; font-size: 11px; line-height: 1.45; }
	.connection-result.failed strong { color: #f4c3c6; }
	.connection-result small { color: #789b87; font-size: 10px; line-height: 1.5; }
	.connection-result.failed small { color: #a77b80; }

	.security-note { gap: 9px; padding: 11px 13px; border: 1px solid rgba(56, 158, 105, .22); border-radius: 10px; background: rgba(27, 91, 57, .09); color: #67d699; }
	.security-note div { display: grid; gap: 3px; }
	.security-note strong { color: #cfe9da; font-size: 12px; }
	.security-note span { color: #769786; font-size: 10px; }

	.loading-state,
	.error-state { display: grid; place-items: center; align-content: center; min-height: 360px; border: 1px solid rgba(148, 163, 184, .14); border-radius: 14px; color: #808c9a; text-align: center; }
	.loading-state span { width: 26px; height: 26px; margin-bottom: 12px; border: 2px solid rgba(148, 163, 184, .2); border-top-color: #2f85ed; border-radius: 50%; animation: spin .8s linear infinite; }
	.loading-state strong,
	.error-state strong { color: #dfe4ea; font-size: 13px; }
	.loading-state small { margin-top: 4px; font-size: 10px; }
	.error-state p { margin: 7px 0 14px; font-size: 11px; }
	.error-state button { min-height: var(--settings-control-height); padding: 0 12px; border: 1px solid #2f85ed; border-radius: var(--settings-control-radius); background: #226fc8; color: white; }
	@keyframes spin { to { transform: rotate(360deg); } }

	@media (min-width: 901px) {
		:global(.main:has(.settings-page) > .topbar) { display: none; }
	}

	@media (max-width: 1050px) {
		.settings-frame { padding-inline: 22px; }
		.settings-toolbar { grid-template-columns: 1fr; gap: 12px; }
		.save-actions { justify-content: space-between; }
		.provider-footer { grid-template-columns: 1fr; }
		.provider-footer .connection-actions { justify-content: space-between; }
		.provider-footer .connection-actions > span { flex: 1; text-align: left; }
	}

	@media (max-width: 720px) {
		.settings-frame { padding: 18px 16px 76px; }
		.settings-header { margin-bottom: 16px; }
		.title-row { margin-bottom: 14px; }
		h1 { font-size: 25px; }
		.search-shell { min-height: 44px; padding-inline: 12px; }
		.search-shell input { min-height: 42px; }
		.save-actions { align-items: stretch; }
		.save-status { min-width: 0; font-size: 11px; }
		.save-button { min-width: 112px; min-height: 44px; padding-inline: 12px; }
		.section-nav { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0 5px; overflow: visible; margin-top: 14px; }
		.section-nav button { min-width: 0; min-height: 42px; padding-inline: 4px; font-size: 11px; }
		.section-nav button::after { right: 11px; left: 11px; }
		.section-heading { align-items: stretch; flex-direction: column; }
		.section-heading h2 { font-size: 17px; }
		.cloud-master { align-self: flex-start; }
		.common-status-strip { grid-template-columns: 1fr; }
		.common-status-item:first-child { border-right: 0; border-bottom: 1px solid rgba(148, 163, 184, .13); }
		.provider-card summary { min-height: 60px; padding-inline: 12px; }
		.provider-copy small { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
		.provider-form { grid-template-columns: 1fr; padding: 12px; }
		.wide { grid-column: auto; }
		.setting-list :global(input:not([type='checkbox']):not([type='radio'])),
		.setting-list :global(select) { height: 44px; min-height: 44px; }
		.common-settings :global(select) { width: 100%; }
		.connection-actions { align-items: flex-start; flex-direction: column; gap: 7px; }
		.provider-footer .connection-actions { align-items: center; flex-direction: row; }
		.test-button { min-height: var(--settings-control-touch-height); }
		.error-state button { min-height: var(--settings-control-touch-height); }
		.check-cluster { align-items: stretch; }
		.security-note { align-items: flex-start; }
	}

	@media (max-width: 430px) {
		.settings-frame { padding-inline: 13px; }
		.save-actions { gap: 8px; }
		.save-status { max-width: 150px; }
		.provider-status { display: none; }
		.subsection-title .provider-status { display: inline-flex; }
		.provider-copy small { max-width: 160px; }
		.provider-footer .connection-actions { align-items: flex-start; flex-wrap: wrap; }
		.provider-footer .connection-actions > span { flex-basis: 100%; }
		.provider-footer .test-button { margin-left: auto; }
	}

	@media (prefers-reduced-motion: reduce) {
		*, *::before, *::after { scroll-behavior: auto !important; }
		.loading-state span { animation: none; }
		.provider-card summary :global(.provider-chevron) { transition: none; }
	}
</style>
