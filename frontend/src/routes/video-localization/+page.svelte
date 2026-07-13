<script lang="ts">
	import { Api } from '$lib/api';
	import { ApiError } from '$lib/api/client';
	import type {
		BatchTask,
		GenerateRequest,
		Project,
		VideoLocalizationCue,
		VideoLocalizationCueUpdate,
		VideoLocalizationDraft,
		VideoLocalizationGeneratedCandidate,
		VideoLocalizationOperation,
		VideoLocalizationReferenceClip,
		VideoLocalizationReferenceClipCreate,
		VideoLocalizationReferenceClipUpdate,
		VideoLocalizationTimelineClip,
		VideoLocalizationVoiceRecipe,
		VideoLocalizationSpeakerCreate
	} from '$lib/api/types';
	import {
		AlertTriangle,
		AudioLines,
		Captions,
		Check,
		ChevronDown,
		Clapperboard,
		FolderOpen,
		Palette,
		PanelRightClose,
		PanelRightOpen,
		Pencil,
		Trash2,
		WandSparkles,
		X
	} from 'lucide-svelte';
	import { onMount } from 'svelte';
	import { downloadBlob, downloadJson, downloadText } from './downloads';
	import {
		batchProjectId,
		buildGenerateRequest,
		buildWorkflow,
		createManualCue,
		isActiveOperation,
		operationStatusLabel,
		sourceAudioUrl,
		stemAudioUrl,
		sortOperations,
		suggestSpeakerSeed,
		type WorkflowStep
	} from './utils';
	import CuttingInspector from './CuttingInspector.svelte';
	import LocalizationTextImport from './LocalizationTextImport.svelte';
	import PreviewPanel from './PreviewPanel.svelte';
	import VideoCuttingTimeline from './VideoCuttingTimeline.svelte';
	import {
		resolveSubtitlePreviewState,
		resolveTrackStates,
		type SubtitlePreviewSource,
		type SubtitlePreviewState,
		type VideoLocalizationTrackId,
		type VideoLocalizationTrackState
	} from './studio-state';

	type AsrEngineId = 'faster-whisper-turbo' | 'qwen3-asr-mlx' | 'mimo-v2.5-asr';
	type AsrEngineHealth = {
		healthy: boolean;
		status: string;
		detail: string;
	};

	const ASR_ENGINE_PRIORITY: AsrEngineId[] = ['faster-whisper-turbo', 'qwen3-asr-mlx', 'mimo-v2.5-asr'];

	let projects = $state<Project[]>([]);
	let batches = $state<BatchTask[]>([]);
	let operations = $state<VideoLocalizationOperation[]>([]);
	let projectId = $state('');
	let draft = $state<VideoLocalizationDraft | null>(null);
	let draftOnlyCueIds = $state<string[]>([]);
	let selectedCueId = $state('');
	let loading = $state(true);
	let resetting = $state(false);
	let savingCue = $state(false);
	let creatingSpeaker = $state(false);
	let importing = $state(false);
	let openingProjectDirectory = $state(false);
	let editingProjectName = $state(false);
	let projectNameDraft = $state('');
	let projectNameSaving = $state(false);
	let projectMenuOpen = $state(false);
	let projectMenuSyncing = $state(false);
	let extractingAudio = $state(false);
	let separatingStems = $state(false);
	let transcribingAsr = $state(false);
	let localizingDraft = $state(false);
	let selectedAsrEngineId = $state<AsrEngineId>('faster-whisper-turbo');
	let selectedAsrEngineTouched = $state(false);
	let asrEngineHealth = $state<Record<AsrEngineId, AsrEngineHealth | null>>({
		'faster-whisper-turbo': null,
		'qwen3-asr-mlx': null,
		'mimo-v2.5-asr': null
	});
	let creatingReferences = $state(false);
	let submittingBatch = $state(false);
	let syncingBatch = $state(false);
	let exportingAudioPackage = $state(false);
	let exportingLocalizedVideo = $state(false);
	let loadingBatches = $state(false);
	let referenceUpdatingId = $state('');
	let candidateApplyingId = $state('');
	let operationActionId = $state('');
	let ttsBatchId = $state('');
	let localizationImportOpen = $state(false);
	let viewMode = $state<'single' | 'batch'>('single');
	let rightPanelMode = $state<'speakers' | 'references' | 'delivery'>('speakers');
	let inspectorCollapsed = $state(false);
	let inspectorSection = $state<'voice' | 'generate' | 'subtitle' | 'style'>('subtitle');
	let inspectorVoiceTab = $state<'library' | 'save-selection'>('library');
	let selectedVoiceId = $state('');
	let selectedRecipeId = $state('');
	let previewTimeMs = $state(0);
	let previewPlaying = $state(false);
	let audioSelectionRange = $state<{ start_ms: number; end_ms: number } | null>(null);
	let previewPlaybackController: { playPause: () => void; seek: (timeMs: number) => void } | null = null;
	let autoSaveStatus = $state<'idle' | 'dirty' | 'saving' | 'saved' | 'failed'>('idle');
	let autoSaveScope = $state<'ui' | 'draft' | null>(null);
	let pendingUiStatePatch = $state<Record<string, unknown>>({});
	let lastAutoSavedAt = $state('');
	type TimelineSnapshot = { clips: VideoLocalizationTimelineClip[]; disabledMediaTracks: string[] };
	let timelineUndoStack = $state<TimelineSnapshot[]>([]);
	let timelineRedoStack = $state<TimelineSnapshot[]>([]);
	let videoInput: HTMLInputElement | null = null;
	let operationPollingTimer: ReturnType<typeof setInterval> | null = null;
	let autoSaveTimer: ReturnType<typeof setTimeout> | null = null;
	let message = $state('');
	let error = $state('');

	const workflow = $derived<WorkflowStep[]>(buildWorkflow(draft));
	const selectedProject = $derived(projects.find((project) => project.project_id === projectId) ?? null);
	const hasImportedProject = $derived(Boolean(draft?.source_media.video_path || draft?.source_media.filename));
	const selectedCue = $derived(draft?.cues.find((cue) => cue.cue_id === selectedCueId) ?? draft?.cues[0] ?? null);
	const previewCue = $derived(
		draft?.cues.find((cue) => cue.start_ms !== null && cue.end_ms !== null && previewTimeMs >= cue.start_ms && previewTimeMs <= cue.end_ms) ?? selectedCue
	);
	const readyCount = $derived(draft?.cues.filter((cue) => cue.review_status === 'ready' || cue.review_status === 'locked').length ?? 0);
	const reviewCount = $derived(draft?.cues.filter((cue) => cue.review_status === 'needs_review').length ?? 0);
	const blockedCount = $derived(draft?.cues.filter((cue) => cue.review_status === 'blocked').length ?? 0);
	const generatedCount = $derived(draft?.cues.filter((cue) => cue.tts_audio_path).length ?? 0);
	const localizedCount = $derived(
		draft?.cues.filter((cue) => cue.zh_localized_subtitle_text?.trim() || cue.tts_recommended_text?.trim()).length ?? 0
	);
	const projectBatches = $derived(batches.filter((batch) => batchProjectId(batch) === projectId));
	const hasActiveOperation = $derived(operations.some((operation) => isActiveOperation(operation)));
	const latestOperation = $derived(operations[0] ?? null);
	const speakerSeed = $derived(suggestSpeakerSeed(draft?.speakers ?? []));
	const cueTimelineAudioSrc = $derived(stemAudioUrl(projectId, draft, 'vocals') || sourceAudioUrl(projectId, draft));
	const cueTimelineAudioLabel = $derived(draft?.stems.vocals_clean_path ? '分离后人声' : '源音轨');
	const cueTimelineDurationMs = $derived(draft?.source_media.duration_ms ?? null);
	const subtitlePreview = $derived(resolveSubtitlePreviewState(draft?.ui_state?.subtitle_preview));
	const trackStates = $derived(resolveTrackStates(draft?.ui_state?.track_states));
	const timelineZoom = $derived(clampNumber(draft?.ui_state?.timeline_zoom, 1, 1200, 1));
	const canSubmitCount = $derived(
		draft?.cues.filter((cue) => cue.review_status === 'ready' && cue.audio_route === 'clone_from_source' && cue.tts_recommended_text?.trim() && referenceReady(cue.reference_clip_id)).length ?? 0
	);
	const hasResettableDraft = $derived(Boolean(projectId && draft && hasResettableContent(draft)));
	const saveStatusLabel = $derived(
		autoSaveStatus === 'saving'
			? '保存中'
			: autoSaveStatus === 'dirty'
				? '有未保存修改'
				: autoSaveStatus === 'failed'
					? '保存失败'
					: autoSaveStatus === 'saved'
						? `已保存${lastAutoSavedAt ? ` ${lastAutoSavedAt}` : ''}`
						: draft?.updated_at
							? '草稿已保存'
							: '等待保存'
	);

	onMount(() => {
		void loadAsrEngineHealth();
		loadProjects();
		return () => {
			stopOperationPolling();
			if (autoSaveTimer) clearTimeout(autoSaveTimer);
		};
	});

	function normalizeAsrHealth(payload: Record<string, unknown>): AsrEngineHealth {
		const healthy = payload.healthy === true;
		const status = String(payload.status ?? (healthy ? 'ready' : 'unknown'));
		const detail =
			String(payload.detail ?? '').trim() ||
			(Array.isArray(payload.missing) ? payload.missing.map((item) => String(item)).join(', ') : '') ||
			(healthy ? '可用' : '当前不可用');
		return { healthy, status, detail };
	}

	function recommendedAsrEngine(healthMap: Record<AsrEngineId, AsrEngineHealth | null>): AsrEngineId {
		return ASR_ENGINE_PRIORITY.find((engineId) => healthMap[engineId]?.healthy) ?? 'mimo-v2.5-asr';
	}

	async function loadAsrEngineHealth() {
		try {
			const entries = await Promise.all(
				ASR_ENGINE_PRIORITY.map(async (engineId) => {
					const result = await Api.healthEngine(engineId);
					return [engineId, normalizeAsrHealth(result)] as const;
				})
			);
			asrEngineHealth = Object.fromEntries(entries) as Record<AsrEngineId, AsrEngineHealth>;
			if (!selectedAsrEngineTouched) {
				selectedAsrEngineId = recommendedAsrEngine(asrEngineHealth);
			}
		} catch {
			// 健康检查失败时保留当前选项，不阻断页面使用。
		}
	}

	async function loadProjects() {
		loading = true;
		error = '';
		try {
			projects = await Api.syncVideoLocalizationProjects();
			const urlProjectId = new URLSearchParams(window.location.search).get('project_id');
			const fallbackProject = projects.find(projectHasVideoLocalizationSource) ?? projects[0];
			projectId = (urlProjectId && projects.some((project) => project.project_id === urlProjectId) ? urlProjectId : fallbackProject?.project_id) ?? '';
			if (projectId) await loadDraft(projectId);
			else if (urlProjectId) clearProjectIdFromUrl();
		} catch (e) {
			error = (e as Error).message || '加载项目失败';
		} finally {
			loading = false;
		}
	}

	function projectHasVideoLocalizationSource(project: Project) {
		const draftLike = project.parameters?.video_localization as { source_media?: { filename?: unknown; video_path?: unknown } } | undefined;
		return Boolean(draftLike?.source_media?.video_path || draftLike?.source_media?.filename);
	}

	function isInspectorSection(value: string): value is 'voice' | 'generate' | 'subtitle' | 'style' {
		return value === 'voice' || value === 'generate' || value === 'subtitle' || value === 'style';
	}

	function isInspectorVoiceTab(value: string): value is 'library' | 'save-selection' {
		return value === 'library' || value === 'save-selection';
	}

	async function loadDraft(nextProjectId = projectId) {
		if (!nextProjectId) {
			draft = null;
			draftOnlyCueIds = [];
			return;
		}
		error = '';
		try {
			const loadedDraft = await Api.videoLocalizationDraft(nextProjectId);
			const editableDraft = withEditableMediaClips(loadedDraft);
			const addedMediaClips = editableDraft.timeline_clips.length > loadedDraft.timeline_clips.length;
			draft = editableDraft;
			if (addedMediaClips) scheduleDraftAutosave();
			const lastEngineId = draft.source_media.metadata?.english_asr_engine_id;
			if (typeof lastEngineId === 'string' && ASR_ENGINE_PRIORITY.includes(lastEngineId as AsrEngineId)) {
				selectedAsrEngineId = lastEngineId as AsrEngineId;
			} else if (!selectedAsrEngineTouched) {
				selectedAsrEngineId = recommendedAsrEngine(asrEngineHealth);
			}
			draftOnlyCueIds = [];
			operations = sortOperations(draft.operations ?? []);
			const savedCueId = typeof draft.ui_state?.selected_cue_id === 'string' ? draft.ui_state.selected_cue_id : '';
			const savedVoiceId = typeof draft.ui_state?.selected_reference_clip_id === 'string' ? draft.ui_state.selected_reference_clip_id : '';
			const savedRecipeId = typeof draft.ui_state?.selected_recipe_id === 'string' ? draft.ui_state.selected_recipe_id : '';
			const savedInspectorSection = typeof draft.ui_state?.inspector_section === 'string' ? draft.ui_state.inspector_section : '';
			const savedInspectorVoiceTab = typeof draft.ui_state?.inspector_voice_tab === 'string' ? draft.ui_state.inspector_voice_tab : '';
			selectedCueId = draft.cues.some((cue) => cue.cue_id === savedCueId) ? savedCueId : (draft.cues[0]?.cue_id ?? '');
			selectedVoiceId = draft.reference_clips.some((clip) => clip.reference_clip_id === savedVoiceId) ? savedVoiceId : (draft.reference_clips[0]?.reference_clip_id ?? '');
			selectedRecipeId = draft.voice_recipes.some((recipe) => recipe.recipe_id === savedRecipeId) ? savedRecipeId : (draft.voice_recipes.find((recipe) => recipe.reference_clip_id === selectedVoiceId)?.recipe_id ?? '');
			inspectorCollapsed = draft.ui_state?.sidebar_collapsed === true;
			inspectorSection = isInspectorSection(savedInspectorSection) ? savedInspectorSection : 'subtitle';
			inspectorVoiceTab = isInspectorVoiceTab(savedInspectorVoiceTab) ? savedInspectorVoiceTab : 'library';
			previewTimeMs = clampNumber(draft.ui_state?.playhead_ms, 0, Number.MAX_SAFE_INTEGER, 0);
			autoSaveStatus = draft.updated_at ? 'saved' : 'idle';
			await loadOperations(nextProjectId);
			await loadBatches();
		} catch (e) {
			error = (e as Error).message || '加载草稿失败';
		}
	}

	async function loadOperations(nextProjectId = projectId) {
		if (!nextProjectId) {
			operations = [];
			stopOperationPolling();
			return;
		}
		try {
			operations = sortOperations(await Api.videoLocalizationOperations(nextProjectId));
			if (operations.some((operation) => isActiveOperation(operation))) startOperationPolling();
			else stopOperationPolling();
		} catch {
			operations = [];
			stopOperationPolling();
		}
	}

	async function loadBatches() {
		loadingBatches = true;
		try {
			batches = await Api.batches();
		} catch {
			batches = [];
		} finally {
			loadingBatches = false;
		}
	}

	async function selectProject(nextProjectId: string) {
		if (!nextProjectId || nextProjectId === projectId) {
			projectMenuOpen = false;
			return;
		}
		if (!(await flushPendingAutosave())) return;
		clearProjectRuntimeState();
		projectId = nextProjectId;
		editingProjectName = false;
		projectMenuOpen = false;
		await loadDraft(projectId);
	}

	async function toggleProjectMenu(event: MouseEvent) {
		event.stopPropagation();
		if (projectMenuOpen) {
			projectMenuOpen = false;
			return;
		}
		projectMenuOpen = true;
		projectMenuSyncing = true;
		error = '';
		try {
			projects = await Api.syncVideoLocalizationProjects();
			if (projectId && !projects.some((project) => project.project_id === projectId)) {
				cancelPendingAutosave();
				clearProjectRuntimeState();
				projectId = projects[0]?.project_id ?? '';
				if (projectId) await loadDraft(projectId);
				else clearProjectIdFromUrl();
				message = '原项目目录已不存在，已从历史列表隐藏';
				setTimeout(() => (message = ''), 2200);
			}
		} catch (e) {
			error = (e as Error).message || '同步本地项目失败';
		} finally {
			projectMenuSyncing = false;
		}
	}

	async function flushPendingAutosave() {
		if (autoSaveTimer) {
			clearTimeout(autoSaveTimer);
			autoSaveTimer = null;
		}
		if (autoSaveStatus === 'dirty') await runDraftAutosave();
		return autoSaveStatus !== 'failed';
	}

	function cancelPendingAutosave() {
		if (autoSaveTimer) clearTimeout(autoSaveTimer);
		autoSaveTimer = null;
		autoSaveScope = null;
		pendingUiStatePatch = {};
		autoSaveStatus = 'idle';
	}

	function clearProjectRuntimeState() {
		if (previewPlaying) previewPlaybackController?.playPause();
		draft = null;
		draftOnlyCueIds = [];
		selectedCueId = '';
		selectedVoiceId = '';
		selectedRecipeId = '';
		operations = [];
		batches = [];
		previewTimeMs = 0;
		stopOperationPolling();
	}

	function clearProjectIdFromUrl() {
		const url = new URL(window.location.href);
		url.searchParams.delete('project_id');
		window.history.replaceState({}, '', url);
	}

	function closeProjectMenuFromPage(event: PointerEvent) {
		if (!(event.target as HTMLElement | null)?.closest('.project-switcher')) projectMenuOpen = false;
	}

	function handlePageKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') projectMenuOpen = false;
	}

	function defaultLocalizationProjectName(file: File) {
		const now = new Date();
		const stamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}`;
		const stem = file.name.replace(/\.[^.]+$/, '').trim() || '未命名视频';
		return `视频本土化_${stamp}_${stem}`;
	}

	function startProjectNameEdit() {
		if (!selectedProject) return;
		projectNameDraft = selectedProject.name;
		editingProjectName = true;
	}

	function cancelProjectNameEdit() {
		editingProjectName = false;
		projectNameDraft = '';
	}

	async function saveProjectNameEdit() {
		if (!projectId || !selectedProject) return;
		const nextName = projectNameDraft.trim();
		if (!nextName || nextName === selectedProject.name) {
			cancelProjectNameEdit();
			return;
		}
		projectNameSaving = true;
		error = '';
		try {
			const updated = await Api.updateProject(projectId, { name: nextName });
			projects = projects.map((project) => (project.project_id === updated.project_id ? updated : project));
			await loadDraft(projectId);
			editingProjectName = false;
			message = '项目名称和本地目录已更新';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '修改项目名称失败';
		} finally {
			projectNameSaving = false;
		}
	}

	function handleProjectNameKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			void saveProjectNameEdit();
		} else if (event.key === 'Escape') {
			event.preventDefault();
			cancelProjectNameEdit();
		}
	}

	async function resetCurrentTask() {
		if (!projectId || !draft || !hasResettableContent(draft)) return;
		const confirmed = window.confirm('这会清空当前项目的视频、本土化 cue、参考音、分离结果和当前页面状态，并回到初始空白态。项目本身会保留，是否继续？');
		if (!confirmed) return;
		resetting = true;
		error = '';
		try {
			draft = await Api.resetVideoLocalizationDraft(projectId);
			draftOnlyCueIds = [];
			selectedCueId = '';
			operations = [];
			ttsBatchId = '';
			localizationImportOpen = false;
			stopOperationPolling();
			message = '当前任务已清空，已回到初始状态';
			setTimeout(() => (message = ''), 2200);
		} catch (e) {
			error = (e as Error).message || '清空当前任务失败';
		} finally {
			resetting = false;
		}
	}

	async function importVideoFile(file: File | null | undefined) {
		if (!file) return;
		importing = true;
		error = '';
		try {
			const projectName = defaultLocalizationProjectName(file);
			const project = await Api.createProject(projectName, '外文视频中文配音草稿');
			projects = [...projects, project];
			projectId = project.project_id;
			const targetProjectId = project.project_id;
			draft = withEditableMediaClips(await Api.importVideoLocalizationSource(targetProjectId, file));
			if (!draft.source_media.audio_path && !draft.stems.original_audio_path) {
				const operation = await Api.submitVideoLocalizationOperation(targetProjectId, 'source_audio', {});
				operations = sortOperations([operation, ...operations.filter((item) => item.operation_id !== operation.operation_id)]);
				message = '视频已导入，原音轨抽取已开始';
				startOperationPolling();
			} else {
				message = '视频已导入';
			}
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导入视频失败';
		} finally {
			importing = false;
			if (videoInput) videoInput.value = '';
		}
	}

	async function extractSourceAudio() {
		if (!projectId || !draft?.source_media.video_path) return;
		extractingAudio = true;
		error = '';
		try {
			await submitMediaOperation('source_audio', '源音轨抽取任务已开始');
		} catch (e) {
			error = (e as Error).message || '提交源音轨抽取失败';
		} finally {
			extractingAudio = false;
		}
	}

	async function transcribeEnglishSource() {
		if (!projectId || !(draft?.source_media.audio_path || draft?.stems.original_audio_path)) return;
		transcribingAsr = true;
		error = '';
		try {
			const selectedHealth = asrEngineHealth[selectedAsrEngineId];
			if (selectedHealth?.healthy === false) {
				const fallbackEngineId = recommendedAsrEngine(asrEngineHealth);
				if (fallbackEngineId !== selectedAsrEngineId && asrEngineHealth[fallbackEngineId]?.healthy) {
					selectedAsrEngineId = fallbackEngineId;
					await submitMediaOperation('english_asr', `英文字幕转录任务已开始（已自动切换到 ${fallbackEngineId}）`, {
						engine_id: fallbackEngineId
					});
					return;
				}
				throw new Error(selectedHealth.detail || `${selectedAsrEngineId} 当前不可用`);
			}
			await submitMediaOperation('english_asr', `英文字幕转录任务已开始（${selectedAsrEngineId}）`, { engine_id: selectedAsrEngineId });
		} catch (e) {
			error = (e as Error).message || '提交英文 ASR 失败';
		} finally {
			transcribingAsr = false;
		}
	}

	async function separateStems() {
		if (!projectId || !(draft?.source_media.audio_path || draft?.stems.original_audio_path)) return;
		separatingStems = true;
		error = '';
		try {
			await submitMediaOperation('stems', '人声与背景声分离任务已开始');
		} catch (e) {
			error = (e as Error).message || '提交人声分离失败';
		} finally {
			separatingStems = false;
		}
	}

	async function localizeChineseDraft() {
		if (!projectId || !draft?.cues.some((cue) => cue.en_subtitle_text?.trim())) return;
		localizingDraft = true;
		error = '';
		try {
			draft = await Api.generateVideoLocalizationChineseDraft(projectId);
			if (!selectedCueId && draft.cues[0]) selectedCueId = draft.cues[0].cue_id;
			message = '已生成中文草稿，请继续人工校对';
			setTimeout(() => (message = ''), 2200);
		} catch (e) {
			error = (e as Error).message || '生成中文草稿失败';
		} finally {
			localizingDraft = false;
		}
	}

	async function submitMediaOperation(kind: VideoLocalizationOperation['kind'], successMessage: string, parameters: Record<string, unknown> = {}) {
		if (!projectId) return;
		const operation = await Api.submitVideoLocalizationOperation(projectId, kind, parameters);
		operations = sortOperations([operation, ...operations.filter((item) => item.operation_id !== operation.operation_id)]);
		await refreshDraftOnly();
		message = successMessage;
		setTimeout(() => (message = ''), 1800);
		startOperationPolling();
	}

	async function cancelOperation(operation: VideoLocalizationOperation) {
		if (!projectId || !isActiveOperation(operation)) return;
		operationActionId = operation.operation_id;
		error = '';
		try {
			const updated = await Api.cancelVideoLocalizationOperation(projectId, operation.operation_id);
			operations = sortOperations([updated, ...operations.filter((item) => item.operation_id !== updated.operation_id)]);
			await refreshDraftOnly();
			message = '任务已取消';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '取消任务失败';
		} finally {
			operationActionId = '';
		}
	}

	async function retryOperation(operation: VideoLocalizationOperation) {
		if (!projectId || isActiveOperation(operation)) return;
		operationActionId = operation.operation_id;
		error = '';
		try {
			const retry = await Api.retryVideoLocalizationOperation(projectId, operation.operation_id);
			operations = sortOperations([retry, ...operations]);
			await refreshDraftOnly();
			message = '任务已重新提交';
			setTimeout(() => (message = ''), 1800);
			startOperationPolling();
		} catch (e) {
			error = (e as Error).message || '重试任务失败';
		} finally {
			operationActionId = '';
		}
	}

	async function createReferenceCandidates() {
		if (!projectId || draft?.stems.separation_status !== 'completed') return;
		creatingReferences = true;
		error = '';
		try {
			await submitMediaOperation('reference_clips', '参考音候选生成任务已开始');
		} catch (e) {
			error = (e as Error).message || '提交参考音候选任务失败';
		} finally {
			creatingReferences = false;
		}
	}

	async function createReferenceFromSelection(payload: VideoLocalizationReferenceClipCreate) {
		if (!projectId || draft?.stems.separation_status !== 'completed' || !audioSelectionRange) return;
		creatingReferences = true;
		error = '';
		try {
			const selectionPayload = {
				...payload,
				cue_id: selectedCue?.cue_id ?? null,
				speaker_id: selectedCue?.speaker_id ?? null,
				asr_text: selectedCue?.en_subtitle_text ?? selectedCue?.zh_localized_subtitle_text ?? null,
				start_ms: audioSelectionRange.start_ms,
				end_ms: audioSelectionRange.end_ms
			};
			draft = await Api.createVideoLocalizationReferences(projectId, selectionPayload);
			const savedReferenceId = [...draft.reference_clips]
				.reverse()
				.find((clip) => clip.start_ms === audioSelectionRange?.start_ms && clip.end_ms === audioSelectionRange?.end_ms)?.reference_clip_id ?? '';
			if (savedReferenceId) selectedVoiceId = savedReferenceId;
			selectedRecipeId = draft.voice_recipes.find((recipe) => recipe.reference_clip_id === savedReferenceId)?.recipe_id ?? selectedRecipeId;
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			message = '当前选区已保存为项目音色';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '保存当前选区为音色失败';
		} finally {
			creatingReferences = false;
		}
	}

	async function updateReferenceClip(referenceClipId: string, patch: VideoLocalizationReferenceClipUpdate, successMessage: string) {
		if (!projectId) return;
		referenceUpdatingId = referenceClipId;
		error = '';
		try {
			draft = await Api.updateVideoLocalizationReference(projectId, referenceClipId, patch);
			message = successMessage;
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '更新参考音状态失败';
		} finally {
			referenceUpdatingId = '';
		}
	}

	async function deleteReferenceClip(referenceClipId: string) {
		if (!projectId || !referenceClipId) return;
		const confirmed = window.confirm('删除后会从项目音色库移除，并解绑已引用它的字幕片段。源音频文件会保留在项目目录中，是否继续？');
		if (!confirmed) return;
		referenceUpdatingId = referenceClipId;
		error = '';
		try {
			draft = await Api.deleteVideoLocalizationReference(projectId, referenceClipId);
			if (selectedVoiceId === referenceClipId) {
				selectedVoiceId = draft.reference_clips[0]?.reference_clip_id ?? '';
				selectedRecipeId = draft.voice_recipes.find((recipe) => recipe.reference_clip_id === selectedVoiceId)?.recipe_id ?? '';
			}
			message = '项目音色已删除，相关字幕已解绑';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '删除项目音色失败';
		} finally {
			referenceUpdatingId = '';
		}
	}

	async function applyGeneratedCandidate(candidateId: string) {
		if (!projectId || !candidateId) return;
		candidateApplyingId = candidateId;
		error = '';
		try {
			draft = await Api.applyVideoLocalizationCandidate(projectId, candidateId);
			message = '候选声音已设为当前版本并更新到时间线';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '应用候选声音失败';
		} finally {
			candidateApplyingId = '';
		}
	}

	function markReferenceClean(clip: VideoLocalizationReferenceClip) {
		updateReferenceClip(
			clip.reference_clip_id,
			{
				cleanliness: 'clean',
				asr_status: 'verified',
				asr_text: clip.asr_text ?? ''
			},
			'参考音已确认可用'
		);
	}

	function markReferenceBlocked(clip: VideoLocalizationReferenceClip) {
		updateReferenceClip(clip.reference_clip_id, { cleanliness: 'blocked', asr_status: 'failed' }, '参考音已标记阻断');
	}

	function markReferenceNeedsReview(clip: VideoLocalizationReferenceClip) {
		updateReferenceClip(clip.reference_clip_id, { cleanliness: 'needs_review', asr_status: clip.asr_text ? 'candidate' : 'pending' }, '参考音已退回复听');
	}

	function handleAsrEngineChange(engineId: AsrEngineId) {
		selectedAsrEngineTouched = true;
		selectedAsrEngineId = engineId;
	}

	async function openProjectDirectory() {
		if (!projectId) return;
		openingProjectDirectory = true;
		error = '';
		try {
			await Api.openVideoLocalizationProjectDirectory(projectId);
			message = '已打开项目文件目录';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '打开项目目录失败';
		} finally {
			openingProjectDirectory = false;
		}
	}

	async function exportTimelineEdl() {
		if (!projectId || !draft) return;
		error = '';
		try {
			if (autoSaveStatus === 'dirty') await runDraftAutosave();
			const data = await Api.exportVideoLocalizationTimeline(projectId);
			downloadJson(`${projectId}-video-localization-edl.json`, data);
			message = '时间轴 EDL 已导出';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导出 EDL 失败';
		}
	}

	async function exportTimelineAudioPackage() {
		if (!projectId || !draft) return;
		exportingAudioPackage = true;
		error = '';
		try {
			if (autoSaveStatus === 'dirty') await runDraftAutosave();
			const response = await fetch(`/api/projects/${projectId}/video-localization/export/timeline/audio-package`);
			if (!response.ok) {
				const data = await response.json().catch(() => null);
				throw new Error(data?.error?.message || '导出音频包失败');
			}
			const blob = await response.blob();
			downloadBlob(filenameFromDisposition(response.headers.get('content-disposition')) || `${projectId}-video-localization-audio-package.zip`, blob);
			await refreshDraftOnly();
			message = '时间线音频包已导出';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导出音频包失败';
		} finally {
			exportingAudioPackage = false;
		}
	}

	async function exportLocalizedVideo() {
		if (!projectId || !draft) return;
		exportingLocalizedVideo = true;
		error = '';
		try {
			if (autoSaveStatus === 'dirty') await runDraftAutosave();
			const response = await fetch(`/api/projects/${projectId}/video-localization/export/timeline/video`);
			if (!response.ok) {
				const data = await response.json().catch(() => null);
				throw new Error(data?.error?.message || '导出合成视频失败');
			}
			const blob = await response.blob();
			downloadBlob(filenameFromDisposition(response.headers.get('content-disposition')) || `${projectId}-video-localization-localized-video.mp4`, blob);
			await refreshDraftOnly();
			message = '合成视频已导出';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导出合成视频失败';
		} finally {
			exportingLocalizedVideo = false;
		}
	}

	async function exportReadinessAudit() {
		if (!projectId) return;
		error = '';
		try {
			const data = await Api.videoLocalizationReadiness(projectId);
			downloadJson(`${projectId}-video-localization-readiness.json`, data);
			message = 'Readiness JSON 已导出';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导出 readiness 失败';
		}
	}

	function filenameFromDisposition(value: string | null) {
		if (!value) return '';
		const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(value);
		if (utf8?.[1]) return decodeURIComponent(utf8[1]);
		const plain = /filename="?([^";]+)"?/i.exec(value);
		return plain?.[1] ?? '';
	}

	async function submitBatchTts() {
		if (!projectId || !canSubmitCount) return;
		submittingBatch = true;
		error = '';
		try {
			const task = await Api.submitVideoLocalizationBatchTts(projectId);
			ttsBatchId = task.batch_task_id;
			batches = [task, ...batches.filter((batch) => batch.batch_task_id !== task.batch_task_id)];
			message = `已提交批量 TTS：${task.batch_task_id}`;
			setTimeout(() => (message = ''), 2400);
		} catch (e) {
			error = (e as Error).message || '批量 TTS 提交失败';
		} finally {
			submittingBatch = false;
		}
	}

	async function syncBatchTtsResults() {
		if (!projectId || !ttsBatchId.trim()) return;
		syncingBatch = true;
		error = '';
		try {
			draft = await Api.syncVideoLocalizationBatchTts(projectId, ttsBatchId.trim());
			await loadBatches();
			message = 'TTS 生成结果已同步到 cue';
			setTimeout(() => (message = ''), 2200);
		} catch (e) {
			error = (e as Error).message || '同步 TTS 结果失败';
		} finally {
			syncingBatch = false;
		}
	}

	async function exportBilingualSrt() {
		if (!projectId) return;
		error = '';
		try {
			const response = await fetch(`/api/projects/${projectId}/video-localization/subtitles/bilingual`);
			if (!response.ok) {
				const data = await response.json().catch(() => null);
				throw new Error(data?.error?.message || '导出字幕失败');
			}
			const text = await response.text();
			downloadText(`${projectId}-video-localization-bilingual.srt`, text, 'application/x-subrip;charset=utf-8');
			message = '中英字幕草稿已导出';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导出字幕失败';
		}
	}

	function addCue() {
		if (!draft) return;
		const cue = createManualCue(draft);
		draft.cues = [...draft.cues, cue];
		draftOnlyCueIds = [...draftOnlyCueIds, cue.cue_id];
		selectedCueId = cue.cue_id;
		viewMode = 'single';
		focusInspector('subtitle');
	}

	function selectCue(cueId: string) {
		selectedCueId = cueId;
		updateDraftUiState({ selected_cue_id: cueId });
		focusInspector('subtitle');
	}

	function updateSelectedCue(patch: Partial<VideoLocalizationCue>) {
		if (!draft || !selectedCue) return;
		draft.cues = draft.cues.map((cue) => (cue.cue_id === selectedCue.cue_id ? normalizeCueTimePatch({ ...cue, ...patch }, patch) : cue));
		scheduleDraftAutosave();
	}

	function updateCueTimeFromTimeline(cueId: string, startMs: number, endMs: number) {
		if (!draft) return;
		const normalizedStart = Math.max(0, Math.round(startMs));
		const normalizedEnd = Math.max(normalizedStart + 300, Math.round(endMs));
		draft.cues = draft.cues.map((cue) =>
			cue.cue_id === cueId
				? {
						...cue,
						start_ms: normalizedStart,
						end_ms: normalizedEnd,
						source_duration_ms: normalizedEnd - normalizedStart
					}
				: cue
		);
		selectedCueId = cueId;
		updateDraftUiState({ selected_cue_id: cueId });
		focusInspector('subtitle');
	}

	function splitSelectedCue() {
		if (!draft || !selectedCue || selectedCue.start_ms === null || selectedCue.end_ms === null) return;
		const durationMs = selectedCue.end_ms - selectedCue.start_ms;
		if (durationMs < 700) {
			message = '当前字幕片段太短，无法拆分';
			setTimeout(() => (message = ''), 1600);
			return;
		}
		const splitAt = previewTimeMs > selectedCue.start_ms + 300 && previewTimeMs < selectedCue.end_ms - 300
			? previewTimeMs
			: selectedCue.start_ms + Math.round(durationMs / 2);
		const splitMs = Math.max(selectedCue.start_ms + 300, Math.min(selectedCue.end_ms - 300, Math.round(splitAt)));
		const [firstEn, secondEn] = splitCueText(selectedCue.en_subtitle_text ?? '');
		const [firstZh, secondZh] = splitCueText(selectedCue.zh_localized_subtitle_text ?? '');
		const [firstTts, secondTts] = splitCueText(selectedCue.tts_recommended_text ?? '');
		const nextCue: VideoLocalizationCue = {
			...selectedCue,
			cue_id: nextCueId(draft),
			start_ms: splitMs,
			end_ms: selectedCue.end_ms,
			en_subtitle_text: secondEn,
			zh_localized_subtitle_text: secondZh,
			tts_recommended_text: secondTts,
			source_duration_ms: selectedCue.end_ms - splitMs,
			tts_result_id: null,
			tts_audio_path: null,
			tts_batch_task_id: null,
			tts_batch_status: null,
			tts_batch_error: null,
			tts_attempted_at: null,
			generated_duration_ms: null,
			review_status: 'needs_review',
			quality_flags: [...new Set([...(selectedCue.quality_flags ?? []), 'timeline_split'])]
		};
		const currentCue: VideoLocalizationCue = {
			...selectedCue,
			end_ms: splitMs,
			en_subtitle_text: firstEn,
			zh_localized_subtitle_text: firstZh,
			tts_recommended_text: firstTts,
			source_duration_ms: splitMs - selectedCue.start_ms,
			tts_result_id: null,
			tts_audio_path: null,
			tts_batch_task_id: null,
			tts_batch_status: null,
			tts_batch_error: null,
			tts_attempted_at: null,
			generated_duration_ms: null,
			review_status: 'needs_review',
			quality_flags: [...new Set([...(selectedCue.quality_flags ?? []), 'timeline_split'])]
		};
		const cues = draft.cues.flatMap((cue) => (cue.cue_id === selectedCue.cue_id ? [currentCue, nextCue] : [cue]));
		draft = { ...draft, cues };
		draftOnlyCueIds = [...new Set([...draftOnlyCueIds, nextCue.cue_id])];
		selectedCueId = nextCue.cue_id;
		updateDraftUiState({ selected_cue_id: nextCue.cue_id });
		focusInspector('subtitle');
		message = '字幕片段已拆分';
		setTimeout(() => (message = ''), 1600);
	}

	function mergeSelectedCueWithNext() {
		if (!draft || !selectedCue) return;
		const sorted = [...draft.cues].sort((a, b) => (a.start_ms ?? 0) - (b.start_ms ?? 0));
		const index = sorted.findIndex((cue) => cue.cue_id === selectedCue.cue_id);
		const nextCue = index >= 0 ? sorted[index + 1] : null;
		if (!nextCue) {
			message = '当前字幕后面没有可合并的片段';
			setTimeout(() => (message = ''), 1600);
			return;
		}
		const mergedCue: VideoLocalizationCue = {
			...selectedCue,
			end_ms: Math.max(selectedCue.end_ms ?? 0, nextCue.end_ms ?? selectedCue.end_ms ?? 0),
			en_subtitle_text: mergeCueText(selectedCue.en_subtitle_text, nextCue.en_subtitle_text),
			zh_localized_subtitle_text: mergeCueText(selectedCue.zh_localized_subtitle_text, nextCue.zh_localized_subtitle_text),
			tts_recommended_text: mergeCueText(selectedCue.tts_recommended_text, nextCue.tts_recommended_text),
			source_duration_ms:
				selectedCue.start_ms !== null && (nextCue.end_ms ?? selectedCue.end_ms) !== null
					? Math.max(0, (nextCue.end_ms ?? selectedCue.end_ms ?? 0) - selectedCue.start_ms)
					: selectedCue.source_duration_ms,
			tts_result_id: null,
			tts_audio_path: null,
			tts_batch_task_id: null,
			tts_batch_status: null,
			tts_batch_error: null,
			tts_attempted_at: null,
			generated_duration_ms: null,
			review_status: 'needs_review',
			quality_flags: [...new Set([...(selectedCue.quality_flags ?? []), ...(nextCue.quality_flags ?? []), 'timeline_merge'])]
		};
		draft = {
			...draft,
			cues: draft.cues.map((cue) => (cue.cue_id === selectedCue.cue_id ? mergedCue : cue)).filter((cue) => cue.cue_id !== nextCue.cue_id),
			timeline_clips: draft.timeline_clips.filter((clip) => clip.cue_id !== nextCue.cue_id)
		};
		draftOnlyCueIds = draftOnlyCueIds.filter((id) => id !== nextCue.cue_id);
		selectedCueId = mergedCue.cue_id;
		updateDraftUiState({ selected_cue_id: mergedCue.cue_id });
		focusInspector('subtitle');
		message = '字幕片段已合并';
		setTimeout(() => (message = ''), 1600);
	}

	function rememberTimelineClips() {
		if (!draft) return;
		timelineUndoStack = [timelineSnapshot(), ...timelineUndoStack].slice(0, 30);
		timelineRedoStack = [];
	}

	function timelineSnapshot(): TimelineSnapshot {
		return {
			clips: draft?.timeline_clips.map((clip) => ({ ...clip })) ?? [],
			disabledMediaTracks: Array.isArray(draft?.ui_state?.disabled_media_tracks) ? draft.ui_state.disabled_media_tracks.map(String) : []
		};
	}

	function applyTimelineSnapshot(snapshot: TimelineSnapshot) {
		if (!draft) return;
		draft = {
			...draft,
			timeline_clips: snapshot.clips.map((clip) => ({ ...clip })),
			ui_state: { ...draft.ui_state, disabled_media_tracks: [...snapshot.disabledMediaTracks] }
		};
	}

	function updateTimelineClipFromTimeline(clipId: string, startMs: number, endMs: number, sourceStartMs: number, sourceEndMs: number | null) {
		if (!draft) return;
		rememberTimelineClips();
		const normalizedStart = Math.max(0, Math.round(startMs));
		const normalizedEnd = Math.max(normalizedStart + 300, Math.round(endMs));
		const normalizedSourceStart = Math.max(0, Math.round(sourceStartMs));
		const normalizedSourceEnd = sourceEndMs === null ? null : Math.max(normalizedSourceStart + 300, Math.round(sourceEndMs));
		draft = {
			...draft,
			timeline_clips: draft.timeline_clips.map((clip) =>
				clip.clip_id === clipId
					? {
							...clip,
							start_ms: normalizedStart,
							end_ms: normalizedEnd,
							source_start_ms: normalizedSourceStart,
							source_end_ms: normalizedSourceEnd
						}
					: clip
			)
		};
		scheduleDraftAutosave();
	}

	function deleteTimelineClip(clipId: string) {
		if (!draft) return;
		rememberTimelineClips();
		const target = draft.timeline_clips.find((clip) => clip.clip_id === clipId);
		const isMediaTrack = Boolean(target && ['original', 'vocals', 'background'].includes(target.track_id));
		const disabledTracks = Array.isArray(draft.ui_state?.disabled_media_tracks) ? draft.ui_state.disabled_media_tracks.map(String) : [];
		draft = {
			...draft,
			timeline_clips: draft.timeline_clips.filter((clip) => clip.clip_id !== clipId),
			ui_state: isMediaTrack && target
				? { ...draft.ui_state, disabled_media_tracks: [...new Set([...disabledTracks, target.track_id])] }
				: draft.ui_state
		};
		scheduleDraftAutosave();
		message = '音频片段已从时间线移除';
		setTimeout(() => (message = ''), 1600);
	}

	function undoTimelineClipEdit() {
		if (!draft || !timelineUndoStack.length) return;
		const [previous, ...rest] = timelineUndoStack;
		timelineUndoStack = rest;
		timelineRedoStack = [timelineSnapshot(), ...timelineRedoStack].slice(0, 30);
		applyTimelineSnapshot(previous);
		scheduleDraftAutosave();
	}

	function redoTimelineClipEdit() {
		if (!draft || !timelineRedoStack.length) return;
		const [next, ...rest] = timelineRedoStack;
		timelineRedoStack = rest;
		timelineUndoStack = [timelineSnapshot(), ...timelineUndoStack].slice(0, 30);
		applyTimelineSnapshot(next);
		scheduleDraftAutosave();
	}

	function deleteSelectedCue() {
		if (!draft || !selectedCue) return;
		const cueId = selectedCue.cue_id;
		const index = draft.cues.findIndex((cue) => cue.cue_id === cueId);
		const nextCues = draft.cues.filter((cue) => cue.cue_id !== cueId);
		draft = { ...draft, cues: nextCues };
		draftOnlyCueIds = draftOnlyCueIds.filter((id) => id !== cueId);
		selectedCueId = nextCues[Math.min(index, nextCues.length - 1)]?.cue_id ?? '';
		updateDraftUiState({ selected_cue_id: selectedCueId });
		focusInspector('subtitle');
		message = '字幕片段已删除';
		setTimeout(() => (message = ''), 1600);
	}

	function nextCueId(currentDraft: VideoLocalizationDraft) {
		const used = new Set(currentDraft.cues.map((cue) => cue.cue_id));
		let index = currentDraft.cues.length + 1;
		while (used.has(`cue_${String(index).padStart(4, '0')}`)) index += 1;
		return `cue_${String(index).padStart(4, '0')}`;
	}

	function splitCueText(value: string) {
		const text = value.trim();
		if (!text) return ['', ''] as const;
		const parts = text.split(/(?<=[。！？.!?])\s+/).filter(Boolean);
		if (parts.length > 1) {
			const middle = Math.ceil(parts.length / 2);
			return [parts.slice(0, middle).join(' '), parts.slice(middle).join(' ')] as const;
		}
		const words = text.split(/\s+/).filter(Boolean);
		if (words.length > 3) {
			const middle = Math.ceil(words.length / 2);
			return [words.slice(0, middle).join(' '), words.slice(middle).join(' ')] as const;
		}
		return [text, ''] as const;
	}

	function mergeCueText(first: string | null | undefined, second: string | null | undefined) {
		return [first?.trim(), second?.trim()].filter(Boolean).join('\n');
	}

	function normalizeCueTimePatch(cue: VideoLocalizationCue, patch: Partial<VideoLocalizationCue>) {
		if (cue.start_ms !== null && cue.end_ms !== null && cue.end_ms < cue.start_ms) {
			if ('start_ms' in patch) cue.end_ms = cue.start_ms + 500;
			else cue.start_ms = Math.max(0, cue.end_ms - 500);
		}
		return cue;
	}

	async function createSpeaker(payload: VideoLocalizationSpeakerCreate, assignCurrentCue: boolean) {
		if (!projectId) return;
		creatingSpeaker = true;
		error = '';
		try {
			if (draftOnlyCueIds.length) {
				await persistDraftSnapshot();
			}
			draft = await Api.createVideoLocalizationSpeaker(projectId, payload);
			message = assignCurrentCue && selectedCue ? '说话人已新增，正在绑定当前片段' : '说话人已新增';
			if (assignCurrentCue && selectedCue) {
				await assignSpeakerToCue(payload.speaker_id || speakerSeed.speaker_id, false);
			} else {
				setTimeout(() => (message = ''), 1800);
			}
		} catch (e) {
			error = (e as Error).message || '新增说话人失败';
		} finally {
			creatingSpeaker = false;
		}
	}

	async function assignSpeakerToCue(speakerId: string, showToast = true) {
		if (!projectId || !selectedCue || !speakerId) return;
		savingCue = true;
		error = '';
		try {
			if (cueNeedsDraftSave(selectedCue.cue_id)) {
				await persistDraftSnapshot();
			}
			draft = await Api.updateVideoLocalizationCue(projectId, selectedCue.cue_id, {
				speaker_id: speakerId,
				audio_route: selectedCue.audio_route === 'manual_review' ? (draft?.speakers.find((speaker) => speaker.speaker_id === speakerId)?.route ?? 'clone_from_source') : selectedCue.audio_route
			});
			selectedCueId = selectedCue.cue_id;
			if (showToast) {
				message = '当前片段已绑定说话人';
				setTimeout(() => (message = ''), 1800);
			} else {
				message = '说话人已新增并绑定当前片段';
				setTimeout(() => (message = ''), 1800);
			}
		} catch (e) {
			error = (e as Error).message || '绑定说话人失败';
		} finally {
			savingCue = false;
		}
	}

	function updateSelectedCueTime(field: 'start_ms' | 'end_ms', value: string | number) {
		const raw = typeof value === 'number' ? String(value) : value;
		const normalized = raw.trim();
		const parsed = normalized ? Number(normalized) : null;
		updateSelectedCue({ [field]: parsed !== null && Number.isFinite(parsed) ? Math.max(0, parsed) : null });
	}

	function applyLocalizationText(text: string) {
		if (!draft) return;
		const lines = text
			.split(/\r?\n/)
			.map((line) => line.trim())
			.filter(Boolean);
		if (!lines.length) return;
		const nextCues = draft.cues.map((cue, index) => {
			const line = lines[index];
			if (!line) return cue;
			const [subtitleText, ttsText] = line.split(/\s*\|\|\s*/, 2).map((part) => part.trim());
			return {
				...cue,
				zh_localized_subtitle_text: subtitleText || cue.zh_localized_subtitle_text,
				tts_recommended_text: ttsText || cue.tts_recommended_text,
				quality_flags: [...new Set([...cue.quality_flags.filter((flag) => !flag.startsWith('manual_localization_import')), 'manual_localization_import'])]
			};
		});
		draft = { ...draft, cues: nextCues };
		scheduleDraftAutosave();
		localizationImportOpen = false;
		message = `已应用 ${Math.min(lines.length, draft.cues.length)} 行中文稿，请校对后保存草稿`;
		setTimeout(() => (message = ''), 2400);
	}

	async function applyLocalizationSrt(text: string) {
		if (!projectId || !draft) return;
		error = '';
		try {
			const previousCueCount = draft.cues.length;
			draft = await Api.importVideoLocalizationSubtitles(projectId, 'zh', {
				srt_text: text,
				update_timing: true,
				overwrite_tts: false
			});
			selectedCueId = draft.cues[0]?.cue_id ?? selectedCueId;
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			localizationImportOpen = false;
			message = `已导入本土化 SRT，最多匹配 ${previousCueCount} 条 cue，请校对时间轴和 TTS 文本`;
			setTimeout(() => (message = ''), 2600);
		} catch (e) {
			error = (e as Error).message || '导入 SRT 失败';
		}
	}

	async function saveSelectedCue() {
		if (!projectId || !selectedCue) return;
		savingCue = true;
		error = '';
		const cueId = selectedCue.cue_id;
		const patch: VideoLocalizationCueUpdate = {
			speaker_id: selectedCue.speaker_id,
			start_ms: selectedCue.start_ms,
			end_ms: selectedCue.end_ms,
			audio_route: selectedCue.audio_route,
			en_subtitle_text: selectedCue.en_subtitle_text,
			zh_localized_subtitle_text: selectedCue.zh_localized_subtitle_text,
			tts_recommended_text: selectedCue.tts_recommended_text,
			reference_clip_id: selectedCue.reference_clip_id,
			review_status: selectedCue.review_status,
			quality_flags: selectedCue.quality_flags,
			notes: selectedCue.notes
		};
		try {
			if (cueNeedsDraftSave(cueId)) {
				await persistDraftSnapshot();
			}
			draft = await Api.updateVideoLocalizationCue(projectId, cueId, patch);
			selectedCueId = cueId;
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			message = '当前片段已保存';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '保存当前片段失败';
		} finally {
			savingCue = false;
		}
	}

	function speakerLabel(speakerId: string | null | undefined) {
		if (!speakerId) return '未选';
		const speaker = draft?.speakers.find((item) => item.speaker_id === speakerId);
		return speaker?.display_name || speakerId;
	}

	function referenceReady(referenceClipId: string | null | undefined) {
		if (!referenceClipId) return false;
		const clip = draft?.reference_clips.find((item) => item.reference_clip_id === referenceClipId);
		return Boolean(clip?.audio_path && clip.cleanliness === 'clean' && clip.asr_status === 'verified');
	}

	function referenceForCue(cue: VideoLocalizationCue | null) {
		if (!cue?.reference_clip_id) return null;
		return draft?.reference_clips.find((item) => item.reference_clip_id === cue.reference_clip_id) ?? null;
	}

	function cueCanSendToGenerate(cue: VideoLocalizationCue | null) {
		const reference = referenceForCue(cue);
		return Boolean(cue?.tts_recommended_text?.trim() && reference?.audio_path && reference.cleanliness === 'clean' && reference.asr_status === 'verified');
	}

	function selectedReferenceRecipe(reference: VideoLocalizationReferenceClip) {
		const recipes = draft?.voice_recipes.filter((recipe) => recipe.reference_clip_id === reference.reference_clip_id) ?? [];
		return recipes.find((recipe) => recipe.recipe_id === selectedRecipeId) ?? recipes[0] ?? null;
	}

	function sendSelectedCueToGenerate(mode: 'default' | 'recipe' = 'default') {
		if (!selectedCue || !cueCanSendToGenerate(selectedCue)) return;
		const reference = referenceForCue(selectedCue);
		const baseRequest = buildGenerateRequest(projectId, selectedCue, reference);
		const recipe = reference ? selectedReferenceRecipe(reference) : null;
		const request = mode === 'recipe' ? applyRecipeToRequest(baseRequest, recipe) : baseRequest;
		const handoffMode = mode === 'recipe' ? 'tune_with_recipe' : 'reference_only';
		const handoffMeta = {
			source: 'video_localization',
			mode: handoffMode,
			project_id: projectId,
			cue_id: selectedCue.cue_id,
			reference_clip_id: reference?.reference_clip_id ?? null,
			recipe_id: mode === 'recipe' ? (recipe?.recipe_id ?? null) : null,
			created_at: new Date().toISOString()
		};
		const params = new URLSearchParams({
			source: 'video_localization',
			mode: handoffMode,
			project_id: projectId,
			cue_id: selectedCue.cue_id
		});
		if (reference?.reference_clip_id) params.set('reference_id', reference.reference_clip_id);
		if (handoffMeta.recipe_id) params.set('recipe_id', handoffMeta.recipe_id);
		sessionStorage.setItem('voice-studio-history-reuse', JSON.stringify(request));
		sessionStorage.setItem('voice-studio-video-localization-handoff', JSON.stringify(handoffMeta));
		window.location.href = `/generate?${params.toString()}`;
	}

	function recipeIdFor(reference: VideoLocalizationReferenceClip, suffix = 'default') {
		return `recipe_${reference.reference_clip_id}_${suffix}`.replace(/[^A-Za-z0-9_-]+/g, '_');
	}

	function candidateIdFor(taskId: string) {
		return `candidate_${taskId}`.replace(/[^A-Za-z0-9_-]+/g, '_');
	}

	function recipeFromRequest(reference: VideoLocalizationReferenceClip, request: GenerateRequest, existingRecipe?: VideoLocalizationVoiceRecipe | null): VideoLocalizationVoiceRecipe {
		const existing = existingRecipe ?? selectedReferenceRecipe(reference);
		const now = new Date().toISOString();
		return {
			...(existing ?? {}),
			recipe_id: existing?.recipe_id || recipeIdFor(reference),
			reference_clip_id: reference.reference_clip_id,
			name: existing?.name || reference.title || reference.person_name || '默认参数',
			description: existing?.description ?? '从当前项目音色创建的默认参数组',
			engine_id: request.engine_id,
			parameter_snapshot: parameterSnapshotFromRequest(request),
			tags: existing?.tags?.length ? existing.tags : ['默认参数', ...(reference.tags ?? [])],
			created_from_task_id: existing?.created_from_task_id ?? null,
			created_at: existing?.created_at || now,
			updated_at: now
		};
	}

	function parameterSnapshotFromRequest(request: GenerateRequest) {
		const { text, project_id, segment_id, source, ...rest } = request;
		return rest;
	}

	function applyRecipeToRequest(request: GenerateRequest, recipe: VideoLocalizationVoiceRecipe | null | undefined): GenerateRequest {
		if (!recipe?.parameter_snapshot) return request;
		const snapshot = recipe.parameter_snapshot as Partial<GenerateRequest>;
		return {
			...request,
			...snapshot,
			text: request.text,
			source: request.source,
			project_id: request.project_id,
			segment_id: request.segment_id,
			reference_audio_path: request.reference_audio_path,
			ref_text: request.ref_text,
			custom_reference_source_audio_path: request.custom_reference_source_audio_path,
			custom_reference_source_duration_ms: request.custom_reference_source_duration_ms
		};
	}

	function upsertRecipe(recipes: VideoLocalizationVoiceRecipe[], recipe: VideoLocalizationVoiceRecipe) {
		return [recipe, ...recipes.filter((item) => item.recipe_id !== recipe.recipe_id)];
	}

	function newRecipeId(reference: VideoLocalizationReferenceClip) {
		return recipeIdFor(reference, String(Date.now()));
	}

	function selectedVoiceReference() {
		return draft?.reference_clips.find((item) => item.reference_clip_id === selectedVoiceId) ?? referenceForCue(selectedCue);
	}

	async function saveVoiceRecipes(nextRecipes: VideoLocalizationVoiceRecipe[], nextSelectedRecipeId = selectedRecipeId) {
		if (!projectId || !draft) return;
		draft = await Api.saveVideoLocalizationDraft(projectId, {
			...draft,
			voice_recipes: nextRecipes,
			ui_state: { ...(draft.ui_state ?? {}), selected_recipe_id: nextSelectedRecipeId }
		});
		selectedRecipeId = nextSelectedRecipeId;
		autoSaveStatus = 'saved';
		lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
	}

	async function createVoiceRecipe() {
		if (!projectId || !draft || !selectedCue) return;
		const reference = selectedVoiceReference();
		if (!reference) return;
		const request = buildGenerateRequest(projectId, selectedCue, reference);
		const now = new Date().toISOString();
		const recipe: VideoLocalizationVoiceRecipe = {
			recipe_id: newRecipeId(reference),
			reference_clip_id: reference.reference_clip_id,
			name: `参数组 ${draft.voice_recipes.filter((item) => item.reference_clip_id === reference.reference_clip_id).length + 1}`,
			description: '',
			engine_id: request.engine_id,
			parameter_snapshot: parameterSnapshotFromRequest(request),
			tags: [],
			created_from_task_id: null,
			created_at: now,
			updated_at: now
		};
		try {
			await saveVoiceRecipes([recipe, ...draft.voice_recipes], recipe.recipe_id);
			message = '已新增参数组';
			setTimeout(() => (message = ''), 1600);
		} catch (e) {
			error = (e as Error).message || '新增参数组失败';
		}
	}

	async function updateVoiceRecipe(recipeId: string, patch: Partial<VideoLocalizationVoiceRecipe>) {
		if (!projectId || !draft) return;
		const nextRecipes = draft.voice_recipes.map((recipe) =>
			recipe.recipe_id === recipeId ? { ...recipe, ...patch, updated_at: new Date().toISOString() } : recipe
		);
		try {
			await saveVoiceRecipes(nextRecipes, recipeId);
			message = '参数组已保存';
			setTimeout(() => (message = ''), 1600);
		} catch (e) {
			error = (e as Error).message || '保存参数组失败';
		}
	}

	async function deleteVoiceRecipe(recipeId: string) {
		if (!projectId || !draft) return;
		const recipe = draft.voice_recipes.find((item) => item.recipe_id === recipeId);
		if (!recipe) return;
		if (!window.confirm(`删除参数组「${recipe.name}」吗？已生成候选会保留，但不再归入这个参数组。`)) return;
		const nextRecipes = draft.voice_recipes.filter((item) => item.recipe_id !== recipeId);
		const nextSelected = nextRecipes.find((item) => item.reference_clip_id === recipe.reference_clip_id)?.recipe_id ?? '';
		try {
			await saveVoiceRecipes(nextRecipes, nextSelected);
			message = '参数组已删除';
			setTimeout(() => (message = ''), 1600);
		} catch (e) {
			error = (e as Error).message || '删除参数组失败';
		}
	}

	async function tuneSelectedVoiceInGenerate() {
		if (!selectedCue || !cueCanSendToGenerate(selectedCue)) return;
		sendSelectedCueToGenerate('recipe');
	}

	async function sendSelectedReferenceOnlyToGenerate() {
		if (!selectedCue || !cueCanSendToGenerate(selectedCue)) return;
		sendSelectedCueToGenerate('default');
	}

	async function quickGenerateSelectedVoice() {
		if (!projectId || !draft || !selectedCue || !cueCanSendToGenerate(selectedCue)) return;
		const reference = referenceForCue(selectedCue);
		if (!reference) return;
		submittingBatch = true;
		error = '';
		try {
			const baseRequest = buildGenerateRequest(projectId, selectedCue, reference);
			const selectedRecipe = selectedReferenceRecipe(reference);
			const recipe = recipeFromRequest(reference, baseRequest, selectedRecipe);
			const request = applyRecipeToRequest(baseRequest, recipe);
			const task = await Api.generate(request);
			const candidateId = candidateIdFor(task.task_id);
			const candidate: VideoLocalizationGeneratedCandidate = {
				candidate_id: candidateId,
				recipe_id: recipe.recipe_id,
				reference_clip_id: reference.reference_clip_id,
				cue_id: selectedCue.cue_id,
				audio_path: null,
				duration_ms: null,
				text_used: request.text,
				task_id: task.task_id,
				notes: null,
				status: task.status,
				created_at: new Date().toISOString()
			};
			const timelineClip: VideoLocalizationTimelineClip = {
				clip_id: `clip_${candidateId}`,
				cue_id: selectedCue.cue_id,
				candidate_id: candidateId,
				track_id: 'dub',
				start_ms: selectedCue.start_ms,
				end_ms: selectedCue.end_ms,
				source_start_ms: 0,
				source_end_ms: null,
				audio_path: null,
				status: 'queued'
			};
			const nextDraft = {
				...draft,
				voice_recipes: upsertRecipe(draft.voice_recipes, recipe),
				generated_candidates: [candidate, ...draft.generated_candidates.filter((item) => item.candidate_id !== candidateId)],
				timeline_clips: [timelineClip, ...draft.timeline_clips.filter((item) => item.clip_id !== timelineClip.clip_id)],
				ui_state: { ...(draft.ui_state ?? {}), selected_recipe_id: recipe.recipe_id }
			};
			draft = await Api.saveVideoLocalizationDraft(projectId, nextDraft);
			selectedRecipeId = recipe.recipe_id;
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			message = `已提交一键生成：${task.task_id}`;
			setTimeout(() => (message = ''), 2400);
		} catch (e) {
			error = (e as Error).message || '一键生成失败';
		} finally {
			submittingBatch = false;
		}
	}

	function operationFor(kind: VideoLocalizationOperation['kind']) {
		return operations.find((operation) => operation.kind === kind) ?? null;
	}

	function operationBusy(kind: VideoLocalizationOperation['kind']) {
		const operation = operationFor(kind);
		return Boolean(operation && isActiveOperation(operation));
	}

	async function refreshDraftOnly() {
		if (!projectId) return;
		const loadedDraft = await Api.videoLocalizationDraft(projectId);
		const editableDraft = withEditableMediaClips(loadedDraft);
		const addedMediaClips = editableDraft.timeline_clips.length > loadedDraft.timeline_clips.length;
		draft = editableDraft;
		if (addedMediaClips) scheduleDraftAutosave();
		draftOnlyCueIds = [];
		operations = sortOperations(draft.operations ?? operations);
		if (!selectedCueId && draft.cues[0]) selectedCueId = draft.cues[0].cue_id;
		if (!selectedVoiceId && draft.reference_clips[0]) selectedVoiceId = draft.reference_clips[0].reference_clip_id;
	}

	function withEditableMediaClips(value: VideoLocalizationDraft) {
		const durationMs = Math.max(300, Math.round(value.source_media.duration_ms ?? 0));
		const disabledTracks = Array.isArray(value.ui_state?.disabled_media_tracks) ? value.ui_state.disabled_media_tracks.map(String) : [];
		const mediaTracks: Array<{ trackId: 'original' | 'vocals' | 'background'; audioPath: string | null }> = [
			{ trackId: 'original', audioPath: value.source_media.audio_path || value.stems.original_audio_path },
			{ trackId: 'vocals', audioPath: value.stems.vocals_clean_path },
			{ trackId: 'background', audioPath: value.stems.background_path }
		];
		const additions: VideoLocalizationTimelineClip[] = [];
		for (const track of mediaTracks) {
			if (!track.audioPath || disabledTracks.includes(track.trackId) || value.timeline_clips.some((clip) => clip.track_id === track.trackId)) continue;
			additions.push({
				clip_id: `media_${track.trackId}`,
				track_id: track.trackId,
				start_ms: 0,
				end_ms: durationMs,
				source_start_ms: 0,
				source_end_ms: durationMs,
				audio_path: track.audioPath,
				status: 'ready',
				media_clip: true
			});
		}
		if (!additions.length) return value;
		const initializeOriginalSolo = additions.some((clip) => clip.track_id === 'original') && value.ui_state?.initial_track_mix_configured !== true;
		if (!initializeOriginalSolo) return { ...value, timeline_clips: [...value.timeline_clips, ...additions] };
		const existingStates = value.ui_state?.track_states && typeof value.ui_state.track_states === 'object'
			? value.ui_state.track_states as Record<string, Record<string, unknown>>
			: {};
		return {
			...value,
			timeline_clips: [...value.timeline_clips, ...additions],
			ui_state: {
				...value.ui_state,
				initial_track_mix_configured: true,
				track_states: {
					...existingStates,
					original: { ...(existingStates.original ?? {}), muted: false, solo: true, volume: existingStates.original?.volume ?? 1 },
					vocals: { ...(existingStates.vocals ?? {}), solo: false },
					background: { ...(existingStates.background ?? {}), solo: false },
					dub: { ...(existingStates.dub ?? {}), solo: false }
				}
			}
		};
	}

	function closeCurrentProject() {
		projectId = '';
		draft = null;
		draftOnlyCueIds = [];
		operations = [];
		selectedCueId = '';
		selectedVoiceId = '';
		selectedRecipeId = '';
		ttsBatchId = '';
		autoSaveStatus = 'idle';
		stopOperationPolling();
	}

	function updateSelectedVoiceId(voiceId: string) {
		selectedVoiceId = voiceId;
		const nextRecipeId = draft?.voice_recipes.find((recipe) => recipe.reference_clip_id === voiceId)?.recipe_id ?? '';
		selectedRecipeId = nextRecipeId;
		updateDraftUiState({ selected_reference_clip_id: voiceId, selected_recipe_id: nextRecipeId });
		focusInspector('generate');
	}

	function updateSelectedRecipeId(recipeId: string) {
		selectedRecipeId = recipeId;
		updateDraftUiState({ selected_recipe_id: recipeId });
	}

	function updateSubtitlePreview(patch: Partial<SubtitlePreviewState>) {
		updateDraftUiState({ subtitle_preview: { ...subtitlePreview, ...patch } });
	}

	function autoSubtitleSources() {
		const cue = previewCue;
		const hasLocalized = Boolean(cue?.zh_localized_subtitle_text?.trim());
		const hasAsr = Boolean(cue?.en_subtitle_text?.trim());
		const hasTts = Boolean(cue?.tts_recommended_text?.trim());
		return {
			asr: !hasLocalized && hasAsr,
			localized: hasLocalized,
			tts: !hasLocalized && !hasAsr && hasTts
		};
	}

	function subtitleSourceEnabled(source: Exclude<SubtitlePreviewSource, 'auto' | 'compare'>) {
		return (subtitlePreview.sources ?? autoSubtitleSources())[source];
	}

	function toggleSubtitleSource(source: Exclude<SubtitlePreviewSource, 'auto' | 'compare'>) {
		const current = subtitlePreview.sources ?? autoSubtitleSources();
		updateSubtitlePreview({ enabled: true, sources: { ...current, [source]: !current[source] } });
	}

	function updateTrackState(trackId: VideoLocalizationTrackId, patch: Partial<VideoLocalizationTrackState>) {
		updateDraftUiState({ track_states: { ...trackStates, [trackId]: { ...trackStates[trackId], ...patch } } });
	}

	function updateTimelineZoom(nextZoom: number) {
		updateDraftUiState({ timeline_zoom: clampNumber(nextZoom, 1, 1200, 1) });
	}

	function toggleInspectorCollapsed() {
		inspectorCollapsed = !inspectorCollapsed;
		updateDraftUiState({ sidebar_collapsed: inspectorCollapsed });
	}

	function focusInspector(section: 'voice' | 'generate' | 'subtitle' | 'style', voiceTab: 'library' | 'save-selection' = inspectorVoiceTab) {
		inspectorCollapsed = false;
		inspectorSection = section;
		inspectorVoiceTab = voiceTab;
		updateDraftUiState({ sidebar_collapsed: false, inspector_section: section, inspector_voice_tab: voiceTab });
	}

	function focusSaveSelectionAsVoice(startMs: number, endMs: number) {
		audioSelectionRange = { start_ms: startMs, end_ms: endMs };
		focusInspector('voice', 'save-selection');
	}

	function focusGenerateToSelection(startMs: number, endMs: number) {
		audioSelectionRange = { start_ms: startMs, end_ms: endMs };
		focusInspector('generate');
	}

	function updatePreviewTime(timeMs: number) {
		previewTimeMs = timeMs;
	}

	function updatePreviewPlaying(playing: boolean) {
		previewPlaying = playing;
	}

	function seekPreview(timeMs: number) {
		const boundedTimeMs = Math.max(0, Math.round(timeMs));
		previewTimeMs = boundedTimeMs;
		previewPlaybackController?.seek(boundedTimeMs);
	}

	function handleTimelineTransport(action: 'start' | 'play-pause' | 'next') {
		if (action === 'play-pause') {
			previewPlaybackController?.playPause();
			return;
		}
		if (action === 'start') {
			seekPreview(0);
			return;
		}
		const nextCue = (draft?.cues ?? [])
			.filter((cue) => cue.start_ms !== null && cue.start_ms > previewTimeMs + 80)
			.sort((a, b) => (a.start_ms ?? 0) - (b.start_ms ?? 0))[0];
		const nextTimeMs = nextCue?.start_ms ?? draft?.source_media.duration_ms ?? 0;
		seekPreview(nextTimeMs);
		if (nextCue) selectCue(nextCue.cue_id);
	}

	function seekTimeline(timeMs: number) {
		seekPreview(timeMs);
	}

	function updateDraftUiState(patch: Record<string, unknown>) {
		if (!draft) return;
		draft = { ...draft, ui_state: { ...(draft.ui_state ?? {}), ...patch } };
		pendingUiStatePatch = { ...pendingUiStatePatch, ...patch };
		scheduleDraftAutosave('ui');
	}

	function clampNumber(value: unknown, min: number, max: number, fallback: number) {
		const parsed = typeof value === 'number' ? value : Number(value);
		if (!Number.isFinite(parsed)) return fallback;
		return Math.max(min, Math.min(max, parsed));
	}

	function scheduleDraftAutosave(scope: 'ui' | 'draft' = 'draft') {
		if (!projectId || !draft) return;
		autoSaveScope = autoSaveScope === 'draft' || scope === 'draft' ? 'draft' : 'ui';
		autoSaveStatus = 'dirty';
		if (autoSaveTimer) clearTimeout(autoSaveTimer);
		autoSaveTimer = setTimeout(() => {
			void runDraftAutosave();
		}, 1400);
	}

	async function runDraftAutosave() {
		if (!projectId || !draft) return;
		const savingProjectId = projectId;
		const savingDraft = draft;
		const savingScope = autoSaveScope ?? 'draft';
		const uiPatch = pendingUiStatePatch;
		autoSaveScope = null;
		pendingUiStatePatch = {};
		autoSaveStatus = 'saving';
		try {
			let savedDraft: VideoLocalizationDraft;
			if (savingScope === 'ui') {
				savedDraft = await Api.updateVideoLocalizationUiState(savingProjectId, uiPatch);
			} else {
				try {
					savedDraft = await Api.saveVideoLocalizationDraft(savingProjectId, savingDraft);
				} catch (e) {
					if (!(e instanceof ApiError) || e.code !== 'VIDEO_LOCALIZATION_DRAFT_CONFLICT') throw e;
					const latest = await Api.videoLocalizationDraft(savingProjectId);
					savedDraft = await Api.saveVideoLocalizationDraft(savingProjectId, mergeDraftAfterConflict(latest, savingDraft));
				}
			}
			if (projectId === savingProjectId) {
				draft = draft === savingDraft
					? savedDraft
					: savingScope === 'ui'
						? { ...savedDraft, ui_state: draft?.ui_state ?? savedDraft.ui_state }
						: { ...savedDraft, ui_state: draft?.ui_state ?? savedDraft.ui_state, cues: draft?.cues ?? savedDraft.cues, timeline_clips: draft?.timeline_clips ?? savedDraft.timeline_clips };
			}
			draftOnlyCueIds = [];
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
		} catch (e) {
			autoSaveStatus = 'failed';
			error = (e as Error).message || '自动保存失败';
		}
	}

	function mergeDraftAfterConflict(latest: VideoLocalizationDraft, local: VideoLocalizationDraft): VideoLocalizationDraft {
		const latestCues = new Map(latest.cues.map((cue) => [cue.cue_id, cue]));
		const mergedCues = local.cues.map((cue) => {
			const serverCue = latestCues.get(cue.cue_id);
			if (!serverCue) return cue;
			return {
				...serverCue,
				...cue,
				tts_result_id: serverCue.tts_result_id ?? cue.tts_result_id,
				tts_audio_path: serverCue.tts_audio_path ?? cue.tts_audio_path,
				tts_batch_task_id: serverCue.tts_batch_task_id ?? cue.tts_batch_task_id,
				tts_batch_status: serverCue.tts_batch_status ?? cue.tts_batch_status,
				tts_batch_error: serverCue.tts_batch_error ?? cue.tts_batch_error,
				tts_attempted_at: serverCue.tts_attempted_at ?? cue.tts_attempted_at,
				generated_duration_ms: serverCue.generated_duration_ms ?? cue.generated_duration_ms,
				quality_flags: [...new Set([...(cue.quality_flags ?? []), ...(serverCue.quality_flags ?? [])])]
			};
		});
		const localClips = new Map(local.timeline_clips.map((clip) => [clip.clip_id, clip]));
		const latestClips = new Map(latest.timeline_clips.map((clip) => [clip.clip_id, clip]));
		const mergedClips = local.timeline_clips.map((clip) => {
			const serverClip = latestClips.get(clip.clip_id);
			return serverClip
				? { ...serverClip, ...clip, audio_path: serverClip.audio_path ?? clip.audio_path, status: serverClip.status ?? clip.status, candidate_id: serverClip.candidate_id ?? clip.candidate_id }
				: clip;
		});
		for (const clip of latest.timeline_clips) {
			if (!localClips.has(clip.clip_id)) mergedClips.push(clip);
		}
		return {
			...latest,
			ui_state: local.ui_state,
			cues: mergedCues,
			timeline_clips: mergedClips
		};
	}

	function cueNeedsDraftSave(cueId: string) {
		return draftOnlyCueIds.includes(cueId);
	}

	function hasResettableContent(currentDraft: VideoLocalizationDraft | null) {
		if (!currentDraft) return false;
		return Boolean(
			currentDraft.source_media.filename ||
				currentDraft.source_media.video_path ||
				currentDraft.source_media.audio_path ||
				currentDraft.stems.original_audio_path ||
				currentDraft.stems.vocals_clean_path ||
				currentDraft.stems.background_path ||
				currentDraft.cues.length ||
				currentDraft.speakers.length ||
				currentDraft.reference_clips.length ||
				currentDraft.operations.length
		);
	}

	async function persistDraftSnapshot() {
		if (!projectId || !draft) return;
		draft = await Api.saveVideoLocalizationDraft(projectId, draft);
		draftOnlyCueIds = [];
	}

	function startOperationPolling() {
		if (operationPollingTimer) return;
		operationPollingTimer = setInterval(() => {
			void pollOperations();
		}, 1500);
	}

	function stopOperationPolling() {
		if (!operationPollingTimer) return;
		clearInterval(operationPollingTimer);
		operationPollingTimer = null;
	}

	async function pollOperations() {
		if (!projectId) {
			stopOperationPolling();
			return;
		}
		try {
			const latest = sortOperations(await Api.videoLocalizationOperations(projectId));
			operations = latest;
			await refreshDraftOnly();
			if (!latest.some((operation) => isActiveOperation(operation))) stopOperationPolling();
			const failed = latest.find((operation) => operation.status === 'failed');
			if (failed?.error_message) error = failed.error_message;
		} catch (e) {
			error = (e as Error).message || '刷新任务状态失败';
			stopOperationPolling();
		}
	}

</script>

<svelte:window onpointerdown={closeProjectMenuFromPage} onkeydown={handlePageKeydown} />

<svelte:head>
	<title>视频本土化配音 - Voice Studio</title>
</svelte:head>

<main class="page video-localization-page cutting-mode">
	<header class="cutting-head">
		<div class="cutting-project-line">
			<div class="cutting-brand">
				<div class="brand-mark" aria-hidden="true"><Clapperboard size={16} /></div>
				<div>
					{#if !hasImportedProject}
						<span class="workspace-label">视频本土化工作台</span>
					{/if}
					{#if editingProjectName}
						<div class="project-name-editor">
							<input
								aria-label="项目名称"
								bind:value={projectNameDraft}
								disabled={projectNameSaving}
								onkeydown={handleProjectNameKeydown}
							/>
							<button type="button" aria-label="保存项目名称" data-tooltip="保存名称：同步修改项目名称和对应的项目目录名称。" onclick={saveProjectNameEdit} disabled={projectNameSaving || !projectNameDraft.trim()}><Check size={13} /></button>
							<button type="button" aria-label="取消修改项目名称" data-tooltip="取消修改：保留当前项目名称不变。" onclick={cancelProjectNameEdit} disabled={projectNameSaving}><X size={13} /></button>
						</div>
					{:else}
						<div class="project-name-display">
							<h1>{selectedProject?.name || draft?.source_media.filename || '未命名本土化项目'}</h1>
							<button type="button" aria-label="修改项目名称" data-tooltip="修改名称：项目目录会随新名称同步调整。" onclick={startProjectNameEdit} disabled={!selectedProject || importing}><Pencil size={12} /></button>
							<div class="project-switcher">
								<button class="project-history-toggle" class:active={projectMenuOpen} type="button" aria-label="切换历史项目" aria-expanded={projectMenuOpen} data-tooltip="切换项目：同步本地项目目录并打开已有的视频本土化项目。" onclick={toggleProjectMenu} disabled={loading}><ChevronDown size={13} /></button>
								{#if projectMenuOpen}
									<div class="project-menu" role="menu" aria-label="历史项目">
										<div class="project-menu-head"><strong>历史项目</strong><span>{projectMenuSyncing ? '同步中' : projects.length}</span></div>
										<div class="project-menu-list">
											{#if !projectMenuSyncing && !projects.length}
												<div class="project-menu-empty">本地没有可用项目</div>
											{/if}
											{#each projects as project}
												<button class:active={project.project_id === projectId} type="button" role="menuitem" onclick={() => selectProject(project.project_id)}>
													<span>{project.name}</span>
													<small>{project.description || '视频本土化项目'}</small>
													{#if project.project_id === projectId}<Check size={13} />{/if}
												</button>
											{/each}
										</div>
									</div>
								{/if}
							</div>
						</div>
					{/if}
					<p class="muted">
						{draft?.source_media.duration_ms ? `${(draft.source_media.duration_ms / 1000 / 60).toFixed(1)} 分钟` : '导入视频后自动创建草稿'} · {saveStatusLabel}
					</p>
				</div>
			</div>
		</div>
		<div class="cutting-actions">
			<input bind:this={videoInput} data-video-localization-file class="visually-hidden" type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.m4v,.webm,.mkv" onchange={(event) => importVideoFile(event.currentTarget.files?.[0])} />
			<button
				class="icon-action"
				type="button"
				onclick={closeCurrentProject}
				disabled={!projectId}
				aria-label="关闭当前项目"
				data-tooltip="关闭当前项目，项目文件仍会保留在本地目录。"
			>
				<X size={15} />
			</button>
			<button
				class="icon-action"
				type="button"
				onclick={openProjectDirectory}
				disabled={!projectId || openingProjectDirectory}
				aria-label="打开项目目录"
				data-tooltip="在 Finder 中打开当前项目保存目录。"
			>
				<FolderOpen size={15} />
			</button>
			<button class="icon-action" type="button" onclick={toggleInspectorCollapsed} data-tooltip={inspectorCollapsed ? '展开侧栏：显示音色、字幕与样式检查器。' : '收起侧栏：为视频和时间线释放更多空间。'} aria-label={inspectorCollapsed ? '展开侧栏' : '收起侧栏'}>
				{#if inspectorCollapsed}
					<PanelRightOpen size={16} />
				{:else}
					<PanelRightClose size={16} />
				{/if}
			</button>
		</div>
	</header>

	{#if error || message}
		<div class={`notice ${error ? 'fail' : 'ok'}`}>{error || message}</div>
	{/if}

	<section class="cutting-shell" class:collapsed={inspectorCollapsed}>
		<section class="cutting-stage">
			<PreviewPanel
				selectedCue={previewCue}
				{draft}
				{projectId}
				{importing}
				{subtitlePreview}
				{trackStates}
				onRequestImport={() => videoInput?.click()}
				onImportFile={importVideoFile}
				onVideoTimeUpdate={updatePreviewTime}
				onPlaybackStateChange={updatePreviewPlaying}
				onControllerReady={(controller) => (previewPlaybackController = controller)}
			/>
			<LocalizationTextImport open={localizationImportOpen} cueCount={draft?.cues.length ?? 0} onApply={applyLocalizationText} onApplySrt={applyLocalizationSrt} onClose={() => (localizationImportOpen = false)} />
			<VideoCuttingTimeline
				{projectId}
				{draft}
				{selectedCueId}
				currentTimeMs={previewTimeMs}
				isPlaying={previewPlaying}
				{latestOperation}
				{extractingAudio}
				{separatingStems}
				{transcribingAsr}
				{trackStates}
				{timelineZoom}
				subtitlePreview={{ ...subtitlePreview, sources: subtitlePreview.sources ?? autoSubtitleSources() }}
				onSelectCue={selectCue}
				onExtractAudio={extractSourceAudio}
				onSeparateStems={separateStems}
				onTranscribeEnglish={transcribeEnglishSource}
				onTransportAction={handleTimelineTransport}
				onTrackStateChange={updateTrackState}
				onTimelineZoomChange={updateTimelineZoom}
				onToggleSubtitleSource={toggleSubtitleSource}
				onSeekTimeline={seekTimeline}
				onUpdateCueTime={updateCueTimeFromTimeline}
				onSplitCue={splitSelectedCue}
				onMergeCue={mergeSelectedCueWithNext}
				onDeleteCue={deleteSelectedCue}
				onSaveSelectionAsVoice={focusSaveSelectionAsVoice}
				onGenerateToSelection={focusGenerateToSelection}
				onUpdateTimelineClip={updateTimelineClipFromTimeline}
				onDeleteTimelineClip={deleteTimelineClip}
				onUndoTimelineClip={undoTimelineClipEdit}
				onRedoTimelineClip={redoTimelineClipEdit}
				canUndoTimeline={timelineUndoStack.length > 0}
				canRedoTimeline={timelineRedoStack.length > 0}
			/>
			{#if draft?.quality_gate.warnings.length || draft?.quality_gate.blockers.length}
				<div class="quality-bar panel-inline">
					{#each draft?.quality_gate.warnings ?? [] as issue}
						<span class="badge warn"><AlertTriangle size={13} /> {issue.message}</span>
					{/each}
					{#each draft?.quality_gate.blockers ?? [] as issue}
						<span class="badge fail"><AlertTriangle size={13} /> {issue.message}</span>
					{/each}
				</div>
			{/if}
			<div class="cutting-utility-row">
				<button class="mini-btn" type="button" data-tooltip="导入字幕：载入外部完成的本土化文本或 SRT，并按字幕片段匹配。" onclick={() => (localizationImportOpen = !localizationImportOpen)} disabled={!draft?.cues.length}>
					{localizationImportOpen ? '收起字幕导入' : '导入字幕'}
				</button>
				<button class="mini-btn" type="button" data-tooltip="创建本土化占位稿：复制原文并标记待本土化，便于后续逐条编辑或导入译文。" onclick={localizeChineseDraft} disabled={!draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) || localizingDraft}>
					{localizingDraft ? '创建中' : '创建本土化占位稿'}
				</button>
				<button class="mini-btn" type="button" data-tooltip="新增字幕片段：在时间线末尾创建一条可编辑字幕。" onclick={addCue} disabled={!draft}>新增字幕片段</button>
				<button class="mini-btn" type="button" data-tooltip="生成参考音候选：从已识别的干净人声片段创建项目音色候选。" onclick={createReferenceCandidates} disabled={draft?.stems.separation_status !== 'completed' || creatingReferences}>
					{creatingReferences ? '生成中' : '生成参考音候选'}
				</button>
				<button class="mini-btn btn-danger" type="button" data-tooltip="清空当前任务：移除当前项目的本土化工作数据，保留项目本身。" onclick={resetCurrentTask} disabled={!hasResettableDraft || resetting}>
					<Trash2 size={13} /> {resetting ? '清空中' : '清空当前任务'}
				</button>
			</div>
			<div class="studio-command-strip">
				<section class="job-monitor" aria-label="任务队列">
					<div class="strip-head">
						<strong>任务队列</strong>
						<span>{hasActiveOperation ? '运行中' : operations.length ? '最近任务' : '暂无任务'}</span>
					</div>
					{#if operations.length}
						<div class="operation-list">
							{#each operations.slice(0, 3) as operation}
								<div class="operation-row">
									<div>
										<strong>{operation.label ?? operation.kind}</strong>
										<span>{operationStatusLabel(operation)}</span>
									</div>
									<div class="operation-actions">
										{#if isActiveOperation(operation)}
											<button class="tiny-btn" type="button" data-tooltip="取消任务：停止该后台处理任务并保留已完成的数据。" onclick={() => cancelOperation(operation)} disabled={operationActionId === operation.operation_id}>取消</button>
										{:else if operation.status === 'failed' || operation.status === 'cancelled'}
											<button class="tiny-btn" type="button" data-tooltip="重试任务：使用相同参数重新提交失败或已取消的任务。" onclick={() => retryOperation(operation)} disabled={operationActionId === operation.operation_id}>重试</button>
										{/if}
									</div>
								</div>
							{/each}
						</div>
					{:else}
						<p>导入、分离、ASR 和参考音生成会显示在这里。</p>
					{/if}
				</section>
				<section class="delivery-strip" aria-label="批处理与产物">
					<div class="strip-head">
						<strong>交付输出</strong>
						<span>{readyCount} 可生成 / {generatedCount} 已有音频 / {blockedCount} 阻断</span>
					</div>
					<div class="delivery-actions">
						<button class="mini-btn" type="button" data-tooltip="批量 TTS：提交所有满足条件且尚未生成音频的字幕片段。" onclick={submitBatchTts} disabled={!canSubmitCount || submittingBatch}>
							{submittingBatch ? '提交中' : `批量 TTS ${canSubmitCount || ''}`}
						</button>
						<input class="batch-input" value={ttsBatchId} placeholder="Batch ID" oninput={(event) => (ttsBatchId = event.currentTarget.value)} />
						<button class="mini-btn" type="button" data-tooltip="同步结果：按 Batch ID 拉取已完成的 TTS 音频并回填时间线。" onclick={syncBatchTtsResults} disabled={!ttsBatchId.trim() || syncingBatch}>{syncingBatch ? '同步中' : '同步结果'}</button>
						<button class="mini-btn" type="button" data-tooltip="导出 SRT：下载包含原文和本土化文本的字幕文件。" onclick={exportBilingualSrt} disabled={!draft?.cues.length}>导出 SRT</button>
						<button class="mini-btn" type="button" data-tooltip="导出 EDL：下载时间线片段、轨道路由和字幕信息。" onclick={exportTimelineEdl} disabled={!draft}>导出 EDL</button>
						<button class="mini-btn" type="button" data-tooltip="导出音频包：输出对齐后的中文配音轨、分段音频和清单。" onclick={exportTimelineAudioPackage} disabled={!generatedCount || exportingAudioPackage}>
							{exportingAudioPackage ? '导出中' : `导出音频包${generatedCount ? ` ${generatedCount}` : ''}`}
						</button>
						<button class="mini-btn" type="button" data-tooltip="导出合成视频：按当前时间线生成本土化视频文件。" onclick={exportLocalizedVideo} disabled={!draft?.source_media.video_path || !generatedCount || exportingLocalizedVideo}>
							{exportingLocalizedVideo ? '合成中' : '导出合成视频'}
						</button>
						<button class="mini-btn" type="button" data-tooltip="交付检查：导出阻断项、警告和可生成片段统计。" onclick={exportReadinessAudit} disabled={!draft}>交付检查</button>
					</div>
					<p class="delivery-note">
						{draft?.exports.localized_video_path
							? `上次合成视频：${String(draft.exports.localized_video_path).split('/').pop()}`
							: draft?.exports.timeline_audio_package_path
								? `上次音频包：${String(draft.exports.timeline_audio_package_path).split('/').pop()}`
								: '音频包会包含按时间线对齐的 dub-track.wav、分段 wav 和 manifest.json。'}
					</p>
				</section>
			</div>
		</section>

		{#if !inspectorCollapsed}
			<CuttingInspector
				{draft}
				{projectId}
				{selectedCue}
				selectionRange={audioSelectionRange}
				{selectedVoiceId}
				{selectedRecipeId}
				{inspectorSection}
				{inspectorVoiceTab}
				{subtitlePreview}
				onSelectedVoiceIdChange={updateSelectedVoiceId}
				onSectionChange={(section) => focusInspector(section)}
				onUpdateCue={updateSelectedCue}
				onSaveCue={saveSelectedCue}
				onDeleteCue={deleteSelectedCue}
				onUpdateSubtitlePreview={updateSubtitlePreview}
				onCreateReferenceCandidates={createReferenceCandidates}
				onCreateReferenceFromSelection={createReferenceFromSelection}
				onUpdateReferenceClip={updateReferenceClip}
				onDeleteReferenceClip={deleteReferenceClip}
				onSelectedRecipeIdChange={updateSelectedRecipeId}
				onCreateVoiceRecipe={createVoiceRecipe}
				onUpdateVoiceRecipe={updateVoiceRecipe}
				onDeleteVoiceRecipe={deleteVoiceRecipe}
				onQuickGenerateVoice={quickGenerateSelectedVoice}
				onTuneVoiceInGenerate={tuneSelectedVoiceInGenerate}
				onSendReferenceOnlyToGenerate={sendSelectedReferenceOnlyToGenerate}
				onApplyGeneratedCandidate={applyGeneratedCandidate}
				{creatingReferences}
				{savingCue}
				{referenceUpdatingId}
				{candidateApplyingId}
				generatingVoice={submittingBatch}
			/>
		{:else}
			<aside class="inspector-rail">
				<button class="rail-tab" type="button" onclick={() => focusInspector('voice')} aria-label="音色" data-tooltip="音色｜展开项目音色库和样音保存面板。"><AudioLines size={15} /></button>
				<button class="rail-tab" type="button" onclick={() => focusInspector('generate')} aria-label="生成" data-tooltip="生成｜展开音色参数组和配音候选面板。"><WandSparkles size={15} /></button>
				<button class="rail-tab" type="button" onclick={() => focusInspector('subtitle')} aria-label="字幕" data-tooltip="字幕｜展开当前字幕片段编辑面板。"><Captions size={15} /></button>
				<button class="rail-tab" type="button" onclick={() => focusInspector('style')} aria-label="样式" data-tooltip="样式｜展开字幕外观和位置设置。"><Palette size={15} /></button>
			</aside>
		{/if}
	</section>
</main>

<style>
	.video-localization-page {
		max-width: none;
		padding: 14px 14px 48px;
		background: #0f1114;
	}

	.cutting-mode {
		--studio-bg: #0f1114;
		--studio-panel: #16191d;
		--studio-panel-2: #1b2025;
		--studio-panel-3: #20262b;
		--studio-soft: #303941;
		--studio-accent: #57d0c8;
		--studio-warn: #d9b45f;
	}

	.cutting-head {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 10px;
		min-height: 44px;
		margin: 0 auto;
		max-width: 1720px;
		padding: 6px 8px;
		border: 1px solid var(--line);
		border-bottom: 0;
		border-radius: 9px 9px 0 0;
		background:
			linear-gradient(180deg, rgba(255, 255, 255, 0.025), transparent),
			var(--studio-panel);
	}

	.cutting-project-line {
		display: flex;
		align-items: center;
		gap: 9px;
		min-width: 0;
		overflow: visible;
	}

	.cutting-brand {
		display: flex;
		align-items: center;
		gap: 8px;
		min-width: 0;
		flex: 1 1 320px;
		overflow: visible;
	}

	.brand-mark {
		width: 28px;
		height: 28px;
		border-radius: 6px;
		border: 1px solid rgba(255, 255, 255, 0.18);
		display: grid;
		place-items: center;
		background: #17292a;
		color: #8ae5de;
		flex: 0 0 auto;
	}

	.workspace-label {
		display: inline-flex;
		align-items: center;
		margin: 0;
		color: #8c99a5;
		font-size: 10px;
		font-weight: 750;
		letter-spacing: 0;
		text-transform: uppercase;
		white-space: nowrap;
	}

	.cutting-brand > div:not(.brand-mark) {
		display: flex;
		align-items: baseline;
		gap: 8px;
		min-width: 0;
	}

	.cutting-brand h1,
	.cutting-brand p {
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.cutting-brand h1 {
		min-width: 90px;
		font-size: 16px;
		line-height: 1.2;
	}

	.cutting-brand p {
		flex: 0 0 auto;
		font-size: 12px;
	}

	.project-name-display,
	.project-name-editor {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		min-width: 90px;
		max-width: 310px;
	}

	.project-name-display h1 {
		min-width: 0;
	}

	.project-name-display button,
	.project-name-editor button {
		width: 23px;
		height: 23px;
		border: 1px solid var(--line);
		border-radius: 6px;
		display: inline-grid;
		place-items: center;
		padding: 0;
		background: #1d2328;
		color: var(--muted);
		cursor: pointer;
		flex: 0 0 auto;
	}

	.project-name-display button:hover:not(:disabled),
	.project-name-display button:focus-visible,
	.project-name-editor button:hover:not(:disabled),
	.project-name-editor button:focus-visible {
		border-color: rgba(113, 224, 215, 0.72);
		background: #26343a;
		color: #efffff;
		outline: none;
	}

	.project-switcher {
		position: relative;
		flex: 0 0 auto;
	}

	.project-history-toggle.active :global(svg) {
		transform: rotate(180deg);
	}

	.project-history-toggle :global(svg) {
		transition: transform 150ms ease;
	}

	.project-menu {
		position: absolute;
		top: calc(100% + 7px);
		left: 0;
		z-index: 75;
		width: min(330px, 76vw);
		border: 1px solid #3a454d;
		border-radius: 7px;
		background: #11161a;
		box-shadow: 0 18px 38px rgba(0, 0, 0, 0.46);
		overflow: hidden;
		transform-origin: top left;
		animation: project-menu-in 150ms cubic-bezier(0.2, 0.8, 0.2, 1);
	}

	@keyframes project-menu-in {
		from { opacity: 0; transform: translateY(-5px) scale(0.98); }
		to { opacity: 1; transform: translateY(0) scale(1); }
	}

	.project-menu-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 8px 10px;
		border-bottom: 1px solid #303940;
		color: #dce5ea;
		font-size: 11px;
	}

	.project-menu-head span {
		color: var(--muted);
		font-variant-numeric: tabular-nums;
	}

	.project-menu-list {
		max-height: 300px;
		overflow-y: auto;
		padding: 4px;
	}

	.project-menu-empty {
		padding: 18px 10px;
		color: #7f8d96;
		font-size: 10px;
		text-align: center;
	}

	.project-menu-list button {
		width: 100%;
		height: auto;
		min-height: 42px;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 2px 8px;
		border: 0;
		border-radius: 5px;
		padding: 6px 8px;
		background: transparent;
		text-align: left;
	}

	.project-menu-list button:hover,
	.project-menu-list button.active {
		background: #1d282d;
		color: #ddfffb;
	}

	.project-menu-list button > span,
	.project-menu-list button > small {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.project-menu-list button > span {
		font-size: 11px;
		font-weight: 750;
	}

	.project-menu-list button > small {
		grid-column: 1;
		color: #7f8d96;
		font-size: 9px;
	}

	.project-menu-list button > :global(svg) {
		grid-column: 2;
		grid-row: 1 / span 2;
		color: #6fd9d1;
	}

	.project-name-display button:hover:not(:disabled),
	.project-name-editor button:hover:not(:disabled) {
		color: var(--text);
		border-color: #52636d;
		background: #242b31;
	}

	.project-name-editor input {
		min-width: 150px;
		width: min(250px, 24vw);
		height: 25px;
		border: 1px solid rgba(87, 208, 200, 0.58);
		border-radius: 6px;
		padding: 2px 7px;
		background: #0f1418;
		color: var(--text);
		font-size: 12px;
		font-weight: 750;
		outline: none;
	}

	.cutting-actions,
	.cutting-utility-row {
		display: flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
	}

	.cutting-actions {
		flex: 0 0 auto;
		flex-wrap: nowrap;
		justify-content: flex-end;
	}

	.cutting-mode .btn,
	.cutting-mode .mini-btn,
	.cutting-mode .project-select,
	.cutting-mode .icon-action {
		min-height: 27px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #1d2328;
		color: var(--text);
		font-size: 11px;
	}

	.cutting-mode .icon-action {
		width: 28px;
		height: 28px;
		display: inline-grid;
		place-items: center;
		padding: 0;
		position: relative;
		cursor: pointer;
	}

	.cutting-mode .icon-action:hover:not(:disabled),
	.cutting-mode .icon-action:focus-visible {
		border-color: rgba(113, 224, 215, 0.72);
		background: #26343a;
		color: #efffff;
		outline: none;
	}

	.cutting-mode .btn,
	.cutting-mode .mini-btn,
	.cutting-mode .icon-action {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 4px;
		padding: 3px 7px;
		cursor: pointer;
	}

	.cutting-mode .icon-action {
		width: 28px;
		padding: 0;
	}

	.cutting-mode .project-select {
		width: clamp(180px, 18vw, 320px);
		min-width: 0;
		padding: 3px 7px;
		background: #20262c;
	}

	.cutting-mode .btn:disabled,
	.cutting-mode .mini-btn:disabled,
	.cutting-mode .icon-action:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.cutting-mode .btn:hover:not(:disabled),
	.cutting-mode .mini-btn:hover:not(:disabled),
	.cutting-mode .icon-action:hover:not(:disabled) {
		border-color: #4f606a;
		background: #242b31;
	}

	.cutting-mode .primary-action {
		border-color: rgba(87, 208, 200, 0.78);
		background: #133b39;
		color: #d7fffb;
		font-weight: 800;
	}

	.cutting-mode .mini-btn.active {
		border-color: var(--studio-accent);
		background: #173a37;
		color: #d4fffb;
	}

	.cutting-shell {
		display: grid;
		grid-template-columns: minmax(0, 1fr) clamp(340px, 24vw, 400px);
		max-width: 1720px;
		margin: 0 auto;
		border: 1px solid var(--line);
		border-radius: 0 0 10px 10px;
		background: var(--studio-panel);
		overflow: hidden;
	}

	.cutting-shell.collapsed {
		grid-template-columns: minmax(0, 1fr) 52px;
	}

	.cutting-stage {
		display: grid;
		grid-template-rows: auto auto auto auto auto auto;
		gap: 10px;
		min-width: 0;
		padding: 12px;
		border-right: 1px solid var(--line);
		background: #121519;
		overflow-x: hidden;
	}

	.cutting-stage :global(.preview-panel) {
		width: 100%;
		max-width: none;
		justify-self: stretch;
		justify-items: center;
	}

	.cutting-utility-row {
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #15191e;
	}

	.inspector-rail {
		display: grid;
		align-content: start;
		gap: 7px;
		justify-items: center;
		width: 42px;
		padding: 8px 6px;
		border-left: 1px solid var(--line);
		background: #171a1d;
	}

	.rail-tab {
		width: 28px;
		height: 28px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #1d2328;
		color: var(--muted);
		display: grid;
		place-items: center;
		cursor: pointer;
	}

	.rail-tab {
		font-size: 12px;
		font-weight: 800;
	}

	.rail-tab:hover {
		color: var(--text);
		border-color: #4d626b;
		background: #222b31;
	}

	.localization-head {
		align-items: center;
	}

	.head-actions {
		justify-content: flex-end;
	}

	.project-select {
		min-width: 180px;
	}

	.notice {
		max-width: 1720px;
		margin: 8px auto;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 10px 12px;
		font-size: 13px;
		background: var(--panel);
	}

	.notice.ok {
		color: #9ee6c8;
		border-color: #23634f;
		background: #12261f;
	}

	.notice.fail {
		color: #ff9a9a;
		border-color: #6d3030;
		background: #2b1515;
	}

	.btn-danger {
		color: #ffb0b0;
		border-color: #6d3030;
		background: #261617;
	}

	.btn-danger:disabled {
		color: var(--muted);
		border-color: var(--line);
		background: #15181d;
	}

	.badge.active {
		color: #9cc9ff;
		border-color: #27527e;
		background: #101d2d;
	}

	.visually-hidden {
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

	.section-title {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 12px;
		margin-bottom: 12px;
	}

	.mini-btn {
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #15181d;
		color: var(--text);
		padding: 3px 7px;
		font-size: 11px;
		cursor: pointer;
	}

	.mini-btn:disabled {
		color: var(--muted);
		cursor: not-allowed;
		opacity: 0.65;
	}

	.quality-bar {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}

	.studio-command-strip {
		display: grid;
		grid-template-columns: minmax(260px, 0.8fr) minmax(380px, 1.2fr);
		gap: 10px;
	}

	.job-monitor,
	.delivery-strip {
		min-width: 0;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #14191e;
		overflow: hidden;
	}

	.strip-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		min-height: 34px;
		padding: 7px 10px;
		border-bottom: 1px solid #303941;
	}

	.strip-head strong,
	.strip-head span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.strip-head strong {
		font-size: 12px;
	}

	.strip-head span,
	.job-monitor p,
	.operation-row span {
		color: var(--muted);
		font-size: 11px;
	}

	.job-monitor p {
		margin: 0;
		padding: 9px 10px;
	}

	.operation-list {
		display: grid;
	}

	.operation-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 8px;
		min-height: 38px;
		padding: 6px 10px;
		border-bottom: 1px solid #303941;
	}

	.operation-row:last-child {
		border-bottom: 0;
	}

	.operation-row strong,
	.operation-row span {
		display: block;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.operation-row strong {
		font-size: 12px;
	}

	.tiny-btn {
		min-height: 24px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #20262c;
		color: var(--text);
		font-size: 11px;
		padding: 2px 7px;
		cursor: pointer;
	}

	.delivery-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 7px;
		align-items: center;
		padding: 8px 10px;
	}

	.delivery-note {
		margin: 0;
		padding: 0 10px 9px;
		color: var(--muted);
		font-size: 11px;
		line-height: 1.45;
	}

	.batch-input {
		min-width: 104px;
		flex: 1 1 112px;
		height: 31px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #11161b;
		color: var(--text);
		padding: 5px 8px;
		font-size: 12px;
	}

	.view-switcher {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin: 0 0 14px;
		flex-wrap: wrap;
	}

	.segmented-tabs {
		display: inline-flex;
		padding: 4px;
		border-radius: 9px;
		background: #0f1318;
		border: 1px solid var(--line);
	}

	.compact {
		gap: 8px;
		flex-wrap: wrap;
	}

	.single-shell {
		display: grid;
		grid-template-columns: minmax(280px, 0.78fr) minmax(560px, 1.35fr) minmax(340px, 0.9fr);
		gap: 14px;
		align-items: start;
	}

	.batch-shell {
		display: grid;
		grid-template-columns: minmax(320px, 0.82fr) minmax(760px, 1.4fr);
		gap: 14px;
		align-items: start;
	}

	.single-left,
	.main-workbench,
	.single-right,
	.batch-left {
		align-self: start;
	}

	.main-workbench {
		display: grid;
		gap: 12px;
	}

	.context-panel {
		display: grid;
		gap: 10px;
	}

	.context-tabs {
		width: 100%;
	}

	.sentence-progress-panel {
		display: grid;
		gap: 12px;
	}

	.progress-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 8px;
	}

	.progress-actions {
		justify-content: space-between;
	}

	.panel-inline {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 10px;
		background: #101215;
	}

	.batch-main {
		min-width: 0;
	}

	@media (max-width: 1560px) {
		.cutting-head {
			grid-template-columns: minmax(260px, 360px) minmax(180px, 1fr);
		}

		.cutting-actions {
			grid-column: 1 / -1;
			justify-content: flex-end;
		}

		.single-shell {
			grid-template-columns: minmax(300px, 0.9fr) minmax(520px, 1.1fr);
		}

		.single-right {
			grid-column: 1 / -1;
			grid-template-columns: minmax(320px, 0.9fr) minmax(0, 1fr);
			display: grid;
			gap: 14px;
		}
	}

	@media (max-width: 1380px) {
		.cutting-shell,
		.cutting-shell.collapsed {
			grid-template-columns: 1fr;
		}

		.cutting-stage {
			border-right: 0;
			border-bottom: 1px solid var(--line);
		}

		.studio-command-strip {
			grid-template-columns: 1fr;
		}

		.inspector-rail {
			display: none;
		}
	}

	@media (max-width: 1100px) {
		.single-shell,
		.batch-shell,
		.single-right,
		.cutting-head {
			grid-template-columns: 1fr;
		}

		.inspector-rail {
			display: none;
		}
	}

	@media (max-width: 900px) {
		.localization-head {
			align-items: flex-start;
		}

		.head-actions {
			justify-content: flex-start;
		}

		.view-switcher {
			align-items: flex-start;
		}

		.subtitle-display-bar {
			grid-template-columns: 1fr;
		}
	}
</style>
