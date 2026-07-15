<script lang="ts">
	import type { VideoLocalizationGlossaryEntry } from '$lib/api/types';
	import { BookOpenText, Check, Plus, Trash2, X } from 'lucide-svelte';

	let {
		open,
		glossary,
		sceneContext,
		onChange
	}: {
		open: boolean;
		glossary: VideoLocalizationGlossaryEntry[];
		sceneContext: string;
		onChange: (patch: { glossary?: VideoLocalizationGlossaryEntry[]; scene_context?: string }) => void;
	} = $props();

	let pendingEntry = $state<VideoLocalizationGlossaryEntry | null>(null);

	function createGlossaryId() {
		return globalThis.crypto?.randomUUID?.() ?? `glossary_${Date.now().toString(36)}`;
	}

	function addEntry() {
		if (pendingEntry) return;
		pendingEntry = {
			glossary_id: createGlossaryId(),
			source_text: '',
			corrected_source_text: null,
			zh_text: null,
			notes: null
		};
	}

	function commitPending() {
		if (!pendingEntry) return;
		const sourceText = pendingEntry.source_text.trim();
		if (!sourceText) return;
		onChange({ glossary: [...glossary, { ...pendingEntry, source_text: sourceText }] });
		pendingEntry = null;
	}

	function updatePending(field: 'corrected_source_text' | 'zh_text' | 'notes', value: string) {
		if (!pendingEntry) return;
		pendingEntry[field] = value || null;
	}

	function updateEntry(entryId: string, patch: Partial<VideoLocalizationGlossaryEntry>) {
		onChange({
			glossary: glossary.map((entry) => (entry.glossary_id === entryId ? { ...entry, ...patch } : entry))
		});
	}

	function updateSourceEntry(entry: VideoLocalizationGlossaryEntry, input: HTMLInputElement) {
		const value = input.value.trim();
		if (!value) {
			input.value = entry.source_text;
			return;
		}
		updateEntry(entry.glossary_id, { source_text: value });
	}

	function removeEntry(entryId: string) {
		onChange({ glossary: glossary.filter((entry) => entry.glossary_id !== entryId) });
	}
</script>

{#if open}
	<section class="subtitle-workflow-settings" aria-label="字幕规则与术语">
		<header class="settings-head">
			<div class="settings-title">
				<BookOpenText size={15} />
				<strong>字幕规则与术语</strong>
				<span>{glossary.length} 条</span>
			</div>
			<button class="add-entry" type="button" onclick={addEntry} disabled={Boolean(pendingEntry)} data-tooltip="新增术语：填写原词后加入当前项目草稿。">
				<Plus size={13} /> 新增术语
			</button>
		</header>

		<label class="scene-context">
			<span>场景上下文</span>
			<textarea
				rows="2"
				value={sceneContext}
				placeholder="人物关系、地点、时代背景或需要保持一致的语气"
				oninput={(event) => onChange({ scene_context: event.currentTarget.value })}
			></textarea>
		</label>

		<div class="glossary-grid" role="table" aria-label="项目术语表">
			<div class="glossary-head" role="row">
				<span role="columnheader">原词</span>
				<span role="columnheader">校正原词</span>
				<span role="columnheader">中文</span>
				<span role="columnheader">备注</span>
				<span aria-hidden="true"></span>
			</div>
			{#each glossary as entry (entry.glossary_id)}
				<div class="glossary-row" role="row">
					<input aria-label="原词" value={entry.source_text} required onblur={(event) => updateSourceEntry(entry, event.currentTarget)} />
					<input aria-label="校正原词" value={entry.corrected_source_text ?? ''} oninput={(event) => updateEntry(entry.glossary_id, { corrected_source_text: event.currentTarget.value || null })} />
					<input aria-label="中文术语" value={entry.zh_text ?? ''} oninput={(event) => updateEntry(entry.glossary_id, { zh_text: event.currentTarget.value || null })} />
					<input aria-label="术语备注" value={entry.notes ?? ''} oninput={(event) => updateEntry(entry.glossary_id, { notes: event.currentTarget.value || null })} />
					<div class="entry-actions">
						<button class="remove-entry" type="button" aria-label={`删除术语 ${entry.source_text}`} data-tooltip="删除术语" onclick={() => removeEntry(entry.glossary_id)}>
							<Trash2 size={13} />
						</button>
					</div>
				</div>
			{/each}
			{#if pendingEntry}
				<div class="glossary-row pending" role="row">
					<input aria-label="新术语原词" placeholder="必填" oninput={(event) => (pendingEntry!.source_text = event.currentTarget.value)} onkeydown={(event) => event.key === 'Enter' && commitPending()} />
					<input aria-label="新术语校正原词" placeholder="可选" oninput={(event) => updatePending('corrected_source_text', event.currentTarget.value)} />
					<input aria-label="新术语中文" placeholder="可选" oninput={(event) => updatePending('zh_text', event.currentTarget.value)} />
					<input aria-label="新术语备注" placeholder="可选" oninput={(event) => updatePending('notes', event.currentTarget.value)} />
					<div class="entry-actions">
						<button class="remove-entry confirm-entry" type="button" aria-label="确认新增术语" data-tooltip="加入术语表" disabled={!pendingEntry.source_text.trim()} onclick={commitPending}>
							<Check size={13} />
						</button>
						<button class="remove-entry" type="button" aria-label="取消新增术语" data-tooltip="取消新增" onclick={() => (pendingEntry = null)}>
							<X size={13} />
						</button>
					</div>
				</div>
			{:else if !glossary.length}
				<p class="empty-glossary">暂无项目术语</p>
			{/if}
		</div>
	</section>
{/if}

<style>
	.subtitle-workflow-settings {
		display: grid;
		gap: 9px;
		padding: 10px;
		border-block: 1px solid var(--line);
		background: #15191e;
	}

	.settings-head,
	.settings-title {
		display: flex;
		align-items: center;
	}

	.settings-head {
		justify-content: space-between;
		gap: 10px;
	}

	.settings-title {
		gap: 6px;
		color: #dce4e7;
	}

	.settings-title strong {
		font-size: 12px;
	}

	.settings-title span {
		color: #7f8c93;
		font-size: 10px;
	}

	.add-entry,
	.remove-entry {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: #20262b;
		color: #cfd8dc;
		cursor: pointer;
	}

	.entry-actions {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 3px;
	}

	.confirm-entry {
		color: #8fd5bd;
	}

	.remove-entry:disabled {
		opacity: 0.38;
		cursor: not-allowed;
	}

	.add-entry {
		gap: 5px;
		min-height: 26px;
		padding: 3px 8px;
		font-size: 11px;
	}

	.remove-entry {
		width: 28px;
		height: 28px;
		padding: 0;
		color: #9ba7ad;
	}

	.add-entry:hover:not(:disabled),
	.add-entry:focus-visible,
	.remove-entry:hover,
	.remove-entry:focus-visible {
		border-color: rgba(113, 224, 215, 0.68);
		color: #efffff;
		outline: none;
	}

	.add-entry:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}

	.scene-context {
		display: grid;
		grid-template-columns: 92px minmax(0, 1fr);
		align-items: start;
		gap: 8px;
	}

	.scene-context > span {
		padding-top: 7px;
		color: #93a0a7;
		font-size: 10px;
		font-weight: 700;
	}

	.scene-context textarea,
	.glossary-row input {
		box-sizing: border-box;
		width: 100%;
		border: 1px solid #343d43;
		border-radius: 5px;
		background: #101418;
		color: #dce4e7;
		font: inherit;
		font-size: 11px;
	}

	.scene-context textarea {
		min-height: 48px;
		padding: 6px 8px;
		line-height: 16px;
		resize: vertical;
	}

	.glossary-grid {
		display: grid;
		gap: 4px;
	}

	.glossary-head,
	.glossary-row {
		display: grid;
		grid-template-columns: minmax(110px, 1fr) minmax(110px, 1fr) minmax(110px, 1fr) minmax(140px, 1.25fr) 59px;
		gap: 5px;
		align-items: center;
	}

	.glossary-head {
		padding-inline: 7px 0;
		color: #7f8c93;
		font-size: 9px;
		font-weight: 700;
	}

	.glossary-row input {
		height: 28px;
		padding: 3px 7px;
	}

	.scene-context textarea:focus,
	.glossary-row input:focus {
		border-color: rgba(87, 208, 200, 0.72);
		outline: 1px solid rgba(87, 208, 200, 0.2);
	}

	.glossary-row.pending input:first-child {
		border-color: rgba(205, 151, 73, 0.58);
	}

	.empty-glossary {
		margin: 0;
		padding: 6px 7px;
		color: #6f7c83;
		font-size: 10px;
	}

	@media (max-width: 900px) {
		.scene-context {
			grid-template-columns: 1fr;
			gap: 4px;
		}

		.scene-context > span {
			padding-top: 0;
		}

		.glossary-head {
			display: none;
		}

		.glossary-row {
			grid-template-columns: repeat(2, minmax(0, 1fr)) 59px;
		}

		.glossary-row input:nth-child(4) {
			grid-column: 1 / 3;
		}

		.glossary-row .entry-actions {
			grid-column: 3;
			grid-row: 1;
		}
	}
</style>
