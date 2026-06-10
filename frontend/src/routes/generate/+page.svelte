<script lang="ts">
	import { Api } from '$lib/api';
	import type {
		AppSettings,
		EngineDetail,
		EngineSpeaker,
		GenerationTask,
		GeneratePlanResponse,
		GenerateRequest,
		LongformTask,
		PlannedTextSegment,
		PresetTemplate,
		TTSVerificationResponse,
		VoiceAsset
	} from '$lib/api/types';
	import { engineStatusLabel, taskStatusLabel, voiceAuthTags } from '$lib/labels';
	import {
		CheckSquare,
		ChevronLeft,
		ChevronRight,
		ChevronsLeft,
		ChevronsRight,
		Download,
		FileText,
		Hash,
		Info,
		Mic,
		Cpu,
		Pencil,
		Pause,
		Play,
		Plus,
		Repeat,
		RotateCcw,
		Save,
		Scissors,
		Search,
		Send,
		Settings,
		SlidersHorizontal,
		Sparkles,
		Square,
		Trash2,
		Wand2,
		X
	} from 'lucide-svelte';
	import { onMount } from 'svelte';

	type TaskStatusTab = 'all' | 'active' | 'success' | 'failed';
	type TaskSourceFilter = 'all' | 'local' | 'cloud';
	type TaskDateFilter = 'all' | 'today' | '7d' | '30d';
	type TaskSortBy = 'latest' | 'oldest' | 'duration_desc';
	type LongformStrategy = 'split_merge' | 'split_only' | 'single';
	type TextCueAction = {
		label: string;
		insert: string;
		hint: string;
		mode: 'prefix' | 'append';
	};
	type PresetDraft = {
		name: string;
		scene: string;
		description: string;
		tags: string;
		sample_text: string;
	};
	type ParameterEntry = {
		label: string;
		value: string;
	};
	type RuntimeProfile = {
		slowAfterSeconds: number;
		timeoutSeconds: number;
	};

	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let presets = $state<PresetTemplate[]>([]);
	let settings = $state<AppSettings | null>(null);
	let voicePreviewAudio = $state<HTMLAudioElement | null>(null);
	let resultPreviewAudio = $state<HTMLAudioElement | null>(null);
	let playingResultTaskId = $state('');
	let showPresetEditor = $state(false);
	let editingPresetId = $state('');
	let presetBusy = $state(false);
	let presetDraft = $state<PresetDraft>({
		name: '',
		scene: '',
		description: '',
		tags: '',
		sample_text: ''
	});

	let text = $state('');
	let textSegments = $state<string[]>([]);
	let textToolBusy = $state<'clean' | 'numbers' | 'split' | ''>('');
	let showSplitPreview = $state(false);
	let splitPreviewCollapsed = $state(false);
	let lastGeneratePlan = $state<GeneratePlanResponse | null>(null);

	let engineId = $state('indextts-v2');
	let voiceId = $state('');
	let language = $state('zh');
	let emotion = $state('');
	let voiceDesign = $state('女，青年，中音调');
	let voiceDesignPrompt = $state('中年男性，声线沉稳偏正式，吐字工整，语速适中。');
	let optimizeTextPreview = $state(false);
	let styleInstruction = $state('');
	let mimoVoice = $state('mimo_default');
	let speakerId = $state('');
	let speakerQuery = $state('');
	let speakerGenderFilter = $state<'all' | 'F' | 'M'>('all');
	let speakerCatalog = $state<EngineSpeaker[]>([]);
	let speakerCatalogLoading = $state(false);
	let speakerCatalogKey = $state('');
	let voicePrompt = $state('');
	let emoAlpha = $state(0.6);
	let speed = $state(1.0);
	let nfeStep = $state(32);
	let cfgStrength = $state(2.0);
	let targetRms = $state(0.1);
	let crossFadeDuration = $state(0.15);
	let removeSilence = $state(false);
	let temperature = $state(0.8);
	let topP = $state(0.8);
	let topK = $state(30);
	let maxTextTokensPerSegment = $state(120);
	let intervalSilence = $state(200);
	let diffusionSteps = $state(25);
	let cfgRate = $state(0.7);
	let outputFormat = $state<'wav' | 'mp3' | 'flac'>('wav');
	let showAdvanced = $state(false);
	let showMoreParams = $state(false);

	let tasks = $state<GenerationTask[]>([]);
	let longformTasks = $state<LongformTask[]>([]);
	let selectedTaskIds = $state<string[]>([]);
	let taskQuery = $state('');
	let taskStatusTab = $state<TaskStatusTab>('all');
	let taskEngineFilter = $state('all');
	let taskSourceFilter = $state<TaskSourceFilter>('all');
	let taskDateFilter = $state<TaskDateFilter>('all');
	let taskSortBy = $state<TaskSortBy>('latest');
	let currentPage = $state(1);
	let pageSize = $state(12);
	let pageSizeAuto = $state(true);
	let resultGridEl: HTMLDivElement | undefined = $state();
	let pageJumpInput = $state('');
	let actionBusyTaskId = $state('');
	let verificationBusyTaskId = $state('');
	let verificationReports = $state<Record<string, TTSVerificationResponse>>({});
	let verificationErrors = $state<Record<string, string>>({});
	let showLongformDialog = $state(false);
	let pendingLongformPlan = $state<GeneratePlanResponse | null>(null);
	let pendingLongformResolve = $state<((value: LongformStrategy | null) => void) | null>(null);
	let longformStrategy = $state<LongformStrategy>('split_merge');
	let longformVerifyEnabled = $state(true);
	let longformMergeEnabled = $state(true);
	let longformMaxRetries = $state(2);

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

	const F5_DEFAULTS = {
		nfeStep: 32,
		cfgStrength: 2.0,
		targetRms: 0.1,
		crossFadeDuration: 0.15,
		removeSilence: false
	};

	const RUNTIME_PROFILES: Record<string, RuntimeProfile> = {
		omnivoice: { slowAfterSeconds: 480, timeoutSeconds: 600 },
		'indextts-v2': { slowAfterSeconds: 150, timeoutSeconds: 420 },
		emotivoice: { slowAfterSeconds: 150, timeoutSeconds: 420 },
		'f5-tts': { slowAfterSeconds: 210, timeoutSeconds: 600 },
		'cosyvoice-sft': { slowAfterSeconds: 360, timeoutSeconds: 900 },
		'cosyvoice-zero-shot': { slowAfterSeconds: 360, timeoutSeconds: 900 },
		'mimo-v2.5-tts-preset': { slowAfterSeconds: 90, timeoutSeconds: 300 },
		'mimo-v2.5-tts-voicedesign': { slowAfterSeconds: 90, timeoutSeconds: 300 },
		'mimo-v2.5-tts-voiceclone': { slowAfterSeconds: 120, timeoutSeconds: 300 }
	};

	function statusIsActive(status: string) {
		return ['pending', 'queued', 'running', 'postprocessing', 'retrying'].includes(status);
	}

	function taskIsActive(task: GenerationTask) {
		return statusIsActive(task.status);
	}

	function taskIsWaiting(task: GenerationTask) {
		return task.status === 'pending' || task.status === 'queued';
	}

	function taskIsProcessing(task: GenerationTask) {
		return task.status === 'running' || task.status === 'postprocessing' || task.status === 'retrying';
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
	const activeParamKeys = $derived(new Set(selected?.manifest.parameter_schema.map((p) => p.key) ?? []));
	const ttsEngines = $derived(engines.filter((e) => !e.manifest.capabilities.includes('speech_recognition')));
	const selectedVoice = $derived(voices.find((v) => v.voice_id === voiceId) ?? null);
	const selectedVoicePreviewUrl = $derived(
		selectedVoice?.reference_audio_ids[0]
			? `/api/voices/${selectedVoice.voice_id}/audio/${selectedVoice.reference_audio_ids[0]}`
			: ''
	);
	const voiceMap = $derived(new Map(voices.map((voice) => [voice.voice_id, voice])));
	const engineMap = $derived(new Map(engines.map((engine) => [engine.manifest.engine_id, engine])));
	const supportsEmotion = $derived(activeParamKeys.has('emotion'));
	const isIndexTTS = $derived(engineId === 'indextts-v2');
	const isOmniVoice = $derived(engineId === 'omnivoice');
	const isMimoPreset = $derived(engineId === 'mimo-v2.5-tts-preset');
	const isMimoDesign = $derived(engineId === 'mimo-v2.5-tts-voicedesign');
	const isMimoClone = $derived(engineId === 'mimo-v2.5-tts-voiceclone');
	const isMimo = $derived(engineId.startsWith('mimo-v2.5'));
	const isEmotiVoice = $derived(engineId === 'emotivoice');
	const isF5 = $derived(engineId === 'f5-tts');
	const isCosyVoice = $derived(engineId === 'cosyvoice-sft');
	const isCosyVoiceZeroShot = $derived(engineId === 'cosyvoice-zero-shot');
	const usesReferenceVoice = $derived(isIndexTTS || isOmniVoice || isMimoClone || isF5 || isCosyVoiceZeroShot);
	const followsReferenceEmotion = $derived(isIndexTTS && !emotion);
	const hasAdvancedParameters = $derived(
		['temperature', 'top_p', 'top_k', 'max_text_tokens_per_segment', 'interval_silence', 'diffusion_steps', 'cfg_rate', 'optimize_text_preview', 'nfe_step', 'cfg_strength', 'target_rms', 'cross_fade_duration', 'remove_silence'].some(
			(key) => activeParamKeys.has(key)
		)
	);
	const enginePresets = $derived(presets.filter((preset) => preset.engine_id === engineId));
	const textCueActions = $derived.by<TextCueAction[]>(() => {
		if (isMimo) {
			return [
				{
					label: '风格前缀',
					insert: '(温柔自然)',
					mode: 'prefix',
					hint: 'MiMo 支持在合成文本中放风格或音频标签；正文仍放在文本框，整体风格建议优先写到“风格指令”。'
				},
				{
					label: '停顿标签',
					insert: '[停顿]',
					mode: 'append',
					hint: '用于 MiMo 文本内的短暂停顿提示；不要在 IndexTTS 参数里期待这个标签生效。'
				}
			];
		}
		if (isIndexTTS) {
			return [
				{
					label: '拼音标注',
					insert: '行(HANG2)',
					mode: 'append',
					hint: 'IndexTTS 可用拼音标注辅助多音字纠错；只在需要纠正读音时插入。'
				}
			];
		}
		if (isEmotiVoice) {
			return [
				{
					label: '更开心',
					insert: '（开心）',
					mode: 'prefix',
					hint: 'EmotiVoice 主要通过“说话人 + 情绪提示”控制，不吃本地参考音色；这个按钮只给正文加情绪语境。'
				},
				{
					label: '更平静',
					insert: '（中立）',
					mode: 'prefix',
					hint: '配合右侧“情绪提示”使用。官方推理格式是 speaker、prompt、phoneme、content。'
				}
			];
		}
		if (isF5 || isCosyVoiceZeroShot) {
			return [
				{
					label: '参考台词',
					insert: '请先在音色库补全参考音频实际台词。',
					mode: 'append',
					hint: 'F5-TTS 和 CosyVoice Zero-Shot 都依赖参考音频对应台词；台词不准确会明显影响贴近度。'
				}
			];
		}
		if (isOmniVoice) {
			return [
				{
					label: '非语言标签',
					insert: '[笑]',
					mode: 'append',
					hint: 'OmniVoice 适合短句里尝试非语言标签；复杂声线优先用声音设计标签。'
				}
			];
		}
		return [];
	});
	const mimoVoiceOptions = $derived(selected?.manifest.parameter_schema.find((p) => p.key === 'mimo_voice')?.options ?? []);
	const speakerOptions = $derived(selected?.manifest.parameter_schema.find((p) => p.key === 'speaker_id')?.options ?? []);
	const speakerChoices = $derived(
		isEmotiVoice && speakerCatalog.length
			? speakerCatalog.map((speaker) => ({ label: speaker.label, value: speaker.speaker_id }))
			: speakerOptions
	);
	const promptOptions = $derived(selected?.manifest.parameter_schema.find((p) => p.key === 'prompt')?.options ?? []);

	function voiceOptionLabel(voice: VoiceAsset) {
		const tags = voiceAuthTags(voice.tags);
		return tags.length ? `${voice.name}（${tags.join('、')}）` : voice.name;
	}
	const hasRunningTasks = $derived(
		tasks.some((task) => taskIsActive(task) || taskVerificationPending(task)) ||
			longformTasks.some((task) => statusIsActive(task.status))
	);
	const visibleLongformTasks = $derived(
		longformTasks.filter((task) => task.status !== 'success')
	);
	const engineRuntimeHint = $derived.by(() => {
		const trimmedLength = text.trim().length;
		if (isOmniVoice && selectedVoice?.reference_audio_ids.length && !selectedVoice.reference_text.trim()) {
			return '当前音色缺少参考台词，已跳过 OmniVoice 自动听写以避免长时间卡住；后续补台词可提升克隆稳定性。';
		}
		if (isOmniVoice && trimmedLength > 90) {
			return 'OmniVoice 更适合先用短句确认音色和语气；长文本或复杂参考音色更容易等待很久，甚至触发超时。';
		}
		if (isMimoClone && trimmedLength > 160) {
			return 'MiMo 音色复刻建议先用短段试听；确认贴近度后再继续更长的文本。';
		}
		if (isF5 && selectedVoice?.reference_audio_ids.length && !selectedVoice.reference_text.trim()) {
			return 'F5-TTS 需要参考音频对应台词；当前音色缺少 reference_text，会被后端拦截以避免自动听写和额外下载。';
		}
		if (isCosyVoiceZeroShot && selectedVoice?.reference_audio_ids.length && !selectedVoice.reference_text.trim()) {
			return 'CosyVoice Zero-Shot 需要参考音频对应台词；目标文本太短或台词不准都会降低贴近参考音色的效果。';
		}
		if (isCosyVoice && trimmedLength > 120) {
			return 'CosyVoice 首次加载模型较慢，建议先用短句确认预置音色，再跑长文本。';
		}
		if (isIndexTTS && speed > 1.25 && trimmedLength > 160) {
			return '当前文本较长且语速偏快，建议先做短段试听，确认稳定性后再整段生成。';
		}
		return '';
	});
	const textLengthStatus = $derived.by(() => textLengthStatusFor(engineId, text.trim().length));
	const inputSubtitle = $derived.by(() =>
		textLengthStatus.level === 'direct'
			? '短文本会直接生成，完成后可接入自动校对内容完整性。'
			: textLengthStatus.level === 'recommended'
				? '当前文本较长，建议先查看分段计划，减少漏句、截断和长时间等待。'
				: '当前文本已经超过强提醒阈值，建议分段生成并校对后再合并。'
	);

	const statusCounts = $derived.by(() => ({
		all: tasks.length,
		active: tasks.filter((task) => taskIsActive(task)).length,
		success: tasks.filter((task) => taskIsSuccess(task)).length,
		failed: tasks.filter((task) => taskIsFailed(task)).length
	}));

	const queueOrderedTasks = $derived.by(() =>
		tasks
			.filter((task) => taskIsActive(task))
			.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.task_id.localeCompare(b.task_id))
	);
	const queueCounts = $derived.by(() => ({
		processing: tasks.filter((task) => taskIsProcessing(task)).length,
		waiting: tasks.filter((task) => taskIsWaiting(task)).length
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
			const activeRank = (task: GenerationTask) =>
				taskIsProcessing(task) ? 0 : taskIsWaiting(task) ? 1 : 2;
			const rankDelta = activeRank(a) - activeRank(b);
			if (rankDelta !== 0) return rankDelta;
			if (taskIsActive(a) && taskIsActive(b)) {
				return a.created_at.localeCompare(b.created_at) || a.task_id.localeCompare(b.task_id);
			}
			if (taskSortBy === 'latest' || taskSortBy === 'oldest') {
				const longformDelta = compareLongformGroupOrder(a, b, filtered, taskSortBy);
				if (longformDelta !== null) return longformDelta;
			}
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
		const [e, v, t, lf, p, s] = await Promise.all([
			Api.engines(),
			Api.voices(),
			Api.tasks(),
			Api.longformTasks(),
			Api.presets(),
			Api.settings()
		]);
		engines = e;
		voices = v;
		tasks = t;
		longformTasks = lf;
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

	async function loadSpeakerCatalog(engine: string, query: string, gender: 'all' | 'F' | 'M') {
		if (engine !== 'emotivoice') {
			speakerCatalog = [];
			return;
		}
		const key = `${engine}|${query}|${gender}`;
		speakerCatalogKey = key;
		speakerCatalogLoading = true;
		try {
			const items = await Api.engineSpeakers(engine, { q: query, gender, limit: query ? 120 : 40 });
			if (speakerCatalogKey !== key) return;
			speakerCatalog = items;
			if (!speakerId && items[0]) speakerId = items[0].speaker_id;
		} finally {
			if (speakerCatalogKey === key) speakerCatalogLoading = false;
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
			nfeStep = F5_DEFAULTS.nfeStep;
			cfgStrength = F5_DEFAULTS.cfgStrength;
			targetRms = F5_DEFAULTS.targetRms;
			crossFadeDuration = F5_DEFAULTS.crossFadeDuration;
			removeSilence = F5_DEFAULTS.removeSilence;
			const speakerParam = selected?.manifest.parameter_schema.find((param) => param.key === 'speaker_id');
			const promptParam = selected?.manifest.parameter_schema.find((param) => param.key === 'prompt');
			speakerId = String(speakerParam?.default ?? speakerParam?.options?.[0]?.value ?? '');
			voicePrompt = String(promptParam?.default ?? promptParam?.options?.[0]?.value ?? '');
			showAdvanced = engineId === 'f5-tts';
			lastEngineId = engineId;
		}
	});

	$effect(() => {
		loadSpeakerCatalog(engineId, speakerQuery.trim(), speakerGenderFilter);
	});

	$effect(() => {
		if (!initialized) return;
		if (!usesReferenceVoice) {
			voiceId = '';
			return;
		}
		if (voiceId && !voices.some((voice) => voice.voice_id === voiceId)) {
			voiceId = '';
		}
	});

	$effect(() => {
		if (currentPage > pageCount) currentPage = pageCount;
	});

	function requestBody(): GenerateRequest {
		const usesEmotionControl = supportsEmotion && Boolean(emotion);
		const referenceVoiceId = usesReferenceVoice ? voiceId || null : null;
		const referenceText =
			referenceVoiceId && selectedVoice?.reference_text.trim()
				? selectedVoice.reference_text.trim()
				: null;
		return {
			text,
			engine_id: engineId,
			voice_id: referenceVoiceId,
			ref_text: referenceText,
			language,
			emotion_mode: usesEmotionControl ? 'emotion_vector' : 'follow_reference',
			emotion: usesEmotionControl ? emotion : null,
			emotion_text: isOmniVoice && !voiceId ? voiceDesign : null,
			style_instruction: isMimo ? styleInstruction || null : null,
			voice_design_prompt: isMimoDesign ? voiceDesignPrompt : null,
			optimize_text_preview: isMimoDesign ? optimizeTextPreview : false,
			mimo_voice: isMimoPreset ? mimoVoice : null,
			speaker_id: activeParamKeys.has('speaker_id') ? speakerId || null : null,
			prompt: activeParamKeys.has('prompt') ? voicePrompt || null : null,
			nfe_step: nfeStep,
			cfg_strength: cfgStrength,
			target_rms: targetRms,
			cross_fade_duration: crossFadeDuration,
			remove_silence: removeSilence,
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
		optimizeTextPreview = req.optimize_text_preview ?? false;
		styleInstruction = req.style_instruction || '';
		mimoVoice = req.mimo_voice || 'mimo_default';
		speakerId = req.speaker_id || '';
		voicePrompt = req.prompt || '';
		nfeStep = req.nfe_step ?? F5_DEFAULTS.nfeStep;
		cfgStrength = req.cfg_strength ?? F5_DEFAULTS.cfgStrength;
		targetRms = req.target_rms ?? F5_DEFAULTS.targetRms;
		crossFadeDuration = req.cross_fade_duration ?? F5_DEFAULTS.crossFadeDuration;
		removeSilence = req.remove_silence ?? F5_DEFAULTS.removeSilence;
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
			speaker_id:
				typeof preset.parameters.speaker_id === 'string'
					? preset.parameters.speaker_id
					: null,
			prompt:
				typeof preset.parameters.prompt === 'string'
					? preset.parameters.prompt
					: null,
			nfe_step: Number(preset.parameters.nfe_step ?? F5_DEFAULTS.nfeStep),
			cfg_strength: Number(preset.parameters.cfg_strength ?? F5_DEFAULTS.cfgStrength),
			target_rms: Number(preset.parameters.target_rms ?? F5_DEFAULTS.targetRms),
			cross_fade_duration: Number(
				preset.parameters.cross_fade_duration ?? F5_DEFAULTS.crossFadeDuration
			),
			remove_silence: Boolean(preset.parameters.remove_silence ?? F5_DEFAULTS.removeSilence),
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

	function currentPresetParameters() {
		const req = requestBody();
		const params: Record<string, unknown> = {
			language: req.language,
			output_format: req.output_format
		};
		const keys = selected?.manifest.parameter_schema.map((param) => param.key) ?? [];
		for (const key of keys) {
			const value = (req as unknown as Record<string, unknown>)[key];
			if (value !== undefined && value !== null && value !== '') params[key] = value;
		}
		return params;
	}

	function resetPresetDraft() {
		presetDraft = {
			name: '',
			scene: '',
			description: '',
			tags: '',
			sample_text: text
		};
		editingPresetId = '';
	}

	function openPresetEditor(preset?: PresetTemplate) {
		if (preset) {
			editingPresetId = preset.preset_id;
			presetDraft = {
				name: preset.name,
				scene: preset.scene,
				description: preset.description,
				tags: preset.tags.join('，'),
				sample_text: preset.sample_text
			};
		} else {
			resetPresetDraft();
		}
		showPresetEditor = true;
	}

	async function savePreset() {
		if (!presetDraft.name.trim()) return;
		presetBusy = true;
		try {
			const payload = {
				preset_id: editingPresetId || null,
				name: presetDraft.name.trim(),
				scene: presetDraft.scene.trim(),
				description: presetDraft.description.trim(),
				engine_id: engineId,
				sample_text: presetDraft.sample_text.trim() || text,
				parameters: currentPresetParameters(),
				source_test_id: null,
				recommended_voice_type: isOmniVoice && !voiceId ? 'voice_design' : 'reference_voice',
				tags: presetDraft.tags
					.split(/[，,]/)
					.map((tag) => tag.trim())
					.filter(Boolean)
			};
			const saved = editingPresetId
				? await Api.updatePreset(editingPresetId, payload)
				: await Api.createPreset(payload);
			presets = [saved, ...presets.filter((item) => item.preset_id !== saved.preset_id)];
			showPresetEditor = false;
			resetPresetDraft();
		} catch (cause) {
			error = (cause as Error).message;
		} finally {
			presetBusy = false;
		}
	}

	async function deletePreset(preset: PresetTemplate) {
		if (!preset.preset_id.startsWith('custom_')) return;
		const ok = window.confirm(`删除自定义预设「${preset.name}」吗？`);
		if (!ok) return;
		presetBusy = true;
		try {
			await Api.deletePreset(preset.preset_id);
			presets = presets.filter((item) => item.preset_id !== preset.preset_id);
		} catch (cause) {
			error = (cause as Error).message;
		} finally {
			presetBusy = false;
		}
	}

	function appendTextCue(action: TextCueAction) {
		const current = text.trim();
		if (action.mode === 'prefix') {
			text = current.startsWith(action.insert)
				? current
				: `${action.insert}${current ? ` ${current}` : ''}`;
			return;
		}
		text = current ? `${current} ${action.insert}` : action.insert;
	}

	async function previewSelectedVoice() {
		if (!voicePreviewAudio || !selectedVoicePreviewUrl) return;
		voicePreviewAudio.currentTime = 0;
		await voicePreviewAudio.play();
	}

	function resultAudioUrl(task: GenerationTask) {
		return task.result_id ? `/api/history/${task.result_id}/audio` : '';
	}

	async function toggleResultPlayback(task: GenerationTask) {
		const audioUrl = resultAudioUrl(task);
		if (!audioUrl || !resultPreviewAudio) return;
		if (playingResultTaskId === task.task_id && !resultPreviewAudio.paused) {
			resultPreviewAudio.pause();
			playingResultTaskId = '';
			return;
		}
		const absoluteUrl = new URL(audioUrl, window.location.href).href;
		if (resultPreviewAudio.src !== absoluteUrl) {
			resultPreviewAudio.src = audioUrl;
			resultPreviewAudio.currentTime = 0;
		}
		playingResultTaskId = task.task_id;
		try {
			await resultPreviewAudio.play();
		} catch (cause) {
			playingResultTaskId = '';
			error = (cause as Error).message;
		}
	}

	function upsertTask(task: GenerationTask) {
		tasks = [task, ...tasks.filter((item) => item.task_id !== task.task_id)];
	}

	function textLengthStatusFor(currentEngineId: string, length: number) {
		const policy =
			{
				omnivoice: { threshold: 120, hard: 220 },
				'indextts-v2': { threshold: 300, hard: 600 },
				'mimo-v2.5-tts-preset': { threshold: 600, hard: 1200 },
				'mimo-v2.5-tts-voiceclone': { threshold: 400, hard: 800 },
				'mimo-v2.5-tts-voicedesign': { threshold: 400, hard: 800 }
			}[currentEngineId] ?? { threshold: 300, hard: 600 };
		if (!length || length <= policy.threshold) {
			return { level: 'direct', label: '适合直接生成', className: 'ok' };
		}
		if (length > policy.hard) {
			return { level: 'strong', label: '强烈建议分段', className: 'warn' };
		}
		return { level: 'recommended', label: '建议分段', className: 'info' };
	}

	function prepareLongformPlan(plan: GeneratePlanResponse) {
		lastGeneratePlan = plan;
		textSegments = plan.segments.map((segment) => segment.text);
		showSplitPreview = plan.segments.length > 1;
		splitPreviewCollapsed = false;
	}

	function requestLongformStrategy(plan: GeneratePlanResponse): Promise<LongformStrategy | null> {
		prepareLongformPlan(plan);
		if (!plan.requires_user_confirmation) return Promise.resolve('single');
		pendingLongformPlan = plan;
		longformStrategy = plan.recommended_action === 'split_generate' ? 'split_only' : 'split_merge';
		longformVerifyEnabled = plan.recommended_action.includes('verify');
		longformMergeEnabled = longformStrategy === 'split_merge';
		longformMaxRetries = 2;
		showLongformDialog = true;
		return new Promise((resolve) => {
			pendingLongformResolve = resolve;
		});
	}

	function closeLongformDialog(value: LongformStrategy | null) {
		const resolve = pendingLongformResolve;
		showLongformDialog = false;
		pendingLongformResolve = null;
		pendingLongformPlan = null;
		resolve?.(value);
	}

	function longformSegmentsFor(plan: GeneratePlanResponse): PlannedTextSegment[] {
		return plan.segments.length
			? plan.segments
			: [{ index: 1, text: text.trim(), char_count: text.trim().length, segment_reason: 'direct_text' }];
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
			if ((isF5 || isCosyVoiceZeroShot) && !voiceId) {
				error = `${selected?.manifest.display_name ?? '当前模型'} 需要选择带参考音频和参考台词的本地音色。`;
				return;
			}
			if ((isF5 || isCosyVoiceZeroShot) && !selectedVoice?.reference_text.trim()) {
				error = `${selectedVoice?.name ?? '当前音色'} 缺少参考台词，请先在音色库补全 reference_text。`;
				return;
			}
			if (isMimoClone && !selectedVoicePreviewUrl) {
				error = '请选择一个带参考音频的本地音色。';
				return;
			}
			const plan = await Api.generatePlan({
				text: text.trim(),
				engine_id: engineId,
				planner_mode: 'auto',
				target_format: outputFormat
			});
			const longformChoice = await requestLongformStrategy(plan);
			if (!longformChoice) return;
			if (isMimoClone && settings?.mimo_voiceclone_confirm_upload) {
				const name = selectedVoice?.name ?? '当前参考音色';
				const ok = window.confirm(
					`MiMo 音色复刻会把「${name}」的本次参考音频发送到小米云端用于生成。继续吗？`
				);
				if (!ok) return;
			}
			const engine = engines.find((item) => item.manifest.engine_id === engineId);
			if (engine && engine.state.status !== 'loaded') await Api.startEngine(engineId);
			if (longformChoice !== 'single') {
				const res = await Api.generateLongform({
					generate_request: requestBody(),
					segments: longformSegmentsFor(plan),
					verify_enabled: longformVerifyEnabled,
					merge_enabled: longformChoice === 'split_merge' && longformMergeEnabled,
					max_retries: longformMaxRetries,
					stop_merge_on_verification_failed: true,
					asr_engine_id: 'qwen3-asr-mlx',
					silence_ms: 300,
					normalize: false
				});
				longformTasks = [res, ...longformTasks.filter((item) => item.longform_task_id !== res.longform_task_id)];
				currentPage = 1;
				return;
			}
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
			splitPreviewCollapsed = false;
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

	function taskVerificationLanguage(task: GenerationTask): 'auto' | 'zh' | 'en' {
		const value = textParam(task, 'language');
		return value === 'en' || value === 'auto' ? value : 'zh';
	}

	function verificationStatusLabel(status: TTSVerificationResponse['status']) {
		return {
			passed: '校对通过',
			warning: '需要复听',
			failed: '缺句风险',
			skipped: '未校对'
		}[status];
	}

	function taskVerificationReport(task: GenerationTask) {
		return verificationReports[task.task_id] ?? task.verification;
	}

	function taskVerificationError(task: GenerationTask) {
		return verificationErrors[task.task_id] || task.verification_error || '';
	}

	function taskVerificationPending(task: GenerationTask) {
		if (task.status !== 'success' || !task.result_id || taskVerificationReport(task) || taskVerificationError(task)) return false;
		const completed = new Date(task.completed_at ?? task.created_at).getTime();
		if (!Number.isFinite(completed)) return false;
		return Date.now() - completed < 20 * 60 * 1000;
	}

	async function verifyTask(task: GenerationTask) {
		if (!task.result_id) return;
		verificationBusyTaskId = task.task_id;
		verificationErrors = { ...verificationErrors, [task.task_id]: '' };
		try {
			const report = await Api.verifyTTSOutput({
				result_id: task.result_id,
				expected_text: task.input_text,
				asr_engine_id: 'qwen3-asr-mlx',
				language: taskVerificationLanguage(task)
			});
			verificationReports = { ...verificationReports, [task.task_id]: report };
			tasks = tasks.map((item) =>
				item.task_id === task.task_id ? { ...item, verification: report, verification_error: null } : item
			);
		} catch (cause) {
			verificationErrors = {
				...verificationErrors,
				[task.task_id]: (cause as Error).message || '校对失败，请检查 ASR 引擎是否可用。'
			};
		} finally {
			verificationBusyTaskId = '';
		}
	}

	function longformTitle(task: LongformTask) {
		return task.input_text.trim() || '长文本任务';
	}

	function longformStatusText(task: LongformTask) {
		const success = task.segments.filter((segment) => segment.status === 'success').length;
		return `${taskStatusLabel(task.status)} · ${success}/${task.segments.length} 段`;
	}

	function longformDownloadUrl(task: LongformTask) {
		return task.export_id ? `/api/longform/${task.longform_task_id}/download` : '';
	}

	function longformGroupSortTime(task: GenerationTask, group: GenerationTask[], sortBy: TaskSortBy) {
		const times = group
			.map((item) => new Date(item.created_at).getTime())
			.filter((time) => Number.isFinite(time));
		if (!times.length) return new Date(task.created_at).getTime() || 0;
		return sortBy === 'oldest' ? Math.min(...times) : Math.max(...times);
	}

	function longformItemRank(task: GenerationTask) {
		if (taskIsLongformExport(task)) return 0;
		if (taskIsLongformSegment(task)) return task.longform_segment_index ?? 999;
		return 999;
	}

	function compareLongformGroupOrder(
		a: GenerationTask,
		b: GenerationTask,
		scope: GenerationTask[],
		sortBy: TaskSortBy
	) {
		const groupA = a.longform_task_id ? scope.filter((item) => item.longform_task_id === a.longform_task_id) : [a];
		const groupB = b.longform_task_id ? scope.filter((item) => item.longform_task_id === b.longform_task_id) : [b];
		const groupKeyA = a.longform_task_id ?? a.task_id;
		const groupKeyB = b.longform_task_id ?? b.task_id;
		if (groupKeyA !== groupKeyB) {
			const timeA = longformGroupSortTime(a, groupA, sortBy);
			const timeB = longformGroupSortTime(b, groupB, sortBy);
			if (timeA !== timeB) return sortBy === 'oldest' ? timeA - timeB : timeB - timeA;
			return groupKeyA.localeCompare(groupKeyB);
		}
		const rankDelta = longformItemRank(a) - longformItemRank(b);
		if (rankDelta !== 0) return rankDelta;
		return a.created_at.localeCompare(b.created_at) || a.task_id.localeCompare(b.task_id);
	}

	function taskIsLongformSegment(task: GenerationTask) {
		return Boolean(task.longform_task_id && task.longform_segment_index && task.longform_segment_count);
	}

	function taskIsLongformExport(task: GenerationTask) {
		return Boolean(task.longform_task_id && task.task_type === 'export');
	}

	function longformResultLabel(task: GenerationTask) {
		if (taskIsLongformExport(task)) return '完整长文本';
		if (taskIsLongformSegment(task)) return `长文本 ${task.longform_segment_index}/${task.longform_segment_count}`;
		return '';
	}

	function longformResultTitle(task: GenerationTask) {
		if (taskIsLongformExport(task)) return '合并后的完整长文本音频';
		if (taskIsLongformSegment(task)) return `同一篇长文本的第 ${task.longform_segment_index} 段，共 ${task.longform_segment_count} 段`;
		return '';
	}

	async function retryLongform(task: LongformTask) {
		actionBusyTaskId = task.longform_task_id;
		try {
			const next = await Api.retryLongformFailed(task.longform_task_id);
			longformTasks = [next, ...longformTasks.filter((item) => item.longform_task_id !== next.longform_task_id)];
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

	// 自适应 pageSize：根据 resultGrid 可用高度和列数计算最佳条数
	$effect(() => {
		if (!pageSizeAuto || !resultGridEl) return;
		const observer = new ResizeObserver(() => {
			if (!resultGridEl) return;
			const rect = resultGridEl.getBoundingClientRect();
			const availableHeight = window.innerHeight - rect.top - 40;
			const cols = Math.max(1, Math.floor(rect.width / 260));
			const cardHeight = 95;
			const rows = Math.max(1, Math.floor(availableHeight / cardHeight));
			let ideal = Math.max(12, rows * cols);
			ideal = Math.ceil(ideal / cols) * cols;
			if (ideal !== pageSize) {
				pageSize = ideal;
				if (currentPage > Math.max(1, Math.ceil(filteredTasks.length / pageSize))) {
					currentPage = 1;
				}
			}
		});
		observer.observe(resultGridEl);
		return () => observer.disconnect();
	});

	function taskPageGoTo(page: number) {
		currentPage = Math.min(pageCount, Math.max(1, page));
	}

	function jumpToPage() {
		const n = parseInt(pageJumpInput, 10);
		if (Number.isFinite(n) && n >= 1 && n <= pageCount) {
			currentPage = n;
		}
		pageJumpInput = '';
	}

	function progressLabel(task: GenerationTask) {
		if (taskIsWaiting(task)) {
			const position = taskQueuePosition(task);
			return position ? `等待 ${position}` : '等待';
		}
		if (task.status === 'running') return `${Math.round((task.progress || 0) * 100)}%`;
		if (task.status === 'postprocessing') return '收尾';
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

	function waitingSeconds(task: GenerationTask) {
		const created = new Date(task.created_at).getTime();
		if (!Number.isFinite(created)) return 0;
		return Math.max(0, Math.floor((Date.now() - created) / 1000));
	}

	function elapsedLabel(task: GenerationTask) {
		const totalSeconds = elapsedSeconds(task);
		return formatSeconds(totalSeconds);
	}

	function formatSeconds(totalSeconds: number) {
		const minutes = Math.floor(totalSeconds / 60);
		const seconds = totalSeconds % 60;
		return `${minutes}:${seconds.toString().padStart(2, '0')}`;
	}

	function taskTimingLine(task: GenerationTask) {
		if (taskIsWaiting(task)) return `已等待 ${formatSeconds(waitingSeconds(task))}`;
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
		if (taskIsWaiting(task)) {
			const position = taskQueuePosition(task);
			return position ? `等待队列第 ${position} 位` : '等待后台接手';
		}
		if (task.status === 'cancelled') return '已取消';
		if (task.status === 'failed') return '已失败';
		if (task.status === 'success') return '已完成';
		if (task.status === 'postprocessing') return '后处理';
		if ((task.progress ?? 0) < 0.2) return '预热模型';
		if ((task.progress ?? 0) < 0.55) return '声学推理';
		if ((task.progress ?? 0) < 0.88) return '写入音频';
		return '收尾处理中';
	}

	function taskEtaLabel(task: GenerationTask) {
		if (taskIsWaiting(task)) return '';
		if (!taskIsActive(task) || !task.started_at) return '';
		const progress = task.progress ?? 0;
		const profile = RUNTIME_PROFILES[task.engine_id] ?? { slowAfterSeconds: 180, timeoutSeconds: 300 };
		const elapsed = elapsedSeconds(task);
		if (progress >= 0.9) {
			const remainingToTimeout = profile.timeoutSeconds - elapsed;
			return remainingToTimeout > 10 ? `保护窗口剩 ${formatSeconds(remainingToTimeout)}` : '';
		}
		if (progress < 0.18) return '';
		if (elapsed < 2) return '';
		const totalEstimate = elapsed / progress;
		const remaining = Math.max(0, Math.round(totalEstimate - elapsed));
		if (!Number.isFinite(remaining) || remaining <= 1) return '';
		return `预计剩余 ${formatSeconds(remaining)}`;
	}

	function taskRuntimeHint(task: GenerationTask) {
		if (taskIsWaiting(task)) {
			if (queueCounts.processing === 0) return '正在等待后台 worker 接手；服务恢复后会自动从最早任务开始。';
			const position = taskQueuePosition(task);
			return position && position > 1 ? `前面还有 ${position - 1} 条任务。` : '';
		}
		if (!taskIsActive(task) || !task.started_at) return '';
		const profile = RUNTIME_PROFILES[task.engine_id] ?? { slowAfterSeconds: 180, timeoutSeconds: 300 };
		const elapsed = elapsedSeconds(task);
		if (elapsed >= profile.timeoutSeconds) return '已超过超时保护窗口，等待后台收敛状态。';
		if (elapsed >= profile.slowAfterSeconds) return '已超过常规时长，仍在等待模型返回。';
		if ((task.progress ?? 0) >= 0.9) return '接近收尾，长音频可能会在最后阶段停留一会儿。';
		return '';
	}

	function taskQueuePosition(task: GenerationTask) {
		if (!taskIsWaiting(task)) return 0;
		return queueOrderedTasks.filter((item) => taskIsWaiting(item)).findIndex((item) => item.task_id === task.task_id) + 1;
	}

	function taskProgressWidth(task: GenerationTask) {
		if (taskIsWaiting(task)) return 0;
		return Math.max(8, Math.round((task.progress || 0) * 100));
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
		const title = task.input_text.trim() || '未命名任务';
		return taskIsLongformExport(task) ? `完整长文本：${title}` : title;
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

	function boolParam(task: GenerationTask, key: string) {
		const value = task.parameters[key];
		return typeof value === 'boolean' ? value : null;
	}

	function taskSupportsParam(task: GenerationTask, key: string) {
		const schema = engineMap.get(task.engine_id)?.manifest.parameter_schema ?? [];
		return schema.some((param) => param.key === key);
	}

	function presetEngineLabel(preset: PresetTemplate) {
		return engineMap.get(preset.engine_id)?.manifest.display_name ?? preset.engine_id;
	}

	function taskParameterEntries(task: GenerationTask): ParameterEntry[] {
		const entries: ParameterEntry[] = [
			{ label: '引擎', value: engineMap.get(task.engine_id)?.manifest.display_name ?? task.engine_id },
			{ label: '来源', value: engineTypeLabel(task.engine_id) }
		];
		const voice = voiceName(task);
		if (voice) entries.push({ label: '音色', value: voice });
		if (taskSupportsParam(task, 'language') && textParam(task, 'language'))
			entries.push({ label: '语言', value: textParam(task, 'language') ?? '' });
		if (taskSupportsParam(task, 'emotion') && textParam(task, 'emotion'))
			entries.push({ label: '情绪', value: textParam(task, 'emotion') ?? '' });
		if (taskSupportsParam(task, 'mimo_voice') && textParam(task, 'mimo_voice'))
			entries.push({ label: 'MiMo 音色', value: textParam(task, 'mimo_voice') ?? '' });
		if (taskSupportsParam(task, 'speaker_id') && textParam(task, 'speaker_id'))
			entries.push({ label: '预置音色', value: textParam(task, 'speaker_id') ?? '' });
		if (taskSupportsParam(task, 'prompt') && textParam(task, 'prompt'))
			entries.push({ label: '提示', value: textParam(task, 'prompt') ?? '' });
		if (taskSupportsParam(task, 'style_instruction') && textParam(task, 'style_instruction'))
			entries.push({ label: '风格指令', value: textParam(task, 'style_instruction') ?? '' });
		if (taskSupportsParam(task, 'voice_design_prompt') && textParam(task, 'voice_design_prompt'))
			entries.push({ label: '音色描述', value: textParam(task, 'voice_design_prompt') ?? '' });
		if (taskSupportsParam(task, 'optimize_text_preview') && boolParam(task, 'optimize_text_preview') !== null)
			entries.push({ label: '润色文本', value: boolParam(task, 'optimize_text_preview') ? '开启' : '关闭' });
		if (taskSupportsParam(task, 'emotion_text') && textParam(task, 'emotion_text'))
			entries.push({ label: '声音设计', value: textParam(task, 'emotion_text') ?? '' });
		if (taskSupportsParam(task, 'speed') && numericParam(task, 'speed') !== null)
			entries.push({ label: '语速', value: numericParam(task, 'speed')?.toFixed(2) ?? '' });
		if (taskSupportsParam(task, 'temperature') && numericParam(task, 'temperature') !== null)
			entries.push({ label: 'Temperature', value: numericParam(task, 'temperature')?.toFixed(2) ?? '' });
		if (taskSupportsParam(task, 'top_p') && numericParam(task, 'top_p') !== null)
			entries.push({ label: 'Top-P', value: numericParam(task, 'top_p')?.toFixed(2) ?? '' });
		if (taskSupportsParam(task, 'top_k') && numericParam(task, 'top_k') !== null)
			entries.push({ label: 'Top-K', value: String(numericParam(task, 'top_k')) });
		if (taskSupportsParam(task, 'emo_alpha') && numericParam(task, 'emo_alpha') !== null)
			entries.push({ label: '情绪强度', value: numericParam(task, 'emo_alpha')?.toFixed(2) ?? '' });
		if (taskSupportsParam(task, 'interval_silence') && numericParam(task, 'interval_silence') !== null)
			entries.push({ label: '段间静默', value: `${numericParam(task, 'interval_silence')} ms` });
		if (taskSupportsParam(task, 'max_text_tokens_per_segment') && numericParam(task, 'max_text_tokens_per_segment') !== null)
			entries.push({ label: '分段长度', value: String(numericParam(task, 'max_text_tokens_per_segment')) });
		if (taskSupportsParam(task, 'diffusion_steps') && numericParam(task, 'diffusion_steps') !== null)
			entries.push({ label: '扩散步数', value: String(numericParam(task, 'diffusion_steps')) });
		if (taskSupportsParam(task, 'cfg_rate') && numericParam(task, 'cfg_rate') !== null)
			entries.push({ label: 'CFG', value: numericParam(task, 'cfg_rate')?.toFixed(2) ?? '' });
		if (taskSupportsParam(task, 'nfe_step') && numericParam(task, 'nfe_step') !== null)
			entries.push({ label: 'NFE', value: String(numericParam(task, 'nfe_step')) });
		if (taskSupportsParam(task, 'cfg_strength') && numericParam(task, 'cfg_strength') !== null)
			entries.push({ label: 'F5 CFG', value: numericParam(task, 'cfg_strength')?.toFixed(2) ?? '' });
		if (taskSupportsParam(task, 'target_rms') && numericParam(task, 'target_rms') !== null)
			entries.push({ label: 'RMS', value: numericParam(task, 'target_rms')?.toFixed(2) ?? '' });
		if (taskSupportsParam(task, 'cross_fade_duration') && numericParam(task, 'cross_fade_duration') !== null)
			entries.push({ label: '淡化', value: `${numericParam(task, 'cross_fade_duration')?.toFixed(2)}s` });
		if (taskSupportsParam(task, 'remove_silence') && boolParam(task, 'remove_silence') !== null)
			entries.push({ label: '移除静音', value: boolParam(task, 'remove_silence') ? '开启' : '关闭' });
		if (textParam(task, 'output_format'))
			entries.push({ label: '格式', value: textParam(task, 'output_format')?.toUpperCase() ?? '' });
		return entries;
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
				<div>
					<h2>合成预设</h2>
					<p class="muted">跟随当前引擎，只显示可用于 {selected?.manifest.display_name ?? engineId} 的参数组合。</p>
				</div>
				<div class="row wrap preset-tools">
					<span class="muted">{enginePresets.length} 组</span>
					<button class="btn compact" type="button" onclick={() => openPresetEditor()}>
						<Plus size={14} /> 保存当前
					</button>
				</div>
			</div>
			{#if enginePresets.length}
				<div class="preset-strip">
					{#each enginePresets as preset}
						<div class="preset-chip">
							<button class="preset-main" type="button" onclick={() => applyPreset(preset)}>
								<strong>{preset.name}</strong>
								<span>{preset.scene || preset.description || presetEngineLabel(preset)}</span>
							</button>
							<button
								type="button"
								class="text-pop preset-info"
								data-text={`${preset.description || '无详细描述'}\n示例：${preset.sample_text || '未设置'}\n标签：${preset.tags.join('、') || '无'}`}
								aria-label="查看预设说明"
							>
								<Info size={13} />
							</button>
							{#if preset.preset_id.startsWith('custom_')}
								<button class="icon-btn mini" type="button" onclick={() => openPresetEditor(preset)} title="编辑预设" aria-label="编辑预设">
									<Pencil size={13} />
								</button>
								<button class="icon-btn mini danger" type="button" onclick={() => deletePreset(preset)} title="删除预设" aria-label="删除预设">
									<Trash2 size={13} />
								</button>
							{/if}
						</div>
					{/each}
				</div>
			{:else}
				<p class="muted empty-line">当前引擎还没有预设，可以先调整参数，再保存为自定义预设。</p>
			{/if}

			{#if showPresetEditor}
				<div class="preset-editor">
					<div class="row section-head">
						<div>
							<h3>{editingPresetId ? '编辑自定义预设' : '保存当前为预设'}</h3>
							<p class="muted">预设会绑定到当前引擎：{selected?.manifest.display_name ?? engineId}</p>
						</div>
						<button class="icon-btn mini" type="button" onclick={() => (showPresetEditor = false)} aria-label="关闭预设编辑">
							X
						</button>
					</div>
					<div class="preset-editor-grid">
						<label class="field">
							<span>名称</span>
							<input bind:value={presetDraft.name} placeholder="例如：课程慢讲" />
						</label>
						<label class="field">
							<span>场景</span>
							<input bind:value={presetDraft.scene} placeholder="例如：教程 / 长文旁白" />
						</label>
						<label class="field wide">
							<span>描述</span>
							<input bind:value={presetDraft.description} placeholder="简短说明这个预设适合什么情况" />
						</label>
						<label class="field">
							<span>标签</span>
							<input bind:value={presetDraft.tags} placeholder="慢讲，课程" />
						</label>
						<label class="field wide">
							<span>示例文本</span>
							<textarea bind:value={presetDraft.sample_text} placeholder="可选，用于复用时填充文本"></textarea>
						</label>
					</div>
					<div class="row wrap">
						<button class="btn primary compact" type="button" onclick={savePreset} disabled={presetBusy || !presetDraft.name.trim()}>
							<Save size={14} /> {presetBusy ? '保存中' : '保存预设'}
						</button>
						<button class="btn compact" type="button" onclick={() => (showPresetEditor = false)}>取消</button>
						<span class="muted">只保存当前引擎有效参数。</span>
					</div>
				</div>
			{/if}

			<div class="input-toolbar">
				<div class="input-title-line">
					<label class="input-label" for="generate-text">输入要合成的文本</label>
					<span
						class="text-pop input-help"
						data-text="长文本建议先生成分段计划；后续会接入分段生成、ASR 校对、失败重试和自动合并。当前规则规划不会离开本机。"
						aria-label="长文本生成说明"
					>
						<Info size={14} />
					</span>
					<span class={`badge plan-status ${textLengthStatus.className}`}>{textLengthStatus.label}</span>
				</div>
				<p class="input-subtitle">{inputSubtitle}</p>
			</div>
			<textarea id="generate-text" bind:value={text} placeholder="输入要合成的文本"></textarea>

			<!-- 核心参数行（始终可见） -->
			<div class="param-inline-row">
				<label class="param-inline">
					<span>引擎</span>
					<select bind:value={engineId}>
						{#each ttsEngines as engine}
							<option value={engine.manifest.engine_id}>{engine.manifest.display_name}</option>
						{/each}
					</select>
				</label>
				{#if usesReferenceVoice}
				<label class="param-inline">
					<span>声音</span>
					<div class="voice-inline">
						<select bind:value={voiceId} disabled={voices.length === 0}>
							<option value="">{voices.length === 0 ? '无音色' : '未选择'}</option>
							{#each voices as voice}
								<option value={voice.voice_id}>{voiceOptionLabel(voice)}</option>
							{/each}
						</select>
						<button
							class="icon-btn mini"
							type="button"
							onclick={previewSelectedVoice}
							disabled={!selectedVoicePreviewUrl}
							title="试听"
							aria-label="试听当前音色"
						>
							<Play size={13} />
						</button>
					</div>
				</label>
				{#if selectedVoicePreviewUrl}
					<audio bind:this={voicePreviewAudio} src={selectedVoicePreviewUrl} preload="metadata"></audio>
				{/if}
				{/if}
				{#if activeParamKeys.has('speed')}
				<label class="param-inline-range">
					<span>语速 {speed.toFixed(1)}x</span>
					<input type="range" min="0.5" max="2" step="0.1" bind:value={speed} />
				</label>
				{/if}
				<label class="param-inline">
					<span>格式</span>
					<select bind:value={outputFormat}>
						<option value="wav">WAV</option>
						<option value="mp3">MP3</option>
						<option value="flac">FLAC</option>
					</select>
				</label>
				<button class="btn" type="button" onclick={() => (showMoreParams = !showMoreParams)}>
					<Settings size={14} /> {showMoreParams ? '收起' : '更多'}
				</button>
			</div>

			{#if showMoreParams}
			<div class="more-params-panel">
				{#if isEmotiVoice}
					<div class="engine-note">
						<strong>EmotiVoice 参数</strong>
						<small>使用官方说话人和情绪提示，不读取本地参考音色。</small>
					</div>
				{:else if isF5}
					<div class="engine-note">
						<strong>F5-TTS 参数</strong>
						<small>使用本地参考音频和准确参考台词。</small>
					</div>
				{:else if isCosyVoice}
					<div class="engine-note">
						<strong>CosyVoice SFT 参数</strong>
						<small>使用官方 SFT 预置音色。</small>
					</div>
				{:else if isCosyVoiceZeroShot}
					<div class="engine-note">
						<strong>CosyVoice Zero-Shot 参数</strong>
						<small>使用本地参考音频和准确参考台词。</small>
					</div>
				{/if}

				{#if isMimoClone}
					<small>显示本地音色库中带参考音频的全部音色；生成前会按设置确认上传云端。</small>
				{/if}
				{#if isF5}
					<small>F5 使用本地参考音频和对应台词；缺少 reference_text 的音色会在生成前提示。</small>
				{/if}
				{#if isCosyVoiceZeroShot}
					<small>CosyVoice Zero-Shot 使用本地参考音频和对应台词。</small>
				{/if}
				{#if isMimoClone && settings?.mimo_voiceclone_confirm_upload}
					<small>生成前会再次提醒：本次参考音频将发送到 MiMo 云端。</small>
				{/if}

				{#if activeParamKeys.has('speaker_id')}
				<div class="field param-field">
					<label class="param-label" for="speaker-id">音色</label>
					<div class="param-control">
						{#if isEmotiVoice}
							<div class="speaker-catalog-tools">
								<div class="search-field speaker-search">
									<Search size={14} />
									<input bind:value={speakerQuery} placeholder="搜索 ID、名字、描述" />
									{#if speakerQuery.trim()}
										<button class="search-clear" type="button" aria-label="清空说话人搜索" title="清空说话人搜索" onclick={() => (speakerQuery = '')}>
											<X size={13} />
										</button>
									{/if}
								</div>
								<select class="speaker-gender" bind:value={speakerGenderFilter} aria-label="筛选说话人性别">
									<option value="all">全部</option>
									<option value="F">女声</option>
									<option value="M">男声</option>
								</select>
							</div>
						{/if}
						<select id="speaker-id" bind:value={speakerId}>
							{#each speakerChoices as option}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
						{#if isEmotiVoice}
							<small>{speakerCatalogLoading ? '正在读取说话人目录' : `目录结果 ${speakerCatalog.length} 条`}</small>
						{/if}
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('prompt')}
				<div class="field param-field">
					<label class="param-label" for="voice-prompt">情绪提示</label>
					<div class="param-control">
						<select id="voice-prompt" bind:value={voicePrompt}>
							{#each promptOptions as option}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
					</div>
				</div>
				{/if}

				{#if isMimoPreset}
				<div class="field param-field">
					<label class="param-label" for="mimo-voice">MiMo 音色</label>
					<div class="param-control">
						<select id="mimo-voice" bind:value={mimoVoice}>
							{#each mimoVoiceOptions as option}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('language')}
				<div class="field param-field">
					<label class="param-label" for="language">语言</label>
					<div class="param-control">
						<select id="language" bind:value={language}>
							<option value="zh">中文</option>
							<option value="en">英文</option>
							<option value="auto">自动</option>
						</select>
					</div>
				</div>
				{/if}

				{#if isMimo}
					{#if isMimoDesign}
					<div class="field param-field">
						<label class="param-label" for="voice-design-prompt">音色描述</label>
						<div class="param-control">
							<textarea id="voice-design-prompt" bind:value={voiceDesignPrompt}></textarea>
							<small>描述声音本身，例如年龄、性别、质感、语速和情绪底色。</small>
						</div>
					</div>
					{:else}
					<div class="field param-field">
						<label class="param-label" for="style-instruction">风格指令</label>
						<div class="param-control">
							<textarea
								id="style-instruction"
								bind:value={styleInstruction}
								placeholder="例如：语速稍慢，语气温柔，像知识视频旁白。"
							></textarea>
						</div>
					</div>
					{/if}
				{/if}

				{#if supportsEmotion}
				<div class="field param-field">
					<label class="param-label" for="emotion">情绪</label>
					<div class="param-control">
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
								: '当前会叠加情绪控制；如果想更贴参考音色，改回"跟随参考音色"。'}
						</small>
					</div>
				</div>
				{#if isIndexTTS && !followsReferenceEmotion}
				<div class="field param-slider">
					<div class="field-head">
						<label for="emo-alpha">情绪强度</label>
						<input class="field-number" aria-label="情绪强度数值" type="number" min="0" max="1" step="0.05" bind:value={emoAlpha} />
					</div>
					<div class="range-control">
						<input id="emo-alpha" type="range" min="0" max="1" step="0.05" bind:value={emoAlpha} />
						<div class="range-scale"><span>0</span><span>1</span></div>
					</div>
				</div>
				{/if}
				{/if}

				{#if isOmniVoice && !voiceId}
				<div class="field param-field">
					<label class="param-label" for="voice-design">设计标签</label>
					<div class="param-control">
						<select id="voice-design" bind:value={voiceDesign}>
							<option value="女，青年，中音调">女，青年，中音调</option>
							<option value="男，青年，中音调">男，青年，中音调</option>
							<option value="女，中年，高音调">女，中年，高音调</option>
							<option value="男，中年，低音调">男，中年，低音调</option>
							<option value="女，青年，耳语">女，青年，耳语</option>
						</select>
					</div>
				</div>
				{/if}

				{#if hasAdvancedParameters}
				<div class="advanced-divider"><span>高级参数</span></div>
				{/if}

				{#if isMimoDesign && activeParamKeys.has('optimize_text_preview')}
				<label class="toggle-field" for="optimize-text-preview">
					<input id="optimize-text-preview" type="checkbox" bind:checked={optimizeTextPreview} />
					<span>
						<strong>润色播报文本</strong>
						<small>MiMo VoiceDesign 官方可选项，会根据音色描述优化目标文本。</small>
					</span>
				</label>
				{/if}

				{#if activeParamKeys.has('nfe_step')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="nfe-step">采样步数 NFE</label>
						<input class="field-number" aria-label="NFE 数值" type="number" min="4" max="64" step="1" bind:value={nfeStep} />
					</div>
					<div class="range-control">
						<input id="nfe-step" type="range" min="4" max="64" step="1" bind:value={nfeStep} />
						<div class="range-scale"><span>4</span><span>64</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('cfg_strength')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="cfg-strength">引导强度 CFG</label>
						<input class="field-number" aria-label="F5 CFG 数值" type="number" min="0.1" max="5" step="0.1" bind:value={cfgStrength} />
					</div>
					<div class="range-control">
						<input id="cfg-strength" type="range" min="0.1" max="5" step="0.1" bind:value={cfgStrength} />
						<div class="range-scale"><span>0.1</span><span>5</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('target_rms')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="target-rms">响度目标 RMS</label>
						<input class="field-number" aria-label="RMS 数值" type="number" min="0.01" max="0.5" step="0.01" bind:value={targetRms} />
					</div>
					<div class="range-control">
						<input id="target-rms" type="range" min="0.01" max="0.5" step="0.01" bind:value={targetRms} />
						<div class="range-scale"><span>0.01</span><span>0.5</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('cross_fade_duration')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="cross-fade">交叉淡化</label>
						<input class="field-number" aria-label="交叉淡化秒数" type="number" min="0" max="1" step="0.05" bind:value={crossFadeDuration} />
					</div>
					<div class="range-control">
						<input id="cross-fade" type="range" min="0" max="1" step="0.05" bind:value={crossFadeDuration} />
						<div class="range-scale"><span>0s</span><span>1s</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('remove_silence')}
				<label class="toggle-field" for="remove-silence">
					<input id="remove-silence" type="checkbox" bind:checked={removeSilence} />
					<span>
						<strong>移除静音</strong>
						<small>生成后裁掉较长静音；需要保留自然停顿时关闭。</small>
					</span>
				</label>
				{/if}

				{#if activeParamKeys.has('temperature')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="temp">随机性 Temperature</label>
						<input class="field-number" aria-label="Temperature 数值" type="number" min={isMimo ? 0 : 0.1} max={isMimo ? 1.5 : 2} step="0.05" bind:value={temperature} />
					</div>
					<div class="range-control">
						<input id="temp" type="range" min={isMimo ? 0 : 0.1} max={isMimo ? 1.5 : 2} step="0.05" bind:value={temperature} />
						<div class="range-scale"><span>{isMimo ? '0' : '0.1'}</span><span>{isMimo ? '1.5' : '2'}</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('top_p')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="top-p">采样范围 Top-P</label>
						<input class="field-number" aria-label="Top-P 数值" type="number" min={isMimo ? 0.01 : 0} max="1" step={isMimo ? 0.01 : 0.05} bind:value={topP} />
					</div>
					<div class="range-control">
						<input id="top-p" type="range" min={isMimo ? 0.01 : 0} max="1" step={isMimo ? 0.01 : 0.05} bind:value={topP} />
						<div class="range-scale"><span>{isMimo ? '0.01' : '0'}</span><span>1</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('top_k')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="top-k">候选数量 Top-K</label>
						<input class="field-number" aria-label="Top-K 数值" type="number" min="1" max="100" step="1" bind:value={topK} />
					</div>
					<div class="range-control">
						<input id="top-k" type="range" min="1" max="100" step="1" bind:value={topK} />
						<div class="range-scale"><span>1</span><span>100</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('max_text_tokens_per_segment')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="segment">分段长度 Token</label>
						<input class="field-number" aria-label="分段长度数值" type="number" min="20" max="500" step="10" bind:value={maxTextTokensPerSegment} />
					</div>
					<div class="range-control">
						<input id="segment" type="range" min="20" max="500" step="10" bind:value={maxTextTokensPerSegment} />
						<div class="range-scale"><span>20</span><span>500</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('interval_silence')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="silence">段间静默</label>
						<input class="field-number" aria-label="段间静默数值" type="number" min="0" max="2000" step="50" bind:value={intervalSilence} />
					</div>
					<div class="range-control">
						<input id="silence" type="range" min="0" max="2000" step="50" bind:value={intervalSilence} />
						<div class="range-scale"><span>0ms</span><span>2000ms</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('cfg_rate')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="cfg">引导强度 CFG Rate</label>
						<input class="field-number" aria-label="CFG 数值" type="number" min="0" max="1" step="0.05" bind:value={cfgRate} />
					</div>
					<div class="range-control">
						<input id="cfg" type="range" min="0" max="1" step="0.05" bind:value={cfgRate} />
						<div class="range-scale"><span>0</span><span>1</span></div>
					</div>
				</div>
				{/if}

				{#if activeParamKeys.has('diffusion_steps')}
				<div class="field param-slider">
					<div class="field-head">
						<label for="diffusion">扩散步数</label>
						<input class="field-number" aria-label="扩散步数数值" type="number" min="5" max="60" step="1" bind:value={diffusionSteps} />
					</div>
					<div class="range-control">
						<input id="diffusion" type="range" min="5" max="60" step="1" bind:value={diffusionSteps} />
						<div class="range-scale"><span>5</span><span>60</span></div>
					</div>
				</div>
				{/if}
			</div>
			{/if}

			<div class="row tool-row" id="text-tools">
				<div class="row wrap tool-actions">
					<span class="muted">{text.length} 字</span>
					<button
						class="btn tool-btn text-pop"
						data-text="清理多余空白、异常标点和不利于播报的格式；只处理输入文本，不改变模型参数。适用于所有 TTS 引擎。"
						onclick={() => runTextTool('clean')}
						disabled={textToolBusy !== ''}
					>
						<Wand2 size={15} /> {textToolBusy === 'clean' ? '清洗中' : '清洗文本'}
					</button>
					<button
						class="btn tool-btn text-pop"
						data-text="把数字、年份和常见符号转成更适合中文口播的写法；只处理输入文本，不改变模型参数。适用于所有 TTS 引擎。"
						onclick={() => runTextTool('numbers')}
						disabled={textToolBusy !== ''}
					>
						<Hash size={15} /> {textToolBusy === 'numbers' ? '处理中' : '数字规范'}
					</button>
					<button
						class="btn tool-btn text-pop"
						data-text="按语义预览切句和停顿位置，方便检查节奏；不会提交生成任务。适用于所有 TTS 引擎。"
						onclick={() => runTextTool('split')}
						disabled={!text.trim() || textToolBusy !== ''}
					>
						<Scissors size={15} /> {textToolBusy === 'split' ? '分句中' : '分句预览'}
					</button>
					{#each textCueActions as action}
						<button class="btn tool-btn model-cue text-pop" type="button" data-text={action.hint} onclick={() => appendTextCue(action)}>
							{action.label}
						</button>
					{/each}
				</div>
				<button
					class="btn primary tool-btn text-pop"
					data-text="使用当前引擎、声音和参数提交语音合成任务；生成后会出现在下方结果与记录中。"
					disabled={busy || !text.trim()}
					onclick={generate}
				>
					<Send size={15} /> {busy ? '生成中' : '生成'}
				</button>
			</div>
			{#if engineRuntimeHint}
				<p class="badge">{engineRuntimeHint}</p>
			{/if}

			{#if showLongformDialog && pendingLongformPlan}
				<div class="modal-backdrop" role="presentation">
					<div class="longform-dialog" role="dialog" aria-modal="true" aria-label="长文本生成策略">
						<div class="dialog-head">
							<div>
								<h3>长文本生成策略</h3>
								<p class="muted">
									预计 {pendingLongformPlan.segments.length} 段。{pendingLongformPlan.planner_reason}
								</p>
							</div>
							<button class="icon-btn" type="button" onclick={() => closeLongformDialog(null)} aria-label="关闭">×</button>
						</div>
						{#if pendingLongformPlan.warnings.length}
							<p class="dialog-warning">{pendingLongformPlan.warnings[0]}</p>
						{/if}
						<div class="strategy-grid">
							<button
								class:active={longformStrategy === 'split_merge'}
								type="button"
								onclick={() => {
									longformStrategy = 'split_merge';
									longformMergeEnabled = true;
									longformVerifyEnabled = true;
								}}
							>
								<strong>分段生成并合并</strong>
								<span>逐段生成，校对后自动合并为一个音频。</span>
							</button>
							<button
								class:active={longformStrategy === 'split_only'}
								type="button"
								onclick={() => {
									longformStrategy = 'split_only';
									longformMergeEnabled = false;
								}}
							>
								<strong>只分段生成</strong>
								<span>保留每段结果，适合先人工复听。</span>
							</button>
							<button
								class:active={longformStrategy === 'single'}
								type="button"
								onclick={() => {
									longformStrategy = 'single';
									longformMergeEnabled = false;
								}}
							>
								<strong>仍然单条生成</strong>
								<span>最快开始，但更容易超时、漏句或截断。</span>
							</button>
						</div>
						<div class="dialog-options">
							<label class="check-row">
								<input type="checkbox" bind:checked={longformVerifyEnabled} disabled={longformStrategy === 'single'} />
								<span>生成后自动 ASR 校对</span>
							</label>
							<label class="field compact-field">
								<span>失败重试</span>
								<input type="number" min="0" max="5" bind:value={longformMaxRetries} disabled={longformStrategy === 'single'} />
							</label>
						</div>
						<div class="dialog-preview">
							{#each pendingLongformPlan.segments.slice(0, 4) as segment}
								<p><strong>{segment.index}</strong> {segment.text}</p>
							{/each}
							{#if pendingLongformPlan.segments.length > 4}
								<p class="muted">还有 {pendingLongformPlan.segments.length - 4} 段...</p>
							{/if}
						</div>
						<div class="dialog-actions">
							<button class="btn" type="button" onclick={() => closeLongformDialog(null)}>取消</button>
							<button class="btn primary" type="button" onclick={() => closeLongformDialog(longformStrategy)}>开始</button>
						</div>
					</div>
				</div>
			{/if}

			{#if showSplitPreview && textSegments.length}
				<div class="split-preview" class:collapsed={splitPreviewCollapsed}>
					<div class="split-preview-head">
						<div class="split-preview-title">
							<div class="row wrap split-title-row">
								<h3>{lastGeneratePlan ? '系统分段计划' : '智能分句预览'}</h3>
								<span class="badge">{textSegments.length} 段</span>
							</div>
							<p>
								用来提前检查停顿和节奏。{#if lastGeneratePlan}
									{lastGeneratePlan.planner_reason}
								{/if}
							</p>
						</div>
						<button
							class="icon-btn split-collapse"
							class:expanded={!splitPreviewCollapsed}
							type="button"
							onclick={() => (splitPreviewCollapsed = !splitPreviewCollapsed)}
							title={splitPreviewCollapsed ? '展开分段计划' : '收起分段计划'}
							aria-label={splitPreviewCollapsed ? '展开分段计划' : '收起分段计划'}
							aria-expanded={!splitPreviewCollapsed}
						>
							<ChevronRight size={15} />
						</button>
					</div>
					{#if !splitPreviewCollapsed}
						<div class="segment-list">
							{#each textSegments as segment, index}
								<div class="segment-card">
									<span class="segment-index">{index + 1}</span>
									<p>{segment}</p>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}

			{#if error}
				<div class="badge fail">{error}</div>
			{/if}
			<audio
				class="result-shared-audio"
				bind:this={resultPreviewAudio}
				preload="none"
				onended={() => (playingResultTaskId = '')}
				onpause={() => {
					if (resultPreviewAudio?.ended || !resultPreviewAudio?.currentTime) playingResultTaskId = '';
				}}
			></audio>

			<div class="result-panel stack section-divider" id="records">
				<div class="row section-head result-headline">
					<h2>结果与记录</h2>
					<div class="records-row-summary">
						<span class="muted">{filteredTasks.length} 条</span>
						{#if statusCounts.active}
							<span class="badge">生成中 {queueCounts.processing}</span>
							<span class="badge">等待 {queueCounts.waiting}</span>
						{/if}
						{#if selectedTaskIds.length}<span class="badge ok">已选 {selectedTaskIds.length}</span>{/if}
					</div>
				</div>

					<div class="records-toolbar">
						<div class="toolbar-row-1">
							<div class="toolbar-tabs">
								<div class="segmented compact-tabs" role="tablist" aria-label="任务筛选">
									<button class:active={taskStatusTab === 'all'} type="button" onclick={() => { taskStatusTab = 'all'; currentPage = 1; }}>
										全部
										<span>{statusCounts.all}</span>
									</button>
									<button class:active={taskStatusTab === 'active'} type="button" onclick={() => { taskStatusTab = 'active'; currentPage = 1; }}>
										队列
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
							</div>
							<div class="toolbar-right">
								{#if pageCount > 1}
								<div class="pagination-bar pagination-bar-top">
									<button class="btn icon-text-btn" onclick={() => taskPageGoTo(1)} disabled={currentPage <= 1}>
										<ChevronsLeft size={15} />
									</button>
									<button class="btn icon-text-btn" onclick={() => taskPageJump(-1)} disabled={currentPage <= 1}>
										<ChevronLeft size={15} />
									</button>
									<span class="muted page-info">{currentPage} / {pageCount}</span>
									<button class="btn icon-text-btn" onclick={() => taskPageJump(1)} disabled={currentPage >= pageCount}>
										<ChevronRight size={15} />
									</button>
									<button class="btn icon-text-btn" onclick={() => taskPageGoTo(pageCount)} disabled={currentPage >= pageCount}>
										<ChevronsRight size={15} />
									</button>
								</div>
								{/if}
								<div class="toolbar-actions" aria-label="批量操作">
									<button
										class="icon-btn"
										type="button"
										onclick={toggleVisibleSelection}
										disabled={!visibleSelectableTasks.length}
										title={allVisibleSelected ? '取消全选当前页' : '全选当前页'}
										aria-label={allVisibleSelected ? '取消全选当前页' : '全选当前页'}
									>
										{#if allVisibleSelected}
											<CheckSquare size={15} />
										{:else}
											<Square size={15} />
										{/if}
									</button>
									<button
										class="icon-btn danger"
										type="button"
										onclick={deleteSelectedTasks}
										disabled={!selectedTaskIds.length}
										title="删除已选记录"
										aria-label="删除已选记录"
									>
										<Trash2 size={15} />
									</button>
									{#if hasActiveFilters}
										<button
											class="icon-btn"
											type="button"
											onclick={clearTaskFilters}
											title="重置筛选"
											aria-label="重置筛选"
										>
											<RotateCcw size={15} />
										</button>
									{/if}
								</div>
							</div>
						</div>

						<div class="records-filter-inline">
							<div class="search-field">
								<Search size={14} />
								<input bind:value={taskQuery} placeholder="搜索文本、音色、引擎" oninput={() => (currentPage = 1)} />
							</div>
							<select bind:value={taskEngineFilter} onchange={() => (currentPage = 1)}>
								{#each taskEngineOptions as option}
									<option value={option}>{option === 'all' ? '全部引擎' : engineMap.get(option)?.manifest.display_name ?? option}</option>
								{/each}
							</select>
							<select bind:value={taskSourceFilter} onchange={() => (currentPage = 1)}>
								<option value="all">全部来源</option>
								<option value="local">本地</option>
								<option value="cloud">云端</option>
							</select>
							<select bind:value={taskDateFilter} onchange={() => (currentPage = 1)}>
								<option value="all">全部时间</option>
								<option value="today">24小时</option>
								<option value="7d">7天</option>
								<option value="30d">30天</option>
							</select>
							<select bind:value={taskSortBy} onchange={() => (currentPage = 1)}>
								<option value="latest">最新</option>
								<option value="oldest">最早</option>
								<option value="duration_desc">时长↓</option>
							</select>
							<select bind:value={pageSize} onchange={() => (currentPage = 1)}>
								<option value={8}>8条/页</option>
								<option value={12}>12条/页</option>
								<option value={16}>16条/页</option>
								<option value={24}>24条/页</option>
								<option value={32}>32条/页</option>
								<option value={48}>48条/页</option>
							</select>
						</div>
					</div>

				{#if visibleLongformTasks.length}
					<section class="longform-list" aria-label="长文本任务">
						<div class="row section-subhead">
							<div>
								<h3>长文本任务</h3>
								<p class="muted">进行中的分段生成、校对、重试和合并父任务；完成后会进入下方结果记录。</p>
							</div>
						</div>
						{#each visibleLongformTasks.slice(0, 6) as task}
							<article class={`longform-card ${task.status}`}>
								<div class="longform-card-head">
									<div>
										<strong>{longformTitle(task)}</strong>
										<p class="muted">{longformStatusText(task)}</p>
									</div>
									<div class="row wrap longform-actions">
										<span class="badge">{Math.round(task.progress * 100)}%</span>
										{#if task.export_id}
											<a class="btn" href={longformDownloadUrl(task)}>下载合并音频</a>
										{/if}
										{#if task.status === 'failed'}
											<button class="btn" type="button" onclick={() => retryLongform(task)} disabled={actionBusyTaskId === task.longform_task_id}>
												<RotateCcw size={15} /> 重试失败段
											</button>
										{/if}
									</div>
								</div>
								<div class="progress-track">
									<div class="progress-fill" style={`width:${Math.max(3, Math.round(task.progress * 100))}%`}></div>
								</div>
								<div class="longform-segments">
									{#each task.segments as segment}
										<div class={`longform-segment ${segment.status}`}>
											<span class="badge">{segment.index}</span>
											<p>{segment.text}</p>
											<span class="badge">{taskStatusLabel(segment.status)}</span>
											{#if segment.verification}
												<span class={`badge verify-${segment.verification.status}`}>{verificationStatusLabel(segment.verification.status)}</span>
											{/if}
											{#if segment.error_message}
												<span class="muted">{segment.error_message}</span>
											{/if}
										</div>
									{/each}
								</div>
								{#if task.error_message}
									<p class="muted error-line">{task.error_message}</p>
								{/if}
							</article>
						{/each}
					</section>
				{/if}

				{#if pagedTasks.length}
					<div class="result-grid" bind:this={resultGridEl}>
						{#each pagedTasks as task}
							<article class={`card stack result-card engine-surface ${engineKind(task.engine_id) === 'cloud' ? 'engine-cloud' : 'engine-local'}${playingResultTaskId === task.task_id ? ' playing' : ''}`}>
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
									{#if voiceName(task)}<span class="badge"><Mic size={11} /> {voiceName(task)}</span>{/if}
									<span class="text-pop text-chip result-script-chip" data-text={displayTitle(task)}>
										<FileText size={13} /> 台词
									</span>
									{#if longformResultLabel(task)}
										<span
											class="badge longform-result-badge"
											class:merged={taskIsLongformExport(task)}
											title={longformResultTitle(task)}
										>
											{longformResultLabel(task)}
										</span>
									{/if}
									<span class="meta-line-break"></span>
									<span class="badge engine"><Cpu size={11} /> {engineMap.get(task.engine_id)?.manifest.display_name ?? task.engine_id}</span>
									<span class="badge badge-kind">{engineTypeLabel(task.engine_id)}</span>
									{#if task.created_at}<span class="badge">{formatTime(task.created_at)}</span>{/if}
								</div>

								<div class="row result-info">
									<p class="muted result-subline">{taskTimingLine(task)}</p>
									<div class="row wrap result-info-right">
										{#if task.result_duration_ms}
											<span class="badge">{formatAudioDuration(task.result_duration_ms)}</span>
										{/if}
										{#if taskIsActive(task)}
											<button class="icon-btn danger" onclick={() => cancel(task)} disabled={actionBusyTaskId === task.task_id} title="取消" aria-label="取消">
												<X size={14} />
											</button>
										{/if}
										<button
											type="button"
											class="param-pop compact"
											aria-label="查看生成参数"
											title="查看生成参数"
										>
											<SlidersHorizontal size={13} />
											<span class="param-panel" role="tooltip">
												<strong>生成参数</strong>
												<span class="param-grid">
													{#each taskParameterEntries(task) as entry}
														<span class="param-key">{entry.label}</span>
														<span class="param-value">{entry.value}</span>
													{/each}
												</span>
											</span>
										</button>
									</div>
								</div>

								{#if taskIsActive(task)}
									<div class="progress-block">
										<div class="row" style="justify-content:space-between">
											<span class="muted">{taskStageLabel(task)}</span>
											<span class="badge">{progressLabel(task)}</span>
										</div>
										<div class="progress-track" class:waiting-track={taskIsWaiting(task)}>
											<div
												class="progress-fill"
												class:waiting-fill={taskIsWaiting(task)}
												style={`width:${taskProgressWidth(task)}%`}
											></div>
										</div>
										<div class="row wrap progress-foot">
											<span class="muted">
												{#if taskIsWaiting(task)}
													已等待 {formatSeconds(waitingSeconds(task))}
												{:else}
													已运行 {elapsedLabel(task)}
												{/if}
											</span>
											{#if taskEtaLabel(task)}<span class="muted">{taskEtaLabel(task)}</span>{/if}
										</div>
										{#if taskRuntimeHint(task)}
											<p class="progress-hint">{taskRuntimeHint(task)}</p>
										{/if}
									</div>
								{/if}

								{#if taskVerificationReport(task)}
									{@const report = taskVerificationReport(task)}
									<div class="verification-line {report.status}">
										<span class="dot"></span>
										{verificationStatusLabel(report.status)}
										<span class="coverage">覆盖率 {Math.round(report.coverage * 100)}%</span>
									</div>
								{:else if taskVerificationPending(task)}
									<p class="muted verification-pending-line">自动校对中…</p>
								{:else if taskVerificationError(task)}
									<p class="muted error-line">{taskVerificationError(task)}</p>
								{/if}

								<div class="result-footer" class:without-audio={!task.result_id}>
									{#if task.result_id}
										<div class="result-audio-compact">
											<button
												class="icon-btn result-play-btn {playingResultTaskId === task.task_id ? 'playing' : ''}"
												type="button"
												onclick={() => toggleResultPlayback(task)}
												title={playingResultTaskId === task.task_id ? '暂停播放' : '播放结果'}
												aria-label={playingResultTaskId === task.task_id ? '暂停播放' : '播放结果'}
											>
												{#if playingResultTaskId === task.task_id}
													<Pause size={15} />
												{:else}
													<Play size={15} />
												{/if}
											</button>
											<span class="muted audio-compact-label">
												{task.result_duration_ms ? formatAudioDuration(task.result_duration_ms) : '播放结果'}
											</span>
											<a
												class="icon-btn result-download-btn"
												href={resultAudioUrl(task)}
												title="下载音频"
												aria-label="下载音频"
											>
												<Download size={15} />
											</a>
											
										</div>
									{/if}

									<div class="row wrap card-actions">
										{#if task.status === 'failed'}
											<button class="icon-btn" onclick={() => retry(task)} disabled={actionBusyTaskId === task.task_id} title="重试" aria-label="重试">
												<RotateCcw size={15} />
											</button>
										{/if}
										{#if taskCanDelete(task)}
											<button class="icon-btn" onclick={() => reuse(task)} disabled={actionBusyTaskId === task.task_id} title="复用" aria-label="复用">
												<Repeat size={15} />
											</button>
										{/if}
										{#if !taskIsActive(task)}
											<button class="icon-btn danger" onclick={() => deleteTaskRecord(task)} disabled={actionBusyTaskId === task.task_id} title="删除" aria-label="删除">
												<Trash2 size={15} />
											</button>
										{/if}
									</div>
								</div>

								{#if task.error_message}
									<p class="muted error-line">{task.error_message}</p>
								{/if}

							</article>
						{/each}
					</div>

					{#if pageCount > 1}
						<div class="pagination-bar">
							<button class="btn" onclick={() => currentPage = 1} disabled={currentPage <= 1}>
								<ChevronsLeft size={15} /> 首页
							</button>
							<button class="btn" onclick={() => taskPageJump(-1)} disabled={currentPage <= 1}>
								<ChevronLeft size={15} /> 上一页
							</button>
							<span class="muted">第 {currentPage} / {pageCount} 页</span>
							<div class="page-jump">
								<span class="muted">跳至</span>
								<input type="number" min="1" max={pageCount} bind:value={pageJumpInput} onkeydown={(e) => e.key === 'Enter' && jumpToPage()} />
								<span class="muted">页</span>
							</div>
							<button class="btn" onclick={() => taskPageJump(1)} disabled={currentPage >= pageCount}>
								下一页 <ChevronRight size={15} />
							</button>
							<button class="btn" onclick={() => currentPage = pageCount} disabled={currentPage >= pageCount}>
								尾页 <ChevronsRight size={15} />
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

	</div>
</main>

<style>
	.compose-panel {
		min-width: 0;
	}

	.section-head {
		justify-content: space-between;
	}

	.preset-tools {
		justify-content: flex-end;
	}

	.preset-strip {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		align-items: stretch;
	}

	.preset-chip {
		display: inline-flex;
		align-items: stretch;
		min-width: 0;
		max-width: 320px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #121519;
		overflow: visible;
	}

	.preset-main {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
		max-width: 210px;
		padding: 7px 9px;
		border: 0;
		border-right: 1px solid rgba(255, 255, 255, 0.06);
		background: transparent;
		color: var(--text);
		text-align: left;
	}

	.preset-main strong,
	.preset-main span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.preset-main strong {
		font-size: 13px;
		line-height: 1.25;
	}

	.preset-main span {
		color: var(--muted);
		font-size: 11px;
	}

	.preset-info {
		border: 0;
		border-radius: 0;
		background: transparent;
		padding: 0 7px;
	}

	.preset-editor {
		display: grid;
		gap: 10px;
		border: 1px solid rgba(79, 156, 249, 0.28);
		border-radius: 8px;
		padding: 11px;
		background: rgba(18, 24, 32, 0.82);
	}

	.preset-editor-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 9px;
	}

	.preset-editor-grid .wide {
		grid-column: 1 / -1;
	}

	.preset-editor textarea {
		min-height: 64px;
	}

	.empty-line {
		margin: 0;
		font-size: 12px;
	}

	.input-toolbar {
		display: grid;
		gap: 5px;
		margin-bottom: -2px;
	}

	.input-title-line {
		display: flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
		flex-wrap: wrap;
	}

	.input-label {
		font-size: 13px;
		color: var(--text);
		font-weight: 600;
	}

	.input-help {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 22px;
		height: 22px;
		border-radius: 7px;
		color: var(--muted);
		border: 1px solid var(--line);
		background: rgba(255, 255, 255, 0.04);
	}

	.input-subtitle {
		margin: 0;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.45;
	}

	.plan-status {
		font-size: 11px;
		line-height: 1;
		padding: 5px 7px;
	}

	.plan-status.ok {
		border-color: rgba(76, 175, 123, 0.35);
		background: rgba(36, 120, 82, 0.18);
		color: #aee8c7;
	}

	.plan-status.info {
		border-color: rgba(79, 156, 249, 0.38);
		background: rgba(79, 156, 249, 0.16);
		color: #a9cfff;
	}

	.plan-status.warn {
		border-color: rgba(245, 182, 83, 0.42);
		background: rgba(245, 182, 83, 0.17);
		color: #ffd89a;
	}

	.tool-row {
		justify-content: space-between;
		align-items: flex-start;
		gap: 10px;
	}

	.tool-actions {
		gap: 8px;
	}

	.tool-row .tool-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		height: 28px;
		min-height: 28px;
		padding: 0 9px;
		border-radius: 7px;
		font-size: 12px;
		line-height: 1;
		gap: 5px;
		white-space: nowrap;
		box-sizing: border-box;
	}

	.tool-row .tool-btn.text-pop {
		background: var(--panel-2);
		backdrop-filter: none;
		color: var(--text);
		box-shadow: none;
	}

	.tool-row .tool-btn.primary {
		background: var(--accent);
		border-color: var(--accent);
		color: #07121f;
		font-weight: 700;
	}

	.tool-row .tool-btn:disabled {
		opacity: 0.48;
		cursor: not-allowed;
	}

	.tool-row .tool-btn.model-cue {
		padding: 0 9px;
	}

	.split-preview {
		display: grid;
		gap: 12px;
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #111821;
	}

	.split-preview.collapsed {
		gap: 0;
	}

	.split-preview-head {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: start;
		gap: 12px;
	}

	.split-preview-title {
		display: grid;
		gap: 5px;
		min-width: 0;
	}

	.split-title-row {
		gap: 8px;
	}

	.split-title-row h3 {
		margin: 0;
		font-size: 14px;
		line-height: 1.25;
	}

	.split-preview-title p {
		margin: 0;
		color: #aeb8c7;
		font-size: 12px;
		line-height: 1.45;
	}

	.split-collapse {
		width: 30px;
		height: 30px;
		border-radius: 7px;
		transition: transform 160ms ease;
	}

	.split-collapse.expanded {
		transform: rotate(90deg);
	}

	.segment-list {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
		gap: 8px;
	}

	.segment-card {
		display: grid;
		grid-template-columns: 26px minmax(0, 1fr);
		align-items: start;
		gap: 9px;
		min-height: 118px;
		padding: 9px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #0e141c;
	}

	.segment-index {
		display: inline-grid;
		width: 24px;
		height: 24px;
		place-items: center;
		border: 1px solid rgba(148, 163, 184, 0.22);
		border-radius: 999px;
		background: #122033;
		color: #bcd1f1;
		font-size: 12px;
		line-height: 1;
	}

	.segment-card p {
		margin: 0;
		color: #dbe4f0;
		font-size: 12px;
		line-height: 1.55;
		overflow-wrap: anywhere;
	}

	.modal-backdrop {
		position: fixed;
		inset: 0;
		z-index: 20;
		display: grid;
		place-items: center;
		padding: 20px;
		background: rgba(3, 6, 10, 0.68);
	}

	.longform-dialog {
		width: min(760px, 100%);
		max-height: min(86vh, 760px);
		overflow: auto;
		display: grid;
		gap: 12px;
		padding: 16px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #11161d;
		box-shadow: 0 20px 60px rgba(0, 0, 0, 0.42);
	}

	.dialog-head,
	.dialog-actions,
	.dialog-options,
	.longform-card-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}

	.dialog-head h3,
	.section-subhead h3 {
		margin: 0;
		font-size: 16px;
	}

	.dialog-warning {
		margin: 0;
		padding: 8px 10px;
		border: 1px solid rgba(245, 158, 11, 0.32);
		border-radius: 7px;
		background: rgba(245, 158, 11, 0.08);
		color: #f8d99d;
		font-size: 12px;
		line-height: 1.5;
	}

	.strategy-grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 8px;
	}

	.strategy-grid button {
		display: grid;
		gap: 6px;
		min-height: 86px;
		padding: 10px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #0d1218;
		color: var(--text);
		text-align: left;
	}

	.strategy-grid button.active {
		border-color: rgba(79, 156, 249, 0.72);
		background: #122237;
	}

	.strategy-grid span,
	.dialog-preview p {
		margin: 0;
		color: #b7c1cf;
		font-size: 12px;
		line-height: 1.45;
	}

	.check-row {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		color: #d8e0ea;
		font-size: 13px;
	}

	.compact-field {
		width: 130px;
	}

	.dialog-preview {
		display: grid;
		gap: 6px;
		padding: 10px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #0d1218;
	}

	.dialog-actions {
		justify-content: flex-end;
	}

	.longform-list {
		display: grid;
		gap: 10px;
	}

	.section-subhead {
		justify-content: space-between;
	}

	.longform-card {
		display: grid;
		gap: 10px;
		padding: 10px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #10151c;
	}

	.longform-card.success {
		border-color: rgba(66, 196, 155, 0.32);
	}

	.longform-card.failed {
		border-color: rgba(248, 113, 113, 0.36);
	}

	.longform-card-head strong {
		display: block;
		max-width: min(620px, 66vw);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.longform-segments {
		display: grid;
		gap: 6px;
	}

	.longform-segment {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto auto;
		align-items: center;
		gap: 7px;
		padding: 6px 7px;
		border: 1px solid rgba(255, 255, 255, 0.06);
		border-radius: 7px;
		background: #0d1218;
	}

	.longform-segment p {
		margin: 0;
		overflow: hidden;
		color: #d8e0ea;
		font-size: 12px;
		line-height: 1.35;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.longform-result-badge {
		border-color: rgba(96, 165, 250, 0.48);
		background: rgba(37, 99, 235, 0.16);
		color: #b9d7ff;
	}

	.longform-result-badge.merged {
		border-color: rgba(66, 196, 155, 0.5);
		background: rgba(16, 185, 129, 0.16);
		color: #bbf7d0;
	}

	.verify-passed {
		color: #5ee0ae;
	}

	.verify-warning,
	.verify-skipped {
		color: #f5c56b;
	}

	.verify-failed {
		color: #fb8a8a;
	}

	.result-panel {
		gap: 12px;
	}

	.result-headline {
		align-items: center;
	}

	.result-title-line {
		display: flex;
		align-items: baseline;
		gap: 10px;
		flex-wrap: wrap;
		min-width: 0;
	}

	.result-title-line h2,
	.result-title-line p {
		margin: 0;
	}

	.records-row-summary {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		justify-content: flex-end;
		margin-left: auto;
		min-width: 0;
		flex-wrap: wrap;
	}

	.segmented {
		display: inline-flex;
		flex-wrap: wrap;
		gap: 4px;
		padding: 3px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: #111418;
	}

	.segmented button {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		min-height: 26px;
		padding: 4px 7px;
		border: 0;
		border-radius: 6px;
		background: transparent;
		color: var(--muted);
		font-size: 12px;
		line-height: 1;
	}

	.segmented button span {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 18px;
		height: 18px;
		padding: 0 5px;
		border-radius: 999px;
		background: #1a1f26;
		font-size: 10px;
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

	.records-toolbar {
		display: grid;
		gap: 8px;
	}

	.toolbar-row-1 {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.toolbar-tabs {
		display: flex;
		align-items: center;
		flex: 0 1 auto;
		min-width: 0;
	}

	.toolbar-right {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 8px;
		flex: 1 1 0;
		min-width: 0;
		flex-wrap: nowrap;
	}

	.compact-tabs {
		max-width: 100%;
	}

	.records-filter-inline select {
		min-height: 28px;
		padding: 3px 8px;
		border-radius: 6px;
		font-size: 12px;
		min-width: 72px;
		flex-shrink: 0;
		width: auto;
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
		min-height: 30px;
		color: inherit;
		outline: none;
		padding: 0;
	}

	.search-clear {
		display: inline-grid;
		place-items: center;
		flex: 0 0 auto;
		width: 22px;
		height: 22px;
		border: 1px solid rgba(255, 255, 255, 0.08);
		border-radius: 999px;
		background: rgba(255, 255, 255, 0.04);
		color: var(--muted);
		cursor: pointer;
		padding: 0;
	}

	.speaker-catalog-tools {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 76px;
		gap: 6px;
		align-items: center;
	}

	.speaker-search {
		min-width: 0;
		height: 32px;
		padding: 0 8px;
	}

	.speaker-search input {
		height: 30px;
		min-height: 30px;
	}

	.speaker-gender {
		min-width: 0;
	}

	.toolbar-actions {
		display: flex;
		align-items: center;
		justify-content: flex-start;
		gap: 5px;
		min-height: 30px;
	}

	.icon-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 30px;
		height: 30px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #12161c;
		color: #d6deea;
	}

	.icon-btn.mini {
		width: 26px;
		height: 26px;
		border-radius: 6px;
	}

	.voice-select-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 32px;
		gap: 6px;
		align-items: center;
	}

	.voice-select-row select {
		min-width: 0;
	}

	.voice-preview-btn {
		width: 32px;
		height: 32px;
		border-radius: 7px;
	}

	.icon-btn:hover:not(:disabled) {
		border-color: rgba(79, 156, 249, 0.45);
		background: #17202b;
	}

	.icon-btn.danger {
		color: #ffb6ad;
		border-color: rgba(244, 108, 95, 0.28);
		background: rgba(244, 108, 95, 0.08);
	}

	.icon-btn:disabled {
		opacity: 0.42;
		cursor: not-allowed;
	}

	.meta-line-break {
		flex-basis: 100%;
		height: 0;
		overflow: hidden;
	}

	.pagination-bar-top {
		display: flex;
		flex-direction: row;
		align-items: center;
		gap: 3px;
		flex-wrap: nowrap;
		flex-shrink: 0;
		min-width: max-content;
	}

	.pagination-bar .icon-text-btn {
		min-width: auto;
		padding: 4px 8px;
	}

	.result-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		gap: 12px;
		overflow: visible;
	}

		.result-card {
			gap: 9px;
			padding: 10px;
			min-width: 0;
			transition: border-color 200ms ease, box-shadow 200ms ease;
		}

		.result-card.playing {
			border-color: rgba(79, 156, 249, 0.35);
			box-shadow: 0 0 0 1px rgba(79, 156, 249, 0.12), 0 4px 18px rgba(79, 156, 249, 0.1);
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

	.result-script-chip {
		padding: 1px 6px;
		font-size: 11px;
		line-height: 1.4;
		min-height: 22px;
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

	.result-shared-audio {
		display: none;
	}

	.result-audio-compact {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
		padding: 4px 6px 4px 4px;
		border: 1px solid rgba(255, 255, 255, 0.08);
		border-radius: 8px;
		background: #10151c;
		width: fit-content;
		max-width: 100%;
	}

	.result-play-btn,
	.result-download-btn {
		width: 30px;
		height: 30px;
		border-radius: 7px;
	}

		.result-play-btn.playing {
			background: var(--accent);
			border-color: var(--accent);
			color: #07121f;
		}

	.audio-compact-label {
		min-width: 0;
		font-size: 12px;
		line-height: 1;
		white-space: nowrap;
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

	.progress-track.waiting-track {
		background: repeating-linear-gradient(
			90deg,
			#1a2027 0,
			#1a2027 10px,
			#202832 10px,
			#202832 20px
		);
	}

	.progress-fill {
		height: 100%;
		border-radius: inherit;
		background: linear-gradient(90deg, #4f9cf9 0%, #42c49b 100%);
		transition: width 240ms ease;
		min-width: 8px;
	}

	.progress-fill.waiting-fill {
		min-width: 0;
		background: transparent;
	}

	.progress-foot {
		justify-content: space-between;
	}

	.progress-hint {
		margin: -2px 0 0;
		color: #b7c1cf;
		font-size: 12px;
		line-height: 1.45;
	}

	.card-actions {
		margin-left: auto;
		gap: 8px;
		justify-content: flex-end;
	}

	.result-footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px 10px;
		flex-wrap: wrap;
	}

	.result-footer.without-audio {
		justify-content: flex-start;
	}

	.result-footer.without-audio .card-actions {
		margin-left: 0;
	}

	.error-line {
		margin: 0;
		font-size: 12px;
		line-height: 1.5;
	}

	.verification-pending-line {
		margin: 0;
		padding: 7px 8px;
		border: 1px solid rgba(96, 165, 250, 0.24);
		border-radius: 7px;
		background: rgba(37, 99, 235, 0.1);
		font-size: 12px;
		line-height: 1.5;
	}

	.verification-line {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 12px;
		line-height: 1;
	}

	.verification-line.passed { color: #42c49b; }
	.verification-line.passed .dot { background: #42c49b; }

	.verification-line.warning { color: #e5a842; }
	.verification-line.warning .dot { background: #e5a842; }

	.verification-line.failed { color: #e54d4d; }
	.verification-line.failed .dot { background: #e54d4d; }

	.verification-line .dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		flex-shrink: 0;
	}

	.verification-line .coverage {
		color: #7d8a9a;
	}

	.pagination-bar {
		display: flex;
		align-items: center;
		justify-content: flex-start;
		gap: 8px;
		flex-wrap: nowrap;
		min-width: max-content;
	}
	.page-jump {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.page-jump input[type='number'] {
		width: 52px;
		min-height: 26px;
		padding: 2px 6px;
		font-size: 12px;
		text-align: center;
		border-radius: 5px;
		-moz-appearance: textfield;
	}
	.page-jump input[type='number']::-webkit-inner-spin-button,
	.page-jump input[type='number']::-webkit-outer-spin-button {
		-webkit-appearance: none;
		margin: 0;
	}

	.field small {
		color: var(--muted);
		font-size: 11px;
		line-height: 1.45;
	}

	.engine-note {
		display: grid;
		gap: 4px;
		padding: 9px 10px;
		border: 1px solid var(--border);
		background: rgba(59, 130, 246, 0.08);
		border-radius: 6px;
	}

	.engine-note strong {
		color: var(--text);
		font-size: 12px;
	}

	.engine-note small {
		color: var(--muted);
		line-height: 1.45;
	}

	.field-head label {
		color: var(--text);
		font-size: 13px;
		font-weight: 600;
		min-width: 0;
		flex: 1 1 auto;
		line-height: 1.3;
	}

	.param-control {
		display: grid;
		min-width: 0;
		gap: 6px;
	}

	.param-control select,
	.param-control input:not([type='checkbox']):not([type='radio']):not([type='range']),
	.param-control textarea {
		width: 100%;
		min-height: 32px;
		border-radius: 7px;
		font-size: 13px;
		line-height: 1.3;
	}

	.param-control select,
	.param-control input:not([type='checkbox']):not([type='radio']):not([type='range']) {
		height: 32px;
		padding-top: 4px;
		padding-bottom: 4px;
	}

	.param-control textarea {
		min-height: 72px;
		max-height: 132px;
		padding-top: 7px;
		padding-bottom: 7px;
		resize: vertical;
	}

	.field-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 6px;
		min-height: 26px;
	}

	.param-slider {
		padding: 7px 0;
		gap: 5px;
	}

	.param-slider .field-head {
		min-height: 28px;
	}

	.param-slider .field-head label {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	input.field-number {
		width: 54px;
		min-width: 54px;
		max-width: 54px;
		flex: 0 0 54px;
		height: 28px;
		min-height: 28px;
		padding: 2px 5px;
		border-radius: 7px;
		border: 1px solid rgba(255, 255, 255, 0.08);
		background: #11151b;
		color: #d6deea;
		font-size: 11.5px;
		line-height: 1.2;
		min-height: 28px;
	}

	.range-control {
		width: 100%;
		margin: 0;
	}

	.range-scale {
		display: flex;
		align-items: center;
		justify-content: space-between;
		color: var(--muted);
		font-size: 10.5px;
		line-height: 1;
	}

	.toggle-field {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		align-items: start;
		gap: 9px;
		padding: 8px 0;
		border-bottom: 1px solid rgba(255, 255, 255, 0.04);
		color: var(--text);
	}

	.toggle-field input {
		margin-top: 3px;
	}

	.toggle-field strong {
		display: block;
		font-size: 13px;
		line-height: 1.25;
	}

	.toggle-field small {
		display: block;
		margin-top: 3px;
		color: var(--muted);
		font-size: 11px;
		line-height: 1.45;
	}

	.param-pop.compact {
		position: relative;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		padding: 0;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: rgba(22, 26, 32, 0.72);
		color: #d9e2ef;
		cursor: help;
	}

	.param-panel {
		position: absolute;
		right: 0;
		bottom: calc(100% + 10px);
		display: none;
		width: min(320px, calc(100vw - 32px));
		max-height: 248px;
		overflow: auto;
		padding: 10px;
		border-radius: 10px;
		border: 1px solid rgba(255, 255, 255, 0.1);
		background: rgba(12, 15, 20, 0.94);
		backdrop-filter: blur(18px);
		color: #eef3fb;
		box-shadow: 0 18px 42px rgba(0, 0, 0, 0.4);
		z-index: 130;
		text-align: left;
	}

	.param-pop:hover .param-panel,
	.param-pop:focus-within .param-panel {
		display: grid;
		gap: 10px;
	}

	.param-panel > strong {
		font-size: 12px;
		line-height: 1.2;
		color: #ffffff;
	}

	.param-grid {
		display: grid;
		grid-template-columns: minmax(56px, max-content) minmax(0, 1fr);
		gap: 6px 8px;
	}

	.param-key,
	.param-value {
		min-width: 0;
		font-size: 11px;
		line-height: 1.45;
	}

	.param-key {
		color: #91a0b3;
	}

	.param-value {
		color: #edf3fb;
		overflow-wrap: anywhere;
	}

	/* ── 区域分隔线 ── */
	.section-divider {
		border-top: 1px solid var(--line);
		padding-top: 14px;
	}

	/* ── 内联参数行 ── */
	.param-inline-row {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
		padding: 6px 0;
	}

	.param-inline {
		display: flex;
		align-items: center;
		gap: 4px;
		font-size: 12px;
		color: var(--muted);
	}

	.param-inline select {
		min-height: 28px;
		padding: 3px 8px;
		border-radius: 6px;
		font-size: 12px;
		min-width: 90px;
	}

	.param-inline-range {
		display: flex;
		align-items: center;
		gap: 4px;
		font-size: 12px;
		color: var(--muted);
	}

	.param-inline-range input[type='range'] {
		width: 80px;
		height: 4px;
	}

	.voice-inline {
		display: flex;
		align-items: center;
		gap: 4px;
	}

	.voice-inline select {
		min-height: 28px;
		padding: 3px 8px;
		border-radius: 6px;
		font-size: 12px;
		min-width: 100px;
	}

	/* ── 更多参数折叠面板 ── */
	.more-params-panel {
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: var(--panel-2);
		display: grid;
		gap: 8px;
	}

	.advanced-divider {
		border-top: 1px dashed var(--line);
		margin: 6px 0 0;
		text-align: center;
	}

	.advanced-divider span {
		background: var(--panel-2);
		padding: 0 8px;
		font-size: 11px;
		color: var(--muted);
		position: relative;
		top: -8px;
	}

	/* ── 结果筛选行（单行内联） ── */
	.records-filter-inline {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}

	.records-filter-inline select {
		min-height: 28px;
		padding: 3px 8px;
		border-radius: 6px;
		font-size: 12px;
		min-width: 80px;
	}

	.records-filter-inline .search-field {
		flex: 1;
		min-width: 140px;
	}

	/* ── 响应式 ── */
	@media (max-width: 900px) {
		.param-inline-row {
			gap: 6px;
		}

		.records-filter-inline {
			gap: 6px;
		}

		.tool-row,
		.result-headline,
		.result-info {
			flex-direction: column;
			align-items: flex-start;
		}

		.result-info-right {
			margin-left: 0;
		}

		.preset-editor-grid,
		.strategy-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}

	@media (max-width: 640px) {
		.param-inline-row {
			flex-direction: column;
			align-items: flex-start;
		}

		.records-filter-inline {
			flex-direction: column;
			align-items: stretch;
		}

		.preset-editor-grid,
		.strategy-grid {
			grid-template-columns: 1fr;
		}

		.dialog-head,
		.dialog-options,
		.longform-card-head {
			align-items: stretch;
			flex-direction: column;
		}

		.longform-segment {
			grid-template-columns: auto minmax(0, 1fr);
		}

		.param-field {
			grid-template-columns: 1fr;
			gap: 4px;
		}

		.param-label {
			min-height: auto;
		}
	}
</style>
