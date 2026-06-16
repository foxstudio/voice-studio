<script lang="ts">
	import { ClipboardPaste, X } from 'lucide-svelte';

	let {
		open,
		cueCount,
		onApply,
		onClose
	}: {
		open: boolean;
		cueCount: number;
		onApply: (text: string) => void;
		onClose: () => void;
	} = $props();

	let text = $state('');

	function apply() {
		onApply(text);
		text = '';
	}
</script>

{#if open}
	<section class="localization-import">
		<div class="import-head">
			<div>
				<strong>粘贴中文稿</strong>
				<p class="muted">每行对应一个 cue；可写成“中文字幕 || TTS台词”。单列只填中文字幕。</p>
			</div>
			<button class="mini-btn" type="button" onclick={onClose} aria-label="关闭中文稿导入"><X size={13} /></button>
		</div>
		<textarea
			rows="7"
			value={text}
			placeholder={`共 ${cueCount} 条 cue。示例：\n这是一九九二年的故事 || 这是一九九二年的故事\n他回头看向大海 || 他，回头看向大海`}
			oninput={(event) => (text = event.currentTarget.value)}
		></textarea>
		<div class="import-actions">
			<span class="muted">{text.split(/\r?\n/).filter((line) => line.trim()).length} 行</span>
			<button class="btn primary" type="button" onclick={apply} disabled={!text.trim() || !cueCount}>
				<ClipboardPaste size={14} /> 应用到 cue
			</button>
		</div>
	</section>
{/if}

<style>
	.localization-import {
		display: grid;
		gap: 8px;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 10px;
		margin-bottom: 10px;
		background: #101215;
	}

	.import-head,
	.import-actions {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 10px;
	}

	.import-head strong,
	.import-head p {
		margin: 0;
	}

	.import-head p {
		font-size: 12px;
		line-height: 1.45;
	}

	.localization-import textarea {
		min-height: 130px;
		resize: vertical;
	}
</style>
