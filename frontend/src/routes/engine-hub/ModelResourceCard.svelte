<script lang="ts">
	import type { EngineInstallation } from '$lib/api/types';
	import { Download, ExternalLink, FolderSymlink, Trash2 } from 'lucide-svelte';
	import { modelInstallationGuidance } from '$lib/model-installation-presentation';
	import EnginePopover from './EnginePopover.svelte';
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
	const sourceLinks = $derived.by(() => {
		const links: Array<{ url: string; label: string; kind: 'download' | 'external' }> = [];
		const seen = new Set<string>();
		const add = (url: string | null | undefined, label: string, kind: 'download' | 'external') => {
			if (!url || seen.has(url)) return;
			seen.add(url);
			links.push({ url, label, kind });
		};
		for (const source of installation.download_sources) add(source.url, source.label, 'download');
		add(installation.source_url, installation.source_label, 'external');
		add(installation.runtime_url, installation.runtime_label ?? '运行时', 'external');
		return links;
	});
</script>

<article class="panel resource-card">
	<div class="resource-identity">
		<div class="resource-title">
			<strong>{displayName ?? installation.display_name ?? installation.engine_id}</strong>
			<span class="badge badge-kind">{roleLabel}</span>
			<span class="badge" class:ok={availability.tone === 'ok'} class:fail={availability.tone === 'fail'} class:warning={availability.tone === 'warning'}>{availability.label}</span>
		</div>
		<p>{installation.architecture}{installation.recommended_for ? ` · ${installation.recommended_for}` : ''}</p>
	</div>

	<div class="resource-model">
		<div class="model-meta">
			<span title={installation.preferred_path ?? ''}><FolderSymlink size={13} /> {installation.preferred_path || '不保存本地权重'}</span>
			{#if sizeLabel}<strong>{sizeLabel}</strong>{/if}
		</div>
		{#if installation.installation_status === 'installing'}
			<div class="download-progress" aria-label="模型下载进度"><div style={`width: ${Math.max(1, Math.round((installation.progress ?? 0) * 100))}%`}></div></div>
			<small>{Math.round((installation.progress ?? 0) * 100)}% · {formatBytes(installation.downloaded_bytes)} / {formatBytes(installation.total_bytes)}</small>
		{:else if modelInstallationGuidance(installation)}
			<small>{modelInstallationGuidance(installation)}</small>
		{:else}
			<small>{installation.reference_only ? '参考版本已归入模型家族；不参与自动选择，也不能在本页启动。' : (installation.reuse_note || availability.detail)}</small>
		{/if}
		{#if installation.error}<small class="error">{installation.error}</small>{/if}
	</div>

	<div class="resource-actions">
		{#if installation.installed && onUninstall}
			<button class="btn mini-btn" disabled={busy} onclick={() => onUninstall?.(installation.engine_id)}><Trash2 size={13} /> 删除模型</button>
		{:else if !installation.installed && installation.automatic_download_supported}
			<button class="btn primary mini-btn" disabled={busy || installation.installation_status === 'installing'} onclick={() => onInstall(installation)}><Download size={13} /> {installation.installation_status === 'installing' ? '安装中' : '下载模型'}</button>
		{:else if !installation.installed}
			<span class="badge fail">暂不支持一键安装</span>
		{/if}
		<EnginePopover id={`resource-${installation.engine_id}`} label="详情与来源" size="wide">
			<div class="detail-content">
				{#if installation.benchmark_note}<p>{installation.benchmark_note}</p>{/if}
				<p>{installation.license_note}</p>
				<dl>
					<div><dt>保存位置</dt><dd>{installation.preferred_path || '无本地模型目录'}</dd></div>
					<div><dt>下载策略</dt><dd>{installation.download_policy}</dd></div>
					<div><dt>复用方式</dt><dd>{installation.reuse_note}</dd></div>
				</dl>
				<div class="source-links">{#each sourceLinks as source}<a href={source.url} target="_blank" rel="noreferrer">{#if source.kind === 'download'}<Download size={12} />{:else}<ExternalLink size={12} />{/if} {source.label}</a>{/each}</div>
			</div>
		</EnginePopover>
	</div>
</article>

<style>
	.resource-card { display: grid; grid-template-columns: var(--engine-card-columns, minmax(260px, 1fr) minmax(290px, 1.05fr) minmax(360px, auto)); align-items: center; gap: var(--engine-card-gap, 12px); min-height: 98px; padding: 10px 12px; border-left: 3px solid var(--hub-resource-color, #64748b); }
	.resource-identity, .resource-model { display: grid; gap: 5px; min-width: 0; }
	.resource-title { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
	.resource-title strong { color: #edf2f7; font-size: 13px; }
	p, small { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.45; }
	.model-meta { display: flex; align-items: center; gap: 10px; min-width: 0; color: var(--muted); font-size: 10px; }
	.model-meta span { display: flex; align-items: center; gap: 5px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.model-meta strong { flex: none; color: #c9d3df; font-size: 10px; }
	.resource-actions { display: flex; justify-content: flex-end; align-items: center; flex-wrap: wrap; gap: 6px; }
	.mini-btn { min-height: 26px; padding: 3px 8px; gap: 4px; border-radius: 6px; font-size: 10px; line-height: 1.1; }
	.download-progress { height: 4px; overflow: hidden; border-radius: 999px; background: rgba(255, 255, 255, .08); }
	.download-progress div { height: 100%; border-radius: inherit; background: #4f9cf9; transition: width 180ms ease; }
	.error { color: #ff9b9b; }
	.badge.warning { border-color: rgba(245, 184, 78, .32); background: rgba(245, 184, 78, .09); color: #eac884; }
	.detail-content { display: grid; gap: 8px; }
	.detail-content p { margin: 0; color: #c6cbd2; }
	dl { display: grid; gap: 6px; margin: 0; }
	dl div { display: grid; grid-template-columns: 70px minmax(0, 1fr); gap: 9px; padding: 5px 0; border-top: 1px solid rgba(148, 163, 184, .1); font-size: 9px; line-height: 1.5; }
	dt { color: #7d8794; } dd { margin: 0; overflow-wrap: anywhere; color: #b5bdc7; }
	.source-links { display: flex; flex-wrap: wrap; gap: 6px; padding-top: 2px; }
	.source-links a { display: inline-flex; align-items: center; gap: 4px; min-height: 26px; padding: 4px 7px; border: 1px solid #343a43; border-radius: 6px; background: #20242a; color: #b9c9dc; font-size: 9px; text-decoration: none; }
	.source-links a:hover, .source-links a:focus-visible { border-color: #4b5664; background: #272c33; color: #e2e8f0; }
	@media (max-width: 1120px) { .resource-card { grid-template-columns: minmax(230px, .8fr) minmax(250px, 1fr); } .resource-actions { grid-column: 1 / -1; justify-content: flex-start; } }
	@media (max-width: 760px) { .resource-card { grid-template-columns: 1fr; min-height: 0; padding: 12px; } .resource-actions { grid-column: 1; justify-content: flex-start; } }
	@media (prefers-reduced-motion: reduce) { .download-progress div { transition: none; } }
</style>
