<script lang="ts">
	import { Hash, Plus, Scissors, Send, Wand2 } from 'lucide-svelte';

	interface TagTool {
		label: string;
		insert: string;
		hint: string;
	}

	type Props = {
		text: string;
		engineId: string;
		onchange?: (e: Event) => void;
		ontexttool?: (mode: 'clean' | 'numbers' | 'split') => void;
		onGenerate?: () => void;
		textToolBusy?: string;
		generateBusy?: boolean;
	};

	let {
		text = $bindable(''),
		engineId = '',
		onchange,
		ontexttool,
		onGenerate = () => {},
		textToolBusy = '',
		generateBusy = false
	}: Props = $props();

	const isCosyVoice = $derived(
		engineId === 'cosyvoice-sft' || engineId === 'cosyvoice-zero-shot'
	);
	const isOmniVoice = $derived(engineId === 'omnivoice');
	const hasTagTools = $derived(isCosyVoice || isOmniVoice);

	const tagTools = $derived.by<TagTool[]>(() => {
		if (isCosyVoice) {
			return [
				{
					label: '停顿',
					insert: '<|pause_300|>',
					hint: '在文本中插入 CosyVoice 停顿标签，控制语速节奏和自然停顿'
				},
				{
					label: '笑声',
					insert: '<laughter>',
					hint: '在文本中插入 CosyVoice 笑声标签，生成自然的笑声效果'
				}
			];
		}
		if (isOmniVoice) {
			return [
				{
					label: '停顿',
					insert: '[pause]',
					hint: '在文本中插入 OmniVoice 停顿标签，控制语速节奏和自然停顿'
				},
				{
					label: '笑声',
					insert: '[laughter]',
					hint: '在文本中插入 OmniVoice 笑声标签，生成自然的笑声效果'
				},
				{
					label: '叹气',
					insert: '[sigh]',
					hint: '在文本中插入 OmniVoice 叹气标签，表达叹息或无奈的情绪'
				},
				{
					label: '咳嗽',
					insert: '[cough]',
					hint: '在文本中插入 OmniVoice 咳嗽标签，模拟咳嗽音效'
				}
			];
		}
		return [];
	});

	let textareaEl: HTMLTextAreaElement | undefined = $state();

	function insertAtCursor(insertText: string) {
		const el = textareaEl;
		if (!el) {
			const trimmed = text.trimEnd();
			text = trimmed ? `${trimmed} ${insertText}` : insertText;
			return;
		}
		const start = el.selectionStart;
		const end = el.selectionEnd;
		const before = text.slice(0, start);
		const after = text.slice(end);
		text = before + insertText + after;
		requestAnimationFrame(() => {
			el.focus();
			const cursor = start + insertText.length;
			el.selectionStart = el.selectionEnd = cursor;
		});
	}
</script>

<div class="text-input-wrap">
	<textarea
		bind:this={textareaEl}
		bind:value={text}
		{onchange}
		class="text-input-area"
		placeholder="输入要合成的文本"
		spellcheck="false"
	></textarea>

	<div class="tool-row">
		<div class="tool-actions">
			<span class="char-count">{text.length} 字</span>
			<button
				class="btn tool-btn text-pop"
				type="button"
				data-text={"把复制来的稿子整理成更适合朗读的样子。\n会处理：多余空格、连续空行、奇怪标点。\n例：'你好   ，  世界！！' → '你好，世界！'\n不会改模型参数。"}
				onclick={() => ontexttool?.('clean')}
				disabled={textToolBusy !== ''}
			>
				<Wand2 size={14} />
				<span class="tool-label">{textToolBusy === 'clean' ? '清洗中' : '清洗文本'}</span>
			</button>
			<button
				class="btn tool-btn text-pop"
				type="button"
				data-text={"把数字和符号改成更自然的口播读法。\n例：'2026年、3.5%、AI' 会转成更适合朗读的文本。\n适合财经、教程、年份很多的稿子。"}
				onclick={() => ontexttool?.('numbers')}
				disabled={textToolBusy !== ''}
			>
				<Hash size={14} />
				<span class="tool-label">{textToolBusy === 'numbers' ? '处理中' : '数字规范'}</span>
			</button>
			<button
				class="btn tool-btn text-pop"
				type="button"
				data-text={"先预览系统会怎么分句和停顿。\n适合长文、课程稿、需要控制节奏的旁白。\n只做预览，不会提交生成任务。"}
				onclick={() => ontexttool?.('split')}
				disabled={!text.trim() || textToolBusy !== ''}
			>
				<Scissors size={14} />
				<span class="tool-label">{textToolBusy === 'split' ? '分句中' : '分句预览'}</span>
			</button>

			{#if hasTagTools}
				<span class="tool-sep" aria-hidden="true"></span>
			{/if}

			{#each tagTools as tool}
				<button
					class="btn tool-btn tag-btn text-pop"
					type="button"
					data-text={tool.hint}
					onclick={() => insertAtCursor(tool.insert)}
				>
					<Plus size={14} />
					<span class="tool-label">{tool.label}</span>
				</button>
			{/each}
		</div>
		<button
			class="btn primary tool-btn generate-inline-btn text-pop"
			type="button"
			data-text={"提交当前文本和参数开始生成语音。\n长文本会先弹出分段策略确认，云端复刻会按设置提醒上传参考音频。"}
			onclick={onGenerate}
			disabled={generateBusy || !text.trim()}
		>
			<Send size={14} />
			<span class="tool-label">{generateBusy ? '提交中' : '生成'}</span>
		</button>
	</div>
</div>

<style>
	.text-input-wrap {
		display: grid;
		gap: 0;
	}

	.text-input-area {
		width: 100%;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 10px 12px;
		background: #101215;
		color: var(--text);
		font-family:
			'PingFang SC',
			'Hiragino Sans GB',
			'Microsoft YaHei',
			Inter,
			ui-sans-serif,
			system-ui,
			sans-serif;
		font-size: 0.875rem;
		line-height: 1.65;
		resize: vertical;
		min-height: 120px;
		box-sizing: border-box;
		transition: border-color 140ms ease;
	}

	.text-input-area:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(79, 156, 249, 0.14);
	}

	.tool-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		margin-top: 8px;
		min-height: 28px;
	}

	.char-count {
		font-size: 11px;
		color: var(--muted);
		white-space: nowrap;
	}

	.tool-actions {
		display: flex;
		align-items: center;
		gap: 6px;
		flex-wrap: wrap;
		justify-content: flex-start;
		min-width: 0;
	}

	.tool-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 4px;
		height: 28px;
		min-height: 28px;
		padding: 0 8px;
		border-radius: 6px;
		font-size: 12px;
		line-height: 1;
		white-space: nowrap;
		box-sizing: border-box;
		border: 1px solid var(--line);
		background: var(--panel-2);
		color: var(--text);
		cursor: pointer;
		transition: border-color 120ms ease, background 120ms ease;
	}

	.tool-btn:hover {
		border-color: rgba(79, 156, 249, 0.35);
		background: rgba(79, 156, 249, 0.08);
	}

	.tool-btn:active {
		background: rgba(79, 156, 249, 0.14);
	}

	.tool-btn:disabled {
		opacity: 0.48;
		cursor: not-allowed;
	}

	.tool-btn:disabled:hover {
		border-color: var(--line);
		background: var(--panel-2);
	}

	.tool-label {
		font-size: 11px;
		letter-spacing: 0.01em;
	}

	.tool-sep {
		width: 1px;
		height: 18px;
		background: var(--line);
		border-radius: 1px;
		flex-shrink: 0;
		margin: 0 2px;
	}

	.tag-btn {
		background: rgba(79, 156, 249, 0.06);
		border-color: rgba(79, 156, 249, 0.16);
	}

	.tag-btn:hover {
		background: rgba(79, 156, 249, 0.14);
		border-color: rgba(79, 156, 249, 0.4);
	}

	.generate-inline-btn {
		margin-left: auto;
		flex: 0 0 auto;
	}

	@media (max-width: 640px) {
		.tool-row {
			align-items: stretch;
			flex-direction: column;
		}

		.generate-inline-btn {
			margin-left: 0;
			width: 100%;
		}
	}
</style>
