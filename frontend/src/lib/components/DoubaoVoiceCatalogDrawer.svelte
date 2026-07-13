<script lang="ts">
	import { browser } from '$app/environment';
	import { Api } from '$lib/api';
	import type { DoubaoSpeakerCatalogStatus, EngineSpeaker } from '$lib/api/types';
	import { Check, Clock3, Heart, Library, Pause, Play, RefreshCw, Search, Sparkles, X } from 'lucide-svelte';
	import { onDestroy, onMount, tick } from 'svelte';
	import {
		buildDoubaoCatalogFacets,
		doubaoCatalogTabCounts,
		EMPTY_DOUBAO_FILTERS,
		filterDoubaoSpeakers,
		mergeRecentIds,
		normalizeGender,
		speakerCategories,
		uniqueStrings,
		type DoubaoCatalogTab,
		type DoubaoVoiceFilters
	} from './doubao-voice-catalog';

	type Props = {
		speakers: EngineSpeaker[];
		mode?: 'drawer' | 'embedded';
		value?: string;
		loading?: boolean;
		recentIds?: string[];
		onChange?: (speakerId: string) => void;
		onRefresh?: () => void | Promise<void>;
	};

	let {
		speakers,
		mode = 'drawer',
		value = $bindable(''),
		loading = false,
		recentIds = [],
		onChange = () => {},
		onRefresh = () => {}
	}: Props = $props();

	const FAVORITES_KEY = 'voice-studio:doubao-speakers:favorites';
	const RECENTS_KEY = 'voice-studio:doubao-speakers:recent-success';
	const DRAWER_WIDTH_KEY = 'voice-studio:doubao-speakers:drawer-width';
	const DRAWER_MIN_WIDTH = 380;
	const DRAWER_MAX_WIDTH = 720;
	let drawerOpen = $state(false);
	let tab: DoubaoCatalogTab = $state('recommended');
	let filters: DoubaoVoiceFilters = $state({ ...EMPTY_DOUBAO_FILTERS });
	let favoriteIds: string[] = $state([]);
	let persistedRecentIds: string[] = $state([]);
	let manualId = $state('');
	let manualIdOpen = $state(false);
	let previewAudio: HTMLAudioElement | null = $state(null);
	let previewingId = $state('');
	let previewErrorId = $state('');
	let drawerEl: HTMLElement | null = $state(null);
	let currentTrigger: HTMLButtonElement | null = $state(null);
	let searchInput: HTMLInputElement | null = $state(null);
	let drawerWidth = $state(480);
	let resizeCleanup: (() => void) | null = null;
	let syncing = $state(false);
	let catalogStatus: DoubaoSpeakerCatalogStatus | null = $state(null);
	let statusError = $state('');

	const byId = $derived(new Map(speakers.map((speaker) => [speaker.speaker_id, speaker])));
	const currentSpeaker = $derived(byId.get(value) ?? fallbackSpeaker(value));
	const allRecentIds = $derived(mergeRecentIds(persistedRecentIds, recentIds));
	const visibleSpeakers = $derived(filterDoubaoSpeakers(speakers, filters, tab, favoriteIds, allRecentIds));
	const filterOptions = $derived(buildDoubaoCatalogFacets(speakers));
	const tabCounts = $derived(doubaoCatalogTabCounts(speakers, favoriteIds, allRecentIds));
	const hasActiveFilters = $derived(Object.entries(filters).some(([key, item]) => key === 'query' ? Boolean(String(item).trim()) : item !== 'all'));

	onMount(() => {
		favoriteIds = readIds(FAVORITES_KEY);
		persistedRecentIds = mergeRecentIds(readIds(RECENTS_KEY), recentIds);
		writeIds(RECENTS_KEY, persistedRecentIds);
		drawerWidth = clampDrawerWidth(Number(localStorage.getItem(DRAWER_WIDTH_KEY)) || 480);
		void loadStatus();
	});

	onDestroy(() => {
		stopPreview();
		resizeCleanup?.();
	});

	$effect(() => {
		if (!browser || !recentIds.length) return;
		const next = mergeRecentIds(persistedRecentIds, recentIds);
		if (next.join('|') === persistedRecentIds.join('|')) return;
		persistedRecentIds = next;
		writeIds(RECENTS_KEY, next);
	});

	$effect(() => {
		if (mode === 'embedded' && !drawerOpen) drawerOpen = true;
	});

	$effect(() => {
		const next = { ...filters };
		let changed = false;
		for (const [key, options] of [
			['gender', filterOptions.genders],
			['age', filterOptions.ages],
			['language', filterOptions.languages],
			['emotion', filterOptions.emotions],
			['category', filterOptions.categories],
			['specialLabel', filterOptions.specialLabels]
		] as const) {
			if (next[key] !== 'all' && !options.some((option) => option.value === next[key])) {
				next[key] = 'all';
				changed = true;
			}
		}
		if (changed) filters = next;
	});

	async function loadStatus() {
		try {
			catalogStatus = await Api.doubaoSpeakerCatalogStatus();
			statusError = '';
		} catch {
			catalogStatus = null;
			statusError = '正在使用本地目录';
		}
	}

	async function syncCatalog() {
		if (syncing) return;
		syncing = true;
		statusError = '';
		try {
			catalogStatus = await Api.syncDoubaoSpeakerCatalog();
			await onRefresh();
		} catch {
			statusError = '本次刷新失败，已保留当前目录';
		} finally {
			syncing = false;
		}
	}

	async function openDrawer() {
		drawerOpen = true;
		await tick();
		searchInput?.focus({ preventScroll: true });
	}

	function closeDrawer() {
		if (mode === 'embedded') return;
		drawerOpen = false;
		stopPreview();
		void tick().then(() => currentTrigger?.focus({ preventScroll: true }));
	}

	function clampDrawerWidth(value: number) {
		const viewportMax = browser ? Math.max(DRAWER_MIN_WIDTH, window.innerWidth - 24) : DRAWER_MAX_WIDTH;
		return Math.round(Math.min(DRAWER_MAX_WIDTH, viewportMax, Math.max(DRAWER_MIN_WIDTH, value)));
	}

	function persistDrawerWidth() {
		if (browser) localStorage.setItem(DRAWER_WIDTH_KEY, String(drawerWidth));
	}

	function beginDrawerResize(event: PointerEvent) {
		if (mode !== 'drawer' || !drawerEl || window.innerWidth <= 720) return;
		event.preventDefault();
		const handle = event.currentTarget as HTMLElement;
		const pointerId = event.pointerId;
		const startX = event.clientX;
		const startWidth = drawerEl.getBoundingClientRect().width;
		handle.setPointerCapture(pointerId);
		const move = (next: PointerEvent) => {
			if (next.pointerId !== pointerId) return;
			drawerWidth = clampDrawerWidth(startWidth + startX - next.clientX);
		};
		const finish = (next?: PointerEvent) => {
			if (next && next.pointerId !== pointerId) return;
			handle.removeEventListener('pointermove', move);
			handle.removeEventListener('pointerup', finish);
			handle.removeEventListener('pointercancel', finish);
			handle.removeEventListener('lostpointercapture', finish);
			if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
			persistDrawerWidth();
			resizeCleanup = null;
		};
		resizeCleanup?.();
		handle.addEventListener('pointermove', move);
		handle.addEventListener('pointerup', finish);
		handle.addEventListener('pointercancel', finish);
		handle.addEventListener('lostpointercapture', finish);
		resizeCleanup = () => finish();
	}

	function handleResizeKeydown(event: KeyboardEvent) {
		if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
		event.preventDefault();
		if (event.key === 'Home') drawerWidth = DRAWER_MIN_WIDTH;
		else if (event.key === 'End') drawerWidth = clampDrawerWidth(DRAWER_MAX_WIDTH);
		else drawerWidth = clampDrawerWidth(drawerWidth + (event.key === 'ArrowLeft' ? 16 : -16));
		persistDrawerWidth();
	}

	function choose(speakerId: string, close = false) {
		const next = speakerId.trim();
		if (!next) return;
		value = next;
		onChange(next);
		if (close) closeDrawer();
	}

	function useManualId() {
		choose(manualId, true);
		manualId = '';
	}

	function toggleFavorite(speakerId: string) {
		favoriteIds = favoriteIds.includes(speakerId) ? favoriteIds.filter((id) => id !== speakerId) : [speakerId, ...favoriteIds];
		writeIds(FAVORITES_KEY, favoriteIds);
	}

	async function togglePreview(speaker: EngineSpeaker) {
		if (!previewAudio) return;
		if (previewingId === speaker.speaker_id && !previewAudio.paused) {
			stopPreview();
			return;
		}
		stopPreview();
		previewErrorId = '';
		previewAudio.src = Api.doubaoSpeakerPreviewUrl(speaker.speaker_id);
		previewingId = speaker.speaker_id;
		try {
			await previewAudio.play();
		} catch {
			previewErrorId = speaker.speaker_id;
			previewingId = '';
		}
	}

	function stopPreview() {
		if (previewAudio) {
			previewAudio.pause();
			previewAudio.currentTime = 0;
		}
		previewingId = '';
	}

	function clearFilters() {
		filters = { ...EMPTY_DOUBAO_FILTERS };
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (mode !== 'drawer' || !drawerOpen) return;
		if (event.key === 'Escape') {
			event.preventDefault();
			closeDrawer();
			return;
		}
		if (event.key !== 'Tab' || !drawerEl) return;
		const focusable = [...drawerEl.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [href], [tabindex]:not([tabindex="-1"])')];
		if (!focusable.length) return;
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
		else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
	}

	function fallbackSpeaker(speakerId: string): EngineSpeaker | null {
		if (!speakerId) return null;
		return { speaker_id: speakerId, name: '自定义官方音色', label: speakerId, gender: '', description: '当前音色不在本地目录中，仍会按此 ID 尝试生成。' };
	}

	function readIds(key: string): string[] {
		if (!browser) return [];
		try { return uniqueStrings(JSON.parse(localStorage.getItem(key) || '[]')); } catch { return []; }
	}

	function writeIds(key: string, ids: string[]) {
		if (!browser) return;
		localStorage.setItem(key, JSON.stringify(ids));
	}

	function genderLabel(value: string) {
		return normalizeGender(value) === 'F' ? '女声' : normalizeGender(value) === 'M' ? '男声' : '音色';
	}

	function authorizationLabel(speaker: EngineSpeaker) {
		if (speaker.authorization_status === 'verified') return '已验证可用';
		if (speaker.authorization_status === 'denied') return '当前账号不可用';
		return '生成时校验';
	}

	function statusLine() {
		if (syncing) return '正在同步官方目录';
		if (statusError) return statusError;
		if (!catalogStatus) return `本地目录 · ${speakers.length} 个音色`;
		const source = catalogStatus.source === 'official' ? '官方目录' : catalogStatus.source === 'cache' ? '本地缓存' : '内置目录';
		return `${source} · ${catalogStatus.total ?? speakers.length} 个${catalogStatus.stale ? ' · 可能不是最新' : ''}`;
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

{#if mode === 'drawer'}<div class="doubao-speaker-picker">
	<div class="doubao-speaker-current">
		<span class="doubao-speaker-label">音色 <span class="doubao-speaker-count" aria-label={`${speakers.length} 个音色`}>{speakers.length || '—'}</span></span>
		<div class="doubao-current-control">
			<button bind:this={currentTrigger} class="doubao-current-card" type="button" onclick={openDrawer} aria-label="打开豆包官方音色目录" title={currentSpeaker?.speaker_id || '选择豆包官方音色'}>
				<span class="doubao-current-copy"><strong>{currentSpeaker?.name || '选择官方音色'}</strong>{#if currentSpeaker?.speaker_id}<small>{currentSpeaker.speaker_id}</small>{/if}</span>
			</button>
			<button class="doubao-inline-preview-action" class:active={previewingId === currentSpeaker?.speaker_id} type="button" aria-label={previewingId === currentSpeaker?.speaker_id ? '暂停试听当前音色' : '试听当前音色'} disabled={!currentSpeaker} onclick={() => currentSpeaker && togglePreview(currentSpeaker)}>
				{#if previewingId === currentSpeaker?.speaker_id}<Pause size={13} />{:else}<Play size={13} />{/if}
			</button>
		</div>
	</div>
	{#if currentSpeaker && previewErrorId === currentSpeaker.speaker_id}<small class="doubao-inline-preview-error">当前音色暂无可用试听，不影响直接生成。</small>{/if}
</div>{/if}

<audio bind:this={previewAudio} preload="none" onended={stopPreview} onerror={() => { if (previewingId) previewErrorId = previewingId; previewingId = ''; }}></audio>

{#if drawerOpen}
	<div class="doubao-drawer-backdrop" class:embedded={mode === 'embedded'} role="presentation" onclick={(event) => mode === 'drawer' && event.target === event.currentTarget && closeDrawer()}>
		<aside class="doubao-catalog-drawer" class:embedded={mode === 'embedded'} style={`--doubao-drawer-width: ${drawerWidth}px`} bind:this={drawerEl} role={mode === 'drawer' ? 'dialog' : 'region'} aria-modal={mode === 'drawer' ? 'true' : undefined} aria-labelledby="doubao-catalog-title">
			{#if mode === 'drawer'}<button class="doubao-drawer-resizer" type="button" aria-label={`调整音色目录宽度，当前 ${drawerWidth} 像素`} data-tooltip="左右拖动调整音色目录宽度；方向键可微调" onpointerdown={beginDrawerResize} onkeydown={handleResizeKeydown}></button>{/if}
			<header class="doubao-drawer-head">
				<div class="doubao-head-meta">
					<span class="doubao-kicker">DOUBAO · TTS 2.0</span>
					<p class:syncing class:warning={Boolean(statusError) || catalogStatus?.stale} role="status" aria-live="polite">{statusLine()}</p>
				</div>
				<div class="doubao-head-title-row">
					<h2 id="doubao-catalog-title">官方音色目录</h2>
					<div class="doubao-head-actions">
						<button class="doubao-icon-action" type="button" aria-label="刷新官方音色目录" data-tooltip={catalogStatus?.sync_available === false ? '在设置中配置火山引擎 AK/SK 后可同步官方目录' : '刷新官方音色目录'} disabled={syncing || catalogStatus?.sync_available === false} onclick={syncCatalog}><RefreshCw size={15} class={syncing ? 'spinning' : ''} /></button>
						{#if mode === 'drawer'}<button class="doubao-icon-action" type="button" aria-label="关闭音色目录" data-tooltip="关闭音色目录" onclick={closeDrawer}><X size={17} /></button>{/if}
					</div>
				</div>
			</header>

			<div class="doubao-search-row">
				<Search size={15} />
				<input bind:this={searchInput} bind:value={filters.query} placeholder="搜索名称、音色 ID、标签或描述" autocomplete="off" />
				{#if filters.query}<button type="button" aria-label="清除搜索" onclick={() => (filters.query = '')}><X size={13} /></button>{/if}
			</div>

			<nav class="doubao-tabs" aria-label="音色目录分组">
				<button class:active={tab === 'recommended'} type="button" onclick={() => (tab = 'recommended')}><Sparkles size={13} />推荐 <span>{tabCounts.recommended}</span></button>
				<button class:active={tab === 'favorites'} type="button" onclick={() => (tab = 'favorites')}><Heart size={13} />收藏 <span>{tabCounts.favorites}</span></button>
				<button class:active={tab === 'recent'} type="button" onclick={() => (tab = 'recent')}><Clock3 size={13} />最近 <span>{tabCounts.recent}</span></button>
				<button class:active={tab === 'all'} type="button" onclick={() => (tab = 'all')}><Library size={13} />全部 <span>{tabCounts.all}</span></button>
			</nav>

			<div class="doubao-filter-grid">
				{#if filterOptions.genders.length}<label><span>性别</span><select bind:value={filters.gender}><option value="all">全部（{tabCounts.all}）</option>{#each filterOptions.genders as item}<option value={item.value}>{item.label}（{item.count}）</option>{/each}</select></label>{/if}
				{#if filterOptions.ages.length}<label><span>年龄</span><select bind:value={filters.age}><option value="all">全部（{tabCounts.all}）</option>{#each filterOptions.ages as item}<option value={item.value}>{item.label}（{item.count}）</option>{/each}</select></label>{/if}
				{#if filterOptions.languages.length}<label><span>语言</span><select bind:value={filters.language}><option value="all">全部（{tabCounts.all}）</option>{#each filterOptions.languages as item}<option value={item.value}>{item.label}（{item.count}）</option>{/each}</select></label>{/if}
				{#if filterOptions.emotions.length}<label><span>情绪</span><select bind:value={filters.emotion}><option value="all">全部（{tabCounts.all}）</option>{#each filterOptions.emotions as item}<option value={item.value}>{item.label}（{item.count}）</option>{/each}</select></label>{/if}
				{#if filterOptions.categories.length}<label><span>分类</span><select bind:value={filters.category}><option value="all">全部（{tabCounts.all}）</option>{#each filterOptions.categories as item}<option value={item.value}>{item.label}（{item.count}）</option>{/each}</select></label>{/if}
				{#if filterOptions.specialLabels.length}<label><span>同款标签</span><select bind:value={filters.specialLabel}><option value="all">全部（{tabCounts.all}）</option>{#each filterOptions.specialLabels as item}<option value={item.value}>{item.label}（{item.count}）</option>{/each}</select></label>{/if}
			</div>

			<div class="doubao-results-meta"><span>{loading ? '读取中' : `${visibleSpeakers.length} 个音色`}</span>{#if hasActiveFilters}<button type="button" onclick={clearFilters}>清除筛选</button>{/if}</div>

			<section class="doubao-catalog-list" aria-live="polite">
				{#if loading && !speakers.length}
					{#each [1, 2, 3, 4] as item}<div class="doubao-card-skeleton" aria-hidden="true"><span></span><span></span></div>{/each}
				{:else}
					{#each visibleSpeakers as speaker (speaker.speaker_id)}
						<article class="doubao-voice-card" class:selected={value === speaker.speaker_id} class:denied={speaker.authorization_status === 'denied'}>
							<div class="doubao-card-main">
								<span class="doubao-voice-orb large" class:playing={previewingId === speaker.speaker_id}><span></span><span></span><span></span></span>
								<div class="doubao-card-copy">
									<div class="doubao-card-title"><strong>{speaker.name}</strong><span>{genderLabel(speaker.gender)}{speaker.age ? ` · ${speaker.age}` : ''}</span></div>
									<p>{speaker.description || speaker.speaker_id}</p>
									<div class="doubao-tags">
										{#each speakerCategories(speaker).slice(0, 3) as tag}<span>{tag}</span>{/each}
										<span class:verified={speaker.authorization_status === 'verified'} class:denied={speaker.authorization_status === 'denied'}>{authorizationLabel(speaker)}</span>
									</div>
								</div>
							</div>
							<div class="doubao-card-id" title={speaker.speaker_id}>{speaker.speaker_id}</div>
							<div class="doubao-card-actions">
								<button class="doubao-icon-action" class:active={previewingId === speaker.speaker_id} type="button" aria-label={previewingId === speaker.speaker_id ? `暂停试听${speaker.name}` : `试听${speaker.name}`} data-tooltip={previewingId === speaker.speaker_id ? '暂停试听' : '试听这个音色'} onclick={() => togglePreview(speaker)}>{#if previewingId === speaker.speaker_id}<Pause size={14} />{:else}<Play size={14} />{/if}</button>
								<button class="doubao-icon-action favorite" class:active={favoriteIds.includes(speaker.speaker_id)} type="button" aria-label={favoriteIds.includes(speaker.speaker_id) ? `取消收藏${speaker.name}` : `收藏${speaker.name}`} data-tooltip={favoriteIds.includes(speaker.speaker_id) ? '取消收藏' : '收藏这个音色'} onclick={() => toggleFavorite(speaker.speaker_id)}><Heart size={14} fill={favoriteIds.includes(speaker.speaker_id) ? 'currentColor' : 'none'} /></button>
								<button class="doubao-icon-action use" class:active={value === speaker.speaker_id} type="button" aria-label={value === speaker.speaker_id ? `${speaker.name}是当前音色` : `使用${speaker.name}`} data-tooltip={speaker.authorization_status === 'denied' ? '当前账号不能使用这个音色' : value === speaker.speaker_id ? '当前正在使用' : '使用这个音色'} disabled={speaker.authorization_status === 'denied'} onclick={() => choose(speaker.speaker_id, true)}><Check size={14} /></button>
							</div>
							{#if previewErrorId === speaker.speaker_id}<small class="doubao-preview-error">试听未加载，可稍后重试；不影响选择音色。</small>{/if}
						</article>
					{:else}
						<div class="doubao-empty">
							<strong>{tab === 'favorites' ? '还没有收藏音色' : tab === 'recent' ? '还没有最近使用' : '没有匹配的音色'}</strong>
							<p>{tab === 'favorites' ? '去“全部”试听后点亮收藏。' : tab === 'recent' ? '成功生成后，音色会自动出现在这里。' : '清除筛选，或直接输入官方音色 ID。'}</p>
							{#if hasActiveFilters}<button type="button" onclick={clearFilters}>清除筛选</button>{/if}
						</div>
					{/each}
				{/if}
			</section>

			<footer class="doubao-manual-entry">
				<button class="doubao-manual-toggle" type="button" aria-expanded={manualIdOpen} onclick={() => (manualIdOpen = !manualIdOpen)}>目录里没有？输入官方音色 ID</button>
				{#if manualIdOpen}<div><input bind:value={manualId} placeholder="例如 zh_female_vv_uranus_bigtts" onkeydown={(event) => event.key === 'Enter' && useManualId()} /><button type="button" disabled={!manualId.trim()} onclick={useManualId}>使用这个 ID</button></div>{/if}
			</footer>
		</aside>
	</div>
{/if}

<style>
	.doubao-speaker-picker { flex: 0 1 auto; min-width: 0; }
	.doubao-speaker-current { display: flex; align-items: center; gap: 6px; min-width: 0; }
	.doubao-speaker-label { display: inline-flex; align-items: center; gap: 4px; color: var(--muted); font-size: 12px; white-space: nowrap; }
	.doubao-speaker-count { min-width: 22px; height: 16px; display: inline-flex; align-items: center; justify-content: center; padding: 0 5px; border: 1px solid rgba(116, 151, 190, .22); border-radius: 999px; background: #111820; color: #7faee0; font: 9px/1 ui-monospace, SFMono-Regular, Menlo, monospace; }
	.doubao-current-control { width: 300px; min-width: 220px; height: 28px; display: flex; align-items: stretch; overflow: hidden; border: 1px solid var(--line); border-radius: 6px; background: #101215; transition: border-color 120ms ease, box-shadow 120ms ease; }
	.doubao-current-control:hover, .doubao-current-control:focus-within { border-color: #46515f; box-shadow: 0 0 0 2px rgba(80, 147, 224, .09); }
	.doubao-current-card { flex: 1 1 auto; min-width: 0; height: 26px; display: flex; align-items: center; padding: 0 8px; border: 0; border-radius: 0; color: var(--text); background: transparent; text-align: left; }
	.doubao-current-card:focus-visible { outline: 2px solid rgba(90, 167, 255, .72); outline-offset: -2px; }
	.doubao-current-copy { min-width: 0; width: 100%; display: flex; align-items: baseline; gap: 8px; }
	.doubao-current-copy strong, .doubao-current-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.doubao-current-copy strong { flex: 0 0 auto; max-width: 120px; font-size: 12px; font-weight: 500; }
	.doubao-current-copy small { min-width: 0; color: #707b89; font: 9px ui-monospace, SFMono-Regular, Menlo, monospace; }
	.doubao-inline-preview-action { width: 30px; flex: 0 0 30px; display: inline-grid; place-items: center; padding: 0; border: 0; border-left: 1px solid var(--line); border-radius: 0; color: #9aa7b6; background: transparent; }
	.doubao-inline-preview-action:hover, .doubao-inline-preview-action:focus-visible, .doubao-inline-preview-action.active { color: #beddff; background: #17202b; outline: none; }
	.doubao-inline-preview-action:disabled { color: #59616c; cursor: not-allowed; }
	.doubao-voice-orb { width: 25px; height: 25px; flex: 0 0 25px; border: 1px solid rgba(112, 178, 255, .35); border-radius: 50%; background: radial-gradient(circle at 35% 30%, #263e58, #10161d 65%); display: flex; align-items: center; justify-content: center; gap: 2px; }
	.doubao-voice-orb span { width: 2px; height: 7px; border-radius: 2px; background: #8dc7ff; opacity: .72; }
	.doubao-voice-orb span:nth-child(2) { height: 12px; background: #8ce0c0; }
	.doubao-voice-orb.playing span { animation: doubao-level .7s ease-in-out infinite alternate; }
	.doubao-voice-orb.playing span:nth-child(2) { animation-delay: -.3s; }
	.doubao-voice-orb.playing span:nth-child(3) { animation-delay: -.5s; }
	.doubao-voice-orb.large { width: 36px; height: 36px; flex-basis: 36px; }
	.doubao-icon-action, .doubao-tabs button, .doubao-results-meta button, .doubao-empty button, .doubao-manual-toggle, .doubao-manual-entry div button { border: 1px solid var(--line); background: #131820; color: #cfd9e6; }
	.doubao-icon-action { width: 30px; height: 30px; padding: 0; display: inline-grid; place-items: center; border-radius: 7px; flex: 0 0 30px; }
	.doubao-icon-action:hover, .doubao-icon-action:focus-visible, .doubao-icon-action.active { color: #a9d5ff; border-color: rgba(113, 173, 241, .52); background: #172331; outline: none; }
	.doubao-icon-action.favorite.active { color: #f1a9b8; border-color: rgba(230, 118, 147, .44); background: rgba(166, 55, 90, .16); }
	.doubao-icon-action.use.active { color: #93e0bf; border-color: rgba(80, 177, 143, .46); background: rgba(43, 131, 102, .18); }
	.doubao-inline-preview-error { display: block; margin: 4px 0 0 35px; color: #b99a70; font-size: 9px; }
	audio { display: none; }
	.doubao-drawer-backdrop { position: fixed; inset: 0; z-index: 130; display: flex; justify-content: flex-end; background: rgba(2, 5, 9, .68); backdrop-filter: blur(3px); }
	.doubao-catalog-drawer { position: relative; width: min(var(--doubao-drawer-width, 480px), calc(100vw - 24px)); height: 100%; display: grid; grid-template-rows: auto auto auto auto auto minmax(0, 1fr) auto; gap: 9px; padding: 15px 16px; overflow: hidden; border-left: 1px solid rgba(119, 165, 216, .24); background: linear-gradient(160deg, #111820 0%, #0c1015 48%, #0c0f13 100%); box-shadow: -24px 0 70px rgba(0, 0, 0, .55); animation: doubao-drawer-in 160ms ease-out; container-type: inline-size; }
	.doubao-drawer-resizer { position: absolute; z-index: 3; inset: 0 auto 0 -5px; width: 10px; padding: 0; border: 0; border-radius: 0; background: transparent; cursor: ew-resize; touch-action: none; }
	.doubao-drawer-resizer::after { content: ''; position: absolute; inset: 0 auto 0 4px; width: 1px; background: rgba(119, 165, 216, .24); transition: width 120ms ease, background 120ms ease; }
	.doubao-drawer-resizer:hover::after, .doubao-drawer-resizer:focus-visible::after { width: 2px; background: #71b8f3; }
	.doubao-drawer-resizer:focus-visible { outline: none; }
	.doubao-drawer-backdrop.embedded { position: static; inset: auto; z-index: auto; display: block; background: transparent; backdrop-filter: none; }
	.doubao-catalog-drawer.embedded { width: 100%; height: min(780px, calc(100vh - 180px)); min-height: 560px; border: 1px solid rgba(119, 165, 216, .24); border-radius: 12px; box-shadow: none; animation: none; }
	.doubao-drawer-head { display: grid; gap: 4px; min-width: 0; }
	.doubao-head-meta, .doubao-head-title-row { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
	.doubao-kicker { color: #78b9ed; font: 10px ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .13em; }
	.doubao-drawer-head h2 { margin: 0; color: #eef5fc; font-size: 18px; letter-spacing: -.02em; }
	.doubao-drawer-head p { min-width: 0; margin: 0; overflow: hidden; color: #7f8d9d; font-size: 10px; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
	.doubao-drawer-head p.warning { color: #d4b883; }
	.doubao-drawer-head p.syncing { color: #7fd3ff; font-weight: 600; }
	.doubao-drawer-head p.syncing::before { content: ''; width: 6px; height: 6px; display: inline-block; margin-right: 5px; border-radius: 999px; background: #61c7ff; box-shadow: 0 0 0 3px rgba(97, 199, 255, .14); animation: doubao-status-pulse 1s ease-in-out infinite alternate; }
	.doubao-head-actions { display: flex; gap: 6px; }
	.doubao-search-row { width: 100%; min-width: 0; min-height: 36px; display: flex; align-items: center; justify-self: stretch; gap: 8px; padding: 0 10px; box-sizing: border-box; border: 1px solid rgba(126, 159, 196, .24); border-radius: 9px; background: #0b1117; color: #6f8194; }
	.doubao-search-row:focus-within { border-color: rgba(101, 171, 242, .58); box-shadow: 0 0 0 2px rgba(54, 121, 190, .12); }
	.doubao-search-row input { flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--text); font-size: 12px; }
	.doubao-search-row button { border: 0; background: transparent; color: #7f8b98; padding: 3px; }
	.doubao-tabs { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; padding: 3px; border: 1px solid rgba(117, 144, 175, .16); border-radius: 9px; background: #0b0f14; }
	.doubao-tabs button { min-height: 31px; display: flex; align-items: center; justify-content: center; gap: 5px; border-color: transparent; border-radius: 6px; background: transparent; color: #7f8b99; font-size: 11px; }
	.doubao-tabs button span { color: #6faee6; font: 9px ui-monospace, SFMono-Regular, Menlo, monospace; }
	.doubao-tabs button.active { border-color: rgba(100, 169, 234, .28); background: #152334; color: #d7eaff; }
	.doubao-filter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(86px, 1fr)); gap: 6px; }
	.doubao-filter-grid label { min-width: 0; display: grid; gap: 4px; }
	.doubao-filter-grid label span { color: #6f7c8b; font-size: 9px; }
	.doubao-filter-grid select { width: 100%; min-height: 29px; padding: 3px 6px; border-radius: 6px; border-color: rgba(121, 149, 181, .2); background: #10151c; color: #bdc8d5; font-size: 10px; }
	.doubao-results-meta { display: flex; align-items: center; justify-content: space-between; min-height: 20px; color: #718091; font-size: 10px; }
	.doubao-results-meta button { border: 0; background: transparent; color: #82b7e8; font-size: 10px; }
	.doubao-catalog-list { min-height: 0; overflow-y: auto; display: grid; align-content: start; gap: 6px; padding-right: 2px; }
	.doubao-voice-card { display: grid; grid-template-columns: minmax(0, 1fr) 28px; column-gap: 8px; padding: 9px; border: 1px solid rgba(117, 144, 175, .18); border-radius: 9px; background: rgba(17, 23, 30, .88); transition: border-color 120ms ease, background 120ms ease; }
	.doubao-voice-card:hover, .doubao-voice-card.selected { border-color: rgba(103, 172, 239, .42); background: #121d28; }
	.doubao-voice-card.denied { opacity: .68; }
	.doubao-card-main { min-width: 0; display: flex; align-items: flex-start; gap: 8px; }
	.doubao-card-copy { min-width: 0; flex: 1; }
	.doubao-card-title { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
	.doubao-card-title strong { color: #e6edf5; font-size: 13px; }
	.doubao-card-title > span { color: #798797; font-size: 9px; white-space: nowrap; }
	.doubao-card-copy p { margin: 3px 0 6px; overflow: hidden; color: #8b98a7; font-size: 10.5px; line-height: 1.45; display: -webkit-box; line-clamp: 2; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
	.doubao-tags { display: flex; flex-wrap: wrap; gap: 4px; }
	.doubao-tags span { padding: 2px 5px; border: 1px solid rgba(119, 151, 186, .18); border-radius: 999px; color: #8494a5; background: rgba(25, 35, 46, .7); font-size: 8.5px; }
	.doubao-tags span.verified { color: #8fd6bd; border-color: rgba(80, 177, 143, .28); }
	.doubao-tags span.denied { color: #d49aa4; border-color: rgba(190, 86, 106, .28); }
	.doubao-card-id { min-width: 0; margin: 6px 0 0 44px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #596778; font: 8.5px ui-monospace, SFMono-Regular, Menlo, monospace; }
	.doubao-card-actions { grid-column: 2; grid-row: 1 / span 2; align-self: center; display: flex; flex-direction: column; gap: 5px; }
	.doubao-card-actions .doubao-icon-action { width: 28px; height: 28px; flex-basis: 28px; }
	.doubao-preview-error { grid-column: 1; display: block; margin-top: 6px; color: #c4a374; font-size: 9px; text-align: right; }
	.doubao-empty { min-height: 190px; display: grid; place-content: center; justify-items: center; gap: 7px; text-align: center; color: #8290a0; }
	.doubao-empty strong { color: #c7d1dc; font-size: 13px; }
	.doubao-empty p { margin: 0; max-width: 280px; font-size: 10px; }
	.doubao-empty button { min-height: 28px; padding: 0 9px; border-radius: 6px; font-size: 10px; }
	.doubao-card-skeleton { height: 114px; padding: 14px; border: 1px solid rgba(117, 144, 175, .12); border-radius: 10px; background: #11171e; }
	.doubao-card-skeleton span { display: block; width: 42%; height: 11px; margin-bottom: 10px; border-radius: 4px; background: linear-gradient(90deg, #18212b, #202d3a, #18212b); background-size: 200% 100%; animation: doubao-shimmer 1.2s linear infinite; }
	.doubao-card-skeleton span + span { width: 76%; height: 8px; }
	.doubao-manual-entry { padding-top: 10px; border-top: 1px solid rgba(111, 139, 170, .16); }
	.doubao-manual-toggle { width: 100%; min-height: 26px; border: 0; background: transparent; color: #7896b5; font-size: 10px; text-align: left; }
	.doubao-manual-entry > div { display: flex; gap: 6px; margin-top: 6px; }
	.doubao-manual-entry input { flex: 1; min-width: 0; height: 32px; font: 10px ui-monospace, SFMono-Regular, Menlo, monospace; }
	.doubao-manual-entry div button { min-width: 94px; border-radius: 7px; font-size: 10px; }
	:global(.spinning) { animation: doubao-spin .8s linear infinite; }
	@keyframes doubao-drawer-in { from { transform: translateX(24px); opacity: .75; } }
	@keyframes doubao-spin { to { transform: rotate(360deg); } }
	@keyframes doubao-status-pulse { to { opacity: .52; transform: scale(.82); } }
	@keyframes doubao-shimmer { to { background-position: -200% 0; } }
	@keyframes doubao-level { to { height: 4px; opacity: .45; } }
	@media (max-width: 720px) {
		.doubao-speaker-picker { min-width: 100%; flex-basis: 100%; }
		.doubao-speaker-current { flex-wrap: wrap; }
		.doubao-current-control { flex: 1 1 220px; width: auto; }
		.doubao-catalog-drawer { width: 100vw; max-width: none; padding: 16px 14px; border-left: 0; }
		.doubao-drawer-resizer { display: none; }
		.doubao-catalog-drawer.embedded { width: 100%; height: 720px; min-height: 520px; border: 1px solid rgba(119, 165, 216, .24); }
		.doubao-filter-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
	}
	@container (max-width: 430px) {
		.doubao-filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.doubao-head-meta { align-items: flex-start; }
		.doubao-drawer-head p { max-width: 52%; }
		.doubao-voice-orb.large { width: 30px; height: 30px; flex-basis: 30px; }
		.doubao-card-id { margin-left: 38px; }
	}
	@media (prefers-reduced-motion: reduce) {
		.doubao-catalog-drawer, .doubao-drawer-head p.syncing::before, .doubao-voice-orb.playing span, .doubao-card-skeleton span, :global(.spinning) { animation: none; }
	}
</style>
