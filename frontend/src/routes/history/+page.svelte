	<script lang="ts">
	import { Api } from '$lib/api';
	import type { EngineDetail, HistoryItem } from '$lib/api/types';
	import { Download, Search, Trash2 } from 'lucide-svelte';
	import { onMount } from 'svelte';

	let items = $state<HistoryItem[]>([]);
	let engines = $state<EngineDetail[]>([]);
	let selectedIds = $state<string[]>([]);
	let query = $state('');
	let engineFilter = $state('all');
	let sortBy = $state<'latest' | 'oldest' | 'duration_desc'>('latest');

	const engineOptions = $derived(['all', ...new Set(items.map((item) => item.engine_id))]);
	const visibleItems = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const filtered = items.filter((item) => {
			if (engineFilter !== 'all' && item.engine_id !== engineFilter) return false;
			if (!q) return true;
			return (
				item.input_text.toLowerCase().includes(q) ||
				(item.voice_name ?? '').toLowerCase().includes(q) ||
				item.engine_id.toLowerCase().includes(q)
			);
		});
		return filtered.sort((a, b) => {
			if (sortBy === 'oldest') return a.created_at.localeCompare(b.created_at);
			if (sortBy === 'duration_desc') return (b.duration_ms ?? 0) - (a.duration_ms ?? 0);
			return b.created_at.localeCompare(a.created_at);
		});
	});
	const allVisibleSelected = $derived(
		visibleItems.length > 0 &&
			visibleItems.every((item) => selectedIds.includes(item.result_id))
	);

	async function refresh() {
		[items, engines] = await Promise.all([Api.history(), Api.engines()]);
	}

	onMount(() => {
		refresh();
	});

	function toggleSelect(resultId: string, checked: boolean) {
		selectedIds = checked
			? [...selectedIds, resultId]
			: selectedIds.filter((item) => item !== resultId);
	}

	async function deleteOne(resultId: string) {
		await Api.deleteHistory(resultId);
		selectedIds = selectedIds.filter((item) => item !== resultId);
		await refresh();
	}

	async function deleteSelected() {
		await Promise.all(selectedIds.map((resultId) => Api.deleteHistory(resultId)));
		selectedIds = [];
		await refresh();
	}

	function toggleVisibleSelection() {
		if (allVisibleSelected) {
			selectedIds = selectedIds.filter(
				(resultId) => !visibleItems.some((item) => item.result_id === resultId)
			);
			return;
		}
		selectedIds = Array.from(
			new Set([...selectedIds, ...visibleItems.map((item) => item.result_id)])
		);
	}

	const engineMap = $derived(new Map(engines.map((engine) => [engine.manifest.engine_id, engine])));

	function engineKind(engineId: string) {
		return engineMap.get(engineId)?.manifest.engine_type ?? (engineId.startsWith('mimo-') ? 'cloud' : 'local');
	}

	function engineTypeLabel(engineId: string) {
		return engineKind(engineId) === 'cloud' ? '云端' : '本地';
	}

	function formatTime(value: string) {
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat('zh-CN', {
			month: '2-digit',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit'
		}).format(date);
	}
</script>

<svelte:head><title>历史记录 - 声音工作台</title></svelte:head>
<main class="page">
	<div class="page-head">
		<div>
			<h1>历史记录</h1>
			<p class="muted">按文本、引擎和时长回看生成结果；内容多起来时也能快速筛选和批量清理。</p>
		</div>
	</div>

	<section class="panel stack">
		<div class="toolbar-grid">
			<label class="field">
				<span>搜索</span>
				<div class="search-field">
					<Search size={15} />
					<input bind:value={query} placeholder="文本、音色、引擎" />
				</div>
			</label>
			<label class="field">
				<span>引擎</span>
				<select bind:value={engineFilter}>
					{#each engineOptions as option}
						<option value={option}>{option === 'all' ? '全部' : option}</option>
					{/each}
				</select>
			</label>
			<label class="field">
				<span>排序</span>
				<select bind:value={sortBy}>
					<option value="latest">最新优先</option>
					<option value="oldest">最早优先</option>
					<option value="duration_desc">时长最长</option>
				</select>
			</label>
			<div class="stack summary-box">
				<span class="muted">共 {visibleItems.length} 条</span>
				{#if selectedIds.length}<span class="badge ok">已选 {selectedIds.length}</span>{/if}
			</div>
		</div>

		<div class="row wrap">
			<button class="btn" onclick={toggleVisibleSelection} disabled={!visibleItems.length}>
				{allVisibleSelected ? '取消全选当前筛选' : '全选当前筛选'}
			</button>
			<button class="btn danger" onclick={deleteSelected} disabled={!selectedIds.length}>
				<Trash2 size={15} /> 批量删除
			</button>
		</div>

		{#if visibleItems.length}
			<div class="history-grid">
				{#each visibleItems as item}
					<article class={`card stack history-card engine-surface ${engineKind(item.engine_id) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
						<div class="row" style="justify-content:space-between">
							<div class="row">
								<input
									type="checkbox"
									checked={selectedIds.includes(item.result_id)}
									onchange={(event) => toggleSelect(item.result_id, (event.currentTarget as HTMLInputElement).checked)}
								/>
								<strong class="history-title" title={item.input_text}>{item.input_text}</strong>
							</div>
							<span class="badge badge-kind">{engineTypeLabel(item.engine_id)}</span>
						</div>
						<div class="row wrap">
							<span class="badge engine">{engineMap.get(item.engine_id)?.manifest.display_name ?? item.engine_id}</span>
							{#if item.voice_name}<span class="badge">{item.voice_name}</span>{/if}
							<span class="badge">{item.duration_ms ? `${(item.duration_ms / 1000).toFixed(1)}s` : '-'}</span>
							<span class="badge">{formatTime(item.created_at)}</span>
						</div>
						<audio class="audio" controls src={`/api/history/${item.result_id}/audio`}></audio>
						<div class="row wrap">
							<a class="btn" href={`/api/history/${item.result_id}/audio`}><Download size={15} /> 下载</a>
							<button class="btn danger" onclick={() => deleteOne(item.result_id)}><Trash2 size={15} /> 删除</button>
						</div>
					</article>
				{/each}
			</div>
		{:else}
			<div class="empty">当前筛选下没有生成历史</div>
		{/if}
	</section>
</main>

<style>
	.toolbar-grid {
		display: grid;
		grid-template-columns: minmax(0, 1.5fr) minmax(180px, 0.7fr) minmax(180px, 0.7fr) minmax(120px, 0.5fr);
		gap: 12px;
		align-items: end;
	}

	.search-field {
		display: flex;
		align-items: center;
		gap: 8px;
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 0 10px;
		background: #0f1216;
	}

	.search-field input {
		border: 0;
		background: transparent;
		width: 100%;
		min-height: 34px;
		color: inherit;
		outline: none;
	}

	.summary-box {
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
		min-height: 100%;
	}

	.history-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
		gap: 12px;
	}

	.history-card {
		gap: 10px;
		padding: 10px;
	}

	.history-title {
		display: block;
		max-width: min(100%, 320px);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	@media (max-width: 1080px) {
		.toolbar-grid {
			grid-template-columns: 1fr 1fr;
		}
	}

	@media (max-width: 720px) {
		.toolbar-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
