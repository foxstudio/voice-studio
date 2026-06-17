<script lang="ts">
	import type { VideoLocalizationCue } from '$lib/api/types';
	import { speakerColor, statusLabel, timeLabel } from './utils';

	let {
		cues,
		selectedCueId,
		speakerLabel,
		onSelect
	}: {
		cues: VideoLocalizationCue[];
		selectedCueId: string;
		speakerLabel: (speakerId: string | null | undefined) => string;
		onSelect: (cueId: string) => void;
	} = $props();

	function cueStateClass(cue: VideoLocalizationCue) {
		if (cue.review_status === 'ready' || cue.review_status === 'locked') return 'ready';
		if (cue.review_status === 'blocked') return 'blocked';
		return 'review';
	}
</script>

<section class="panel sentence-rail-panel">
	<div class="section-title">
		<div>
			<h2>逐句导航</h2>
			<p class="muted">每一行就是一句。先选句，再在中间精修字幕、TTS 台词和时间点。</p>
		</div>
		<span class="badge ok">{cues.length} 句</span>
	</div>

	<div class="sentence-rail">
		{#each cues as cue, index}
			<button class:selected={cue.cue_id === selectedCueId} class={`sentence-card ${cueStateClass(cue)}`} type="button" onclick={() => onSelect(cue.cue_id)}>
				<div class="sentence-card-head">
					<div class="row compact">
						<span class="sentence-index">{index + 1}</span>
						<span class="speaker-pill" style={`--speaker:${speakerColor(cue.speaker_id)}`}>{speakerLabel(cue.speaker_id)}</span>
					</div>
					<span class={`badge ${cue.review_status === 'ready' || cue.review_status === 'locked' ? 'ok' : cue.review_status === 'blocked' ? 'fail' : 'warn'}`}>
						{statusLabel(cue.review_status)}
					</span>
				</div>
				<strong>{cue.zh_localized_subtitle_text || cue.tts_recommended_text || cue.en_subtitle_text || '待补充文本'}</strong>
				<span class="sentence-en">{cue.en_subtitle_text || 'English line pending'}</span>
				<div class="sentence-meta">
					<span>{timeLabel(cue)}</span>
					<span>{cue.reference_clip_id || '无参考音'}</span>
				</div>
			</button>
		{/each}
		{#if !cues.length}
			<p class="muted empty-copy">还没有句子。先导入视频并运行 ASR，或手动新增 cue。</p>
		{/if}
	</div>
</section>

<style>
	.sentence-rail-panel {
		display: grid;
		gap: 12px;
	}

	.sentence-rail {
		display: grid;
		gap: 8px;
		max-height: min(68vh, 860px);
		overflow: auto;
		padding-right: 2px;
	}

	.sentence-card {
		display: grid;
		gap: 7px;
		padding: 10px;
		border-radius: 8px;
		border: 1px solid var(--line);
		background: #101215;
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}

	.sentence-card.selected {
		border-color: #4f9cf9;
		background: #112033;
		box-shadow: inset 0 0 0 1px rgba(79, 156, 249, 0.2);
	}

	.sentence-card.ready {
		border-left: 3px solid #42c49b;
	}

	.sentence-card.review {
		border-left: 3px solid #d2a447;
	}

	.sentence-card.blocked {
		border-left: 3px solid #d66868;
	}

	.sentence-card-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}

	.sentence-card strong {
		font-size: 13px;
		line-height: 1.45;
	}

	.sentence-en,
	.sentence-meta,
	.empty-copy {
		color: var(--muted);
		font-size: 12px;
		line-height: 1.45;
	}

	.sentence-meta {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}

	.sentence-index {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 22px;
		height: 22px;
		border-radius: 999px;
		background: #1a1e25;
		color: var(--muted);
		font-size: 11px;
		font-weight: 700;
	}

	.speaker-pill {
		--speaker: #4f9cf9;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 34px;
		height: 22px;
		padding: 0 8px;
		border-radius: 999px;
		border: 1px solid color-mix(in srgb, var(--speaker), #000 24%);
		color: #fff;
		background: color-mix(in srgb, var(--speaker), #111315 42%);
		font-weight: 700;
		font-size: 11px;
	}

	.compact {
		gap: 6px;
		align-items: center;
	}
</style>
