<script lang="ts">
	import { AudioLines, Play, ScissorsLineDashed, UsersRound } from 'lucide-svelte';
	import type { VideoLocalizationCue, VideoLocalizationDraft } from '$lib/api/types';
	import { durationLabel, sourceAudioUrl, sourceVideoUrl, stemAudioUrl } from './utils';

	let {
		selectedCue,
		hasCleanReference,
		draft,
		projectId
	}: {
		selectedCue: VideoLocalizationCue | null;
		hasCleanReference: boolean;
		draft: VideoLocalizationDraft | null;
		projectId: string;
	} = $props();

	let sourceVideoFailed = $state(false);
	let sourceAudioFailed = $state(false);
	let vocalsFailed = $state(false);
	let backgroundFailed = $state(false);

	const previewVideoSrc = $derived(sourceVideoUrl(projectId, draft));
	const previewSourceAudioSrc = $derived(sourceAudioUrl(projectId, draft));
	const previewVocalsSrc = $derived(stemAudioUrl(projectId, draft, 'vocals'));
	const previewBackgroundSrc = $derived(stemAudioUrl(projectId, draft, 'background'));

	$effect(() => {
		projectId;
		draft?.updated_at;
		draft?.source_media.video_path;
		draft?.source_media.audio_path;
		draft?.stems.original_audio_path;
		draft?.stems.vocals_clean_path;
		draft?.stems.background_path;
		sourceVideoFailed = false;
		sourceAudioFailed = false;
		vocalsFailed = false;
		backgroundFailed = false;
	});
</script>

<section class="panel preview-panel">
	<div class="video-preview">
		{#if previewVideoSrc && !sourceVideoFailed}
			<!-- svelte-ignore a11y_media_has_caption -->
			<video class="preview-video" controls preload="metadata" src={previewVideoSrc} onerror={() => (sourceVideoFailed = true)}></video>
		{:else}
			<div class="video-glow"></div>
			<div class="play-button"><span><Play size={24} /></span></div>
		{/if}
		<div class="subtitle-overlay">
			<p>{selectedCue?.zh_localized_subtitle_text || '中文字幕将在这里预览'}</p>
			<span>{selectedCue?.en_subtitle_text || 'English subtitle preview'}</span>
		</div>
	</div>
	<div class="media-audio-grid">
		<div class="audio-card">
			<span>源音频</span>
			{#if previewSourceAudioSrc && !sourceAudioFailed}
				<audio controls src={previewSourceAudioSrc} onerror={() => (sourceAudioFailed = true)}></audio>
			{:else}
				<p class="muted">{sourceAudioFailed ? '加载失败' : '待抽取'}</p>
			{/if}
		</div>
		<div class="audio-card">
			<span>人声</span>
			{#if previewVocalsSrc && !vocalsFailed}
				<audio controls src={previewVocalsSrc} onerror={() => (vocalsFailed = true)}></audio>
			{:else}
				<p class="muted">{vocalsFailed ? '加载失败' : '待分离'}</p>
			{/if}
		</div>
		<div class="audio-card">
			<span>背景音</span>
			{#if previewBackgroundSrc && !backgroundFailed}
				<audio controls src={previewBackgroundSrc} onerror={() => (backgroundFailed = true)}></audio>
			{:else}
				<p class="muted">{backgroundFailed ? '加载失败' : '待分离'}</p>
			{/if}
		</div>
	</div>
	{#if draft?.stems.separation_status === 'failed'}
		<p class="media-status muted">人声分离失败。请检查本地分离依赖或重试当前任务。</p>
	{/if}
	<div class="wave-panel">
		<div class="wave-head">
			<span><AudioLines size={14} /> 编辑基线</span>
			<span class={`badge ${hasCleanReference ? 'ok' : ''}`}>
				{hasCleanReference ? '已有干净参考音' : '待确认参考音'}
			</span>
		</div>
		<div class="media-summary-grid">
			<div class="summary-card">
				<span><ScissorsLineDashed size={13} /> 当前裁切轨道</span>
				<strong>{draft?.stems.vocals_clean_path ? '分离人声 stem' : draft?.source_media.audio_path || draft?.stems.original_audio_path ? '源音轨' : '待抽取音频'}</strong>
				<small>{draft?.stems.vocals_clean_path ? '更适合框定说话段落和挑选参考音色。' : '完成人声分离后，这里会自动切换到更干净的人声轨。'}</small>
			</div>
			<div class="summary-card">
				<span><UsersRound size={13} /> 说话人进度</span>
				<strong>{draft?.speakers.length ?? 0} 位说话人 / {draft?.reference_clips.filter((clip) => clip.cleanliness === 'clean').length ?? 0} 条干净参考音</strong>
				<small>{selectedCue?.speaker_id ? `当前 cue 已绑定 ${selectedCue.speaker_id}` : '当前 cue 还没有绑定说话人。'}</small>
			</div>
			<div class="summary-card">
				<span><AudioLines size={13} /> 当前 cue</span>
				<strong>{selectedCue ? durationLabel((selectedCue.end_ms ?? 0) - (selectedCue.start_ms ?? 0)) : '未选择 cue'}</strong>
				<small>{selectedCue && selectedCue.start_ms !== null && selectedCue.end_ms !== null ? `${selectedCue.start_ms}ms - ${selectedCue.end_ms}ms` : '请在右侧时间轴里拖动 IN / OUT。'}</small>
			</div>
		</div>
	</div>
</section>

<style>
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
		pointer-events: none;
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
		bottom: 52px;
		text-align: center;
		text-shadow: 0 1px 6px rgba(0, 0, 0, 0.7);
		pointer-events: none;
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

	.media-status {
		margin: 8px 0 0;
		font-size: 12px;
	}

	.preview-video {
		width: 100%;
		height: 100%;
		object-fit: contain;
		background: #050608;
	}

	.media-audio-grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 8px;
		margin-top: 12px;
	}

	.audio-card {
		display: grid;
		gap: 6px;
		padding: 8px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
	}

	.audio-card span {
		font-size: 12px;
		color: var(--muted);
	}

	.audio-card audio {
		width: 100%;
		height: 32px;
	}

	.audio-card p {
		margin: 0;
		font-size: 12px;
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

	.media-summary-grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 8px;
	}

	.summary-card {
		display: grid;
		gap: 6px;
		padding: 10px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
	}

	.summary-card span,
	.summary-card small {
		color: var(--muted);
	}

	.summary-card span {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 12px;
	}

	.summary-card strong {
		font-size: 13px;
	}

	.summary-card small {
		font-size: 11px;
		line-height: 1.5;
	}

	@media (max-width: 900px) {
		.media-audio-grid {
			grid-template-columns: 1fr;
		}

		.media-summary-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
