<script lang="ts">
	import type {
		VideoLocalizationCue,
		VideoLocalizationDraft,
		VideoLocalizationGeneratedCandidate,
		VideoLocalizationReferenceClip,
		VideoLocalizationReferenceClipCreate,
		VideoLocalizationReferenceClipUpdate,
		VideoLocalizationSubtitleCue,
		VideoLocalizationVoiceRecipe
	} from '$lib/api/types';
	import { AudioLines, Captions, CheckCircle2, ListTodo, Palette, WandSparkles } from 'lucide-svelte';
	import TaskProgressPanel from './TaskProgressPanel.svelte';
	import type { ActivityTask } from './activity-notice';
	import { candidateAudioUrl, durationLabel, msLabel, referenceAudioUrl, referenceCoverUrl, timeLabel } from './utils';
	import {
		SUBTITLE_SOURCE_LABELS,
		SUBTITLE_STYLE_LABELS,
		defaultSubtitlePreviewState,
		type SubtitlePreviewState,
		type SubtitleStylePreset
	} from './studio-state';

	let {
		draft,
		projectId,
		selectedCue,
		selectedLocalizedSubtitle = null,
		selectionRange,
		selectedVoiceId,
		selectedRecipeId,
		inspectorSection = 'subtitle',
		inspectorVoiceTab = 'library',
		subtitlePreview = defaultSubtitlePreviewState(),
		onSelectedVoiceIdChange,
		onSectionChange,
		onUpdateCue,
		onUpdateLocalizedSubtitle = undefined,
		onDeleteLocalizedSubtitle = undefined,
		onSaveCue,
		onConfirmCueTiming,
		onDeleteCue,
		onUpdateSubtitlePreview,
		onCreateReferenceCandidates,
		onCreateReferenceFromSelection,
		onUpdateReferenceClip,
		onDeleteReferenceClip,
		onSelectedRecipeIdChange,
		onCreateVoiceRecipe,
		onUpdateVoiceRecipe,
		onDeleteVoiceRecipe,
		onQuickGenerateVoice,
		onTuneVoiceInGenerate,
		onSendReferenceOnlyToGenerate,
		onApplyGeneratedCandidate,
		creatingReferences,
		savingCue,
		confirmingCueTiming,
		referenceUpdatingId,
		candidateApplyingId,
		generatingVoice,
		taskHistory = [],
		onCancelTask = undefined,
		subtitleRuntimeBusy = false,
		localizationRuntimeBusy = false,
		taskCenterPulseKey = 0
	}: {
		draft: VideoLocalizationDraft | null;
		projectId: string;
		selectedCue: VideoLocalizationCue | null;
		selectedLocalizedSubtitle?: VideoLocalizationSubtitleCue | null;
		selectionRange: { start_ms: number; end_ms: number } | null;
		selectedVoiceId: string;
		selectedRecipeId: string;
		inspectorSection?: 'tasks' | 'voice' | 'generate' | 'subtitle' | 'style';
		inspectorVoiceTab?: 'library' | 'save-selection';
		subtitlePreview?: SubtitlePreviewState;
		onSelectedVoiceIdChange: (voiceId: string) => void;
		onSectionChange: (section: 'tasks' | 'voice' | 'generate' | 'subtitle' | 'style') => void;
		onUpdateCue: (patch: Partial<VideoLocalizationCue>) => void;
		onUpdateLocalizedSubtitle?: (patch: Partial<VideoLocalizationSubtitleCue>) => void | Promise<void>;
		onDeleteLocalizedSubtitle?: (subtitleId: string) => void | Promise<void>;
		onSaveCue: () => void;
		onConfirmCueTiming: () => void;
		onDeleteCue: () => void;
		onUpdateSubtitlePreview: (patch: Partial<SubtitlePreviewState>) => void;
		onCreateReferenceCandidates: () => void | Promise<void>;
		onCreateReferenceFromSelection: (payload: VideoLocalizationReferenceClipCreate) => void | Promise<void>;
		onUpdateReferenceClip: (referenceClipId: string, patch: VideoLocalizationReferenceClipUpdate, successMessage: string) => void | Promise<void>;
		onDeleteReferenceClip: (referenceClipId: string) => void | Promise<void>;
		onSelectedRecipeIdChange: (recipeId: string) => void;
		onCreateVoiceRecipe: () => void | Promise<void>;
		onUpdateVoiceRecipe: (recipeId: string, patch: Partial<VideoLocalizationVoiceRecipe>) => void | Promise<void>;
		onDeleteVoiceRecipe: (recipeId: string) => void | Promise<void>;
		onQuickGenerateVoice: () => void | Promise<void>;
		onTuneVoiceInGenerate: () => void | Promise<void>;
		onSendReferenceOnlyToGenerate: () => void | Promise<void>;
		onApplyGeneratedCandidate: (candidateId: string) => void | Promise<void>;
		creatingReferences: boolean;
		savingCue: boolean;
		confirmingCueTiming: boolean;
		referenceUpdatingId: string;
		candidateApplyingId: string;
		generatingVoice: boolean;
		taskHistory?: ActivityTask[];
		onCancelTask?: (task: ActivityTask) => void | Promise<void>;
		subtitleRuntimeBusy?: boolean;
		localizationRuntimeBusy?: boolean;
		taskCenterPulseKey?: number;
	} = $props();

	let activeTab = $state<'library' | 'save-selection'>('library');
	let sampleTitle = $state('');
	let samplePerson = $state('');
	let sampleEmotion = $state('');
	let sampleTags = $state('');
	let sampleDescription = $state('');
	let voiceSearch = $state('');
	let editTitle = $state('');
	let editPerson = $state('');
	let editEmotion = $state('');
	let editTags = $state('');
	let editDescription = $state('');
	let recipeName = $state('');
	let recipeDescription = $state('');
	let recipeTags = $state('');
	let recipeSnapshotText = $state('');
	let activeSection = $state<'tasks' | 'voice' | 'generate' | 'subtitle' | 'style'>('subtitle');

	const selectedVoice = $derived(
		(draft?.reference_clips ?? []).find((clip) => clip.reference_clip_id === selectedVoiceId) ?? draft?.reference_clips[0] ?? null
	);
	const filteredVoices = $derived(
		(draft?.reference_clips ?? []).filter((clip) => {
			const query = voiceSearch.trim().toLowerCase();
			if (!query) return true;
			return [voiceTitle(clip), clip.person_name, clip.emotion, clip.description, clip.asr_text, clip.speaker_id, ...(clip.tags ?? [])]
				.filter(Boolean)
				.some((value) => String(value).toLowerCase().includes(query));
		})
	);
	const selectedVoiceRecipes = $derived((draft?.voice_recipes ?? []).filter((recipe) => recipe.reference_clip_id === selectedVoice?.reference_clip_id));
	const selectedRecipe = $derived(selectedVoiceRecipes.find((recipe) => recipe.recipe_id === selectedRecipeId) ?? selectedVoiceRecipes[0] ?? null);
	const selectedVoiceCandidates = $derived(
		(draft?.generated_candidates ?? []).filter((candidate) => candidate.recipe_id === selectedRecipe?.recipe_id)
	);
	const canGenerateVoice = $derived(Boolean(selectedVoice?.audio_path && selectedCue?.tts_recommended_text?.trim()));
	const selectedReviewSegments = $derived(reviewSegmentsForCue(draft, selectedCue));
	const canSaveSelection = $derived(
		Boolean(
			selectionRange &&
				selectionRange.end_ms > selectionRange.start_ms &&
				draft?.stems.separation_status === 'completed' &&
				draft?.stems.vocals_clean_path
		)
	);

	function reviewSegmentsForCue(currentDraft: VideoLocalizationDraft | null, cue: VideoLocalizationCue | null) {
		if (!currentDraft?.transcription || !cue) return [];
		const wordIds = new Set(cue.source_word_ids ?? []);
		const segmentIds = new Set(
			currentDraft.transcription.words
				.filter((word) => wordIds.has(word.word_id))
				.map((word) => word.segment_id)
		);
		if (!segmentIds.size && cue.start_ms !== null && cue.end_ms !== null) {
			for (const segment of currentDraft.transcription.segments) {
				if (segment.end_ms > cue.start_ms && segment.start_ms < cue.end_ms) segmentIds.add(segment.segment_id);
			}
		}
		return currentDraft.transcription.segments.filter((segment) => segmentIds.has(segment.segment_id));
	}

	function reviewReasonLabel(reason: string | null | undefined) {
		const labels: Record<string, string> = {
			'llm_review_rejected:numbers_changed': '候选修改了数字或数值',
				'llm_review_rejected:negation_changed': '候选修改了否定关系',
				'llm_review_rejected:language_changed': '候选改变了原文语言',
				'llm_review_rejected:too_different': '候选改写幅度过大',
				'llm_review_rejected:empty_text': '候选为空，已保留原始识别文本'
		};
		return reason ? labels[reason] ?? reason : '';
	}

	function subtitleStatusLabel(status: VideoLocalizationCue['review_status']) {
		return {
			ready: '字幕已校对',
			needs_review: '待校对',
			blocked: '阻断',
			locked: '已锁定'
		}[status];
	}
	const saveSelectionHint = $derived(selectionHint());

	function selectVoice(clip: VideoLocalizationReferenceClip) {
		onSelectedVoiceIdChange(clip.reference_clip_id);
		if (selectedCue) onUpdateCue({ reference_clip_id: clip.reference_clip_id });
	}

	function voiceTitle(clip: VideoLocalizationReferenceClip) {
		return clip.title?.trim() || clip.person_name?.trim() || clip.reference_clip_id.replace(/^ref_/, 'Ref ');
	}

	function isAppliedCandidate(candidate: VideoLocalizationGeneratedCandidate) {
		return Boolean(candidate.audio_path && candidate.cue_id === selectedCue?.cue_id && candidate.audio_path === selectedCue?.tts_audio_path);
	}

	function resetSampleFields() {
		sampleTitle = '';
		samplePerson = selectedCue?.speaker_id ?? '';
		sampleEmotion = '';
		sampleTags = '';
		sampleDescription = '';
	}

	function tagsFromInput(value: string) {
		return value
			.split(/[，,]/)
			.map((tag) => tag.trim())
			.filter(Boolean);
	}

	function syncEditFields(clip: VideoLocalizationReferenceClip | null) {
		editTitle = clip?.title ?? '';
		editPerson = clip?.person_name ?? '';
		editEmotion = clip?.emotion ?? '';
		editTags = (clip?.tags ?? []).join(', ');
		editDescription = clip?.description ?? '';
	}

	async function saveSelectedVoiceMeta() {
		if (!selectedVoice) return;
		await onUpdateReferenceClip(
			selectedVoice.reference_clip_id,
			{
				title: editTitle,
				person_name: editPerson,
				emotion: editEmotion,
				tags: tagsFromInput(editTags),
				description: editDescription
			},
			'项目音色信息已更新'
		);
	}

	async function saveSelectionAsVoice() {
		await onCreateReferenceFromSelection({
			start_ms: selectionRange?.start_ms ?? null,
			end_ms: selectionRange?.end_ms ?? null,
			speaker_id: selectedCue?.speaker_id ?? null,
			asr_text: selectedCue?.en_subtitle_text ?? selectedCue?.zh_localized_subtitle_text ?? null,
			title: sampleTitle,
			person_name: samplePerson || selectedCue?.speaker_id || null,
			emotion: sampleEmotion,
			tags: tagsFromInput(sampleTags),
			description: sampleDescription
		});
		resetSampleFields();
		activeTab = 'library';
	}

	function syncRecipeFields(recipe: VideoLocalizationVoiceRecipe | null) {
		recipeName = recipe?.name ?? '';
		recipeDescription = recipe?.description ?? '';
		recipeTags = (recipe?.tags ?? []).join(', ');
		recipeSnapshotText = recipe ? JSON.stringify(recipe.parameter_snapshot ?? {}, null, 2) : '';
	}

	async function saveSelectedRecipe() {
		if (!selectedRecipe) return;
		let snapshot: Record<string, unknown>;
		try {
			const parsed = JSON.parse(recipeSnapshotText || '{}') as unknown;
			if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('参数快照必须是 JSON object');
			snapshot = parsed as Record<string, unknown>;
		} catch (e) {
			window.alert((e as Error).message || '参数快照 JSON 无效');
			return;
		}
		await onUpdateVoiceRecipe(selectedRecipe.recipe_id, {
			name: recipeName,
			description: recipeDescription,
			tags: tagsFromInput(recipeTags),
			parameter_snapshot: snapshot
		});
	}

	function selectionHint() {
		if (!selectionRange || selectionRange.end_ms <= selectionRange.start_ms) return '在人声波形上拖动，先创建一个音频选区。';
		if (draft?.stems.separation_status !== 'completed' || !draft?.stems.vocals_clean_path) return '先生成人声轨，再从干净人声保存项目音色。';
		return '会从人声轨当前选区切出音频，加入项目音色库等待复听确认。';
	}

	const stylePresets: SubtitleStylePreset[] = ['yellow-outline', 'boxed', 'clean-shadow', 'strong-outline'];

	$effect(() => {
		selectedVoice?.reference_clip_id;
		syncEditFields(selectedVoice);
	});

	$effect(() => {
		selectedRecipe?.recipe_id;
		syncRecipeFields(selectedRecipe);
	});

	$effect(() => {
		inspectorSection;
		activeSection = inspectorSection;
	});

	$effect(() => {
		inspectorVoiceTab;
		activeTab = inspectorVoiceTab;
	});
</script>

<aside class="inspector" class:tasks-view={activeSection === 'tasks'}>
	<div class="inspector-mode-tabs" aria-label="右侧检查器">
		<button class:active={activeSection === 'tasks'} type="button" data-tooltip="任务：查看后台处理进度、每一步状态和历史结果。" onclick={() => onSectionChange('tasks')}><ListTodo size={14} /><span>任务</span></button>
		<button class:active={activeSection === 'subtitle'} type="button" data-tooltip="字幕：编辑时间线中当前选中的字幕片段。" onclick={() => onSectionChange('subtitle')}><Captions size={14} /><span>字幕</span></button>
		<button class:active={activeSection === 'voice'} type="button" data-tooltip="音色：管理项目样音，或把当前音频选区保存为音色。" onclick={() => onSectionChange('voice')}><AudioLines size={14} /><span>音色</span></button>
		<button class:active={activeSection === 'generate'} type="button" data-tooltip="生成：使用当前音色和参数组生成所选字幕的配音。" onclick={() => onSectionChange('generate')}><WandSparkles size={14} /><span>生成</span></button>
		<button class:active={activeSection === 'style'} type="button" data-tooltip="样式：调整视频预览中的字幕位置和外观。" onclick={() => onSectionChange('style')}><Palette size={14} /><span>样式</span></button>
	</div>

	{#if activeSection === 'tasks'}
		<div class="task-view-content">
			<TaskProgressPanel tasks={taskHistory} {onCancelTask} pulseKey={taskCenterPulseKey} full />
		</div>
	{/if}

	{#if activeSection === 'voice'}
		<div class="inspector-tabs">
			<button class:active={activeTab === 'library'} type="button" data-tooltip="项目音色库：试听、检索和编辑本项目已保存的样音。" onclick={() => (activeTab = 'library')}>项目音色库</button>
			<button class:active={activeTab === 'save-selection'} type="button" data-tooltip="保存当前选区：把时间线上的自由音频范围裁成项目样音。" onclick={() => (activeTab = 'save-selection')}>保存当前选区</button>
		</div>
	{/if}

	{#if activeSection === 'voice' && activeTab === 'library'}
		<section class="inspector-panel">
			<div class="panel-head">
				<h2>已保存音色</h2>
				<span>{draft?.reference_clips.length ?? 0} 个</span>
			</div>
			<label class="field search-field">
				<span>搜索</span>
				<input value={voiceSearch} placeholder="按人物、标签、情绪、ASR 搜索" oninput={(event) => (voiceSearch = event.currentTarget.value)} />
			</label>
			{#if draft?.reference_clips.length}
				<div class="voice-list">
					{#each filteredVoices as clip}
						<button class="voice-card" class:active={selectedVoice?.reference_clip_id === clip.reference_clip_id} type="button" data-tooltip={`选择音色：将“${voiceTitle(clip)}”绑定到当前字幕和生成面板。`} onclick={() => selectVoice(clip)}>
							<div class="voice-cover">
								{#if referenceCoverUrl(projectId, clip)}<img src={referenceCoverUrl(projectId, clip)} alt="" />{/if}
							</div>
							<div>
								<strong>{voiceTitle(clip)}</strong>
								<span>{durationLabel(clip.duration_ms)} · {clip.person_name || clip.speaker_id || '未命名'} · {clip.emotion || clip.cleanliness}</span>
								<small>{clip.asr_text || '暂无参考音 ASR'}</small>
							</div>
						</button>
					{/each}
				</div>
				{#if !filteredVoices.length}
					<p class="empty-text">没有匹配的项目音色。</p>
				{/if}
			{:else}
				<p class="empty-text">还没有项目音色。先在人声轨选择一段干净人声，再保存为当前项目音色。</p>
			{/if}
			{#if selectedVoice}
				<div class="voice-detail">
					<div class="detail-row"><span>来源</span><strong>{selectedVoice.source_stem}</strong></div>
					<div class="detail-row"><span>时间</span><strong>{selectedVoice.start_ms ?? 0}ms - {selectedVoice.end_ms ?? 0}ms</strong></div>
					<div class="detail-row"><span>状态</span><strong>{selectedVoice.cleanliness} / {selectedVoice.asr_status}</strong></div>
					{#if referenceAudioUrl(projectId, selectedVoice)}
						<audio controls src={referenceAudioUrl(projectId, selectedVoice)}></audio>
					{/if}
					<div class="sample-form">
						<label class="field compact-field">
							<span>标题</span>
							<input value={editTitle} oninput={(event) => (editTitle = event.currentTarget.value)} />
						</label>
						<div class="editor-grid">
							<label class="field compact-field">
								<span>人物</span>
								<input value={editPerson} oninput={(event) => (editPerson = event.currentTarget.value)} />
							</label>
							<label class="field compact-field">
								<span>情绪</span>
								<input value={editEmotion} oninput={(event) => (editEmotion = event.currentTarget.value)} />
							</label>
						</div>
						<label class="field compact-field">
							<span>标签</span>
							<input value={editTags} placeholder="室内, 开心, 干声" oninput={(event) => (editTags = event.currentTarget.value)} />
						</label>
						<label class="field compact-field">
							<span>介绍</span>
							<textarea rows="2" value={editDescription} oninput={(event) => (editDescription = event.currentTarget.value)}></textarea>
						</label>
					</div>
					<div class="reference-actions">
						<button type="button" data-tooltip="保存信息：更新当前项目音色的名称、人物、情绪和标签。" onclick={saveSelectedVoiceMeta} disabled={referenceUpdatingId === selectedVoice.reference_clip_id}>保存信息</button>
						<button
							type="button"
							data-tooltip="确认可用：标记当前样音为干净且 ASR 已验证，可用于声音克隆。"
							disabled={referenceUpdatingId === selectedVoice.reference_clip_id || !selectedVoice.asr_text?.trim()}
							onclick={() => onUpdateReferenceClip(selectedVoice.reference_clip_id, { cleanliness: 'clean', asr_status: 'verified', asr_text: selectedVoice.asr_text ?? '' }, '参考音已确认可用')}
						>
							确认可用
						</button>
						<button class="danger-btn" type="button" data-tooltip="删除音色：从当前项目音色库移除该样音。" onclick={() => onDeleteReferenceClip(selectedVoice.reference_clip_id)} disabled={referenceUpdatingId === selectedVoice.reference_clip_id}>删除</button>
					</div>
				</div>
			{/if}
		</section>
	{:else if activeSection === 'voice'}
		<section class="inspector-panel">
			<div class="panel-head">
				<h2>保存当前选区为音色</h2>
				<span>{selectedCue ? selectedCue.cue_id : '未选择'}</span>
			</div>
			<div class="save-selection">
				<div class="cover-placeholder">
					<span>{samplePerson || selectedCue?.speaker_id || '未命名人物'}</span>
				</div>
				<div class="selection-rows">
					<div><span>时间段</span><strong>{selectionRange ? `${msLabel(selectionRange.start_ms)} - ${msLabel(selectionRange.end_ms)}` : '--:--'}</strong></div>
					<div><span>来源轨道</span><strong>{draft?.stems.vocals_clean_path ? '人声轨' : '等待人声轨'}</strong></div>
					<div><span>文本</span><strong>{selectedCue?.en_subtitle_text || selectedCue?.zh_localized_subtitle_text || '无字幕文本'}</strong></div>
				</div>
				<div class="sample-form">
					<label class="field compact-field">
						<span>标题</span>
						<input value={sampleTitle} placeholder="例如：机场开场自然说话" oninput={(event) => (sampleTitle = event.currentTarget.value)} />
					</label>
					<div class="editor-grid">
						<label class="field compact-field">
							<span>人物</span>
							<input value={samplePerson || selectedCue?.speaker_id || ''} placeholder="人名/角色" oninput={(event) => (samplePerson = event.currentTarget.value)} />
						</label>
						<label class="field compact-field">
							<span>情绪</span>
							<input value={sampleEmotion} placeholder="自然、兴奋、低声" oninput={(event) => (sampleEmotion = event.currentTarget.value)} />
						</label>
					</div>
					<label class="field compact-field">
						<span>标签</span>
						<input value={sampleTags} placeholder="室内, 近景, 干声" oninput={(event) => (sampleTags = event.currentTarget.value)} />
					</label>
					<label class="field compact-field">
						<span>备注</span>
						<textarea rows="2" value={sampleDescription} placeholder="这段声音后续适合哪些台词或场景" oninput={(event) => (sampleDescription = event.currentTarget.value)}></textarea>
					</label>
				</div>
				<p>{saveSelectionHint}</p>
				<button type="button" data-tooltip={canSaveSelection ? '保存选区：从人声轨裁切当前范围并加入项目音色库。' : saveSelectionHint} disabled={!canSaveSelection || creatingReferences} onclick={saveSelectionAsVoice}>
					{creatingReferences ? '保存中' : '保存到项目音色库'}
				</button>
			</div>
		</section>
	{/if}

	{#if activeSection === 'generate'}
		<section class="inspector-panel voice-lab-panel">
			<div class="panel-head">
				<h2>配音生成</h2>
				<span>{selectedVoice ? voiceTitle(selectedVoice) : '未选择音色'}</span>
			</div>
			<div class="voice-lab">
				<div class="dubbing-stage-note">
					<strong>配音阶段</strong>
					<span>这里的台词和路线只用于生成声音，不影响字幕校对与 SRT 导出。</span>
				</div>
				<div class="voice-route">
					<div>
						<span>当前音色</span>
						<strong>{selectedVoice ? voiceTitle(selectedVoice) : '从音色库选择'}</strong>
					</div>
					<div>
						<span>当前字幕</span>
						<strong>{selectedCue?.cue_id ?? '未选择字幕'}</strong>
					</div>
				</div>
				<div class="dubbing-fields">
					<label class="field compact-field">
						<span>配音台词（TTS）</span>
						<textarea rows="3" value={selectedCue?.tts_recommended_text ?? ''} placeholder="进入配音阶段后填写；字幕阶段无需准备" disabled={!selectedCue} oninput={(event) => onUpdateCue({ tts_recommended_text: event.currentTarget.value })}></textarea>
					</label>
					<label class="field compact-field">
						<span>配音路线</span>
						<select value={selectedCue?.audio_route ?? 'manual_review'} disabled={!selectedCue} onchange={(event) => onUpdateCue({ audio_route: event.currentTarget.value as VideoLocalizationCue['audio_route'] })}>
							<option value="clone_from_source">克隆源声音</option>
							<option value="preset_tts">预设 TTS</option>
							<option value="preserve_original_audio">保留原声</option>
							<option value="manual_review">人工复核</option>
						</select>
					</label>
				</div>
			<div class="recipe-list">
				{#if selectedVoiceRecipes.length}
					{#each selectedVoiceRecipes as recipe}
						<button class="recipe-card" class:active={selectedRecipe?.recipe_id === recipe.recipe_id} type="button" data-tooltip={`选择参数组：后续生成将复用“${recipe.name}”的引擎与参数。`} disabled={!selectedVoice} onclick={() => onSelectedRecipeIdChange(recipe.recipe_id)}>
							<strong>{recipe.name}</strong>
							<span>{recipe.description || `${recipe.engine_id} · ${(recipe.tags ?? []).join(' / ') || '无标签'}`}</span>
						</button>
					{/each}
				{:else}
					<button class="recipe-card active" type="button" data-tooltip="默认参数：首次生成使用当前引擎默认值，并自动保存为参数组。" disabled={!canGenerateVoice}>
						<strong>默认参数</strong>
						<span>首次一键生成时会自动保存为参数组</span>
					</button>
				{/if}
				<button class="recipe-card add-recipe" type="button" data-tooltip="新增参数组：复制当前音色默认参数并创建可复用预设。" disabled={!canGenerateVoice} onclick={onCreateVoiceRecipe}>
					<strong>新增参数组</strong>
					<span>复制当前音色默认参数后再命名</span>
				</button>
			</div>
			{#if selectedRecipe}
				<div class="recipe-editor">
					<div class="editor-grid">
						<label class="field compact-field">
							<span>参数组名称</span>
							<input value={recipeName} oninput={(event) => (recipeName = event.currentTarget.value)} />
						</label>
						<label class="field compact-field">
							<span>标签</span>
							<input value={recipeTags} placeholder="室内, 开心" oninput={(event) => (recipeTags = event.currentTarget.value)} />
						</label>
					</div>
					<label class="field compact-field">
						<span>描述</span>
						<input value={recipeDescription} placeholder="例如：室外偏兴奋，语速略快" oninput={(event) => (recipeDescription = event.currentTarget.value)} />
					</label>
					<details class="advanced-recipe">
						<summary>高级参数 JSON</summary>
						<label class="field compact-field">
							<span>参数快照</span>
							<textarea rows="4" value={recipeSnapshotText} oninput={(event) => (recipeSnapshotText = event.currentTarget.value)}></textarea>
						</label>
					</details>
					<div class="reference-actions">
						<button type="button" data-tooltip="保存参数组：更新名称、标签、描述和参数快照。" onclick={saveSelectedRecipe}>保存参数组</button>
						<button class="danger-btn" type="button" data-tooltip="删除参数组：移除当前音色下的这组生成参数。" onclick={() => onDeleteVoiceRecipe(selectedRecipe.recipe_id)}>删除参数组</button>
					</div>
				</div>
			{/if}
			<div class="voice-lab-actions">
				<button type="button" data-tooltip="一键生成：使用当前音色、参数组和字幕台词提交配音。" onclick={onQuickGenerateVoice} disabled={!canGenerateVoice || generatingVoice}>{generatingVoice ? '提交中' : '一键生成'}</button>
				<button type="button" data-tooltip="重新调参：打开语音生成页并带入当前样音、台词和参数。" onclick={onTuneVoiceInGenerate} disabled={!canGenerateVoice}>重新调参</button>
				<button type="button" data-tooltip="仅带样音：打开语音生成页，只复用当前样音并使用默认参数。" onclick={onSendReferenceOnlyToGenerate} disabled={!canGenerateVoice}>仅带样音</button>
			</div>
			<div class="recipe-summary">
				<strong>已生成版本</strong>
				<span>{selectedVoiceCandidates.length} 个候选；一键生成会使用当前参数组，只替换当前台词。</span>
			</div>
			{#if selectedVoiceCandidates.length}
				<div class="candidate-list">
				{#each selectedVoiceCandidates.slice(0, 3) as candidate}
					<div class="candidate-row" class:current={isAppliedCandidate(candidate)}>
						<div>
							<strong>{isAppliedCandidate(candidate) ? '当前版本' : candidate.status}</strong>
							<span>{candidate.text_used || candidate.task_id || candidate.candidate_id}</span>
						</div>
						{#if candidateAudioUrl(projectId, candidate)}
							<audio controls preload="metadata" src={candidateAudioUrl(projectId, candidate)}></audio>
						{/if}
						<button type="button" data-tooltip="采用候选：把这个声音设为当前字幕版本并替换合成配音轨片段。" disabled={isAppliedCandidate(candidate) || candidate.status !== 'success' || candidateApplyingId === candidate.candidate_id} onclick={() => onApplyGeneratedCandidate(candidate.candidate_id)}>
							{candidateApplyingId === candidate.candidate_id ? '应用中' : isAppliedCandidate(candidate) ? '已采用' : '采用'}
						</button>
					</div>
					{/each}
				</div>
			{/if}
		</div>
	</section>
	{/if}

	{#if activeSection === 'subtitle'}
	<section class="inspector-panel" class:runtime-locked={selectedLocalizedSubtitle ? localizationRuntimeBusy : subtitleRuntimeBusy} aria-busy={selectedLocalizedSubtitle ? localizationRuntimeBusy : subtitleRuntimeBusy}>
		<div class="panel-head">
			<h2>{selectedLocalizedSubtitle ? `本土化字幕：${selectedLocalizedSubtitle.subtitle_id}` : `ASR 字幕${selectedCue ? `：${selectedCue.cue_id}` : ''}`}</h2>
			<span>{selectedLocalizedSubtitle ? '初稿' : selectedCue ? subtitleStatusLabel(selectedCue.review_status) : '未选择'}</span>
		</div>
		{#if selectedLocalizedSubtitle}
			<div class="cue-meta">
				<span>{msLabel(selectedLocalizedSubtitle.end_ms - selectedLocalizedSubtitle.start_ms)}</span>
				<span>来源 {selectedLocalizedSubtitle.source_cue_ids?.length || (selectedLocalizedSubtitle.linked_cue_id ? 1 : 0)} 条原文</span>
			</div>
			<div class="editor-grid time-grid">
				<label class="field">
					<span>入点 ms</span>
					<input type="number" min="0" step="100" value={selectedLocalizedSubtitle.start_ms} disabled={localizationRuntimeBusy} onchange={(event) => onUpdateLocalizedSubtitle?.({ start_ms: Number(event.currentTarget.value) })} />
				</label>
				<label class="field">
					<span>出点 ms</span>
					<input type="number" min="0" step="100" value={selectedLocalizedSubtitle.end_ms} disabled={localizationRuntimeBusy} onchange={(event) => onUpdateLocalizedSubtitle?.({ end_ms: Number(event.currentTarget.value) })} />
				</label>
			</div>
			<label class="field">
				<span>上屏字幕</span>
				<textarea class="subtitle-textarea" rows="1" value={selectedLocalizedSubtitle.text} disabled={localizationRuntimeBusy} onchange={(event) => onUpdateLocalizedSubtitle?.({ text: event.currentTarget.value })}></textarea>
			</label>
			<label class="field">
				<span>配音台词</span>
				<textarea class="subtitle-textarea" rows="1" value={selectedLocalizedSubtitle.tts_text ?? selectedLocalizedSubtitle.text} disabled={localizationRuntimeBusy} onchange={(event) => onUpdateLocalizedSubtitle?.({ tts_text: event.currentTarget.value })}></textarea>
			</label>
			{#if selectedLocalizedSubtitle.adaptation_note}
				<div class="audit-row accepted">
					<span>本土化处理</span>
					<p>{selectedLocalizedSubtitle.adaptation_note}</p>
				</div>
			{/if}
			<div class="cue-actions">
				<button class="danger-btn" type="button" data-tooltip="删除片段：只移除当前本土化字幕片段。" onclick={() => onDeleteLocalizedSubtitle?.(selectedLocalizedSubtitle.subtitle_id)} disabled={localizationRuntimeBusy}>删除片段</button>
			</div>
		{:else if selectedCue}
			<div class="cue-meta">
				<span>{timeLabel(selectedCue)}</span>
				<div class="timing-meta">
					<span class:timing-low={selectedCue.timing_confidence === 'low'}>时间置信度：{selectedCue.timing_confidence ?? '未评估'}</span>
					{#if selectedCue.quality_flags.includes('manual_timing_verified')}
						<span class="timing-verified"><CheckCircle2 size={12} /> 已试听确认</span>
					{:else if selectedCue.timing_confidence === 'low' || selectedCue.quality_flags.includes('timing_review_required')}
						<button class="confirm-timing" type="button" data-tooltip="确认时间码：试听当前片段并确认出入点准确后，解除低置信时间阻断。" onclick={onConfirmCueTiming} disabled={confirmingCueTiming || subtitleRuntimeBusy}>
							<CheckCircle2 size={12} /> {confirmingCueTiming ? '确认中' : '确认时间码'}
						</button>
					{/if}
				</div>
			</div>
			{#if selectedCue.source_text_raw || selectedReviewSegments.length}
				<details class="asr-audit" open>
					<summary>识别与校对依据</summary>
					<div class="audit-row">
						<span>原始 ASR</span>
						<p>{selectedCue.source_text_raw || selectedReviewSegments.map((segment) => segment.raw_text).join(' ')}</p>
					</div>
					<div class="audit-row accepted">
						<span>当前采用</span>
						<p>{selectedCue.en_subtitle_text || '尚无文本'}</p>
					</div>
					{#each selectedReviewSegments.filter((segment) => segment.review_rejection_reason && segment.review_candidate_text) as segment}
						<div class="audit-row rejected">
							<span>已拒绝候选 · {reviewReasonLabel(segment.review_rejection_reason)}</span>
							<p>{segment.review_candidate_text}</p>
						</div>
					{/each}
				</details>
			{/if}
			<div class="editor-grid time-grid">
				<label class="field">
					<span>入点 ms</span>
					<input type="number" min="0" step="100" value={selectedCue.start_ms ?? ''} disabled={subtitleRuntimeBusy} oninput={(event) => onUpdateCue({ start_ms: event.currentTarget.value ? Number(event.currentTarget.value) : null })} />
				</label>
				<label class="field">
					<span>出点 ms</span>
					<input type="number" min="0" step="100" value={selectedCue.end_ms ?? ''} disabled={subtitleRuntimeBusy} oninput={(event) => onUpdateCue({ end_ms: event.currentTarget.value ? Number(event.currentTarget.value) : null })} />
				</label>
			</div>
			<label class="field">
				<span>原文/ASR</span>
			<textarea class="subtitle-textarea" rows="1" value={selectedCue.en_subtitle_text ?? ''} disabled={subtitleRuntimeBusy} oninput={(event) => onUpdateCue({ en_subtitle_text: event.currentTarget.value })}></textarea>
			</label>
			<label class="field">
				<span>本土化字幕</span>
			<textarea class="subtitle-textarea" rows="1" value={selectedCue.zh_localized_subtitle_text ?? ''} disabled={subtitleRuntimeBusy} oninput={(event) => onUpdateCue({ zh_localized_subtitle_text: event.currentTarget.value })}></textarea>
			</label>
			<label class="field">
				<span>字幕状态</span>
			<select value={selectedCue.review_status} disabled={subtitleRuntimeBusy} onchange={(event) => onUpdateCue({ review_status: event.currentTarget.value as VideoLocalizationCue['review_status'] })}>
					<option value="needs_review">待校对</option>
					<option value="ready">已校对</option>
					<option value="blocked">阻断</option>
					<option value="locked">已锁定</option>
				</select>
			</label>
			<div class="cue-actions">
				<button class="save-btn" type="button" data-tooltip="保存字幕：保存当前片段的时间码、字幕文本和校对状态。" onclick={onSaveCue} disabled={savingCue || subtitleRuntimeBusy}>{savingCue ? '保存中' : subtitleRuntimeBusy ? '任务处理中' : '保存字幕'}</button>
				<button class="danger-btn" type="button" data-tooltip="删除片段：从字幕轨移除当前字幕片段。" onclick={onDeleteCue} disabled={subtitleRuntimeBusy}>删除片段</button>
			</div>
		{:else}
			<p class="empty-text">点击时间线上的字幕片段后，这里会同步显示原文/ASR 与本土化字幕。</p>
		{/if}
	</section>
	{/if}

	{#if activeSection === 'style'}
	<section class="inspector-panel">
		<div class="panel-head">
			<h2>字幕显示</h2>
			<span>{subtitlePreview.enabled ? SUBTITLE_SOURCE_LABELS[subtitlePreview.source] : '已隐藏'}</span>
		</div>
		<div class="subtitle-controls">
			<div class="segmented">
				<button class:active={subtitlePreview.enabled} type="button" data-tooltip="切换字幕：显示或隐藏视频预览中的所有字幕层。" onclick={() => onUpdateSubtitlePreview({ enabled: !subtitlePreview.enabled })}>
					{subtitlePreview.enabled ? '显示' : '隐藏'}
				</button>
				<button class:active={subtitlePreview.position === 'bottom'} type="button" data-tooltip="底部：将字幕预览放在视频安全区底部。" onclick={() => onUpdateSubtitlePreview({ position: 'bottom' })}>底部</button>
				<button class:active={subtitlePreview.position === 'middle'} type="button" data-tooltip="中部：将字幕预览移动到视频画面中部。" onclick={() => onUpdateSubtitlePreview({ position: 'middle' })}>中部</button>
			</div>
			<label class="field">
				<span>字幕来源</span>
				<select value={subtitlePreview.source} onchange={(event) => onUpdateSubtitlePreview({ source: event.currentTarget.value as SubtitlePreviewState['source'] })}>
					{#each Object.entries(SUBTITLE_SOURCE_LABELS) as [value, label]}
						<option value={value}>{label}</option>
					{/each}
				</select>
			</label>
			<label class="field range-field">
				<span>字号 {subtitlePreview.fontSize}px</span>
				<input type="range" min="12" max="32" step="1" value={subtitlePreview.fontSize} oninput={(event) => onUpdateSubtitlePreview({ fontSize: Number(event.currentTarget.value) })} />
			</label>
			<label class="field range-field">
				<span>背景透明度 {(subtitlePreview.backgroundOpacity * 100).toFixed(0)}%</span>
				<input type="range" min="0" max="0.8" step="0.05" value={subtitlePreview.backgroundOpacity} oninput={(event) => onUpdateSubtitlePreview({ backgroundOpacity: Number(event.currentTarget.value) })} />
			</label>
		</div>
		<div class="subtitle-style-grid">
			{#each stylePresets as preset}
				<button
					class="style-card"
					class:active={subtitlePreview.stylePreset === preset}
					class:yellow={preset === 'yellow-outline'}
					class:boxed={preset === 'boxed'}
					class:clean={preset === 'clean-shadow'}
					class:outline={preset === 'strong-outline'}
					type="button"
					data-tooltip={`字幕样式：应用“${SUBTITLE_STYLE_LABELS[preset]}”预设到视频预览。`}
					onclick={() => onUpdateSubtitlePreview({ stylePreset: preset })}
				>
					<span>{SUBTITLE_STYLE_LABELS[preset]}</span>
				</button>
			{/each}
		</div>
	</section>
	{/if}
</aside>

<style>
	.inspector {
		display: grid;
		align-content: start;
		gap: 10px;
		min-width: 0;
		padding: 12px;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.018), transparent 180px),
			#161a1f;
		max-height: calc(100dvh - 108px);
		overflow: auto;
	}

	.inspector.tasks-view {
		height: calc(100dvh - 108px);
		min-height: 0;
		grid-template-rows: auto minmax(0, 1fr);
		align-content: stretch;
		overflow: hidden;
	}

	.task-view-content {
		min-width: 0;
		min-height: 0;
		overflow: hidden;
	}

	.inspector-tabs {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 5px;
		padding: 5px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #11161b;
	}

	.inspector-mode-tabs {
		position: sticky;
		top: 0;
		z-index: 3;
		display: grid;
		grid-template-columns: repeat(5, 1fr);
		gap: 4px;
		padding: 5px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #11161b;
		box-shadow: 0 8px 18px rgba(0, 0, 0, 0.18);
	}

	.inspector-tabs button,
	.inspector-mode-tabs button {
		border: 1px solid transparent;
		border-radius: 6px;
		min-height: 26px;
		background: transparent;
		color: var(--muted);
		font-size: 11px;
		cursor: pointer;
	}

	.inspector-mode-tabs button {
		min-height: 25px;
		font-weight: 760;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 5px;
	}

	.inspector-tabs button.active,
	.inspector-mode-tabs button.active {
		background: #273038;
		border-color: var(--line);
		color: var(--text);
	}

	.inspector-panel {
		border: 1px solid var(--line);
		border-radius: 7px;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.025), transparent),
			#1c2126;
		overflow: hidden;
	}

	.inspector-panel.runtime-locked {
		border-color: rgba(82, 149, 169, 0.42);
		box-shadow: inset 0 0 0 1px rgba(82, 149, 169, 0.08);
	}

	.panel-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 10px 11px;
		border-bottom: 1px solid #303941;
	}

	.panel-head h2 {
		margin: 0;
		font-size: 13px;
	}

	.panel-head span,
	.empty-text,
	.cue-meta,
	.field span,
	.voice-card span,
	.voice-card small,
	.detail-row span {
		color: var(--muted);
		font-size: 11px;
	}

	.voice-list {
		display: grid;
	}

	.voice-card {
		display: grid;
		grid-template-columns: 52px minmax(0, 1fr);
		gap: 9px;
		padding: 9px 11px;
		border: 0;
		border-bottom: 1px solid #303941;
		background: transparent;
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}

	.voice-card.active {
		background:
			linear-gradient(90deg, rgba(87, 208, 200, 0.16), transparent 70%),
			#172927;
	}

	.voice-cover,
	.cover-placeholder {
		border: 1px solid var(--line);
		border-radius: 7px;
		background:
			radial-gradient(circle at 50% 32%, #cfa47a 0 17%, transparent 18%),
			linear-gradient(180deg, #42515f, #151a1f);
	}

	.voice-cover {
		height: 44px;
		overflow: hidden;
	}

	.voice-cover img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}

	.voice-card strong,
	.voice-card span,
	.voice-card small {
		display: block;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.voice-card span,
	.voice-card small {
		margin-top: 3px;
	}

	.voice-detail,
	.save-selection,
	.cue-meta,
	.field,
	.subtitle-style-grid {
		padding: 9px 11px;
	}

	.voice-detail {
		display: grid;
		gap: 8px;
		border-top: 1px solid #303941;
	}

	.voice-detail audio {
		width: 100%;
		height: 34px;
		filter: invert(0.88) hue-rotate(160deg) saturate(0.8);
	}

	.detail-row {
		display: grid;
		grid-template-columns: 58px minmax(0, 1fr);
		gap: 8px;
	}

	.detail-row strong {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 12px;
	}

	.save-selection {
		display: grid;
		gap: 8px;
	}

	.cover-placeholder {
		height: 68px;
		display: grid;
		align-items: end;
		padding: 8px;
		color: #f5f3eb;
		font-size: 12px;
		font-weight: 800;
	}

	.sample-form {
		display: grid;
		gap: 6px;
	}

	.compact-field {
		padding: 0;
	}

	.selection-rows {
		display: grid;
		gap: 6px;
	}

	.selection-rows div {
		display: grid;
		grid-template-columns: 54px minmax(0, 1fr);
		gap: 8px;
		align-items: center;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #15191d;
		padding: 7px 8px;
	}

	.selection-rows span,
	.selection-rows strong {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 11px;
	}

	.selection-rows span {
		color: var(--muted);
	}

	.selection-rows strong {
		font-size: 12px;
	}

	.save-selection p {
		margin: 0;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.45;
	}

	.save-selection button,
	.save-btn,
	.voice-lab-actions button {
		border: 1px solid var(--line);
		border-radius: 7px;
		min-height: 27px;
		background: #143b39;
		color: #d8fffb;
		padding: 3px 7px;
		font-size: 11px;
		font-weight: 800;
		cursor: pointer;
	}

	.save-selection button:disabled,
	.save-btn:disabled,
	.voice-lab-actions button:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.cue-meta {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		border-bottom: 1px solid #303941;
	}

	.cue-meta .timing-low {
		color: #efa3a9;
	}

	.timing-meta,
	.timing-verified,
	.confirm-timing {
		display: inline-flex;
		align-items: center;
		gap: 5px;
	}

	.timing-meta {
		justify-content: flex-end;
		flex-wrap: wrap;
	}

	.timing-verified {
		color: #79d7bd;
	}

	.confirm-timing {
		min-height: 24px;
		padding: 2px 7px;
		border: 1px solid #45665e;
		border-radius: 6px;
		background: #17332f;
		color: #bceee4;
		font-size: 11px;
		cursor: pointer;
	}

	.confirm-timing:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.asr-audit {
		margin: 8px 11px 2px;
		border: 1px solid #303941;
		border-radius: 7px;
		background: #14191e;
		overflow: hidden;
	}

	.asr-audit summary {
		padding: 7px 8px;
		color: #b8c3c8;
		font-size: 11px;
		font-weight: 760;
		cursor: pointer;
	}

	.audit-row {
		display: grid;
		gap: 3px;
		padding: 7px 8px;
		border-top: 1px solid rgba(255, 255, 255, 0.06);
	}

	.audit-row span {
		color: #87949b;
		font-size: 10px;
	}

	.audit-row p {
		margin: 0;
		color: #d8e0e3;
		font-size: 11px;
		line-height: 1.45;
	}

	.audit-row.accepted {
		border-left: 2px solid rgba(73, 167, 132, 0.72);
	}

	.audit-row.rejected {
		border-left: 2px solid rgba(198, 82, 91, 0.72);
		background: rgba(101, 35, 42, 0.12);
	}

	.field {
		display: grid;
		gap: 5px;
	}

	.field textarea,
	.field select,
	.field input[type='number'],
	.field input:not([type]) {
		width: 100%;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #12171c;
		color: var(--text);
		padding: 8px;
		resize: vertical;
		font-size: 12px;
	}

	.field textarea.subtitle-textarea {
		min-height: 30px;
		height: 30px;
		resize: vertical;
		line-height: 18px;
	}

	.field input[type='range'] {
		width: 100%;
	}

	.editor-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 4px;
	}

	.time-grid {
		padding: 10px 11px 0;
	}

	.cue-actions {
		display: grid;
		grid-template-columns: 1fr auto;
		gap: 7px;
		padding: 0 11px 11px;
	}

	.voice-lab {
		display: grid;
		gap: 9px;
		padding: 10px 11px;
	}

	.dubbing-stage-note {
		display: grid;
		gap: 2px;
		padding: 8px 9px;
		border-left: 2px solid rgba(87, 208, 200, 0.72);
		background: rgba(87, 208, 200, 0.07);
	}

	.dubbing-stage-note strong {
		color: #d5fffb;
		font-size: 11px;
	}

	.dubbing-stage-note span {
		color: #95aaa9;
		font-size: 10px;
		line-height: 1.4;
	}

	.dubbing-fields {
		display: grid;
		gap: 7px;
		padding: 9px;
		border: 1px solid #303941;
		border-radius: 7px;
		background: #14191e;
	}

	.voice-route {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 7px;
	}

	.voice-route div,
	.recipe-card {
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #12171c;
		padding: 8px;
	}

	.voice-route span,
	.recipe-card span {
		display: block;
		color: var(--muted);
		font-size: 11px;
	}

	.voice-route strong,
	.recipe-card strong {
		display: block;
		margin-top: 3px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 12px;
	}

	.recipe-list {
		display: grid;
		gap: 7px;
	}

	.recipe-card {
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}

	.recipe-card.active {
		border-color: rgba(87, 208, 200, 0.62);
		background: #173a37;
	}

	.recipe-card.add-recipe {
		border-style: dashed;
		background: #151b20;
	}

	.recipe-card:disabled,
	.voice-lab-actions button:disabled {
		cursor: not-allowed;
	}

	.recipe-editor {
		display: grid;
		gap: 7px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #10151a;
		padding: 8px;
	}

	.recipe-editor textarea {
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-size: 11px;
		line-height: 1.4;
	}

	.voice-lab-actions {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 5px;
	}

	.advanced-recipe {
		border-top: 1px solid var(--line);
		padding-top: 6px;
	}

	.advanced-recipe summary {
		color: var(--muted);
		font-size: 11px;
		cursor: pointer;
	}

	.advanced-recipe[open] summary {
		margin-bottom: 6px;
		color: var(--text);
	}

	.candidate-list {
		display: grid;
		gap: 6px;
	}

	.recipe-summary {
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #12171c;
		padding: 8px;
	}

	.recipe-summary strong,
	.recipe-summary span {
		display: block;
		font-size: 11px;
	}

	.recipe-summary span {
		margin-top: 3px;
		color: var(--muted);
	}

	.candidate-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 96px auto;
		gap: 7px;
		align-items: center;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #12171c;
		padding: 7px 8px;
	}

	.candidate-row.current {
		border-color: rgba(87, 208, 200, 0.58);
		background: #132421;
	}

	.candidate-row > div {
		min-width: 0;
	}

	.candidate-row strong,
	.candidate-row span {
		display: block;
	}

	.candidate-row audio {
		width: 96px;
		height: 26px;
	}

	.candidate-row > button {
		min-height: 26px;
		padding: 3px 7px;
		white-space: nowrap;
	}

	.candidate-row strong,
	.candidate-row span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 11px;
	}

	.candidate-row span {
		color: var(--muted);
	}

	.save-btn,
	.danger-btn {
		min-height: 27px;
		border-radius: 6px;
		padding: 3px 7px;
		font-size: 11px;
		font-weight: 800;
		cursor: pointer;
	}

	.danger-btn {
		border: 1px solid #7b3b3b;
		background: #2b1717;
		color: #ffb4b4;
	}

	@media (max-width: 1380px) {
		.inspector {
			max-height: none;
			grid-template-columns: minmax(0, 0.95fr) minmax(320px, 0.75fr);
			align-items: start;
		}

		.inspector.tasks-view {
			height: min(720px, calc(100vh - 64px));
			max-height: none;
			grid-template-columns: 1fr;
			grid-template-rows: auto minmax(0, 1fr);
			align-items: stretch;
		}

		.inspector-mode-tabs,
		.inspector-tabs {
			grid-column: 1 / -1;
		}
	}

	@media (max-width: 900px) {
		.inspector {
			grid-template-columns: 1fr;
		}

		.inspector.tasks-view {
			height: min(640px, calc(100vh - 32px));
		}
	}

	.subtitle-controls {
		display: grid;
		gap: 8px;
		padding: 10px 11px 0;
	}

	.segmented {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 5px;
	}

	.segmented button {
		border: 1px solid var(--line);
		border-radius: 6px;
		min-height: 26px;
		background: #15191d;
		color: var(--muted);
		font-size: 11px;
		cursor: pointer;
	}

	.segmented button.active,
	.style-card.active {
		border-color: #78ddd5;
		background-color: #173a37;
		color: #d4fffb;
	}

	.subtitle-style-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 8px;
	}

	.style-card {
		min-height: 62px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #15191d;
		font-weight: 800;
		cursor: pointer;
	}

	.style-card.yellow {
		color: #fff1a8;
		text-shadow: 0 2px 2px #000, 0 0 4px #000;
	}

	.style-card.boxed span {
		background: rgba(0, 0, 0, 0.58);
		border-radius: 5px;
		padding: 5px 7px;
		color: #fff;
	}

	.style-card.clean {
		color: white;
		text-shadow: 0 0 3px #000;
	}

	.style-card.outline {
		color: white;
		text-shadow: 1px 1px #000, -1px -1px #000, 1px -1px #000, -1px 1px #000;
	}
</style>
