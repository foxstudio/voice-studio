<script lang="ts">
	import { Api } from '$lib/api';
	import type { VoiceAsset, VoiceSeed } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { Check, Download, FileText, Pencil, Play, Plus, Search, Trash2, Upload, X } from 'lucide-svelte';
	import { licenseLabel } from '$lib/labels';
	import { onMount } from 'svelte';

	let voices = $state<VoiceAsset[]>([]);
	let seeds = $state<VoiceSeed[]>([]);
	let name = $state('');
	let description = $state('');
	let tags = $state('');
	let referenceText = $state('');
	let license = $state('unknown');
	let engine = $state<string | null>('indextts-v2');
	let file = $state<File | null>(null);
	let uploadMessage = $state('');
	let importing = $state('');
	let editingVoice = $state<VoiceAsset | null>(null);
	let voiceQuery = $state('');
	let voiceEngineFilter = $state('all');
	let voiceLicenseFilter = $state('all');
	let voiceSort = $state<'updated' | 'name'>('updated');
	let seedQuery = $state('');

	async function refresh() {
		[voices, seeds] = await Promise.all([Api.voices(), Api.voiceSeeds()]);
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
	async function importSeed(seedId: string) {
		importing = seedId;
		try {
			await Api.importVoiceSeed(seedId);
			await refresh();
		} catch (err) {
			uploadMessage = err instanceof Error ? err.message : '导入失败';
		} finally {
			importing = '';
		}
	}

	function tagClass(tag: string) {
		if (tag.startsWith('seed:') || tag.includes('官方')) return 'source';
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

	function seedCardKind(seed: VoiceSeed) {
		return engineKind(seed.recommended_engine_id);
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

	const filteredSeeds = $derived.by(() => {
		const query = seedQuery.trim().toLowerCase();
		return seeds.filter((seed) => {
			if (!query) return true;
			return (
				seed.name.toLowerCase().includes(query) ||
				seed.description.toLowerCase().includes(query) ||
				seed.tags.join(' ').toLowerCase().includes(query)
			);
		});
	});

	const help = [
		{ title: '什么是“可导入参考音色”', body: '这里的官方参考音色还没有真正进入你的音色库，像素材候选。点“导入”后，它会下载到本地，变成下面音色库里的声音，之后才能在单条生成或批处理里选择。' },
		{ title: '音色库怎么用', body: '音色库里的声音主要作为声音克隆参考。IndexTTS v2 通常需要选择一个参考声音；OmniVoice 可以选择参考声音，也可以不选，改用声音设计标签。' },
		{ title: '参考文本', body: '参考文本是参考音频里大概说了什么。克隆或多语言模型有时会用它理解发音和音色；卡片里的文本按钮可以快速查看，不会撑大卡片。' },
		{ title: '编辑声音', body: '卡片上的“编辑”会把名称、描述、标签、参考文本和推荐引擎载入右侧表单。这里保存的是同一个声音名称，生成页下拉菜单会同步显示。' }
	];
</script>

<svelte:head><title>音色管理 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head"><div><h1>音色管理</h1><p class="muted">导入、管理、试听和授权标记参考声音；内容多起来时也能按来源、授权和可用引擎查找。</p></div><HelpDrawer title="音色管理" sections={help} /></div>
	<section class="panel stack" style="margin-bottom:16px">
		<div class="row" style="justify-content:space-between">
			<h2>官方参考音色（可导入）</h2>
			<div class="row">
				<span class="badge">{filteredSeeds.length} 条</span>
				<span class="muted">还未进入音色库的官方参考声音；导入后才能选择使用</span>
			</div>
		</div>
		<div class="toolbar-grid">
			<label class="field">
				<span>搜索官方参考音色</span>
				<div class="search-field">
					<Search size={15} />
					<input bind:value={seedQuery} placeholder="名称、描述、标签" />
				</div>
			</label>
		</div>
		<div class="seed-grid">
			{#each filteredSeeds as seed}
				<article class={`seed engine-surface ${seedCardKind(seed) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
					<div class="row" style="justify-content:space-between"><strong>{seed.name}</strong><span class="badge license">{licenseLabel(seed.license_status)}</span></div>
					<p>{seed.description}</p>
					<div class="row">{#each seed.tags as tag}<span class={`badge ${tagClass(tag)}`}>{tag}</span>{/each}</div>
					<div class="row">
						<span class="badge source">来源：{seed.source}</span>
						<span class="badge badge-kind">{seedCardKind(seed) === 'cloud' ? '云端' : '本地'}</span>
						<span class="badge engine">引擎：{seed.recommended_engine_id}</span>
						<span class="text-pop" data-text={seed.reference_text}><FileText size={15} /> 文本</span>
					</div>
					{#if seed.quality}
						<span class="badge" class:ok={seed.quality.passed} class:warn={!seed.quality.passed}>{seed.quality.passed ? '质量通过' : '需复核'} · RMS {seed.quality.rms.toFixed(3)}</span>
					{/if}
					{#if seed.imported_voice_id}
						<a class="btn success" href={`/generate?voice=${seed.imported_voice_id}`}><Play size={15} /> 使用</a>
					{:else}
						<button class="btn" disabled={Boolean(importing)} onclick={() => importSeed(seed.seed_id)}><Download size={15} /> {importing === seed.seed_id ? '导入中' : '导入'}</button>
					{/if}
				</article>
			{:else}
				<div class="empty">当前筛选下没有可导入的官方参考音色</div>
			{/each}
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
				<article class={`card stack engine-surface ${voiceCardKind(voice) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
					<div class="row" style="justify-content:space-between"><h2>{voice.name}</h2><span class="badge license" class:ok={voice.license_status === 'self_voice'}>{licenseLabel(voice.license_status)}</span></div>
					<p class="muted">{voice.description || '暂无描述'}</p>
					<div class="row">{#each voice.tags as tag}<span class={`badge ${tagClass(tag)}`}>{tag.startsWith('seed:') ? `来源：${tag.replace('seed:', '')}` : tag}</span>{/each}</div>
						<div class="row">
							<span class="badge">参考音频：{voice.reference_audio_ids.length} 个</span>
							<span class="badge badge-kind">{voiceCardKind(voice) === 'cloud' ? '云端' : '本地'}</span>
							<span class="badge engine">推荐引擎：{voice.recommended_engine_id ?? '自动引擎'}</span>
							<span class="text-pop" data-text={voice.reference_text || '暂无参考文本'}><FileText size={15} /> 文本</span>
						</div>
						<div class="row">
							{#each (voice.engine_bindings ?? []).filter((binding) => binding.engine_id !== 'mimo-v2.5-tts-preset') as binding}
								<span class="badge" class:ok={binding.available} class:warn={!binding.available} title={binding.reason}>{bindingLabel(binding.engine_id)}</span>
							{/each}
						</div>
					{#if voice.reference_audio_ids[0]}
						<audio class="audio" controls src={`/api/voices/${voice.voice_id}/audio/${voice.reference_audio_ids[0]}`}></audio>
					{/if}
					<div class="row">
						<a class="btn primary" href={`/generate?voice=${voice.voice_id}`}><Play size={15} /> 使用</a>
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
			<div class="field"><label for="voice-file">{editingVoice ? '追加参考音频' : '参考音频'}</label><input id="voice-file" type="file" accept="audio/*" onchange={(e) => (file = (e.currentTarget as HTMLInputElement).files?.[0] ?? null)} /></div>
			{#if uploadMessage}<p class="muted"><Check size={13} /> {uploadMessage}</p>{/if}
			<button class="btn primary" disabled={!name.trim()} onclick={saveVoice}><Upload size={15} /> {editingVoice ? '保存修改' : '保存声音'}</button>
		</aside>
	</div>
</main>

<style>
	.seed-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		gap: 10px;
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

	.library-toolbar {
		padding-bottom: 12px;
	}

	.voice-toolbar {
		grid-template-columns: minmax(0, 1.4fr) repeat(3, minmax(150px, 0.8fr));
	}

	.voice-grid {
		grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
	}

	.seed {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 12px;
		background: #121519;
		display: grid;
		gap: 8px;
	}

	.seed p {
		margin: 0;
		color: var(--muted);
		line-height: 1.45;
		font-size: 13px;
	}

	@media (max-width: 1100px) {
		.toolbar-grid,
		.voice-toolbar {
			grid-template-columns: 1fr 1fr;
		}
	}

	@media (max-width: 720px) {
		.toolbar-grid,
		.voice-toolbar {
			grid-template-columns: 1fr;
		}
	}
</style>
