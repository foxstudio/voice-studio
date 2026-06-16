<script lang="ts">
	import type { Project, VideoLocalizationDraft, VideoLocalizationOperation } from '$lib/api/types';
	import { Film, UploadCloud } from 'lucide-svelte';
	import { durationLabel, isActiveOperation, operationBadgeClass, operationStatusLabel } from './utils';

	let {
		draft,
		selectedProject,
		latestOperation,
		hasActiveOperation,
		operationActionId,
		extractingAudio,
		separatingStems,
		transcribingAsr,
		onImportVideo,
		onExtractAudio,
		onSeparateStems,
		onTranscribeEnglish,
		onCancelOperation,
		onRetryOperation,
		operationFor,
		operationBusy
	}: {
		draft: VideoLocalizationDraft | null;
		selectedProject: Project | null;
		latestOperation: VideoLocalizationOperation | null;
		hasActiveOperation: boolean;
		operationActionId: string;
		extractingAudio: boolean;
		separatingStems: boolean;
		transcribingAsr: boolean;
		onImportVideo: (file: File) => void;
		onExtractAudio: () => void;
		onSeparateStems: () => void;
		onTranscribeEnglish: () => void;
		onCancelOperation: (operation: VideoLocalizationOperation) => void;
		onRetryOperation: (operation: VideoLocalizationOperation) => void;
		operationFor: (kind: VideoLocalizationOperation['kind']) => VideoLocalizationOperation | null;
		operationBusy: (kind: VideoLocalizationOperation['kind']) => boolean;
	} = $props();

	let dragActive = $state(false);

	function handleDrop(event: DragEvent) {
		event.preventDefault();
		dragActive = false;
		const file = event.dataTransfer?.files?.[0];
		if (file) onImportVideo(file);
	}
</script>

<section class="panel import-panel">
	<div class="section-title">
		<h2>素材与模型</h2>
		<span class={`badge ${draft?.updated_at ? 'ok' : ''}`}>{draft?.updated_at ? '草稿已保存' : '等待保存'}</span>
	</div>
	<button
		class:drag-active={dragActive}
		class="drop-target"
		type="button"
		ondragenter={(event) => {
			event.preventDefault();
			dragActive = true;
		}}
		ondragover={(event) => event.preventDefault()}
		ondragleave={() => (dragActive = false)}
		ondrop={handleDrop}
		onclick={() => document.querySelector<HTMLInputElement>('[data-video-localization-file]')?.click()}
	>
		{#if draft?.source_media.filename}
			<Film size={22} />
		{:else}
			<UploadCloud size={22} />
		{/if}
		<div>
			<strong>{draft?.source_media.filename || '拖入或点击导入英文视频'}</strong>
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
	</button>
	<div class="model-list">
		<div class="model-row">
			<span>ASR</span>
			<strong>faster-whisper-turbo</strong>
			<span class={`badge ${operationBadgeClass(operationFor('english_asr')) || (draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) ? 'ok' : '')}`}>
				{operationBusy('english_asr') ? operationStatusLabel(operationFor('english_asr')) : draft?.cues.some((cue) => cue.en_subtitle_text?.trim()) ? '有草稿' : operationStatusLabel(operationFor('english_asr'))}
			</span>
			<button class="mini-btn" type="button" onclick={onTranscribeEnglish} disabled={!(draft?.source_media.audio_path || draft?.stems.original_audio_path) || transcribingAsr || operationBusy('english_asr')}>
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
			<button class="mini-btn" type="button" onclick={onSeparateStems} disabled={!(draft?.source_media.audio_path || draft?.stems.original_audio_path) || separatingStems || operationBusy('stems')}>
				{operationBusy('stems') || separatingStems ? '分离中' : '分离'}
			</button>
		</div>
		<div class="model-row">
			<span>源音</span>
			<strong>{draft?.source_media.audio_path ? 'source.wav 已记录' : '等待抽取'}</strong>
			<span class={`badge ${operationBadgeClass(operationFor('source_audio')) || (draft?.source_media.audio_path ? 'ok' : '')}`}>
				{operationBusy('source_audio') ? operationStatusLabel(operationFor('source_audio')) : draft?.source_media.audio_path ? '已完成' : operationStatusLabel(operationFor('source_audio'))}
			</span>
			<button class="mini-btn" type="button" onclick={onExtractAudio} disabled={!draft?.source_media.video_path || extractingAudio || operationBusy('source_audio')}>
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
				<button class="mini-btn" type="button" onclick={() => onCancelOperation(latestOperation)} disabled={operationActionId === latestOperation.operation_id}>
					{operationActionId === latestOperation.operation_id ? '取消中' : '取消'}
				</button>
			{:else if latestOperation.status === 'failed' || latestOperation.status === 'cancelled'}
				<button class="mini-btn" type="button" onclick={() => onRetryOperation(latestOperation)} disabled={operationActionId === latestOperation.operation_id || hasActiveOperation}>
					{operationActionId === latestOperation.operation_id ? '重试中' : '重试'}
				</button>
			{/if}
		</div>
	{/if}
</section>

<style>
	.drop-target {
		width: 100%;
		text-align: left;
		color: var(--text);
		cursor: pointer;
		display: grid;
		grid-template-columns: 38px minmax(0, 1fr);
		gap: 10px;
		align-items: center;
		border: 1px dashed var(--line);
		border-radius: 7px;
		padding: 12px;
		background: #101215;
	}

	.drop-target.drag-active {
		border-color: #4f9cf9;
		background: #102033;
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
</style>
