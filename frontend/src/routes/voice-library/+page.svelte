<script lang="ts">
	import { Api } from '$lib/api';
	import type { VoiceAsset } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { Check, Database, FileText, Pencil, Play, Plus, Search, ShieldCheck, Trash2, Upload, X } from 'lucide-svelte';
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
	let voiceSort = $state<'updated' | 'name'>('updated');

	async function refresh() {
		voices = await Api.voices();
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

	function tagClass(tag: string) {
		if (tag.includes('男') || tag.includes('女') || tag.includes('角色') || tag.includes('本人')) return 'role';
		if (tag.includes('情绪') || tag.includes('悲伤') || tag.includes('悬疑')) return 'emotion';
		if (tag.includes('讲解') || tag.includes('旁白') || tag.includes('口播') || tag.includes('测试')) return 'use';
		return '';
	}

	function bindingLabel(engineId: string) {
		return {
			'indextts-v2': 'IndexTTS',
			omnivoice: 'OmniVoice',
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

	function cleanTags(tags: string[], limit = 5) {
		return tags
			.filter((tag) => !tag.startsWith('seed:') && !tag.startsWith('pack:') && !tag.startsWith('community:'))
			.filter((tag) => !['官方示例', '参考声音', '社区音色', 'Apache 2.0', '中文'].includes(tag))
			.slice(0, limit);
	}

	function textLabel(text: string | null | undefined) {
		const value = (text ?? '').trim();
		if (!value) return '说明';
		return value.includes('参考音频') || value.includes('用于测试') || value.includes('官方示例') ? '说明' : '台词';
	}

	const filteredVoices = $derived.by(() => {
		const query = voiceQuery.trim().toLowerCase();
		return [...voices]
			.filter((voice) => {
				if (voiceEngineFilter !== 'all' && !voice.engine_bindings?.some((binding) => binding.engine_id === voiceEngineFilter && binding.available)) return false;
				if (voiceLicenseFilter !== 'all' && voice.license_status !== voiceLicenseFilter) return false;
				if (!query) return true;
				return (
					voice.name.toLowerCase().includes(query) ||
					voice.description.toLowerCase().includes(query) ||
					voice.tags.join(' ').toLowerCase().includes(query)
				);
			})
			.sort((a, b) => {
				if (voiceSort === 'name') return a.name.localeCompare(b.name, 'zh-Hans-CN');
				return b.updated_at.localeCompare(a.updated_at);
			});
	});

	const cloudCloneReadyCount = $derived(
		voices.filter((voice) => voice.engine_bindings?.some((binding) => binding.engine_id === 'mimo-v2.5-tts-voiceclone' && binding.available)).length
	);
	const selfOrAuthorizedCount = $derived(
		voices.filter((voice) => ['self_voice', 'authorized', 'company_authorized'].includes(voice.license_status)).length
	);
	const referenceAudioCount = $derived(voices.reduce((total, voice) => total + voice.reference_audio_ids.length, 0));
	const canSaveVoice = $derived(Boolean(name.trim()) && (Boolean(editingVoice) || Boolean(file)));

	const help = [
		{ title: '新增声音', body: '这里只保留自己上传参考音频这一条路径。准备一段 10-20 秒左右的 mp3 或 wav，填写名称和参考文本后保存，它就会进入本地音色库。' },
		{ title: '音色库怎么用', body: '音色库里的声音主要作为声音克隆参考。IndexTTS v2 通常需要选择一个参考声音；OmniVoice 可以选择参考声音，也可以不选，改用声音设计标签。' },
		{ title: '参考文本', body: '参考文本是参考音频里大概说了什么。克隆或多语言模型有时会用它理解发音和音色；卡片里的文本按钮可以快速查看，不会撑大卡片。' },
		{ title: '编辑声音', body: '卡片上的“编辑”会把名称、描述、标签、参考文本和推荐引擎载入右侧表单。这里保存的是同一个声音名称，生成页下拉菜单会同步显示。' }
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
			<Play size={17} />
			<div><span>可云端复刻</span><strong>{cloudCloneReadyCount}</strong></div>
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
							<option value="updated">最近更新</option>
							<option value="name">名称</option>
						</select>
					</label>
				</div>
			</section>

			<section class="grid voice-grid">
			{#each filteredVoices as voice}
				<article class={`card stack voice-card engine-surface ${voiceCardKind(voice) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
					<div class="voice-card-head">
						<h2 title={voice.name}>{voice.name}</h2>
						<span class="badge license" class:ok={voice.license_status === 'self_voice'}>{licenseLabel(voice.license_status)}</span>
					</div>
					<p class="muted voice-desc desc-pop" data-text={voice.description || '暂无描述'}>{voice.description || '暂无描述'}</p>
					<div class="tag-row">{#each cleanTags(voice.tags, 6) as tag}<span class={`badge ${tagClass(tag)}`}>{tag}</span>{/each}</div>
					<div class="asset-meta">
						<span>参考音频 {voice.reference_audio_ids.length}</span>
						<span>{voiceCardKind(voice) === 'cloud' ? '云端' : '本地'}</span>
						<span>{voice.recommended_engine_id ? bindingLabel(voice.recommended_engine_id) : '自动引擎'}</span>
						<span class="text-pop text-chip" data-text={voice.reference_text || '暂无参考文本'}><FileText size={13} /> {textLabel(voice.reference_text)}</span>
					</div>
					<div class="binding-row">
						{#each (voice.engine_bindings ?? []).filter((binding) => binding.engine_id !== 'mimo-v2.5-tts-preset') as binding}
							<span class="badge" class:ok={binding.available} class:warn={!binding.available} title={binding.reason}>{bindingLabel(binding.engine_id)}</span>
						{/each}
					</div>
					{#if voice.reference_audio_ids[0]}
						<audio class="audio" controls src={`/api/voices/${voice.voice_id}/audio/${voice.reference_audio_ids[0]}`}></audio>
					{/if}
					<div class="card-actions">
						<a class="btn primary" href={`/generate?voice=${voice.voice_id}`}><Play size={15} /> 去合成</a>
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

	.library-toolbar {
		padding-bottom: 12px;
	}

	.voice-toolbar {
		grid-template-columns: minmax(0, 1.4fr) repeat(3, minmax(150px, 0.8fr));
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

	.voice-card .audio {
		width: 100%;
		max-width: 100%;
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
