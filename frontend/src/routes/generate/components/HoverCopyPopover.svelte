<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Check, Clipboard, ClipboardX } from 'lucide-svelte';

	type Props = {
		label: string;
		copyText: string;
		title: string;
		children?: Snippet;
	};

	let { label, copyText, title, children }: Props = $props();
	let open = $state(false);
	let copyState = $state<'idle' | 'copied' | 'failed'>('idle');
	let copyTimer: ReturnType<typeof setTimeout> | undefined;
	let hideTimer: ReturnType<typeof setTimeout> | undefined;

	function openPanel() {
		if (hideTimer) clearTimeout(hideTimer);
		open = true;
	}

	function scheduleHide() {
		if (hideTimer) clearTimeout(hideTimer);
		hideTimer = setTimeout(() => (open = false), 160);
	}

	function writeClipboardEvent(text: string) {
		let handled = false;
		const handler = (event: ClipboardEvent) => {
			event.clipboardData?.setData('text/plain', text);
			event.preventDefault();
			handled = true;
		};
		document.addEventListener('copy', handler);
		let ok = false;
		try {
			ok = document.execCommand('copy');
		} finally {
			document.removeEventListener('copy', handler);
		}
		return ok && handled;
	}

	function writeTextareaClipboard(text: string) {
		const textarea = document.createElement('textarea');
		textarea.value = text;
		textarea.setAttribute('readonly', '');
		textarea.style.position = 'fixed';
		textarea.style.left = '-9999px';
		textarea.style.top = '0';
		document.body.appendChild(textarea);
		textarea.focus({ preventScroll: true });
		textarea.select();
		let ok = false;
		try {
			ok = document.execCommand('copy');
		} finally {
			document.body.removeChild(textarea);
		}
		return ok;
	}

	async function writeClipboard(text: string) {
		if (navigator.clipboard?.writeText) {
			try {
				await navigator.clipboard.writeText(text);
				return true;
			} catch {
				// Fall through to the textarea fallback for browsers that block clipboard writes.
			}
		}
		return writeClipboardEvent(text) || writeTextareaClipboard(text);
	}

	async function copyContent(event: MouseEvent) {
		event.preventDefault();
		event.stopPropagation();
		copyState = (await writeClipboard(copyText)) ? 'copied' : 'failed';
		if (copyTimer) clearTimeout(copyTimer);
		copyTimer = setTimeout(() => (copyState = 'idle'), 1400);
	}
</script>

<span
	class="hover-copy-pop"
	role="group"
	onmouseenter={openPanel}
	onmouseleave={scheduleHide}
	onfocusin={openPanel}
	onfocusout={scheduleHide}
>
	<span class="hover-copy-trigger">
		{@render children?.()}
	</span>
	<span
		class="hover-copy-panel"
		class:open
		role="tooltip"
		onmouseenter={openPanel}
		onmouseleave={scheduleHide}
		onfocusin={openPanel}
		onfocusout={scheduleHide}
	>
		<span class="hover-copy-head">
			<strong>{title}</strong>
			<button
				type="button"
				class="hover-copy-btn"
				class:copied={copyState === 'copied'}
				class:failed={copyState === 'failed'}
				aria-label={copyState === 'copied' ? `${label}已复制` : `复制${label}`}
				title={copyState === 'copied' ? '已复制' : copyState === 'failed' ? '复制失败' : `复制${label}`}
				onclick={copyContent}
			>
				{#if copyState === 'copied'}<Check size={13} />{:else if copyState === 'failed'}<ClipboardX size={13} />{:else}<Clipboard size={13} />{/if}
			</button>
		</span>
		<span class="hover-copy-body">{copyText}</span>
	</span>
</span>

<style>
	.hover-copy-pop {
		position: relative;
		display: inline-flex;
		min-width: 0;
	}

	.hover-copy-trigger {
		display: inline-flex;
		min-width: 0;
	}

	.hover-copy-panel {
		position: absolute;
		left: 0;
		bottom: calc(100% + 10px);
		z-index: 150;
		display: grid;
		gap: 8px;
		visibility: hidden;
		opacity: 0;
		pointer-events: none;
		width: min(360px, calc(100vw - 32px));
		max-height: 260px;
		overflow: auto;
		padding: 10px;
		border: 1px solid rgba(255, 255, 255, 0.1);
		border-radius: 10px;
		background: rgba(12, 15, 20, 0.96);
		color: #eef3fb;
		box-shadow: 0 18px 42px rgba(0, 0, 0, 0.4);
		transition: opacity 120ms ease, visibility 0s linear 120ms;
	}

	.hover-copy-pop:hover .hover-copy-panel,
	.hover-copy-pop:focus-within .hover-copy-panel,
	.hover-copy-panel.open {
		visibility: visible;
		opacity: 1;
		pointer-events: auto;
		transition-delay: 260ms, 0s;
	}

	.hover-copy-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
	}

	.hover-copy-head strong {
		font-size: 12px;
		line-height: 1.2;
	}

	.hover-copy-btn {
		display: inline-grid;
		place-items: center;
		width: 24px;
		height: 24px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: #141a22;
		color: #dce8f8;
		cursor: pointer;
		transition:
			transform 90ms ease,
			border-color 120ms ease,
			background 120ms ease,
			color 120ms ease;
	}

	.hover-copy-btn:hover {
		border-color: rgba(79, 156, 249, 0.48);
		background: rgba(79, 156, 249, 0.12);
	}

	.hover-copy-btn:active {
		transform: translateY(1px) scale(0.92);
	}

	.hover-copy-btn.copied {
		border-color: rgba(66, 196, 155, 0.48);
		background: rgba(66, 196, 155, 0.14);
		color: #9ee6c8;
		animation: copy-pop 180ms ease;
	}

	.hover-copy-btn.failed {
		border-color: rgba(240, 161, 161, 0.45);
		background: rgba(240, 161, 161, 0.12);
		color: #f0a1a1;
	}

	@keyframes copy-pop {
		0% { transform: scale(0.88); }
		70% { transform: scale(1.08); }
		100% { transform: scale(1); }
	}

	.hover-copy-body {
		font-size: 11.5px;
		line-height: 1.55;
		color: #dbe6f4;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
</style>
