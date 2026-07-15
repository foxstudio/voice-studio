<script lang="ts">
	import { Api } from '$lib/api';
	import type {
		LlmModelInfo,
		LlmProviderListResponse,
		LlmProviderProfile,
		LlmProviderProfileUpsert
	} from '$lib/api/types';
	import { Activity, Bot, CircleCheck, Plus, RefreshCw, Save, Trash2 } from 'lucide-svelte';
	import { onMount } from 'svelte';
	import SettingsCheck from './SettingsCheck.svelte';
	import SettingsField from './SettingsField.svelte';

	let profiles = $state<LlmProviderProfile[]>([]);
	let defaultProfileId = $state<string | null>(null);
	let draft = $state<LlmProviderProfile | null>(null);
	let draftPersisted = $state(false);
	let apiKey = $state('');
	let clearApiKey = $state(false);
	let makeDefault = $state(false);
	let models = $state<LlmModelInfo[]>([]);
	let busy = $state<'load' | 'save' | 'models' | 'test' | 'delete' | ''>('');
	let message = $state('');
	let messageKind = $state<'ok' | 'error' | 'info'>('info');

	const draftIsDefault = $derived(Boolean(draft && draft.profile_id === defaultProfileId));

	onMount(loadProfiles);

	function errorMessage(error: unknown, fallback: string) {
		return error instanceof Error && error.message.trim() ? error.message : fallback;
	}

	function setMessage(value: string, kind: 'ok' | 'error' | 'info' = 'info') {
		message = value;
		messageKind = kind;
	}

	function selectProfile(profileId: string) {
		const profile = profiles.find((item) => item.profile_id === profileId);
		if (!profile) return;
		draft = { ...profile };
		draftPersisted = true;
		apiKey = '';
		clearApiKey = false;
		makeDefault = profile.profile_id === defaultProfileId;
		models = [];
		message = '';
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
			makeDefault = false;
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
			enabled: true,
			api_key_configured: false
		};
		draftPersisted = false;
		apiKey = '';
		clearApiKey = false;
		makeDefault = profiles.length === 0;
		models = [];
		setMessage('填写连接信息后保存', 'info');
	}

	function payload(): LlmProviderProfileUpsert | null {
		if (!draft) return null;
		const name = draft.name.trim();
		const baseUrl = draft.base_url.trim();
		if (!name) {
			setMessage('请输入配置名称', 'error');
			return null;
		}
		if (!baseUrl) {
			setMessage('请输入 Base URL', 'error');
			return null;
		}
		const value: LlmProviderProfileUpsert = {
			name,
			protocol: 'openai_compatible',
			base_url: baseUrl,
			model_id: draft.model_id.trim(),
			enabled: draft.enabled,
			make_default: makeDefault || draftIsDefault
		};
		const newKey = apiKey.trim();
		if (newKey) value.api_key = newKey;
		else if (clearApiKey && draft.api_key_configured) value.clear_api_key = true;
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
			setMessage(response.models.length ? `已获取 ${response.models.length} 个模型` : '服务未返回模型', response.models.length ? 'ok' : 'info');
		} catch (error) {
			setMessage(errorMessage(error, '获取模型失败'), 'error');
		} finally {
			busy = '';
		}
	}

	async function testConnection() {
		if (!draft) return;
		const profileId = draft.profile_id;
		if (!(await saveProfile({ silent: true }))) return;
		busy = 'test';
		try {
			const response = await Api.testLlmProfile(profileId);
			setMessage(response.message, response.selected_model_available === false ? 'info' : 'ok');
		} catch (error) {
			setMessage(errorMessage(error, '连接测试失败'), 'error');
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
		if (!window.confirm(`确认删除「${profile.name}」？已保存的 API Key 也会一并删除。`)) return;
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

<div class="llm-settings">
	<div class="section-toolbar">
		<div>
			<h2>服务配置</h2>
			<p>管理 OpenAI Compatible 连接；测试或获取模型前会先保存当前配置。</p>
		</div>
		<div class="toolbar-actions">
			{#if message}
				<span class:ok={messageKind === 'ok'} class:error={messageKind === 'error'} class="status" role={messageKind === 'error' ? 'alert' : 'status'}>{message}</span>
			{/if}
			<button class="icon-button" type="button" aria-label="刷新语言模型配置" title="刷新语言模型配置" onclick={loadProfiles} disabled={Boolean(busy)}>
				<span class:spinning={busy === 'load'}><RefreshCw size={16} /></span>
			</button>
			<button class="secondary-button" type="button" onclick={addProfile} disabled={Boolean(busy)}><Plus size={16} /> 新增服务</button>
		</div>
	</div>

	<div class="workspace">
		<aside class="provider-pane" aria-label="语言模型服务列表">
			<div class="provider-head"><span>服务</span><span>{profiles.length}</span></div>
			<div class="provider-list" role="list" aria-label="语言模型服务">
				{#if draft && !draftPersisted}
					<div class="provider-item-shell active" role="listitem">
						<button class="provider-item" type="button" aria-pressed="true">
							<span class="provider-mark draft"><Plus size={13} /></span>
							<span><strong>{draft.name || '新语言模型服务'}</strong><small>尚未保存</small></span>
						</button>
						<button class="provider-delete" type="button" aria-label="移除尚未保存的服务" title="移除尚未保存的服务" onclick={discardDraft}><Trash2 size={13} /></button>
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
								<small>{profile.profile_id === defaultProfileId ? '默认' : profile.enabled ? '可用' : '已停用'} · {profile.api_key_configured ? 'Key 已配置' : '无 Key'}</small>
							</span>
						</button>
						<button class="provider-delete" type="button" aria-label={`删除服务：${profile.name}`} title={`删除服务：${profile.name}`} onclick={() => deleteProfile(profile)} disabled={Boolean(busy)}><Trash2 size={13} /></button>
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
						<select id="llm-protocol" bind:value={draft.protocol} disabled><option value="openai_compatible">OpenAI Compatible</option></select>
					</SettingsField>
					<SettingsField label="Base URL" controlId="llm-base-url" wide>
						<input id="llm-base-url" type="url" bind:value={draft.base_url} maxlength="500" placeholder="https://example.com/v1" autocomplete="url" />
					</SettingsField>
					<SettingsField label="API Key" controlId="llm-api-key">
						<input id="llm-api-key" type="password" bind:value={apiKey} placeholder={draft.api_key_configured ? '已配置；填写新 Key 可覆盖' : '本地服务可留空'} autocomplete="new-password" />
						{#snippet description()}密钥只写入本机，不会回显。{/snippet}
					</SettingsField>
					<SettingsField label="模型 ID" controlId="llm-model-id">
						<div class="model-row">
							<input id="llm-model-id" list={`llm-model-options-${draft.profile_id}`} bind:value={draft.model_id} maxlength="200" placeholder="手动输入或获取模型" />
							<button class="icon-button" type="button" aria-label="从服务获取模型" title="从服务获取模型" onclick={fetchModels} disabled={Boolean(busy)}><span class:spinning={busy === 'models'}><RefreshCw size={15} /></span></button>
						</div>
						<datalist id={`llm-model-options-${draft.profile_id}`}>{#each models as model}<option value={model.model_id}>{model.owned_by ?? ''}</option>{/each}</datalist>
					</SettingsField>
				</div>

				<div class="options-row">
					<div class="option-checks">
						<SettingsCheck id="llm-enabled" bind:checked={draft.enabled}>启用此服务</SettingsCheck>
						<SettingsCheck id="llm-default" checked={draftIsDefault || makeDefault} disabled={draftIsDefault} onchange={(event) => (makeDefault = (event.currentTarget as HTMLInputElement).checked)}>
							{draftIsDefault ? '当前默认服务' : '设为默认服务'}
						</SettingsCheck>
						{#if draft.api_key_configured}<SettingsCheck id="llm-clear-key" danger bind:checked={clearApiKey}>清除已保存 Key</SettingsCheck>{/if}
					</div>
					<div class="actions">
						<button class="secondary-button" type="button" onclick={testConnection} disabled={Boolean(busy)}><Activity size={15} /> {busy === 'test' ? '测试中' : '测试连接'}</button>
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
		overflow: hidden;
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
	.secondary-button { padding: 0 11px; }
	.primary-button { padding: 0 13px; border-color: #2f82e6; background: #2478db; color: white; font-weight: 650; }
	button:disabled { cursor: not-allowed; opacity: .48; }

	.workspace { display: grid; grid-template-columns: 218px minmax(0, 1fr); min-height: 320px; }
	.provider-pane { border-right: 1px solid rgba(148, 163, 184, 0.13); background: rgba(9, 13, 19, 0.38); }
	.provider-head { display: flex; justify-content: space-between; padding: 9px 11px; color: #75808f; font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }
	.provider-list { display: grid; }
	.provider-item-shell { display: grid; grid-template-columns: minmax(0, 1fr) 34px; align-items: center; border-left: 2px solid transparent; border-bottom: 1px solid rgba(148, 163, 184, 0.08); background: transparent; }
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
	.provider-delete { display: grid; width: 28px; height: 28px; padding: 0; place-items: center; border: 1px solid transparent; border-radius: 7px; background: transparent; color: #778391; }
	.provider-delete:hover:not(:disabled) { border-color: rgba(228, 89, 94, .26); background: rgba(106, 36, 40, .22); color: #ffabad; }
	.provider-delete:focus-visible { outline: 2px solid rgba(94, 165, 246, .55); outline-offset: 1px; }
	.list-empty { padding: 24px 12px; color: #788392; font-size: 12px; text-align: center; }

	.editor { display: grid; align-content: start; gap: 13px; min-width: 0; padding: 14px 16px 16px; }
	.form-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(220px, .72fr); gap: 10px 13px; }
	.model-row { gap: 7px; }
	.model-row input { min-width: 0; flex: 1; }
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

	@media (max-width: 520px) {
		.editor { padding: 12px; }
		.icon-button,
		.secondary-button,
		.primary-button { height: 44px; min-height: 44px; }
		.icon-button { width: 44px; }
		.options-row { align-items: stretch; }
		.option-checks { width: 100%; }
		.actions { display: grid; grid-template-columns: 1fr 1fr; width: 100%; margin-left: 0; }
		.actions button { width: 100%; }
	}

	@media (prefers-reduced-motion: reduce) { .spinning { animation: none; } }
</style>
