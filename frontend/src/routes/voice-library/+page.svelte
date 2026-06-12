<script lang="ts">
	import { Api } from '$lib/api';
	import type { VoiceAsset } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { ArrowRight, Check, ClipboardCopy, Database, FileText, FileAudio, Heart, Pencil, Pause, Plus, Search, ShieldCheck, Trash2, Upload, Volume2, X } from 'lucide-svelte';
	import { licenseLabel } from '$lib/labels';
	import { onMount } from 'svelte';

	const PAGE_SIZE = 40;

	let allVoices = $state<VoiceAsset[]>([]);
	let displayedCount = $state(0);
	let loading = $state(false);
	let hasMore = $state(true);
	let sentinel = $state<HTMLElement | null>(null);
	let name = $state('');
	let description = $state('');
	let tags = $state('');
	let referenceText = $state('');
	let license = $state('unknown');
	let engine = $state<string | null>('indextts-v2');
	let file = $state<File | null>(null);
	let uploadMessage = $state('');
	let editingVoice = $state<VoiceAsset | null>(null);
	let voiceQuery = $state('');
	function hashStr(s: string) { let h = 0; for (let i = 0; i < s.length; i++) { h = ((h << 5) - h + s.charCodeAt(i)) | 0; } return h; }

	let voiceEngineFilter = $state('all');
	let voiceLicenseFilter = $state('all');
	let voiceSort = $state<'random' | 'updated' | 'name'>('random');
	const sessionRandomSeed = Math.floor(Math.random() * 1e9);
	let voicePreviewAudio = $state<HTMLAudioElement | null>(null);
	let playingVoiceId = $state('');

	let batchAsrProgress = $state({ active: false, current: 0, total: 0 });
	let voiceAsrStatus = $state(new Map<string, 'idle' | 'generating' | 'done' | 'error'>());
	let voiceSerStatus = $state(new Map<string, 'idle' | 'generating' | 'done' | 'error'>());
	let batchSerProgress = $state({ active: false, current: 0, total: 0 });
	let showVoiceModal = $state(false);
	let copiedId = $state("");

	function checkOverflow(node: HTMLElement, _text: string) {
		let frame = 0;
		const check = () => {
			frame = 0;
			node.classList.toggle('fade-overflow', node.scrollHeight > node.offsetHeight);
		};
		const schedule = () => {
			if (frame) cancelAnimationFrame(frame);
			frame = requestAnimationFrame(check);
		};
		schedule();
		return {
			update(_nextText: string) {
				schedule();
			},
			destroy() {
				if (frame) cancelAnimationFrame(frame);
			}
		};
	}


	async function loadInitial() {
		loading = true;
		allVoices = await Api.voices({ offset: 0, limit: 2000 });
		displayedCount = Math.min(PAGE_SIZE, allVoices.length);
		hasMore = displayedCount < allVoices.length;
		loading = false;
	}

	async function loadMore() {
		if (loading || !hasMore) return;
		loading = true;
		await new Promise(r => setTimeout(r, 50));
		displayedCount = Math.min(displayedCount + PAGE_SIZE, allVoices.length);
		hasMore = displayedCount < allVoices.length;
		loading = false;
	}

	async function refresh() {
		await loadInitial();
	}

	async function toggleVoicePlayback(voice: VoiceAsset) {
		if (!voice.reference_audio_ids[0] || !voicePreviewAudio) return;
		const audioUrl = `/api/voices/${voice.voice_id}/audio/${voice.reference_audio_ids[0]}`;
		if (playingVoiceId === voice.voice_id && !voicePreviewAudio.paused) {
			voicePreviewAudio.pause();
			playingVoiceId = '';
			return;
		}
		const absoluteUrl = new URL(audioUrl, window.location.href).href;
		if (voicePreviewAudio.src !== absoluteUrl) {
			voicePreviewAudio.src = audioUrl;
			voicePreviewAudio.currentTime = 0;
		}
		playingVoiceId = voice.voice_id;
		try {
			await voicePreviewAudio.play();
		} catch {
			playingVoiceId = '';
		}
	}
	onMount(() => {
		loadInitial();
		const scrollRoot = document.querySelector('.main');
		const observer = new IntersectionObserver((entries) => {
			if (entries[0].isIntersecting) loadMore();
		}, { root: scrollRoot, rootMargin: '200px' });
		if (sentinel) observer.observe(sentinel);
		return () => observer.disconnect();
	});


	async function generateAsrForVoice(voice: VoiceAsset) {
		if (!voice.reference_audio_ids[0]) return;
		voiceAsrStatus = new Map([...voiceAsrStatus, [voice.voice_id, 'generating']]);
		try {
			const audioUrl = `/api/voices/${voice.voice_id}/audio/${voice.reference_audio_ids[0]}`;
			const resp = await fetch(audioUrl);
			const blob = await resp.blob();
			const file = new File([blob], 'audio.wav', { type: blob.type });
			const result = await Api.transcribeAudio(file);
			const text = result.text?.trim();
			if (!text) throw new Error('空转写结果');
			await Api.updateVoice(voice.voice_id, {
				reference_text: text,
			});
			voice.reference_text = text;
			voiceAsrStatus = new Map([...voiceAsrStatus, [voice.voice_id, 'done']]);
		} catch (e) {
			console.error('ASR failed for', voice.name, e);
			voiceAsrStatus = new Map([...voiceAsrStatus, [voice.voice_id, 'error']]);
		}
	}

	async function batchGenerateAsr() {
		const candidates = allVoices.filter((v) =>
			isFakeReferenceText(v.reference_text) && v.reference_audio_ids[0]
		);
		if (!candidates.length) return;
		batchAsrProgress = { active: true, current: 0, total: candidates.length };
		for (let i = 0; i < candidates.length; i++) {
			batchAsrProgress = { ...batchAsrProgress, current: i + 1 };
			await generateAsrForVoice(candidates[i]);
		}
		batchAsrProgress = { active: false, current: 0, total: 0 };
	}

	async function generateSerForVoice(voice: VoiceAsset) {
		if (!voice.reference_audio_ids?.length) return;
		voiceSerStatus = new Map([...voiceSerStatus, [voice.voice_id, 'generating']]);
		try {
			const result = await Api.predictEmotion(voice.voice_id);
			if (result.top_emotion) {
				const tags = [result.top_emotion];
				for (const [emo, score] of Object.entries(result.emotion_scores)) {
					if (emo !== result.top_emotion && score > 0.15) tags.push(emo);
				}
				await Api.updateVoice(voice.voice_id, { emotion_tags: tags.slice(0, 3) });
			}
			voiceSerStatus = new Map([...voiceSerStatus, [voice.voice_id, 'done']]);
			await refresh();
		} catch (e: any) {
			voiceSerStatus = new Map([...voiceSerStatus, [voice.voice_id, 'error']]);
			console.error('SER 识别失败:', e);
		}
	}

	async function batchGenerateSer() {
		const candidates = allVoices.filter(v => v.reference_audio_ids?.length && (!v.emotion_tags || v.emotion_tags.length === 0));
		if (!candidates.length) return;
		batchSerProgress = { active: true, current: 0, total: candidates.length };
		for (let i = 0; i < candidates.length; i++) {
			batchSerProgress = { ...batchSerProgress, current: i + 1 };
			await generateSerForVoice(candidates[i]);
		}
		batchSerProgress = { active: false, current: 0, total: 0 };
	}
	function resetForm() {
		name = '';
		description = '';
		tags = '';
		referenceText = '';
		license = 'unknown';
		engine = 'indextts-v2';
		file = null;
		editingVoice = null;
	showVoiceModal = false;
	}

	function editVoice(voice: VoiceAsset) {
		editingVoice = voice;
		name = voice.name;
		description = voice.description;
		tags = voice.tags.join(', ');
		referenceText = voice.reference_text;
		license = voice.license_status;
		engine = voice.recommended_engine_id;
		file = null;
		uploadMessage = '';
	showVoiceModal = true;
	}

	async function saveVoice() {
		let ids = editingVoice ? [...editingVoice.reference_audio_ids] : [];
		if (file) {
			const uploaded = await Api.uploadVoice(file);
			ids = editingVoice ? [...ids, uploaded.file_id] : [uploaded.file_id];
			uploadMessage = uploaded.quality.warnings.join('；') || '音频已上传';
		}
		const payload = {
			name,
			description,
			tags: tags.split(',').map((x) => x.trim()).filter(Boolean),
			reference_text: referenceText,
			reference_audio_ids: ids,
			license_status: license,
			recommended_engine_id: engine,
			default_language: 'zh',
			voice_type: 'test_sample'
		};
		if (editingVoice) {
			await Api.updateVoice(editingVoice.voice_id, payload);
			uploadMessage = uploadMessage || '声音信息已更新';
		} else {
			await Api.createVoice(payload);
		}
		resetForm();
		await refresh();
	}
	async function remove(id: string) {
		await Api.deleteVoice(id);
		if (editingVoice?.voice_id === id) resetForm();
		await refresh();
	}


	const NOISE_TAGS = new Set([
		'官方示例', '参考声音', '社区音色', 'Apache 2.0', '中文',
		'ASR待复核', '仅测试', '测试音色', '声音设计'
	]);

	function tagCategory(tag: string): string {
		if (['男声', '女声', '童声'].some((k) => tag.includes(k))) return 'gender';
		if (['少女音', '少年音', '成熟', '年轻', '少年', '少女'].some((k) => tag.includes(k))) return 'age';
		if ([
			'原神', '绝区零', '二次元', '虚拟主播', '虚拟UP主', 'A-SOUL', '崩坏',
			'游戏', '动漫', '真人', '狐狸', 'Fox', '角色音'
		].some((k) => tag.includes(k))) return 'source';
		if ([
			'情绪', '悲伤', '悬疑', '开心', '愤怒', '恐惧', '惊悚', '反感',
			'爽朗', '沧桑', '压迫', '元气', '爆发', '冷静', '活泼', '热情',
			'冷淡', '傲娇', '从容', '戏谑', '俏皮', '古灵精怪', '威严'
		].some((k) => tag.includes(k))) return 'emotion';
		if ([
			'讲解', '旁白', '口播', '播报', '独白', '叙事', '对白', '新闻',
			'睡前', '纪录片', '科技', '小说', '历史', '故事', '知识', '方言',
			'四川话', '动画', '轻喜剧', '解说', '角色扮演', '角色配音', '角色感',
			'对白'
		].some((k) => tag.includes(k))) return 'use';
		return 'timbre';
	}

	function tagClass(tag: string) {
		return `tag-${tagCategory(tag)}`;
	}

	function bindingLabel(engineId: string) {
		return {
			'indextts-v2': 'IndexTTS',
			omnivoice: 'OmniVoice',
			'f5-tts': 'F5-TTS',
			'cosyvoice-zero-shot': 'CosyVoice 复刻',
			'mimo-v2.5-tts-preset': 'MiMo 预置',
			'mimo-v2.5-tts-voiceclone': 'MiMo 复刻'
		}[engineId] ?? engineId;
	}

	function engineKind(engineId: string | null | undefined) {
		if (!engineId) return 'local';
		return engineId.startsWith('mimo-') ? 'cloud' : 'local';
	}

	function voiceCardKind(voice: VoiceAsset) {
		return engineKind(voice.recommended_engine_id);
	}

	function cleanTags(voiceTags: string[], limit = 5) {
		const names = new Set(allVoices.map((v) => v.name));
		return voiceTags
			.filter((tag) => !/^(seed|pack|community|voice_design|design_prompt|user|source|emotion):/.test(tag))
			.filter((tag) => !NOISE_TAGS.has(tag))
			.filter((tag) => !names.has(tag))
			.filter((tag) => !tag.match(/^[⭐🌟✨]/))
			.slice(0, limit);
	}

	function voiceLineText(text: string | null | undefined) {
		const value = (text ?? '').trim();
		if (!value) return '暂无台词。可以点击「编辑」补充参考音频里实际说的话。';
		if (value.includes('参考音频') || value.includes('用于测试') || value.includes('官方示例')) {
			return `未记录台词。当前只保存了素材说明：${value}`;
		}
		return value;
	}

	function isFakeReferenceText(text: string | null | undefined): boolean {
		const value = (text ?? '').trim();
		if (!value) return true;
		return value.includes('参考音频') || value.includes('用于测试') || value.includes('官方示例');
	}

	function voiceTypeLabel(type: string): string {
		return { real_person: '真人', virtual_character: '虚拟', host: '主持', singer: '歌手', narrator: '旁白', emotion_reference: '情绪', test_sample: '测试' }[type] ?? '';
	}
	function emotionLabel(e: string): string {
		const map: Record<string, string> = { happy: "开心", angry: "愤怒", sad: "悲伤", afraid: "恐惧", disgusted: "反感", melancholic: "忧郁", surprised: "惊讶", calm: "平静", neutral: "平静" };
		return map[e] ?? e;
	}

	function queryTokens(query: string) {
		return query
			.split(/[\s,，、]+/)
			.map((token) => token.trim().toLowerCase())
			.filter(Boolean);
	}

	function appendVoiceQueryTag(tag: string) {
		const value = tag.trim();
		if (!value) return;
		voiceQuery = voiceQuery.trim() ? `${voiceQuery.trim()}、${value}` : value;
	}

	let expandedCards = $state(new Set<string>());
	let expandedCategories = $state(new Set<string>());

		const { tagsByCategory, tagCounts } = $derived.by(() => {
		const counts = new Map<string, number>();
		const groups: Record<string, string[]> = {};
		for (const voice of allVoices) {
			for (const tag of cleanTags(voice.tags, 99)) {
				counts.set(tag, (counts.get(tag) ?? 0) + 1);
				const cat = tagCategory(tag);
				if (!groups[cat]) groups[cat] = [];
				if (!groups[cat].includes(tag)) groups[cat].push(tag);
			}
		}
		for (const cat of Object.keys(groups)) {
			groups[cat].sort((a, b) => (counts.get(b) ?? 0) - (counts.get(a) ?? 0));
		}
		return { tagsByCategory: groups, tagCounts: counts };
	});

	function selectedTags(): Set<string> {
		return new Set(queryTokens(voiceQuery));
	}

	function toggleTagFromCloud(tag: string) {
		const tokens = queryTokens(voiceQuery);
		const lower = tag.toLowerCase();
		if (tokens.includes(lower)) {
			voiceQuery = tokens.filter((t) => t !== lower).join('、');
		} else {
			appendVoiceQueryTag(tag);
		}
	}

	const filteredVoices = $derived.by(() => {
		const tokens = queryTokens(voiceQuery);
		return allVoices
			.filter((voice) => {
				if (voiceEngineFilter !== 'all' && !voice.engine_bindings?.some((binding) => binding.engine_id === voiceEngineFilter && binding.available)) return false;
				if (voiceLicenseFilter !== 'all' && voice.license_status !== voiceLicenseFilter) return false;
				if (!tokens.length) return true;
				const haystack = [voice.name, voice.description, voice.tags.join(' '), voice.reference_text].join(' ').toLowerCase();
				return tokens.every((token) => haystack.includes(token));
			})
			.sort((a, b) => {
				if (voiceSort === 'name') return a.name.localeCompare(b.name, 'zh-Hans-CN');
				if (voiceSort === 'updated') return b.updated_at.localeCompare(a.updated_at);
				const ha = ((sessionRandomSeed * 2654435761 + hashStr(a.voice_id || a.name)) >>> 0);
				const hb = ((sessionRandomSeed * 2654435761 + hashStr(b.voice_id || b.name)) >>> 0);
				return ha - hb;
			});
	});

	const visibleVoices = $derived(filteredVoices.slice(0, displayedCount));

	const selfOrAuthorizedCount = $derived(
		allVoices.filter((voice) => ['self_voice', 'authorized', 'company_authorized'].includes(voice.license_status)).length
	);
	const canSaveVoice = $derived(Boolean(name.trim()) && (Boolean(editingVoice) || Boolean(file)));

	const help = [
		{ title: '新增声音', body: '这里只保留自己上传参考音频这一条路径。准备一段 10-20 秒左右的 mp3 或 wav，填写名称和参考文本后保存，它就会进入本地音色库。' },
		{ title: '音色库怎么用', body: '音色库里的声音主要作为声音克隆参考。IndexTTS v2 通常需要选择一个参考声音；F5-TTS 和 CosyVoice Zero-Shot 需要参考音频和准确参考台词；OmniVoice 可以选择参考声音，也可以不选，改用声音设计标签。' },
		{ title: '参考文本', body: '参考文本是参考音频里大概说了什么。克隆或多语言模型有时会用它理解发音和音色；卡片里的文本按钮可以快速查看，不会撑大卡片。' },
		{ title: '编辑声音', body: '卡片上的“编辑”会把名称、描述、标签、参考文本和推荐引擎载入右侧表单。这里保存的是同一个声音名称，生成页下拉菜单会同步显示。' },
	];
</script>

<svelte:head><title>音色管理 - 声音工作台</title></svelte:head>

<main class="page">
		<div class="page-head">
			<div class="page-title-row">
				<h1>音色管理</h1>
				<div class="stat-pills">
					<span class="stat-pill"><Database size={14} /> {allVoices.length} 音色</span>
					<span class="stat-pill"><ShieldCheck size={14} /> {selfOrAuthorizedCount} 授权</span>
				</div>
				<HelpDrawer title="音色管理" sections={help} />
			</div>
			<div class="page-title-actions">
				<button class="btn-add-voice" onclick={() => { resetForm(); showVoiceModal = true; }}><Plus size={13} /> 新增声音</button>
				{#if batchAsrProgress.active}
					<span class="batch-indicator asr">
						<span class="batch-bar" style="width: {(batchAsrProgress.current / batchAsrProgress.total * 100).toFixed(0)}%"></span>
						<span class="batch-label"><FileAudio size={12} /> ASR {batchAsrProgress.current}/{batchAsrProgress.total}</span>
					</span>
				{:else}
					<button class="btn-asr-batch" onclick={batchGenerateAsr} disabled={batchAsrProgress.active}>
						<FileAudio size={13} /> 批量ASR
					</button>
				{/if}
				{#if batchSerProgress.active}
					<span class="batch-indicator ser">
						<span class="batch-bar" style="width: {(batchSerProgress.current / batchSerProgress.total * 100).toFixed(0)}%"></span>
						<span class="batch-label"><Heart size={12} /> SER {batchSerProgress.current}/{batchSerProgress.total}</span>
					</span>
				{:else}
					<button class="btn-ser-batch" onclick={batchGenerateSer} disabled={batchSerProgress.active}>
						<Heart size={13} /> 批量情绪识别
					</button>
				{/if}
			</div>
		</div>

	<div class="workbench">
		<section class="stack">
			<section class="panel stack library-toolbar">
				<div class="toolbar-grid voice-toolbar">
					<label class="field">
						<span>搜索</span>
						<div class="search-field">
							<Search size={15} />
							<input bind:value={voiceQuery} placeholder="名称、描述、标签" />
							{#if voiceQuery.trim()}
								<button class="search-clear" type="button" aria-label="清空搜索" data-tooltip="清空音色搜索条件" onclick={() => (voiceQuery = '')}>
									<X size={14} />
								</button>
							{/if}
						</div>
					</label>
					<label class="field">
						<span>可用引擎</span>
						<select bind:value={voiceEngineFilter}>
							<option value="all">全部</option>
							<option value="indextts-v2">IndexTTS v2</option>
							<option value="omnivoice">OmniVoice</option>
							<option value="mimo-v2.5-tts-voiceclone">MiMo VoiceClone</option>
						</select>
					</label>
					<label class="field">
						<span>授权</span>
						<select bind:value={voiceLicenseFilter}>
							<option value="all">全部</option>
							<option value="self_voice">本人声音</option>
							<option value="authorized">已授权</option>
							<option value="test_only">仅测试</option>
							<option value="unknown">未知</option>
						</select>
					</label>
					<label class="field">
						<span>排序</span>
						<select bind:value={voiceSort}>
							<option value="random">随机</option>
							<option value="updated">最近更新</option>
							<option value="name">名称</option>
						</select>
					</label>
				</div>
					<span class="toolbar-count muted">{visibleVoices.length} / {filteredVoices.length} / {allVoices.length} 条结果</span>
			</section>

			{#if Object.keys(tagsByCategory).length > 0}
			<section class="tag-cloud-section">
				<div class="tag-cloud-header">
					<span>标签筛选</span>
				</div>
				{#each [
					{ key: 'gender', label: '性别' },
					{ key: 'age', label: '年龄' },
					{ key: 'timbre', label: '音色' },
					{ key: 'emotion', label: '情绪' },
					{ key: 'use', label: '用途' },
					{ key: 'source', label: '来源' }
				] as cat}
					{#if tagsByCategory[cat.key]?.length}
						<div class="tag-cloud-category" class:expanded={expandedCategories.has(cat.key)}>
							<span class="tag-cloud-label">{cat.label}</span>
							{#each tagsByCategory[cat.key] as tag}
								<button
									class="tag-cloud-chip tag-cloud-{tagCategory(tag)} {selectedTags().has(tag.toLowerCase()) ? 'active' : ''}"
									type="button"
									onclick={() => toggleTagFromCloud(tag)}
								>{tag}<span class="tag-count">{tagCounts.get(tag)}</span></button>
							{/each}
							{#if tagsByCategory[cat.key].length > 3}
								<button class="tag-cloud-expand" type="button" onclick={() => {
									expandedCategories = expandedCategories.has(cat.key)
										? new Set([...expandedCategories].filter(k => k !== cat.key))
										: new Set([...expandedCategories, cat.key]);
								}}>{expandedCategories.has(cat.key) ? '收起' : '更多'}</button>
							{/if}
						</div>
					{/if}
				{/each}
			</section>
			{/if}

		<section class="grid voice-grid">
		{#each visibleVoices as voice}
				<article class={`card stack voice-card engine-surface ${voiceCardKind(voice) === 'cloud' ? 'engine-cloud' : 'engine-local'} ${playingVoiceId === voice.voice_id ? 'playing' : ''}`}>
					<div class="voice-card-head">
						<h2 title={voice.name}>{voice.name}</h2>
						<div class="card-head-actions">
							<button class="icon-btn-sm" type="button" aria-label="生成 ASR 台词" data-tooltip="为这个音色生成或刷新 ASR 台词" onclick={() => generateAsrForVoice(voice)} disabled={voiceAsrStatus.get(voice.voice_id) === 'generating'}>
								<FileAudio size={13} />
							</button>
							<button class="icon-btn-sm ser-card-btn" type="button" aria-label="识别音色情绪" data-tooltip="识别这个音色的情绪标签" onclick={() => generateSerForVoice(voice)} disabled={voiceSerStatus.get(voice.voice_id) === 'generating'}>
								<Heart size={13} />
							</button>
							<button class="icon-btn-sm" type="button" aria-label="复制音色 ID" data-tooltip="复制这个音色的 ID" onclick={() => navigator.clipboard.writeText(voice.voice_id).then(() => { copiedId = voice.voice_id; setTimeout(() => copiedId = '', 1500); })}>
								{#if copiedId === voice.voice_id}
									<Check size={13} />
								{:else}
									<ClipboardCopy size={13} />
								{/if}
							</button>
							<button class="icon-btn-sm" type="button" aria-label="编辑音色" data-tooltip="编辑这个音色的名称、标签和授权信息" onclick={() => editVoice(voice)}>
								<Pencil size={13} />
							</button>
							<button class="icon-btn-sm danger" type="button" aria-label="删除音色" data-tooltip="删除这个音色资产" onclick={() => remove(voice.voice_id)}>
								<Trash2 size={13} />
							</button>
					</div>
				</div>
					<p class="muted voice-desc" use:checkOverflow={voice.description || "暂无描述"} data-text={voice.description || '暂无描述'}>{voice.description || '暂无描述'}</p>
					<div class="tag-row">
						{#if voice.emotion_tags?.length}
								{#each voice.emotion_tags as tag}
									<span class="badge tag-filter tag-emotion">{emotionLabel(tag)}</span>
								{/each}
							{/if}
							{#each cleanTags(voice.tags, expandedCards.has(voice.voice_id) ? 99 : 4) as tag}
							<button class={`badge tag-filter ${tagClass(tag)}`} type="button" title={`添加到搜索：${tag}`} onclick={() => appendVoiceQueryTag(tag)}>{tag}<span class="tag-count">{tagCounts.get(tag)}</span></button>
						{/each}
						{#if cleanTags(voice.tags, 99).length > 4 && !expandedCards.has(voice.voice_id)}
							<button class="tag-expand-btn" type="button" onclick={() => { expandedCards = new Set([...expandedCards, voice.voice_id]); }}>+{cleanTags(voice.tags, 99).length - 4}</button>
						{/if}
					</div>
					<div class="asset-meta">
						{#if voiceTypeLabel(voice.voice_type)}
							<span>{voiceTypeLabel(voice.voice_type)}</span>
						{/if}
						<span>{voiceCardKind(voice) === 'cloud' ? '云端' : '本地'}</span>
						<span>{voice.recommended_engine_id ? bindingLabel(voice.recommended_engine_id) : '自动引擎'}</span>
						<span class="text-pop text-chip" data-text={voiceLineText(voice.reference_text)}><FileText size={13} /> 台词</span>
					</div>
					<div class="card-actions">
					{#if voice.reference_audio_ids[0]}
						<button class={`btn icon-text ${playingVoiceId === voice.voice_id ? 'playing' : ''}`} type="button" onclick={() => toggleVoicePlayback(voice)}>
							{#if playingVoiceId === voice.voice_id}<Pause size={14} /> 暂停{:else}<Volume2 size={14} /> 试听{/if}
						</button>
					{/if}
					<a class="btn btn-goto" href={`/generate?voice=${voice.voice_id}`}><ArrowRight size={14} /> 去合成</a>
					</div>
					<span class="dog-ear" class:ok={voice.license_status === "self_voice"}>{licenseLabel(voice.license_status)}</span>
				</article>
		{:else}
			<div class="empty">还没有声音资产</div>
		{/each}
		{#if hasMore}<div bind:this={sentinel} class="scroll-sentinel"></div>{/if}
		{#if loading}
			<div class="loading-indicator">
				<div class="loading-spinner"></div>
				<span>加载中...</span>
			</div>
		{:else if !hasMore && filteredVoices.length > PAGE_SIZE}
			<div class="end-of-list">— 已加载全部 {filteredVoices.length} 个音色 —</div>
		{/if}
		</section>
		</section>
			{#if showVoiceModal}
			<div
					class="modal-backdrop"
					role="button"
					tabindex="0"
					aria-label="关闭声音编辑弹窗"
					onclick={() => resetForm()}
					onkeydown={(event) => {
						if (event.key === 'Enter' || event.key === ' ' || event.key === 'Escape') {
							event.preventDefault();
							resetForm();
						}
					}}
				></div>
			<dialog class="modal" open>
				<div class="modal-header">
					<h2>{#if editingVoice}<Pencil size={16} /> 编辑声音{:else}<Plus size={16} /> 新增声音{/if}</h2>
					<button class="btn icon-text" onclick={resetForm}><X size={15} /></button>
				</div>
				<div class="modal-body stack">
					<div class="field"><label for="voice-name">名称</label><input id="voice-name" bind:value={name} /></div>
					<div class="field"><label for="voice-desc">描述</label><input id="voice-desc" bind:value={description} /></div>
					<div class="field"><label for="voice-tags">标签</label><input id="voice-tags" bind:value={tags} placeholder="温柔, 女声" /></div>
					<div class="field"><label for="voice-ref">参考文本</label><input id="voice-ref" bind:value={referenceText} /></div>
					<div class="field"><label for="voice-license">授权</label><select id="voice-license" bind:value={license}><option value="unknown">未知</option><option value="self_voice">本人声音</option><option value="authorized">已授权</option><option value="test_only">仅测试</option></select></div>
					<div class="field"><label for="voice-engine">推荐引擎</label><select id="voice-engine" bind:value={engine}><option value="indextts-v2">IndexTTS v2</option><option value="omnivoice">OmniVoice</option><option value="mimo-v2.5-tts-voiceclone">MiMo V2.5 VoiceClone</option></select></div>
					<div class="field">
						<label for="voice-file">{editingVoice ? '追加参考音频' : '参考音频'}</label>
						<div class="file-row">
							<label class="btn file-picker" for="voice-file"><Upload size={14} /> 选择音频</label>
							<span class="muted file-name">{file?.name ?? '未选择文件'}</span>
							<input id="voice-file" class="sr-only" type="file" accept="audio/*" onchange={(e) => (file = (e.currentTarget as HTMLInputElement).files?.[0] ?? null)} />
						</div>
					</div>
					{#if uploadMessage}<p class="muted"><Check size={13} /> {uploadMessage}</p>{/if}
					<button class="btn primary" disabled={!canSaveVoice} onclick={saveVoice}><Upload size={15} /> {editingVoice ? '保存修改' : '保存声音'}</button>
				</div>
			</dialog>
			{/if}
	</div>
	<audio
		bind:this={voicePreviewAudio}
		preload="none"
		onended={() => (playingVoiceId = "")}
		onpause={() => {
			if (voicePreviewAudio?.ended || !voicePreviewAudio?.currentTime) playingVoiceId = "";
		}}
	></audio>
</main>

<style>
	/* Workbench override: full width (no sidebar) */
	.workbench {
		grid-template-columns: 1fr;
	}

	/* Modal */
	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		backdrop-filter: blur(4px);
		z-index: 100;
	}
	.modal {
		position: fixed;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		z-index: 101;
		width: min(480px, 92vw);
		max-height: 85vh;
		overflow-y: auto;
		border: 1px solid var(--line);
		border-radius: 16px;
		background: #14181f;
		padding: 20px;
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5);
	}
	.modal-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 16px;
	}
	.modal-header h2 {
		margin: 0;
		font-size: 16px;
		display: flex;
		align-items: center;
		gap: 6px;
		color: var(--text);
	}
	.modal-body {
		gap: 10px;
	}

	/* Add voice button */
	.btn-add-voice {
		appearance: none;
		border: 1px solid rgba(78, 163, 255, 0.3);
		background: rgba(78, 163, 255, 0.08);
		color: #7cb8f0;
		border-radius: 999px;
		padding: 3px 10px;
		font-size: 11px;
		line-height: 1.5;
		cursor: pointer;
		display: inline-flex;
		align-items: center;
		gap: 4px;
		transition: all 0.15s;
	}
	.btn-add-voice:hover {
		border-color: rgba(78, 163, 255, 0.6);
		background: rgba(78, 163, 255, 0.18);
		color: #b0d4ff;
	}
	.btn-add-voice :global(svg) {
		flex-shrink: 0;
	}

		.page-head {
			display: flex;
			justify-content: space-between;
			align-items: center;
			flex-wrap: wrap;
			gap: 8px;
			padding-bottom: 12px;
		}
		.page-title-row {
			display: flex;
			align-items: center;
			gap: 14px;
		}
		.page-title-row h1 {
			margin: 0;
			font-size: 18px;
		}
		.stat-pills {
			display: flex;
			align-items: center;
			gap: 10px;
		}
		.stat-pill {
			display: inline-flex;
			align-items: center;
			gap: 4px;
			font-size: 12px;
			color: var(--muted);
		}
		.stat-pill :global(svg) {
			color: var(--accent);
			opacity: 0.6;
			flex-shrink: 0;
		}
		.page-title-actions {
			display: flex;
			align-items: center;
			gap: 8px;
		}
		.toolbar-count {
			display: block;
			font-size: 11px;
			padding-top: 4px;
		}
	.voice-card-head {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		align-items: flex-start;
		min-width: 0;
	}

	.toolbar-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 12px;
		align-items: end;
	}

	.search-field {
		display: flex;
		align-items: center;
		gap: 6px;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 0 8px;
		background: #0f1216;
		min-height: 34px;
		overflow: hidden;
	}

	.search-field input {
		border: 0;
		background: transparent;
		width: 100%;
		min-height: 30px;
		padding: 0;
		color: inherit;
		outline: none;
		font-size: 12px;
	}

	.search-clear {
		display: inline-grid;
		place-items: center;
		flex: 0 0 auto;
		width: 22px;
		height: 22px;
		border: 1px solid rgba(255, 255, 255, 0.08);
		border-radius: 999px;
		background: rgba(255, 255, 255, 0.04);
		color: var(--muted);
		cursor: pointer;
		padding: 0;
	}

	.search-clear:hover {
		border-color: rgba(78, 163, 255, 0.5);
		background: rgba(78, 163, 255, 0.12);
		color: var(--text);
	}

	.search-clear:focus-visible {
		outline: 2px solid rgba(78, 163, 255, 0.75);
		outline-offset: 2px;
	}

	.library-toolbar {
		padding-bottom: 12px;
	}

	.voice-toolbar {
			grid-template-columns: 1fr repeat(3, auto);
	}


	.voice-grid {
		grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
	}



	.tag-row {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		align-items: center;
		min-height: 22px;
	}

	.binding-row {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		align-items: center;
		min-height: 22px;
	}

	.card-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		align-items: center;
		justify-content: flex-start;
	}

		/* 折角标签 - 右下角 */
		.dog-ear {
			position: absolute;
			right: 0;
			bottom: 0;
			width: 56px;
			height: 24px;
			background: linear-gradient(225deg, transparent 45%, rgba(255,255,255,0.06) 45%);
			display: flex;
			align-items: flex-end;
			justify-content: center;
			font-size: 10px;
			color: var(--muted);
			letter-spacing: 0.3px;
			padding-bottom: 3px;
			pointer-events: none;
			user-select: none;
		}
		.dog-ear.ok {
			color: #4ade80;
			background: linear-gradient(225deg, transparent 45%, rgba(74,222,128,0.08) 45%);
		}


	.tag-row .badge,
	.binding-row .badge,
	.asset-meta span,
	.text-chip {
		padding: 1px 6px;
		font-size: 11px;
		line-height: 1.4;
		min-height: 22px;
	}

	.tag-filter {
		appearance: none;
		border: 1px solid rgba(148, 163, 184, 0.22);
		background: rgba(148, 163, 184, 0.08);
		font: inherit;
		color: #b8c2cf;
		cursor: pointer;
	}

	.tag-filter.tag-gender {
		color: #a8c8f0;
		border-color: rgba(80, 130, 190, 0.45);
		background: rgba(55, 95, 140, 0.2);
	}

	.tag-filter.tag-age {
		color: #c3b5ff;
		border-color: rgba(120, 100, 190, 0.45);
		background: rgba(85, 70, 140, 0.2);
	}

	.tag-filter.tag-timbre {
		color: #f0c5d8;
		border-color: rgba(157, 82, 112, 0.52);
		background: rgba(107, 59, 80, 0.24);
	}

	.tag-filter.tag-emotion {
		color: #ffd08a;
		border-color: rgba(167, 111, 35, 0.56);
		background: rgba(108, 75, 30, 0.24);
	}

	.tag-filter.tag-use {
		color: #abdcb9;
		border-color: rgba(61, 121, 79, 0.52);
		background: rgba(49, 91, 61, 0.24);
	}

	.tag-filter.tag-source {
		color: #d4c8e8;
		border-color: rgba(130, 110, 160, 0.45);
		background: rgba(95, 80, 120, 0.2);
	}

	.tag-expand-btn {
		appearance: none;
		border: 1px dashed rgba(148, 163, 184, 0.3);
		background: transparent;
		color: var(--muted);
		cursor: pointer;
		padding: 1px 6px;
		font-size: 11px;
		line-height: 1.4;
		min-height: 22px;
		border-radius: 999px;
	}

	.tag-expand-btn:hover {
		border-color: rgba(78, 163, 255, 0.5);
		color: var(--text);
	}

			.tag-cloud-section {
			padding: 10px 0;
		}

		.tag-cloud-header {
			margin-bottom: 8px;
			font-size: 12px;
			font-weight: 500;
		}

		.tag-cloud-category {
			display: flex;
			flex-wrap: wrap;
			align-items: center;
			gap: 5px;
			margin-bottom: 6px;
			position: relative;
			max-height: 26px;
			overflow: hidden;
		}

		.tag-cloud-category.expanded {
			max-height: none;
			overflow: visible;
		}

		.tag-cloud-label {
			font-size: 11px;
			color: var(--muted);
			min-width: 36px;
			flex: 0 0 auto;
		}

		.tag-cloud-chip {
			appearance: none;
			border: 1px solid rgba(148, 163, 184, 0.22);
			background: rgba(148, 163, 184, 0.08);
			font: inherit;
			color: #b8c2cf;
			cursor: pointer;
			padding: 2px 8px;
			font-size: 11px;
			line-height: 1.4;
			border-radius: 999px;
			transition: border-color 0.15s, background 0.15s;
		}

		.tag-cloud-chip:hover {
			border-color: rgba(78, 163, 255, 0.55);
			background: rgba(78, 163, 255, 0.12);
			color: var(--text);
		}

		.tag-cloud-chip.active {
			border-color: rgba(78, 163, 255, 0.7);
			background: rgba(78, 163, 255, 0.18);
			color: #fff;
		}

		.tag-count {
			font-size: 10px;
			opacity: 0.45;
			margin-left: 2px;
		}

		.tag-cloud-expand {
			appearance: none;
			border: none;
			background: transparent;
			color: var(--accent);
			cursor: pointer;
			font-size: 11px;
			padding: 2px 4px;
			flex-shrink: 0;
		}

		.tag-cloud-category:not(.expanded) .tag-cloud-expand {
			position: absolute;
			right: 0;
			bottom: 0;
			padding-left: 24px;
			background: linear-gradient(to right, transparent, #12161d 45%);
			line-height: 1.4;
		}

		.tag-cloud-category.expanded .tag-cloud-expand {
			position: static;
			background: none;
			padding-left: 0;
		}

		.tag-cloud-expand:hover {
			text-decoration: underline;
		}

	.tag-filter:hover {
		border-color: rgba(78, 163, 255, 0.55);
		background: rgba(78, 163, 255, 0.12);
		color: var(--text);
	}

	.tag-filter:focus-visible {
		outline: 2px solid rgba(78, 163, 255, 0.75);
		outline-offset: 2px;
	}

	.asset-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		color: var(--muted);
		font-size: 11px;
		min-height: 22px;
	}

	.asset-meta span {
		display: inline-flex;
		align-items: center;
		gap: 3px;
		border: 1px solid rgba(255, 255, 255, 0.06);
		border-radius: 999px;
		background: rgba(255, 255, 255, 0.025);
		padding: 1px 5px;
		font-size: 10.5px;
		white-space: nowrap;
	}


		.voice-card {
			position: relative;
			overflow: hidden;
			gap: 9px;
			transition: border-color 200ms ease, box-shadow 200ms ease;
		}

		.voice-card.playing {
			border-color: rgba(79, 156, 249, 0.35);
			box-shadow: 0 0 0 1px rgba(79, 156, 249, 0.12), 0 4px 18px rgba(79, 156, 249, 0.1);
		}

		.voice-card .btn.icon-text.playing {
			background: var(--accent);
			border-color: var(--accent);
			color: #07121f;
			font-weight: 600;
		}

	.voice-card-head h2 {
		margin: 0;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 16px;
		line-height: 1.25;
	}

		.voice-desc {
			max-height: 58px;
			overflow-y: auto;
			scrollbar-width: thin;
		}
		.voice-desc.fade-overflow {
			mask-image: linear-gradient(to bottom, black 60%, transparent 100%);
			-webkit-mask-image: linear-gradient(to bottom, black 60%, transparent 100%);
		}

	.voice-card-head .badge {
			margin: 0;
			min-height: 38px;
			max-height: 58px;
			line-height: 1.45;
			overflow-y: auto;
			scrollbar-width: thin;
		}

	.voice-audio-compact {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
		padding: 4px 6px 4px 4px;
		border: 1px solid rgba(255, 255, 255, 0.08);
		border-radius: 8px;
		background: #10151c;
		width: fit-content;
		max-width: 100%;
	}

	.voice-play-btn {
		width: 30px;
		height: 30px;
		border-radius: 7px;
	}

	.voice-audio-label {
		min-width: 0;
		font-size: 12px;
		line-height: 1;
		white-space: nowrap;
	}

	.file-row {
		display: flex;
		align-items: center;
		gap: 8px;
		min-width: 0;
	}

	.file-picker {
		flex: 0 0 auto;
	}

	.file-name {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 12px;
	}

	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}

	@media (max-width: 1100px) {
		.voice-overview {
			grid-template-columns: 1fr 1fr;
		}

		.toolbar-grid,
		.voice-toolbar {
			grid-template-columns: 1fr 1fr;
		}
	}

	@media (max-width: 720px) {
		.voice-overview,
		.toolbar-grid,
		.voice-toolbar {
			grid-template-columns: 1fr;
		}
	}

		/* Icon buttons in card head */
		.card-head-actions {
			display: flex;
			align-items: center;
			gap: 2px;
			flex-shrink: 0;
		}
		.icon-btn-sm {
			appearance: none;
			border: none;
			background: transparent;
			color: var(--muted);
			cursor: pointer;
			padding: 4px;
			border-radius: 6px;
			display: inline-grid;
			place-items: center;
			opacity: 0;
			transition: opacity 0.15s, color 0.15s, background 0.15s, transform 0.1s ease;
		}
		.voice-card:hover .icon-btn-sm {
			opacity: 1;
		}
		.icon-btn-sm:hover {
			background: rgba(255, 255, 255, 0.08);
			color: var(--text);
		}
		.icon-btn-sm.danger:hover {
			color: #ff6b6b;
			background: rgba(255, 80, 80, 0.12);
		}
		.icon-btn-sm:active {
			transform: scale(0.88);
			opacity: 0.7;
		}
		/* Batch ASR button */
		.btn-asr-batch {
			appearance: none;
			border: 1px solid rgba(78, 163, 255, 0.3);
			background: rgba(78, 163, 255, 0.06);
			color: #7cb8f0;
			border-radius: 999px;
			padding: 3px 10px;
			font-size: 11px;
			line-height: 1.5;
			cursor: pointer;
			display: inline-flex;
			align-items: center;
			gap: 4px;
			transition: all 0.15s;
		}
		.btn-asr-batch:hover:not(:disabled) {
			border-color: rgba(78, 163, 255, 0.6);
			background: rgba(78, 163, 255, 0.14);
			color: #b0d4ff;
		}
		.btn-asr-batch:disabled {
			opacity: 0.4;
			cursor: not-allowed;
		}
		.batch-indicator {
			position: relative;
			display: inline-flex;
			align-items: center;
			gap: 5px;
			padding: 3px 10px;
			border-radius: 999px;
			font-size: 11px;
			line-height: 1.5;
			overflow: hidden;
			isolation: isolate;
		}
		.batch-indicator .batch-bar {
			position: absolute;
			inset: 0;
			border-radius: inherit;
			transition: width 0.3s ease;
		}
		.batch-indicator .batch-label {
			position: relative;
			z-index: 1;
			display: inline-flex;
			align-items: center;
			gap: 4px;
			animation: batch-pulse 1.8s ease-in-out infinite;
		}
		.batch-indicator.asr {
			color: #8ec5f5;
			background: rgba(78, 163, 255, 0.08);
			border: 1px solid rgba(78, 163, 255, 0.25);
		}
		.batch-indicator.asr .batch-bar { background: rgba(78, 163, 255, 0.12); }
		.batch-indicator.ser {
			color: #f0d78a;
			background: rgba(224, 173, 66, 0.08);
			border: 1px solid rgba(224, 173, 66, 0.25);
		}
		.batch-indicator.ser .batch-bar { background: rgba(224, 173, 66, 0.12); }
		@keyframes batch-pulse {
			0%, 100% { opacity: 1; }
			50% { opacity: 0.6; }
		}
		.btn-goto {
			border-color: rgba(78, 163, 255, 0.22);
			background: rgba(78, 163, 255, 0.06);
			color: #8ec5f5;
		}
		.btn-goto:hover {
			border-color: rgba(78, 163, 255, 0.48);
			background: rgba(78, 163, 255, 0.14);
			color: #c0dfff;
		}


		/* SER emotion styles */
		.btn-ser-batch {
			appearance: none;
			border: 1px solid rgba(224, 173, 66, 0.3);
			background: rgba(224, 173, 66, 0.06);
			color: #d4b96a;
			border-radius: 999px;
			padding: 3px 10px;
			font-size: 11px;
			line-height: 1.5;
			cursor: pointer;
			display: inline-flex;
			align-items: center;
			gap: 4px;
			transition: all 0.15s;
		}
		.btn-ser-batch:hover:not(:disabled) {
			border-color: rgba(224, 173, 66, 0.6);
			background: rgba(224, 173, 66, 0.14);
			color: #f0d78a;
		}
		.btn-ser-batch:disabled { opacity: 0.4; cursor: not-allowed; }
		.ser-card-btn { color: #d4b96a; }
		.ser-card-btn:hover:not(:disabled) { color: #f0d78a; background: rgba(224, 173, 66, 0.1); }
		.ser-card-btn:disabled { opacity: 0.4; }

		.scroll-sentinel {
			height: 1px;
			width: 100%;
		}

		.loading-indicator {
			display: flex;
			align-items: center;
			justify-content: center;
			gap: 8px;
			padding: 20px;
			color: var(--muted);
			font-size: 13px;
		}

		.loading-spinner {
			width: 18px;
			height: 18px;
			border: 2px solid rgba(255, 255, 255, 0.1);
			border-top-color: var(--accent);
			border-radius: 50%;
			animation: spin 0.8s linear infinite;
		}

		@keyframes spin {
			to { transform: rotate(360deg); }
		}

		.end-of-list {
			grid-column: 1 / -1;
			text-align: center;
			padding: 20px;
			color: var(--muted);
			font-size: 12px;
			opacity: 0.6;
		}
</style>
