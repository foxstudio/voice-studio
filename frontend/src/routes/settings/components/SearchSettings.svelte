<script lang="ts">
	import { Api } from '$lib/api';
	import type { WebSearchProvider, WebSearchSettings } from '$lib/api/types';
	import { Activity, Save, Search } from 'lucide-svelte';
	import { onMount } from 'svelte';
	import SettingsCheck from './SettingsCheck.svelte';
	import SettingsField from './SettingsField.svelte';

	let settings = $state<WebSearchSettings | null>(null);
	let apiKey = $state('');
	let clearApiKey = $state(false);
	let busy = $state<'load' | 'save' | 'test' | ''>('load');
	let message = $state('');
	let failed = $state(false);
	const providerDescription = $derived(
		settings?.provider === 'tavily'
			? '通用网页搜索，免费额度无需信用卡。'
			: settings?.provider === 'wikipedia'
				? '完全免费但覆盖有限，适合作为基础兜底。'
				: '使用你自己的 SearXNG JSON API，不依赖商业搜索服务。'
	);

	onMount(load);

	async function load() {
		busy = 'load';
		try {
			settings = await Api.webSearchSettings();
		} catch (error) {
			show(error instanceof Error ? error.message : '搜索设置加载失败', true);
		} finally {
			busy = '';
		}
	}

	function show(value: string, isError = false) {
		message = value;
		failed = isError;
	}

	function changeProvider(event: Event) {
		if (!settings) return;
		settings.provider = (event.currentTarget as HTMLSelectElement).value as WebSearchProvider;
		if (settings.provider !== 'searxng') settings.base_url = '';
	}

	async function save(silent = false) {
		if (!settings) return false;
		busy = 'save';
		try {
			settings = await Api.saveWebSearchSettings({
				enabled: settings.enabled,
				provider: settings.provider,
				base_url: settings.base_url.trim(),
				api_key: apiKey.trim() || undefined,
				clear_api_key: clearApiKey,
				max_queries: settings.max_queries,
				max_results_per_query: settings.max_results_per_query
			});
			apiKey = '';
			clearApiKey = false;
			if (!silent) show('搜索设置已保存');
			return true;
		} catch (error) {
			show(error instanceof Error ? error.message : '搜索设置保存失败', true);
			return false;
		} finally {
			busy = '';
		}
	}

	async function testConnection() {
		if (!(await save(true))) return;
		busy = 'test';
		try {
			const result = await Api.testWebSearch();
			show(result.message);
		} catch (error) {
			show(error instanceof Error ? error.message : '搜索连接测试失败', true);
		} finally {
			busy = '';
		}
	}
</script>

<section class="search-settings" aria-labelledby="web-search-title">
	<header>
		<div class="title"><Search size={17} /><div><h2 id="web-search-title">按需联网检索</h2><p>语言模型先判断是否需要；搜索词会发给所选服务，敏感音频请关闭。</p></div></div>
		{#if message}<span class:failed class="message" role={failed ? 'alert' : 'status'}>{message}</span>{/if}
	</header>
	{#if settings}
		<div class="form-grid">
			<SettingsField label="搜索服务" controlId="web-search-provider">
				<select id="web-search-provider" value={settings.provider} onchange={changeProvider}>
					<option value="tavily">Tavily · 每月 1000 免费额度</option>
					<option value="wikipedia">Wikipedia · 无需 Key</option>
					<option value="searxng">SearXNG · 自建服务</option>
				</select>
				{#snippet description()}{providerDescription}{/snippet}
			</SettingsField>
			<SettingsField label="每次最多查询" controlId="web-search-query-limit">
				<select id="web-search-query-limit" bind:value={settings.max_queries}>
					<option value={1}>1 条</option><option value={2}>2 条</option><option value={3}>3 条</option>
				</select>
			</SettingsField>
			{#if settings.provider === 'tavily'}
				<SettingsField label="API Key" controlId="web-search-key" wide>
					<input id="web-search-key" type="password" bind:value={apiKey} placeholder={settings.api_key_configured ? '已配置；填写新 Key 可覆盖' : 'tvly-...'} autocomplete="new-password" />
					{#snippet description()}密钥只保存在本机，搜索结果只保存标题、摘要和来源 URL。{/snippet}
				</SettingsField>
			{:else if settings.provider === 'searxng'}
				<SettingsField label="服务地址" controlId="web-search-base" wide>
					<input id="web-search-base" type="url" bind:value={settings.base_url} placeholder="https://search.example.com" />
				</SettingsField>
			{/if}
		</div>
		<div class="actions">
			<div class="checks">
				<SettingsCheck id="web-search-enabled" bind:checked={settings.enabled}>{settings.enabled ? '已允许按需搜索' : '不使用网络搜索'}</SettingsCheck>
				{#if settings.api_key_configured}<SettingsCheck id="web-search-clear" danger bind:checked={clearApiKey} onchange={() => { if (clearApiKey) apiKey = ''; }}>清除已保存的 Key</SettingsCheck>{/if}
			</div>
			<div class="buttons">
				<button type="button" class="secondary" onclick={testConnection} disabled={Boolean(busy)}><Activity size={15} />{busy === 'test' ? '测试中' : '测试搜索'}</button>
				<button type="button" class="primary" onclick={() => save()} disabled={Boolean(busy)}><Save size={15} />{busy === 'save' ? '保存中' : '保存'}</button>
			</div>
		</div>
	{/if}
</section>

<style>
	.search-settings { margin-top: 26px; padding-top: 24px; border-top: 1px solid #222b35; }
	header, .title, .actions, .buttons, .checks { display: flex; align-items: center; }
	header { min-height: 42px; justify-content: space-between; gap: 18px; margin-bottom: 16px; }
	.title { gap: 10px; min-width: 0; }
	h2, p { margin: 0; }
	h2 { font-size: 14px; font-weight: 650; }
	p { margin-top: 3px; color: #8794a3; font-size: 12px; }
	.message { color: #8dd0ae; font-size: 12px; }
	.message.failed { color: #ee8d8d; }
	.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 18px; }
	.actions { justify-content: space-between; gap: 16px; margin-top: 18px; }
	.checks, .buttons { gap: 10px; flex-wrap: wrap; }
	button { min-height: 34px; display: inline-flex; align-items: center; justify-content: center; gap: 7px; border-radius: 7px; padding: 0 12px; font: inherit; cursor: pointer; }
	button:disabled { opacity: .5; cursor: default; }
	.secondary { border: 1px solid #34404d; background: transparent; color: #d8e0e8; }
	.primary { border: 1px solid #4678a8; background: #346895; color: white; }
	@media (max-width: 760px) { .form-grid { grid-template-columns: 1fr; } header, .actions { align-items: flex-start; flex-direction: column; } }
</style>
