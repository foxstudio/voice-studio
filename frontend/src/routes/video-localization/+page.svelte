<script module lang="ts">
	import type {
		VideoLocalizationCue as ProtectedCue,
		VideoLocalizationDraft as ConflictDraft,
		VideoLocalizationQualityIssue as ProtectedQualityIssue
	} from '$lib/api/types';

	export type AsrEngineId = 'faster-whisper-turbo' | 'qwen3-asr-mlx' | 'mimo-v2.5-asr';
	export type InspectorSection = 'tasks' | 'subtitle' | 'dubbing';
	export const DEFAULT_ASR_ENGINE_ID: AsrEngineId = 'qwen3-asr-mlx';
	type ManualTimingCue = ProtectedCue & {
		manual_timing_revision?: number;
		manual_timing_review_status?: 'not_reviewed' | 'required' | 'confirmed';
		manual_timing_confirmed_revision?: number | null;
		manual_timing_confirmed_start_ms?: number | null;
		manual_timing_confirmed_end_ms?: number | null;
		manual_timing_confirmed_at?: string | null;
		manual_timing_confirmation_method?: 'auditioned' | null;
	};

	export function asrSelectionRequiresUploadConfirmation(engineId: AsrEngineId) {
		return engineId === 'mimo-v2.5-asr';
	}

	export function inspectorSectionOnProjectLoad(): InspectorSection {
		return 'tasks';
	}

	export function mergeDraftAfterConflict(
		latest: ConflictDraft,
		local: ConflictDraft,
		options: { deletedTimelineClipIds?: Iterable<string> } = {}
	): ConflictDraft {
		const latestLocalizationRevision = String(latest.localization_state?.created_at ?? '');
		const localLocalizationRevision = String(local.localization_state?.created_at ?? '');
		const preserveLatestLocalization = Boolean(
			(latestLocalizationRevision || localLocalizationRevision) &&
			latestLocalizationRevision !== localLocalizationRevision
		);
		const latestTranscriptionRevision = String(latest.transcription?.revision_id ?? '');
		const localTranscriptionRevision = String(local.transcription?.revision_id ?? '');
		const preserveLatestTranscription = Boolean(
			latestTranscriptionRevision && latestTranscriptionRevision !== localTranscriptionRevision
		);
		const latestCues = new Map(latest.cues.map((cue) => [cue.cue_id, cue]));
		const mergedCues = preserveLatestTranscription
			? latest.cues
			: local.cues.map((cue) => {
					const serverCue = latestCues.get(cue.cue_id);
					if (!serverCue) return cue;
					const merged = {
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
					if (!preserveLatestLocalization) return merged;
					return {
						...merged,
						zh_localized_subtitle_text: serverCue.zh_localized_subtitle_text,
						tts_recommended_text: serverCue.tts_recommended_text
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
		const deletedTimelineClipIds = new Set(options.deletedTimelineClipIds ?? []);
		for (const clip of latest.timeline_clips) {
			// A local delete is an explicit edit, not an absent stale value. Do not let a
			// concurrent task refresh silently put that clip back on the timeline.
			if (!localClips.has(clip.clip_id) && !deletedTimelineClipIds.has(clip.clip_id)) mergedClips.push(clip);
		}
		return {
			...latest,
			ui_state: local.ui_state,
			cues: mergedCues,
			localized_subtitles: preserveLatestLocalization ? latest.localized_subtitles : local.localized_subtitles,
			localization_state: preserveLatestLocalization ? latest.localization_state : local.localization_state,
			glossary: local.glossary,
			scene_context: local.scene_context,
			timeline_clips: mergedClips
		};
	}

	export function isDubbingInspectorSection(section: InspectorSection) {
		return section === 'dubbing';
	}

	export function cueHasCurrentManualTimingConfirmation(cue: ProtectedCue) {
		const reviewed = cue as ManualTimingCue;
		const explicitConfirmation =
			reviewed.manual_timing_review_status === 'confirmed' &&
			reviewed.manual_timing_confirmed_revision === (reviewed.manual_timing_revision ?? 0) &&
			reviewed.manual_timing_confirmed_start_ms === cue.start_ms &&
			reviewed.manual_timing_confirmed_end_ms === cue.end_ms &&
			reviewed.manual_timing_confirmation_method === 'auditioned' &&
			Boolean(reviewed.manual_timing_confirmed_at);
		return explicitConfirmation || cue.quality_flags.includes('manual_timing_verified');
	}

	export function qualityIssueAppliesToStage(
		issue: ProtectedQualityIssue,
		hasLocalizationWork: boolean,
		dubbingStageActive: boolean
	) {
		const code = issue.code;
		const isDubbingIssue =
			code.startsWith('TTS_') ||
			code.startsWith('REFERENCE_') ||
			code.startsWith('CUE_SPEAKER_') ||
			code === 'AUDIO_ROUTE_NEEDS_REVIEW' ||
			code === 'MIXED_SPEAKER_NEEDS_SPLIT';
		if (isDubbingIssue) return dubbingStageActive;

		const isLocalizationIssue = code.startsWith('ZH_') || code.startsWith('LOCALIZED_');
		if (isLocalizationIssue) return hasLocalizationWork || dubbingStageActive;
		return true;
	}

	export function protectCueManualEdit(
		previous: ProtectedCue,
		next: ProtectedCue,
		changedFields: { text?: boolean; timing?: boolean }
	): ProtectedCue {
		const textChanged = changedFields.text === true && previous.en_subtitle_text !== next.en_subtitle_text;
		const timingChanged = changedFields.timing === true && (previous.start_ms !== next.start_ms || previous.end_ms !== next.end_ms);
		if (!textChanged && !timingChanged) return next;

		const qualityFlags = [...(next.quality_flags ?? [])];
		if (textChanged) qualityFlags.push('manual_text_edit');
		if (textChanged || timingChanged) qualityFlags.push('protected_manual_edit');
		if (timingChanged) {
			qualityFlags.push('manual_timing_edit', 'timing_review_required');
		}
		const sanitizedFlags = qualityFlags.filter(
			(flag) => !timingChanged || flag !== 'manual_timing_verified'
		);
		const reviewed = previous as ManualTimingCue;

		return {
			...next,
			...(timingChanged
				? {
						timing_confidence: 'low' as const,
						manual_timing_revision: (reviewed.manual_timing_revision ?? 0) + 1,
						manual_timing_review_status: 'required' as const
					}
				: {}),
			quality_flags: [...new Set(sanitizedFlags)]
		};
	}
</script>

<script lang="ts">
	import { Api } from '$lib/api';
	import { ApiError } from '$lib/api/client';
	import { withoutSubtitleTrack } from './subtitle-track-clear';
	import type {
		GenerateRequest,
		HistoryItem,
		Project,
		VideoLocalizationCue,
		VideoLocalizationCueUpdate,
		VideoLocalizationDraft,
		VideoLocalizationGeneratedCandidate,
		VideoLocalizationOperation,
		VideoLocalizationReferenceClip,
		VideoLocalizationReferenceClipCreate,
		VideoLocalizationReferenceClipUpdate,
		VideoLocalizationSubtitleCue,
		VideoLocalizationTimelineClip,
		VideoLocalizationVoiceRecipe,
		VideoLocalizationSpeakerCreate
	} from '$lib/api/types';
	import {
		AudioLines,
		BookOpenText,
		Captions,
		Check,
		ChevronDown,
		Clapperboard,
		Download,
		Film,
		FolderOpen,
		FileUp,
		ListTodo,
		PanelRightClose,
		PanelRightOpen,
		Pencil,
		Trash2,
		X
	} from 'lucide-svelte';
	import { onMount } from 'svelte';
	import { selectionForPlaybackAtTime } from '$lib/audio/selection-playback';
	import { normalizeFrameRate, snapTimeToFrame } from './frame-timeline';
	import { downloadBlob, downloadText } from './downloads';
	import {
		buildGenerateRequest,
		isActiveOperation,
		summarizeVideoLocalizationError,
		operationStatusLabel,
		sourceAudioUrl,
		stemAudioUrl,
		sortOperations,
		suggestSpeakerSeed,
	} from './utils';
	import CuttingInspector from './CuttingInspector.svelte';
	import PreviewPanel from './PreviewPanel.svelte';
	import SubtitleWorkflowSettings from './SubtitleWorkflowSettings.svelte';
	import VideoCuttingTimeline from './VideoCuttingTimeline.svelte';
	import type { TimelineSelectionItem } from './timeline-context-menu';
	import { activityTaskAffectsTrack, activityTaskDisplayName, asrSubtitleActionLabel, operationActivityTask, pendingOperationActivityTask, type ActivityTask } from './activity-notice';
	import { resolveAsrOperationPreview } from './asr-operation-preview';
	import {
		extendSubtitleCuesAcrossShortGaps,
		resolveAudioTrackOrder,
		resolveSubtitlePreviewState,
		resolveTrackStates,
		MIN_SUBTITLE_DURATION_MS,
		subtitleCueDragBounds,
		type SubtitlePreviewSource,
		type SubtitlePreviewState,
		type VideoLocalizationAudioTrackOrder,
		type VideoLocalizationTrackId,
		type VideoLocalizationTrackState
	} from './studio-state';

	let projects = $state<Project[]>([]);
	let operations = $state<VideoLocalizationOperation[]>([]);
	let foregroundTasks = $state<ActivityTask[]>([]);
	let ttsHistory = $state<HistoryItem[]>([]);
	let projectId = $state('');
	let draft = $state<VideoLocalizationDraft | null>(null);
	let draftOnlyCueIds = $state<string[]>([]);
	let selectedCueId = $state('');
	let selectedLocalizedSubtitleId = $state('');
	let selectedTimelineAudioClipId = $state('');
	let timelineSelectionItems = $state<TimelineSelectionItem[]>([]);
	let loading = $state(true);
	let resetting = $state(false);
	let savingCue = $state(false);
	let confirmingCueTiming = $state(false);
	let creatingSpeaker = $state(false);
	let importing = $state(false);
	let openingProjectDirectory = $state(false);
	let editingProjectName = $state(false);
	let projectNameDraft = $state('');
	let projectNameSaving = $state(false);
	let projectMenuOpen = $state(false);
	let deliveryMenuOpen = $state(false);
	let projectMenuSyncing = $state(false);
	let extractingAudio = $state(false);
	let separatingStems = $state(false);
	let transcribingAsr = $state(false);
	let creatingReferences = $state(false);
	let submittingBatch = $state(false);
	let exportingLocalizedVideo = $state(false);
	let referenceUpdatingId = $state('');
	let candidateApplyingId = $state('');
	let historyApplyingResultId = $state('');
	let operationActionId = $state('');
	let inspectorCollapsed = $state(false);
	let inspectorWidth = $state(380);
	let inspectorSection = $state<InspectorSection>(inspectorSectionOnProjectLoad());
	let inspectorVoiceTab = $state<'library' | 'save-selection'>('library');
	let selectedVoiceId = $state('');
	let selectedRecipeId = $state('');
	let previewTimeMs = $state(0);
	let hoverPreviewTimeMs = $state<number | null>(null);
	let previewPlaying = $state(false);
	let timelineSelectionRange = $state<{ start_ms: number; end_ms: number } | null>(null);
	let activePlaybackLoopRange = $state<{ start_ms: number; end_ms: number } | null>(null);
	let audioSelectionRange = $state<{ start_ms: number; end_ms: number } | null>(null);
	let previewPlaybackController: { playPause: () => void; play: () => void; seek: (timeMs: number) => void; scrub: (timeMs: number) => void; endScrub: () => void } | null = null;
	let autoSaveStatus = $state<'idle' | 'dirty' | 'saving' | 'saved' | 'failed'>('idle');
	let autoSaveScope = $state<'ui' | 'draft' | null>(null);
	let pendingUiStatePatch = $state<Record<string, unknown>>({});
	let lastAutoSavedAt = $state('');
	type TimelineSnapshot = {
		clips: VideoLocalizationTimelineClip[];
		cues: VideoLocalizationCue[];
		localizedSubtitles: VideoLocalizationSubtitleCue[];
		disabledMediaTracks: string[];
	};
	let timelineUndoStack = $state<TimelineSnapshot[]>([]);
	let timelineRedoStack = $state<TimelineSnapshot[]>([]);
	let timelineDeletedClipIds = new Set<string>();
	let timelineEditRevision = 0;
	let timelineSavedRevision = 0;
	let cueSaveRevision = 0;
	let localizedSubtitleSaveRevision = 0;
	let videoInput: HTMLInputElement | null = null;
	let localizationSrtInput: HTMLInputElement | null = null;
	let operationPollingTimer: ReturnType<typeof setTimeout> | null = null;
	let operationPollingInFlight = false;
	let operationPollingGeneration = 0;
	let autoSaveTimer: ReturnType<typeof setTimeout> | null = null;
	let message = $state('');
	let error = $state('');
	let operationErrorId = $state('');
	let operationErrorMessage = $state('');
	let taskCenterPulseKey = $state(0);

	const selectedProject = $derived(projects.find((project) => project.project_id === projectId) ?? null);
	const hasImportedProject = $derived(Boolean(draft?.source_media.video_path || draft?.source_media.filename));
	const selectedCue = $derived(selectedCueId ? draft?.cues.find((cue) => cue.cue_id === selectedCueId) ?? null : null);
	const selectedLocalizedSubtitle = $derived(
		selectedLocalizedSubtitleId
			? draft?.localized_subtitles.find((cue) => cue.subtitle_id === selectedLocalizedSubtitleId) ?? null
			: null
	);
	const selectedTimelineAudioClip = $derived(
		selectedTimelineAudioClipId
			? draft?.timeline_clips.find((clip) => clip.clip_id === selectedTimelineAudioClipId) ?? null
			: null
	);
	const selectedLocalizedSubtitles = $derived.by(() => {
		if (!draft || timelineSelectionItems.length < 2 || timelineSelectionItems.some((item) => item.kind !== 'subtitle' || item.trackId !== 'localizedSubtitles')) return [];
		const selectedIds = new Set(timelineSelectionItems.map((item) => item.itemId));
		return draft.localized_subtitles.filter((item) => selectedIds.has(item.subtitle_id)).sort((left, right) => left.start_ms - right.start_ms);
	});
	const selectedLocalizedSubtitlesContiguous = $derived.by(() => {
		if (!draft || selectedLocalizedSubtitles.length < 2) return false;
		const orderedIds = draft.localized_subtitles.slice().sort((left, right) => left.start_ms - right.start_ms).map((item) => item.subtitle_id);
		const indexes = selectedLocalizedSubtitles.map((item) => orderedIds.indexOf(item.subtitle_id)).sort((left, right) => left - right);
		return indexes.every((value, index) => index === 0 || value === indexes[index - 1] + 1);
	});
	const displayedPreviewTimeMs = $derived(hoverPreviewTimeMs ?? previewTimeMs);
	const previewCue = $derived(
		draft?.cues.find((cue) => cue.start_ms !== null && cue.end_ms !== null && displayedPreviewTimeMs >= cue.start_ms && displayedPreviewTimeMs < cue.end_ms) ?? null
	);
	const localizationPreview = $derived.by((): VideoLocalizationSubtitleCue[] => {
		const operation = operations.find((item) => item.kind === 'localization_draft' && isActiveOperation(item));
		const raw = operation?.result_summary?.preview_cues;
		if (!Array.isArray(raw)) return [];
		return raw.flatMap((item, index) => {
			if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
			const cue = item as Record<string, unknown>;
			const startMs = Number(cue.start_ms);
			const endMs = Number(cue.end_ms);
			const text = typeof cue.text === 'string' ? cue.text.trim() : '';
			if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs || !text) return [];
			return [{
				subtitle_id: typeof cue.subtitle_id === 'string' ? cue.subtitle_id : `localized_preview_${index + 1}`,
				start_ms: Math.round(startMs),
				end_ms: Math.round(endMs),
				text,
				tts_text: typeof cue.tts_text === 'string' ? cue.tts_text : null,
				linked_cue_id: null,
				quality_flags: Array.isArray(cue.quality_flags) ? cue.quality_flags.map(String) : []
			}];
		});
	});
	const previewLocalizedSubtitle = $derived(
		(localizationPreview.length ? localizationPreview : (draft?.localized_subtitles ?? []))
			.find((cue) => displayedPreviewTimeMs >= cue.start_ms && displayedPreviewTimeMs < cue.end_ms) ?? null
	);
	const readyCount = $derived(draft?.cues.filter((cue) => cue.review_status === 'ready' || cue.review_status === 'locked').length ?? 0);
	const reviewCount = $derived(draft?.cues.filter((cue) => cue.review_status === 'needs_review').length ?? 0);
	const blockedCount = $derived(draft?.cues.filter((cue) => cue.review_status === 'blocked').length ?? 0);
	const lowTimingCount = $derived(
		draft?.cues.filter((cue) => cue.timing_confidence === 'low' && !cueHasCurrentManualTimingConfirmation(cue)).length ?? 0
	);
	const mediumTimingCount = $derived(draft?.cues.filter((cue) => cue.timing_confidence === 'medium').length ?? 0);
	const generatedCount = $derived(draft?.cues.filter((cue) => cue.tts_audio_path).length ?? 0);
	const transcription = $derived(draft?.transcription ?? null);
	const noticeText = $derived(error ? summarizeVideoLocalizationError(error) : message);
	const localizedCount = $derived(draft?.localized_subtitles?.length ?? 0);
	const hasActiveOperation = $derived(operations.some((operation) => isActiveOperation(operation)));
	const activityTasks = $derived([
		...foregroundTasks,
		...operations
			.filter((operation) => isActiveOperation(operation))
			.map((operation) => operationActivityTask(operation, operationActionId === operation.operation_id))
	]);
	const taskHistory = $derived([
		...foregroundTasks,
		...operations.slice(0, 40).map((operation) => operationActivityTask(operation, operationActionId === operation.operation_id))
	]);
	const asrPreview = $derived(resolveAsrOperationPreview(operations));
	const subtitleRuntimeBusy = $derived(activityTasks.some((task) => activityTaskAffectsTrack(task, 'subtitles')));
	const localizationRuntimeBusy = $derived(activityTasks.some((task) => activityTaskAffectsTrack(task, 'localizedSubtitles')));
	const latestOperation = $derived(operations.find((operation) => isActiveOperation(operation)) ?? operations[0] ?? null);
	const speakerSeed = $derived(suggestSpeakerSeed(draft?.speakers ?? []));
	const cueTimelineAudioSrc = $derived(stemAudioUrl(projectId, draft, 'vocals') || sourceAudioUrl(projectId, draft));
	const cueTimelineAudioLabel = $derived(draft?.stems.vocals_clean_path ? '分离后人声' : '源音轨');
	const cueTimelineDurationMs = $derived(draft?.source_media.duration_ms ?? null);
	const subtitlePreview = $derived(resolveSubtitlePreviewState(draft?.ui_state?.subtitle_preview));
	const subtitleWorkflowSettingsOpen = $derived(draft?.ui_state?.subtitle_workflow_settings_open === true);
	const trackStates = $derived(resolveTrackStates(draft?.ui_state?.track_states));
	const audioTrackOrder = $derived(resolveAudioTrackOrder(draft?.ui_state?.audio_track_order));
	const timelineZoom = $derived(clampNumber(draft?.ui_state?.timeline_zoom, 1, 1200, 1));
	const hoverScrubEnabled = $derived(draft?.ui_state?.timeline_hover_scrub_enabled !== false);
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

	function transcriptReviewLabel(status: NonNullable<VideoLocalizationDraft['transcription']>['review_status']) {
		return status === 'completed'
			? '语义校对完成'
			: status === 'partial'
				? '语义校对需复核'
				: status === 'failed'
					? '语义校对已降级'
					: status === 'skipped'
						? '语义校对已跳过'
						: '未启用语义校对';
	}

	function alignmentStageLabel(status: NonNullable<VideoLocalizationDraft['transcription']>['alignment_status']) {
		return status === 'completed'
			? '逐词对齐完成'
			: status === 'partial'
				? '部分逐词对齐'
				: status === 'failed'
					? '粗略时间待复核'
					: '尚未运行对齐';
	}

	function audioBoundaryStageLabel(status: NonNullable<VideoLocalizationDraft['transcription']>['audio_boundary_status']) {
		return status === 'completed'
			? '声学边界完成'
			: status === 'failed'
				? '声学边界已降级'
				: status === 'skipped'
					? '声学边界已跳过'
					: '声学边界未运行';
	}

	function boundaryReviewStageLabel(status: NonNullable<VideoLocalizationDraft['transcription']>['boundary_review_status']) {
		return status === 'completed'
			? '边界复核完成'
			: status === 'partial'
				? '边界复核部分完成'
				: status === 'failed'
					? '边界复核已降级'
					: status === 'skipped'
						? '边界复核已跳过'
						: '边界复核未配置';
	}

	onMount(() => {
		loadProjects();
		document.addEventListener('visibilitychange', handleOperationVisibilityChange);
		window.addEventListener('keydown', handlePageKeydown, true);
		return () => {
			document.removeEventListener('visibilitychange', handleOperationVisibilityChange);
			window.removeEventListener('keydown', handlePageKeydown, true);
			stopOperationPolling();
			if (autoSaveTimer) clearTimeout(autoSaveTimer);
		};
	});

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

	function isInspectorVoiceTab(value: string): value is 'library' | 'save-selection' {
		return value === 'library' || value === 'save-selection';
	}

	async function loadDraft(nextProjectId = projectId) {
		timelineUndoStack = [];
		timelineRedoStack = [];
		timelineSelectionItems = [];
		cueSaveRevision += 1;
		localizedSubtitleSaveRevision += 1;
		if (!nextProjectId) {
			draft = null;
			draftOnlyCueIds = [];
			ttsHistory = [];
			timelineDeletedClipIds = new Set();
			timelineEditRevision = 0;
			timelineSavedRevision = 0;
			return;
		}
		error = '';
		try {
			const loadedDraft = await Api.videoLocalizationDraft(nextProjectId);
			const editableDraft = withEditableMediaClips(loadedDraft);
			const addedMediaClips = editableDraft.timeline_clips.length > loadedDraft.timeline_clips.length;
			draft = editableDraft;
			timelineDeletedClipIds = new Set();
			timelineEditRevision = 0;
			timelineSavedRevision = 0;
			if (addedMediaClips) scheduleDraftAutosave();
			draftOnlyCueIds = [];
			operations = sortOperations(draft.operations ?? []);
			const savedCueId = typeof draft.ui_state?.selected_cue_id === 'string' ? draft.ui_state.selected_cue_id : '';
			const savedVoiceId = typeof draft.ui_state?.selected_reference_clip_id === 'string' ? draft.ui_state.selected_reference_clip_id : '';
			const savedRecipeId = typeof draft.ui_state?.selected_recipe_id === 'string' ? draft.ui_state.selected_recipe_id : '';
			const savedInspectorVoiceTab = typeof draft.ui_state?.inspector_voice_tab === 'string' ? draft.ui_state.inspector_voice_tab : '';
			selectedCueId = draft.cues.some((cue) => cue.cue_id === savedCueId) ? savedCueId : (draft.cues[0]?.cue_id ?? '');
			selectedVoiceId = draft.reference_clips.some((clip) => clip.reference_clip_id === savedVoiceId) ? savedVoiceId : (draft.reference_clips[0]?.reference_clip_id ?? '');
			selectedRecipeId = draft.voice_recipes.some((recipe) => recipe.recipe_id === savedRecipeId) ? savedRecipeId : (draft.voice_recipes.find((recipe) => recipe.reference_clip_id === selectedVoiceId)?.recipe_id ?? '');
			inspectorCollapsed = draft.ui_state?.sidebar_collapsed === true;
			inspectorWidth = clampNumber(draft.ui_state?.inspector_width, 320, 560, 380);
			inspectorSection = inspectorSectionOnProjectLoad();
			inspectorVoiceTab = isInspectorVoiceTab(savedInspectorVoiceTab) ? savedInspectorVoiceTab : 'library';
			previewTimeMs = clampNumber(draft.ui_state?.playhead_ms, 0, Number.MAX_SAFE_INTEGER, 0);
			autoSaveStatus = draft.updated_at ? 'saved' : 'idle';
			await loadOperations(nextProjectId);
			await loadTtsHistory(nextProjectId);
		} catch (e) {
			error = (e as Error).message || '加载草稿失败';
		}
	}

	async function loadTtsHistory(nextProjectId = projectId) {
		if (!nextProjectId) {
			ttsHistory = [];
			return;
		}
		try {
			const items = await Api.history({ limit: 500, project_id: nextProjectId, source: 'video_localization' });
			if (projectId !== nextProjectId) return;
			ttsHistory = items;
		} catch {
			if (projectId === nextProjectId) ttsHistory = [];
		}
	}

	async function loadOperations(nextProjectId = projectId) {
		if (!nextProjectId) {
			operations = [];
			stopOperationPolling();
			return;
		}
		try {
			const latest = sortOperations(await Api.videoLocalizationOperations(nextProjectId));
			if (projectId !== nextProjectId) return;
			operations = latest;
			if (operations.some((operation) => isActiveOperation(operation))) startOperationPolling();
			else stopOperationPolling();
		} catch {
			if (projectId === nextProjectId && operations.some((operation) => isActiveOperation(operation))) {
				startOperationPolling(3000);
			}
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
		selectedLocalizedSubtitleId = '';
		selectedVoiceId = '';
		selectedRecipeId = '';
		operations = [];
		ttsHistory = [];
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
		if (!(event.target as HTMLElement | null)?.closest('.delivery-menu')) deliveryMenuOpen = false;
	}

	function toggleDeliveryMenu(event: MouseEvent) {
		event.stopPropagation();
		deliveryMenuOpen = !deliveryMenuOpen;
		projectMenuOpen = false;
	}

	function handlePageKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			projectMenuOpen = false;
			deliveryMenuOpen = false;
			return;
		}
		if (event.code !== 'Space' || event.repeat || event.isComposing || event.metaKey || event.ctrlKey || event.altKey) return;
		const target = event.target as HTMLElement | null;
		if (target?.isContentEditable || target?.closest('input,textarea,select,[contenteditable="true"],[role="textbox"]')) return;
		event.preventDefault();
		event.stopPropagation();
		handleTimelineTransport('play-pause');
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
			inspectorCollapsed = false;
			inspectorSection = 'tasks';
			inspectorVoiceTab = 'library';
			taskCenterPulseKey += 1;
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

	async function restoreOriginalAudio() {
		if (!projectId || !draft) return;
		extractingAudio = true;
		error = '';
		try {
			const response = await fetch(`/api/projects/${projectId}/video-localization/source-media/audio`, {
				cache: 'no-store',
				headers: { Range: 'bytes=0-0' }
			});
			const disabledTracks = Array.isArray(draft.ui_state?.disabled_media_tracks)
				? draft.ui_state.disabled_media_tracks.map(String).filter((trackId) => trackId !== 'original')
				: [];
			if (response.ok) {
				const restoredDraft = withEditableMediaClips({
					...draft,
					timeline_clips: draft.timeline_clips.filter((clip) => clip.track_id !== 'original'),
					ui_state: { ...draft.ui_state, disabled_media_tracks: disabledTracks }
				});
				draft = await Api.saveVideoLocalizationDraft(projectId, restoredDraft);
				autoSaveStatus = 'saved';
				message = '原音轨已重新载入';
				setTimeout(() => (message = ''), 1800);
				return;
			}
			if (response.status !== 404) throw new Error(`检查原音频失败（HTTP ${response.status}）`);
			if (!draft.source_media.video_path) throw new Error('原音频和原视频文件都不可用，请重新导入视频');
			draft = {
				...draft,
				timeline_clips: draft.timeline_clips.filter((clip) => clip.track_id !== 'original'),
				ui_state: { ...draft.ui_state, disabled_media_tracks: disabledTracks }
			};
			draft = await Api.saveVideoLocalizationDraft(projectId, draft);
			await submitMediaOperation('source_audio', '原音频文件缺失，已开始从视频重新抽取');
		} catch (e) {
			error = (e as Error).message || '恢复原音轨失败';
		} finally {
			extractingAudio = false;
		}
	}

	async function transcribeEnglishSource(sourceTrackId: 'vocals' | 'dub' | 'original' = 'vocals') {
		const sourceReady = sourceTrackId === 'vocals'
			? Boolean(draft?.stems.vocals_clean_path)
			: sourceTrackId === 'dub'
				? Boolean(draft?.timeline_clips.some((clip) => clip.track_id === 'dub' && clip.audio_path))
				: Boolean(draft?.source_media.audio_path || draft?.stems.original_audio_path);
		if (!projectId || !sourceReady || operationBusy('english_asr')) return;
		if (!(await flushPendingAutosave())) {
			error = '存在未保存的字幕修改，请先处理保存错误后再开始听写。';
			return;
		}
		transcribingAsr = true;
		error = '';
		try {
			await submitMediaOperation('english_asr', '正在从人声轨生成 ASR 字幕', {
				engine_id: DEFAULT_ASR_ENGINE_ID,
				source_track_id: sourceTrackId,
				source_language: sourceTrackId === 'dub' ? 'zh' : (draft?.language_config?.source_language || 'auto')
			});
		} catch (e) {
			error = (e as Error).message || '提交字幕听写失败';
		} finally {
			transcribingAsr = false;
		}
	}

	async function generateAsrFromTimeline() {
		if (!draft) return;
		if (draft.cues.length) {
			const confirmed = window.confirm('重新生成会替换自动生成的 ASR 字幕片段，已保护的人工编辑会保留。是否继续？');
			if (!confirmed) return;
		}
		await transcribeEnglishSource('vocals');
	}

	async function generateLocalizationFromTimeline() {
		if (!projectId || !draft?.cues.length || operationBusy('localization_draft')) return;
		if (draft.localized_subtitles.length) {
			const confirmed = window.confirm('重新生成会替换当前本土化字幕初稿，上屏字幕和配音台词都会更新。是否继续？');
			if (!confirmed) return;
		}
		const pendingTaskId = beginPendingOperation('localization_draft', '正在保存修改并提交任务');
		error = '';
		message = '正在提交本土化字幕任务';
		try {
			if (!(await flushPendingAutosave())) {
				error = '存在未保存的字幕修改，请先处理保存错误后再生成本土化字幕。';
				return;
			}
			await submitMediaOperation('localization_draft', '本土化字幕初稿任务已开始', {
				source_language: draft.language_config?.detected_source_language || draft.transcription?.language || 'en',
				target_language: draft.language_config?.target_language || 'zh-Hans',
				localization_level: 'L1',
				worldview_permeability: 'W0'
			}, pendingTaskId);
		} catch (e) {
			error = (e as Error).message || '提交本土化字幕任务失败';
		} finally {
			endPendingOperation(pendingTaskId);
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

	function beginPendingOperation(kind: VideoLocalizationOperation['kind'], stage = '正在提交任务') {
		const taskId = `submit-operation:${projectId}:${kind}`;
		if (!foregroundTasks.some((task) => task.id === taskId)) {
			foregroundTasks = [...foregroundTasks, pendingOperationActivityTask(kind, taskId, stage)];
		}
		return taskId;
	}

	function endPendingOperation(taskId: string) {
		foregroundTasks = foregroundTasks.filter((task) => task.id !== taskId);
	}

	async function submitMediaOperation(
		kind: VideoLocalizationOperation['kind'],
		successMessage: string,
		parameters: Record<string, unknown> = {},
		pendingTaskId = ''
	) {
		if (!projectId) return;
		const taskId = pendingTaskId || beginPendingOperation(kind);
		try {
			const operation = await Api.submitVideoLocalizationOperation(projectId, kind, parameters);
			operations = sortOperations([operation, ...operations.filter((item) => item.operation_id !== operation.operation_id)]);
			message = successMessage;
			setTimeout(() => (message = ''), 1800);
			startOperationPolling(0);
			void refreshDraftOnly().catch((refreshError) => {
				error = (refreshError as Error).message || '任务已开始，但刷新项目状态失败，正在继续同步';
			});
		} finally {
			endPendingOperation(taskId);
		}
	}

	async function cancelOperation(operation: VideoLocalizationOperation) {
		if (!projectId || !isActiveOperation(operation)) return;
		operationActionId = operation.operation_id;
		error = '';
		try {
			const updated = await Api.cancelVideoLocalizationOperation(projectId, operation.operation_id);
			operations = sortOperations([updated, ...operations.filter((item) => item.operation_id !== updated.operation_id)]);
			message = updated.status === 'cancelled' ? '任务已取消' : '已请求取消，正在等待当前步骤安全结束';
			startOperationPolling();
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

	function filenameFromDisposition(value: string | null) {
		if (!value) return '';
		const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(value);
		if (utf8?.[1]) return decodeURIComponent(utf8[1]);
		const plain = /filename="?([^";]+)"?/i.exec(value);
		return plain?.[1] ?? '';
	}

	async function exportSubtitleSrt(kind: 'en' | 'zh' | 'bilingual') {
		if (!projectId) return;
		error = '';
		try {
			const response = await fetch(`/api/projects/${projectId}/video-localization/subtitles/${kind}`);
			if (!response.ok) {
				const data = await response.json().catch(() => null);
				throw new Error(data?.error?.message || '导出字幕失败');
			}
			const text = await response.text();
			downloadText(`${projectId}-video-localization-${kind}.srt`, text, 'application/x-subrip;charset=utf-8');
			message = kind === 'en' ? 'ASR 字幕已导出' : kind === 'zh' ? '本土化字幕已导出' : '双语字幕已导出';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导出字幕失败';
		}
	}

	function selectCue(cueId: string) {
		selectedTimelineAudioClipId = '';
		selectedLocalizedSubtitleId = '';
		selectedCueId = cueId;
		updateDraftUiState({ selected_cue_id: cueId });
		focusInspector('subtitle');
	}

	function selectLocalizedSubtitle(subtitleId: string) {
		selectedTimelineAudioClipId = '';
		selectedLocalizedSubtitleId = subtitleId;
		const subtitle = draft?.localized_subtitles.find((item) => item.subtitle_id === subtitleId);
		if (subtitle?.linked_cue_id) selectedCueId = subtitle.linked_cue_id;
		focusInspector('subtitle');
	}

	function selectTimelineAudioClip(clipId: string | null) {
		selectedTimelineAudioClipId = clipId ?? '';
		if (!clipId) return;
		const clip = draft?.timeline_clips.find((item) => item.clip_id === clipId);
		if (!clip || clip.track_id !== 'dub') return;
		focusInspector('dubbing');
		if (clip.subtitle_id) {
			selectedLocalizedSubtitleId = clip.subtitle_id;
			const subtitle = draft?.localized_subtitles.find((item) => item.subtitle_id === clip.subtitle_id);
			if (subtitle?.linked_cue_id) selectedCueId = subtitle.linked_cue_id;
			return;
		}
		if (clip.cue_id) {
			selectedCueId = clip.cue_id;
			selectedLocalizedSubtitleId = '';
		}
	}

	function jumpToTimingConfidence(confidence: 'low' | 'medium') {
		if (!draft) return;
		const matches = draft.cues
			.filter((cue) => cue.timing_confidence === confidence && cue.start_ms !== null)
			.sort((left, right) => (left.start_ms ?? 0) - (right.start_ms ?? 0));
		if (!matches.length) return;
		const selectedIndex = matches.findIndex((cue) => cue.cue_id === selectedCueId);
		const next = matches[(selectedIndex + 1 + matches.length) % matches.length];
		selectCue(next.cue_id);
		seekPreview(next.start_ms ?? 0);
	}

	function updateSelectedCue(patch: Partial<VideoLocalizationCue>) {
		if (!draft || !selectedCue || subtitleRuntimeBusy) return;
		draft.cues = draft.cues.map((cue) => {
			if (cue.cue_id !== selectedCue.cue_id) return cue;
			const normalizedCue = normalizeCueTimePatch({ ...cue, ...patch }, patch);
			return protectCueManualEdit(cue, normalizedCue, {
				text: 'en_subtitle_text' in patch,
				timing: 'start_ms' in patch || 'end_ms' in patch
			});
		});
		scheduleDraftAutosave();
	}

	async function updateCueTimeFromTimeline(cueId: string, startMs: number, endMs: number) {
		if (!projectId || !draft || subtitleRuntimeBusy) return;
		const normalizedStart = Math.max(0, Math.round(startMs));
		const normalizedEnd = Math.max(normalizedStart + MIN_SUBTITLE_DURATION_MS, Math.round(endMs));
		const targetCue = draft.cues.find((cue) => cue.cue_id === cueId);
		if (!targetCue) return;
		const previousCue = targetCue;
		const expectedProjectId = projectId;
		const saveRevision = ++cueSaveRevision;
		rememberTimelineClips();
		const protectedCue = protectCueManualEdit(
			targetCue,
			{
				...targetCue,
				start_ms: normalizedStart,
				end_ms: normalizedEnd,
				source_duration_ms: normalizedEnd - normalizedStart
			},
			{ timing: true }
		);
		draft.cues = draft.cues.map((cue) =>
			cue.cue_id === cueId ? protectedCue : cue
		);
		try {
				if (cueNeedsDraftSave(cueId)) await persistDraftSnapshot();
				const savedDraft = await Api.updateVideoLocalizationCue(expectedProjectId, cueId, {
					start_ms: protectedCue.start_ms,
					end_ms: protectedCue.end_ms
				});
			if (projectId !== expectedProjectId || saveRevision !== cueSaveRevision || !draft) return;
			const savedCue = savedDraft.cues.find((cue) => cue.cue_id === cueId);
			if (savedCue) draft = { ...draft, cues: draft.cues.map((cue) => cue.cue_id === cueId ? savedCue : cue) };
			selectedCueId = cueId;
			updateDraftUiState({ selected_cue_id: cueId });
			focusInspector('subtitle');
			autoSaveStatus = 'saved';
		} catch (e) {
			if (projectId !== expectedProjectId || saveRevision !== cueSaveRevision || !draft) return;
			draft = { ...draft, cues: draft.cues.map((cue) => cue.cue_id === cueId ? previousCue : cue) };
			error = (e as Error).message || 'ASR 字幕时间保存失败';
		}
	}

	async function updateLocalizedSubtitleTime(subtitleId: string, startMs: number, endMs: number) {
		if (!projectId || !draft || localizationRuntimeBusy) return;
		const target = draft.localized_subtitles.find((cue) => cue.subtitle_id === subtitleId);
		if (!target) return;
		const normalized = normalizeLocalizedSubtitleTiming(target, { start_ms: startMs, end_ms: endMs });
		const normalizedStart = normalized.start_ms;
		const normalizedEnd = normalized.end_ms;
		const previousSubtitle = target;
		const expectedProjectId = projectId;
		const saveRevision = ++localizedSubtitleSaveRevision;
		rememberTimelineClips();
		draft = {
			...draft,
			localized_subtitles: draft.localized_subtitles.map((cue) =>
				cue.subtitle_id === subtitleId ? { ...cue, start_ms: normalizedStart, end_ms: normalizedEnd } : cue
			)
		};
		try {
			const savedDraft = await Api.updateVideoLocalizationLocalizedSubtitle(expectedProjectId, subtitleId, {
				start_ms: normalizedStart,
				end_ms: normalizedEnd
			});
			if (projectId !== expectedProjectId || saveRevision !== localizedSubtitleSaveRevision || !draft) return;
			const savedSubtitle = savedDraft.localized_subtitles.find((cue) => cue.subtitle_id === subtitleId);
			if (savedSubtitle) draft = { ...draft, localized_subtitles: draft.localized_subtitles.map((cue) => cue.subtitle_id === subtitleId ? savedSubtitle : cue) };
			autoSaveStatus = 'saved';
		} catch (e) {
			if (projectId !== expectedProjectId || saveRevision !== localizedSubtitleSaveRevision || !draft) return;
			draft = { ...draft, localized_subtitles: draft.localized_subtitles.map((cue) => cue.subtitle_id === subtitleId ? previousSubtitle : cue) };
			error = (e as Error).message || '本土化字幕时间保存失败';
		}
	}

	function normalizeLocalizedSubtitleTiming(
		subtitle: VideoLocalizationSubtitleCue,
		patch: Partial<VideoLocalizationSubtitleCue>
	) {
		if (!draft) return subtitle;
		const timelineDurationMs = Math.max(draft.source_media.duration_ms ?? subtitle.end_ms, subtitle.end_ms, MIN_SUBTITLE_DURATION_MS);
		const bounds = subtitleCueDragBounds(
			draft.localized_subtitles.map((item) => ({ cue_id: item.subtitle_id, start_ms: item.start_ms, end_ms: item.end_ms })),
			subtitle.subtitle_id,
			timelineDurationMs
		);
		let startMs = Math.round(patch.start_ms ?? subtitle.start_ms);
		let endMs = Math.round(patch.end_ms ?? subtitle.end_ms);
		startMs = Math.max(bounds.minStartMs, Math.min(startMs, endMs - MIN_SUBTITLE_DURATION_MS));
		endMs = Math.max(startMs + MIN_SUBTITLE_DURATION_MS, Math.min(bounds.maxEndMs, endMs));
		return { ...subtitle, ...patch, start_ms: startMs, end_ms: endMs };
	}

	function previewSelectedLocalizedSubtitle(patch: Partial<VideoLocalizationSubtitleCue>) {
		if (!draft || !selectedLocalizedSubtitle || localizationRuntimeBusy) return;
		const normalized = normalizeLocalizedSubtitleTiming(selectedLocalizedSubtitle, patch);
		draft = {
			...draft,
			localized_subtitles: draft.localized_subtitles.map((cue) =>
				cue.subtitle_id === selectedLocalizedSubtitle.subtitle_id ? normalized : cue
			)
		};
	}

	async function updateSelectedLocalizedSubtitle(patch: Partial<VideoLocalizationSubtitleCue>) {
		if (!projectId || !draft || !selectedLocalizedSubtitle || localizationRuntimeBusy) return;
		const subtitleId = selectedLocalizedSubtitle.subtitle_id;
		const previousSubtitle = selectedLocalizedSubtitle;
		const expectedProjectId = projectId;
		const saveRevision = ++localizedSubtitleSaveRevision;
		rememberTimelineClips();
		const normalized = normalizeLocalizedSubtitleTiming(selectedLocalizedSubtitle, patch);
		draft = {
			...draft,
			localized_subtitles: draft.localized_subtitles.map((cue) =>
				cue.subtitle_id === subtitleId ? normalized : cue
			)
		};
		try {
			const savedDraft = await Api.updateVideoLocalizationLocalizedSubtitle(expectedProjectId, subtitleId, {
				...patch,
				...(('start_ms' in patch || 'end_ms' in patch) ? { start_ms: normalized.start_ms, end_ms: normalized.end_ms } : {})
			});
			if (projectId !== expectedProjectId || saveRevision !== localizedSubtitleSaveRevision || !draft) return;
			const savedSubtitle = savedDraft.localized_subtitles.find((cue) => cue.subtitle_id === subtitleId);
			if (savedSubtitle) draft = { ...draft, localized_subtitles: draft.localized_subtitles.map((cue) => cue.subtitle_id === subtitleId ? savedSubtitle : cue) };
			autoSaveStatus = 'saved';
		} catch (e) {
			if (projectId !== expectedProjectId || saveRevision !== localizedSubtitleSaveRevision || !draft) return;
			draft = { ...draft, localized_subtitles: draft.localized_subtitles.map((cue) => cue.subtitle_id === subtitleId ? previousSubtitle : cue) };
			error = (e as Error).message || '本土化字幕保存失败';
		}
	}

	function splitSelectedCue() {
		if (!draft || !selectedCue || selectedCue.start_ms === null || selectedCue.end_ms === null) return;
		const durationMs = selectedCue.end_ms - selectedCue.start_ms;
		if (durationMs < MIN_SUBTITLE_DURATION_MS * 2) {
			message = '当前字幕片段太短，无法拆分';
			setTimeout(() => (message = ''), 1600);
			return;
		}
		rememberTimelineClips();
		const splitAt = previewTimeMs > selectedCue.start_ms + MIN_SUBTITLE_DURATION_MS && previewTimeMs < selectedCue.end_ms - MIN_SUBTITLE_DURATION_MS
			? previewTimeMs
			: selectedCue.start_ms + Math.round(durationMs / 2);
		const split = cueSplitPoint(selectedCue, Math.round(splitAt));
		const splitMs = split.splitMs;
		const [firstEn, secondEn] = splitCueText(selectedCue.en_subtitle_text ?? '', split.ratio);
		const [firstZh, secondZh] = splitCueText(selectedCue.zh_localized_subtitle_text ?? '', split.ratio);
		const [firstTts, secondTts] = splitCueText(selectedCue.tts_recommended_text ?? '', split.ratio);
		const [firstRaw, secondRaw] = splitCueText(selectedCue.source_text_raw ?? '', split.ratio);
		const nextCue: VideoLocalizationCue = {
			...selectedCue,
			cue_id: nextCueId(draft),
			start_ms: splitMs,
			end_ms: selectedCue.end_ms,
			en_subtitle_text: secondEn,
			zh_localized_subtitle_text: secondZh,
			tts_recommended_text: secondTts,
			source_word_ids: split.secondWordIds,
			source_text_raw: secondRaw || null,
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
			source_word_ids: split.firstWordIds,
			source_text_raw: firstRaw || null,
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

	function nextLocalizedSubtitleId(currentDraft: VideoLocalizationDraft) {
		const used = new Set(currentDraft.localized_subtitles.map((subtitle) => subtitle.subtitle_id));
		let index = currentDraft.localized_subtitles.length + 1;
		while (used.has(`localized_${String(index).padStart(4, '0')}`)) index += 1;
		return `localized_${String(index).padStart(4, '0')}`;
	}

	function nextTimelineClipId(currentDraft: VideoLocalizationDraft, baseId: string) {
		const used = new Set(currentDraft.timeline_clips.map((clip) => clip.clip_id));
		let index = 2;
		let candidate = `${baseId}_part_${index}`;
		while (used.has(candidate)) {
			index += 1;
			candidate = `${baseId}_part_${index}`;
		}
		return candidate;
	}

	function splitLocalizedSubtitleFromTimeline(subtitleId: string, requestedSplitMs: number) {
		if (!draft || localizationRuntimeBusy) return;
		const currentDraft = draft;
		const subtitle = currentDraft.localized_subtitles.find((item) => item.subtitle_id === subtitleId);
		if (!subtitle) return;
		const frameRate = normalizeFrameRate(currentDraft.source_media.frame_rate);
		const splitMs = snapTimeToFrame(
			requestedSplitMs,
			frameRate,
			'nearest',
			subtitle.start_ms + MIN_SUBTITLE_DURATION_MS,
			subtitle.end_ms - MIN_SUBTITLE_DURATION_MS
		);
		if (splitMs <= subtitle.start_ms || splitMs >= subtitle.end_ms) return;
		const ratio = (splitMs - subtitle.start_ms) / Math.max(1, subtitle.end_ms - subtitle.start_ms);
		const [firstText, secondText] = splitCueText(subtitle.text, ratio);
		const [firstTts, secondTts] = splitCueText(subtitle.tts_text ?? '', ratio);
		const nextSubtitleId = nextLocalizedSubtitleId(currentDraft);
		const clearGeneratedResult = {
			tts_result_id: null,
			tts_audio_path: null,
			tts_batch_task_id: null,
			tts_batch_status: null,
			tts_batch_error: null,
			tts_attempted_at: null,
			generated_duration_ms: null
		};
		const firstSubtitle: VideoLocalizationSubtitleCue = {
			...subtitle,
			...clearGeneratedResult,
			end_ms: splitMs,
			text: firstText,
			tts_text: firstTts || null,
			quality_flags: [...new Set([...(subtitle.quality_flags ?? []), 'timeline_split'])]
		};
		const secondSubtitle: VideoLocalizationSubtitleCue = {
			...subtitle,
			...clearGeneratedResult,
			subtitle_id: nextSubtitleId,
			start_ms: splitMs,
			text: secondText,
			tts_text: secondTts || null,
			quality_flags: [...new Set([...(subtitle.quality_flags ?? []), 'timeline_split'])]
		};
		const clips = currentDraft.timeline_clips.flatMap((clip) => {
			if (clip.subtitle_id !== subtitleId) return [clip];
			const startMs = Math.round(clip.start_ms ?? subtitle.start_ms);
			const endMs = Math.max(startMs + 300, Math.round(clip.end_ms ?? subtitle.end_ms));
			if (splitMs <= startMs + 300 || splitMs >= endMs - 300) return [clip];
			const sourceStartMs = Math.max(0, Math.round(clip.source_start_ms ?? 0));
			const sourceEndMs = Math.max(sourceStartMs + 300, Math.round(clip.source_end_ms ?? sourceStartMs + endMs - startMs));
			const sourceSplitMs = sourceStartMs + (splitMs - startMs);
			return [
				{ ...clip, end_ms: splitMs, source_end_ms: sourceSplitMs },
				{
					...clip,
					clip_id: nextTimelineClipId(currentDraft, clip.clip_id),
					subtitle_id: nextSubtitleId,
					start_ms: splitMs,
					end_ms: endMs,
					source_start_ms: sourceSplitMs,
					source_end_ms: sourceEndMs
				}
			];
		});
		rememberTimelineClips();
		draft = {
			...draft,
			localized_subtitles: currentDraft.localized_subtitles.flatMap((item) =>
				item.subtitle_id === subtitleId ? [firstSubtitle, secondSubtitle] : [item]
			),
			timeline_clips: clips
		};
		selectedLocalizedSubtitleId = nextSubtitleId;
		scheduleDraftAutosave();
		message = '本土化字幕与关联音频已按当前帧拆分';
		setTimeout(() => (message = ''), 1800);
	}

	function splitTimelineClipFromTimeline(clipId: string, requestedSplitMs: number) {
		if (!draft) return;
		const currentDraft = draft;
		const clip = currentDraft.timeline_clips.find((item) => item.clip_id === clipId);
		if (!clip) return;
		const startMs = Math.max(0, Math.round(clip.start_ms ?? 0));
		const endMs = Math.max(startMs + 300, Math.round(clip.end_ms ?? startMs + 1800));
		const frameRate = normalizeFrameRate(currentDraft.source_media.frame_rate);
		const splitMs = snapTimeToFrame(requestedSplitMs, frameRate, 'nearest', startMs + 300, endMs - 300);
		if (splitMs <= startMs || splitMs >= endMs) return;
		const sourceStartMs = Math.max(0, Math.round(clip.source_start_ms ?? 0));
		const sourceEndMs = Math.max(sourceStartMs + 300, Math.round(clip.source_end_ms ?? sourceStartMs + endMs - startMs));
		const sourceSplitMs = sourceStartMs + (splitMs - startMs);
		rememberTimelineClips();
		draft = {
			...draft,
			timeline_clips: currentDraft.timeline_clips.flatMap((item) =>
				item.clip_id !== clipId
					? [item]
					: [
							{ ...item, end_ms: splitMs, source_end_ms: sourceSplitMs },
							{
								...item,
								clip_id: nextTimelineClipId(currentDraft, item.clip_id),
								start_ms: splitMs,
								end_ms: endMs,
								source_start_ms: sourceSplitMs,
								source_end_ms: sourceEndMs
							}
						]
			)
		};
		scheduleDraftAutosave();
		message = '音频片段已按当前帧拆分';
		setTimeout(() => (message = ''), 1800);
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
		rememberTimelineClips();
		const mergedCue: VideoLocalizationCue = {
			...selectedCue,
			end_ms: Math.max(selectedCue.end_ms ?? 0, nextCue.end_ms ?? selectedCue.end_ms ?? 0),
			en_subtitle_text: mergeCueText(selectedCue.en_subtitle_text, nextCue.en_subtitle_text),
			zh_localized_subtitle_text: mergeCueText(selectedCue.zh_localized_subtitle_text, nextCue.zh_localized_subtitle_text),
			tts_recommended_text: mergeCueText(selectedCue.tts_recommended_text, nextCue.tts_recommended_text),
			source_word_ids: [...new Set([...(selectedCue.source_word_ids ?? []), ...(nextCue.source_word_ids ?? [])])],
			source_text_raw: mergeCueText(selectedCue.source_text_raw, nextCue.source_text_raw) || null,
			transcription_revision_id:
				selectedCue.transcription_revision_id === nextCue.transcription_revision_id
					? selectedCue.transcription_revision_id
					: null,
			timing_confidence:
				selectedCue.timing_confidence === 'low' || nextCue.timing_confidence === 'low'
					? 'low'
					: selectedCue.timing_confidence === 'medium' || nextCue.timing_confidence === 'medium'
						? 'medium'
						: selectedCue.timing_confidence ?? nextCue.timing_confidence,
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
			clips: cloneTimelineData(draft?.timeline_clips ?? []),
			cues: cloneTimelineData(draft?.cues ?? []),
			localizedSubtitles: cloneTimelineData(draft?.localized_subtitles ?? []),
			disabledMediaTracks: Array.isArray(draft?.ui_state?.disabled_media_tracks) ? draft.ui_state.disabled_media_tracks.map(String) : []
		};
	}

	function cloneTimelineData<T>(value: T): T {
		return JSON.parse(JSON.stringify(value)) as T;
	}

	function applyTimelineSnapshot(snapshot: TimelineSnapshot) {
		if (!draft) return;
		draft = {
			...draft,
			timeline_clips: cloneTimelineData(snapshot.clips),
			cues: cloneTimelineData(snapshot.cues),
			localized_subtitles: cloneTimelineData(snapshot.localizedSubtitles),
			ui_state: { ...draft.ui_state, disabled_media_tracks: [...snapshot.disabledMediaTracks] }
		};
	}

	function updateTimelineClipFromTimeline(clipId: string, startMs: number, endMs: number, sourceStartMs: number, sourceEndMs: number | null) {
		if (!draft) return;
		rememberTimelineClips();
		timelineEditRevision += 1;
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
		const target = draft?.timeline_clips.find((clip) => clip.clip_id === clipId);
		if (!target) return;
		deleteTimelineItems([{ kind: 'audio', trackId: target.track_id as VideoLocalizationTrackId, itemId: clipId }]);
	}

	function deleteTimelineItems(items: TimelineSelectionItem[]) {
		if (!draft || !items.length) return;
		const uniqueItems = items.filter((item, index) => items.findIndex((candidate) =>
			candidate.kind === item.kind && candidate.trackId === item.trackId && candidate.itemId === item.itemId
		) === index);
		const asrIds = new Set(uniqueItems.filter((item) => item.kind === 'subtitle' && item.trackId === 'subtitles').map((item) => item.itemId));
		const localizedIds = new Set(uniqueItems.filter((item) => item.kind === 'subtitle' && item.trackId === 'localizedSubtitles').map((item) => item.itemId));
		const audioIds = new Set(uniqueItems.filter((item) => item.kind === 'audio').map((item) => item.itemId));
		const existingCount = draft.cues.filter((cue) => asrIds.has(cue.cue_id)).length
			+ draft.localized_subtitles.filter((cue) => localizedIds.has(cue.subtitle_id)).length
			+ draft.timeline_clips.filter((clip) => audioIds.has(clip.clip_id)).length;
		if (!existingCount) return;

		rememberTimelineClips();
		timelineEditRevision += 1;
		cueSaveRevision += asrIds.size ? 1 : 0;
		localizedSubtitleSaveRevision += localizedIds.size ? 1 : 0;
		for (const clipId of audioIds) timelineDeletedClipIds.add(clipId);
		const nextClips = draft.timeline_clips.filter((clip) => !audioIds.has(clip.clip_id));
		const affectedMediaTracks = new Set(
			draft.timeline_clips
				.filter((clip) => audioIds.has(clip.clip_id) && ['original', 'vocals', 'background'].includes(clip.track_id))
				.map((clip) => clip.track_id)
		);
		const disabledTracks = new Set(Array.isArray(draft.ui_state?.disabled_media_tracks) ? draft.ui_state.disabled_media_tracks.map(String) : []);
		for (const trackId of affectedMediaTracks) {
			if (!nextClips.some((clip) => clip.track_id === trackId)) disabledTracks.add(trackId);
		}
		const nextCues = draft.cues.filter((cue) => !asrIds.has(cue.cue_id));
		draft = {
			...draft,
			cues: nextCues,
			localized_subtitles: draft.localized_subtitles.filter((cue) => !localizedIds.has(cue.subtitle_id)),
			timeline_clips: nextClips,
			ui_state: { ...draft.ui_state, disabled_media_tracks: [...disabledTracks] }
		};
		draftOnlyCueIds = draftOnlyCueIds.filter((id) => !asrIds.has(id));
		if (asrIds.has(selectedCueId)) selectedCueId = nextCues[0]?.cue_id ?? '';
		if (localizedIds.has(selectedLocalizedSubtitleId)) selectedLocalizedSubtitleId = '';
		if (audioIds.has(selectedTimelineAudioClipId)) selectedTimelineAudioClipId = '';
		timelineSelectionItems = timelineSelectionItems.filter((item) => !uniqueItems.some((deleted) =>
			deleted.kind === item.kind && deleted.trackId === item.trackId && deleted.itemId === item.itemId
		));
		updateDraftUiState({ selected_cue_id: selectedCueId });
		scheduleDraftAutosave();
		message = existingCount > 1 ? `已删除所选的 ${existingCount} 个片段` : '片段已从时间线移除';
		setTimeout(() => (message = ''), 1600);
	}

	function undoTimelineClipEdit() {
		if (!draft || !timelineUndoStack.length) return;
		cueSaveRevision += 1;
		localizedSubtitleSaveRevision += 1;
		timelineEditRevision += 1;
		const [previous, ...rest] = timelineUndoStack;
		timelineUndoStack = rest;
		timelineRedoStack = [timelineSnapshot(), ...timelineRedoStack].slice(0, 30);
		applyTimelineSnapshot(previous);
		scheduleDraftAutosave();
	}

	function redoTimelineClipEdit() {
		if (!draft || !timelineRedoStack.length) return;
		cueSaveRevision += 1;
		localizedSubtitleSaveRevision += 1;
		timelineEditRevision += 1;
		const [next, ...rest] = timelineRedoStack;
		timelineRedoStack = rest;
		timelineUndoStack = [timelineSnapshot(), ...timelineUndoStack].slice(0, 30);
		applyTimelineSnapshot(next);
		scheduleDraftAutosave();
	}

	function clearCueSelection() {
		selectedTimelineAudioClipId = '';
		selectedCueId = '';
		selectedLocalizedSubtitleId = '';
		updateDraftUiState({ selected_cue_id: '' });
	}

	function deleteSubtitleItem(track: 'asr' | 'localized', itemId: string) {
		if (!draft || subtitleRuntimeBusy || localizationRuntimeBusy) return;
		rememberTimelineClips();
		if (track === 'localized') {
			if (!draft.localized_subtitles.some((cue) => cue.subtitle_id === itemId)) return;
			draft = { ...draft, localized_subtitles: draft.localized_subtitles.filter((cue) => cue.subtitle_id !== itemId) };
			if (selectedLocalizedSubtitleId === itemId) selectedLocalizedSubtitleId = '';
			scheduleDraftAutosave();
			message = '本土化字幕片段已删除';
			return;
		}
		const cueId = itemId;
		const index = draft.cues.findIndex((cue) => cue.cue_id === cueId);
		if (index < 0) return;
		const nextCues = draft.cues.filter((cue) => cue.cue_id !== cueId);
		draft = { ...draft, cues: nextCues };
		draftOnlyCueIds = draftOnlyCueIds.filter((id) => id !== cueId);
		selectedCueId = selectedCueId === cueId ? (nextCues[Math.min(index, nextCues.length - 1)]?.cue_id ?? '') : selectedCueId;
		scheduleDraftAutosave();
		updateDraftUiState({ selected_cue_id: selectedCueId });
		message = '字幕片段已删除';
	}

	function deleteSelectedCue() {
		if (!selectedCue) return;
		deleteSubtitleItem('asr', selectedCue.cue_id);
	}

	function fillSubtitleGaps(track: 'asr' | 'localized') {
		if (!draft || subtitleRuntimeBusy) return;
		if (track === 'asr') {
			const previous = draft.cues;
			const next = extendSubtitleCuesAcrossShortGaps(previous);
			const changed = next.filter((cue, index) => cue.end_ms !== previous[index]?.end_ms).length;
			if (!changed) {
				message = '没有需要延续的短停顿';
				return;
			}
			draft = { ...draft, cues: next.map((cue, index) => cue.end_ms === previous[index]?.end_ms ? cue : protectCueManualEdit(previous[index], cue, { timing: true })) };
			message = `已延续 ${changed} 处短停顿`;
		} else {
			const previous = draft.localized_subtitles;
			const next = extendSubtitleCuesAcrossShortGaps(previous);
			const changed = next.filter((cue, index) => cue.end_ms !== previous[index]?.end_ms).length;
			if (!changed) {
				message = '没有需要延续的短停顿';
				return;
			}
			draft = { ...draft, localized_subtitles: next };
			message = `已延续 ${changed} 处短停顿`;
		}
		scheduleDraftAutosave();
	}

	async function clearSubtitleTrack(track: 'asr' | 'localized') {
		if (!projectId || !draft) return;
		if (track === 'asr' && operationBusy('english_asr')) {
			error = 'ASR 字幕听写正在运行，完成或取消任务后才能清空字幕轨。';
			return;
		}
		if (track === 'localized' && operationBusy('localization_draft')) {
			error = '本土化字幕正在生成，完成或取消任务后才能清空字幕轨。';
			return;
		}
		const count = track === 'asr' ? draft.cues.length : draft.localized_subtitles.length;
		if (!count) return;
		const label = track === 'asr' ? 'ASR 字幕轨' : '本土化字幕轨';
		const confirmed = window.confirm(`确定删除${label}中的全部 ${count} 个字幕片段吗？另一条字幕轨不会被删除。`);
		if (!confirmed) return;

		const taskId = `clear-subtitles:${projectId}:${track}`;
		if (foregroundTasks.some((task) => task.id === taskId)) return;
		foregroundTasks = [...foregroundTasks, {
			id: taskId,
			label: `清空${label}`,
			stage: '正在完成后台清理',
			progress: null,
			status: 'running',
			scope: {
				trackIds: [track === 'asr' ? 'subtitles' : 'localizedSubtitles'],
				itemIds: [],
				area: 'subtitle',
				exclusive: true
			}
		}];
		error = '';
		const previousDraft = draft;
		const previousSelectedCueId = selectedCueId;
		const previousDraftOnlyCueIds = draftOnlyCueIds;
		if (autoSaveTimer) clearTimeout(autoSaveTimer);
		autoSaveTimer = null;
		const pendingSave = autoSaveStatus === 'dirty' || autoSaveStatus === 'failed' || autoSaveStatus === 'saving'
			? runDraftAutosave()
			: Promise.resolve();
		draft = withoutSubtitleTrack(draft, track);
		if (track === 'asr') {
			selectedCueId = '';
			draftOnlyCueIds = [];
		} else selectedLocalizedSubtitleId = '';
		message = `${label}已从时间线移除，正在后台清理`;
		try {
			await pendingSave;
			if (autoSaveStatus === 'failed') throw new Error('自动保存失败，字幕未删除');
			if (draft) draft = withoutSubtitleTrack(draft, track);
			draft = await Api.clearVideoLocalizationSubtitles(projectId, track === 'asr' ? 'en' : 'zh');
			if (track === 'asr') {
				selectedCueId = '';
				draftOnlyCueIds = [];
			} else selectedLocalizedSubtitleId = '';
			message = `${label}已清空`;
		} catch (e) {
			try {
				draft = await Api.videoLocalizationDraft(projectId);
			} catch {
				draft = previousDraft;
			}
			if (track === 'asr') {
				const restoredCueIds = new Set(draft?.cues.map((cue) => cue.cue_id) ?? []);
				selectedCueId = restoredCueIds.has(previousSelectedCueId) ? previousSelectedCueId : '';
				draftOnlyCueIds = previousDraftOnlyCueIds.filter((cueId) => restoredCueIds.has(cueId));
			}
			error = (e as Error).message || `${label}清空失败`;
		} finally {
			foregroundTasks = foregroundTasks.filter((task) => task.id !== taskId);
		}
	}

	function nextCueId(currentDraft: VideoLocalizationDraft) {
		const used = new Set(currentDraft.cues.map((cue) => cue.cue_id));
		let index = currentDraft.cues.length + 1;
		while (used.has(`cue_${String(index).padStart(4, '0')}`)) index += 1;
		return `cue_${String(index).padStart(4, '0')}`;
	}

	function splitCueText(value: string, ratio = 0.5) {
		const text = value.trim();
		if (!text) return ['', ''] as const;
		const words = text.split(/\s+/).filter(Boolean);
		if (words.length > 1) {
			const middle = Math.max(1, Math.min(words.length - 1, Math.round(words.length * ratio)));
			return [words.slice(0, middle).join(' '), words.slice(middle).join(' ')] as const;
		}
		const characters = Array.from(text);
		if (characters.length > 1) {
			const middle = Math.max(1, Math.min(characters.length - 1, Math.round(characters.length * ratio)));
			return [characters.slice(0, middle).join(''), characters.slice(middle).join('')] as const;
		}
		return [text, ''] as const;
	}

	function cueAlignedWords(cue: VideoLocalizationCue) {
		const ids = new Set(cue.source_word_ids ?? []);
		return (draft?.transcription?.words ?? [])
			.filter((word) => ids.has(word.word_id))
			.sort((left, right) => left.start_ms - right.start_ms || left.end_ms - right.end_ms);
	}

	function cueSplitPoint(cue: VideoLocalizationCue, preferredMs: number) {
		const words = cueAlignedWords(cue);
		if (words.length < 2 || cue.start_ms === null || cue.end_ms === null) {
			return { splitMs: preferredMs, firstWordIds: cue.source_word_ids ?? [], secondWordIds: [], ratio: 0.5 };
		}
		const boundaries = words.slice(0, -1).map((word, index) => ({
			index: index + 1,
			timeMs: Math.round((word.end_ms + words[index + 1].start_ms) / 2)
		}));
		const selected = boundaries.reduce((best, item) =>
			Math.abs(item.timeMs - preferredMs) < Math.abs(best.timeMs - preferredMs) ? item : best
		);
		return {
			splitMs: Math.max(cue.start_ms + MIN_SUBTITLE_DURATION_MS, Math.min(cue.end_ms - MIN_SUBTITLE_DURATION_MS, selected.timeMs)),
			firstWordIds: words.slice(0, selected.index).map((word) => word.word_id),
			secondWordIds: words.slice(selected.index).map((word) => word.word_id),
			ratio: selected.index / words.length
		};
	}

	function mergeCueText(first: string | null | undefined, second: string | null | undefined) {
		return [first?.trim(), second?.trim()].filter(Boolean).join('\n');
	}

	function normalizeCueTimePatch(cue: VideoLocalizationCue, patch: Partial<VideoLocalizationCue>) {
		if (!draft || cue.start_ms === null || cue.end_ms === null || (!('start_ms' in patch) && !('end_ms' in patch))) return cue;
		const timelineDurationMs = Math.max(draft.source_media.duration_ms ?? cue.end_ms, cue.end_ms, MIN_SUBTITLE_DURATION_MS);
		const bounds = subtitleCueDragBounds(draft.cues, cue.cue_id, timelineDurationMs);
		const minDurationMs = MIN_SUBTITLE_DURATION_MS;
		if ('start_ms' in patch) {
			const maxStart = Math.max(bounds.minStartMs, cue.end_ms - minDurationMs);
			cue.start_ms = Math.max(bounds.minStartMs, Math.min(maxStart, Math.round(cue.start_ms)));
		}
		if ('end_ms' in patch) {
			const minEnd = Math.min(bounds.maxEndMs, cue.start_ms + minDurationMs);
			cue.end_ms = Math.max(minEnd, Math.min(bounds.maxEndMs, Math.round(cue.end_ms)));
		}
		cue.source_duration_ms = Math.max(0, cue.end_ms - cue.start_ms);
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

	async function applyLocalizationSrt(text: string) {
		if (!projectId || !draft || localizationRuntimeBusy) return;
		error = '';
		try {
			draft = await Api.importVideoLocalizationSubtitles(projectId, 'zh', {
				srt_text: text,
				update_timing: true,
				overwrite_tts: false
			});
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			message = `已导入 ${draft.localized_subtitles.length} 条本土化字幕，ASR 字幕时间保持不变`;
			setTimeout(() => (message = ''), 2600);
		} catch (e) {
			error = (e as Error).message || '导入 SRT 失败';
		}
	}

	async function importLocalizationSrtFile(file: File | null | undefined) {
		if (!file) return;
		try {
			await applyLocalizationSrt(await file.text());
		} finally {
			if (localizationSrtInput) localizationSrtInput.value = '';
		}
	}

	function editableCuePatch(cue: VideoLocalizationCue): VideoLocalizationCueUpdate {
		return {
			speaker_id: cue.speaker_id,
			start_ms: cue.start_ms,
			end_ms: cue.end_ms,
			audio_route: cue.audio_route,
			en_subtitle_text: cue.en_subtitle_text,
			zh_localized_subtitle_text: cue.zh_localized_subtitle_text,
			tts_recommended_text: cue.tts_recommended_text,
			reference_clip_id: cue.reference_clip_id,
			review_status: cue.review_status,
			quality_flags: cue.quality_flags,
			notes: cue.notes
		};
	}

	async function saveSelectedCue() {
		if (!projectId || !selectedCue || subtitleRuntimeBusy) return;
		savingCue = true;
		error = '';
		const cueId = selectedCue.cue_id;
		const patch = editableCuePatch(selectedCue);
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

	async function confirmSelectedCueTiming() {
		if (!projectId || !selectedCue || selectedCue.start_ms === null || selectedCue.end_ms === null) return;
		confirmingCueTiming = true;
		error = '';
		const cueId = selectedCue.cue_id;
		try {
			if (cueNeedsDraftSave(cueId)) await persistDraftSnapshot();
			const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/video-localization/cues/${encodeURIComponent(cueId)}/timing-confirmation`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					start_ms: selectedCue.start_ms,
					end_ms: selectedCue.end_ms,
					confirmation_method: 'auditioned'
				})
			});
			const payload = await response.json();
			if (!response.ok) {
				const apiError = payload?.error ?? {};
				throw new ApiError(String(apiError.message ?? '确认时间码失败'), response.status, String(apiError.code ?? 'API_ERROR'));
			}
			draft = payload as VideoLocalizationDraft;
			selectedCueId = cueId;
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			message = '时间码已按人工试听确认';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '确认时间码失败';
		} finally {
			confirmingCueTiming = false;
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

	function groupedTimelineSegmentId(clip: VideoLocalizationTimelineClip | null | undefined) {
		if (!clip || clip.track_id !== 'dub') return '';
		if (clip.subtitle_id?.startsWith('group_')) return clip.subtitle_id;
		return clip.clip_id.startsWith('clip_group_') ? clip.clip_id.slice('clip_'.length) : '';
	}

	function selectedReferenceRecipe(reference: VideoLocalizationReferenceClip) {
		const recipes = draft?.voice_recipes.filter((recipe) => recipe.reference_clip_id === reference.reference_clip_id) ?? [];
		return recipes.find((recipe) => recipe.recipe_id === selectedRecipeId) ?? recipes[0] ?? null;
	}

	async function selectedSubtitleGenerateRequest() {
		const groupedSegmentId = groupedTimelineSegmentId(selectedTimelineAudioClip);
		const segmentId = selectedLocalizedSubtitle?.subtitle_id ?? selectedTimelineAudioClip?.cue_id ?? selectedCue?.cue_id;
		if (!projectId || !segmentId || !draft?.stems.vocals_clean_path) return null;
		const base = await Api.prepareVideoLocalizationTtsHandoff(projectId, segmentId);
		if (groupedSegmentId && selectedTimelineAudioClip) {
			const sourceCueIds = new Set(selectedTimelineAudioClip.source_cue_ids ?? []);
			const sourceText = draft.cues
				.filter((cue) => sourceCueIds.has(cue.cue_id))
				.sort((left, right) => (left.start_ms ?? 0) - (right.start_ms ?? 0))
				.map((cue) => cue.en_subtitle_text || cue.source_text_raw || '')
				.filter(Boolean)
				.join(' ');
			const candidate = draft.generated_candidates.find((item) => item.candidate_id === selectedTimelineAudioClip.candidate_id);
			return {
				...base,
				segment_id: groupedSegmentId,
				text: candidate?.text_used?.trim() || base.text,
				ref_text: sourceText || base.ref_text,
				custom_reference_trim_start_ms: selectedTimelineAudioClip.start_ms,
				custom_reference_trim_end_ms: selectedTimelineAudioClip.end_ms
			};
		}
		if (!selectedLocalizedSubtitlesContiguous || selectedLocalizedSubtitles.length < 2) return base;
		const first = selectedLocalizedSubtitles[0];
		const last = selectedLocalizedSubtitles.at(-1)!;
		const sourceCueIds = new Set(selectedLocalizedSubtitles.flatMap((item) => item.source_cue_ids ?? (item.linked_cue_id ? [item.linked_cue_id] : [])));
		const sourceText = draft.cues
			.filter((cue) => sourceCueIds.has(cue.cue_id))
			.sort((left, right) => (left.start_ms ?? 0) - (right.start_ms ?? 0))
			.map((cue) => cue.en_subtitle_text || cue.source_text_raw || '')
			.filter(Boolean)
			.join(' ');
		return {
			...base,
			segment_id: `group_${first.subtitle_id}_${last.subtitle_id}_${selectedLocalizedSubtitles.length}`,
			text: selectedLocalizedSubtitles.map((item) => item.tts_text?.trim() || item.text.trim()).filter(Boolean).join('\n'),
			ref_text: sourceText || base.ref_text,
			custom_reference_trim_start_ms: first.start_ms,
			custom_reference_trim_end_ms: last.end_ms
		};
	}

	function localizedSubtitleForRequest(request: GenerateRequest) {
		if (!request.segment_id) return null;
		return draft?.localized_subtitles.find((item) => item.subtitle_id === request.segment_id) ?? null;
	}

	async function openSelectedSubtitleInGenerate() {
		if (submittingBatch) return;
		submittingBatch = true;
		message = '正在准备当前人声片段…';
		let request: GenerateRequest | null = null;
		try {
			request = await selectedSubtitleGenerateRequest();
		} catch (e) {
			error = (e as Error).message || '准备语音合成素材失败';
			return;
		} finally {
			submittingBatch = false;
		}
		if (!request || !request.segment_id) return;
		const subtitle = localizedSubtitleForRequest(request);
		const handoffMeta = {
			source: 'video_localization',
			mode: 'tune_with_recipe',
			project_id: projectId,
			cue_id: request.segment_id,
			subtitle_id: subtitle?.subtitle_id ?? null,
			reference_clip_id: null,
			recipe_id: null,
			created_at: new Date().toISOString()
		};
		const params = new URLSearchParams({
			source: 'video_localization',
			mode: 'tune_with_recipe',
			project_id: projectId,
			cue_id: request.segment_id
		});
		sessionStorage.setItem('voice-studio-history-reuse', JSON.stringify(request));
		sessionStorage.setItem('voice-studio-video-localization-handoff', JSON.stringify(handoffMeta));
		window.location.href = `/generate?${params.toString()}`;
	}

	function requestWithHistoryParameters(base: GenerateRequest, history: HistoryItem): GenerateRequest {
		const snapshot = history.parameter_snapshot as Partial<GenerateRequest>;
		return {
			...base,
			...snapshot,
			text: base.text,
			source: base.source,
			project_id: base.project_id,
			segment_id: base.segment_id,
			voice_source: base.voice_source,
			reference_audio_path: base.reference_audio_path,
			reference_audio_license_status: base.reference_audio_license_status,
			reference_audio_tags: base.reference_audio_tags,
			ref_text: base.ref_text,
			custom_reference_source_audio_path: base.custom_reference_source_audio_path,
			custom_reference_source_duration_ms: base.custom_reference_source_duration_ms,
			custom_reference_trim_start_ms: base.custom_reference_trim_start_ms,
			custom_reference_trim_end_ms: base.custom_reference_trim_end_ms
		};
	}

	async function reuseSubtitleHistory(history: HistoryItem) {
		let base: GenerateRequest | null = null;
		try {
			message = '正在准备当前人声片段…';
			base = await selectedSubtitleGenerateRequest();
		} catch (e) {
			error = (e as Error).message || '准备语音合成素材失败';
			return;
		}
		if (!base || !draft || !base.segment_id) return;
		await submitSubtitleTts(requestWithHistoryParameters(base, history), `沿用 ${history.engine_id} 参数`);
	}

	async function applySubtitleHistoryToTimeline(history: HistoryItem) {
		if (!projectId || !draft || historyApplyingResultId) return;
		const segmentId = groupedTimelineSegmentId(selectedTimelineAudioClip) || selectedLocalizedSubtitle?.subtitle_id || selectedCue?.cue_id || '';
		if (!segmentId) {
			error = '请先选择要采用配音的字幕片段';
			return;
		}
		const selectedDubClip = selectedTimelineAudioClip?.track_id === 'dub' && groupedTimelineSegmentId(selectedTimelineAudioClip) === segmentId
			? selectedTimelineAudioClip
			: null;
		const clip = selectedDubClip ?? draft.timeline_clips.find((item) =>
				item.track_id === 'dub' && (item.subtitle_id === segmentId || (!selectedLocalizedSubtitle && item.cue_id === segmentId))
			);
		historyApplyingResultId = history.result_id;
		error = '';
		try {
			draft = await Api.applyVideoLocalizationHistoryToTimeline(projectId, history.result_id, {
				segment_id: segmentId,
				clip_id: clip?.clip_id ?? null
			});
			const adoptedClip = draft.timeline_clips.find((item) =>
				item.track_id === 'dub' && item.result_id === history.result_id && (item.subtitle_id === segmentId || item.cue_id === segmentId)
			);
			selectedTimelineAudioClipId = adoptedClip?.clip_id ?? clip?.clip_id ?? '';
			message = clip ? '已替换当前字幕的配音片段' : '已将历史声音添加到合成配音轨';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '采用历史声音失败';
		} finally {
			historyApplyingResultId = '';
		}
	}

	async function deleteSubtitleHistory(history: HistoryItem) {
		if (!window.confirm('删除这条配音记录吗？已经复制到时间线的音频不会受影响。')) return;
		try {
			await Api.deleteHistory(history.result_id);
			ttsHistory = ttsHistory.filter((item) => item.result_id !== history.result_id);
			message = '配音记录已删除';
		} catch (e) {
			await loadTtsHistory(projectId);
			error = (e as Error).message || '删除配音记录失败';
		}
	}

	async function deleteCurrentSubtitleHistory() {
		const segmentId = groupedTimelineSegmentId(selectedTimelineAudioClip) || selectedLocalizedSubtitle?.subtitle_id || selectedCue?.cue_id || '';
		const records = ttsHistory.filter((item) => (item.localized_subtitle_id || item.segment_id || item.cue_id || '') === segmentId);
		if (!records.length || !window.confirm(`删除当前字幕的 ${records.length} 条配音记录吗？已经复制到时间线的音频不会受影响。`)) return;
		try {
			await Promise.all(records.map((item) => Api.deleteHistory(item.result_id)));
			const removed = new Set(records.map((item) => item.result_id));
			ttsHistory = ttsHistory.filter((item) => !removed.has(item.result_id));
			message = `已删除 ${records.length} 条配音记录`;
		} catch (e) {
			await loadTtsHistory(projectId);
			error = (e as Error).message || '删除配音记录失败';
		}
	}

	async function deleteAllSubtitleHistory() {
		if (!ttsHistory.length || !window.confirm(`删除当前项目的全部 ${ttsHistory.length} 条配音记录吗？已经复制到时间线的音频不会受影响。`)) return;
		try {
			await Promise.all(ttsHistory.map((item) => Api.deleteHistory(item.result_id)));
			const removedCount = ttsHistory.length;
			ttsHistory = [];
			message = `已删除 ${removedCount} 条配音记录`;
		} catch (e) {
			await loadTtsHistory(projectId);
			error = (e as Error).message || '删除全部配音记录失败';
		}
	}

	async function submitSubtitleTts(request: GenerateRequest, actionLabel: string) {
		if (!draft || !request.segment_id || submittingBatch) return;
		submittingBatch = true;
		error = '';
		message = `${actionLabel}，正在提交…`;
		try {
			const task = await Api.generate(request);
			const groupedSubtitles = request.segment_id.startsWith('group_') && selectedLocalizedSubtitlesContiguous ? selectedLocalizedSubtitles : [];
			const subtitle = groupedSubtitles[0] ?? localizedSubtitleForRequest(request);
			const requestCue = draft.cues.find((item) => item.cue_id === request.segment_id) ?? null;
			const sourceCueIds = Array.from(new Set(
				groupedSubtitles.length
					? groupedSubtitles.flatMap((item) => item.source_cue_ids ?? (item.linked_cue_id ? [item.linked_cue_id] : []))
					: subtitle?.source_cue_ids ?? (subtitle?.linked_cue_id ? [subtitle.linked_cue_id] : requestCue?.cue_id ? [requestCue.cue_id] : [])
			));
			const sourceCueId = sourceCueIds[0] ?? null;
			const candidateId = candidateIdFor(task.task_id);
			const candidate: VideoLocalizationGeneratedCandidate = {
				candidate_id: candidateId,
				recipe_id: `history_${task.task_id}`,
				reference_clip_id: null,
				cue_id: sourceCueId,
				subtitle_id: groupedSubtitles.length ? request.segment_id : subtitle?.subtitle_id ?? null,
				audio_path: null,
				duration_ms: null,
				text_used: request.text,
				task_id: task.task_id,
				notes: actionLabel,
				status: task.status,
				created_at: new Date().toISOString()
			};
			const startMs = request.custom_reference_trim_start_ms ?? requestCue?.start_ms ?? 0;
			const endMs = request.custom_reference_trim_end_ms ?? requestCue?.end_ms ?? startMs + 1800;
			const timelineClip: VideoLocalizationTimelineClip = {
				clip_id: `clip_${request.segment_id}`,
				cue_id: sourceCueId,
				subtitle_id: groupedSubtitles.length ? request.segment_id : subtitle?.subtitle_id ?? null,
				source_cue_ids: sourceCueIds,
				candidate_id: candidateId,
				track_id: 'dub',
				start_ms: startMs,
				end_ms: endMs,
				source_start_ms: 0,
				source_end_ms: null,
				audio_path: null,
				status: 'queued'
			};
			const coveredSourceCueIds = new Set(sourceCueIds);
			draft = await Api.saveVideoLocalizationDraft(projectId, {
				...draft,
				generated_candidates: [candidate, ...draft.generated_candidates.filter((item) => item.candidate_id !== candidateId)],
				timeline_clips: [timelineClip, ...draft.timeline_clips.filter((item) => {
					if (item.clip_id === timelineClip.clip_id) return false;
					if (item.track_id !== 'dub' || !coveredSourceCueIds.size) return true;
					return !(item.source_cue_ids ?? []).some((cueId) => coveredSourceCueIds.has(cueId));
				})]
			});
			message = `${actionLabel}，任务已开始`;
			void monitorSubtitleTtsTask(task.task_id);
		} catch (e) {
			error = (e as Error).message || '提交配音生成失败';
		} finally {
			submittingBatch = false;
		}
	}

	async function monitorSubtitleTtsTask(taskId: string) {
		for (let attempt = 0; attempt < 900; attempt += 1) {
			await new Promise((resolve) => setTimeout(resolve, 1000));
			try {
				const task = await Api.task(taskId);
				if (!['success', 'failed', 'cancelled'].includes(task.status)) continue;
				await refreshDraftOnly();
				await loadTtsHistory();
				if (task.status === 'success') message = '配音已生成，并已放入合成配音轨';
				else error = task.error_message || '配音生成没有完成';
				return;
			} catch {
				if (attempt > 5) return;
			}
		}
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
		const refreshingProjectId = projectId;
		const loadedDraft = await Api.videoLocalizationDraft(refreshingProjectId);
		if (projectId !== refreshingProjectId) return;
		const editableDraft = withEditableMediaClips(loadedDraft);
		const addedMediaClips = editableDraft.timeline_clips.length > loadedDraft.timeline_clips.length;
		const hasUnsavedTimelineEdits = timelineEditRevision !== timelineSavedRevision;
		draft = draft && hasUnsavedTimelineEdits
			? mergeDraftAfterConflict(editableDraft, draft, { deletedTimelineClipIds: timelineDeletedClipIds })
			: editableDraft;
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
		inspectorCollapsed = false;
		inspectorSection = 'tasks';
		inspectorVoiceTab = 'library';
		autoSaveStatus = 'idle';
		stopOperationPolling();
	}

	function updateSelectedVoiceId(voiceId: string) {
		selectedVoiceId = voiceId;
		const nextRecipeId = draft?.voice_recipes.find((recipe) => recipe.reference_clip_id === voiceId)?.recipe_id ?? '';
		selectedRecipeId = nextRecipeId;
		updateDraftUiState({ selected_reference_clip_id: voiceId, selected_recipe_id: nextRecipeId });
		focusInspector('dubbing');
	}

	function updateSelectedRecipeId(recipeId: string) {
		selectedRecipeId = recipeId;
		updateDraftUiState({ selected_recipe_id: recipeId });
	}

	function updateSubtitlePreview(patch: Partial<SubtitlePreviewState>) {
		updateDraftUiState({ subtitle_preview: { ...subtitlePreview, ...patch } });
	}

	function autoSubtitleSources() {
		const hasLocalized = Boolean(previewLocalizedSubtitle?.text.trim());
		const hasAsr = Boolean(previewCue?.en_subtitle_text?.trim());
		return {
			asr: !hasLocalized && hasAsr,
			localized: hasLocalized
		};
	}

	function toggleSubtitleSource(source: SubtitlePreviewSource) {
		const current = subtitlePreview.sources ?? autoSubtitleSources();
		const currentlyVisible = subtitlePreview.enabled && current[source];
		updateSubtitlePreview({ enabled: true, sources: { ...current, [source]: !currentlyVisible } });
	}

	function updateTrackState(trackId: VideoLocalizationTrackId, patch: Partial<VideoLocalizationTrackState>) {
		updateDraftUiState({ track_states: { ...trackStates, [trackId]: { ...trackStates[trackId], ...patch } } });
	}

	function updateAudioTrackOrder(order: VideoLocalizationAudioTrackOrder) {
		updateDraftUiState({ audio_track_order: resolveAudioTrackOrder(order) });
	}

	function updateTimelineZoom(nextZoom: number) {
		updateDraftUiState({ timeline_zoom: clampNumber(nextZoom, 1, 1200, 1) });
	}

	function updateHoverScrubEnabled(enabled: boolean) {
		updateDraftUiState({ timeline_hover_scrub_enabled: enabled });
		if (!enabled) {
			hoverPreviewTimeMs = null;
			activePlaybackLoopRange = null;
			previewPlaybackController?.endScrub();
		}
	}

	function hoverScrubPreview(timeMs: number) {
		const boundedTimeMs = Math.max(0, Math.round(timeMs));
		hoverPreviewTimeMs = boundedTimeMs;
		previewPlaybackController?.scrub(boundedTimeMs);
	}

	function endHoverScrubPreview() {
		hoverPreviewTimeMs = null;
		previewPlaybackController?.endScrub();
	}

	function beginInspectorWidthResize(event: PointerEvent) {
		event.preventDefault();
		const startX = event.clientX;
		const startWidth = inspectorWidth;
		document.body.style.cursor = 'col-resize';
		document.body.style.userSelect = 'none';
		const move = (moveEvent: PointerEvent) => {
			inspectorWidth = clampNumber(startWidth + startX - moveEvent.clientX, 320, 560, startWidth);
		};
		const stop = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', stop);
			document.body.style.cursor = '';
			document.body.style.userSelect = '';
			updateDraftUiState({ inspector_width: inspectorWidth });
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', stop, { once: true });
	}

	function toggleInspectorCollapsed() {
		inspectorCollapsed = !inspectorCollapsed;
		updateDraftUiState({ sidebar_collapsed: inspectorCollapsed });
	}

	function focusInspector(section: InspectorSection, voiceTab: 'library' | 'save-selection' = inspectorVoiceTab) {
		inspectorCollapsed = false;
		inspectorSection = section;
		inspectorVoiceTab = voiceTab;
		updateDraftUiState({ sidebar_collapsed: false, inspector_voice_tab: voiceTab });
	}

	function openTaskCenter() {
		focusInspector('tasks');
		taskCenterPulseKey += 1;
	}

	async function cancelActivityTask(task: ActivityTask) {
		if (!task.operationId) return;
		const operation = operations.find((item) => item.operation_id === task.operationId);
		if (operation) await cancelOperation(operation);
	}

	async function retryActivityTask(task: ActivityTask) {
		if (!task.operationId || (task.status !== 'failed' && task.status !== 'cancelled')) return;
		const operation = operations.find((item) => item.operation_id === task.operationId);
		if (operation) await retryOperation(operation);
	}

	function focusSaveSelectionAsVoice(startMs: number, endMs: number) {
		audioSelectionRange = { start_ms: startMs, end_ms: endMs };
		focusInspector('dubbing', 'save-selection');
	}

	function focusGenerateToSelection(startMs: number, endMs: number) {
		audioSelectionRange = { start_ms: startMs, end_ms: endMs };
		focusInspector('dubbing');
	}

	function updatePreviewTime(timeMs: number) {
		previewTimeMs = timeMs;
	}

	function updatePreviewPlaying(playing: boolean) {
		previewPlaying = playing;
		if (playing) hoverPreviewTimeMs = null;
	}

	function updateTimelineSelectionRange(range: { startMs: number; endMs: number } | null) {
		timelineSelectionRange = range ? { start_ms: range.startMs, end_ms: range.endMs } : null;
		if (!range) {
			activePlaybackLoopRange = null;
			return;
		}
		if (activePlaybackLoopRange) activePlaybackLoopRange = { start_ms: range.startMs, end_ms: range.endMs };
	}

	function playCommittedTimelineSelection(range: { startMs: number; endMs: number }) {
		if (!hoverScrubEnabled) return;
		hoverPreviewTimeMs = null;
		previewPlaybackController?.endScrub();
		activePlaybackLoopRange = { start_ms: range.startMs, end_ms: range.endMs };
		previewTimeMs = range.startMs;
		previewPlaybackController?.seek(range.startMs);
		previewPlaybackController?.play();
	}

	function seekPreview(timeMs: number) {
		const boundedTimeMs = Math.max(0, Math.round(timeMs));
		previewTimeMs = boundedTimeMs;
		previewPlaybackController?.seek(boundedTimeMs);
	}

	function handleTimelineTransport(action: 'start' | 'play-pause' | 'next') {
		if (action === 'play-pause') {
			if (!previewPlaying) {
				const range = timelineSelectionRange;
				const selected = range
					? selectionForPlaybackAtTime(previewTimeMs, range.start_ms, range.end_ms)
					: null;
				activePlaybackLoopRange = selected ? { start_ms: selected.start, end_ms: selected.end } : null;
			}
			previewPlaybackController?.playPause();
			return;
		}
		if (action === 'start') {
			activePlaybackLoopRange = null;
			seekPreview(0);
			return;
		}
		const nextCue = (draft?.cues ?? [])
			.filter((cue) => cue.start_ms !== null && cue.start_ms > previewTimeMs + 80)
			.sort((a, b) => (a.start_ms ?? 0) - (b.start_ms ?? 0))[0];
		const nextTimeMs = nextCue?.start_ms ?? draft?.source_media.duration_ms ?? 0;
		activePlaybackLoopRange = null;
		seekPreview(nextTimeMs);
		if (nextCue) selectCue(nextCue.cue_id);
	}

	function seekTimeline(timeMs: number) {
		if (
			activePlaybackLoopRange &&
			!selectionForPlaybackAtTime(timeMs, activePlaybackLoopRange.start_ms, activePlaybackLoopRange.end_ms)
		) activePlaybackLoopRange = null;
		seekPreview(timeMs);
	}

	function updateDraftUiState(patch: Record<string, unknown>) {
		if (!draft) return;
		draft = { ...draft, ui_state: { ...(draft.ui_state ?? {}), ...patch } };
		pendingUiStatePatch = { ...pendingUiStatePatch, ...patch };
		scheduleDraftAutosave('ui');
	}

	function updateSubtitleWorkflowSettings(patch: Partial<Pick<VideoLocalizationDraft, 'glossary' | 'scene_context'>>) {
		if (!draft) return;
		draft = { ...draft, ...patch };
		scheduleDraftAutosave();
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
		const savingTimelineRevision = timelineEditRevision;
		const savingDeletedTimelineClipIds = new Set(timelineDeletedClipIds);
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
					savedDraft = await Api.saveVideoLocalizationDraft(
						savingProjectId,
						mergeDraftAfterConflict(latest, savingDraft, { deletedTimelineClipIds: savingDeletedTimelineClipIds })
					);
				}
			}
			if (projectId === savingProjectId) {
				draft = draft === savingDraft || !draft
					? savedDraft
						: mergeDraftAfterConflict(savedDraft, draft, { deletedTimelineClipIds: timelineDeletedClipIds });
			}
			if (timelineEditRevision === savingTimelineRevision) {
				timelineSavedRevision = savingTimelineRevision;
				timelineDeletedClipIds = new Set();
			}
			draftOnlyCueIds = [];
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
		} catch (e) {
			autoSaveStatus = 'failed';
			error = (e as Error).message || '自动保存失败';
		}
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
					currentDraft.localized_subtitles.length ||
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

	function startOperationPolling(delayMs = 1500) {
		if (!projectId || operationPollingTimer || document.visibilityState === 'hidden') return;
		const expectedProjectId = projectId;
		const expectedGeneration = operationPollingGeneration;
		operationPollingTimer = setTimeout(() => {
			operationPollingTimer = null;
			void pollOperations(expectedProjectId, expectedGeneration);
		}, delayMs);
	}

	function stopOperationPolling() {
		operationPollingGeneration += 1;
		if (operationPollingTimer) clearTimeout(operationPollingTimer);
		operationPollingTimer = null;
	}

	function handleOperationVisibilityChange() {
		if (document.visibilityState === 'hidden') {
			if (operationPollingTimer) clearTimeout(operationPollingTimer);
			operationPollingTimer = null;
			return;
		}
		if (operations.some((operation) => isActiveOperation(operation))) startOperationPolling(0);
	}

	async function pollOperations(expectedProjectId = projectId, expectedGeneration = operationPollingGeneration) {
		if (!expectedProjectId || expectedProjectId !== projectId || expectedGeneration !== operationPollingGeneration) return;
		if (operationPollingInFlight) {
			startOperationPolling(250);
			return;
		}
		operationPollingInFlight = true;
		let retryDelay = 1500;
		let shouldContinue = false;
		try {
			const previousById = new Map(operations.map((operation) => [operation.operation_id, operation]));
			const summaries = await Api.videoLocalizationOperationSummaries(expectedProjectId);
			const latest = sortOperations(summaries.map((summary) => {
				const previous = previousById.get(summary.operation_id);
				if (!previous) return summary;
				return {
					...previous,
					...summary,
					result_summary: { ...previous.result_summary, ...summary.result_summary }
				};
			}));
			if (expectedProjectId !== projectId || expectedGeneration !== operationPollingGeneration) return;
			const terminalTransition = latest.find((operation) => {
				const previous = previousById.get(operation.operation_id);
				return Boolean(previous && isActiveOperation(previous) && !isActiveOperation(operation));
			});
			operations = latest;
			if (terminalTransition) await refreshDraftOnly();
			if (expectedProjectId !== projectId || expectedGeneration !== operationPollingGeneration) return;
			shouldContinue = latest.some((operation) => isActiveOperation(operation));
			if (!shouldContinue) stopOperationPolling();
			if (terminalTransition?.status === 'failed' && terminalTransition.error_message) {
				operationErrorId = terminalTransition.operation_id;
				operationErrorMessage = terminalTransition.error_message;
				error = terminalTransition.error_message;
			} else if (terminalTransition?.status === 'success') {
				message = `${terminalTransition.label || '后台任务'}已完成`;
			} else if (terminalTransition?.status === 'cancelled') {
				message = `${terminalTransition.label || '后台任务'}已取消`;
			} else if (operationErrorId && !latest.some((operation) => operation.operation_id === operationErrorId && operation.status === 'failed')) {
				if (error === operationErrorMessage) error = '';
				operationErrorId = '';
				operationErrorMessage = '';
			}
		} catch (e) {
			if (expectedProjectId !== projectId || expectedGeneration !== operationPollingGeneration) return;
			error = (e as Error).message || '刷新任务状态失败，正在重试';
			shouldContinue = true;
			retryDelay = 3000;
		} finally {
			operationPollingInFlight = false;
			if (
				shouldContinue &&
				expectedProjectId === projectId &&
				expectedGeneration === operationPollingGeneration
			) {
				startOperationPolling(retryDelay);
			}
		}
	}

</script>

<svelte:window onpointerdown={closeProjectMenuFromPage} />

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
			<input bind:this={localizationSrtInput} data-video-localization-srt-file class="visually-hidden" type="file" accept=".srt,application/x-subrip,text/plain" onchange={(event) => importLocalizationSrtFile(event.currentTarget.files?.[0])} />
			<button
				class="icon-action"
				class:active={subtitleWorkflowSettingsOpen}
				type="button"
				disabled={!draft}
				aria-label="字幕规则与术语"
				aria-expanded={subtitleWorkflowSettingsOpen}
				data-tooltip="字幕规则与术语：维护场景上下文、原词校正和本土化术语。"
				onclick={() => updateDraftUiState({ subtitle_workflow_settings_open: !subtitleWorkflowSettingsOpen })}
			>
				<BookOpenText size={15} />
			</button>
			<div class="delivery-menu">
				<button class="icon-action" class:active={deliveryMenuOpen} type="button" disabled={!draft} aria-label="导出" aria-haspopup="menu" aria-expanded={deliveryMenuOpen} data-tooltip="导出：下载字幕或在配音完成后输出视频。" onclick={toggleDeliveryMenu}>
					<Download size={15} />
				</button>
				{#if deliveryMenuOpen}
					<div class="delivery-popover" role="menu" aria-label="导出内容">
						<button class="delivery-menu-item" role="menuitem" type="button" onclick={() => { deliveryMenuOpen = false; void exportSubtitleSrt('en'); }} disabled={!draft?.cues.length}><Download size={14} /><span><strong>ASR 字幕</strong><small>原文字幕 SRT</small></span></button>
						<button class="delivery-menu-item" role="menuitem" type="button" onclick={() => { deliveryMenuOpen = false; void exportSubtitleSrt('zh'); }} disabled={!localizedCount}><Download size={14} /><span><strong>本土化字幕</strong><small>中文上屏字幕 SRT</small></span></button>
						<button class="delivery-menu-item" role="menuitem" type="button" onclick={() => { deliveryMenuOpen = false; void exportSubtitleSrt('bilingual'); }} disabled={!draft?.cues.length || !localizedCount}><Download size={14} /><span><strong>双语字幕</strong><small>原文与本土化字幕 SRT</small></span></button>
						<div class="delivery-menu-separator"></div>
						<button class="delivery-menu-item" role="menuitem" type="button" onclick={() => { deliveryMenuOpen = false; void exportLocalizedVideo(); }} disabled={!draft?.source_media.video_path || !generatedCount || exportingLocalizedVideo}><Film size={14} /><span><strong>{exportingLocalizedVideo ? '正在导出视频' : '本土化视频'}</strong><small>使用当前时间线和合成配音</small></span></button>
					</div>
				{/if}
			</div>
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
			<button class="icon-action" type="button" onclick={toggleInspectorCollapsed} data-tooltip={inspectorCollapsed ? '展开侧栏：显示任务、字幕与配音检查器。' : '收起侧栏：为视频和时间线释放更多空间。'} aria-label={inspectorCollapsed ? '展开侧栏' : '收起侧栏'}>
				{#if inspectorCollapsed}
					<PanelRightOpen size={16} />
				{:else}
					<PanelRightClose size={16} />
				{/if}
			</button>
		</div>
	</header>

	<section class="cutting-shell" class:collapsed={inspectorCollapsed} style={`--inspector-width:${inspectorWidth}px`}>
		<section class="cutting-stage">
			<PreviewPanel
				asrCue={previewCue}
				localizedSubtitle={previewLocalizedSubtitle}
				{draft}
				{projectId}
				{importing}
				{subtitlePreview}
				{trackStates}
				playbackLoopRange={activePlaybackLoopRange}
				onRequestImport={() => videoInput?.click()}
				onImportFile={importVideoFile}
				onVideoTimeUpdate={updatePreviewTime}
				onPlaybackStateChange={updatePreviewPlaying}
				onControllerReady={(controller) => (previewPlaybackController = controller)}
			/>
			<SubtitleWorkflowSettings
				open={subtitleWorkflowSettingsOpen}
				glossary={draft?.glossary ?? []}
				sceneContext={draft?.scene_context ?? ''}
				onChange={updateSubtitleWorkflowSettings}
			/>
			<VideoCuttingTimeline
				{projectId}
				{draft}
				{selectedCueId}
				currentTimeMs={previewTimeMs}
				isPlaying={previewPlaying}
				{latestOperation}
				extractingAudio={extractingAudio || operationBusy('source_audio')}
				separatingStems={separatingStems || operationBusy('stems')}
				noticeKind={error ? 'error' : message ? 'success' : 'idle'}
				noticeSummary={noticeText}
				noticeDetail={error}
				{activityTasks}
				{asrPreview}
				onOpenTaskCenter={openTaskCenter}
				asrBusy={transcribingAsr || operationBusy('english_asr')}
				{trackStates}
				{audioTrackOrder}
				{timelineZoom}
				subtitlePreview={{ ...subtitlePreview, sources: subtitlePreview.sources ?? autoSubtitleSources() }}
				onSelectCue={selectCue}
				onSelectAudioClip={selectTimelineAudioClip}
				onTimelineSelectionChange={(items) => (timelineSelectionItems = items)}
				onClearCueSelection={clearCueSelection}
				onExtractAudio={extractSourceAudio}
				onRestoreOriginalAudio={restoreOriginalAudio}
				onSeparateStems={separateStems}
				onImportLocalizedSrt={() => localizationSrtInput?.click()}
				onGenerateLocalization={generateLocalizationFromTimeline}
				onTransportAction={handleTimelineTransport}
				onTrackStateChange={updateTrackState}
				onAudioTrackOrderChange={updateAudioTrackOrder}
				onTimelineZoomChange={updateTimelineZoom}
				onToggleSubtitleSource={toggleSubtitleSource}
				onSeekTimeline={seekTimeline}
				onSelectionRangeChange={updateTimelineSelectionRange}
				onSelectionRangeCommit={playCommittedTimelineSelection}
				onUpdateCueTime={updateCueTimeFromTimeline}
				onUpdateLocalizedSubtitleTime={updateLocalizedSubtitleTime}
				onClearSubtitleTrack={clearSubtitleTrack}
				onDeleteSubtitleItem={deleteSubtitleItem}
				onFillSubtitleGaps={fillSubtitleGaps}
				onGenerateAsr={generateAsrFromTimeline}
				onSelectLocalizedSubtitle={selectLocalizedSubtitle}
				localizationBusy={localizationRuntimeBusy}
				{localizationPreview}
				onSplitCue={splitSelectedCue}
				onSplitLocalizedSubtitle={splitLocalizedSubtitleFromTimeline}
				onSplitTimelineClip={splitTimelineClipFromTimeline}
				onMergeCue={mergeSelectedCueWithNext}
				onDeleteCue={deleteSelectedCue}
				onSaveSelectionAsVoice={focusSaveSelectionAsVoice}
				onGenerateToSelection={focusGenerateToSelection}
				onUpdateTimelineClip={updateTimelineClipFromTimeline}
				onDeleteTimelineClip={deleteTimelineClip}
				onDeleteTimelineItems={deleteTimelineItems}
				hoverScrubEnabled={hoverScrubEnabled}
				onHoverScrubChange={updateHoverScrubEnabled}
				onHoverScrub={hoverScrubPreview}
				onHoverScrubEnd={endHoverScrubPreview}
				onUndoTimelineClip={undoTimelineClipEdit}
				onRedoTimelineClip={redoTimelineClipEdit}
				canUndoTimeline={timelineUndoStack.length > 0}
				canRedoTimeline={timelineRedoStack.length > 0}
				undoTimelineCount={timelineUndoStack.length}
				redoTimelineCount={timelineRedoStack.length}
			/>
		</section>

		{#if !inspectorCollapsed}
			<button class="inspector-resize-handle" type="button" aria-label="调整侧边栏宽度" data-tooltip="调整侧边栏宽度｜左右拖动分隔线。" onpointerdown={beginInspectorWidthResize}></button>
			<CuttingInspector
				{draft}
				{projectId}
				{selectedCue}
				{selectedLocalizedSubtitle}
				{selectedLocalizedSubtitles}
				selectedLocalizedSubtitlesContiguous={selectedLocalizedSubtitlesContiguous}
				selectedTimelineAudioClip={selectedTimelineAudioClip}
				selectionRange={audioSelectionRange}
				{selectedVoiceId}
				{selectedRecipeId}
				{inspectorSection}
				{inspectorVoiceTab}
				{subtitlePreview}
				onSelectedVoiceIdChange={updateSelectedVoiceId}
				onSectionChange={(section) => focusInspector(section)}
				onUpdateCue={updateSelectedCue}
				onPreviewLocalizedSubtitle={previewSelectedLocalizedSubtitle}
				onUpdateLocalizedSubtitle={updateSelectedLocalizedSubtitle}
				onDeleteLocalizedSubtitle={(subtitleId) => deleteSubtitleItem('localized', subtitleId)}
					onSaveCue={saveSelectedCue}
					onConfirmCueTiming={confirmSelectedCueTiming}
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
					{confirmingCueTiming}
				{referenceUpdatingId}
				{candidateApplyingId}
					generatingVoice={submittingBatch}
					{taskHistory}
					{ttsHistory}
					onOpenSubtitleGenerate={openSelectedSubtitleInGenerate}
					onReuseSubtitleHistory={reuseSubtitleHistory}
				onApplySubtitleHistory={applySubtitleHistoryToTimeline}
				onDeleteSubtitleHistory={deleteSubtitleHistory}
				onDeleteCurrentSubtitleHistory={deleteCurrentSubtitleHistory}
				onDeleteAllSubtitleHistory={deleteAllSubtitleHistory}
					historyApplyingResultId={historyApplyingResultId}
					onCancelTask={cancelActivityTask}
					onRetryTask={retryActivityTask}
					{subtitleRuntimeBusy}
					{localizationRuntimeBusy}
					{taskCenterPulseKey}
				/>
		{:else}
			<aside class="inspector-rail">
				<button class="rail-tab" type="button" onclick={openTaskCenter} aria-label="任务" data-tooltip="任务｜查看后台处理进度、子步骤和历史结果。"><ListTodo size={15} /></button>
				<button class="rail-tab" type="button" onclick={() => focusInspector('subtitle')} aria-label="字幕" data-tooltip="字幕｜展开当前字幕片段编辑面板。"><Captions size={15} /></button>
				<button class="rail-tab" type="button" onclick={() => focusInspector('dubbing')} aria-label="配音" data-tooltip="配音｜展开音色、生成参数和配音结果。"><AudioLines size={15} /></button>
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

	@media (min-width: 1381px) {
		.video-localization-page {
			display: grid;
			grid-template-rows: auto minmax(0, 1fr);
			height: calc(100dvh - 49px);
			min-height: 0;
			padding-bottom: 14px;
			overflow: hidden;
		}

		.cutting-shell {
			height: 100%;
			min-height: 0;
		}

		.cutting-stage {
			min-height: 0;
			overflow-y: auto;
		}
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
		width: 100%;
		min-height: 44px;
		margin: 0 auto;
		max-width: 1720px;
		padding: 6px 8px;
		box-sizing: border-box;
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

	.cutting-actions {
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

	.cutting-mode .icon-action:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.cutting-mode .icon-action:hover:not(:disabled) {
		border-color: #4f606a;
		background: #242b31;
	}

	.cutting-shell {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 7px var(--inspector-width, 380px);
		width: 100%;
		max-width: 1720px;
		margin: 0 auto;
		box-sizing: border-box;
		border: 1px solid var(--line);
		border-radius: 0 0 10px 10px;
		background: var(--studio-panel);
		overflow: hidden;
	}

	.cutting-shell.collapsed {
		grid-template-columns: minmax(0, 1fr) 52px;
	}

	.inspector-resize-handle {
		position: relative;
		z-index: 4;
		width: 7px;
		min-width: 7px;
		padding: 0;
		border: 0;
		border-left: 1px solid var(--line);
		border-right: 1px solid rgba(255, 255, 255, 0.025);
		background: #14181c;
		cursor: col-resize;
	}

	.inspector-resize-handle::after {
		content: "";
		position: absolute;
		inset: 0 2px;
		background: transparent;
		transition: background 140ms ease;
	}

	.inspector-resize-handle:hover::after,
	.inspector-resize-handle:focus-visible::after {
		background: rgba(87, 208, 200, 0.56);
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

	.delivery-menu {
		position: relative;
	}

	.delivery-popover {
		position: absolute;
		top: calc(100% + 7px);
		right: 0;
		z-index: 120;
		display: grid;
		width: 220px;
		padding: 4px;
		border: 1px solid #3a424b;
		border-radius: 6px;
		background: #171b20;
		box-shadow: 0 12px 30px rgb(0 0 0 / 38%);
	}

	.delivery-menu-item {
		display: grid;
		grid-template-columns: 20px minmax(0, 1fr);
		align-items: center;
		gap: 7px;
		width: 100%;
		min-height: 42px;
		padding: 5px 7px;
		border: 0;
		border-radius: 4px;
		background: transparent;
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}

	.delivery-menu-item:hover:not(:disabled),
	.delivery-menu-item:focus-visible {
		background: #242a31;
		outline: none;
	}

	.delivery-menu-item:disabled {
		opacity: 0.42;
		cursor: not-allowed;
	}

	.delivery-menu-item span {
		display: grid;
		gap: 1px;
		min-width: 0;
	}

	.delivery-menu-item strong {
		font-size: 11px;
		font-weight: 600;
	}

	.delivery-menu-item small {
		overflow: hidden;
		color: var(--muted);
		font-size: 10px;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.delivery-menu-separator {
		height: 1px;
		margin: 3px 5px;
		background: var(--line);
	}

	@media (max-width: 1380px) {
		.cutting-shell,
		.cutting-shell.collapsed {
			grid-template-columns: 1fr;
		}

		.inspector-resize-handle {
			display: none;
		}

		.cutting-stage {
			border-right: 0;
			border-bottom: 1px solid var(--line);
		}

		.inspector-rail {
			display: none;
		}
	}

	@media (max-width: 1100px) {
		.cutting-head {
			grid-template-columns: 1fr;
		}

		.inspector-rail {
			display: none;
		}
	}

</style>
