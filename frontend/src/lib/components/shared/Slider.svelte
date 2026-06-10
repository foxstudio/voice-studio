<script lang="ts">
	type Props = {
		value?: number;
		min?: number;
		max?: number;
		step?: number;
		manualInput?: boolean;
		onChange?: (value: number) => void;
	};

	let {
		value = $bindable(0),
		min = 0,
		max = 100,
		step = 1,
		manualInput = false,
		onChange = () => {}
	}: Props = $props();

	let inputValue = $state(formatValue(value));
	const rangeValue = $derived(Number.isFinite(value) ? value : min);

	$effect(() => {
		inputValue = formatValue(value);
	});

	function formatValue(nextValue: number) {
		return (Number.isFinite(nextValue) ? nextValue : min).toFixed(2);
	}

	function clamp(nextValue: number) {
		if (!Number.isFinite(nextValue)) return min;
		return Math.min(max, Math.max(min, nextValue));
	}

	function roundToTwo(nextValue: number) {
		return Math.round(nextValue * 100) / 100;
	}

	function commit(nextValue: number) {
		const rounded = roundToTwo(clamp(nextValue));
		value = rounded;
		inputValue = formatValue(rounded);
		onChange(rounded);
	}

	function handleRangeInput(event: Event) {
		commit(Number((event.currentTarget as HTMLInputElement).value));
	}

	function handleManualInput(event: Event) {
		const raw = (event.currentTarget as HTMLInputElement).value;
		const sanitized = raw
			.replace(/[^0-9.-]/g, '')
			.replace(/(?!^)-/g, '')
			.replace(/(\.\d{0,2}).*$/g, '$1');
		inputValue = sanitized;
	}

	function commitManualInput() {
		commit(Number(inputValue));
	}

	function handleManualKeydown(event: KeyboardEvent) {
		if (event.key !== 'Enter') return;
		commitManualInput();
	}
</script>

{#if manualInput}
	<input
		class="numeric-input"
		type="text"
		inputmode="decimal"
		bind:value={inputValue}
		oninput={handleManualInput}
		onblur={commitManualInput}
		onkeydown={handleManualKeydown}
		aria-label="数值输入"
	/>
{:else}
	<div class="slider">
		<input type="range" value={rangeValue} {min} {max} {step} oninput={handleRangeInput} aria-label="数值滑块" />
		<span class="value">{formatValue(rangeValue)}</span>
	</div>
{/if}

<style>
	.slider {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 54px;
		align-items: center;
		gap: 10px;
		width: 100%;
	}

	input[type='range'] {
		width: 100%;
		accent-color: var(--accent);
	}

	.value {
		color: var(--muted);
		font-size: 12px;
		font-variant-numeric: tabular-nums;
		text-align: right;
	}

	.numeric-input {
		font-variant-numeric: tabular-nums;
	}
</style>
