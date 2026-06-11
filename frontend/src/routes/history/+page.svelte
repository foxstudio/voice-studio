<script lang="ts">
	import { onMount } from 'svelte';
	import { Download, FileText, History, Play, RefreshCw, Repeat, Search, Square, Trash2, X } from 'lucide-svelte';
	import { Api } from '$lib/api';
	import type { GenerateRequest, HistoryItem } from '$lib/api/types';
	import { formatAudioDuration } from '../generate/helpers';

	type DateFilter = 'all' | 'today' | '7d' | '30d';
	type SortBy = 'latest' | 'oldest' | 'duration_desc';

	let items = $state<HistoryItem[]>([]);
	let query = $state('');
	let engineFilter = $state('all');
	let dateFilter = $state<DateFilter>('all');
	let sortBy = $state<SortBy>('latest');
	let loading = $state(false);
	let busyId = $state('');
	let error = $state('');
	let audio = $state<HTMLAudioElement | null>(null);
	let playingId = $state('');

	const engineOptions = $derived(['all', ...Array.from(new Set(items.map((item) => item.engine_id))).sort()]);
	const hasFilters = $derived(Boolean(query.trim()) || engineFilter !== 'all' || dateFilter !== 'all' || sortBy !== 'latest');
	const filteredItems = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const now = Date.now();
		return items
			.filter((item) => {
				if (engineFilter !== 'all' && item.engine_id !== engineFilter) return false;
				if (dateFilter !== 'all') {
					const created = new Date(item.created_at).getTime();
					if (!Number.isFinite(created)) return false;
					const cutoff = dateFilter === 'today' ? 86_400_000 : dateFilter === '7d' ? 604_800_000 : 2_592_000_000;
					if (now - created > cutoff) return false;
				}
				if (!q) return true;
				return [
					item.input_text,
					item.engine_id,
					item.voice_name ?? '',
					item.voice_id ?? '',
					item.task_id,
					item.result_id
				].some((value) => value.toLowerCase().includes(q));
			})
			.sort((a, b) => {
				if (sortBy === 'oldest') return a.created_at.localeCompare(b.created_at);
				if (sortBy === 'duration_desc') return (b.duration_ms ?? 0) - (a.duration_ms ?? 0);
				return b.created_at.localeCompare(a.created_at);
			});
	});

	function audioUrl(item: HistoryItem) {
		return item.output_audio_id ? `/api/history/${item.result_id}/audio` : '';
	}

	function formatTime(value: string) {
		const date = new Date(value);
		if (!Number.isFinite(date.getTime())) return value;
		return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
	}

	async function loadHistory() {
		loading = true;
		error = '';
		try {
			items = await Api.history();
		} catch (err) {
			error = (err as Error).message;
		} finally {
			loading = false;
		}
	}

	function togglePlayback(item: HistoryItem) {
		const url = audioUrl(item);
		if (!audio || !url) return;
		if (playingId === item.result_id) {
			audio.pause();
			audio.currentTime = 0;
			playingId = '';
			return;
		}
		const absoluteUrl = new URL(url, window.location.href).href;
		if (audio.src !== absoluteUrl) audio.src = url;
		audio.currentTime = 0;
		playingId = item.result_id;
		void audio.play().catch((err) => {
			playingId = '';
			error = (err as Error).message;
		});
	}

	function reuse(item: HistoryItem) {
		const request = {
			...item.parameter_snapshot,
			text: item.input_text,
			engine_id: item.engine_id,
			voice_id: item.voice_id,
			output_format: item.parameter_snapshot.output_format ?? 'wav'
		} as GenerateRequest;
		sessionStorage.setItem('voice-studio-history-reuse', JSON.stringify(request));
		location.href = '/generate';
	}

	async function deleteItem(item: HistoryItem) {
		if (!window.confirm('删除这条历史记录和本地音频？')) return;
		busyId = item.result_id;
		try {
			await Api.deleteHistory(item.result_id);
			items = items.filter((entry) => entry.result_id !== item.result_id);
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busyId = '';
		}
	}

	function clearFilters() {
		query = '';
		engineFilter = 'all';
		dateFilter = 'all';
		sortBy = 'latest';
	}

	onMount(() => {
		void loadHistory();
	});
</script>

<svelte:head>
	<title>历史记录 · Voice Studio</title>
</svelte:head>

<main class="page history-page">
	<audio bind:this={audio} onended={() => (playingId = '')} onpause={() => (playingId = '')}></audio>
	<header class="page-head history-head">
		<div>
			<h1>历史记录</h1>
			<p class="muted">集中管理已生成音频，保留播放、下载、复用参数和删除。</p>
		</div>
		<div class="row history-summary">
			<span class="badge">{items.length} 条</span>
			<span class="badge ok">{filteredItems.length} 当前筛选</span>
		</div>
	</header>

	<section class="panel history-panel">
		<div class="history-toolbar">
			<div class="history-filters">
				<div class="history-search">
					<Search size={14} />
					<input bind:value={query} placeholder="搜索台词、模型、音色、任务 ID" />
				</div>
				<select bind:value={engineFilter}>
					{#each engineOptions as option}
						<option value={option}>{option === 'all' ? '全部模型' : option}</option>
					{/each}
				</select>
				<select bind:value={dateFilter}>
					<option value="all">全部时间</option>
					<option value="today">今天</option>
					<option value="7d">最近 7 天</option>
					<option value="30d">最近 30 天</option>
				</select>
				<select bind:value={sortBy}>
					<option value="latest">最新</option>
					<option value="oldest">最旧</option>
					<option value="duration_desc">时长↓</option>
				</select>
			</div>
			<div class="history-actions">
				{#if hasFilters}
					<button class="gen-icon-btn" type="button" aria-label="清除筛选" data-tooltip="清除当前历史筛选" onclick={clearFilters}><X size={15} /></button>
				{/if}
				<button class="gen-icon-btn" type="button" aria-label="刷新历史" data-tooltip="重新加载历史记录" onclick={loadHistory} disabled={loading}><RefreshCw size={15} /></button>
			</div>
		</div>

		{#if error}<p class="error-line">{error}</p>{/if}
		{#if loading}
			<div class="empty">正在加载历史记录。</div>
		{:else if filteredItems.length}
			<div class="history-grid">
				{#each filteredItems as item}
					<article class="card history-card">
						<div class="history-card-head">
							<FileText size={15} />
							<strong title={item.input_text}>{item.input_text || '未命名记录'}</strong>
							<span class="badge">{formatTime(item.created_at)}</span>
						</div>
						<div class="history-meta">
							<span class="badge engine">{item.engine_id}</span>
							<span class="badge">{item.voice_name || '未选音色'}</span>
							{#if item.longform_segment_count}<span class="badge">长文本 {item.longform_segment_index ?? 0}/{item.longform_segment_count}</span>{/if}
							{#if item.duration_ms}<span class="badge">{formatAudioDuration(item.duration_ms)}</span>{/if}
						</div>
						{#if item.verification}
							<p class={`history-verify ${item.verification.status}`}>校对 {item.verification.status} · 覆盖率 {Math.round(item.verification.coverage * 100)}%</p>
						{:else if item.verification_error}
							<p class="history-verify failed">{item.verification_error}</p>
						{/if}
						<div class="history-card-footer">
							<div class="history-audio">
								<button class="gen-icon-btn" type="button" aria-label={playingId === item.result_id ? '停止播放' : '播放音频'} data-tooltip={playingId === item.result_id ? '停止播放这条历史音频' : '播放这条历史音频'} onclick={() => togglePlayback(item)} disabled={!item.output_audio_id}>
									{#if playingId === item.result_id}<Square size={15} />{:else}<Play size={15} />{/if}
								</button>
								{#if item.output_audio_id}<a class="gen-icon-btn" href={audioUrl(item)} aria-label="下载音频" data-tooltip="下载这条历史音频"><Download size={15} /></a>{/if}
							</div>
							<div class="history-card-actions">
								<button class="gen-icon-btn" type="button" aria-label="复用参数" data-tooltip="把这条记录的文本和参数带回生成页" onclick={() => reuse(item)}><Repeat size={15} /></button>
								<button class="gen-icon-btn danger" type="button" aria-label="删除历史" data-tooltip="删除这条历史记录和音频" onclick={() => deleteItem(item)} disabled={busyId === item.result_id}><Trash2 size={15} /></button>
							</div>
						</div>
					</article>
				{/each}
			</div>
		{:else}
			<div class="empty">当前筛选下没有历史记录。</div>
		{/if}
	</section>
</main>

<style>
	.history-page {
		padding-bottom: 56px;
	}

	.history-head {
		align-items: center;
	}

	.history-summary {
		justify-content: flex-end;
	}

	.history-panel {
		display: grid;
		gap: 14px;
	}

	.history-toolbar {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 10px;
		align-items: start;
	}

	.history-filters {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		min-width: 0;
	}

	.history-search {
		display: flex;
		align-items: center;
		gap: 8px;
		min-width: 260px;
		flex: 1 1 360px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
		padding: 0 10px;
	}

	.history-search input {
		border: 0;
		background: transparent;
		min-height: 32px;
		padding: 0;
		outline: 0;
	}

	.history-filters select {
		min-width: 132px;
		flex: 0 1 150px;
	}

	.history-actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
	}

	.history-page .gen-icon-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 30px;
		height: 30px;
		padding: 0;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #12161c;
		color: #d6deea;
	}

	.history-page .gen-icon-btn:hover:not(:disabled) {
		border-color: rgba(79, 156, 249, 0.45);
		background: #17202b;
	}

	.history-page .gen-icon-btn.danger {
		color: #ffb6ad;
		border-color: rgba(244, 108, 95, 0.28);
		background: rgba(244, 108, 95, 0.08);
	}

	.history-page .gen-icon-btn:disabled {
		opacity: 0.42;
		cursor: not-allowed;
	}

	.history-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
		gap: 12px;
	}

	.history-card {
		display: grid;
		gap: 10px;
		min-width: 0;
	}

	.history-card-head {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		gap: 8px;
		align-items: start;
	}

	.history-card-head strong {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 14px;
		line-height: 1.35;
	}

	.history-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.history-verify {
		margin: 0;
		font-size: 12px;
		color: var(--muted);
	}

	.history-verify.passed {
		color: #42c49b;
	}

	.history-verify.warning {
		color: #e5a842;
	}

	.history-verify.failed {
		color: #e54d4d;
	}

	.history-card-footer {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 10px;
		align-items: center;
		margin-top: auto;
	}

	.history-audio,
	.history-card-actions {
		display: flex;
		gap: 8px;
		align-items: center;
	}

	.history-card-actions {
		justify-content: flex-end;
	}

	.error-line {
		margin: 0;
		color: #ff9a9a;
		font-size: 13px;
	}

	@media (max-width: 760px) {
		.history-toolbar {
			grid-template-columns: 1fr;
		}

		.history-actions {
			justify-content: flex-end;
		}

		.history-search,
		.history-filters select {
			min-width: 100%;
			flex-basis: 100%;
		}
	}
</style>
