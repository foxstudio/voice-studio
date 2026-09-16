<script lang="ts">
 import type { EngineInstallation } from '$lib/api/types';
 import { Download, ExternalLink } from 'lucide-svelte';
 import EnginePopover from './EnginePopover.svelte';
 let { installation, description = '', documentationUrl, label = '详情与来源' }: { installation: EngineInstallation; description?: string; documentationUrl?: string; label?: string } = $props();
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
		add(documentationUrl, '官方说明', 'external');
		return links;
	});
</script>
<EnginePopover id={`model-details-${installation.engine_id}`} {label} size="wide">
			<div class="detail-content">
				{#if description}<p>{description}</p>{/if}
				{#if installation.benchmark_note}<p>{installation.benchmark_note}</p>{/if}
				<p>{installation.license_note}</p>
				<dl>
					{#if installation.architecture}<div><dt>模型架构</dt><dd>{installation.architecture}</dd></div>{/if}
					{#if installation.recommended_for}<div><dt>适合用途</dt><dd>{installation.recommended_for}</dd></div>{/if}
					<div><dt>保存位置</dt><dd>{installation.preferred_path || '无本地模型目录'}</dd></div>
					<div><dt>下载策略</dt><dd>{installation.download_policy}</dd></div>
					<div><dt>复用方式</dt><dd>{installation.reuse_note}</dd></div>
				</dl>
				<div class="source-links">{#each sourceLinks as source}<a href={source.url} target="_blank" rel="noreferrer">{#if source.kind === 'download'}<Download size={12} />{:else}<ExternalLink size={12} />{/if} {source.label}</a>{/each}</div>
			</div>
</EnginePopover>
<style>
	.detail-content { display: grid; gap: 8px; }
	.detail-content p { margin: 0; color: #c6cbd2; font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
	dl { display: grid; gap: 6px; margin: 0; }
	dl div { display: grid; grid-template-columns: 70px minmax(0, 1fr); gap: 9px; padding: 5px 0; border-top: 1px solid rgba(148, 163, 184, .1); font-size: 12px; line-height: 1.5; }
	dt { color: #7d8794; } dd { margin: 0; overflow-wrap: anywhere; color: #b5bdc7; }
	.source-links { display: flex; flex-wrap: wrap; gap: 6px; padding-top: 2px; }
	.source-links a { display: inline-flex; align-items: center; gap: 4px; min-height: 26px; padding: 4px 7px; border: 1px solid #343a43; border-radius: 6px; background: #20242a; color: #b9c9dc; font-size: 12px; text-decoration: none; }
	.source-links a:hover, .source-links a:focus-visible { border-color: #4b5664; background: #272c33; color: #e2e8f0; }
</style>
