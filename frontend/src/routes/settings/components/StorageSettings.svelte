<script lang="ts">
	import { Api } from '$lib/api';
	import type { StorageAudit, StorageLocation } from '$lib/api/types';
	import { ChevronDown, Database, FolderOpen, HardDrive, RefreshCw, Trash2 } from 'lucide-svelte';
	import { onMount } from 'svelte';

	let audit = $state<StorageAudit | null>(null);
	let message = $state('');
	let busy = $state(false);
	let cleanupBusy = $state('');
	let openingBusy = $state('');

	const cleanupLocations = $derived((audit?.locations ?? []).filter((location) => location.cleanup_key));

	onMount(refresh);

	async function refresh() {
		busy = true;
		try {
			audit = await Api.settingsStorage();
		} catch (error) {
			message = error instanceof Error ? error.message : '存储信息加载失败';
		} finally {
			busy = false;
		}
	}

	async function cleanup(location: StorageLocation) {
		if (!location.cleanup_key) return;
		const warning =
			location.cleanup_risk === 'high'
				? '这会删除 ASR 源音频。历史文字仍会保留，但之后无法基于源音频补时间戳。确认继续？'
				: location.cleanup_risk === 'medium'
					? '这会删除日志类文件，可能影响历史问题排查。确认继续？'
					: `确认清理「${location.label}」？`;
		if (!window.confirm(warning)) return;
		cleanupBusy = location.cleanup_key;
		try {
			const result = await Api.cleanupSettingsStorage([location.cleanup_key]);
			message = `已清理 ${formatBytes(result.removed_bytes)}`;
			await refresh();
		} catch (error) {
			message = error instanceof Error ? error.message : '清理失败';
		} finally {
			cleanupBusy = '';
			setTimeout(() => (message = ''), 2400);
		}
	}

	async function open(location: StorageLocation) {
		openingBusy = location.key;
		try {
			const result = await Api.openSettingsStorageLocation(location.key);
			message = `已打开：${result.path}`;
		} catch (error) {
			message = error instanceof Error ? error.message : '打开目录失败';
		} finally {
			openingBusy = '';
			setTimeout(() => (message = ''), 2400);
		}
	}

	function formatBytes(value: number | null | undefined) {
		const bytes = value ?? 0;
		if (bytes < 1024) return `${bytes} B`;
		const units = ['KB', 'MB', 'GB', 'TB'];
		let size = bytes / 1024;
		let unit = units[0];
		for (let index = 1; index < units.length && size >= 1024; index += 1) {
			size /= 1024;
			unit = units[index];
		}
		return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${unit}`;
	}

	function fileCount(location: StorageLocation) {
		return `${location.file_count}${location.truncated ? '+' : ''} 个文件`;
	}

	function riskLabel(risk: string | null) {
		if (risk === 'high') return '谨慎';
		if (risk === 'medium') return '日志';
		if (risk === 'low') return '低风险';
		return '只读';
	}
</script>

<div class="storage-settings">
	<div class="section-toolbar">
		<div>
			<h2>存储概览</h2>
			<p>查看各目录占用，打开位置，或清理明确允许删除的内容。</p>
		</div>
		<div class="toolbar-actions">
			{#if message}<span class="status" role="status">{message}</span>{/if}
			<span class="total"><HardDrive size={14} /> 生成与缓存 {formatBytes(audit?.total_bytes)}</span>
			<button class="icon-button" type="button" aria-label="刷新存储信息" title="刷新存储信息" onclick={refresh} disabled={busy}>
				<span class:spinning={busy}><RefreshCw size={16} /></span>
			</button>
		</div>
	</div>

	{#if audit}
		<div class="location-list">
			{#each audit.locations as location}
				<article class="location-row">
					<span class="location-icon" aria-hidden="true"><Database size={18} /></span>
					<div class="location-copy">
						<div class="location-name">
							<strong>{location.label}</strong>
							<span>{location.category}</span>
							{#if !location.exists}<span class="warning">未创建</span>{/if}
						</div>
						<p>{location.description}</p>
						<code title={location.path}>{location.path}</code>
					</div>
					<div class="location-meta">
						<strong>{formatBytes(location.size_bytes)}</strong>
						<span>{fileCount(location)}</span>
						<span class:risk-low={location.cleanup_risk === 'low'} class:risk-medium={location.cleanup_risk === 'medium'} class:risk-high={location.cleanup_risk === 'high'} class="risk">{riskLabel(location.cleanup_risk)}</span>
					</div>
					<div class="location-actions">
						<button class="secondary-button" type="button" onclick={() => open(location)} disabled={openingBusy === location.key}><FolderOpen size={14} /> 打开</button>
						{#if location.cleanup_key}
							<button class="danger-button" type="button" onclick={() => cleanup(location)} disabled={cleanupBusy === location.cleanup_key}><Trash2 size={14} /> {location.cleanup_label}</button>
						{/if}
					</div>
				</article>
			{/each}
		</div>

		{#if cleanupLocations.length}
			<div class="cleanup-strip">
				<span>快捷清理</span>
				{#each cleanupLocations as location}
					<button type="button" onclick={() => cleanup(location)} disabled={cleanupBusy === location.cleanup_key}><Trash2 size={13} /> {location.cleanup_label}</button>
				{/each}
			</div>
		{/if}

		<details class="flow-details">
			<summary><span><Database size={15} /> 文件从哪里来</span><ChevronDown class="flow-chevron" size={15} /></summary>
			<div class="flow-list">
				{#each audit.flows as flow}
					<div class="flow-row">
						<strong>{flow.name}</strong>
						<code title={flow.path}>{flow.path}</code>
						<p>{flow.description}</p>
					</div>
				{/each}
			</div>
		</details>
	{:else}
		<div class="loading">{busy ? '正在读取存储信息…' : message || '暂无存储信息'}</div>
	{/if}
</div>

<style>
	.storage-settings {
		border: 1px solid rgba(148, 163, 184, 0.16);
		border-radius: 11px;
		background: rgba(17, 22, 30, 0.82);
		overflow: hidden;
	}

	.section-toolbar,
	.toolbar-actions,
	.location-name,
	.location-meta,
	.location-actions,
	.cleanup-strip,
	.cleanup-strip button,
	.flow-details summary,
	.flow-details summary span,
	.icon-button,
	.secondary-button,
	.danger-button,
	.total {
		display: flex;
		align-items: center;
	}

	.section-toolbar { justify-content: space-between; gap: 14px; padding: 12px 14px; border-bottom: 1px solid rgba(148, 163, 184, 0.13); }
	h2 { margin: 0; font-size: 15px; }
	.section-toolbar p { margin: 4px 0 0; color: #7f8997; font-size: 12px; }
	.toolbar-actions { justify-content: flex-end; gap: 8px; }
	.status { max-width: 280px; overflow: hidden; color: #78dcaa; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
	.total { gap: 5px; min-height: 30px; padding: 0 9px; border: 1px solid rgba(148, 163, 184, 0.17); border-radius: 8px; color: #9ca7b5; font-size: 11px; }

	.icon-button,
	.secondary-button,
	.danger-button {
		justify-content: center;
		gap: 6px;
		min-height: var(--settings-control-height, 34px);
		border: 1px solid rgba(148, 163, 184, 0.2);
		border-radius: var(--settings-control-radius, 7px);
		background: #1a2029;
		color: #d9e0e8;
		font-size: 11px;
	}
	.icon-button { width: var(--settings-control-height, 34px); padding: 0; }
	.secondary-button,
	.danger-button { padding: 0 9px; }
	.danger-button { border-color: rgba(228, 89, 94, .25); background: rgba(106, 36, 40, .26); color: #ffabad; }
	button:disabled { cursor: not-allowed; opacity: .45; }

	.location-list { display: grid; }
	.location-row { display: grid; grid-template-columns: 32px minmax(0, 1fr) 100px auto; align-items: center; gap: 11px; min-height: 82px; padding: 10px 14px; border-bottom: 1px solid rgba(148, 163, 184, 0.11); }
	.location-row:last-child { border-bottom: 0; }
	.location-row:hover { background: rgba(255, 255, 255, 0.018); }
	.location-icon { display: grid; width: 30px; height: 30px; place-items: center; border: 1px solid rgba(148, 163, 184, .15); border-radius: 8px; color: #aeb8c5; }
	.location-copy { min-width: 0; }
	.location-name { gap: 7px; }
	.location-name strong { color: #edf1f5; font-size: 13px; }
	.location-name span { padding: 2px 6px; border-radius: 999px; background: rgba(123, 144, 168, .11); color: #84909e; font-size: 9px; }
	.location-name .warning { color: #eac36b; }
	.location-copy p { margin: 3px 0; color: #778291; font-size: 10px; line-height: 1.4; }
	code { display: block; max-width: 100%; overflow: hidden; color: #9eb8d5; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
	.location-meta { align-items: flex-end; flex-direction: column; gap: 3px; color: #737f8e; font-size: 10px; }
	.location-meta strong { color: #cbd3dc; font-size: 12px; }
	.risk { margin-top: 2px; padding: 2px 6px; border: 1px solid rgba(148, 163, 184, .16); border-radius: 999px; }
	.risk-low { color: #77dba8; }
	.risk-medium { color: #ecc469; }
	.risk-high { color: #ff9c9f; }
	.location-actions { justify-content: flex-end; gap: 6px; flex-wrap: wrap; max-width: 210px; }

	.cleanup-strip { gap: 7px; flex-wrap: wrap; padding: 9px 14px; border-top: 1px solid rgba(148, 163, 184, .11); color: #7d8997; font-size: 11px; }
	.cleanup-strip button { justify-content: center; gap: 5px; min-height: 28px; padding: 0 8px; border: 1px solid rgba(148, 163, 184, .16); border-radius: 7px; background: #171d25; color: #b7c0ca; font-size: 10px; }
	.flow-details { border-top: 1px solid rgba(148, 163, 184, .11); }
	.flow-details summary { justify-content: space-between; min-height: 42px; padding: 9px 14px; color: #aeb8c4; font-size: 12px; cursor: pointer; list-style: none; }
	.flow-details summary span { gap: 7px; }
	.flow-details summary::-webkit-details-marker { display: none; }
	.flow-details[open] summary :global(.flow-chevron) { transform: rotate(180deg); }
	.flow-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; padding: 0 14px 14px; }
	.flow-row { min-width: 0; padding: 11px; border: 1px solid rgba(148, 163, 184, .12); border-radius: 9px; background: rgba(8, 12, 18, .34); }
	.flow-row strong { display: block; margin-bottom: 6px; color: #dce2e9; font-size: 11px; }
	.flow-row p { margin: 6px 0 0; color: #778291; font-size: 10px; line-height: 1.5; }
	.loading { padding: 42px 18px; color: #7f8997; font-size: 12px; text-align: center; }
	.spinning { animation: spin .8s linear infinite; }
	@keyframes spin { to { transform: rotate(360deg); } }

	@media (max-width: 900px) {
		.location-row { grid-template-columns: 36px minmax(0, 1fr) auto; }
		.location-meta { grid-column: 2; align-items: flex-start; flex-direction: row; flex-wrap: wrap; }
		.location-actions { grid-column: 3; grid-row: 1 / span 2; }
	}

	@media (max-width: 680px) {
		.section-toolbar { align-items: stretch; flex-direction: column; padding: 11px 12px; }
		.toolbar-actions { justify-content: flex-start; flex-wrap: wrap; }
		.status { width: 100%; max-width: none; order: 3; }
		.location-row { grid-template-columns: 30px minmax(0, 1fr); min-height: auto; padding: 12px; }
		.location-meta,
		.location-actions { grid-column: 2; grid-row: auto; justify-content: flex-start; max-width: none; }
		.location-actions { flex-wrap: wrap; }
		.location-actions button,
		.cleanup-strip button,
		.icon-button { min-height: var(--settings-control-touch-height, 44px); }
		.icon-button { width: var(--settings-control-touch-height, 44px); }
		.flow-list { grid-template-columns: 1fr; padding-inline: 14px; }
	}

	@media (prefers-reduced-motion: reduce) { .spinning { animation: none; } }
</style>
