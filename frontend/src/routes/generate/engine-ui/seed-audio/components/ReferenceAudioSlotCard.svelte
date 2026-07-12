<script lang="ts">
	import { FileAudio, Library, Mic2, Pencil, Play, RefreshCw, Trash2, Upload } from 'lucide-svelte';
	import type { SeedAudioReferenceSlot } from '../state';

	interface Props {
		slot: SeedAudioReferenceSlot;
		onUpload?: (slot: 1 | 2 | 3, file: File) => void;
		onChooseVoice?: (slot: 1 | 2 | 3) => void;
		onChooseSpeaker?: (slot: 1 | 2 | 3) => void;
		onEdit?: (slot: 1 | 2 | 3) => void;
		onPreview?: (slot: 1 | 2 | 3) => void;
		onRemove?: (slot: 1 | 2 | 3) => void;
	}

	let {
		slot,
		onUpload = () => {},
		onChooseVoice = () => {},
		onChooseSpeaker = () => {},
		onEdit = () => {},
		onPreview = () => {},
		onRemove = () => {}
	}: Props = $props();
	let input: HTMLInputElement;
	let dragActive = $state(false);

	function useFiles(files: FileList | null) {
		const file = files?.[0];
		if (file) onUpload(slot.slot, file);
		if (input) input.value = '';
	}

	function handleDrop(event: DragEvent) {
		event.preventDefault();
		dragActive = false;
		useFiles(event.dataTransfer?.files ?? null);
	}

	function durationLabel(durationMs: number | null | undefined) {
		return durationMs ? `${(durationMs / 1000).toFixed(1)} 秒` : '时长待检测';
	}
	const uploadAuthorized = $derived(slot.asset?.type === 'speaker' || ['self_voice', 'authorized', 'company_authorized'].includes(slot.asset?.licenseStatus ?? ''));
	function licenseLabel(status: string) {
		if (status === 'self_voice') return '本人声音';
		if (status === 'authorized') return '已授权';
		if (status === 'company_authorized') return '公司授权';
		return '未授权云端上传';
	}
</script>

<article
	class:filled={Boolean(slot.asset)}
	class:drag-active={dragActive}
	class="reference-slot"
	role="group"
	aria-label={`参考声音槽位 @音频${slot.slot}`}
	ondragover={(event) => { event.preventDefault(); dragActive = true; }}
	ondragleave={() => (dragActive = false)}
	ondrop={handleDrop}
>
	<header>
		<span class="reference-token">@音频{slot.slot}</span>
		{#if slot.asset}<span class="source-badge" class:blocked={!uploadAuthorized}>{slot.asset.source === 'cloud_speaker' ? '云端音色' : `${slot.asset.source === 'voice_library' ? '音色库' : slot.asset.source === 'preset' ? '预设' : '自定义'} · ${licenseLabel(slot.asset.licenseStatus)}`}</span>{/if}
	</header>

	{#if slot.asset}
		<div class="asset-summary">
			<div class="asset-icon">{#if slot.asset.type === 'speaker'}<Mic2 size={18} />{:else}<FileAudio size={18} />{/if}</div>
			<div>
				<strong>{slot.asset.displayName}</strong>
				<span>{slot.asset.type === 'speaker' ? slot.asset.speakerId : durationLabel(slot.asset.referenceAudio?.clip.durationMs)}</span>
			</div>
		</div>
		<div class="slot-actions">
			<button type="button" onclick={() => onPreview(slot.slot)} aria-label={`试听 @音频${slot.slot}`}><Play size={14} />试听</button>
			{#if slot.asset.type === 'audio'}<button type="button" onclick={() => onEdit(slot.slot)}><Pencil size={14} />编辑片段</button>{/if}
			<button type="button" onclick={() => input.click()}><RefreshCw size={14} />替换</button>
			<button class="danger" type="button" onclick={() => onRemove(slot.slot)} aria-label={`删除 @音频${slot.slot}`}><Trash2 size={14} /></button>
		</div>
		{#if !uploadAuthorized}<p class="license-error">该素材不能上传云端，请替换为本人声音或已获授权的素材。</p>{/if}
	{:else}
		<div class="empty-copy">
			<strong>添加第 {slot.slot} 条参考声音</strong>
			<span>拖入 WAV、MP3、PCM 或 OGG，单条不超过 30 秒 / 10MB</span>
		</div>
		<div class="source-actions">
			<button type="button" onclick={() => onChooseVoice(slot.slot)}><Library size={15} />从音色库添加</button>
			<button type="button" onclick={() => onChooseSpeaker(slot.slot)}><Mic2 size={15} />选择云端音色</button>
			<button type="button" onclick={() => input.click()}><Upload size={15} />上传音频</button>
		</div>
	{/if}

	<input bind:this={input} class="file-input" type="file" accept="audio/wav,audio/mpeg,audio/ogg,audio/opus,.pcm,.wav,.mp3,.ogg,.opus" onchange={(event) => useFiles(event.currentTarget.files)} />
</article>

<style>
	.reference-slot { border: 1px dashed var(--line); border-radius: 10px; background: var(--bg); padding: 12px; transition: border-color 120ms ease, background 120ms ease; }
	.reference-slot.filled { border-style: solid; border-color: #354353; }
	.reference-slot.drag-active { border-color: #77aef4; background: #152131; }
	header, .slot-actions, .source-actions, .asset-summary { display: flex; align-items: center; }
	header { justify-content: space-between; gap: 8px; margin-bottom: 11px; }
	.reference-token { border-radius: 5px; background: #243a52; padding: 3px 7px; color: #b8d8ff; font: 700 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
	.source-badge { color: #8f9dad; font-size: 11px; }
	.source-badge.blocked { color: #ef9a92; }
	.asset-summary { gap: 9px; min-width: 0; }
	.asset-icon { display: grid; flex: 0 0 36px; height: 36px; place-items: center; border-radius: 8px; background: #202a35; color: #83b7f7; }
	.asset-summary div:last-child { min-width: 0; }
	.asset-summary strong, .asset-summary span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.asset-summary strong { color: var(--text); font-size: 13px; }
	.asset-summary span, .empty-copy span { margin-top: 3px; color: var(--muted); font-size: 11px; }
	.empty-copy { min-height: 48px; }
	.empty-copy strong { display: block; color: #cdd7e3; font-size: 12px; }
	.empty-copy span { display: block; line-height: 1.45; }
	.slot-actions, .source-actions { flex-wrap: wrap; gap: 6px; margin-top: 12px; }
	button { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel-2); padding: 6px 8px; color: var(--text); font: inherit; font-size: 11px; cursor: pointer; }
	button:hover:not(:disabled), button:focus-visible:not(:disabled) { border-color: #77aef4; color: #eef6ff; outline: none; }
	button.danger { margin-left: auto; color: #eaa19a; }
	.license-error { margin: 8px 0 0; color: #ef9a92; font-size: 10.5px; line-height: 1.4; }
	.file-input { display: none; }
</style>
