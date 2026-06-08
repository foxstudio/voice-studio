<script lang="ts">
	import { Api } from '$lib/api';
	import type { VoiceAsset } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { Check, CircleCheck, Database, FileText, Pencil, Pause, Play, Plus, Search, ShieldCheck, Trash2, Upload, X } from 'lucide-svelte';
	import { licenseLabel } from '$lib/labels';
	import { onMount } from 'svelte';

	let voices = $state<VoiceAsset[]>([]);
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
	let voiceEngineFilter = $state('all');
	let voiceLicenseFilter = $state('all');
	let voiceQualityFilter = $state('all');
	let voiceSort = $state<'updated' | 'name'>('updated');
	let voicePreviewAudio = $state<HTMLAudioElement | null>(null);
	let playingVoiceId = $state('');

	async function refresh() {
		voices = await Api.voices();
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
		refresh();
	});

	function resetForm() {
		name = '';
		description = '';
		tags = '';
		referenceText = '';
		license = 'unknown';
		engine = 'indextts-v2';
		file = null;
		editingVoice = null;
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

	async function markVoiceReviewed(voice: VoiceAsset) {
		const tags = voice.tags.filter((tag) => tag !== 'ASR待复核');
		const reviewedNote = '人工已复核 reference_text。';
		const quality_notes = voice.quality_notes?.includes(reviewedNote)
			? voice.quality_notes
			: [voice.quality_notes, reviewedNote].filter(Boolean).join('\n');
		await Api.updateVoice(voice.voice_id, {
			tags,
			quality_status: 'verified',
			quality_notes
		});
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
		const names = new Set(voices.map((v) => v.name));
		return voiceTags
			.filter((tag) => !/^(seed|pack|community|voice_design|design_prompt|user|source|emotion):/.test(tag))
			.filter((tag) => !NOISE_TAGS.has(tag))
			.filter((tag) => !names.has(tag))
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

	function needsReview(voice: VoiceAsset) {
		return voice.quality_status === 'needs_review' || voice.tags.includes('ASR待复核');
	}

	function qualityLabel(voice: VoiceAsset) {
		if (needsReview(voice)) return 'ASR待复核';
		if (voice.quality_status === 'verified') return '已复核';
		if (voice.quality_status === 'unchecked') return '未质检';
		return voice.quality_status || '未质检';
	}

	function qualityNoteText(voice: VoiceAsset) {
		return voice.quality_notes?.trim() || '暂无质检备注';
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
		for (const voice of voices) {
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
		return [...voices]
			.filter((voice) => {
				if (voiceEngineFilter !== 'all' && !voice.engine_bindings?.some((binding) => binding.engine_id === voiceEngineFilter && binding.available)) return false;
				if (voiceLicenseFilter !== 'all' && voice.license_status !== voiceLicenseFilter) return false;
				if (voiceQualityFilter === 'needs_review' && !needsReview(voice)) return false;
				if (voiceQualityFilter === 'verified' && voice.quality_status !== 'verified') return false;
				if (voiceQualityFilter === 'unchecked' && voice.quality_status !== 'unchecked') return false;
				if (!tokens.length) return true;
				const haystack = [voice.name, voice.description, voice.tags.join(' '), voice.reference_text].join(' ').toLowerCase();
				return tokens.every((token) => haystack.includes(token));
			})
			.sort((a, b) => {
				if (voiceSort === 'name') return a.name.localeCompare(b.name, 'zh-Hans-CN');
				return b.updated_at.localeCompare(a.updated_at);
			});
	});

	const selfOrAuthorizedCount = $derived(
		voices.filter((voice) => ['self_voice', 'authorized', 'company_authorized'].includes(voice.license_status)).length
	);
	const referenceAudioCount = $derived(voices.reduce((total, voice) => total + voice.reference_audio_ids.length, 0));
	const needsReviewCount = $derived(voices.filter((voice) => needsReview(voice)).length);
	const canSaveVoice = $derived(Boolean(name.trim()) && (Boolean(editingVoice) || Boolean(file)));

	const help = [
		{ title: '新增声音', body: '这里只保留自己上传参考音频这一条路径。准备一段 10-20 秒左右的 mp3 或 wav，填写名称和参考文本后保存，它就会进入本地音色库。' },
		{ title: '音色库怎么用', body: '音色库里的声音主要作为声音克隆参考。IndexTTS v2 通常需要选择一个参考声音；F5-TTS 和 CosyVoice Zero-Shot 需要参考音频和准确参考台词；OmniVoice 可以选择参考声音，也可以不选，改用声音设计标签。' },
		{ title: '参考文本', body: '参考文本是参考音频里大概说了什么。克隆或多语言模型有时会用它理解发音和音色；卡片里的文本按钮可以快速查看，不会撑大卡片。' },
		{ title: '编辑声音', body: '卡片上的“编辑”会把名称、描述、标签、参考文本和推荐引擎载入右侧表单。这里保存的是同一个声音名称，生成页下拉菜单会同步显示。' },
		{ title: 'ASR 待复核', body: '用本地 ASR 回填的参考文本会保留 ASR待复核 标签。人工听过参考音频并确认台词后，可以在卡片上标记为已复核。' }
	];
</script>

<svelte:head><title>音色管理 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head"><div><h1>音色管理</h1><p class="muted">上传、管理、试听和授权标记自己的参考声音；内容多起来时也能按授权和可用引擎查找。</p></div><HelpDrawer title="音色管理" sections={help} /></div>
	<section class="voice-overview">
		<div class="metric-card">
			<Database size={17} />
			<div><span>本地音色</span><strong>{voices.length}</strong></div>
		</div>
		<div class="metric-card">
			<ShieldCheck size={17} />
			<div><span>授权可用</span><strong>{selfOrAuthorizedCount}</strong></div>
		</div>
		<div class="metric-card">
			<Play size={17} />
			<div><span>参考音频</span><strong>{referenceAudioCount}</strong></div>
		</div>
		<div class="metric-card">
			<CircleCheck size={17} />
			<div><span>待复核</span><strong>{needsReviewCount}</strong></div>
		</div>
	</section>

	<div class="workbench">
		<section class="stack">
			<section class="panel stack library-toolbar">
				<div class="row" style="justify-content:space-between">
					<h2>本地音色库</h2>
					<span class="muted">{filteredVoices.length} 条</span>
				</div>
				<div class="toolbar-grid voice-toolbar">
					<label class="field">
						<span>搜索</span>
						<div class="search-field">
							<Search size={15} />
							<input bind:value={voiceQuery} placeholder="名称、描述、标签" />
							{#if voiceQuery.trim()}
								<button class="search-clear" type="button" aria-label="清空搜索" title="清空搜索" onclick={() => (voiceQuery = '')}>
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
						<span>复核</span>
						<select bind:value={voiceQualityFilter}>
							<option value="all">全部</option>
							<option value="needs_review">ASR待复核</option>
							<option value="verified">已复核</option>
							<option value="unchecked">未质检</option>
						</select>
					</label>
					<label class="field">
						<span>排序</span>
						<select bind:value={voiceSort}>
							<option value="updated">最近更新</option>
							<option value="name">名称</option>
						</select>
					</label>
				</div>
			</section>

			{#if Object.keys(tagsByCategory).length > 0}
			<section class="tag-cloud-section">
				<div class="tag-cloud-header">
					<span>标签筛选</span>
					<span class="muted">{filteredVoices.length} / {voices.length} 条结果</span>
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
			{#each filteredVoices as voice}
				<article class={`card stack voice-card engine-surface ${voiceCardKind(voice) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
					<div class="voice-card-head">
						<h2 title={voice.name}>{voice.name}</h2>
						<span class="badge license" class:ok={voice.license_status === 'self_voice'}>{licenseLabel(voice.license_status)}</span>
					</div>
					<p class="muted voice-desc desc-pop" data-text={voice.description || '暂无描述'}>{voice.description || '暂无描述'}</p>
					<div class="tag-row">
						{#each cleanTags(voice.tags, expandedCards.has(voice.voice_id) ? 99 : 4) as tag}
							<button class={`badge tag-filter ${tagClass(tag)}`} type="button" title={`添加到搜索：${tag}`} onclick={() => appendVoiceQueryTag(tag)}>{tag}</button>
						{/each}
						{#if cleanTags(voice.tags, 99).length > 4 && !expandedCards.has(voice.voice_id)}
							<button class="tag-expand-btn" type="button" onclick={() => { expandedCards = new Set([...expandedCards, voice.voice_id]); }}>+{cleanTags(voice.tags, 99).length - 4}</button>
						{/if}
					</div>
					<div class="asset-meta">
						<span>×{voice.reference_audio_ids.length} 音频</span>
						<span>{voiceCardKind(voice) === 'cloud' ? '云端' : '本地'}</span>
						<span>{voice.recommended_engine_id ? bindingLabel(voice.recommended_engine_id) : '自动引擎'}</span>
						<span class={`quality-chip ${needsReview(voice) ? 'warn' : voice.quality_status === 'verified' ? 'ok' : ''}`} title={qualityNoteText(voice)}>
							<CircleCheck size={13} /> {qualityLabel(voice)}
						</span>
						<span class="text-pop text-chip" data-text={voiceLineText(voice.reference_text)}><FileText size={13} /> 台词</span>
					</div>
					{#if voice.reference_audio_ids[0]}
						<div class="voice-audio-compact">
							<button
								class="icon-btn voice-play-btn"
								onclick={() => toggleVoicePlayback(voice)}
								title={playingVoiceId === voice.voice_id ? '暂停' : '播放'}
								aria-label={playingVoiceId === voice.voice_id ? '暂停' : '播放'}
							>
								{#if playingVoiceId === voice.voice_id}
									<Pause size={14} />
								{:else}
									<Play size={14} />
								{/if}
							</button>
							<span class="muted voice-audio-label">{playingVoiceId === voice.voice_id ? '播放中…' : '试听'}</span>
						</div>
					{/if}
					<div class="card-actions">
						<a class="btn primary" href={`/generate?voice=${voice.voice_id}`}><Play size={15} /> 去合成</a>
						{#if needsReview(voice)}
							<button class="btn" onclick={() => markVoiceReviewed(voice)}><CircleCheck size={15} /> 已复核</button>
						{/if}
						<button class="btn" onclick={() => editVoice(voice)}><Pencil size={15} /> 编辑</button>
						<button class="btn danger" onclick={() => remove(voice.voice_id)}><Trash2 size={15} /> 删除</button>
					</div>
				</article>
			{:else}
				<div class="empty">还没有声音资产</div>
			{/each}
			</section>
		</section>
		<aside class="panel stack">
			<div class="row" style="justify-content:space-between">
				<h2>{#if editingVoice}<Pencil size={16} /> 编辑声音{:else}<Plus size={16} /> 新增声音{/if}</h2>
				{#if editingVoice}<button class="btn icon-text" onclick={resetForm}><X size={15} /> 取消</button>{/if}
			</div>
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
		</aside>
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
	.voice-overview {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 16px;
	}

	.metric-card {
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.018));
		padding: 12px;
	}

	.metric-card :global(svg) {
		color: var(--accent);
		flex: 0 0 auto;
	}

	.metric-card span {
		display: block;
		color: var(--muted);
		font-size: 12px;
	}

	.metric-card strong {
		display: block;
		margin-top: 3px;
		font-size: 20px;
		line-height: 1;
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
		gap: 8px;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 0 10px;
		background: #0f1216;
		height: 34px;
		min-height: 34px;
		overflow: hidden;
	}

	.search-field input {
		border: 0;
		background: transparent;
		width: 100%;
		height: 30px;
		min-height: 30px;
		padding: 0;
		color: inherit;
		outline: none;
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
		grid-template-columns: minmax(220px, 1.35fr) repeat(4, minmax(118px, 0.72fr));
	}

	.quality-chip.warn {
		color: #ffd08a;
		border-color: rgba(167, 111, 35, 0.56);
		background: rgba(108, 75, 30, 0.24);
	}

	.quality-chip.ok {
		color: #a7e9be;
		border-color: rgba(56, 142, 89, 0.55);
		background: rgba(44, 104, 65, 0.24);
	}

	.voice-grid {
		grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
	}

	.desc-pop {
		position: relative;
		cursor: help;
	}

	.desc-pop:hover::after,
	.desc-pop:focus::after {
		content: attr(data-text);
		position: absolute;
		left: 0;
		bottom: calc(100% + 8px);
		width: min(320px, 76vw);
		max-height: 240px;
		overflow: auto;
		white-space: pre-wrap;
		line-height: 1.65;
		padding: 11px 12px;
		border-radius: 12px;
		border: 1px solid rgba(255, 255, 255, 0.08);
		background: rgba(12, 15, 20, 0.92);
		backdrop-filter: blur(18px);
		color: #eef3fb;
		font-size: 11.5px;
		box-shadow: 0 18px 42px rgba(0, 0, 0, 0.38);
		z-index: 50;
	}

	.tag-row,
	.binding-row,
	.card-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		align-items: center;
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
			display: flex;
			justify-content: space-between;
			align-items: center;
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
		gap: 6px;
		color: var(--muted);
		font-size: 12px;
	}

	.asset-meta span {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		border: 1px solid rgba(255, 255, 255, 0.06);
		border-radius: 999px;
		background: rgba(255, 255, 255, 0.025);
	}

	.voice-card {
		gap: 9px;
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

	.voice-card-head .badge {
		flex: 0 0 auto;
	}

	.voice-desc {
		margin: 0;
		min-height: 38px;
		line-height: 1.45;
		display: -webkit-box;
		line-clamp: 2;
		-webkit-line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
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
</style>
