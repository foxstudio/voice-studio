<script lang="ts">
	import { RotateCcw, Settings } from 'lucide-svelte';
	import {
		activeSeedAudioDraft,
		resetActiveSeedAudioParameters,
		setSeedAudioMode,
		updateSeedAudioParameters,
		type SeedAudioFormat,
		type SeedAudioState
	} from './state';
	import { SEED_AUDIO_MODE_OPTIONS } from './ui';

	interface Props {
		state: SeedAudioState;
		showAdvanced?: boolean;
		onChange?: (state: SeedAudioState) => void;
		onToggleAdvanced?: () => void;
	}

	let {
		state,
		showAdvanced = false,
		onChange = () => {},
		onToggleAdvanced = () => {}
	}: Props = $props();

	const draft = $derived(activeSeedAudioDraft(state));
</script>

<div class="seed-inline-mode" role="group" aria-label="生成模式">
	<span>模式</span>
	<div class="gen-segmented seed-mode-tabs" role="radiogroup" aria-label="Seed Audio 生成模式">
		{#each SEED_AUDIO_MODE_OPTIONS as option}
			<button
				class:active={state.mode === option.value}
				type="button"
				role="radio"
				aria-checked={state.mode === option.value}
				onclick={() => onChange(setSeedAudioMode(state, option.value))}
			>{option.label}</button>
		{/each}
	</div>
</div>

<label class="param-inline-range seed-inline-speech-rate">
	<span>语速</span>
	<input class="speed-number" type="number" min="-50" max="100" step="1" aria-label="Seed Audio 语速数值" value={draft.parameters.speech_rate} oninput={(event) => onChange(updateSeedAudioParameters(state, { speech_rate: Number(event.currentTarget.value) }))} />
	<input type="range" min="-50" max="100" step="1" aria-label="Seed Audio 语速滑块" value={draft.parameters.speech_rate} oninput={(event) => onChange(updateSeedAudioParameters(state, { speech_rate: Number(event.currentTarget.value) }))} />
</label>

<label class="param-inline param-inline-format seed-inline-format">
	<span>格式</span>
	<select
		aria-label="Seed Audio 输出格式"
		value={draft.parameters.format}
		onchange={(event) => onChange(updateSeedAudioParameters(state, { format: event.currentTarget.value as SeedAudioFormat }))}
	>
		<option value="wav">WAV</option>
		<option value="mp3">MP3</option>
		<option value="pcm">PCM</option>
		<option value="ogg_opus">OGG Opus</option>
	</select>
</label>

<div class="param-actions-inline seed-inline-actions">
	<button
		class="btn param-action-btn param-reset-btn"
		type="button"
		data-tooltip="恢复当前模式的输出参数，保留描述和参考素材。"
		onclick={() => onChange(resetActiveSeedAudioParameters(state))}
	><RotateCcw size={14} /> 重置参数</button>
	<button
		class="btn param-action-btn param-inline-more"
		class:active={showAdvanced}
		type="button"
		aria-expanded={showAdvanced}
		onclick={onToggleAdvanced}
	><Settings size={14} /> {showAdvanced ? '收起高级' : '高级选项'}</button>
</div>

<style>
	.seed-inline-mode {
		display: flex;
		align-items: center;
		gap: 6px;
		flex: 0 0 auto;
		min-width: 0;
		color: var(--muted);
		font-size: 12px;
	}
	.seed-inline-mode > span { flex: 0 0 auto; white-space: nowrap; }
	.seed-mode-tabs { min-height: 28px; padding: 2px; }
	.seed-mode-tabs button { min-width: 48px; min-height: 24px; padding: 3px 10px; font-size: 12px; }
	.seed-inline-speech-rate { flex: 0 1 210px; }
	.seed-inline-format { flex: 0 0 112px; max-width: 112px; }
	.seed-inline-actions .active { border-color: var(--generate-control-hover-border); background: var(--generate-control-hover-bg); color: #eef5ff; }
	@media (max-width: 900px) {
		.seed-inline-mode { order: 2; }
		.seed-inline-speech-rate { order: 3; }
		.seed-inline-format { order: 4; }
		.seed-inline-actions { order: 5; }
	}
	@media (max-width: 640px) {
		.seed-inline-mode { flex: 1 1 100%; width: 100%; }
		.seed-mode-tabs { flex: 1 1 auto; }
		.seed-mode-tabs button { flex: 1 1 0; justify-content: center; }
		.seed-inline-speech-rate,
		.seed-inline-format { flex: 1 1 calc(50% - 4px); max-width: none; min-width: 0; }
		.seed-inline-format select { flex: 1 1 auto; width: 100%; }
		.seed-inline-actions { display: grid; grid-template-columns: 1fr 1fr; width: 100%; }
	}
</style>
