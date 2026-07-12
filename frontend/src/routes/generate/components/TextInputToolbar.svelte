<script lang="ts">
	import { Hash, Scissors, Send, Wand2 } from 'lucide-svelte';

	interface TagTool {
		label: string;
		insert: string;
		hint: string;
	}

	type Props = {
		textLength?: number;
		hasText?: boolean;
		textToolBusy?: string;
		generateBusy?: boolean;
		generateDisabled?: boolean;
		tagTools?: TagTool[];
		onTextTool?: (mode: 'clean' | 'numbers' | 'split') => void;
		onGenerate?: () => void;
		onInsertTag?: (insert: string) => void;
	};

	let {
		textLength = 0,
		hasText = false,
		textToolBusy = '',
		generateBusy = false,
		generateDisabled = false,
		tagTools = [],
		onTextTool = () => {},
		onGenerate = () => {},
		onInsertTag = () => {}
	}: Props = $props();
</script>

<div class="tool-row">
	<div class="tool-actions">
		<span class="char-count">{textLength} 字</span>
		<button class="btn tool-btn text-pop" type="button" data-text={"把复制来的稿子整理成更适合朗读的样子。\n会处理：多余空格、连续空行、奇怪标点。\n例：'你好   ，  世界！！' → '你好，世界！'\n不会改模型参数。"} onclick={() => onTextTool('clean')} disabled={textToolBusy !== ''}>
			<Wand2 size={14} /><span class="tool-label">{textToolBusy === 'clean' ? '清洗中' : '清洗文本'}</span>
		</button>
		<button class="btn tool-btn text-pop" type="button" data-text={"把数字和符号改成更自然的口播读法。\n例：'2026年、3.5%、AI' 会转成更适合朗读的文本。\n适合财经、教程、年份很多的稿子。"} onclick={() => onTextTool('numbers')} disabled={textToolBusy !== ''}>
			<Hash size={14} /><span class="tool-label">{textToolBusy === 'numbers' ? '处理中' : '数字规范'}</span>
		</button>
		<button class="btn tool-btn text-pop" type="button" data-text={"先预览系统会怎么分句和停顿。\n适合长文、课程稿、需要控制节奏的旁白。\n只做预览，不会提交生成任务。"} onclick={() => onTextTool('split')} disabled={!hasText || textToolBusy !== ''}>
			<Scissors size={14} /><span class="tool-label">{textToolBusy === 'split' ? '分句中' : '分句预览'}</span>
		</button>

		{#if tagTools.length}<span class="tool-sep" aria-hidden="true"></span>{/if}
		{#each tagTools as tool}
			<button class="btn tool-btn tag-btn text-pop" type="button" data-text={tool.hint} onclick={() => onInsertTag(tool.insert)}><span class="tool-label">{tool.label}</span></button>
		{/each}
	</div>
	<button class="btn primary tool-btn generate-inline-btn text-pop" type="button" data-text={"提交当前文本和参数开始生成语音。\n长文本会先弹出分段策略确认，云端复刻会按设置提醒上传参考音频。"} onclick={onGenerate} disabled={generateBusy || generateDisabled}>
		<Send size={14} /><span class="tool-label">{generateBusy ? '提交中' : '生成'}</span>
	</button>
</div>

<style>
	.tool-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 8px; min-height: 28px; }
	.char-count { color: var(--muted); font-size: 11px; white-space: nowrap; }
	.tool-actions { display: flex; align-items: center; justify-content: flex-start; gap: 6px; flex-wrap: wrap; min-width: 0; }
	.tool-btn { display: inline-flex; align-items: center; justify-content: center; gap: 4px; box-sizing: border-box; height: 28px; min-height: 28px; padding: 0 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel-2); color: var(--text); font-size: 12px; line-height: 1; white-space: nowrap; cursor: pointer; transition: border-color 120ms ease, background 120ms ease; }
	.tool-btn:hover { border-color: rgba(79, 156, 249, .35); background: rgba(79, 156, 249, .08); }
	.tool-btn:active { background: rgba(79, 156, 249, .14); }
	.tool-btn:disabled { opacity: .48; cursor: not-allowed; }
	.tool-btn:disabled:hover { border-color: var(--line); background: var(--panel-2); }
	.tool-label { font-size: 11px; letter-spacing: .01em; }
	.tool-sep { width: 1px; height: 18px; margin: 0 2px; border-radius: 1px; background: var(--line); flex-shrink: 0; }
	.tag-btn { border-color: rgba(79, 156, 249, .16); background: rgba(79, 156, 249, .06); }
	.tag-btn:hover { border-color: rgba(79, 156, 249, .4); background: rgba(79, 156, 249, .14); }
	.generate-inline-btn { margin-left: auto; flex: 0 0 auto; }
	@media (max-width: 640px) {
		.tool-row { align-items: stretch; flex-direction: column; }
		.generate-inline-btn { margin-left: 0; width: 100%; }
	}
</style>
