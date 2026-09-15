<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import {
		AUDIO_GAIN_MAX_DB,
		AUDIO_GAIN_MIN_DB,
		formatAudioGainDb,
		isPotentialAudioGainDraft,
		normalizeAudioGainDb,
		parseAudioGainDraft
	} from './audio-gain';

	let {
		valueDb,
		ariaLabel,
		onChange,
		onCommit,
		onCancel
	}: {
		valueDb: number;
		ariaLabel: string;
		onChange: (valueDb: number) => void;
		onCommit: () => void;
		onCancel: () => void;
	} = $props();

	const initialValueDb = normalizeAudioGainDb(untrack(() => valueDb));
	let lastValidDb = initialValueDb;
	let draft = $state(formatAudioGainDb(initialValueDb));
	let inputEl: HTMLInputElement;
	let settled = false;

	onMount(() => {
		inputEl?.focus();
		inputEl?.select();
	});

	function draftValue() {
		return parseAudioGainDraft(draft);
	}

	function draftInvalid() {
		const value = draftValue();
		return !isPotentialAudioGainDraft(draft)
			|| (value !== null && (value < AUDIO_GAIN_MIN_DB || value > AUDIO_GAIN_MAX_DB));
	}

	function updateDraft(value: string) {
		draft = value;
		const parsed = parseAudioGainDraft(value);
		if (
			parsed === null
			|| parsed < AUDIO_GAIN_MIN_DB
			|| parsed > AUDIO_GAIN_MAX_DB
		) return;
		lastValidDb = parsed;
		onChange(parsed);
	}

	function commit() {
		if (settled) return;
		settled = true;
		const parsed = parseAudioGainDraft(draft);
		const committed = parsed === null ? lastValidDb : normalizeAudioGainDb(parsed);
		draft = formatAudioGainDb(committed);
		lastValidDb = committed;
		onChange(committed);
		onCommit();
	}

	function cancel() {
		if (settled) return;
		settled = true;
		onChange(initialValueDb);
		onCancel();
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			commit();
		} else if (event.key === 'Escape') {
			event.preventDefault();
			cancel();
		} else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
			event.preventDefault();
			const direction = event.key === 'ArrowUp' ? 1 : -1;
			const step = event.shiftKey ? 1 : 0.1;
			const next = normalizeAudioGainDb((draftValue() ?? lastValidDb) + direction * step);
			draft = formatAudioGainDb(next);
			lastValidDb = next;
			onChange(next);
		}
	}
</script>

<div class="audio-gain-editor" data-audio-gain-editor role="group" aria-label={ariaLabel.replace(/ dB$/, '')}>
	<input
		class="gain-value-input"
		bind:this={inputEl}
		aria-label={ariaLabel}
		aria-invalid={draftInvalid() ? 'true' : undefined}
		aria-valuemin={AUDIO_GAIN_MIN_DB}
		aria-valuemax={AUDIO_GAIN_MAX_DB}
		aria-valuenow={draftValue() ?? undefined}
		type="text"
		role="spinbutton"
		inputmode="decimal"
		autocomplete="off"
		spellcheck="false"
		value={draft}
		oninput={(event) => updateDraft(event.currentTarget.value)}
		onblur={commit}
		onkeydown={handleKeydown}
	/>
	<span aria-hidden="true">dB</span>
</div>

<style>
	.audio-gain-editor {
		width: 100%;
		height: 23px;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 3px;
		padding: 0 5px 0 4px;
		box-sizing: border-box;
		border: 1px solid rgba(87, 208, 200, 0.5);
		border-radius: 5px;
		background: #0d1216;
		box-shadow: inset 0 0 0 1px transparent;
		overflow: hidden;
	}

	.audio-gain-editor:focus-within {
		border-color: rgba(93, 220, 210, 0.82);
		box-shadow: inset 0 0 0 1px rgba(93, 220, 210, 0.18);
	}

	input.gain-value-input[type='text'][role='spinbutton'] {
		min-width: 0;
		min-height: 0;
		width: 100%;
		height: 19px;
		padding: 0;
		border: 0;
		border-radius: 0;
		outline: 0;
		background: transparent;
		box-shadow: none;
		color: #e2edf2;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 9px;
		font-variant-numeric: tabular-nums;
		line-height: 19px;
		text-align: right;
	}

	input.gain-value-input[type='text'][role='spinbutton']::selection {
		background: rgba(87, 208, 200, 0.3);
		color: #f4ffff;
	}

	input.gain-value-input[type='text'][role='spinbutton'][aria-invalid='true'] {
		color: #ffbd7a;
	}

	span {
		color: #d9e4ea;
		font-size: 8px;
		font-weight: 750;
	}
</style>
