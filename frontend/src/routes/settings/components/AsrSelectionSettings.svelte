<script lang="ts">
	import { Api } from '$lib/api';
	import type { AppSettings, AsrSelectionStatus } from '$lib/api/types';
	import { AudioLines, ChevronRight } from 'lucide-svelte';
	import { onMount } from 'svelte';
	import SettingsRow from './SettingsRow.svelte';

	let { settings, onChange }: { settings: AppSettings; onChange: () => void } = $props();
	let selection = $state<AsrSelectionStatus | null>(null);

	onMount(async () => {
		try {
			selection = await Api.asrSelection();
		} catch {
			selection = null;
		}
	});

	function selectedLabel(engineId: string | undefined) {
		const labels: Record<string, string> = {
			'vibevoice-asr-mlx-4bit': 'VibeVoice 4bit',
			'vibevoice-asr-mlx-8bit': 'VibeVoice 8bit',
			'qwen3-asr-mlx': 'Qwen3-ASR',
			'faster-whisper-turbo': 'Faster Whisper'
		};
		return labels[engineId ?? ''] ?? engineId ?? '读取中';
	}
</script>

<SettingsRow
	icon={AudioLines}
	title="默认语音识别"
	description="自动模式按任务类型、已安装模型和本机条件综合选择"
	controlId="default-asr-engine"
	stacked
>
	<div class="asr-selection-control">
		<select id="default-asr-engine" bind:value={settings.default_asr_engine_id} onchange={onChange}>
			<option value="auto">自动综合（推荐）</option>
			<option value="vibevoice-asr-mlx-4bit">固定 VibeVoice 4bit</option>
			<option value="vibevoice-asr-mlx-8bit">固定 VibeVoice 8bit</option>
			<option value="qwen3-asr-mlx">固定 Qwen3-ASR</option>
			<option value="faster-whisper-turbo">固定 Faster Whisper</option>
		</select>
		<div class="selection-result">
			<span>当前长视频：<strong>{selectedLabel(selection?.engine_id)}</strong></span>
			<small>{selection?.reason ?? '模型状态可在引擎管理中查看'}</small>
		</div>
		<a class="manage-link" href="/engine-hub?type=asr">管理 ASR 模型 <ChevronRight size={14} /></a>
	</div>
</SettingsRow>

<style>
	.asr-selection-control { display: grid; grid-template-columns: minmax(210px, 300px) minmax(180px, 1fr) auto; align-items: center; gap: 10px; width: 100%; }
	select { width: 100%; min-height: 34px; border: 1px solid #2c3541; border-radius: 7px; background: #0d1218; color: #e8edf4; padding: 0 30px 0 9px; }
	.selection-result { display: grid; gap: 2px; min-width: 0; color: #8793a1; font-size: 10px; }
	.selection-result span, .selection-result small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.selection-result strong { color: #dbeafe; font-weight: 650; }
	.manage-link { display: inline-flex; align-items: center; gap: 3px; color: #82b8f2; font-size: 10px; text-decoration: none; white-space: nowrap; }
	.manage-link:hover { color: #b9d8fb; }
	@media (max-width: 980px) { .asr-selection-control { grid-template-columns: minmax(210px, 1fr) auto; } .selection-result { grid-column: 1 / -1; grid-row: 2; } }
	@media (max-width: 720px) { .asr-selection-control { grid-template-columns: 1fr; } .manage-link, .selection-result { grid-column: 1; } select { min-height: 44px; } }
</style>
