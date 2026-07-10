<script lang="ts">
	import { ClipboardPaste, FileText, X } from 'lucide-svelte';

	let {
		open,
		cueCount,
		onApply,
		onApplySrt,
		onClose
	}: {
		open: boolean;
		cueCount: number;
		onApply: (text: string) => void;
		onApplySrt: (text: string) => void | Promise<void>;
		onClose: () => void;
	} = $props();

	let text = $state('');
	let srtInput = $state<HTMLInputElement | null>(null);

	function apply() {
		onApply(text);
		text = '';
	}

	async function importSrtFile(file: File | null | undefined) {
		if (!file) return;
		const content = await file.text();
		onApplySrt(content);
		if (srtInput) srtInput.value = '';
	}
</script>

{#if open}
	<section class="localization-import">
		<div class="import-head">
			<div>
				<strong>粘贴中文稿</strong>
				<p class="muted">每行对应一个 cue；也可以导入外部做好的本土化 SRT，时间码会同步到字幕轨。</p>
			</div>
			<button class="mini-btn" type="button" title="关闭导入面板：不会应用尚未提交的字幕内容。" onclick={onClose} aria-label="关闭中文稿导入"><X size={13} /></button>
		</div>
		<textarea
			rows="7"
			value={text}
			placeholder={`共 ${cueCount} 条 cue。示例：\n这是一九九二年的故事 || 这是一九九二年的故事\n他回头看向大海 || 他，回头看向大海`}
			oninput={(event) => (text = event.currentTarget.value)}
		></textarea>
		<div class="import-actions">
			<span class="muted">{text.split(/\r?\n/).filter((line) => line.trim()).length} 行</span>
			<input bind:this={srtInput} class="hidden-file" type="file" accept=".srt,application/x-subrip,text/plain" onchange={(event) => importSrtFile(event.currentTarget.files?.[0])} />
			<button class="btn" type="button" title="导入 SRT：按时间码匹配并更新本土化字幕片段。" onclick={() => srtInput?.click()} disabled={!cueCount}>
				<FileText size={14} /> 导入 SRT
			</button>
			<button class="btn primary" type="button" title="应用文本：按行顺序写入现有字幕片段。" onclick={apply} disabled={!text.trim() || !cueCount}>
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

	.hidden-file {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
	}
</style>
