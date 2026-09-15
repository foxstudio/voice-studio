import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { VideoLocalizationCue, VideoLocalizationDraft, VideoLocalizationQualityIssue } from '$lib/api/types';
import {
	draftForPersistence,
	mergeDraftAfterConflict,
	mergeTimelineClipAfterConflict,
	shouldKeepServerResetDraft,
	type TimelineDirtyField
} from './draft-ownership';
import {
	DEFAULT_ASR_ENGINE_ID,
	buildSourceAsrOperationParameters,
	asrSelectionRequiresUploadConfirmation,
	discardedTtsTaskIdsAfterClipRemoval,
	errorAfterRecoveredSync,
	formatProjectUpdatedAt,
	inspectorSectionForTimelineSelection,
	inspectorSectionOnProjectLoad,
	isOrphanedDubPlaceholder,
	isDubbingInspectorSection,
	preserveDubClipsAfterLocalizedSubtitleMerge,
	protectCueManualEdit,
	qualityIssueAppliesToStage,
	resolveInitialProjectId
} from './+page.svelte';
import { sortProjectSummariesByUpdatedAt } from './project-catalog-controller';
import { withTtsWorkflowPlaceholders } from './tts-workflow-placeholders';

const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
const exportDialogSource = readFileSync(new URL('./ExportDialog.svelte', import.meta.url), 'utf8');
const cuttingInspectorSource = readFileSync(new URL('./CuttingInspector.svelte', import.meta.url), 'utf8');
const dubbingInspectorPanelSource = readFileSync(new URL('./DubbingInspectorPanel.svelte', import.meta.url), 'utf8');
const subtitleTtsHistorySource = readFileSync(new URL('./SubtitleTtsHistory.svelte', import.meta.url), 'utf8');
const previewPanelSource = readFileSync(new URL('./PreviewPanel.svelte', import.meta.url), 'utf8');
const taskProgressPanelSource = readFileSync(new URL('./TaskProgressPanel.svelte', import.meta.url), 'utf8');
const videoCuttingTimelineSource = readFileSync(new URL('./VideoCuttingTimeline.svelte', import.meta.url), 'utf8');
const playbackSessionControllerSource = readFileSync(new URL('./playback-session-controller.ts', import.meta.url), 'utf8');
const previewCacheSessionControllerSource = readFileSync(new URL('./preview-cache-session-controller.ts', import.meta.url), 'utf8');
const operationFeedControllerSource = readFileSync(new URL('./operation-feed-controller.ts', import.meta.url), 'utf8');
const projectCatalogControllerSource = readFileSync(new URL('./project-catalog-controller.ts', import.meta.url), 'utf8');
const ttsHistoryControllerSource = readFileSync(new URL('./tts-history-controller.ts', import.meta.url), 'utf8');
const projectSessionControllerSource = readFileSync(new URL('./project-session-controller.ts', import.meta.url), 'utf8');
const timelineEditControllerSource = readFileSync(new URL('./timeline-edit-controller.ts', import.meta.url), 'utf8');

function dirtyFields(entries: Array<[string, TimelineDirtyField[]]>) {
	return new Map(entries.map(([clipId, fields]) => [clipId, new Set(fields)]));
}

function issue(code: string): VideoLocalizationQualityIssue {
	return { code, message: code, severity: 'blocker', cue_id: null, speaker_id: null, reference_clip_id: null };
}

function cue(overrides: Partial<VideoLocalizationCue> = {}): VideoLocalizationCue {
	return {
		cue_id: 'cue_1',
		speaker_id: null,
		start_ms: 1000,
		end_ms: 2400,
		audio_route: 'manual_review',
		en_subtitle_text: 'Original text',
		zh_localized_subtitle_text: null,
		tts_recommended_text: null,
		reference_clip_id: null,
		tts_result_id: null,
		tts_audio_path: null,
		tts_batch_task_id: null,
		tts_batch_status: null,
		tts_batch_error: null,
		tts_attempted_at: null,
		source_duration_ms: 1400,
		generated_duration_ms: null,
		source_word_ids: ['word_1', 'word_2'],
		source_text_raw: 'Original text',
		timing_confidence: 'high',
		transcription_revision_id: 'revision_1',
		review_status: 'needs_review',
		quality_flags: ['generated_by_asr'],
		notes: null,
		...overrides
	};
}

describe('localized subtitle merge timeline ownership', () => {
	it('keeps arranged dub clips and marks their remapped target binding stale', () => {
		const clips = [
			{
				clip_id: 'clip-1', track_id: 'dub', subtitle_id: 'localized-2',
				target_subtitle_ids: ['localized-2'], start_ms: 32_292, end_ms: 35_417,
				source_start_ms: 1_083, source_end_ms: 4_208, dub_lane: 2,
				audio_path: '/managed/clip-1.wav', result_id: 'result-1', status: 'ready'
			},
			{ clip_id: 'background', track_id: 'background', start_ms: 0, end_ms: 60_000 }
		] as any[];
		const subtitles = [
			{ subtitle_id: 'localized-1', text: '第一句', tts_text: '第一句口播' },
			{ subtitle_id: 'localized-2', text: '第二句', tts_text: '第二句口播' }
		] as any[];

		const preserved = preserveDubClipsAfterLocalizedSubtitleMerge(
			clips,
			subtitles,
			'localized-1'
		);

		expect(preserved).toHaveLength(2);
		expect(preserved[0]).toMatchObject({
			clip_id: 'clip-1', subtitle_id: 'localized-1',
			target_subtitle_ids: ['localized-1'], start_ms: 32_292, end_ms: 35_417,
			source_start_ms: 1_083, source_end_ms: 4_208, dub_lane: 2,
			audio_path: '/managed/clip-1.wav', result_id: 'result-1',
			tts_target_binding_status: 'stale', tts_target_text: '第二句口播'
		});
		expect(preserved[1]).toBe(clips[1]);
	});
});

describe('video localization ASR engine policy', () => {
	it('uses the compact timeline command before the full-draft autosave fallback', () => {
		const autosaveSource = pageSource.slice(
			pageSource.indexOf('async function saveProjectSessionAutosave('),
			pageSource.indexOf('function cueNeedsDraftSave(')
		);
		expect(autosaveSource).toContain('prepareCompactSave()');
		expect(autosaveSource).toContain('Api.updateVideoLocalizationTimelineEdit(');
		expect(autosaveSource.indexOf('Api.updateVideoLocalizationTimelineEdit('))
			.toBeLessThan(autosaveSource.indexOf('Api.saveVideoLocalizationWorkspace('));
	});

	it('distributes the remaining inspector tabs without a retired marker slot', () => {
		expect(cuttingInspectorSource).toContain('grid-template-columns: repeat(3, 1fr)');
		expect(cuttingInspectorSource).not.toContain('grid-template-columns: repeat(4, 1fr)');
		expect(cuttingInspectorSource).not.toContain('.inspector.marker-view');
	});

	it('defaults to local Qwen3 ASR', () => {
		expect(DEFAULT_ASR_ENGINE_ID).toBe('qwen3-asr-mlx');
	});

	it('requires upload confirmation only for MiMo cloud ASR', () => {
		expect(asrSelectionRequiresUploadConfirmation('qwen3-asr-mlx')).toBe(false);
		expect(asrSelectionRequiresUploadConfirmation('faster-whisper-turbo')).toBe(false);
		expect(asrSelectionRequiresUploadConfirmation('mimo-v2.5-asr')).toBe(true);
	});

	it('runs speaker analysis automatically and still allows an explicit fast path', () => {
		expect(buildSourceAsrOperationParameters('vocals', 'auto', true)).toEqual({
			engine_id: 'auto',
			source_track_id: 'vocals',
			source_language: 'auto',
			diarization_engine_id: 'moss-transcribe-diarize-mlx'
		});
		expect(buildSourceAsrOperationParameters('vocals', 'en', false)).toEqual({
			engine_id: 'auto',
			source_track_id: 'vocals',
			source_language: 'en',
			diarization_engine_id: null
		});
		expect(pageSource).toContain('自动判断说话人');
		expect(pageSource).toContain('跳过说话人分析');
	});

	it('describes regeneration as a complete replacement of the current ASR track', () => {
		expect(pageSource).toContain('重新生成会完整替换当前 ASR 字幕。是否继续？');
		expect(pageSource).not.toContain('已保护的人工编辑会保留');
	});

	it('submits dubbing subtitle alignment only after save and with the ASR engine input', () => {
		const source = pageSource.match(
			/async function generateDubSubtitlesFromTimeline\(\)[\s\S]*?\n\tasync function generateSemanticTtsGroups/
		)?.[0] ?? '';
		expect(source).toContain("operationBusy('dub_subtitle_generation')");
		expect(source).toContain('所有合成配音轨均已静音，请先打开至少一条');
		expect(source).toMatch(/flushPendingAutosave\(\)[\s\S]*?submitMediaOperation\([\s\S]*?'dub_subtitle_generation'/);
		expect(source).toContain('engine_id: DEFAULT_ASR_ENGINE_ID');
		expect(source).toContain("execution_mode: 'full'");
		expect(source).not.toContain(
			"updateDraftUiState({ subtitle_display_mode: 'dub' })"
		);
		expect(source).not.toContain('source_language:');
		expect(source).not.toContain('lane_ids:');
	});

	it('replaces semantic timing preview with localized and dubbing subtitle display modes', () => {
		expect(videoCuttingTimelineSource).toContain('切换到本土化上屏字幕');
		expect(videoCuttingTimelineSource).toContain('切换到合成配音字幕');
		expect(pageSource).toContain("draft?.ui_state?.subtitle_display_mode !== 'localized'");
		expect(pageSource).toContain("updateDraftUiState({ subtitle_display_mode: 'dub' })");
		expect(pageSource).toContain(
			"terminalTransition.result_summary?.stage_id === 'commit'"
		);
		expect(pageSource).toContain('SubtitleDisplayModel.resolveDubSubtitlePreview(operations)');
		expect(pageSource).toContain("'配音字幕生成中'");
		expect(videoCuttingTimelineSource).toMatch(
			/openTrackHeightContextMenu[\s\S]*?trackId === 'localizedSubtitles'[\s\S]*?subtitleTrack: 'localized'/
		);
		expect(videoCuttingTimelineSource).not.toContain('预览语义映射段落');
		expect(pageSource).not.toContain('resolveSemanticAlignmentPreview');
	});

	it('routes formal dubbing subtitle edits through the versioned review command', () => {
		expect(pageSource).toMatch(
			/async function updateSelectedDubSubtitle[\s\S]*?flushPendingAutosave\(\)[\s\S]*?buildDubSubtitleReviewRequest[\s\S]*?reviewVideoLocalizationDubSubtitles/
		);
		expect(pageSource).toContain('onUpdateDubSubtitle={updateSelectedDubSubtitle}');
		expect(cuttingInspectorSource).toContain('保存并同步上屏');
		expect(cuttingInspectorSource).toContain('label="入点"');
		expect(cuttingInspectorSource).toContain('label="出点"');
		expect(cuttingInspectorSource).toContain('selectedCue?.reviewable');
		expect(videoCuttingTimelineSource).toContain('调整合成配音字幕入点');
		expect(videoCuttingTimelineSource).toContain('调整合成配音字幕出点');
		expect(cuttingInspectorSource).not.toContain('根据实际合成配音识别出的只读字幕');
	});

	it('reconnects the video-only rendering surface after the host window resumes', () => {
		const resumeFunction = previewPanelSource.match(
			/function reconnectVideoSurfaceAfterResume\(\)[\s\S]*?\n\tfunction hasTimelineMedia/
		)?.[0] ?? '';
		expect(previewPanelSource).toContain("window.addEventListener('blur', handleWindowBlur)");
		expect(previewPanelSource).toContain("window.addEventListener('focus', handleWindowFocus)");
		expect(previewPanelSource).toContain('videoSurfaceResumeController.consumeResume');
		expect(previewPanelSource).toContain('{#key previewVideoSurfaceKey(previewVideoSrc, videoSurfaceElementRevision)}');
		expect(resumeFunction).toContain('videoSurfaceElementRevision = Math.max(');
		expect(previewPanelSource).not.toContain('Math.max(mediaReloadRevision, videoSurfaceReloadRevision)');
		expect(resumeFunction).not.toContain('mediaReloadRevision = Date.now()');
	});

	it('loads the secondary inspector outside the initial timeline bundle', () => {
		expect(pageSource).not.toContain("import CuttingInspector from './CuttingInspector.svelte'");
		expect(pageSource).toContain("{#await import('./CuttingInspector.svelte')}");
	});
});

describe('split dub clip deletion policy', () => {
	it('keeps a shared generation active while another split segment still uses it', () => {
		const removed = [{
			clip_id: 'clip-a', track_id: 'dub', start_ms: 0, end_ms: 1000,
			task_id: 'task-shared', generation_id: 'task-shared'
		}];
		const remaining = [{
			clip_id: 'clip-a_part_2', track_id: 'dub', start_ms: 1000, end_ms: 2000,
			task_id: 'task-shared', generation_id: 'task-shared', media_source_clip_id: 'clip-a'
		}];

		expect(discardedTtsTaskIdsAfterClipRemoval([], removed, remaining)).toEqual([]);
	});

	it('discards a generation after its final timeline segment is removed', () => {
		const removed = [{
			clip_id: 'clip-a_part_2', track_id: 'dub', start_ms: 1000, end_ms: 2000,
			task_id: 'task-shared', generation_id: 'task-shared'
		}];

		expect(discardedTtsTaskIdsAfterClipRemoval(['task-old'], removed, [])).toEqual(['task-old', 'task-shared']);
	});
});

describe('video localization project routing', () => {
	it('clears a transient polling error after synchronization recovers', () => {
		expect(errorAfterRecoveredSync('Bad Gateway', 'Bad Gateway')).toBe('');
		expect(errorAfterRecoveredSync('保存项目失败', 'Bad Gateway')).toBe('保存项目失败');
	});

	it('keeps export failures visible inside the open export dialog', () => {
		expect(pageSource).toContain('errorMessage={exportError}');
		expect(pageSource).toContain('exportError = (e as Error).message ||');
		expect(exportDialogSource).toContain('<div class="export-error" role="alert">{errorMessage}</div>');
	});

	it('sorts history projects by last update without mutating the API response', () => {
		const projects = [
			{ project_id: 'older', created_at: '2026-07-29T08:00:00Z', updated_at: '2026-07-29T09:00:00Z' },
			{ project_id: 'newer', created_at: '2026-07-29T08:00:00Z', updated_at: '2026-07-29T10:00:00Z' },
			{ project_id: 'offset-earlier', created_at: '2026-07-29T07:00:00Z', updated_at: '2026-07-29T12:00:00+08:00' }
		];

		expect(sortProjectSummariesByUpdatedAt(projects).map((project) => project.project_id)).toEqual([
			'newer',
			'older',
			'offset-earlier'
		]);
		expect(projects.map((project) => project.project_id)).toEqual(['older', 'newer', 'offset-earlier']);
	});

	it('formats the visible last-update field and handles invalid legacy values', () => {
		expect(formatProjectUpdatedAt('2026-07-29T08:05:00')).toBe('7/29 08:05');
		expect(formatProjectUpdatedAt('not-a-date')).toBe('时间未知');
		expect(pageSource).toContain('aria-label={`最后更新时间 ${formatProjectUpdatedAt(project.updated_at)}`}');
		expect(pageSource).toContain('>{formatProjectUpdatedAt(project.updated_at)}</time>');
	});

	it('keeps an explicitly requested project even before the synchronized menu contains it', () => {
		expect(resolveInitialProjectId('requested-project', [
			{ project_id: 'fallback-project', has_source_media: true }
		])).toBe('requested-project');
	});

	it('falls back to the first project with imported media only when the URL has no project', () => {
		expect(resolveInitialProjectId(null, [
			{
				project_id: 'missing-project',
				has_source_media: true,
				source_media_status: 'missing'
			},
			{
				project_id: 'media-project',
				has_source_media: true,
				source_media_status: 'available'
			}
		])).toBe('media-project');
	});

	it('queues project deletion while keeping project transitions isolated', () => {
		const deleteButtonSource = pageSource.match(
			/<button\s+class="project-menu-delete"[\s\S]*?<\/button>/
		)?.[0] ?? '';
		expect(pageSource).toContain('new ProjectDeleteQueue');
		expect(pageSource).toMatch(
			/projectDeleteQueue\.enqueue\(\s*project\.project_id/
		);
		expect(deleteButtonSource).toContain(
			'aria-disabled={pendingProjectDeleteIds.has(project.project_id)}'
		);
		expect(deleteButtonSource).not.toContain('disabled={Boolean(deletingProjectId)}');
		expect(pageSource).toMatch(
			/const operation = await operationFeedController\.submit\(targetProjectId, kind, parameters\);[\s\S]*?if \(!operation\) return;/
		);
		expect(operationFeedControllerSource).toMatch(
			/private async mutate[\s\S]*?restartGeneration\(\)[\s\S]*?isCurrent\(projectId, generation\)/
		);
		expect(pageSource).toMatch(
			/projectCatalogController\.delete\(project\.project_id\)[\s\S]*?if \(projectId === project\.project_id\)/
		);
		expect(projectCatalogControllerSource).toMatch(/async delete[\s\S]*?this\.remove\(projectId\)/);
	});

	it('loads the URL project independently from slow project-package reconciliation', () => {
		const loadProjectsSource = pageSource.match(
			/async function loadProjects\(\)[\s\S]*?\n\tasync function loadDraft/
		)?.[0] ?? '';
		expect(loadProjectsSource).toContain('projectCatalogController.load()');
		expect(loadProjectsSource).toContain('loadDraft(urlProjectId)');
		expect(loadProjectsSource).not.toContain('projectCatalogController.reconcile(');
	});

	it('does not replace the timeline workspace while preview playback is active', () => {
		expect(pageSource).toMatch(/function refreshDubbingWorkspaceOnFocus[\s\S]*?if \(!projectId \|\| previewPlaying/);
		expect(pageSource).toMatch(/function refreshDubbingDraftLive[\s\S]*?\|\| previewPlaying/);
	});

	it('retries retained autosave work when the backend recovers', () => {
		const recoverySource = pageSource.match(
			/function handleApiRecovered\(\)[\s\S]*?\n\tfunction hasPendingDraftWork/
		)?.[0] ?? '';
		expect(recoverySource).toContain('draft !== null && hasPendingSaveWork()');
		expect(recoverySource).toContain('void flushPendingAutosave()');
		expect(recoverySource.indexOf('void flushPendingAutosave()')).toBeLessThan(
			recoverySource.indexOf('shouldRetryInitialLoadOnApiRecovery')
		);
	});

	it('saves the current draft with Ctrl or Cmd plus S without adding a button', () => {
		const keydownSource = pageSource.match(
			/function handlePageKeydown\(event: KeyboardEvent\)[\s\S]*?\n\tfunction defaultLocalizationProjectName/
		)?.[0] ?? '';
		expect(keydownSource).toContain("event.key.toLowerCase() === 's'");
		expect(keydownSource).toContain('event.ctrlKey || event.metaKey');
		expect(keydownSource).toContain('event.preventDefault()');
		expect(keydownSource).toContain('void saveDraftManually()');
		expect(pageSource).toContain('async function saveDraftManually()');
		expect(pageSource).not.toContain('aria-label="手动保存"');
	});

	it('routes catalog reads and mutations through one concurrency boundary', () => {
		for (const directCall of [
			"Api.projectSummaries('video_localization')",
			'Api.syncVideoLocalizationProjectSummaries()',
			'Api.createProject(',
			'Api.updateProject(',
			'Api.autoNameVideoLocalizationProject(',
			'Api.deleteProject('
		]) expect(pageSource).not.toContain(directCall);
		expect(projectCatalogControllerSource).toContain('private readEpoch = 0');
		expect(projectCatalogControllerSource).toContain('private mutationEpoch = 0');
		expect(projectCatalogControllerSource).toContain('private completeMutation()');
	});

	it('routes TTS history reads and deletions through one project-scoped boundary', () => {
		expect(pageSource).not.toContain('Api.history(');
		expect(pageSource).not.toContain('Api.deleteHistory(');
		expect(pageSource).toContain('ttsHistoryController.loadProject(nextProjectId)');
		expect(pageSource).toContain('ttsHistoryController.deleteMany(');
		expect(pageSource).toContain('ttsHistoryController.deleteSegment(');
		expect(pageSource).toContain('ttsHistoryController.deleteAll(');
		expect(ttsHistoryControllerSource).toContain('private generation = 0');
		expect(ttsHistoryControllerSource).toContain('private readEpoch = 0');
		expect(ttsHistoryControllerSource).toContain('private mutationEpoch = 0');
		expect(ttsHistoryControllerSource).toContain(
			'deleteVideoLocalizationTtsHistory(projectId, request)'
		);
		expect(ttsHistoryControllerSource).not.toContain('Promise.allSettled');
	});

	it('clears every project-scoped selection through one transition boundary', () => {
		const resetSource = pageSource.match(
			/function resetProjectSelectionState\(\)[\s\S]*?\n\tfunction clearProjectRuntimeState/
		)?.[0] ?? '';
		for (const assignment of [
			"selectedCueId = ''",
			"selectedLocalizedSubtitleId = ''",
			'selectedProvisionalSubtitle = null',
			"selectedTimelineAudioClipId = ''",
			'timelineSelectionItems = []',
			'ttsSelectionSession = { ...EMPTY_TTS_SELECTION_SESSION }',
			'timelineSelectionRange = null',
			'audioSelectionRange = null',
			'playbackSessionController.updateSelectionRange(null)'
		]) expect(resetSource).toContain(assignment);
		expect(pageSource.match(/async function loadDraft[\s\S]*?\n\tfunction applyFreshProjectDraft/)?.[0])
			.toContain('resetProjectSelectionState()');
		expect(pageSource.match(/function clearProjectRuntimeState[\s\S]*?\n\tfunction clearProjectIdFromUrl/)?.[0])
			.toContain('resetProjectSelectionState()');
	});
});

describe('video localization draft persistence', () => {
	it('waits for the in-flight autosave before a project transition can continue', () => {
		expect(pageSource).toMatch(/async function flushPendingAutosave\(\)[\s\S]*?return projectSessionController\.flush\(\)/);
		expect(projectSessionControllerSource).toContain('private inFlight: Promise<void> | null = null');
		expect(projectSessionControllerSource).toMatch(/async flush\(\)[\s\S]*?if \(this\.inFlight\) await this\.inFlight/);
	});

	it('does not let an already rolled-back subtitle save failure block later project commands', () => {
		const flushSource = pageSource.match(
			/async function flushPendingAutosave\(\)[\s\S]*?\n\tfunction cancelPendingAutosave/
		)?.[0] ?? '';
		expect(pageSource).not.toContain('localizedSubtitleSaveFailures');
		expect(flushSource).toContain('localizedSubtitleSaveBarrier.flush()');
	});

	it('does not autosave compatibility-only timeline initialization while opening a project', () => {
		const loadDraftSource = pageSource.match(
			/async function loadDraft[\s\S]*?\n\tfunction applyFreshProjectDraft/
		)?.[0] ?? '';
		expect(loadDraftSource).toContain('withEditableMediaClips(loadedWorkspace.draft, mediaHealth)');
		expect(loadDraftSource).not.toContain('initializedEditableTimeline');
	});

	it('keeps media attachment projection separate from mix policy', () => {
		const projectionSource = pageSource.match(
			/function withEditableMediaClips[\s\S]*?\n\tasync function closeCurrentProject/
		)?.[0] ?? '';
		expect(projectionSource).toContain('normalizeWorkspaceDraft');
		expect(projectionSource).not.toContain('configureAutomaticDubMix');
		expect(projectionSource).not.toContain('automatic_dub_mix_configured');
		expect(projectionSource).not.toContain('initial_track_mix_configured');
	});

	it('keeps the original clip mounted when only its waveform visualization fails', () => {
		expect(videoCuttingTimelineSource).not.toContain('originalWaveformUnavailable');
		expect(videoCuttingTimelineSource).not.toContain('源音频文件缺失');
		expect(videoCuttingTimelineSource).toContain("{#if clipsForTrack('original').length}");
	});

	it('preserves unsaved project content during a background refresh', () => {
		expect(projectSessionControllerSource).toContain('hasPendingDraft()');
		expect(pageSource).toMatch(/function hasPendingProjectContentEdits\(\)[\s\S]*?projectSessionController\.hasPendingDraft\(\)/);
		const pendingContentSource = pageSource.match(
			/function hasPendingProjectContentEdits\(\)[\s\S]*?\n\t}/
		)?.[0] ?? '';
		expect(pendingContentSource).not.toContain('pendingTtsInitializations');
		expect(pageSource).toMatch(/function applyFreshProjectDraft[\s\S]*?hasPendingProjectContentEdits\(\)/);
		expect(pageSource).toMatch(/draft && shouldPreserveLocalContent[\s\S]*?mergeDraftAfterConflict/);
		expect(pageSource).toMatch(/timelineEditController\.mergeRefresh\(editableDraft\);[\s\S]*?timelineEditController\.synchronizeExternalDraft\(mergedDraft\)/);
		const focusRefreshSource = pageSource.match(
			/function refreshDubbingWorkspaceOnFocus\(\)[\s\S]*?\n\t}/
		)?.[0] ?? '';
		const liveRefreshSource = pageSource.match(
			/function refreshDubbingDraftLive\(\)[\s\S]*?\n\t}/
		)?.[0] ?? '';
		expect(focusRefreshSource).not.toContain('hasPendingProjectContentEdits()');
		expect(liveRefreshSource).not.toContain('hasPendingProjectContentEdits()');
	});

	it('persists a single ASR timeline move through the same owned timeline transaction as group moves', () => {
		const singleMoveSource = pageSource.match(
			/function updateCueTimeFromTimeline[\s\S]*?\n\tfunction updateLocalizedSubtitleTime/
		)?.[0] ?? '';
		expect(singleMoveSource).toContain('moveTimelineItemsFromTimeline([');
		expect(singleMoveSource).not.toContain('Api.updateVideoLocalizationCue');
		expect(pageSource).toMatch(
			/function moveTimelineItemsFromTimeline[\s\S]*?source_duration_ms: Math\.round\(moved\.endMs - moved\.startMs\)[\s\S]*?dispatchTimelineReplacement/
		);
	});

	it('uses the persisted merge base when retrying concurrent draft saves', () => {
		const autosaveSource = pageSource.match(
			/async function saveProjectSessionAutosave[\s\S]*?\n\tfunction cueNeedsDraftSave/
		)?.[0] ?? '';
		expect(autosaveSource).toMatch(
			/if \(savingScope === 'ui'\)[\s\S]*?Api\.updateVideoLocalizationUiState[\s\S]*?ui_state_patch[\s\S]*?return;/
		);
		expect(autosaveSource).not.toContain('preserveClientContentAfterUiStateSave');
		expect(autosaveSource).toMatch(
			/mergeDraftAfterConflict\(latest, persistedSavingDraft, \{[\s\S]*?baseDraft: savingConflictMergeBase/
		);
		expect(autosaveSource).toMatch(
			/mergeDraftAfterConflict\(workspaceSavedDraft, currentDraft, \{[\s\S]*?baseDraft: savingDraft/
		);
		expect(autosaveSource).toContain(
			'draftConflictMergeBase = snapshotDraftForConflictMerge(workspaceSavedDraft)'
		);
	});

	it('never loads the 47 MB compatibility draft during normal workbench use', () => {
		expect(pageSource).not.toContain('Api.videoLocalizationDraft(');
	});

	it('keeps an optimistic history clip playable while its bounded placement save runs', () => {
		expect(previewPanelSource).toContain(
			'const dubTrackClips = $derived(dubTimelineClips.filter(timelineClipHasAudioSource))'
		);
		expect(previewPanelSource).not.toMatch(
			/dubTrackClips[^\n]*filter[^\n]*(?:applying|history:)/
		);
	});

	it('keeps operation summaries independent from embedded draft history', () => {
		const applyFreshDraftSource = pageSource.match(
			/function applyFreshProjectDraft[\s\S]*?\n\tasync function mutateCurrentProjectDraft/
		)?.[0] ?? '';
		expect(applyFreshDraftSource).not.toMatch(/\boperations\s*=/);
		expect(pageSource).toMatch(
			/function refreshDubbingWorkspaceOnFocus[\s\S]*?loadOperations\(refreshingProjectId\)/
		);
		expect(pageSource).not.toContain('draft.operations ??');
		expect(pageSource).not.toContain('mergeOperationResultSummary');
		expect(operationFeedControllerSource).toMatch(
			/private async poll[\s\S]*?client\.readHead\([\s\S]*?feed\.changed[\s\S]*?applyHeadFeed\(feed, 'poll'\)/
		);
	});

	it('continues low-frequency task discovery while the visible page is idle', () => {
		expect(pageSource).toMatch(
			/function handleOperationVisibilityChange[\s\S]*?operationFeedController\.setVisible\(false\)[\s\S]*?operationFeedController\.setVisible\(true\)/
		);
		expect(operationFeedControllerSource).toContain('this.idlePollMs = options.idlePollMs ?? 5_000');
		expect(operationFeedControllerSource).toContain('this.activePollMs = options.activePollMs ?? 5_000');
		expect(operationFeedControllerSource).toMatch(
			/scheduleNextPoll[\s\S]*?currentOperations\.some\(isActiveOperation\)[\s\S]*?idlePollMs/
		);
	});

	it('routes authoritative draft writes through the active project mutation boundary', () => {
		expect(pageSource).toMatch(/async function mutateCurrentProjectDraft[\s\S]*?projectDraftSessionController\.mutate/);
		expect(pageSource).not.toMatch(/draft\s*=\s*(?:withEditableMediaClips\()?await Api\./);
		expect(pageSource).not.toContain('draft = updated;');
	});

	it('keeps high-frequency timeline view state in the current tab instead of draft autosave', () => {
		expect(pageSource).toContain('let previewPlaybackRestored = false');
		expect(pageSource).toMatch(/function updatePreviewTime\(timeMs: number\) \{[\s\S]*?playbackSessionController\.updateTime\(timeMs\)/);
		expect(pageSource).toMatch(/async function restorePreviewPlaybackState[\s\S]*?playbackSessionController\.restore\(expectedProjectId\)/);
		expect(playbackSessionControllerSource).toMatch(/updateTime\(timeMs: number\)[\s\S]*?if \(!this\.restored\) return;[\s\S]*?this\.requestPersistence\(boundedTimeMs\)/);
		expect(playbackSessionControllerSource).toContain('persistIntervalMs ?? 250');
		expect(playbackSessionControllerSource).toMatch(/persistCurrentTime\(\)[\s\S]*?this\.flushPersistence\(\)/);
		expect(playbackSessionControllerSource).toMatch(/restore\(expectedProjectId: string\)[\s\S]*?this\.driver\.seek\(this\.options\.getTimeMs\(\)\);[\s\S]*?this\.options\.setRestored\(true\)/);
		expect(pageSource).toContain('resolveTimelineViewState(');
		expect(pageSource).not.toContain('queueDraftUiStatePatch({ playhead_ms:');
		expect(pageSource).not.toMatch(/function updateTimelineZoom[\s\S]*?updateDraftUiState\(\{ timeline_zoom \}\)/);
		expect(pageSource).not.toMatch(/function updateTimelineViewportStart[\s\S]*?updateDraftUiState\(\{ timeline_viewport_start_ms \}\)/);
	});

	it('keeps UI-only controls from invalidating the complete project draft', () => {
		const updateUiState = pageSource.match(
			/function updateDraftUiState\(patch: Record<string, unknown>\)[\s\S]*?\n\tfunction queueDraftUiStatePatch/
		)?.[0] ?? '';
		expect(updateUiState).toContain('draft.ui_state = mergeVideoLocalizationUiState');
		expect(updateUiState).not.toContain('draft = { ...draft');
		expect(pageSource).toMatch(/function focusInspector[\s\S]*?if \(wasCollapsed\) updateDraftUiState/);
	});

	it('does not pause the video while an auxiliary clip is still buffering', () => {
		const bufferingHandler = previewPanelSource.match(
			/function pausePlaybackForAudioBuffering[\s\S]*?\n\tfunction markMediaRuntimeStall/
		)?.[0] ?? '';
		const failureHandler = previewPanelSource.match(
			/function reportAudioPlaybackFailure[\s\S]*?\n\tfunction resumePlaybackAfterAudioStall/
		)?.[0] ?? '';
		expect(bufferingHandler).toContain('startAudioWhenReady(audio)');
		expect(bufferingHandler).not.toContain('previewVideoEl?.pause()');
		expect(failureHandler).toContain('视频已继续播放');
		expect(failureHandler).not.toContain('previewVideoEl?.pause()');
	});

	it('never persists an in-flight history-drop placeholder', () => {
		const draft = {
			timeline_clips: [
				{ clip_id: 'ready', track_id: 'dub', audio_path: '/ready.wav', status: 'ready' },
				{ clip_id: 'pending_history_1', track_id: 'dub', optimistic_history_result_id: 'result-1', status: 'applying' }
			]
		} as unknown as VideoLocalizationDraft;

		expect(draftForPersistence(draft).timeline_clips.map((clip) => clip.clip_id)).toEqual(['ready']);
	});

	it('keeps a newer empty server draft after another page resets the project', () => {
		const latest = {
			updated_at: '2026-07-22T15:00:00',
			source_media: {}, stems: {}, cues: [], localized_subtitles: [], speakers: [],
			reference_clips: [], operations: []
		} as unknown as VideoLocalizationDraft;
		const stale = {
			...latest,
			updated_at: '2026-07-22T14:59:00',
			source_media: { filename: 'demo.mp4', video_path: '/project/source/demo.mp4' }
		} as unknown as VideoLocalizationDraft;

		expect(shouldKeepServerResetDraft(latest, stale)).toBe(true);
	});

	it('sends dub lane changes as an explicit one-save edit intent', () => {
		const draft = {
			ui_state: {},
			timeline_clips: [{ clip_id: 'clip-1', track_id: 'dub', start_ms: 0, end_ms: 1000, dub_lane: 2 }]
		} as unknown as VideoLocalizationDraft;

		const persisted = draftForPersistence(draft, {
			timelineDirtyFieldsByClipId: dirtyFields([['clip-1', ['lane']]])
		});

		expect(persisted.ui_state?.client_timeline_edit_intent).toEqual({ dub_lane_clip_ids: ['clip-1'] });
	});
});

describe('video localization inspector policy', () => {
	it('opens every project on the task tab', () => {
		expect(inspectorSectionOnProjectLoad()).toBe('tasks');
	});

	it('keeps task detail transport behind the page project-session boundary', () => {
		expect(taskProgressPanelSource).not.toContain("from '$lib/api'");
		expect(taskProgressPanelSource).toContain('onLoadTaskDetail');
		expect(cuttingInspectorSource).toContain('{onLoadTaskDetail}');
		expect(pageSource).toMatch(
			/function loadActivityTaskDetail[\s\S]*?operationDetailSessionController\.refresh[\s\S]*?Api\.videoLocalizationOperation/
		);
	});

	it('opens dubbing for every localized subtitle selection', () => {
		expect(inspectorSectionForTimelineSelection([
			{ kind: 'subtitle', trackId: 'localizedSubtitles' }
		])).toBe('dubbing');
		expect(inspectorSectionForTimelineSelection([
			{ kind: 'subtitle', trackId: 'localizedSubtitles' },
			{ kind: 'subtitle', trackId: 'localizedSubtitles' }
		])).toBe('dubbing');
	});

	it('opens localized subtitles on the all-clips dubbing view', () => {
		expect(pageSource).toMatch(
			/function selectLocalizedSubtitle[\s\S]*?dubbingHistoryScope = 'all';[\s\S]*?focusInspector\('dubbing'\)/
		);
		expect(pageSource).toContain('bind:dubbingHistoryScope');
		expect(cuttingInspectorSource).toContain('bind:historyScope={dubbingHistoryScope}');
	});

	it('does not redirect mixed or non-localized selections', () => {
		expect(inspectorSectionForTimelineSelection([])).toBeNull();
		expect(inspectorSectionForTimelineSelection([
			{ kind: 'subtitle', trackId: 'localizedSubtitles' },
			{ kind: 'audio', trackId: 'dub' }
		])).toBeNull();
	});
});

describe('draft conflict merge policy', () => {
	function draft(revisionId: string, cues: VideoLocalizationCue[]): VideoLocalizationDraft {
		return {
			transcription: { revision_id: revisionId },
			cues,
			localized_subtitles: [],
			localization_state: {},
			timeline_clips: [],
			ui_state: {},
			glossary: [],
			scene_context: ''
		} as unknown as VideoLocalizationDraft;
	}

	it('keeps another client glossary and scene edits when this client only changes a cue', () => {
		const base = draft('revision_1', [cue({ cue_id: 'cue-local', en_subtitle_text: 'Before' })]);
		base.glossary = [{
			glossary_id: 'term-1',
			source_text: 'LLM',
			corrected_source_text: null,
			zh_text: '大语言模型',
			notes: null
		}];
		base.scene_context = '原始场景';
		const latest = structuredClone(base);
		latest.glossary = [{
			...latest.glossary[0],
			zh_text: '语言模型'
		}];
		latest.scene_context = '另一个窗口更新后的场景';
		const local = structuredClone(base);
		local.cues[0] = { ...local.cues[0], en_subtitle_text: 'Local correction' };

		const merged = mergeDraftAfterConflict(latest, local, { baseDraft: base });

		expect(merged.glossary[0].zh_text).toBe('语言模型');
		expect(merged.scene_context).toBe('另一个窗口更新后的场景');
		expect(merged.cues[0].en_subtitle_text).toBe('Local correction');
	});

	it('merges concurrent cue edits by entity field instead of restoring a stale cue snapshot', () => {
		const base = draft('revision_1', [
			cue({ cue_id: 'cue-a', en_subtitle_text: 'A before', notes: null }),
			cue({ cue_id: 'cue-b', en_subtitle_text: 'B before', notes: null })
		]);
		const latest = structuredClone(base);
		latest.cues[1] = { ...latest.cues[1], notes: 'server review' };
		const local = structuredClone(base);
		local.cues[0] = { ...local.cues[0], en_subtitle_text: 'A corrected locally' };

		const merged = mergeDraftAfterConflict(latest, local, { baseDraft: base });

		expect(merged.cues.find((item) => item.cue_id === 'cue-a')?.en_subtitle_text).toBe('A corrected locally');
		expect(merged.cues.find((item) => item.cue_id === 'cue-b')?.notes).toBe('server review');
	});

	it('keeps the complete server cue set when a new ASR revision finishes', () => {
		const local = draft('revision_old', [cue({ cue_id: 'old_cue', transcription_revision_id: 'revision_old' })]);
		const latest = draft('revision_new', [
			cue({ cue_id: 'new_cue_1', transcription_revision_id: 'revision_new' }),
			cue({ cue_id: 'new_cue_2', transcription_revision_id: 'revision_new' })
		]);

		const merged = mergeDraftAfterConflict(latest, local);

		expect(merged.cues.map((item) => item.cue_id)).toEqual(['new_cue_1', 'new_cue_2']);
	});

	it('preserves a local deletion within the same ASR revision', () => {
		const kept = cue({ cue_id: 'kept', transcription_revision_id: 'revision_1' });
		const removed = cue({ cue_id: 'removed', transcription_revision_id: 'revision_1' });
		const latest = draft('revision_1', [kept, removed]);
		const local = draft('revision_1', [kept]);

		const merged = mergeDraftAfterConflict(latest, local);

		expect(merged.cues.map((item) => item.cue_id)).toEqual(['kept']);
	});

	it('preserves an explicit timeline audio delete during a draft conflict merge', () => {
		const latest = draft('revision_1', []);
		latest.timeline_clips = [{
			clip_id: 'clip_localized_0002',
			track_id: 'dub',
			start_ms: 1000,
			end_ms: 2000,
			source_start_ms: 0,
			source_end_ms: 1000,
			audio_path: '/tmp/clip.wav',
			status: 'ready'
		}];
		const local = draft('revision_1', []);

		const merged = mergeDraftAfterConflict(latest, local, { deletedTimelineClipIds: ['clip_localized_0002'] });

		expect(merged.timeline_clips).toEqual([]);
	});

	it('keeps backend task tombstones when a stale browser has an older ui state', () => {
		const latest = draft('revision_1', []);
		latest.ui_state = {
			discarded_tts_task_ids: ['task-deleted'],
			latest_tts_task_by_segment: { localized_1: 'task-new' }
		};
		const local = draft('revision_1', []);
		local.ui_state = { discarded_tts_task_ids: ['task-local'] };

		const merged = mergeDraftAfterConflict(latest, local);

		expect(new Set(merged.ui_state.discarded_tts_task_ids as string[])).toEqual(new Set(['task-deleted', 'task-local']));
		expect(merged.ui_state.latest_tts_task_by_segment).toEqual({ localized_1: 'task-new' });
	});

	it('does not restore a stale task mapping after the backend clears it', () => {
		const latest = draft('revision_1', []);
		latest.ui_state = { latest_tts_task_by_segment: {} };
		const local = draft('revision_1', []);
		local.ui_state = {
			selected_cue_id: 'cue-local',
			latest_tts_task_by_segment: { localized_1: 'task-stale' }
		};

		const merged = mergeDraftAfterConflict(latest, local);

		expect(merged.ui_state.selected_cue_id).toBe('cue-local');
		expect(merged.ui_state.latest_tts_task_by_segment).toEqual({});
	});

	it('does not treat a candidate status alone as proof that a queued dub placeholder is active', () => {
		expect(isOrphanedDubPlaceholder({
			clip_id: 'orphan', track_id: 'dub', start_ms: 0, end_ms: 1000, status: 'queued'
		})).toBe(true);
		expect(isOrphanedDubPlaceholder({
			clip_id: 'active', track_id: 'dub', start_ms: 0, end_ms: 1000, status: 'queued', task_id: 'task-active'
		})).toBe(false);
		expect(isOrphanedDubPlaceholder({
			clip_id: 'candidate-active', track_id: 'dub', start_ms: 0, end_ms: 1000, status: 'queued', candidate_id: 'candidate-active'
		})).toBe(true);
		expect(isOrphanedDubPlaceholder({
			clip_id: 'candidate-finished', track_id: 'dub', start_ms: 0, end_ms: 1000, status: 'queued', candidate_id: 'candidate-finished'
		})).toBe(true);
		expect(isOrphanedDubPlaceholder({
			clip_id: 'ready', track_id: 'dub', start_ms: 0, end_ms: 1000, status: 'ready'
		})).toBe(false);
	});

	it('keeps a newer generated clip when a stale browser moves the previous generation', () => {
		const latest = {
			clip_id: 'clip_localized_0004', track_id: 'dub', start_ms: 26240, end_ms: 28329,
			source_start_ms: 0, source_end_ms: 2089, audio_path: '/tts/new.wav', status: 'ready',
			generation_id: 'generation-new', task_id: 'generation-new', result_id: 'result-new', dub_lane: 1
		};
		const stale = {
			...latest, start_ms: 29208, end_ms: 31125, source_end_ms: 1917,
			audio_path: '/tts/old.wav', generation_id: 'generation-old', task_id: 'generation-old', result_id: 'result-old', speech_onset_ms: 289
		};

		expect(mergeTimelineClipAfterConflict(latest, stale)).toEqual(latest);
	});

	it('keeps the server dub lane when a stale browser did not edit lane placement', () => {
		const latest = {
			clip_id: 'clip_localized_0004', track_id: 'dub', start_ms: 26240, end_ms: 28167,
			audio_path: '/tts/new.wav', status: 'ready', generation_id: 'generation-new', dub_lane: 0
		};
		const locallyMoved = {
			...latest, audio_path: '/tts/old.wav', generation_id: 'generation-old', dub_lane: 2
		};

		expect(mergeTimelineClipAfterConflict(latest, locallyMoved)).toEqual(latest);
		expect(mergeTimelineClipAfterConflict(latest, locallyMoved, ['lane'])).toEqual({ ...latest, dub_lane: 2 });
	});

	it('replaces a pending clip when its task finishes even if it still carries the previous generation id', () => {
		const latest = {
			clip_id: 'clip_localized_0004', track_id: 'dub', start_ms: 26240, end_ms: 28167,
			source_start_ms: 0, source_end_ms: 1927, audio_path: '/tts/new.wav', status: 'ready',
			generation_id: 'generation-new', task_id: 'generation-new', result_id: 'result-new', dub_lane: 1
		};
		const pending = {
			...latest, end_ms: 28329, source_end_ms: 2089, audio_path: '/tts/old.wav', status: 'queued',
			generation_id: 'generation-old', task_id: 'generation-new', result_id: 'result-old'
		};

		expect(mergeTimelineClipAfterConflict(latest, pending)).toEqual(latest);
	});

	it('uses ready server media and only preserves an explicitly edited local lane', () => {
		const latest = {
			clip_id: 'clip_localized_0004', track_id: 'dub', start_ms: 26240, end_ms: 28167,
			audio_path: '/tts/new.wav', status: 'ready', generation_id: 'generation-new', dub_lane: 0
		};
		const pending = {
			...latest, audio_path: null, status: 'queued', generation_id: 'generation-old', dub_lane: 2
		};

		expect(mergeTimelineClipAfterConflict(latest, pending)).toEqual(latest);
		expect(mergeTimelineClipAfterConflict(latest, pending, ['lane'])).toEqual({ ...latest, dub_lane: 2 });
	});

	it('only lets conflict retry overwrite a server lane for clips moved in this page', () => {
		const latest = draft('revision_1', [cue()]);
		latest.timeline_clips = [{ clip_id: 'clip-1', track_id: 'dub', start_ms: 0, end_ms: 1000, dub_lane: 2 }];
		const local = draft('revision_1', [cue()]);
		local.timeline_clips = [{ clip_id: 'clip-1', track_id: 'dub', start_ms: 0, end_ms: 1000, dub_lane: 0 }];

		expect(mergeDraftAfterConflict(latest, local).timeline_clips[0].dub_lane).toBe(2);
		expect(mergeDraftAfterConflict(latest, local, {
			timelineDirtyFieldsByClipId: dirtyFields([['clip-1', ['lane']]])
		}).timeline_clips[0].dub_lane).toBe(0);
	});

	it('keeps both server lanes when a second client retries an unrelated stale edit', () => {
		const latest = draft('revision_1', [cue()]);
		latest.timeline_clips = [
			{ clip_id: 'clip-a', track_id: 'dub', start_ms: 1000, end_ms: 2000, dub_lane: 2, generation_id: 'generation-a' },
			{ clip_id: 'clip-b', track_id: 'dub', start_ms: 2500, end_ms: 3500, dub_lane: 1, generation_id: 'generation-b' }
		];
		const staleSecondClient = draft('revision_1', [cue()]);
		staleSecondClient.timeline_clips = [
			{ clip_id: 'clip-a', track_id: 'dub', start_ms: 900, end_ms: 1900, dub_lane: 0, generation_id: 'generation-a' },
			{ clip_id: 'clip-b', track_id: 'dub', start_ms: 2400, end_ms: 3400, dub_lane: 0, generation_id: 'generation-b' }
		];

		const merged = mergeDraftAfterConflict(latest, staleSecondClient);

		expect(merged.timeline_clips.map((clip) => ({
			clip_id: clip.clip_id,
			start_ms: clip.start_ms,
			dub_lane: clip.dub_lane
		}))).toEqual([
			{ clip_id: 'clip-a', start_ms: 1000, dub_lane: 2 },
			{ clip_id: 'clip-b', start_ms: 2500, dub_lane: 1 }
		]);
	});

	it('overlays only the explicitly dirty lane and timing groups during a client conflict', () => {
		const latest = draft('revision_1', [cue()]);
		latest.timeline_clips = [
			{ clip_id: 'lane-edit', track_id: 'dub', start_ms: 1000, end_ms: 2000, source_start_ms: 0, source_end_ms: 1000, dub_lane: 2 },
			{ clip_id: 'timing-edit', track_id: 'dub', start_ms: 3000, end_ms: 4000, source_start_ms: 0, source_end_ms: 1000, dub_lane: 1 }
		];
		const local = draft('revision_1', [cue()]);
		local.timeline_clips = [
			{ clip_id: 'lane-edit', track_id: 'dub', start_ms: 900, end_ms: 1900, source_start_ms: 100, source_end_ms: 1100, dub_lane: 0 },
			{ clip_id: 'timing-edit', track_id: 'dub', start_ms: 3200, end_ms: 4200, source_start_ms: 200, source_end_ms: 1200, dub_lane: 0 }
		];

		const merged = mergeDraftAfterConflict(latest, local, {
			timelineDirtyFieldsByClipId: dirtyFields([
				['lane-edit', ['lane']],
				['timing-edit', ['timing']]
			])
		});

		expect(merged.timeline_clips[0]).toMatchObject({ start_ms: 1000, end_ms: 2000, dub_lane: 0 });
		expect(merged.timeline_clips[1]).toMatchObject({ start_ms: 3200, end_ms: 4200, source_start_ms: 200, source_end_ms: 1200, dub_lane: 1 });
	});

	it('preserves same-generation timing edits without restoring removed backend metadata', () => {
		const latest = {
			clip_id: 'clip_localized_0004', track_id: 'dub', start_ms: 26240, end_ms: 28329,
			source_start_ms: 0, source_end_ms: 2089, audio_path: '/tts/new.wav', status: 'ready',
			generation_id: 'generation-new', task_id: 'generation-new', result_id: 'result-new', dub_lane: 1,
			media_source_clip_id: 'clip_localized_0004'
		};
		const { media_source_clip_id: _removedMediaSourceClipId, ...latestWithoutSplitMetadata } = latest;
		const moved = { ...latestWithoutSplitMetadata, start_ms: 29208, end_ms: 31297, speech_onset_ms: 289 };

		expect(mergeTimelineClipAfterConflict(latest, moved)).toEqual(latest);
		expect(mergeTimelineClipAfterConflict(latest, moved, ['timing'])).toEqual({
			...latestWithoutSplitMetadata, start_ms: 29208, end_ms: 31297
		});
	});

	it('does not restore a localized track after the server clears its revision', () => {
		const latest = draft('revision_1', [cue()]);
		latest.localization_state = {};
		latest.localized_subtitles = [];
		const local = draft('revision_1', [cue()]);
		local.localization_state = { created_at: '2026-07-17T10:00:00Z' };
		local.localized_subtitles = [
			{ subtitle_id: 'localized_1', start_ms: 0, end_ms: 1000, text: '旧字幕', quality_flags: [] }
		];

		const merged = mergeDraftAfterConflict(latest, local);

		expect(merged.localized_subtitles).toEqual([]);
		expect(merged.localization_state).toEqual({});
	});
});

describe('stage-aware quality issues', () => {
	it('treats only the unified dubbing inspector as the dubbing stage', () => {
		expect(isDubbingInspectorSection('subtitle')).toBe(false);
		expect(isDubbingInspectorSection('tasks')).toBe(false);
		expect(isDubbingInspectorSection('dubbing')).toBe(true);
	});

	it('keeps ASR issues visible without treating TTS as a subtitle dependency', () => {
		expect(qualityIssueAppliesToStage(issue('ASR_ALIGNMENT_FAILED'), false, false)).toBe(true);
		expect(qualityIssueAppliesToStage(issue('TTS_TEXT_MISSING'), false, false)).toBe(false);
		expect(qualityIssueAppliesToStage(issue('REFERENCE_CLIP_MISSING'), false, false)).toBe(false);
		expect(qualityIssueAppliesToStage(issue('CUE_SPEAKER_MISSING'), false, false)).toBe(false);
	});

	it('reveals localization and dubbing checks only after those stages begin', () => {
		expect(qualityIssueAppliesToStage(issue('ZH_SUBTITLE_MISSING'), true, false)).toBe(true);
		expect(qualityIssueAppliesToStage(issue('CUE_SPEAKER_MISSING'), true, false)).toBe(false);
		expect(qualityIssueAppliesToStage(issue('CUE_SPEAKER_MISSING'), true, true)).toBe(true);
		expect(qualityIssueAppliesToStage(issue('TTS_TEXT_MISSING'), true, false)).toBe(false);
		expect(qualityIssueAppliesToStage(issue('TTS_TEXT_MISSING'), true, true)).toBe(true);
	});
});

describe('manual cue edit protection', () => {
	it('preserves ASR provenance and protects a manual source-text correction', () => {
		const previous = cue();
		const unchanged = protectCueManualEdit(previous, { ...previous }, { text: true });
		expect(unchanged).toEqual(previous);

		const edited = protectCueManualEdit(previous, { ...previous, en_subtitle_text: 'Human correction' }, { text: true });
		expect(edited.source_word_ids).toEqual(['word_1', 'word_2']);
		expect(edited.transcription_revision_id).toBe('revision_1');
		expect(edited.timing_confidence).toBe('high');
		expect(edited.quality_flags).toContain('generated_by_asr');
		expect(edited.quality_flags).toEqual(expect.arrayContaining([
			'manual_text_edit',
			'protected_manual_edit'
		]));
		expect(edited.quality_flags).not.toContain('timing_review_required');
	});

	it('preserves source provenance but requires review after a manual timeline move', () => {
		const previous = cue();
		const edited = protectCueManualEdit(previous, { ...previous, start_ms: 1100, end_ms: 2500 }, { timing: true });
		expect(edited.source_word_ids).toEqual(['word_1', 'word_2']);
		expect(edited.transcription_revision_id).toBe('revision_1');
		expect(edited.timing_confidence).toBe('low');
		expect(edited.quality_flags).toContain('generated_by_asr');
		expect(edited.quality_flags).toEqual(expect.arrayContaining([
			'manual_timing_edit',
			'protected_manual_edit',
			'timing_review_required'
		]));
	});
});

describe('preview cache polling lifecycle', () => {
	it('clears stale green coverage and stops polling after a definitive 404', () => {
		expect(previewCacheSessionControllerSource).toMatch(
			/error instanceof PreviewCacheRequestError && error\.kind === 'not_found'[\s\S]*?cache: null[\s\S]*?onPlaybackCoverageInvalidated[\s\S]*?stopPolling/
		);
		expect(pageSource).toContain('previewCacheSessionController.dispose()');
		expect(pageSource).not.toContain('Api.videoPreviewCacheStatus(');
		expect(pageSource).not.toContain('Api.refreshVideoPreviewCache(');
	});

	it('starts preview caching immediately after a new source video is imported', () => {
		expect(pageSource).toMatch(
			/async function importVideoFile[\s\S]*?Api\.importVideoLocalizationSource\(targetProjectId, file\)[\s\S]*?if \(!imported\) return;[\s\S]*?await refreshDraftOnly\(\);[\s\S]*?void initializePreviewCache\(targetProjectId\);/
		);
	});
});

describe('TTS dual-track handoff policy', () => {
	it('keeps TTS runtime overlays separate from every ordinary draft refresh', () => {
		expect(pageSource).toContain('preserveTtsTasks = true');
		expect(pageSource).toContain('let timelineRuntimeClips = $state<VideoLocalizationTimelineClip[]>([]);');
		expect(pageSource).toContain('composeTimelineRuntimeDraft(draft, timelineRuntimeClips)');
		expect(pageSource).toContain('const refreshDraft = durableTimelineDraft({');
		expect(pageSource).toContain('preserveTtsTasks?: boolean;');
		expect(pageSource).toContain('options.preserveTtsTasks !== false');
	});

	it('keeps the initialization placeholder until the persisted workflow handoff is ready', () => {
		const submitSource = pageSource.match(
			/async function submitSubtitleTts[\s\S]*?\n\tasync function monitorUnboundSubtitleTtsTask/
		)?.[0] ?? '';
		expect(submitSource.indexOf('endSubtitleTtsInitialization(selection)')).toBeGreaterThan(
			submitSource.indexOf('Api.videoLocalizationTtsTask(context.projectId, workflowId)')
		);
	});

	it('registers the durable workflow before exposing a direct-reuse placeholder', () => {
		const reserveSource = pageSource.match(
			/async function reserveSelectedSubtitleGenerateWorkflow[\s\S]*?\n\tasync function prepareSelectedSubtitleGenerateRequest/
		)?.[0] ?? '';
		const prepareSource = pageSource.match(
			/async function prepareSelectedSubtitleGenerateRequest[\s\S]*?\n\tfunction localizedSubtitleForRequest/
		)?.[0] ?? '';
		const reuseSource = pageSource.match(
			/async function reuseSubtitleHistory[\s\S]*?\n\tasync function executeSubtitleHistorySubmission/
		)?.[0] ?? '';
		const submitSource = pageSource.match(
			/async function submitSubtitleTts[\s\S]*?\n\tasync function monitorUnboundSubtitleTtsTask/
		)?.[0] ?? '';

		expect(reserveSource).toContain('Api.reserveVideoLocalizationTtsHandoff');
		expect(reserveSource).toContain('submission_id: selection.clientId');
		expect(prepareSource).toContain('Api.prepareVideoLocalizationTtsHandoff');
		expect(prepareSource).not.toContain('Api.previewVideoLocalizationTtsHandoff');
		expect(reuseSource).toContain('stageSubtitleTtsInitialization(selection)');
		expect(reuseSource).not.toContain('beginSubtitleTtsInitialization(selection)');
		expect(pageSource).toContain("submissionError instanceof ApiError && submissionError.code === 'TIMEOUT'");
		expect(pageSource).toContain('submissionError instanceof TypeError');
		expect(pageSource).toContain('request.video_localization_submission_id');
		expect(submitSource).toContain('reconcileSubmittedTtsWorkflow(');
	});

	it('removes a successful workflow placeholder because it is not durable media', () => {
		const optimisticClip = {
			clip_id: 'pending_tts_workflow-1',
			track_id: 'dub',
			subtitle_id: 'localized-1',
			cue_id: 'cue-1',
			source_cue_ids: ['cue-1'],
			start_ms: 1000,
			end_ms: 2200,
			source_start_ms: 0,
			source_end_ms: 1200,
			audio_path: null,
			task_id: 'generation-1',
			generation_id: 'generation-1',
			status: 'running',
			generation_progress: 0.9,
			optimistic_tts_workflow_id: 'workflow-1'
		};
		const value = {
			timeline_clips: [optimisticClip],
			tts_tasks: [{
				workflow_id: 'workflow-1',
				project_id: 'project-1',
				segment_id: 'localized-1',
				subtitle_summary: '测试',
				text: '测试',
				source_cue_ids: ['cue-1'],
				start_ms: 1000,
				end_ms: 2200,
				status: 'success',
				generation_task_id: 'generation-1',
				result_id: 'result-1',
				timeline_clip_id: 'clip-localized-1',
				stages: [
					{ kind: 'generation', status: 'success', progress: 1, parameters: {} },
					{ kind: 'placement', status: 'success', progress: 1, parameters: {} }
				],
				created_at: '2026-07-29T12:00:00',
				updated_at: '2026-07-29T12:00:01',
				completed_at: '2026-07-29T12:00:01'
			}]
		} as unknown as VideoLocalizationDraft;

		expect(withTtsWorkflowPlaceholders(value).timeline_clips).toEqual([]);
	});

	it('sends the exact subtitle and source ranges selected by the user', () => {
		expect(pageSource).toContain('target_subtitle_ids: selection.localizedSubtitleIds');
		expect(pageSource).toContain('source_cue_ids: selection.sourceCueIds');
		expect(pageSource).not.toContain('selection.primarySubtitleId');
	});

	it('uses the workflow projector as the only owner of submitted TTS placeholders', () => {
		const submitSource = pageSource.match(
			/async function submitSubtitleTts[\s\S]*?\n\tasync function monitorUnboundSubtitleTtsTask/
		)?.[0] ?? '';
		expect(submitSource).toContain('promoteTtsInitializationPlaceholder(');
		expect(submitSource).toContain(
			'(item) => item.optimistic_tts_workflow_id === workflowMarker'
		);
		expect(submitSource).toContain('mergeTtsWorkflowTask(');
		expect(submitSource).not.toContain('item.clip_id === selection.targetClip?.clip_id');
		expect(submitSource).not.toContain(
			"draft.timeline_clips.find((item) => item.track_id === 'dub' && item.subtitle_id === request.segment_id)"
		);
		expect(submitSource).not.toContain('const timelineClip: VideoLocalizationTimelineClip');
		expect(submitSource).not.toContain('timeline_clips: replacingClip');
	});

	it('handles init markers locally and only deletes persisted workflow ids through the API', () => {
		expect(pageSource).toContain("cancelTtsInitialization(initializationClientId, 'delete')");
		expect(pageSource).toContain('const workflowId = persistedTtsWorkflowId(workflowMarker)');
		expect(pageSource).toContain('Api.deleteVideoLocalizationTtsTask(ttsContext.projectId, workflowId)');
	});

	it('offers stop and stop-plus-delete for active TTS tasks in the task panel', () => {
		expect(pageSource).toContain('async function deleteActivityTask(task: ActivityTask)');
		expect(pageSource).not.toContain('task.initializationId');
		expect(pageSource).toContain('onDeleteTask={deleteActivityTask}');
	});

	it('can stop or delete an initialization placeholder after its live snapshot is lost', () => {
		expect(pageSource).toContain('cleanupTtsInitializationClips,');
		expect(pageSource).toContain("} from './tts-initialization-clips'");
		expect(pageSource).toMatch(/if \(!snapshot\) \{[\s\S]*?cleanupTtsInitializationClips\(timelineRuntimeClips, clientId\)/);
		expect(pageSource).toContain("message = action === 'delete' ? '已停止并删除初始化中的配音片段' : '已停止初始化配音'");
	});

	it('stops the task monitor and removes its generic foreground row after deleting a persisted workflow', () => {
		expect(pageSource).toContain('function stopTrackingDeletedTtsWorkflow(task: VideoLocalizationTtsTask | null | undefined)');
		expect(pageSource).toContain('ttsWorkflowSessionController.cancelMonitor(monitorKey)');
		expect(pageSource).toContain('endSubtitleTtsActivity(taskId)');
		expect(pageSource).toMatch(/const deletedDraft = await Api\.deleteVideoLocalizationTtsTask\(context\.projectId, task\.workflowId\);[\s\S]*?stopTrackingDeletedTtsWorkflow\(workflow\);[\s\S]*?applyFreshProjectDraft\(deletedDraft, false, false\);/);
		expect(pageSource).toMatch(/for \(const workflow of deletedWorkflows\) stopTrackingDeletedTtsWorkflow\(workflow\);[\s\S]*?applyFreshProjectDraft\(deletedDraft, false, false\);/);
	});

	it('clears the deleted workflow placeholder from the timeline selection', () => {
		expect(pageSource).toContain('const deletedWorkflowClipIds = new Set(');
		expect(pageSource).toContain('clip.optimistic_tts_workflow_id === task.workflowId');
		expect(pageSource).toContain('deletedWorkflowClipIds.add(`pending_tts_${task.workflowId}`)');
		expect(pageSource).toMatch(/applyFreshProjectDraft\(deletedDraft, false, false\);[\s\S]*?timelineSelectionItems = timelineSelectionItems\.filter\([\s\S]*?!deletedWorkflowClipIds\.has\(item\.itemId\)/);
		expect(pageSource).toContain("if (deletedWorkflowClipIds.has(selectedTimelineAudioClipId)) selectedTimelineAudioClipId = ''");
	});

	it('makes a direct localized-subtitle click replace stale TTS target state', () => {
		expect(pageSource).toContain('ensureDirectTtsTargetSelection({');
		expect(pageSource).toMatch(/function selectLocalizedSubtitle[\s\S]*?ensureDirectTtsTargetSelection\([\s\S]*?subtitleId,[\s\S]*?selectionItems: timelineSelectionItems/);
		expect(pageSource).toMatch(/function selectSemanticTtsGroup[\s\S]*?resolveTtsSelectionSession\([\s\S]*?selectionItems: timelineSelectionItems/);
	});

	it('uses one project-scoped workflow poll for every queued localization clip', () => {
		expect(pageSource).toContain('ttsWorkflowSessionController.activate(nextProjectId, editableDraft.tts_tasks ?? [])');
		expect(pageSource).toContain('ttsWorkflowSessionController.deactivate()');
		expect(pageSource).toContain('Api.reserveVideoLocalizationTtsHandoff');
		expect(pageSource).toContain('Api.prepareVideoLocalizationTtsHandoff');
		expect(pageSource).not.toContain('Api.previewVideoLocalizationTtsHandoff');
		expect(pageSource).toContain('selection.localizedSubtitleIds');
		expect(pageSource).toContain('selection.sourceCueIds');
		expect(pageSource).toMatch(/else if \(workflowId\) \{[\s\S]*?ttsWorkflowSessionController\.startPolling\(0\)/);
		expect(pageSource).not.toContain('monitorSubtitleTtsTask(');
		expect(pageSource).toContain('async function monitorUnboundSubtitleTtsTask(taskId: string)');
		expect(pageSource).toContain("ttsWorkflowSessionController.monitor<LongformTask>(`longform:${longformTaskId}`");
		expect(pageSource).not.toContain('ttsWorkflowPollingInFlight');
		expect(pageSource).not.toContain('ttsWorkflowPollingGeneration');
	});

	it('does not run transcript verification in the localization TTS submit path', () => {
		const submitSource = pageSource.match(
			/async function submitSubtitleTts[\s\S]*?\n\tasync function monitorUnboundSubtitleTtsTask/
		)?.[0] ?? '';
		expect(submitSource).toContain('verify_enabled: false');
		expect(submitSource).not.toContain('verify_enabled: true');
	});

	it('makes a new TTS placeholder the only destructive timeline selection', () => {
		expect(pageSource).toContain("timelineSelectionItems = [{ kind: 'audio', trackId: 'dub', itemId: placeholderId }]");
	});

	it('allows independent TTS submissions without inventing a second task or failed timeline clip', () => {
		expect(pageSource).not.toContain('if (submittingBatch) return null');
		expect(pageSource).toContain('ttsSubmissionQueue.enqueue(selection.clientId');
		expect(pageSource).toMatch(
			/async function executeSubtitleHistorySubmission[\s\S]*?reserveSelectedSubtitleGenerateWorkflow\(selection, history\.result_id\)[\s\S]*?beginSubtitleTtsInitialization\(selection\)[\s\S]*?prepareSelectedSubtitleGenerateRequest\(selection, history\.result_id\)[\s\S]*?submitSubtitleTts\(base/
		);
		expect(pageSource).toContain('submission_id: selection.clientId');
		expect(pageSource).not.toContain('function failSubtitleTtsInitialization(');
		expect(pageSource).not.toContain("stage: '提交失败'");
		expect(pageSource).not.toContain('status_label: ttsInitializationFailureLabel(failureMessage)');
		expect(pageSource).not.toContain('id: `tts-init:${snapshot.clientId}`');
		expect(pageSource).toMatch(/const failureMessage = ttsInitializationFailureMessage\(e\);\s*endSubtitleTtsInitialization\(selection\);/);
	});

	it('blocks a duplicate active submission for the same segment without blocking other segments', () => {
		expect(pageSource).toMatch(/function ttsSelectionAlreadyActive[\s\S]*?item\.segmentId === snapshot\.segmentId/);
		expect(pageSource).toMatch(/function ttsSelectionAlreadyActive[\s\S]*?task\.segment_id === snapshot\.segmentId[\s\S]*?prepared[\s\S]*?queued[\s\S]*?running/);
		expect(pageSource).toMatch(/async function reuseSubtitleHistory[\s\S]*?ttsSelectionAlreadyActive\(selection\)[\s\S]*?这段字幕已经在生成/);
	});

	it('waits only for authoritative subtitle edits before preparing TTS', () => {
		const saveBarrierSource = pageSource.match(
			/async function flushPendingSubtitleEditsForTts[\s\S]*?\n\tasync function reserveSelectedSubtitleGenerateWorkflow/
		)?.[0] ?? '';
		expect(saveBarrierSource).toContain('flushPendingLocalizedSubtitleSaves()');
		expect(saveBarrierSource).toContain('localizedSubtitleSaveBarrier.flush()');
		expect(saveBarrierSource).not.toContain('projectSessionController.flush()');
		expect(saveBarrierSource).not.toContain('flushPendingAutosave()');
		expect(pageSource).toMatch(
			/async function reserveSelectedSubtitleGenerateWorkflow[\s\S]*?flushPendingSubtitleEditsForTts\(\)/
		);
	});

	it('navigates before materializing reference audio and keeps navigation separate from task submission', () => {
		const openSource = pageSource.match(
			/async function openSelectedSubtitleInGenerate[\s\S]*?\n\tasync function reuseSubtitleHistory/
		)?.[0] ?? '';
		expect(openSource).toContain("schema_version: 'video-localization-tts-handoff-v1'");
		expect(openSource).toContain('VIDEO_LOCALIZATION_TTS_HANDOFF_INTENT_KEY');
		expect(openSource).toContain('await goto(`/generate?${params.toString()}`)');
		expect(openSource).not.toContain('prepareSelectedSubtitleGenerateRequest(');
		expect(openSource).not.toContain('beginTtsSubmission()');
	});

	it('does not expose the removed one-click production workflow in the dubbing inspector', () => {
		expect(pageSource).not.toContain('generateSelectedSubtitleWithProductionRun');
		expect(pageSource).not.toContain('onGenerateSubtitleCanonical=');
		expect(cuttingInspectorSource).not.toContain('onGenerateSubtitleCanonical');
		expect(dubbingInspectorPanelSource).not.toContain('onGenerateCanonical');
		expect(subtitleTtsHistorySource).not.toContain('按流程生成');
	});

	it('shows the frozen TTS target in the dubbing inspector instead of the active source cue binding', () => {
		expect(pageSource).toContain('ttsPrimaryLocalizedSubtitle={ttsPrimaryLocalizedSubtitle}');
		expect(cuttingInspectorSource).toContain('const dubbingLocalizedSubtitle = $derived(ttsPrimaryLocalizedSubtitle ?? selectedLocalizedSubtitle)');
		expect(cuttingInspectorSource).toContain('dubbingLocalizedSubtitle?.tts_text?.trim()');
	});
});

describe('subtitle timeline mutation transactions', () => {
	it('routes source cue delete, merge and split through server transactions', () => {
		expect(pageSource).toContain('Api.deleteVideoLocalizationCue(activeProjectId, cueId)');
		expect(pageSource).toContain('Api.mergeVideoLocalizationCues(activeProjectId');
		expect(pageSource).toContain('Api.splitVideoLocalizationCue(');
		expect(pageSource).toContain('mutateCurrentProjectDraft(');
	});

	it('commits localized subtitle linkage before applying the explicit audio split', () => {
		const endpointIndex = pageSource.indexOf('Api.splitVideoLocalizationLocalizedSubtitle(');
		const audioSplitIndex = pageSource.indexOf('splitTimelineAudioClip(clips, clipId, splitMs, {', endpointIndex);
		expect(endpointIndex).toBeGreaterThan(0);
		expect(audioSplitIndex).toBeGreaterThan(endpointIndex);
		expect(pageSource).toContain("timelineHistoryLabel: '拆分本土化字幕'");
		expect(pageSource).toContain('timelineHistoryPersisted: false');
	});

	it('invalidates earlier per-item saves before committing a group move', () => {
		expect(pageSource).toMatch(/if \(kind === 'subtitle' && trackId === 'subtitles'\) \{[\s\S]*?cueSaveRevision \+= 1;/);
		expect(pageSource).toMatch(/trackId === 'localizedSubtitles'\) \{[\s\S]*?invalidateLocalizedSubtitleSaves\(\);/);
	});

	it('keeps locked dubbing lanes out of drag-reorder commands', () => {
		expect(pageSource).toContain("dubLaneStates[String(fromLane)]?.locked || dubLaneStates[String(toLane)]?.locked");
		expect(videoCuttingTimelineSource).toContain("if (dubLaneStates[String(lane)]?.locked) return");
		expect(videoCuttingTimelineSource).toContain("!dubLaneStates[String(draggedDubLane)]?.locked");
	});

	it('does not erase undo history merely because a background refresh arrived after autosave', () => {
		expect(pageSource).not.toContain('timelineUndoStack');
		expect(pageSource).not.toContain('timelineUndoOrder');
		expect(pageSource).toContain('timelineEditController.mergeRefresh(editableDraft)');
		expect(timelineEditControllerSource).toContain('this.past = [...this.past, entry].slice(-this.maxHistory)');
		expect(timelineEditControllerSource).toContain('SUBTITLE_RUNTIME_FIELDS');
	});

		it('routes clip timing, group moves, refresh, and save acknowledgement through the edit controller', () => {
		expect(pageSource).toContain('function dispatchTimelineEdit(command: TimelineEditTransaction)');
		expect(pageSource).toMatch(
			/function dispatchTimelineEditWithSaveMode[\s\S]*?timelineEditRevision \+= 1;[\s\S]*?saveTimelineEditImmediately\(\)/
		);
		expect(pageSource).toMatch(
			/async function loadDraft[\s\S]*?hasPendingSaveWork\(\)[\s\S]*?flushPendingAutosave\(\)[\s\S]*?workspaceRevisionController\.reset/
		);
		expect(pageSource).toContain('已阻止旧工作区覆盖当前片段');
		expect(pageSource).toContain("label: timingChanged ? '调整音频片段' : '移动配音分轨'");
		expect(pageSource).toContain("label: items.length > 1 ? '移动多个音频片段' : '移动音频片段'");
		expect(pageSource).toContain("label: audioIds.size > 1 ? '删除多个音频片段' : '删除音频片段'");
		expect(pageSource).toMatch(/label: '拆分音频片段'[\s\S]*?addClips:/);
		expect(pageSource).toContain('timelineEditController.mergeRefresh(editableDraft)');
		expect(pageSource).toContain('timelineSavePacket = savingTimelineController.prepareSave()');
			expect(pageSource).toContain('savingTimelineController.acknowledgeSave(timelineSavePacket.packetId, workspaceSavedDraft)');
			// Uncertain-save retry is verified by controller behavior tests and the
			// real partial-ack browser harness, not a source-code branch pattern.
			expect(pageSource).not.toContain('discardStaleTimelineClipEdit');
			expect(pageSource).toMatch(
				/if \(savingTimelineController\?\.hasPendingChanges\)[\s\S]*?settlePendingNoop\(\)[\s\S]*?throw new Error\('时间线编辑无法生成安全的局部保存请求/
			);
			expect(pageSource).toMatch(
				/settlePendingNoop\(\)[\s\S]*?savingTimelineDirtyFields = new Map\(\);[\s\S]*?timelineDirtyFieldsByClipId = new Map\(\);[\s\S]*?timelineSavedRevision = savingTimelineRevision/
			);
		});

	it('keeps placed and split clips local while saves or older live projections are pending', () => {
		expect(pageSource).toContain('timelineProjectionSessionController.refreshLatest(');
			expect(pageSource).toMatch(
				/async function refreshTimelineProjectionOnly[\s\S]*?workspaceRevisionController.canApply[\s\S]*?deletedClipIds: timelineDeletedClipIds[\s\S]*?addedClipIds: timelineEditController\?\.addedTimelineClipIds[\s\S]*?dirtyFieldsByClipId: timelineDirtyFieldsByClipId/
			);
		expect(pageSource.match(
			/async function refreshTimelineProjectionOnly[\s\S]*?\n\tfunction withEditableMediaClips/
		)?.[0] ?? '').not.toContain('hasPendingProjectContentEdits()');
		expect(pageSource).toMatch(
			/function dispatchTimelineEdit[\s\S]*?timelineProjectionSessionController\.invalidate\(\)/
		);
		expect(timelineEditControllerSource).toContain('added_clips: addedClips');
		expect(timelineEditControllerSource).toContain('deleted_clips: deletedClipIds.map');
		expect(timelineEditControllerSource).toContain('cue_collection_change: cueCollectionChange');
		expect(timelineEditControllerSource).toContain('localized_subtitle_collection_change: localizedSubtitleCollectionChange');
		expect(pageSource).toMatch(/function updateSelectedCue[\s\S]*?dispatchTimelineEditDeferred[\s\S]*?replaceCues: nextCues/);
	});

	it('advances the live revision only after the timeline projection was actually applied', () => {
		const liveRefreshSource = pageSource.match(
			/function refreshDubbingDraftLive\(\)[\s\S]*?\n\t}/
		)?.[0] ?? '';
		expect(liveRefreshSource).toContain('refreshTimelineProjectionOnly()');
		expect(liveRefreshSource).not.toContain('workspaceRevisionController.consume');
		expect(pageSource).toMatch(
			/async function refreshTimelineProjectionOnly[\s\S]*?applyFreshProjectDraft[\s\S]*?workspaceRevisionController.consume\(refreshingProjectId, projection.revision, 'timeline'\)/
		);
	});

	it('binds full workspace reads to their repository revision and skips unchanged focus reloads', () => {
		expect(pageSource).toContain('workspaceRevisionController.activate(nextProjectId, loadedWorkspace.revision)');
		expect(pageSource).toMatch(
			/async function refreshDraftOnly[\s\S]*?workspaceRevisionController.canApply\(refreshingProjectId, loadedWorkspace.revision\)[\s\S]*?applyFreshProjectDraft[\s\S]*?workspaceRevisionController.consume\(refreshingProjectId, loadedWorkspace.revision, 'workspace'\)/
		);
		const focusSource = pageSource.match(
			/function refreshDubbingWorkspaceOnFocus\(\)[\s\S]*?\n\t}/
		)?.[0] ?? '';
		expect(focusSource).toContain('Api.videoLocalizationWorkspaceRevision(refreshingProjectId)');
		expect(focusSource).toMatch(
			/workspaceRevisionController.needsRefresh\(refreshingProjectId, current.revision, 'workspace'\)[\s\S]*?await refreshDraftOnly\(\)/
		);
	});

	it('refreshes before confirming an idempotent compact receipt older than the observed workspace', () => {
		const autosaveSource = pageSource.match(
			/async function saveProjectSessionAutosave[\s\S]*?\n\tfunction cueNeedsDraftSave/
		)?.[0] ?? '';
		expect(autosaveSource).toMatch(
			/workspaceRevisionController\.canApply\([\s\S]*?compactSaved\.revision[\s\S]*?if \(!applyCompactReceipt\)[\s\S]*?await refreshDraftOnly\(\)/
		);
		expect(autosaveSource).toMatch(
			/acknowledgeCompactSave\([\s\S]*?\{ applyReceipt: applyCompactReceipt \}/
		);
	});

	it('invalidates stale workspace reads before persisting localized subtitle timing', () => {
		const timingSaveSource = pageSource.match(
			/async function persistLocalizedSubtitleTime[\s\S]*?\n\t}/
		)?.[0] ?? '';
		expect(timingSaveSource).toMatch(
			/projectDraftSessionController\.mutate\([\s\S]*?Api\.editVideoLocalizationLocalizedSubtitle/
		);
		expect(timingSaveSource).toContain('localizedSubtitleSaveIsCurrent');
	});

	it('accepts per-clip target lanes when a multi-lane dubbing selection is committed', () => {
		expect(pageSource).toContain('requestedDubLanesByClipId = new Map');
		expect(pageSource).toContain('canPlaceDubClipGroupAcrossLanes(');
		expect(pageSource).not.toContain('if (requestedLanes.size > 1) return');
		expect(pageSource).toContain('{ dub_lane: requestedDubLanesByClipId.get(clip.clip_id) }');
	});

	it('only clears a destructive selection after the parent confirms deletion', () => {
		expect(videoCuttingTimelineSource).toContain('if (!(await onDeleteTimelineItems(deletable))) return');
		expect(videoCuttingTimelineSource).toContain('if (!(await onDeleteTimelineClip(itemId))) return');
		expect(pageSource).toContain('await performDeleteTimelineItems(items)');
	});

	it('restores a whole media track as one group transaction', () => {
		expect(videoCuttingTimelineSource).toContain('const moves: TimelineGroupMoveCommitItem[] = clips.map');
		expect(videoCuttingTimelineSource).toContain('onMoveTimelineItems(moves)');
	});
});
