<script lang="ts">
	import { AudioLines, Play } from 'lucide-svelte';
	import type { VideoLocalizationCue, VideoLocalizationDraft } from '$lib/api/types';
	import { sourceAudioUrl, sourceVideoUrl, stemAudioUrl } from './utils';

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
			<span><AudioLines size={14} /> 分离人声</span>
			<span class={`badge ${hasCleanReference ? 'ok' : ''}`}>
				{hasCleanReference ? '有干净参考音' : '待选择参考音'}
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

	@media (max-width: 900px) {
		.media-audio-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
