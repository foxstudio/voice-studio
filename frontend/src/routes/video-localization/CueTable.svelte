<script lang="ts">
	import type { VideoLocalizationCue } from '$lib/api/types';
	import { durationLabel, speakerColor, statusLabel, timeLabel, ttsBatchLabel } from './utils';

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
</script>

<div class="cue-table-wrap">
	<table class="table cue-table">
		<thead>
			<tr>
				<th>时间</th>
				<th>说话人</th>
				<th>英文字幕</th>
				<th>中文字幕</th>
				<th>TTS 台词</th>
				<th>参考音色</th>
				<th>TTS 音频</th>
				<th>状态</th>
			</tr>
		</thead>
		<tbody>
			{#each cues as cue}
				<tr class:blocked={cue.review_status === 'blocked'} class:selected={cue.cue_id === selectedCueId}>
					<td><button class="time-btn" type="button" onclick={() => onSelect(cue.cue_id)}>{timeLabel(cue)}</button></td>
					<td><span class="speaker-pill" style={`--speaker:${speakerColor(cue.speaker_id)}`}>{speakerLabel(cue.speaker_id)}</span></td>
					<td>{cue.en_subtitle_text || '未填写'}</td>
					<td>{cue.zh_localized_subtitle_text || '未填写'}</td>
					<td><strong>{cue.tts_recommended_text || '未填写'}</strong></td>
					<td>{cue.reference_clip_id || '未选择'}</td>
					<td>
						{#if cue.tts_audio_path}
							<span class="badge ok">已生成</span>
							<small>{durationLabel(cue.generated_duration_ms)}</small>
						{:else if cue.tts_batch_status}
							<span class={`badge ${cue.tts_batch_status === 'failed' || cue.tts_batch_status === 'cancelled' ? 'fail' : 'warn'}`}>{ttsBatchLabel(cue.tts_batch_status)}</span>
							{#if cue.tts_batch_error}<small>{cue.tts_batch_error}</small>{/if}
						{:else}
							<span class="badge warn">待生成</span>
						{/if}
					</td>
					<td>
						<span class={`badge ${cue.review_status === 'ready' || cue.review_status === 'locked' ? 'ok' : cue.review_status === 'blocked' ? 'fail' : 'warn'}`}>{statusLabel(cue.review_status)}</span>
						<div class="flag-list">
							{#each cue.quality_flags as flag}<small>{flag}</small>{/each}
						</div>
					</td>
				</tr>
			{/each}
			{#if !cues.length}
				<tr>
					<td colspan="8" class="empty-cell">当前项目还没有 cue。可以先手动新增一条，后续 ASR 会自动生成候选。</td>
				</tr>
			{/if}
		</tbody>
	</table>
</div>

<style>
	.cue-table-wrap {
		overflow-x: auto;
		border: 1px solid var(--line);
		border-radius: 7px;
	}

	.cue-table {
		min-width: 840px;
		table-layout: fixed;
	}

	.cue-table th,
	.cue-table td {
		padding: 9px 8px;
		overflow-wrap: anywhere;
	}

	.cue-table th:nth-child(1),
	.cue-table td:nth-child(1) {
		width: 96px;
	}

	.cue-table th:nth-child(2),
	.cue-table td:nth-child(2) {
		width: 54px;
		text-align: center;
	}

	.cue-table th:nth-child(6),
	.cue-table td:nth-child(6) {
		width: 86px;
	}

	.cue-table th:nth-child(7),
	.cue-table td:nth-child(7) {
		width: 86px;
	}

	.cue-table th:nth-child(8),
	.cue-table td:nth-child(8) {
		width: 112px;
	}

	.cue-table tr.selected td {
		background: rgba(79, 156, 249, 0.08);
	}

	.cue-table tr.blocked td {
		background: rgba(242, 109, 109, 0.05);
	}

	.empty-cell {
		color: var(--muted);
		text-align: center;
		padding: 22px !important;
	}

	.time-btn {
		border: 0;
		background: transparent;
		color: #9cc9ff;
		padding: 0;
		font-size: 12px;
	}

	.speaker-pill {
		--speaker: #4f9cf9;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 30px;
		height: 22px;
		border-radius: 999px;
		border: 1px solid color-mix(in srgb, var(--speaker), #000 24%);
		color: #fff;
		background: color-mix(in srgb, var(--speaker), #111315 42%);
		font-weight: 700;
		font-size: 12px;
	}

	.flag-list {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 5px;
	}

	.flag-list small {
		color: var(--muted);
		border: 1px solid var(--line);
		border-radius: 999px;
		padding: 1px 5px;
		font-size: 10px;
		white-space: nowrap;
	}
</style>
