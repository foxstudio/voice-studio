<script lang="ts">
	import { Api } from '$lib/api';
	import type { AppSettings, EngineDetail, StorageAudit, StorageLocation, VoiceAsset } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { Database, ExternalLink, FolderOpen, HardDrive, RefreshCw, Save, Trash2 } from 'lucide-svelte';
	import { onMount } from 'svelte';

	let settings = $state<AppSettings | null>(null);
	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let storageAudit = $state<StorageAudit | null>(null);
	let saved = $state('');
	let storageMessage = $state('');
	let storageBusy = $state(false);
	let cleanupBusy = $state('');
	let openingBusy = $state('');
	let mimoApiKey = $state('');
	let clearMimoKey = $state(false);
	let doubaoApiKey = $state('');
	let clearDoubaoKey = $state(false);
	let volcengineAccessKeyId = $state('');
	let volcengineSecretAccessKey = $state('');
	let clearVolcengineAccessKeyId = $state(false);
	let clearVolcengineSecretAccessKey = $state(false);

	const ttsEngines = $derived(engines.filter((engine) => !engine.manifest.capabilities.includes('speech_recognition')));
	const cleanupLocations = $derived((storageAudit?.locations ?? []).filter((location) => location.cleanup_key));

	onMount(() => {
		load();
	});

	async function load() {
		const [s, e, v, storage] = await Promise.all([
			Api.settings(),
			Api.engines(),
			Api.voices({ offset: 0, limit: 2000 }),
			Api.settingsStorage()
		]);
		settings = s;
		engines = e;
		voices = v;
		storageAudit = storage;
	}

	async function save() {
		if (!settings) return;
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
		if (
			volcengineAccessKeyId.trim() ||
			volcengineSecretAccessKey.trim() ||
			clearVolcengineAccessKeyId ||
			clearVolcengineSecretAccessKey
		) {
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
		saved = '已保存';
		setTimeout(() => (saved = ''), 1600);
		await refreshStorage();
	}

	async function refreshStorage() {
		storageBusy = true;
		try {
			storageAudit = await Api.settingsStorage();
		} finally {
			storageBusy = false;
		}
	}

	async function cleanupLocation(location: StorageLocation) {
		if (!location.cleanup_key) return;
		const warning =
			location.cleanup_risk === 'high'
				? '这会删除 ASR 源音频，历史文字仍会保留，但之后无法基于源音频补时间戳。确认继续？'
				: location.cleanup_risk === 'medium'
					? '这会删除日志类文件，排查历史问题时可能用不到这些日志。确认继续？'
					: `确认清理「${location.label}」？`;
		if (!window.confirm(warning)) return;
		cleanupBusy = location.cleanup_key;
		try {
			const result = await Api.cleanupSettingsStorage([location.cleanup_key]);
			storageMessage = `已清理 ${formatBytes(result.removed_bytes)}`;
			await refreshStorage();
		} finally {
			cleanupBusy = '';
			setTimeout(() => (storageMessage = ''), 2200);
		}
	}

	async function openLocation(location: StorageLocation) {
		openingBusy = location.key;
		try {
			const result = await Api.openSettingsStorageLocation(location.key);
			storageMessage = `已打开：${result.path}`;
		} catch (e) {
			storageMessage = (e as Error).message || '打开目录失败';
		} finally {
			openingBusy = '';
			setTimeout(() => (storageMessage = ''), 2200);
		}
	}

	function formatBytes(value: number | null | undefined) {
		const bytes = value ?? 0;
		if (bytes < 1024) return `${bytes} B`;
		const units = ['KB', 'MB', 'GB', 'TB'];
		let size = bytes / 1024;
		let unit = units[0];
		for (let i = 1; i < units.length && size >= 1024; i += 1) {
			size /= 1024;
			unit = units[i];
		}
		return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${unit}`;
	}

	function fileCountLabel(location: StorageLocation) {
		const suffix = location.truncated ? '+' : '';
		return `${location.file_count}${suffix} 个文件`;
	}

	function riskLabel(risk: string | null) {
		if (risk === 'high') return '谨慎';
		if (risk === 'medium') return '日志';
		if (risk === 'low') return '低风险';
		return '只读';
	}

	const help = [
		{ title: 'MiMo Token Plan 怎么用', body: '先开启云端引擎，再填写 Token Plan 专属 base URL 和 API Key。保存后，引擎中心会显示 MiMo 的预置音色、音色设计、音色复刻和 ASR；没有 key 时会提示不可用，不影响本地 IndexTTS 和 OmniVoice。' },
		{ title: '豆包语音怎么用', body: '先开启云端引擎，再配置豆包 API Key。豆包首期规划拆成官方音色 TTS、云端复刻音色、音色训练/查询和 ASR，不把所有能力塞进一个入口。' },
		{ title: '密钥安全', body: 'API Key 只保存在本地后端设置表里，前端只显示是否已配置，不会把 key 回填到输入框。需要换 key 时直接填新的；需要删除时勾选清除。' },
		{ title: '目录设置', body: '声音目录保存参考音频，输出目录保存生成结果，导出目录保存合并/打包结果，缓存目录保存 ASR 源音频和对齐日志。' },
		{ title: '缓存清理', body: '诊断音频、对齐缓存和日志可以从设置页清理；ASR 源音频属于历史转写的参考材料，清理前会二次确认。' }
	];
</script>

<svelte:head><title>设置 - 声音工作台</title></svelte:head>

<main class="page settings-page">
	<div class="page-head">
		<div>
			<h1>设置</h1>
			<p class="muted">模型、输出、缓存、本地/云端策略和默认参数</p>
		</div>
		<div class="row">
			<HelpDrawer title="设置" sections={help} />
			<button class="btn" onclick={refreshStorage} disabled={storageBusy}>
				<RefreshCw size={15} /> 刷新存储
			</button>
			<button class="btn primary" onclick={save} disabled={!settings}>
				<Save size={15} /> 保存
			</button>
		</div>
	</div>

	{#if settings}
		<div class="settings-grid">
			<section class="panel stack">
				<div class="section-title">
					<h2>默认行为</h2>
					<span class="badge">TTS</span>
				</div>
				<div class="field">
					<label for="default-engine">默认引擎</label>
					<select id="default-engine" bind:value={settings.default_engine_id}>
						{#each ttsEngines as engine}<option value={engine.manifest.engine_id}>{engine.manifest.display_name}</option>{/each}
					</select>
				</div>
				<div class="field">
					<label for="default-voice">默认音色</label>
					<select id="default-voice" bind:value={settings.default_voice_id}>
						<option value={null}>不指定</option>
						{#each voices as voice}<option value={voice.voice_id}>{voice.name}</option>{/each}
					</select>
				</div>
				<div class="field">
					<label for="default-lang">默认语言</label>
					<select id="default-lang" bind:value={settings.default_language}>
						<option value="zh">中文</option>
						<option value="en">英文</option>
						<option value="auto">自动</option>
					</select>
				</div>
				<div class="field">
					<label for="default-format">默认格式</label>
					<select id="default-format" bind:value={settings.default_output_format}>
						<option value="wav">WAV</option>
						<option value="mp3">MP3</option>
						<option value="flac">FLAC</option>
					</select>
				</div>
				<div class="field">
					<label for="device">设备</label>
					<select id="device" bind:value={settings.device}>
						<option value="auto">自动</option>
						<option value="mps">Apple 芯片 MPS</option>
						<option value="cpu">CPU</option>
					</select>
				</div>
				<label class="check-row" for="cloud"><input id="cloud" type="checkbox" bind:checked={settings.cloud_enabled} /> 启用云端引擎</label>
			</section>

			<section class="panel stack">
				<div class="section-title">
					<h2>MiMo Token Plan</h2>
					<span class="badge" class:ok={settings.mimo_api_key_configured} class:warn={!settings.mimo_api_key_configured}>
						{settings.mimo_api_key_configured ? 'Key 已配置' : 'Key 未配置'}
					</span>
				</div>
				<div class="field">
					<label for="mimo-base">专属 Base URL</label>
					<input id="mimo-base" bind:value={settings.mimo_base_url} />
				</div>
				<div class="field">
					<label for="mimo-voice">默认 MiMo 音色</label>
					<input id="mimo-voice" bind:value={settings.mimo_default_voice} placeholder="例如 mimo_default 或官方音色名" />
				</div>
				<label class="check-row" for="mimo-upload-confirm">
					<input id="mimo-upload-confirm" type="checkbox" bind:checked={settings.mimo_voiceclone_confirm_upload} /> MiMo 音色复刻每次生成前提醒云端上传
				</label>
				<div class="field">
					<label for="mimo-key">API Key（不会回显）</label>
					<input id="mimo-key" type="password" bind:value={mimoApiKey} placeholder={settings.mimo_api_key_configured ? '已配置；填写新 key 可覆盖' : '未配置'} />
				</div>
				<label class="check-row" for="mimo-clear"><input id="mimo-clear" type="checkbox" bind:checked={clearMimoKey} /> 清除已保存的 MiMo API Key</label>
				<p class="muted">Token Plan 专属入口默认使用 https://token-plan-cn.xiaomimimo.com/v1。</p>
			</section>

			<section class="panel stack">
				<div class="section-title">
					<h2>豆包语音</h2>
					<span class="badge" class:ok={settings.doubao_api_key_configured} class:warn={!settings.doubao_api_key_configured}>
						{settings.doubao_api_key_configured ? 'Key 已配置' : 'Key 未配置'}
					</span>
				</div>
				<div class="field">
					<label for="doubao-base">Base URL</label>
					<input id="doubao-base" bind:value={settings.doubao_base_url} />
				</div>
				<div class="field">
					<label for="doubao-tts-resource">默认 TTS Resource ID</label>
					<input id="doubao-tts-resource" bind:value={settings.doubao_default_tts_resource_id} />
				</div>
				<div class="field">
					<label for="doubao-icl-resource">默认复刻 Resource ID</label>
					<input id="doubao-icl-resource" bind:value={settings.doubao_default_icl_resource_id} />
				</div>
				<label class="check-row" for="doubao-upload-confirm">
					<input id="doubao-upload-confirm" type="checkbox" bind:checked={settings.doubao_upload_confirm} /> 豆包音色训练/ASR 上传前提醒
				</label>
				<div class="field">
					<div class="credential-label">
						<label for="doubao-key">API Key（不会回显）</label>
						<span class="credential-links">
							<a href="https://console.volcengine.com/speech/new/overview?projectName=default" target="_blank" rel="noreferrer">登录获取 <ExternalLink size={12} /></a>
							<a href="https://docs.volcengine.com/docs/6561/1167802?lang=zh" target="_blank" rel="noreferrer">官方说明</a>
						</span>
					</div>
					<input id="doubao-key" type="password" bind:value={doubaoApiKey} placeholder={settings.doubao_api_key_configured ? '已配置；填写新 key 可覆盖' : '未配置'} />
					<small>登录后进入“API Key 管理”，新建或复制用于豆包语音调用的 API Key。</small>
				</div>
				<label class="check-row" for="doubao-clear"><input id="doubao-clear" type="checkbox" bind:checked={clearDoubaoKey} /> 清除已保存的豆包 API Key</label>
				<p class="muted">默认入口为 https://openspeech.bytedance.com；环境变量 VOLCENGINE_API_KEY 也会被识别为已配置。</p>
				<div class="section-title">
					<h3>官方音色目录同步专用</h3>
					<span
						class="badge"
						class:ok={settings.volcengine_access_key_id_configured && settings.volcengine_secret_access_key_configured}
						class:warn={!settings.volcengine_access_key_id_configured || !settings.volcengine_secret_access_key_configured}
					>
						{settings.volcengine_access_key_id_configured && settings.volcengine_secret_access_key_configured ? 'AK/SK 已配置' : 'AK/SK 未完整配置'}
					</span>
				</div>
				<div class="credential-guide">
					<p class="muted">仅用于调用火山引擎 ListSpeakers 同步官方音色目录；不会替代上方豆包 X-Api-Key，也不会回显凭据。</p>
					<span class="credential-links">
						<a href="https://console.volcengine.com/iam/keymanage/" target="_blank" rel="noreferrer">登录创建 AK/SK <ExternalLink size={12} /></a>
						<a href="https://www.volcengine.com/docs/6291/65568?lang=zh" target="_blank" rel="noreferrer">官方说明</a>
					</span>
				</div>
				<div class="field">
					<label for="volcengine-access-key-id">Volcengine Access Key ID（不会回显）</label>
					<input
						id="volcengine-access-key-id"
						type="password"
						bind:value={volcengineAccessKeyId}
						placeholder={settings.volcengine_access_key_id_configured ? '已配置；填写新值可覆盖' : '未配置'}
					/>
				</div>
				<label class="check-row" for="volcengine-clear-access-key-id">
					<input id="volcengine-clear-access-key-id" type="checkbox" bind:checked={clearVolcengineAccessKeyId} /> 清除已保存的 Access Key ID
				</label>
				<div class="field">
					<label for="volcengine-secret-access-key">Volcengine Secret Access Key（不会回显）</label>
					<input
						id="volcengine-secret-access-key"
						type="password"
						bind:value={volcengineSecretAccessKey}
						placeholder={settings.volcengine_secret_access_key_configured ? '已配置；填写新值可覆盖' : '未配置'}
					/>
				</div>
				<label class="check-row" for="volcengine-clear-secret-access-key">
					<input id="volcengine-clear-secret-access-key" type="checkbox" bind:checked={clearVolcengineSecretAccessKey} /> 清除已保存的 Secret Access Key
				</label>
			</section>

			<section class="panel stack directories-panel">
				<div class="section-title">
					<h2>目录策略</h2>
					<span class="badge source">本机路径</span>
				</div>
				<div class="field"><label for="data-dir">数据根目录</label><input id="data-dir" bind:value={settings.data_dir} /><small>默认承载配置数据库和各子目录。</small></div>
				<div class="field"><label for="model-dir">模型目录</label><input id="model-dir" bind:value={settings.model_dir} /><small>相对路径会解析到项目根目录。</small></div>
				<div class="field"><label for="voice-dir">音色库目录</label><input id="voice-dir" bind:value={settings.voice_dir} /><small>自定义音色确认、导入音色包和注册音色会写入这里。</small></div>
				<div class="field"><label for="output-dir">生成输出目录</label><input id="output-dir" bind:value={settings.output_dir} /><small>单条、长文本和批处理默认写入这里。</small></div>
				<div class="field"><label for="export-dir">导出目录</label><input id="export-dir" bind:value={settings.export_dir} /><small>合并、打包和音频工具导出文件。</small></div>
				<div class="field"><label for="project-dir">项目目录</label><input id="project-dir" bind:value={settings.project_dir} /><small>项目级资产和批量合成材料。</small></div>
				<div class="field"><label for="cache-dir">缓存目录</label><input id="cache-dir" bind:value={settings.cache_dir} /><small>ASR 源音频、对齐缓存和可复用中间产物。</small></div>
				<div class="field"><label for="log-dir">日志目录</label><input id="log-dir" bind:value={settings.log_dir} /><small>应用层日志；外部引擎可能另有本地日志。</small></div>
			</section>
		</div>

		<section class="panel storage-panel">
			<div class="section-title">
				<div>
					<h2>存储概览</h2>
					<p class="muted">生成过程涉及的目录、大小、文件数量和可清理项。</p>
				</div>
				<div class="row">
					{#if storageMessage}<span class="badge ok">{storageMessage}</span>{/if}
					{#if saved}<span class="badge ok">{saved}</span>{/if}
					<span class="badge"><HardDrive size={13} /> 合计 {formatBytes(storageAudit?.total_bytes)}</span>
				</div>
			</div>

			{#if storageAudit}
				<div class="location-grid">
					{#each storageAudit.locations as location}
						<article class="location-row">
							<div class="location-main">
								<div class="location-head">
									<strong>{location.label}</strong>
									<span class="badge">{location.category}</span>
									{#if !location.exists}<span class="badge warn">未创建</span>{/if}
								</div>
								<p>{location.description}</p>
								<code>{location.path}</code>
							</div>
							<div class="location-meta">
								<span>{formatBytes(location.size_bytes)}</span>
								<span>{fileCountLabel(location)}</span>
								<button class="btn location-open-btn" onclick={() => openLocation(location)} disabled={openingBusy === location.key}>
									<FolderOpen size={14} /> 打开位置
								</button>
								{#if location.cleanup_key}
									<span class={`risk ${location.cleanup_risk ?? 'none'}`}>{riskLabel(location.cleanup_risk)}</span>
									<button class="btn danger subtle" onclick={() => cleanupLocation(location)} disabled={cleanupBusy === location.cleanup_key}>
										<Trash2 size={14} /> {location.cleanup_label}
									</button>
								{/if}
							</div>
						</article>
					{/each}
				</div>
			{:else}
				<div class="empty">加载存储信息中</div>
			{/if}
		</section>

		<section class="panel flow-panel">
			<div class="section-title">
				<div>
					<h2>生成产物流转</h2>
					<p class="muted">这里列出生成、ASR、导出和诊断会产生的主要中间产物。</p>
				</div>
				<Database size={18} />
			</div>
			{#if storageAudit}
				<div class="flow-list">
					{#each storageAudit.flows as flow}
						<div class="flow-row">
							<strong>{flow.name}</strong>
							<code>{flow.path}</code>
							<p>{flow.description}</p>
						</div>
					{/each}
				</div>
				<div class="cleanup-strip">
					<span class="muted">可清理项</span>
					{#each cleanupLocations as location}
						<button class="btn" onclick={() => cleanupLocation(location)} disabled={cleanupBusy === location.cleanup_key}>
							<Trash2 size={14} /> {location.cleanup_label}
						</button>
					{/each}
				</div>
			{/if}
		</section>
	{:else}
		<div class="empty">加载设置中</div>
	{/if}
</main>

<style>
	.settings-page {
		display: grid;
		gap: 14px;
	}

	.settings-grid {
		display: grid;
		grid-template-columns: minmax(240px, 0.9fr) minmax(270px, 1fr) minmax(360px, 1.35fr);
		gap: 14px;
		align-items: start;
	}

	.section-title {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 12px;
		min-width: 0;
	}

	.section-title h2 {
		margin-bottom: 2px;
	}

	.check-row {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		color: var(--text);
		font-size: 13px;
	}

	.credential-label,
	.credential-guide,
	.credential-links,
	.credential-links a {
		display: flex;
		align-items: center;
	}

	.credential-label,
	.credential-guide {
		justify-content: space-between;
		gap: 10px;
	}

	.credential-guide {
		align-items: flex-start;
	}

	.credential-guide p {
		margin: 0;
	}

	.credential-links {
		flex: none;
		gap: 10px;
		font-size: 11px;
	}

	.credential-links a {
		gap: 3px;
		color: #91c4ff;
		text-decoration: none;
		white-space: nowrap;
	}

	.credential-links a:hover {
		color: #c5e1ff;
		text-decoration: underline;
		text-underline-offset: 3px;
	}

	.credential-links a:focus-visible {
		outline: 2px solid #5aa7ff;
		outline-offset: 3px;
		border-radius: 3px;
	}

	.field small {
		color: var(--muted);
		font-size: 11px;
		line-height: 1.35;
	}

	.directories-panel {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}

	.directories-panel .section-title {
		grid-column: 1 / -1;
	}

	.storage-panel,
	.flow-panel {
		display: grid;
		gap: 12px;
	}

	.location-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
		gap: 10px;
	}

	.location-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 12px;
		padding: 10px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #14181d;
		min-width: 0;
	}

	.location-main,
	.flow-row {
		min-width: 0;
	}

	.location-head {
		display: flex;
		align-items: center;
		gap: 7px;
		flex-wrap: wrap;
	}

	.location-row p,
	.flow-row p {
		margin: 6px 0;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.45;
	}

	code {
		display: block;
		max-width: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: #b9d7ff;
		background: #0f1216;
		border: 1px solid rgba(255, 255, 255, 0.06);
		border-radius: 6px;
		padding: 5px 7px;
		font-size: 11px;
	}

	.location-meta {
		display: grid;
		justify-items: end;
		align-content: start;
		gap: 6px;
		min-width: 142px;
		color: var(--muted);
		font-size: 12px;
	}

	.location-open-btn {
		background: #17202b;
		border-color: #2c4b6e;
		color: #b9d7ff;
	}

	.risk {
		display: inline-flex;
		align-items: center;
		border-radius: 999px;
		padding: 2px 7px;
		font-size: 11px;
		border: 1px solid var(--line);
		color: var(--muted);
	}

	.risk.low {
		color: #9ee6c8;
		border-color: #23634f;
		background: #12261f;
	}

	.risk.medium {
		color: #f0c76a;
		border-color: #604b18;
		background: #261f10;
	}

	.risk.high {
		color: #ff9a9a;
		border-color: #6d3030;
		background: #2b1515;
	}

	.btn.subtle {
		background: #2a1b1e;
	}

	.flow-list {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
		gap: 10px;
	}

	.flow-row {
		padding: 10px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #14181d;
	}

	.flow-row strong {
		display: block;
		margin-bottom: 7px;
	}

	.cleanup-strip {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
		padding-top: 2px;
	}

	@media (max-width: 1180px) {
		.settings-grid {
			grid-template-columns: repeat(2, minmax(280px, 1fr));
		}

		.directories-panel {
			grid-column: 1 / -1;
		}
	}

	@media (max-width: 760px) {
		.page-head,
		.section-title,
		.location-row {
			grid-template-columns: 1fr;
			flex-direction: column;
			align-items: stretch;
		}

		.settings-grid,
		.directories-panel {
			grid-template-columns: 1fr;
		}

		.location-meta {
			grid-template-columns: repeat(2, minmax(120px, 1fr));
			justify-items: start;
			min-width: 0;
		}

		.credential-label,
		.credential-guide {
			align-items: flex-start;
			flex-direction: column;
		}
	}
</style>
