<script lang="ts">
	import { Api } from '$lib/api';
	import type { VoiceAsset, VoiceClipResponse } from '$lib/api/types';
	import Slider from '$lib/components/shared/Slider.svelte';
	import Toggle from '$lib/components/shared/Toggle.svelte';
	import VoiceSelector from '../../components/VoiceSelector.svelte';
	import ReferenceAudioRangeEditor from '../reference-audio/ReferenceAudioRangeEditor.svelte';
	import { createReferenceAudioDraft, withReferenceAudioTrim } from '../reference-audio/draft';
	import {
		activeEmotionReferenceSlot,
		setEmotionReferenceSlot,
		setEmotionReferenceSource,
		type EmotionReferenceSource,
		type IndexTtsEmotionState
	} from './state';
	import { validateIndexTtsEmotionState } from './validation';

	interface Props {
		state: IndexTtsEmotionState;
		voices: VoiceAsset[];
		mainVoiceName?: string;
		onChange?: (state: IndexTtsEmotionState) => void;
	}

	let { state: value, voices, mainVoiceName = '当前音色', onChange = () => {} }: Props = $props();
	let fileInput: HTMLInputElement = $state()!;
	let busy = $state(false);
	let localError = $state('');
	let historyHydrationKey = '';
	const eligibleVoices = $derived(voices.filter((voice) => voice.reference_audio_ids.length > 0));
	const activeSlot = $derived(activeEmotionReferenceSlot(value));
	const activeDraft = $derived(activeSlot.draft);
	const sourceUrl = $derived(activeDraft?.source.previewUrl || activeDraft?.clip.previewUrl || '');
	const durationMs = $derived(activeDraft?.source.durationMs ?? 0);
	const validation = $derived(validateIndexTtsEmotionState(value));
	const selectedMs = $derived(activeDraft?.trim.startMs != null && activeDraft.trim.endMs != null ? activeDraft.trim.endMs - activeDraft.trim.startMs : 0);

	function change(next: IndexTtsEmotionState) { localError = ''; onChange(next); }
	function fileUrl(fileId: string) { return `/api/voices/files/${encodeURIComponent(fileId)}/audio`; }
	function fileIdFromPath(path: string) { return (path.split('/').pop() ?? '').replace(/\.[^.]+$/, ''); }
	function formatRange() {
		if (!activeDraft || activeDraft.trim.startMs == null || activeDraft.trim.endMs == null) return '未选择片段';
		return `${(activeDraft.trim.startMs / 1000).toFixed(1)}–${(activeDraft.trim.endMs / 1000).toFixed(1)} 秒`;
	}
	async function audioDuration(url: string) {
		return await new Promise<number>((resolve, reject) => {
			const audio = new Audio();
			audio.preload = 'metadata';
			audio.onloadedmetadata = () => resolve(Math.round(audio.duration * 1000));
			audio.onerror = () => reject(new Error('无法读取音频时长'));
			audio.src = url;
		});
	}
	async function hydrateLibraryHistory(voice: VoiceAsset, audioId: string, draft: NonNullable<typeof activeDraft>) {
		const sourcePreviewUrl = fileUrl(audioId);
		const clipPreviewUrl = draft.clip.previewUrl || fileUrl(draft.clip.fileId);
		try {
			const [length] = await Promise.all([
				audioDuration(sourcePreviewUrl),
				clipPreviewUrl ? audioDuration(clipPreviewUrl) : Promise.reject(new Error('历史情绪片段缺少文件 ID'))
			]);
			const nextDraft = createReferenceAudioDraft(draft.draftId, {
				...draft,
				sourceKind: 'voice_library',
				source: { ...draft.source, fileId: audioId, fileName: voice.name, previewUrl: sourcePreviewUrl, durationMs: length },
				clip: { ...draft.clip },
				trim: { ...draft.trim }
			});
			change(setEmotionReferenceSlot(value, 'voice_library', { ...value.library, audioId, displayName: voice.name, draft: nextDraft }));
		} catch (error) {
			localError = `历史情绪参考无法恢复，请重新选择音色：${(error as Error).message || '文件已被清理'}`;
		}
	}
	async function chooseLibraryVoice(voiceId: string) {
		const voice = eligibleVoices.find((item) => item.voice_id === voiceId);
		const audioId = voice?.reference_audio_ids[0] ?? '';
		if (!voice || !audioId) return;
		busy = true; localError = '';
		try {
			const previewUrl = fileUrl(audioId);
			const length = await audioDuration(previewUrl);
			const draft = createReferenceAudioDraft(`emotion-library-${voice.voice_id}-${audioId}`, {
				sourceKind: 'voice_library',
				source: { fileId: audioId, fileName: voice.name, previewUrl, durationMs: length },
				trim: { startMs: 0, endMs: length },
				selectionDirty: true
			});
			change(setEmotionReferenceSlot(value, 'voice_library', { voiceId, audioId, displayName: voice.name, draft }));
		} catch (error) { localError = (error as Error).message || '这条样音无法读取，请选择其他音色。'; }
		finally { busy = false; }
	}
	async function upload(file: File) {
		busy = true; localError = '';
		try {
			const uploaded = await Api.uploadVoice(file);
			const previewUrl = fileUrl(uploaded.file_id);
			const length = uploaded.duration_ms ?? await audioDuration(previewUrl);
			const draft = createReferenceAudioDraft(`emotion-upload-${uploaded.file_id}`, {
				sourceKind: 'upload',
				source: { fileId: uploaded.file_id, fileName: uploaded.source_filename || uploaded.filename, path: uploaded.path, previewUrl, durationMs: length, sizeBytes: uploaded.size_bytes ?? file.size, mimeType: file.type },
				trim: { startMs: 0, endMs: length },
				selectionDirty: true,
				qualityWarnings: uploaded.quality.warnings
			});
			change(setEmotionReferenceSlot(value, 'upload', { voiceId: '', audioId: uploaded.file_id, displayName: uploaded.source_filename || uploaded.filename, draft }));
		} catch (error) { localError = (error as Error).message || '情绪参考音频上传失败。'; }
		finally { busy = false; if (fileInput) fileInput.value = ''; }
	}
	function updateRange(startMs: number, endMs: number) {
		if (!activeDraft) return;
		const nextDraft = withReferenceAudioTrim(activeDraft, startMs, endMs);
		change(setEmotionReferenceSlot(value, value.source, { ...activeSlot, draft: { ...nextDraft, selectionDirty: true } }));
	}
	async function applyRange() {
		if (!activeDraft || activeDraft.trim.startMs == null || activeDraft.trim.endMs == null) return;
		const fileId = activeDraft.source.fileId || activeSlot.audioId || fileIdFromPath(activeDraft.source.path);
		if (!fileId) { localError = '原始情绪样音已被清理，请重新选择。'; return; }
		busy = true; localError = '';
		try {
			const clip: VoiceClipResponse = await Api.clipVoice(fileId, { start_ms: activeDraft.trim.startMs, end_ms: activeDraft.trim.endMs });
			const length = clip.voice_file.duration_ms ?? selectedMs;
			const nextDraft = createReferenceAudioDraft(activeDraft.draftId, {
				...activeDraft,
				source: { ...activeDraft.source },
				clip: { fileId: clip.file_id, fileName: clip.filename, path: clip.path, previewUrl: fileUrl(clip.file_id), durationMs: length, sizeBytes: clip.voice_file.size_bytes, mimeType: clip.voice_file.mime_type || 'audio/wav' },
				trim: { ...activeDraft.trim },
				confirmed: true,
				selectionDirty: false,
				qualityWarnings: clip.quality.warnings ?? []
			});
			change(setEmotionReferenceSlot(value, value.source, { ...activeSlot, draft: nextDraft }));
		} catch (error) { localError = (error as Error).message || '情绪片段处理失败，请重试。'; }
		finally { busy = false; }
	}
	function setSource(source: EmotionReferenceSource) { change(setEmotionReferenceSource(value, source)); }

	$effect(() => {
		const slot = value.library;
		const draft = slot.draft;
		if (!value.enabled || value.source !== 'voice_library' || !slot.voiceId || draft?.sourceKind !== 'history') return;
		const voice = eligibleVoices.find((item) => item.voice_id === slot.voiceId);
		if (!voice) { localError = '历史任务引用的情绪音色已不存在，请重新选择。'; return; }
		const audioId = voice.reference_audio_ids[0] ?? '';
		if (!audioId) { localError = '历史任务引用的情绪音色没有可用本地样音，请重新选择。'; return; }
		const key = `${slot.voiceId}:${audioId}:${draft.clip.fileId || draft.clip.path}`;
		if (historyHydrationKey === key) return;
		historyHydrationKey = key;
		void hydrateLibraryHistory(voice, audioId, draft);
	});
</script>

<section class="emotion-reference" aria-label="IndexTTS 情绪表现">
	<div class="toggle-row">
		<div><strong>使用独立情绪参考</strong><span>{value.enabled ? `声音仍使用「${mainVoiceName}」，只从下面片段提取情绪。` : `未开启：音色和情绪都跟随「${mainVoiceName}」。`}</span></div>
		<Toggle compact checked={value.enabled} label={value.enabled ? '已开启' : '已关闭'} onChange={(enabled) => change({ ...value, enabled })} />
	</div>
	{#if value.enabled}
		<div class="source-and-strength">
			<div class="source-tabs" role="tablist" aria-label="情绪参考来源">
				<button role="tab" aria-selected={value.source === 'voice_library'} class:active={value.source === 'voice_library'} type="button" onclick={() => setSource('voice_library')}>音色库</button>
				<button role="tab" aria-selected={value.source === 'upload'} class:active={value.source === 'upload'} type="button" onclick={() => setSource('upload')}>上传文件</button>
			</div>
			<label class="strength"><span>情绪参考强度</span><Slider value={value.alpha} min={0} max={1} step={0.05} onChange={(alpha) => change({ ...value, alpha })} /></label>
		</div>
		<div class="source-panel" role="tabpanel">
			{#if value.source === 'voice_library'}
				<div class="library-row"><VoiceSelector voices={eligibleVoices} value={value.library.voiceId} onChange={chooseLibraryVoice} /><span>{eligibleVoices.length ? '默认使用所选音色的第一条本地样音' : '音色库中还没有带本地样音的音色'}</span></div>
			{:else}
				<button class="upload-button" type="button" onclick={() => fileInput.click()} disabled={busy}>{activeSlot.displayName ? `替换：${activeSlot.displayName}` : '上传情绪参考音频或视频'}</button>
				<input bind:this={fileInput} class="file-input" type="file" accept="audio/*,video/mp4,video/quicktime,video/webm,video/x-matroska,.wav,.mp3,.m4a,.flac,.aac,.ogg,.opus,.mp4,.mov,.m4v,.webm,.mkv" onchange={(event) => { const file = event.currentTarget.files?.[0]; if (file) void upload(file); }} />
			{/if}
			{#if activeDraft}
				<div class="combination" aria-live="polite"><b>声音：{mainVoiceName}</b><span>+</span><b>情绪：{activeSlot.displayName || '参考片段'} · {formatRange()}</b></div>
				<ReferenceAudioRangeEditor ariaLabel="情绪参考片段时间线" purposeLabel="情绪片段" sourceUrl={sourceUrl} durationMs={durationMs} startMs={activeDraft.trim.startMs ?? 0} endMs={activeDraft.trim.endMs ?? durationMs} {busy} dirty={!activeDraft.confirmed || activeDraft.selectionDirty} onRangeChange={updateRange} onApply={applyRange} />
			{/if}
			{#if localError}<p class="message error" role="alert">{localError}</p>{:else if validation.errors.length}<p class="message error">{validation.errors[0]}</p>{:else if validation.warnings.length}<p class="message warning">{validation.warnings[0]}</p>{:else if activeDraft?.qualityWarnings[0]}<p class="message warning">{activeDraft.qualityWarnings[0]}</p>{/if}
		</div>
	{/if}
</section>

<style>
	.emotion-reference { grid-column: 1 / -1; display: grid; gap: 9px; min-width: 0; padding-top: 8px; border-top: 1px solid color-mix(in srgb, var(--line) 82%, transparent); }
	.toggle-row, .source-and-strength, .library-row, .combination { display: flex; align-items: center; }
	.toggle-row { justify-content: space-between; gap: 16px; }
	.toggle-row strong, .toggle-row span { display: block; }
	.toggle-row strong { color: var(--text); font-size: 12px; }
	.toggle-row span { margin-top: 2px; color: var(--muted); font-size: 10.5px; }
	.source-and-strength { justify-content: space-between; gap: 14px; }
	.source-tabs { display: inline-grid; grid-template-columns: repeat(2, minmax(88px, 1fr)); padding: 2px; border: 1px solid var(--line); border-radius: 7px; background: var(--bg); }
	.source-tabs button { min-height: 28px; border: 0; border-radius: 5px; background: transparent; padding: 4px 9px; color: var(--muted); font: inherit; font-size: 11px; }
	.source-tabs button.active { background: #273646; color: #eef6ff; }
	.strength { width: min(330px, 48%); display: grid; grid-template-columns: 96px minmax(0, 1fr); align-items: center; gap: 8px; color: var(--muted); font-size: 10.5px; }
	.strength :global(.slider) { grid-template-columns: minmax(90px, 1fr) 40px; }
	.source-panel { display: grid; gap: 9px; min-width: 0; }
	.library-row { gap: 9px; min-width: 0; }
	.library-row :global(.select) { width: min(390px, 60%); }
	.library-row > span { color: var(--muted); font-size: 10.5px; }
	.upload-button { min-height: 34px; justify-self: start; border: 1px dashed #536579; border-radius: 7px; background: #101721; padding: 6px 12px; color: var(--text); font: inherit; font-size: 11px; cursor: pointer; }
	.file-input { display: none; }
	.combination { justify-content: center; gap: 9px; min-height: 30px; border-radius: 6px; background: rgba(79, 156, 249, .08); color: #d8e7f8; font-size: 11px; }
	.combination span { color: #7faee6; font-weight: 700; }
	.message { margin: 0; font-size: 10.5px; }
	.message.error { color: #ef9a92; }
	.message.warning { color: #d7bd83; }
	@media (max-width: 640px) { .toggle-row, .source-and-strength, .library-row { align-items: stretch; flex-direction: column; } .source-tabs, .strength, .library-row :global(.select) { width: 100%; } .strength { grid-template-columns: 1fr; } .source-tabs button, .upload-button { min-height: 44px; } .combination { align-items: flex-start; flex-direction: column; gap: 2px; padding: 8px; } .combination span { display: none; } }
</style>
