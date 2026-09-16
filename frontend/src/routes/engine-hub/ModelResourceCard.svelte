<script lang="ts">
	import type { EngineInstallation } from '$lib/api/types';
	import { Download, Trash2 } from 'lucide-svelte';
	import { modelInstallationGuidance } from '$lib/model-installation-presentation';
	import ModelDetails from './ModelDetails.svelte';
	import { resourceAvailability, resourceRoleLabel, resourceSizeLabel } from './engine-hub-presentation';

	let {
		installation,
		displayName,
		busy = false,
		onInstall,
		onUninstall
	}: {
		installation: EngineInstallation;
		displayName?: string;
		busy?: boolean;
		onInstall: (installation: EngineInstallation) => void;
		onUninstall?: (engineId: string) => void;
	} = $props();

	function formatBytes(value: number | undefined) {
		const bytes = Number(value ?? 0);
		if (!bytes) return '0 MB';
		if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
		return `${Math.round(bytes / 1_000_000)} MB`;
	}

	const sizeLabel = $derived(resourceSizeLabel(installation));
	const availability = $derived(resourceAvailability(installation));
	const roleLabel = $derived(resourceRoleLabel(installation));
</script>

<article class="panel resource-card">
	<div class="resource-identity">
		<div class="resource-title">
			<strong>{displayName ?? installation.display_name ?? installation.engine_id}</strong>
			<span class="badge" class:ok={availability.tone === 'ok'} class:fail={availability.tone === 'fail'} class:warning={availability.tone === 'warning'}>{availability.label}</span>
		</div>
		{#if installation.architecture || installation.recommended_for}
			<p>{installation.architecture}{installation.recommended_for ? ` · ${installation.recommended_for}` : ''}</p>
		{/if}
		<div class="compact-tags feature-tags"><span class="badge badge-kind">{roleLabel}</span></div>
	</div>

	<div class="resource-model">
		<div class="model-meta">
			<span>{availability.detail}</span>
			{#if sizeLabel}<strong>{sizeLabel}</strong>{/if}
		</div>
		{#if installation.installation_status === 'installing'}
			<div class="download-progress" aria-label="模型下载进度"><div style={`width: ${Math.max(1, Math.round((installation.progress ?? 0) * 100))}%`}></div></div>
			<small>{Math.round((installation.progress ?? 0) * 100)}% · {formatBytes(installation.downloaded_bytes)} / {formatBytes(installation.total_bytes)}</small>
		{:else if modelInstallationGuidance(installation)}
			<small>{modelInstallationGuidance(installation)}</small>
		{:else if installation.reference_only}
			<small>参考版本已归入模型家族；不参与自动选择，也不能在本页启动。</small>
		{/if}
		{#if installation.error}<small class="error">{installation.error}</small>{/if}
	</div>

	<div class="resource-actions">
		{#if installation.installed && onUninstall}
			<button class="btn mini-btn" disabled={busy} onclick={() => onUninstall?.(installation.engine_id)}><Trash2 size={13} /> 删除模型</button>
		{:else if !installation.installed && installation.automatic_download_supported}
			<button class="btn primary mini-btn" disabled={busy || installation.installation_status === 'installing'} onclick={() => onInstall(installation)}><Download size={13} /> {installation.installation_status === 'installing' ? '安装中' : '下载模型'}</button>
		{:else if !installation.installed}
			<span class="badge">暂不支持一键安装</span>
		{/if}
		<ModelDetails {installation} />
	</div>
</article>

<style>
	.resource-card { display: grid; grid-template-columns: var(--engine-card-columns, minmax(260px, 1fr) minmax(290px, 1.05fr) minmax(360px, auto)); align-items: center; gap: var(--engine-card-gap, 12px); min-height: 112px; padding: 10px 12px; border-left: 3px solid var(--hub-resource-color, #64748b); }
	.resource-identity, .resource-model { display: grid; gap: 6px; min-width: 0; }
	.resource-title { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
	.resource-title strong { color: #edf2f7; font-size: 13px; }
	p, small { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.45; }
	.compact-tags { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 4px; }
	.compact-tags .badge { padding: 1px 6px; font-size: 11px; line-height: 1.35; }
	.model-meta { display: flex; align-items: center; gap: 10px; min-width: 0; color: var(--muted); font-size: 12px; }
	.model-meta span { display: flex; align-items: center; gap: 5px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.model-meta strong { flex: none; color: #c9d3df; font-size: 12px; }
	.resource-actions { display: flex; justify-content: flex-end; align-items: center; flex-wrap: wrap; gap: 6px; }
	.mini-btn { min-height: 32px; padding: 3px 8px; gap: 4px; border-radius: 6px; font-size: 12px; line-height: 1.1; }
	.download-progress { height: 4px; overflow: hidden; border-radius: 999px; background: rgba(255, 255, 255, .08); }
	.download-progress div { height: 100%; border-radius: inherit; background: #4f9cf9; transition: width 180ms ease; }
	.error { color: #ff9b9b; }
	.badge.warning { border-color: rgba(245, 184, 78, .32); background: rgba(245, 184, 78, .09); color: #eac884; }
	@media (max-width: 1120px) { .resource-card { grid-template-columns: minmax(230px, .8fr) minmax(250px, 1fr); } .resource-actions { grid-column: 1 / -1; justify-content: flex-start; } }
	@media (max-width: 760px) { .resource-card { grid-template-columns: 1fr; min-height: 0; padding: 12px; } .resource-actions { grid-column: 1; justify-content: flex-start; } }
	@media (prefers-reduced-motion: reduce) { .download-progress div { transition: none; } }
</style>
