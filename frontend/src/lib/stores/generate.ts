import { get, writable } from 'svelte/store';
import type {
	EngineDetail,
	EngineSpeaker,
	GenerationTask,
	GeneratePlanResponse,
	GenerateRequest,
	LongformTask,
	PresetTemplate,
	TTSVerificationResponse,
	VoiceAsset,
	AppSettings
} from '$lib/api/types';

export type TaskStatusTab = 'all' | 'active' | 'success' | 'failed';
export type TaskSourceFilter = 'all' | 'local' | 'cloud';
export type TaskDateFilter = 'all' | 'today' | '7d' | '30d';
export type TaskSortBy = 'latest' | 'oldest' | 'duration_desc';
export type LongformStrategy = 'split_merge' | 'split_only' | 'single';

export type PresetDraft = {
	name: string;
	scene: string;
	description: string;
	tags: string;
	sample_text: string;
};

export type GenerateStoreState = {
	engines: EngineDetail[];
	voices: VoiceAsset[];
	presets: PresetTemplate[];
	settings: AppSettings | null;
	voicePreviewAudio: HTMLAudioElement | null;
	voicePreviewPlaying: boolean;
	resultPreviewAudio: HTMLAudioElement | null;
	playingResultTaskId: string;
	resultAudioPlaying: boolean;
	showPresetEditor: boolean;
	editingPresetId: string;
	presetBusy: boolean;
	presetDraft: PresetDraft;
	text: string;
	textSegments: string[];
	textToolBusy: 'clean' | 'numbers' | 'split' | '';
	showSplitPreview: boolean;
	splitPreviewCollapsed: boolean;
	lastGeneratePlan: GeneratePlanResponse | null;
	engineId: string;
	voiceId: string;
	language: string;
	emotion: string;
	voiceDesign: string;
	voiceDesignPrompt: string;
	optimizeTextPreview: boolean;
	styleInstruction: string;
	mimoVoice: string;
	speakerId: string;
	speakerQuery: string;
	speakerGenderFilter: 'all' | 'F' | 'M';
	speakerCatalog: EngineSpeaker[];
	speakerCatalogLoading: boolean;
	speakerCatalogKey: string;
	voicePrompt: string;
	emoAlpha: number;
	speed: number;
	nfeStep: number;
	cfgStrength: number;
	targetRms: number;
	crossFadeDuration: number;
	swaySamplingCoef: number;
	fixDuration: number;
	removeSilence: boolean;
	temperature: number;
	topP: number;
	topK: number;
	maxTextTokensPerSegment: number;
	intervalSilence: number;
	diffusionSteps: number;
	cfgRate: number;
	guidanceScale: number;
	duration: number;
	maxMelTokens: number;
	repetitionPenalty: number;
	outputFormat: 'wav' | 'mp3' | 'flac';
	showAdvanced: boolean;
	showMoreParams: boolean;
	tasks: GenerationTask[];
	longformTasks: LongformTask[];
	selectedTaskIds: string[];
	taskQuery: string;
	taskStatusTab: TaskStatusTab;
	taskEngineFilter: string;
	taskSourceFilter: TaskSourceFilter;
	taskDateFilter: TaskDateFilter;
	taskSortBy: TaskSortBy;
	currentPage: number;
	pageSize: number;
	pageSizeAuto: boolean;
	resultGridEl: HTMLDivElement | undefined;
	pageJumpInput: string;
	actionBusyTaskId: string;
	verificationBusyTaskId: string;
	verificationReports: Record<string, TTSVerificationResponse>;
	verificationErrors: Record<string, string>;
	showLongformDialog: boolean;
	pendingLongformPlan: GeneratePlanResponse | null;
	pendingLongformResolve: ((value: LongformStrategy | null) => void) | null;
	longformStrategy: LongformStrategy;
	longformVerifyEnabled: boolean;
	longformMergeEnabled: boolean;
	longformMaxRetries: number;
	busy: boolean;
	error: string;
	initialized: boolean;
	lastEngineId: string;
};

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
	maxMelTokens: 1500,
	repetitionPenalty: 10,
	outputFormat: 'wav' as const
};

const MIMO_DEFAULTS = {
	temperature: 0.6,
	topP: 0.95
};

const OMNIVOICE_DEFAULTS = {
	diffusionSteps: 32,
	guidanceScale: 2.0,
	duration: 0
};

const F5_DEFAULTS = {
	nfeStep: 32,
	cfgStrength: 2.0,
	targetRms: 0.1,
	crossFadeDuration: 0.15,
	swaySamplingCoef: -1.0,
	fixDuration: 0,
	removeSilence: false
};

const DEFAULT_PRESET_DRAFT: PresetDraft = {
	name: '',
	scene: '',
	description: '',
	tags: '',
	sample_text: ''
};

const DEFAULT_VOICE_DESIGN = '女，青年，中音调';
const DEFAULT_VOICE_DESIGN_PROMPT = '中年男性，声线沉稳偏正式，吐字工整，语速适中。';

function createInitialState(): GenerateStoreState {
	return {
		engines: [],
		voices: [],
		presets: [],
		settings: null,
		voicePreviewAudio: null,
		voicePreviewPlaying: false,
		resultPreviewAudio: null,
		playingResultTaskId: '',
		resultAudioPlaying: false,
		showPresetEditor: false,
		editingPresetId: '',
		presetBusy: false,
		presetDraft: { ...DEFAULT_PRESET_DRAFT },
		text: '',
		textSegments: [],
		textToolBusy: '',
		showSplitPreview: false,
		splitPreviewCollapsed: false,
		lastGeneratePlan: null,
		engineId: 'indextts-v2',
		voiceId: '',
		language: 'zh',
		emotion: '',
		voiceDesign: DEFAULT_VOICE_DESIGN,
		voiceDesignPrompt: DEFAULT_VOICE_DESIGN_PROMPT,
		optimizeTextPreview: false,
		styleInstruction: '',
		mimoVoice: 'mimo_default',
		speakerId: '',
		speakerQuery: '',
		speakerGenderFilter: 'all',
		speakerCatalog: [],
		speakerCatalogLoading: false,
		speakerCatalogKey: '',
		voicePrompt: '',
		emoAlpha: INDEX_TTS_DEFAULTS.emoAlpha,
		speed: INDEX_TTS_DEFAULTS.speed,
		nfeStep: F5_DEFAULTS.nfeStep,
		cfgStrength: F5_DEFAULTS.cfgStrength,
		targetRms: F5_DEFAULTS.targetRms,
		crossFadeDuration: F5_DEFAULTS.crossFadeDuration,
		swaySamplingCoef: F5_DEFAULTS.swaySamplingCoef,
		fixDuration: F5_DEFAULTS.fixDuration,
		removeSilence: F5_DEFAULTS.removeSilence,
		temperature: INDEX_TTS_DEFAULTS.temperature,
		topP: INDEX_TTS_DEFAULTS.topP,
		topK: INDEX_TTS_DEFAULTS.topK,
		maxTextTokensPerSegment: INDEX_TTS_DEFAULTS.maxTextTokensPerSegment,
		intervalSilence: INDEX_TTS_DEFAULTS.intervalSilence,
		diffusionSteps: INDEX_TTS_DEFAULTS.diffusionSteps,
		cfgRate: INDEX_TTS_DEFAULTS.cfgRate,
		guidanceScale: OMNIVOICE_DEFAULTS.guidanceScale,
		duration: OMNIVOICE_DEFAULTS.duration,
		maxMelTokens: INDEX_TTS_DEFAULTS.maxMelTokens,
		repetitionPenalty: INDEX_TTS_DEFAULTS.repetitionPenalty,
		outputFormat: INDEX_TTS_DEFAULTS.outputFormat,
		showAdvanced: false,
		showMoreParams: false,
		tasks: [],
		longformTasks: [],
		selectedTaskIds: [],
		taskQuery: '',
		taskStatusTab: 'all',
		taskEngineFilter: 'all',
		taskSourceFilter: 'all',
		taskDateFilter: 'all',
		taskSortBy: 'latest',
		currentPage: 1,
		pageSize: 12,
		pageSizeAuto: true,
		resultGridEl: undefined,
		pageJumpInput: '',
		actionBusyTaskId: '',
		verificationBusyTaskId: '',
		verificationReports: {},
		verificationErrors: {},
		showLongformDialog: false,
		pendingLongformPlan: null,
		pendingLongformResolve: null,
		longformStrategy: 'split_merge',
		longformVerifyEnabled: true,
		longformMergeEnabled: true,
		longformMaxRetries: 2,
		busy: false,
		error: '',
		initialized: false,
		lastEngineId: 'indextts-v2'
	};
}

function isMimoEngine(engineId: string) {
	return engineId.startsWith('mimo-v2.5');
}

function getSelectedEngine(state: GenerateStoreState, engineId = state.engineId) {
	return state.engines.find((engine) => engine.manifest.engine_id === engineId);
}

function getActiveParamKeys(state: GenerateStoreState, engineId = state.engineId) {
	return new Set(getSelectedEngine(state, engineId)?.manifest.parameter_schema.map((param) => param.key) ?? []);
}

function getEngineDefaults(state: GenerateStoreState, engineId: string) {
	const selectedEngine = getSelectedEngine(state, engineId);
	const speakerParam = selectedEngine?.manifest.parameter_schema.find((param) => param.key === 'speaker_id');
	const promptParam = selectedEngine?.manifest.parameter_schema.find((param) => param.key === 'prompt');
	const languageParam = selectedEngine?.manifest.parameter_schema.find((param) => param.key === 'language');

	return {
		engineId,
		lastEngineId: engineId,
		showAdvanced: engineId === 'f5-tts',
		language: String(languageParam?.default ?? state.language),
		emotion: INDEX_TTS_DEFAULTS.emotion,
		emoAlpha: INDEX_TTS_DEFAULTS.emoAlpha,
		speed: INDEX_TTS_DEFAULTS.speed,
		temperature: isMimoEngine(engineId) ? MIMO_DEFAULTS.temperature : INDEX_TTS_DEFAULTS.temperature,
		topP: isMimoEngine(engineId) ? MIMO_DEFAULTS.topP : INDEX_TTS_DEFAULTS.topP,
		topK: INDEX_TTS_DEFAULTS.topK,
		maxTextTokensPerSegment: INDEX_TTS_DEFAULTS.maxTextTokensPerSegment,
		intervalSilence: INDEX_TTS_DEFAULTS.intervalSilence,
		diffusionSteps: engineId === 'omnivoice' ? OMNIVOICE_DEFAULTS.diffusionSteps : INDEX_TTS_DEFAULTS.diffusionSteps,
		cfgRate: INDEX_TTS_DEFAULTS.cfgRate,
		guidanceScale: OMNIVOICE_DEFAULTS.guidanceScale,
		duration: OMNIVOICE_DEFAULTS.duration,
		outputFormat: INDEX_TTS_DEFAULTS.outputFormat,
		nfeStep: F5_DEFAULTS.nfeStep,
		cfgStrength: F5_DEFAULTS.cfgStrength,
		targetRms: F5_DEFAULTS.targetRms,
		crossFadeDuration: F5_DEFAULTS.crossFadeDuration,
		removeSilence: F5_DEFAULTS.removeSilence,
		speakerId: String(speakerParam?.default ?? speakerParam?.options?.[0]?.value ?? ''),
		voicePrompt: String(promptParam?.default ?? promptParam?.options?.[0]?.value ?? '')
	};
}

function createRequest(state: GenerateStoreState): GenerateRequest {
	const selected = getSelectedEngine(state);
	const activeParamKeys = getActiveParamKeys(state);
	const selectedVoice = state.voices.find((voice) => voice.voice_id === state.voiceId) ?? null;
	const usesReferenceVoice = Boolean(
		selected &&
		['indextts-v2', 'omnivoice', 'mimo-v2.5-tts-voiceclone', 'f5-tts', 'cosyvoice-zero-shot'].includes(
			selected.manifest.engine_id
		)
	);
	const supportsEmotion = activeParamKeys.has('emotion');
	const isOmniVoice = state.engineId === 'omnivoice';
	const isMimoPreset = state.engineId === 'mimo-v2.5-tts-preset';
	const isMimoDesign = state.engineId === 'mimo-v2.5-tts-voicedesign';
	const isMimo = isMimoEngine(state.engineId);

	return {
		text: state.text,
		engine_id: state.engineId,
		voice_id: usesReferenceVoice ? state.voiceId || null : null,
		ref_text:
			usesReferenceVoice && selectedVoice?.reference_text.trim()
				? selectedVoice.reference_text.trim()
				: null,
		language: state.language,
		emotion_mode: supportsEmotion && Boolean(state.emotion) ? 'emotion_vector' : 'follow_reference',
		emotion: supportsEmotion && Boolean(state.emotion) ? state.emotion : null,
		emotion_text: isOmniVoice && !state.voiceId ? state.voiceDesign : null,
		style_instruction: isMimo ? state.styleInstruction || null : null,
		voice_design_prompt: isMimoDesign ? state.voiceDesignPrompt : null,
		optimize_text_preview: isMimoDesign ? state.optimizeTextPreview : false,
		mimo_voice: isMimoPreset ? state.mimoVoice : null,
		speaker_id: activeParamKeys.has('speaker_id') ? state.speakerId || null : null,
		prompt: activeParamKeys.has('prompt') ? state.voicePrompt || null : null,
		nfe_step: state.nfeStep,
		cfg_strength: state.cfgStrength,
		target_rms: state.targetRms,
		cross_fade_duration: state.crossFadeDuration,
		sway_sampling_coef: state.swaySamplingCoef,
		fix_duration: state.fixDuration,
		remove_silence: state.removeSilence,
		emo_alpha: state.emoAlpha,
		speed: state.speed,
		temperature: state.temperature,
		top_p: state.topP,
		top_k: state.topK,
		repetition_penalty: state.repetitionPenalty,
		max_mel_tokens: state.maxMelTokens,
		max_text_tokens_per_segment: state.maxTextTokensPerSegment,
		interval_silence: state.intervalSilence,
		segment_overlap_ms: 50,
		diffusion_steps: state.diffusionSteps,
		cfg_rate: state.cfgRate,
		guidance_scale: state.guidanceScale,
		duration: state.duration,
		output_format: state.outputFormat
	};
}

function applyRequest(state: GenerateStoreState, req: GenerateRequest): Partial<GenerateStoreState> {
	const engineDefaults = getEngineDefaults(state, req.engine_id);
	const isMimoEngineRequest = isMimoEngine(req.engine_id);

	return {
		...engineDefaults,
		text: req.text,
		voiceId: req.voice_id ?? '',
		language: req.language || 'zh',
		emotion:
			req.emotion_mode === 'emotion_vector' && typeof req.emotion === 'string' ? req.emotion : '',
		voiceDesign:
			req.emotion_mode === 'emotion_text' && typeof req.emotion_text === 'string'
				? req.emotion_text
				: DEFAULT_VOICE_DESIGN,
		voiceDesignPrompt: req.voice_design_prompt || DEFAULT_VOICE_DESIGN_PROMPT,
		optimizeTextPreview: req.optimize_text_preview ?? false,
		styleInstruction: req.style_instruction || '',
		mimoVoice: req.mimo_voice || 'mimo_default',
		speakerId: req.speaker_id || '',
		voicePrompt: req.prompt || '',
		nfeStep: req.nfe_step ?? F5_DEFAULTS.nfeStep,
		cfgStrength: req.cfg_strength ?? F5_DEFAULTS.cfgStrength,
		targetRms: req.target_rms ?? F5_DEFAULTS.targetRms,
		crossFadeDuration: req.cross_fade_duration ?? F5_DEFAULTS.crossFadeDuration,
		swaySamplingCoef: req.sway_sampling_coef ?? F5_DEFAULTS.swaySamplingCoef,
		fixDuration: req.fix_duration ?? F5_DEFAULTS.fixDuration,
		removeSilence: req.remove_silence ?? F5_DEFAULTS.removeSilence,
		emoAlpha: req.emo_alpha ?? INDEX_TTS_DEFAULTS.emoAlpha,
		speed: req.speed ?? INDEX_TTS_DEFAULTS.speed,
		temperature:
			req.temperature ?? (isMimoEngineRequest ? MIMO_DEFAULTS.temperature : INDEX_TTS_DEFAULTS.temperature),
		topP: req.top_p ?? (isMimoEngineRequest ? MIMO_DEFAULTS.topP : INDEX_TTS_DEFAULTS.topP),
		topK: req.top_k ?? INDEX_TTS_DEFAULTS.topK,
		maxTextTokensPerSegment:
			req.max_text_tokens_per_segment ?? INDEX_TTS_DEFAULTS.maxTextTokensPerSegment,
		intervalSilence: req.interval_silence ?? INDEX_TTS_DEFAULTS.intervalSilence,
		diffusionSteps: req.diffusion_steps ?? (req.engine_id === 'omnivoice' ? OMNIVOICE_DEFAULTS.diffusionSteps : INDEX_TTS_DEFAULTS.diffusionSteps),
		cfgRate: req.cfg_rate ?? INDEX_TTS_DEFAULTS.cfgRate,
		guidanceScale: req.guidance_scale ?? OMNIVOICE_DEFAULTS.guidanceScale,
		duration: req.duration ?? OMNIVOICE_DEFAULTS.duration,
		maxMelTokens: req.max_mel_tokens ?? INDEX_TTS_DEFAULTS.maxMelTokens,
		repetitionPenalty: req.repetition_penalty ?? INDEX_TTS_DEFAULTS.repetitionPenalty,
		outputFormat: req.output_format ?? INDEX_TTS_DEFAULTS.outputFormat,
		showAdvanced: req.engine_id === 'f5-tts'
	};
}

function createGenerateStore() {
	const store = writable<GenerateStoreState>(createInitialState());

	return {
		subscribe: store.subscribe,
		set: store.set,
		update: store.update,
		setEngine(engineId: string) {
			store.update((state) => ({
				...state,
				...getEngineDefaults(state, engineId)
			}));
		},
		toRequest() {
			return createRequest(get(store));
		},
		fromRequest(req: GenerateRequest) {
			store.update((state) => ({
				...state,
				...applyRequest(state, req)
			}));
		},
		reset() {
			store.set(createInitialState());
		}
	};
}

export const generateStore = createGenerateStore();
