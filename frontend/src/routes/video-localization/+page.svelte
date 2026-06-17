<script lang="ts">
	import { Api } from '$lib/api';
	import type {
		BatchTask,
		Project,
		VideoLocalizationCue,
		VideoLocalizationCueUpdate,
		VideoLocalizationDraft,
		VideoLocalizationOperation,
		VideoLocalizationReferenceClip,
		VideoLocalizationReferenceClipUpdate,
		VideoLocalizationSpeakerCreate
	} from '$lib/api/types';
	import {
		AlertTriangle,
		CheckCircle2,
		FileJson,
		Send,
		Save,
		Trash2,
		UploadCloud
	} from 'lucide-svelte';
	import { onMount } from 'svelte';
	import { downloadJson, downloadText } from './downloads';
	import {
		batchProjectId,
		buildGenerateRequest,
		buildWorkflow,
		createManualCue,
		isActiveOperation,
		sourceAudioUrl,
		stemAudioUrl,
		sortOperations,
		suggestSpeakerSeed,
		type WorkflowStep
	} from './utils';
	import BatchSentenceReviewPanel from './BatchSentenceReviewPanel.svelte';
	import CueEditor from './CueEditor.svelte';
	import DeliveryPanel from './DeliveryPanel.svelte';
	import LocalizationTextImport from './LocalizationTextImport.svelte';
	import PreviewPanel from './PreviewPanel.svelte';
	import ReferencePool from './ReferencePool.svelte';
	import SentenceRail from './SentenceRail.svelte';
	import SpeakerRoster from './SpeakerRoster.svelte';
	import SourceModelPanel from './SourceModelPanel.svelte';
	import WorkflowStrip from './WorkflowStrip.svelte';

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
	let saving = $state(false);
	let resetting = $state(false);
	let savingCue = $state(false);
	let creating = $state(false);
	let creatingSpeaker = $state(false);
	let importing = $state(false);
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
	let loadingBatches = $state(false);
	let referenceUpdatingId = $state('');
	let operationActionId = $state('');
	let ttsBatchId = $state('');
	let localizationImportOpen = $state(false);
	let viewMode = $state<'single' | 'batch'>('single');
	let rightPanelMode = $state<'speakers' | 'references' | 'delivery'>('speakers');
	let videoInput: HTMLInputElement | null = null;
	let operationPollingTimer: ReturnType<typeof setInterval> | null = null;
	let message = $state('');
	let error = $state('');

	const workflow = $derived<WorkflowStep[]>(buildWorkflow(draft));
	const selectedProject = $derived(projects.find((project) => project.project_id === projectId) ?? null);
	const selectedCue = $derived(draft?.cues.find((cue) => cue.cue_id === selectedCueId) ?? draft?.cues[0] ?? null);
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
	const canSubmitCount = $derived(
		draft?.cues.filter((cue) => cue.review_status === 'ready' && cue.audio_route === 'clone_from_source' && cue.tts_recommended_text?.trim() && referenceReady(cue.reference_clip_id)).length ?? 0
	);
	const hasResettableDraft = $derived(Boolean(projectId && draft && hasResettableContent(draft)));

	onMount(() => {
		void loadAsrEngineHealth();
		loadProjects();
		return () => stopOperationPolling();
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
			projects = await Api.projects();
			const urlProjectId = new URLSearchParams(window.location.search).get('project_id');
			projectId = (urlProjectId && projects.some((project) => project.project_id === urlProjectId) ? urlProjectId : projects[0]?.project_id) ?? '';
			if (projectId) await loadDraft(projectId);
		} catch (e) {
			error = (e as Error).message || '加载项目失败';
		} finally {
			loading = false;
		}
	}

	async function loadDraft(nextProjectId = projectId) {
		if (!nextProjectId) {
			draft = null;
			draftOnlyCueIds = [];
			return;
		}
		error = '';
		try {
			draft = await Api.videoLocalizationDraft(nextProjectId);
			const lastEngineId = draft.source_media.metadata?.english_asr_engine_id;
			if (typeof lastEngineId === 'string' && ASR_ENGINE_PRIORITY.includes(lastEngineId as AsrEngineId)) {
				selectedAsrEngineId = lastEngineId as AsrEngineId;
			} else if (!selectedAsrEngineTouched) {
				selectedAsrEngineId = recommendedAsrEngine(asrEngineHealth);
			}
			draftOnlyCueIds = [];
			operations = sortOperations(draft.operations ?? []);
			selectedCueId = draft.cues[0]?.cue_id ?? '';
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

	async function changeProject(event: Event) {
		projectId = (event.currentTarget as HTMLSelectElement).value;
		await loadDraft(projectId);
	}

	async function createLocalizationProject() {
		creating = true;
		error = '';
		try {
			const project = await Api.createProject('视频本土化项目', '外文视频中文配音草稿');
			projects = [...projects, project];
			projectId = project.project_id;
			await loadDraft(project.project_id);
			message = '已创建本土化项目';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '创建项目失败';
		} finally {
			creating = false;
		}
	}

	async function saveDraft() {
		if (!projectId || !draft) return;
		saving = true;
		error = '';
		try {
			draft = await Api.saveVideoLocalizationDraft(projectId, draft);
			draftOnlyCueIds = [];
			message = '草稿已保存';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '保存失败';
		} finally {
			saving = false;
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
			let targetProjectId = projectId;
			if (!targetProjectId) {
				const projectName = file.name.replace(/\.[^.]+$/, '') || '视频本土化项目';
				const project = await Api.createProject(projectName, '外文视频中文配音草稿');
				projects = [...projects, project];
				projectId = project.project_id;
				targetProjectId = project.project_id;
			}
			draft = await Api.importVideoLocalizationSource(targetProjectId, file);
			message = '视频已导入';
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

	async function exportJson() {
		if (!projectId) return;
		error = '';
		try {
			const data = await Api.exportVideoLocalizationDraft(projectId);
			downloadJson(`${projectId}-video-localization.json`, data);
			message = 'JSON 已导出';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导出失败';
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
	}

	function selectCue(cueId: string) {
		selectedCueId = cueId;
	}

	function updateSelectedCue(patch: Partial<VideoLocalizationCue>) {
		if (!draft || !selectedCue) return;
		draft.cues = draft.cues.map((cue) => (cue.cue_id === selectedCue.cue_id ? { ...cue, ...patch } : cue));
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
		localizationImportOpen = false;
		message = `已应用 ${Math.min(lines.length, draft.cues.length)} 行中文稿，请校对后保存草稿`;
		setTimeout(() => (message = ''), 2400);
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

	function sendSelectedCueToGenerate() {
		if (!selectedCue || !cueCanSendToGenerate(selectedCue)) return;
		const reference = referenceForCue(selectedCue);
		const request = buildGenerateRequest(projectId, selectedCue, reference);
		sessionStorage.setItem('voice-studio-history-reuse', JSON.stringify(request));
		window.location.href = '/generate';
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
		draft = await Api.videoLocalizationDraft(projectId);
		draftOnlyCueIds = [];
		operations = sortOperations(draft.operations ?? operations);
		if (!selectedCueId && draft.cues[0]) selectedCueId = draft.cues[0].cue_id;
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

<svelte:head>
	<title>视频本土化配音 - Voice Studio</title>
</svelte:head>

<main class="page video-localization-page">
	<header class="page-head localization-head">
		<div>
			<h1>视频本土化配音</h1>
			<p class="muted">从英文视频整理可审校的中文字幕、TTS 台词、参考音色和批量合成 JSON。</p>
		</div>
		<div class="row head-actions">
			<select class="project-select" value={projectId} onchange={changeProject} aria-label="选择项目" disabled={loading || !projects.length}>
				{#each projects as project}
					<option value={project.project_id}>{project.name}</option>
				{/each}
			</select>
			{#if !projects.length}
				<button class="btn" type="button" onclick={createLocalizationProject} disabled={creating}>{creating ? '创建中' : '新建本土化项目'}</button>
			{/if}
			<input bind:this={videoInput} data-video-localization-file class="visually-hidden" type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.m4v,.webm,.mkv" onchange={(event) => importVideoFile(event.currentTarget.files?.[0])} />
			<button class="btn" type="button" onclick={() => videoInput?.click()} disabled={importing}>
				<UploadCloud size={15} /> {importing ? '导入中' : projectId ? '导入视频' : '导入视频并新建项目'}
			</button>
			<button class="btn btn-danger" type="button" onclick={resetCurrentTask} disabled={!hasResettableDraft || resetting}>
				<Trash2 size={15} /> {resetting ? '清空中' : '清空当前任务'}
			</button>
			<button class="btn" type="button" onclick={saveDraft} disabled={!draft || saving}><Save size={15} /> {saving ? '保存中' : '保存草稿'}</button>
			<button class="btn" type="button" onclick={exportJson} disabled={!draft}><FileJson size={15} /> 导出 JSON</button>
			<a class="btn primary" href="/generate"><Send size={15} /> 发送到语音合成</a>
		</div>
	</header>

	{#if error || message}
		<div class={`notice ${error ? 'fail' : 'ok'}`}>{error || message}</div>
	{/if}

	<WorkflowStrip steps={workflow} />

	<section class="view-switcher">
		<div class="segmented-tabs">
			<button class:active={viewMode === 'single'} type="button" onclick={() => (viewMode = 'single')}>单句精修</button>
			<button class:active={viewMode === 'batch'} type="button" onclick={() => (viewMode = 'batch')}>批量逐句审校</button>
		</div>
		<div class="row compact">
			<span class="badge ok">{readyCount} 可生成</span>
			<span class="badge warn">{reviewCount} 待校对</span>
			<span class="badge fail">{blockedCount} 阻断</span>
		</div>
	</section>

	{#if viewMode === 'single'}
		<section class="single-shell">
			<aside class="stack single-left">
				<SourceModelPanel
					{draft}
					{selectedProject}
					{latestOperation}
					{hasActiveOperation}
					{operationActionId}
					{extractingAudio}
					{separatingStems}
					{transcribingAsr}
					{localizingDraft}
					{selectedAsrEngineId}
					{asrEngineHealth}
					onImportVideo={importVideoFile}
					onExtractAudio={extractSourceAudio}
					onSeparateStems={separateStems}
					onTranscribeEnglish={transcribeEnglishSource}
					onLocalizeDraft={localizeChineseDraft}
					onSelectedAsrEngineIdChange={handleAsrEngineChange}
					onCancelOperation={cancelOperation}
					onRetryOperation={retryOperation}
					{operationFor}
					{operationBusy}
				/>

				<section class="panel sentence-progress-panel">
					<div class="section-title">
						<div>
							<h2>句子进度</h2>
							<p class="muted">把工作拆成一句一句，当前页只专注一条。</p>
						</div>
					</div>
					<div class="progress-grid">
						<div><strong>{draft?.cues.length ?? 0}</strong><span>总句数</span></div>
						<div><strong>{localizedCount}</strong><span>已打样</span></div>
						<div><strong>{draft?.reference_clips.filter((clip) => clip.cleanliness === 'clean').length ?? 0}</strong><span>干净参考音</span></div>
						<div><strong>{generatedCount}</strong><span>已生成 TTS</span></div>
					</div>
					<div class="quality-bar">
						<span class={`badge ${draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) ? 'ok' : 'warn'}`}><CheckCircle2 size={13} /> ASR 覆盖</span>
						<span class={`badge ${localizedCount ? 'ok' : 'warn'}`}><CheckCircle2 size={13} /> 中文打样</span>
						<span class={`badge ${draft?.reference_clips.some((clip) => clip.asr_status === 'verified') ? 'ok' : 'warn'}`}><CheckCircle2 size={13} /> 参考音 ASR</span>
					</div>
					<div class="row progress-actions">
						<button class="mini-btn" type="button" onclick={() => (localizationImportOpen = !localizationImportOpen)} disabled={!draft?.cues.length}>
							{localizationImportOpen ? '收起中文稿' : '粘贴中文稿'}
						</button>
						<button class="mini-btn" type="button" onclick={addCue} disabled={!draft}>新增句子</button>
					</div>
				</section>
			</aside>

			<section class="stack main-workbench">
				<PreviewPanel {selectedCue} hasCleanReference={Boolean(draft?.reference_clips.some((clip) => clip.cleanliness === 'clean'))} {draft} {projectId} />
				<LocalizationTextImport open={localizationImportOpen} cueCount={draft?.cues.length ?? 0} onApply={applyLocalizationText} onClose={() => (localizationImportOpen = false)} />
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
				<CueEditor
					{selectedCue}
					speakers={draft?.speakers ?? []}
					referenceClips={draft?.reference_clips ?? []}
					{projectId}
					timelineAudioSrc={cueTimelineAudioSrc}
					timelineAudioLabel={cueTimelineAudioLabel}
					timelineDurationMs={cueTimelineDurationMs}
					{savingCue}
					{speakerLabel}
					canSendToGenerate={cueCanSendToGenerate(selectedCue)}
					onUpdateCue={updateSelectedCue}
					onUpdateCueTime={updateSelectedCueTime}
					onSave={saveSelectedCue}
					onSend={sendSelectedCueToGenerate}
				/>
			</section>

			<aside class="stack single-right">
				<SentenceRail cues={draft?.cues ?? []} {selectedCueId} {speakerLabel} onSelect={selectCue} />

				<section class="context-panel">
					<div class="segmented-tabs context-tabs">
						<button class:active={rightPanelMode === 'speakers'} type="button" onclick={() => (rightPanelMode = 'speakers')}>说话人</button>
						<button class:active={rightPanelMode === 'references'} type="button" onclick={() => (rightPanelMode = 'references')}>参考音</button>
						<button class:active={rightPanelMode === 'delivery'} type="button" onclick={() => (rightPanelMode = 'delivery')}>批量与交付</button>
					</div>
					{#if rightPanelMode === 'speakers'}
						<SpeakerRoster
							speakers={draft?.speakers ?? []}
							{selectedCue}
							{creatingSpeaker}
							suggestedSpeakerId={speakerSeed.speaker_id}
							suggestedDisplayName={speakerSeed.display_name}
							onCreateSpeaker={createSpeaker}
							onAssignToCue={assignSpeakerToCue}
						/>
					{:else if rightPanelMode === 'references'}
						<ReferencePool
							clips={draft?.reference_clips ?? []}
							operation={operationFor('reference_clips')}
							{creatingReferences}
							canCreateCandidates={draft?.stems.separation_status === 'completed' && !operationBusy('reference_clips')}
							{referenceUpdatingId}
							{projectId}
							{speakerLabel}
							onGenerateCandidates={createReferenceCandidates}
							onMarkClean={markReferenceClean}
							onMarkBlocked={markReferenceBlocked}
							onMarkNeedsReview={markReferenceNeedsReview}
						/>
					{:else}
						<DeliveryPanel
							qualityGate={draft?.quality_gate}
							{canSubmitCount}
							{generatedCount}
							{projectBatches}
							{ttsBatchId}
							{loadingBatches}
							{submittingBatch}
							{syncingBatch}
							hasDraft={Boolean(draft)}
							canExportBilingual={Boolean(draft?.cues.some((cue) => cue.start_ms !== null && cue.end_ms !== null))}
							onSubmitBatch={submitBatchTts}
							onSyncBatch={syncBatchTtsResults}
							onExportJson={exportJson}
							onExportReadiness={exportReadinessAudit}
							onExportBilingual={exportBilingualSrt}
							onTtsBatchIdChange={(batchId) => (ttsBatchId = batchId)}
						/>
					{/if}
				</section>
			</aside>
		</section>
	{:else}
		<section class="batch-shell">
			<aside class="stack batch-left">
				<PreviewPanel {selectedCue} hasCleanReference={Boolean(draft?.reference_clips.some((clip) => clip.cleanliness === 'clean'))} {draft} {projectId} />
				<DeliveryPanel
					qualityGate={draft?.quality_gate}
					{canSubmitCount}
					{generatedCount}
					{projectBatches}
					{ttsBatchId}
					{loadingBatches}
					{submittingBatch}
					{syncingBatch}
					hasDraft={Boolean(draft)}
					canExportBilingual={Boolean(draft?.cues.some((cue) => cue.start_ms !== null && cue.end_ms !== null))}
					onSubmitBatch={submitBatchTts}
					onSyncBatch={syncBatchTtsResults}
					onExportJson={exportJson}
					onExportReadiness={exportReadinessAudit}
					onExportBilingual={exportBilingualSrt}
					onTtsBatchIdChange={(batchId) => (ttsBatchId = batchId)}
				/>
			</aside>
			<section class="batch-main">
				<BatchSentenceReviewPanel
					cues={draft?.cues ?? []}
					speakers={draft?.speakers ?? []}
					referenceClips={draft?.reference_clips ?? []}
					{selectedCueId}
					{projectId}
					on:select={(event) => {
						selectCue(event.detail.cueId);
					}}
				/>
			</section>
		</section>
	{/if}
</main>

<style>
	.video-localization-page {
		max-width: 1720px;
		padding-bottom: 64px;
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
		margin: -4px 0 14px;
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

	.section-title h2,
	.section-title p {
		margin: 0;
	}

	.mini-btn {
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #15181d;
		color: var(--text);
		padding: 5px 8px;
		font-size: 12px;
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
		margin-bottom: 10px;
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

	.segmented-tabs button {
		border: 0;
		background: transparent;
		color: var(--muted);
		padding: 8px 14px;
		border-radius: 7px;
		font-size: 12px;
		cursor: pointer;
	}

	.segmented-tabs button.active {
		background: #1a2432;
		color: #e7f0fd;
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

	.progress-grid div {
		display: grid;
		gap: 4px;
		padding: 10px;
		border-radius: 8px;
		border: 1px solid var(--line);
		background: #101215;
	}

	.progress-grid strong {
		font-size: 18px;
	}

	.progress-grid span {
		color: var(--muted);
		font-size: 12px;
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

	@media (max-width: 1100px) {
		.single-shell,
		.batch-shell,
		.single-right {
			grid-template-columns: 1fr;
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
	}
</style>
