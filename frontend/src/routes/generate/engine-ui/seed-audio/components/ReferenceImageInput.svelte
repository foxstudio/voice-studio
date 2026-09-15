<script lang="ts">
	import { Image, RefreshCw, Trash2, Upload } from 'lucide-svelte';
	import type { SeedAudioImageAsset } from '../state';

	interface Props {
		image: SeedAudioImageAsset | null;
		onUpload?: (file: File) => void;
		onRemove?: () => void;
	}
	let { image, onUpload = () => {}, onRemove = () => {} }: Props = $props();
	let input: HTMLInputElement;
	let dragActive = $state(false);
	function useFiles(files: FileList | null) { const file = files?.[0]; if (file) onUpload(file); if (input) input.value = ''; }
	function drop(event: DragEvent) { event.preventDefault(); dragActive = false; useFiles(event.dataTransfer?.files ?? null); }
</script>

<div class="image-input" class:filled={Boolean(image)} class:drag-active={dragActive} role="group" aria-label="参考图片上传区" ondragover={(event) => { event.preventDefault(); dragActive = true; }} ondragleave={() => (dragActive = false)} ondrop={drop}>
	{#if image}
		{#if image.previewUrl}<img src={image.previewUrl} alt="参考图片预览" />{:else}<div class="image-placeholder"><Image size={28} /></div>{/if}
		<div class="image-copy"><strong>{image.displayName}</strong><span>{image.mimeType || '格式待检测'} · {image.sizeBytes ? `${(image.sizeBytes / 1024 / 1024).toFixed(2)}MB` : '大小待检测'}</span></div>
		<div class="image-actions"><button type="button" onclick={() => input.click()}><RefreshCw size={14} />替换图片</button><button class="danger" type="button" onclick={onRemove}><Trash2 size={14} />删除</button></div>
	{:else}
		<div class="image-placeholder"><Image size={28} /></div>
		<div class="image-copy"><strong>拖入一张场景或角色图片</strong><span>支持 JPEG、PNG、WebP，不超过 10MB。上传即确认你有权将图片用于云端生成。</span></div>
		<button type="button" onclick={() => input.click()}><Upload size={14} />选择图片</button>
	{/if}
	<input bind:this={input} type="file" accept="image/jpeg,image/png,image/webp" onchange={(event) => useFiles(event.currentTarget.files)} />
</div>

<style>
	.image-input { display: grid; grid-template-columns: 72px minmax(0, 1fr) auto; align-items: center; gap: 12px; min-height: 92px; border: 1px dashed var(--line); border-radius: 10px; background: var(--bg); padding: 10px; }
	.image-input.filled { border-style: solid; }.image-input.drag-active { border-color: #77aef4; background: #152131; }
	img, .image-placeholder { width: 72px; height: 72px; border-radius: 8px; object-fit: cover; }
	.image-placeholder { display: grid; place-items: center; background: #202a35; color: #83b7f7; }
	.image-copy { min-width: 0; }.image-copy strong,.image-copy span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.image-copy strong { color: var(--text); font-size: 13px; }.image-copy span { margin-top: 4px; color: var(--muted); font-size: 11px; }
	.image-actions { display: flex; gap: 6px; }button { display: inline-flex; align-items: center; gap: 5px; border: 1px solid #354150; border-radius: 6px; background: #181e25; padding: 7px 9px; color: #c5d0dc; font: inherit; font-size: 11px; cursor: pointer; }button:hover:not(:disabled),button:focus-visible:not(:disabled) { border-color: #77aef4; color: #eef6ff; outline: none; }button.danger { color: #eaa19a; }input { display: none; }
	@media (max-width: 620px) { .image-input { grid-template-columns: 58px minmax(0, 1fr); }.image-input > button,.image-actions { grid-column: 1 / -1; }.image-actions { display: flex; }.image-placeholder,img { width: 58px; height: 58px; } }
</style>
