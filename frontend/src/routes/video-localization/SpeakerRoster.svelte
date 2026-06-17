<script lang="ts">
	import type { VideoLocalizationCue, VideoLocalizationSpeaker, VideoLocalizationSpeakerCreate } from '$lib/api/types';
	import { AudioLines, Plus, UserRoundPlus } from 'lucide-svelte';
	import { statusLabel } from './utils';

	let {
		speakers,
		selectedCue,
		creatingSpeaker,
		suggestedSpeakerId,
		suggestedDisplayName,
		onCreateSpeaker,
		onAssignToCue
	}: {
		speakers: VideoLocalizationSpeaker[];
		selectedCue: VideoLocalizationCue | null;
		creatingSpeaker: boolean;
		suggestedSpeakerId: string;
		suggestedDisplayName: string;
		onCreateSpeaker: (payload: VideoLocalizationSpeakerCreate, assignCurrentCue: boolean) => void;
		onAssignToCue: (speakerId: string) => void;
	} = $props();

	let speakerId = $state('');
	let displayName = $state('');
	let route = $state<VideoLocalizationSpeaker['route']>('clone_from_source');
	let reviewStatus = $state<VideoLocalizationSpeaker['review_status']>('needs_review');
	let notes = $state('');

	function submit(assignCurrentCue: boolean) {
		onCreateSpeaker(
			{
				speaker_id: speakerId.trim() || suggestedSpeakerId,
				display_name: displayName.trim() || suggestedDisplayName,
				route,
				review_status: reviewStatus,
				notes: notes.trim() || null
			},
			assignCurrentCue
		);
		speakerId = '';
		displayName = '';
		notes = '';
		route = 'clone_from_source';
		reviewStatus = 'needs_review';
	}

	function routeLabel(routeValue: VideoLocalizationSpeaker['route']) {
		return {
			clone_from_source: '参考原声',
			preset_tts: '预设音色',
			preserve_original_audio: '保留原声',
			manual_review: '待确认'
		}[routeValue];
	}
</script>

<section class="panel speaker-panel">
	<div class="section-title">
		<div>
			<h2>说话人分配</h2>
			<p class="muted">先建说话人，再把 cue 绑到对应说话人，后续参考音和批量 TTS 才会顺起来。</p>
		</div>
		<span class="badge ok">{speakers.length} 人</span>
	</div>

	<div class="speaker-form">
		<label class="field">
			<span>speaker id</span>
			<input bind:value={speakerId} placeholder={suggestedSpeakerId} aria-label="speaker id" />
		</label>
		<label class="field">
			<span>显示名</span>
			<input bind:value={displayName} placeholder={suggestedDisplayName} aria-label="speaker display name" />
		</label>
		<div class="speaker-row">
			<label class="field">
				<span>音频路线</span>
				<select bind:value={route} aria-label="speaker route">
					<option value="clone_from_source">参考原声克隆</option>
					<option value="preset_tts">预设 TTS</option>
					<option value="preserve_original_audio">保留原声</option>
					<option value="manual_review">人工判断</option>
				</select>
			</label>
			<label class="field">
				<span>状态</span>
				<select bind:value={reviewStatus} aria-label="speaker review status">
					<option value="needs_review">待校对</option>
					<option value="ready">可生成</option>
					<option value="blocked">阻断</option>
					<option value="locked">已锁定</option>
				</select>
			</label>
		</div>
		<label class="field">
			<span>备注</span>
			<input bind:value={notes} placeholder="例如：主讲人 / 情绪稳定 / 口音明显" aria-label="speaker notes" />
		</label>
		<div class="row speaker-form-actions">
			<button class="btn" type="button" onclick={() => submit(false)} disabled={creatingSpeaker}>
				<Plus size={14} /> {creatingSpeaker ? '新增中' : '新增说话人'}
			</button>
			<button class="btn primary" type="button" onclick={() => submit(true)} disabled={creatingSpeaker || !selectedCue}>
				<UserRoundPlus size={14} /> {selectedCue ? '新增并绑定当前片段' : '先选择片段'}
			</button>
		</div>
	</div>

	<div class="speaker-list">
		{#each speakers as speaker}
			<article class="speaker-card">
				<div class="speaker-card-head">
					<div>
						<strong>{speaker.display_name || speaker.speaker_id}</strong>
						<small>{speaker.speaker_id}</small>
					</div>
					<div class="row compact">
						<span class="badge">{routeLabel(speaker.route)}</span>
						<span class={`badge ${speaker.review_status === 'ready' || speaker.review_status === 'locked' ? 'ok' : speaker.review_status === 'blocked' ? 'fail' : 'warn'}`}>
							{statusLabel(speaker.review_status)}
						</span>
					</div>
				</div>
				<div class="row compact speaker-stats">
					<span class="badge role">{speaker.time_ranges.length} 段</span>
					<span class="badge role">{speaker.reference_clip_ids.length} 条参考音</span>
				</div>
				{#if speaker.notes}
					<p>{speaker.notes}</p>
				{/if}
				<div class="row speaker-actions">
					<button class="mini-btn" type="button" onclick={() => onAssignToCue(speaker.speaker_id)} disabled={!selectedCue}>
						<AudioLines size={13} /> {selectedCue ? '绑定当前片段' : '先选择片段'}
					</button>
				</div>
			</article>
		{/each}
		{#if !speakers.length}
			<p class="muted">ASR 只会先给出字幕草稿。这里新增 A/B/C 等说话人后，再去给 cue 逐段分配。</p>
		{/if}
	</div>
</section>

<style>
	.speaker-panel,
	.speaker-form,
	.speaker-list,
	.speaker-card {
		display: grid;
		gap: 10px;
	}

	.speaker-row {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 8px;
	}

	.speaker-form-actions {
		justify-content: flex-end;
	}

	.speaker-card {
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 10px;
		background: #101215;
	}

	.speaker-card-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 10px;
	}

	.speaker-card small,
	.speaker-card p {
		margin: 0;
		color: var(--muted);
		font-size: 12px;
	}

	.compact {
		gap: 6px;
		flex-wrap: wrap;
	}

	.speaker-actions {
		justify-content: flex-end;
	}

	@media (max-width: 900px) {
		.speaker-row {
			grid-template-columns: 1fr;
		}
	}
</style>
