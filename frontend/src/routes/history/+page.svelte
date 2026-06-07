<script lang="ts">
	import { Api } from '$lib/api';
	import type { HistoryItem } from '$lib/api/types';
	import { Download, Trash2 } from 'lucide-svelte';

	let items = $state<HistoryItem[]>([]);
	async function refresh() { items = await Api.history(); }
	$effect(() => { refresh(); });
</script>

<svelte:head><title>历史记录 - 声音工作台</title></svelte:head>
<main class="page">
	<div class="page-head"><div><h1>历史记录</h1><p class="muted">生成历史、试听、下载和删除</p></div></div>
	<section class="grid">
		{#each items as item}
			<article class="card stack">
				<div class="row" style="justify-content:space-between"><strong>{item.input_text.slice(0, 52)}{item.input_text.length > 52 ? '...' : ''}</strong><span class="badge">{item.engine_id}</span></div>
				<p class="muted">{item.voice_name ?? '未命名声音'} · {item.duration_ms ? (item.duration_ms / 1000).toFixed(1) + 's' : '-'}</p>
				<audio class="audio" controls src={`/api/history/${item.result_id}/audio`}></audio>
				<div class="row"><a class="btn" href={`/api/history/${item.result_id}/audio`}><Download size={15} /> 下载</a><button class="btn danger" onclick={async () => { await Api.deleteHistory(item.result_id); await refresh(); }}><Trash2 size={15} /> 删除</button></div>
			</article>
		{:else}
			<div class="empty">暂无生成历史</div>
		{/each}
	</section>
</main>
