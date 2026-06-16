<script lang="ts">
	import { AudioLines, Play } from 'lucide-svelte';
	import type { VideoLocalizationCue } from '$lib/api/types';

	let { selectedCue, hasCleanReference }: { selectedCue: VideoLocalizationCue | null; hasCleanReference: boolean } = $props();
</script>

<section class="panel preview-panel">
	<div class="video-preview">
		<div class="video-glow"></div>
		<div class="play-button"><span><Play size={24} /></span></div>
		<div class="subtitle-overlay">
			<p>{selectedCue?.zh_localized_subtitle_text || '中文字幕将在这里预览'}</p>
			<span>{selectedCue?.en_subtitle_text || 'English subtitle preview'}</span>
		</div>
	</div>
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
</style>
