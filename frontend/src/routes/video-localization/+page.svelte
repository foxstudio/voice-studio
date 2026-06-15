<script lang="ts">
	import { Api } from '$lib/api';
	import type { BatchTask, GenerateRequest, Project, VideoLocalizationCue, VideoLocalizationDraft } from '$lib/api/types';
	import {
		AlertTriangle,
		CheckCircle2,
		Download,
		FileJson,
		Film,
		Languages,
		Lock,
		Mic2,
		Play,
		Send,
		Save,
		UploadCloud,
		AudioLines,
		Wand2
	} from 'lucide-svelte';
	import { onMount } from 'svelte';

	type WorkflowStep = {
		label: string;
		status: 'done' | 'active' | 'blocked' | 'pending';
	};

	let projects = $state<Project[]>([]);
	let batches = $state<BatchTask[]>([]);
	let projectId = $state('');
	let draft = $state<VideoLocalizationDraft | null>(null);
	let selectedCueId = $state('');
	let loading = $state(true);
	let saving = $state(false);
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
	let ttsBatchId = $state('');
	let videoInput: HTMLInputElement | null = null;
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
	const canSubmitCount = $derived(
		draft?.cues.filter((cue) => cue.review_status === 'ready' && cue.audio_route === 'clone_from_source' && cue.tts_recommended_text?.trim() && referenceReady(cue.reference_clip_id)).length ?? 0
	);

	onMount(() => {
		loadProjects();
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
			selectedCueId = draft.cues[0]?.cue_id ?? '';
			await loadBatches();
		} catch (e) {
			error = (e as Error).message || '加载草稿失败';
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
		if (!projectId) {
			error = '请先选择或新建项目';
			return;
		}
		importing = true;
		error = '';
		try {
			draft = await Api.importVideoLocalizationSource(projectId, file);
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
			draft = await Api.extractVideoLocalizationAudio(projectId);
			message = '源音轨已抽取';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '抽取源音轨失败';
		} finally {
			extractingAudio = false;
		}
	}

	async function transcribeEnglishSource() {
		if (!projectId || !(draft?.source_media.audio_path || draft?.stems.original_audio_path)) return;
		transcribingAsr = true;
		error = '';
		try {
			draft = await Api.transcribeVideoLocalizationEnglish(projectId);
			selectedCueId = draft.cues[0]?.cue_id ?? '';
			message = '英文字幕草稿已生成';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '英文 ASR 失败';
		} finally {
			transcribingAsr = false;
		}
	}

	async function separateStems() {
		if (!projectId || !(draft?.source_media.audio_path || draft?.stems.original_audio_path)) return;
		separatingStems = true;
		error = '';
		try {
			draft = await Api.separateVideoLocalizationStems(projectId);
			message = '人声与背景声已分离';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '人声分离失败';
		} finally {
			separatingStems = false;
		}
	}

	async function createReferenceCandidates() {
		if (!projectId || draft?.stems.separation_status !== 'completed') return;
		creatingReferences = true;
		error = '';
		try {
			draft = await Api.createVideoLocalizationReferences(projectId);
			message = '参考音候选已生成';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '生成参考音候选失败';
		} finally {
			creatingReferences = false;
		}
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
			const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = `${projectId}-video-localization.json`;
			link.click();
			URL.revokeObjectURL(url);
			message = 'JSON 已导出';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导出失败';
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
			const blob = new Blob([text], { type: 'application/x-subrip;charset=utf-8' });
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = `${projectId}-video-localization-bilingual.srt`;
			link.click();
			URL.revokeObjectURL(url);
			message = '中英字幕草稿已导出';
			setTimeout(() => (message = ''), 1800);
		} catch (e) {
			error = (e as Error).message || '导出字幕失败';
		}
	}

	function addCue() {
		if (!draft) return;
		const index = draft.cues.length + 1;
		const cue: VideoLocalizationCue = {
			cue_id: `cue_${String(index).padStart(4, '0')}`,
			speaker_id: draft.speakers[0]?.speaker_id ?? null,
			start_ms: null,
			end_ms: null,
			audio_route: 'manual_review',
			en_subtitle_text: '',
			zh_localized_subtitle_text: '',
			tts_recommended_text: '',
			reference_clip_id: null,
			tts_result_id: null,
			tts_audio_path: null,
			tts_batch_task_id: null,
			tts_batch_status: null,
			tts_batch_error: null,
			tts_attempted_at: null,
			source_duration_ms: null,
			generated_duration_ms: null,
			review_status: 'needs_review',
			quality_flags: ['手动新增'],
			notes: null
		};
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

	function buildWorkflow(current: VideoLocalizationDraft | null): WorkflowStep[] {
		const hasSource = Boolean(current?.source_media.filename || current?.source_media.video_path);
		const hasSourceAudio = Boolean(current?.source_media.audio_path || current?.stems.original_audio_path);
		const stemsReady = current?.stems.separation_status === 'completed';
		const hasAsr = Boolean(current?.cues.some((cue) => cue.en_subtitle_text?.trim()));
		const hasSpeakers = Boolean(current?.speakers.length);
		const hasReviewed = Boolean(current?.cues.some((cue) => cue.review_status === 'ready' || cue.review_status === 'locked'));
		const hasTts = Boolean(current?.cues.some((cue) => cue.tts_audio_path || cue.tts_result_id));
		const readyForTts = Boolean(current?.cues.some((cue) => cue.review_status === 'ready' && cue.tts_recommended_text?.trim()));
		const blocked = current?.quality_gate.status === 'blocked';
		return [
			{ label: '导入', status: hasSource ? 'done' : 'active' },
			{ label: '人声分离', status: stemsReady ? 'done' : hasSource ? 'active' : 'pending' },
			{ label: '英文 ASR', status: hasAsr ? 'done' : hasSourceAudio ? 'active' : 'pending' },
			{ label: '说话人', status: hasSpeakers ? 'done' : hasAsr ? 'active' : 'pending' },
			{ label: '人工校对', status: blocked ? 'blocked' : hasReviewed ? 'active' : 'pending' },
			{ label: 'TTS', status: hasTts ? 'done' : readyForTts ? 'active' : 'pending' },
			{ label: 'JSON', status: current ? 'active' : 'pending' }
		];
	}

	function statusLabel(status: VideoLocalizationCue['review_status']) {
		return {
			ready: '可生成',
			needs_review: '待校对',
			blocked: '阻断',
			locked: '已锁定'
		}[status];
	}

	function gateLabel(status: VideoLocalizationDraft['quality_gate']['status'] | undefined) {
		return {
			pass: '质量门通过',
			warning: '存在警告',
			blocked: '存在阻断',
			unknown: '未检查'
		}[status ?? 'unknown'];
	}

	function gateBadgeClass(status: VideoLocalizationDraft['quality_gate']['status'] | undefined) {
		if (status === 'pass') return 'ok';
		if (status === 'blocked') return 'fail';
		if (status === 'warning') return 'warn';
		return '';
	}

	function speakerLabel(speakerId: string | null | undefined) {
		if (!speakerId) return '未选';
		const speaker = draft?.speakers.find((item) => item.speaker_id === speakerId);
		return speaker?.display_name || speakerId;
	}

	function speakerColor(speakerId: string | null | undefined) {
		const colors = ['#4f9cf9', '#42c49b', '#e4ad42', '#b58cff', '#ff8c8c'];
		const index = Math.abs([...(speakerId ?? 'unknown')].reduce((sum, char) => sum + char.charCodeAt(0), 0)) % colors.length;
		return colors[index];
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
		const request: GenerateRequest = {
			text: selectedCue.tts_recommended_text?.trim() ?? '',
			engine_id: 'indextts-v2',
			source: 'video_localization',
			project_id: projectId,
			segment_id: selectedCue.cue_id,
			voice_id: null,
			voice_source: 'reference_audio',
			reference_audio_path: reference?.audio_path ?? null,
			reference_audio_license_status: '本土化',
			reference_audio_tags: ['视频本土化', '本土化', selectedCue.speaker_id ?? 'unknown'],
			ref_text: reference?.asr_text || selectedCue.en_subtitle_text || null,
			custom_reference_source_audio_path: reference?.audio_path ?? null,
			custom_reference_source_duration_ms: reference?.duration_ms ?? null,
			custom_reference_trim_start_ms: null,
			custom_reference_trim_end_ms: null,
			language: 'zh',
			emotion_mode: 'follow_reference',
			emotion: null,
			emotion_values: null,
			emotion_text: null,
			style_instruction: null,
			voice_design_prompt: null,
			optimize_text_preview: false,
			mimo_voice: null,
			speaker_id: null,
			prompt: null,
			nfe_step: 32,
			cfg_strength: 2,
			target_rms: 0.1,
			cross_fade_duration: 0.15,
			sway_sampling_coef: -1,
			fix_duration: 0,
			remove_silence: false,
			emo_alpha: 0.6,
			speed: 1,
			temperature: 0.8,
			top_p: 0.8,
			top_k: 30,
			repetition_penalty: 10,
			seed: null,
			max_mel_tokens: 1500,
			max_text_tokens_per_segment: 120,
			interval_silence: 200,
			segment_overlap_ms: 50,
			diffusion_steps: 25,
			cfg_rate: 0.7,
			guidance_scale: 2,
			duration: 0,
			output_format: 'wav'
		};
		sessionStorage.setItem('voice-studio-history-reuse', JSON.stringify(request));
		window.location.href = '/generate';
	}

	function msLabel(ms: number | null | undefined) {
		if (ms === null || ms === undefined) return '--:--.--';
		const totalSeconds = ms / 1000;
		const minutes = Math.floor(totalSeconds / 60);
		const seconds = totalSeconds % 60;
		return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(2).padStart(5, '0')}`;
	}

	function timeLabel(cue: VideoLocalizationCue) {
		return `${msLabel(cue.start_ms)} - ${msLabel(cue.end_ms)}`;
	}

	function ttsAudioUrl(cue: VideoLocalizationCue) {
		return projectId && cue.tts_audio_path ? `/api/projects/${projectId}/video-localization/cues/${cue.cue_id}/tts-audio` : '';
	}

	function sourceCueAudioUrl(cue: VideoLocalizationCue) {
		return projectId && cue.start_ms !== null && cue.end_ms !== null ? `/api/projects/${projectId}/video-localization/cues/${cue.cue_id}/source-audio` : '';
	}

	function durationLabel(ms: number | null | undefined) {
		if (!ms) return '未知';
		return `${(ms / 1000).toFixed(1)}s`;
	}

	function batchProjectId(batch: BatchTask) {
		const parameters = batch.parameters?.parameters;
		if (!parameters || typeof parameters !== 'object') return '';
		const value = (parameters as Record<string, unknown>).project_id;
		return typeof value === 'string' ? value : '';
	}

	function batchOptionLabel(batch: BatchTask) {
		const success = batch.segments.filter((segment) => segment.status === 'success').length;
		const failed = batch.segments.filter((segment) => segment.status === 'failed').length;
		return `${batch.batch_task_id} · ${ttsBatchLabel(batch.status)} · 成功 ${success}/${batch.segments.length}${failed ? ` · 失败 ${failed}` : ''}`;
	}

	function ttsBatchLabel(status: string | null | undefined) {
		return {
			queued: '队列中',
			running: '生成中',
			postprocessing: '处理中',
			success: '已生成',
			failed: '失败',
			cancelled: '已取消',
			retrying: '重试中'
		}[status ?? ''] ?? '待生成';
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
			<button class="btn" type="button" onclick={() => videoInput?.click()} disabled={!projectId || importing}>
				<UploadCloud size={15} /> {importing ? '导入中' : '导入视频'}
			</button>
			<button class="btn" type="button" onclick={saveDraft} disabled={!draft || saving}><Save size={15} /> {saving ? '保存中' : '保存草稿'}</button>
			<button class="btn" type="button" onclick={exportJson} disabled={!draft}><FileJson size={15} /> 导出 JSON</button>
			<a class="btn primary" href="/generate"><Send size={15} /> 发送到语音合成</a>
		</div>
	</header>

	{#if error || message}
		<div class={`notice ${error ? 'fail' : 'ok'}`}>{error || message}</div>
	{/if}

	<section class="workflow-strip" aria-label="视频本土化流程">
		{#each workflow as step}
			<div class={`workflow-step ${step.status}`}>
				<span>{step.label}</span>
			</div>
		{/each}
	</section>

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
						<span class={`badge ${draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) ? 'ok' : ''}`}>{draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) ? '有草稿' : '待转录'}</span>
						<button class="mini-btn" type="button" onclick={transcribeEnglishSource} disabled={!(draft?.source_media.audio_path || draft?.stems.original_audio_path) || transcribingAsr}>
							{transcribingAsr ? '转录中' : '转录'}
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
						<span class={`badge ${draft?.stems.separation_status === 'completed' ? 'ok' : ''}`}>{draft?.stems.separation_status || 'pending'}</span>
						<button class="mini-btn" type="button" onclick={separateStems} disabled={!(draft?.source_media.audio_path || draft?.stems.original_audio_path) || separatingStems}>
							{separatingStems ? '分离中' : '分离'}
						</button>
					</div>
					<div class="model-row">
						<span>源音</span>
						<strong>{draft?.source_media.audio_path ? 'source.wav 已记录' : '等待抽取'}</strong>
						<button class="mini-btn" type="button" onclick={extractSourceAudio} disabled={!draft?.source_media.video_path || extractingAudio}>
							{extractingAudio ? '抽取中' : '抽取'}
						</button>
					</div>
				</div>
			</section>

			<section class="panel preview-panel">
				<div class="video-preview">
					<div class="video-glow"></div>
					<div class="play-button"><span><Play size={24} /></span></div>
					<div class="subtitle-overlay">
						<p>{selectedCue?.zh_localized_subtitle_text || '中文字幕将在这里预览'}</p>
						<span>{selectedCue?.en_subtitle_text || 'English subtitle preview'}</span>
					</div>
				</div>
				<div class="wave-panel">
					<div class="wave-head">
						<span><AudioLines size={14} /> 分离人声</span>
						<span class={`badge ${draft?.reference_clips.some((clip) => clip.cleanliness === 'clean') ? 'ok' : ''}`}>
							{draft?.reference_clips.some((clip) => clip.cleanliness === 'clean') ? '有干净参考音' : '待选择参考音'}
						</span>
					</div>
					<div class="waveform-line" aria-hidden="true">
						{#each Array.from({ length: 42 }) as _, index}
							<span style={`height:${12 + ((index * 17) % 36)}px`}></span>
						{/each}
					</div>
					<div class="speaker-lanes">
						<div class="lane a"><span>A</span><i style="left:8%;width:24%"></i><i style="left:42%;width:18%"></i></div>
						<div class="lane b"><span>B</span><i style="left:30%;width:14%"></i><i style="left:66%;width:18%"></i></div>
						<div class="lane mixed"><span>混合</span><i style="left:58%;width:10%"></i></div>
					</div>
				</div>
			</section>
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

			<div class="cue-table-wrap">
				<table class="table cue-table">
					<thead>
						<tr>
							<th>时间</th>
							<th>说话人</th>
							<th>英文字幕</th>
							<th>中文字幕</th>
							<th>TTS 台词</th>
							<th>参考音色</th>
							<th>TTS 音频</th>
							<th>状态</th>
						</tr>
					</thead>
					<tbody>
						{#each draft?.cues ?? [] as cue}
							<tr class:blocked={cue.review_status === 'blocked'} class:selected={cue.cue_id === selectedCue?.cue_id}>
								<td><button class="time-btn" type="button" onclick={() => (selectedCueId = cue.cue_id)}>{timeLabel(cue)}</button></td>
								<td><span class="speaker-pill" style={`--speaker:${speakerColor(cue.speaker_id)}`}>{speakerLabel(cue.speaker_id)}</span></td>
								<td>{cue.en_subtitle_text || '未填写'}</td>
								<td>{cue.zh_localized_subtitle_text || '未填写'}</td>
								<td><strong>{cue.tts_recommended_text || '未填写'}</strong></td>
								<td>{cue.reference_clip_id || '未选择'}</td>
								<td>
									{#if cue.tts_audio_path}
										<span class="badge ok">已生成</span>
										<small>{durationLabel(cue.generated_duration_ms)}</small>
									{:else if cue.tts_batch_status}
										<span class={`badge ${cue.tts_batch_status === 'failed' || cue.tts_batch_status === 'cancelled' ? 'fail' : 'warn'}`}>{ttsBatchLabel(cue.tts_batch_status)}</span>
										{#if cue.tts_batch_error}<small>{cue.tts_batch_error}</small>{/if}
									{:else}
										<span class="badge warn">待生成</span>
									{/if}
								</td>
								<td>
									<span class={`badge ${cue.review_status === 'ready' || cue.review_status === 'locked' ? 'ok' : cue.review_status === 'blocked' ? 'fail' : 'warn'}`}>{statusLabel(cue.review_status)}</span>
									<div class="flag-list">
										{#each cue.quality_flags as flag}<small>{flag}</small>{/each}
									</div>
								</td>
							</tr>
						{/each}
						{#if !draft?.cues.length}
							<tr>
								<td colspan="8" class="empty-cell">当前项目还没有 cue。可以先手动新增一条，后续 ASR 会自动生成候选。</td>
							</tr>
						{/if}
					</tbody>
				</table>
			</div>
			<div class="row table-actions">
				<button class="btn" type="button" onclick={addCue} disabled={!draft}>新增 cue</button>
			</div>
		</section>

		<aside class="stack right-editor">
			<section class="panel editor-panel">
				<div class="section-title">
					<h2>当前片段</h2>
					<span class={`badge ${selectedCue?.review_status === 'locked' ? 'ok' : selectedCue?.review_status === 'blocked' ? 'fail' : 'warn'}`}>
						<Lock size={12} /> {selectedCue ? statusLabel(selectedCue.review_status) : '未选择'}
					</span>
				</div>
				{#if selectedCue}
					<div class="editor-grid">
						<label class="field">
							<span>说话人</span>
							<select value={selectedCue.speaker_id ?? ''} aria-label="说话人" onchange={(event) => updateSelectedCue({ speaker_id: event.currentTarget.value || null })}>
								<option value="">未选择</option>
								{#each draft?.speakers ?? [] as speaker}
									<option value={speaker.speaker_id}>{speaker.speaker_id} / {speaker.display_name || speaker.speaker_id}</option>
								{/each}
								<option value="mixed">mixed / 需拆分</option>
							</select>
						</label>
						<div class="time-fields">
							<label class="field"><span>入点 ms</span><input value={selectedCue.start_ms ?? ''} aria-label="入点" oninput={(event) => updateSelectedCueTime('start_ms', event.currentTarget.value)} /></label>
							<label class="field"><span>出点 ms</span><input value={selectedCue.end_ms ?? ''} aria-label="出点" oninput={(event) => updateSelectedCueTime('end_ms', event.currentTarget.value)} /></label>
						</div>
						<label class="field">
							<span>参考音色</span>
							<select value={selectedCue.reference_clip_id ?? ''} aria-label="参考音色" onchange={(event) => updateSelectedCue({ reference_clip_id: event.currentTarget.value || null })}>
								<option value="">未选择</option>
								{#each draft?.reference_clips ?? [] as clip}
									<option value={clip.reference_clip_id}>{clip.reference_clip_id} / {speakerLabel(clip.speaker_id)}</option>
								{/each}
							</select>
						</label>
						<label class="field">
							<span>状态</span>
							<select value={selectedCue.review_status} aria-label="状态" onchange={(event) => updateSelectedCue({ review_status: event.currentTarget.value as VideoLocalizationCue['review_status'] })}>
								<option value="needs_review">待校对</option>
								<option value="ready">可生成</option>
								<option value="blocked">阻断</option>
								<option value="locked">已锁定</option>
							</select>
						</label>
						<label class="field"><span>英文字幕</span><textarea rows="3" value={selectedCue.en_subtitle_text ?? ''} oninput={(event) => updateSelectedCue({ en_subtitle_text: event.currentTarget.value })}></textarea></label>
						<label class="field"><span>中文字幕</span><textarea rows="3" value={selectedCue.zh_localized_subtitle_text ?? ''} oninput={(event) => updateSelectedCue({ zh_localized_subtitle_text: event.currentTarget.value })}></textarea></label>
						<label class="field"><span>TTS 台词</span><textarea rows="3" value={selectedCue.tts_recommended_text ?? ''} oninput={(event) => updateSelectedCue({ tts_recommended_text: event.currentTarget.value })}></textarea></label>
					</div>
					<div class="row editor-actions">
						<div class="cue-audio-compare">
							<div>
								<span>原声</span>
								{#if sourceCueAudioUrl(selectedCue)}
									<audio class="cue-audio" controls src={sourceCueAudioUrl(selectedCue)}></audio>
								{:else}
									<button class="btn" type="button" disabled><Play size={14} /> 原声</button>
								{/if}
							</div>
							<div>
								<span>TTS</span>
								{#if ttsAudioUrl(selectedCue)}
									<audio class="cue-audio" controls src={ttsAudioUrl(selectedCue)}></audio>
								{:else}
									<button class="btn" type="button" disabled><Mic2 size={14} /> TTS</button>
								{/if}
							</div>
						</div>
						<button class="btn primary" type="button" onclick={sendSelectedCueToGenerate} disabled={!cueCanSendToGenerate(selectedCue)}>
							<Send size={14} /> 单条发送
						</button>
					</div>
				{:else}
					<p class="muted">选择或新增一个 cue 后，可以编辑三轨文本和参考音色。</p>
				{/if}
			</section>

			<section class="panel refs-panel">
				<div class="section-title">
					<h2>干净参考音色池</h2>
					<div class="row">
						<span class="badge ok">{draft?.reference_clips.length ?? 0} 候选</span>
						<button class="mini-btn" type="button" onclick={createReferenceCandidates} disabled={draft?.stems.separation_status !== 'completed' || creatingReferences}>
							{creatingReferences ? '生成中' : '生成候选'}
						</button>
					</div>
				</div>
				<div class="reference-list">
					{#each draft?.reference_clips ?? [] as clip}
						<article class={`reference-card ${clip.cleanliness === 'clean' && clip.asr_status === 'verified' ? 'ready' : clip.cleanliness === 'blocked' ? 'blocked' : 'review'}`}>
							<div>
								<strong>{clip.reference_clip_id}</strong>
								<p>{clip.audio_path || '尚未生成参考音文件'}</p>
							</div>
							<div class="row">
								<span class="badge role">{speakerLabel(clip.speaker_id)}</span>
								<span class="badge">{durationLabel(clip.duration_ms)}</span>
								<span class={`badge ${clip.cleanliness === 'clean' && clip.asr_status === 'verified' ? 'ok' : clip.cleanliness === 'blocked' ? 'fail' : 'warn'}`}>
									{clip.cleanliness === 'clean' && clip.asr_status === 'verified' ? '可用' : clip.cleanliness === 'blocked' ? '阻断' : '复听'}
								</span>
							</div>
							<small>ASR: {clip.asr_text || '待独立 ASR'}</small>
						</article>
					{/each}
					{#if !draft?.reference_clips.length}
						<p class="muted">暂无参考音候选。下一批接入人声分离和参考音裁切。</p>
					{/if}
				</div>
			</section>

			<section class="panel export-panel">
				<div class="section-title">
					<h2>批量与交付</h2>
					<span class={`badge ${gateBadgeClass(draft?.quality_gate.status)}`}>{gateLabel(draft?.quality_gate.status)}</span>
				</div>
				<div class="handoff-summary">
					<div><strong>{canSubmitCount}</strong><span>可提交</span></div>
					<div><strong>{generatedCount}</strong><span>已生成</span></div>
					<div><strong>{draft?.quality_gate.blockers.length ?? 0}</strong><span>阻断</span></div>
					<div><strong>{draft?.quality_gate.warnings.length ?? 0}</strong><span>警告</span></div>
				</div>
				<p class="muted small-note">
					{draft?.quality_gate.checked_at ? `最近检查：${draft.quality_gate.checked_at}` : '保存或导出后会自动刷新质量门。'}
				</p>
				<div class="stack">
					<button class="btn success" type="button" onclick={submitBatchTts} disabled={!canSubmitCount || submittingBatch}><Wand2 size={14} /> {submittingBatch ? '提交中' : '批量发送可生成片段'}</button>
					<div class="batch-sync-row">
						<select value={ttsBatchId} aria-label="选择当前项目批次" onchange={(event) => (ttsBatchId = event.currentTarget.value)} disabled={loadingBatches}>
							<option value="">{loadingBatches ? '加载批次中' : projectBatches.length ? '选择最近批次' : '暂无项目批次'}</option>
							{#each projectBatches as batch}
								<option value={batch.batch_task_id}>{batchOptionLabel(batch)}</option>
							{/each}
						</select>
						<input value={ttsBatchId} oninput={(event) => (ttsBatchId = event.currentTarget.value)} placeholder="batch id" aria-label="批量 TTS 任务 ID" />
						<button class="btn" type="button" onclick={syncBatchTtsResults} disabled={!ttsBatchId.trim() || syncingBatch}>
							<Mic2 size={14} /> {syncingBatch ? '同步中' : '同步 TTS 结果'}
						</button>
					</div>
					<button class="btn" type="button" onclick={exportJson} disabled={!draft}><Download size={14} /> 下载 production JSON</button>
					<button class="btn" type="button" onclick={exportBilingualSrt} disabled={!draft?.cues.some((cue) => cue.start_ms !== null && cue.end_ms !== null)}><Languages size={14} /> 导出中英字幕草稿</button>
				</div>
			</section>
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

	.workflow-strip {
		display: grid;
		grid-template-columns: repeat(7, minmax(92px, 1fr));
		gap: 8px;
		margin-bottom: 14px;
	}

	.workflow-step {
		border: 1px solid var(--line);
		border-radius: 7px;
		background: var(--panel);
		color: var(--muted);
		padding: 8px 10px;
		font-size: 12px;
		min-height: 34px;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.workflow-step.done {
		color: #9ee6c8;
		border-color: #23634f;
		background: #12261f;
	}

	.workflow-step.active {
		color: #9cc9ff;
		border-color: #27527e;
		background: #101d2d;
	}

	.workflow-step.blocked {
		color: #ff9a9a;
		border-color: #6d3030;
		background: #2b1515;
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

	.video-preview {
		position: relative;
		aspect-ratio: 16 / 9;
		border-radius: 7px;
		overflow: hidden;
		background:
			linear-gradient(130deg, rgba(79, 156, 249, 0.18), transparent 42%),
			linear-gradient(25deg, rgba(66, 196, 155, 0.14), transparent 34%),
			#0c0f13;
		border: 1px solid var(--line);
	}

	.video-glow {
		position: absolute;
		inset: 18% 12%;
		background: linear-gradient(120deg, rgba(255, 255, 255, 0.08), transparent);
		border-radius: 50%;
	}

	.play-button {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		color: #fff;
	}

	.play-button span {
		padding: 10px;
		width: 52px;
		height: 52px;
		border-radius: 999px;
		background: rgba(0, 0, 0, 0.4);
		display: grid;
		place-items: center;
	}

	.subtitle-overlay {
		position: absolute;
		left: 18px;
		right: 18px;
		bottom: 16px;
		text-align: center;
		text-shadow: 0 1px 6px rgba(0, 0, 0, 0.7);
	}

	.subtitle-overlay p {
		margin: 0;
		font-size: 16px;
		font-weight: 700;
	}

	.subtitle-overlay span {
		display: block;
		margin-top: 3px;
		color: rgba(255, 255, 255, 0.78);
		font-size: 12px;
	}

	.wave-panel {
		margin-top: 12px;
	}

	.wave-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		font-size: 12px;
		color: var(--muted);
		margin-bottom: 8px;
	}

	.wave-head span:first-child {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}

	.waveform-line {
		height: 58px;
		display: flex;
		align-items: center;
		gap: 3px;
		padding: 8px;
		border-radius: 7px;
		background: #101215;
		border: 1px solid var(--line);
	}

	.waveform-line span {
		width: 4px;
		border-radius: 999px;
		background: #4f9cf9;
		opacity: 0.72;
	}

	.speaker-lanes {
		display: grid;
		gap: 6px;
		margin-top: 8px;
	}

	.lane {
		position: relative;
		height: 20px;
		border-radius: 6px;
		background: #101215;
		border: 1px solid var(--line);
		overflow: hidden;
	}

	.lane span {
		position: relative;
		z-index: 1;
		display: inline-flex;
		align-items: center;
		height: 100%;
		padding-left: 8px;
		font-size: 11px;
		color: var(--muted);
	}

	.lane i {
		position: absolute;
		top: 4px;
		bottom: 4px;
		border-radius: 999px;
	}

	.lane.a i { background: #4f9cf9; }
	.lane.b i { background: #42c49b; }
	.lane.mixed i { background: #e4ad42; }

	.quality-bar {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin-bottom: 10px;
	}

	.cue-table-wrap {
		overflow-x: auto;
		border: 1px solid var(--line);
		border-radius: 7px;
	}

	.cue-table {
		min-width: 840px;
		table-layout: fixed;
	}

	.cue-table th,
	.cue-table td {
		padding: 9px 8px;
		overflow-wrap: anywhere;
	}

	.cue-table th:nth-child(1),
	.cue-table td:nth-child(1) {
		width: 96px;
	}

	.cue-table th:nth-child(2),
	.cue-table td:nth-child(2) {
		width: 54px;
		text-align: center;
	}

	.cue-table th:nth-child(6),
	.cue-table td:nth-child(6) {
		width: 86px;
	}

	.cue-table th:nth-child(7),
	.cue-table td:nth-child(7) {
		width: 86px;
	}

	.cue-table th:nth-child(8),
	.cue-table td:nth-child(8) {
		width: 112px;
	}

	.cue-table tr.selected td {
		background: rgba(79, 156, 249, 0.08);
	}

	.cue-table tr.blocked td {
		background: rgba(242, 109, 109, 0.05);
	}

	.empty-cell {
		color: var(--muted);
		text-align: center;
		padding: 22px !important;
	}

	.table-actions {
		margin-top: 12px;
		justify-content: flex-end;
	}

	.time-btn {
		border: 0;
		background: transparent;
		color: #9cc9ff;
		padding: 0;
		font-size: 12px;
	}

	.speaker-pill {
		--speaker: #4f9cf9;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 30px;
		height: 22px;
		border-radius: 999px;
		border: 1px solid color-mix(in srgb, var(--speaker), #000 24%);
		color: #fff;
		background: color-mix(in srgb, var(--speaker), #111315 42%);
		font-weight: 700;
		font-size: 12px;
	}

	.flag-list {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 5px;
	}

	.flag-list small {
		color: var(--muted);
		border: 1px solid var(--line);
		border-radius: 999px;
		padding: 1px 5px;
		font-size: 10px;
		white-space: nowrap;
	}

	.editor-grid {
		display: grid;
		gap: 10px;
	}

	.field span {
		font-size: 12px;
		color: var(--muted);
	}

	.time-fields {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 8px;
	}

	.editor-grid textarea {
		min-height: 74px;
	}

	.editor-actions {
		margin-top: 12px;
		justify-content: flex-end;
	}

	.cue-audio {
		width: min(260px, 100%);
		height: 34px;
	}

	.cue-audio-compare {
		display: grid;
		grid-template-columns: repeat(2, minmax(160px, 1fr));
		gap: 8px;
		flex: 1;
		min-width: 280px;
	}

	.cue-audio-compare > div {
		display: grid;
		gap: 4px;
	}

	.cue-audio-compare span {
		font-size: 11px;
		color: var(--muted);
	}

	.reference-list {
		display: grid;
		gap: 8px;
	}

	.reference-card {
		display: grid;
		gap: 7px;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 10px;
		background: #101215;
	}

	.reference-card.ready {
		border-color: #23634f;
	}

	.reference-card.review {
		border-color: #604b18;
	}

	.reference-card p,
	.reference-card small {
		margin: 0;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.45;
	}

	.handoff-summary {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 8px;
		margin-bottom: 8px;
	}

	.handoff-summary div {
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 8px;
		background: #101215;
	}

	.handoff-summary strong,
	.handoff-summary span {
		display: block;
	}

	.handoff-summary strong {
		font-size: 18px;
	}

	.handoff-summary span,
	.small-note {
		font-size: 12px;
	}

	.export-panel .btn {
		justify-content: center;
	}

	.batch-sync-row {
		display: grid;
		grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr) auto;
		gap: 8px;
	}

	.batch-sync-row input,
	.batch-sync-row select {
		min-width: 0;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 8px 10px;
		background: #fff;
		color: var(--ink);
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
