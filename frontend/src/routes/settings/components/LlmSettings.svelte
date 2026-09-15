<script lang="ts">
	import { Api } from '$lib/api';
	import type {
		CodexCliStatus,
		LlmModelInfo,
		LlmProviderListResponse,
		LlmProviderProfile,
		LlmProviderProtocol,
		LlmProviderProfileUpsert
	} from '$lib/api/types';
	import { Activity, Bot, Check, ChevronDown, CircleCheck, LogIn, LogOut, Plus, RefreshCw, Save, Star, Terminal, Trash2 } from 'lucide-svelte';
	import { onMount } from 'svelte';
	import SettingsCheck from './SettingsCheck.svelte';
	import SettingsField from './SettingsField.svelte';

	let profiles = $state<LlmProviderProfile[]>([]);
	let defaultProfileId = $state<string | null>(null);
	let draft = $state<LlmProviderProfile | null>(null);
	let draftPersisted = $state(false);
	let apiKey = $state('');
	let clearApiKey = $state(false);
	let models = $state<LlmModelInfo[]>([]);
	let busy = $state<'load' | 'save' | 'models' | 'test' | 'default' | 'delete' | ''>('');
	let message = $state('');
	let messageKind = $state<'ok' | 'error' | 'info'>('info');
	let modelMenuOpen = $state(false);
	let modelCombobox: HTMLDivElement | null = $state(null);
	let modelQuery = $state('');
	let codexStatus = $state<CodexCliStatus | null>(null);
	let codexStatusBusy = $state(false);
	let codexStatusError = $state('');
	let codexAction = $state<'login' | 'logout' | ''>('');
	let codexPollTimer: ReturnType<typeof setTimeout> | null = null;
	let codexPollDeadline = 0;

	const filteredModels = $derived.by(() => {
		const query = modelQuery.trim().toLocaleLowerCase();
		if (!query) return models;
		return models.filter((model) => `${model.model_id} ${model.owned_by ?? ''}`.toLocaleLowerCase().includes(query));
	});
	const codexUsesChatGptSubscription = $derived(Boolean(codexStatus?.subscription_usable));

	onMount(() => {
		loadProfiles();
		return stopCodexPolling;
	});

	function errorMessage(error: unknown, fallback: string) {
		return error instanceof Error && error.message.trim() ? error.message : fallback;
	}

	function setMessage(value: string, kind: 'ok' | 'error' | 'info' = 'info') {
		message = value;
		messageKind = kind;
	}

	function isChatGptAuth(authType: string | null | undefined) {
		const normalized = (authType ?? '').trim().toLocaleLowerCase().replace(/[\s_-]+/g, '');
		return normalized.includes('chatgpt');
	}

	function codexAuthLabel(authType: string | null | undefined) {
		if (!authType) return '未识别';
		if (isChatGptAuth(authType)) return 'ChatGPT 订阅';
		if (authType.toLocaleLowerCase().includes('api')) return 'API Key';
		return authType;
	}

	function stopCodexPolling() {
		if (codexPollTimer) clearTimeout(codexPollTimer);
		codexPollTimer = null;
		codexPollDeadline = 0;
	}

	async function loadCodexStatus(options: { quiet?: boolean; continuePolling?: boolean } = {}) {
		if (!options.quiet) codexStatusBusy = true;
		try {
			codexStatus = await Api.codexCliStatus();
			codexStatusError = '';
			if (codexStatus.operation === 'failed') {
				stopCodexPolling();
				codexStatusError = codexStatus.operation_error || 'Codex 登录未完成';
			} else if (codexStatus.subscription_usable && codexStatus.operation !== 'running') {
				stopCodexPolling();
			}
		} catch (error) {
			const detail = errorMessage(error, '');
			codexStatusError = detail ? `无法读取本机 Codex 状态：${detail}` : '无法读取本机 Codex 状态，请确认本机服务正在运行。';
		} finally {
			if (!options.quiet) codexStatusBusy = false;
		}
		if (
			options.continuePolling &&
			codexPollDeadline &&
			Date.now() < codexPollDeadline &&
			!codexStatus?.subscription_usable &&
			codexStatus?.operation !== 'failed'
		) {
			codexPollTimer = setTimeout(() => loadCodexStatus({ quiet: true, continuePolling: true }), 2_000);
		} else if (options.continuePolling && codexPollDeadline && Date.now() >= codexPollDeadline) {
			stopCodexPolling();
			codexStatusError ||= '登录等待已结束。完成浏览器登录后，点“刷新状态”继续。';
		}
	}

	function startCodexPolling() {
		stopCodexPolling();
		codexPollDeadline = Date.now() + 90_000;
		codexPollTimer = setTimeout(() => loadCodexStatus({ quiet: true, continuePolling: true }), 2_000);
	}

	async function runCodexAction(action: 'login' | 'logout') {
		if (action === 'logout' && !window.confirm('确认退出本机 Codex？这会影响其他使用同一 Codex 登录状态的本机应用。')) return;
		stopCodexPolling();
		codexAction = action;
		codexStatusError = '';
		try {
			const response = action === 'login' ? Api.loginCodexCli() : Api.logoutCodexCli();
			const result = await response;
			if (action === 'logout') {
				await loadCodexStatus({ quiet: true });
				setMessage(result.message || '已退出本机 Codex', 'ok');
			} else {
				setMessage(result.message, 'info');
				await loadCodexStatus({ quiet: true });
				startCodexPolling();
			}
		} catch (error) {
			codexStatusError = errorMessage(error, action === 'logout' ? '退出失败' : '无法发起登录');
		} finally {
			codexAction = '';
		}
	}

	function selectProfile(profileId: string) {
		const profile = profiles.find((item) => item.profile_id === profileId);
		if (!profile) return;
		draft = { ...profile };
		draftPersisted = true;
		apiKey = '';
		clearApiKey = false;
		models = [];
		modelMenuOpen = false;
		modelQuery = '';
		message = '';
		if (draft.protocol === 'codex_cli') loadCodexStatus();
	}

	function applyProfiles(response: LlmProviderListResponse, preferredProfileId?: string) {
		profiles = response.profiles;
		defaultProfileId = response.default_profile_id;
		const nextId =
			(preferredProfileId && response.profiles.some((item) => item.profile_id === preferredProfileId)
				? preferredProfileId
				: response.default_profile_id) ?? response.profiles[0]?.profile_id;
		if (nextId) selectProfile(nextId);
		else {
			draft = null;
			draftPersisted = false;
			apiKey = '';
			clearApiKey = false;
			models = [];
			message = '';
		}
	}

	async function loadProfiles() {
		const preferredId = draftPersisted ? draft?.profile_id : undefined;
		busy = 'load';
		try {
			applyProfiles(await Api.llmProfiles(), preferredId);
		} catch (error) {
			setMessage(errorMessage(error, '语言模型配置加载失败'), 'error');
		} finally {
			busy = '';
		}
	}

	function addProfile() {
		draft = {
			profile_id: `llm-${Date.now().toString(36)}`,
			name: '新语言模型服务',
			protocol: 'openai_compatible',
			base_url: '',
			model_id: '',
			reasoning_effort: 'default',
			enabled: true,
			api_key_configured: false,
			model_test_verified: false
		};
		draftPersisted = false;
		apiKey = '';
		clearApiKey = false;
		models = [];
		modelMenuOpen = false;
		modelQuery = '';
		setMessage('填写连接信息后保存', 'info');
	}

	function changeProtocol(event: Event) {
		if (!draft) return;
		const protocol = (event.currentTarget as HTMLSelectElement).value as LlmProviderProtocol;
		if (draft.protocol === protocol) return;
		draft.protocol = protocol;
		apiKey = '';
		clearApiKey = protocol === 'codex_cli' && draft.api_key_configured;
		models = [];
		modelMenuOpen = false;
		modelQuery = '';
		if (protocol === 'codex_cli') {
			draft.base_url = '';
			loadCodexStatus();
			setMessage('本机 Codex 复用系统登录；保存后可测试订阅调用', 'info');
		} else {
			codexStatusError = '';
			stopCodexPolling();
			setMessage('请填写 OpenAI Compatible 服务的连接信息', 'info');
		}
	}

	function payload(): LlmProviderProfileUpsert | null {
		if (!draft) return null;
		const name = draft.name.trim();
		if (!name) {
			setMessage('请输入配置名称', 'error');
			return null;
		}
		const baseUrl = draft.base_url.trim();
		if (draft.protocol === 'openai_compatible' && !baseUrl) {
			setMessage('请输入 Base URL', 'error');
			return null;
		}
		const value: LlmProviderProfileUpsert = {
			name,
			protocol: draft.protocol,
			base_url: draft.protocol === 'openai_compatible' ? baseUrl : '',
			model_id: draft.model_id.trim(),
			reasoning_effort: draft.reasoning_effort,
			enabled: draft.enabled
		};
		if (draft.protocol === 'openai_compatible') {
			const newKey = apiKey.trim();
			if (newKey) value.api_key = newKey;
			else if (clearApiKey && draft.api_key_configured) value.clear_api_key = true;
		} else if (draft.api_key_configured) {
			value.clear_api_key = true;
		}
		return value;
	}

	async function saveProfile(options: { silent?: boolean } = {}) {
		if (!draft) return false;
		const value = payload();
		if (!value) return false;
		const profileId = draft.profile_id;
		busy = 'save';
		try {
			applyProfiles(await Api.saveLlmProfile(profileId, value), profileId);
			if (!options.silent) setMessage('配置已保存', 'ok');
			return true;
		} catch (error) {
			setMessage(errorMessage(error, '保存失败'), 'error');
			return false;
		} finally {
			busy = '';
		}
	}

	async function fetchModels() {
		if (!draft) return;
		const profileId = draft.profile_id;
		if (!(await saveProfile({ silent: true }))) return;
		busy = 'models';
		try {
			const response = await Api.llmProfileModels(profileId);
			models = response.models;
			modelQuery = '';
			modelMenuOpen = response.models.length > 0;
			setMessage(response.models.length ? `已获取 ${response.models.length} 个模型` : '服务未返回模型', response.models.length ? 'ok' : 'info');
		} catch (error) {
			setMessage(errorMessage(error, '获取模型失败'), 'error');
		} finally {
			busy = '';
		}
	}

	function chooseFetchedModel(modelId: string) {
		if (!draft) return;
		draft.model_id = modelId;
		modelQuery = '';
		modelMenuOpen = false;
	}

	function filterModels(event: Event) {
		if (!draft) return;
		const value = (event.currentTarget as HTMLInputElement).value;
		draft.model_id = value;
		modelQuery = value;
		if (models.length) modelMenuOpen = true;
	}

	function toggleModelMenu() {
		if (!models.length) return;
		if (!modelMenuOpen) modelQuery = '';
		modelMenuOpen = !modelMenuOpen;
	}

	function closeModelMenuOnOutsideClick(event: MouseEvent) {
		if (!modelMenuOpen || modelCombobox?.contains(event.target as Node)) return;
		modelMenuOpen = false;
	}

	function closeModelMenuOnEscape(event: KeyboardEvent) {
		if (modelMenuOpen && event.key === 'Escape') modelMenuOpen = false;
	}

	async function testConnection() {
		if (!draft) return;
		if (draft.protocol === 'codex_cli' && !codexUsesChatGptSubscription) {
			setMessage('请先使用 ChatGPT 账号登录本机 Codex，再测试订阅调用', 'error');
			return;
		}
		const profileId = draft.profile_id;
		if (!(await saveProfile({ silent: true }))) return;
		busy = 'test';
		try {
			const response = await Api.testLlmProfile(profileId);
			applyProfiles(await Api.llmProfiles(), profileId);
			setMessage(response.message, response.response_verified ? 'ok' : 'info');
		} catch (error) {
			applyProfiles(await Api.llmProfiles(), profileId);
			setMessage(errorMessage(error, '连接测试失败'), 'error');
		} finally {
			busy = '';
		}
	}

	async function setDefaultProfile(profile: LlmProviderProfile) {
		if (!profile.model_test_verified || !profile.enabled || profile.profile_id === defaultProfileId) return;
		busy = 'default';
		try {
			applyProfiles(await Api.setDefaultLlmProfile(profile.profile_id), profile.profile_id);
			setMessage(`已将 ${profile.name} 设为默认服务`, 'ok');
		} catch (error) {
			setMessage(errorMessage(error, '设置默认服务失败'), 'error');
		} finally {
			busy = '';
		}
	}

	function discardDraft() {
		if (!draft || draftPersisted) return;
		if (!window.confirm(`确认移除「${draft.name || '尚未保存的服务'}」？当前填写内容不会保留。`)) return;
		applyProfiles({ profiles, default_profile_id: defaultProfileId });
	}

	async function deleteProfile(profile: LlmProviderProfile) {
		const suffix = profile.protocol === 'openai_compatible' ? '已保存的 API Key 也会一并删除。' : '本机 Codex 的全局登录状态不会改变。';
		if (!window.confirm(`确认删除「${profile.name}」？${suffix}`)) return;
		const preferredProfileId = draftPersisted && draft?.profile_id !== profile.profile_id ? draft?.profile_id : undefined;
		busy = 'delete';
		try {
			applyProfiles(await Api.deleteLlmProfile(profile.profile_id), preferredProfileId);
			setMessage('配置已删除', 'ok');
		} catch (error) {
			setMessage(errorMessage(error, '删除失败'), 'error');
		} finally {
			busy = '';
		}
	}
</script>

<svelte:window onclick={closeModelMenuOnOutsideClick} onkeydown={closeModelMenuOnEscape} />

<div class="llm-settings">
	<div class="section-toolbar">
		<div>
			<h2>服务配置</h2>
			<p>统一管理 API 与本机 Codex；测试模型会发送最小请求，并产生少量 API 或订阅用量。</p>
		</div>
		<div class="toolbar-actions">
			{#if message}
				<span class:ok={messageKind === 'ok'} class:error={messageKind === 'error'} class="status" role={messageKind === 'error' ? 'alert' : 'status'}>{message}</span>
			{/if}
			<button class="secondary-button" type="button" onclick={addProfile} disabled={Boolean(busy)}><Plus size={16} /> 新增服务</button>
		</div>
	</div>

	<div class="workspace">
		<aside class="provider-pane" aria-label="语言模型服务列表">
			<div class="provider-head"><span>服务 · {profiles.length}</span><span>默认</span></div>
			<div class="provider-list" role="list" aria-label="语言模型服务">
				{#if draft && !draftPersisted}
					<div class="provider-item-shell active" role="listitem">
						<button class="provider-item" type="button" aria-pressed="true">
							<span class="provider-mark draft"><Plus size={13} /></span>
							<span><strong>{draft.name || '新语言模型服务'}</strong><small>尚未保存</small></span>
						</button>
					<div class="provider-actions">
						<span class="provider-action-placeholder"></span>
						<button class="provider-delete" type="button" aria-label="移除尚未保存的服务" title="移除尚未保存的服务" onclick={discardDraft}><Trash2 size={13} /></button>
					</div>
					</div>
				{/if}
				{#each profiles as profile (profile.profile_id)}
					<div class:active={draftPersisted && draft?.profile_id === profile.profile_id} class="provider-item-shell" role="listitem">
						<button
							class="provider-item"
							type="button"
							aria-pressed={draftPersisted && draft?.profile_id === profile.profile_id}
							onclick={() => selectProfile(profile.profile_id)}
						>
							<span class:disabled={!profile.enabled} class="provider-mark"><Bot size={13} /></span>
							<span>
								<strong>{profile.name}</strong>
								<small title={`${profile.protocol === 'codex_cli' ? '本机 Codex' : profile.api_key_configured ? 'Key 已配置' : '未配置 Key'} · ${profile.model_test_verified ? '测试通过' : '尚未通过模型测试'}`}>
									{profile.profile_id === defaultProfileId ? '默认' : profile.enabled ? '已启用' : '已停用'} · {profile.model_test_verified ? '已验证' : '待测试'}
								</small>
							</span>
						</button>
						<div class="provider-actions" title={!profile.model_test_verified ? '先测试模型，通过后才能设为默认' : undefined}>
							<button
								class:current={profile.profile_id === defaultProfileId}
								class="provider-default"
								type="button"
								aria-label={profile.profile_id === defaultProfileId ? `当前默认服务：${profile.name}` : `设为默认服务：${profile.name}`}
								aria-pressed={profile.profile_id === defaultProfileId}
								title={profile.profile_id === defaultProfileId ? '当前默认服务' : profile.model_test_verified ? '设为默认服务' : '先测试模型，通过后才能设为默认'}
								onclick={() => setDefaultProfile(profile)}
								disabled={Boolean(busy) || profile.profile_id === defaultProfileId || !profile.enabled || !profile.model_test_verified}
							><Star size={13} fill={profile.profile_id === defaultProfileId ? 'currentColor' : 'none'} /></button>
							<button class="provider-delete" type="button" aria-label={`删除服务：${profile.name}`} title={`删除服务：${profile.name}`} onclick={() => deleteProfile(profile)} disabled={Boolean(busy)}><Trash2 size={13} /></button>
						</div>
					</div>
				{/each}
				{#if !draft && profiles.length === 0}<div class="list-empty">暂无服务</div>{/if}
			</div>
		</aside>

		{#if draft}
			<div class="editor">
				<div class="form-grid">
					<SettingsField label="配置名称" controlId="llm-name">
						<input id="llm-name" bind:value={draft.name} maxlength="80" placeholder="例如：日常字幕校对" />
					</SettingsField>
					<SettingsField label="接口协议" controlId="llm-protocol">
						<select id="llm-protocol" value={draft.protocol} onchange={changeProtocol}>
							<option value="openai_compatible">OpenAI Compatible</option>
							<option value="codex_cli">本机 Codex（ChatGPT 订阅）</option>
						</select>
						{#snippet description()}子任务保持不变，只切换底层调用方式。{/snippet}
					</SettingsField>
					{#if draft.protocol === 'openai_compatible'}
						<SettingsField label="Base URL" controlId="llm-base-url" wide>
							<input id="llm-base-url" type="url" bind:value={draft.base_url} maxlength="500" placeholder="https://example.com/v1" autocomplete="url" />
						</SettingsField>
						<SettingsField label="API Key" controlId="llm-api-key">
							<input id="llm-api-key" type="password" bind:value={apiKey} placeholder={draft.api_key_configured ? '已配置；填写新 Key 可覆盖' : '本地服务可留空'} autocomplete="new-password" />
							{#snippet description()}密钥只写入本机，不会回显。{/snippet}
						</SettingsField>
					{/if}
					<div class="model-settings-row">
						<SettingsField label="模型 ID" controlId="llm-model-id">
							<div class="model-row">
								<div class="model-combobox" bind:this={modelCombobox}>
									<input
										id="llm-model-id"
										type="text"
										value={draft.model_id}
										maxlength="200"
										placeholder={draft.protocol === 'codex_cli' ? '跟随 Codex 默认模型' : '输入关键字搜索，或手动填写模型 ID'}
										role="combobox"
										aria-autocomplete="list"
										aria-expanded={modelMenuOpen}
										aria-controls={models.length ? `llm-model-options-${draft.profile_id}` : undefined}
										oninput={filterModels}
									/>
									{#if models.length}
										<button
											class:open={modelMenuOpen}
											class="model-menu-button"
											type="button"
											aria-label={`选择模型，共 ${models.length} 个`}
											aria-expanded={modelMenuOpen}
											aria-controls={`llm-model-options-${draft.profile_id}`}
											title={`选择模型（${models.length} 个）`}
											onclick={toggleModelMenu}
										><ChevronDown size={14} /></button>
										{#if modelMenuOpen}
											<div class="model-menu menu-scroll-region" id={`llm-model-options-${draft.profile_id}`} role="listbox" aria-label="已获取的模型">
												{#if draft.protocol === 'codex_cli' && !modelQuery.trim()}
													<button
														type="button"
														role="option"
														aria-selected={!draft.model_id}
														onclick={() => chooseFetchedModel('')}
													>
														<span>
															<strong>跟随 Codex 默认模型</strong>
															<small>以后默认模型更新时自动跟随</small>
														</span>
														{#if !draft.model_id}<Check size={14} />{/if}
													</button>
												{/if}
												{#each filteredModels as model}
													<button type="button" role="option" aria-selected={draft.model_id === model.model_id} onclick={() => chooseFetchedModel(model.model_id)}>
														<span><strong>{model.model_id}</strong>{#if model.owned_by}<small>{model.owned_by}</small>{/if}</span>
														{#if draft.model_id === model.model_id}<Check size={14} />{/if}
													</button>
												{:else}
													<div class="model-menu-empty">没有包含“{modelQuery.trim()}”的模型</div>
												{/each}
											</div>
										{/if}
									{/if}
								</div>
								<button
									class="icon-button"
									type="button"
									aria-label={draft.protocol === 'codex_cli' ? '获取当前账号可用模型' : '获取模型列表'}
									title={draft.protocol === 'codex_cli' ? '获取当前账号可用模型（不生成内容、不消耗模型用量）' : '获取模型列表（不生成内容、不产生生成用量）'}
									onclick={fetchModels}
									disabled={Boolean(busy) || (draft.protocol === 'codex_cli' && !codexUsesChatGptSubscription)}
								><span class:spinning={busy === 'models'}><RefreshCw size={15} /></span></button>
							</div>
							{#snippet description()}
								{draft?.protocol === 'codex_cli'
									? '留空时跟随 Codex 默认模型；也可刷新当前账号的模型列表后选择。'
									: '可从服务获取列表后搜索选择，也可以手动填写模型 ID。'}
							{/snippet}
						</SettingsField>
						<SettingsField label="思考深度" controlId="llm-reasoning-effort">
							<select id="llm-reasoning-effort" bind:value={draft.reasoning_effort}>
								<option value="default">跟随模型默认</option>
								<option value="low">低（更快、更省）</option>
								<option value="high">高（更充分）</option>
								<option value="max">最高（最慢、消耗最多）</option>
							</select>
							{#snippet description()}新调用实时读取这里；开发单步和正式流程使用同一配置。{/snippet}
						</SettingsField>
					</div>
					{#if draft.protocol === 'codex_cli'}
						<div class="codex-card" class:error-card={Boolean(codexStatusError)} aria-live="polite">
							<div class="codex-card-head">
								<div class="codex-title">
									<span class="codex-icon"><Terminal size={17} /></span>
									<span>
										<strong>本机 Codex</strong>
										<small>复用 Codex CLI 的系统登录，不需要填写 API Key</small>
									</span>
								</div>
								<span class:ready={codexUsesChatGptSubscription} class:warning={Boolean(codexStatus?.logged_in && !codexUsesChatGptSubscription)} class="codex-badge">
									{#if codexStatusBusy}
										读取中
									{:else if codexStatus?.operation === 'running'}
										登录中
									{:else if codexUsesChatGptSubscription}
										订阅可用
									{:else if codexStatus?.logged_in}
										非订阅登录
									{:else}
										未登录
									{/if}
								</span>
							</div>

							<div class="codex-facts">
								<div><span>安装</span><strong>{codexStatus?.installed ? '已发现' : '未发现'}</strong></div>
								<div><span>版本</span><strong>{codexStatus?.version || '—'}</strong></div>
								<div><span>登录身份</span><strong>{codexStatus?.logged_in ? codexAuthLabel(codexStatus.auth_type) : '未登录'}</strong></div>
								<div><span>图片输入</span><strong class:supported={codexStatus?.supports_image_input}>{codexStatus?.supports_image_input ? '支持' : codexStatus?.installed ? '不支持' : '待安装'}</strong></div>
							</div>
							<div class="codex-path" title={codexStatus?.executable_path || undefined}>
								<span>安装路径</span>
								<code>{codexStatus?.executable_path || '尚未检测到 Codex CLI'}</code>
							</div>

							{#if codexStatus?.logged_in && !codexUsesChatGptSubscription}
								<div class="codex-notice warning" role="alert">
									当前是 {codexAuthLabel(codexStatus.auth_type)} 登录，不会按 ChatGPT 订阅目标使用。请先退出，再选择“使用 ChatGPT 登录”。
								</div>
							{:else if codexStatusError}
								<div class="codex-notice error" role="alert">{codexStatusError}</div>
							{:else if codexStatus?.operation_message}
								<div class="codex-notice">{codexStatus.operation_message}</div>
							{:else if !codexStatus?.installed && !codexStatusBusy}
								<div class="codex-notice">未找到 Codex CLI。请先安装或打开带 Codex 的 ChatGPT 桌面版，再刷新状态。</div>
							{/if}

							<div class="codex-actions">
								<button class="secondary-button" type="button" onclick={() => loadCodexStatus()} disabled={codexStatusBusy || Boolean(codexAction)}>
									<span class:spinning={codexStatusBusy}><RefreshCw size={14} /></span> 刷新状态
								</button>
								{#if codexStatus?.logged_in}
									<button class="danger-button" type="button" onclick={() => runCodexAction('logout')} disabled={Boolean(codexAction) || codexStatus.operation === 'running'}>
										<LogOut size={14} /> {codexAction === 'logout' ? '退出中' : '退出'}
									</button>
								{:else}
									<button class="primary-button" type="button" onclick={() => runCodexAction('login')} disabled={!codexStatus?.installed || Boolean(codexAction) || codexStatus?.operation === 'running'}>
										<LogIn size={14} /> {codexAction === 'login' || codexStatus?.operation === 'running' ? '等待完成登录' : '使用 ChatGPT 登录'}
									</button>
								{/if}
							</div>
						</div>
					{/if}
				</div>

				<div class="options-row">
					<div class="option-checks">
						<SettingsCheck id="llm-enabled" bind:checked={draft.enabled}>启用此服务</SettingsCheck>
						{#if draft.protocol === 'openai_compatible' && draft.api_key_configured}<SettingsCheck id="llm-clear-key" danger bind:checked={clearApiKey}>清除已保存 Key</SettingsCheck>{/if}
					</div>
					<div class="actions">
						<button
							class="secondary-button"
							type="button"
							title={draft.protocol === 'codex_cli' ? '要求本机 Codex 返回指定 JSON 内容，会消耗少量 ChatGPT 订阅额度' : '要求当前模型返回指定 JSON 内容，会产生少量 API 用量'}
							onclick={testConnection}
							disabled={Boolean(busy) || (draft.protocol === 'codex_cli' && !codexUsesChatGptSubscription)}
						><Activity size={15} /> {busy === 'test' ? '测试中' : '测试模型回复'}</button>
						<button class="primary-button" type="button" onclick={() => saveProfile()} disabled={Boolean(busy)}><Save size={15} /> {busy === 'save' ? '保存中' : '保存配置'}</button>
					</div>
				</div>
			</div>
		{:else}
			<div class="editor-empty">
				<CircleCheck size={26} />
				<strong>还没有语言模型服务</strong>
				<span>需要 AI 辅助功能时，再新增一个兼容服务。</span>
				<button class="secondary-button" type="button" onclick={addProfile}><Plus size={15} /> 新增服务</button>
			</div>
		{/if}
	</div>
</div>

<style>
	.llm-settings {
		border: 1px solid rgba(148, 163, 184, 0.16);
		border-radius: 11px;
		background: rgba(17, 22, 30, 0.82);
		overflow: visible;
	}

	.section-toolbar,
	.toolbar-actions,
	.provider-item,
	.options-row,
	.option-checks,
	.actions,
	.model-row,
	.editor-empty {
		display: flex;
		align-items: center;
	}

	.section-toolbar {
		justify-content: space-between;
		gap: 14px;
		padding: 12px 14px;
		border-bottom: 1px solid rgba(148, 163, 184, 0.13);
	}

	h2 { margin: 0; font-size: 15px; }
	p { margin: 4px 0 0; color: #7f8997; font-size: 12px; }
	.toolbar-actions { justify-content: flex-end; gap: 8px; }

	.status {
		max-width: 360px;
		padding: 5px 9px;
		border-radius: 7px;
		background: rgba(92, 113, 137, 0.12);
		color: #a9b5c4;
		font-size: 11px;
		line-height: 1.35;
	}
	.status.ok { background: rgba(48, 168, 112, 0.12); color: #7ee2af; }
	.status.error { background: rgba(219, 83, 90, 0.13); color: #ffabad; }

	.icon-button,
	.danger-button,
	.secondary-button,
	.primary-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		min-height: 34px;
		border: 1px solid rgba(148, 163, 184, 0.22);
		border-radius: 8px;
		background: #1a2029;
		color: #dfe5ec;
		font-size: 12px;
	}
	.icon-button { width: 34px; padding: 0; }
	.icon-button > span { display: grid; width: 100%; height: 100%; place-items: center; line-height: 0; }
	.secondary-button { padding: 0 11px; }
	.primary-button { padding: 0 13px; border-color: #2f82e6; background: #2478db; color: white; font-weight: 650; }
	.danger-button { padding: 0 11px; border-color: rgba(228, 89, 94, .28); background: rgba(106, 36, 40, .2); color: #ffabad; }
	button:disabled { cursor: not-allowed; opacity: .48; }

	.workspace { display: grid; grid-template-columns: 218px minmax(0, 1fr); min-height: 320px; }
	.provider-pane { border-right: 1px solid rgba(148, 163, 184, 0.13); background: rgba(9, 13, 19, 0.38); }
	.provider-head { display: grid; grid-template-columns: minmax(0, 1fr) 64px; align-items: center; padding: 9px 6px 9px 11px; color: #75808f; font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }
	.provider-head span:last-child { text-align: center; }
	.provider-list { display: grid; }
	.provider-item-shell { display: grid; grid-template-columns: minmax(0, 1fr) 64px; align-items: center; border-left: 2px solid transparent; border-bottom: 1px solid rgba(148, 163, 184, 0.08); background: transparent; }
	.provider-item-shell:hover { background: rgba(255, 255, 255, 0.03); }
	.provider-item-shell.active { border-left-color: #3d92f2; background: rgba(45, 127, 224, 0.12); }
	.provider-item { width: 100%; min-width: 0; gap: 8px; min-height: 49px; padding: 7px 4px 7px 8px; border: 0; background: transparent; color: #e9edf2; text-align: left; }
	.provider-item > span:last-child { display: grid; gap: 2px; min-width: 0; }
	.provider-item strong,
	.provider-item small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.provider-item strong { font-size: 12px; font-weight: 620; }
	.provider-item small { color: #778291; font-size: 10px; }
	.provider-mark { display: grid; width: 27px; height: 27px; place-items: center; flex: none; border: 1px solid #315a75; border-radius: 7px; background: #142633; color: #8ed5f0; }
	.provider-mark.draft { border-style: dashed; border-color: #7c6b37; background: #29230f; color: #e8c76b; }
	.provider-mark.disabled { border-color: #404854; background: #1b1e23; color: #727d89; }
	.provider-actions { display: grid; grid-template-columns: 28px 28px; gap: 2px; align-items: center; }
	.provider-action-placeholder { width: 28px; height: 28px; }
	.provider-default,
	.provider-delete { display: grid; width: 28px; height: 28px; padding: 0; place-items: center; border: 1px solid transparent; border-radius: 7px; background: transparent; color: #778391; }
	.provider-default:hover:not(:disabled) { border-color: rgba(77, 150, 238, .3); background: rgba(45, 127, 224, .16); color: #8dc2ff; }
	.provider-default.current { border-color: rgba(77, 150, 238, .28); background: rgba(45, 127, 224, .16); color: #82baff; opacity: 1; }
	.provider-default:disabled:not(.current) { opacity: .32; }
	.provider-delete:hover:not(:disabled) { border-color: rgba(228, 89, 94, .26); background: rgba(106, 36, 40, .22); color: #ffabad; }
	.provider-default:focus-visible,
	.provider-delete:focus-visible { outline: 2px solid rgba(94, 165, 246, .55); outline-offset: 1px; }
	.list-empty { padding: 24px 12px; color: #788392; font-size: 12px; text-align: center; }

	.editor { display: grid; align-content: start; gap: 13px; min-width: 0; padding: 14px 16px 16px; }
	.form-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(220px, .72fr); gap: 10px 13px; }
	.model-settings-row {
		grid-column: 1 / -1;
		display: grid;
		grid-template-columns: minmax(260px, 1.45fr) minmax(190px, .65fr);
		gap: 10px 13px;
		min-width: 0;
	}
	.model-row { width: 100%; gap: 7px; }
	.model-combobox { position: relative; display: flex; align-items: stretch; min-width: 0; height: var(--settings-control-height, 34px); flex: 1; border: 1px solid #2c3541; border-radius: var(--settings-control-radius, 7px); background: #0d1218; }
	.model-combobox:focus-within { border-color: #3c86d5; box-shadow: 0 0 0 2px rgba(45, 127, 224, .12); }
	.model-combobox > #llm-model-id { height: 100%; min-height: 0; min-width: 0; flex: 1; padding-block: 0; padding-right: 8px; border: 0; border-radius: inherit; background: transparent; box-shadow: none; outline: 0; }
	.model-menu-button { display: grid; width: 32px; flex: none; padding: 0; place-items: center; border: 0; border-left: 1px solid rgba(148, 163, 184, .15); border-radius: 0 6px 6px 0; background: transparent; color: #7f8c9c; }
	.model-menu-button:hover,
	.model-menu-button.open { background: rgba(45, 127, 224, .12); color: #a9cfff; }
	.model-menu-button :global(svg) { transition: transform 140ms ease; }
	.model-menu-button.open :global(svg) { transform: rotate(180deg); }
	.model-menu { position: absolute; top: calc(100% + 5px); right: 0; z-index: 60; display: grid; gap: 2px; width: min(360px, calc(100vw - 48px)); max-height: 220px; padding: 5px; overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; border: 1px solid #303b49; border-radius: 8px; background: #111820; box-shadow: 0 16px 36px rgba(0, 0, 0, .42); }
	.model-menu button { display: grid; grid-template-columns: minmax(0, 1fr) 16px; align-items: center; gap: 8px; width: 100%; min-height: 36px; padding: 6px 8px; border: 0; border-radius: 6px; background: transparent; color: #dce3eb; text-align: left; }
	.model-menu button:hover,
	.model-menu button[aria-selected='true'] { background: rgba(45, 127, 224, .14); }
	.model-menu button > span { display: grid; gap: 2px; min-width: 0; }
	.model-menu strong { overflow: hidden; font-size: 11px; font-weight: 620; text-overflow: ellipsis; white-space: nowrap; }
	.model-menu small { color: #748190; font-size: 10px; }
	.model-menu button :global(svg) { color: #67aaf1; }
	.model-menu-empty { padding: 18px 10px; color: #778494; font-size: 11px; text-align: center; }
	.codex-card {
		grid-column: 1 / -1;
		display: grid;
		gap: 10px;
		padding: 12px;
		border: 1px solid rgba(91, 145, 204, .26);
		border-radius: 9px;
		background: linear-gradient(135deg, rgba(22, 43, 59, .58), rgba(12, 18, 25, .72));
	}
	.codex-card.error-card { border-color: rgba(228, 89, 94, .3); }
	.codex-card-head,
	.codex-title,
	.codex-actions { display: flex; align-items: center; }
	.codex-card-head { justify-content: space-between; gap: 12px; }
	.codex-title { gap: 9px; min-width: 0; }
	.codex-title > span:last-child { display: grid; gap: 2px; min-width: 0; }
	.codex-title strong { color: #edf4fb; font-size: 12px; font-weight: 650; }
	.codex-title small { color: #8291a1; font-size: 10px; line-height: 1.4; }
	.codex-icon { display: grid; width: 31px; height: 31px; flex: none; place-items: center; border: 1px solid rgba(76, 157, 195, .38); border-radius: 8px; background: rgba(21, 69, 84, .5); color: #91dce7; }
	.codex-badge { flex: none; padding: 3px 7px; border-radius: 999px; background: rgba(102, 117, 134, .15); color: #8794a3; font-size: 9px; }
	.codex-badge.ready { background: rgba(48, 168, 112, .15); color: #79deb0; }
	.codex-badge.warning { background: rgba(213, 153, 54, .15); color: #e8bd67; }
	.codex-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; overflow: hidden; border: 1px solid rgba(148, 163, 184, .11); border-radius: 7px; background: rgba(148, 163, 184, .1); }
	.codex-facts > div { display: grid; gap: 3px; min-width: 0; padding: 7px 8px; background: rgba(9, 14, 20, .88); }
	.codex-facts span,
	.codex-path > span { color: #6f7c8c; font-size: 9px; text-transform: uppercase; letter-spacing: .06em; }
	.codex-facts strong { overflow: hidden; color: #c7d1dc; font-size: 11px; font-weight: 580; text-overflow: ellipsis; white-space: nowrap; }
	.codex-facts strong.supported { color: #79dcb0; }
	.codex-path { display: grid; grid-template-columns: 66px minmax(0, 1fr); align-items: center; gap: 8px; padding: 6px 8px; border-radius: 6px; background: rgba(6, 10, 15, .5); }
	.codex-path code { overflow: hidden; color: #8796a6; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
	.codex-notice { padding: 7px 9px; border-left: 2px solid #477999; border-radius: 0 6px 6px 0; background: rgba(49, 92, 117, .13); color: #9fb0bf; font-size: 10px; line-height: 1.5; }
	.codex-notice.warning { border-left-color: #c28b37; background: rgba(126, 89, 30, .13); color: #dbb66e; }
	.codex-notice.error { border-left-color: #d75b61; background: rgba(106, 36, 40, .17); color: #ffabad; }
	.codex-actions { justify-content: flex-end; gap: 7px; flex-wrap: wrap; }
	.options-row { justify-content: space-between; gap: 10px 14px; flex-wrap: wrap; padding: 9px 0; border-top: 1px solid rgba(148, 163, 184, 0.11); border-bottom: 1px solid rgba(148, 163, 184, 0.11); }
	.option-checks { gap: 7px; flex-wrap: wrap; min-width: 0; }
	.actions { gap: 8px; margin-left: auto; }
	.editor-empty { min-height: 290px; justify-content: center; flex-direction: column; gap: 8px; color: #74808f; font-size: 12px; }
	.editor-empty strong { color: #e7ebf0; font-size: 14px; }
	.editor-empty .secondary-button { margin-top: 6px; }
	.spinning { animation: spin .8s linear infinite; }
	@keyframes spin { to { transform: rotate(360deg); } }

	@media (max-width: 820px) {
		.section-toolbar { align-items: stretch; flex-direction: column; }
		.toolbar-actions { justify-content: flex-start; flex-wrap: wrap; }
		.status { width: 100%; max-width: none; order: 3; }
		.workspace,
		.form-grid { grid-template-columns: 1fr; }
		.provider-pane { border-right: 0; border-bottom: 1px solid rgba(148, 163, 184, 0.13); }
		.provider-list { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
	}

	@media (max-width: 640px) {
		.model-settings-row { grid-template-columns: 1fr; }
	}

	@media (max-width: 520px) {
		.editor { padding: 12px; }
		.icon-button,
		.danger-button,
		.secondary-button,
		.primary-button { height: 44px; min-height: 44px; }
		.icon-button { width: 44px; }
		.options-row { align-items: stretch; }
		.option-checks { width: 100%; }
		.actions { display: grid; grid-template-columns: 1fr 1fr; width: 100%; margin-left: 0; }
		.actions button { width: 100%; }
		.model-combobox { height: var(--settings-control-touch-height, 44px); }
		.model-menu { right: 0; left: 0; width: auto; }
		.codex-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.codex-path { grid-template-columns: 1fr; gap: 3px; }
		.codex-actions { display: grid; grid-template-columns: 1fr; }
		.codex-actions button { width: 100%; }
	}

	@media (prefers-reduced-motion: reduce) { .spinning { animation: none; } }
</style>
