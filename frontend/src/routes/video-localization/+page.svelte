<script module lang="ts">
	import type {
		VideoLocalizationCue as ProtectedCue,
		VideoLocalizationQualityIssue as ProtectedQualityIssue,
		VideoLocalizationSubtitleCue as ProtectedLocalizedSubtitle,
		VideoLocalizationTimelineClip as ConflictTimelineClip
	} from '$lib/api/types';
	export { isOrphanedDubPlaceholder } from './workspace-draft';

	export type AsrEngineId = 'auto' | 'faster-whisper-turbo' | 'qwen3-asr-mlx' | 'mimo-v2.5-asr' | 'vibevoice-asr-mlx-4bit' | 'vibevoice-asr-mlx-8bit';
	export type InspectorSection = 'tasks' | 'subtitle' | 'dubbing';
	export const DEFAULT_ASR_ENGINE_ID: AsrEngineId = 'qwen3-asr-mlx';
	export const SOURCE_ASR_ENGINE_ID: AsrEngineId = 'auto';
	export const SPEAKER_DIARIZATION_ENGINE_ID = 'moss-transcribe-diarize-mlx';

	export function buildSourceAsrOperationParameters(
		sourceTrackId: 'vocals' | 'dub' | 'original',
		sourceLanguage: string,
		includeSpeakerGrouping: boolean
	) {
		return {
			engine_id: SOURCE_ASR_ENGINE_ID,
			source_track_id: sourceTrackId,
			source_language: sourceLanguage,
			diarization_engine_id: includeSpeakerGrouping
				? SPEAKER_DIARIZATION_ENGINE_ID
				: null
		};
	}
	type ManualTimingCue = ProtectedCue & {
		manual_timing_revision?: number;
		manual_timing_review_status?: 'not_reviewed' | 'required' | 'confirmed';
		manual_timing_confirmed_revision?: number | null;
		manual_timing_confirmed_start_ms?: number | null;
		manual_timing_confirmed_end_ms?: number | null;
		manual_timing_confirmed_at?: string | null;
		manual_timing_confirmation_method?: 'auditioned' | 'asr_vad_verified' | null;
		manual_timing_confirmation_evidence?: {
			transcription_revision_id: string;
			source_word_ids: string[];
			source_timing_correction_id?: string | null;
			vad_interval_ids?: string[];
		} | null;
	};

	export function asrSelectionRequiresUploadConfirmation(engineId: AsrEngineId) {
		return engineId === 'mimo-v2.5-asr';
	}

	export function inspectorSectionOnProjectLoad(): InspectorSection {
		return 'tasks';
	}

	export function resolveInitialProjectId(
		urlProjectId: string | null,
		projects: Array<{
			project_id: string;
			has_source_media?: boolean;
			source_media_status?: string;
		}>
	) {
		if (urlProjectId) return urlProjectId;
		const withSource = projects.find(
			(project) => project.source_media_status === 'available'
		);
		const legacyWithSource = projects.find(
			(project) => project.source_media_status === undefined && project.has_source_media
		);
		return withSource?.project_id ?? legacyWithSource?.project_id ?? projects[0]?.project_id ?? '';
	}

	export function formatProjectUpdatedAt(value: string): string {
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return '时间未知';
		const pad = (part: number) => String(part).padStart(2, '0');
		return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
	}

	export function errorAfterRecoveredSync(currentError: string, recoveredError: string) {
		return recoveredError && currentError === recoveredError ? '' : currentError;
	}

	export function inspectorSectionForTimelineSelection(
		items: Array<{ kind: string; trackId: string }>
	): InspectorSection | null {
		if (!items.length || items.some((item) => item.kind !== 'subtitle' || item.trackId !== 'localizedSubtitles')) return null;
		return 'dubbing';
	}

	function timelineTaskIdentity(clip: ConflictTimelineClip) {
		return typeof clip.task_id === 'string' && clip.task_id
			? clip.task_id
			: typeof clip.generation_id === 'string' ? clip.generation_id : '';
	}

	export function discardedTtsTaskIdsAfterClipRemoval(
		currentIds: Iterable<string>,
		removedClips: ConflictTimelineClip[],
		remainingClips: ConflictTimelineClip[]
	) {
		const discarded = new Set([...currentIds].filter(Boolean));
		const remainingIdentities = new Set(remainingClips.map(timelineTaskIdentity).filter(Boolean));
		for (const clip of removedClips) {
			const identity = timelineTaskIdentity(clip);
			if (identity && !remainingIdentities.has(identity)) discarded.add(identity);
		}
		return [...discarded];
	}

	export function preserveDubClipsAfterLocalizedSubtitleMerge(
		clips: ConflictTimelineClip[],
		selectedSubtitles: ProtectedLocalizedSubtitle[],
		survivorSubtitleId: string
	) {
		const selectedIds = new Set(selectedSubtitles.map((item) => item.subtitle_id));
		const textById = new Map(
			selectedSubtitles.map((item) => [item.subtitle_id, (item.tts_text || item.text).trim()])
		);
		return clips.map((clip) => {
			if (clip.track_id !== 'dub') return clip;
			const targetIds = Array.isArray(clip.target_subtitle_ids)
				? clip.target_subtitle_ids.map(String).filter(Boolean)
				: clip.subtitle_id ? [clip.subtitle_id] : [];
			if (!targetIds.some((id) => selectedIds.has(id))) return clip;
			const remappedTargetIds = [...new Set(targetIds.map((id) => selectedIds.has(id) ? survivorSubtitleId : id))];
			const targetText = String(clip.tts_target_text || '').trim()
				|| targetIds.map((id) => textById.get(id) || '').filter(Boolean).join('\n');
			return {
				...clip,
				subtitle_id: clip.subtitle_id && selectedIds.has(clip.subtitle_id)
					? survivorSubtitleId
					: clip.subtitle_id,
				target_subtitle_ids: remappedTargetIds,
				tts_target_binding_status: 'stale',
				...(targetText ? { tts_target_text: targetText } : {})
			};
		});
	}

	export function isDubbingInspectorSection(section: InspectorSection) {
		return section === 'dubbing';
	}

	export function cueHasCurrentManualTimingConfirmation(
		cue: ProtectedCue,
		confirmationCurrent = false
	) {
		const reviewed = cue as ManualTimingCue;
		const evidence = reviewed.manual_timing_confirmation_evidence;
		return (
			reviewed.manual_timing_review_status === 'confirmed' &&
			reviewed.manual_timing_confirmed_revision === (reviewed.manual_timing_revision ?? 0) &&
			reviewed.manual_timing_confirmed_start_ms === cue.start_ms &&
			reviewed.manual_timing_confirmed_end_ms === cue.end_ms &&
			Boolean(reviewed.manual_timing_confirmed_at) &&
			(
				(reviewed.manual_timing_confirmation_method === 'auditioned' && !reviewed.manual_timing_confirmation_evidence) ||
				(reviewed.manual_timing_confirmation_method === 'asr_vad_verified' &&
					evidence?.transcription_revision_id === cue.transcription_revision_id &&
					JSON.stringify(evidence?.source_word_ids) === JSON.stringify(cue.source_word_ids) &&
					confirmationCurrent)
			)
		);
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
	import {
		VIDEO_LOCALIZATION_TTS_HANDOFF_INTENT_KEY,
		VIDEO_LOCALIZATION_TTS_HANDOFF_META_KEY,
		VIDEO_LOCALIZATION_TTS_HANDOFF_REQUEST_KEY,
		type VideoLocalizationTtsHandoffIntent
	} from '$lib/video-localization-tts-handoff';
	import { ApiError } from '$lib/api/client';
	import { withoutSubtitleTrack } from './subtitle-track-clear';
	import { createHistoryTimelineClipId, mergeTimelineMutation, resolveHistoryTimelineClip, timelineDubSegmentId } from './timeline-history-command';
	import { HistoryPlacementSessionController } from './history-placement-session-controller';
	import type {
		GenerateRequest,
		HistoryItem,
		LongformTask,
		ProjectMediaHealth,
		ProjectSummary,
		VideoLocalizationCue,
		VideoLocalizationDraft,
		VideoLocalizationGeneratedCandidate,
		VideoLocalizationOperation,
		VideoLocalizationSubtitleCue,
		VideoLocalizationTimelineClip,
		VideoLocalizationTimelineMutationResponse,
		VideoLocalizationTtsHistoryDeleteResponse,
		VideoLocalizationTtsTask,
		VideoLocalizationSpeakerCreate,
		VideoLocalizationWorkspaceReadModel,
		VideoLocalizationWorkspaceDetailSection,
		VideoPreviewCacheStatus
	} from '$lib/api/types';
	import {
		AudioLines,
		BookOpenText,
		Captions,
		Check,
		ChevronDown,
		Clapperboard,
		Download,
		FolderOpen,
		FileUp,
		ListTodo,
		PanelRightClose,
		PanelRightOpen,
		Pencil,
		Sparkles,
		Trash2,
		X
	} from 'lucide-svelte';
	import { onMount, tick } from 'svelte';
	import { goto, replaceState } from '$app/navigation';
	import {
		API_RECOVERED_EVENT,
		shouldRetryInitialLoadOnApiRecovery
	} from '$lib/api-health-recovery';
	import { nextSubtitleSaveRevision, subtitleSaveRevisionIsCurrent } from './subtitle-save-revision';
	import { buildDubSubtitleReviewRequest, type DubSubtitleReviewPatch } from './dub-subtitle-review';
	import { semanticTtsGroupOrdinal } from './semantic-tts-group-selection';
	import type { SemanticTtsGroupView } from './dubbing-production-view';
	import { ProjectDraftSessionController } from './project-draft-session-controller';
	import { ProjectRequestSessionController } from './project-request-session-controller';
	import {
		draftForPersistence,
		mergeDraftAfterConflict,
		snapshotDraftForConflictMerge,
		shouldKeepServerResetDraft,
		timelineDeletionIntentForSave,
		type DraftConflictMergeBase,
		type TimelineDirtyField,
		type TimelineDirtyFieldsByClipId
	} from './draft-ownership';
	import { mergeVideoLocalizationUiState } from './ui-state-merge';
	import { saveTimelineEditWithUiPatch } from './timeline-edit-save';
	import { clientVideoLocalizationUiPatch } from './ui-state-ownership';
	import {
		lastFrameStartMs,
		minimumFrameDurationMs,
		normalizeFrameRate,
		snapTimeToFrame
	} from './frame-timeline';
	import {
		buildTtsCoverageByIdentity,
		isActiveOperation,
		summarizeVideoLocalizationError,
		operationStatusLabel,
		sourceAudioUrl,
		stemAudioUrl,
		suggestSpeakerSeed,
	} from './utils';
	import CommandSpinner from './CommandSpinner.svelte';
	import PreviewPanel from './PreviewPanel.svelte';
	import VideoCuttingTimeline from './VideoCuttingTimeline.svelte';
	import type { TimelineSelectionItem } from './timeline-context-menu';
	import { activityTaskAffectsTrack, activityTaskDisplayName, activityTaskIsActive, asrSubtitleActionLabel, operationActivityTask, pendingOperationActivityTask, type ActivityTask } from './activity-notice';
	import {
		buildDevelopmentAsrPreview,
		buildDevelopmentInitialAnalysisPreview,
		buildDevelopmentEntityNormalizationPreview,
		buildDevelopmentReviewDecisionsPreview,
		buildDevelopmentWholeRecheckPreview,
		canDisplayDevelopmentAsrPreview,
		developmentInitialAnalysisResultMatchesDraft,
		developmentEntityNormalizationResultMatchesDraft,
		developmentReviewDecisionsResultMatchesDraft,
		developmentWholeRecheckResultMatchesDraft,
		developmentAsrResultMatchesDraft,
		resolveAsrOperationPreview,
		resolveDevelopmentAsrPreviewOperation,
		type AsrOperationPreview
	} from './asr-operation-preview';
	import {
		SubtitleDisplayModel,
		type SubtitleDisplayCue,
		type SubtitleDisplaySelection,
		type SubtitleDisplaySettings,
		type SubtitleDisplaySource
	} from './subtitle-display';
	import { AsyncSaveBarrier } from './async-save-barrier';
	import { runProjectCommandAfterSave } from './project-command-save-barrier';
	import type { MediaExportAvailability, MediaExportRequest } from './export-options';
	import {
		PlaybackSessionController,
		type PlaybackDriver
	} from './playback-session-controller';
	import {
		TtsWorkflowSessionController,
		type TtsWorkflowSessionContext,
		type TtsWorkflowSyncReason
	} from './tts-workflow-session-controller';
	import { TtsWorkflowFeedCache } from './tts-workflow-feed-cache';
	import {
		ProjectSessionController,
		type ProjectAutosaveRequest,
		type ProjectAutosaveStatus
	} from './project-session-controller';
	import { splitTimelineAudioClip } from './timeline-clip-split';
	import { buildDubTrackLaneLayout, canPlaceDubClipGroupAcrossLanes, canPlaceDubClipGroupInLane, canPlaceDubClipInLane, resolveDubClipLane, resolveDubSubtitleGenerationSource, swapDubTrackLanes } from './dub-track-lanes';
	import type { PlaybackReadinessStatus } from './playback-readiness';
	import type { TimelineGroupMoveCommitItem, TimelineSubtitleMergeRequest } from './timeline-interaction';
	import { timelineBoundaryTarget, type TimelineNavigationSources } from './timeline-navigation';
	import {
		TimelineEditController,
		timelineClipTimingChanged,
		type TimelineClipEditablePatch,
		type TimelineEditTransaction
	} from './timeline-edit-controller';
	import { buildTtsSubmissionSnapshot, type TtsSubmissionSnapshot } from './tts-submission-snapshot';
	import {
		cleanupTtsInitializationClips,
		createTtsInitializationPlaceholder,
		promoteTtsInitializationPlaceholder,
		ttsInitializationFailureMessage
	} from './tts-initialization-clips';
	import { persistedTtsWorkflowId, ttsInitializationClientId } from './tts-workflow-marker';
	import { upsertTtsGenerationActivity } from './tts-activity-task';
	import {
		composeTimelineRuntimeDraft,
		durableTimelineDraft,
		reconcileTimelineRuntimeClips
	} from './timeline-runtime-projection';
	import { mediaAssetAvailable, sourceVideoConfigured } from './media-health';
	import { normalizeWorkspaceDraft } from './workspace-draft';
	import {
		draftWithLiveTimelineProjection,
		reconcileTimelineClipPayloads
	} from './workspace-timeline-projection';
	import { WorkspaceRevisionController } from './workspace-revision-controller';
	import { NoticeClearController } from './notice-clear-controller';
	import { VideoLocalizationPreviewMediaClient } from './preview-media-client';
	import {
		PreviewCacheSessionController,
		VideoLocalizationPreviewCacheClient
	} from './preview-cache-session-controller';
	import {
		OperationFeedController,
		VideoLocalizationOperationFeedClient
	} from './operation-feed-controller';
	import {
		ProjectCatalogController,
		VideoLocalizationProjectCatalogClient
	} from './project-catalog-controller';
	import { ProjectDeleteQueue } from './project-delete-queue';
	import { SerializedSaveQueue } from './serialized-save-queue';
	import { TtsSubmissionQueue, withTtsSubmissionQueueLabels } from './tts-submission-queue';
	import {
		historyBelongsToSegment,
		TtsHistoryController,
		VideoLocalizationTtsHistoryClient
	} from './tts-history-controller';
	import {
		EMPTY_TTS_SELECTION_SESSION,
		ensureDirectTtsTargetSelection,
		refreshTtsSelectionSession,
		resolveTtsSelectionSession,
		ttsSelectionIsContiguous,
		ttsSourceSelectionAdvisory,
		updateTtsSelectionSession,
		type TtsSelectionAnchor,
		type TtsSelectionSession
	} from './tts-selection-session';
	import { readTimelineViewState, resolveTimelineViewState, writeTimelineViewState, type TimelineViewState } from './timeline-view-state';
	import {
		extendSubtitleCuesToFollowingStart,
		resolveAudioTrackOrder,
		resolveDubLaneStates,
		resolveTrackStates,
		MIN_SUBTITLE_DURATION_MS,
		subtitleCueDragBounds,
		type VideoLocalizationAudioTrackOrder,
		type VideoLocalizationDubLaneStates,
		type VideoLocalizationTrackId,
		type VideoLocalizationTrackState
	} from './studio-state';

	type SemanticTtsGroup = SemanticTtsGroupView;

	function projectNameMarquee(node: HTMLElement) {
		let animationFrame = 0;
		let cycleStartedAt = 0;
		let maxScroll = 0;
		const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
		const stop = () => {
			if (animationFrame) cancelAnimationFrame(animationFrame);
			animationFrame = 0;
			cycleStartedAt = 0;
		};
		const animate = (timestamp: number) => {
			if (maxScroll <= 1 || reduceMotion) return stop();
			if (!cycleStartedAt) cycleStartedAt = timestamp;
			const leadPauseMs = 900;
			const tailPauseMs = 700;
			const travelMs = Math.max(1200, (maxScroll / 28) * 1000);
			const cycleMs = leadPauseMs + travelMs + tailPauseMs;
			const elapsed = (timestamp - cycleStartedAt) % cycleMs;
			if (elapsed < leadPauseMs) node.scrollLeft = 0;
			else if (elapsed < leadPauseMs + travelMs) node.scrollLeft = maxScroll * ((elapsed - leadPauseMs) / travelMs);
			else node.scrollLeft = maxScroll;
			animationFrame = requestAnimationFrame(animate);
		};
		const refresh = () => {
			stop();
			maxScroll = Math.max(0, node.scrollWidth - node.clientWidth);
			node.dataset.overflowing = String(maxScroll > 1);
			node.scrollLeft = 0;
			if (maxScroll > 1 && !reduceMotion) animationFrame = requestAnimationFrame(animate);
		};
		const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(refresh);
		const mutationObserver = new MutationObserver(refresh);
		resizeObserver?.observe(node);
		mutationObserver.observe(node, { characterData: true, childList: true, subtree: true });
		requestAnimationFrame(refresh);
		return {
			destroy() {
				stop();
				resizeObserver?.disconnect();
				mutationObserver.disconnect();
			}
		};
	}

	function fitStackedInspectorToViewport(node: HTMLElement) {
		let animationFrame = 0;
		const schedule = () => {
			if (animationFrame) cancelAnimationFrame(animationFrame);
			animationFrame = requestAnimationFrame(() => {
				animationFrame = 0;
				if (window.matchMedia('(min-width: 1381px)').matches) {
					node.style.removeProperty('--stacked-inspector-height');
					return;
				}
				const inspector = node.querySelector<HTMLElement>('.inspector.tasks-view');
				if (!inspector) return;
				const scrollContainer = node.closest<HTMLElement>('.main');
				const inspectorTop = inspector.getBoundingClientRect().top;
				const contentTop = scrollContainer
					? inspectorTop - scrollContainer.getBoundingClientRect().top + scrollContainer.scrollTop
					: inspectorTop + window.scrollY;
				const page = node.closest<HTMLElement>('.video-localization-page');
				// Reserve one extra pixel so fractional layout rounding never creates page overflow.
				const bottomInset = Number.parseFloat(getComputedStyle(node).borderBottomWidth)
					+ (page ? Number.parseFloat(getComputedStyle(page).paddingBottom) : 14)
					+ 1;
				const availableHeight = (scrollContainer?.clientHeight ?? window.innerHeight) - contentTop - bottomInset;
				const compactFallback = Math.max(360, Math.min(640, window.innerHeight - 32));
				node.style.setProperty(
					'--stacked-inspector-height',
					`${Math.max(compactFallback, availableHeight)}px`
				);
			});
		};
		const stage = node.querySelector<HTMLElement>('.cutting-stage');
		const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
		if (stage) resizeObserver?.observe(stage);
		const mutationObserver = new MutationObserver(schedule);
		mutationObserver.observe(node, { childList: true });
		window.addEventListener('resize', schedule);
		schedule();
		return {
			destroy() {
				if (animationFrame) cancelAnimationFrame(animationFrame);
				resizeObserver?.disconnect();
				mutationObserver.disconnect();
				window.removeEventListener('resize', schedule);
			}
		};
	}

	let projects = $state<ProjectSummary[]>([]);
	const projectCatalogController = new ProjectCatalogController(
		new VideoLocalizationProjectCatalogClient(),
		{ onProjects: (nextProjects) => projects = nextProjects }
	);
	let operations = $state<VideoLocalizationOperation[]>([]);
	let operationHistoryTotal = $state(0);
	let operationHistoryLoaded = $state(0);
	let operationHistoryHasMore = $state(false);
	let operationHistoryLoading = $state(false);
	let developmentAsrPreview = $state<AsrOperationPreview | null>(null);
	let developmentAsrPreviewResolvedId = '';
	let developmentAsrPreviewLoadingId = '';
	let developmentAsrPreviewGeneration = 0;
	let foregroundTasks = $state<ActivityTask[]>([]);
	let ttsHistory = $state<HistoryItem[]>([]);
	let ttsHistoryTotal = $state(0);
	let ttsHistoryLoaded = $state(0);
	let ttsHistoryHasMore = $state(false);
	let ttsHistoryLoading = $state(false);
	const ttsHistoryController = new TtsHistoryController(
		new VideoLocalizationTtsHistoryClient(),
		{
			onItems: (items) => {
				ttsHistory = items;
				applyTtsHistoryCoverage(items);
			},
			onPageState: (state) => {
				ttsHistoryTotal = state.total;
				ttsHistoryLoaded = state.loaded;
				ttsHistoryHasMore = state.hasMore;
				ttsHistoryLoading = state.loading;
			}
		}
	);
	const ttsCoverageByIdentity = $derived(buildTtsCoverageByIdentity(ttsHistory));
	let lastDubbingWorkspaceSyncAt = 0;
	let dubbingWorkspaceSyncPromise: Promise<void> | null = null;
	const workspaceRevisionController = new WorkspaceRevisionController();
	let projectId = $state('');
	let draftProjectId = $state('');
	let draft = $state<VideoLocalizationDraft | null>(null);
	let timelineRuntimeClips = $state<VideoLocalizationTimelineClip[]>([]);
	let semanticTtsGroups = $state<SemanticTtsGroup[]>([]);
	let timingConfirmationCurrentByCueId = $state<Map<string, boolean>>(new Map());
	const loadedWorkspaceDetailSections = new Set<VideoLocalizationWorkspaceDetailSection>();
	const workspaceDetailLoads = new Map<VideoLocalizationWorkspaceDetailSection, Promise<void>>();
	let draftConflictMergeBase: DraftConflictMergeBase | null = null;
	let mediaHealth = $state<ProjectMediaHealth | null>(null);
	const projectDraftSessionController = new ProjectDraftSessionController({
		getActiveProjectId: () => projectId
	});
	const historyPlacementSessionController = new HistoryPlacementSessionController(() => projectId);
	const timelineProjectionSessionController = new ProjectRequestSessionController({
		getActiveProjectId: () => projectId
	});
	const operationDetailSessionController = new ProjectRequestSessionController({
		getActiveProjectId: () => projectId
	});
	let draftOnlyCueIds = $state<string[]>([]);
	let selectedCueId = $state('');
	let selectedLocalizedSubtitleId = $state('');
	let selectedProvisionalSubtitle = $state<SubtitleDisplaySelection | null>(null);
	let dubSubtitleDisplayProjectId = $state('');
	let selectedTimelineAudioClipId = $state('');
	let timelineSelectionItems = $state<TimelineSelectionItem[]>([]);
	let ttsSelectionSession = $state<TtsSelectionSession>({ ...EMPTY_TTS_SELECTION_SESSION });
	let loading = $state(true);
	let retryInitialProjectLoadOnApiRecovery = false;
	let resetting = $state(false);
	let confirmingCueTiming = $state(false);
	let dubSubtitleReviewBusy = $state(false);
	let requestedDubSubtitleFullRegeneration = false;
	let creatingSpeaker = $state(false);
	let importing = $state(false);
	let openingProjectDirectory = $state(false);
	const previewMediaClient = new VideoLocalizationPreviewMediaClient();
	let editingProjectName = $state(false);
	let projectNameDraft = $state('');
	let projectNameSaving = $state(false);
	let projectAutoNaming = $state(false);
	let projectMenuOpen = $state(false);
	let deliveryMenuOpen = $state(false);
	let mediaExportRequest = $state<MediaExportRequest | null>(null);
	let mediaExportAvailability = $state<MediaExportAvailability | null>(null);
	let projectMenuSyncing = $state(false);
	let deletingProjectId = $state('');
	let queuedProjectDeleteIds = $state<string[]>([]);
	const pendingProjectDeleteIds = $derived(new Set([
		deletingProjectId,
		...queuedProjectDeleteIds
	].filter(Boolean)));
	let extractingAudio = $state(false);
	let separatingStems = $state(false);
	let transcribingAsr = $state(false);
	let asrSetupOpen = $state(false);
	let speakerGroupingHealthLoading = $state(false);
	let speakerGroupingAvailable = $state(false);
	let speakerGroupingStatus = $state('');
	let pendingTtsSubmissionCount = $state(0);
	let preparingTtsHandoff = $state(false);
	const pendingTtsInitializations = new Map<string, TtsSubmissionSnapshot>();
	const ttsInitializationContexts = new Map<string, TtsWorkflowSessionContext>();
	const cancelledTtsInitializationActions = new Map<string, 'stop' | 'delete'>();
	const ttsSubmissionQueue = new TtsSubmissionQueue({
		onStateChange: (state) => {
			if (!draft) return;
			timelineRuntimeClips = withTtsSubmissionQueueLabels(
				{ ...draft, timeline_clips: timelineRuntimeClips },
				state
			).timeline_clips;
		},
		onError: (_submissionId, submissionError) => {
			error = (submissionError as Error).message || '提交配音生成失败';
		}
	});
	let importingLocalizedSrt = $state(false);
	let exportingDelivery = $state(false);
	let selectingExportDestination = $state(false);
	let exportDestination = $state<{
		destination_id: string;
		display_path: string;
	} | null>(null);
	let exportProgress = $state(0);
	let exportStage = $state('');
	let exportedFilename = $state('');
	let exportOutputFilename = $state('');
	let exportFilenameUserEdited = $state(false);
	let exportFilenameRequestSequence = 0;
	let exportError = $state('');
	let historyApplyingResultId = $state('');
	let draggingTtsHistory = $state<HistoryItem | null>(null);
	let operationAction = $state<{ id: string; kind: 'cancel' | 'retry' } | null>(null);
	let ttsTaskAction = $state<{ id: string; kind: 'cancel' | 'delete' } | null>(null);
	let inspectorCollapsed = $state(false);
	let inspectorWidth = $state(380);
	let inspectorSection = $state<InspectorSection>(inspectorSectionOnProjectLoad());
	let dubbingHistoryScope = $state<'current' | 'all'>('current');
	let previewTimeMs = $state(0);
	let timelineZoom = $state(1);
	let timelineViewportStartMs = $state(0);
	let hoverPreviewTimeMs = $state<number | null>(null);
	let previewPlaying = $state(false);
	let previewPlaybackPreparing = $state(false);
	let previewCache = $state<VideoPreviewCacheStatus | null>(null);
	let playbackReadiness = $state<PlaybackReadinessStatus | null>(null);
	let previewCacheRefreshing = $state(false);
	let timelineSelectionRange = $state<{ start_ms: number; end_ms: number } | null>(null);
	let activePlaybackLoopRange = $state<{ start_ms: number; end_ms: number } | null>(null);
	let audioSelectionRange = $state<{ start_ms: number; end_ms: number } | null>(null);
	let previewPlaybackRestored = false;
	let autoSaveStatus = $state<ProjectAutosaveStatus>('idle');
	let lastAutoSavedAt = $state('');
	let timelineEditController = $state.raw<TimelineEditController | null>(null);
	let timelineUndoCount = $state(0);
	let timelineRedoCount = $state(0);
	let timelineControllerDeletedClipIds = new Set<string>();
	let timelineDeletedClipIds = new Set<string>();
	let timelineDirtyFieldsByClipId = new Map<string, Set<TimelineDirtyField>>();
	let timelineEditRevision = 0;
	let timelineSavedRevision = 0;
	let cueSaveRevision = 0;
	let localizedSubtitleSaveEpoch = 0;
	let localizedSubtitleSaveRevisions = new Map<string, number>();
	type PendingLocalizedSubtitleSave = {
		projectId: string;
		subtitleId: string;
		patch: Partial<VideoLocalizationSubtitleCue>;
		previousSubtitle: VideoLocalizationSubtitleCue;
		revision: number;
		epoch: number;
	};
	let pendingLocalizedSubtitleSaves = new Map<string, PendingLocalizedSubtitleSave>();
	let localizedSubtitleSaveTimer: ReturnType<typeof setTimeout> | null = null;
	let videoInput: HTMLInputElement | null = null;
	let localizationSrtInput: HTMLInputElement | null = null;
	const localizedSubtitleSaveBarrier = new AsyncSaveBarrier();
	const editorialSaveQueue = new SerializedSaveQueue();
	let message = $state('');
	const messageClearController = new NoticeClearController();
	let error = $state('');
	const projectDeleteQueue = new ProjectDeleteQueue({
		onStateChange: (state) => {
			deletingProjectId = state.activeProjectId;
			queuedProjectDeleteIds = state.queuedProjectIds;
		},
		onError: (failedProjectId, cause) => {
			const failedProject = projects.find(
				(project) => project.project_id === failedProjectId
			);
			const detail = cause instanceof Error && cause.message
				? `：${cause.message}`
				: '';
			error = `项目「${failedProject?.name || failedProjectId}」删除失败${detail}`;
		}
	});
	const previewCacheSessionController = new PreviewCacheSessionController(
		new VideoLocalizationPreviewCacheClient(),
		{
			onStateChange: (state) => {
				previewCache = state.cache;
				previewCacheRefreshing = state.refreshing;
			},
			onPlaybackCoverageInvalidated: () => (playbackReadiness = null)
		}
	);
	const operationFeedController = new OperationFeedController(
		new VideoLocalizationOperationFeedClient(),
		{
			onOperations: (activeProjectId, latest) => {
				operations = latest;
				if (latest.some((operation) =>
					operation.kind === 'dub_subtitle_generation'
					&& (operation.status === 'queued' || operation.status === 'running')
				)) {
					dubSubtitleDisplayProjectId = activeProjectId;
				}
				void syncDevelopmentAsrPreview(activeProjectId, latest);
			},
			refreshAfterTerminal: async (activeProjectId) => {
				if (projectId === activeProjectId) await refreshDraftOnly();
			},
			onPollingSynchronized: (latest, terminalTransition) => {
				const recoveredSyncError = operationFeedSyncErrorMessage;
				operationFeedSyncErrorMessage = '';
				error = errorAfterRecoveredSync(error, recoveredSyncError);
				if (terminalTransition?.status === 'failed' && terminalTransition.error_message) {
					operationErrorId = terminalTransition.operation_id;
					operationErrorMessage = terminalTransition.error_message;
					error = terminalTransition.error_message;
				} else if (terminalTransition?.status === 'success') {
					if (
						terminalTransition.kind === 'dub_subtitle_generation'
						&& terminalTransition.result_summary?.stage_id === 'commit'
						&& terminalTransition.parameters?.execution_mode !== 'development_target'
					) {
						dubSubtitleDisplayProjectId = terminalTransition.project_id;
						updateDraftUiState({ subtitle_display_mode: 'dub' });
					}
					message = `${terminalTransition.label || '后台任务'}已完成`;
				} else if (terminalTransition?.status === 'cancelled') {
					message = `${terminalTransition.label || '后台任务'}已取消`;
				} else if (
					operationErrorId
					&& !latest.some((operation) =>
						operation.operation_id === operationErrorId && operation.status === 'failed'
					)
				) {
					if (error === operationErrorMessage) error = '';
					operationErrorId = '';
					operationErrorMessage = '';
				}
			},
			onTimeout: () => {
				message = '任务状态同步稍慢，正在重试';
				scheduleMessageClear(2400);
			},
			onError: (operationError) => {
				operationFeedSyncErrorMessage = operationError || '刷新任务状态失败，正在重试';
				error = operationFeedSyncErrorMessage;
			},
			onHistoryState: (_activeProjectId, state) => {
				operationHistoryTotal = state.total;
				operationHistoryLoaded = state.loaded;
				operationHistoryHasMore = state.hasMore;
				operationHistoryLoading = state.loading;
			}
		}
	);
	let operationErrorId = $state('');
	let operationErrorMessage = $state('');
	let operationFeedSyncErrorMessage = $state('');
	let taskCenterPulseKey = $state(0);
	const projectSessionController = new ProjectSessionController({
		getProjectId: () => projectId,
		canSave: () => Boolean(draft),
		getExternalRevision: () => timelineEditRevision,
		hasExternalPending: () => (
			timelineEditRevision !== timelineSavedRevision
			|| timelineDirtyFieldsByClipId.size > 0
			|| timelineDeletedClipIds.size > 0
		),
		save: (request) => editorialSaveQueue.run(() => saveProjectSessionAutosave(request)),
		onStatusChange: (status) => (autoSaveStatus = status),
		onSaved: () => {
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
		},
		onError: (saveError) => {
			error = (saveError as Error).message || '自动保存失败';
		}
	});
	const playbackSessionController = new PlaybackSessionController({
		getProjectId: () => projectId,
		getDraftProjectId: () => draftProjectId,
		hasDraft: () => Boolean(draft),
		getTimeMs: () => previewTimeMs,
		getPlaying: () => previewPlaying,
		setTimeMs: (timeMs) => (previewTimeMs = timeMs),
		setPlaying: (playing) => (previewPlaying = playing),
		setPreparing: (preparing) => (previewPlaybackPreparing = preparing),
		setHoverTimeMs: (timeMs) => (hoverPreviewTimeMs = timeMs),
		setLoopRange: (range) => (activePlaybackLoopRange = range),
		setRestored: (restored) => (previewPlaybackRestored = restored),
		persistPlayhead: (activeProjectId, playheadMs) => {
			if (typeof window !== 'undefined') {
				writeTimelineViewState(window.sessionStorage, activeProjectId, { playhead_ms: playheadMs });
			}
		}
	});
	const ttsWorkflowFeedCache = new TtsWorkflowFeedCache();

	async function loadTtsWorkflowFeed(projectId: string) {
		const feed = await Api.videoLocalizationTtsTaskFeed(projectId, ttsWorkflowFeedCache.revision);
		return ttsWorkflowFeedCache.apply(feed);
	}

	const ttsWorkflowSessionController = new TtsWorkflowSessionController<VideoLocalizationTtsTask>({
		loadTasks: (context) => loadTtsWorkflowFeed(context.projectId),
		taskId: (task) => task.workflow_id,
		taskRevision: (task) => JSON.stringify(task),
		isActive: ttsWorkflowActive,
		isTerminal: (task) => ['success', 'failed', 'cancelled'].includes(task.status),
		onTasks: (context, tasks) => {
			if (!ttsWorkflowSessionController.isCurrent(context) || !draft) return;
			if (error === '配音任务状态同步失败，正在重试') error = '';
			draft = { ...draft, tts_tasks: tasks.slice().reverse() };
			timelineRuntimeClips = reconcileTimelineRuntimeClips(draft, timelineRuntimeClips);
		},
		onTerminal: async (context) => {
			if (!ttsWorkflowSessionController.isCurrent(context)) return;
			await refreshTimelineProjectionOnly();
			if (!ttsWorkflowSessionController.isCurrent(context)) return;
			await loadTtsHistory(context.projectId);
			for (const delayMs of [2_000, 6_000]) {
				setTimeout(() => {
					if (ttsWorkflowSessionController.isCurrent(context)) void loadTtsHistory(context.projectId);
				}, delayMs);
			}
		},
		onError: (context, _syncError, source) => {
			if (
				source === 'sync'
				&& ttsWorkflowSessionController.isCurrent(context)
				&& !error
			) {
				error = '配音任务状态同步失败，正在重试';
			}
		},
		pollIntervalMs: 5_000
	});

	const selectedProject = $derived(projects.find((project) => project.project_id === projectId) ?? null);
	const timelineViewDraft = $derived(
		draft ? composeTimelineRuntimeDraft(draft, timelineRuntimeClips) : null
	);
	const hasImportedProject = $derived(sourceVideoConfigured(mediaHealth));
	const selectedCue = $derived(selectedCueId ? draft?.cues.find((cue) => cue.cue_id === selectedCueId) ?? null : null);
	const selectedLocalizedSubtitle = $derived(
		selectedLocalizedSubtitleId
			? draft?.localized_subtitles.find((cue) => cue.subtitle_id === selectedLocalizedSubtitleId) ?? null
			: null
	);
	const selectedTimelineAudioClip = $derived(
		selectedTimelineAudioClipId
			? timelineViewDraft?.timeline_clips.find((clip) => clip.clip_id === selectedTimelineAudioClipId) ?? null
			: null
	);
	const selectedLocalizedSubtitles = $derived.by(() => {
		if (!draft || !ttsSelectionSession.localizedSubtitleIds.length) return [];
		const selectedIds = new Set(ttsSelectionSession.localizedSubtitleIds);
		return draft.localized_subtitles.filter((item) => selectedIds.has(item.subtitle_id)).sort((left, right) => left.start_ms - right.start_ms);
	});
	const selectedLocalizedSubtitlesContiguous = $derived(Boolean(
		draft && ttsSelectionIsContiguous(ttsSelectionSession, draft.localized_subtitles)
	));
	const ttsSourceSelectionAdvisoryMessage = $derived(
		draft ? ttsSourceSelectionAdvisory(ttsSelectionSession, draft.cues) : null
	);
	const ttsPrimaryLocalizedSubtitle = $derived(
		(ttsSelectionSession.anchor?.kind === 'target'
			? selectedLocalizedSubtitles.find((item) => item.subtitle_id === ttsSelectionSession.anchor?.itemId)
			: null)
		?? selectedLocalizedSubtitles[0]
		?? null
	);
	const semanticGroupingBusy = $derived(operationBusy('semantic_tts_grouping'));
	const displayedPreviewTimeMs = $derived(hoverPreviewTimeMs ?? previewTimeMs);
	const activeLocalizationPreview = $derived(SubtitleDisplayModel.resolveLocalizedPreview(operations));
	const activeDubSubtitlePreview = $derived(SubtitleDisplayModel.resolveDubSubtitlePreview(operations));
	const dubSubtitleDisplayEnabled = $derived(Boolean(
		projectId
		&& (
			dubSubtitleDisplayProjectId === projectId
			|| draft?.ui_state?.subtitle_display_mode !== 'localized'
		)
		&& (
			activeDubSubtitlePreview.length
			|| draft?.dub_subtitles?.length
		)
	));
	const localizationPreview = $derived(
		dubSubtitleDisplayEnabled
			? activeDubSubtitlePreview
			: activeLocalizationPreview
	);
	const localizationPreviewLabel = $derived(
		activeDubSubtitlePreview.length && dubSubtitleDisplayEnabled
			? '配音字幕生成中'
			: activeLocalizationPreview.length
				? '本土化字幕生成中'
				: null
	);
	const localizationPreviewActive = $derived(Boolean(
		localizationPreview.length
	));
	const readyCount = $derived(draft?.cues.filter((cue) => cue.review_status === 'ready' || cue.review_status === 'locked').length ?? 0);
	const reviewCount = $derived(draft?.cues.filter((cue) => cue.review_status === 'needs_review').length ?? 0);
	const blockedCount = $derived(draft?.cues.filter((cue) => cue.review_status === 'blocked').length ?? 0);
	const lowTimingCount = $derived(
		draft?.cues.filter((cue) => cue.timing_confidence === 'low' && !cueHasCurrentManualTimingConfirmation(cue, timingConfirmationCurrentByCueId.get(cue.cue_id) ?? false)).length ?? 0
	);
	const confirmedTimingCueIds = $derived(new Set(
		(draft?.cues ?? []).filter((cue) => cueHasCurrentManualTimingConfirmation(
			cue, timingConfirmationCurrentByCueId.get(cue.cue_id) ?? false
		)).map((cue) => cue.cue_id)
	));
	const mediumTimingCount = $derived(draft?.cues.filter((cue) => cue.timing_confidence === 'medium').length ?? 0);
	const generatedCount = $derived(draft?.cues.filter((cue) => cue.tts_audio_path).length ?? 0);
	const transcription = $derived(draft?.transcription ?? null);
	const noticeText = $derived(error ? summarizeVideoLocalizationError(error) : message);
	const localizedCount = $derived(draft?.localized_subtitles?.length ?? 0);
	const hasActiveOperation = $derived(operations.some((operation) => isActiveOperation(operation)));
	const ttsWorkflowTasks = $derived((draft?.tts_tasks ?? [])
		.slice()
		.reverse()
		.map((task) => ttsWorkflowActivityTask(
			task,
			ttsTaskAction?.id === task.workflow_id ? ttsTaskAction.kind : undefined
		)));
	const persistedTtsGenerationTaskIds = $derived(new Set(
		(draft?.tts_tasks ?? []).map((task) => task.generation_task_id).filter((taskId): taskId is string => Boolean(taskId))
	));
	const activityTasks = $derived([
		...foregroundTasks.filter((task) => (
			activityTaskIsActive(task)
			&& (!task.id.startsWith('tts:') || !persistedTtsGenerationTaskIds.has(task.id.slice(4)))
		)),
		...ttsWorkflowTasks.filter((task) => task.status === 'queued' || task.status === 'running'),
		...operations
			.filter((operation) => isActiveOperation(operation))
			.map((operation) => operationActivityTask(operation, operationAction?.id === operation.operation_id ? operationAction.kind : undefined))
	]);
	const taskHistory = $derived([
		...foregroundTasks.filter((task) => !task.id.startsWith('tts:') || !persistedTtsGenerationTaskIds.has(task.id.slice(4))),
		...ttsWorkflowTasks,
		...operations.map((operation) => operationActivityTask(operation, operationAction?.id === operation.operation_id ? operationAction.kind : undefined))
	]);
	const activeAsrPreview = $derived(resolveAsrOperationPreview(operations));
	const developmentAsrPreviewOperation = $derived(resolveDevelopmentAsrPreviewOperation(operations));
	const asrPreview = $derived.by(() => {
		if (activeAsrPreview) return activeAsrPreview;
		const preview = developmentAsrPreview;
		if (!canDisplayDevelopmentAsrPreview(
			preview,
			draft,
			developmentAsrPreviewOperation?.operation_id ?? null,
			developmentAsrPreviewLoadingId
		)) return null;
		return preview;
	});
	const subtitleRuntimeBusy = $derived(activityTasks.some((task) => activityTaskAffectsTrack(task, 'subtitles')));
	const localizationRuntimeBusy = $derived(activityTasks.some((task) => activityTaskAffectsTrack(task, 'localizedSubtitles')));
	const latestOperation = $derived(operations.find((operation) => isActiveOperation(operation)) ?? operations[0] ?? null);
	const speakerSeed = $derived(suggestSpeakerSeed(draft?.speakers ?? []));
	const cueTimelineAudioSrc = $derived(stemAudioUrl(projectId, mediaHealth, 'vocals') || sourceAudioUrl(projectId, mediaHealth));
	const cueTimelineAudioLabel = $derived(mediaAssetAvailable(mediaHealth, 'vocals') ? '分离后人声' : '源音轨');
	const cueTimelineDurationMs = $derived(draft?.source_media.duration_ms ?? null);
	const subtitleDisplayAvailability = $derived({
		asr: Boolean(asrPreview?.cues.length || draft?.cues.length),
		localized: Boolean(localizationPreview.length || draft?.localized_subtitles.length || draft?.dub_subtitles?.length)
	});
	const subtitleDisplaySettings = $derived(SubtitleDisplayModel.resolveSettings(
		draft?.ui_state?.subtitle_preview,
		subtitleDisplayAvailability
	));
	const subtitleDisplaySelection = $derived<SubtitleDisplaySelection | null>(
		selectedProvisionalSubtitle
			?? (selectedLocalizedSubtitleId
				? { source: 'localized', id: selectedLocalizedSubtitleId }
				: selectedCueId
					? { source: 'asr', id: selectedCueId }
					: null)
	);
	const subtitleDisplay = $derived(new SubtitleDisplayModel({
		settings: subtitleDisplaySettings,
		asrCues: draft?.cues ?? [],
		asrPreview,
		localizedCues: draft?.localized_subtitles ?? [],
		localizedPreview: localizationPreview,
		localizedPreviewLabel: localizationPreviewLabel,
		localizedPreviewActive: localizationPreviewActive,
		localizedAlternative: dubSubtitleDisplayEnabled
			? {
				cues: draft?.dub_subtitles ?? [],
				label: '合成配音字幕'
			}
			: null,
		selection: subtitleDisplaySelection
	}));
	const subtitleDisplayFrame = $derived(subtitleDisplay.frameAt(displayedPreviewTimeMs));
	const subtitleWorkflowSettingsOpen = $derived(draft?.ui_state?.subtitle_workflow_settings_open === true);
	const trackStates = $derived(resolveTrackStates(draft?.ui_state?.track_states));
	const dubLaneLayout = $derived(buildDubTrackLaneLayout((draft?.timeline_clips ?? []).filter((clip) => clip.track_id === 'dub')));
	const dubLaneCount = $derived(dubLaneLayout.laneCount);
	const dubLaneStates = $derived(resolveDubLaneStates(draft?.ui_state?.dub_lane_states, dubLaneCount, trackStates.dub));
	const dubSubtitleGenerationSource = $derived(resolveDubSubtitleGenerationSource(dubLaneLayout, dubLaneStates));
	const hasDirtyDubSubtitleScope = $derived(Boolean(
		draft?.dub_subtitle_dirty_scope
		&& (
			draft.dub_subtitle_dirty_scope.affected_clip_ids.length > 0
			|| draft.dub_subtitle_dirty_scope.affected_ranges.length > 0
		)
	));
	const audioTrackOrder = $derived(resolveAudioTrackOrder(draft?.ui_state?.audio_track_order));
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

	function scheduleMessageClear(delayMs: number) {
		messageClearController.schedule({
			message,
			delayMs,
			getMessage: () => message,
			clear: () => (message = '')
		});
	}

	onMount(() => {
		const pageVisible = document.visibilityState !== 'hidden';
		operationFeedController.setVisible(pageVisible);
		ttsWorkflowSessionController.setVisible(pageVisible);
		loadProjects();
		const liveWorkspaceRefreshTimer = window.setInterval(
			refreshDubbingDraftLive,
			5_000
		);
		document.addEventListener('visibilitychange', handleOperationVisibilityChange);
		window.addEventListener(API_RECOVERED_EVENT, handleApiRecovered);
		window.addEventListener('focus', refreshDubbingWorkspaceOnFocus);
		window.addEventListener('pagehide', handlePageHide);
		window.addEventListener('beforeunload', handleBeforeUnload);
		window.addEventListener('keydown', handlePageKeydown, true);
		return () => {
			window.clearInterval(liveWorkspaceRefreshTimer);
			document.removeEventListener('visibilitychange', handleOperationVisibilityChange);
			window.removeEventListener(API_RECOVERED_EVENT, handleApiRecovered);
			window.removeEventListener('focus', refreshDubbingWorkspaceOnFocus);
			window.removeEventListener('pagehide', handlePageHide);
			window.removeEventListener('beforeunload', handleBeforeUnload);
			window.removeEventListener('keydown', handlePageKeydown, true);
			operationFeedController.dispose();
			ttsWorkflowSessionController.dispose();
			ttsHistoryController.deactivate();
			previewCacheSessionController.dispose();
			persistPlayhead(previewTimeMs);
			playbackSessionController.dispose();
			messageClearController.dispose();
			if (hasPendingSaveWork()) void flushPendingAutosave();
			projectSessionController.cancelScheduledRun();
		};
	});

	function handleApiRecovered() {
		// A backend restart can interrupt a debounced or in-flight autosave while
		// the edited timeline remains visible in memory.  Retry that retained
		// request before any later refresh can make the persisted draft appear to
		// jump back to its older state.
		if (draft !== null && hasPendingSaveWork()) {
			void flushPendingAutosave();
			return;
		}
		if (!shouldRetryInitialLoadOnApiRecovery({
			retryPending: retryInitialProjectLoadOnApiRecovery,
			loading,
			hasLoadedResource: draft !== null
		})) return;
		retryInitialProjectLoadOnApiRecovery = false;
		void loadProjects();
	}

	function hasPendingDraftWork() {
		return historyPlacementSessionController.hasPending
			|| localizedSubtitleSaveBarrier.size > 0
			|| pendingLocalizedSubtitleSaves.size > 0
			|| Boolean(localizedSubtitleSaveTimer)
			|| projectSessionController.hasPendingDraft();
	}

	function hasPendingSaveWork() {
		return historyPlacementSessionController.hasPending
			|| localizedSubtitleSaveBarrier.size > 0
			|| pendingLocalizedSubtitleSaves.size > 0
			|| Boolean(localizedSubtitleSaveTimer)
			|| projectSessionController.hasPending();
	}

	function hasPendingProjectContentEdits() {
		return localizedSubtitleSaveBarrier.size > 0
			|| pendingLocalizedSubtitleSaves.size > 0
			|| Boolean(localizedSubtitleSaveTimer)
			|| Boolean(historyApplyingResultId)
			|| projectSessionController.hasPendingDraft();
	}

	function handlePageHide() {
		persistPlayhead(previewTimeMs);
		if (hasPendingSaveWork()) void flushPendingAutosave();
	}

	function handleBeforeUnload(event: BeforeUnloadEvent) {
		persistPlayhead(previewTimeMs);
		if (hasPendingSaveWork()) void flushPendingAutosave();
		if (!hasPendingDraftWork()) return;
		event.preventDefault();
		event.returnValue = '';
	}

	async function loadProjects() {
		loading = true;
		retryInitialProjectLoadOnApiRecovery = false;
		error = '';
		const urlProjectId = new URLSearchParams(window.location.search).get('project_id');
		let catalogError: unknown = null;
		const initialCatalog = projectCatalogController.load().catch((catalogLoadError) => {
				catalogError = catalogLoadError;
				return null;
			});
		try {
			if (urlProjectId) {
				projectId = urlProjectId;
				const loaded = await loadDraft(urlProjectId);
				if (loaded) {
					setProjectIdInUrl(urlProjectId);
					return;
				}
				await initialCatalog;
				if (catalogError) throw catalogError;
			} else {
				await initialCatalog;
				if (catalogError) throw catalogError;
			}
			projectId = resolveInitialProjectId(null, projectCatalogController.projects);
			if (projectId) {
				const loaded = await loadDraft(projectId);
				if (loaded) setProjectIdInUrl(projectId);
				else {
					projectId = '';
					clearProjectIdFromUrl();
				}
			} else if (urlProjectId) clearProjectIdFromUrl();
		} catch (e) {
			retryInitialProjectLoadOnApiRecovery = true;
			error = (e as Error).message || '加载项目失败';
		} finally {
			loading = false;
		}
	}

	function resetWorkspaceDetails() {
		loadedWorkspaceDetailSections.clear();
		workspaceDetailLoads.clear();
		semanticTtsGroups = [];
		timingConfirmationCurrentByCueId = new Map();
	}

	function applyWorkspaceCueTimingConfirmations(
		workspace: VideoLocalizationWorkspaceReadModel
	) {
		timingConfirmationCurrentByCueId = new Map(
			(workspace.cue_timing_confirmations ?? []).map((item) => [
				item.cue_id,
				item.confirmation_current
			])
		);
	}

	function withLoadedWorkspaceDetails(
		workspaceDraft: VideoLocalizationDraft
	): VideoLocalizationDraft {
		if (!draft || draftProjectId !== projectId) return workspaceDraft;
		return {
			...workspaceDraft,
			...(loadedWorkspaceDetailSections.has('transcription')
				? { transcription: draft.transcription }
				: {}),
			...(loadedWorkspaceDetailSections.has('reference_clips')
				? { reference_clips: draft.reference_clips }
				: {}),
			...(loadedWorkspaceDetailSections.has('generated_candidates')
				? { generated_candidates: draft.generated_candidates }
				: {}),
			...(loadedWorkspaceDetailSections.has('dubbing_production')
				? { dubbing_production: draft.dubbing_production }
				: {})
		};
	}

	async function ensureWorkspaceDetail(
		section: VideoLocalizationWorkspaceDetailSection
	) {
		if (!projectId || !draft || loadedWorkspaceDetailSections.has(section)) return;
		const existing = workspaceDetailLoads.get(section);
		if (existing) return existing;
		const loadingProjectId = projectId;
		const request = (async () => {
			try {
				const detail = await Api.videoLocalizationWorkspaceDetail(loadingProjectId, section);
				if (projectId !== loadingProjectId || !draft) return;
				if (section === 'transcription') {
					draft = { ...draft, transcription: detail.transcription };
				} else if (section === 'reference_clips') {
					draft = { ...draft, reference_clips: detail.reference_clips ?? [] };
				} else if (section === 'generated_candidates') {
					draft = { ...draft, generated_candidates: detail.generated_candidates ?? [] };
				} else {
					draft = { ...draft, dubbing_production: detail.dubbing_production ?? draft.dubbing_production };
				}
				loadedWorkspaceDetailSections.add(section);
			} catch (detailError) {
				if (projectId === loadingProjectId) {
					error = (detailError as Error).message || '加载项目详情失败';
				}
			} finally {
				workspaceDetailLoads.delete(section);
			}
		})();
		workspaceDetailLoads.set(section, request);
		return request;
	}

	async function loadDraft(nextProjectId = projectId): Promise<boolean> {
		if (draft && draftProjectId && hasPendingSaveWork()) {
			if (!(await flushPendingAutosave())) return false;
		}
		workspaceRevisionController.reset(nextProjectId);
		resetWorkspaceDetails();
		draftProjectId = '';
		historyPlacementSessionController.reset();
		historyApplyingResultId = '';
		draftConflictMergeBase = null;
		mediaHealth = null;
		operationFeedController.deactivate();
		operationDetailSessionController.invalidate();
		previewPlaybackRestored = false;
		ttsWorkflowSessionController.deactivate();
		ttsHistoryController.deactivate();
		cancelPendingLocalizedSubtitleSaves();
		timelineEditController = null;
		timelineUndoCount = 0;
		timelineRedoCount = 0;
		timelineControllerDeletedClipIds = new Set();
		resetProjectSelectionState();
		pendingTtsInitializations.clear();
		timelineRuntimeClips = [];
		ttsSubmissionQueue.reset();
		cueSaveRevision += 1;
		localizedSubtitleSaveEpoch += 1;
		localizedSubtitleSaveRevisions.clear();
		if (!nextProjectId) {
			projectDraftSessionController.invalidate();
			draft = null;
			draftConflictMergeBase = null;
			mediaHealth = null;
			previewTimeMs = 0;
			timelineZoom = 1;
			timelineViewportStartMs = 0;
			draftOnlyCueIds = [];
			timelineDeletedClipIds = new Set();
			timelineDirtyFieldsByClipId = new Map();
			timelineEditRevision = 0;
			timelineSavedRevision = 0;
			return true;
		}
		error = '';
		try {
			const loadedWorkspace = await projectDraftSessionController.load(
				nextProjectId,
				() => Api.videoLocalizationWorkspace(nextProjectId)
			);
			if (!loadedWorkspace) return false;
			mediaHealth = loadedWorkspace.media_health;
			semanticTtsGroups = loadedWorkspace.semantic_tts_groups ?? [];
			applyWorkspaceCueTimingConfirmations(loadedWorkspace);
			const editableDraft = withEditableMediaClips(loadedWorkspace.draft, mediaHealth);
			const sessionViewState = readTimelineViewState(window.sessionStorage, nextProjectId);
			const timelineViewState = resolveTimelineViewState(editableDraft.ui_state, sessionViewState);
			draft = editableDraft;
			draftConflictMergeBase = snapshotDraftForConflictMerge(editableDraft);
			draftProjectId = nextProjectId;
			workspaceRevisionController.activate(nextProjectId, loadedWorkspace.revision);
			timelineEditController = new TimelineEditController(editableDraft);
			timelineUndoCount = 0;
			timelineRedoCount = 0;
			ttsWorkflowFeedCache.reset(editableDraft.tts_tasks ?? []);
			ttsWorkflowSessionController.activate(nextProjectId, editableDraft.tts_tasks ?? []);
			timelineZoom = timelineViewState.timeline_zoom ?? 1;
			timelineViewportStartMs = timelineViewState.timeline_viewport_start_ms ?? 0;
			timelineDeletedClipIds = new Set();
			timelineDirtyFieldsByClipId = new Map();
			timelineEditRevision = 0;
			timelineSavedRevision = 0;
			draftOnlyCueIds = [];
			const savedCueId = typeof draft.ui_state?.selected_cue_id === 'string' ? draft.ui_state.selected_cue_id : '';
			selectedCueId = draft.cues.some((cue) => cue.cue_id === savedCueId) ? savedCueId : (draft.cues[0]?.cue_id ?? '');
			if (selectedCueId) updateTtsSelectionAnchor({ kind: 'source', itemId: selectedCueId });
			inspectorCollapsed = draft.ui_state?.sidebar_collapsed === true;
			inspectorWidth = clampNumber(draft.ui_state?.inspector_width, 320, 560, 380);
			inspectorSection = inspectorSectionOnProjectLoad();
			previewTimeMs = timelineViewState.playhead_ms ?? 0;
			await restorePreviewPlaybackState(nextProjectId);
			autoSaveStatus = draft.updated_at ? 'saved' : 'idle';
			await loadOperations(nextProjectId);
			if (projectId !== nextProjectId) return false;
			await loadTtsHistory(nextProjectId);
			if (projectId !== nextProjectId) return false;
			await refreshTtsWorkflowTasks(nextProjectId, 'load');
			if (projectId !== nextProjectId) return false;
			void initializePreviewCache(nextProjectId);
			return true;
		} catch (e) {
			if (projectId === nextProjectId) error = (e as Error).message || '加载草稿失败';
			return false;
		}
	}

	async function loadTtsHistory(nextProjectId = projectId) {
		if (!nextProjectId) {
			ttsHistoryController.deactivate();
			return;
		}
		try {
			await ttsHistoryController.loadProject(nextProjectId);
			if (projectId === nextProjectId) {
				const segmentId = groupedTimelineSegmentId(selectedTimelineAudioClip)
					|| selectedLocalizedSubtitle?.subtitle_id
					|| selectedCue?.cue_id
					|| '';
				if (segmentId) await ttsHistoryController.ensureSegment(segmentId, nextProjectId);
			}
		} catch {
			// Keep the last successful list during transient backend restarts.
		}
	}

	function applyTtsHistoryCoverage(items: HistoryItem[]) {
		if (!draft || ttsHistoryController.projectId !== draftProjectId) return;
		const coverageByIdentity = buildTtsCoverageByIdentity(items);
		draft = {
			...draft,
			timeline_clips: draft.timeline_clips.map((clip) => {
				const coverage = [clip.result_id, clip.task_id, clip.generation_id]
					.map((value) => value ? coverageByIdentity.get(String(value)) : undefined)
					.find((value): value is number => typeof value === 'number');
				return coverage === undefined ? clip : { ...clip, verification_coverage: coverage };
			})
		};
	}

	function ttsWorkflowActive(task: VideoLocalizationTtsTask) {
		return task.status === 'prepared' || task.status === 'queued' || task.status === 'running';
	}

	function stopTtsWorkflowPolling() {
		ttsWorkflowSessionController.stopPolling();
	}

	async function refreshTtsWorkflowTasks(
		nextProjectId = projectId,
		reason: TtsWorkflowSyncReason = 'manual'
	) {
		const context = ttsWorkflowSessionController.captureContext();
		if (!context || context.projectId !== nextProjectId) return null;
		return ttsWorkflowSessionController.sync(reason);
	}

	async function initializePreviewCache(nextProjectId: string) {
		await previewCacheSessionController.activate(nextProjectId, {
			sourceVideoAvailable: mediaAssetAvailable(mediaHealth, 'source_video'),
			playheadMs: previewTimeMs
		});
	}

	async function refreshPreviewCache() {
		if (!projectId || previewCacheRefreshing) return;
		playbackSessionController.refreshMediaCache();
		const result = await previewCacheSessionController.refresh();
		if (result.status === 'started' || result.status === 'completed') {
			message = result.message;
		} else if (result.status === 'failed') {
			error = result.error;
		} else {
			return;
		}
		scheduleMessageClear(2200);
	}

	function requestPreviewCacheAt(timeMs: number) {
		previewCacheSessionController.requestAt(timeMs);
	}

	function clearDevelopmentAsrPreview() {
		developmentAsrPreviewGeneration += 1;
		developmentAsrPreview = null;
		developmentAsrPreviewResolvedId = '';
		developmentAsrPreviewLoadingId = '';
	}

	async function syncDevelopmentAsrPreview(
		nextProjectId: string,
		nextOperations: VideoLocalizationOperation[]
	) {
		const operation = resolveDevelopmentAsrPreviewOperation(nextOperations);
		if (!operation) {
			if (developmentAsrPreview || developmentAsrPreviewResolvedId || developmentAsrPreviewLoadingId) {
				clearDevelopmentAsrPreview();
			}
			return;
		}
		if (
			operation.operation_id === developmentAsrPreviewResolvedId
			|| operation.operation_id === developmentAsrPreviewLoadingId
		) return;
		const generation = ++developmentAsrPreviewGeneration;
		developmentAsrPreviewResolvedId = '';
		developmentAsrPreviewLoadingId = operation.operation_id;
		try {
			const isInitialAnalysis = operation.parameters?.stop_after_step === 'initial_analysis';
			const isEntityNormalization = operation.parameters?.stop_after_step === 'normalize_entities';
			const isReviewDecisions = operation.parameters?.stop_after_step === 'review_decisions_r1';
			const isWholeRecheck = operation.parameters?.stop_after_step === 'whole_recheck_r1';
			const result = isInitialAnalysis
				? {
						kind: 'initial_analysis' as const,
						value: await Api.videoLocalizationDevelopmentInitialAnalysisResult(
							nextProjectId,
							operation.operation_id
						)
					}
				: isEntityNormalization
					? {
							kind: 'entity_normalization' as const,
							value: await Api.videoLocalizationDevelopmentEntityNormalizationResult(
								nextProjectId,
								operation.operation_id
							)
						}
				: isReviewDecisions
					? {
							kind: 'review_decisions' as const,
							value: await Api.videoLocalizationDevelopmentReviewDecisionsResult(
								nextProjectId,
								operation.operation_id
							)
						}
				: isWholeRecheck
					? {
							kind: 'whole_recheck' as const,
							value: await Api.videoLocalizationDevelopmentWholeRecheckResult(
								nextProjectId,
								operation.operation_id
							)
						}
				: {
						kind: 'asr' as const,
						value: await Api.videoLocalizationDevelopmentAsrResult(
							nextProjectId,
							operation.operation_id
						)
					};
			const currentOperation = resolveDevelopmentAsrPreviewOperation(operations);
			if (
				generation !== developmentAsrPreviewGeneration
				|| projectId !== nextProjectId
				|| currentOperation?.operation_id !== operation.operation_id
			) return;
			developmentAsrPreviewResolvedId = operation.operation_id;
			if (!draft) return;
			if (result.kind === 'initial_analysis') {
				if (developmentInitialAnalysisResultMatchesDraft(result.value, draft)) {
					developmentAsrPreview = buildDevelopmentInitialAnalysisPreview(operation, result.value);
				}
			} else if (result.kind === 'entity_normalization') {
				if (developmentEntityNormalizationResultMatchesDraft(result.value, draft)) {
					developmentAsrPreview = buildDevelopmentEntityNormalizationPreview(operation, result.value);
				}
			} else if (result.kind === 'review_decisions') {
				if (developmentReviewDecisionsResultMatchesDraft(result.value, draft)) {
					developmentAsrPreview = buildDevelopmentReviewDecisionsPreview(operation, result.value);
				}
			} else if (result.kind === 'whole_recheck') {
				if (developmentWholeRecheckResultMatchesDraft(result.value, draft)) {
					developmentAsrPreview = buildDevelopmentWholeRecheckPreview(operation, result.value);
				}
			} else if (developmentAsrResultMatchesDraft(result.value, draft)) {
				developmentAsrPreview = buildDevelopmentAsrPreview(operation, result.value);
			}
		} catch {
			// The compact operation summary remains usable if the temporary
			// development snapshot has already been removed.
		} finally {
			if (
				generation === developmentAsrPreviewGeneration
				&& developmentAsrPreviewLoadingId === operation.operation_id
			) {
				developmentAsrPreviewLoadingId = '';
			}
		}
	}

	async function loadOperations(nextProjectId = projectId) {
		if (!nextProjectId) {
			operationFeedController.deactivate();
			return;
		}
		await operationFeedController.loadProject(nextProjectId);
	}

	async function loadActivityTaskDetail(
		expectedProjectId: string,
		operationId: string
	): Promise<ActivityTask | null> {
		const operation = await operationDetailSessionController.refresh(
			expectedProjectId,
			() => Api.videoLocalizationOperation(expectedProjectId, operationId)
		);
		return operation ? operationActivityTask(operation) : null;
	}

	async function selectProject(nextProjectId: string) {
		if (!nextProjectId || nextProjectId === projectId) {
			projectMenuOpen = false;
			return;
		}
		if (!(await flushPendingAutosave())) return;
		clearProjectRuntimeState();
		projectId = nextProjectId;
		setProjectIdInUrl(projectId);
		editingProjectName = false;
		projectMenuOpen = false;
		await loadDraft(projectId);
	}

	async function deleteHistoryProject(project: ProjectSummary, event: MouseEvent) {
		event.stopPropagation();
		if (projectDeleteQueue.has(project.project_id)) {
			message = `项目「${project.name}」已经在删除中`;
			scheduleMessageClear(1800);
			return;
		}
		const confirmed = window.confirm(
			`确定彻底删除项目「${project.name}」吗？\n\n项目草稿、源视频、字幕、配音、导出文件、生成记录和相关缓存都会永久删除，无法恢复。`
		);
		if (!confirmed) return;
		const deletingCurrentProject = project.project_id === projectId;
		if (
			deletingCurrentProject
			&& (deletingProjectId || queuedProjectDeleteIds.length)
		) {
			error = '请等待历史项目删除队列完成后，再删除当前项目';
			return;
		}
		if (
			!deletingCurrentProject
			&& pendingProjectDeleteIds.has(projectId)
		) {
			error = '当前项目正在删除，暂时不能继续添加项目';
			return;
		}
		if (deletingCurrentProject && !(await flushPendingAutosave())) {
			error = '当前项目仍有未保存修改，请先处理保存失败后再删除';
			return;
		}
		if (deletingCurrentProject) cancelPendingAutosave();
		error = '';
		const enqueued = projectDeleteQueue.enqueue(
			project.project_id,
			async () => {
				const deleted = await projectCatalogController.delete(project.project_id);
				if (!deleted) return;
				if (projectId === project.project_id) {
					cancelPendingAutosave();
					clearProjectRuntimeState();
					clearProjectIdFromUrl();
					projectId = projects[0]?.project_id ?? '';
					if (projectId) {
						setProjectIdInUrl(projectId);
						await loadDraft(projectId);
					}
				}
				projectMenuOpen = projects.length > 0;
				message = `项目「${project.name}」已从列表删除，相关文件正在后台清理`;
				scheduleMessageClear(2400);
			}
		);
		if (
			enqueued
			&& deletingProjectId
			&& deletingProjectId !== project.project_id
		) {
			message = `项目「${project.name}」已加入删除队列`;
			scheduleMessageClear(1800);
		}
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
			const syncedProjects = await projectCatalogController.sync(projectId);
			if (!syncedProjects) return;
			const activeProject = projects.find((project) => project.project_id === projectId);
			if (activeProject && !activeProject.has_local_package) {
				message = '当前项目目录暂不可用，已保留当前编辑；目录恢复后可重新同步';
				scheduleMessageClear(2200);
			}
		} catch (e) {
			error = (e as Error).message || '同步本地项目失败';
		} finally {
			projectMenuSyncing = false;
		}
	}

	async function flushPendingAutosave() {
		persistPlayhead(previewTimeMs);
		await historyPlacementSessionController.waitForPending();
		while (pendingLocalizedSubtitleSaves.size > 0 || localizedSubtitleSaveBarrier.size > 0) {
			if (!(await flushPendingLocalizedSubtitleSaves())) return false;
			if (!(await localizedSubtitleSaveBarrier.flush())) return false;
		}
		return projectSessionController.flush();
	}

	function cancelPendingAutosave() {
		projectSessionController.discardPending();
	}

	function cancelPendingLocalizedSubtitleSaves() {
		if (localizedSubtitleSaveTimer) clearTimeout(localizedSubtitleSaveTimer);
		localizedSubtitleSaveTimer = null;
		pendingLocalizedSubtitleSaves.clear();
		localizedSubtitleSaveEpoch += 1;
		localizedSubtitleSaveRevisions.clear();
	}

	function nextLocalizedSubtitleSaveRevision(subtitleId: string) {
		return nextSubtitleSaveRevision(localizedSubtitleSaveRevisions, subtitleId);
	}

	function localizedSubtitleSaveIsCurrent(subtitleId: string, revision: number, epoch: number) {
		return subtitleSaveRevisionIsCurrent(
			localizedSubtitleSaveRevisions,
			subtitleId,
			revision,
			epoch,
			localizedSubtitleSaveEpoch
		);
	}

	function invalidateLocalizedSubtitleSaves() {
		localizedSubtitleSaveEpoch += 1;
		localizedSubtitleSaveRevisions.clear();
	}

	function resetProjectSelectionState() {
		selectedCueId = '';
		selectedLocalizedSubtitleId = '';
		selectedProvisionalSubtitle = null;
		selectedTimelineAudioClipId = '';
		timelineSelectionItems = [];
		ttsSelectionSession = { ...EMPTY_TTS_SELECTION_SESSION };
		timelineSelectionRange = null;
		audioSelectionRange = null;
		playbackSessionController.updateSelectionRange(null);
	}

	function clearProjectRuntimeState() {
		historyPlacementSessionController.reset();
		workspaceRevisionController.reset();
		operationFeedController.deactivate();
		if (previewPlaying) playbackSessionController.playPause();
		ttsWorkflowSessionController.deactivate();
		ttsHistoryController.deactivate();
		cancelPendingLocalizedSubtitleSaves();
		draft = null;
		timelineRuntimeClips = [];
		draftConflictMergeBase = null;
		mediaHealth = null;
		draftOnlyCueIds = [];
		resetProjectSelectionState();
		pendingTtsInitializations.clear();
		ttsSubmissionQueue.reset();
		foregroundTasks = [];
		pendingTtsSubmissionCount = 0;
		preparingTtsHandoff = false;
		ttsTaskAction = null;
		historyApplyingResultId = '';
		draggingTtsHistory = null;
		message = '';
		error = '';
		timelineDeletedClipIds = new Set();
		timelineControllerDeletedClipIds = new Set();
		timelineDirtyFieldsByClipId = new Map();
		timelineEditRevision = 0;
		timelineSavedRevision = 0;
		timelineEditController = null;
		timelineUndoCount = 0;
		timelineRedoCount = 0;
		previewTimeMs = 0;
		previewCacheSessionController.deactivate();
	}

	function clearProjectIdFromUrl() {
		const url = new URL(window.location.href);
		url.searchParams.delete('project_id');
		replaceState(url, {});
	}

	function setProjectIdInUrl(nextProjectId: string) {
		const url = new URL(window.location.href);
		if (nextProjectId) url.searchParams.set('project_id', nextProjectId);
		else url.searchParams.delete('project_id');
		replaceState(url, {});
	}

	function closeProjectMenuFromPage(event: PointerEvent) {
		if (!(event.target as HTMLElement | null)?.closest('.project-switcher')) projectMenuOpen = false;
	}

	async function toggleDeliveryMenu(event: MouseEvent) {
		event.stopPropagation();
		if (deliveryMenuOpen) {
			deliveryMenuOpen = false;
			mediaExportRequest = null;
			mediaExportAvailability = null;
			exportDestination = null;
			exportProgress = 0;
			exportStage = '';
			exportedFilename = '';
			exportOutputFilename = '';
			exportFilenameUserEdited = false;
			return;
		}
		exportError = '';
		const {
			buildMediaExportAvailability,
			buildDefaultMediaExportRequest
		} = await import('./export-options');
		const availability = buildMediaExportAvailability(draft, mediaHealth, dubLaneLayout.lanes);
		mediaExportAvailability = availability;
		mediaExportRequest = buildDefaultMediaExportRequest({
			kind: 'video',
			trackStates,
			dubLaneStates,
			subtitleVisibility: {
				asr: subtitleDisplay.tracks.asr.visible,
				localized: subtitleDisplay.tracks.localized.visible
			},
			localizedSubtitleVariant: dubSubtitleDisplayEnabled
				? 'dub'
				: 'localized',
			availability
		});
		const initialExportRequest = mediaExportRequest;
		exportOutputFilename = '';
		exportFilenameUserEdited = false;
		deliveryMenuOpen = true;
		projectMenuOpen = false;
		if (projectId) {
			selectingExportDestination = true;
			try {
				const { VideoLocalizationExportController } = await import('./export-controller');
				const controller = new VideoLocalizationExportController();
				const [selected, filenamePreview] = await Promise.all([
					controller.selectDestination(projectId, 'default'),
					controller.defaultFilename(projectId, initialExportRequest)
				]);
				exportDestination = selected.status === 'selected'
					&& selected.destination_id
					&& selected.display_path
					? {
						destination_id: selected.destination_id,
						display_path: selected.display_path
					}
					: null;
				exportOutputFilename = filenamePreview.output_filename;
			} catch (e) {
				exportError = (e as Error).message || '准备导出设置失败';
			} finally {
				selectingExportDestination = false;
			}
		}
	}

	function closeDeliveryDialog() {
		if (exportingDelivery) return;
		deliveryMenuOpen = false;
		mediaExportRequest = null;
		mediaExportAvailability = null;
		exportDestination = null;
		exportProgress = 0;
		exportStage = '';
		exportedFilename = '';
		exportOutputFilename = '';
		exportFilenameUserEdited = false;
		exportError = '';
	}

	function handlePageKeydown(event: KeyboardEvent) {
		if (
			(event.ctrlKey || event.metaKey)
			&& event.key.toLowerCase() === 's'
			&& !event.altKey
			&& !event.repeat
			&& !event.isComposing
		) {
			event.preventDefault();
			event.stopPropagation();
			void saveDraftManually();
			return;
		}
		if (event.key === 'Escape') {
			projectMenuOpen = false;
			if (!exportingDelivery) closeDeliveryDialog();
			return;
		}
		if (event.code !== 'Space' || event.repeat || event.isComposing || event.metaKey || event.ctrlKey || event.altKey) return;
		const target = event.target as HTMLElement | null;
		if (target?.isContentEditable || target?.closest('input,textarea,select,[contenteditable="true"],[role="textbox"]')) return;
		event.preventDefault();
		event.stopPropagation();
		handleTimelineTransport('play-pause');
	}

	async function saveDraftManually() {
		if (!projectId || !draft) return;
		error = '';
		try {
			// Manual save drains the same pending owners as autosave. It must not
			// create a full-workspace write just because the shortcut was pressed.
			if (!(await flushPendingAutosave())) throw new Error(error || '手动保存失败');
			message = '已手动保存';
			scheduleMessageClear(1800);
		} catch (saveError) {
			error = (saveError as Error).message || '手动保存失败';
		}
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
		const targetProjectId = projectId;
		try {
			if (!(await flushPendingAutosave())) throw new Error('项目仍有未保存修改，请先重试保存');
			const updated = await projectCatalogController.rename(targetProjectId, nextName);
			if (!updated || projectId !== targetProjectId) return;
			await loadDraft(targetProjectId);
			editingProjectName = false;
			message = '项目名称和本地目录已更新';
			scheduleMessageClear(1800);
		} catch (e) {
			error = (e as Error).message || '修改项目名称失败';
		} finally {
			projectNameSaving = false;
		}
	}

	async function autoNameProject() {
		if (!projectId || !selectedProject || projectAutoNaming) return;
		projectAutoNaming = true;
		error = '';
		const targetProjectId = projectId;
		try {
			if (!(await flushPendingAutosave())) throw new Error('项目仍有未保存修改，请先重试保存');
			const updated = await projectCatalogController.autoName(targetProjectId);
			if (!updated || projectId !== targetProjectId) return;
			await loadDraft(targetProjectId);
			message = '项目已自动命名，本地目录同步更新';
			scheduleMessageClear(1800);
		} catch (e) {
			error = (e as Error).message || '自动命名失败';
		} finally {
			projectAutoNaming = false;
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

	async function importVideoFile(file: File | null | undefined) {
		if (!file || importing) return;
		importing = true;
		error = '';
		try {
			if (hasPendingDraftWork() && !(await flushPendingAutosave())) {
				throw new Error('当前项目仍有未保存修改，请重试后再导入新视频');
			}
			const projectName = defaultLocalizationProjectName(file);
			const project = await projectCatalogController.create(
				projectName,
				'外文视频中文配音草稿'
			);
			clearProjectRuntimeState();
			projectId = project.project_id;
			workspaceRevisionController.reset(projectId);
			setProjectIdInUrl(projectId);
			inspectorCollapsed = false;
			inspectorSection = 'tasks';
			taskCenterPulseKey += 1;
			const targetProjectId = project.project_id;
			const imported = await projectDraftSessionController.mutate(
				targetProjectId,
				() => Api.importVideoLocalizationSource(targetProjectId, file)
			);
			if (!imported) return;
			await refreshDraftOnly();
			void initializePreviewCache(targetProjectId);
			if (!mediaAssetAvailable(mediaHealth, 'source_audio')) {
				const operation = await operationFeedController.submit(targetProjectId, 'source_audio');
				if (!operation) return;
				message = '视频已导入，原音轨抽取已开始';
			} else {
				message = '视频已导入';
			}
			scheduleMessageClear(1800);
		} catch (e) {
			error = (e as Error).message || '导入视频失败';
		} finally {
			importing = false;
			if (videoInput) videoInput.value = '';
		}
	}

	async function extractSourceAudio() {
		if (!projectId || !mediaAssetAvailable(mediaHealth, 'source_video')) return;
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
			const disabledTracks = Array.isArray(draft.ui_state?.disabled_media_tracks)
				? draft.ui_state.disabled_media_tracks.map(String).filter((trackId) => trackId !== 'original')
				: [];
			if (mediaAssetAvailable(mediaHealth, 'source_audio')) {
				const restoredDraft = withEditableMediaClips({
					...draft,
					timeline_clips: draft.timeline_clips.filter((clip) => clip.track_id !== 'original'),
					ui_state: { ...draft.ui_state, disabled_media_tracks: disabledTracks }
				}, mediaHealth);
				const savedDraft = await mutateCurrentProjectDraft(
					(activeProjectId) => Api.saveVideoLocalizationWorkspace(activeProjectId, restoredDraft)
				);
				if (!savedDraft) return;
				autoSaveStatus = 'saved';
				message = '原音轨已重新载入';
				scheduleMessageClear(1800);
				return;
			}
			if (!mediaAssetAvailable(mediaHealth, 'source_video')) {
				throw new Error('原音频和原视频文件都不可用，请重新导入视频');
			}
			draft = {
				...draft,
				timeline_clips: draft.timeline_clips.filter((clip) => clip.track_id !== 'original'),
				ui_state: { ...draft.ui_state, disabled_media_tracks: disabledTracks }
			};
			const savedDraft = await mutateCurrentProjectDraft(
				(activeProjectId) => Api.saveVideoLocalizationWorkspace(activeProjectId, draft!)
			);
			if (!savedDraft) return;
			await submitMediaOperation('source_audio', '原音频文件缺失，已开始从视频重新抽取');
		} catch (e) {
			error = (e as Error).message || '恢复原音轨失败';
		} finally {
			extractingAudio = false;
		}
	}

	async function transcribeEnglishSource(
		sourceTrackId: 'vocals' | 'dub' | 'original' = 'vocals',
		includeSpeakerGrouping = true
	) {
		const sourceReady = sourceTrackId === 'vocals'
			? mediaAssetAvailable(mediaHealth, 'vocals')
			: sourceTrackId === 'dub'
				? Boolean(draft?.timeline_clips.some((clip) => clip.track_id === 'dub' && clip.audio_path))
				: mediaAssetAvailable(mediaHealth, 'source_audio');
		if (!projectId || !sourceReady || operationBusy('english_asr')) return;
		if (!(await flushPendingAutosave())) {
			error = '存在未保存的字幕修改，请先处理保存错误后再开始听写。';
			return;
		}
		transcribingAsr = true;
		error = '';
		try {
			await submitMediaOperation(
				'english_asr',
				includeSpeakerGrouping
					? '正在生成字幕并自动判断是否需要区分说话人'
					: '正在从人声轨生成 ASR 字幕',
				buildSourceAsrOperationParameters(
					sourceTrackId,
					sourceTrackId === 'dub'
						? 'zh'
						: (draft?.language_config?.source_language || 'auto'),
					includeSpeakerGrouping
				)
			);
		} catch (e) {
			error = (e as Error).message || '提交字幕听写失败';
		} finally {
			transcribingAsr = false;
		}
	}

	async function generateAsrFromTimeline() {
		if (!draft) return;
		if (draft.cues.length) {
			const confirmed = window.confirm('重新生成会完整替换当前 ASR 字幕。是否继续？');
			if (!confirmed) return;
		}
		asrSetupOpen = true;
		speakerGroupingHealthLoading = true;
		speakerGroupingAvailable = false;
		speakerGroupingStatus = '正在检查说话人模型…';
		try {
			const installations = await Api.engineInstallations();
			const speakerEngine = installations.find(
				(item) => item.engine_id === SPEAKER_DIARIZATION_ENGINE_ID
			);
			speakerGroupingAvailable = Boolean(speakerEngine?.installed);
			speakerGroupingStatus = speakerGroupingAvailable
				? '可用：会与听写并行，结果是匿名声纹分组，后续仍需核对角色。'
				: '当前未安装 MOSS 说话人模型，所以不能开启；纯字幕识别不受影响。';
		} catch {
			speakerGroupingStatus = '暂时无法确认说话人模型状态；仍可正常生成字幕。';
		} finally {
			speakerGroupingHealthLoading = false;
		}
	}

	async function startSourceAsr(includeSpeakerGrouping: boolean) {
		if (includeSpeakerGrouping && !speakerGroupingAvailable) return;
		asrSetupOpen = false;
		await transcribeEnglishSource('vocals', includeSpeakerGrouping);
	}

	async function generateLocalizationFromTimeline() {
		if (!projectId || !draft?.cues.length || operationBusy('localization_draft')) return;
		if (draft.localized_subtitles.length) {
			const confirmed = window.confirm('重新生成会替换当前本土化字幕，上屏字幕和配音台词都会更新。是否继续？');
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
			await submitMediaOperation('localization_draft', '本土化字幕任务已开始', {
				source_language: draft.language_config?.detected_source_language || draft.transcription?.language || 'en',
				target_language: draft.language_config?.target_language || 'zh-Hans'
			}, pendingTaskId);
		} catch (e) {
			error = (e as Error).message || '提交本土化字幕任务失败';
		} finally {
			endPendingOperation(pendingTaskId);
		}
	}

	async function generateDubSubtitlesFromTimeline() {
		const regenerationMode: 'auto' | 'full' = requestedDubSubtitleFullRegeneration ? 'full' : 'auto';
		requestedDubSubtitleFullRegeneration = false;
		if (!projectId || operationBusy('dub_subtitle_generation')) return;
		if (!dubSubtitleGenerationSource.hasContent && !hasDirtyDubSubtitleScope) {
			error = dubSubtitleGenerationSource.unavailableReason;
			return;
		}
		if (dubSubtitleGenerationSource.allContentLanesMuted && !hasDirtyDubSubtitleScope) {
			const mutedMessage = '所有合成配音轨均已静音，请先打开至少一条';
			error = mutedMessage;
			window.alert(mutedMessage);
			return;
		}
		const pendingTaskId = beginPendingOperation('dub_subtitle_generation', '正在保存修改并提交任务');
		error = '';
		message = '正在提交合成配音字幕识别任务';
		try {
			if (!(await flushPendingAutosave())) {
				error = '存在未保存的项目修改，请先处理保存错误后再识别合成配音字幕。';
				return;
			}
			await submitMediaOperation(
				'dub_subtitle_generation',
				'合成配音字幕识别任务已开始',
				{
					engine_id: DEFAULT_ASR_ENGINE_ID,
					regeneration_mode: regenerationMode,
					execution_mode: 'full'
				},
				pendingTaskId
			);
			dubSubtitleDisplayProjectId = projectId;
		} catch (e) {
			error = (e as Error).message || '提交合成配音字幕识别任务失败';
		} finally {
			endPendingOperation(pendingTaskId);
		}
	}

	async function generateSemanticTtsGroups() {
		if (!projectId || !draft?.localized_subtitles.length || operationBusy('semantic_tts_grouping')) return;
		const pendingTaskId = beginPendingOperation('semantic_tts_grouping', '正在保存字幕并提交语义分组');
		error = '';
		message = '正在提交语义分组任务';
		try {
			if (!(await flushPendingAutosave())) {
				error = '存在未保存的字幕修改，请先处理保存错误后再执行语义成组。';
				return;
			}
			await submitMediaOperation(
				'semantic_tts_grouping',
				'语义分组任务已开始',
				{ target_chars: 120, max_chars: 180 },
				pendingTaskId
			);
		} catch (e) {
			error = (e as Error).message || '提交语义分组任务失败';
		} finally {
			endPendingOperation(pendingTaskId);
		}
	}

	function selectSemanticTtsGroup(groupId: string) {
		if (!draft) return;
		const group = semanticTtsGroups.find((item) => item.group_id === groupId);
		if (!group) return;
		const groupOrdinal = semanticTtsGroupOrdinal(semanticTtsGroups, groupId);
		if (groupOrdinal === null) return;
		const selected = group.subtitle_ids
			.map((subtitleId) => draft?.localized_subtitles.find((item) => item.subtitle_id === subtitleId) ?? null)
			.filter((item): item is VideoLocalizationSubtitleCue => Boolean(item));
		if (!selected.length) return;
		timelineSelectionItems = selected.map((item) => ({
			kind: 'subtitle',
			trackId: 'localizedSubtitles',
			itemId: item.subtitle_id
		}));
		selectedLocalizedSubtitleId = selected[0].subtitle_id;
		ttsSelectionSession = resolveTtsSelectionSession({
			anchor: { kind: 'target', itemId: selected[0].subtitle_id },
			selectionItems: timelineSelectionItems,
			cues: draft.cues,
			localizedSubtitles: draft.localized_subtitles
		});
		selectedTimelineAudioClipId = '';
		if (selected[0].linked_cue_id) selectedCueId = selected[0].linked_cue_id;
		seekTimeline(selected[0].start_ms);
		focusInspector('dubbing');
		message = `已选中第 ${groupOrdinal} 组，共 ${selected.length} 条字幕`;
		scheduleMessageClear(1800);
	}

	async function separateStems() {
		if (!projectId || !mediaAssetAvailable(mediaHealth, 'source_audio')) return;
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
		const targetProjectId = projectId;
		openTaskCenter();
		const taskId = pendingTaskId || beginPendingOperation(kind);
		try {
			const operation = await operationFeedController.submit(targetProjectId, kind, parameters);
			if (!operation) return;
			message = successMessage;
			scheduleMessageClear(1800);
			void refreshDraftOnly().catch((refreshError) => {
				error = (refreshError as Error).message || '任务已开始，但刷新项目状态失败，正在继续同步';
			});
		} catch (e) {
			if (projectId === targetProjectId) await loadOperations(targetProjectId);
			throw e;
		} finally {
			endPendingOperation(taskId);
		}
	}

	async function cancelOperation(operation: VideoLocalizationOperation) {
		if (!projectId || !isActiveOperation(operation) || operationAction) return;
		const targetProjectId = projectId;
		operationAction = { id: operation.operation_id, kind: 'cancel' };
		error = '';
		try {
			const updated = await operationFeedController.cancel(targetProjectId, operation.operation_id);
			if (!updated) return;
			message = updated.status === 'cancelled' ? '任务已取消' : '已请求取消，正在等待当前步骤安全结束';
		} catch (e) {
			error = (e as Error).message || '取消任务失败';
		} finally {
			operationAction = null;
		}
	}

	async function retryOperation(operation: VideoLocalizationOperation) {
		if (!projectId || isActiveOperation(operation) || operationAction) return;
		const targetProjectId = projectId;
		operationAction = { id: operation.operation_id, kind: 'retry' };
		error = '';
		try {
			const retry = await operationFeedController.retry(targetProjectId, operation.operation_id);
			if (!retry) return;
			message = '任务已重新提交';
			scheduleMessageClear(1800);
		} catch (e) {
			error = (e as Error).message || '重试任务失败';
		} finally {
			operationAction = null;
		}
	}

	async function openProjectDirectory() {
		if (!projectId) return;
		openingProjectDirectory = true;
		error = '';
		try {
			await Api.openVideoLocalizationProjectDirectory(projectId);
			message = '已打开项目文件目录';
			scheduleMessageClear(1800);
		} catch (e) {
			error = (e as Error).message || '打开项目目录失败';
		} finally {
			openingProjectDirectory = false;
		}
	}

	async function chooseExportDestination() {
		if (!projectId || exportingDelivery) return;
		selectingExportDestination = true;
		exportError = '';
		try {
			const { VideoLocalizationExportController } = await import('./export-controller');
			const selected = await new VideoLocalizationExportController()
				.selectDestination(projectId, 'choose');
			if (selected.status === 'cancelled') return;
			if (!selected.destination_id || !selected.display_path) {
				throw new Error('没有取得可用的保存目录');
			}
			exportDestination = {
				destination_id: selected.destination_id,
				display_path: selected.display_path
			};
		} catch (e) {
			exportError = (e as Error).message || '选择保存目录失败';
		} finally {
			selectingExportDestination = false;
		}
	}

	async function refreshExportOutputFilename(
		request: MediaExportRequest,
		{ force = false }: { force?: boolean } = {}
	) {
		if (!projectId || (!force && exportFilenameUserEdited)) return;
		const requestedProjectId = projectId;
		const requestSequence = ++exportFilenameRequestSequence;
		try {
			const { VideoLocalizationExportController } = await import('./export-controller');
			const preview = await new VideoLocalizationExportController()
				.defaultFilename(requestedProjectId, request);
			if (
				projectId !== requestedProjectId
				|| requestSequence !== exportFilenameRequestSequence
				|| (!force && exportFilenameUserEdited)
			) return;
			exportOutputFilename = preview.output_filename;
		} catch (e) {
			exportError = (e as Error).message || '生成默认文件名失败';
		}
	}

	function updateExportRequest(request: MediaExportRequest) {
		mediaExportRequest = request;
		void refreshExportOutputFilename(request);
	}

	function updateExportOutputFilename(value: string) {
		exportOutputFilename = value;
		exportFilenameUserEdited = true;
		exportedFilename = '';
	}

	async function exportSelectedMedia() {
		if (
			!projectId
			|| !draft
			|| !mediaExportRequest
			|| !exportDestination
			|| !exportOutputFilename.trim()
		) return;
		const exportingProjectId = projectId;
		const request = { ...mediaExportRequest };
		const destination = { ...exportDestination };
		const outputFilename = exportOutputFilename.trim();
		exportingDelivery = true;
		error = '';
		exportError = '';
		exportProgress = 0;
		exportStage = '正在提交导出任务';
		exportedFilename = '';
		try {
			const { VideoLocalizationExportController } = await import('./export-controller');
			const videoExportController = new VideoLocalizationExportController();
			let operation = await runProjectCommandAfterSave({
				flushPendingSave: flushPendingAutosave,
				command: () => videoExportController.startMediaExport(
					exportingProjectId,
					destination.destination_id,
					outputFilename,
					request
				),
				saveFailureMessage: '项目修改尚未保存，请重试'
			});
			await loadOperations(exportingProjectId);
			while (operation.status === 'queued' || operation.status === 'running') {
				exportProgress = Math.max(exportProgress, operation.progress);
				exportStage = String(operation.result_summary?.stage || '正在渲染');
				await new Promise((resolve) => window.setTimeout(resolve, 750));
				operation = await videoExportController.operation(
					exportingProjectId,
					operation.operation_id
				);
			}
			exportProgress = Math.max(exportProgress, operation.progress);
			exportStage = String(operation.result_summary?.stage || '');
			await loadOperations(exportingProjectId);
			if (operation.status !== 'success') {
				throw new Error(
					operation.error_message
					|| (operation.status === 'cancelled' ? '导出已取消' : '导出失败')
				);
			}
			exportedFilename = String(operation.result_summary?.filename || '');
			message = request.kind === 'video'
				? '合成视频已保存'
				: request.kind === 'audio'
					? '混合音频已保存'
					: '字幕文件已保存';
			scheduleMessageClear(2400);
		} catch (e) {
			exportError = (e as Error).message || '导出成品失败';
			error = exportError;
		} finally {
			exportingDelivery = false;
		}
	}

	function selectCue(cueId: string) {
		selectedTimelineAudioClipId = '';
		selectedLocalizedSubtitleId = '';
		selectedProvisionalSubtitle = null;
		selectedCueId = cueId;
		updateDraftUiState({ selected_cue_id: cueId });
		focusInspector('subtitle');
		void ttsHistoryController.ensureSegment(cueId);
	}

	function selectSubtitleDisplayCue(cue: SubtitleDisplayCue) {
		if (cue.editable) {
			if (cue.source === 'asr') selectCue(cue.id);
			else selectLocalizedSubtitle(cue.id);
			return;
		}
		selectedTimelineAudioClipId = '';
		selectedCueId = '';
		selectedLocalizedSubtitleId = '';
		selectedProvisionalSubtitle = { source: cue.source, id: cue.id };
		timelineSelectionItems = [];
		ttsSelectionSession = { ...EMPTY_TTS_SELECTION_SESSION };
		seekPreview(cue.start_ms);
		focusInspector('subtitle');
	}

	function toggleDubSubtitleDisplay() {
		if (!projectId || !draft?.dub_subtitles?.length) return;
		const enabling = !dubSubtitleDisplayEnabled;
		dubSubtitleDisplayProjectId = enabling ? projectId : '';
		updateDraftUiState({
			subtitle_display_mode: enabling ? 'dub' : 'localized'
		});
		selectedLocalizedSubtitleId = '';
		selectedProvisionalSubtitle = null;
		timelineSelectionItems = [];
		ttsSelectionSession = { ...EMPTY_TTS_SELECTION_SESSION };
		message = enabling
			? `正在查看 ${draft.dub_subtitles.length} 条合成配音字幕`
			: '已切换到本土化上屏字幕';
		scheduleMessageClear(2400);
	}

	function updateTtsSelectionAnchor(anchor: TtsSelectionAnchor, items: TimelineSelectionItem[] = [], additive = false) {
		if (!draft) {
			ttsSelectionSession = { ...EMPTY_TTS_SELECTION_SESSION };
			return;
		}
		ttsSelectionSession = updateTtsSelectionSession({
			current: ttsSelectionSession,
			clicked: anchor,
			selectionItems: items,
			additive,
			cues: draft.cues,
			localizedSubtitles: draft.localized_subtitles
		});
	}

	function updateTimelineSelection(items: TimelineSelectionItem[]) {
		timelineSelectionItems = items;
		if (!items.length) ttsSelectionSession = { ...EMPTY_TTS_SELECTION_SESSION };
	}

	function selectLocalizedSubtitle(subtitleId: string) {
		selectedTimelineAudioClipId = '';
		selectedProvisionalSubtitle = null;
		selectedLocalizedSubtitleId = subtitleId;
		const subtitle = draft?.localized_subtitles.find((item) => item.subtitle_id === subtitleId);
		if (draft && subtitle) {
			ttsSelectionSession = ensureDirectTtsTargetSelection({
				current: ttsSelectionSession,
				subtitleId,
				selectionItems: timelineSelectionItems,
				cues: draft.cues,
				localizedSubtitles: draft.localized_subtitles
			});
		}
		if (subtitle?.linked_cue_id) selectedCueId = subtitle.linked_cue_id;
		dubbingHistoryScope = 'all';
		focusInspector('dubbing');
		void ttsHistoryController.ensureSegment(subtitleId);
	}

	function selectTimelineAudioClip(clipId: string | null) {
		selectedTimelineAudioClipId = clipId ?? '';
		if (!clipId) return;
		const clip = draft?.timeline_clips.find((item) => item.clip_id === clipId);
		if (!clip || clip.track_id !== 'dub') return;
		if (clip.status === 'failed' && clip.generation_error_message) {
			error = String(clip.generation_error_message);
		}
		focusInspector('dubbing');
		const historySegmentId = groupedTimelineSegmentId(clip) || clip.subtitle_id || clip.cue_id || '';
		if (historySegmentId) void ttsHistoryController.ensureSegment(historySegmentId);
		if (clip.subtitle_id) {
			selectedLocalizedSubtitleId = clip.subtitle_id;
			if (draft?.localized_subtitles.some((item) => item.subtitle_id === clip.subtitle_id)) {
				updateTtsSelectionAnchor({ kind: 'target', itemId: clip.subtitle_id });
			}
			const subtitle = draft?.localized_subtitles.find((item) => item.subtitle_id === clip.subtitle_id);
			if (subtitle?.linked_cue_id) selectedCueId = subtitle.linked_cue_id;
			return;
		}
		if (clip.cue_id) {
			selectedCueId = clip.cue_id;
			selectedLocalizedSubtitleId = '';
			updateTtsSelectionAnchor({ kind: 'source', itemId: clip.cue_id });
		}
	}

	function updateSelectedCue(patch: Partial<VideoLocalizationCue>) {
		if (!draft || !selectedCue || subtitleRuntimeBusy) return;
		const nextCues = draft.cues.map((cue) => {
				if (cue.cue_id !== selectedCue.cue_id) return cue;
				const normalizedCue = normalizeCueTimePatch({ ...cue, ...patch }, patch);
				return protectCueManualEdit(cue, normalizedCue, {
					text: 'en_subtitle_text' in patch,
					timing: 'start_ms' in patch || 'end_ms' in patch
				});
			});
		dispatchTimelineEditDeferred({
			type: 'transaction',
			label: '编辑 ASR 字幕',
			replaceCues: nextCues
		});
	}

	function updateCueTimeFromTimeline(cueId: string, startMs: number, endMs: number) {
		if (!projectId || !draft || subtitleRuntimeBusy) return;
		const normalizedStart = Math.max(0, Math.round(startMs));
		const normalizedEnd = Math.max(normalizedStart + MIN_SUBTITLE_DURATION_MS, Math.round(endMs));
		moveTimelineItemsFromTimeline([{
			kind: 'subtitle',
			trackId: 'subtitles',
			itemId: cueId,
			startMs: normalizedStart,
			endMs: normalizedEnd
		}]);
	}

	function updateLocalizedSubtitleTime(subtitleId: string, startMs: number, endMs: number) {
		return localizedSubtitleSaveBarrier.track(
			editorialSaveQueue.run(() => persistLocalizedSubtitleTime(subtitleId, startMs, endMs))
		);
	}

	function adoptLocalizedSubtitleMutation(
		result: VideoLocalizationTimelineMutationResponse,
		subtitleId: string,
		previousSubtitle: VideoLocalizationSubtitleCue,
		label: string
	) {
		if (!draft) return false;
		const previousDraft = {
			...draft,
			localized_subtitles: draft.localized_subtitles.map((subtitle) =>
				subtitle.subtitle_id === subtitleId ? previousSubtitle : subtitle
			)
		};
		const applied = applyTimelineMutationResult(result);
		if (!applied) return false;
		adoptPersistedTimelineReplacement(previousDraft, applied, label);
		draft = applied;
		return true;
	}

	async function persistLocalizedSubtitleTime(subtitleId: string, startMs: number, endMs: number): Promise<boolean> {
		if (!projectId || !draft || localizationRuntimeBusy) return false;
		const target = draft.localized_subtitles.find((cue) => cue.subtitle_id === subtitleId);
		if (!target) return false;
		const normalized = normalizeLocalizedSubtitleTiming(target, { start_ms: startMs, end_ms: endMs });
		const normalizedStart = normalized.start_ms;
		const normalizedEnd = normalized.end_ms;
		const previousSubtitle = target;
		const expectedProjectId = projectId;
		const saveEpoch = localizedSubtitleSaveEpoch;
		const saveRevision = nextLocalizedSubtitleSaveRevision(subtitleId);
		draft = {
			...draft,
			localized_subtitles: draft.localized_subtitles.map((cue) =>
				cue.subtitle_id === subtitleId ? { ...cue, start_ms: normalizedStart, end_ms: normalizedEnd } : cue
			)
		};
		try {
			const mutation = await projectDraftSessionController.mutate(
				expectedProjectId,
				() => Api.editVideoLocalizationLocalizedSubtitle(expectedProjectId, subtitleId, {
					start_ms: normalizedStart,
					end_ms: normalizedEnd
				})
			);
			if (!mutation) return true;
			if (projectId !== expectedProjectId || !localizedSubtitleSaveIsCurrent(subtitleId, saveRevision, saveEpoch) || !draft) return true;
			adoptLocalizedSubtitleMutation(
				mutation,
				subtitleId,
				previousSubtitle,
				'调整本土化字幕时间'
			);
			autoSaveStatus = 'saved';
			return true;
		} catch (e) {
			if (projectId !== expectedProjectId || !localizedSubtitleSaveIsCurrent(subtitleId, saveRevision, saveEpoch) || !draft) return true;
			draft = { ...draft, localized_subtitles: draft.localized_subtitles.map((cue) => cue.subtitle_id === subtitleId ? previousSubtitle : cue) };
			const saveError = (e as Error).message || '本土化字幕时间保存失败';
			autoSaveStatus = 'failed';
			error = saveError;
			return false;
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

	function updateSelectedLocalizedSubtitle(patch: Partial<VideoLocalizationSubtitleCue>) {
		if (!projectId || !draft || !selectedLocalizedSubtitle || localizationRuntimeBusy) return;
		const subtitleId = selectedLocalizedSubtitle.subtitle_id;
		const existing = pendingLocalizedSubtitleSaves.get(subtitleId);
		const normalized = normalizeLocalizedSubtitleTiming(selectedLocalizedSubtitle, patch);
		const revision = nextLocalizedSubtitleSaveRevision(subtitleId);
		pendingLocalizedSubtitleSaves.set(subtitleId, {
			projectId,
			subtitleId,
			patch: { ...(existing?.patch ?? {}), ...patch },
			previousSubtitle: existing?.previousSubtitle ?? selectedLocalizedSubtitle,
			revision,
			epoch: localizedSubtitleSaveEpoch
		});
		draft = {
			...draft,
			localized_subtitles: draft.localized_subtitles.map((cue) => cue.subtitle_id === subtitleId ? normalized : cue)
		};
		autoSaveStatus = 'dirty';
		if (localizedSubtitleSaveTimer) clearTimeout(localizedSubtitleSaveTimer);
		localizedSubtitleSaveTimer = setTimeout(() => {
			localizedSubtitleSaveTimer = null;
			void flushPendingLocalizedSubtitleSaves();
		}, 260);
	}

	async function updateSelectedDubSubtitle(subtitleId: string, patch: DubSubtitleReviewPatch) {
		if (!projectId || !draft || dubSubtitleReviewBusy) return;
		dubSubtitleReviewBusy = true;
		error = '';
		try {
			if (!(await flushPendingAutosave())) {
				throw new Error('项目仍有未保存修改，请保存后再编辑配音字幕');
			}
			if (!draft) throw new Error('项目字幕尚未加载');
			const request = buildDubSubtitleReviewRequest(draft, subtitleId, patch);
			const updated = await mutateCurrentProjectDraft(
				(activeProjectId) => Api.reviewVideoLocalizationDubSubtitles(activeProjectId, request),
				{ preserveConcurrentChanges: true }
			);
			if (!updated) return;
			message = '配音字幕已保存，并同步到时间线、上屏与导出字幕';
			scheduleMessageClear(2200);
		} catch (e) {
			error = (e as Error).message || '配音字幕保存失败';
		} finally {
			dubSubtitleReviewBusy = false;
		}
	}

	async function flushPendingLocalizedSubtitleSaves() {
		if (localizedSubtitleSaveTimer) clearTimeout(localizedSubtitleSaveTimer);
		localizedSubtitleSaveTimer = null;
		const saves = [...pendingLocalizedSubtitleSaves.values()];
		pendingLocalizedSubtitleSaves.clear();
		let succeeded = true;
		for (const save of saves) {
			const work = editorialSaveQueue.run(() => persistSelectedLocalizedSubtitle(save));
			localizedSubtitleSaveBarrier.track(work);
			succeeded = (await work) && succeeded;
		}
		return succeeded;
	}

	async function persistSelectedLocalizedSubtitle(save: PendingLocalizedSubtitleSave): Promise<boolean> {
		if (!draft || localizationRuntimeBusy || projectId !== save.projectId) return false;
		const { subtitleId, patch, previousSubtitle, revision: saveRevision, epoch: saveEpoch, projectId: expectedProjectId } = save;
		const target = draft.localized_subtitles.find((cue) => cue.subtitle_id === subtitleId);
		if (!target) return false;
		const normalized = normalizeLocalizedSubtitleTiming(target, patch);
		draft = {
			...draft,
			localized_subtitles: draft.localized_subtitles.map((cue) =>
				cue.subtitle_id === subtitleId ? normalized : cue
			)
		};
		try {
			const mutation = await projectDraftSessionController.mutate(
				expectedProjectId,
				() => Api.editVideoLocalizationLocalizedSubtitle(expectedProjectId, subtitleId, {
					...patch,
					...(('start_ms' in patch || 'end_ms' in patch) ? { start_ms: normalized.start_ms, end_ms: normalized.end_ms } : {})
				})
			);
			if (!mutation) return true;
			if (projectId !== expectedProjectId || !localizedSubtitleSaveIsCurrent(subtitleId, saveRevision, saveEpoch) || !draft) return true;
			adoptLocalizedSubtitleMutation(
				mutation,
				subtitleId,
				previousSubtitle,
				'编辑本土化字幕'
			);
			autoSaveStatus = 'saved';
			return true;
		} catch (e) {
			if (projectId !== expectedProjectId || !localizedSubtitleSaveIsCurrent(subtitleId, saveRevision, saveEpoch) || !draft) return true;
			draft = { ...draft, localized_subtitles: draft.localized_subtitles.map((cue) => cue.subtitle_id === subtitleId ? previousSubtitle : cue) };
			const saveError = (e as Error).message || '本土化字幕保存失败';
			autoSaveStatus = 'failed';
			error = saveError;
			return false;
		}
	}

	async function splitSelectedCue(cueId = selectedCue?.cue_id ?? '', requestedSplitMs = previewTimeMs) {
		if (!draft || subtitleRuntimeBusy) return;
		await ensureWorkspaceDetail('transcription');
		if (!draft) return;
		const cueToSplit = draft.cues.find((cue) => cue.cue_id === cueId);
		if (!cueToSplit || cueToSplit.start_ms === null || cueToSplit.end_ms === null) return;
		const durationMs = cueToSplit.end_ms - cueToSplit.start_ms;
		if (durationMs < MIN_SUBTITLE_DURATION_MS * 2) {
			message = '当前字幕片段太短，无法拆分';
			scheduleMessageClear(1600);
			return;
		}
		const splitAt = requestedSplitMs > cueToSplit.start_ms + MIN_SUBTITLE_DURATION_MS && requestedSplitMs < cueToSplit.end_ms - MIN_SUBTITLE_DURATION_MS
			? requestedSplitMs
			: cueToSplit.start_ms + Math.round(durationMs / 2);
		const split = cueSplitPoint(cueToSplit, Math.round(splitAt));
		const splitMs = split.splitMs;
		const [firstEn, secondEn] = splitCueText(cueToSplit.en_subtitle_text ?? '', split.ratio);
		const [firstZh, secondZh] = splitCueText(cueToSplit.zh_localized_subtitle_text ?? '', split.ratio);
		const [firstTts, secondTts] = splitCueText(cueToSplit.tts_recommended_text ?? '', split.ratio);
		const [firstRaw, secondRaw] = splitCueText(cueToSplit.source_text_raw ?? '', split.ratio);
		const nextCue: VideoLocalizationCue = {
			...cueToSplit,
			cue_id: nextCueId(draft),
			start_ms: splitMs,
			end_ms: cueToSplit.end_ms,
			en_subtitle_text: secondEn,
			zh_localized_subtitle_text: secondZh,
			tts_recommended_text: secondTts,
			source_word_ids: split.secondWordIds,
			source_text_raw: secondRaw || null,
			source_duration_ms: cueToSplit.end_ms - splitMs,
			tts_result_id: null,
			tts_audio_path: null,
			tts_batch_task_id: null,
			tts_batch_status: null,
			tts_batch_error: null,
			tts_attempted_at: null,
			generated_duration_ms: null,
			review_status: 'needs_review',
			quality_flags: [...new Set([...(cueToSplit.quality_flags ?? []), 'timeline_split'])]
		};
		const currentCue: VideoLocalizationCue = {
			...cueToSplit,
			end_ms: splitMs,
			en_subtitle_text: firstEn,
			zh_localized_subtitle_text: firstZh,
			tts_recommended_text: firstTts,
			source_word_ids: split.firstWordIds,
			source_text_raw: firstRaw || null,
			source_duration_ms: splitMs - cueToSplit.start_ms,
			tts_result_id: null,
			tts_audio_path: null,
			tts_batch_task_id: null,
			tts_batch_status: null,
			tts_batch_error: null,
			tts_attempted_at: null,
			generated_duration_ms: null,
			review_status: 'needs_review',
			quality_flags: [...new Set([...(cueToSplit.quality_flags ?? []), 'timeline_split'])]
		};
		try {
			if (!(await flushPendingAutosave())) throw new Error('字幕修改尚未保存，请重试');
			const updated = await mutateCurrentProjectDraft(
				(activeProjectId) => Api.splitVideoLocalizationCue(
					activeProjectId,
					cueId,
					{ replacements: [currentCue, nextCue] }
				),
				{
					timelineHistoryLabel: '拆分 ASR 字幕',
					beforeApply: () => { cueSaveRevision += 1; }
				}
			);
			if (!updated) return;
			draftOnlyCueIds = draftOnlyCueIds.filter((id) => updated.cues.some((cue) => cue.cue_id === id));
			selectedCueId = nextCue.cue_id;
			timelineSelectionItems = [{ kind: 'subtitle', trackId: 'subtitles', itemId: nextCue.cue_id }];
			updateTtsSelectionAnchor({ kind: 'source', itemId: nextCue.cue_id }, timelineSelectionItems);
			updateDraftUiState({ selected_cue_id: nextCue.cue_id });
			focusInspector('subtitle');
			message = '字幕片段已拆分，关联本土化字幕已同步';
			scheduleMessageClear(1600);
		} catch (e) {
			error = (e as Error).message || '拆分字幕片段失败';
		}
	}

	function nextLocalizedSubtitleId(currentDraft: VideoLocalizationDraft) {
		const used = new Set(currentDraft.localized_subtitles.map((subtitle) => subtitle.subtitle_id));
		let index = currentDraft.localized_subtitles.length + 1;
		while (used.has(`localized_${String(index).padStart(4, '0')}`)) index += 1;
		return `localized_${String(index).padStart(4, '0')}`;
	}

	async function splitLocalizedSubtitleFromTimeline(subtitleId: string, requestedSplitMs: number) {
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
		const linkedClipIds = currentDraft.timeline_clips
			.filter((item) => item.subtitle_id === subtitleId)
			.map((item) => item.clip_id);
		const splitClipIds: string[] = [];
		try {
			if (!(await flushPendingAutosave())) throw new Error('字幕修改尚未保存，请重试');
			const updated = await mutateCurrentProjectDraft(
				async (activeProjectId) => {
					const serverDraft = await Api.splitVideoLocalizationLocalizedSubtitle(
						activeProjectId,
						subtitleId,
						{ children: [firstSubtitle, secondSubtitle] }
					);
					let clips = serverDraft.timeline_clips;
					for (const clipId of linkedClipIds) {
						const split = splitTimelineAudioClip(clips, clipId, splitMs, {
							nextSubtitleId,
							minDurationMs: minimumFrameDurationMs(frameRate)
						});
						if (!split) continue;
						clips = split.clips;
						splitClipIds.push(clipId, split.second.clip_id);
					}
					return { ...serverDraft, timeline_clips: clips };
				},
				{
					timelineHistoryLabel: '拆分本土化字幕',
					timelineHistoryPersisted: false,
					beforeApply: invalidateLocalizedSubtitleSaves
				}
			);
			if (!updated) return;
			selectedLocalizedSubtitleId = nextSubtitleId;
			timelineSelectionItems = [{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: nextSubtitleId }];
			updateTtsSelectionAnchor({ kind: 'target', itemId: nextSubtitleId }, timelineSelectionItems);
			message = splitClipIds.length
				? '本土化字幕与关联音频已按当前帧拆分'
				: '本土化字幕已拆分，来源关系已同步';
			scheduleMessageClear(1800);
		} catch (e) {
			error = (e as Error).message || '拆分本土化字幕失败';
		}
	}

	function splitTimelineClipFromTimeline(clipId: string, requestedSplitMs: number) {
		if (!draft) return;
		const currentDraft = draft;
		const clip = currentDraft.timeline_clips.find((item) => item.clip_id === clipId);
		if (!clip) return;
		const startMs = Math.max(0, Math.round(clip.start_ms ?? 0));
		const frameRate = normalizeFrameRate(currentDraft.source_media.frame_rate);
		const minDurationMs = minimumFrameDurationMs(frameRate);
		const endMs = Math.max(
			startMs + minDurationMs,
			Math.round(clip.end_ms ?? startMs + 1800)
		);
		const splitMs = snapTimeToFrame(
			requestedSplitMs,
			frameRate,
			'nearest',
			startMs + minDurationMs,
			endMs - minDurationMs
		);
		if (splitMs <= startMs || splitMs >= endMs) return;
		const split = splitTimelineAudioClip(
			currentDraft.timeline_clips,
			clipId,
			splitMs,
			{ minDurationMs }
		);
		if (!split) return;
		dispatchTimelineEdit({
			type: 'transaction',
			label: '拆分音频片段',
			clipPatches: [{
				clipId,
				patch: editableTimelineClipPatch(clip, split.first)
			}],
			addClips: [{
				clip: split.second,
				index: currentDraft.timeline_clips.findIndex((item) => item.clip_id === clipId) + 1
			}]
		});
		message = '音频片段已按当前帧拆分';
		scheduleMessageClear(1800);
	}

	async function mergeSelectedTimelineSubtitles(request: TimelineSubtitleMergeRequest) {
		if (!draft || request.itemIds.length < 2) return;
		const existingIds = new Set(request.track === 'asr'
			? draft.cues.map((cue) => cue.cue_id)
			: draft.localized_subtitles.map((subtitle) => subtitle.subtitle_id));
		if (request.itemIds.some((id) => !existingIds.has(id))) return;
		if (request.track === 'asr') {
			const survivorCueId = request.itemIds[0];
			try {
				if (!(await flushPendingAutosave())) throw new Error('字幕修改尚未保存，请重试');
				const updated = await mutateCurrentProjectDraft(
					(activeProjectId) => Api.mergeVideoLocalizationCues(activeProjectId, {
						cue_ids: request.itemIds,
						survivor_cue_id: survivorCueId
					}),
					{
						timelineHistoryLabel: '合并 ASR 字幕',
						beforeApply: () => { cueSaveRevision += 1; }
					}
				);
				if (!updated) return;
				draftOnlyCueIds = draftOnlyCueIds.filter((id) => updated.cues.some((cue) => cue.cue_id === id));
				selectedCueId = survivorCueId;
				selectedLocalizedSubtitleId = '';
				timelineSelectionItems = [{ kind: 'subtitle', trackId: 'subtitles', itemId: survivorCueId }];
				updateTtsSelectionAnchor({ kind: 'source', itemId: survivorCueId }, timelineSelectionItems);
				updateDraftUiState({ selected_cue_id: selectedCueId });
				focusInspector('subtitle');
				message = `已合并 ${request.itemIds.length} 个 ASR 字幕片段，来源关系已同步`;
				scheduleMessageClear(1600);
			} catch (e) {
				error = (e as Error).message || '合并 ASR 字幕片段失败';
			}
			return;
		}
		const byId = new Map(draft.localized_subtitles.map((subtitle) => [subtitle.subtitle_id, subtitle]));
			const selected = request.itemIds.map((id) => byId.get(id)).filter((subtitle): subtitle is VideoLocalizationSubtitleCue => Boolean(subtitle));
			if (selected.length !== request.itemIds.length) return;
			const first = selected[0];
			const removedIds = new Set(selected.slice(1).map((subtitle) => subtitle.subtitle_id));
			const mergedText = (pick: (subtitle: VideoLocalizationSubtitleCue) => string | null | undefined) =>
				selected.map(pick).map((text) => text?.trim()).filter(Boolean).join('\n');
			const mergedSubtitle: VideoLocalizationSubtitleCue = {
				...first,
				start_ms: request.startMs,
				end_ms: request.endMs,
				text: request.text,
				tts_text: mergedText((subtitle) => subtitle.tts_text || subtitle.text),
				tts_result_id: null,
				tts_audio_path: null,
				tts_batch_task_id: null,
				tts_batch_status: null,
				tts_batch_error: null,
				tts_attempted_at: null,
				generated_duration_ms: null,
				linked_cue_id: selected.every((subtitle) => subtitle.linked_cue_id === first.linked_cue_id) ? first.linked_cue_id : null,
				source_cue_ids: [...new Set(selected.flatMap((subtitle) => subtitle.source_cue_ids ?? (subtitle.linked_cue_id ? [subtitle.linked_cue_id] : [])))],
				source_word_ids: [...new Set(selected.flatMap((subtitle) => subtitle.source_word_ids ?? []))],
				adaptation_note: mergedText((subtitle) => subtitle.adaptation_note) || null,
				quality_flags: [...new Set([...selected.flatMap((subtitle) => subtitle.quality_flags ?? []), 'timeline_merge'])]
			};
			invalidateLocalizedSubtitleSaves();
			const mergedDraft = {
				...draft,
				localized_subtitles: draft.localized_subtitles
					.map((subtitle) => subtitle.subtitle_id === first.subtitle_id ? mergedSubtitle : subtitle)
					.filter((subtitle) => !removedIds.has(subtitle.subtitle_id)),
				timeline_clips: preserveDubClipsAfterLocalizedSubtitleMerge(
					draft.timeline_clips,
					selected,
					first.subtitle_id
				)
			};
			selectedLocalizedSubtitleId = first.subtitle_id;
			selectedCueId = mergedSubtitle.linked_cue_id ?? '';
			timelineSelectionItems = [{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: first.subtitle_id }];
		dispatchTimelineReplacement(mergedDraft, '合并本土化字幕');

		updateDraftUiState({ selected_cue_id: selectedCueId });
		focusInspector('subtitle');
		message = `已合并 ${request.itemIds.length} 个字幕片段`;
		scheduleMessageClear(1600);
	}

	function applyTimelineControllerDraft(previousDraft: VideoLocalizationDraft) {
		if (!draft || !timelineEditController) return;
		const controlledDraft = withPendingDraftUiState(timelineEditController.draft);
		const beforeById = new Map(previousDraft.timeline_clips.map((clip) => [clip.clip_id, clip]));
		const afterById = new Map(controlledDraft.timeline_clips.map((clip) => [clip.clip_id, clip]));
		for (const clipId of new Set([...beforeById.keys(), ...afterById.keys()])) {
			const before = beforeById.get(clipId);
			const after = afterById.get(clipId);
			if (!before || !after || timelineClipTimingChanged(before, after)) markTimelineClipDirty(clipId, 'timing');
			if (
				!before
				|| !after
				|| Number(before.dub_lane ?? 0) !== Number(after.dub_lane ?? 0)
			) markTimelineClipDirty(clipId, 'lane');
		}
		for (const clipId of timelineControllerDeletedClipIds) timelineDeletedClipIds.delete(clipId);
		timelineControllerDeletedClipIds = new Set(timelineEditController.deletedTimelineClipIds);
		for (const clipId of timelineControllerDeletedClipIds) timelineDeletedClipIds.add(clipId);
		draft = {
			...draft,
			cues: controlledDraft.cues,
			localized_subtitles: controlledDraft.localized_subtitles,
			timeline_clips: reconcileTimelineClipPayloads(
				previousDraft.timeline_clips,
				controlledDraft.timeline_clips
			),
			ui_state: {
				...draft.ui_state,
				dub_lane_states: controlledDraft.ui_state?.dub_lane_states,
				disabled_media_tracks: controlledDraft.ui_state?.disabled_media_tracks,
				discarded_tts_task_ids: controlledDraft.ui_state?.discarded_tts_task_ids,
				track_states: {
					...trackStates,
					dub: {
						...((controlledDraft.ui_state?.dub_lane_states as VideoLocalizationDubLaneStates | undefined)?.['0']
							?? trackStates.dub)
					}
				}
			}
		};
	}

	function dispatchTimelineEdit(command: TimelineEditTransaction) {
		return dispatchTimelineEditWithSaveMode(command, 'immediate');
	}

	function dispatchTimelineEditDeferred(command: TimelineEditTransaction) {
		return dispatchTimelineEditWithSaveMode(command, 'debounced');
	}

	function dispatchTimelineEditWithSaveMode(
		command: TimelineEditTransaction,
		saveMode: 'immediate' | 'debounced'
	) {
		if (!draft) return false;
		timelineProjectionSessionController.invalidate();
		if (!timelineEditController) timelineEditController = new TimelineEditController(draft);
		timelineEditController.synchronizeExternalDraft(draft);
		const previousDraft = draft;
		const result = timelineEditController.dispatch(command);
		if (result.status !== 'applied') return false;
		syncTimelineHistoryCounts();
		applyTimelineControllerDraft(previousDraft);
		timelineEditRevision += 1;
		if (saveMode === 'immediate') saveTimelineEditImmediately();
		else scheduleDraftAutosave();
		return true;
	}

	function syncTimelineHistoryCounts() {
		timelineUndoCount = timelineEditController?.undoCount ?? 0;
		timelineRedoCount = timelineEditController?.redoCount ?? 0;
	}

	function timelineReplacementTransaction(
		currentDraft: VideoLocalizationDraft,
		nextDraft: VideoLocalizationDraft,
		label: string
	): TimelineEditTransaction {
		const nextLaneStates = (
			nextDraft.ui_state?.dub_lane_states
			?? {}
		) as VideoLocalizationDubLaneStates;
		const currentDisabledTracks = Array.isArray(currentDraft.ui_state?.disabled_media_tracks)
			? currentDraft.ui_state.disabled_media_tracks.map(String)
			: [];
		const nextDisabledTracks = Array.isArray(nextDraft.ui_state?.disabled_media_tracks)
			? nextDraft.ui_state.disabled_media_tracks.map(String)
			: [];
		const currentDiscardedTaskIds = Array.isArray(currentDraft.ui_state?.discarded_tts_task_ids)
			? currentDraft.ui_state.discarded_tts_task_ids.map(String)
			: [];
		const nextDiscardedTaskIds = Array.isArray(nextDraft.ui_state?.discarded_tts_task_ids)
			? nextDraft.ui_state.discarded_tts_task_ids.map(String)
			: [];
		const timelineClipsChanged = JSON.stringify(currentDraft.timeline_clips) !== JSON.stringify(nextDraft.timeline_clips);
		return {
			type: 'transaction',
			label,
			replaceCues: nextDraft.cues,
			replaceLocalizedSubtitles: nextDraft.localized_subtitles,
			...(timelineClipsChanged ? { replaceTimelineClips: nextDraft.timeline_clips } : {}),
			dubLaneStatePatches: dubLaneStatePatchCommands(nextLaneStates, currentDraft),
			uiStatePatch: {
				...(JSON.stringify(currentDisabledTracks) === JSON.stringify(nextDisabledTracks)
					? {}
					: { disabled_media_tracks: nextDisabledTracks }),
				...(JSON.stringify(currentDiscardedTaskIds) === JSON.stringify(nextDiscardedTaskIds)
					? {}
					: { discarded_tts_task_ids: nextDiscardedTaskIds })
			}
		};
	}

	function dispatchTimelineReplacement(nextDraft: VideoLocalizationDraft, label: string) {
		if (!draft) return false;
		return dispatchTimelineReplacementFrom(draft, nextDraft, label);
	}

	function dispatchTimelineReplacementFrom(
		previousDraft: VideoLocalizationDraft,
		nextDraft: VideoLocalizationDraft,
		label: string
	) {
		if (!draft) return false;
		if (!timelineEditController) timelineEditController = new TimelineEditController(previousDraft);
		timelineEditController.synchronizeExternalDraft(previousDraft);
		const result = timelineEditController.dispatch(
			timelineReplacementTransaction(previousDraft, nextDraft, label)
		);
		if (result.status !== 'applied') return false;
		syncTimelineHistoryCounts();
		applyTimelineControllerDraft(previousDraft);
		timelineEditRevision += 1;
		saveTimelineEditImmediately();
		return true;
	}

	function adoptPersistedTimelineReplacement(
		previousDraft: VideoLocalizationDraft,
		persistedDraft: VideoLocalizationDraft,
		label: string
	) {
		const editablePersistedDraft = withEditableMediaClips(persistedDraft);
		if (!timelineEditController) timelineEditController = new TimelineEditController(previousDraft);
		timelineEditController.synchronizeExternalDraft(previousDraft);
		const result = timelineEditController.adoptPersisted(
			timelineReplacementTransaction(previousDraft, editablePersistedDraft, label),
			editablePersistedDraft
		);
		syncTimelineHistoryCounts();
		if (result.status !== 'applied') return false;
		timelineEditRevision += 1;
		timelineSavedRevision += 1;
		return true;
	}

	function dubLaneStatePatchCommands(
		nextStates: VideoLocalizationDubLaneStates,
		currentDraft: VideoLocalizationDraft | null = draft
	) {
		const currentStates = currentDraft?.ui_state?.dub_lane_states as VideoLocalizationDubLaneStates | undefined;
		return [...new Set([
			...Object.keys(currentStates ?? {}),
			...Object.keys(nextStates)
		])].map((lane) => (
			nextStates[lane]
				? { lane, patch: { ...nextStates[lane] } }
				: { lane, remove: true }
		));
	}

	function editableTimelineClipPatch(
		before: VideoLocalizationTimelineClip,
		after: VideoLocalizationTimelineClip
	): TimelineClipEditablePatch {
		const patch: TimelineClipEditablePatch = {};
		const values = patch as Record<string, unknown>;
		for (const field of ['start_ms', 'end_ms', 'source_start_ms', 'source_end_ms', 'media_source_clip_id'] as const) {
			if (!Object.is(before[field], after[field])) values[field] = after[field] ?? null;
		}
		if (!Object.is(before.dub_lane, after.dub_lane) && typeof after.dub_lane === 'number') {
			patch.dub_lane = after.dub_lane;
		}
		return patch;
	}

	function markTimelineClipDirty(clipId: string, ...fields: TimelineDirtyField[]) {
		if (!clipId || !fields.length) return;
		const current = timelineDirtyFieldsByClipId.get(clipId) ?? new Set<TimelineDirtyField>();
		for (const field of fields) current.add(field);
		timelineDirtyFieldsByClipId.set(clipId, current);
	}

	function updateTimelineClipFromTimeline(clipId: string, startMs: number, endMs: number, sourceStartMs: number, sourceEndMs: number | null, requestedDubLane?: number) {
		if (!draft) return;
		const minDurationMs = minimumFrameDurationMs(
			normalizeFrameRate(draft.source_media.frame_rate)
		);
		const normalizedStart = Math.max(0, Math.round(startMs));
		const normalizedEnd = Math.max(normalizedStart + minDurationMs, Math.round(endMs));
		const normalizedSourceStart = Math.max(0, Math.round(sourceStartMs));
		const normalizedSourceEnd = sourceEndMs === null
			? null
			: Math.max(normalizedSourceStart + minDurationMs, Math.round(sourceEndMs));
		const targetClip = draft.timeline_clips.find((clip) => clip.clip_id === clipId);
		const lockedDubLanes = Object.entries(dubLaneStates)
			.filter(([, state]) => state.locked === true)
			.map(([lane]) => Number(lane));
		const normalizedRequestedLane = requestedDubLane === undefined ? undefined : Math.max(0, Math.floor(requestedDubLane));
		if (targetClip?.track_id === 'dub' && normalizedRequestedLane !== undefined && (
			lockedDubLanes.includes(normalizedRequestedLane)
			|| !canPlaceDubClipInLane(draft.timeline_clips, normalizedStart, normalizedEnd, normalizedRequestedLane, clipId)
		)) return;
		const nextDubLane = targetClip?.track_id === 'dub'
			? normalizedRequestedLane ?? resolveDubClipLane(draft.timeline_clips, normalizedStart, normalizedEnd, Number(targetClip.dub_lane ?? 0), clipId, lockedDubLanes)
			: null;
		const timingChanged = Boolean(targetClip && (
			normalizedStart !== targetClip.start_ms ||
			normalizedEnd !== targetClip.end_ms ||
			normalizedSourceStart !== targetClip.source_start_ms ||
			normalizedSourceEnd !== targetClip.source_end_ms
		));
		const laneChanged = Boolean(
			targetClip?.track_id === 'dub' &&
			nextDubLane !== Number(targetClip.dub_lane ?? 0)
		);
		if (!timingChanged && !laneChanged) return;
		const nextTimelineClips = draft.timeline_clips.map((clip) =>
			clip.clip_id === clipId
				? {
						...clip,
						start_ms: normalizedStart,
						end_ms: normalizedEnd,
						source_start_ms: normalizedSourceStart,
						source_end_ms: normalizedSourceEnd,
						...(nextDubLane === null ? {} : { dub_lane: nextDubLane })
					}
				: clip
		);
		dispatchTimelineEdit({
			type: 'transaction',
			label: timingChanged ? '调整音频片段' : '移动配音分轨',
			clipPatches: [{
				clipId,
				patch: editableTimelineClipPatch(targetClip!, nextTimelineClips.find((clip) => clip.clip_id === clipId)!)
			}]
		});
	}

	function moveTimelineItemsFromTimeline(items: TimelineGroupMoveCommitItem[]) {
		if (!draft || !items.length) return;
		const kind = items[0].kind;
		const trackId = items[0].trackId;
		if (items.some((item) => item.kind !== kind || item.trackId !== trackId)) return;
		const byId = new Map(items.map((item) => [item.itemId, item]));
		let movedTimelineClips = draft.timeline_clips;
		if (kind === 'audio') {
			const minDurationMs = minimumFrameDurationMs(
				normalizeFrameRate(draft.source_media.frame_rate)
			);
			const currentById = new Map(draft.timeline_clips.map((clip) => [clip.clip_id, clip]));
			const currentClips = items.map((item) => currentById.get(item.itemId));
			if (currentClips.some((clip) => !clip || clip.track_id !== trackId)) return;
			const normalizedItems = items.map((item) => {
				const clip = currentById.get(item.itemId)!;
				const startMs = Math.max(0, Math.round(item.startMs));
				const endMs = Math.max(startMs + minDurationMs, Math.round(item.endMs));
				const sourceStartMs = Math.max(0, Math.round(item.sourceStartMs ?? Number(clip.source_start_ms ?? 0)));
				const sourceEndMs = item.sourceEndMs === null
					? null
					: Math.max(
							sourceStartMs + minDurationMs,
							Math.round(item.sourceEndMs ?? Number(clip.source_end_ms ?? endMs - startMs))
						);
				return { ...item, startMs, endMs, sourceStartMs, sourceEndMs };
			});
			const normalizedById = new Map(normalizedItems.map((item) => [item.itemId, item]));
			let requestedDubLanesByClipId: Map<string, number> | null = null;
			if (trackId === 'dub') {
				const suppliedLanes = normalizedItems.filter((item) => item.dubLane !== undefined);
				if (suppliedLanes.length !== 0 && suppliedLanes.length !== normalizedItems.length) return;
				const lockedDubLanes = new Set(Object.entries(dubLaneStates)
					.filter(([, state]) => state.locked === true)
					.map(([lane]) => Number(lane)));
				if (suppliedLanes.length) {
					requestedDubLanesByClipId = new Map(suppliedLanes.map((item) => [
						item.itemId,
						Math.max(0, Math.floor(item.dubLane!))
					]));
					if (!canPlaceDubClipGroupAcrossLanes(
						draft.timeline_clips,
						normalizedItems.map((item) => ({
							clipId: item.itemId,
							startMs: item.startMs,
							endMs: item.endMs,
							lane: requestedDubLanesByClipId!.get(item.itemId)!
						})),
						lockedDubLanes
					)) return;
				} else {
					const rangesByLane = new Map<number, { clipId: string; startMs: number; endMs: number }[]>();
					for (const item of normalizedItems) {
						const lane = Math.max(0, Math.floor(Number(currentById.get(item.itemId)?.dub_lane ?? 0)));
						if (lockedDubLanes.has(lane)) return;
						const ranges = rangesByLane.get(lane) ?? [];
						ranges.push({ clipId: item.itemId, startMs: item.startMs, endMs: item.endMs });
						rangesByLane.set(lane, ranges);
					}
					for (const [lane, ranges] of rangesByLane) {
						if (!canPlaceDubClipGroupInLane(draft.timeline_clips, ranges, lane)) return;
					}
				}
			}
			movedTimelineClips = draft.timeline_clips.map((clip) => {
				const moved = normalizedById.get(clip.clip_id);
				return moved ? {
					...clip,
					start_ms: moved.startMs,
					end_ms: moved.endMs,
					source_start_ms: moved.sourceStartMs,
					source_end_ms: moved.sourceEndMs,
					...(trackId === 'dub' && requestedDubLanesByClipId
						? { dub_lane: requestedDubLanesByClipId.get(clip.clip_id) }
						: {})
				} : clip;
			});
		}
		if (kind === 'audio') {
			const beforeById = new Map(draft.timeline_clips.map((clip) => [clip.clip_id, clip]));
			dispatchTimelineEdit({
				type: 'transaction',
				label: items.length > 1 ? '移动多个音频片段' : '移动音频片段',
				clipPatches: movedTimelineClips.flatMap((clip) => {
					const before = beforeById.get(clip.clip_id);
					if (!before) return [];
					const patch = editableTimelineClipPatch(before, clip);
					return Object.keys(patch).length ? [{ clipId: clip.clip_id, patch }] : [];
				}),
			});
			return;
		}
		let nextDraft: VideoLocalizationDraft;
		if (kind === 'subtitle' && trackId === 'subtitles') {
			cueSaveRevision += 1;
			nextDraft = {
				...draft,
				cues: draft.cues.map((cue) => {
					const moved = byId.get(cue.cue_id);
					if (!moved) return cue;
					return protectCueManualEdit(cue, {
						...cue,
						start_ms: Math.round(moved.startMs),
						end_ms: Math.round(moved.endMs),
						source_duration_ms: Math.round(moved.endMs - moved.startMs)
					}, { timing: true });
				})
			};
		} else if (kind === 'subtitle' && trackId === 'localizedSubtitles') {
			invalidateLocalizedSubtitleSaves();
			nextDraft = {
				...draft,
				localized_subtitles: draft.localized_subtitles.map((subtitle) => {
					const moved = byId.get(subtitle.subtitle_id);
					return moved ? { ...subtitle, start_ms: Math.round(moved.startMs), end_ms: Math.round(moved.endMs) } : subtitle;
				})
			};
		} else return;
		dispatchTimelineReplacement(
			nextDraft,
			items.length > 1 ? '移动多个字幕片段' : '移动字幕片段'
		);
	}

	function reorderDubLanes(fromLane: number, toLane: number) {
		if (!draft || fromLane === toLane || fromLane < 0 || toLane < 0) return;
		if (dubLaneStates[String(fromLane)]?.locked || dubLaneStates[String(toLane)]?.locked) return;
		const occupiedLanes = new Set(draft.timeline_clips
			.filter((clip) => clip.track_id === 'dub')
			.map((clip) => Number(clip.dub_lane ?? 0)));
		if (!occupiedLanes.has(fromLane) || !occupiedLanes.has(toLane)) return;
		const clips = swapDubTrackLanes(draft.timeline_clips, fromLane, toLane);
		const fromState = { ...(dubLaneStates[String(fromLane)] ?? trackStates.dub) };
		const toState = { ...(dubLaneStates[String(toLane)] ?? trackStates.dub) };
		const nextLaneStates = {
			...dubLaneStates,
			[String(fromLane)]: toState,
			[String(toLane)]: fromState
		};
		dispatchTimelineEdit({
			type: 'transaction',
			label: '重排配音分轨',
			clipPatches: clips.flatMap((clip) => {
				const before = draft?.timeline_clips.find((item) => item.clip_id === clip.clip_id);
				return before && Number(before.dub_lane ?? 0) !== Number(clip.dub_lane ?? 0)
					? [{ clipId: clip.clip_id, patch: { dub_lane: Number(clip.dub_lane ?? 0) } }]
					: [];
			}),
			dubLaneStatePatches: dubLaneStatePatchCommands(nextLaneStates)
		});
	}

	async function deleteTimelineClip(clipId: string) {
		const target = timelineViewDraft?.timeline_clips.find((clip) => clip.clip_id === clipId);
		if (!target) return false;
		return deleteTimelineItems([{ kind: 'audio', trackId: target.track_id as VideoLocalizationTrackId, itemId: clipId }]);
	}

	async function deleteTimelineItems(items: TimelineSelectionItem[]) {
		await performDeleteTimelineItems(items);
		if (!timelineViewDraft) return false;
		return items.every((item) => {
			if (item.kind === 'audio') return !timelineViewDraft?.timeline_clips.some((clip) => clip.clip_id === item.itemId);
			if (item.trackId === 'subtitles') return !draft?.cues.some((cue) => cue.cue_id === item.itemId);
			return !draft?.localized_subtitles.some((subtitle) => subtitle.subtitle_id === item.itemId);
		});
	}

	async function performDeleteTimelineItems(items: TimelineSelectionItem[]) {
		if (!draft || !items.length) return;
		const ttsContext = ttsWorkflowSessionController.captureContext();
		let uniqueItems = items.filter((item, index) => items.findIndex((candidate) =>
			candidate.kind === item.kind && candidate.trackId === item.trackId && candidate.itemId === item.itemId
		) === index);
		const activeWorkflowClipIds = new Set<string>();
		const persistedWorkflowClipIds = new Set<string>();
		const workflowIds = new Set<string>();
		for (const item of uniqueItems) {
			if (item.kind !== 'audio') continue;
			const clip = timelineViewDraft?.timeline_clips.find((candidate) => candidate.clip_id === item.itemId);
			if (!clip?.optimistic_tts_workflow_id) continue;
			const workflowMarker = String(clip.optimistic_tts_workflow_id);
			const initializationClientId = ttsInitializationClientId(workflowMarker);
			if (initializationClientId) {
				if (cancelTtsInitialization(initializationClientId, 'delete')) {
					activeWorkflowClipIds.add(item.itemId);
				}
				continue;
			}
			activeWorkflowClipIds.add(item.itemId);
			const workflowId = persistedTtsWorkflowId(workflowMarker);
			if (workflowId) {
				workflowIds.add(workflowId);
				persistedWorkflowClipIds.add(item.itemId);
			}
		}
		if (activeWorkflowClipIds.size) {
			const locallyDeletedClipIds = new Set(
				[...activeWorkflowClipIds].filter((clipId) => !persistedWorkflowClipIds.has(clipId))
			);
			timelineSelectionItems = timelineSelectionItems.filter((item) => !locallyDeletedClipIds.has(item.itemId));
			if (locallyDeletedClipIds.has(selectedTimelineAudioClipId)) selectedTimelineAudioClipId = '';
			uniqueItems = uniqueItems.filter((item) => !activeWorkflowClipIds.has(item.itemId));
		}
		if (workflowIds.size) {
			try {
				if (!ttsContext) throw new Error('配音会话尚未就绪');
				if (!(await flushPendingAutosave())) throw new Error('项目修改尚未保存，请重试');
				if (!ttsWorkflowSessionController.isCurrent(ttsContext)) return;
				const deletedWorkflows = (draft.tts_tasks ?? []).filter((task) => workflowIds.has(task.workflow_id));
				let deletedDraft: VideoLocalizationDraft | null = null;
				for (const workflowId of workflowIds) {
					deletedDraft = await Api.deleteVideoLocalizationTtsTask(ttsContext.projectId, workflowId);
				}
				if (!ttsWorkflowSessionController.isCurrent(ttsContext)) return;
				for (const workflow of deletedWorkflows) stopTrackingDeletedTtsWorkflow(workflow);
				if (deletedDraft) {
					timelineRuntimeClips = timelineRuntimeClips.filter((clip) => {
						const marker = String(clip.optimistic_tts_workflow_id ?? '');
						return !workflowIds.has(persistedTtsWorkflowId(marker) ?? '');
					});
					applyFreshProjectDraft(deletedDraft, false, false);
					ttsWorkflowSessionController.stopPolling();
					if ((deletedDraft.tts_tasks ?? []).some(ttsWorkflowActive)) {
						ttsWorkflowSessionController.startPolling(0);
					}
				}
				timelineSelectionItems = timelineSelectionItems.filter((item) => !persistedWorkflowClipIds.has(item.itemId));
				if (persistedWorkflowClipIds.has(selectedTimelineAudioClipId)) selectedTimelineAudioClipId = '';
				message = workflowIds.size > 1 ? `已停止并删除 ${workflowIds.size} 个配音片段` : '已停止并删除配音片段';
				if (!uniqueItems.length) return;
			} catch (e) {
				error = (e as Error).message || '停止并删除配音片段失败';
				return;
			}
		}
		if (!uniqueItems.length) return;
		const deletionBaseline = draft;
		const requestedDeleteCount = uniqueItems.length;
		const deletionLabel = requestedDeleteCount > 1 ? '删除多个时间线片段' : '删除时间线片段';
		let persistedSubtitleChanges = false;
		const sourceCueIds = uniqueItems
			.filter((item) => item.kind === 'subtitle' && item.trackId === 'subtitles')
			.map((item) => item.itemId);
		if (sourceCueIds.length) {
			try {
				if (!(await flushPendingAutosave())) throw new Error('字幕修改尚未保存，请重试');
				const updated = await mutateCurrentProjectDraft(
					async (activeProjectId) => {
						let serverDraft = draft!;
						for (const cueId of sourceCueIds) {
							serverDraft = await Api.deleteVideoLocalizationCue(activeProjectId, cueId);
						}
						return serverDraft;
					},
					{ beforeApply: () => { cueSaveRevision += 1; } }
				);
				if (!updated) return;
				persistedSubtitleChanges = true;
				draftOnlyCueIds = draftOnlyCueIds.filter((id) => updated.cues.some((cue) => cue.cue_id === id));
				if (sourceCueIds.includes(selectedCueId)) selectedCueId = updated.cues[0]?.cue_id ?? '';
				uniqueItems = uniqueItems.filter((item) => !(item.kind === 'subtitle' && item.trackId === 'subtitles'));
				timelineSelectionItems = timelineSelectionItems.filter((item) => !sourceCueIds.includes(item.itemId));
				if (selectedCueId) updateTtsSelectionAnchor({ kind: 'source', itemId: selectedCueId }, timelineSelectionItems);
				else ttsSelectionSession = { ...EMPTY_TTS_SELECTION_SESSION };
				updateDraftUiState({ selected_cue_id: selectedCueId });
				message = sourceCueIds.length > 1
					? `已删除 ${sourceCueIds.length} 个 ASR 字幕片段，来源关系已同步`
					: 'ASR 字幕片段已删除，来源关系已同步';
				if (!uniqueItems.length) {
					adoptPersistedTimelineReplacement(deletionBaseline, updated, deletionLabel);
					return;
				}
			} catch (e) {
				error = (e as Error).message || '删除 ASR 字幕片段失败';
				return;
			}
		}
		const localizedSubtitleIds = uniqueItems
			.filter((item) => item.kind === 'subtitle' && item.trackId === 'localizedSubtitles')
			.map((item) => item.itemId);
		if (localizedSubtitleIds.length) {
			try {
				if (!(await flushPendingAutosave())) throw new Error('字幕修改尚未保存，请重试');
				const updated = await mutateCurrentProjectDraft(
					async (activeProjectId) => {
						let serverDraft = draft!;
						for (const subtitleId of localizedSubtitleIds) {
							serverDraft = await Api.deleteVideoLocalizationLocalizedSubtitle(activeProjectId, subtitleId);
						}
						return serverDraft;
					},
					{ beforeApply: invalidateLocalizedSubtitleSaves }
				);
				if (!updated) return;
				persistedSubtitleChanges = true;
				if (localizedSubtitleIds.includes(selectedLocalizedSubtitleId)) selectedLocalizedSubtitleId = '';
				uniqueItems = uniqueItems.filter((item) => !(
					item.kind === 'subtitle' && item.trackId === 'localizedSubtitles'
				));
				timelineSelectionItems = timelineSelectionItems.filter((item) => !localizedSubtitleIds.includes(item.itemId));
				ttsSelectionSession = refreshTtsSelectionSession(
					ttsSelectionSession,
					updated.cues,
					updated.localized_subtitles
				);
				message = localizedSubtitleIds.length > 1
					? `已删除 ${localizedSubtitleIds.length} 个本土化字幕片段，关联音频已解除绑定`
					: '本土化字幕片段已删除，关联音频已解除绑定';
				if (!uniqueItems.length) {
					adoptPersistedTimelineReplacement(deletionBaseline, updated, deletionLabel);
					return;
				}
			} catch (e) {
				error = (e as Error).message || '删除本土化字幕片段失败';
				return;
			}
		}
		const asrIds = new Set(uniqueItems.filter((item) => item.kind === 'subtitle' && item.trackId === 'subtitles').map((item) => item.itemId));
		const localizedIds = new Set(uniqueItems.filter((item) => item.kind === 'subtitle' && item.trackId === 'localizedSubtitles').map((item) => item.itemId));
		const audioIds = new Set(uniqueItems.filter((item) => item.kind === 'audio').map((item) => item.itemId));
		const existingCount = draft.cues.filter((cue) => asrIds.has(cue.cue_id)).length
			+ draft.localized_subtitles.filter((cue) => localizedIds.has(cue.subtitle_id)).length
			+ draft.timeline_clips.filter((clip) => audioIds.has(clip.clip_id)).length;
		if (!existingCount) {
			if (persistedSubtitleChanges && draft) {
				adoptPersistedTimelineReplacement(deletionBaseline, draft, deletionLabel);
			}
			return;
		}

		const nextClips = draft.timeline_clips.filter((clip) => !audioIds.has(clip.clip_id));
		const affectedMediaTracks = new Set(
			draft.timeline_clips
				.filter((clip) => audioIds.has(clip.clip_id) && ['original', 'vocals', 'background'].includes(clip.track_id))
				.map((clip) => clip.track_id)
		);
		const disabledTracks = new Set(Array.isArray(draft.ui_state?.disabled_media_tracks) ? draft.ui_state.disabled_media_tracks.map(String) : []);
		const removedDubClips = draft.timeline_clips.filter((clip) => audioIds.has(clip.clip_id) && clip.track_id === 'dub');
		const remainingDubClips = nextClips.filter((clip) => clip.track_id === 'dub');
		const discardedTtsTaskIds = discardedTtsTaskIdsAfterClipRemoval(
			Array.isArray(draft.ui_state?.discarded_tts_task_ids) ? draft.ui_state.discarded_tts_task_ids.map(String) : [],
			removedDubClips,
			remainingDubClips
		);
		for (const trackId of affectedMediaTracks) {
			if (!nextClips.some((clip) => clip.track_id === trackId)) disabledTracks.add(trackId);
		}
		if (audioIds.size && !asrIds.size && !localizedIds.size) {
			if (persistedSubtitleChanges) {
				dispatchTimelineReplacementFrom(deletionBaseline, {
					...draft,
					timeline_clips: nextClips,
					ui_state: {
						...draft.ui_state,
						disabled_media_tracks: [...disabledTracks],
						discarded_tts_task_ids: discardedTtsTaskIds,
						dub_lane_states: draft.ui_state?.dub_lane_states,
						track_states: trackStates
					}
				}, deletionLabel);
			} else {
				dispatchTimelineEdit({
					type: 'transaction',
					label: audioIds.size > 1 ? '删除多个音频片段' : '删除音频片段',
					deleteClipIds: [...audioIds],
					uiStatePatch: {
						disabled_media_tracks: [...disabledTracks],
						discarded_tts_task_ids: discardedTtsTaskIds
					}
				});
			}
			if (audioIds.has(selectedTimelineAudioClipId)) selectedTimelineAudioClipId = '';
			timelineSelectionItems = timelineSelectionItems.filter((item) => !audioIds.has(item.itemId));
			message = audioIds.size > 1 ? `已删除所选的 ${audioIds.size} 个音频片段` : '音频片段已从时间线移除';
			scheduleMessageClear(1600);
			return;
		}
	}

	function undoTimelineClipEdit() {
		if (!draft || !timelineEditController?.undoCount) return;
		cueSaveRevision += 1;
		invalidateLocalizedSubtitleSaves();
		timelineEditController.synchronizeExternalDraft(draft);
		const previousDraft = draft;
		const result = timelineEditController.undo();
		if (result.status !== 'applied') return;
		syncTimelineHistoryCounts();
		timelineEditRevision += 1;
		applyTimelineControllerDraft(previousDraft);
		saveTimelineEditImmediately();
	}

	function redoTimelineClipEdit() {
		if (!draft || !timelineEditController?.redoCount) return;
		cueSaveRevision += 1;
		invalidateLocalizedSubtitleSaves();
		timelineEditController.synchronizeExternalDraft(draft);
		const previousDraft = draft;
		const result = timelineEditController.redo();
		if (result.status !== 'applied') return;
		syncTimelineHistoryCounts();
		timelineEditRevision += 1;
		applyTimelineControllerDraft(previousDraft);
		saveTimelineEditImmediately();
	}

	function clearCueSelection() {
		selectedTimelineAudioClipId = '';
		selectedCueId = '';
		selectedLocalizedSubtitleId = '';
		selectedProvisionalSubtitle = null;
		updateDraftUiState({ selected_cue_id: '' });
	}

	async function deleteSubtitleItem(track: 'asr' | 'localized', itemId: string) {
		if (!draft || subtitleRuntimeBusy || localizationRuntimeBusy) return;
		if (track === 'localized') {
			if (!draft.localized_subtitles.some((cue) => cue.subtitle_id === itemId)) return;
			try {
				if (!(await flushPendingAutosave())) throw new Error('字幕修改尚未保存，请重试');
				const updated = await mutateCurrentProjectDraft(
					(activeProjectId) => Api.deleteVideoLocalizationLocalizedSubtitle(activeProjectId, itemId),
					{
						timelineHistoryLabel: '删除本土化字幕',
						beforeApply: invalidateLocalizedSubtitleSaves
					}
				);
				if (!updated) return;
				if (selectedLocalizedSubtitleId === itemId) selectedLocalizedSubtitleId = '';
				timelineSelectionItems = timelineSelectionItems.filter((item) => !(
					item.kind === 'subtitle'
					&& item.trackId === 'localizedSubtitles'
					&& item.itemId === itemId
				));
				ttsSelectionSession = refreshTtsSelectionSession(
					ttsSelectionSession,
					updated.cues,
					updated.localized_subtitles
				);
				message = '本土化字幕片段已删除，关联音频已解除绑定';
			} catch (e) {
				error = (e as Error).message || '删除本土化字幕片段失败';
			}
			return;
		}
		const cueId = itemId;
		const index = draft.cues.findIndex((cue) => cue.cue_id === cueId);
		if (index < 0) return;
		try {
			if (!(await flushPendingAutosave())) throw new Error('字幕修改尚未保存，请重试');
			const updated = await mutateCurrentProjectDraft(
				(activeProjectId) => Api.deleteVideoLocalizationCue(activeProjectId, cueId),
				{
					timelineHistoryLabel: '删除 ASR 字幕',
					beforeApply: () => { cueSaveRevision += 1; }
				}
			);
			if (!updated) return;
			draftOnlyCueIds = draftOnlyCueIds.filter((id) => id !== cueId && updated.cues.some((cue) => cue.cue_id === id));
			selectedCueId = selectedCueId === cueId
				? (updated.cues[Math.min(index, updated.cues.length - 1)]?.cue_id ?? '')
				: selectedCueId;
			timelineSelectionItems = timelineSelectionItems.filter((item) => !(item.kind === 'subtitle' && item.trackId === 'subtitles' && item.itemId === cueId));
			if (selectedCueId) updateTtsSelectionAnchor({ kind: 'source', itemId: selectedCueId }, timelineSelectionItems);
			else ttsSelectionSession = { ...EMPTY_TTS_SELECTION_SESSION };
			updateDraftUiState({ selected_cue_id: selectedCueId });
			message = '字幕片段已删除，关联本土化字幕已同步';
		} catch (e) {
			error = (e as Error).message || '删除字幕片段失败';
		}
	}

	function fillSubtitleGaps(track: 'asr' | 'localized') {
		if (!draft || subtitleRuntimeBusy) return;
		let nextDraft: VideoLocalizationDraft;
		let changed = 0;
		if (track === 'asr') {
			const previous = draft.cues;
			const next = extendSubtitleCuesToFollowingStart(previous);
			changed = next.filter((cue, index) => cue.end_ms !== previous[index]?.end_ms).length;
			if (!changed) {
				message = '没有需要延续的短停顿';
				return;
			}
			nextDraft = { ...draft, cues: next.map((cue, index) => cue.end_ms === previous[index]?.end_ms ? cue : protectCueManualEdit(previous[index], cue, { timing: true })) };
		} else {
			const previous = draft.localized_subtitles;
			const next = extendSubtitleCuesToFollowingStart(previous);
			changed = next.filter((cue, index) => cue.end_ms !== previous[index]?.end_ms).length;
			if (!changed) {
				message = '没有需要延续的短停顿';
				return;
			}
			nextDraft = { ...draft, localized_subtitles: next };
		}
		const applied = dispatchTimelineReplacement(
			nextDraft,
			track === 'asr' ? '延续 ASR 字幕短停顿' : '延续本土化字幕短停顿'
		);
		if (!applied) {
			error = '延续短停顿没有保存成功，请重试';
			return;
		}
		message = `已延续 ${changed} 处短停顿`;
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
		projectSessionController.cancelScheduledRun();
		const pendingSave = autoSaveStatus === 'dirty' || autoSaveStatus === 'failed' || autoSaveStatus === 'saving'
			? projectSessionController.run()
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
			const clearedDraft = await mutateCurrentProjectDraft(
				(activeProjectId) => Api.clearVideoLocalizationSubtitles(
					activeProjectId,
					track === 'asr' ? 'en' : 'zh'
				)
			);
			if (!clearedDraft) return;
			adoptPersistedTimelineReplacement(previousDraft, clearedDraft, `清空${label}`);
			if (track === 'asr') {
				selectedCueId = '';
				draftOnlyCueIds = [];
			} else selectedLocalizedSubtitleId = '';
			message = `${label}已清空`;
		} catch (e) {
			try {
				const activeProjectId = projectId;
				const recovered = activeProjectId
					? await projectDraftSessionController.refresh(
							activeProjectId,
							() => Api.videoLocalizationWorkspace(activeProjectId).then((workspace) => {
								applyWorkspaceCueTimingConfirmations(workspace);
								return workspace.draft;
							})
						)
					: null;
				if (recovered) applyFreshProjectDraft(recovered);
				else if (projectId) draft = previousDraft;
			} catch {
				if (projectId) draft = previousDraft;
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

	async function assignSpeakerToCue(speakerId: string, showToast = true) {
		if (!projectId || !selectedCue || !speakerId) return;
		error = '';
		try {
			if (cueNeedsDraftSave(selectedCue.cue_id)) {
				await persistDraftSnapshot();
			}
			const cueId = selectedCue.cue_id;
			const audioRoute = selectedCue.audio_route === 'manual_review'
				? (draft?.speakers.find((speaker) => speaker.speaker_id === speakerId)?.route ?? 'clone_from_source')
				: selectedCue.audio_route;
			if (!(await mutateCurrentProjectDraft(
				(activeProjectId) => Api.updateVideoLocalizationCue(activeProjectId, cueId, {
					speaker_id: speakerId,
					audio_route: audioRoute
				})
			))) return;
			selectedCueId = cueId;
			if (showToast) {
				message = '当前片段已绑定说话人';
				scheduleMessageClear(1800);
			} else {
				message = '说话人已新增并绑定当前片段';
				scheduleMessageClear(1800);
			}
		} catch (e) {
			error = (e as Error).message || '绑定说话人失败';
		}
	}

	async function applyLocalizationSrt(text: string) {
		if (!projectId || !draft || localizationRuntimeBusy) return;
		error = '';
		try {
			if (!(await mutateCurrentProjectDraft(
				(activeProjectId) => Api.importVideoLocalizationSubtitles(activeProjectId, 'zh', {
					srt_text: text,
					update_timing: true,
					overwrite_tts: false
				})
			))) return;
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			message = `已导入 ${draft.localized_subtitles.length} 条本土化字幕，ASR 字幕时间保持不变`;
			scheduleMessageClear(2600);
		} catch (e) {
			error = (e as Error).message || '导入 SRT 失败';
		}
	}

	async function importLocalizationSrtFile(file: File | null | undefined) {
		if (!file || importingLocalizedSrt) return;
		importingLocalizedSrt = true;
		try {
			await applyLocalizationSrt(await file.text());
		} finally {
			importingLocalizedSrt = false;
			if (localizationSrtInput) localizationSrtInput.value = '';
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
			applyFreshProjectDraft(payload as VideoLocalizationDraft);
			selectedCueId = cueId;
			autoSaveStatus = 'saved';
			lastAutoSavedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			message = '时间码已按人工试听确认';
			scheduleMessageClear(1800);
		} catch (e) {
			error = (e as Error).message || '确认时间码失败';
		} finally {
			confirmingCueTiming = false;
		}
	}

	function groupedTimelineSegmentId(clip: VideoLocalizationTimelineClip | null | undefined) {
		if (!clip || clip.track_id !== 'dub') return '';
		if (clip.subtitle_id?.startsWith('group_')) return clip.subtitle_id;
		return clip.clip_id.startsWith('clip_group_') ? clip.clip_id.slice('clip_'.length) : '';
	}

	function captureSubtitleTtsSelection(): TtsSubmissionSnapshot | null {
		return buildTtsSubmissionSnapshot({
			clientId: crypto.randomUUID().replaceAll('-', '').slice(0, 12),
			selectedSubtitle: ttsPrimaryLocalizedSubtitle,
			selectedSubtitles: selectedLocalizedSubtitles,
			selectedClip: selectedTimelineAudioClip,
			groupedClipSegmentId: groupedTimelineSegmentId(selectedTimelineAudioClip),
			sourceCueIds: ttsSelectionSession.sourceCueIds,
			localizedSubtitleIds: ttsSelectionSession.localizedSubtitleIds
		});
	}

	function beginTtsSubmission() {
		const context = ttsWorkflowSessionController.captureContext();
		if (!context) return null;
		pendingTtsSubmissionCount += 1;
		return context;
	}

	function endTtsSubmission(context: TtsWorkflowSessionContext) {
		if (ttsWorkflowSessionController.isCurrent(context)) {
			pendingTtsSubmissionCount = Math.max(0, pendingTtsSubmissionCount - 1);
		}
	}

	function stageSubtitleTtsInitialization(snapshot: TtsSubmissionSnapshot) {
		if (!draft) return;
		const context = ttsWorkflowSessionController.captureContext();
		if (!context) return;
		pendingTtsInitializations.set(snapshot.clientId, snapshot);
		ttsInitializationContexts.set(snapshot.clientId, context);
		cancelledTtsInitializationActions.delete(snapshot.clientId);
		return context;
	}

	function beginSubtitleTtsInitialization(snapshot: TtsSubmissionSnapshot) {
		if (!draft) return;
		const context = ttsInitializationContexts.get(snapshot.clientId)
			?? stageSubtitleTtsInitialization(snapshot);
		if (!context || !ttsWorkflowSessionController.isCurrent(context)) return;
		if (timelineRuntimeClips.some((clip) => clip.optimistic_tts_workflow_id === `init:${snapshot.clientId}`)) return;
		const placeholder = createTtsInitializationPlaceholder(timelineViewDraft?.timeline_clips ?? draft.timeline_clips, {
			clientId: snapshot.clientId,
			segmentId: snapshot.segmentId,
			primaryCueId: snapshot.primaryCueId,
			sourceCueIds: snapshot.sourceCueIds,
			startMs: snapshot.startMs,
			endMs: snapshot.endMs,
			targetClip: snapshot.targetClip
		});
		const placeholderId = placeholder.clip_id;
		timelineRuntimeClips = [placeholder, ...timelineRuntimeClips];
		selectedTimelineAudioClipId = placeholderId;
		timelineSelectionItems = [{ kind: 'audio', trackId: 'dub', itemId: placeholderId }];
	}

	function ttsSelectionAlreadyActive(snapshot: TtsSubmissionSnapshot) {
		if ([...pendingTtsInitializations.values()].some((item) => item.segmentId === snapshot.segmentId)) {
			return true;
		}
		return (draft?.tts_tasks ?? []).some((task) =>
			task.segment_id === snapshot.segmentId
			&& ['prepared', 'queued', 'running'].includes(task.status)
		);
	}

	function endSubtitleTtsInitialization(
		snapshot: TtsSubmissionSnapshot,
		restoreTarget = true,
		preserveContext = false
	) {
		pendingTtsInitializations.delete(snapshot.clientId);
		if (!preserveContext) {
			ttsInitializationContexts.delete(snapshot.clientId);
			cancelledTtsInitializationActions.delete(snapshot.clientId);
		}
		if (draft) {
			const cleanup = cleanupTtsInitializationClips(timelineRuntimeClips, snapshot.clientId);
			timelineRuntimeClips = cleanup.clips;
			const removedIds = new Set(cleanup.removedClipIds);
			timelineSelectionItems = timelineSelectionItems.filter((item) => !removedIds.has(item.itemId));
			if (removedIds.has(selectedTimelineAudioClipId)) {
				selectedTimelineAudioClipId = restoreTarget ? snapshot.targetClip?.clip_id ?? '' : '';
			}
		}
	}

	function cancelTtsInitialization(clientId: string, action: 'stop' | 'delete') {
		const snapshot = pendingTtsInitializations.get(clientId);
		if (!snapshot) {
			if (!draft) return false;
			const cleanup = cleanupTtsInitializationClips(timelineRuntimeClips, clientId);
			if (!cleanup.removedClipIds.length) return false;
			pendingTtsInitializations.delete(clientId);
			ttsInitializationContexts.delete(clientId);
			cancelledTtsInitializationActions.delete(clientId);
			const removedIds = new Set(cleanup.removedClipIds);
			timelineRuntimeClips = cleanup.clips;
			timelineSelectionItems = timelineSelectionItems.filter((item) => !removedIds.has(item.itemId));
			if (removedIds.has(selectedTimelineAudioClipId)) {
				selectedTimelineAudioClipId = '';
			}
			message = action === 'delete' ? '已停止并删除初始化中的配音片段' : '已停止初始化配音';
			return true;
		}
		cancelledTtsInitializationActions.set(clientId, action);
		endSubtitleTtsInitialization(snapshot, action === 'stop', true);
		message = action === 'delete' ? '已停止并删除初始化中的配音片段' : '已停止初始化配音';
		return true;
	}

	async function settleCancelledTtsInitialization(
		snapshot: TtsSubmissionSnapshot,
		request: GenerateRequest | null,
		reservedWorkflowId = ''
	) {
		const action = cancelledTtsInitializationActions.get(snapshot.clientId);
		const context = ttsInitializationContexts.get(snapshot.clientId);
		const stale = Boolean(context && !ttsWorkflowSessionController.isCurrent(context));
		if (!action && !stale) return false;
		const workflowId = reservedWorkflowId
			|| request?.video_localization_workflow_id
			|| request?.video_localization_submission_id;
		try {
			if (workflowId && context) {
				if (action === 'delete') await Api.deleteVideoLocalizationTtsTask(context.projectId, workflowId);
				else await Api.cancelVideoLocalizationTtsTask(context.projectId, workflowId);
			}
		} finally {
			endSubtitleTtsInitialization(snapshot, action !== 'delete');
		}
		return true;
	}

	async function flushPendingSubtitleEditsForTts() {
		while (pendingLocalizedSubtitleSaves.size > 0 || localizedSubtitleSaveBarrier.size > 0) {
			if (!(await flushPendingLocalizedSubtitleSaves())) return false;
			if (!(await localizedSubtitleSaveBarrier.flush())) return false;
		}
		return true;
	}

	async function reserveSelectedSubtitleGenerateWorkflow(
		selection: TtsSubmissionSnapshot,
		historyResultId = ''
	) {
		const context = ttsInitializationContexts.get(selection.clientId)
			?? ttsWorkflowSessionController.captureContext();
		if (!context) throw new Error('当前项目状态已经变化，请重新选择字幕后生成');
		if (!selection.segmentId) throw new Error('当前字幕选择缺少稳定的生成范围');
		if (!mediaAssetAvailable(mediaHealth, 'vocals')) throw new Error('请先准备分离人声轨，再生成字幕配音');
		if (!(await flushPendingSubtitleEditsForTts())) throw new Error('字幕修改尚未保存，请重试');
		if (!ttsWorkflowSessionController.isCurrent(context)) return null;
		const handoffBody = {
			submission_id: selection.clientId,
			...(historyResultId ? { history_result_id: historyResultId } : {}),
			target_subtitle_ids: selection.localizedSubtitleIds,
			source_cue_ids: selection.sourceCueIds
		};
		return Api.reserveVideoLocalizationTtsHandoff(
			context.projectId,
			selection.segmentId,
			handoffBody
		);
	}

	async function prepareSelectedSubtitleGenerateRequest(
		selection: TtsSubmissionSnapshot,
		historyResultId = ''
	) {
		const context = ttsInitializationContexts.get(selection.clientId)
			?? ttsWorkflowSessionController.captureContext();
		if (!context || !ttsWorkflowSessionController.isCurrent(context)) return null;
		const handoffBody = {
			submission_id: selection.clientId,
			...(historyResultId ? { history_result_id: historyResultId } : {}),
			target_subtitle_ids: selection.localizedSubtitleIds,
			source_cue_ids: selection.sourceCueIds
		};
		return Api.prepareVideoLocalizationTtsHandoff(
			context.projectId,
			selection.segmentId,
			handoffBody
		);
	}

	function localizedSubtitleForRequest(request: GenerateRequest) {
		if (!request.segment_id) return null;
		return draft?.localized_subtitles.find((item) => item.subtitle_id === request.segment_id) ?? null;
	}

	async function openSelectedSubtitleInGenerate() {
		const selection = captureSubtitleTtsSelection();
		if (!selection || preparingTtsHandoff) return;
		const context = ttsWorkflowSessionController.captureContext();
		if (!context) return;
		preparingTtsHandoff = true;
		message = '正在保存当前字幕…';
		try {
			if (!(await flushPendingSubtitleEditsForTts())) {
				throw new Error('字幕修改尚未保存，请重试');
			}
			if (!ttsWorkflowSessionController.isCurrent(context)) return;
			const intent: VideoLocalizationTtsHandoffIntent = {
				schema_version: 'video-localization-tts-handoff-v1',
				source: 'video_localization',
				mode: 'reference_only',
				project_id: context.projectId,
				segment_id: selection.segmentId,
				subtitle_id: selection.localizedSubtitleIds[0] ?? null,
				target_subtitle_ids: selection.localizedSubtitleIds,
				source_cue_ids: selection.sourceCueIds,
				created_at: new Date().toISOString()
			};
			const handoffMeta = {
				source: 'video_localization',
				mode: 'reference_only',
				project_id: context.projectId,
				cue_id: selection.segmentId,
				subtitle_id: intent.subtitle_id,
				reference_clip_id: null,
				recipe_id: null,
				created_at: intent.created_at
			};
			const params = new URLSearchParams({
				source: 'video_localization',
				mode: 'reference_only',
				project_id: context.projectId,
				cue_id: selection.segmentId
			});
			sessionStorage.removeItem(VIDEO_LOCALIZATION_TTS_HANDOFF_REQUEST_KEY);
			sessionStorage.setItem(VIDEO_LOCALIZATION_TTS_HANDOFF_INTENT_KEY, JSON.stringify(intent));
			sessionStorage.setItem(VIDEO_LOCALIZATION_TTS_HANDOFF_META_KEY, JSON.stringify(handoffMeta));
			await goto(`/generate?${params.toString()}`);
		} catch (e) {
			if (ttsWorkflowSessionController.isCurrent(context)) {
				sessionStorage.removeItem(VIDEO_LOCALIZATION_TTS_HANDOFF_INTENT_KEY);
				sessionStorage.removeItem(VIDEO_LOCALIZATION_TTS_HANDOFF_META_KEY);
				error = (e as Error).message || '打开语音合成失败，请重试';
			}
		} finally {
			if (ttsWorkflowSessionController.isCurrent(context)) preparingTtsHandoff = false;
		}
	}

	async function reuseSubtitleHistory(history: HistoryItem) {
		const selection = captureSubtitleTtsSelection();
		if (!selection) return;
		if (ttsSelectionAlreadyActive(selection)) {
			message = '这段字幕已经在生成，请等待当前任务完成';
			scheduleMessageClear(2200);
			return;
		}
		if (!stageSubtitleTtsInitialization(selection)) return;
		if (!ttsSubmissionQueue.enqueue(selection.clientId, async () => {
			await executeSubtitleHistorySubmission(history, selection);
		})) {
			endSubtitleTtsInitialization(selection);
		}
	}

	async function executeSubtitleHistorySubmission(
		history: HistoryItem,
		selection: TtsSubmissionSnapshot
	) {
		if (!pendingTtsInitializations.has(selection.clientId)) return;
		let base: GenerateRequest | null = null;
		const context = beginTtsSubmission();
		if (!context) {
			endSubtitleTtsInitialization(selection);
			return;
		}
		try {
			message = '正在登记配音任务…';
			const reserved = await reserveSelectedSubtitleGenerateWorkflow(selection, history.result_id);
			if (!reserved) {
				endSubtitleTtsInitialization(selection);
				return;
			}
			if (await settleCancelledTtsInitialization(selection, null, reserved.workflow_id)) return;
			if (!ttsWorkflowSessionController.isCurrent(context)) return;
			beginSubtitleTtsInitialization(selection);
			timelineRuntimeClips = promoteTtsInitializationPlaceholder(
				timelineRuntimeClips,
				selection.clientId,
				reserved.workflow_id
			);
			message = '正在准备当前人声片段…';
			base = await prepareSelectedSubtitleGenerateRequest(selection, history.result_id);
		} catch (e) {
			if (ttsWorkflowSessionController.isCurrent(context)) {
				try {
					const workflow = await Api.videoLocalizationTtsTask(context.projectId, selection.clientId);
					timelineRuntimeClips = promoteTtsInitializationPlaceholder(
						timelineRuntimeClips,
						selection.clientId,
						selection.clientId
					);
					mergeTtsWorkflowTask(workflow);
					endSubtitleTtsInitialization(selection);
					error = workflow.stages.find((stage) => stage.error_message)?.error_message
						?? ttsInitializationFailureMessage(e);
				} catch {
					const failureMessage = ttsInitializationFailureMessage(e);
					endSubtitleTtsInitialization(selection);
					error = failureMessage;
				}
			} else {
				endSubtitleTtsInitialization(selection);
			}
			return;
		} finally {
			endTtsSubmission(context);
		}
		try {
			if (await settleCancelledTtsInitialization(selection, base)) return;
		} catch (e) {
			if (ttsWorkflowSessionController.isCurrent(context)) {
				error = (e as Error).message || '停止初始化配音失败';
			}
			return;
		}
		if (!ttsWorkflowSessionController.isCurrent(context)) return;
		if (!base || !draft || !base.segment_id) {
			endSubtitleTtsInitialization(selection);
			return;
		}
		const workflowId = base.video_localization_workflow_id ?? selection.clientId;
		timelineRuntimeClips = promoteTtsInitializationPlaceholder(
			timelineRuntimeClips,
			selection.clientId,
			workflowId
		);
		await submitSubtitleTts(base, `沿用 ${history.engine_id} 参数`, selection);
	}

	async function executeHistoryPlacement(
		history: HistoryItem,
		request: Parameters<typeof Api.applyVideoLocalizationHistoryToTimeline>[2],
		optimisticClip?: VideoLocalizationTimelineClip
	) {
		const context = historyPlacementSessionController.capture(request.request_id);
		const clipId = request.new_clip_id ?? request.clip_id ?? '';
		const payload = Object.freeze({ ...request });
		// Capture the existing save before registering this command, so a later
		// autosave can wait for placement without creating a mutual wait.
		const currentSave = projectSessionController.waitForCurrentSave();
		historyApplyingResultId = history.result_id;
		timelineProjectionSessionController.invalidate();
		error = '';
		if (optimisticClip) {
			timelineRuntimeClips = [...timelineRuntimeClips, optimisticClip];
			selectedTimelineAudioClipId = clipId;
			draggingTtsHistory = null;
			message = `已放入合成配音轨 ${Number(optimisticClip.dub_lane ?? 0) + 1}，正在保存…`;
		}
		try {
			await historyPlacementSessionController.execute(context, {
				currentSave,
				request: () => projectDraftSessionController.mutate(
					context.projectId,
					() => Api.applyVideoLocalizationHistoryToTimeline(context.projectId, history.result_id, payload)
				),
				applyReceipt: (result) => applyTimelineMutationResult(result) !== null,
				refresh: refreshTimelineProjectionOnly,
				onApplied: () => {
					const appliedClip = draft?.timeline_clips.find((clip) => clip.clip_id === clipId);
					selectedTimelineAudioClipId = appliedClip?.clip_id ?? '';
					message = !appliedClip
						? '已同步最新时间线，配音记录仍保留'
						: request.clip_id
							? '已替换当前字幕的配音片段'
							: `已添加到合成配音轨 ${Number(appliedClip.dub_lane ?? 0) + 1}`;
					scheduleMessageClear(1800);
				},
				onSettled: () => {
					timelineRuntimeClips = timelineRuntimeClips.filter((clip) => clip.clip_id !== optimisticClip?.clip_id);
					if (selectedTimelineAudioClipId === optimisticClip?.clip_id && !draft?.timeline_clips.some((clip) => clip.clip_id === clipId)) {
						selectedTimelineAudioClipId = '';
					}
					historyApplyingResultId = '';
					draggingTtsHistory = null;
				}
			});
		} catch (placementError) {
			if (!historyPlacementSessionController.isCurrent(context)) return;
			if ((placementError instanceof ApiError && placementError.status === 0) || placementError instanceof TypeError) {
				// A network timeout does not tell us whether the server committed.
				await refreshTimelineProjectionOnly().catch(() => undefined);
			}
			if (historyPlacementSessionController.isCurrent(context)) error = (placementError as Error).message || '放入历史声音失败';
		}
	}

	async function applySubtitleHistoryToTimeline(history: HistoryItem) {
		if (!projectId || !draft || historyApplyingResultId) return;
		const segmentId = timelineDubSegmentId(selectedTimelineAudioClip) || selectedLocalizedSubtitle?.subtitle_id || selectedCue?.cue_id || '';
		if (!segmentId) {
			error = '请先选择要采用配音的字幕片段';
			return;
		}
		const clip = resolveHistoryTimelineClip(draft.timeline_clips, selectedTimelineAudioClip, segmentId, !selectedLocalizedSubtitle);
		const newClipId = clip
			? null
			: createHistoryTimelineClipId(globalThis.crypto.randomUUID());
		await executeHistoryPlacement(history, {
			request_id: newClipId ?? globalThis.crypto.randomUUID(),
			segment_id: segmentId,
			clip_id: clip?.clip_id ?? null,
			new_clip_id: newClipId
		});
	}

	function beginSubtitleHistoryDrag(history: HistoryItem) {
		draggingTtsHistory = history;
	}

	function endSubtitleHistoryDrag() {
		draggingTtsHistory = null;
	}

	async function dropSubtitleHistoryOnTimeline(history: HistoryItem, startMs: number, dubLane: number) {
		if (!projectId || !draft || historyApplyingResultId) return;
		const segmentId = history.segment_id || history.localized_subtitle_id || history.cue_id || '';
		if (!segmentId) {
			error = '这条配音记录缺少字幕关联，无法放入时间线';
			return;
		}
		const durationMs = Math.max(300, Math.round(history.duration_ms || 1800));
		const linkedSubtitle = draft.localized_subtitles.find((item) => item.subtitle_id === segmentId) ?? null;
		const optimisticClipId = createHistoryTimelineClipId(globalThis.crypto.randomUUID());
		const optimisticClip: VideoLocalizationTimelineClip = {
			clip_id: optimisticClipId,
			track_id: 'dub',
			subtitle_id: linkedSubtitle?.subtitle_id ?? history.localized_subtitle_id ?? null,
			cue_id: history.cue_id ?? linkedSubtitle?.linked_cue_id ?? linkedSubtitle?.source_cue_ids?.[0] ?? null,
			source_cue_ids: linkedSubtitle?.source_cue_ids ?? [],
			start_ms: startMs,
			end_ms: startMs + durationMs,
			source_start_ms: 0,
			source_end_ms: durationMs,
			audio_path: history.output_path || `history:${history.result_id}`,
			result_id: history.result_id,
			task_id: history.task_id,
			generation_id: history.generation_id ?? history.task_id,
			dub_lane: dubLane,
			status: 'applying',
			optimistic_history_result_id: history.result_id
		};
		await executeHistoryPlacement(history, {
			request_id: optimisticClipId,
			segment_id: segmentId,
			clip_id: null,
			new_clip_id: optimisticClipId,
			start_ms: startMs,
			dub_lane: dubLane,
			force_new: true
		}, optimisticClip);
	}

	async function deleteSubtitleHistory(history: HistoryItem) {
		if (!window.confirm('删除这条配音记录吗？已经复制到时间线的音频不会受影响。')) return;
		try {
			const outcome = await ttsHistoryController.deleteMany(projectId, [history.result_id]);
			if (outcome === null) return;
			reportTtsHistoryDeletion(outcome, '配音记录已删除');
		} catch (e) {
			await loadTtsHistory(projectId);
			error = (e as Error).message || '删除配音记录失败';
		}
	}

	async function deleteCurrentSubtitleHistory() {
		const segmentId = groupedTimelineSegmentId(selectedTimelineAudioClip) || selectedLocalizedSubtitle?.subtitle_id || selectedCue?.cue_id || '';
		const records = ttsHistory.filter((item) => historyBelongsToSegment(item, segmentId));
		if (!records.length || !window.confirm(`删除当前字幕的 ${records.length} 条配音记录吗？已经复制到时间线的音频不会受影响。`)) return;
		try {
			const outcome = await ttsHistoryController.deleteSegment(projectId, segmentId);
			if (outcome === null) return;
			reportTtsHistoryDeletion(outcome, `已删除 ${outcome.removed_records} 条配音记录`);
		} catch (e) {
			await loadTtsHistory(projectId);
			error = (e as Error).message || '删除配音记录失败';
		}
	}

	async function deleteAllSubtitleHistory() {
		if (!ttsHistoryTotal || !window.confirm('删除当前项目的全部配音记录吗？已经复制到时间线的音频不会受影响。')) return;
		try {
			const outcome = await ttsHistoryController.deleteAll(projectId);
			if (outcome === null) return;
			reportTtsHistoryDeletion(outcome, `已删除 ${outcome.removed_records} 条配音记录`);
		} catch (e) {
			await loadTtsHistory(projectId);
			error = (e as Error).message || '删除全部配音记录失败';
		}
	}

	function reportTtsHistoryDeletion(
		outcome: VideoLocalizationTtsHistoryDeleteResponse,
		successMessage: string
	) {
		message = successMessage;
		if (outcome.cleanup_failures > 0) {
			error = `记录已删除，但有 ${outcome.cleanup_failures} 个关联文件未能清理`;
		}
	}

	async function cleanupUnusedSubtitleHistory(scope: 'current' | 'all') {
		if (!projectId || !draft) return;
		const segmentId = groupedTimelineSegmentId(selectedTimelineAudioClip) || selectedLocalizedSubtitle?.subtitle_id || selectedCue?.cue_id || '';
		if (scope === 'current' && !segmentId) return;
		const scopeLabel = scope === 'all' ? '全部片段' : '当前片段';
		if (!window.confirm(`清理${scopeLabel}未使用的配音吗？会删除对应历史记录、原始生成文件和波形缓存；时间线正在使用的声音会保留。`)) return;
		error = '';
		try {
			if (!(await flushPendingAutosave())) throw new Error('项目修改尚未保存，请重试');
			const cleanupResult = await Api.cleanupUnusedVideoLocalizationTtsHistory(
				projectId,
				scope === 'current' ? segmentId : null
			);
			await loadTtsHistory(projectId);
			message = cleanupResult.removed_records
				? `已清理 ${cleanupResult.removed_records} 条未使用配音和 ${cleanupResult.removed_files} 个项目文件`
				: `${scopeLabel}没有可清理的未使用素材`;
		} catch (e) {
			await loadTtsHistory(projectId);
			error = (e as Error).message || '清理未使用配音素材失败';
		}
	}

	function ttsWorkflowProgress(task: VideoLocalizationTtsTask) {
		if (task.status === 'needs_attention') return null;
		const generation = task.stages.find((stage) => stage.kind === 'generation');
		const placement = task.stages.find((stage) => stage.kind === 'placement');
		if (placement?.status === 'success') return 1;
		if (generation?.status === 'success') return 0.85 + Math.max(0, Math.min(1, placement?.progress ?? 0)) * 0.15;
		return Math.max(0, Math.min(1, generation?.progress ?? 0)) * 0.85;
	}

	function ttsWorkflowActivityTask(task: VideoLocalizationTtsTask, actionPending?: 'cancel' | 'delete'): ActivityTask {
		const generation = task.stages.find((stage) => stage.kind === 'generation');
		const placement = task.stages.find((stage) => stage.kind === 'placement');
		const activeStage = generation?.status !== 'success' ? generation : placement;
		const errorStage = task.stages.find((stage) => stage.status === 'failed');
		const superseded = task.status === 'cancelled'
			&& placement?.parameters?.completion_reason === 'superseded_by_formal_group_candidate';
		const status: ActivityTask['status'] = task.status === 'prepared' ? 'queued' : task.status;
		const failedStageLabel = errorStage?.kind === 'generation' ? '生成声音' : '放入轨道';
		const failureSummary = errorStage?.error_message || '合成配音任务失败';
		const failureNotes = ['根据失败原因检查台词、参考音和生成设置后重试。'];
		const attentionStage = task.required_action === 'process_gaps'
			? '声音已生成，等待处理气口'
			: task.required_action === 'review_semantic_boundaries'
				? '等待继续处理语义边界'
				: task.required_action === 'regenerate_candidate'
					? '等待恢复处理'
					: task.required_action === 'resolve_capacity'
						? '等待处理时间窗容量'
						: task.required_action === 'edit_timeline'
							? '等待调整时间线'
							: '等待完成落轨';
		const taskStage = task.status === 'prepared'
			? '等待提交生成'
			: status === 'needs_attention'
				? attentionStage
			: status === 'success'
				? '已完成'
			: status === 'failed'
				? `${failedStageLabel}失败`
			: status === 'cancelled'
					? superseded ? '已采用其他版本' : '任务已取消'
					: activeStage?.kind === 'placement'
						? '正在放入轨道'
						: status === 'queued'
							? '排队等待生成'
							: '正在生成声音';
		const verificationRecord = ttsHistory.find((item) =>
			item.result_id === task.result_id || item.task_id === task.generation_task_id
		);
		const verification = verificationRecord?.verification;
		const verificationError = verificationRecord?.verification_error;
		const verificationNeedsReview = verification?.status === 'warning'
			|| verification?.status === 'failed'
			|| Boolean(verificationError);
		const stepStatus = (value: VideoLocalizationTtsTask['stages'][number]['status']): NonNullable<ActivityTask['steps']>[number]['status'] =>
			value === 'pending' ? 'todo' : value === 'queued' ? 'running' : value;
		return {
			id: `tts-workflow:${task.workflow_id}`,
			workflowId: task.workflow_id,
			label: `生成合成配音 · ${task.subtitle_summary || task.segment_id}`,
			stage: taskStage,
			detail: superseded
				? `${task.text}；这版声音仍保留在生成历史。`
				: task.text,
			progress: ttsWorkflowProgress(task),
			status,
			cancellable: task.status === 'prepared' || task.status === 'queued' || task.status === 'running',
			deletable: task.status !== 'success',
			actionPending,
			scope: { trackIds: ['dub'], itemIds: task.timeline_clip_id ? [task.timeline_clip_id] : [], area: 'generate', exclusive: false },
			createdAt: task.created_at,
			startedAt: generation?.started_at ?? null,
			completedAt: task.completed_at,
			engineId: typeof generation?.parameters?.engine_id === 'string' ? generation.parameters.engine_id : null,
			steps: task.stages.map((stage) => ({
				id: stage.kind,
				label: stage.kind === 'generation' ? '生成声音' : '放入轨道',
				status: status === 'needs_attention' && stage.kind === 'placement'
					? 'todo'
					: stepStatus(stage.status)
			})),
			finalResult: status === 'success' && (verification || verificationError) ? {
				status: verificationNeedsReview ? 'warning' : 'success',
				summary: verificationNeedsReview
					? '音频已放入配音轨；历史校对信息仅供参考。'
					: '音频已生成并放入配音轨。',
				metrics: !verification || verification.status === 'skipped'
					? []
					: [{ label: '台词覆盖率', value: `${Math.round(verification.coverage * 100)}%` }],
				sections: [],
				notes: verificationError ? [verificationError] : verification?.warnings ?? []
			} : undefined,
			failureResult: errorStage ? {
				status: 'failed',
				purpose: '说明配音任务在哪一步停止、已经保留了什么，以及应该如何继续。',
				summary: failureSummary,
				metrics: [
					{ label: '失败步骤', value: failedStageLabel },
					{ label: '声音生成', value: generation?.status === 'success' ? '已完成并保留' : '未完成' },
					{ label: '配音轨', value: placement?.status === 'success' ? '已写入' : '未写入' }
				],
				sections: [{
					title: '失败原因',
					items: [{
						title: failedStageLabel,
						text: errorStage.error_message || failureSummary,
						tone: 'warning',
						facts: [],
						links: []
					}]
				}],
				notes: failureNotes,
				debug: {
					description: '用于核对失败阶段和程序错误代码。',
					metrics: errorStage.error_code ? [{ label: '错误代码', value: errorStage.error_code }] : [],
					sections: [],
					notes: []
				}
			} : undefined
		};
	}

	function updateSubtitleTtsActivity(
		taskId: string,
		status: string,
		progress = 0,
		stageOverride = ''
	) {
		const activityStatus: ActivityTask['status'] = status === 'success' || status === 'failed' || status === 'cancelled'
			? status
			: status === 'pending' || status === 'queued'
				? 'queued'
				: 'running';
		const stage = stageOverride || (status === 'postprocessing'
			? '正在整理生成音频'
			: status === 'retrying'
				? '正在重试生成'
				: activityStatus === 'queued'
					? '等待生成声音'
						: '正在生成声音');
		foregroundTasks = upsertTtsGenerationActivity(foregroundTasks, {
			taskId,
			status: activityStatus,
			stage,
			progress
		});
	}

	function endSubtitleTtsActivity(taskId: string) {
		foregroundTasks = foregroundTasks.filter((task) => task.id !== `tts:${taskId}`);
	}

	function stopTrackingDeletedTtsWorkflow(task: VideoLocalizationTtsTask | null | undefined) {
		const taskId = task?.generation_task_id;
		if (!taskId) return;
		const monitorKey = taskId.startsWith('longform:') ? taskId : `task:${taskId}`;
		ttsWorkflowSessionController.cancelMonitor(monitorKey);
		endSubtitleTtsActivity(taskId);
	}

	function mergeTtsWorkflowTask(task: VideoLocalizationTtsTask) {
		if (!draft) return;
		const ttsTasks = [
			...(draft.tts_tasks ?? []).filter((item) => item.workflow_id !== task.workflow_id),
			task
		];
		draft = { ...draft, tts_tasks: ttsTasks };
		timelineRuntimeClips = reconcileTimelineRuntimeClips(draft, timelineRuntimeClips);
	}

	async function reconcileSubmittedTtsWorkflow(
		request: GenerateRequest,
		actionLabel: string,
		selection: TtsSubmissionSnapshot,
		context: TtsWorkflowSessionContext,
		submissionError: unknown
	) {
		const workflowId = request.video_localization_workflow_id
			?? request.video_localization_submission_id;
		if (!workflowId || !draft || !ttsWorkflowSessionController.isCurrent(context)) return false;
		if (await settleCancelledTtsInitialization(selection, request)) return true;
		const resultUnknown = (
			(submissionError instanceof ApiError && submissionError.code === 'TIMEOUT')
			|| submissionError instanceof TypeError
		);
		let workflow: VideoLocalizationTtsTask | null = null;
		try {
			workflow = await Api.videoLocalizationTtsTask(context.projectId, workflowId);
		} catch {
			if (!resultUnknown) return false;
		}
		if (workflow?.status === 'prepared' && !resultUnknown) {
			await Api.cancelVideoLocalizationTtsTask(context.projectId, workflowId).catch(() => null);
			return false;
		}

		timelineRuntimeClips = promoteTtsInitializationPlaceholder(
			timelineRuntimeClips,
			selection.clientId,
			workflowId
		);
		if (workflow) mergeTtsWorkflowTask(workflow);
		if (!ttsWorkflowSessionController.isCurrent(context)) return true;
		endSubtitleTtsInitialization(selection);
		const terminalFailure = workflow && ['failed', 'cancelled'].includes(workflow.status);
		error = terminalFailure
			? workflow?.stages.find((stage) => stage.error_message)?.error_message
				?? ttsInitializationFailureMessage(submissionError)
			: '';
		message = terminalFailure
			? ''
			: workflow
				? `${actionLabel}，后台任务已确认`
				: `${actionLabel}，连接中断，正在按任务编号恢复状态`;
		ttsWorkflowSessionController.startPolling(0);
		return true;
	}

	async function submitSubtitleTts(request: GenerateRequest, actionLabel: string, selection: TtsSubmissionSnapshot) {
		if (!draft || !request.segment_id) return;
		const context = beginTtsSubmission();
		if (!context) return;
		error = '';
		message = `${actionLabel}，正在提交…`;
		try {
			let longformTask: LongformTask | null = null;
			let task: {
				task_id: string;
				status: string;
				video_localization_workflow_id?: string | null;
			};
			try {
				task = await Api.generate(request);
			} catch (submitError) {
				if (!(submitError instanceof ApiError) || submitError.code !== 'LONGFORM_REQUIRED') throw submitError;
				const plan = await Api.generatePlan({
					text: request.text,
					engine_id: request.engine_id,
					planner_mode: 'rules',
					target_format: request.output_format
				});
				longformTask = await Api.generateLongform({
					generate_request: request,
					segments: plan.segments,
					verify_enabled: false,
					merge_enabled: true,
					max_retries: 2,
					stop_merge_on_verification_failed: false,
					asr_engine_id: 'qwen3-asr-mlx',
					silence_ms: 120,
					normalize: false
				});
				task = { task_id: `longform:${longformTask.longform_task_id}`, status: longformTask.status };
			}
			if (!ttsWorkflowSessionController.isCurrent(context)) {
				await settleCancelledTtsInitialization(selection, request);
				return;
			}
			if (await settleCancelledTtsInitialization(selection, request)) return;
			updateSubtitleTtsActivity(
				task.task_id,
				task.status,
				longformTask?.progress ?? 0,
				longformTask ? longformTtsStageLabel(longformTask) : ''
			);
			const workflowId = task.video_localization_workflow_id
				?? request.video_localization_workflow_id
				?? '';
			const workflowMarker = workflowId || task.task_id;
			timelineRuntimeClips = promoteTtsInitializationPlaceholder(
				timelineRuntimeClips,
				selection.clientId,
				workflowMarker
			);
			if (workflowId) {
				try {
					mergeTtsWorkflowTask(await Api.videoLocalizationTtsTask(context.projectId, workflowId));
				} catch {
					// The generic task still remains visible while the workflow endpoint catches up.
				}
			}
			if (longformTask) {
				if (workflowId) ttsWorkflowSessionController.startPolling(0);
				void monitorSubtitleLongformTask(longformTask.longform_task_id, task.task_id, workflowId, request.segment_id);
			} else if (workflowId) {
				// One project-scoped poll owns every ordinary localization TTS task.
				// Starting one monitor per queued clip multiplies full project reads and
				// lets a busy queue starve its own timeline/task updates.
				ttsWorkflowSessionController.startPolling(0);
			} else {
				void monitorUnboundSubtitleTtsTask(task.task_id);
			}
			endSubtitleTtsInitialization(selection);
			const groupedSubtitles = selection.subtitles.length > 1 ? selection.subtitles : [];
			const subtitle = groupedSubtitles[0] ?? selection.subtitles[0] ?? localizedSubtitleForRequest(request);
			const requestCue = draft.cues.find((item) => item.cue_id === request.segment_id) ?? null;
			const sourceCueIds = selection.sourceCueIds.length
				? [...selection.sourceCueIds]
				: requestCue?.cue_id
					? [requestCue.cue_id]
					: [];
			const sourceCueId = selection.primaryCueId ?? sourceCueIds[0] ?? null;
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
			draft = {
				...draft,
				generated_candidates: [candidate, ...draft.generated_candidates.filter((item) => item.candidate_id !== candidateId)]
			};
			const projectedPlaceholder = timelineViewDraft?.timeline_clips.find(
				(item) => item.optimistic_tts_workflow_id === workflowMarker
			);
			if (projectedPlaceholder) selectedTimelineAudioClipId = projectedPlaceholder.clip_id;
			message = `${actionLabel}，任务已开始`;
		} catch (e) {
			if (ttsWorkflowSessionController.isCurrent(context)) {
				if (await reconcileSubmittedTtsWorkflow(
					request,
					actionLabel,
					selection,
					context,
					e
				)) return;
				const failureMessage = ttsInitializationFailureMessage(e);
				endSubtitleTtsInitialization(selection);
				error = failureMessage;
			} else {
				await settleCancelledTtsInitialization(selection, request).catch(() => false);
			}
		} finally {
			endTtsSubmission(context);
		}
	}

	async function monitorUnboundSubtitleTtsTask(taskId: string) {
		const context = ttsWorkflowSessionController.captureContext();
		if (!context) return;
		await ttsWorkflowSessionController.monitor<Awaited<ReturnType<typeof Api.task>>>(`task:${taskId}`, {
			fetch: () => Api.task(taskId),
			isTerminal: (task) => ['success', 'failed', 'cancelled'].includes(task.status),
			onUpdate: (_activeContext, task) => {
				updateSubtitleTtsActivity(taskId, task.status, Number(task.progress ?? 0));
			},
			onTerminal: async (activeContext, task) => {
				await refreshTimelineProjectionOnly();
				if (ttsWorkflowSessionController.isCurrent(activeContext)) await loadTtsHistory(activeContext.projectId);
				if (!ttsWorkflowSessionController.isCurrent(activeContext)) return;
				if (task.status === 'success') {
					const generatedClip = draft?.timeline_clips.find((clip) =>
						clip.track_id === 'dub' && clip.task_id === taskId && Boolean(clip.audio_path)
					);
					if (generatedClip) {
						selectedTimelineAudioClipId = generatedClip.clip_id;
						const auditionStartMs = generatedClip.start_ms ?? 0;
						playbackSessionController.seek(auditionStartMs);
						setTimeout(() => {
							if (ttsWorkflowSessionController.isCurrent(activeContext)) playbackSessionController.play();
						}, 80);
						message = '配音已生成，已替换时间线并开始试听';
					} else {
						message = '配音已生成并加入记录';
					}
				} else {
					error = task.error_message || '配音生成没有完成';
				}
			},
			intervalMs: 1_000,
			maxAttempts: 3_600
		});
		if (ttsWorkflowSessionController.isCurrent(context)) endSubtitleTtsActivity(taskId);
	}

	function longformTtsStageLabel(task: LongformTask) {
		const total = Math.max(1, task.segments.length);
		const completed = task.segments.filter((segment) => ['success', 'failed', 'cancelled'].includes(segment.status)).length;
		const active = task.segments.find((segment) => ['running', 'postprocessing', 'retrying'].includes(segment.status));
		if (task.status === 'queued' || task.status === 'pending') return `排队中 · 准备 ${total} 个分段`;
		if (active) return `正在处理第 ${Math.max(1, active.index)} / ${total} 段`;
		if (completed >= total && !['success', 'failed', 'cancelled'].includes(task.status)) return `已完成 ${completed} / ${total} 段 · 正在合并`;
		if (task.status === 'success') return `已完成 ${total} 个分段并合并`;
		if (task.status === 'failed') return `分段任务未完成 · ${completed} / ${total}`;
		if (task.status === 'cancelled') return `分段任务已取消 · ${completed} / ${total}`;
		return `正在处理 ${completed} / ${total} 段`;
	}

	function updateLongformTimelinePlaceholder(activityId: string, workflowId: string, task: LongformTask) {
		if (!draft) return;
		const statusLabel = longformTtsStageLabel(task);
		const nextStatus = ['success', 'failed', 'cancelled'].includes(task.status) ? task.status : task.status === 'queued' ? 'queued' : 'running';
		timelineRuntimeClips = timelineRuntimeClips.map((clip) =>
			clip.optimistic_tts_workflow_id === workflowId || clip.task_id === activityId
				? { ...clip, status: nextStatus, status_label: statusLabel, generation_progress: task.progress }
				: clip
		);
	}

	async function monitorSubtitleLongformTask(longformTaskId: string, activityId: string, workflowId: string, segmentId: string) {
		const context = ttsWorkflowSessionController.captureContext();
		if (!context) return;
		await ttsWorkflowSessionController.monitor<LongformTask>(`longform:${longformTaskId}`, {
			fetch: () => Api.longformTask(longformTaskId),
			isTerminal: (task) => ['success', 'failed', 'cancelled'].includes(task.status),
			onUpdate: async (_activeContext, task) => {
				updateSubtitleTtsActivity(activityId, task.status, task.progress, longformTtsStageLabel(task));
				updateLongformTimelinePlaceholder(activityId, workflowId, task);
			},
			onTerminal: async (activeContext, task) => {
				let workflow: VideoLocalizationTtsTask | null = null;
				if (workflowId) {
					await ttsWorkflowSessionController.sync('terminal');
					if (!ttsWorkflowSessionController.isCurrent(activeContext)) return;
					workflow = await Api.videoLocalizationTtsTask(activeContext.projectId, workflowId).catch(() => null);
				} else {
					await refreshTimelineProjectionOnly();
					if (ttsWorkflowSessionController.isCurrent(activeContext)) await loadTtsHistory(activeContext.projectId);
				}
				if (!ttsWorkflowSessionController.isCurrent(activeContext)) return;
				if (workflow) mergeTtsWorkflowTask(workflow);
				if (task.status === 'success') {
					const generatedClip = draft?.timeline_clips.find((clip) =>
						clip.track_id === 'dub'
						&& Boolean(clip.audio_path)
						&& (clip.task_id === workflow?.generation_task_id || (!workflow?.generation_task_id && clip.subtitle_id === segmentId))
					);
					if (generatedClip) {
						selectedTimelineAudioClipId = generatedClip.clip_id;
						const auditionStartMs = generatedClip.start_ms ?? 0;
						playbackSessionController.seek(auditionStartMs);
						setTimeout(() => {
							if (ttsWorkflowSessionController.isCurrent(activeContext)) playbackSessionController.play();
						}, 80);
						message = `长段配音已完成 ${task.segments.length} 个分段，已合并回填并开始试听`;
					} else {
						message = '长段配音已生成并加入记录，但时间线没有采用本次结果';
					}
				} else {
					error = task.error_message || (task.status === 'cancelled' ? '长段配音任务已取消' : '长段配音任务没有完成');
				}
			},
			intervalMs: 1_000,
			maxAttempts: 21_600
		});
		if (ttsWorkflowSessionController.isCurrent(context)) endSubtitleTtsActivity(activityId);
	}

	function candidateIdFor(taskId: string) {
		return `candidate_${taskId}`.replace(/[^A-Za-z0-9_-]+/g, '_');
	}

	function operationFor(kind: VideoLocalizationOperation['kind']) {
		return operations.find((operation) => operation.kind === kind) ?? null;
	}

	function operationBusy(kind: VideoLocalizationOperation['kind']) {
		const operation = operationFor(kind);
		return Boolean(operation && isActiveOperation(operation));
	}

	function applyFreshProjectDraft(
		loadedDraft: VideoLocalizationDraft,
		preserveLocalContent = false,
		preserveTtsTasks = true,
		projectionHealth: ProjectMediaHealth | null = mediaHealth
	) {
		const refreshDraft = durableTimelineDraft({
			...loadedDraft,
			tts_tasks: preserveTtsTasks && draft
				? draft.tts_tasks ?? []
				: loadedDraft.tts_tasks ?? []
		});
		const editableDraft = withPendingDraftUiState(withEditableMediaClips(
			refreshDraft,
			projectionHealth
		));
		if (timelineEditController) {
			const replacedClipIds = timelineEditController.mergeRefresh(editableDraft);
			for (const clipId of replacedClipIds) {
				timelineDeletedClipIds.delete(clipId);
				timelineDirtyFieldsByClipId.delete(clipId);
			}
			for (const clipId of timelineControllerDeletedClipIds) timelineDeletedClipIds.delete(clipId);
			timelineControllerDeletedClipIds = new Set(timelineEditController.deletedTimelineClipIds);
			for (const clipId of timelineControllerDeletedClipIds) timelineDeletedClipIds.add(clipId);
			if (!timelineEditController.hasPendingChanges) timelineSavedRevision = timelineEditRevision;
			syncTimelineHistoryCounts();
		}
		const hasUnsavedTimelineEdits = timelineEditRevision !== timelineSavedRevision
			|| timelineDirtyFieldsByClipId.size > 0
			|| timelineDeletedClipIds.size > 0;
		const shouldPreserveLocalContent = Boolean(draft) && (
			preserveLocalContent
			|| hasUnsavedTimelineEdits
			|| hasPendingProjectContentEdits()
		);
		if (!hasUnsavedTimelineEdits) timelineDirtyFieldsByClipId = new Map();
		const mergedDraft = draft && shouldPreserveLocalContent
			? mergeDraftAfterConflict(editableDraft, draft, {
					baseDraft: draftConflictMergeBase ?? undefined,
					deletedTimelineClipIds: timelineDeletedClipIds,
					timelineDirtyFieldsByClipId
				})
			: editableDraft;
		draftConflictMergeBase = snapshotDraftForConflictMerge(editableDraft);
		if (timelineEditController) {
			timelineEditController.synchronizeExternalDraft(mergedDraft);
			const controlledDraft = withPendingDraftUiState(timelineEditController.draft);
			draft = {
				...mergedDraft,
				cues: controlledDraft.cues,
				localized_subtitles: controlledDraft.localized_subtitles,
				timeline_clips: controlledDraft.timeline_clips,
				ui_state: {
					...mergedDraft.ui_state,
					dub_lane_states: controlledDraft.ui_state?.dub_lane_states,
					disabled_media_tracks: controlledDraft.ui_state?.disabled_media_tracks,
					discarded_tts_task_ids: controlledDraft.ui_state?.discarded_tts_task_ids
				}
			};
		} else {
			draft = mergedDraft;
			timelineEditController = new TimelineEditController(mergedDraft);
		}
		timelineRuntimeClips = reconcileTimelineRuntimeClips(draft, timelineRuntimeClips);
		draftOnlyCueIds = [];
		// Task history is synchronized independently through the compact
		// operation summaries endpoint. Draft refreshes may carry older embedded
		// operation records and must never overwrite that richer task state.
		if (!selectedCueId && draft.cues[0]) selectedCueId = draft.cues[0].cue_id;
		return draft;
	}

	async function mutateCurrentProjectDraft(
		request: (activeProjectId: string) => Promise<VideoLocalizationDraft>,
		options: {
			preserveConcurrentChanges?: boolean;
			preserveTtsTasks?: boolean;
			beforeApply?: () => void;
			timelineHistoryLabel?: string;
			timelineHistoryPersisted?: boolean;
		} = {}
	) {
		if (!projectId) return null;
		timelineProjectionSessionController.invalidate();
		const mutationProjectId = projectId;
		const baselineDraft = draft;
		let updated: VideoLocalizationDraft | null;
		try {
			updated = await projectDraftSessionController.mutate(
				mutationProjectId,
				() => request(mutationProjectId)
			);
		} catch (mutationError) {
			if (projectId !== mutationProjectId) return null;
			throw mutationError;
		}
		if (!updated) return null;
		if (baselineDraft && options.timelineHistoryLabel) {
			if (options.timelineHistoryPersisted === false) {
				dispatchTimelineEdit(
					timelineReplacementTransaction(
						baselineDraft,
						withEditableMediaClips(updated),
						options.timelineHistoryLabel
					)
				);
			} else {
				adoptPersistedTimelineReplacement(
					baselineDraft,
					updated,
					options.timelineHistoryLabel
				);
			}
		}
		options.beforeApply?.();
		const applied = applyFreshProjectDraft(
			updated,
			options.preserveConcurrentChanges !== false && draft !== baselineDraft,
			options.preserveTtsTasks !== false
		);
		draftProjectId = mutationProjectId;
		return applied;
	}

	async function refreshDraftOnly() {
		if (!projectId) return null;
		timelineProjectionSessionController.invalidate();
		const refreshingProjectId = projectId;
		const loadedWorkspace = await projectDraftSessionController.refresh(
			refreshingProjectId,
			() => Api.videoLocalizationWorkspace(refreshingProjectId)
		);
		if (!loadedWorkspace) return null;
		if (!workspaceRevisionController.canApply(refreshingProjectId, loadedWorkspace.revision)) return null;
		const refreshedMediaHealth = loadedWorkspace.media_health;
		mediaHealth = refreshedMediaHealth;
		semanticTtsGroups = loadedWorkspace.semantic_tts_groups ?? [];
		applyWorkspaceCueTimingConfirmations(loadedWorkspace);
		const refreshedDraft = withLoadedWorkspaceDetails(loadedWorkspace.draft);
		applyFreshProjectDraft(
			refreshedDraft,
			false,
			true,
			refreshedMediaHealth
		);
		workspaceRevisionController.consume(refreshingProjectId, loadedWorkspace.revision, 'workspace');
		return durableTimelineDraft(withEditableMediaClips(refreshedDraft, refreshedMediaHealth));
	}

	async function refreshTimelineProjectionOnly() {
		if (!projectId || !draft) return;
		const refreshingProjectId = projectId;
		const projection = await timelineProjectionSessionController.refreshLatest(
			refreshingProjectId,
			() => Api.videoLocalizationTimelineProjection(refreshingProjectId)
		);
		if (
			!projection
			|| projectId !== refreshingProjectId
			|| !draft
			|| !workspaceRevisionController.canApply(refreshingProjectId, projection.revision)
		) return;
		applyFreshProjectDraft(
				draftWithLiveTimelineProjection(draft, projection, {
					deletedClipIds: timelineDeletedClipIds,
					addedClipIds: timelineEditController?.addedTimelineClipIds,
					dirtyFieldsByClipId: timelineDirtyFieldsByClipId
				}),
			false,
			true,
			mediaHealth
		);
		workspaceRevisionController.consume(refreshingProjectId, projection.revision, 'timeline');
	}

	function withEditableMediaClips(
		value: VideoLocalizationDraft,
		health: ProjectMediaHealth | null = mediaHealth
	) {
		return normalizeWorkspaceDraft(value, health);
	}

	function applyTimelineMutationResult(
		result: VideoLocalizationTimelineMutationResponse
	) {
		const currentDraft = draft;
		if (!currentDraft) return null;
		return workspaceRevisionController.applyReceipt(projectId, result.revision, () => {
			const authoritativeDraft = withPendingDraftUiState(durableTimelineDraft(mergeTimelineMutation(currentDraft, result)));
			if (timelineEditController) {
				timelineEditController.synchronizeExternalDraft(authoritativeDraft);
				const controlledDraft = withPendingDraftUiState(timelineEditController.draft);
				draft = {
					...authoritativeDraft,
					cues: controlledDraft.cues,
					localized_subtitles: controlledDraft.localized_subtitles,
					timeline_clips: controlledDraft.timeline_clips,
					ui_state: {
						...authoritativeDraft.ui_state,
						dub_lane_states: controlledDraft.ui_state?.dub_lane_states,
						disabled_media_tracks: controlledDraft.ui_state?.disabled_media_tracks,
						discarded_tts_task_ids: controlledDraft.ui_state?.discarded_tts_task_ids
					}
				};
			} else {
				draft = authoritativeDraft;
			}
			timelineRuntimeClips = reconcileTimelineRuntimeClips(draft, timelineRuntimeClips);
			draftConflictMergeBase = snapshotDraftForConflictMerge(authoritativeDraft);
			syncTimelineHistoryCounts();
			return draft;
		});
	}

	async function closeCurrentProject() {
		if (hasPendingSaveWork() && !(await flushPendingAutosave())) {
			error = '项目仍有未保存修改，已保留当前页面，请重试保存后再关闭';
			return;
		}
		cancelPendingAutosave();
		clearProjectRuntimeState();
		projectId = '';
		clearProjectIdFromUrl();
		inspectorCollapsed = false;
		inspectorSection = 'tasks';
		autoSaveStatus = 'idle';
	}

	function updateSubtitleDisplaySettings(settings: SubtitleDisplaySettings) {
		updateDraftUiState({ subtitle_preview: settings });
	}

	function toggleSubtitleSource(source: SubtitleDisplaySource) {
		updateSubtitleDisplaySettings(subtitleDisplay.toggleSource(source));
	}

	function updateTrackState(trackId: VideoLocalizationTrackId, patch: Partial<VideoLocalizationTrackState>) {
		updateDraftUiState({ track_states: { [trackId]: patch } });
	}

	function updateDubLaneState(lane: number, patch: Partial<VideoLocalizationTrackState>) {
		const key = String(Math.max(0, Math.floor(lane)));
		updateDraftUiState({
			dub_lane_states: { [key]: patch },
			...(key === '0'
				? { track_states: { dub: patch } }
				: {})
		});
	}

	function updateAudioTrackOrder(order: VideoLocalizationAudioTrackOrder) {
		updateDraftUiState({ audio_track_order: resolveAudioTrackOrder(order) });
	}

	function updateTimelineZoom(nextZoom: number) {
		timelineZoom = clampNumber(nextZoom, 1, 1200, 1);
		rememberTimelineViewState({ timeline_zoom: timelineZoom });
	}

	function updateTimelineViewportStart(nextStartMs: number) {
		timelineViewportStartMs = Math.max(0, Math.round(nextStartMs));
		rememberTimelineViewState({ timeline_viewport_start_ms: timelineViewportStartMs });
	}

	function updateHoverScrubEnabled(enabled: boolean) {
		updateDraftUiState({ timeline_hover_scrub_enabled: enabled });
		if (!enabled) playbackSessionController.disableHoverScrub();
	}

	function hoverScrubPreview(timeMs: number) {
		playbackSessionController.hoverScrub(timeMs);
	}

	function endHoverScrubPreview() {
		playbackSessionController.endHoverScrub();
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

	function focusInspector(section: InspectorSection) {
		const wasCollapsed = inspectorCollapsed;
		inspectorCollapsed = false;
		inspectorSection = section;
		if (section === 'subtitle') void ensureWorkspaceDetail('transcription');
		if (section === 'dubbing') {
			void ensureWorkspaceDetail('generated_candidates');
			void ensureWorkspaceDetail('dubbing_production');
		}
		// Switching tabs is page-local interaction. Avoid replacing the full draft on
		// every click; that invalidates the entire timeline and makes the sidebar feel
		// blocked. Persist only when this action actually expands a collapsed sidebar.
		if (wasCollapsed) updateDraftUiState({ sidebar_collapsed: false });
	}

	function openTaskCenter() {
		focusInspector('tasks');
		taskCenterPulseKey += 1;
	}

	async function cancelActivityTask(task: ActivityTask) {
		if (task.workflowId) {
			if (ttsTaskAction) return;
			const context = ttsWorkflowSessionController.captureContext();
			if (!context) return;
			ttsTaskAction = { id: task.workflowId, kind: 'cancel' };
			try {
				await Api.cancelVideoLocalizationTtsTask(context.projectId, task.workflowId);
				if (!ttsWorkflowSessionController.isCurrent(context)) return;
				ttsWorkflowSessionController.stopPolling();
				await ttsWorkflowSessionController.sync('manual');
				if (!ttsWorkflowSessionController.isCurrent(context)) return;
				await refreshDraftOnly();
				message = '配音任务已停止';
			} catch (e) {
				if (ttsWorkflowSessionController.isCurrent(context)) {
					error = (e as Error).message || '停止配音任务失败';
				}
			} finally {
				if (ttsWorkflowSessionController.isCurrent(context)) ttsTaskAction = null;
			}
			return;
		}
		if (!task.operationId) return;
		const operation = operations.find((item) => item.operation_id === task.operationId);
		if (operation) await cancelOperation(operation);
	}

	async function deleteActivityTask(task: ActivityTask) {
		if (!task.workflowId || ttsTaskAction) return;
		const context = ttsWorkflowSessionController.captureContext();
		if (!context) return;
		const workflow = draft?.tts_tasks?.find((item) => item.workflow_id === task.workflowId) ?? null;
		const generationTaskId = workflow?.generation_task_id ?? '';
		const deletedWorkflowClipIds = new Set(
			(timelineViewDraft?.timeline_clips ?? [])
				.filter((clip) =>
					clip.optimistic_tts_workflow_id === task.workflowId
					|| (generationTaskId && (clip.task_id === generationTaskId || clip.generation_id === generationTaskId))
					|| (workflow?.timeline_clip_id && clip.clip_id === workflow.timeline_clip_id)
				)
				.map((clip) => clip.clip_id)
		);
		deletedWorkflowClipIds.add(`pending_tts_${task.workflowId}`);
		ttsTaskAction = { id: task.workflowId, kind: 'delete' };
		try {
			if (!(await flushPendingAutosave())) throw new Error('项目修改尚未保存，请重试');
			if (!ttsWorkflowSessionController.isCurrent(context)) return;
			const deletedDraft = await Api.deleteVideoLocalizationTtsTask(context.projectId, task.workflowId);
			if (!ttsWorkflowSessionController.isCurrent(context)) return;
			stopTrackingDeletedTtsWorkflow(workflow);
			ttsWorkflowSessionController.stopPolling();
			timelineRuntimeClips = timelineRuntimeClips.filter((clip) => (
				persistedTtsWorkflowId(String(clip.optimistic_tts_workflow_id ?? '')) !== task.workflowId
			));
			applyFreshProjectDraft(deletedDraft, false, false);
			if ((deletedDraft.tts_tasks ?? []).some(ttsWorkflowActive)) {
				ttsWorkflowSessionController.startPolling();
			}
			timelineSelectionItems = timelineSelectionItems.filter(
				(item) => item.kind !== 'audio' || !deletedWorkflowClipIds.has(item.itemId)
			);
			if (deletedWorkflowClipIds.has(selectedTimelineAudioClipId)) selectedTimelineAudioClipId = '';
			message = task.status === 'queued' || task.status === 'running'
				? '已停止并删除配音片段'
				: '已删除配音任务';
		} catch (e) {
			if (ttsWorkflowSessionController.isCurrent(context)) {
				error = (e as Error).message || '停止并删除配音片段失败';
			}
		} finally {
			if (ttsWorkflowSessionController.isCurrent(context)) ttsTaskAction = null;
		}
	}

	async function retryActivityTask(task: ActivityTask) {
		if (!task.operationId || (task.status !== 'failed' && task.status !== 'cancelled')) return;
		const operation = operations.find((item) => item.operation_id === task.operationId);
		if (operation) await retryOperation(operation);
	}

	function focusGenerateToSelection(startMs: number, endMs: number) {
		audioSelectionRange = { start_ms: startMs, end_ms: endMs };
		focusInspector('dubbing');
	}

	function updatePreviewTime(timeMs: number) {
		playbackSessionController.updateTime(timeMs);
	}

	function updatePreviewPlaying(playing: boolean) {
		playbackSessionController.updatePlaying(playing);
	}

	function registerPreviewPlaybackController(controller: PlaybackDriver | null) {
		playbackSessionController.register(controller);
		if (controller) void restorePreviewPlaybackState(projectId);
	}

	async function restorePreviewPlaybackState(expectedProjectId: string) {
		await tick();
		playbackSessionController.restore(expectedProjectId);
	}

	function persistPlayhead(_timeMs: number) {
		playbackSessionController.persistCurrentTime();
	}

	function updateTimelineSelectionRange(range: { startMs: number; endMs: number } | null) {
		timelineSelectionRange = range ? { start_ms: range.startMs, end_ms: range.endMs } : null;
		playbackSessionController.updateSelectionRange(timelineSelectionRange);
	}

	function playCommittedTimelineSelection(range: { startMs: number; endMs: number }) {
		playbackSessionController.playRange({ start_ms: range.startMs, end_ms: range.endMs });
	}

	function seekPreview(timeMs: number) {
		playbackSessionController.seek(timeMs);
	}

	function timelineNavigationSources(): TimelineNavigationSources {
		return {
			durationMs: draft?.source_media.duration_ms ?? 0,
			frameRate: draft?.source_media.frame_rate,
			asrCues: subtitleDisplay.tracks.asr.cues,
			localizedSubtitles: subtitleDisplay.tracks.localized.cues,
			audioClips: draft?.timeline_clips ?? []
		};
	}

	function handleTimelineTransport(action: 'start' | 'end' | 'previous-boundary' | 'next-boundary' | 'play-pause') {
		if (action === 'play-pause') {
			playbackSessionController.togglePlayback(timelineSelectionRange);
			return;
		}
		playbackSessionController.setLoopRange(null);
		if (action === 'start') seekPreview(0);
		else if (action === 'end') seekPreview(lastFrameStartMs(draft?.source_media.duration_ms ?? 0, normalizeFrameRate(draft?.source_media.frame_rate)));
		else seekPreview(timelineBoundaryTarget(timelineNavigationSources(), previewTimeMs, action === 'previous-boundary' ? 'previous' : 'next'));
	}

	function seekTimeline(timeMs: number, transient = false) {
		if (transient) playbackSessionController.previewSeek(timeMs);
		else playbackSessionController.commitSeek(timeMs);
	}

	function updateDraftUiState(patch: Record<string, unknown>) {
		if (!draft) return;
		const clientPatch = clientVideoLocalizationUiPatch(patch);
		if (!Object.keys(clientPatch).length) return;
		// UI-only controls (mute, visibility, panel state) must not replace the
		// complete project draft. Replacing it invalidates every timeline-derived
		// value even though media, subtitles and clips did not change.
		draft.ui_state = mergeVideoLocalizationUiState(draft.ui_state ?? {}, clientPatch);
		queueDraftUiStatePatch(clientPatch);
	}

	function withPendingDraftUiState(value: VideoLocalizationDraft): VideoLocalizationDraft {
		return { ...value, ui_state: projectSessionController.mergePendingUiState(value.ui_state ?? {}) };
	}

	function queueDraftUiStatePatch(patch: Record<string, unknown>) {
		if (!draft) return;
		projectSessionController.queueUiPatch(patch);
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

	function rememberTimelineViewState(patch: TimelineViewState) {
		if (!projectId || typeof window === 'undefined') return;
		writeTimelineViewState(window.sessionStorage, projectId, patch);
	}

	function scheduleDraftAutosave(scope: 'ui' | 'draft' = 'draft') {
		projectSessionController.schedule(scope);
	}

	function saveTimelineEditImmediately() {
		projectSessionController.schedule('draft');
		void projectSessionController.flush();
	}

	function discardScheduledDraftAutosave() {
		projectSessionController.discardPending(false);
	}

	async function saveProjectSessionAutosave(request: ProjectAutosaveRequest) {
		if (!draft) return;
		const savingProjectId = request.projectId;
		const savingScope = request.scope;
		const uiPatch = request.uiPatch;
		if (savingScope === 'draft') {
			await historyPlacementSessionController.waitForPending();
			if (!draft || projectId !== savingProjectId) return;
		}
		const savingDraft = Object.keys(uiPatch).length
			? { ...draft, ui_state: mergeVideoLocalizationUiState(draft.ui_state ?? {}, uiPatch) }
			: draft;
		if (savingScope === 'draft') timelineProjectionSessionController.invalidate();
		if (savingScope === 'ui') {
			const savedUiState = await projectDraftSessionController.mutate(
				savingProjectId,
				() => Api.updateVideoLocalizationUiState(savingProjectId, uiPatch)
			);
			if (!savedUiState || projectId !== savingProjectId || !draft) return;
			draft = {
				...draft,
				updated_at: savedUiState.updated_at,
				ui_state: projectSessionController.mergePendingUiState(mergeVideoLocalizationUiState(
					draft.ui_state ?? {},
					savedUiState.ui_state_patch
				))
			};
			timelineEditController?.synchronizeExternalDraft(draft);
			workspaceRevisionController.observe(savingProjectId, savedUiState.revision);
			return;
		}
		const { clearPersistedTimelineDirtyFields, snapshotTimelineDirtyFields } =
			await import('./timeline-edit-save-state');
		let savingTimelineDirtyFields = snapshotTimelineDirtyFields(timelineDirtyFieldsByClipId);
		const savingTimelineController = timelineEditController;
		const savingTimelineRevision = timelineEditRevision;
		savingTimelineController?.synchronizeExternalDraft(savingDraft);
		const compactTimelineSavePacket = savingTimelineController
			? savingTimelineController.prepareCompactSave()
			: null;
		if (
			!savingTimelineController
			&& (
				savingTimelineRevision !== timelineSavedRevision
				|| savingTimelineDirtyFields.size > 0
				|| timelineDeletedClipIds.size > 0
			)
		) {
			throw new Error('时间线保存状态已失效，已阻止旧工作区覆盖当前片段');
		}
			if (compactTimelineSavePacket) {
				let compactSaved;
				let compactPersistedDraft: VideoLocalizationDraft | null = null;
				let timelineRequestStarted = false;
			try {
				compactSaved = await projectDraftSessionController.mutate(
					savingProjectId,
					() => saveTimelineEditWithUiPatch(
						() => {
							timelineRequestStarted = true;
							return Api.updateVideoLocalizationTimelineEdit(savingProjectId, compactTimelineSavePacket.request);
						},
						(patch) => Api.updateVideoLocalizationUiState(savingProjectId, patch),
						uiPatch
					)
				);
			} catch (saveError) {
				if (timelineRequestStarted && saveError instanceof ApiError
					&& saveError.status >= 400 && saveError.status < 500 && saveError.status !== 408
					&& saveError.code !== 'INVALID_RESPONSE') {
					savingTimelineController?.rejectSave(compactTimelineSavePacket.packetId);
				} else {
					savingTimelineController?.retainCompactSaveForRetry(compactTimelineSavePacket);
				}
				throw saveError;
			}
			if (!compactSaved) {
				savingTimelineController?.retainCompactSaveForRetry(compactTimelineSavePacket);
				if (
					projectId === savingProjectId
					&& savingTimelineController?.hasPendingChanges
				) scheduleDraftAutosave();
				return;
			}
			const applyCompactReceipt = workspaceRevisionController.canApply(
				savingProjectId,
				compactSaved.revision
			);
			if (!applyCompactReceipt) {
				try {
					compactPersistedDraft = await refreshDraftOnly();
					if (!compactPersistedDraft || projectId !== savingProjectId) {
						throw new Error('旧保存回执已确认，等待同步最新项目后再完成保存');
					}
				} catch (refreshError) {
					savingTimelineController?.retainCompactSaveForRetry(compactTimelineSavePacket);
					throw refreshError;
				}
			}
			if (
				projectId === savingProjectId
				&& savingTimelineController
				&& timelineEditController === savingTimelineController
				&& draft
			) {
				savingTimelineController.acknowledgeCompactSave(
					compactTimelineSavePacket.packetId,
					compactSaved,
					{ applyReceipt: applyCompactReceipt }
				);
				const controlledDraft = withPendingDraftUiState(durableTimelineDraft(savingTimelineController.draft));
				draft = {
					...draft,
					updated_at: controlledDraft.updated_at,
					cues: controlledDraft.cues,
					localized_subtitles: controlledDraft.localized_subtitles,
					timeline_clips: controlledDraft.timeline_clips,
					ui_state: {
						...draft.ui_state,
						dub_lane_states: controlledDraft.ui_state?.dub_lane_states,
						disabled_media_tracks: controlledDraft.ui_state?.disabled_media_tracks,
						discarded_tts_task_ids: controlledDraft.ui_state?.discarded_tts_task_ids
					}
				};
				savingTimelineController.synchronizeExternalDraft(draft);
				clearPersistedTimelineDirtyFields(
					timelineDirtyFieldsByClipId,
					savingTimelineDirtyFields,
					compactPersistedDraft ?? { ...draft, timeline_clips: compactSaved.timeline_clips },
					draft
				);
				draftConflictMergeBase = snapshotDraftForConflictMerge(draft);
			}
			if (
				timelineEditRevision === savingTimelineRevision
				&& timelineDirtyFieldsByClipId.size === 0
			) {
				timelineSavedRevision = savingTimelineRevision;
				timelineDeletedClipIds = new Set();
			}
			workspaceRevisionController.observe(savingProjectId, compactSaved.revision);
			draftOnlyCueIds = [];
			if (savingTimelineController?.hasPendingChanges) scheduleDraftAutosave();
			return;
		}
		if (savingTimelineController?.hasPendingChanges) {
			if (!savingTimelineController.settlePendingNoop()) {
				throw new Error('时间线编辑无法生成安全的局部保存请求，已保留修改，请重试');
			}
			savingTimelineDirtyFields = new Map();
			timelineDirtyFieldsByClipId = new Map();
			for (const clipId of timelineControllerDeletedClipIds) timelineDeletedClipIds.delete(clipId);
			timelineControllerDeletedClipIds = new Set();
			if (timelineEditRevision === savingTimelineRevision) {
				timelineSavedRevision = savingTimelineRevision;
			}
		}
		let timelineSavePacket = null as ReturnType<TimelineEditController['prepareSave']> | null;
		if (savingTimelineController) {
			timelineSavePacket = savingTimelineController.prepareSave();
		}
		const savingDeletedTimelineClipIds = timelineDeletionIntentForSave(
			timelineDeletedClipIds,
			timelineControllerDeletedClipIds,
			timelineSavePacket?.deletedClipIds ?? []
		);
		const savingDeletedTimelineClipIdentities = new Map(
			[...savingDeletedTimelineClipIds].flatMap((clipId) => {
				const identity = timelineSavePacket?.deletedClipIdentities.get(clipId);
				return identity ? [[clipId, identity] as const] : [];
			})
		);
		const savingAddedTimelineClipIds = new Set(timelineSavePacket?.addedClipIds ?? []);
		const persistedSavingDraft = draftForPersistence(savingDraft, {
			timelineDirtyFieldsByClipId: savingTimelineDirtyFields,
			addedTimelineClipIds: savingAddedTimelineClipIds,
			deletedTimelineClipIdentities: savingDeletedTimelineClipIdentities
		});
		const savingConflictMergeBase = draftConflictMergeBase
			? snapshotDraftForConflictMerge(draftConflictMergeBase)
			: snapshotDraftForConflictMerge(persistedSavingDraft);
		let savedDraft: VideoLocalizationDraft | null;
		try {
			savedDraft = await projectDraftSessionController.mutate(
				savingProjectId,
				async () => {
					try {
						return await Api.saveVideoLocalizationWorkspace(savingProjectId, persistedSavingDraft);
					} catch (e) {
						if (!(e instanceof ApiError) || e.code !== 'VIDEO_LOCALIZATION_DRAFT_CONFLICT') throw e;
						const latestWorkspace = await Api.videoLocalizationWorkspace(savingProjectId);
						applyWorkspaceCueTimingConfirmations(latestWorkspace);
						const latest = withLoadedWorkspaceDetails(latestWorkspace.draft);
						if (shouldKeepServerResetDraft(latest, persistedSavingDraft)) return latest;
						const mergedAfterConflict = mergeDraftAfterConflict(latest, persistedSavingDraft, {
							baseDraft: savingConflictMergeBase,
							deletedTimelineClipIds: savingDeletedTimelineClipIds,
							timelineDirtyFieldsByClipId: savingTimelineDirtyFields
						});
						return Api.saveVideoLocalizationWorkspace(
							savingProjectId,
							draftForPersistence(mergedAfterConflict, {
								timelineDirtyFieldsByClipId: savingTimelineDirtyFields,
								addedTimelineClipIds: savingAddedTimelineClipIds,
								deletedTimelineClipIdentities: savingDeletedTimelineClipIdentities
							})
						);
					}
				}
			);
		} catch (saveError) {
			if (timelineSavePacket) savingTimelineController?.rejectSave(timelineSavePacket.packetId);
			throw saveError;
		}
		// A newer mutation invalidated this response.  The server write may have
		// succeeded, but applying its older snapshot here would hide newer clips.
		// Keep the edit packet pending and let the next refresh/save reconcile it.
		if (!savedDraft) {
			if (timelineSavePacket) savingTimelineController?.rejectSave(timelineSavePacket.packetId);
			if (
				projectId === savingProjectId
				&& savingTimelineController?.hasPendingChanges
			) scheduleDraftAutosave();
			return;
		}
		if (projectId === savingProjectId) {
			const currentDraft = draft;
			const workspaceSavedDraft = withEditableMediaClips({
				...savedDraft,
				tts_tasks: currentDraft?.tts_tasks ?? savedDraft.tts_tasks ?? []
			});
			draft = !currentDraft
				? workspaceSavedDraft
				: currentDraft === savingDraft
					? workspaceSavedDraft
					: mergeDraftAfterConflict(workspaceSavedDraft, currentDraft, {
							baseDraft: savingDraft,
							deletedTimelineClipIds: timelineDeletedClipIds,
							timelineDirtyFieldsByClipId
						});
			draftConflictMergeBase = snapshotDraftForConflictMerge(workspaceSavedDraft);
			clearPersistedTimelineDirtyFields(
				timelineDirtyFieldsByClipId,
				savingTimelineDirtyFields,
				workspaceSavedDraft,
				draft
			);
			if (
				timelineSavePacket
				&& savingTimelineController
				&& timelineEditController === savingTimelineController
				&& draft
			) {
				savingTimelineController.acknowledgeSave(timelineSavePacket.packetId, workspaceSavedDraft);
				const controlledDraft = withPendingDraftUiState(savingTimelineController.draft);
				draft = {
					...draft,
					cues: controlledDraft.cues,
					localized_subtitles: controlledDraft.localized_subtitles,
					timeline_clips: controlledDraft.timeline_clips,
					ui_state: {
						...draft.ui_state,
						dub_lane_states: controlledDraft.ui_state?.dub_lane_states,
						disabled_media_tracks: controlledDraft.ui_state?.disabled_media_tracks,
						discarded_tts_task_ids: controlledDraft.ui_state?.discarded_tts_task_ids
					}
				};
				for (const clipId of timelineControllerDeletedClipIds) timelineDeletedClipIds.delete(clipId);
				timelineControllerDeletedClipIds = new Set(savingTimelineController.deletedTimelineClipIds);
				for (const clipId of timelineControllerDeletedClipIds) timelineDeletedClipIds.add(clipId);
			}
			if (draft) {
				draft = withPendingDraftUiState(draft);
				timelineEditController?.synchronizeExternalDraft(draft);
				timelineRuntimeClips = reconcileTimelineRuntimeClips(draft, timelineRuntimeClips);
			}
		}
		if (
			timelineEditRevision === savingTimelineRevision &&
			timelineDirtyFieldsByClipId.size === 0
		) {
			timelineSavedRevision = savingTimelineRevision;
			timelineDeletedClipIds = new Set();
		}
		draftOnlyCueIds = [];
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
		scheduleDraftAutosave('draft');
		if (!(await flushPendingAutosave())) throw new Error(error || '自动保存失败');
		draftOnlyCueIds = [];
	}

	function handleOperationVisibilityChange() {
		if (document.visibilityState === 'hidden') {
			operationFeedController.setVisible(false);
			ttsWorkflowSessionController.setVisible(false);
			if (hasPendingSaveWork()) void flushPendingAutosave();
			return;
		}
		operationFeedController.setVisible(true);
		ttsWorkflowSessionController.setVisible(true);
		refreshDubbingWorkspaceOnFocus();
	}

	function refreshDubbingWorkspaceOnFocus() {
		if (!projectId || previewPlaying || document.visibilityState === 'hidden' || Date.now() - lastDubbingWorkspaceSyncAt < 1000 || dubbingWorkspaceSyncPromise) return;
		lastDubbingWorkspaceSyncAt = Date.now();
		dubbingWorkspaceSyncPromise = (async () => {
			const refreshingProjectId = projectId;
			const current = await Api.videoLocalizationWorkspaceRevision(refreshingProjectId);
			if (projectId !== refreshingProjectId) return;
			workspaceRevisionController.observe(refreshingProjectId, current.revision);
			if (workspaceRevisionController.needsRefresh(refreshingProjectId, current.revision, 'workspace')) {
				await refreshDraftOnly();
			}
			await Promise.all([
				loadOperations(refreshingProjectId),
				loadTtsHistory(),
				refreshTtsWorkflowTasks(refreshingProjectId, 'focus')
			]);
		})()
			.catch(() => undefined)
			.finally(() => (dubbingWorkspaceSyncPromise = null));
	}

	function refreshDubbingDraftLive() {
		if (
			!projectId
			|| previewPlaying
			|| document.visibilityState === 'hidden'
			|| dubbingWorkspaceSyncPromise
		) return;
		const refreshingProjectId = projectId;
		lastDubbingWorkspaceSyncAt = Date.now();
		dubbingWorkspaceSyncPromise = (async () => {
			const current = await Api.videoLocalizationWorkspaceRevision(
				refreshingProjectId
			);
			if (projectId !== refreshingProjectId) return;
			workspaceRevisionController.observe(refreshingProjectId, current.revision);
			if (!workspaceRevisionController.needsRefresh(refreshingProjectId, current.revision, 'timeline')) return;
			await Promise.all([
				refreshTimelineProjectionOnly(),
				refreshTtsWorkflowTasks(refreshingProjectId, 'poll')
			]);
		})()
			.catch(() => undefined)
			.finally(() => (dubbingWorkspaceSyncPromise = null));
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
							<button type="button" aria-label="保存项目名称" aria-busy={projectNameSaving} data-tooltip="保存名称：同步修改项目名称和对应的项目目录名称。" onclick={saveProjectNameEdit} disabled={projectNameSaving || !projectNameDraft.trim()}>{#if projectNameSaving}<CommandSpinner size={13} />{:else}<Check size={13} />{/if}</button>
							<button type="button" aria-label="取消修改项目名称" data-tooltip="取消修改：保留当前项目名称不变。" onclick={cancelProjectNameEdit} disabled={projectNameSaving}><X size={13} /></button>
						</div>
					{:else}
						<div class="project-name-display">
							<h1>{selectedProject?.name || draft?.source_media.filename || '未命名本土化项目'}</h1>
							<button type="button" aria-label="修改项目名称" data-tooltip="修改名称：项目目录会随新名称同步调整。" onclick={startProjectNameEdit} disabled={!selectedProject || importing}><Pencil size={12} /></button>
							<button class="auto-name-button" type="button" aria-label="自动命名项目" aria-busy={projectAutoNaming} data-tooltip="自动命名：根据字幕内容和已查资料生成项目名。" onclick={autoNameProject} disabled={!selectedProject || importing || projectNameSaving || projectAutoNaming}>
								{#if projectAutoNaming}<CommandSpinner size={12} />{:else}<Sparkles size={12} />{/if}
							</button>
							<div class="project-switcher">
								<button class="project-history-toggle" class:active={projectMenuOpen} type="button" aria-label="切换历史项目" aria-busy={projectMenuSyncing} aria-expanded={projectMenuOpen} data-tooltip="切换项目：同步本地项目目录并打开已有的视频本土化项目。" onclick={toggleProjectMenu} disabled={loading || projectMenuSyncing}>{#if projectMenuSyncing}<CommandSpinner size={13} />{:else}<ChevronDown size={13} />{/if}</button>
								{#if projectMenuOpen}
									<div class="project-menu" role="menu" aria-label="历史项目">
										<div class="project-menu-head"><strong>历史项目</strong><span>{deletingProjectId ? `删除中${queuedProjectDeleteIds.length ? ` · 排队 ${queuedProjectDeleteIds.length}` : ''}` : projectMenuSyncing ? '同步中' : projects.length}</span></div>
										<div class="project-menu-list menu-scroll-region">
											{#if !projectMenuSyncing && !projects.length}
												<div class="project-menu-empty">本地没有可用项目</div>
											{/if}
											{#each projects as project}
												<div class="project-menu-item" class:active={project.project_id === projectId} role="none">
													<button class="project-menu-project" type="button" role="menuitem" onclick={() => selectProject(project.project_id)} disabled={pendingProjectDeleteIds.has(project.project_id)}>
														<span class="project-menu-name" use:projectNameMarquee><span>{project.name}</span></span>
														<span class="project-menu-meta">
															<small>{project.description || '视频本土化项目'}</small>
															<time
																datetime={project.updated_at}
																title={`最后更新时间 ${project.updated_at}`}
																aria-label={`最后更新时间 ${formatProjectUpdatedAt(project.updated_at)}`}
															>{formatProjectUpdatedAt(project.updated_at)}</time>
														</span>
														{#if project.project_id === projectId}<Check size={13} />{/if}
													</button>
									<button
										class="project-menu-delete"
										class:queued={queuedProjectDeleteIds.includes(project.project_id)}
										type="button"
										role="menuitem"
										aria-label={`删除项目 ${project.name}`}
										aria-busy={deletingProjectId === project.project_id}
										aria-disabled={pendingProjectDeleteIds.has(project.project_id)}
										data-tooltip={deletingProjectId === project.project_id
											? '正在从项目列表删除，相关文件随后在后台清理'
											: queuedProjectDeleteIds.includes(project.project_id)
												? '已加入删除队列'
												: '彻底删除项目及相关文件'}
										onclick={(event) => deleteHistoryProject(project, event)}
									>
										{#if deletingProjectId === project.project_id}<CommandSpinner size={12} />{:else if queuedProjectDeleteIds.includes(project.project_id)}<ListTodo size={12} />{:else}<Trash2 size={12} />{/if}
									</button>
												</div>
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
			<input bind:this={localizationSrtInput} data-video-localization-srt-file class="visually-hidden" type="file" accept=".srt,application/x-subrip,text/plain" disabled={importingLocalizedSrt} onchange={(event) => importLocalizationSrtFile(event.currentTarget.files?.[0])} />
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
				<button class="icon-action" class:active={deliveryMenuOpen} type="button" disabled={!draft || exportingDelivery} aria-label="导出" aria-busy={exportingDelivery} aria-haspopup="dialog" aria-expanded={deliveryMenuOpen} data-tooltip="导出视频、音频或字幕。" onclick={toggleDeliveryMenu}>
					{#if exportingDelivery}<CommandSpinner size={15} />{:else}<Download size={15} />{/if}
				</button>
				{#if deliveryMenuOpen && mediaExportRequest && mediaExportAvailability}
					{#await import('./ExportDialog.svelte')}
						<span class="visually-hidden">正在加载导出设置</span>
					{:then { default: ExportDialog }}
						<ExportDialog
							bind:request={mediaExportRequest}
							availability={mediaExportAvailability}
							exporting={exportingDelivery}
							selectingDestination={selectingExportDestination}
							destinationPath={exportDestination?.display_path ?? ''}
							progress={exportProgress}
							stage={exportStage}
							{exportedFilename}
							outputFilename={exportOutputFilename}
							errorMessage={exportError}
							onClose={closeDeliveryDialog}
							onChooseDestination={() => void chooseExportDestination()}
							onRequestChange={updateExportRequest}
							onOutputFilenameChange={updateExportOutputFilename}
							onExport={() => void exportSelectedMedia()}
						/>
					{/await}
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
				aria-busy={openingProjectDirectory}
				data-tooltip="在 Finder 中打开当前项目保存目录。"
			>
				{#if openingProjectDirectory}<CommandSpinner size={15} />{:else}<FolderOpen size={15} />{/if}
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

	<section class="cutting-shell" class:collapsed={inspectorCollapsed} style={`--inspector-width:${inspectorWidth}px`} use:fitStackedInspectorToViewport>
		<section class="cutting-stage">
			<PreviewPanel
				subtitleFrame={subtitleDisplayFrame}
				draft={timelineViewDraft}
				{mediaHealth}
				{projectId}
				{importing}
				{trackStates}
				{dubLaneStates}
				{previewCache}
				playbackLoopRange={activePlaybackLoopRange}
				onRequestImport={() => videoInput?.click()}
				onImportFile={importVideoFile}
				onVideoTimeUpdate={updatePreviewTime}
				onPlaybackStateChange={updatePreviewPlaying}
					onPlaybackPreparingChange={(preparing) => playbackSessionController.updatePreparing(preparing)}
					onPlaybackReadinessChange={(status) => (playbackReadiness = status)}
					onPlaybackIssue={(issue) => {
						error = issue;
						setTimeout(() => { if (error === issue) error = ''; }, 5000);
					}}
				onRequestPreviewCache={requestPreviewCacheAt}
				onPrepareEditingProxy={previewMediaClient.prepareEditingProxy}
				onPrepareAudioPreviewProxies={previewMediaClient.prepareAudioPreviewProxies}
				onControllerReady={registerPreviewPlaybackController}
			/>
			{#if subtitleWorkflowSettingsOpen}
				{#await import('./SubtitleWorkflowSettings.svelte')}
					<div class="dialog-loading">正在打开字幕设置…</div>
				{:then { default: SubtitleWorkflowSettings }}
					<SubtitleWorkflowSettings
						open
						glossary={draft?.glossary ?? []}
						sceneContext={draft?.scene_context ?? ''}
						onChange={updateSubtitleWorkflowSettings}
					/>
				{/await}
			{/if}
			<VideoCuttingTimeline
				{projectId}
				{confirmedTimingCueIds}
				draft={timelineViewDraft}
				{mediaHealth}
				{selectedCueId}
				currentTimeMs={previewTimeMs}
				isPlaying={previewPlaying}
				playbackPreparing={previewPlaybackPreparing}
				{latestOperation}
				extractingAudio={extractingAudio || operationBusy('source_audio')}
				separatingStems={separatingStems || operationBusy('stems')}
				noticeKind={error ? 'error' : message ? 'success' : 'idle'}
				noticeSummary={noticeText}
				noticeDetail={error}
				{activityTasks}
				onOpenTaskCenter={openTaskCenter}
				asrBusy={transcribingAsr || operationBusy('english_asr')}
				{trackStates}
				{dubLaneStates}
				{audioTrackOrder}
				{timelineZoom}
				{subtitleDisplay}
				{dubSubtitleDisplayEnabled}
				dubSubtitleDisplayAvailable={Boolean(draft?.dub_subtitles?.length)}
				onToggleDubSubtitleDisplay={toggleDubSubtitleDisplay}
				onSelectCue={selectCue}
				onSelectAudioClip={selectTimelineAudioClip}
				{timelineSelectionItems}
				{ttsSelectionSession}
				{ttsCoverageByIdentity}
				onTimelineSelectionChange={updateTimelineSelection}
				onTtsSelectionAnchorChange={updateTtsSelectionAnchor}
				onClearCueSelection={clearCueSelection}
				onExtractAudio={extractSourceAudio}
				onRestoreOriginalAudio={restoreOriginalAudio}
				onSeparateStems={separateStems}
				onImportLocalizedSrt={() => localizationSrtInput?.click()}
				{importingLocalizedSrt}
				onGenerateLocalization={generateLocalizationFromTimeline}
				onGenerateDubSubtitles={(mode) => {
					requestedDubSubtitleFullRegeneration = mode === 'full';
					return generateDubSubtitlesFromTimeline();
				}}
				canGenerateDubSubtitles={dubSubtitleGenerationSource.hasContent || hasDirtyDubSubtitleScope}
				dubSubtitleUnavailableReason={hasDirtyDubSubtitleScope ? '' : dubSubtitleGenerationSource.unavailableReason}
				dubSubtitleGenerationBusy={operationBusy('dub_subtitle_generation')}
				{semanticGroupingBusy}
				{semanticTtsGroups}
				onGenerateSemanticTtsGroups={generateSemanticTtsGroups}
				onSelectSemanticTtsGroup={selectSemanticTtsGroup}
				onTransportAction={handleTimelineTransport}
				onTrackStateChange={updateTrackState}
				onDubLaneStateChange={updateDubLaneState}
				onDubLaneOrderChange={reorderDubLanes}
				onAudioTrackOrderChange={updateAudioTrackOrder}
				onTimelineZoomChange={updateTimelineZoom}
				{timelineViewportStartMs}
				timelineViewportRestoreReady={draftProjectId === projectId}
				onTimelineViewportChange={updateTimelineViewportStart}
				onToggleSubtitleSource={toggleSubtitleSource}
				onSeekTimeline={seekTimeline}
				onSelectionRangeChange={updateTimelineSelectionRange}
				onSelectionRangeCommit={playCommittedTimelineSelection}
				onUpdateCueTime={updateCueTimeFromTimeline}
				onUpdateDubSubtitleTime={(subtitleId, startMs, endMs) => updateSelectedDubSubtitle(subtitleId, { start_ms: startMs, end_ms: endMs })}
				onUpdateLocalizedSubtitleTime={updateLocalizedSubtitleTime}
				onClearSubtitleTrack={clearSubtitleTrack}
				onDeleteSubtitleItem={deleteSubtitleItem}
				onFillSubtitleGaps={fillSubtitleGaps}
				onGenerateAsr={generateAsrFromTimeline}
				onSelectLocalizedSubtitle={selectLocalizedSubtitle}
				onSelectSubtitleDisplayCue={selectSubtitleDisplayCue}
				localizationBusy={localizationRuntimeBusy}
				onSplitCue={splitSelectedCue}
				onSplitLocalizedSubtitle={splitLocalizedSubtitleFromTimeline}
				onSplitTimelineClip={splitTimelineClipFromTimeline}
				onMergeTimelineSubtitles={mergeSelectedTimelineSubtitles}
				onGenerateToSelection={focusGenerateToSelection}
				onUpdateTimelineClip={updateTimelineClipFromTimeline}
				onMoveTimelineItems={moveTimelineItemsFromTimeline}
				onDeleteTimelineClip={deleteTimelineClip}
				onDeleteTimelineItems={deleteTimelineItems}
				hoverScrubEnabled={hoverScrubEnabled}
				{previewCache}
				{playbackReadiness}
				previewCacheRefreshing={previewCacheRefreshing || previewCache?.state === 'building'}
				onRefreshPreviewCache={refreshPreviewCache}
				onHoverScrubChange={updateHoverScrubEnabled}
				onHoverScrub={hoverScrubPreview}
				onHoverScrubEnd={endHoverScrubPreview}
				onUndoTimelineClip={undoTimelineClipEdit}
				onRedoTimelineClip={redoTimelineClipEdit}
				canUndoTimeline={timelineUndoCount > 0}
				canRedoTimeline={timelineRedoCount > 0}
					undoTimelineCount={timelineUndoCount}
					redoTimelineCount={timelineRedoCount}
					{draggingTtsHistory}
					onDropTtsHistory={dropSubtitleHistoryOnTimeline}
					onEndTtsHistoryDrag={endSubtitleHistoryDrag}
				/>
		</section>

		{#if !inspectorCollapsed}
			<button class="inspector-resize-handle" type="button" aria-label="调整侧边栏宽度" data-tooltip="调整侧边栏宽度｜左右拖动分隔线。" onpointerdown={beginInspectorWidthResize}></button>
			{#await import('./CuttingInspector.svelte')}
				<aside class="inspector-loading" aria-label="正在打开右侧检查器">正在打开检查器…</aside>
			{:then { default: CuttingInspector }}
			<CuttingInspector
				{draft}
				{mediaHealth}
				{projectId}
				{timingConfirmationCurrentByCueId}
				{selectedCue}
				{selectedLocalizedSubtitle}
				ttsPrimaryLocalizedSubtitle={ttsPrimaryLocalizedSubtitle}
				{selectedLocalizedSubtitles}
				selectedLocalizedSubtitlesContiguous={selectedLocalizedSubtitlesContiguous}
				{ttsSourceSelectionAdvisoryMessage}
				selectedTimelineAudioClip={selectedTimelineAudioClip}
				{inspectorSection}
				bind:dubbingHistoryScope
				{subtitleDisplay}
				onSectionChange={(section) => focusInspector(section)}
				onUpdateCue={updateSelectedCue}
				onPreviewLocalizedSubtitle={previewSelectedLocalizedSubtitle}
				onUpdateLocalizedSubtitle={updateSelectedLocalizedSubtitle}
				onUpdateDubSubtitle={updateSelectedDubSubtitle}
				{dubSubtitleReviewBusy}
				onConfirmCueTiming={confirmSelectedCueTiming}
				onUpdateSubtitleDisplaySettings={updateSubtitleDisplaySettings}
				{confirmingCueTiming}
				generatingVoice={preparingTtsHandoff}
				{taskHistory}
				{operationHistoryTotal}
				{operationHistoryLoaded}
				{operationHistoryHasMore}
					{operationHistoryLoading}
					onLoadMoreOperationHistory={() => operationFeedController.loadMoreHistory()}
					{ttsHistory}
					{ttsHistoryTotal}
					{ttsHistoryLoaded}
					{ttsHistoryHasMore}
					{ttsHistoryLoading}
					onLoadMoreTtsHistory={() => ttsHistoryController.loadMore()}
					onOpenSubtitleGenerate={openSelectedSubtitleInGenerate}
					onReuseSubtitleHistory={reuseSubtitleHistory}
				onApplySubtitleHistory={applySubtitleHistoryToTimeline}
				onDeleteSubtitleHistory={deleteSubtitleHistory}
					onDeleteCurrentSubtitleHistory={deleteCurrentSubtitleHistory}
					onDeleteAllSubtitleHistory={deleteAllSubtitleHistory}
					onCleanupUnusedSubtitleHistory={cleanupUnusedSubtitleHistory}
					onHistoryDragStart={beginSubtitleHistoryDrag}
					historyApplyingResultId={historyApplyingResultId}
					onCancelTask={cancelActivityTask}
					onDeleteTask={deleteActivityTask}
					onRetryTask={retryActivityTask}
					onLoadTaskDetail={loadActivityTaskDetail}
					onSeekTimeline={seekTimeline}
				onPlayTimelineRange={playCommittedTimelineSelection}
					{subtitleRuntimeBusy}
					{localizationRuntimeBusy}
					{taskCenterPulseKey}
				/>
			{/await}
		{:else}
			<aside class="inspector-rail">
				<button class="rail-tab" type="button" onclick={openTaskCenter} aria-label="任务" data-tooltip="任务｜查看后台处理进度、子步骤和历史结果。"><ListTodo size={15} /></button>
				<button class="rail-tab" type="button" onclick={() => focusInspector('subtitle')} aria-label="字幕" data-tooltip="字幕｜展开当前字幕片段编辑面板。"><Captions size={15} /></button>
				<button class="rail-tab" type="button" onclick={() => focusInspector('dubbing')} aria-label="配音" data-tooltip="配音｜展开音色、生成参数和配音结果。"><AudioLines size={15} /></button>
			</aside>
		{/if}
	</section>
</main>

{#if asrSetupOpen}
	<div
		class="asr-setup-backdrop"
		role="presentation"
		onclick={(event) => {
			if (event.currentTarget === event.target) asrSetupOpen = false;
		}}
	>
		<div
			class="asr-setup-dialog"
			role="dialog"
			aria-modal="true"
			aria-labelledby="asr-setup-title"
		>
			<header>
				<div>
					<strong id="asr-setup-title">选择听写方式</strong>
					<p>默认按真实音频自动判断；只有多个稳定声音角色才会给字幕加匿名标签。</p>
				</div>
				<button type="button" aria-label="关闭听写方式选择" onclick={() => asrSetupOpen = false}>
					<X size={16} />
				</button>
			</header>

			<div class="asr-setup-options">
				<button
					class="asr-setup-option recommended"
					type="button"
					disabled={speakerGroupingHealthLoading}
					onclick={() => startSourceAsr(speakerGroupingAvailable)}
				>
					<span class="asr-setup-option-title">自动判断说话人 <em>默认</em></span>
					<span>并行分析真实声音；单一角色不加标签，多个角色才保留匿名分组。模型不可用时自动退回纯字幕。</span>
				</button>

				<button
					class="asr-setup-option"
					type="button"
					onclick={() => startSourceAsr(false)}
				>
					<span class="asr-setup-option-title">跳过说话人分析</span>
					<span>只生成字幕，速度更快；适合确定不需要按角色配音的内容。</span>
				</button>
			</div>

			<p class:available={speakerGroupingAvailable} class="asr-speaker-status">
				{speakerGroupingStatus}
			</p>
		</div>
	</div>
{/if}

<style>
	.video-localization-page {
		max-width: none;
		padding: 14px 14px 48px;
		background: #0f1114;
	}

	.asr-setup-backdrop {
		position: fixed;
		inset: 0;
		z-index: 120;
		display: grid;
		place-items: center;
		padding: 20px;
		background: rgba(4, 7, 9, 0.72);
		backdrop-filter: blur(5px);
	}

	.asr-setup-dialog {
		width: min(620px, 100%);
		border: 1px solid #40515a;
		border-radius: 12px;
		padding: 16px;
		background: #171c20;
		box-shadow: 0 24px 70px rgba(0, 0, 0, 0.48);
	}

	.asr-setup-dialog header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 16px;
	}

	.asr-setup-dialog header strong {
		font-size: 17px;
		color: var(--text);
	}

	.asr-setup-dialog header p,
	.asr-speaker-status {
		margin: 6px 0 0;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.55;
	}

	.asr-setup-dialog header button {
		display: grid;
		place-items: center;
		width: 30px;
		height: 30px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #20272c;
		color: var(--muted);
		cursor: pointer;
	}

	.asr-setup-options {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 10px;
		margin-top: 15px;
	}

	.asr-setup-option {
		display: grid;
		align-content: start;
		gap: 8px;
		min-height: 132px;
		padding: 13px;
		border: 1px solid #394851;
		border-radius: 9px;
		background: #1d2429;
		color: var(--muted);
		text-align: left;
		line-height: 1.48;
		cursor: pointer;
	}

	.asr-setup-option.recommended,
	.asr-setup-option:hover:not(:disabled),
	.asr-setup-option:focus-visible {
		border-color: rgba(87, 208, 200, 0.72);
		background: #203034;
		outline: none;
	}

	.asr-setup-option:disabled {
		opacity: 0.48;
		cursor: not-allowed;
	}

	.asr-setup-option-title {
		color: var(--text);
		font-size: 13px;
		font-weight: 800;
	}

	.asr-setup-option-title em {
		margin-left: 5px;
		border-radius: 999px;
		padding: 2px 6px;
		background: rgba(87, 208, 200, 0.13);
		color: var(--studio-accent);
		font-size: 10px;
		font-style: normal;
	}

	.asr-speaker-status.available {
		color: #9edbd6;
	}

	@media (max-width: 640px) {
		.asr-setup-options {
			grid-template-columns: 1fr;
		}
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
		overflow-x: hidden;
		overflow-y: auto;
		overscroll-behavior: contain;
		padding: 4px;
	}

	.project-menu-empty {
		padding: 18px 10px;
		color: #7f8d96;
		font-size: 10px;
		text-align: center;
	}

	.project-menu-item {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 28px;
		align-items: center;
		gap: 2px;
		border-radius: 5px;
	}

	.project-menu-item:hover,
	.project-menu-item.active {
		background: #1d282d;
		color: #ddfffb;
	}

	.project-menu-list .project-menu-project {
		width: 100%;
		height: auto;
		min-height: 42px;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 3px 8px;
		border: 0;
		border-radius: 5px;
		padding: 4px 8px;
		background: transparent;
		text-align: left;
	}

	.project-menu-project > span,
	.project-menu-meta > small,
	.project-menu-meta > time {
		min-width: 0;
		overflow: hidden;
		white-space: nowrap;
	}

	.project-menu-meta > small,
	.project-menu-meta > time {
		text-overflow: ellipsis;
		text-align: left;
	}

	.project-menu-project > .project-menu-name {
		width: 100%;
		text-align: left;
		scrollbar-width: none;
	}

	.project-menu-name > span {
		display: inline-block;
		min-width: 100%;
		width: max-content;
		text-align: left;
		font-size: 11px;
		font-weight: 750;
	}

	.project-menu-meta {
		grid-column: 1;
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 10px;
		width: 100%;
		min-width: 0;
		color: #7f8d96;
	}

	.project-menu-meta > small,
	.project-menu-meta > time {
		grid-column: 1;
		color: #7f8d96;
		font-size: 9px;
	}

	.project-menu-meta > small {
		flex: 1 1 auto;
	}

	.project-menu-meta > time {
		flex: 0 0 auto;
		color: #95a7b1;
		font-variant-numeric: tabular-nums;
	}

	.project-menu-project > :global(svg) {
		grid-column: 2;
		grid-row: 1 / span 2;
		color: #6fd9d1;
	}

	.project-menu-list .project-menu-delete {
		width: 26px;
		height: 26px;
		padding: 0;
		border: 0;
		border-radius: 4px;
		background: transparent;
		color: #78868e;
	}

	.project-menu-list .project-menu-delete:hover:not([aria-disabled='true']) {
		background: rgba(190, 75, 75, 0.14);
		color: #e69a9a;
	}

	.project-menu-list .project-menu-delete[aria-disabled='true'] {
		cursor: progress;
	}

	.project-menu-list .project-menu-delete.queued {
		color: var(--studio-warn);
		opacity: 0.9;
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

	.video-localization-page :global(button[aria-busy='true']) {
		opacity: 0.88;
		cursor: progress;
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

	.inspector-loading {
		display: grid;
		place-items: center;
		min-width: 0;
		border-left: 1px solid var(--line);
		background: #171a1d;
		color: var(--muted);
		font-size: 12px;
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

	@media (max-width: 1380px) {
		.video-localization-page {
			padding-bottom: 14px;
		}

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

	@media (max-width: 720px) {
		.cutting-head {
			grid-template-columns: 1fr;
		}

		.inspector-rail {
			display: none;
		}
	}

</style>
