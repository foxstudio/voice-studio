<script lang="ts">
	import type { VideoLocalizationCue, VideoLocalizationReferenceClip, VideoLocalizationSpeaker } from '$lib/api/types';
	import { Lock, Mic2, Play, Save, Send } from 'lucide-svelte';
	import { sourceCueAudioUrl, statusLabel, ttsAudioUrl } from './utils';

	let {
		selectedCue,
		speakers,
		referenceClips,
		projectId,
		savingCue,
		speakerLabel,
		canSendToGenerate,
		onUpdateCue,
		onUpdateCueTime,
		onSave,
		onSend
	}: {
		selectedCue: VideoLocalizationCue | null;
		speakers: VideoLocalizationSpeaker[];
		referenceClips: VideoLocalizationReferenceClip[];
		projectId: string;
		savingCue: boolean;
		speakerLabel: (speakerId: string | null | undefined) => string;
		canSendToGenerate: boolean;
		onUpdateCue: (patch: Partial<VideoLocalizationCue>) => void;
		onUpdateCueTime: (field: 'start_ms' | 'end_ms', value: string) => void;
		onSave: () => void;
		onSend: () => void;
	} = $props();
</script>

<section class="panel editor-panel">
	<div class="section-title">
		<h2>当前片段</h2>
		<span class={`badge ${selectedCue?.review_status === 'locked' ? 'ok' : selectedCue?.review_status === 'blocked' ? 'fail' : 'warn'}`}>
			<Lock size={12} /> {selectedCue ? statusLabel(selectedCue.review_status) : '未选择'}
		</span>
	</div>
	{#if selectedCue}
		<div class="editor-grid">
			<label class="field">
				<span>说话人</span>
				<select value={selectedCue.speaker_id ?? ''} aria-label="说话人" onchange={(event) => onUpdateCue({ speaker_id: event.currentTarget.value || null })}>
					<option value="">未选择</option>
					{#each speakers as speaker}
						<option value={speaker.speaker_id}>{speaker.speaker_id} / {speaker.display_name || speaker.speaker_id}</option>
					{/each}
					<option value="mixed">mixed / 需拆分</option>
				</select>
			</label>
			<div class="time-fields">
				<label class="field"><span>入点 ms</span><input value={selectedCue.start_ms ?? ''} aria-label="入点" oninput={(event) => onUpdateCueTime('start_ms', event.currentTarget.value)} /></label>
				<label class="field"><span>出点 ms</span><input value={selectedCue.end_ms ?? ''} aria-label="出点" oninput={(event) => onUpdateCueTime('end_ms', event.currentTarget.value)} /></label>
			</div>
			<label class="field">
				<span>参考音色</span>
				<select value={selectedCue.reference_clip_id ?? ''} aria-label="参考音色" onchange={(event) => onUpdateCue({ reference_clip_id: event.currentTarget.value || null })}>
					<option value="">未选择</option>
					{#each referenceClips as clip}
						<option value={clip.reference_clip_id}>{clip.reference_clip_id} / {speakerLabel(clip.speaker_id)}</option>
					{/each}
				</select>
			</label>
			<label class="field">
				<span>状态</span>
				<select value={selectedCue.review_status} aria-label="状态" onchange={(event) => onUpdateCue({ review_status: event.currentTarget.value as VideoLocalizationCue['review_status'] })}>
					<option value="needs_review">待校对</option>
					<option value="ready">可生成</option>
					<option value="blocked">阻断</option>
					<option value="locked">已锁定</option>
				</select>
			</label>
			<label class="field"><span>英文字幕</span><textarea rows="3" value={selectedCue.en_subtitle_text ?? ''} oninput={(event) => onUpdateCue({ en_subtitle_text: event.currentTarget.value })}></textarea></label>
			<label class="field"><span>中文字幕</span><textarea rows="3" value={selectedCue.zh_localized_subtitle_text ?? ''} oninput={(event) => onUpdateCue({ zh_localized_subtitle_text: event.currentTarget.value })}></textarea></label>
			<label class="field"><span>TTS 台词</span><textarea rows="3" value={selectedCue.tts_recommended_text ?? ''} oninput={(event) => onUpdateCue({ tts_recommended_text: event.currentTarget.value })}></textarea></label>
		</div>
		<div class="row editor-actions">
			<button class="btn primary" type="button" onclick={onSave} disabled={savingCue}>
				<Save size={14} /> {savingCue ? '保存中' : '保存当前片段'}
			</button>
			<div class="cue-audio-compare">
				<div>
					<span>原声</span>
					{#if sourceCueAudioUrl(projectId, selectedCue)}
						<audio class="cue-audio" controls src={sourceCueAudioUrl(projectId, selectedCue)}></audio>
					{:else}
						<button class="btn" type="button" disabled><Play size={14} /> 原声</button>
					{/if}
				</div>
				<div>
					<span>TTS</span>
					{#if ttsAudioUrl(projectId, selectedCue)}
						<audio class="cue-audio" controls src={ttsAudioUrl(projectId, selectedCue)}></audio>
					{:else}
						<button class="btn" type="button" disabled><Mic2 size={14} /> TTS</button>
					{/if}
				</div>
			</div>
			<button class="btn primary" type="button" onclick={onSend} disabled={!canSendToGenerate}>
				<Send size={14} /> 单条发送
			</button>
		</div>
	{:else}
		<p class="muted">选择或新增一个 cue 后，可以编辑三轨文本和参考音色。</p>
	{/if}
</section>

<style>
	.editor-grid {
		display: grid;
		gap: 10px;
	}

	.field span {
		font-size: 12px;
		color: var(--muted);
	}

	.time-fields {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 8px;
	}

	.editor-grid textarea {
		min-height: 74px;
	}

	.editor-actions {
		margin-top: 12px;
		justify-content: flex-end;
	}

	.cue-audio {
		width: min(260px, 100%);
		height: 34px;
	}

	.cue-audio-compare {
		display: grid;
		grid-template-columns: repeat(2, minmax(160px, 1fr));
		gap: 8px;
		flex: 1;
		min-width: 280px;
	}

	.cue-audio-compare > div {
		display: grid;
		gap: 4px;
	}

	.cue-audio-compare span {
		font-size: 11px;
		color: var(--muted);
	}
</style>
