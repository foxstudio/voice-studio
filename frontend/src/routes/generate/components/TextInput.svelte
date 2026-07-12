<script lang="ts">
	import TextInputToolbar from './TextInputToolbar.svelte';

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
				},
				{
					label: '吸鼻',
					insert: '[sniff]',
					hint: '在文本中插入 OmniVoice 吸鼻音标签'
				},
				{
					label: '确认',
					insert: '[confirmation-en]',
					hint: '在文本中插入 OmniVoice 英文确认语气标签'
				},
				{
					label: '疑问-en',
					insert: '[question-en]',
					hint: '在文本中插入 OmniVoice 英文疑问语气标签'
				},
				{
					label: '疑问-ah',
					insert: '[question-ah]',
					hint: '在文本中插入 OmniVoice ah 疑问音标签'
				},
				{
					label: '疑问-oh',
					insert: '[question-oh]',
					hint: '在文本中插入 OmniVoice oh 疑问音标签'
				},
				{
					label: '疑问-ei',
					insert: '[question-ei]',
					hint: '在文本中插入 OmniVoice ei 疑问音标签'
				},
				{
					label: '疑问-yi',
					insert: '[question-yi]',
					hint: '在文本中插入 OmniVoice yi 疑问音标签'
				},
				{
					label: '惊讶-ah',
					insert: '[surprise-ah]',
					hint: '在文本中插入 OmniVoice ah 惊讶音标签'
				},
				{
					label: '惊讶-oh',
					insert: '[surprise-oh]',
					hint: '在文本中插入 OmniVoice oh 惊讶音标签'
				},
				{
					label: '惊讶-wa',
					insert: '[surprise-wa]',
					hint: '在文本中插入 OmniVoice wa 惊讶音标签'
				},
				{
					label: '惊讶-yo',
					insert: '[surprise-yo]',
					hint: '在文本中插入 OmniVoice yo 惊讶音标签'
				},
				{
					label: '不满',
					insert: '[dissatisfaction-hnn]',
					hint: '在文本中插入 OmniVoice 不满鼻音标签'
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

	<TextInputToolbar
		textLength={text.length}
		hasText={Boolean(text.trim())}
		{textToolBusy}
		{generateBusy}
		generateDisabled={!text.trim()}
		{tagTools}
		onTextTool={(mode) => ontexttool?.(mode)}
		onGenerate={onGenerate}
		onInsertTag={insertAtCursor}
	/>
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

</style>
