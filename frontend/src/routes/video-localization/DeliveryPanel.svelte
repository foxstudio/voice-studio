<script lang="ts">
	import type { BatchTask, VideoLocalizationQualityGate } from '$lib/api/types';
	import { Download, FileJson, Languages, Mic2, Wand2 } from 'lucide-svelte';
	import { batchOptionLabel, gateBadgeClass, gateLabel } from './utils';

	let {
		qualityGate,
		canSubmitCount,
		generatedCount,
		projectBatches,
		ttsBatchId,
		loadingBatches,
		submittingBatch,
		syncingBatch,
		hasDraft,
		canExportBilingual,
		onSubmitBatch,
		onSyncBatch,
		onExportJson,
		onExportReadiness,
		onExportBilingual,
		onTtsBatchIdChange
	}: {
		qualityGate: VideoLocalizationQualityGate | null | undefined;
		canSubmitCount: number;
		generatedCount: number;
		projectBatches: BatchTask[];
		ttsBatchId: string;
		loadingBatches: boolean;
		submittingBatch: boolean;
		syncingBatch: boolean;
		hasDraft: boolean;
		canExportBilingual: boolean;
		onSubmitBatch: () => void;
		onSyncBatch: () => void;
		onExportJson: () => void;
		onExportReadiness: () => void;
		onExportBilingual: () => void;
		onTtsBatchIdChange: (batchId: string) => void;
	} = $props();

	function updateBatchId(event: Event) {
		onTtsBatchIdChange((event.currentTarget as HTMLInputElement | HTMLSelectElement).value);
	}
</script>

<section class="panel export-panel">
	<div class="section-title">
		<h2>批量与交付</h2>
		<span class={`badge ${gateBadgeClass(qualityGate?.status)}`}>{gateLabel(qualityGate?.status)}</span>
	</div>
	<div class="handoff-summary">
		<div><strong>{canSubmitCount}</strong><span>可提交</span></div>
		<div><strong>{generatedCount}</strong><span>已生成</span></div>
		<div><strong>{qualityGate?.blockers.length ?? 0}</strong><span>阻断</span></div>
		<div><strong>{qualityGate?.warnings.length ?? 0}</strong><span>警告</span></div>
	</div>
	<p class="muted small-note">
		{qualityGate?.checked_at ? `最近检查：${qualityGate.checked_at}` : '保存或导出后会自动刷新质量门。'}
	</p>
	<div class="stack">
		<button class="btn success" type="button" onclick={onSubmitBatch} disabled={!canSubmitCount || submittingBatch}><Wand2 size={14} /> {submittingBatch ? '提交中' : '批量发送可生成片段'}</button>
		<div class="batch-sync-row">
			<select value={ttsBatchId} aria-label="选择当前项目批次" onchange={updateBatchId} disabled={loadingBatches}>
				<option value="">{loadingBatches ? '加载批次中' : projectBatches.length ? '选择最近批次' : '暂无项目批次'}</option>
				{#each projectBatches as batch}
					<option value={batch.batch_task_id}>{batchOptionLabel(batch)}</option>
				{/each}
			</select>
			<input value={ttsBatchId} oninput={updateBatchId} placeholder="batch id" aria-label="批量 TTS 任务 ID" />
			<button class="btn" type="button" onclick={onSyncBatch} disabled={!ttsBatchId.trim() || syncingBatch}>
				<Mic2 size={14} /> {syncingBatch ? '同步中' : '同步 TTS 结果'}
			</button>
		</div>
		<button class="btn" type="button" onclick={onExportJson} disabled={!hasDraft}><Download size={14} /> 下载 production JSON</button>
		<button class="btn" type="button" onclick={onExportReadiness} disabled={!hasDraft}><FileJson size={14} /> 下载 readiness JSON</button>
		<button class="btn" type="button" onclick={onExportBilingual} disabled={!canExportBilingual}><Languages size={14} /> 导出中英字幕草稿</button>
	</div>
</section>

<style>
	.handoff-summary {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 8px;
		margin-bottom: 8px;
	}

	.handoff-summary div {
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 8px;
		background: #101215;
	}

	.handoff-summary strong,
	.handoff-summary span {
		display: block;
	}

	.handoff-summary strong {
		font-size: 18px;
	}

	.handoff-summary span,
	.small-note {
		font-size: 12px;
	}

	.export-panel .btn {
		justify-content: center;
	}

	.batch-sync-row {
		display: grid;
		grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr) auto;
		gap: 8px;
	}

	.batch-sync-row input,
	.batch-sync-row select {
		min-width: 0;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 8px 10px;
		background: #fff;
		color: var(--ink);
	}
</style>
