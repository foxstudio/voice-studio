<script lang="ts">
	import type { ParameterSchema } from '$lib/api/types';
	import Field from '$lib/components/shared/Field.svelte';
	import Slider from '$lib/components/shared/Slider.svelte';
	import Select from '$lib/components/shared/Select.svelte';
	import Toggle from '$lib/components/shared/Toggle.svelte';

	interface Props {
		engineId?: string;
		parameterSchema: ParameterSchema[];
		values: Record<string, unknown>;
		onChange?: (key: string, value: unknown) => void;
		autoExpand?: boolean;
	}

	let {
		engineId = '',
		parameterSchema = [],
		values = {},
		onChange = () => {},
		autoExpand = false
	}: Props = $props();

	const basicParams = $derived(parameterSchema.filter((p) => p.level === 'basic'));
	const advancedParams = $derived(
		parameterSchema.filter((p) => p.level === 'advanced' || p.level === 'developer')
	);
	type ParameterGroup = { label: string; params: ParameterSchema[]; showTitle: boolean };

	type GroupDefinition = { label: string; keys: string[] };
	const ENGINE_ADVANCED_LAYOUT: Record<string, GroupDefinition[]> = {
		'indextts-v2': [
			{ label: '生成控制', keys: ['temperature', 'top_p', 'top_k', 'repetition_penalty'] },
			{ label: '质量与长文本', keys: ['cfg_rate', 'diffusion_steps', 'max_mel_tokens', 'max_text_tokens_per_segment', 'interval_silence'] }
		],
		omnivoice: [
			{ label: '生成质量', keys: ['diffusion_steps', 'guidance_scale'] },
			{ label: '时长与分段', keys: ['duration', 'audio_chunk_duration', 'audio_chunk_threshold'] },
			{ label: '开发调试', keys: ['t_shift', 'layer_penalty_factor', 'position_temperature', 'class_temperature', 'denoise', 'preprocess_prompt', 'postprocess_output'] }
		],
		'confucius4-mlx-int8': [
			{ label: '生成与复现', keys: ['temperature', 'top_p', 'top_k', 'repetition_penalty', 'diffusion_steps', 'cfg_rate', 'seed'] }
		],
		'qwen3-tts-mlx-0.6b': [
			{ label: '生成与长度', keys: ['temperature', 'top_p', 'top_k', 'repetition_penalty', 'max_tokens'] }
		],
		'f5-tts': [
			{ label: '质量与衔接', keys: ['nfe_step', 'cfg_strength', 'cross_fade_duration', 'remove_silence'] },
			{ label: '调试与复现', keys: ['target_rms', 'sway_sampling_coef', 'fix_duration', 'seed'] }
		],
		'doubao-tts-preset': [
			{ label: '文本处理', keys: ['max_length_to_filter_parenthesis', 'disable_markdown_filter', 'latex_parser_mode'] },
			{ label: '来源信息', keys: ['aigc_metadata_enable', 'content_producer', 'produce_id', 'content_propagator', 'propagate_id'] }
		],
		'doubao-tts-voiceclone': [
			{ label: '文本处理', keys: ['max_length_to_filter_parenthesis', 'disable_markdown_filter', 'latex_parser_mode'] },
			{ label: '来源信息', keys: ['aigc_metadata_enable', 'content_producer', 'produce_id', 'content_propagator', 'propagate_id'] }
		],
		'doubao-seed-audio-1.0': [
			{ label: '输出质量', keys: ['sample_rate', 'loudness_rate', 'pitch_rate'] },
			{ label: '交付标记', keys: ['enable_subtitle', 'aigc_watermark'] },
			{ label: '来源信息', keys: ['aigc_metadata_enable', 'content_producer', 'produce_id', 'content_propagator', 'propagate_id'] }
		]
	};

	function inferredEngineId(params: ParameterSchema[]): string {
		const keys = new Set(params.map((param) => param.key));
		if (keys.has('t_shift')) return 'omnivoice';
		if (keys.has('nfe_step')) return 'f5-tts';
		if (keys.has('max_mel_tokens')) return 'indextts-v2';
		if (keys.has('max_tokens')) return 'qwen3-tts-mlx-0.6b';
		if (keys.has('max_length_to_filter_parenthesis')) return 'doubao-tts-preset';
		if (keys.has('cfg_rate')) return 'confucius4-mlx-int8';
		return '';
	}

	const layoutEngineId = $derived(engineId || inferredEngineId(advancedParams));

	const advancedGroups = $derived.by((): ParameterGroup[] => {
		const byKey = new Map(advancedParams.map((param) => [param.key, param]));
		const used = new Set<string>();
		const configured = (ENGINE_ADVANCED_LAYOUT[layoutEngineId] ?? []).flatMap((group) => {
			const params = group.keys.flatMap((key) => {
				const param = byKey.get(key);
				if (!param) return [];
				used.add(key);
				return [param];
			});
			return params.length ? [{ label: group.label, params, showTitle: params.length > 1 }] : [];
		});
		const unconfigured = advancedParams.filter((param) => !used.has(param.key));
		return unconfigured.length
			? [...configured, { label: '其他高级设置', params: unconfigured, showTitle: unconfigured.length > 1 }]
			: configured;
	});

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
				{#each advancedGroups as group}
					<section class="advanced-group" class:source-info-group={group.label === '来源信息'} aria-label={group.label}>
						{#if group.showTitle}<h3 class="param-group-title">{group.label}</h3>{/if}
						<div class="advanced-group-grid">
							{#each group.params as param}
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
					</section>
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
		gap: 10px;
		padding-top: 0;
	}

	.advanced-group {
		display: grid;
		gap: 6px;
		min-width: 0;
	}

	.advanced-group + .advanced-group {
		padding-top: 8px;
	}

	.advanced-group-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 8px 10px;
		align-items: start;
	}

	.param-item {
		grid-column: auto;
		min-width: 0;
	}

	.param-group-title {
		display: flex;
		align-items: center;
		gap: 7px;
		min-height: 18px;
		margin: 0;
		color: var(--text);
		font-size: 12px;
		font-weight: 600;
		line-height: 18px;
		letter-spacing: 0.015em;
	}

	.param-group-title::before {
		content: '';
		width: 3px;
		height: 12px;
		border-radius: 999px;
		background: var(--accent);
		box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 32%, transparent);
		flex: 0 0 auto;
	}

	.source-info-group .advanced-group-grid > .param-item:last-child {
		grid-column: span 2;
	}

	.param-textarea {
		grid-column: span 2;
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
		height: 32px;
		min-height: 32px;
		padding: 5px 8px;
		border-radius: 7px;
		font-size: 12px;
		line-height: 1.2;
		box-sizing: border-box;
	}

	:global(.field) .param-textarea {
		min-height: 32px;
		line-height: 18px;
		resize: vertical;
	}

	.advanced-section :global(.select-trigger) {
		min-height: 32px;
		height: 32px;
		padding: 5px 8px;
		font-size: 12px;
	}

	@media (max-width: 1000px) {
		.advanced-group-grid {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}

		.param-item {
			grid-column: auto;
		}

		.param-textarea {
			grid-column: span 6;
		}
	}

	@media (max-width: 760px) {
		.advanced-group-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}

		.param-item {
			grid-column: auto;
		}

		.param-textarea {
			grid-column: 1 / -1;
		}

		.advanced-section :global(.field) {
			max-width: none;
		}
	}

	@media (max-width: 560px) {
		.advanced-group-grid {
			grid-template-columns: 1fr;
			gap: 8px;
		}

		.param-item {
			grid-column: 1 / -1;
		}
	}
</style>
