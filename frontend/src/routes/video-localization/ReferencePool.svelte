<script lang="ts">
	import type { VideoLocalizationOperation, VideoLocalizationReferenceClip } from '$lib/api/types';
	import { durationLabel, operationBadgeClass, operationStatusLabel, referenceAudioUrl } from './utils';

	let {
		clips,
		operation,
		creatingReferences,
		canCreateCandidates,
		referenceUpdatingId,
		projectId,
		speakerLabel,
		onGenerateCandidates,
		onMarkClean,
		onMarkBlocked,
		onMarkNeedsReview
	}: {
		clips: VideoLocalizationReferenceClip[];
		operation: VideoLocalizationOperation | null;
		creatingReferences: boolean;
		canCreateCandidates: boolean;
		referenceUpdatingId: string;
		projectId: string;
		speakerLabel: (speakerId: string | null | undefined) => string;
		onGenerateCandidates: () => void;
		onMarkClean: (clip: VideoLocalizationReferenceClip) => void;
		onMarkBlocked: (clip: VideoLocalizationReferenceClip) => void;
		onMarkNeedsReview: (clip: VideoLocalizationReferenceClip) => void;
	} = $props();

	function referenceCanBeConfirmed(clip: VideoLocalizationReferenceClip) {
		return Boolean(clip.audio_path && clip.source_stem === 'vocals_clean' && clip.asr_text?.trim());
	}

	function clipStateClass(clip: VideoLocalizationReferenceClip) {
		if (clip.cleanliness === 'clean' && clip.asr_status === 'verified') return 'ready';
		if (clip.cleanliness === 'blocked') return 'blocked';
		return 'review';
	}

	function clipStateBadgeClass(clip: VideoLocalizationReferenceClip) {
		if (clip.cleanliness === 'clean' && clip.asr_status === 'verified') return 'ok';
		if (clip.cleanliness === 'blocked') return 'fail';
		return 'warn';
	}

	function clipStateLabel(clip: VideoLocalizationReferenceClip) {
		if (clip.cleanliness === 'clean' && clip.asr_status === 'verified') return '可用';
		if (clip.cleanliness === 'blocked') return '阻断';
		return '复听';
	}
</script>

<section class="panel refs-panel">
	<div class="section-title">
		<h2>干净参考音色池</h2>
		<div class="row">
			<span class="badge ok">{clips.length} 候选</span>
			<span class={`badge ${operationBadgeClass(operation)}`}>{operationStatusLabel(operation)}</span>
			<button class="mini-btn" type="button" onclick={onGenerateCandidates} disabled={!canCreateCandidates || creatingReferences}>
				{creatingReferences || operation?.status === 'queued' || operation?.status === 'running' ? '生成中' : '生成候选'}
			</button>
		</div>
	</div>
	<div class="reference-list">
		{#each clips as clip}
			<article class={`reference-card ${clipStateClass(clip)}`}>
				<div>
					<strong>{clip.reference_clip_id}</strong>
					<p>{clip.audio_path || '尚未生成参考音文件'}</p>
				</div>
				{#if referenceAudioUrl(projectId, clip)}
					<audio class="reference-audio" controls src={referenceAudioUrl(projectId, clip)}></audio>
				{/if}
				<div class="row">
					<span class="badge role">{speakerLabel(clip.speaker_id)}</span>
					<span class="badge">{durationLabel(clip.duration_ms)}</span>
					<span class={`badge ${clipStateBadgeClass(clip)}`}>{clipStateLabel(clip)}</span>
				</div>
				<small>ASR: {clip.asr_text || '待独立 ASR'}</small>
				<div class="reference-actions">
					<button class="mini-btn" type="button" onclick={() => onMarkClean(clip)} disabled={!referenceCanBeConfirmed(clip) || referenceUpdatingId === clip.reference_clip_id}>
						确认可用
					</button>
					<button class="mini-btn danger-text" type="button" onclick={() => onMarkBlocked(clip)} disabled={referenceUpdatingId === clip.reference_clip_id}>
						标记阻断
					</button>
					<button class="mini-btn" type="button" onclick={() => onMarkNeedsReview(clip)} disabled={referenceUpdatingId === clip.reference_clip_id}>
						退回复听
					</button>
				</div>
			</article>
		{/each}
		{#if !clips.length}
			<p class="muted">暂无参考音候选。先给 cue 绑定说话人，再从干净人声里生成可复听的参考音。</p>
		{/if}
	</div>
</section>

<style>
	.reference-list {
		display: grid;
		gap: 8px;
	}

	.reference-card {
		display: grid;
		gap: 7px;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 10px;
		background: #101215;
	}

	.reference-card.ready {
		border-color: #23634f;
	}

	.reference-card.review {
		border-color: #604b18;
	}

	.reference-audio {
		width: 100%;
		height: 34px;
	}

	.reference-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.danger-text {
		color: #ff9f9f;
		border-color: #6d3030;
	}

	.reference-card p,
	.reference-card small {
		margin: 0;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.45;
	}
</style>
