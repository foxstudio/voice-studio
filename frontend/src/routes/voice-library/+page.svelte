<script lang="ts">
	import { Api } from '$lib/api';
	import type { CommunityVoicePack, VoiceAsset, VoiceSeed } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { Check, Database, Download, FileText, PackageOpen, Pencil, Play, Plus, Search, ShieldCheck, Trash2, Upload, X } from 'lucide-svelte';
	import { licenseLabel } from '$lib/labels';
	import { onMount } from 'svelte';

	let voices = $state<VoiceAsset[]>([]);
	let seeds = $state<VoiceSeed[]>([]);
	let communityPacks = $state<CommunityVoicePack[]>([]);
	let name = $state('');
	let description = $state('');
	let tags = $state('');
	let referenceText = $state('');
	let license = $state('unknown');
	let engine = $state<string | null>('indextts-v2');
	let file = $state<File | null>(null);
	let uploadMessage = $state('');
	let importing = $state('');
	let importingPack = $state('');
	let editingVoice = $state<VoiceAsset | null>(null);
	let voiceQuery = $state('');
	let voiceEngineFilter = $state('all');
	let voiceLicenseFilter = $state('all');
	let voiceSort = $state<'updated' | 'name'>('updated');
	let seedQuery = $state('');
	let packQuery = $state('');
	let sourceTab = $state<'official' | 'community'>('official');

	async function refresh() {
		[voices, seeds, communityPacks] = await Promise.all([Api.voices(), Api.voiceSeeds(), Api.communityVoicePacks()]);
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
	async function importCommunityPack(packId: string, candidateIds: string[] = []) {
		importingPack = candidateIds[0] ?? packId;
		try {
			await Api.importCommunityVoicePack(packId, candidateIds);
			await refresh();
		} catch (err) {
			uploadMessage = err instanceof Error ? err.message : '社区音色包导入失败';
		} finally {
			importingPack = '';
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

	const filteredCommunityPacks = $derived.by(() => {
		const query = packQuery.trim().toLowerCase();
		return communityPacks
			.map((pack) => ({
				...pack,
				candidates: pack.candidates.filter((candidate) => {
					if (!query) return true;
					return (
						pack.name.toLowerCase().includes(query) ||
						pack.description.toLowerCase().includes(query) ||
						candidate.name.toLowerCase().includes(query) ||
						candidate.description.toLowerCase().includes(query) ||
						candidate.tags.join(' ').toLowerCase().includes(query)
					);
				})
			}))
			.filter((pack) => !query || pack.candidates.length > 0 || pack.name.toLowerCase().includes(query) || pack.description.toLowerCase().includes(query));
	});
	const communityCandidateCount = $derived(communityPacks.reduce((total, pack) => total + pack.candidates.length, 0));
	const communityImportedCount = $derived(communityPacks.reduce((total, pack) => total + pack.imported_count, 0));
	const cloudCloneReadyCount = $derived(
		voices.filter((voice) => voice.engine_bindings?.some((binding) => binding.engine_id === 'mimo-v2.5-tts-voiceclone' && binding.available)).length
	);
	const selfOrAuthorizedCount = $derived(
		voices.filter((voice) => ['self_voice', 'authorized', 'company_authorized'].includes(voice.license_status)).length
	);

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
			<PackageOpen size={17} />
			<div><span>社区候选</span><strong>{communityImportedCount}/{communityCandidateCount}</strong></div>
		</div>
		<div class="metric-card">
			<Play size={17} />
			<div><span>可云端复刻</span><strong>{cloudCloneReadyCount}</strong></div>
		</div>
	</section>

	<section class="panel stack source-panel">
		<div class="source-head">
			<div>
				<h2>素材导入</h2>
				<p class="muted">先从官方示例或社区包挑参考音频，导入后会进入下方本地音色库。</p>
			</div>
			<div class="source-tabs" role="tablist" aria-label="素材来源">
				<button type="button" class:active={sourceTab === 'official'} onclick={() => (sourceTab = 'official')}>
					官方示例 <span>{filteredSeeds.length}</span>
				</button>
				<button type="button" class:active={sourceTab === 'community'} onclick={() => (sourceTab = 'community')}>
					社区包 <span>{communityPacks.length}</span>
				</button>
			</div>
		</div>

		{#if sourceTab === 'official'}
			<div class="toolbar-grid compact-toolbar">
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
					<article class={`seed asset-card engine-surface ${seedCardKind(seed) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
						<div class="asset-card-head">
							<strong>{seed.name}</strong>
							<span class="badge license">{licenseLabel(seed.license_status)}</span>
						</div>
						<p>{seed.description}</p>
						<div class="tag-row">{#each seed.tags.slice(0, 6) as tag}<span class={`badge ${tagClass(tag)}`}>{tag}</span>{/each}</div>
						<div class="asset-meta">
							<span>来源：{seed.source}</span>
							<span>{seed.recommended_engine_id}</span>
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
		{:else}
			<div class="toolbar-grid compact-toolbar">
				<label class="field">
					<span>搜索社区音色包</span>
					<div class="search-field">
						<Search size={15} />
						<input bind:value={packQuery} placeholder="包名、候选、标签" />
					</div>
				</label>
			</div>
			<div class="pack-grid">
				{#each filteredCommunityPacks as pack}
					<article class="pack engine-surface engine-local">
						<div class="pack-head">
							<div>
								<h3>{pack.name}</h3>
								<p>{pack.description}</p>
							</div>
							<span class="badge">{pack.imported_count}/{pack.candidates.length} 已导入</span>
						</div>
						<div class="tag-row">
							<span class="badge source">来源：{pack.source}</span>
							<span class="badge license">{pack.license_summary}</span>
							{#each pack.tags.slice(0, 5) as tag}<span class={`badge ${tagClass(tag)}`}>{tag}</span>{/each}
						</div>
						<div class="candidate-list">
							{#each pack.candidates as candidate}
								<div class="candidate-row">
									<div>
										<strong>{candidate.name}</strong>
										<p>{candidate.description}</p>
										<div class="tag-row">
											<span class="badge license">{licenseLabel(candidate.license_status)}</span>
											{#each candidate.tags.slice(0, 4) as tag}<span class={`badge ${tagClass(tag)}`}>{tag}</span>{/each}
										</div>
									</div>
									{#if candidate.imported_voice_id}
										<a class="btn success" href={`/generate?voice=${candidate.imported_voice_id}`}><Play size={15} /> 使用</a>
									{:else}
										<button class="btn" disabled={Boolean(importingPack)} onclick={() => importCommunityPack(pack.pack_id, [candidate.candidate_id])}>
											<Download size={15} /> {importingPack === candidate.candidate_id ? '导入中' : '导入'}
										</button>
									{/if}
								</div>
							{/each}
						</div>
						<button class="btn primary pack-action" disabled={Boolean(importingPack) || pack.imported_count === pack.candidates.length} onclick={() => importCommunityPack(pack.pack_id)}>
							<Download size={15} /> {importingPack === pack.pack_id ? '整包导入中' : '导入整包'}
						</button>
					</article>
				{:else}
					<div class="empty">当前筛选下没有社区音色包</div>
				{/each}
			</div>
		{/if}
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
					<p class="muted voice-desc">{voice.description || '暂无描述'}</p>
					<div class="tag-row">{#each voice.tags.slice(0, 8) as tag}<span class={`badge ${tagClass(tag)}`}>{tag.startsWith('seed:') ? `来源：${tag.replace('seed:', '')}` : tag}</span>{/each}</div>
					<div class="asset-meta">
						<span>参考音频 {voice.reference_audio_ids.length}</span>
						<span>{voiceCardKind(voice) === 'cloud' ? '云端' : '本地'}</span>
						<span>{voice.recommended_engine_id ?? '自动引擎'}</span>
						<span class="text-pop" data-text={voice.reference_text || '暂无参考文本'}><FileText size={15} /> 文本</span>
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

	.source-panel {
		margin-bottom: 16px;
	}

	.source-head,
	.pack-head,
	.asset-card-head,
	.voice-card-head {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		align-items: flex-start;
		min-width: 0;
	}

	.source-head h2,
	.source-head p,
	.pack-head h3,
	.pack-head p {
		margin: 0;
	}

	.source-tabs {
		display: inline-flex;
		gap: 4px;
		padding: 4px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #0d1014;
	}

	.source-tabs button {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		border: 0;
		border-radius: 6px;
		background: transparent;
		color: var(--muted);
		min-height: 30px;
		padding: 0 10px;
		cursor: pointer;
	}

	.source-tabs button.active {
		background: #1a2533;
		color: var(--text);
	}

	.source-tabs span {
		color: var(--muted);
		font-size: 12px;
	}

	.seed-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
		gap: 10px;
	}

	.pack-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
		gap: 12px;
	}

	.toolbar-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 12px;
		align-items: end;
	}

	.compact-toolbar {
		grid-template-columns: minmax(260px, 420px);
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

	.seed,
	.asset-card {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 12px;
		background: #121519;
		display: grid;
		gap: 8px;
	}

	.seed p,
	.asset-card p {
		margin: 0;
		color: var(--muted);
		line-height: 1.45;
		font-size: 13px;
		display: -webkit-box;
		line-clamp: 2;
		-webkit-line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.pack {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 12px;
		background: #121519;
		display: grid;
		gap: 10px;
	}

	.pack h3,
	.pack p {
		margin: 0;
	}

	.pack p {
		color: var(--muted);
		line-height: 1.45;
		font-size: 13px;
	}

	.tag-row,
	.binding-row,
	.card-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		align-items: center;
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
		min-height: 23px;
		border: 1px solid rgba(255, 255, 255, 0.06);
		border-radius: 999px;
		padding: 0 8px;
		background: rgba(255, 255, 255, 0.025);
	}

	.candidate-list {
		display: grid;
		gap: 8px;
		max-height: 330px;
		overflow: auto;
		padding-right: 2px;
	}

	.candidate-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 10px;
		align-items: center;
		border: 1px solid rgba(255, 255, 255, 0.06);
		border-radius: 6px;
		padding: 8px;
		background: rgba(255, 255, 255, 0.025);
	}

	.candidate-row p {
		margin: 4px 0 6px;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.35;
		display: -webkit-box;
		line-clamp: 2;
		-webkit-line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.pack-action {
		justify-self: start;
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

		.source-head,
		.pack-head,
		.candidate-row {
			grid-template-columns: 1fr;
		}

		.source-head,
		.pack-head {
			display: grid;
		}

		.pack-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
