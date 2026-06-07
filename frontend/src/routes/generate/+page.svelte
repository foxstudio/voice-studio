<script lang="ts">
	import { Api } from '$lib/api';
	import type {
		AppSettings,
		EngineDetail,
		GenerationTask,
		GenerateRequest,
		PresetTemplate,
		VoiceAsset
	} from '$lib/api/types';
	import { engineStatusLabel, taskStatusLabel } from '$lib/labels';
	import {
		ChevronLeft,
		ChevronRight,
		Download,
		Hash,
		Play,
		RotateCcw,
		Scissors,
		Search,
		Send,
		SlidersHorizontal,
		Sparkles,
		Trash2,
		Wand2
	} from 'lucide-svelte';
	import { onMount } from 'svelte';

	type TaskStatusTab = 'all' | 'active' | 'success' | 'failed';
	type TaskSourceFilter = 'all' | 'local' | 'cloud';
	type TaskDateFilter = 'all' | 'today' | '7d' | '30d';
	type TaskSortBy = 'latest' | 'oldest' | 'duration_desc';

	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let presets = $state<PresetTemplate[]>([]);
	let settings = $state<AppSettings | null>(null);

	let text = $state('');
	let textSegments = $state<string[]>([]);
	let textToolBusy = $state<'clean' | 'numbers' | 'split' | ''>('');
	let showSplitPreview = $state(false);

	let engineId = $state('indextts-v2');
	let voiceId = $state('');
	let language = $state('zh');
	let emotion = $state('');
	let voiceDesign = $state('女，青年，中音调');
	let voiceDesignPrompt = $state('中年男性，声线沉稳偏正式，吐字工整，语速适中。');
	let styleInstruction = $state('');
	let mimoVoice = $state('mimo_default');
	let emoAlpha = $state(0.6);
	let speed = $state(1.0);
	let temperature = $state(0.8);
	let topP = $state(0.8);
	let topK = $state(30);
	let maxTextTokensPerSegment = $state(120);
	let intervalSilence = $state(200);
	let diffusionSteps = $state(25);
	let cfgRate = $state(0.7);
	let outputFormat = $state<'wav' | 'mp3' | 'flac'>('wav');
	let showAdvanced = $state(false);

	let tasks = $state<GenerationTask[]>([]);
	let selectedTaskIds = $state<string[]>([]);
	let taskQuery = $state('');
	let taskStatusTab = $state<TaskStatusTab>('all');
	let taskEngineFilter = $state('all');
	let taskSourceFilter = $state<TaskSourceFilter>('all');
	let taskDateFilter = $state<TaskDateFilter>('all');
	let taskSortBy = $state<TaskSortBy>('latest');
	let currentPage = $state(1);
	let pageSize = $state(8);
	let actionBusyTaskId = $state('');

	let busy = $state(false);
	let error = $state('');
	let initialized = $state(false);
	let lastEngineId = $state('indextts-v2');

	const INDEX_TTS_DEFAULTS = {
		emotion: '',
		emoAlpha: 0.6,
		speed: 1.0,
		temperature: 0.8,
		topP: 0.8,
		topK: 30,
		maxTextTokensPerSegment: 120,
		intervalSilence: 200,
		diffusionSteps: 25,
		cfgRate: 0.7,
		outputFormat: 'wav' as const
	};

	const MIMO_DEFAULTS = {
		temperature: 0.6,
		topP: 0.95
	};

	function taskIsActive(task: GenerationTask) {
		return ['pending', 'queued', 'running', 'postprocessing'].includes(task.status);
	}

	function taskIsSuccess(task: GenerationTask) {
		return task.status === 'success';
	}

	function taskIsFailed(task: GenerationTask) {
		return task.status === 'failed' || task.status === 'cancelled';
	}

	function taskCanDelete(task: GenerationTask) {
		return !taskIsActive(task);
	}

	const selected = $derived(engines.find((e) => e.manifest.engine_id === engineId));
	const ttsEngines = $derived(engines.filter((e) => !e.manifest.capabilities.includes('speech_recognition')));
	const selectedVoice = $derived(voices.find((v) => v.voice_id === voiceId) ?? null);
	const voiceMap = $derived(new Map(voices.map((voice) => [voice.voice_id, voice])));
	const engineMap = $derived(new Map(engines.map((engine) => [engine.manifest.engine_id, engine])));
	const supportsEmotion = $derived(Boolean(selected?.manifest.capabilities.includes('emotion_control')));
	const isIndexTTS = $derived(engineId === 'indextts-v2');
	const isOmniVoice = $derived(engineId === 'omnivoice');
	const isMimoPreset = $derived(engineId === 'mimo-v2.5-tts-preset');
	const isMimoDesign = $derived(engineId === 'mimo-v2.5-tts-voicedesign');
	const isMimoClone = $derived(engineId === 'mimo-v2.5-tts-voiceclone');
	const isMimo = $derived(engineId.startsWith('mimo-v2.5'));
	const followsReferenceEmotion = $derived(isIndexTTS && !emotion);
	const mimoVoiceOptions = $derived(selected?.manifest.parameter_schema.find((p) => p.key === 'mimo_voice')?.options ?? []);
	const voiceChoices = $derived(
		isMimoClone
			? voices.filter((voice) =>
					voice.engine_bindings?.some(
						(binding) => binding.engine_id === 'mimo-v2.5-tts-voiceclone' && binding.available
					)
				)
			: voices
	);
	const hasRunningTasks = $derived(tasks.some((task) => taskIsActive(task)));

	const statusCounts = $derived.by(() => ({
		all: tasks.length,
		active: tasks.filter((task) => taskIsActive(task)).length,
		success: tasks.filter((task) => taskIsSuccess(task)).length,
		failed: tasks.filter((task) => taskIsFailed(task)).length
	}));

	const taskEngineOptions = $derived(['all', ...new Set(tasks.map((task) => task.engine_id))]);

	const filteredTasks = $derived.by(() => {
		const query = taskQuery.trim().toLowerCase();
		const now = Date.now();
		const filtered = tasks.filter((task) => {
			if (taskStatusTab === 'active' && !taskIsActive(task)) return false;
			if (taskStatusTab === 'success' && !taskIsSuccess(task)) return false;
			if (taskStatusTab === 'failed' && !taskIsFailed(task)) return false;
			if (taskEngineFilter !== 'all' && task.engine_id !== taskEngineFilter) return false;
			if (taskSourceFilter !== 'all' && engineKind(task.engine_id) !== taskSourceFilter) return false;

			if (taskDateFilter !== 'all') {
				const createdAt = new Date(task.created_at).getTime();
				if (!Number.isFinite(createdAt)) return false;
				const age = now - createdAt;
				const cutoff =
					taskDateFilter === 'today'
						? 24 * 60 * 60 * 1000
						: taskDateFilter === '7d'
							? 7 * 24 * 60 * 60 * 1000
							: 30 * 24 * 60 * 60 * 1000;
				if (age > cutoff) return false;
			}

			if (!query) return true;
			const voiceName = task.voice_id ? voiceMap.get(task.voice_id)?.name ?? '' : '';
			return (
				displayTitle(task).toLowerCase().includes(query) ||
				task.engine_id.toLowerCase().includes(query) ||
				voiceName.toLowerCase().includes(query) ||
				taskStatusLabel(task.status).toLowerCase().includes(query)
			);
		});

		return filtered.sort((a, b) => {
			if (taskSortBy === 'oldest') return a.created_at.localeCompare(b.created_at);
			if (taskSortBy === 'duration_desc') return (b.result_duration_ms ?? 0) - (a.result_duration_ms ?? 0);
			return b.created_at.localeCompare(a.created_at);
		});
	});

	const pageCount = $derived(Math.max(1, Math.ceil(filteredTasks.length / pageSize)));
	const pagedTasks = $derived.by(() => {
		const start = (currentPage - 1) * pageSize;
		return filteredTasks.slice(start, start + pageSize);
	});
	const visibleSelectableTasks = $derived(pagedTasks.filter((task) => taskCanDelete(task)));
	const allVisibleSelected = $derived(
		visibleSelectableTasks.length > 0 &&
			visibleSelectableTasks.every((task) => selectedTaskIds.includes(task.task_id))
	);
	const hasActiveFilters = $derived(
		Boolean(taskQuery.trim()) ||
			taskStatusTab !== 'all' ||
			taskEngineFilter !== 'all' ||
			taskSourceFilter !== 'all' ||
			taskDateFilter !== 'all' ||
			taskSortBy !== 'latest'
	);

	async function refreshPageData() {
		const [e, v, t, p, s] = await Promise.all([
			Api.engines(),
			Api.voices(),
			Api.tasks(),
			Api.presets(),
			Api.settings()
		]);
		engines = e;
		voices = v;
		tasks = t;
		presets = p;
		settings = s;

		const params = new URLSearchParams(location.search);
		const vId = params.get('voice');
		if (!initialized) {
			const defaultEngine = e.find(
				(engine) =>
					engine.manifest.engine_id === s.default_engine_id &&
					!engine.manifest.capabilities.includes('speech_recognition')
			);
			engineId = defaultEngine?.manifest.engine_id || engineId;
			voiceId = vId || s.default_voice_id || '';
			language = s.default_language || language;
			showSplitPreview = params.get('tools') === 'text';
			initialized = true;
		} else if (vId) {
			voiceId = vId;
		}
	}

	onMount(() => {
		refreshPageData();
		const id = setInterval(() => {
			if (hasRunningTasks) refreshPageData();
		}, 2000);
		return () => clearInterval(id);
	});

	$effect(() => {
		if (engineId !== lastEngineId) {
			if (engineId.startsWith('mimo-v2.5')) {
				temperature = MIMO_DEFAULTS.temperature;
				topP = MIMO_DEFAULTS.topP;
			} else {
				temperature = INDEX_TTS_DEFAULTS.temperature;
				topP = INDEX_TTS_DEFAULTS.topP;
			}
			if (!isMimoClone && !isIndexTTS && !isOmniVoice) voiceId = '';
			lastEngineId = engineId;
		}
	});

	$effect(() => {
		if (currentPage > pageCount) currentPage = pageCount;
	});

	function requestBody(): GenerateRequest {
		const usesEmotionControl = supportsEmotion && Boolean(emotion);
		return {
			text,
			engine_id: engineId,
			voice_id: voiceId || null,
			ref_text: selectedVoice?.reference_text || null,
			language,
			emotion_mode: usesEmotionControl ? 'emotion_vector' : 'follow_reference',
			emotion: usesEmotionControl ? emotion : null,
			emotion_text: isOmniVoice && !voiceId ? voiceDesign : null,
			style_instruction: isMimo ? styleInstruction || null : null,
			voice_design_prompt: isMimoDesign ? voiceDesignPrompt : null,
			mimo_voice: isMimoPreset ? mimoVoice : null,
			emo_alpha: emoAlpha,
			speed,
			temperature,
			top_p: topP,
			top_k: topK,
			repetition_penalty: 10,
			max_mel_tokens: 1500,
			max_text_tokens_per_segment: maxTextTokensPerSegment,
			interval_silence: intervalSilence,
			segment_overlap_ms: 50,
			diffusion_steps: diffusionSteps,
			cfg_rate: cfgRate,
			output_format: outputFormat
		};
	}

	function restoreRequest(req: GenerateRequest) {
		const isMimoEngine = req.engine_id.startsWith('mimo-v2.5');
		const restoredEmotion =
			req.emotion_mode === 'emotion_vector' && typeof req.emotion === 'string' ? req.emotion : '';
		const restoredVoiceDesign =
			req.emotion_mode === 'emotion_text' && typeof req.emotion_text === 'string'
				? req.emotion_text
				: '女，青年，中音调';

		engineId = req.engine_id;
		lastEngineId = req.engine_id;
		text = req.text;
		voiceId = req.voice_id ?? '';
		language = req.language || 'zh';
		emotion = restoredEmotion;
		voiceDesign = restoredVoiceDesign;
		voiceDesignPrompt = req.voice_design_prompt || '中年男性，声线沉稳偏正式，吐字工整，语速适中。';
		styleInstruction = req.style_instruction || '';
		mimoVoice = req.mimo_voice || 'mimo_default';
		emoAlpha = req.emo_alpha ?? INDEX_TTS_DEFAULTS.emoAlpha;
		speed = req.speed ?? INDEX_TTS_DEFAULTS.speed;
		temperature =
			req.temperature ??
			(isMimoEngine ? MIMO_DEFAULTS.temperature : INDEX_TTS_DEFAULTS.temperature);
		topP = req.top_p ?? (isMimoEngine ? MIMO_DEFAULTS.topP : INDEX_TTS_DEFAULTS.topP);
		topK = req.top_k ?? INDEX_TTS_DEFAULTS.topK;
		maxTextTokensPerSegment =
			req.max_text_tokens_per_segment ?? INDEX_TTS_DEFAULTS.maxTextTokensPerSegment;
		intervalSilence = req.interval_silence ?? INDEX_TTS_DEFAULTS.intervalSilence;
		diffusionSteps = req.diffusion_steps ?? INDEX_TTS_DEFAULTS.diffusionSteps;
		cfgRate = req.cfg_rate ?? INDEX_TTS_DEFAULTS.cfgRate;
		outputFormat = req.output_format ?? INDEX_TTS_DEFAULTS.outputFormat;
	}

	function applyPreset(preset: PresetTemplate) {
		restoreRequest({
			text: preset.sample_text,
			engine_id: preset.engine_id,
			voice_id: null,
			reference_audio_path: null,
			ref_text: null,
			language: String(preset.parameters.language ?? 'zh'),
			emotion_mode: preset.parameters.emotion ? 'emotion_vector' : 'follow_reference',
			emotion:
				typeof preset.parameters.emotion === 'string' ? preset.parameters.emotion : null,
			emotion_values: null,
			emotion_text:
				typeof preset.parameters.emotion_text === 'string'
					? preset.parameters.emotion_text
					: null,
			style_instruction:
				typeof preset.parameters.style_instruction === 'string'
					? preset.parameters.style_instruction
					: null,
			voice_design_prompt:
				typeof preset.parameters.voice_design_prompt === 'string'
					? preset.parameters.voice_design_prompt
					: null,
			mimo_voice:
				typeof preset.parameters.mimo_voice === 'string'
					? preset.parameters.mimo_voice
					: null,
			emo_alpha: Number(preset.parameters.emo_alpha ?? INDEX_TTS_DEFAULTS.emoAlpha),
			speed: Number(preset.parameters.speed ?? INDEX_TTS_DEFAULTS.speed),
			temperature: Number(
				preset.parameters.temperature ??
					(preset.engine_id.startsWith('mimo-v2.5')
						? MIMO_DEFAULTS.temperature
						: INDEX_TTS_DEFAULTS.temperature)
			),
			top_p: Number(
				preset.parameters.top_p ??
					(preset.engine_id.startsWith('mimo-v2.5')
						? MIMO_DEFAULTS.topP
						: INDEX_TTS_DEFAULTS.topP)
			),
			top_k: Number(preset.parameters.top_k ?? INDEX_TTS_DEFAULTS.topK),
			repetition_penalty: Number(preset.parameters.repetition_penalty ?? 10),
			seed: null,
			max_mel_tokens: Number(preset.parameters.max_mel_tokens ?? 1500),
			max_text_tokens_per_segment: Number(
				preset.parameters.max_text_tokens_per_segment ??
					INDEX_TTS_DEFAULTS.maxTextTokensPerSegment
			),
			interval_silence: Number(
				preset.parameters.interval_silence ?? INDEX_TTS_DEFAULTS.intervalSilence
			),
			segment_overlap_ms: Number(preset.parameters.segment_overlap_ms ?? 50),
			diffusion_steps: Number(
				preset.parameters.diffusion_steps ?? INDEX_TTS_DEFAULTS.diffusionSteps
			),
			cfg_rate: Number(preset.parameters.cfg_rate ?? INDEX_TTS_DEFAULTS.cfgRate),
			output_format:
				(preset.parameters.output_format as 'wav' | 'mp3' | 'flac') ??
				INDEX_TTS_DEFAULTS.outputFormat
		});
	}

	function upsertTask(task: GenerationTask) {
		tasks = [task, ...tasks.filter((item) => item.task_id !== task.task_id)];
	}

	async function poll(taskId: string) {
		for (let i = 0; i < 900; i++) {
			const task = await Api.task(taskId);
			upsertTask(task);
			if (['success', 'failed', 'cancelled'].includes(task.status)) return;
			await new Promise((resolve) => setTimeout(resolve, 1000));
		}
	}

	async function generate() {
		if (!text.trim()) return;
		error = '';
		busy = true;
		try {
			if (isMimoClone && settings?.mimo_voiceclone_confirm_upload) {
				const name = selectedVoice?.name ?? '当前参考音色';
				const ok = window.confirm(
					`MiMo 音色复刻会把「${name}」的本次参考音频发送到小米云端用于生成。继续吗？`
				);
				if (!ok) return;
			}
			const engine = engines.find((item) => item.manifest.engine_id === engineId);
			if (engine && engine.state.status !== 'loaded') await Api.startEngine(engineId);
			const res = await Api.generate(requestBody());
			currentPage = 1;
			await poll(res.task_id);
		} catch (cause) {
			error = (cause as Error).message;
		} finally {
			busy = false;
		}
	}

	function reuse(task: GenerationTask) {
		restoreRequest(task.parameters as unknown as GenerateRequest);
	}

	async function runTextTool(mode: 'clean' | 'numbers' | 'split') {
		if (!text.trim() && mode !== 'split') return;
		textToolBusy = mode;
		try {
			if (mode === 'clean') {
				text = (await Api.cleanText(text)).text;
				return;
			}
			if (mode === 'numbers') {
				text = (await Api.normalizeNumbers(text)).text;
				return;
			}
			textSegments = (await Api.splitText(text)).segments;
			showSplitPreview = true;
		} finally {
			textToolBusy = '';
		}
	}

	async function cancel(task: GenerationTask) {
		actionBusyTaskId = task.task_id;
		try {
			await Api.cancelTask(task.task_id);
			await refreshPageData();
		} finally {
			actionBusyTaskId = '';
		}
	}

	async function retry(task: GenerationTask) {
		actionBusyTaskId = task.task_id;
		try {
			const res = await Api.retryTask(task.task_id);
			currentPage = 1;
			await poll(res.task_id);
			await refreshPageData();
		} finally {
			actionBusyTaskId = '';
		}
	}

	async function deleteTaskRecord(task: GenerationTask) {
		const hasAudio = Boolean(task.result_id);
		const ok = window.confirm(
			hasAudio ? '删除这条记录，并一并删除本地生成音频？' : '删除这条任务记录？'
		);
		if (!ok) return;
		actionBusyTaskId = task.task_id;
		try {
			await Api.deleteTask(task.task_id);
			selectedTaskIds = selectedTaskIds.filter((taskId) => taskId !== task.task_id);
			await refreshPageData();
		} finally {
			actionBusyTaskId = '';
		}
	}

	function toggleTaskSelection(taskId: string, checked: boolean) {
		selectedTaskIds = checked
			? [...selectedTaskIds, taskId]
			: selectedTaskIds.filter((item) => item !== taskId);
	}

	function toggleVisibleSelection() {
		if (allVisibleSelected) {
			selectedTaskIds = selectedTaskIds.filter(
				(taskId) => !visibleSelectableTasks.some((task) => task.task_id === taskId)
			);
			return;
		}
		selectedTaskIds = Array.from(
			new Set([...selectedTaskIds, ...visibleSelectableTasks.map((task) => task.task_id)])
		);
	}

	async function deleteSelectedTasks() {
		if (!selectedTaskIds.length) return;
		const ok = window.confirm(`批量删除 ${selectedTaskIds.length} 条记录，并删除相关本地音频？`);
		if (!ok) return;
		await Promise.all(selectedTaskIds.map((taskId) => Api.deleteTask(taskId)));
		selectedTaskIds = [];
		await refreshPageData();
	}

	function clearTaskFilters() {
		taskQuery = '';
		taskStatusTab = 'all';
		taskEngineFilter = 'all';
		taskSourceFilter = 'all';
		taskDateFilter = 'all';
		taskSortBy = 'latest';
		currentPage = 1;
	}

	function taskPageJump(delta: number) {
		const next = currentPage + delta;
		currentPage = Math.min(pageCount, Math.max(1, next));
	}

	function progressLabel(task: GenerationTask) {
		if (task.status === 'queued' || task.status === 'pending') return '等待排队';
		if (task.status === 'running') return `${Math.round((task.progress || 0) * 100)}%`;
		if (task.status === 'success') return '100%';
		return taskStatusLabel(task.status);
	}

	function elapsedSeconds(task: GenerationTask) {
		if (!task.started_at) return 0;
		const started = new Date(task.started_at).getTime();
		if (!Number.isFinite(started)) return 0;
		const end = task.completed_at ? new Date(task.completed_at).getTime() : Date.now();
		return Math.max(0, Math.floor((end - started) / 1000));
	}

	function elapsedLabel(task: GenerationTask) {
		const totalSeconds = elapsedSeconds(task);
		const minutes = Math.floor(totalSeconds / 60);
		const seconds = totalSeconds % 60;
		return `${minutes}:${seconds.toString().padStart(2, '0')}`;
	}

	function taskTimingLine(task: GenerationTask) {
		if (task.generation_time_ms) {
			const seconds = (task.generation_time_ms / 1000).toFixed(1);
			return task.status === 'failed' ? `失败前运行 ${seconds}s` : `生成耗时 ${seconds}s`;
		}
		const elapsed = elapsedLabel(task);
		if (!elapsed || elapsed === '0:00') return '等待开始';
		if (task.status === 'failed') return `失败前运行 ${elapsed}`;
		if (task.status === 'cancelled') return `取消前运行 ${elapsed}`;
		if (task.status === 'success') return `总耗时 ${elapsed}`;
		return `已运行 ${elapsed}`;
	}

	function taskStageLabel(task: GenerationTask) {
		if (task.status === 'queued' || task.status === 'pending') return '等待排队';
		if (task.status === 'cancelled') return '已取消';
		if (task.status === 'failed') return '已失败';
		if (task.status === 'success') return '已完成';
		if ((task.progress ?? 0) < 0.2) return '预热模型';
		if ((task.progress ?? 0) < 0.55) return '声学推理';
		if ((task.progress ?? 0) < 0.88) return '写入音频';
		return '收尾处理中';
	}

	function taskEtaLabel(task: GenerationTask) {
		if (!taskIsActive(task) || !task.started_at) return '';
		const progress = task.progress ?? 0;
		if (progress < 0.18 || progress >= 0.98) return '';
		const elapsed = elapsedSeconds(task);
		if (elapsed < 2) return '';
		const totalEstimate = elapsed / progress;
		const remaining = Math.max(0, Math.round(totalEstimate - elapsed));
		if (!Number.isFinite(remaining) || remaining <= 1) return '';
		const minutes = Math.floor(remaining / 60);
		const seconds = remaining % 60;
		return `预计剩余 ${minutes}:${seconds.toString().padStart(2, '0')}`;
	}

	function engineKind(engineId: string) {
		return engineMap.get(engineId)?.manifest.engine_type ?? (engineId.startsWith('mimo-') ? 'cloud' : 'local');
	}

	function engineTypeLabel(engineId: string) {
		return engineKind(engineId) === 'cloud' ? '云端' : '本地';
	}

	function formatTime(value: string | null) {
		if (!value) return '';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat('zh-CN', {
			month: '2-digit',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit'
		}).format(date);
	}

	function formatAudioDuration(durationMs: number | null) {
		if (!durationMs) return '';
		return `${(durationMs / 1000).toFixed(1)}s`;
	}

	function displayTitle(task: GenerationTask) {
		return task.input_text.trim() || '未命名任务';
	}

	function voiceName(task: GenerationTask) {
		return task.voice_id ? voiceMap.get(task.voice_id)?.name ?? '' : '';
	}

	function numericParam(task: GenerationTask, key: string) {
		const value = task.parameters[key];
		return typeof value === 'number' ? value : null;
	}

	function textParam(task: GenerationTask, key: string) {
		const value = task.parameters[key];
		return typeof value === 'string' && value.trim() ? value : null;
	}

	function presetEngineLabel(preset: PresetTemplate) {
		return engineMap.get(preset.engine_id)?.manifest.display_name ?? preset.engine_id;
	}

	function presetEngineKind(preset: PresetTemplate) {
		return engineKind(preset.engine_id);
	}

	function taskParameterText(task: GenerationTask) {
		const lines = [
			`引擎：${engineMap.get(task.engine_id)?.manifest.display_name ?? task.engine_id}`,
			`来源：${engineTypeLabel(task.engine_id)}`
		];
		const voice = voiceName(task);
		if (voice) lines.push(`音色：${voice}`);
		if (textParam(task, 'language')) lines.push(`语言：${textParam(task, 'language')}`);
		if (textParam(task, 'emotion')) lines.push(`情绪：${textParam(task, 'emotion')}`);
		if (textParam(task, 'mimo_voice')) lines.push(`MiMo 音色：${textParam(task, 'mimo_voice')}`);
		if (textParam(task, 'style_instruction'))
			lines.push(`风格指令：${textParam(task, 'style_instruction')}`);
		if (textParam(task, 'voice_design_prompt'))
			lines.push(`音色描述：${textParam(task, 'voice_design_prompt')}`);
		if (textParam(task, 'emotion_text'))
			lines.push(`声音设计：${textParam(task, 'emotion_text')}`);
		if (numericParam(task, 'speed') !== null)
			lines.push(`语速：${numericParam(task, 'speed')?.toFixed(2)}`);
		if (numericParam(task, 'temperature') !== null)
			lines.push(`Temperature：${numericParam(task, 'temperature')?.toFixed(2)}`);
		if (numericParam(task, 'top_p') !== null)
			lines.push(`Top-P：${numericParam(task, 'top_p')?.toFixed(2)}`);
		if (numericParam(task, 'top_k') !== null)
			lines.push(`Top-K：${numericParam(task, 'top_k')}`);
		if (numericParam(task, 'emo_alpha') !== null)
			lines.push(`情绪强度：${numericParam(task, 'emo_alpha')?.toFixed(2)}`);
		if (numericParam(task, 'interval_silence') !== null)
			lines.push(`段间静默：${numericParam(task, 'interval_silence')} ms`);
		if (numericParam(task, 'max_text_tokens_per_segment') !== null)
			lines.push(`分段长度：${numericParam(task, 'max_text_tokens_per_segment')}`);
		if (numericParam(task, 'diffusion_steps') !== null)
			lines.push(`扩散步数：${numericParam(task, 'diffusion_steps')}`);
		if (numericParam(task, 'cfg_rate') !== null)
			lines.push(`CFG：${numericParam(task, 'cfg_rate')?.toFixed(2)}`);
		if (textParam(task, 'output_format'))
			lines.push(`格式：${textParam(task, 'output_format')?.toUpperCase()}`);
		return lines.join('\n');
	}
</script>

<svelte:head>
	<title>语音合成 - 声音工作台</title>
</svelte:head>

<main class="page">
	<div class="page-head">
		<div>
			<h1>语音合成</h1>
			<p class="muted">短文本合成、文本处理、任务进度和生成记录统一放在一个工作台里。</p>
		</div>
	</div>

	<div class="workbench">
		<section class="panel stack compose-panel">
			<div class="row section-head">
				<h2>参数模板</h2>
				<span class="muted">{presets.length} 组</span>
			</div>
			<div class="preset-grid">
				{#each presets as preset}
					<button class="preset-card" type="button" onclick={() => applyPreset(preset)}>
						<div class="preset-head">
							<strong>{preset.name}</strong>
							<span class={`badge ${presetEngineKind(preset) === 'cloud' ? 'badge-cloud' : ''}`}>
								支持 {presetEngineLabel(preset)}
							</span>
						</div>
						<p class="preset-scene">{preset.scene}</p>
						<small class="preset-description">{preset.description}</small>
					</button>
				{/each}
			</div>

			<div class="row input-toolbar">
				<label class="input-label" for="generate-text">输入要合成的文本</label>
			</div>
			<textarea id="generate-text" bind:value={text} placeholder="输入要合成的文本"></textarea>
			<div class="row tool-row" id="text-tools">
				<div class="row wrap tool-actions">
					<span class="muted">{text.length} 字</span>
					<button class="btn" onclick={() => runTextTool('clean')} disabled={textToolBusy !== ''}>
						<Wand2 size={15} /> {textToolBusy === 'clean' ? '清洗中' : '清洗文本'}
					</button>
					<button class="btn" onclick={() => runTextTool('numbers')} disabled={textToolBusy !== ''}>
						<Hash size={15} /> {textToolBusy === 'numbers' ? '处理中' : '数字规范'}
					</button>
					<button class="btn" onclick={() => runTextTool('split')} disabled={!text.trim() || textToolBusy !== ''}>
						<Scissors size={15} /> {textToolBusy === 'split' ? '分句中' : '分句预览'}
					</button>
				</div>
				<button class="btn primary" disabled={busy || !text.trim()} onclick={generate}>
					<Send size={15} /> {busy ? '生成中' : '生成'}
				</button>
			</div>

			{#if showSplitPreview && textSegments.length}
				<div class="split-preview">
					<div class="row" style="justify-content:space-between">
						<div>
							<h3>智能分句预览</h3>
							<p class="muted">共 {textSegments.length} 段，用来提前检查停顿和节奏。</p>
						</div>
						<button class="btn" type="button" onclick={() => (showSplitPreview = false)}>收起</button>
					</div>
					<div class="segment-list">
						{#each textSegments as segment, index}
							<div class="segment-card">
								<span class="badge">{index + 1}</span>
								<p>{segment}</p>
							</div>
						{/each}
					</div>
				</div>
			{/if}

			{#if error}
				<div class="badge fail">{error}</div>
			{/if}

			<div class="result-panel stack" id="records">
				<div class="row section-head result-headline">
					<div>
						<h2>结果与记录</h2>
						<p class="muted">统一查看成功、失败和进行中的任务；支持搜索、筛选、分页、删除。</p>
					</div>
					<div class="summary-inline">
						<span class="muted">{filteredTasks.length} 条匹配</span>
						{#if selectedTaskIds.length}<span class="badge ok">已选 {selectedTaskIds.length}</span>{/if}
					</div>
				</div>

				<div class="segmented" role="tablist" aria-label="任务筛选">
					<button class:active={taskStatusTab === 'all'} type="button" onclick={() => { taskStatusTab = 'all'; currentPage = 1; }}>
						全部
						<span>{statusCounts.all}</span>
					</button>
					<button class:active={taskStatusTab === 'active'} type="button" onclick={() => { taskStatusTab = 'active'; currentPage = 1; }}>
						进行中
						<span>{statusCounts.active}</span>
					</button>
					<button class:active={taskStatusTab === 'success'} type="button" onclick={() => { taskStatusTab = 'success'; currentPage = 1; }}>
						成功
						<span>{statusCounts.success}</span>
					</button>
					<button class:active={taskStatusTab === 'failed'} type="button" onclick={() => { taskStatusTab = 'failed'; currentPage = 1; }}>
						异常
						<span>{statusCounts.failed}</span>
					</button>
				</div>

				<div class="toolbar-grid">
					<label class="field">
						<span>搜索</span>
						<div class="search-field">
							<Search size={15} />
							<input bind:value={taskQuery} placeholder="文本、音色、引擎" oninput={() => (currentPage = 1)} />
						</div>
					</label>
					<label class="field">
						<span>引擎</span>
						<select bind:value={taskEngineFilter} onchange={() => (currentPage = 1)}>
							{#each taskEngineOptions as option}
								<option value={option}>{option === 'all' ? '全部引擎' : engineMap.get(option)?.manifest.display_name ?? option}</option>
							{/each}
						</select>
					</label>
					<label class="field">
						<span>来源</span>
						<select bind:value={taskSourceFilter} onchange={() => (currentPage = 1)}>
							<option value="all">全部</option>
							<option value="local">本地</option>
							<option value="cloud">云端</option>
						</select>
					</label>
					<label class="field">
						<span>日期</span>
						<select bind:value={taskDateFilter} onchange={() => (currentPage = 1)}>
							<option value="all">全部时间</option>
							<option value="today">最近 24 小时</option>
							<option value="7d">最近 7 天</option>
							<option value="30d">最近 30 天</option>
						</select>
					</label>
					<label class="field">
						<span>排序</span>
						<select bind:value={taskSortBy} onchange={() => (currentPage = 1)}>
							<option value="latest">最新优先</option>
							<option value="oldest">最早优先</option>
							<option value="duration_desc">音频时长最长</option>
						</select>
					</label>
					<label class="field">
						<span>每页</span>
						<select bind:value={pageSize} onchange={() => (currentPage = 1)}>
							<option value={8}>8 条</option>
							<option value={12}>12 条</option>
							<option value={24}>24 条</option>
						</select>
					</label>
				</div>

				<div class="row wrap action-row">
					<button class="btn" onclick={toggleVisibleSelection} disabled={!visibleSelectableTasks.length}>
						{allVisibleSelected ? '取消全选当前页' : '全选当前页'}
					</button>
					<button class="btn danger" onclick={deleteSelectedTasks} disabled={!selectedTaskIds.length}>
						<Trash2 size={15} /> 批量删除
					</button>
					{#if hasActiveFilters}
						<button class="btn" onclick={clearTaskFilters}>
							<RotateCcw size={15} /> 重置筛选
						</button>
					{/if}
				</div>

				{#if pagedTasks.length}
					<div class="result-grid">
						{#each pagedTasks as task}
							<article class={`card stack result-card engine-surface ${engineKind(task.engine_id) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
								<div class="row result-head">
									<div class="row title-row">
										<input
											type="checkbox"
											checked={selectedTaskIds.includes(task.task_id)}
											disabled={!taskCanDelete(task)}
											onchange={(event) =>
												toggleTaskSelection(
													task.task_id,
													(event.currentTarget as HTMLInputElement).checked
												)}
										/>
										<strong class="result-title" title={displayTitle(task)}>{displayTitle(task)}</strong>
									</div>
									<span
										class="badge result-status"
										class:ok={task.status === 'success'}
										class:fail={task.status === 'failed'}
										class:warn={task.status === 'cancelled' || taskIsActive(task)}
									>
										{taskStatusLabel(task.status)}
									</span>
								</div>

								<div class="row wrap result-meta">
									<span class="badge badge-kind">{engineTypeLabel(task.engine_id)}</span>
									<span class="badge engine">{engineMap.get(task.engine_id)?.manifest.display_name ?? task.engine_id}</span>
									{#if voiceName(task)}<span class="badge">{voiceName(task)}</span>{/if}
									{#if task.created_at}<span class="badge">{formatTime(task.created_at)}</span>{/if}
								</div>

								<div class="row result-info">
									<p class="muted result-subline">{taskTimingLine(task)}</p>
									<div class="row wrap result-info-right">
										{#if task.result_duration_ms}
											<span class="badge">{formatAudioDuration(task.result_duration_ms)}</span>
										{/if}
										<button
											type="button"
											class="meta-pop compact"
											data-text={taskParameterText(task)}
											aria-label="查看生成参数"
											title="查看生成参数"
										>
											<SlidersHorizontal size={13} />
										</button>
									</div>
								</div>

								{#if taskIsActive(task)}
									<div class="progress-block">
										<div class="row" style="justify-content:space-between">
											<span class="muted">{taskStageLabel(task)}</span>
											<span class="badge">{progressLabel(task)}</span>
										</div>
										<div class="progress-track">
											<div class="progress-fill" style={`width:${Math.max(8, Math.round((task.progress || 0) * 100))}%`}></div>
										</div>
										<div class="row wrap progress-foot">
											<span class="muted">已运行 {elapsedLabel(task)}</span>
											{#if taskEtaLabel(task)}<span class="muted">{taskEtaLabel(task)}</span>{/if}
										</div>
									</div>
								{/if}

								{#if task.result_id}
									<audio class="audio" controls src={`/api/history/${task.result_id}/audio`}></audio>
								{/if}

								<div class="row wrap card-actions">
									{#if task.status === 'failed'}
										<button class="btn" onclick={() => retry(task)} disabled={actionBusyTaskId === task.task_id}>
											<RotateCcw size={15} /> 重试
										</button>
									{/if}
									{#if taskCanDelete(task)}
										<button class="btn" onclick={() => reuse(task)} disabled={actionBusyTaskId === task.task_id}>
											<RotateCcw size={15} /> 复用
										</button>
									{/if}
									{#if task.result_id}
										<a class="btn" href={`/api/history/${task.result_id}/audio`}>
											<Download size={15} /> 下载
										</a>
									{/if}
									{#if taskIsActive(task)}
										<button class="btn danger" onclick={() => cancel(task)} disabled={actionBusyTaskId === task.task_id}>
											取消
										</button>
									{:else}
										<button class="btn danger" onclick={() => deleteTaskRecord(task)} disabled={actionBusyTaskId === task.task_id}>
											<Trash2 size={15} /> 删除
										</button>
									{/if}
								</div>

								{#if task.error_message}
									<p class="muted error-line">{task.error_message}</p>
								{/if}
							</article>
						{/each}
					</div>

					{#if pageCount > 1}
						<div class="pagination-bar">
							<button class="btn" onclick={() => taskPageJump(-1)} disabled={currentPage <= 1}>
								<ChevronLeft size={15} /> 上一页
							</button>
							<span class="muted">第 {currentPage} / {pageCount} 页</span>
							<button class="btn" onclick={() => taskPageJump(1)} disabled={currentPage >= pageCount}>
								下一页 <ChevronRight size={15} />
							</button>
						</div>
					{/if}
				{:else}
					<div class="empty">
						当前筛选下没有任务记录，可以放宽筛选条件，或者直接开始一条新的生成。
					</div>
				{/if}
			</div>
		</section>

		<aside class="panel stack sticky-aside">
			<div class="row" style="justify-content:space-between">
				<h2><Play size={16} /> 参数</h2>
				<button class="btn" type="button" onclick={() => (showAdvanced = !showAdvanced)}>
					{showAdvanced ? '收起高级' : '高级参数'}
				</button>
			</div>

			<div class="field">
				<label for="engine">引擎</label>
				<select id="engine" bind:value={engineId}>
					{#each ttsEngines as engine}
						<option value={engine.manifest.engine_id}>
							{engine.manifest.display_name} · {engineStatusLabel(engine.state.status)}
						</option>
					{/each}
				</select>
			</div>

			{#if !isMimoPreset && !isMimoDesign}
				<div class="field">
					<label for="voice">声音</label>
					<select id="voice" bind:value={voiceId}>
						<option value="">未选择</option>
						{#each voiceChoices as voice}
							<option value={voice.voice_id}>{voice.name}</option>
						{/each}
					</select>
					{#if isMimoClone}
						<small>只显示已授权且允许云端复刻的本地参考音色。</small>
					{/if}
				</div>
			{/if}

			{#if isMimoPreset}
				<div class="field">
					<label for="mimo-voice">MiMo 官方音色</label>
					<select id="mimo-voice" bind:value={mimoVoice}>
						{#each mimoVoiceOptions as option}
							<option value={option.value}>{option.label}</option>
						{/each}
					</select>
				</div>
			{/if}

			<div class="field">
				<label for="language">语言</label>
				<select id="language" bind:value={language}>
					<option value="zh">中文</option>
					<option value="en">英文</option>
					<option value="auto">自动</option>
				</select>
			</div>

			{#if isMimo}
				{#if isMimoDesign}
					<div class="field">
						<label for="voice-design-prompt">音色描述</label>
						<textarea id="voice-design-prompt" bind:value={voiceDesignPrompt}></textarea>
						<small>描述声音本身，例如年龄、性别、质感、语速和情绪底色。</small>
					</div>
				{:else}
					<div class="field">
						<label for="style-instruction">风格指令</label>
						<textarea
							id="style-instruction"
							bind:value={styleInstruction}
							placeholder="例如：语速稍慢，语气温柔，像知识视频旁白。"
						></textarea>
					</div>
				{/if}
				{#if isMimoClone && settings?.mimo_voiceclone_confirm_upload}
					<small>生成前会再次提醒：本次参考音频将发送到 MiMo 云端。</small>
				{/if}
			{/if}

			{#if supportsEmotion}
				<div class="field">
					<label for="emotion">情绪</label>
					<select id="emotion" bind:value={emotion}>
						<option value="">跟随参考音色</option>
						<option value="calm">自然 calm</option>
						<option value="happy">高兴 happy</option>
						<option value="sad">悲伤 sad</option>
						<option value="angry">愤怒 angry</option>
						<option value="afraid">恐惧 afraid</option>
						<option value="disgusted">反感 disgusted</option>
						<option value="melancholic">低落 melancholic</option>
						<option value="surprised">惊讶 surprised</option>
					</select>
					<small>
						{followsReferenceEmotion
							? '当前不会额外叠加情绪向量，会尽量贴近参考音色本身。'
							: '当前会叠加情绪控制；如果想更贴参考音色，改回“跟随参考音色”。'}
					</small>
				</div>

				{#if isIndexTTS && !followsReferenceEmotion}
					<div class="field">
						<div class="field-head">
							<label for="emo-alpha">情绪强度</label>
							<span class="field-value">{emoAlpha.toFixed(2)}</span>
						</div>
						<input id="emo-alpha" type="range" min="0" max="1" step="0.05" bind:value={emoAlpha} />
						<small>数值越高，表演感越强；长文本通常不宜过高。</small>
					</div>
				{/if}
			{/if}

			{#if isOmniVoice && !voiceId}
				<div class="field">
					<label for="voice-design">声音设计标签</label>
					<select id="voice-design" bind:value={voiceDesign}>
						<option value="女，青年，中音调">女，青年，中音调</option>
						<option value="男，青年，中音调">男，青年，中音调</option>
						<option value="女，中年，高音调">女，中年，高音调</option>
						<option value="男，中年，低音调">男，中年，低音调</option>
						<option value="女，青年，耳语">女，青年，耳语</option>
					</select>
				</div>
			{/if}

			<div class="field">
				<div class="field-head">
					<label for="speed">语速</label>
					<span class="field-value">{speed.toFixed(2)}</span>
				</div>
				<input id="speed" type="range" min="0.5" max="2" step="0.05" bind:value={speed} />
				<small>低于 1 更稳更慢，高于 1 更适合短视频快讲。</small>
			</div>

			<div class="field">
				<label for="format">输出格式</label>
				<select id="format" bind:value={outputFormat}>
					<option value="wav">WAV</option>
					<option value="mp3">MP3</option>
					<option value="flac">FLAC</option>
				</select>
			</div>

			{#if showAdvanced}
				<div class="advanced-panel stack">
					<div class="field">
						<div class="field-head">
							<label for="temp">随机性 Temperature</label>
							<span class="field-value">{temperature.toFixed(2)}</span>
						</div>
						<input id="temp" type="range" min="0.1" max="2" step="0.05" bind:value={temperature} />
						<small>越低越稳定，越高变化越多，也更可能口齿漂移。</small>
					</div>

					<div class="field">
						<div class="field-head">
							<label for="top-p">采样范围 Top-P</label>
							<span class="field-value">{topP.toFixed(2)}</span>
						</div>
						<input id="top-p" type="range" min="0" max="1" step="0.05" bind:value={topP} />
						<small>限制模型从多大概率范围里选声音片段；默认 0.8 较稳。</small>
					</div>

					<div class="field">
						<div class="field-head">
							<label for="top-k">候选数量 Top-K</label>
							<span class="field-value">{topK}</span>
						</div>
						<input id="top-k" type="range" min="1" max="100" step="1" bind:value={topK} />
						<small>每一步最多保留多少候选；过大更自由，过小更保守。</small>
					</div>

					<div class="field">
						<div class="field-head">
							<label for="segment">分段长度 Token</label>
							<span class="field-value">{maxTextTokensPerSegment}</span>
						</div>
						<input id="segment" type="range" min="20" max="500" step="10" bind:value={maxTextTokensPerSegment} />
						<small>长文本会被拆段生成；短分段更利于剪辑和稳定停顿。</small>
					</div>

					<div class="field">
						<div class="field-head">
							<label for="silence">段间静默</label>
							<span class="field-value">{intervalSilence}ms</span>
						</div>
						<input id="silence" type="range" min="0" max="2000" step="50" bind:value={intervalSilence} />
						<small>控制分段之间的留白，便于字幕和剪辑卡点。</small>
					</div>

					{#if isIndexTTS}
						<div class="field">
							<div class="field-head">
								<label for="cfg">引导强度 CFG Rate</label>
								<span class="field-value">{cfgRate.toFixed(2)}</span>
							</div>
							<input id="cfg" type="range" min="0" max="1" step="0.05" bind:value={cfgRate} />
							<small>控制生成时贴合条件的力度；默认 0.7 适合大多数旁白。</small>
						</div>

						<div class="field">
							<div class="field-head">
								<label for="diffusion">扩散步数 Diffusion Steps</label>
								<span class="field-value">{diffusionSteps}</span>
							</div>
							<input id="diffusion" type="range" min="5" max="60" step="1" bind:value={diffusionSteps} />
							<small>步数越多越细致但更慢；25 是当前主力基线。</small>
						</div>
					{/if}
				</div>
			{/if}
		</aside>
	</div>
</main>

<style>
	.preset-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 10px;
	}

	.compose-panel {
		min-width: 0;
	}

	.section-head {
		justify-content: space-between;
	}

	.preset-card {
		text-align: left;
		display: grid;
		gap: 7px;
		border: 1px solid var(--line);
		background: #121519;
		color: var(--text);
		border-radius: 7px;
		padding: 11px;
	}

	.preset-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 8px;
	}

	.preset-head strong {
		font-size: 16px;
		line-height: 1.3;
	}

	.preset-scene,
	.preset-description {
		margin: 0;
		color: var(--muted);
		line-height: 1.45;
	}

	.preset-scene {
		font-size: 12px;
	}

	.preset-description {
		font-size: 13px;
	}

	.input-toolbar {
		margin-bottom: -4px;
	}

	.input-label {
		font-size: 13px;
		color: var(--text);
		font-weight: 600;
	}

	.tool-row {
		justify-content: space-between;
		align-items: flex-start;
		gap: 10px;
	}

	.tool-actions {
		gap: 8px;
	}

	.split-preview {
		display: grid;
		gap: 10px;
		padding: 11px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #121519;
	}

	.segment-list {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 10px;
	}

	.segment-card {
		display: grid;
		gap: 8px;
		padding: 10px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
	}

	.segment-card p {
		margin: 0;
		font-size: 13px;
		line-height: 1.5;
	}

	.result-panel {
		gap: 12px;
	}

	.result-headline {
		align-items: flex-end;
	}

	.summary-inline {
		display: inline-flex;
		align-items: center;
		gap: 8px;
	}

	.segmented {
		display: inline-flex;
		flex-wrap: wrap;
		gap: 6px;
		padding: 4px;
		border: 1px solid var(--line);
		border-radius: 10px;
		background: #111418;
	}

	.segmented button {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		min-height: 32px;
		padding: 6px 10px;
		border: 0;
		border-radius: 8px;
		background: transparent;
		color: var(--muted);
	}

	.segmented button span {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 22px;
		height: 22px;
		padding: 0 6px;
		border-radius: 999px;
		background: #1a1f26;
		font-size: 11px;
		color: #c8d0dc;
	}

	.segmented button.active {
		background: #1a2230;
		color: var(--text);
	}

	.segmented button.active span {
		background: rgba(79, 156, 249, 0.2);
		color: #b6d6ff;
	}

	.toolbar-grid {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
		gap: 10px;
		align-items: end;
	}

	.search-field {
		display: flex;
		align-items: center;
		gap: 8px;
		border: 1px solid var(--line);
		border-radius: 7px;
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
		padding: 0;
	}

	.action-row {
		gap: 8px;
	}

	.result-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		gap: 12px;
	}

	.result-card {
		gap: 9px;
		padding: 10px;
		min-width: 0;
	}

	.result-head {
		flex-wrap: nowrap;
		align-items: flex-start;
		justify-content: space-between;
		gap: 8px;
		min-width: 0;
	}

	.title-row {
		flex: 1 1 auto;
		min-width: 0;
		flex-wrap: nowrap;
		align-items: flex-start;
	}

	.result-title {
		display: block;
		flex: 1 1 auto;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 14px;
		line-height: 1.3;
	}

	.result-status {
		flex: 0 0 auto;
	}

	.result-meta {
		gap: 6px;
		min-width: 0;
	}

	.result-info {
		justify-content: space-between;
		align-items: center;
		gap: 8px;
		min-width: 0;
	}

	.result-info-right {
		margin-left: auto;
		gap: 6px;
	}

	.result-subline {
		margin: 0;
	}

	.progress-block {
		display: grid;
		gap: 8px;
		padding: 9px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
	}

	.progress-track {
		width: 100%;
		height: 8px;
		border-radius: 999px;
		background: #1a2027;
		overflow: hidden;
	}

	.progress-fill {
		height: 100%;
		border-radius: inherit;
		background: linear-gradient(90deg, #4f9cf9 0%, #42c49b 100%);
		transition: width 240ms ease;
		min-width: 8px;
	}

	.progress-foot {
		justify-content: space-between;
	}

	.card-actions {
		gap: 8px;
	}

	.error-line {
		margin: 0;
		font-size: 12px;
		line-height: 1.5;
	}

	.pagination-bar {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 12px;
		padding-top: 4px;
	}

	.sticky-aside {
		position: sticky;
		top: 72px;
	}

	.field small {
		color: var(--muted);
		font-size: 11px;
		line-height: 1.45;
	}

	.sticky-aside .field {
		gap: 4px;
		padding: 8px 0;
		border-bottom: 1px solid rgba(255, 255, 255, 0.04);
	}

	.sticky-aside .field > label,
	.field-head label {
		color: var(--text);
		font-size: 13px;
		font-weight: 600;
	}

	.field-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}

	.field-value {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 52px;
		padding: 2px 8px;
		border-radius: 999px;
		border: 1px solid rgba(255, 255, 255, 0.08);
		background: #171b22;
		color: #d6deea;
		font-size: 12px;
		line-height: 1.2;
	}

	.advanced-panel {
		gap: 0;
		padding: 4px 0 0;
		border-top: 1px solid rgba(255, 255, 255, 0.06);
	}

	.meta-pop.compact {
		width: 28px;
		height: 28px;
		padding: 0;
		border-radius: 8px;
		justify-content: center;
	}

	@media (max-width: 1380px) {
		.toolbar-grid {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}

	@media (max-width: 1180px) {
		.sticky-aside {
			position: static;
		}
	}

	@media (max-width: 900px) {
		.toolbar-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}

		.tool-row,
		.result-headline,
		.result-info,
		.pagination-bar {
			flex-direction: column;
			align-items: flex-start;
		}

		.result-info-right {
			margin-left: 0;
		}
	}

	@media (max-width: 640px) {
		.toolbar-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
