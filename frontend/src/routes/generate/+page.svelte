<script lang="ts">
	import { Api } from '$lib/api';
	import type { AppSettings, EngineDetail, EngineSpeaker, GenerationTask, GeneratePlanResponse, GenerateRequest, LongformTask, PlannedTextSegment, PresetTemplate, TaskPageParams, TaskSummary, TranscriptionRecord, TranscriptionSegment, TTSVerificationResponse, UploadResult, VoiceAsset, VoiceAssetCreate, VoiceClipTranscribeResponse } from '$lib/api/types';
	import { taskStatusLabel } from '$lib/labels';
	import { Captions, CheckSquare, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, CircleCheck, CloudUpload, FileAudio, FileText, Info, Mic, Cpu, Pencil, Play, Plus, Repeat, RotateCcw, Save, Search, Settings, SlidersHorizontal, Square, Trash2, X } from 'lucide-svelte';
	import { onMount, tick, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import EngineSelector from './components/EngineSelector.svelte';
	import VoiceSelector from './components/VoiceSelector.svelte';
	import TextInput from './components/TextInput.svelte';
	import ParameterPanel from './components/ParameterPanel.svelte';
	import HoverCopyPopover from './components/HoverCopyPopover.svelte';
	import WaveformResultPlayer from './components/WaveformResultPlayer.svelte';
	import Slider from '$lib/components/shared/Slider.svelte';
	import Toggle from '$lib/components/shared/Toggle.svelte';
	import Tooltip from '$lib/components/shared/Tooltip.svelte';
	import DoubaoVoiceCatalogDrawer from '$lib/components/DoubaoVoiceCatalogDrawer.svelte';
	import { DOUBAO_TTS_DEFAULTS, generateStore } from '$lib/stores/generate';
	import * as H from './helpers';
	import type { LongformStrategy, PresetDraft } from '$lib/stores/generate';
	import type { TaskDateFilter, TaskSortBy, TaskSourceFilter, TaskStatusTab } from './helpers';
	import { taskDateStartIso, taskServerQuery } from './records-query';
	import SeedAudioPanel from './engine-ui/seed-audio/SeedAudioPanel.svelte';
	import SeedAudioInlineControls from './engine-ui/seed-audio/SeedAudioInlineControls.svelte';
	import { engineUiRegistry } from './engine-ui/registry';
	import { seedAudioProfile } from './engine-ui/seed-audio/profile';
	import { seedAudioStateFromRequest, seedAudioStateToRequest } from './engine-ui/seed-audio/request';
	import { applySeedAudioPreset, seedAudioPresetsForMode, type SeedAudioPresetBundle } from './engine-ui/seed-audio/presets';
	import {
		SEED_AUDIO_ENGINE_ID,
		activeSeedAudioDraft,
		createDefaultSeedAudioState,
		setSeedAudioImage,
		setSeedAudioReference,
		updateSeedAudioPrompt,
		type SeedAudioReferenceAsset,
		type SeedAudioState
	} from './engine-ui/seed-audio/state';
	import {
		createReferenceAudioDraft,
		legacyCustomVoicePatchFromDraft,
		referenceAudioDraftFromLegacyState
	} from './engine-ui/reference-audio/draft';

	if (!engineUiRegistry.has(SEED_AUDIO_ENGINE_ID)) engineUiRegistry.register(seedAudioProfile);

	const store = generateStore;
	type VideoLocalizationHandoffMeta = {
		source: 'video_localization';
		mode: 'tune_with_recipe' | 'reference_only';
		project_id: string;
		cue_id: string;
		reference_clip_id: string | null;
		recipe_id: string | null;
		created_at: string;
	};
	type ComposerPreset = PresetTemplate & { seedBundle?: SeedAudioPresetBundle };

	let _autoResizeRO: ResizeObserver | undefined = $state();
	let _speakerCatalogRequestKey = '';
	let _speakerCatalogTimer: ReturnType<typeof setTimeout> | undefined;
	let composerDataPromise: Promise<void> | null = null;
	let recordsDataPromise: Promise<void> | null = null;
	let taskPageTimer: ReturnType<typeof setTimeout> | null = null;
	let taskSocketReconnectTimer: ReturnType<typeof setTimeout> | null = null;
	let taskSocket: WebSocket | null = null;
	let taskSocketClosed = false;
	let recordsViewportEl: HTMLElement | undefined = $state();
	let recordsBottomPagerEl: HTMLElement | undefined = $state();
	let taskCardStreamTimer: ReturnType<typeof setTimeout> | null = null;
	let taskCardRenderLimit = $state(0);
	let recordsRefreshing = $state(false);
	let recordsInitialized = $state(false);
	let taskTotal = $state(0);
	let taskSummary: TaskSummary = $state({ all: 0, active: 0, processing: 0, waiting: 0, success: 0, failed: 0 });
	let taskDownloadSequences: Record<string, number> = $state({});
	let recordsLastSyncedAt = $state('');
	let presetStripOpen = $state(false);
	let resultAudioPendingTaskId = $state('');
	let resultAudioCurrentTime = $state(0);
	let resultAudioFrame: number | null = null;
	let customVoicePreviewFrame: number | null = null;
	let customVoiceDragActive = $state(false);
	let customVoiceBusyMode: 'source' | 'clip' | '' = $state('');
	let voiceRegisterOpen = $state(false);
	let voiceRegisterBusy = $state(false);
	let voiceRegisterSerBusy = $state(false);
	let voiceRegisterError = $state('');
	let voiceRegisterName = $state('');
	let voiceRegisterDescription = $state('');
	let voiceRegisterTags = $state('');
	let voiceRegisterEmotionTags = $state('');
	let voiceRegisterReferenceText = $state('');
	let voiceRegisterLicense = $state('self_voice');
	let voiceRegisterEngine = $state('indextts-v2');
	let customVoiceOriginalFile: File | null = $state(null);
	let customVoiceSourcePreviewUrl = $state('');
	let customVoiceSourceDurationMs: number | null = $state(null);
	let customVoiceTrimStart = $state(0);
	let customVoiceTrimEnd = $state(0);
	let customVoiceSelectionDirty = $state(false);
	let customVoiceLoopPreview = $state(false);
	let customVoiceLoopEnabled = $state(true);
	let customVoicePlaybackPosition = $state(0);
	let customVoiceWaveformBars: number[] = $state([]);
	let customVoiceWaveformLoading = $state(false);
	let customVoiceWaveformProgress = $state(0);
	let customVoiceTimelineScrollLeft = $state(0);
	let customVoiceTimelineViewportWidth = $state(0);
	let customVoiceTimelineZoom = $state(1);
	let customVoiceTrimHover = $state(false);
	let customVoiceTrimFocusWithin = $state(false);
	let videoLocalizationHandoff = $state<VideoLocalizationHandoffMeta | null>(null);
	let seedVoicePickerSlot: 1 | 2 | 3 | null = $state(null);
	let seedSpeakerPickerSlot: 1 | 2 | 3 | null = $state(null);
	let seedEditingSlot: 1 | 2 | 3 | null = $state(null);
	let seedSpeakerId = $state('');
	let seedSpeakerName = $state('');
	let seedAssetBusy = $state(false);
	let seedAssetError = $state('');
	let seedPreviewAudio: HTMLAudioElement | null = $state(null);
	let seedPreviewingSlot: 1 | 2 | 3 | null = $state(null);
	let seedShowAdvanced = $state(false);
	const seedOwnedObjectUrls = new Set<string>();

	const selected = $derived($store.engines.find(e => e.manifest.engine_id === $store.engineId));
	const activeParamKeys = $derived(new Set(selected?.manifest.parameter_schema.map(p => p.key) ?? []));
	const ttsEngines = $derived($store.engines.filter(e => !e.manifest.capabilities.includes('speech_recognition')));
	const selectedVoice = $derived($store.voices.find(v => v.voice_id === $store.voiceId) ?? null);
	const selectedVoicePreviewUrl = $derived(selectedVoice?.reference_audio_ids[0] ? `/api/voices/${encodeURIComponent(selectedVoice.voice_id)}/audio/${encodeURIComponent(selectedVoice.reference_audio_ids[0])}` : '');
	const activeVoicePreviewUrl = $derived($store.voiceSource === 'reference_audio' ? $store.customVoicePreviewUrl : selectedVoicePreviewUrl);
	const customVoiceReady = $derived(Boolean($store.customVoiceReferenceAudioPath && $store.customVoiceTranscript.trim()));
	const customVoiceMatched = $derived(customVoiceReady && $store.customVoiceConfirmed);
	const customVoiceSelectedDurationMs = $derived(Math.max(0, Math.round((customVoiceTrimEnd - customVoiceTrimStart) * 1000)));
	const customVoiceDisplayDurationMs = $derived($store.customVoiceDurationMs ?? (customVoiceSourceDurationMs ? customVoiceSelectedDurationMs : null));
	const customVoiceDurationSeconds = $derived(Math.max(0, (customVoiceSourceDurationMs ?? 0) / 1000));
	const customVoiceActiveClipLabel = $derived($store.customVoiceFileId ? `${$store.customVoiceFileId.slice(0, 8)} · ${formatDuration($store.customVoiceDurationMs)}` : (customVoiceSelectionDirty ? '选区待识别' : '未生成'));
	const customVoiceTrimStartPercent = $derived(customVoiceDurationSeconds ? Math.max(0, Math.min(100, (customVoiceTrimStart / customVoiceDurationSeconds) * 100)) : 0);
	const customVoiceTrimEndPercent = $derived(customVoiceDurationSeconds ? Math.max(0, Math.min(100, (customVoiceTrimEnd / customVoiceDurationSeconds) * 100)) : 0);
	const customVoicePlayheadPercent = $derived(customVoiceDurationSeconds ? Math.max(0, Math.min(100, (customVoicePlaybackPosition / customVoiceDurationSeconds) * 100)) : customVoiceTrimStartPercent);
	const customVoiceTimelineTicks = $derived.by(() => buildTimelineTicks(customVoiceDurationSeconds, customVoiceTimelineZoom));
	const customVoiceVisibleWaveformBars = $derived.by(() => buildVisibleWaveformBars(customVoiceWaveformBars, customVoiceTimelineZoom, customVoiceTimelineScrollLeft, customVoiceTimelineViewportWidth));
	const customVoiceTrimHotkeysActive = $derived(customVoiceTrimHover || customVoiceTrimFocusWithin);
	const voiceMap = $derived(new Map($store.voices.map(v => [v.voice_id, v])));
	const engineMap = $derived(new Map($store.engines.map(e => [e.manifest.engine_id, e])));
	const supportsEmotion = $derived(activeParamKeys.has('emotion'));
	const isIndexTTS = $derived($store.engineId === 'indextts-v2'); const isOmniVoice = $derived($store.engineId === 'omnivoice');
	const isMimoPreset = $derived($store.engineId === 'mimo-v2.5-tts-preset'); const isMimoDesign = $derived($store.engineId === 'mimo-v2.5-tts-voicedesign');
	const isMimoClone = $derived($store.engineId === 'mimo-v2.5-tts-voiceclone'); const isMimo = $derived($store.engineId.startsWith('mimo-v2.5'));
	const isDoubaoPreset = $derived($store.engineId === 'doubao-tts-preset'); const isDoubaoClone = $derived($store.engineId === 'doubao-tts-voiceclone'); const isDoubao = $derived(isDoubaoPreset || isDoubaoClone);
	const isEmotiVoice = $derived($store.engineId === 'emotivoice'); const isF5 = $derived($store.engineId === 'f5-tts'); const isConfucius4 = $derived($store.engineId === 'confucius4-mlx-int8'); const isQwen3TTS = $derived($store.engineId === 'qwen3-tts-mlx-0.6b');
	const isCosyVoice = $derived($store.engineId === 'cosyvoice-sft'); const isCosyVoiceZeroShot = $derived($store.engineId === 'cosyvoice-zero-shot');
	const isSeedAudio = $derived($store.engineId === SEED_AUDIO_ENGINE_ID);
	const seedAudioState = $derived(($store.engineUiStateById[SEED_AUDIO_ENGINE_ID] as SeedAudioState | undefined) ?? createDefaultSeedAudioState());
	const seedVoiceChoices = $derived($store.voices.filter(voice => voice.reference_audio_ids.length > 0));
	const seedCloudSpeakerChoices = $derived.by(() => $store.voices.flatMap(voice => voice.engine_bindings.filter(binding => binding.available && binding.external_voice_id).map(binding => ({ id: binding.external_voice_id!, name: voice.name }))));
	const usesReferenceVoice = $derived(isIndexTTS || isOmniVoice || isConfucius4 || isQwen3TTS || isMimoClone || isDoubaoClone || isF5 || isCosyVoiceZeroShot);
	const qwen3ReferenceRoute = $derived(isQwen3TTS && ($store.voiceSource === 'reference_audio' || Boolean($store.voiceId)));
	const qwen3VoiceDesignRoute = $derived(isQwen3TTS && !qwen3ReferenceRoute && Boolean($store.voiceDesignPrompt.trim()));
	const qwen3PresetRoute = $derived(isQwen3TTS && !qwen3ReferenceRoute && !qwen3VoiceDesignRoute);
	const qwen3PresetDisabledText = $derived($store.voiceSource === 'reference_audio' ? '自定义音色已接管，预置音色不参与本次生成。' : '本地音色库已接管，预置音色不参与本次生成。');
	const followsReferenceEmotion = $derived(isIndexTTS && !$store.emotion);
	const enginePresets = $derived.by((): ComposerPreset[] => {
		if (!isSeedAudio) return $store.presets.filter((preset) => preset.engine_id === $store.engineId);
		const builtins = seedAudioPresetsForMode(seedAudioState.mode).map((preset): ComposerPreset => ({
			preset_id: preset.presetId,
			name: preset.name,
			scene: preset.description,
			description: preset.description,
			engine_id: preset.engineId,
			input_mode: preset.inputMode,
			input_assets: preset.assets,
			sample_text: preset.promptTemplate,
			parameters: {},
			source_test_id: null,
			recommended_voice_type: 'generated_audio',
			tags: preset.tags,
			seedBundle: preset
		}));
		const custom = $store.presets.filter((preset) =>
			preset.engine_id === SEED_AUDIO_ENGINE_ID && preset.input_mode === seedAudioState.mode
		);
		return [...builtins, ...custom];
	});
	const hasRunningTasks = $derived(taskSummary.active > 0 || $store.longformTasks.some(t => H.statusIsActive(t.status)));
	const visibleLongformTasks = $derived($store.longformTasks.filter(t => t.status !== 'success'));
	const queueOrderedTasks = $derived.by(() => $store.tasks.filter(t => H.taskIsActive(t)).sort((a, b) => a.created_at.localeCompare(b.created_at) || a.task_id.localeCompare(b.task_id)));
	const queueCounts = $derived.by(() => ({ processing: taskSummary.processing, waiting: taskSummary.waiting }));
	const doubaoRecentSpeakerIds = $derived.by(() => [...new Set($store.tasks
		.filter((task) => task.engine_id === 'doubao-tts-preset' && task.status === 'success')
		.sort((left, right) => right.created_at.localeCompare(left.created_at))
		.map((task) => String(task.parameters?.speaker_id || ''))
		.filter(Boolean))].slice(0, 12));
	const taskEngineOptions = $derived(['all', ...new Set($store.engines.map(e => e.manifest.engine_id))]);
	const hasSearchableSpeakerCatalog = $derived(isEmotiVoice || isDoubaoPreset);
	const activeSpeakerCatalogKey = $derived(`${$store.engineId}|${isDoubaoPreset ? '' : $store.speakerQuery.trim()}|${isDoubaoPreset ? 'all' : $store.speakerGenderFilter}`);
	const speakerCatalogIsCurrent = $derived(hasSearchableSpeakerCatalog && $store.speakerCatalogKey === activeSpeakerCatalogKey);
	const speakerChoices = $derived(speakerCatalogIsCurrent ? $store.speakerCatalog.map(s => ({ label: s.label, value: s.speaker_id })) : selected?.manifest.parameter_schema.find(p => p.key === 'speaker_id')?.options ?? []);
	const promptOptions = $derived(selected?.manifest.parameter_schema.find(p => p.key === 'prompt')?.options ?? []);
	const mimoVoiceOptions = $derived(selected?.manifest.parameter_schema.find(p => p.key === 'mimo_voice')?.options ?? []);
	const doubaoSampleRateOptions = $derived(selected?.manifest.parameter_schema.find(p => p.key === 'sample_rate')?.options ?? []);
	const doubaoBitRateOptions = $derived(selected?.manifest.parameter_schema.find(p => p.key === 'bit_rate')?.options ?? []);
	const languageOptions = $derived(selected?.manifest.parameter_schema.find(p => p.key === 'language')?.options ?? [{ label: '中文', value: 'zh' }, { label: '英文', value: 'en' }, { label: '自动', value: 'auto' }]);
	const advancedParameterSchema = $derived(selected?.manifest.parameter_schema.filter(p => p.level === 'advanced' || p.level === 'developer') ?? []);
	const genericAdvancedParameterSchema = $derived(isDoubao ? advancedParameterSchema.filter(p => !['pitch_rate', 'loudness_rate', 'sample_rate', 'bit_rate', 'enable_subtitle', 'silence_duration', 'aigc_watermark'].includes(p.key)) : advancedParameterSchema);
	const styleInstructionParam = $derived(selected?.manifest.parameter_schema.find(p => p.key === 'style_instruction'));
	const styleInstructionLabel = $derived(styleInstructionParam?.label ?? '风格指令');
	const styleInstructionTooltip = $derived(styleInstructionParam?.description ?? '');
	const doubaoCloneVoices = $derived($store.voices.filter(v => v.engine_bindings.some(b => b.engine_id === 'doubao-tts-voiceclone' && b.available)));
	const visibleVoiceOptions = $derived(isDoubaoClone ? doubaoCloneVoices : $store.voices);
	const styleInstructionPlaceholder = $derived(isDoubao ? '例如：语速慢一点，语气更惊讶，句尾带一点感叹。' : (isQwen3TTS ? '例如：语气温柔，语速稍慢，像在讲解课程' : '例如：语速稍慢，语气温柔，像知识视频旁白。'));
	const hasMoreParams = $derived(
		(activeParamKeys.has('speaker_id') && !isDoubaoPreset) ||
		activeParamKeys.has('prompt') ||
		isMimoPreset ||
		activeParamKeys.has('language') ||
		activeParamKeys.has('style_instruction') ||
		activeParamKeys.has('voice_design_prompt') ||
		isMimo ||
		supportsEmotion ||
		(isOmniVoice && !$store.voiceId) ||
		advancedParameterSchema.length > 0
	);
	const taskFilterEngineIds = $derived.by(() => {
		if ($store.taskEngineFilter !== 'all') return [$store.taskEngineFilter];
		if ($store.taskSourceFilter === 'all') return undefined;
		return $store.engines.filter(e => e.manifest.engine_type === $store.taskSourceFilter).map(e => e.manifest.engine_id);
	});
	const taskFilterVoiceIds = $derived.by(() => {
		const query = $store.taskQuery.trim().toLowerCase();
		if (!query) return undefined;
		return $store.voices.filter(v => v.name.toLowerCase().includes(query)).map(v => v.voice_id);
	});
	const taskPageParams = $derived.by((): TaskPageParams => ({
		offset: ($store.currentPage - 1) * $store.pageSize,
		limit: $store.pageSize,
		status: $store.taskStatusTab,
		engine_ids: taskFilterEngineIds,
		voice_ids: taskFilterVoiceIds,
		q: taskServerQuery($store.taskQuery),
		created_after: taskDateStartIso($store.taskDateFilter),
		sort: $store.taskSortBy
	}));
	const pageCount = $derived(Math.max(1, Math.ceil(taskTotal / $store.pageSize)));
	const pagedTasks = $derived($store.tasks);
	const renderedPagedTasks = $derived(pagedTasks.slice(0, Math.min(taskCardRenderLimit, pagedTasks.length)));
	const taskCardsStreaming = $derived(recordsInitialized && taskCardRenderLimit < pagedTasks.length);
	const visibleSelectableTasks = $derived(pagedTasks.filter(t => H.taskCanDelete(t)));
	const allVisibleSelected = $derived(visibleSelectableTasks.length > 0 && visibleSelectableTasks.every(t => $store.selectedTaskIds.includes(t.task_id)));
	const statusCounts = $derived(taskSummary);
	const resultPageSizePresets = [8, 10, 12, 14, 16, 18, 20, 24, 28, 32];
	const hasActiveFilters = $derived(Boolean($store.taskQuery.trim()) || $store.taskStatusTab !== 'all' || $store.taskEngineFilter !== 'all' || $store.taskSourceFilter !== 'all' || $store.taskDateFilter !== 'all' || $store.taskSortBy !== 'latest');

	function updateSeedAudioState(next: SeedAudioState) {
		const activeObjectUrls = new Set<string>();
		for (const slot of next.drafts.audio.references) {
			const referenceAudio = slot.asset?.referenceAudio;
			if (!referenceAudio) continue;
			for (const url of [referenceAudio.source.previewUrl, referenceAudio.clip.previewUrl]) {
				if (url.startsWith('blob:')) activeObjectUrls.add(url);
			}
		}
		const imagePreviewUrl = next.drafts.image.image?.previewUrl ?? '';
		if (imagePreviewUrl.startsWith('blob:')) activeObjectUrls.add(imagePreviewUrl);
		for (const url of seedOwnedObjectUrls) {
			if (activeObjectUrls.has(url)) continue;
			URL.revokeObjectURL(url);
			seedOwnedObjectUrls.delete(url);
		}
		$store.engineUiStateById = { ...$store.engineUiStateById, [SEED_AUDIO_ENGINE_ID]: next };
		$store.error = '';
	}
	function createSeedObjectUrl(file: File) {
		const url = URL.createObjectURL(file);
		seedOwnedObjectUrls.add(url);
		return url;
	}
	function releaseSeedObjectUrl(url: string) {
		if (!seedOwnedObjectUrls.delete(url)) return;
		URL.revokeObjectURL(url);
	}
	function seedAudioFileUrl(fileId: string) { return fileId ? `/api/voices/files/${encodeURIComponent(fileId)}/audio` : ''; }
	async function uploadSeedAudioReference(slot: 1 | 2 | 3, file: File) {
		seedAssetError = '';
		if (file.size > 10 * 1024 * 1024) { seedAssetError = '参考声音不能超过 10MB，请裁短或压缩后重试。'; return; }
		if (!file.type.startsWith('audio/') && !/\.(wav|mp3|pcm|ogg|opus)$/i.test(file.name)) { seedAssetError = '只支持 WAV、MP3、PCM 或 OGG Opus 音频。'; return; }
		seedAssetBusy = true;
		const previewUrl = createSeedObjectUrl(file);
		try {
			const [uploaded, durationSeconds] = await Promise.all([Api.uploadVoice(file), loadAudioDuration(previewUrl)]);
			const durationMs = Math.round(durationSeconds * 1000);
			const referenceAudio = createReferenceAudioDraft(`seed-slot-${slot}-${uploaded.file_id}`, {
				sourceKind: 'upload',
				source: { fileId: uploaded.file_id, fileName: file.name, path: uploaded.path, previewUrl, durationMs, mimeType: file.type, sizeBytes: file.size },
				clip: { fileId: uploaded.file_id, fileName: file.name, path: uploaded.path, previewUrl, durationMs, mimeType: file.type, sizeBytes: file.size },
				trim: { startMs: 0, endMs: durationMs },
				qualityWarnings: uploaded.quality.warnings
			});
			const asset: SeedAudioReferenceAsset = {
				assetId: referenceAudio.draftId, type: 'audio', source: 'upload', displayName: file.name,
				voiceId: '', speakerId: '', licenseStatus: 'self_voice', referenceAudio
			};
			updateSeedAudioState(setSeedAudioReference(seedAudioState, slot, asset));
			if (durationMs > 30_000) {
				seedAssetError = '原始音频超过 30 秒，已为你打开编辑器。请裁选 30 秒以内片段并点击“使用选区”。';
				await openSeedAudioEditor(slot, asset);
			}
		} catch (e) {
			releaseSeedObjectUrl(previewUrl);
			seedAssetError = (e as Error).message || '参考声音上传失败，请检查文件后重试。';
		} finally {
			seedAssetBusy = false;
		}
	}
	function chooseSeedVoice(slot: 1 | 2 | 3, voice: VoiceAsset, fileId: string) {
		if (!voice.reference_audio_ids.includes(fileId)) { seedAssetError = '所选文件不属于这个音色，请重新选择。'; return; }
		const referenceAudio = createReferenceAudioDraft(`seed-voice-${voice.voice_id}-${fileId}`, {
			sourceKind: 'voice_library',
			source: { fileId, fileName: `${voice.name} · ${fileId.slice(0, 8)}`, path: `${fileId}.wav`, previewUrl: seedAudioFileUrl(fileId) },
			clip: { fileId, fileName: `${voice.name} · ${fileId.slice(0, 8)}`, path: `${fileId}.wav`, previewUrl: seedAudioFileUrl(fileId) },
			transcript: { text: voice.reference_text }
		});
		updateSeedAudioState(setSeedAudioReference(seedAudioState, slot, {
			assetId: referenceAudio.draftId, type: 'audio', source: 'voice_library', displayName: voice.name,
			voiceId: voice.voice_id, speakerId: '', licenseStatus: voice.license_status, referenceAudio
		}));
		seedVoicePickerSlot = null;
	}
	function chooseSeedSpeaker(slot: 1 | 2 | 3, speakerId: string, name = '') {
		const id = speakerId.trim();
		if (!id) { seedAssetError = '请填写有效的豆包 speaker ID。'; return; }
		updateSeedAudioState(setSeedAudioReference(seedAudioState, slot, {
			assetId: `seed-speaker-${id}`, type: 'speaker', source: 'cloud_speaker', displayName: name.trim() || id,
			voiceId: '', speakerId: id, licenseStatus: 'authorized', referenceAudio: null
		}));
		seedSpeakerPickerSlot = null;
		seedSpeakerId = '';
		seedSpeakerName = '';
	}
	async function uploadSeedAudioImage(file: File) {
		seedAssetError = '';
		if (file.size > 10 * 1024 * 1024) { seedAssetError = '参考图片不能超过 10MB，请压缩后重试。'; return; }
		if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) { seedAssetError = '只支持 JPEG、PNG 或 WebP 图片。'; return; }
		seedAssetBusy = true;
		const previewUrl = createSeedObjectUrl(file);
		try {
			const uploaded = await Api.uploadSeedAudioImage(file, 'self_voice');
			updateSeedAudioState(setSeedAudioImage(seedAudioState, {
				assetId: `seed-image-${uploaded.file_id}`, source: 'upload', fileId: uploaded.file_id,
				displayName: uploaded.original_name, previewUrl, mimeType: uploaded.mime_type,
				sizeBytes: uploaded.size_bytes, licenseStatus: uploaded.license_status
			}));
		} catch (e) {
			releaseSeedObjectUrl(previewUrl);
			seedAssetError = (e as Error).message || '参考图片上传失败，请检查格式后重试。';
		} finally { seedAssetBusy = false; }
	}
	async function previewSeedReference(slot: 1 | 2 | 3, asset: SeedAudioReferenceAsset) {
		if (asset.type === 'speaker') { seedAssetError = '云端 speaker 没有本地样音；生成后可在结果中试听。'; return; }
		const fileId = asset.referenceAudio?.clip.fileId;
		const url = asset.referenceAudio?.clip.previewUrl || seedAudioFileUrl(fileId ?? '');
		if (!url) { seedAssetError = '这条参考声音的本地文件已不存在，请替换后再试听。'; return; }
		if (!seedPreviewAudio) seedPreviewAudio = new Audio();
		if (seedPreviewingSlot === slot && !seedPreviewAudio.paused) { seedPreviewAudio.pause(); seedPreviewingSlot = null; return; }
		seedPreviewAudio.src = url;
		seedPreviewingSlot = slot;
		seedPreviewAudio.onended = () => (seedPreviewingSlot = null);
		try { await seedPreviewAudio.play(); } catch (e) { seedPreviewingSlot = null; seedAssetError = `参考声音无法播放：${(e as Error).message || '文件不可访问'}`; }
	}
	async function openSeedAudioEditor(slot: 1 | 2 | 3, asset: SeedAudioReferenceAsset) {
		if (asset.type !== 'audio' || !asset.referenceAudio) return;
		seedEditingSlot = slot;
		const patch = legacyCustomVoicePatchFromDraft(asset.referenceAudio);
		// The slot owns its blob preview URL. The shared editor must not revoke it
		// merely because it switches playback to the managed server file.
		$store = { ...get(store), ...patch, customVoicePreviewUrl: '' };
		await restoreCustomVoiceReference({
			reference_audio_path: patch.customVoiceReferenceAudioPath || `${patch.customVoiceFileId}.wav`,
			custom_reference_source_audio_path: patch.customVoiceSourceAudioPath || `${patch.customVoiceSourceFileId || patch.customVoiceFileId}.wav`,
			custom_reference_source_duration_ms: patch.customVoiceSourceDurationMs,
			custom_reference_trim_start_ms: patch.customVoiceTrimStartMs,
			custom_reference_trim_end_ms: patch.customVoiceTrimEndMs,
			ref_text: patch.customVoiceTranscript
		} as GenerateRequest);
	}
	function syncSeedAudioEditorDraft() {
		if (!seedEditingSlot) return;
		const current = seedAudioState.drafts.audio.references[seedEditingSlot - 1]?.asset;
		if (!current || current.type !== 'audio') return;
		const referenceAudio = referenceAudioDraftFromLegacyState(get(store), current.referenceAudio?.draftId ?? current.assetId);
		updateSeedAudioState(setSeedAudioReference(seedAudioState, seedEditingSlot, { ...current, referenceAudio }));
	}
	async function applyActiveReferenceTrim() { await applyCustomVoiceTrim(); if (isSeedAudio) syncSeedAudioEditorDraft(); }
	function updateActiveReferenceTranscript(text: string) { updateCustomVoiceTranscript(text); if (isSeedAudio) syncSeedAudioEditorDraft(); }
	function closeActiveReferenceEditor() { resetCustomVoice(); seedEditingSlot = null; }
	function referenceAudioFileIdFromPath(path: string | null | undefined) { if (!path) return ''; const filename = path.split('/').pop() ?? ''; return filename.replace(/\.[^.]+$/, ''); }
	function revokeObjectUrlIfNeeded(url: string) { if (url.startsWith('blob:')) URL.revokeObjectURL(url); }
	async function restoreRequest(req: GenerateRequest) {
		resetCustomVoice();
		if (req.engine_id === SEED_AUDIO_ENGINE_ID && req.input_mode) {
			$store.engineId = SEED_AUDIO_ENGINE_ID;
			updateSeedAudioState(seedAudioStateFromRequest(req as unknown as Record<string, unknown>));
			return;
		}
		store.fromRequest(req);
		$store = { ...get(store) };
		if (req.reference_audio_path) await restoreCustomVoiceReference(req);
	}
	function parseVideoLocalizationHandoff(raw: string | null): VideoLocalizationHandoffMeta | null {
		if (!raw) return null;
		try {
			const parsed = JSON.parse(raw) as Partial<VideoLocalizationHandoffMeta>;
			if (parsed.source !== 'video_localization' || !parsed.project_id || !parsed.cue_id) return null;
			return {
				source: 'video_localization',
				mode: parsed.mode === 'tune_with_recipe' ? 'tune_with_recipe' : 'reference_only',
				project_id: parsed.project_id,
				cue_id: parsed.cue_id,
				reference_clip_id: parsed.reference_clip_id ?? null,
				recipe_id: parsed.recipe_id ?? null,
				created_at: parsed.created_at ?? new Date().toISOString()
			};
		} catch {
			return null;
		}
	}
	function videoLocalizationHandoffLabel(meta: VideoLocalizationHandoffMeta) {
		return meta.mode === 'tune_with_recipe' ? '带参数调试' : '仅带样音生成';
	}
	function currentComposerText() { return isSeedAudio ? activeSeedAudioDraft(seedAudioState).prompt : $store.text; }
	function currentPresetParameters() { if (isSeedAudio) return seedAudioStateToRequest(seedAudioState).engine_parameters; const req = store.toRequest(); const params: Record<string, unknown> = { language: req.language, output_format: req.output_format }; for (const k of selected?.manifest.parameter_schema.map(p => p.key) ?? []) { const v = (req as unknown as Record<string, unknown>)[k]; if (v !== undefined && v !== null && v !== '') params[k] = v; } return params; }
	function resetCurrentEngineParams() { const keepText = $store.text; const keepVoiceId = $store.voiceId; const keepShowMore = $store.showMoreParams; store.setEngine($store.engineId); $store.text = keepText; $store.showMoreParams = keepShowMore; if (usesReferenceVoice) $store.voiceId = keepVoiceId; }
	function setSpeedValue(value: string | number) { const n = Number(value); if (!Number.isFinite(n)) return; $store.speed = Math.min(2, Math.max(0.5, Math.round(n * 100) / 100)); }
	function setTaskDateFilter(value: TaskDateFilter) { $store.taskDateFilter = value; $store.currentPage = 1; }
	function resetPresetDraft() { $store.presetDraft = { name: '', scene: '', description: '', tags: '', sample_text: currentComposerText() }; $store.editingPresetId = ''; }
	function presetTooltip(p: PresetTemplate) { return `${p.description || '无说明'}\n示例：${p.sample_text || '未设置'}\n标签：${p.tags.join('、') || '无'}`; }
	function openPresetEditor(p?: PresetTemplate) { if (p) { $store.editingPresetId = p.preset_id; $store.presetDraft = { name: p.name, scene: p.scene, description: p.description, tags: p.tags.join('，'), sample_text: p.sample_text }; } else resetPresetDraft(); $store.showPresetEditor = true; }
	function applyPreset(p: PresetTemplate) { const pp = p.parameters; const m = p.engine_id.startsWith('mimo-v2.5'); const keepVoiceId = p.recommended_voice_type === 'reference_voice' && p.engine_id === $store.engineId && usesReferenceVoice ? $store.voiceId : ''; store.fromRequest({ text: p.sample_text, engine_id: p.engine_id, voice_id: keepVoiceId || null, reference_audio_path: null, ref_text: null, language: String(pp.language ?? 'zh'), emotion_mode: pp.emotion ? 'emotion_vector' : 'follow_reference', emotion: typeof pp.emotion === 'string' ? pp.emotion : null, emotion_values: null, emotion_text: typeof pp.emotion_text === 'string' ? pp.emotion_text : null, style_instruction: typeof pp.style_instruction === 'string' ? pp.style_instruction : null, voice_design_prompt: typeof pp.voice_design_prompt === 'string' ? pp.voice_design_prompt : null, mimo_voice: typeof pp.mimo_voice === 'string' ? pp.mimo_voice : null, speaker_id: typeof pp.speaker_id === 'string' ? pp.speaker_id : null, prompt: typeof pp.prompt === 'string' ? pp.prompt : null, nfe_step: Number(pp.nfe_step ?? 32), cfg_strength: Number(pp.cfg_strength ?? 2.0), target_rms: Number(pp.target_rms ?? 0.1), cross_fade_duration: Number(pp.cross_fade_duration ?? 0.15), sway_sampling_coef: Number(pp.sway_sampling_coef ?? -1.0), fix_duration: Number(pp.fix_duration ?? 0), remove_silence: Boolean(pp.remove_silence ?? false), emo_alpha: Number(pp.emo_alpha ?? 0.6), speed: Number(pp.speed ?? 1.0), pitch_rate: pp.pitch_rate === undefined || pp.pitch_rate === null ? null : Number(pp.pitch_rate), sample_rate: (Number(pp.sample_rate ?? DOUBAO_TTS_DEFAULTS.sampleRate) as GenerateRequest['sample_rate']), bit_rate: Number(pp.bit_rate ?? DOUBAO_TTS_DEFAULTS.bitRate), loudness_rate: Number(pp.loudness_rate ?? 0), enable_subtitle: Boolean(pp.enable_subtitle ?? false), silence_duration: Number(pp.silence_duration ?? 0), aigc_watermark: Boolean(pp.aigc_watermark ?? false), temperature: Number(pp.temperature ?? (m ? 0.6 : 0.8)), top_p: Number(pp.top_p ?? (m ? 0.95 : 0.8)), top_k: Number(pp.top_k ?? 30), repetition_penalty: Number(pp.repetition_penalty ?? 10), seed: pp.seed === undefined || pp.seed === null ? null : Number(pp.seed), max_mel_tokens: Number(pp.max_mel_tokens ?? 1500), max_tokens: Number(pp.max_tokens ?? 1200), cfg_scale: pp.cfg_scale === undefined || pp.cfg_scale === null ? null : Number(pp.cfg_scale), ddpm_steps: pp.ddpm_steps === undefined || pp.ddpm_steps === null ? null : Number(pp.ddpm_steps), max_text_tokens_per_segment: Number(pp.max_text_tokens_per_segment ?? 120), interval_silence: Number(pp.interval_silence ?? 200), diffusion_steps: Number(pp.diffusion_steps ?? (p.engine_id === 'omnivoice' ? 32 : 25)), cfg_rate: Number(pp.cfg_rate ?? 0.7), guidance_scale: Number(pp.guidance_scale ?? 2.0), duration: Number(pp.duration ?? 0), audio_chunk_duration: Number(pp.audio_chunk_duration ?? 15), audio_chunk_threshold: Number(pp.audio_chunk_threshold ?? 30), output_format: (pp.output_format ?? 'wav') as 'wav' | 'mp3' | 'flac', } as GenerateRequest); $store.error = ''; }
	function applyComposerPreset(p: ComposerPreset) {
		if (p.seedBundle) { updateSeedAudioState(applySeedAudioPreset(seedAudioState, p.seedBundle)); return; }
		if (p.engine_id !== SEED_AUDIO_ENGINE_ID) { applyPreset(p); return; }
		if (!p.input_mode) { $store.error = '这个 Seed Audio 预设缺少模式信息，不能应用。'; return; }
		try {
			const restored = seedAudioStateFromRequest({ engine_id: SEED_AUDIO_ENGINE_ID, text: p.sample_text, input_mode: p.input_mode, input_assets: p.input_assets ?? [], engine_parameters: p.parameters });
			updateSeedAudioState({ ...seedAudioState, mode: restored.mode, drafts: { ...seedAudioState.drafts, [restored.mode]: restored.drafts[restored.mode] } } as SeedAudioState);
		} catch (error) { $store.error = (error as Error).message; }
	}
	async function savePreset() { if (!$store.presetDraft.name.trim()) return; $store.presetBusy = true; try { const seedRequest = isSeedAudio ? seedAudioStateToRequest(seedAudioState) : null; const payload = { preset_id: $store.editingPresetId || null, name: $store.presetDraft.name.trim(), scene: $store.presetDraft.scene.trim(), description: $store.presetDraft.description.trim(), engine_id: $store.engineId, input_mode: seedRequest?.input_mode ?? null, input_assets: seedRequest?.input_assets ?? [], sample_text: $store.presetDraft.sample_text.trim() || currentComposerText(), parameters: currentPresetParameters(), source_test_id: null, recommended_voice_type: isSeedAudio ? 'generated_audio' : isOmniVoice && !$store.voiceId ? 'voice_design' : 'reference_voice', tags: $store.presetDraft.tags.split(/[，,]/).map(t => t.trim()).filter(Boolean) }; const saved = $store.editingPresetId ? await Api.updatePreset($store.editingPresetId, payload) : await Api.createPreset(payload); $store.presets = [saved, ...$store.presets.filter(i => i.preset_id !== saved.preset_id)]; $store.showPresetEditor = false; resetPresetDraft(); } catch (e) { $store.error = (e as Error).message; } finally { $store.presetBusy = false; } }
	async function deletePreset(p: PresetTemplate) { if (!p.preset_id.startsWith('custom_')) return; if (!window.confirm(`删除自定义预设「${p.name}」吗？`)) return; $store.presetBusy = true; try { await Api.deletePreset(p.preset_id); $store.presets = $store.presets.filter(i => i.preset_id !== p.preset_id); } catch (e) { $store.error = (e as Error).message; } finally { $store.presetBusy = false; } }
	function formatSrtTime(valueMs: number) { const total = Math.max(0, Math.round(valueMs)); const h = Math.floor(total / 3600000); const m = Math.floor((total % 3600000) / 60000); const s = Math.floor((total % 60000) / 1000); const ms = total % 1000; return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(ms).padStart(3, '0')}`; }
	function segmentsToSrt(segments: TranscriptionSegment[], fallbackText: string, durationMs: number | null) { const usable = segments.filter(s => s.text.trim()); if (usable.length) return usable.map((s, i) => `${i + 1}\n${formatSrtTime(s.start_ms)} --> ${formatSrtTime(s.end_ms)}\n${s.text.trim()}`).join('\n\n') + '\n'; const text = fallbackText.trim(); if (!text) return ''; return `1\n00:00:00,000 --> ${formatSrtTime(durationMs ?? 2000)}\n${text}\n`; }
	function formatDuration(valueMs: number | null) { if (!valueMs) return '未识别'; const seconds = Math.max(0, valueMs / 1000); if (seconds < 60) { const rounded = Math.round(seconds * 10) / 10; return Number.isInteger(rounded) ? `${rounded}s` : `${rounded.toFixed(1)}s`; } const total = Math.round(seconds); const m = Math.floor(total / 60); const s = total % 60; return `${m}:${String(s).padStart(2, '0')}`; }
	function formatTimecode(valueSeconds: number, fps = 30) { const safeSeconds = Math.max(0, Number.isFinite(valueSeconds) ? valueSeconds : 0); const totalFrames = Math.round(safeSeconds * fps); const frames = totalFrames % fps; const totalWholeSeconds = Math.floor(totalFrames / fps); const s = totalWholeSeconds % 60; const m = Math.floor(totalWholeSeconds / 60) % 60; const h = Math.floor(totalWholeSeconds / 3600); return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}:${String(frames).padStart(2, '0')}`; }
	function formatTimelineTick(valueSeconds: number, fps = 30) { if (valueSeconds < 1) return `${Math.round(valueSeconds * fps)}f`; const safe = Math.max(0, Math.round(valueSeconds * 10) / 10); const h = Math.floor(safe / 3600); const m = Math.floor((safe % 3600) / 60); const s = safe % 60; if (h) return `${h}:${String(m).padStart(2, '0')}:${String(Math.floor(s)).padStart(2, '0')}`; if (safe < 10 && !Number.isInteger(safe)) return `${safe.toFixed(1)}s`; return `${m}:${String(Math.floor(s)).padStart(2, '0')}`; }
	function formatTimelineZoom(value: number) { return value < 10 ? value.toFixed(1) : value.toFixed(0); }
	function buildTimelineTicks(durationSeconds: number, zoom: number) { if (!durationSeconds) return []; const normalizedZoom = Math.max(1, zoom); const target = Math.max(8, Math.min(36000, Math.round(28 * normalizedZoom))); const rawStep = durationSeconds / target; const frameStep = 1 / 30; const steps = [frameStep, frameStep * 2, frameStep * 5, frameStep * 10, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200]; const step = steps.find(v => v >= rawStep) ?? steps[steps.length - 1]; const ticks: Array<{ time: number; percent: number; label: string; major: boolean }> = []; const labelEvery = step < 1 ? Math.max(1, Math.round(1 / step)) : step < 5 ? 2 : 1; for (let t = 0; t <= durationSeconds + 0.001; t += step) { const index = Math.round(t / step); ticks.push({ time: t, percent: durationSeconds ? (t / durationSeconds) * 100 : 0, label: index % labelEvery === 0 ? formatTimelineTick(t) : '', major: index % labelEvery === 0 }); } if (ticks[ticks.length - 1]?.time !== durationSeconds) ticks.push({ time: durationSeconds, percent: 100, label: formatTimelineTick(durationSeconds), major: true }); return ticks; }
	function centerCustomVoiceTimelineOnSelection() {
		requestAnimationFrame(() => {
			const windowEl = document.querySelector<HTMLElement>('.custom-voice-timebar-window');
			const timebarEl = document.querySelector<HTMLElement>('.custom-voice-timebar');
			if (!windowEl || !timebarEl || !customVoiceDurationSeconds) return;
			const centerRatio = ((customVoiceTrimStart + customVoiceTrimEnd) / 2) / customVoiceDurationSeconds;
			const target = timebarEl.offsetWidth * centerRatio - windowEl.clientWidth / 2;
			windowEl.scrollLeft = Math.max(0, Math.min(windowEl.scrollWidth - windowEl.clientWidth, target));
		});
	}
	function zoomCustomVoiceTimeline(nextZoom: number, anchorRatio?: number, anchorOffset?: number) {
		if (!Number.isFinite(nextZoom)) return;
		const windowEl = document.querySelector<HTMLElement>('.custom-voice-timebar-window');
		const ratio = anchorRatio ?? (customVoiceDurationSeconds ? ((customVoiceTrimStart + customVoiceTrimEnd) / 2) / customVoiceDurationSeconds : 0.5);
		const offset = anchorOffset ?? ((windowEl?.clientWidth ?? 0) / 2);
		customVoiceTimelineZoom = Math.max(1, Math.min(1200, Math.round(nextZoom * 10) / 10));
		requestAnimationFrame(() => {
			const updatedWindow = document.querySelector<HTMLElement>('.custom-voice-timebar-window');
			if (!updatedWindow) return;
			const target = updatedWindow.scrollWidth * Math.max(0, Math.min(1, ratio)) - offset;
			updatedWindow.scrollLeft = Math.max(0, Math.min(updatedWindow.scrollWidth - updatedWindow.clientWidth, target));
			updateCustomVoiceTimelineViewport(updatedWindow);
		});
	}
	function setCustomVoiceTimelineZoom(value: string | number) { const n = Number(value); if (!Number.isFinite(n)) return; zoomCustomVoiceTimeline(n); }
	function updateCustomVoiceTimelineViewport(element?: HTMLElement | null) { const el = element ?? document.querySelector<HTMLElement>('.custom-voice-timebar-window'); if (!el) return; customVoiceTimelineScrollLeft = el.scrollLeft; customVoiceTimelineViewportWidth = el.clientWidth; }
	function buildVisibleWaveformBars(bars: number[], zoom: number, scrollLeft: number, viewportWidth: number) { if (!bars.length) return []; const total = bars.length; const safeViewport = Math.max(1, viewportWidth || 900); const scrollWidth = Math.max(safeViewport, safeViewport * Math.max(1, zoom)); const visibleStartRatio = Math.max(0, Math.min(1, scrollLeft / scrollWidth)); const visibleEndRatio = Math.max(visibleStartRatio, Math.min(1, (scrollLeft + safeViewport) / scrollWidth)); const padRatio = Math.min(0.02, Math.max(0.001, (visibleEndRatio - visibleStartRatio) * 0.35)); const start = Math.max(0, Math.floor((visibleStartRatio - padRatio) * total)); const end = Math.min(total, Math.ceil((visibleEndRatio + padRatio) * total)); const span = Math.max(1, end - start); const targetBars = Math.max(180, Math.min(2600, Math.round(safeViewport * 1.35))); const bucket = Math.max(1, Math.ceil(span / targetBars)); const result: Array<{ x: number; width: number; level: number }> = []; for (let i = start; i < end; i += bucket) { let peak = 0; const stop = Math.min(end, i + bucket); for (let j = i; j < stop; j++) peak = Math.max(peak, bars[j] ?? 0); result.push({ x: i, width: Math.max(1, (stop - i) * 0.82), level: peak }); } return result; }
	function nextAnimationFrame() { return new Promise<void>(resolve => requestAnimationFrame(() => resolve())); }
	function splitTags(value: string) { return value.split(/[，,]/).map(t => t.trim()).filter(Boolean); }
	function trimFileName(file: File, start: number, end: number) { const stem = file.name.replace(/\.[^.]+$/, '') || 'custom-voice'; return `${stem}_clip_${start.toFixed(1)}-${end.toFixed(1)}s.wav`; }
	function selectedTrimRange() { const duration = customVoiceSourceDurationMs ? customVoiceSourceDurationMs / 1000 : 0; const start = Math.min(customVoiceTrimStart, Math.max(0, customVoiceTrimEnd - 0.1)); const end = Math.max(start + 0.1, Math.min(duration || customVoiceTrimEnd, customVoiceTrimEnd)); return { start, end }; }
	function syncCustomVoiceTrimMetadata() { const { start, end } = selectedTrimRange(); $store.customVoiceSourceDurationMs = customVoiceSourceDurationMs; $store.customVoiceTrimStartMs = Math.round(start * 1000); $store.customVoiceTrimEndMs = Math.round(end * 1000); }
	function clearCustomVoiceProcessedState() { $store.customVoiceFileId = ''; $store.customVoiceReferenceAudioPath = ''; $store.customVoiceTranscript = ''; $store.customVoiceSrt = ''; $store.customVoiceDurationMs = null; $store.customVoiceSrtSegmentCount = 0; $store.customVoiceTranscriptionId = ''; $store.customVoiceConfirmed = false; $store.customVoiceQualityWarnings = []; resetVoiceRegisterDialog(); }
	function setCustomVoicePreviewUrl(url: string) { if ($store.customVoicePreviewUrl && $store.customVoicePreviewUrl !== url && $store.customVoicePreviewUrl !== customVoiceSourcePreviewUrl) revokeObjectUrlIfNeeded($store.customVoicePreviewUrl); $store.customVoicePreviewUrl = url; }
	function invalidateCustomVoiceTrim() { if (!customVoiceOriginalFile) return; if ($store.customVoiceReferenceAudioPath || $store.customVoiceTranscriptionId) customVoiceSelectionDirty = true; clearCustomVoiceProcessedState(); setCustomVoicePreviewUrl(customVoiceSourcePreviewUrl); if (!customVoiceLoopPreview) stopVoicePreview(); customVoicePlaybackPosition = customVoiceTrimStart; }
	function clampCustomVoicePlaybackTime(value: number) { const duration = (customVoiceSourceDurationMs ?? 0) / 1000; return Math.max(0, Math.min(duration || value, value)); }
	function setCustomVoicePlaybackPosition(value: string | number) { const n = Number(value); if (!Number.isFinite(n)) return; customVoicePlaybackPosition = clampCustomVoicePlaybackTime(n); if (customVoiceLoopPreview && $store.voicePreviewAudio) $store.voicePreviewAudio.currentTime = customVoicePlaybackPosition; }
	function setCustomVoiceTrimStart(value: string | number) { const max = customVoiceTrimEnd || (customVoiceSourceDurationMs ?? 0) / 1000; const n = Number(value); if (!Number.isFinite(n)) return; customVoiceTrimStart = Math.max(0, Math.min(n, Math.max(0, max - 0.1))); if (customVoicePlaybackPosition < customVoiceTrimStart) customVoicePlaybackPosition = customVoiceTrimStart; if (customVoiceLoopPreview && $store.voicePreviewAudio) $store.voicePreviewAudio.currentTime = customVoiceTrimStart; syncCustomVoiceTrimMetadata(); invalidateCustomVoiceTrim(); }
	function setCustomVoiceTrimEnd(value: string | number) { const duration = (customVoiceSourceDurationMs ?? 0) / 1000; const n = Number(value); if (!Number.isFinite(n)) return; customVoiceTrimEnd = Math.max(customVoiceTrimStart + 0.1, Math.min(duration || n, n)); if (customVoicePlaybackPosition > customVoiceTrimEnd) customVoicePlaybackPosition = customVoiceTrimEnd; syncCustomVoiceTrimMetadata(); invalidateCustomVoiceTrim(); }
	function setCustomVoiceTrimStartAtPlayhead() { setCustomVoiceTrimStart(customVoicePlaybackPosition); }
	function setCustomVoiceTrimEndAtPlayhead() { setCustomVoiceTrimEnd(customVoicePlaybackPosition); }
	function resetCustomVoiceTrimRange() { const duration = customVoiceDurationSeconds; if (!duration) return; customVoiceTrimStart = 0; customVoiceTrimEnd = Math.max(0.1, duration); customVoicePlaybackPosition = 0; if (customVoiceLoopPreview && $store.voicePreviewAudio) $store.voicePreviewAudio.currentTime = 0; syncCustomVoiceTrimMetadata(); invalidateCustomVoiceTrim(); centerCustomVoiceTimelineOnSelection(); }
	function customVoiceBusyText() { return customVoiceBusyMode === 'source' ? '正在读取参考音频' : '正在处理选区并识别台词'; }
	function customVoiceStatusText() { if ($store.customVoiceBusy) return customVoiceBusyText(); if ($store.customVoiceConfirmed) return '参考音色与台词已匹配'; if (customVoiceSelectionDirty) return '选区已调整，请重新识别'; if ($store.customVoiceTranscript.trim()) return '可编辑台词，已可使用'; if ($store.customVoiceFileName) return '选择范围后使用选区'; return '拖入 wav 或 mp3 音频'; }
	function setVoiceSource(source: 'voice_library' | 'reference_audio') { $store.voiceSource = source; $store.error = ''; stopVoicePreview(); if (source === 'reference_audio') $store.voiceId = ''; }
	function updateCustomVoiceTranscript(value: string) { $store.customVoiceTranscript = value; $store.customVoiceConfirmed = Boolean($store.customVoiceReferenceAudioPath && value.trim()); }
	function resetVoiceRegisterDialog() { voiceRegisterOpen = false; voiceRegisterBusy = false; voiceRegisterSerBusy = false; voiceRegisterError = ''; }
	function resetCustomVoice() { if ($store.customVoicePreviewUrl) revokeObjectUrlIfNeeded($store.customVoicePreviewUrl); if (customVoiceSourcePreviewUrl && customVoiceSourcePreviewUrl !== $store.customVoicePreviewUrl) revokeObjectUrlIfNeeded(customVoiceSourcePreviewUrl); customVoiceOriginalFile = null; customVoiceSourcePreviewUrl = ''; customVoiceSourceDurationMs = null; customVoiceTrimStart = 0; customVoiceTrimEnd = 0; customVoicePlaybackPosition = 0; customVoiceWaveformBars = []; customVoiceWaveformLoading = false; customVoiceWaveformProgress = 0; customVoiceTimelineScrollLeft = 0; customVoiceTimelineViewportWidth = 0; customVoiceTimelineZoom = 1; customVoiceSelectionDirty = false; customVoiceBusyMode = ''; $store.customVoiceFileName = ''; $store.customVoiceFileId = ''; $store.customVoicePreviewUrl = ''; $store.customVoiceReferenceAudioPath = ''; $store.customVoiceSourceFileId = ''; $store.customVoiceSourceAudioPath = ''; $store.customVoiceSourceDurationMs = null; $store.customVoiceTrimStartMs = null; $store.customVoiceTrimEndMs = null; $store.customVoiceTranscript = ''; $store.customVoiceSrt = ''; $store.customVoiceDurationMs = null; $store.customVoiceSrtSegmentCount = 0; $store.customVoiceTranscriptionId = ''; $store.customVoiceConfirmed = false; $store.customVoiceBusy = false; $store.customVoiceError = ''; $store.customVoiceQualityWarnings = []; resetVoiceRegisterDialog(); stopVoicePreview(); }
	function loadAudioDuration(url: string): Promise<number> { return new Promise((resolve, reject) => { const audio = new Audio(); audio.preload = 'metadata'; audio.onloadedmetadata = () => { resolve(Number.isFinite(audio.duration) ? audio.duration : 0); audio.src = ''; }; audio.onerror = () => reject(new Error('无法读取音频时长')); audio.src = url; }); }
	function encodeWav(buffer: AudioBuffer) { const channelCount = buffer.numberOfChannels; const sampleRate = buffer.sampleRate; const frameCount = buffer.length; const bytesPerSample = 2; const blockAlign = channelCount * bytesPerSample; const dataSize = frameCount * blockAlign; const arrayBuffer = new ArrayBuffer(44 + dataSize); const view = new DataView(arrayBuffer); const writeString = (offset: number, value: string) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); }; writeString(0, 'RIFF'); view.setUint32(4, 36 + dataSize, true); writeString(8, 'WAVE'); writeString(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, channelCount, true); view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * blockAlign, true); view.setUint16(32, blockAlign, true); view.setUint16(34, 16, true); writeString(36, 'data'); view.setUint32(40, dataSize, true); let offset = 44; for (let i = 0; i < frameCount; i++) { for (let channel = 0; channel < channelCount; channel++) { const sample = Math.max(-1, Math.min(1, buffer.getChannelData(channel)[i] ?? 0)); view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true); offset += 2; } } return new Blob([arrayBuffer], { type: 'audio/wav' }); }
	async function buildWaveformBars(file: File, onProgress?: (bars: number[], progress: number) => void, count = 2400) { const audioContext = new AudioContext(); try { onProgress?.([], 0.04); const decoded = await audioContext.decodeAudioData(await file.arrayBuffer()); const channel = decoded.getChannelData(0); const dynamicCount = Math.max(count, Math.min(180000, Math.ceil(decoded.duration * 60), Math.round(decoded.length / 2048))); const bucketSize = Math.max(1, Math.floor(channel.length / dynamicCount)); const rawBars = new Array<number>(dynamicCount).fill(0); let maxPeak = 0.01; const chunkSize = Math.max(360, Math.min(1800, Math.ceil(dynamicCount / 80))); for (let i = 0; i < dynamicCount; i++) { const start = i * bucketSize; const end = Math.min(channel.length, start + bucketSize); let peak = 0; for (let j = start; j < end; j++) peak = Math.max(peak, Math.abs(channel[j] ?? 0)); rawBars[i] = peak; maxPeak = Math.max(maxPeak, peak); if (i % chunkSize === 0 || i === dynamicCount - 1) { const upto = i + 1; const normalized = rawBars.slice(0, upto).map(v => Math.max(0.1, Math.min(1, Math.pow(v / maxPeak, 0.72)))); onProgress?.(normalized, Math.max(0.08, Math.min(0.98, upto / dynamicCount))); await nextAnimationFrame(); } } const finalMax = Math.max(...rawBars, 0.01); const finalBars = rawBars.map(v => Math.max(0.1, Math.min(1, Math.pow(v / finalMax, 0.72)))); onProgress?.(finalBars, 1); return finalBars; } finally { void audioContext.close(); } }
	async function cropAudioFile(file: File, start: number, end: number) { const audioContext = new AudioContext(); try { const decoded = await audioContext.decodeAudioData(await file.arrayBuffer()); const sampleRate = decoded.sampleRate; const startFrame = Math.max(0, Math.floor(start * sampleRate)); const endFrame = Math.min(decoded.length, Math.ceil(end * sampleRate)); const frameCount = Math.max(1, endFrame - startFrame); const clip = audioContext.createBuffer(decoded.numberOfChannels, frameCount, sampleRate); for (let channel = 0; channel < decoded.numberOfChannels; channel++) clip.copyToChannel(decoded.getChannelData(channel).slice(startFrame, endFrame), channel); return encodeWav(clip); } finally { void audioContext.close(); } }
	function applyCustomVoiceClipResult(uploaded: Pick<UploadResult, 'file_id' | 'filename' | 'path' | 'quality'>, transcript: TranscriptionRecord, durationMs: number, previewUrl: string) {
		setCustomVoicePreviewUrl(previewUrl);
		$store.customVoiceFileName = uploaded.filename;
		$store.customVoiceFileId = uploaded.file_id;
		$store.customVoiceReferenceAudioPath = uploaded.path;
		$store.customVoiceQualityWarnings = uploaded.quality.warnings ?? [];
		$store.customVoiceTranscript = transcript.text.trim();
		$store.customVoiceSrt = segmentsToSrt(transcript.segments, transcript.text, transcript.duration_ms ?? durationMs);
		$store.customVoiceDurationMs = transcript.duration_ms ?? durationMs;
		$store.customVoiceSrtSegmentCount = transcript.segments.filter(s => s.text.trim()).length || ($store.customVoiceTranscript ? 1 : 0);
		$store.customVoiceTranscriptionId = transcript.transcription_id;
		$store.customVoiceConfirmed = Boolean($store.customVoiceTranscript);
		customVoiceSelectionDirty = false;
	}
	async function processCustomVoiceClip(file: File, previewUrl: string, durationMs: number) {
		setCustomVoicePreviewUrl(previewUrl);
		$store.customVoiceFileName = file.name;
		$store.customVoiceBusy = true;
		customVoiceBusyMode = 'clip';
		$store.customVoiceError = '';
		syncCustomVoiceTrimMetadata();
		clearCustomVoiceProcessedState();
		stopVoicePreview();
		try {
			const [uploaded, transcript] = await Promise.all([Api.uploadVoice(file), Api.transcribeAudio(file, 'auto', 'qwen3-asr-mlx')]);
			applyCustomVoiceClipResult(uploaded, transcript, durationMs, previewUrl);
		} catch (e) {
			$store.customVoiceError = (e as Error).message || '自定义音色上传或识别失败';
		} finally {
			$store.customVoiceBusy = false;
			customVoiceBusyMode = '';
		}
	}
	async function processCustomVoiceClipOnBackend(sourceFileId: string, startMs: number, endMs: number, durationMs: number) {
		const result: VoiceClipTranscribeResponse = await Api.clipTranscribeVoice(sourceFileId, {
			start_ms: startMs,
			end_ms: endMs,
			language: 'auto',
			engine_id: 'qwen3-asr-mlx'
		});
		applyCustomVoiceClipResult(result, result.transcription, durationMs, `/api/voices/files/${encodeURIComponent(result.file_id)}/audio`);
	}
	async function applyCustomVoiceTrim() {
		const sourceFileId = $store.customVoiceSourceFileId;
		if (!customVoiceOriginalFile && !sourceFileId) {
			$store.customVoiceError = '请先拖入音频文件。';
			return;
		}
		const { start, end } = selectedTrimRange();
		const startMs = Math.round(start * 1000);
		const endMs = Math.round(end * 1000);
		const durationMs = Math.max(100, endMs - startMs);
		$store.customVoiceBusy = true;
		customVoiceBusyMode = 'clip';
		$store.customVoiceError = '';
		syncCustomVoiceTrimMetadata();
		clearCustomVoiceProcessedState();
		stopVoicePreview();
		try {
			if (sourceFileId) {
				await processCustomVoiceClipOnBackend(sourceFileId, startMs, endMs, durationMs);
				return;
			}
			if (!customVoiceOriginalFile) throw new Error('原始参考音频不可用');
			const fullDuration = (customVoiceSourceDurationMs ?? 0) / 1000;
			const useOriginal = start <= 0.01 && (!fullDuration || end >= fullDuration - 0.01);
			const blob = useOriginal ? customVoiceOriginalFile : await cropAudioFile(customVoiceOriginalFile, start, end);
			const clipFile = useOriginal ? customVoiceOriginalFile : new File([blob], trimFileName(customVoiceOriginalFile, start, end), { type: 'audio/wav' });
			await processCustomVoiceClip(clipFile, URL.createObjectURL(clipFile), durationMs);
		} catch (e) {
			if (!customVoiceOriginalFile) {
				$store.customVoiceError = (e as Error).message || '音频裁切失败';
				return;
			}
			try {
				const blob = await cropAudioFile(customVoiceOriginalFile, start, end);
				const clipFile = new File([blob], trimFileName(customVoiceOriginalFile, start, end), { type: 'audio/wav' });
				await processCustomVoiceClip(clipFile, URL.createObjectURL(clipFile), durationMs);
			} catch (fallbackError) {
				$store.customVoiceError = (fallbackError as Error).message || (e as Error).message || '音频裁切失败';
			}
		} finally {
			$store.customVoiceBusy = false;
			customVoiceBusyMode = '';
		}
	}
	async function handleCustomVoiceFile(file: File) { if (!file.type.startsWith('audio/') && !/\.(wav|mp3|m4a|flac|aac|ogg)$/i.test(file.name)) { $store.customVoiceError = '请拖入音频文件。'; return; } resetCustomVoice(); $store.voiceSource = 'reference_audio'; $store.voiceId = ''; customVoiceOriginalFile = file; customVoiceSourcePreviewUrl = URL.createObjectURL(file); setCustomVoicePreviewUrl(customVoiceSourcePreviewUrl); $store.customVoiceFileName = file.name; $store.customVoiceError = ''; $store.customVoiceBusy = true; customVoiceBusyMode = 'source'; customVoiceWaveformLoading = true; customVoiceWaveformProgress = 0; const waveformTarget = file; const waveformPromise = buildWaveformBars(file, (bars, progress) => { if (customVoiceOriginalFile !== waveformTarget) return; customVoiceWaveformBars = bars; customVoiceWaveformProgress = progress; }).catch(() => [] as number[]); const sourceUploadPromise = Api.uploadVoice(file); try { const [duration, sourceUpload] = await Promise.all([loadAudioDuration(customVoiceSourcePreviewUrl), sourceUploadPromise]); customVoiceSourceDurationMs = Math.round(duration * 1000); customVoiceTrimStart = 0; customVoicePlaybackPosition = 0; customVoiceTrimEnd = Math.max(0.1, duration); $store.customVoiceSourceFileId = sourceUpload.file_id; $store.customVoiceSourceAudioPath = sourceUpload.path; syncCustomVoiceTrimMetadata(); centerCustomVoiceTimelineOnSelection(); } catch (e) { customVoiceSourceDurationMs = null; customVoiceTrimStart = 0; customVoiceTrimEnd = 0; customVoicePlaybackPosition = 0; $store.customVoiceSourceFileId = ''; $store.customVoiceSourceAudioPath = ''; $store.customVoiceSourceDurationMs = null; $store.customVoiceTrimStartMs = null; $store.customVoiceTrimEndMs = null; $store.customVoiceError = (e as Error).message || '无法读取或保存原始音频'; } finally { const bars = await waveformPromise; if (customVoiceOriginalFile === waveformTarget) { customVoiceWaveformBars = bars; customVoiceWaveformProgress = bars.length ? 1 : 0; customVoiceWaveformLoading = false; } $store.customVoiceBusy = false; customVoiceBusyMode = ''; } }
	async function restoreCustomVoiceReference(req: GenerateRequest) {
		const referencePath = req.reference_audio_path ?? '';
		const fileId = referenceAudioFileIdFromPath(referencePath);
		if (!fileId) {
			$store.customVoiceError = '无法从历史任务恢复参考音频：缺少文件 ID。';
			return;
		}
		const sourcePath = req.custom_reference_source_audio_path || referencePath;
		const sourceFileId = referenceAudioFileIdFromPath(sourcePath);
		const referenceFileName = referencePath.split('/').pop() || '自定义参考音频.wav';
		const sourceFileName = sourcePath.split('/').pop() || referenceFileName;
		const referenceAudioUrl = `/api/voices/files/${encodeURIComponent(fileId)}/audio`;
		const sourceAudioUrl = sourceFileId ? `/api/voices/files/${encodeURIComponent(sourceFileId)}/audio` : referenceAudioUrl;
		const hasOriginalSource = Boolean(req.custom_reference_source_audio_path);
		customVoiceOriginalFile = null;
		customVoiceSourcePreviewUrl = sourceAudioUrl;
		setCustomVoicePreviewUrl(referenceAudioUrl);
		$store.customVoiceFileId = fileId;
		$store.customVoiceFileName = sourceFileName;
		$store.customVoiceReferenceAudioPath = referencePath;
		$store.customVoiceSourceFileId = sourceFileId;
		$store.customVoiceSourceAudioPath = sourcePath;
		$store.customVoiceSourceDurationMs = req.custom_reference_source_duration_ms ?? null;
		$store.customVoiceTrimStartMs = req.custom_reference_trim_start_ms ?? null;
		$store.customVoiceTrimEndMs = req.custom_reference_trim_end_ms ?? null;
		$store.customVoiceTranscript = req.ref_text ?? '';
		$store.customVoiceConfirmed = Boolean(referencePath && (req.ref_text ?? '').trim());
		$store.customVoiceQualityWarnings = [];
		$store.customVoiceError = '';
		customVoiceSelectionDirty = false;
		customVoiceTimelineZoom = 1;
		customVoiceTimelineScrollLeft = 0;
		customVoiceTimelineViewportWidth = 0;
		customVoiceWaveformBars = [];
		customVoiceWaveformLoading = true;
		customVoiceWaveformProgress = 0;
		try {
			const referenceCheck = sourcePath === referencePath ? null : fetch(referenceAudioUrl);
			const response = await fetch(sourceAudioUrl);
			if (!response.ok) {
				const missing = hasOriginalSource ? '原始导入音频不存在或已被清理' : '参考音频文件不存在或已被清理';
				throw new Error(response.status === 404 ? missing : `HTTP ${response.status}`);
			}
			if (referenceCheck) {
				const referenceResponse = await referenceCheck;
				if (!referenceResponse.ok) throw new Error(referenceResponse.status === 404 ? '切分后的参考音频不存在或已被清理' : `HTTP ${referenceResponse.status}`);
			}
			const blob = await response.blob();
			const file = new File([blob], sourceFileName, { type: blob.type || 'audio/wav' });
			customVoiceOriginalFile = file;
			const waveformPromise = buildWaveformBars(file, (bars, progress) => {
				if (customVoiceOriginalFile !== file) return;
				customVoiceWaveformBars = bars;
				customVoiceWaveformProgress = progress;
			}).catch(() => [] as number[]);
			const duration = await loadAudioDuration(sourceAudioUrl);
			customVoiceSourceDurationMs = Math.round(duration * 1000);
			const requestedStart = (req.custom_reference_trim_start_ms ?? 0) / 1000;
			const requestedEnd = (req.custom_reference_trim_end_ms ?? Math.round(duration * 1000)) / 1000;
			customVoiceTrimStart = Math.max(0, Math.min(duration, requestedStart));
			customVoiceTrimEnd = Math.max(customVoiceTrimStart + 0.1, Math.min(duration, requestedEnd || duration));
			customVoicePlaybackPosition = customVoiceTrimStart;
			$store.customVoiceSourceDurationMs = customVoiceSourceDurationMs;
			$store.customVoiceDurationMs = Math.max(100, Math.round((customVoiceTrimEnd - customVoiceTrimStart) * 1000));
			syncCustomVoiceTrimMetadata();
			centerCustomVoiceTimelineOnSelection();
			const bars = await waveformPromise;
			if (customVoiceOriginalFile === file) {
				customVoiceWaveformBars = bars;
				customVoiceWaveformProgress = bars.length ? 1 : 0;
			}
		} catch (e) {
			customVoiceOriginalFile = null;
			customVoiceSourcePreviewUrl = '';
			customVoiceSourceDurationMs = null;
			customVoiceTrimStart = 0;
			customVoiceTrimEnd = 0;
			customVoicePlaybackPosition = 0;
			customVoiceWaveformBars = [];
			customVoiceWaveformProgress = 0;
			$store.customVoicePreviewUrl = '';
			$store.customVoiceDurationMs = null;
			$store.customVoiceSourceDurationMs = null;
			$store.customVoiceConfirmed = false;
			$store.customVoiceError = `历史参考音频无法恢复，请确认文件仍存在：${(e as Error).message || '读取失败'}`;
		} finally {
			customVoiceWaveformLoading = false;
		}
	}
	function emotionTagsFromScores(top: string | null, scores: Record<string, number>) { const tags = top ? [top] : []; for (const [emotion, score] of Object.entries(scores)) { if (emotion !== top && score > 0.15) tags.push(emotion); } return tags.slice(0, 3); }
	async function openVoiceRegisterDialog() { if (!$store.customVoiceFileId || !$store.customVoiceTranscript.trim()) { $store.customVoiceError = '请先完成自定义音色上传和台词识别。'; return; } voiceRegisterName = ($store.customVoiceFileName || '自定义音色').replace(/\.[^.]+$/, ''); voiceRegisterDescription = `由生成页自定义音色注册，参考音频：${$store.customVoiceFileName || '未命名音频'}`; voiceRegisterTags = '自定义音色, ASR已生成'; voiceRegisterEmotionTags = $store.emotion ? $store.emotion : ''; voiceRegisterReferenceText = $store.customVoiceTranscript.trim(); voiceRegisterLicense = 'self_voice'; voiceRegisterEngine = $store.engineId; voiceRegisterError = ''; voiceRegisterOpen = true; voiceRegisterSerBusy = true; try { const result = await Api.predictEmotionForFile($store.customVoiceFileId); const emotionTags = emotionTagsFromScores(result.top_emotion, result.emotion_scores); if (emotionTags.length) voiceRegisterEmotionTags = emotionTags.join(', '); } catch (e) { voiceRegisterError = `情绪识别未完成，可先保存后在音色库重试：${(e as Error).message || 'SER 失败'}`; } finally { voiceRegisterSerBusy = false; } }
	async function saveRegisteredVoice() { if (!$store.customVoiceFileId || !voiceRegisterName.trim()) return; voiceRegisterBusy = true; voiceRegisterError = ''; try { const payload: VoiceAssetCreate = { name: voiceRegisterName.trim(), description: voiceRegisterDescription.trim(), tags: splitTags(voiceRegisterTags), reference_text: voiceRegisterReferenceText.trim(), reference_audio_ids: [$store.customVoiceFileId], license_status: voiceRegisterLicense, recommended_engine_id: voiceRegisterEngine || null, default_language: $store.language === 'en' ? 'en' : 'zh', voice_type: 'test_sample' }; let created = await Api.createVoice(payload); const emotionTags = splitTags(voiceRegisterEmotionTags); if (emotionTags.length) created = await Api.updateVoice(created.voice_id, { emotion_tags: emotionTags }); $store.voices = [created, ...$store.voices.filter(v => v.voice_id !== created.voice_id)]; $store.voiceSource = 'voice_library'; $store.voiceId = created.voice_id; voiceRegisterOpen = false; } catch (e) { voiceRegisterError = (e as Error).message || '注册音色失败'; } finally { voiceRegisterBusy = false; } }
	function onCustomVoiceDrop(event: DragEvent) { event.preventDefault(); customVoiceDragActive = false; const file = event.dataTransfer?.files?.[0]; if (file) void handleCustomVoiceFile(file); }
	function customVoiceTimeFromPointer(event: PointerEvent, timebar: HTMLElement) { const rect = timebar.getBoundingClientRect(); const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)); return Math.round(ratio * customVoiceDurationSeconds * 10) / 10; }
	function handleCustomVoiceTimebarPointer(event: PointerEvent) { if (!customVoiceDurationSeconds) return; if ((event.target as HTMLElement).closest('button,input')) return; setCustomVoicePlaybackPosition(customVoiceTimeFromPointer(event, event.currentTarget as HTMLElement)); }
	function handleCustomVoiceTimelineWheel(event: WheelEvent) {
		if (!customVoiceDurationSeconds) return;
		event.preventDefault();
		const windowEl = event.currentTarget as HTMLElement;
		const rect = windowEl.getBoundingClientRect();
		const anchorOffset = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
		const anchorRatio = (windowEl.scrollLeft + anchorOffset) / Math.max(1, windowEl.scrollWidth);
		const factor = event.deltaY < 0 ? 1.16 : 1 / 1.16;
		zoomCustomVoiceTimeline(customVoiceTimelineZoom * factor, anchorRatio, anchorOffset);
	}
	function beginCustomVoiceTrimDrag(event: PointerEvent, boundary: 'start' | 'end') {
		if (!customVoiceDurationSeconds) return;
		const timebar = (event.currentTarget as HTMLElement).closest('.custom-voice-timebar') as HTMLElement | null;
		if (!timebar) return;
		event.preventDefault();
		event.stopPropagation();
		const apply = (e: PointerEvent) => {
			const time = customVoiceTimeFromPointer(e, timebar);
			if (boundary === 'start') setCustomVoiceTrimStart(time);
			else setCustomVoiceTrimEnd(time);
		};
		const cleanup = () => {
			window.removeEventListener('pointermove', apply);
			window.removeEventListener('pointerup', cleanup);
			window.removeEventListener('pointercancel', cleanup);
		};
		apply(event);
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', cleanup, { once: true });
		window.addEventListener('pointercancel', cleanup, { once: true });
	}
	function beginCustomVoicePlayheadDrag(event: PointerEvent) {
		if (!customVoiceDurationSeconds) return;
		const timebar = (event.currentTarget as HTMLElement).closest('.custom-voice-timebar') as HTMLElement | null;
		if (!timebar) return;
		event.preventDefault();
		event.stopPropagation();
		const apply = (e: PointerEvent) => setCustomVoicePlaybackPosition(customVoiceTimeFromPointer(e, timebar));
		const cleanup = () => {
			window.removeEventListener('pointermove', apply);
			window.removeEventListener('pointerup', cleanup);
			window.removeEventListener('pointercancel', cleanup);
		};
		apply(event);
		window.addEventListener('pointermove', apply);
		window.addEventListener('pointerup', cleanup, { once: true });
		window.addEventListener('pointercancel', cleanup, { once: true });
	}
	function isTypingTarget(target: EventTarget | null) {
		const el = target instanceof HTMLElement ? target : null;
		if (!el) return false;
		const tag = el.tagName.toLowerCase();
		return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
	}
	function handleCustomVoiceTrimFocusOut(event: FocusEvent) {
		const current = event.currentTarget as HTMLElement;
		customVoiceTrimFocusWithin = Boolean(event.relatedTarget && current.contains(event.relatedTarget as Node));
	}
	function handleCustomVoiceTrimKeydown(event: KeyboardEvent) {
		if (!customVoiceTrimHotkeysActive || !customVoiceOriginalFile || !customVoiceSourceDurationMs) return;
		if (isTypingTarget(event.target)) return;
		const key = event.key.toLowerCase();
		const isSpace = event.code === 'Space' || event.key === ' ';
		const isZoomInKey = !event.metaKey && !event.ctrlKey && !event.altKey && (event.key === '+' || event.key === '=' || event.code === 'Equal');
		const isZoomOutKey = !event.metaKey && !event.ctrlKey && !event.altKey && (event.key === '-' || event.code === 'Minus');
		if (isSpace) {
			if ((event.target as HTMLElement | null)?.closest('button,a')) return;
			event.preventDefault();
			if (!event.repeat && customVoiceSelectedDurationMs >= 100) void toggleCustomVoiceSelectionPreview();
			return;
		}
		if (isZoomInKey) {
			event.preventDefault();
			zoomCustomVoiceTimeline(customVoiceTimelineZoom * 1.35);
			return;
		}
		if (isZoomOutKey) {
			event.preventDefault();
			zoomCustomVoiceTimeline(customVoiceTimelineZoom / 1.35);
			return;
		}
		if (event.repeat) return;
		if (key === 'i') {
			event.preventDefault();
			setCustomVoiceTrimStartAtPlayhead();
		} else if (key === 'o') {
			event.preventDefault();
			setCustomVoiceTrimEndAtPlayhead();
		}
	}
	async function toggleCustomVoiceSelectionPreview() {
		if (!$store.voicePreviewAudio || !customVoiceSourcePreviewUrl || !customVoiceSourceDurationMs) return;
		const audio = $store.voicePreviewAudio;
		if (customVoiceLoopPreview && $store.voicePreviewPlaying) {
			stopCustomVoiceSelectionPreview();
			return;
		}
		const { start } = selectedTrimRange();
		const absoluteUrl = new URL(customVoiceSourcePreviewUrl, window.location.href).href;
		if (audio.src !== absoluteUrl) audio.src = customVoiceSourcePreviewUrl;
		audio.currentTime = start;
		customVoicePlaybackPosition = start;
		customVoiceLoopPreview = true;
		$store.voicePreviewPlaying = true;
		try {
			await audio.play();
			startCustomVoicePreviewFrameLoop();
		} catch (e) {
			stopCustomVoicePreviewFrameLoop();
			customVoiceLoopPreview = false;
			$store.voicePreviewPlaying = false;
			$store.error = `选区试听失败：${(e as Error).message || '请确认音频文件可播放'}`;
		}
	}
	function stopCustomVoiceSelectionPreview() {
		stopCustomVoicePreviewFrameLoop();
		const audio = $store.voicePreviewAudio;
		const { start } = selectedTrimRange();
		if (audio) {
			audio.pause();
			audio.currentTime = start;
		}
		customVoicePlaybackPosition = start;
		customVoiceLoopPreview = false;
		$store.voicePreviewPlaying = false;
	}
	async function previewSelectedVoice() { if (!$store.voicePreviewAudio || !activeVoicePreviewUrl) return; const audio = $store.voicePreviewAudio; if ($store.voicePreviewPlaying && !audio.paused) { stopCustomVoicePreviewFrameLoop(); audio.pause(); customVoiceLoopPreview = false; $store.voicePreviewPlaying = false; return; } stopCustomVoicePreviewFrameLoop(); customVoiceLoopPreview = false; const absoluteUrl = new URL(activeVoicePreviewUrl, window.location.href).href; if (audio.src !== absoluteUrl) { audio.src = activeVoicePreviewUrl; audio.currentTime = 0; } $store.error = ''; $store.voicePreviewPlaying = true; try { await audio.play(); } catch (e) { $store.voicePreviewPlaying = false; $store.error = `音色预览播放失败：${(e as Error).message || '请确认参考音频文件可访问'}`; } }
	function syncCustomVoicePreviewPlayback() {
		const audio = $store.voicePreviewAudio;
		if (!audio || !customVoiceLoopPreview) return;
		const { start, end } = selectedTrimRange();
		customVoicePlaybackPosition = Math.max(start, Math.min(end, audio.currentTime));
		if (audio.currentTime >= end || audio.currentTime < start) {
			if (customVoiceLoopEnabled) {
				audio.currentTime = start;
				customVoicePlaybackPosition = start;
				void audio.play().catch(() => {
					stopCustomVoicePreviewFrameLoop();
					customVoiceLoopPreview = false;
					$store.voicePreviewPlaying = false;
				});
			} else stopCustomVoiceSelectionPreview();
		}
	}
	function stopCustomVoicePreviewFrameLoop() { if (customVoicePreviewFrame !== null) cancelAnimationFrame(customVoicePreviewFrame); customVoicePreviewFrame = null; }
	function startCustomVoicePreviewFrameLoop() {
		if (customVoicePreviewFrame !== null) return;
		const step = () => {
			customVoicePreviewFrame = null;
			const audio = $store.voicePreviewAudio;
			if (!audio || !customVoiceLoopPreview || !$store.voicePreviewPlaying) return;
			syncCustomVoicePreviewPlayback();
			if (customVoiceLoopPreview && $store.voicePreviewPlaying && !audio.paused) customVoicePreviewFrame = requestAnimationFrame(step);
		};
		customVoicePreviewFrame = requestAnimationFrame(step);
	}
	function handleVoicePreviewTimeUpdate() { syncCustomVoicePreviewPlayback(); }
	function handleVoicePreviewPause() { stopCustomVoicePreviewFrameLoop(); if ($store.voicePreviewAudio?.ended || !$store.voicePreviewAudio?.currentTime) { customVoiceLoopPreview = false; $store.voicePreviewPlaying = false; } }
	function stopVoicePreview() { stopCustomVoicePreviewFrameLoop(); if ($store.voicePreviewAudio && $store.voicePreviewPlaying) { $store.voicePreviewAudio.pause(); } customVoiceLoopPreview = false; $store.voicePreviewPlaying = false; }
	function upsertTask(t: GenerationTask) { $store.tasks = [t, ...$store.tasks.filter(i => i.task_id !== t.task_id)]; }
	async function poll(taskId: string) { for (let i = 0; i < 900; i++) { const t = await Api.task(taskId); upsertTask(t); if (['success', 'failed', 'cancelled'].includes(t.status)) return; await new Promise(r => setTimeout(r, 1000)); } }
	async function refreshComposerData() {
		if (composerDataPromise) return composerDataPromise;
		composerDataPromise = (async () => {
			const [e, v, p, st] = await Promise.all([Api.engines(), Api.voices({ offset: 0, limit: 2000 }), Api.presets(), Api.settings()]);
			$store.engines = e; $store.voices = v; $store.presets = p; $store.settings = st;
			const params = new URLSearchParams(location.search); const vId = params.get('voice'); const requestedEngineId = params.get('engine'); const requestedSpeakerId = params.get('speaker_id');
			const reuseRaw = sessionStorage.getItem('voice-studio-history-reuse');
			const handoffRaw = sessionStorage.getItem('voice-studio-video-localization-handoff');
			if (!$store.initialized) {
				const requestedEngine = e.find(en => en.manifest.engine_id === requestedEngineId && !en.manifest.capabilities.includes('speech_recognition'));
				const def = requestedEngine ?? e.find(en => en.manifest.engine_id === st.default_engine_id && !en.manifest.capabilities.includes('speech_recognition'));
				store.setEngine(def?.manifest.engine_id || $store.engineId); $store.voiceId = vId || st.default_voice_id || ''; $store.speakerId = requestedSpeakerId || $store.speakerId; $store.language = st.default_language || $store.language; $store.showSplitPreview = params.get('tools') === 'text';
				$store.initialized = true;
			} else {
				if (requestedEngineId && e.some(en => en.manifest.engine_id === requestedEngineId && !en.manifest.capabilities.includes('speech_recognition'))) store.setEngine(requestedEngineId);
				if (vId) $store.voiceId = vId;
				if (requestedSpeakerId) $store.speakerId = requestedSpeakerId;
			}
			if (reuseRaw) {
				videoLocalizationHandoff = parseVideoLocalizationHandoff(handoffRaw);
				try {
					await restoreRequest(JSON.parse(reuseRaw) as GenerateRequest);
					$store.lastGeneratePlan = null;
					$store.textSegments = [];
					$store.showSplitPreview = false;
				} catch {
					/* ignore stale history reuse payload */
				} finally {
					sessionStorage.removeItem('voice-studio-history-reuse');
					sessionStorage.removeItem('voice-studio-video-localization-handoff');
				}
			}
		})().finally(() => {
			composerDataPromise = null;
		});
		return composerDataPromise;
	}
	let taskPageRequestId = 0;
	async function loadTaskPage(params: TaskPageParams = taskPageParams) {
		const requestId = ++taskPageRequestId;
		recordsRefreshing = true;
		try {
			const response = await Api.taskPage({ ...params });
			if (requestId !== taskPageRequestId) return;
			$store.tasks = response.items;
			taskTotal = response.total;
			taskSummary = response.summary;
			taskDownloadSequences = response.download_sequences;
			recordsInitialized = true;
			recordsLastSyncedAt = new Date().toISOString();
		} finally {
			if (requestId === taskPageRequestId) recordsRefreshing = false;
		}
	}
	async function loadLongformTasks() {
		$store.longformTasks = await Api.longformTasks({ includeCompleted: false, limit: 20 });
	}
	async function refreshRecordsData() {
		if (recordsDataPromise) return recordsDataPromise;
		recordsDataPromise = Promise.all([loadTaskPage(), loadLongformTasks()]).then(() => undefined).finally(() => {
			recordsDataPromise = null;
		});
		return recordsDataPromise;
	}
	function scheduleTaskPageRefresh(delay = 120) {
		if (taskPageTimer) clearTimeout(taskPageTimer);
		taskPageTimer = setTimeout(() => {
			taskPageTimer = null;
			void loadTaskPage().catch(() => undefined);
		}, delay);
	}
	function connectTaskSocket() {
		if (taskSocketClosed || taskSocket?.readyState === WebSocket.OPEN || taskSocket?.readyState === WebSocket.CONNECTING) return;
		const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
		taskSocket = new WebSocket(`${protocol}//${location.host}/api/tasks/ws`);
		taskSocket.onmessage = (event) => {
			try {
				const task = JSON.parse(String(event.data)) as GenerationTask;
				if ($store.tasks.some(item => item.task_id === task.task_id)) upsertTask(task);
				scheduleTaskPageRefresh();
				if (task.longform_task_id) void loadLongformTasks().catch(() => undefined);
			} catch {
				// Ignore malformed event payloads and let the fallback refresh recover.
			}
		};
		taskSocket.onclose = () => {
			taskSocket = null;
			if (taskSocketClosed) return;
			if (taskSocketReconnectTimer) clearTimeout(taskSocketReconnectTimer);
			taskSocketReconnectTimer = setTimeout(connectTaskSocket, 1500);
		};
		taskSocket.onerror = () => taskSocket?.close();
	}
	async function loadSpeakerCatalog(engine: string, query: string, gender: 'all' | 'F' | 'M') { if (!['emotivoice', 'doubao-tts-preset'].includes(engine)) { $store.speakerCatalog = []; return; } const catalogQuery = engine === 'doubao-tts-preset' ? '' : query; const catalogGender = engine === 'doubao-tts-preset' ? 'all' : gender; const key = `${engine}|${catalogQuery}|${catalogGender}`; $store.speakerCatalogKey = key; $store.speakerCatalogLoading = true; try { const items = await Api.engineSpeakers(engine, { q: catalogQuery, gender: catalogGender, limit: engine === 'doubao-tts-preset' ? 500 : (catalogQuery ? 120 : 40) }); if ($store.speakerCatalogKey !== key) return; $store.speakerCatalog = items; if (!$store.speakerId && items[0]) $store.speakerId = items[0].speaker_id; } catch { if ($store.speakerCatalogKey === key) { $store.speakerCatalog = []; $store.speakerCatalogKey = ''; $store.speakerCatalogLoading = false; } } finally { if ($store.speakerCatalogKey === key) $store.speakerCatalogLoading = false; } }
	async function refreshDoubaoSpeakerCatalog() { await loadSpeakerCatalog('doubao-tts-preset', '', 'all'); }
	function prepareLongformPlan(plan: GeneratePlanResponse) { $store.lastGeneratePlan = plan; $store.textSegments = plan.segments.map(s => s.text); $store.showSplitPreview = plan.segments.length > 1; $store.splitPreviewCollapsed = false; }
	function requestLongformStrategy(plan: GeneratePlanResponse): Promise<LongformStrategy | null> { prepareLongformPlan(plan); if (!plan.requires_user_confirmation) return Promise.resolve('single'); $store.pendingLongformPlan = plan; $store.longformStrategy = 'split_merge'; $store.longformVerifyEnabled = true; $store.longformMergeEnabled = true; $store.longformMaxRetries = 2; $store.showLongformDialog = true; return new Promise<LongformStrategy | null>(r => { $store.pendingLongformResolve = r; }); }
	function longformSingleDisabled(plan: GeneratePlanResponse | null) { return plan?.mode === 'longform_strongly_recommended'; }
	function closeLongformDialog(v: LongformStrategy | null) { const r = $store.pendingLongformResolve; const next = v === 'single' && longformSingleDisabled($store.pendingLongformPlan) ? null : v; $store.showLongformDialog = false; $store.pendingLongformResolve = null; $store.pendingLongformPlan = null; r?.(next); }
	function longformSegmentsFor(plan: GeneratePlanResponse): PlannedTextSegment[] { return plan.segments.length ? plan.segments : [{ index: 1, text: $store.text.trim(), char_count: $store.text.trim().length, segment_reason: 'direct_text' }]; }
	async function generateSeedAudio() {
		$store.error = '';
		$store.busy = true;
		try {
			const request = seedAudioStateToRequest(seedAudioState);
			const uploadsUserMedia = request.input_assets.some(asset => asset.type !== 'speaker');
			if (uploadsUserMedia) {
				const shouldAsk = $store.settings?.doubao_upload_confirm !== false;
				if (shouldAsk && !window.confirm('本次会把参考素材和生成描述上传到豆包云端，最长可能生成 120 秒，参考费用上限约 2 元。确认继续吗？')) return;
				request.engine_parameters.confirm_upload = true;
			}
			const eng = $store.engines.find(item => item.manifest.engine_id === SEED_AUDIO_ENGINE_ID);
			if (eng && eng.state.status !== 'loaded') await Api.startEngine(SEED_AUDIO_ENGINE_ID);
			const response = await Api.generate(request);
			$store.currentPage = 1;
			void poll(response.task_id).catch((e) => { $store.error = (e as Error).message || '任务状态刷新失败'; });
		} catch (e) {
			$store.error = (e as Error).message || 'Seed Audio 生成失败';
		} finally {
			$store.busy = false;
		}
	}
	async function generate() {
		if (isSeedAudio) { await generateSeedAudio(); return; }
		if (!$store.text.trim()) return;
		$store.error = '';
		$store.busy = true;
		try {
			const usingCustomVoice = usesReferenceVoice && $store.voiceSource === 'reference_audio';
			if (isDoubaoClone && usingCustomVoice) {
				$store.error = '豆包声音复刻合成只使用已训练成功的云端音色，请切回“音色库”。';
				return;
			}
			if (isDoubaoClone) {
				if (!$store.voiceId || !selectedVoice) {
					$store.error = doubaoCloneVoices.length ? '请选择已训练成功的豆包云端音色。' : '还没有可用于合成的豆包云端复刻音色，请先到声音库完成训练并刷新状态。';
					return;
				}
				const doubaoBinding = selectedVoice.engine_bindings.find(b => b.engine_id === 'doubao-tts-voiceclone');
				if (!doubaoBinding?.available) {
					$store.error = doubaoBinding?.reason
						? `当前音色不能用于豆包云端复刻：${doubaoBinding.reason}`
						: '当前音色还没有可用的豆包云端 speaker_id。';
					return;
				}
			}
			if (usesReferenceVoice && usingCustomVoice && customVoiceSelectionDirty) {
				$store.error = '选区已调整，请先点击“使用并识别”更新当前参考音频。';
				return;
			}
			if (usesReferenceVoice && usingCustomVoice && $store.customVoiceError) {
				$store.error = $store.customVoiceError;
				return;
			}
			if (usesReferenceVoice && usingCustomVoice && !$store.customVoiceReferenceAudioPath) {
				$store.error = '请先拖入自定义音色。';
				return;
			}
			if (usesReferenceVoice && usingCustomVoice && !$store.customVoiceTranscript.trim()) {
				$store.error = '请检查或填写自定义音色参考台词。';
				return;
			}
			if ((isF5 || isCosyVoiceZeroShot) && !usingCustomVoice && !$store.voiceId) {
				$store.error = `${selected?.manifest.display_name ?? '当前模型'} 需要选择带参考音频和参考台词的本地音色。`;
				return;
			}
			if ((isF5 || isCosyVoiceZeroShot) && !usingCustomVoice && !selectedVoice?.reference_text.trim()) {
				$store.error = `${selectedVoice?.name ?? '当前音色'} 缺少参考台词。`;
				return;
			}
			if (isConfucius4 && !usingCustomVoice && !$store.voiceId) {
				$store.error = 'Confucius4-TTS 需要选择音色库音色，或切换到自定义音色。';
				return;
			}
			if (isMimoClone && !usingCustomVoice) {
				if (!$store.voiceId || !selectedVoice) {
					$store.error = '请选择可用于 MiMo 云端复刻的本地音色，或切换到自定义音色。';
					return;
				}
				const mimoBinding = selectedVoice.engine_bindings.find(b => b.engine_id === 'mimo-v2.5-tts-voiceclone');
				if (!mimoBinding?.available) {
					$store.error = mimoBinding?.reason
						? `当前音色不能用于 MiMo 云端复刻：${mimoBinding.reason}`
						: '当前音色不能用于 MiMo 云端复刻，请选择已授权且带 wav/mp3 参考音频的音色。';
					return;
				}
			}
			if (isMimoClone && usingCustomVoice && !activeVoicePreviewUrl) {
				$store.error = '请先拖入可用于 MiMo 云端复刻的自定义音色。';
				return;
			}
			const plan = await Api.generatePlan({ text: $store.text.trim(), engine_id: $store.engineId, planner_mode: 'auto', target_format: $store.outputFormat });
			const lfChoice = await requestLongformStrategy(plan);
			if (!lfChoice) return;
			const referenceName = usingCustomVoice ? ($store.customVoiceFileName || '自定义音色') : (selectedVoice?.name ?? '当前参考音色');
			if (isMimoClone && $store.settings?.mimo_voiceclone_confirm_upload && !window.confirm(`MiMo 音色复刻会把「${referenceName}」发送到小米云端。继续吗？`)) return;
			if (lfChoice !== 'single') {
				const res = await Api.generateLongform({ generate_request: store.toRequest(), segments: longformSegmentsFor(plan), verify_enabled: $store.longformVerifyEnabled, merge_enabled: lfChoice === 'split_merge' && $store.longformMergeEnabled, max_retries: $store.longformMaxRetries, stop_merge_on_verification_failed: true, asr_engine_id: 'qwen3-asr-mlx', silence_ms: 300, normalize: false });
				$store.longformTasks = [res, ...$store.longformTasks.filter(i => i.longform_task_id !== res.longform_task_id)];
				$store.currentPage = 1;
				return;
			}
			const eng = $store.engines.find(i => i.manifest.engine_id === $store.engineId);
			if (eng && eng.state.status !== 'loaded') await Api.startEngine($store.engineId);
			const res = await Api.generate(store.toRequest());
			$store.currentPage = 1;
			void poll(res.task_id).catch((e) => {
				$store.error = (e as Error).message || '任务状态刷新失败';
			});
		} catch (e) {
			$store.error = (e as Error).message;
		} finally {
			$store.busy = false;
		}
	}
	async function reuse(t: GenerationTask) {
		$store.actionBusyTaskId = t.task_id;
		try {
			resetResultPlayback();
			await refreshRecordsData();
			const latest = await Api.task(t.task_id).catch(() => t);
			const request = H.requestFromTask(latest);
			await restoreRequest(request);
			$store.lastGeneratePlan = null;
			$store.textSegments = [];
			$store.showSplitPreview = false;
			$store.error = '';
			await tick();
			window.scrollTo({ top: 0, behavior: 'smooth' });
		} finally {
			$store.actionBusyTaskId = '';
		}
	}
	async function runTextToolFor(mode: 'clean' | 'numbers' | 'split', text: string, replaceText: (next: string) => void) {
		if (!text.trim()) return;
		$store.textToolBusy = mode;
		try {
			if (mode === 'clean') { replaceText((await Api.cleanText(text)).text); return; }
			if (mode === 'numbers') { replaceText((await Api.normalizeNumbers(text)).text); return; }
			$store.textSegments = (await Api.splitText(text)).segments;
			$store.showSplitPreview = true;
			$store.splitPreviewCollapsed = false;
		} finally {
			$store.textToolBusy = '';
		}
	}
	async function runTextTool(mode: 'clean' | 'numbers' | 'split') {
		await runTextToolFor(mode, $store.text, (next) => ($store.text = next));
	}
	async function runSeedTextTool(mode: 'clean' | 'numbers' | 'split') {
		const prompt = activeSeedAudioDraft(seedAudioState).prompt;
		await runTextToolFor(mode, prompt, (next) => updateSeedAudioState(updateSeedAudioPrompt(seedAudioState, next)));
	}
	function taskCancelTooltip(t: GenerationTask) {
		return t.longform_task_id && t.task_type === 'segment'
			? '停止这条分段所属的长文本队列'
			: '取消这个正在排队或生成中的任务';
	}
	async function cancelTask(t: GenerationTask) {
		$store.actionBusyTaskId = t.task_id;
		try {
			if (t.longform_task_id && t.task_type === 'segment') {
				await Api.cancelLongform(t.longform_task_id);
			} else {
				await Api.cancelTask(t.task_id);
			}
			await refreshRecordsData();
		} catch (e) {
			$store.error = (e as Error).message || '取消任务失败';
		} finally {
			$store.actionBusyTaskId = '';
		}
	}
	async function retryTask(t: GenerationTask) { $store.actionBusyTaskId = t.task_id; try { const res = await Api.retryTask(t.task_id); $store.currentPage = 1; await poll(res.task_id); await refreshRecordsData(); } finally { $store.actionBusyTaskId = ''; } }
	async function deleteTaskRecord(t: GenerationTask) { if (!window.confirm(t.result_id ? '删除这条记录和本地音频？' : '删除这条任务记录？')) return; $store.actionBusyTaskId = t.task_id; try { await Api.deleteTask(t.task_id); $store.selectedTaskIds = $store.selectedTaskIds.filter(id => id !== t.task_id); await refreshRecordsData(); } finally { $store.actionBusyTaskId = ''; } }
	async function retryLongformTask(t: LongformTask) { $store.actionBusyTaskId = t.longform_task_id; try { const n = await Api.retryLongformFailed(t.longform_task_id); $store.longformTasks = [n, ...$store.longformTasks.filter(i => i.longform_task_id !== n.longform_task_id)]; } finally { $store.actionBusyTaskId = ''; } }
	function longformTaskActionLabel(t: LongformTask) { return H.statusIsActive(t.status) ? '停止长文本队列' : '删除长文本任务'; }
	function longformTaskActionTooltip(t: LongformTask) { return H.statusIsActive(t.status) ? '停止这条长文本分段队列' : '删除这条长文本任务记录'; }
	async function handleLongformTaskAction(t: LongformTask) {
		const isActive = H.statusIsActive(t.status);
		const confirmed = window.confirm(
			isActive
				? '停止这条长文本队列？当前正在排队或生成的分段会取消，已完成片段会保留在结果记录里。'
				: '删除这条长文本任务记录？'
		);
		if (!confirmed) return;
		$store.actionBusyTaskId = t.longform_task_id;
		try {
			if (isActive) {
				await Api.cancelLongform(t.longform_task_id);
				await refreshRecordsData();
				return;
			}
			await Api.dismissLongform(t.longform_task_id);
			$store.longformTasks = $store.longformTasks.filter(i => i.longform_task_id !== t.longform_task_id);
		} catch (e) {
			$store.error = (e as Error).message || (isActive ? '停止长文本队列失败' : '删除长文本任务失败');
		} finally {
			$store.actionBusyTaskId = '';
		}
	}
	function taskVerificationPending(t: GenerationTask) { if (t.status !== 'success' || !t.result_id || H.taskIsLongformSegment(t) || H.taskIsLongformExport(t) || taskVerificationReport(t) || taskVerificationError(t)) return false; const c = new Date(t.completed_at ?? t.created_at).getTime(); if (!Number.isFinite(c)) return false; return Date.now() - c < 5 * 60 * 1000; }
	function taskVerificationReport(t: GenerationTask) { return $store.verificationReports[t.task_id] ?? t.verification; }
	function taskVerificationError(t: GenerationTask) { return $store.verificationErrors[t.task_id] || t.verification_error || ''; }
	async function verifyTask(t: GenerationTask) { if (!t.result_id) return; $store.verificationBusyTaskId = t.task_id; $store.verificationErrors = { ...$store.verificationErrors, [t.task_id]: '' }; try { const report = await Api.verifyTTSOutput({ result_id: t.result_id, asr_engine_id: 'qwen3-asr-mlx', language: (t.parameters['language'] === 'en' || t.parameters['language'] === 'auto' ? t.parameters['language'] : 'zh') as 'auto' | 'zh' | 'en' }); $store.verificationReports = { ...$store.verificationReports, [t.task_id]: report }; $store.tasks = $store.tasks.map(i => i.task_id === t.task_id ? { ...i, verification: report, verification_error: null } : i); } catch (e) { $store.verificationErrors = { ...$store.verificationErrors, [t.task_id]: (e as Error).message || '校对失败' }; } finally { $store.verificationBusyTaskId = ''; } }
	function resultAudioUrl(t: GenerationTask) { return t.result_id ? `/api/history/${t.result_id}/audio` : ''; }
	function resultDownloadUrl(t: GenerationTask, filename = '') { return t.result_id ? `/api/history/${t.result_id}/audio?download=1${filename ? `&filename=${encodeURIComponent(filename)}` : ''}` : ''; }
	function syncResultAudioCurrentTime() {
		const audio = $store.resultPreviewAudio;
		const time = audio?.currentTime ?? 0;
		resultAudioCurrentTime = Number.isFinite(time) ? Math.max(0, time) : 0;
	}
	function stopResultAudioFrameLoop() {
		if (resultAudioFrame !== null) cancelAnimationFrame(resultAudioFrame);
		resultAudioFrame = null;
	}
	function startResultAudioFrameLoop() {
		if (resultAudioFrame !== null) return;
		const step = () => {
			resultAudioFrame = null;
			if (!$store.resultAudioPlaying || !$store.resultPreviewAudio) return;
			syncResultAudioCurrentTime();
			resultAudioFrame = requestAnimationFrame(step);
		};
		resultAudioFrame = requestAnimationFrame(step);
	}
	function resetResultPlayback() { stopResultAudioFrameLoop(); resultAudioPendingTaskId = ''; resultAudioCurrentTime = 0; $store.playingResultTaskId = ''; $store.resultAudioPlaying = false; }
	function pauseResultPlayback() { stopResultAudioFrameLoop(); syncResultAudioCurrentTime(); resultAudioPendingTaskId = ''; $store.resultAudioPlaying = false; }
	function resultPlaybackErrorMessage(e?: unknown) { const message = e instanceof Error ? e.message : ''; return `历史记录音频无法播放，请确认结果文件仍存在且可访问${message ? `：${message}` : ''}`; }
	function isInterruptedResultPlayError(e: unknown) {
		return e instanceof Error && /interrupted by a call to pause|AbortError/i.test(e.message || '');
	}
	function handleResultAudioError() { if (!$store.playingResultTaskId) return; resetResultPlayback(); $store.error = resultPlaybackErrorMessage($store.resultPreviewAudio?.error?.message ? new Error($store.resultPreviewAudio.error.message) : undefined); }
	function handleResultAudioTimeUpdate() { syncResultAudioCurrentTime(); }
	function handleResultAudioPlaying() { if (!$store.playingResultTaskId) return; resultAudioPendingTaskId = ''; $store.resultAudioPlaying = true; syncResultAudioCurrentTime(); startResultAudioFrameLoop(); }
	async function playResultPlayback(t: GenerationTask, startTime = 0) {
		const url = resultAudioUrl(t);
		const audio = $store.resultPreviewAudio;
		if (!url || !audio) return;
		if (resultAudioPendingTaskId === t.task_id) return;

		const abs = new URL(url, window.location.href).href;
		resultAudioPendingTaskId = t.task_id;
		$store.playingResultTaskId = t.task_id;
		$store.resultAudioPlaying = false;
		$store.error = '';
		resultAudioCurrentTime = Math.max(0, startTime);

		try {
			if (!audio.paused) audio.pause();
			if (audio.src !== abs) {
				audio.src = url;
			}
			audio.currentTime = Math.max(0, startTime);
			await audio.play();
			if ($store.playingResultTaskId !== t.task_id) return;
			$store.resultAudioPlaying = true;
			startResultAudioFrameLoop();
		} catch (e) {
			if (!isInterruptedResultPlayError(e)) {
				resetResultPlayback();
				$store.error = resultPlaybackErrorMessage(e);
			}
		} finally {
			if (resultAudioPendingTaskId === t.task_id) resultAudioPendingTaskId = '';
		}
	}
	async function toggleResultPlayback(t: GenerationTask) {
		const audio = $store.resultPreviewAudio;
		const isSameTask = $store.playingResultTaskId === t.task_id;
		if (resultAudioPendingTaskId === t.task_id) return;
		if (isSameTask && ($store.resultAudioPlaying || resultAudioPendingTaskId)) {
			pauseResultPlayback();
			audio?.pause();
			return;
		}
		await playResultPlayback(t, isSameTask ? resultAudioCurrentTime : 0);
	}
	async function seekResultPlayback(t: GenerationTask, timeSeconds: number) {
		const audio = $store.resultPreviewAudio;
		const url = resultAudioUrl(t);
		if (!audio || !url) return;
		const nextTime = Math.max(0, timeSeconds);
		const abs = new URL(url, window.location.href).href;
		if ($store.playingResultTaskId === t.task_id && ($store.resultAudioPlaying || resultAudioPendingTaskId)) {
			if (audio.src !== abs) audio.src = url;
			audio.currentTime = nextTime;
			resultAudioCurrentTime = nextTime;
			return;
		}
		await playResultPlayback(t, nextTime);
	}
	function toggleTaskSelection(tId: string, c: boolean) { $store.selectedTaskIds = c ? [...$store.selectedTaskIds, tId] : $store.selectedTaskIds.filter(i => i !== tId); }
	function toggleVisibleSelection() { if (allVisibleSelected) { $store.selectedTaskIds = $store.selectedTaskIds.filter(id => !visibleSelectableTasks.some(t => t.task_id === id)); } else { $store.selectedTaskIds = Array.from(new Set([...$store.selectedTaskIds, ...visibleSelectableTasks.map(t => t.task_id)])); } }
	async function deleteSelectedTasks() { if (!$store.selectedTaskIds.length) return; if (!window.confirm(`批量删除 ${$store.selectedTaskIds.length} 条记录和本地音频？`)) return; await Promise.all($store.selectedTaskIds.map(id => Api.deleteTask(id))); $store.selectedTaskIds = []; await refreshRecordsData(); }
	function clearTaskFilters() { $store.taskQuery = ''; $store.taskStatusTab = 'all'; $store.taskEngineFilter = 'all'; $store.taskSourceFilter = 'all'; $store.taskDateFilter = 'all'; $store.taskSortBy = 'latest'; $store.currentPage = 1; }
	function taskPageJump(d: number) { $store.currentPage = Math.min(pageCount, Math.max(1, $store.currentPage + d)); }
	function taskPageGoTo(p: number) { $store.currentPage = Math.min(pageCount, Math.max(1, p)); }
	function jumpToPage() { const n = parseInt($store.pageJumpInput, 10); if (Number.isFinite(n) && n >= 1 && n <= pageCount) $store.currentPage = n; $store.pageJumpInput = ''; }
	function setTaskPageSizePreset(value: string) {
		if (value === 'auto') {
			$store.pageSizeAuto = true;
			$store.currentPage = 1;
			recalcAutoPageSize();
			return;
		}
		const next = Number.parseInt(value, 10);
		if (!Number.isFinite(next) || next < 2) return;
		$store.pageSizeAuto = false;
		$store.pageSize = next;
		$store.currentPage = 1;
	}
	function stopTaskCardStream() {
		if (taskCardStreamTimer) clearTimeout(taskCardStreamTimer);
		taskCardStreamTimer = null;
	}
	function startTaskCardStream(total: number) {
		stopTaskCardStream();
		taskCardRenderLimit = Math.min(total, 4);
		if (taskCardRenderLimit >= total) return;
		const step = () => {
			const next = Math.min(total, taskCardRenderLimit + 2);
			taskCardRenderLimit = next;
			if (next < total) taskCardStreamTimer = setTimeout(step, 45);
		};
		taskCardStreamTimer = setTimeout(step, 45);
	}
	function recalcAutoPageSize() {
		if (!$store.pageSizeAuto || !$store.resultGridEl) return;

		const grid = $store.resultGridEl;
		const gridRect = grid.getBoundingClientRect();
		const gridStyle = window.getComputedStyle(grid);
		const resolvedColumns = gridStyle.gridTemplateColumns
			.split(' ')
			.map((token) => token.trim())
			.filter(Boolean);
		const columnCount = Math.max(1, resolvedColumns.length || Math.floor(gridRect.width / 260));
		const ideal = Math.max(4, columnCount * 2);

		if (ideal !== $store.pageSize) {
			$store.pageSize = ideal;
			$store.currentPage = 1;
		}
	}
	function checkOverflow(node: HTMLElement, _text: string) { let frame = 0; const check = () => { frame = 0; node.classList.toggle('fade-overflow', node.scrollHeight > node.offsetHeight); }; const schedule = () => { if (frame) cancelAnimationFrame(frame); frame = requestAnimationFrame(check); }; schedule(); return { update(_nextText: string) { schedule(); }, destroy() { if (frame) cancelAnimationFrame(frame); } }; }
	function presetDescriptionMarquee(node: HTMLElement) {
		const viewport = node.querySelector<HTMLElement>('.preset-description-marquee');
		const track = node.querySelector<HTMLElement>('.preset-description-track');
		let frame = 0;
		const reset = () => {
			if (!track) return;
			if (frame) cancelAnimationFrame(frame);
			track.style.transition = 'none';
			track.style.transform = 'translateX(0)';
		};
		const play = () => {
			if (!track) return;
			reset();
			const distance = Math.max(0, track.scrollWidth - (viewport?.clientWidth ?? 0));
			viewport?.classList.toggle('is-overflowing', distance > 1);
			if (distance <= 1) return;
			frame = requestAnimationFrame(() => {
				track.style.transition = `transform ${Math.max(2200, distance * 26)}ms linear 350ms`;
				track.style.transform = `translateX(-${distance}px)`;
			});
		};
		const resizeObserver = new ResizeObserver(() => reset());
		if (viewport) resizeObserver.observe(viewport);
		node.addEventListener('mouseenter', play);
		node.addEventListener('mouseleave', reset);
		node.addEventListener('focusin', play);
		node.addEventListener('focusout', reset);
		return { destroy() { reset(); resizeObserver.disconnect(); node.removeEventListener('mouseenter', play); node.removeEventListener('mouseleave', reset); node.removeEventListener('focusin', play); node.removeEventListener('focusout', reset); } };
	}
	function trapDialogFocus(node: HTMLElement, closeDialog: () => void) {
		const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		let close = closeDialog;
		const frame = requestAnimationFrame(() => {
			const first = node.querySelector<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]');
			(first ?? node).focus();
		});
		function handleKeydown(event: KeyboardEvent) {
			if (event.key === 'Escape') {
				event.preventDefault();
				close();
				return;
			}
			if (event.key !== 'Tab') return;
			const focusable = [...node.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]')];
			if (!focusable.length) return;
			const first = focusable[0];
			const last = focusable[focusable.length - 1];
			if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
			else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
		}
		node.addEventListener('keydown', handleKeydown);
		return {
			update(next: () => void) { close = next; },
			destroy() {
				cancelAnimationFrame(frame);
				node.removeEventListener('keydown', handleKeydown);
				previousFocus?.focus();
			}
		};
	}
	function handleDialogEscape(event: KeyboardEvent) {
		if (event.key !== 'Escape') return;
		if (voiceRegisterOpen) resetVoiceRegisterDialog();
		else if ($store.showLongformDialog) closeLongformDialog(null);
	}
	async function syncResultBadgeTitles() {
		await tick();
		for (const badge of document.querySelectorAll<HTMLElement>('.result-meta-primary .badge:first-child, .result-meta-secondary .engine')) {
			const text = badge.textContent?.trim();
			if (text) badge.title = text;
		}
	}

	onMount(() => {
		async function retryLoad(load: () => Promise<void>) {
			for (let i = 0; i < 10; i++) {
				try {
					await load();
					return;
				} catch {
					await new Promise(r => setTimeout(r, 2000));
				}
			}
		}
		void retryLoad(refreshComposerData);
		void retryLoad(refreshRecordsData);
		connectTaskSocket();
		const longformId = setInterval(() => {
			if ($store.resultAudioPlaying || resultAudioPendingTaskId) return;
			if ($store.longformTasks.some(task => H.statusIsActive(task.status))) void loadLongformTasks().catch(() => undefined);
		}, 3000);
		const fallbackId = setInterval(() => {
			if ($store.resultAudioPlaying || resultAudioPendingTaskId) return;
			void refreshRecordsData().catch(() => undefined);
		}, 60000);
		const or = () => recalcAutoPageSize();
		window.addEventListener('resize', or);
		window.addEventListener('keydown', handleCustomVoiceTrimKeydown);
		window.addEventListener('keydown', handleDialogEscape);
		_autoResizeRO = new ResizeObserver(or);
		return () => {
			taskSocketClosed = true;
			taskSocket?.close();
			taskSocket = null;
			if (taskSocketReconnectTimer) clearTimeout(taskSocketReconnectTimer);
			if (taskPageTimer) clearTimeout(taskPageTimer);
			stopCustomVoicePreviewFrameLoop();
			stopResultAudioFrameLoop();
			clearInterval(longformId);
			clearInterval(fallbackId);
			window.removeEventListener('resize', or);
			window.removeEventListener('keydown', handleCustomVoiceTrimKeydown);
			window.removeEventListener('keydown', handleDialogEscape);
			_autoResizeRO?.disconnect();
			if (_speakerCatalogTimer) clearTimeout(_speakerCatalogTimer);
			stopTaskCardStream();
			if ($store.customVoicePreviewUrl) revokeObjectUrlIfNeeded($store.customVoicePreviewUrl);
			seedPreviewAudio?.pause();
			for (const url of seedOwnedObjectUrls) URL.revokeObjectURL(url);
			seedOwnedObjectUrls.clear();
		};
	});
	$effect(() => {
		if ($store.engineId === $store.lastEngineId) return;
		if ($store.lastEngineId === SEED_AUDIO_ENGINE_ID && seedEditingSlot !== null) closeActiveReferenceEditor();
		store.setEngine($store.engineId);
	});
	$effect(() => { if (!hasMoreParams && $store.showMoreParams) $store.showMoreParams = false; });
	$effect(() => { const eid = $store.engineId; const q = eid === 'doubao-tts-preset' ? '' : $store.speakerQuery.trim(); const g = eid === 'doubao-tts-preset' ? 'all' : $store.speakerGenderFilter; const key = `${eid}|${q}|${g}`; if (key === _speakerCatalogRequestKey) return; _speakerCatalogRequestKey = key; if (_speakerCatalogTimer) clearTimeout(_speakerCatalogTimer); _speakerCatalogTimer = setTimeout(() => { untrack(() => { void loadSpeakerCatalog(eid, q, g); }); }, 150); });
	$effect(() => { if (!$store.initialized) return; if (!usesReferenceVoice) { if ($store.voiceId) untrack(() => { $store.voiceId = ''; }); if ($store.voiceSource !== 'voice_library') untrack(() => { $store.voiceSource = 'voice_library'; }); return; } if (isDoubaoClone && $store.voiceSource !== 'voice_library') untrack(() => { $store.voiceSource = 'voice_library'; }); if ($store.voiceSource === 'reference_audio' && $store.voiceId) untrack(() => { $store.voiceId = ''; }); if ($store.voiceSource === 'voice_library' && $store.voiceId && !visibleVoiceOptions.some(v => v.voice_id === $store.voiceId)) untrack(() => { $store.voiceId = ''; }); });
	let _lastPreviewVoiceId = $state('');
	$effect(() => { const vid = `${$store.voiceSource}|${$store.voiceId}|${$store.customVoicePreviewUrl}`; if (vid !== _lastPreviewVoiceId) { _lastPreviewVoiceId = vid; untrack(() => stopVoicePreview()); } });
	$effect(() => { if ($store.currentPage > pageCount) $store.currentPage = pageCount; });
	$effect(() => { if ($store.pageSizeAuto && $store.resultGridEl) recalcAutoPageSize(); });
	let _lastTaskPageRequestKey = $state('');
	$effect(() => {
		if (!$store.initialized) return;
		const params = taskPageParams;
		const key = JSON.stringify(params);
		if (key === _lastTaskPageRequestKey) return;
		_lastTaskPageRequestKey = key;
		if (taskPageTimer) clearTimeout(taskPageTimer);
		taskPageTimer = setTimeout(() => {
			taskPageTimer = null;
			untrack(() => void loadTaskPage(params).catch(() => undefined));
		}, 160);
	});
	let _lastPagedTaskKey = $state('');
	$effect(() => {
		const key = pagedTasks.map(t => `${t.task_id}:${t.status}:${t.result_id ?? ''}`).join('|');
		if (key !== _lastPagedTaskKey) {
			_lastPagedTaskKey = key;
			untrack(() => startTaskCardStream(pagedTasks.length));
		}
		void syncResultBadgeTitles();
	});
	$effect(() => {
		if (_autoResizeRO) _autoResizeRO.disconnect();
		if (!_autoResizeRO) return;
		if ($store.resultGridEl) _autoResizeRO.observe($store.resultGridEl);
		if (recordsViewportEl) _autoResizeRO.observe(recordsViewportEl);
		if (recordsBottomPagerEl) _autoResizeRO.observe(recordsBottomPagerEl);
	});
</script>

<svelte:head><title>语音合成 - 声音工作台</title></svelte:head>
<main class="page generate-page">
	<div class="page-head"><div><h1>语音合成</h1><p class="muted">短文本合成、文本处理、任务进度和生成记录统一放在一个工作台里。</p></div></div>
	{#if videoLocalizationHandoff}
		<div class="handoff-banner">
			<div>
				<strong>来自视频本土化</strong>
				<span>{videoLocalizationHandoffLabel(videoLocalizationHandoff)} · {videoLocalizationHandoff.cue_id}</span>
			</div>
			<a class="btn compact" href={`/video-localization?project_id=${encodeURIComponent(videoLocalizationHandoff.project_id)}`}>返回项目</a>
		</div>
	{/if}
	<div class="workbench"><div class="panel stack compose-panel">
		<div class="row gen-section-head preset-head"><div><h2>合成预设</h2><p class="muted">跟随当前引擎{isSeedAudio ? '和模式' : ''}，只显示可用于 {selected?.manifest.display_name ?? $store.engineId}{isSeedAudio ? ` · ${seedAudioState.mode === 'text' ? '文本' : seedAudioState.mode === 'audio' ? '语音' : '图片'}模式` : ''} 的参数组合。</p></div><div class="row wrap preset-tools"><span class="muted">{enginePresets.length} 组</span><button class="btn compact preset-toggle-btn" type="button" aria-expanded={presetStripOpen} data-tooltip={presetStripOpen ? '收起预设面板' : '展开预设面板'} onclick={() => (presetStripOpen = !presetStripOpen)}><ChevronRight size={14} /> {presetStripOpen ? '收起' : '展开'}</button></div></div>
		{#if presetStripOpen}<div class="preset-strip">{#each enginePresets as p}{#if p.preset_id.startsWith('custom_')}<div class="preset-chip custom-preset"><div class="preset-custom-head"><button class="preset-action-btn" type="button" aria-label="编辑预设" data-tooltip="编辑这个自定义预设" onclick={() => openPresetEditor(p)}><Pencil size={12} /></button><button class="preset-custom-title text-pop" type="button" data-text={presetTooltip(p)} onclick={() => applyComposerPreset(p)}><strong>{p.name}</strong></button><button class="preset-action-btn danger" type="button" aria-label="删除预设" data-tooltip="删除这个自定义预设" onclick={() => deletePreset(p)}><Trash2 size={12} /></button></div><button class="preset-custom-subtitle text-pop" type="button" data-text={presetTooltip(p)} onclick={() => applyComposerPreset(p)}>{p.scene || p.description || H.engineTypeLabel(p.engine_id, engineMap)}</button></div>{:else}<div class="preset-chip"><button class="preset-main" type="button" aria-label={`应用预设：${p.name}`} use:presetDescriptionMarquee onclick={() => applyComposerPreset(p)}><strong>{p.name}</strong><span class="preset-description-marquee"><span class="preset-description-track">{p.scene || p.description || H.engineTypeLabel(p.engine_id, engineMap)}</span></span></button></div>{/if}{/each}<button class="preset-chip preset-add-chip" type="button" aria-label="保存当前参数为预设" data-tooltip="把当前文本、素材和参数保存成这个引擎当前模式的自定义预设" onclick={() => openPresetEditor()}><Plus size={17} /><span>保存当前</span></button></div>{/if}
		{#if $store.showPresetEditor}<div class="preset-editor"><div class="row gen-section-head"><div><h3>{$store.editingPresetId ? '编辑自定义预设' : '保存当前为预设'}</h3><p class="muted">绑定引擎：{selected?.manifest.display_name ?? $store.engineId}</p></div><button class="gen-icon-btn mini" type="button" aria-label="关闭预设编辑器" data-tooltip="关闭预设编辑器" onclick={() => ($store.showPresetEditor = false)}>X</button></div><div class="preset-editor-grid"><label class="field"><span>名称</span><input bind:value={$store.presetDraft.name} placeholder="例如：课程慢讲" /></label><label class="field"><span>场景</span><input bind:value={$store.presetDraft.scene} placeholder="例如：教程 / 长文旁白" /></label><label class="field wide"><span>描述</span><input bind:value={$store.presetDraft.description} placeholder="简短说明" /></label><label class="field"><span>标签</span><input bind:value={$store.presetDraft.tags} placeholder="慢讲，课程" /></label><label class="field wide"><span>示例文本</span><textarea bind:value={$store.presetDraft.sample_text} placeholder="可选"></textarea></label></div><div class="row wrap"><button class="btn primary compact" type="button" onclick={savePreset} disabled={$store.presetBusy || !$store.presetDraft.name.trim()}><Save size={14} /> {$store.presetBusy ? '保存中' : '保存预设'}</button><button class="btn compact" type="button" onclick={() => ($store.showPresetEditor = false)}>取消</button></div></div>{/if}
		<div class="param-inline-row">
			<label class="param-inline engine-param-inline"><span>引擎</span><EngineSelector engines={ttsEngines} bind:value={$store.engineId} /></label>
			{#if isDoubaoPreset}<DoubaoVoiceCatalogDrawer speakers={speakerCatalogIsCurrent ? $store.speakerCatalog : []} bind:value={$store.speakerId} loading={$store.speakerCatalogLoading} recentIds={doubaoRecentSpeakerIds} onRefresh={refreshDoubaoSpeakerCatalog} />{/if}
			{#if isSeedAudio}
				<SeedAudioInlineControls
					state={seedAudioState}
					showAdvanced={seedShowAdvanced}
					onChange={updateSeedAudioState}
					onToggleAdvanced={() => (seedShowAdvanced = !seedShowAdvanced)}
				/>
			{:else}
			{#if usesReferenceVoice}<div class="param-inline voice-param-inline" class:library-source={$store.voiceSource === 'voice_library'} class:custom-source={$store.voiceSource === 'reference_audio'}><span>声音</span><div class="voice-source-control"><div class="gen-segmented voice-source-tabs" role="tablist" aria-label="声音来源"><button class:active={$store.voiceSource === 'voice_library'} type="button" onclick={() => setVoiceSource('voice_library')}>{isDoubaoClone ? '云端音色' : '音色库'}</button>{#if !isDoubaoClone}<button class:active={$store.voiceSource === 'reference_audio'} type="button" onclick={() => setVoiceSource('reference_audio')}>自定义</button>{/if}</div>{#if $store.voiceSource === 'voice_library'}<div class="voice-inline"><VoiceSelector voices={visibleVoiceOptions} bind:value={$store.voiceId} /><button class="gen-icon-btn mini" type="button" aria-label={$store.voicePreviewPlaying ? '暂停试听音色' : '试听当前音色'} data-tooltip={$store.voicePreviewPlaying ? '暂停当前音色试听' : '试听当前选择的音色'} onclick={(e) => { e.preventDefault(); e.stopPropagation(); previewSelectedVoice(); }} disabled={!activeVoicePreviewUrl}>{#if $store.voicePreviewPlaying}<Square size={13} />{:else}<Play size={13} />{/if}</button></div>{:else}<div class="custom-voice-inline"><span class="custom-voice-chip" class:ok={customVoiceMatched}>{#if customVoiceMatched}<CircleCheck size={13} />{:else}<FileAudio size={13} />{/if}{$store.customVoiceFileName || '未上传'}</span><button class="gen-icon-btn mini" type="button" aria-label={$store.voicePreviewPlaying ? '暂停试听自定义音色' : '试听自定义音色'} data-tooltip={$store.voicePreviewPlaying ? '暂停自定义音色试听' : '试听当前自定义音色'} onclick={(e) => { e.preventDefault(); e.stopPropagation(); previewSelectedVoice(); }} disabled={!activeVoicePreviewUrl}>{#if $store.voicePreviewPlaying}<Square size={13} />{:else}<Play size={13} />{/if}</button></div>{/if}</div></div>{#if activeVoicePreviewUrl}<audio bind:this={$store.voicePreviewAudio} src={activeVoicePreviewUrl} preload="none" ontimeupdate={handleVoicePreviewTimeUpdate} onended={stopVoicePreview} onpause={handleVoicePreviewPause}></audio>{/if}{/if}
			{#if activeParamKeys.has('speed')}<label class="param-inline-range"><span>语速</span><input class="speed-number" type="number" min="0.5" max="2" step="0.05" value={$store.speed.toFixed(2)} oninput={(e) => setSpeedValue((e.currentTarget as HTMLInputElement).value)} onblur={(e) => ((e.currentTarget as HTMLInputElement).value = $store.speed.toFixed(2))} /><input type="range" min="0.5" max="2" step="0.05" value={$store.speed} oninput={(e) => setSpeedValue((e.currentTarget as HTMLInputElement).value)} /></label>{/if}
			<label class="param-inline param-inline-format"><span>格式</span><select bind:value={$store.outputFormat}><option value="wav">WAV</option><option value="mp3">MP3</option><option value="flac">FLAC</option></select></label>
			<div class="param-actions-inline">
				<button class="btn param-action-btn param-reset-btn" type="button" data-tooltip="把当前引擎的参数恢复到默认值。正文和已选参考音色会保留。" onclick={resetCurrentEngineParams}><RotateCcw size={14} /> 重置参数</button>
				{#if hasMoreParams}<button class="btn param-action-btn param-inline-more" type="button" onclick={() => ($store.showMoreParams = !$store.showMoreParams)}><Settings size={14} /> {$store.showMoreParams ? '收起高级' : '更多选项'}</button>{/if}
			</div>
			{/if}
		</div>
		{#if isSeedAudio}
			<SeedAudioPanel
				state={seedAudioState}
				showAdvanced={seedShowAdvanced}
				generateBusy={$store.busy}
				assetBusy={seedAssetBusy}
				onChange={updateSeedAudioState}
				onGenerate={generateSeedAudio}
				onTextTool={runSeedTextTool}
				textToolBusy={$store.textToolBusy}
				onUploadAudio={uploadSeedAudioReference}
				onChooseVoice={(slot) => { seedVoicePickerSlot = slot; seedAssetError = ''; }}
				onChooseSpeaker={(slot) => { seedSpeakerPickerSlot = slot; seedAssetError = ''; }}
				onEditAudio={openSeedAudioEditor}
				onPreviewAudio={previewSeedReference}
				onUploadImage={uploadSeedAudioImage}
			/>
			{#if seedAssetError}<div class="badge fail seed-asset-error">{seedAssetError}</div>{/if}
		{/if}
			{#if isSeedAudio && seedEditingSlot && $store.customVoicePreviewUrl}<audio bind:this={$store.voicePreviewAudio} src={$store.customVoicePreviewUrl} preload="none" ontimeupdate={handleVoicePreviewTimeUpdate} onended={stopVoicePreview} onpause={handleVoicePreviewPause}></audio>{/if}
			{#if (usesReferenceVoice && $store.voiceSource === 'reference_audio') || (isSeedAudio && seedEditingSlot !== null)}
				<section class="custom-voice-panel">
					<div class="custom-voice-source-row">
						<div class="custom-voice-dropzone" role="region" aria-label="自定义音色拖拽上传区" class:drag-active={customVoiceDragActive} ondragenter={(e) => { e.preventDefault(); customVoiceDragActive = true; }} ondragover={(e) => { e.preventDefault(); customVoiceDragActive = true; }} ondragleave={(e) => { e.preventDefault(); customVoiceDragActive = false; }} ondrop={onCustomVoiceDrop}>
							<CloudUpload size={22} />
							<div>
								<strong>{$store.customVoiceFileName || '拖入参考音频'}</strong>
								<span class:fail={$store.customVoiceError} class:warn={!$store.customVoiceError && $store.customVoiceQualityWarnings.length > 0}>{$store.customVoiceError || $store.customVoiceQualityWarnings[0] || customVoiceStatusText()}</span>
							</div>
							{#if $store.customVoiceBusy}<span class="badge">{customVoiceBusyMode === 'source' ? '读取' : 'ASR'}</span>{:else if customVoiceMatched}<span class="badge ok">已匹配</span>{:else if customVoiceReady}<span class="badge">待识别</span>{/if}
						</div>
						<label class="field custom-voice-text custom-voice-text-inline custom-voice-card">
							<textarea rows="2" aria-label="台词文本" value={$store.customVoiceTranscript} oninput={(e) => updateActiveReferenceTranscript((e.currentTarget as HTMLTextAreaElement).value)} placeholder="ASR 识别后可编辑中文或英文台词" disabled={!$store.customVoiceFileName}></textarea>
						</label>
						<div class="custom-voice-srt-card custom-voice-card">
							<div class="custom-voice-metrics">
								<div><span>字幕状态</span><strong>{#if $store.customVoiceTranscriptionId}<Captions size={12} /> 已生成{:else}未生成{/if}</strong></div>
								<div><span>持续时间</span><strong>{formatDuration(customVoiceDisplayDurationMs)}</strong></div>
								<div><span>字幕段数</span><strong>{$store.customVoiceSrtSegmentCount || '未识别'}</strong></div>
								<div><span>识别引擎</span><strong>{$store.customVoiceTranscriptionId ? 'Qwen3 ASR' : '等待音频'}</strong></div>
								<div><span>当前切片</span><strong>{customVoiceActiveClipLabel}</strong></div>
							</div>
						</div>
					</div>
					{#if customVoiceOriginalFile}
						<div
							class="custom-voice-trimmer"
							class:hotkeys-active={customVoiceTrimHotkeysActive}
							role="group"
							aria-label="裁切选区，空格播放选区，I 设置入点，O 设置出点"
							onmouseenter={() => (customVoiceTrimHover = true)}
							onmouseleave={() => (customVoiceTrimHover = false)}
							onfocusin={() => (customVoiceTrimFocusWithin = true)}
							onfocusout={handleCustomVoiceTrimFocusOut}
						>
							<div class="custom-voice-trimmer-head">
								<div class="custom-voice-trim-readout" aria-live="polite">
									<span class="readout-chip readout-selection"><b>选区</b>{formatDuration(customVoiceSelectedDurationMs)}</span>
									<span class="readout-chip readout-in"><b>IN</b>{formatTimecode(customVoiceTrimStart)}</span>
									<span class="readout-chip readout-out"><b>OUT</b>{formatTimecode(customVoiceTrimEnd)}</span>
									<span class="readout-chip readout-current"><b>当前</b>{formatTimecode(customVoicePlaybackPosition)}</span>
									<span class="readout-chip readout-status" class:ok={customVoiceMatched && !customVoiceSelectionDirty} class:warn={customVoiceSelectionDirty}><b>处理</b>{customVoiceSelectionDirty ? '待重新识别' : (customVoiceMatched ? '已生效' : '待识别')}</span>
								</div>
								<div class="trim-transport-buttons">
									<button class="trim-icon-btn play" type="button" aria-label={customVoiceLoopPreview && $store.voicePreviewPlaying ? '停止选区播放' : '播放选区'} data-tooltip={customVoiceLoopPreview && $store.voicePreviewPlaying ? '停止并回到入点，快捷键 Space' : '从入点播放到出点，快捷键 Space'} onclick={toggleCustomVoiceSelectionPreview} disabled={!customVoiceSourcePreviewUrl || !customVoiceSourceDurationMs || customVoiceSelectedDurationMs < 100}>{#if customVoiceLoopPreview && $store.voicePreviewPlaying}<Square size={16} />{:else}<Play size={16} />{/if}</button>
										<button class="trim-loop-btn trim-icon-only" class:active={customVoiceLoopEnabled} type="button" aria-label={customVoiceLoopEnabled ? '关闭循环播放' : '开启循环播放'} data-tooltip={customVoiceLoopEnabled ? '循环播放已开启，点击关闭' : '循环播放已关闭，点击开启'} onclick={() => (customVoiceLoopEnabled = !customVoiceLoopEnabled)} disabled={!customVoiceSourcePreviewUrl || !customVoiceSourceDurationMs}><Repeat size={14} /></button>
											<div class="trim-zoom-buttons" aria-label="时间轴缩放">
												<button class="trim-tool-btn" type="button" aria-label="缩小时间轴" data-tooltip="缩小时间轴，快捷键 -" onclick={() => zoomCustomVoiceTimeline(customVoiceTimelineZoom / 1.35)} disabled={!customVoiceSourceDurationMs}>−</button>
												<span>{formatTimelineZoom(customVoiceTimelineZoom)}x</span>
												<button class="trim-tool-btn" type="button" aria-label="放大时间轴" data-tooltip="放大时间轴，快捷键 + / =" onclick={() => zoomCustomVoiceTimeline(customVoiceTimelineZoom * 1.35)} disabled={!customVoiceSourceDurationMs}>+</button>
											</div>
											<button class="trim-marker-btn trim-marker-in-btn trim-icon-only" type="button" aria-label="将当前指针设为入点" data-tooltip="将当前指针设为入点，快捷键 I" onclick={setCustomVoiceTrimStartAtPlayhead} disabled={!customVoiceSourceDurationMs}><ChevronsLeft size={13} /></button>
											<button class="trim-marker-btn trim-marker-out-btn trim-icon-only" type="button" aria-label="将当前指针设为出点" data-tooltip="将当前指针设为出点，快捷键 O" onclick={setCustomVoiceTrimEndAtPlayhead} disabled={!customVoiceSourceDurationMs}><ChevronsRight size={13} /></button>
											<button class="trim-marker-btn trim-icon-only" type="button" aria-label="重置为完整选区" data-tooltip="重置为完整选区" onclick={resetCustomVoiceTrimRange} disabled={!customVoiceSourceDurationMs}><RotateCcw size={13} /></button>
											<button class="btn compact primary trim-apply-btn trim-icon-only" type="button" aria-label="使用选区并识别台词" data-tooltip="使用当前选区作为样音，并用 ASR 识别台词" onclick={applyActiveReferenceTrim} disabled={$store.customVoiceBusy || !customVoiceSourceDurationMs || customVoiceSelectedDurationMs < 100}><CircleCheck size={13} /></button>
											{#if !isSeedAudio}<button class="btn compact trim-inline-action trim-icon-only" type="button" aria-label="注册为音色" data-tooltip="把当前选区和台词保存到音色库" onclick={openVoiceRegisterDialog} disabled={$store.customVoiceBusy || !$store.customVoiceFileId || !$store.customVoiceTranscript.trim()}><Plus size={13} /></button>{/if}
											<button class="btn compact trim-inline-action trim-icon-only" type="button" aria-label={isSeedAudio ? '关闭参考声音编辑器' : '清除参考音频'} data-tooltip={isSeedAudio ? '关闭编辑器并保留当前参考声音' : '清除当前参考音频、裁切选区和识别文本'} onclick={isSeedAudio ? closeActiveReferenceEditor : resetCustomVoice} disabled={!$store.customVoiceFileName}><X size={13} /></button>
										</div>
									</div>
								<div class="custom-voice-editor-strip">
								<div class="custom-voice-timebar-window" role="region" aria-label="裁剪时间轴滚动窗口" onwheel={handleCustomVoiceTimelineWheel} onscroll={(e) => updateCustomVoiceTimelineViewport(e.currentTarget as HTMLElement)} onpointerenter={(e) => updateCustomVoiceTimelineViewport(e.currentTarget as HTMLElement)}>
									<div class="custom-voice-timebar" role="group" aria-label="自定义音色裁切时间轴" style={`--trim-start:${customVoiceTrimStartPercent}%;--trim-end:${customVoiceTrimEndPercent}%;--playhead:${customVoicePlayheadPercent}%;width:${customVoiceTimelineZoom * 100}%`} onpointerdown={handleCustomVoiceTimebarPointer}>
										<div class="custom-voice-timebar-ruler" aria-hidden="true">
											{#each customVoiceTimelineTicks as tick}
												<span class:major={tick.major} style={`left:${tick.percent}%`}><i></i><b>{tick.label}</b></span>
											{/each}
										</div>
										<div class="custom-voice-timebar-track" aria-hidden="true"></div>
										<div class="custom-voice-waveform" class:loading={customVoiceWaveformLoading} style={`--waveform-progress:${Math.round(customVoiceWaveformProgress * 100)}%`} aria-hidden="true">
											{#if customVoiceWaveformBars.length}
												<svg class="custom-voice-waveform-svg" viewBox={`0 0 ${customVoiceWaveformBars.length} 100`} preserveAspectRatio="none">
													<line class="waveform-midline" x1="0" y1="50" x2={customVoiceWaveformBars.length} y2="50" />
													{#each customVoiceVisibleWaveformBars as bar}
														<rect x={bar.x + 0.08} y={50 - bar.level * 46} width={bar.width} height={Math.max(8, bar.level * 92)} rx="0.18" />
													{/each}
												</svg>
											{:else}
												<span class="waveform-empty"></span>
											{/if}
										</div>
										<div class="custom-voice-play-progress" aria-hidden="true"></div>
										<button type="button" class="trim-playhead-handle" aria-label="拖动当前播放指针" onpointerdown={beginCustomVoicePlayheadDrag}><span>当前</span></button>
										<button type="button" class="trim-handle-label trim-in-label" aria-label="拖动裁切入点" onpointerdown={(e) => beginCustomVoiceTrimDrag(e, 'start')}><span>IN</span></button>
										<button type="button" class="trim-handle-label trim-out-label" aria-label="拖动裁切出点" onpointerdown={(e) => beginCustomVoiceTrimDrag(e, 'end')}><span>OUT</span></button>
										<input aria-label="裁切入点" class="trim-range trim-start" type="range" min="0" max={customVoiceDurationSeconds} step="0.1" value={customVoiceTrimStart} oninput={(e) => setCustomVoiceTrimStart((e.currentTarget as HTMLInputElement).value)} disabled={!customVoiceSourceDurationMs} />
										<input aria-label="裁切出点" class="trim-range trim-end" type="range" min="0.1" max={customVoiceDurationSeconds} step="0.1" value={customVoiceTrimEnd} oninput={(e) => setCustomVoiceTrimEnd((e.currentTarget as HTMLInputElement).value)} disabled={!customVoiceSourceDurationMs} />
									</div>
								</div>
							</div>
						</div>
					{:else}
						<div class="custom-voice-empty-editor">
							<FileAudio size={22} />
							<strong>等待参考音频</strong>
						</div>
					{/if}
				</section>
			{/if}
		{#if !isSeedAudio && $store.showMoreParams && hasMoreParams}<div class="more-params-panel" class:doubao-tts-params={isDoubao} class:doubao-preset-params={isDoubaoPreset} class:doubao-clone-params={isDoubaoClone}>
			{#if activeParamKeys.has('speaker_id') && !isDoubaoPreset}
				<div class="field param-field" class:span-wide={hasSearchableSpeakerCatalog} class:doubao-speaker-field={isDoubaoPreset} class:field-muted={isQwen3TTS && !qwen3PresetRoute}>
					<label class="param-label" for="spk">{isQwen3TTS ? '预置音色' : '音色'}</label>
					<div class="param-control">
						{#if hasSearchableSpeakerCatalog}
							<div class="speaker-catalog-tools">
								<div class="gen-search-field speaker-search"><Search size={14} /><input bind:value={$store.speakerQuery} placeholder="搜索名称或 ID" />{#if $store.speakerQuery.trim()}<button class="gen-search-clear" type="button" onclick={() => ($store.speakerQuery = '')}><X size={13} /></button>{/if}</div>
								<select class="speaker-gender" aria-label="音色性别" bind:value={$store.speakerGenderFilter}><option value="all">全部</option><option value="F">女声</option><option value="M">男声</option></select>
							</div>
						{/if}
						{#if isDoubaoPreset}
							<div class="doubao-speaker-options" role="listbox" aria-label="内置豆包音色">
								{#each speakerChoices as option}
									<button class:active={$store.speakerId === option.value} type="button" role="option" aria-selected={$store.speakerId === option.value} title={option.value} onclick={() => ($store.speakerId = option.value)}>
										<strong>{option.label}</strong><span>{option.value}</span>
									</button>
								{/each}
								{#if !$store.speakerCatalogLoading && speakerChoices.length === 0}<div class="doubao-speaker-empty">没有匹配的内置音色，可在下方直接填写官方 ID。</div>{/if}
							</div>
							<div class="doubao-speaker-id-row"><span>音色 ID</span><input id="spk" bind:value={$store.speakerId} placeholder="也可输入账号已授权的官方音色 ID" /></div>
						{:else}
							<select id="spk" bind:value={$store.speakerId} disabled={isQwen3TTS && !qwen3PresetRoute}>{#each speakerChoices as option}<option value={option.value}>{option.label}</option>{/each}</select>
						{/if}
						{#if isEmotiVoice}<small>{$store.speakerCatalogLoading ? '读取中' : `结果 ${$store.speakerCatalog.length} 条`}</small>{:else if isDoubaoPreset}<small>{$store.speakerCatalogLoading ? '读取中' : `显示 ${speakerChoices.length} 个内置音色；直接点选即可使用。`}</small>{:else if isQwen3TTS}<small>{qwen3PresetRoute ? '未选择本地/自定义音色时生效。填写声音描述后会改用 VoiceDesign。' : (qwen3VoiceDesignRoute ? '声音描述已接管，预置音色不参与本次生成。' : qwen3PresetDisabledText)}</small>{/if}
					</div>
				</div>
			{/if}
			{#if activeParamKeys.has('prompt')}<div class="field param-field"><label class="param-label" for="vprompt">情绪提示</label><div class="param-control"><select id="vprompt" bind:value={$store.voicePrompt}>{#each promptOptions as o}<option value={o.value}>{o.label}</option>{/each}</select></div></div>{/if}
			{#if isMimoPreset}<div class="field param-field"><label class="param-label" for="mimo-v">MiMo 音色</label><div class="param-control"><select id="mimo-v" bind:value={$store.mimoVoice}>{#each mimoVoiceOptions as o}<option value={o.value}>{o.label}</option>{/each}</select></div></div>{/if}
			{#if activeParamKeys.has('language')}<div class="field param-field"><label class="param-label" for="lang">语言</label><div class="param-control"><select id="lang" bind:value={$store.language}>{#each languageOptions as o}<option value={o.value}>{o.label}</option>{/each}</select></div></div>{/if}
			{#if activeParamKeys.has('voice_design_prompt')}<div class="field param-field span-textarea" class:field-muted={qwen3ReferenceRoute}><label class="param-label" for="vd-prompt">声音描述</label><div class="param-control"><textarea id="vd-prompt" bind:value={$store.voiceDesignPrompt} placeholder={isQwen3TTS ? '例如：温柔的中文女声，吐字清晰，语速稍慢' : '例如：calm British narrator'} disabled={qwen3ReferenceRoute}></textarea>{#if isMimoDesign}<small>描述声音本身：年龄、性别、质感、语速和情绪底色。</small>{:else if isQwen3TTS}<small>{qwen3ReferenceRoute ? '本地音色库或自定义音色复刻时，声音描述不参与本次生成。' : '支持中文或英文。填写后使用 VoiceDesign；留空则使用上面的预置音色。'}</small>{/if}</div></div>{/if}
			{#if activeParamKeys.has('style_instruction')}<div class="field param-field span-textarea" class:doubao-style-field={isDoubao} class:field-muted={isQwen3TTS && (qwen3ReferenceRoute || qwen3VoiceDesignRoute)}><label class="param-label label-with-tooltip" for="style">{styleInstructionLabel}{#if styleInstructionTooltip}<Tooltip content={styleInstructionTooltip} />{/if}</label><div class="param-control"><textarea id="style" bind:value={$store.styleInstruction} placeholder={styleInstructionPlaceholder} disabled={isQwen3TTS && (qwen3ReferenceRoute || qwen3VoiceDesignRoute)}></textarea>{#if isQwen3TTS}<small>{qwen3ReferenceRoute ? '参考音色复刻使用 Base 模型，风格指令不参与本次生成。' : (qwen3VoiceDesignRoute ? '声音描述已使用同一个官方 instruct 槽位，风格指令不会重复提交。' : '支持中文或英文；只影响预置声音路线。留空时后端按官方示例使用 Normal tone。')}</small>{/if}</div></div>{/if}
			{#if isDoubao && activeParamKeys.has('loudness_rate')}<div class="field param-field param-slider doubao-loudness-field"><label class="param-label label-with-tooltip">音量<Tooltip content="官方 loudness_rate：-50 为 0.5 倍，0 为原始音量，100 为 2 倍。" /></label><div class="param-control"><Slider value={$store.doubaoLoudnessRate} min={-50} max={100} step={5} onChange={(value: number) => ($store.doubaoLoudnessRate = value)} /></div></div>{/if}
			{#if isDoubao && activeParamKeys.has('pitch_rate')}<div class="field param-field param-slider doubao-pitch-field"><label class="param-label label-with-tooltip">音调<Tooltip content="官方 additions.post_process.pitch：-12 更低沉，0 为原始音调，12 更明亮。" /></label><div class="param-control"><Slider value={$store.pitchRate} min={-12} max={12} step={1} onChange={(value: number) => ($store.pitchRate = value)} /></div></div>{/if}
			{#if isDoubao && activeParamKeys.has('sample_rate')}<div class="field param-field doubao-sample-rate-field"><label class="param-label label-with-tooltip" for="doubao-sample-rate">采样率<Tooltip content="官方 sample_rate，可选 8–48 kHz。24 kHz 适合普通语音，较高采样率文件更大。" /></label><div class="param-control"><select id="doubao-sample-rate" bind:value={$store.doubaoSampleRate}>{#each doubaoSampleRateOptions as option}<option value={option.value}>{option.label}</option>{/each}</select></div></div>{/if}
			{#if isDoubao && activeParamKeys.has('bit_rate')}<div class="field param-field doubao-bit-rate-field" class:field-muted={$store.outputFormat !== 'mp3'}><label class="param-label label-with-tooltip" for="doubao-bit-rate">MP3 码率<Tooltip content="官方 bit_rate，范围 64–160 kbps；只有格式选择 MP3 时生效。" /></label><div class="param-control"><select id="doubao-bit-rate" bind:value={$store.doubaoBitRate} disabled={$store.outputFormat !== 'mp3'}>{#each doubaoBitRateOptions as option}<option value={option.value}>{option.label}</option>{/each}</select></div></div>{/if}
			{#if isDoubao && activeParamKeys.has('silence_duration')}<div class="field param-field doubao-silence-field"><label class="param-label label-with-tooltip" for="doubao-silence">结尾静音 ms<Tooltip content="官方 additions.silence_duration，范围 0–30000 毫秒。" /></label><div class="param-control"><input id="doubao-silence" type="number" min="0" max="30000" step="100" bind:value={$store.doubaoSilenceDuration} /></div></div>{/if}
			{#if isDoubao && (activeParamKeys.has('enable_subtitle') || activeParamKeys.has('aigc_watermark'))}<div class="field param-field doubao-output-switches"><span class="param-label label-with-tooltip">输出附加<Tooltip content="字级时间戳：随音频返回文字位置，用于字幕或画面对齐。AIGC 声音标识：在音频结尾加入可识别的 AI 生成节奏标识。" /></span><div class="doubao-switch-row">{#if activeParamKeys.has('enable_subtitle')}<Toggle compact checked={$store.doubaoEnableSubtitle} label="字级时间戳" onChange={(checked) => ($store.doubaoEnableSubtitle = checked)} />{/if}{#if activeParamKeys.has('aigc_watermark')}<Toggle compact checked={$store.doubaoAigcWatermark} label="AIGC 声音标识" onChange={(checked) => ($store.doubaoAigcWatermark = checked)} />{/if}</div></div>{/if}
			{#if supportsEmotion}<div class="field param-field"><label class="param-label" for="emo">情绪</label><div class="param-control"><select id="emo" bind:value={$store.emotion}><option value="">跟随参考音色</option><option value="calm">自然</option><option value="happy">高兴</option><option value="sad">悲伤</option><option value="angry">愤怒</option><option value="afraid">恐惧</option><option value="disgusted">反感</option><option value="melancholic">低落</option><option value="surprised">惊讶</option></select></div></div>{#if isIndexTTS && !followsReferenceEmotion}<div class="field param-field param-slider emotion-intensity-field"><label class="param-label label-with-tooltip">情绪强度<Tooltip content="情感强度，0.0=无情感，1.0=最大情感表达" /></label><div class="param-control emotion-intensity-control"><Slider value={$store.emoAlpha} min={0} max={1} step={0.05} onChange={(v: number) => $store.emoAlpha = v} /></div></div>{/if}{/if}
			{#if isOmniVoice && $store.voiceSource === 'voice_library' && !$store.voiceId}<div class="field param-field"><label class="param-label" for="vd">设计标签</label><div class="param-control"><select id="vd" bind:value={$store.voiceDesign}><option value="女，青年，中音调">女，青年，中音调</option><option value="男，青年，中音调">男，青年，中音调</option><option value="女，中年，高音调">女，中年，高音调</option><option value="男，中年，低音调">男，中年，低音调</option><option value="女，青年，耳语">女，青年，耳语</option></select></div></div>{/if}
			{#if genericAdvancedParameterSchema.length > 0}<ParameterPanel autoExpand={true} parameterSchema={genericAdvancedParameterSchema} values={{ temperature: $store.temperature, top_p: $store.topP, top_k: $store.topK, seed: $store.seed, max_text_tokens_per_segment: $store.maxTextTokensPerSegment, interval_silence: $store.intervalSilence, diffusion_steps: $store.diffusionSteps, cfg_rate: $store.cfgRate, guidance_scale: $store.guidanceScale, duration: $store.duration, audio_chunk_duration: $store.audioChunkDuration, audio_chunk_threshold: $store.audioChunkThreshold, max_tokens: $store.maxTokens, cfg_scale: $store.cfgScale, ddpm_steps: $store.ddpmSteps, max_mel_tokens: $store.maxMelTokens, repetition_penalty: $store.repetitionPenalty, nfe_step: $store.nfeStep, cfg_strength: $store.cfgStrength, target_rms: $store.targetRms, cross_fade_duration: $store.crossFadeDuration, sway_sampling_coef: $store.swaySamplingCoef, fix_duration: $store.fixDuration, remove_silence: $store.removeSilence, optimize_text_preview: $store.optimizeTextPreview }} onChange={(k, v) => { const st: Record<string, (x: unknown) => void> = { temperature: (x: unknown) => $store.temperature = x as number, top_p: (x: unknown) => $store.topP = x as number, top_k: (x: unknown) => $store.topK = x as number, seed: (x: unknown) => $store.seed = x === '' || x === null || x === undefined ? null : Number(x), max_text_tokens_per_segment: (x: unknown) => $store.maxTextTokensPerSegment = x as number, interval_silence: (x: unknown) => $store.intervalSilence = x as number, diffusion_steps: (x: unknown) => $store.diffusionSteps = x as number, cfg_rate: (x: unknown) => $store.cfgRate = x as number, guidance_scale: (x: unknown) => $store.guidanceScale = x as number, duration: (x: unknown) => $store.duration = x as number, audio_chunk_duration: (x: unknown) => $store.audioChunkDuration = x as number, audio_chunk_threshold: (x: unknown) => $store.audioChunkThreshold = x as number, max_tokens: (x: unknown) => $store.maxTokens = x as number, cfg_scale: (x: unknown) => $store.cfgScale = x === '' || x === null || x === undefined ? null : Number(x), ddpm_steps: (x: unknown) => $store.ddpmSteps = x === '' || x === null || x === undefined ? null : Number(x), max_mel_tokens: (x: unknown) => $store.maxMelTokens = x as number, repetition_penalty: (x: unknown) => $store.repetitionPenalty = x as number, nfe_step: (x: unknown) => $store.nfeStep = x as number, cfg_strength: (x: unknown) => $store.cfgStrength = x as number, target_rms: (x: unknown) => $store.targetRms = x as number, cross_fade_duration: (x: unknown) => $store.crossFadeDuration = x as number, sway_sampling_coef: (x: unknown) => $store.swaySamplingCoef = x as number, fix_duration: (x: unknown) => $store.fixDuration = x as number, remove_silence: (x: unknown) => $store.removeSilence = x as boolean, optimize_text_preview: (x: unknown) => $store.optimizeTextPreview = x as boolean }; st[k]?.(v); }} />{/if}
		</div>{/if}
		{#if !isSeedAudio}<TextInput bind:text={$store.text} engineId={$store.engineId} ontexttool={(mode: 'clean' | 'numbers' | 'split') => runTextTool(mode)} textToolBusy={$store.textToolBusy} onGenerate={generate} generateBusy={$store.busy} />{/if}
		{#if $store.error}<div class="badge fail">{$store.error}</div>{/if}
			<audio class="result-shared-audio" bind:this={$store.resultPreviewAudio} preload="none" onplaying={handleResultAudioPlaying} ontimeupdate={handleResultAudioTimeUpdate} onended={resetResultPlayback} onerror={handleResultAudioError}></audio>
			{#if seedVoicePickerSlot}
				<div class="gen-modal-backdrop"><div class="seed-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="seed-voice-picker-title">
					<div class="dialog-head"><div><h3 id="seed-voice-picker-title">从音色库添加到 @音频{seedVoicePickerSlot}</h3><p class="muted">如果一个音色有多条样音，请明确选择其中一条。</p></div><button class="gen-icon-btn" type="button" aria-label="关闭" onclick={() => (seedVoicePickerSlot = null)}><X size={15} /></button></div>
					<div class="seed-picker-list">{#each seedVoiceChoices as voice}<article><div><strong>{voice.name}</strong><span>{voice.reference_text || '没有参考台词'}</span></div><div class="seed-file-choices">{#each voice.reference_audio_ids as fileId}<button class="btn compact" type="button" disabled={!['self_voice','authorized','company_authorized'].includes(voice.license_status)} onclick={() => chooseSeedVoice(seedVoicePickerSlot!, voice, fileId)}>{fileId.slice(0, 10)} · 选择这条</button>{/each}</div>{#if !['self_voice','authorized','company_authorized'].includes(voice.license_status)}<small>授权状态不允许上传云端，请先到音色库更新授权。</small>{/if}</article>{/each}</div>
					{#if !seedVoiceChoices.length}<div class="empty">音色库中还没有带参考音频的音色。</div>{/if}
				</div></div>
			{/if}
			{#if seedSpeakerPickerSlot}
				<div class="gen-modal-backdrop"><div class="seed-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="seed-speaker-picker-title">
					<div class="dialog-head"><div><h3 id="seed-speaker-picker-title">选择云端音色到 @音频{seedSpeakerPickerSlot}</h3><p class="muted">可选已训练的豆包复刻音色，也可填写官方 speaker ID。</p></div><button class="gen-icon-btn" type="button" aria-label="关闭" onclick={() => (seedSpeakerPickerSlot = null)}><X size={15} /></button></div>
					{#if seedCloudSpeakerChoices.length}<div class="seed-speaker-choices">{#each seedCloudSpeakerChoices as speaker}<button class="btn" type="button" onclick={() => chooseSeedSpeaker(seedSpeakerPickerSlot!, speaker.id, speaker.name)}><strong>{speaker.name}</strong><span>{speaker.id}</span></button>{/each}</div>{/if}
					<div class="seed-speaker-manual"><label class="field"><span>speaker ID</span><input bind:value={seedSpeakerId} placeholder="例如：zh_female_vv_uranus_bigtts" /></label><label class="field"><span>显示名称（可选）</span><input bind:value={seedSpeakerName} placeholder="例如：温柔女声" /></label></div>
					<div class="dialog-actions"><button class="btn" type="button" onclick={() => (seedSpeakerPickerSlot = null)}>取消</button><button class="btn primary" type="button" disabled={!seedSpeakerId.trim()} onclick={() => chooseSeedSpeaker(seedSpeakerPickerSlot!, seedSpeakerId, seedSpeakerName)}>添加音色</button></div>
				</div></div>
			{/if}
			{#if $store.showLongformDialog && $store.pendingLongformPlan}<div class="gen-modal-backdrop"><div class="longform-dialog" role="dialog" aria-modal="true"><div class="dialog-head"><div><h3>长文本生成策略</h3><p class="muted">预计 {$store.pendingLongformPlan.segments.length} 段。{$store.pendingLongformPlan.planner_reason}</p></div><button class="gen-icon-btn" type="button" aria-label="关闭长文本策略弹窗" data-tooltip="关闭长文本策略弹窗" onclick={() => closeLongformDialog(null)}>×</button></div>{#if $store.pendingLongformPlan.warnings.length}<p class="dialog-warning">{$store.pendingLongformPlan.warnings[0]}</p>{/if}<div class="strategy-grid"><button class:active={$store.longformStrategy === 'split_merge'} type="button" onclick={() => { $store.longformStrategy = 'split_merge'; $store.longformMergeEnabled = true; $store.longformVerifyEnabled = true; }}><strong>分段生成并合并</strong><span>逐段生成，校对后自动合并。</span></button><button class:active={$store.longformStrategy === 'split_only'} type="button" onclick={() => { $store.longformStrategy = 'split_only'; $store.longformMergeEnabled = false; $store.longformVerifyEnabled = false; }}><strong>只分段生成</strong><span>保留每段结果，先人工复听。</span></button><button class:active={$store.longformStrategy === 'single'} type="button" disabled={longformSingleDisabled($store.pendingLongformPlan)} onclick={() => { $store.longformStrategy = 'single'; $store.longformMergeEnabled = false; $store.longformVerifyEnabled = false; }}><strong>{longformSingleDisabled($store.pendingLongformPlan) ? '单条生成已关闭' : '仍然单条生成'}</strong><span>{longformSingleDisabled($store.pendingLongformPlan) ? '文本超过当前引擎安全窗口，请分段生成。' : '最快开始，但容易超时或截断。'}</span></button></div><div class="dialog-options"><label class="check-row"><input type="checkbox" bind:checked={$store.longformVerifyEnabled} disabled={$store.longformStrategy === 'single'} /><span>生成后自动 ASR 校对</span></label><label class="field compact-field"><span>失败重试</span><input type="number" min="0" max="5" bind:value={$store.longformMaxRetries} disabled={$store.longformStrategy === 'single'} /></label></div><div class="dialog-preview">{#each $store.pendingLongformPlan.segments.slice(0, 4) as seg}<p><strong>{seg.index}</strong> {seg.text}</p>{/each}{#if $store.pendingLongformPlan.segments.length > 4}<p class="muted">还有 {$store.pendingLongformPlan.segments.length - 4} 段...</p>{/if}</div><div class="dialog-actions"><button class="btn" type="button" onclick={() => closeLongformDialog(null)}>取消</button><button class="btn primary" type="button" onclick={() => closeLongformDialog($store.longformStrategy)}>确认</button></div></div></div>{/if}
			{#if voiceRegisterOpen}
				<div class="gen-modal-backdrop">
					<div class="voice-register-dialog" role="dialog" aria-modal="true" aria-labelledby="voice-register-dialog-title" tabindex="-1" use:trapDialogFocus={resetVoiceRegisterDialog}>
						<div class="dialog-head">
							<div>
								<h3 id="voice-register-dialog-title"><Plus size={16} /> 注册到音色库</h3>
								<p class="muted">ASR 台词和情绪标签已根据当前音频预填。</p>
							</div>
							<button class="gen-icon-btn" type="button" aria-label="关闭音色注册弹窗" data-tooltip="关闭音色注册弹窗" onclick={resetVoiceRegisterDialog}><X size={15} /></button>
						</div>
						<div class="voice-register-grid">
							<label class="field"><span>名称</span><input bind:value={voiceRegisterName} /></label>
							<label class="field"><span>授权</span><select bind:value={voiceRegisterLicense}><option value="self_voice">本人声音</option><option value="authorized">已授权</option><option value="company_authorized">公司授权</option><option value="test_only">仅测试</option><option value="unknown">未知</option></select></label>
							<label class="field"><span>推荐引擎</span><select bind:value={voiceRegisterEngine}><option value="indextts-v2">IndexTTS v2</option><option value="omnivoice">OmniVoice</option><option value="confucius4-mlx-int8">Confucius4-TTS MLX int8</option><option value="qwen3-tts-mlx-0.6b">Qwen3-TTS MLX 0.6B</option><option value="f5-tts">F5-TTS</option><option value="cosyvoice-zero-shot">CosyVoice Zero-Shot</option><option value="mimo-v2.5-tts-voiceclone">MiMo VoiceClone</option></select></label>
							<label class="field span-full"><span>描述</span><input bind:value={voiceRegisterDescription} /></label>
							<label class="field"><span>标签</span><input bind:value={voiceRegisterTags} /></label>
							<label class="field"><span>情绪标签</span><input bind:value={voiceRegisterEmotionTags} placeholder={voiceRegisterSerBusy ? '情绪识别中' : '例如 calm, happy'} /></label>
							<label class="field span-full"><span>参考文本</span><textarea rows="4" bind:value={voiceRegisterReferenceText}></textarea></label>
						</div>
						{#if voiceRegisterSerBusy}<p class="muted"><Mic size={13} /> 正在识别情绪标签...</p>{/if}
						{#if voiceRegisterError}<p class="muted error-line">{voiceRegisterError}</p>{/if}
						<div class="dialog-actions">
							<button class="btn" type="button" onclick={resetVoiceRegisterDialog}>取消</button>
							<button class="btn primary" type="button" onclick={saveRegisteredVoice} disabled={voiceRegisterBusy || !voiceRegisterName.trim() || !$store.customVoiceFileId}><Save size={14} /> {voiceRegisterBusy ? '保存中' : '保存音色'}</button>
						</div>
					</div>
				</div>
			{/if}
			{#if $store.showSplitPreview && $store.textSegments.length}<div class="split-preview" class:collapsed={$store.splitPreviewCollapsed}><div class="split-preview-head"><div class="split-preview-title"><div class="row wrap split-title-row"><h3>{$store.lastGeneratePlan ? '系统分段计划' : '智能分句预览'}</h3><span class="badge">{$store.textSegments.length} 段</span></div><p>用来提前检查停顿和节奏。{#if $store.lastGeneratePlan}{$store.lastGeneratePlan.planner_reason}{/if}</p></div><button class="gen-icon-btn split-collapse" class:expanded={!$store.splitPreviewCollapsed} type="button" aria-label={$store.splitPreviewCollapsed ? '展开分句预览' : '收起分句预览'} data-tooltip={$store.splitPreviewCollapsed ? '展开分句预览' : '收起分句预览'} onclick={() => ($store.splitPreviewCollapsed = !$store.splitPreviewCollapsed)}><ChevronRight size={15} /></button></div>{#if !$store.splitPreviewCollapsed}<div class="segment-list">{#each $store.textSegments as seg, i}<div class="segment-card"><span class="segment-index">{i + 1}</span><p>{seg}</p></div>{/each}</div>{/if}</div>{/if}
		<div class="result-panel stack section-divider" id="records">
			<section bind:this={recordsViewportEl} class="records-stack">
			<div class="row gen-section-head result-headline">
				<h2>生成记录</h2>
				<div class="gen-segmented compact-tabs records-status-tabs" role="tablist" aria-label="按任务状态筛选">
					<button role="tab" aria-selected={$store.taskStatusTab === 'all'} class:active={$store.taskStatusTab === 'all'} type="button" onclick={() => { $store.taskStatusTab = 'all'; $store.currentPage = 1; }}>全部<span>{statusCounts.all}</span></button>
					<button role="tab" aria-selected={$store.taskStatusTab === 'active'} class:active={$store.taskStatusTab === 'active'} type="button" onclick={() => { $store.taskStatusTab = 'active'; $store.currentPage = 1; }}>队列<span>{statusCounts.active}</span></button>
					<button role="tab" aria-selected={$store.taskStatusTab === 'success'} class:active={$store.taskStatusTab === 'success'} type="button" onclick={() => { $store.taskStatusTab = 'success'; $store.currentPage = 1; }}>成功<span>{statusCounts.success}</span></button>
					<button role="tab" aria-selected={$store.taskStatusTab === 'failed'} class:active={$store.taskStatusTab === 'failed'} type="button" onclick={() => { $store.taskStatusTab = 'failed'; $store.currentPage = 1; }}>异常<span>{statusCounts.failed}</span></button>
				</div>
				<div class="records-head-right">
					<div class="records-row-summary" role="status" aria-live="polite">{#if recordsRefreshing}<span class="badge">加载中</span>{/if}{#if taskCardsStreaming}<span class="badge">显示中 {renderedPagedTasks.length}/{pagedTasks.length}</span>{/if}{#if statusCounts.active}<span class="badge">生成中 {queueCounts.processing}</span><span class="badge">等待 {queueCounts.waiting}</span>{/if}{#if $store.selectedTaskIds.length}<span class="badge ok">已选 {$store.selectedTaskIds.length}</span>{/if}</div>
					<div class="gen-segmented compact-tabs time-tabs" role="tablist" aria-label="按生成时间筛选">
						<button role="tab" aria-selected={$store.taskDateFilter === 'all'} class:active={$store.taskDateFilter === 'all'} type="button" onclick={() => setTaskDateFilter('all')}>全部时间</button>
						<button role="tab" aria-selected={$store.taskDateFilter === 'today'} class:active={$store.taskDateFilter === 'today'} type="button" onclick={() => setTaskDateFilter('today')}>今天</button>
						<button role="tab" aria-selected={$store.taskDateFilter === '7d'} class:active={$store.taskDateFilter === '7d'} type="button" onclick={() => setTaskDateFilter('7d')}>7 天</button>
						<button role="tab" aria-selected={$store.taskDateFilter === '30d'} class:active={$store.taskDateFilter === '30d'} type="button" onclick={() => setTaskDateFilter('30d')}>30 天</button>
					</div>
				</div>
			</div>
			<div class="records-toolbar">
				<div class="toolbar-control-row">
					<div class="records-filter-inline">
						<div class="gen-search-field compact"><Search size={13} /><input bind:value={$store.taskQuery} placeholder="搜索台词、模型、音色、状态" /></div>
						<select class="compact-filter engine-filter" bind:value={$store.taskEngineFilter} onchange={() => ($store.currentPage = 1)}>{#each taskEngineOptions as o}<option value={o}>{o === 'all' ? '全部模型' : o}</option>{/each}</select>
						<select class="compact-filter source-filter" bind:value={$store.taskSourceFilter} onchange={() => ($store.currentPage = 1)}><option value="all">全部来源</option><option value="local">本地</option><option value="cloud">云端</option></select>
						<select class="compact-filter sort-filter" bind:value={$store.taskSortBy} onchange={() => ($store.currentPage = 1)}><option value="latest">最新</option><option value="oldest">最旧</option><option value="duration_desc">时长↓</option></select>
						<select class="compact-filter page-size-filter" aria-label="每页显示记录数量" value={$store.pageSizeAuto ? 'auto' : String($store.pageSize)} onchange={(e) => setTaskPageSizePreset((e.currentTarget as HTMLSelectElement).value)}>
							<option value="auto">自动条数</option>
							{#each resultPageSizePresets as count}<option value={String(count)}>{count} 条</option>{/each}
						</select>
					</div>
					<div class="toolbar-right">
						{#if pageCount > 1}
							<div class="pagination-bar pagination-bar-top">
								<button class="btn icon-text-btn" aria-label="跳到首页" data-tooltip="跳到第一页" onclick={() => taskPageGoTo(1)} disabled={$store.currentPage <= 1}><ChevronsLeft size={15} /></button>
								<button class="btn icon-text-btn" aria-label="上一页" data-tooltip="查看上一页记录" onclick={() => taskPageJump(-1)} disabled={$store.currentPage <= 1}><ChevronLeft size={15} /></button>
								<span class="muted page-info">{$store.currentPage} / {pageCount}</span>
								<button class="btn icon-text-btn" aria-label="下一页" data-tooltip="查看下一页记录" onclick={() => taskPageJump(1)} disabled={$store.currentPage >= pageCount}><ChevronRight size={15} /></button>
								<button class="btn icon-text-btn" aria-label="跳到尾页" data-tooltip="跳到最后一页" onclick={() => taskPageGoTo(pageCount)} disabled={$store.currentPage >= pageCount}><ChevronsRight size={15} /></button>
							</div>
						{/if}
						<div class="toolbar-actions">
							<button class="gen-icon-btn" type="button" aria-label={allVisibleSelected ? '取消选择当前页' : '选择当前页'} data-tooltip={allVisibleSelected ? '取消选择当前页可删除的记录' : '选择当前页可删除的记录'} onclick={toggleVisibleSelection} disabled={!visibleSelectableTasks.length}>{#if allVisibleSelected}<CheckSquare size={15} />{:else}<Square size={15} />{/if}</button>
							<button class="gen-icon-btn danger" type="button" aria-label="删除已选记录" data-tooltip="删除当前已选中的任务记录" onclick={deleteSelectedTasks} disabled={!$store.selectedTaskIds.length}><Trash2 size={15} /></button>
							{#if hasActiveFilters}<button class="gen-icon-btn" type="button" aria-label="清除筛选" data-tooltip="清除搜索、模型、来源和时间筛选" onclick={clearTaskFilters}><X size={15} /></button>{/if}
							<button class="gen-icon-btn" type="button" aria-label="刷新列表" data-tooltip={recordsLastSyncedAt ? `重新加载任务记录，上次同步 ${H.formatTime(recordsLastSyncedAt)}` : '重新加载任务记录和队列状态'} onclick={refreshRecordsData} disabled={recordsRefreshing}><RotateCcw size={15} /></button>
						</div>
					</div>
				</div>
			</div>
			{#if H.queueSummaryText(queueCounts, queueOrderedTasks, engineMap)}<div class="queue-insight"><Info size={14} /><span>{H.queueSummaryText(queueCounts, queueOrderedTasks, engineMap)}</span></div>{/if}
			{#if visibleLongformTasks.length}<section class="longform-list"><div class="row section-subhead"><div><h3>长文本任务</h3><p class="muted">进行中的分段生成、校对、重试和合并父任务。</p></div></div>{#each visibleLongformTasks.slice(0, 6) as t}<article class={`longform-card ${t.status}`}><div class="longform-card-head"><div><strong>{H.longformTitle(t)}</strong><p class="muted">{H.longformStatusText(t)}</p></div><div class="row wrap longform-actions"><span class="badge">{Math.round(t.progress * 100)}%</span>{#if t.export_id}<a class="btn" href={H.longformDownloadUrl(t)}>下载合并音频</a>{/if}{#if t.status === 'failed'}<button class="btn" type="button" onclick={() => retryLongformTask(t)} disabled={$store.actionBusyTaskId === t.longform_task_id}><RotateCcw size={15} /> 重试失败段</button>{/if}<button class="gen-icon-btn mini" type="button" aria-label={longformTaskActionLabel(t)} data-tooltip={longformTaskActionTooltip(t)} onclick={() => handleLongformTaskAction(t)} disabled={$store.actionBusyTaskId === t.longform_task_id}>{#if H.statusIsActive(t.status)}<X size={14} />{:else}<Trash2 size={14} />{/if}</button></div></div><div class="progress-track"><div class="progress-fill" style={`width:${Math.max(3, Math.round(t.progress * 100))}%`}></div></div><div class="longform-segments">{#each t.segments as seg}<div class={`longform-segment ${seg.status}`}><span class="badge">{seg.index}</span><p>{seg.text}</p><span class="badge">{taskStatusLabel(seg.status)}</span>{#if seg.verification}<span class={`badge verify-${seg.verification.status}`}>{H.verificationStatusLabel(seg.verification.status)}</span>{/if}{#if seg.error_message}<span class="muted">{seg.error_message}</span>{/if}</div>{/each}</div>{#if t.error_message}<p class="muted error-line task-error-scroll" use:checkOverflow={t.error_message}>{t.error_message}</p>{/if}</article>{/each}</section>{/if}
			{#if pagedTasks.length}<section class="result-records-block" class:has-longform-above={visibleLongformTasks.length > 0}><div class="result-grid" bind:this={$store.resultGridEl}>{#each renderedPagedTasks as t (t.task_id)}<article class={`card stack result-card engine-surface ${H.engineKind(t.engine_id, engineMap) === 'cloud' ? 'engine-cloud' : 'engine-local'}${$store.playingResultTaskId === t.task_id && ($store.resultAudioPlaying || resultAudioPendingTaskId === t.task_id) ? ' playing' : ''}`}><div class="result-head"><div class="title-row"><input type="checkbox" checked={$store.selectedTaskIds.includes(t.task_id)} disabled={!H.taskCanDelete(t)} onchange={(e) => toggleTaskSelection(t.task_id, (e.currentTarget as HTMLInputElement).checked)} /><strong class="result-title" title={H.displayTitle(t)}>{H.displayTitle(t)}</strong></div><span class="badge result-status" class:ok={t.status === 'success'} class:fail={t.status === 'failed'} class:warn={t.status === 'cancelled' || H.taskIsActive(t)}>{H.taskStatusPillLabel(t, queueCounts, queueOrderedTasks)}</span></div><div class="result-meta result-meta-primary"><span class="badge"><Mic size={11} /> {H.voiceBadgeLabel(t, voiceMap, engineMap)}</span><HoverCopyPopover label="台词" title="台词内容" copyText={H.displayTitle(t)}><button type="button" class="badge result-script-chip" aria-label="查看台词内容"><FileText size={13} /> 台词</button></HoverCopyPopover>{#if H.longformResultLabel(t)}<span class="badge longform-result-badge" class:merged={H.taskIsLongformExport(t)} title={H.longformResultTitle(t)}>{H.longformResultLabel(t)}</span>{/if}</div><div class="result-meta result-meta-secondary"><span class="badge engine"><Cpu size={11} /> {engineMap.get(t.engine_id)?.manifest.display_name ?? t.engine_id}</span><span class="badge badge-kind">{H.engineTypeLabel(t.engine_id, engineMap)}</span>{#if t.created_at}<span class="badge meta-pop" data-text={`生成时间：${H.formatTime(t.created_at)}`}>{H.formatTime(t.created_at)}</span>{/if}</div><div class="row result-info"><p class="muted result-subline">{H.taskTimingLine(t)}</p><div class="row wrap result-info-right">{#if t.result_duration_ms}<span class="badge meta-pop" data-text={`音频时长：${H.formatAudioDuration(t.result_duration_ms)}`}>{H.formatAudioDuration(t.result_duration_ms)}</span>{/if}{#if H.taskIsActive(t)}<button class="gen-icon-btn danger" aria-label="取消任务" data-tooltip={taskCancelTooltip(t)} onclick={() => cancelTask(t)} disabled={$store.actionBusyTaskId === t.task_id}><X size={14} /></button>{/if}<HoverCopyPopover label="参数" title="生成参数" copyText={H.taskParameterCopyText(t, engineMap, voiceMap)}><button type="button" class="param-pop compact" aria-label="查看生成参数"><SlidersHorizontal size={13} /></button></HoverCopyPopover></div></div>{#if H.taskIsActive(t)}<div class="progress-block"><div class="row progress-head"><span class="muted">{H.taskStageLabel(t, queueOrderedTasks)}</span><span class="badge">{H.progressLabel(t, queueOrderedTasks)}</span></div><div class="progress-track" class:waiting-track={H.taskIsWaiting(t)}><div class="progress-fill" class:waiting-fill={H.taskIsWaiting(t)} style={`width:${H.taskProgressWidth(t)}%`}></div></div><div class="row wrap progress-foot"><span class="muted">{#if H.taskIsWaiting(t)}已等待 {H.formatSeconds(H.waitingSeconds(t))}{:else}已运行 {H.elapsedLabel(t)}{/if}</span>{#if H.taskEtaLabel(t)}<span class="muted">{H.taskEtaLabel(t)}</span>{/if}</div>{#if H.taskRuntimeHint(t, queueCounts, queueOrderedTasks)}<p class="progress-hint">{H.taskRuntimeHint(t, queueCounts, queueOrderedTasks)}</p>{/if}</div>{/if}{#if t.result_id}{@const downloadName = H.resultDownloadNameForScope(t, $store.tasks, taskDownloadSequences[t.task_id])}<div class="result-audio-row"><WaveformResultPlayer task={t} audioUrl={resultAudioUrl(t)} peaksUrl={`/api/history/${encodeURIComponent(t.result_id)}/waveform`} downloadUrl={resultDownloadUrl(t, downloadName)} {downloadName} durationLabel={t.result_duration_ms ? H.formatAudioDuration(t.result_duration_ms) : '播放结果'} isPlaying={$store.playingResultTaskId === t.task_id && $store.resultAudioPlaying} isPending={resultAudioPendingTaskId === t.task_id} currentTime={$store.playingResultTaskId === t.task_id ? resultAudioCurrentTime : 0} onPlay={() => toggleResultPlayback(t)} onStop={() => toggleResultPlayback(t)} onSeek={(task, timeSeconds) => seekResultPlayback(task, timeSeconds)} /></div>{/if}{#if t.error_message}<p class="muted error-line task-error-scroll" use:checkOverflow={H.knownErrorMessage(t.error_message)}>{H.knownErrorMessage(t.error_message)}</p>{/if}<div class="result-footer" class:without-audio={!t.result_id}><div class="result-footer-status">{#if taskVerificationReport(t)}{@const r = taskVerificationReport(t)}<div class="verification-line {r.status}"><span class="dot"></span>{H.verificationStatusLabel(r.status)}{#if r.status !== 'skipped'}<span class="coverage">覆盖率 {Math.round(r.coverage * 100)}%</span>{/if}</div>{:else if taskVerificationPending(t)}<p class="muted verification-pending-line">自动校对中…</p>{:else if taskVerificationError(t)}<p class="muted error-line">{H.knownErrorMessage(taskVerificationError(t))}</p>{/if}</div><div class="row wrap result-card-actions">{#if t.status === 'failed'}<button class="gen-icon-btn" aria-label="重试任务" data-tooltip="使用原参数重新尝试生成" onclick={() => retryTask(t)} disabled={$store.actionBusyTaskId === t.task_id}><RotateCcw size={15} /></button>{/if}{#if H.taskCanDelete(t)}<button class="gen-icon-btn" aria-label="复用参数" data-tooltip="把这条记录的文本和参数带回上方生成区" onclick={() => reuse(t)} disabled={$store.actionBusyTaskId === t.task_id}><Repeat size={15} /></button>{/if}{#if !H.taskIsActive(t)}<button class="gen-icon-btn danger" aria-label="删除记录" data-tooltip="删除这条任务记录" onclick={() => deleteTaskRecord(t)} disabled={$store.actionBusyTaskId === t.task_id}><Trash2 size={15} /></button>{/if}</div></div></article>{/each}</div>{#if pageCount > 1}<div bind:this={recordsBottomPagerEl} class="pagination-bar"><button class="btn" onclick={() => $store.currentPage = 1} disabled={$store.currentPage <= 1}><ChevronsLeft size={15} /> 首页</button><button class="btn" onclick={() => taskPageJump(-1)} disabled={$store.currentPage <= 1}><ChevronLeft size={15} /> 上一页</button><span class="muted">第 {$store.currentPage} / {pageCount} 页</span><div class="page-jump"><span class="muted">跳至</span><input type="number" min="1" max={pageCount} bind:value={$store.pageJumpInput} onkeydown={(e) => e.key === 'Enter' && jumpToPage()} /><span class="muted">页</span></div><button class="btn" onclick={() => taskPageJump(1)} disabled={$store.currentPage >= pageCount}>下一页 <ChevronRight size={15} /></button><button class="btn" onclick={() => $store.currentPage = pageCount} disabled={$store.currentPage >= pageCount}>尾页 <ChevronsRight size={15} /></button></div>{/if}</section>{:else}<div class="empty">{#if recordsInitialized}当前筛选下没有任务记录。{:else}正在加载任务记录...{/if}</div>{/if}
			</section>
			</div>
		</div>
	</div>
</main>

<style>@import './+page.css';</style>
