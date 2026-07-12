<script lang="ts">
	import Field from '$lib/components/shared/Field.svelte';
	import Slider from '$lib/components/shared/Slider.svelte';
	import TextInputToolbar from '../../components/TextInputToolbar.svelte';
	import ReferenceAudioSlotCard from './components/ReferenceAudioSlotCard.svelte';
	import ReferenceImageInput from './components/ReferenceImageInput.svelte';
	import { insertAudioPromptReference, validateAudioPromptReferences } from './prompt-references';
	import {
		activeSeedAudioDraft,
		setSeedAudioImage,
		setSeedAudioReference,
		updateSeedAudioParameters,
		updateSeedAudioPrompt,
		type SeedAudioReferenceAsset,
		type SeedAudioSampleRate,
		type SeedAudioState
	} from './state';
	import { seedAudioPromptHelp } from './ui';
	import { validateSeedAudioState } from './validation';

	interface Props {
		state: SeedAudioState;
		showAdvanced?: boolean;
		generateBusy?: boolean;
		assetBusy?: boolean;
		onChange?: (state: SeedAudioState) => void;
		onGenerate?: () => void;
		onTextTool?: (mode: 'clean' | 'numbers' | 'split') => void;
		textToolBusy?: string;
		onUploadAudio?: (slot: 1 | 2 | 3, file: File) => void;
		onChooseVoice?: (slot: 1 | 2 | 3) => void;
		onChooseSpeaker?: (slot: 1 | 2 | 3) => void;
		onEditAudio?: (slot: 1 | 2 | 3, asset: SeedAudioReferenceAsset) => void;
		onPreviewAudio?: (slot: 1 | 2 | 3, asset: SeedAudioReferenceAsset) => void;
		onUploadImage?: (file: File) => void;
	}

	let {
		state: value,
		showAdvanced = false,
		generateBusy = false,
		assetBusy = false,
		onChange = () => {},
		onGenerate = () => {},
		onTextTool = () => {},
		textToolBusy = '',
		onUploadAudio = () => {},
		onChooseVoice = () => {},
		onChooseSpeaker = () => {},
		onEditAudio = () => {},
		onPreviewAudio = () => {},
		onUploadImage = () => {}
	}: Props = $props();

	let promptInput: HTMLTextAreaElement;
	let showReferenceMenu = $state(false);
	const draft = $derived(activeSeedAudioDraft(value));
	const validation = $derived(validateSeedAudioState(value));
	const promptReferenceStatus = $derived(value.mode === 'audio' ? validateAudioPromptReferences(value.drafts.audio.prompt, value.drafts.audio.references) : null);
	const requiredErrors = $derived(validation.errors.filter((entry) => entry.path === 'image' || entry.path === 'references' || entry.path?.startsWith('references.')));
	const promptErrors = $derived(validation.errors.filter((entry) => entry.path === 'prompt' || entry.path?.startsWith('prompt.')));
	const advancedErrors = $derived(validation.errors.filter((entry) => entry.path?.startsWith('parameters.')));
	const canGenerate = $derived(validation.errors.length === 0 && !generateBusy && !assetBusy);

	function change(next: SeedAudioState) { onChange(next); }
	function updatePrompt(prompt: string, cursor: number | null) {
		change(updateSeedAudioPrompt(value, prompt));
		showReferenceMenu = value.mode === 'audio' && cursor !== null && prompt.slice(0, cursor).endsWith('@');
	}
	function insertReference(slot: 1 | 2 | 3) {
		const cursor = promptInput?.selectionStart ?? draft.prompt.length;
		change(updateSeedAudioPrompt(value, insertAudioPromptReference(draft.prompt, slot, cursor)));
		showReferenceMenu = false;
		requestAnimationFrame(() => promptInput?.focus());
	}
	function numberParameter(key: 'loudness_rate' | 'pitch_rate', raw: number) {
		change(updateSeedAudioParameters(value, { [key]: raw }));
	}
	function removeReference(slot: 1 | 2 | 3) { change(setSeedAudioReference(value, slot, null)); }
	function removeImage() { change(setSeedAudioImage(value, null)); }
</script>

<section class="seed-panel" aria-label="Seed Audio 生成设置">
	{#if showAdvanced}
		<section class="seed-advanced-panel more-params-panel" aria-label="Seed Audio 高级参数">
			<div class="seed-advanced-row">
				<label class="seed-select-field"><span>采样率</span><select aria-label="Seed Audio 采样率" value={draft.parameters.sample_rate} onchange={(event) => change(updateSeedAudioParameters(value, { sample_rate: Number(event.currentTarget.value) as SeedAudioSampleRate }))}>{#each [8000, 16000, 24000, 32000, 44100, 48000] as rate}<option value={rate}>{rate === 44100 ? '44.1' : rate / 1000} kHz</option>{/each}</select></label>
				<div class="seed-slider-field"><Field label="音量" tooltip="调整输出音量，0 为原始音量。"><Slider value={draft.parameters.loudness_rate} min={-50} max={100} step={1} onChange={(next) => numberParameter('loudness_rate', next)} /></Field></div>
				<div class="seed-slider-field"><Field label="音调" tooltip="调整输出音调，0 为原始音调。"><Slider value={draft.parameters.pitch_rate} min={-12} max={12} step={1} onChange={(next) => numberParameter('pitch_rate', next)} /></Field></div>
				<label class="seed-advanced-toggle"><input type="checkbox" checked={draft.parameters.enable_subtitle} onchange={(event) => change(updateSeedAudioParameters(value, { enable_subtitle: event.currentTarget.checked }))} /><span><strong>返回字幕时间戳</strong><small>用于后续对齐字幕或画面</small></span></label>
				<label class="seed-advanced-toggle"><input type="checkbox" checked={draft.parameters.aigc_watermark} onchange={(event) => change(updateSeedAudioParameters(value, { aigc_watermark: event.currentTarget.checked }))} /><span><strong>显式音频水印</strong><small>加入可识别音频水印</small></span></label>
				<label class="seed-advanced-toggle"><input type="checkbox" checked={draft.parameters.aigc_metadata.enable} onchange={(event) => change(updateSeedAudioParameters(value, { aigc_metadata: { ...draft.parameters.aigc_metadata, enable: event.currentTarget.checked } }))} /><span><strong>隐式来源信息</strong><small>写入制作方和传播方信息</small></span></label>
			</div>
			{#if draft.parameters.aigc_metadata.enable}
				<div class="metadata-group">
					<div class="metadata-grid">
						<label class="seed-metadata-field"><span>内容制作方</span><input type="text" value={draft.parameters.aigc_metadata.metadata.content_producer} oninput={(event) => change(updateSeedAudioParameters(value, { aigc_metadata: { ...draft.parameters.aigc_metadata, metadata: { ...draft.parameters.aigc_metadata.metadata, content_producer: event.currentTarget.value } } }))} /></label>
						<label class="seed-metadata-field"><span>制作方 ID</span><input type="text" value={draft.parameters.aigc_metadata.metadata.produce_id} oninput={(event) => change(updateSeedAudioParameters(value, { aigc_metadata: { ...draft.parameters.aigc_metadata, metadata: { ...draft.parameters.aigc_metadata.metadata, produce_id: event.currentTarget.value } } }))} /></label>
						<label class="seed-metadata-field"><span>内容传播方</span><input type="text" value={draft.parameters.aigc_metadata.metadata.content_propagator} oninput={(event) => change(updateSeedAudioParameters(value, { aigc_metadata: { ...draft.parameters.aigc_metadata, metadata: { ...draft.parameters.aigc_metadata.metadata, content_propagator: event.currentTarget.value } } }))} /></label>
						<label class="seed-metadata-field"><span>传播方 ID</span><input type="text" value={draft.parameters.aigc_metadata.metadata.propagate_id} oninput={(event) => change(updateSeedAudioParameters(value, { aigc_metadata: { ...draft.parameters.aigc_metadata, metadata: { ...draft.parameters.aigc_metadata.metadata, propagate_id: event.currentTarget.value } } }))} /></label>
					</div>
				</div>
			{/if}
			{#if advancedErrors.length}<div class="seed-field-errors span-full">{#each advancedErrors as item}<span>{item.message}</span>{/each}</div>{/if}
		</section>
	{/if}

	{#if value.mode === 'audio'}
		<section class="seed-required-block" aria-label="参考声音必填区">
			<div class="seed-required-head"><div><strong>参考声音 <em>必填</em></strong><span>至少添加 1 条，最多 3 条；每条不超过 30 秒 / 10MB。</span></div><b>{value.drafts.audio.references.filter((slot) => slot.asset).length} / 3</b></div>
			<div class="reference-grid">
				{#each value.drafts.audio.references as slot}
					<ReferenceAudioSlotCard
						{slot}
						onUpload={onUploadAudio}
						{onChooseVoice}
						{onChooseSpeaker}
						onEdit={(slotNumber) => { const asset = value.drafts.audio.references[slotNumber - 1]?.asset; if (asset) onEditAudio(slotNumber, asset); }}
						onPreview={(slotNumber) => { const asset = value.drafts.audio.references[slotNumber - 1]?.asset; if (asset) onPreviewAudio(slotNumber, asset); }}
						onRemove={removeReference}
					/>
				{/each}
			</div>
			{#if requiredErrors.length}<div class="seed-field-errors">{#each requiredErrors as item}<span>{item.message}</span>{/each}</div>{/if}
		</section>
	{:else if value.mode === 'image'}
		<section class="seed-required-block" aria-label="参考图片必填区">
			<div class="seed-required-head"><div><strong>参考图片 <em>必填</em></strong><span>上传 1 张角色或场景图片，支持 JPEG、PNG、WebP，不超过 10MB。</span></div></div>
			<ReferenceImageInput image={value.drafts.image.image} onUpload={onUploadImage} onRemove={removeImage} />
			{#if requiredErrors.length}<div class="seed-field-errors">{#each requiredErrors as item}<span>{item.message}</span>{/each}</div>{/if}
		</section>
	{/if}

	<div class="prompt-composer">
		<p class="prompt-guidance">{seedAudioPromptHelp(value.mode)}</p>
		<div class="prompt-wrap">
			<textarea
				bind:this={promptInput}
				value={draft.prompt}
				aria-label="生成描述"
				placeholder={value.mode === 'audio' ? '例如：@音频1 用克制的语气说出开场，随后加入轻微雨声…' : value.mode === 'image' ? '例如：画面中的女孩轻声说出对白，背景是清晨街道…' : '例如：安静的录音室里，一名女性自然地讲述…'}
				oninput={(event) => updatePrompt(event.currentTarget.value, event.currentTarget.selectionStart)}
			></textarea>
			{#if showReferenceMenu && value.mode === 'audio'}
				<div class="reference-menu" role="menu" aria-label="插入参考声音">
					{#each value.drafts.audio.references as slot}<button type="button" disabled={!slot.asset} onclick={() => insertReference(slot.slot)}><b>@音频{slot.slot}</b><span>{slot.asset?.displayName ?? '尚未添加声音'}</span></button>{/each}
				</div>
			{/if}
		</div>
		{#if value.mode === 'audio'}
			<div class="reference-insert-row"><span>插入引用</span>{#each value.drafts.audio.references as slot}<button type="button" disabled={!slot.asset} onclick={() => insertReference(slot.slot)}>@音频{slot.slot}</button>{/each}</div>
			{#if promptReferenceStatus?.warnings.length}<div class="reference-status">{#each promptReferenceStatus.warnings as item}<span>{item.message}</span>{/each}</div>{/if}
		{/if}
		{#if promptErrors.length}<div class="seed-field-errors">{#each promptErrors as item}<span>{item.message}</span>{/each}</div>{/if}
		<TextInputToolbar
			textLength={draft.prompt.length}
			hasText={Boolean(draft.prompt.trim())}
			{textToolBusy}
			{generateBusy}
			generateDisabled={!canGenerate}
			onTextTool={onTextTool}
			onGenerate={onGenerate}
		/>
	</div>
</section>

<style>
	.seed-panel { display: grid; gap: 12px; color: var(--text); }
	.seed-advanced-panel { margin-top: 2px; gap: 8px; }
	.seed-advanced-panel :global(.span-full) { grid-column: 1 / -1; }
	.seed-advanced-row { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(145px, 160px) repeat(2, minmax(160px, 190px)) repeat(3, minmax(185px, 220px)); align-items: center; gap: 8px 12px; min-width: 0; }
	.metadata-group { grid-column: 1 / -1; border-top: 1px solid rgba(104, 123, 146, .18); padding-top: 8px; }
	.metadata-grid { grid-template-columns: repeat(4, minmax(180px, 230px)); }
	.metadata-grid { display: grid; justify-content: start; gap: 8px 12px; }
	.seed-select-field,
	.seed-slider-field,
	.seed-advanced-toggle,
	.seed-metadata-field { min-width: 0; }
	.seed-select-field { display: grid; gap: 5px; color: var(--muted); font-size: 11px; }
	.seed-select-field select { box-sizing: border-box; width: 100%; min-height: 30px; border: 1px solid var(--line); border-radius: 6px; background: #101215; padding: 4px 8px; color: var(--text); font: inherit; }
	.seed-slider-field { min-width: 0; padding-top: 1px; }
	.seed-slider-field :global(.shared-field) { display: grid; gap: 8px; }
	.seed-slider-field :global(.slider) { grid-template-columns: minmax(0, 1fr) 42px; gap: 6px; }
	.seed-slider-field :global(.value) { text-align: left; }
	.seed-advanced-toggle { display: flex; align-items: center; gap: 8px; min-height: 38px; color: var(--text); font-size: 11px; }
	.seed-advanced-toggle span,
	.seed-advanced-toggle strong,
	.seed-advanced-toggle small { display: block; }
	.seed-advanced-toggle small { margin-top: 2px; color: var(--muted); font-size: 10px; font-weight: 400; }
	.seed-metadata-field { display: grid; gap: 5px; color: var(--muted); font-size: 11px; }
	.seed-metadata-field input { box-sizing: border-box; width: 100%; min-height: 30px; border: 1px solid var(--line); border-radius: 6px; background: #101215; padding: 5px 8px; color: var(--text); }
	.seed-required-block { display: grid; gap: 10px; border-top: 1px solid var(--line); padding-top: 12px; }
	.seed-required-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
	.seed-required-head strong,
	.seed-required-head span { display: block; }
	.seed-required-head strong { font-size: 12px; }
	.seed-required-head strong em { margin-left: 5px; color: #e4a09a; font-size: 10px; font-style: normal; font-weight: 600; }
	.seed-required-head span { margin-top: 3px; color: var(--muted); font-size: 10.5px; }
	.seed-required-head > b { color: var(--muted); font-size: 11px; }
	.reference-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
	.prompt-composer { display: grid; gap: 7px; }
	.reference-insert-row button { border: 1px solid var(--line); border-radius: 6px; background: var(--panel-2); padding: 4px 8px; color: var(--text); font: inherit; font-size: 10.5px; cursor: pointer; }
	.prompt-guidance { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.45; }
	.prompt-wrap { position: relative; }
	textarea { box-sizing: border-box; width: 100%; min-height: 168px; resize: vertical; border: 1px solid var(--line); border-radius: 8px; background: #0d1115; padding: 11px 12px; color: var(--text); font: inherit; font-size: 13px; line-height: 1.65; }
	textarea:focus { border-color: var(--accent); outline: 2px solid rgba(90, 151, 220, .16); }
	.reference-menu { position: absolute; z-index: 5; left: 12px; bottom: 12px; width: min(310px, calc(100% - 24px)); border: 1px solid #425064; border-radius: 8px; background: #171d25; padding: 5px; box-shadow: 0 12px 30px rgba(0,0,0,.4); }
	.reference-menu button { display: grid; grid-template-columns: 70px minmax(0,1fr); width: 100%; border: 0; background: transparent; padding: 7px; color: #dce7f3; text-align: left; cursor: pointer; }
	.reference-menu button:disabled { opacity: .42; }
	.reference-menu b { color: #9bc5f6; font: 700 11px ui-monospace, monospace; }
	.reference-menu span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
	.reference-insert-row,
	.reference-status,
	.seed-field-errors { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
	.reference-insert-row > span { color: var(--muted); font-size: 10.5px; }
	.reference-insert-row button:disabled { opacity: .35; }
	.reference-status span { color: #bda779; font-size: 10.5px; }
	.seed-field-errors span { color: #ef9a92; font-size: 10.5px; }
	.seed-panel button:hover:not(:disabled),
	.seed-panel button:focus-visible:not(:disabled) { border-color: var(--accent); outline: none; }
	@media (max-width: 1200px) {
		.seed-advanced-row { grid-template-columns: repeat(3, minmax(180px, 1fr)); }
	}
	@media (max-width: 980px) {
		.reference-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.metadata-grid { grid-template-columns: repeat(2, minmax(180px, 230px)); }
	}
	@media (max-width: 640px) {
		.reference-grid { grid-template-columns: 1fr; }
		.seed-advanced-row,
		.metadata-grid { grid-template-columns: minmax(0, 1fr); }
	}
	@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
