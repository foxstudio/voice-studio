<script lang="ts">
	import type { VideoLocalizationCue, VideoLocalizationReferenceClip, VideoLocalizationSpeaker } from '$lib/api/types';
	import { AlertTriangle, CheckCircle2, Play, Send } from 'lucide-svelte';
	import { createEventDispatcher } from 'svelte';
	import { durationLabel, sourceCueAudioUrl, ttsAudioUrl } from './utils';

	type BatchFilter = 'all' | 'ready' | 'blocked';

	let {
		cues,
		speakers,
		referenceClips,
		selectedCueId,
		projectId
	}: {
		cues: VideoLocalizationCue[];
		speakers: VideoLocalizationSpeaker[];
		referenceClips: VideoLocalizationReferenceClip[];
		selectedCueId: string;
		projectId: string;
	} = $props();

	const dispatch = createEventDispatcher<{ select: { cueId: string } }>();

	let statusFilter = $state<BatchFilter>('all');
	let speakerFilter = $state('all');

	const speakerOptions = $derived(
		speakers.map((speaker) => ({
			id: speaker.speaker_id,
			label: speaker.display_name || speaker.speaker_id
		}))
	);

	const filteredCues = $derived(
		cues.filter((cue) => {
			if (statusFilter === 'ready' && !(cue.review_status === 'ready' || cue.review_status === 'locked')) return false;
			if (statusFilter === 'blocked' && cue.review_status !== 'blocked') return false;
			if (speakerFilter !== 'all' && cue.speaker_id !== speakerFilter) return false;
			return true;
		})
	);

	const readyCount = $derived(cues.filter((cue) => cue.review_status === 'ready' || cue.review_status === 'locked').length);
	const blockedCount = $derived(cues.filter((cue) => cue.review_status === 'blocked').length);
	const generatedCount = $derived(cues.filter((cue) => cue.tts_audio_path).length);

	function speakerName(speakerId: string | null | undefined) {
		if (!speakerId) return '未分配';
		return speakers.find((speaker) => speaker.speaker_id === speakerId)?.display_name || speakerId;
	}

	function referenceState(cue: VideoLocalizationCue) {
		const clip = referenceClips.find((item) => item.reference_clip_id === cue.reference_clip_id);
		if (!clip) return { label: '未选择', tone: 'muted' };
		if (clip.cleanliness === 'clean' && clip.asr_status === 'verified') return { label: '干净可用', tone: 'ok' };
		if (clip.cleanliness === 'blocked') return { label: '已阻断', tone: 'fail' };
		return { label: '待复听', tone: 'warn' };
	}

	function fitState(cue: VideoLocalizationCue) {
		if (!cue.source_duration_ms || !cue.generated_duration_ms) return { label: '待生成', tone: 'muted' };
		const delta = Math.abs(cue.generated_duration_ms - cue.source_duration_ms);
		if (delta <= 250) return { label: '匹配好', tone: 'ok' };
		if (delta <= 900) return { label: '需复核', tone: 'warn' };
		return { label: '偏差大', tone: 'fail' };
	}
</script>

<section class="panel batch-review-panel">
	<div class="section-title">
		<div>
			<h2>批量逐句审校</h2>
			<p class="muted">每一行就是一句。先校对中英字幕和 TTS 台词，再批量送去生成。</p>
		</div>
		<div class="batch-totals">
			<span class="badge">{cues.length} 句</span>
			<span class="badge ok">{readyCount} 可生成</span>
			<span class="badge fail">{blockedCount} 阻断</span>
			<span class="badge ok">{generatedCount} 已生成</span>
		</div>
	</div>

	<div class="batch-toolbar">
		<div class="segmented">
			<button class:active={statusFilter === 'all'} type="button" onclick={() => (statusFilter = 'all')}>全部</button>
			<button class:active={statusFilter === 'ready'} type="button" onclick={() => (statusFilter = 'ready')}>可生成</button>
			<button class:active={statusFilter === 'blocked'} type="button" onclick={() => (statusFilter = 'blocked')}>阻断</button>
		</div>
		<label class="filter-select">
			<span>说话人</span>
			<select bind:value={speakerFilter} aria-label="按说话人过滤">
				<option value="all">全部</option>
				{#each speakerOptions as option}
					<option value={option.id}>{option.label}</option>
				{/each}
			</select>
		</label>
	</div>

	<div class="table-wrap">
		<table class="table batch-table">
			<thead>
				<tr>
					<th>#</th>
					<th>说话人</th>
					<th>英文句</th>
					<th>中文字幕句</th>
					<th>TTS 台词句</th>
					<th>参考音</th>
					<th>源时长</th>
					<th>生成时长</th>
					<th>匹配</th>
					<th>试听</th>
					<th>状态</th>
				</tr>
			</thead>
			<tbody>
				{#each filteredCues as cue, index}
					<tr class:selected={cue.cue_id === selectedCueId}>
						<td>
							<button class="row-jump" type="button" onclick={() => dispatch('select', { cueId: cue.cue_id })}>
								{index + 1}
							</button>
						</td>
						<td><span class="speaker-name">{speakerName(cue.speaker_id)}</span></td>
						<td>{cue.en_subtitle_text || '未填写'}</td>
						<td>{cue.zh_localized_subtitle_text || '未填写'}</td>
						<td><strong>{cue.tts_recommended_text || '未填写'}</strong></td>
						<td>
							<span class={`badge ${referenceState(cue).tone}`}>{referenceState(cue).label}</span>
						</td>
						<td>{durationLabel(cue.source_duration_ms ?? (cue.start_ms !== null && cue.end_ms !== null ? cue.end_ms - cue.start_ms : null))}</td>
						<td>{cue.generated_duration_ms ? durationLabel(cue.generated_duration_ms) : '未生成'}</td>
						<td>
							<span class={`badge ${fitState(cue).tone}`}>{fitState(cue).label}</span>
						</td>
						<td>
							<div class="media-actions">
								{#if sourceCueAudioUrl(projectId, cue)}
									<a class="icon-link" href={sourceCueAudioUrl(projectId, cue)} target="_blank" rel="noreferrer" aria-label="试听原句"><Play size={14} /></a>
								{:else}
									<span class="icon-link disabled"><Play size={14} /></span>
								{/if}
								{#if ttsAudioUrl(projectId, cue)}
									<a class="icon-link" href={ttsAudioUrl(projectId, cue)} target="_blank" rel="noreferrer" aria-label="试听 TTS"><Send size={14} /></a>
								{:else}
									<span class="icon-link disabled"><Send size={14} /></span>
								{/if}
							</div>
						</td>
						<td>
							<div class="status-stack">
								<span class={`badge ${cue.review_status === 'ready' || cue.review_status === 'locked' ? 'ok' : cue.review_status === 'blocked' ? 'fail' : 'warn'}`}>
									{cue.review_status === 'ready' || cue.review_status === 'locked' ? '可生成' : cue.review_status === 'blocked' ? '阻断' : '待校对'}
								</span>
								{#if cue.review_status === 'blocked'}
									<small class="warn-inline"><AlertTriangle size={12} /> 需要人工修</small>
								{:else if cue.tts_audio_path}
									<small class="ok-inline"><CheckCircle2 size={12} /> 已有结果</small>
								{/if}
							</div>
						</td>
					</tr>
				{/each}
				{#if !filteredCues.length}
					<tr>
						<td colspan="11" class="empty-cell">当前过滤条件下没有句子。</td>
					</tr>
				{/if}
			</tbody>
		</table>
	</div>
</section>

<style>
	.batch-review-panel {
		display: grid;
		gap: 12px;
	}

	.batch-totals {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}

	.batch-toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		flex-wrap: wrap;
	}

	.segmented {
		display: inline-flex;
		padding: 4px;
		border-radius: 8px;
		background: #0f1318;
		border: 1px solid var(--line);
	}

	.segmented button {
		border: 0;
		background: transparent;
		color: var(--muted);
		padding: 7px 12px;
		border-radius: 6px;
		font-size: 12px;
		cursor: pointer;
	}

	.segmented button.active {
		background: #1a2432;
		color: #dfe9f7;
	}

	.filter-select {
		display: inline-grid;
		gap: 4px;
		font-size: 12px;
		color: var(--muted);
	}

	.filter-select select {
		min-width: 160px;
	}

	.table-wrap {
		overflow: auto;
		border: 1px solid var(--line);
		border-radius: 8px;
	}

	.batch-table {
		min-width: 1320px;
		table-layout: fixed;
	}

	.batch-table th,
	.batch-table td {
		padding: 10px 8px;
		vertical-align: top;
		overflow-wrap: anywhere;
	}

	.batch-table th:nth-child(1),
	.batch-table td:nth-child(1) {
		width: 48px;
	}

	.batch-table th:nth-child(2),
	.batch-table td:nth-child(2) {
		width: 92px;
	}

	.batch-table th:nth-child(6),
	.batch-table td:nth-child(6) {
		width: 92px;
	}

	.batch-table th:nth-child(7),
	.batch-table td:nth-child(7),
	.batch-table th:nth-child(8),
	.batch-table td:nth-child(8),
	.batch-table th:nth-child(9),
	.batch-table td:nth-child(9),
	.batch-table th:nth-child(10),
	.batch-table td:nth-child(10),
	.batch-table th:nth-child(11),
	.batch-table td:nth-child(11) {
		width: 92px;
	}

	.batch-table tr.selected td {
		background: rgba(79, 156, 249, 0.08);
	}

	.row-jump {
		border: 0;
		background: transparent;
		color: #9cc9ff;
		font-weight: 700;
		cursor: pointer;
	}

	.speaker-name {
		font-weight: 600;
		font-size: 12px;
	}

	.media-actions {
		display: flex;
		gap: 6px;
	}

	.icon-link {
		width: 28px;
		height: 28px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border-radius: 6px;
		border: 1px solid var(--line);
		color: var(--text);
		background: #11161c;
	}

	.icon-link.disabled {
		opacity: 0.45;
	}

	.status-stack {
		display: grid;
		gap: 4px;
	}

	.warn-inline,
	.ok-inline {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		font-size: 11px;
	}

	.warn-inline {
		color: #e6b465;
	}

	.ok-inline {
		color: #8fdcb8;
	}

	.empty-cell {
		text-align: center;
		padding: 24px !important;
		color: var(--muted);
	}
</style>
