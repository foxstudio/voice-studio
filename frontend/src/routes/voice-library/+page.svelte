<script lang="ts">
	import { Api } from '$lib/api';
	import type { CommunityVoicePack, VoiceAsset, VoiceSeed } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { Check, ChevronDown, ChevronUp, Database, Download, FileText, PackageOpen, Pencil, Play, Plus, Search, ShieldCheck, Trash2, Upload, X } from 'lucide-svelte';
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
	let showSources = $state(false);

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
		importingPack = candidateIds.length > 1 ? packId : (candidateIds[0] ?? packId);
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

	function cleanTags(tags: string[], limit = 5) {
		return tags
			.filter((tag) => !tag.startsWith('seed:') && !tag.startsWith('pack:') && !tag.startsWith('community:'))
			.filter((tag) => !['官方示例', '参考声音', '社区音色', 'Apache 2.0', '中文'].includes(tag))
			.slice(0, limit);
	}

	function qualityLabel(quality: { passed?: boolean; rms?: number } | null | undefined) {
		if (!quality) return '';
		return quality.passed ? `声量正常 ${quality.rms?.toFixed(3) ?? '-'}` : `声量需复核 ${quality.rms?.toFixed(3) ?? '-'}`;
	}

	function qualityDetail(quality: { rms?: number; peak?: number; duration_ms?: number; warnings?: string[] } | null | undefined) {
		if (!quality) return '';
		const warnings = quality.warnings?.length ? `；提示：${quality.warnings.join('；')}` : '';
		return `RMS 是平均响度，用来判断参考音频会不会太小声。RMS ${quality.rms ?? '-'}，峰值 ${quality.peak ?? '-'}，时长 ${quality.duration_ms ?? '-'}ms${warnings}`;
	}

	function textLabel(text: string | null | undefined) {
		const value = (text ?? '').trim();
		if (!value) return '说明';
		return value.includes('参考音频') || value.includes('用于测试') || value.includes('官方示例') ? '说明' : '台词';
	}

	function seedAudioUrl(seed: VoiceSeed) {
		return `/api/voice-seeds/${seed.seed_id}/audio`;
	}

	function candidateAudioUrl(pack: CommunityVoicePack, candidateId: string) {
		return `/api/community-voice-packs/${pack.pack_id}/candidates/${candidateId}/audio`;
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

	const pendingSeeds = $derived(seeds.filter((seed) => !seed.imported_voice_id));
	const pendingCommunityPacks = $derived.by(() =>
		communityPacks
			.map((pack) => ({
				...pack,
				candidates: pack.candidates.filter((candidate) => !candidate.imported_voice_id)
			}))
			.filter((pack) => pack.candidates.length > 0)
	);
	const pendingCommunityCandidateCount = $derived(pendingCommunityPacks.reduce((total, pack) => total + pack.candidates.length, 0));
	const hasPendingSources = $derived(pendingSeeds.length > 0 || pendingCommunityCandidateCount > 0);

	$effect(() => {
		if (sourceTab === 'official' && pendingSeeds.length === 0 && pendingCommunityCandidateCount > 0) {
			sourceTab = 'community';
		}
		if (sourceTab === 'community' && pendingCommunityCandidateCount === 0 && pendingSeeds.length > 0) {
			sourceTab = 'official';
		}
	});

	const filteredSeeds = $derived.by(() => {
		const query = seedQuery.trim().toLowerCase();
		return pendingSeeds.filter((seed) => {
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
		return pendingCommunityPacks
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
		{ title: '什么是“待导入素材”', body: '上方只显示还没进入本地音色库的候选参考音频。点“导入”后，它会下载到本地，并从上方消失，统一进入下方音色库。' },
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
			<div><span>社区已入库</span><strong>{communityImportedCount}/{communityCandidateCount}</strong></div>
		</div>
		<div class="metric-card">
			<Play size={17} />
			<div><span>可云端复刻</span><strong>{cloudCloneReadyCount}</strong></div>
		</div>
	</section>

	{#if hasPendingSources}
	<section class:source-panel-compact={!showSources} class="panel stack source-panel">
		<div class="source-head">
			<div>
				<h2>待导入素材</h2>
				<p class="muted">
					{pendingSeeds.length} 个官方示例、{pendingCommunityCandidateCount} 个社区候选还没入库；默认收起，避免和下方音色库重复。
				</p>
			</div>
			<div class="source-actions">
				{#if showSources}
					<div class="source-tabs" role="tablist" aria-label="素材来源">
						<button type="button" class:active={sourceTab === 'official'} onclick={() => (sourceTab = 'official')}>
							官方示例 <span>{filteredSeeds.length}</span>
						</button>
						<button type="button" class:active={sourceTab === 'community'} onclick={() => (sourceTab = 'community')}>
							社区候选 <span>{pendingCommunityCandidateCount}</span>
						</button>
					</div>
				{/if}
				<button class="btn icon-text" type="button" onclick={() => (showSources = !showSources)}>
					{#if showSources}
						<ChevronUp size={15} /> 收起
					{:else}
						<ChevronDown size={15} /> 展开导入
					{/if}
				</button>
			</div>
		</div>

		{#if showSources && sourceTab === 'official'}
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
						<p class="clamp-desc desc-pop" data-text={seed.description}>{seed.description}</p>
						<audio class="audio compact-audio" controls preload="metadata" src={seedAudioUrl(seed)}></audio>
						<div class="tag-row">{#each cleanTags(seed.tags) as tag}<span class={`badge ${tagClass(tag)}`}>{tag}</span>{/each}</div>
						<div class="asset-meta">
							<span>来源：{seed.source}</span>
							<span>{bindingLabel(seed.recommended_engine_id)}</span>
							<span class="text-pop text-chip" data-text={seed.reference_text || '暂无参考文本'}><FileText size={13} /> {textLabel(seed.reference_text)}</span>
						</div>
						{#if seed.quality}
							<span class="badge text-pop" class:ok={seed.quality.passed} class:warn={!seed.quality.passed} data-text={qualityDetail(seed.quality)}>{qualityLabel(seed.quality)}</span>
						{/if}
						{#if seed.imported_voice_id}
							<a class="btn success" href={`/generate?voice=${seed.imported_voice_id}`}><Play size={15} /> 去合成</a>
						{:else}
							<button class="btn" disabled={Boolean(importing)} onclick={() => importSeed(seed.seed_id)}><Download size={15} /> {importing === seed.seed_id ? '导入中' : '导入'}</button>
						{/if}
					</article>
				{:else}
					<div class="empty">官方示例已入库或当前筛选下没有待导入素材。已入库音色在下方音色库里管理。</div>
				{/each}
			</div>
		{:else if showSources}
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
							<span class="badge">待导入 {pack.candidates.length}</span>
							{#each cleanTags(pack.tags, 4) as tag}<span class={`badge ${tagClass(tag)}`}>{tag}</span>{/each}
						</div>
						<div class="candidate-list">
							{#each pack.candidates as candidate}
								<div class="candidate-row">
									<div>
										<strong>{candidate.name}</strong>
										<p class="clamp-desc desc-pop" data-text={candidate.description}>{candidate.description}</p>
										<audio class="audio compact-audio" controls preload="metadata" src={candidateAudioUrl(pack, candidate.candidate_id)}></audio>
										<div class="tag-row">
											<span class="badge license">{licenseLabel(candidate.license_status)}</span>
											{#each cleanTags(candidate.tags, 4) as tag}<span class={`badge ${tagClass(tag)}`}>{tag}</span>{/each}
											<span class="text-pop text-chip" data-text={candidate.reference_text || '暂无参考文本'}><FileText size={13} /> {textLabel(candidate.reference_text)}</span>
										</div>
									</div>
									{#if candidate.imported_voice_id}
										<a class="btn success" href={`/generate?voice=${candidate.imported_voice_id}`}><Play size={15} /> 去合成</a>
									{:else}
										<button class="btn" disabled={Boolean(importingPack)} onclick={() => importCommunityPack(pack.pack_id, [candidate.candidate_id])}>
											<Download size={15} /> {importingPack === candidate.candidate_id ? '导入中' : '导入'}
										</button>
									{/if}
								</div>
							{/each}
						</div>
						<button class="btn primary pack-action" disabled={Boolean(importingPack) || pack.candidates.length === 0} onclick={() => importCommunityPack(pack.pack_id, pack.candidates.map((candidate) => candidate.candidate_id))}>
							<Download size={15} /> {importingPack === pack.pack_id ? '导入中' : '导入剩余'}
						</button>
					</article>
				{:else}
					<div class="empty">社区候选已入库或当前筛选下没有待导入素材。已入库音色在下方音色库里管理。</div>
				{/each}
			</div>
		{/if}
	</section>
	{/if}
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

	.source-panel-compact {
		padding-top: 12px;
		padding-bottom: 12px;
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

	.source-actions {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 8px;
		flex-wrap: wrap;
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

	.seed,
	.asset-card {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 10px;
		background: #121519;
		display: grid;
		gap: 8px;
	}

	.asset-card > .btn,
	.seed > .btn {
		justify-self: start;
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

	.clamp-desc,
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

	.pack {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 10px;
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

	.compact-audio {
		height: 30px;
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

		.source-head,
		.pack-head,
		.candidate-row {
			grid-template-columns: 1fr;
		}

		.source-head,
		.pack-head {
			display: grid;
		}

		.source-actions {
			justify-content: flex-start;
		}

		.pack-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
