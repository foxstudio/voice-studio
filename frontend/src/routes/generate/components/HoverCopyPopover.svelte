<script lang="ts">
	import { tick } from 'svelte';
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
	let rootEl: HTMLSpanElement | undefined;
	let panelEl: HTMLSpanElement | undefined;
	let panelStyle = $state('');
	let placement = $state<'top' | 'bottom'>('top');
	let arrowLeft = $state(14);
	let copyTimer: ReturnType<typeof setTimeout> | undefined;
	let hideTimer: ReturnType<typeof setTimeout> | undefined;

	function openPanel() {
		if (hideTimer) clearTimeout(hideTimer);
		open = true;
		void positionPanel();
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

	async function positionPanel() {
		await tick();
		if (!rootEl || !panelEl || typeof window === 'undefined') return;

		const trigger = rootEl.getBoundingClientRect();
		const panel = panelEl.getBoundingClientRect();
		const gap = 10;
		const pad = 8;
		const maxLeft = window.innerWidth - panel.width - pad;
		const maxTop = window.innerHeight - panel.height - pad;

		let left = trigger.left;
		left = Math.max(pad, Math.min(left, maxLeft));

		let top = trigger.top - panel.height - gap;
		placement = 'top';
		if (top < pad) {
			top = trigger.bottom + gap;
			placement = 'bottom';
		}
		if (top > maxTop) {
			top = Math.max(pad, maxTop);
		}

		const triggerCenter = trigger.left + trigger.width / 2;
		arrowLeft = Math.max(14, Math.min(panel.width - 18, triggerCenter - left));
		panelStyle = `left:${Math.round(left)}px; top:${Math.round(top)}px; --arrow-left:${Math.round(arrowLeft)}px;`;
	}

	$effect(() => {
		if (!open || typeof window === 'undefined') return;
		const reposition = () => void positionPanel();
		window.addEventListener('resize', reposition);
		window.addEventListener('scroll', reposition, true);
		return () => {
			window.removeEventListener('resize', reposition);
			window.removeEventListener('scroll', reposition, true);
		};
	});
</script>

<span
	bind:this={rootEl}
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
		bind:this={panelEl}
		class="hover-copy-panel"
		class:open
		data-placement={placement}
		style={panelStyle}
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
		position: fixed;
		z-index: 150;
		display: grid;
		gap: 8px;
		visibility: hidden;
		opacity: 0;
		pointer-events: none;
		width: max-content;
		min-width: min(180px, calc(100vw - 24px));
		max-width: min(320px, calc(100vw - 24px));
		max-height: 260px;
		overflow: auto;
		padding: 10px;
		border: 1px solid rgba(255, 255, 255, 0.1);
		border-radius: 10px;
		background: rgba(12, 15, 20, 0.96);
		color: #eef3fb;
		text-align: left;
		overflow-wrap: anywhere;
		box-shadow: 0 18px 42px rgba(0, 0, 0, 0.4);
		transition: opacity 120ms ease, visibility 0s linear 120ms;
	}

	.hover-copy-panel::before {
		content: '';
		position: absolute;
		left: var(--arrow-left, 14px);
		width: 10px;
		height: 10px;
		border: inherit;
		background: inherit;
		transform: rotate(45deg);
	}

	.hover-copy-panel[data-placement='top']::before {
		bottom: -6px;
		border-left: 0;
		border-top: 0;
	}

	.hover-copy-panel[data-placement='bottom']::before {
		top: -6px;
		border-right: 0;
		border-bottom: 0;
	}

	.hover-copy-panel.open {
		visibility: visible;
		opacity: 1;
		pointer-events: auto;
		transition-delay: 260ms, 0s;
	}

	.hover-copy-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 10px;
	}

	.hover-copy-head strong {
		flex: 1;
		min-width: 0;
		font-size: 12px;
		line-height: 1.35;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		word-break: break-word;
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
		display: block;
		width: 100%;
		min-width: 0;
		max-width: 100%;
		font-size: 11.5px;
		line-height: 1.55;
		color: #dbe6f4;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		word-break: break-word;
	}
</style>
