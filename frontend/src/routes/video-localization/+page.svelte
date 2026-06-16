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
		VideoLocalizationReferenceClipUpdate
	} from '$lib/api/types';
	import {
		AlertTriangle,
		CheckCircle2,
		FileJson,
		Film,
		Send,
		Save,
		UploadCloud
	} from 'lucide-svelte';
	import { onMount } from 'svelte';
	import { downloadJson, downloadText } from './downloads';
	import {
		batchProjectId,
		buildGenerateRequest,
		buildWorkflow,
		createManualCue,
		durationLabel,
		isActiveOperation,
		operationBadgeClass,
		operationStatusLabel,
		sortOperations,
		type WorkflowStep
	} from './utils';
	import CueEditor from './CueEditor.svelte';
	import CueTable from './CueTable.svelte';
	import DeliveryPanel from './DeliveryPanel.svelte';
	import PreviewPanel from './PreviewPanel.svelte';
	import ReferencePool from './ReferencePool.svelte';
	import WorkflowStrip from './WorkflowStrip.svelte';

	let projects = $state<Project[]>([]);
	let batches = $state<BatchTask[]>([]);
	let operations = $state<VideoLocalizationOperation[]>([]);
	let projectId = $state('');
	let draft = $state<VideoLocalizationDraft | null>(null);
	let selectedCueId = $state('');
	let loading = $state(true);
	let saving = $state(false);
	let savingCue = $state(false);
	let creating = $state(false);
	let importing = $state(false);
	let extractingAudio = $state(false);
	let separatingStems = $state(false);
	let transcribingAsr = $state(false);
	let creatingReferences = $state(false);
	let localizingZh = $state(false);
	let submittingBatch = $state(false);
	let syncingBatch = $state(false);
	let loadingBatches = $state(false);
	let referenceUpdatingId = $state('');
	let operationActionId = $state('');
	let ttsBatchId = $state('');
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
	const projectBatches = $derived(batches.filter((batch) => batchProjectId(batch) === projectId));
	const hasActiveOperation = $derived(operations.some((operation) => isActiveOperation(operation)));
	const latestOperation = $derived(operations[0] ?? null);
	const canSubmitCount = $derived(
		draft?.cues.filter((cue) => cue.review_status === 'ready' && cue.audio_route === 'clone_from_source' && cue.tts_recommended_text?.trim() && referenceReady(cue.reference_clip_id)).length ?? 0
	);

	onMount(() => {
		loadProjects();
		return () => stopOperationPolling();
	});

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
			return;
		}
		error = '';
		try {
			draft = await Api.videoLocalizationDraft(nextProjectId);
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
			message = '草稿已保存';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '保存失败';
		} finally {
			saving = false;
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
			await submitMediaOperation('english_asr', '英文字幕转录任务已开始', { engine_id: 'faster-whisper-turbo' });
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

	async function generateChineseDraft() {
		if (!projectId || !draft?.cues.some((cue) => cue.en_subtitle_text?.trim())) return;
		localizingZh = true;
		error = '';
		try {
			draft = await Api.generateVideoLocalizationChineseDraft(projectId);
			message = '中文字幕与 TTS 台词草稿已生成';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '生成中文草稿失败';
		} finally {
			localizingZh = false;
		}
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
		selectedCueId = cue.cue_id;
	}

	function updateSelectedCue(patch: Partial<VideoLocalizationCue>) {
		if (!draft || !selectedCue) return;
		draft.cues = draft.cues.map((cue) => (cue.cue_id === selectedCue.cue_id ? { ...cue, ...patch } : cue));
	}

	function updateSelectedCueTime(field: 'start_ms' | 'end_ms', value: string) {
		const normalized = value.trim();
		updateSelectedCue({ [field]: normalized ? Math.max(0, Number(normalized)) : null });
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
		operations = sortOperations(draft.operations ?? operations);
		if (!selectedCueId && draft.cues[0]) selectedCueId = draft.cues[0].cue_id;
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
			<p class="muted">从英文视频生成可审校的中文字幕、TTS 台词、参考音色和批量合成 JSON。</p>
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
			<input bind:this={videoInput} class="visually-hidden" type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.m4v,.webm,.mkv" onchange={(event) => importVideoFile(event.currentTarget.files?.[0])} />
			<button class="btn" type="button" onclick={() => videoInput?.click()} disabled={importing}>
				<UploadCloud size={15} /> {importing ? '导入中' : projectId ? '导入视频' : '导入视频并新建项目'}
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

	<section class="localization-shell">
		<div class="stack left-rail">
			<section class="panel import-panel">
				<div class="section-title">
					<h2>素材与模型</h2>
					<span class={`badge ${draft?.updated_at ? 'ok' : ''}`}>{draft?.updated_at ? '草稿已保存' : '等待保存'}</span>
				</div>
				<div class="drop-target">
					<Film size={22} />
					<div>
						<strong>{draft?.source_media.filename || '尚未导入视频'}</strong>
						<p class="muted">
							{durationLabel(draft?.source_media.duration_ms)}
							{#if draft?.source_media.width && draft?.source_media.height}
								· {draft.source_media.width}x{draft.source_media.height}
							{/if}
							{#if selectedProject}
								· {selectedProject.name}
							{/if}
						</p>
					</div>
				</div>
				<div class="model-list">
					<div class="model-row">
						<span>ASR</span>
						<strong>faster-whisper-turbo</strong>
						<span class={`badge ${operationBadgeClass(operationFor('english_asr')) || (draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) ? 'ok' : '')}`}>
							{operationBusy('english_asr') ? operationStatusLabel(operationFor('english_asr')) : draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) ? '有草稿' : operationStatusLabel(operationFor('english_asr'))}
						</span>
						<button class="mini-btn" type="button" onclick={transcribeEnglishSource} disabled={!(draft?.source_media.audio_path || draft?.stems.original_audio_path) || transcribingAsr || operationBusy('english_asr')}>
							{operationBusy('english_asr') || transcribingAsr ? '转录中' : '转录'}
						</button>
					</div>
					<div class="model-row">
						<span>备用</span>
						<strong>qwen3-asr-mlx / mimo-v2.5</strong>
						<span class="badge">可选</span>
					</div>
					<div class="model-row">
						<span>分离</span>
						<strong>vocals_clean + background</strong>
						<span class={`badge ${operationBadgeClass(operationFor('stems')) || (draft?.stems.separation_status === 'completed' ? 'ok' : '')}`}>
							{operationBusy('stems') ? operationStatusLabel(operationFor('stems')) : draft?.stems.separation_status === 'completed' ? '已完成' : operationStatusLabel(operationFor('stems'))}
						</span>
						<button class="mini-btn" type="button" onclick={separateStems} disabled={!(draft?.source_media.audio_path || draft?.stems.original_audio_path) || separatingStems || operationBusy('stems')}>
							{operationBusy('stems') || separatingStems ? '分离中' : '分离'}
						</button>
					</div>
					<div class="model-row">
						<span>源音</span>
						<strong>{draft?.source_media.audio_path ? 'source.wav 已记录' : '等待抽取'}</strong>
						<span class={`badge ${operationBadgeClass(operationFor('source_audio')) || (draft?.source_media.audio_path ? 'ok' : '')}`}>
							{operationBusy('source_audio') ? operationStatusLabel(operationFor('source_audio')) : draft?.source_media.audio_path ? '已完成' : operationStatusLabel(operationFor('source_audio'))}
						</span>
						<button class="mini-btn" type="button" onclick={extractSourceAudio} disabled={!draft?.source_media.video_path || extractingAudio || operationBusy('source_audio')}>
							{operationBusy('source_audio') || extractingAudio ? '抽取中' : '抽取'}
						</button>
					</div>
				</div>
				{#if latestOperation}
					<div class:active={hasActiveOperation} class="operation-note">
						<span>
							最近任务：{latestOperation.label || latestOperation.kind} · {operationStatusLabel(latestOperation)}
							{#if latestOperation.error_message}
								· {latestOperation.error_message}
							{/if}
						</span>
						{#if isActiveOperation(latestOperation)}
							<button class="mini-btn" type="button" onclick={() => cancelOperation(latestOperation)} disabled={operationActionId === latestOperation.operation_id}>
								{operationActionId === latestOperation.operation_id ? '取消中' : '取消'}
							</button>
						{:else if latestOperation.status === 'failed' || latestOperation.status === 'cancelled'}
							<button class="mini-btn" type="button" onclick={() => retryOperation(latestOperation)} disabled={operationActionId === latestOperation.operation_id || hasActiveOperation}>
								{operationActionId === latestOperation.operation_id ? '重试中' : '重试'}
							</button>
						{/if}
					</div>
				{/if}
			</section>

			<PreviewPanel {selectedCue} hasCleanReference={Boolean(draft?.reference_clips.some((clip) => clip.cleanliness === 'clean'))} />
		</div>

		<section class="panel cue-panel">
			<div class="section-title">
				<div>
					<h2>cue 审校表</h2>
					<p class="muted">三轨文本独立维护，TTS 台词会保留数字读法和停顿。</p>
				</div>
				<div class="row">
					<button class="mini-btn" type="button" onclick={generateChineseDraft} disabled={!draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) || localizingZh}>
						{localizingZh ? '生成中' : '生成中文草稿'}
					</button>
					<span class="badge ok">{readyCount} 可生成</span>
					<span class="badge warn">{reviewCount} 待校对</span>
					<span class="badge fail">{blockedCount} 阻断</span>
				</div>
			</div>

			<div class="quality-bar">
				<span class={`badge ${draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) ? 'ok' : 'warn'}`}><CheckCircle2 size={13} /> ASR 覆盖</span>
				<span class={`badge ${draft?.reference_clips.some((clip) => clip.asr_status === 'verified') ? 'ok' : 'warn'}`}><CheckCircle2 size={13} /> 参考音 ASR</span>
				{#each draft?.quality_gate.warnings ?? [] as issue}
					<span class="badge warn"><AlertTriangle size={13} /> {issue.message}</span>
				{/each}
				{#each draft?.quality_gate.blockers ?? [] as issue}
					<span class="badge fail"><AlertTriangle size={13} /> {issue.message}</span>
				{/each}
				{#if !(draft?.quality_gate.warnings.length || draft?.quality_gate.blockers.length)}
					<span class="badge">暂无质量门结果</span>
				{/if}
			</div>

			<CueTable cues={draft?.cues ?? []} {selectedCueId} {speakerLabel} onSelect={(cueId) => (selectedCueId = cueId)} />
			<div class="row table-actions">
				<button class="btn" type="button" onclick={addCue} disabled={!draft}>新增 cue</button>
			</div>
		</section>

		<aside class="stack right-editor">
			<CueEditor
				{selectedCue}
				speakers={draft?.speakers ?? []}
				referenceClips={draft?.reference_clips ?? []}
				{projectId}
				{savingCue}
				{speakerLabel}
				canSendToGenerate={cueCanSendToGenerate(selectedCue)}
				onUpdateCue={updateSelectedCue}
				onUpdateCueTime={updateSelectedCueTime}
				onSave={saveSelectedCue}
				onSend={sendSelectedCueToGenerate}
			/>

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
	</section>
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

	.localization-shell {
		display: grid;
		grid-template-columns: minmax(310px, 0.85fr) minmax(460px, 1.35fr) minmax(310px, 0.86fr);
		gap: 14px;
		align-items: start;
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

	.drop-target {
		display: grid;
		grid-template-columns: 38px minmax(0, 1fr);
		gap: 10px;
		align-items: center;
		border: 1px dashed var(--line);
		border-radius: 7px;
		padding: 12px;
		background: #101215;
	}

	.model-list {
		display: grid;
		gap: 8px;
		margin-top: 12px;
	}

	.model-row {
		display: grid;
		grid-template-columns: 44px minmax(0, 1fr) auto auto;
		gap: 8px;
		align-items: center;
		font-size: 12px;
	}

	.model-row > span:first-child {
		color: var(--muted);
	}

	.operation-note {
		margin: 10px 0 0;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.45;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}

	.operation-note.active {
		color: #9cc9ff;
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

	.table-actions {
		margin-top: 12px;
		justify-content: flex-end;
	}

	@media (max-width: 1500px) {
		.localization-shell {
			grid-template-columns: minmax(320px, 0.95fr) minmax(460px, 1.25fr);
		}

		.right-editor {
			grid-column: 1 / -1;
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}

	@media (max-width: 900px) {
		.localization-head {
			align-items: flex-start;
		}

		.workflow-strip,
		.localization-shell,
		.right-editor {
			grid-template-columns: 1fr;
		}

		.head-actions {
			justify-content: flex-start;
		}
	}
</style>
