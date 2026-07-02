<script lang="ts">
	import type { ParameterSchema } from '$lib/api/types';
	import Field from '$lib/components/shared/Field.svelte';
	import Slider from '$lib/components/shared/Slider.svelte';
	import Select from '$lib/components/shared/Select.svelte';
	import Toggle from '$lib/components/shared/Toggle.svelte';

	interface Props {
		parameterSchema: ParameterSchema[];
		values: Record<string, unknown>;
		onChange?: (key: string, value: unknown) => void;
		autoExpand?: boolean;
	}

	let {
		parameterSchema = [],
		values = {},
		onChange = () => {},
		autoExpand = false
	}: Props = $props();

	const basicParams = $derived(parameterSchema.filter((p) => p.level === 'basic'));
	const advancedParams = $derived(
		parameterSchema.filter((p) => p.level === 'advanced' || p.level === 'developer')
	);

	let showAdvanced = $state(false);

	$effect(() => { if (autoExpand) showAdvanced = true; });

	function getValue(param: ParameterSchema): unknown {
		return param.key in values ? values[param.key] : param.default;
	}

	function numberInputValue(param: ParameterSchema): string {
		const value = getValue(param);
		return value === null || value === undefined || value === '' ? '' : String(value);
	}

	function handleChange(param: ParameterSchema, value: unknown) {
		onChange(param.key, value);
	}

	function fieldClass(param: ParameterSchema): string {
		return `param-item param-${param.type}`;
	}
</script>

<div class="parameter-panel">
	{#each basicParams as param}
		<Field label={param.label} tooltip={param.description}>
			{#if param.type === 'slider'}
				{#if param.key === 'speed'}
					<input
						class="param-input"
						type="number"
						value={Number(getValue(param))}
						min={param.min ?? undefined}
						max={param.max ?? undefined}
						step="0.01"
						oninput={(e) => {
							const raw = (e.currentTarget as HTMLInputElement).value;
							handleChange(param, raw === '' ? 0 : Number(raw));
						}}
						onblur={(e) => {
							const input = e.currentTarget as HTMLInputElement;
							const val = parseFloat(input.value);
							if (!isNaN(val)) {
								input.value = val.toFixed(2);
							}
						}}
					/>
				{:else}
					<Slider
						value={Number(getValue(param))}
						min={param.min ?? undefined}
						max={param.max ?? undefined}
						step={param.step ?? undefined}
						onChange={(v: number) => handleChange(param, v)}
					/>
				{/if}
			{:else if param.type === 'select'}
				<Select
					value={String(getValue(param))}
					options={param.options}
					onChange={(v: string) => handleChange(param, v)}
				/>
			{:else if param.type === 'toggle'}
				<Toggle
					checked={Boolean(getValue(param))}
					onChange={(v: boolean) => handleChange(param, v)}
				/>
			{:else if param.type === 'number'}
				<input
					class="param-input"
					type="number"
					value={numberInputValue(param)}
					min={param.min ?? undefined}
					max={param.max ?? undefined}
					step={param.step ?? undefined}
					oninput={(e) => {
						const raw = (e.currentTarget as HTMLInputElement).value;
						handleChange(param, raw === '' ? null : Number(raw));
					}}
				/>
			{:else if param.type === 'textarea'}
				<textarea
					class="param-textarea"
					value={String(getValue(param))}
					oninput={(e) => handleChange(param, (e.currentTarget as HTMLTextAreaElement).value)}
				></textarea>
			{:else if param.type === 'text'}
				<input
					class="param-input"
					type="text"
					value={String(getValue(param))}
					oninput={(e) => handleChange(param, (e.currentTarget as HTMLInputElement).value)}
				/>
			{/if}
		</Field>
	{/each}

	{#if advancedParams.length > 0 && !autoExpand}
		<button
			class="advanced-toggle"
			type="button"
			aria-expanded={showAdvanced}
			onclick={() => (showAdvanced = !showAdvanced)}
		>
			{showAdvanced ? '收起' : '更多'}高级参数
			<span class="toggle-count">({advancedParams.length})</span>
		</button>
	{/if}

	{#if (autoExpand && advancedParams.length > 0) || showAdvanced}
			<div class="advanced-section">
				{#each advancedParams as param}
					<div class={fieldClass(param)}>
						<Field label={param.label} tooltip={param.description}>
							{#if param.type === 'slider'}
								{#if param.key === 'speed'}
									<input
										class="param-input"
										type="number"
										value={Number(getValue(param))}
										min={param.min ?? undefined}
										max={param.max ?? undefined}
										step="0.01"
										oninput={(e) => {
											const raw = (e.currentTarget as HTMLInputElement).value;
											handleChange(param, raw === '' ? 0 : Number(raw));
										}}
										onblur={(e) => {
											const input = e.currentTarget as HTMLInputElement;
											const val = parseFloat(input.value);
											if (!isNaN(val)) {
												input.value = val.toFixed(2);
											}
										}}
									/>
								{:else}
									<Slider
										value={Number(getValue(param))}
										min={param.min ?? undefined}
										max={param.max ?? undefined}
										step={param.step ?? undefined}
										onChange={(v: number) => handleChange(param, v)}
									/>
								{/if}
							{:else if param.type === 'select'}
								<Select
									value={String(getValue(param))}
									options={param.options}
									onChange={(v: string) => handleChange(param, v)}
								/>
							{:else if param.type === 'toggle'}
								<Toggle
									checked={Boolean(getValue(param))}
									onChange={(v: boolean) => handleChange(param, v)}
								/>
							{:else if param.type === 'number'}
								<input
									class="param-input"
									type="number"
									value={numberInputValue(param)}
									min={param.min ?? undefined}
									max={param.max ?? undefined}
									step={param.step ?? undefined}
									oninput={(e) => {
										const raw = (e.currentTarget as HTMLInputElement).value;
										handleChange(param, raw === '' ? null : Number(raw));
									}}
								/>
							{:else if param.type === 'textarea'}
								<textarea
									class="param-textarea"
									value={String(getValue(param) ?? '')}
									oninput={(e) =>
										handleChange(param, (e.currentTarget as HTMLTextAreaElement).value)}
								></textarea>
							{:else if param.type === 'text'}
								<input
									class="param-input"
									type="text"
									value={String(getValue(param) ?? '')}
									oninput={(e) => handleChange(param, (e.currentTarget as HTMLInputElement).value)}
								/>
							{/if}
						</Field>
					</div>
				{/each}
			</div>
	{/if}
</div>

<style>
	.parameter-panel {
		display: grid;
		gap: 10px;
		grid-column: 1 / -1;
	}

	.advanced-toggle {
		width: 100%;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: var(--panel-2);
		color: var(--muted);
		padding: 7px 12px;
		font-size: 12px;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 4px;
		transition: color 160ms ease, border-color 160ms ease;
	}

	.advanced-toggle:hover,
	.advanced-toggle:focus-visible {
		color: var(--accent);
		border-color: var(--accent);
	}

	.toggle-count {
		font-variant-numeric: tabular-nums;
	}

	.advanced-section {
		display: grid;
		grid-template-columns: repeat(12, minmax(0, 1fr));
		gap: 10px 12px;
		padding-top: 2px;
		align-items: start;
	}

	.param-item {
		grid-column: span 3;
		min-width: 0;
	}

	.param-textarea {
		grid-column: span 6;
	}

	.advanced-section :global(.field) {
		min-width: 0;
		width: 100%;
		max-width: none;
	}

	:global(.field) .param-input,
	:global(.field) .param-textarea {
		width: 100%;
	}

	:global(.field) .param-input {
		height: 28px;
		min-height: 28px;
		padding: 3px 8px;
		border-radius: 6px;
		font-size: 12px;
		line-height: 1.2;
		box-sizing: border-box;
	}

	:global(.field) .param-textarea {
		min-height: 68px;
		resize: vertical;
	}

	@media (max-width: 1100px) {
		.param-item {
			grid-column: span 4;
		}

		.param-textarea {
			grid-column: span 6;
		}
	}

	@media (max-width: 760px) {
		.param-item {
			grid-column: span 6;
		}

		.param-textarea {
			grid-column: 1 / -1;
		}

		.advanced-section :global(.field) {
			max-width: none;
		}
	}

	@media (max-width: 560px) {
		.advanced-section {
			gap: 8px;
		}

		.param-item {
			grid-column: 1 / -1;
		}
	}
</style>
