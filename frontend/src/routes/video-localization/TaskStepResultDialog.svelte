<script lang="ts">
	import { AlertTriangle, Check, CircleOff, ExternalLink, Info, LoaderCircle, X } from 'lucide-svelte';
	import { onMount } from 'svelte';
	import type { ActivityTaskStepResult } from './activity-notice';

	let {
		taskLabel = '任务流程',
		stepLabel,
		stepPositionLabel = '',
		result,
		durationLabel = '',
		onClose
	}: {
		taskLabel?: string;
		stepLabel: string;
		stepPositionLabel?: string;
		result: ActivityTaskStepResult;
		durationLabel?: string;
		onClose: () => void;
	} = $props();

	let dialogElement: HTMLElement;

	const statusLabel = $derived({
		running: '处理中',
		success: '结果有效',
		warning: '需要留意',
		failed: '处理失败',
		skipped: '未执行'
	}[result.status]);
	const coverageLabel = $derived.by(() => {
		if (!result.coverage) return '';
		const prefix = result.coverage.mode === 'complete' ? '完整展示' : result.coverage.mode === 'focused' ? '重点展示' : '结果摘要';
		return `${prefix} ${result.coverage.shownCount} / ${result.coverage.totalCount} ${result.coverage.unit}`;
	});

	function portal(node: HTMLElement) {
		document.body.appendChild(node);
		return { destroy: () => node.remove() };
	}

	function safeExternalUrl(value?: string) {
		if (!value) return '';
		try {
			const parsed = new URL(value);
			return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : '';
		} catch {
			return '';
		}
	}

	function changedParts(source = '', target = '') {
		const sourceChars = [...source];
		const targetChars = [...target];
		let prefix = 0;
		while (prefix < sourceChars.length && prefix < targetChars.length && sourceChars[prefix] === targetChars[prefix]) prefix += 1;
		let suffix = 0;
		while (
			suffix < sourceChars.length - prefix
			&& suffix < targetChars.length - prefix
			&& sourceChars[sourceChars.length - 1 - suffix] === targetChars[targetChars.length - 1 - suffix]
		) suffix += 1;
		const end = suffix ? -suffix : undefined;
		return [
			{ text: targetChars.slice(0, prefix).join(''), changed: false },
			{ text: targetChars.slice(prefix, end).join(''), changed: true },
			{ text: suffix ? targetChars.slice(-suffix).join('') : '', changed: false }
		].filter((part) => part.text);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.preventDefault();
			onClose();
			return;
		}
		if (event.key !== 'Tab' || !dialogElement) return;
		const focusable = [...dialogElement.querySelectorAll<HTMLElement>('button, a[href], [tabindex]:not([tabindex="-1"])')]
			.filter((item) => !item.hasAttribute('disabled'));
		if (!focusable.length) return;
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		if (event.shiftKey && document.activeElement === first) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && document.activeElement === last) {
			event.preventDefault();
			first.focus();
		}
	}

	onMount(() => {
		const previousOverflow = document.body.style.overflow;
		const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		document.body.style.overflow = 'hidden';
		requestAnimationFrame(() => dialogElement?.focus());
		return () => {
			document.body.style.overflow = previousOverflow;
			if (previousFocus?.isConnected) previousFocus.focus();
		};
	});
</script>

<svelte:window onkeydown={handleKeydown} />

<div
	class="result-backdrop"
	use:portal
	role="presentation"
	onclick={(event) => event.currentTarget === event.target && onClose()}
>
	<div
		bind:this={dialogElement}
		class="result-dialog"
		class:status-running={result.status === 'running'}
		class:status-warning={result.status === 'warning'}
		class:status-failed={result.status === 'failed'}
		role="dialog"
		aria-modal="true"
		aria-labelledby="task-step-result-title"
		tabindex="-1"
	>
		<header class="result-head">
			<div class="result-heading">
				<span class="heading-icon" aria-hidden="true"><Info size={15} /></span>
				<div>
					<small>{taskLabel}{#if stepPositionLabel} · {stepPositionLabel}{/if}</small>
					<h2 id="task-step-result-title">{stepLabel}</h2>
				</div>
			</div>
			<div class="head-actions">
				<span class="result-status">
					<span class:spinning={result.status === 'running'} aria-hidden="true">
						{#if result.status === 'running'}<LoaderCircle size={12} />
						{:else if result.status === 'success'}<Check size={12} />
						{:else if result.status === 'skipped'}<CircleOff size={12} />
						{:else}<AlertTriangle size={12} />{/if}
					</span>
					{statusLabel}
				</span>
				{#if durationLabel}<span class="result-duration">{durationLabel}</span>{/if}
				<button type="button" aria-label="关闭步骤结果" onclick={onClose}><X size={16} /></button>
			</div>
		</header>

		<div class="result-body">
			{#if result.purpose}
				<section class="result-purpose" aria-label="步骤说明">
					<strong>这一步做什么</strong>
					<p>{result.purpose}</p>
				</section>
			{/if}

			<section class="result-summary" aria-label="结果结论">
				<strong>结论</strong>
				<p>{result.summary}</p>
			</section>

			{#if result.coverage}
				<section class="result-coverage" class:focused={result.coverage.mode !== 'complete'} aria-label="详情展示范围">
					<div><strong>{coverageLabel}</strong><span>{result.coverage.mode === 'complete' ? '本步骤适合人工阅读的信息已全部列出。' : '保留重点结果，未把大体量机器明细直接铺满界面。'}</span></div>
					{#if result.coverage.reason}<p>{result.coverage.reason}</p>{/if}
				</section>
			{/if}

			{#if result.metrics.length}
				<dl class="result-metrics" aria-label="关键指标">
					{#each result.metrics as metric}
						<div><dt>{metric.label}</dt><dd>{metric.value}</dd></div>
					{/each}
				</dl>
			{/if}

			{#each result.sections as section}
				<details class="result-section" open={section.items.length <= 30}>
					<summary><h3>{section.title}</h3><span>{section.items.length} 项</span></summary>
					<div class="result-items">
						{#each section.items as item, index}
							<article
								class="result-item"
								class:item-positive={item.tone === 'positive'}
								class:item-warning={item.tone === 'warning'}
								class:item-muted={item.tone === 'muted'}
							>
								<div class="item-heading">
									<strong>{item.title || `样例 ${index + 1}`}</strong>
									{#if item.meta}<span>{item.meta}</span>{/if}
								</div>
								{#if item.before || item.after}
									<div class="comparison">
										{#if item.before}<p><b>{item.beforeLabel || '识别原文'}</b><span>{item.before}</span></p>{/if}
										{#if item.after}
											<p>
												<b>{item.afterLabel || '校对结果'}</b>
												<span>{#each changedParts(item.before, item.after) as part}<mark class:changed={part.changed}>{part.text}</mark>{/each}</span>
											</p>
										{/if}
									</div>
									{#if item.text}<p class="item-detail">{item.text}</p>{/if}
								{:else if item.text}
									<p class="item-text">{item.text}</p>
								{/if}
								{#if item.facts.length}
									<dl class="item-facts">
										{#each item.facts as fact}<div><dt>{fact.label}</dt><dd>{fact.value}</dd></div>{/each}
									</dl>
								{/if}
								{#if item.visual}
									<div class="item-visual" aria-label={`${item.visual.label} ${Math.round(item.visual.value / item.visual.max * 100)}%`}>
										<span>{item.visual.label}</span>
										<div><i style={`width:${Math.round(item.visual.value / item.visual.max * 100)}%`}></i></div>
									</div>
								{/if}
								{#if item.links.length}
									<div class="item-links" aria-label="参考来源">
										{#each item.links as link}
											{#if safeExternalUrl(link.url)}
												<a href={safeExternalUrl(link.url)} target="_blank" rel="noreferrer">
													<span><strong>{link.title}</strong>{#if link.text}<small>{link.text}</small>{/if}</span>
													{#if link.meta}<em>{link.meta}</em>{/if}<ExternalLink size={11} />
												</a>
											{/if}
										{/each}
									</div>
								{/if}
								{#if safeExternalUrl(item.url)}
									<a href={safeExternalUrl(item.url)} target="_blank" rel="noreferrer">查看来源 <ExternalLink size={11} /></a>
								{/if}
							</article>
						{/each}
					</div>
				</details>
			{/each}

			{#if result.notes.length}
				<section class="result-notes" aria-label="质量提醒">
					<h3>质量提醒</h3>
					{#each result.notes as note}<p>{note}</p>{/each}
				</section>
			{/if}
		</div>
	</div>
</div>

<style>
	.result-backdrop {
		position: fixed;
		inset: 0;
		z-index: 1200;
		display: grid;
		place-items: start center;
		padding: clamp(48px, 7vh, 82px) 16px 24px;
		background: rgba(5, 8, 10, 0.76);
		backdrop-filter: blur(2px);
		animation: backdrop-in 120ms ease-out;
	}
	.result-dialog {
		width: min(760px, calc(100vw - 32px));
		max-height: min(720px, calc(100dvh - 72px));
		display: grid;
		grid-template-rows: auto minmax(0, 1fr);
		border: 1px solid #3b474e;
		border-radius: 8px;
		background: #171c21;
		box-shadow: 0 22px 70px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 255, 255, 0.035) inset;
		color: #d7e0e4;
		outline: none;
		overflow: hidden;
		animation: dialog-in 150ms ease-out;
	}
	.result-head {
		min-height: 62px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		padding: 10px 12px 10px 16px;
		border-bottom: 1px solid #303a40;
		background: #1a2025;
	}
	.result-heading, .head-actions, .result-status { display: flex; align-items: center; }
	.result-heading { min-width: 0; gap: 10px; }
	.heading-icon { width: 28px; height: 28px; display: grid; place-items: center; flex: 0 0 auto; border: 1px solid #39515a; border-radius: 6px; background: #18282e; color: #80bed0; }
	.result-heading div { min-width: 0; }
	.result-heading small { display: block; margin-bottom: 2px; color: #718189; font-size: 9px; }
	.result-heading h2 { margin: 0; overflow: hidden; color: #e3eaed; font-size: 13px; font-weight: 670; letter-spacing: 0; text-overflow: ellipsis; white-space: nowrap; }
	.head-actions { flex: 0 0 auto; gap: 8px; }
	.result-status { gap: 4px; color: #82c6a0; font-size: 9.5px; white-space: nowrap; }
	.status-warning .result-status { color: #d8b36f; }
	.status-failed .result-status { color: #df8d8e; }
	.status-running .result-status { color: #78bed2; }
	.result-duration { color: #78878e; font-size: 9.5px; white-space: nowrap; }
	.head-actions button { width: 28px; height: 28px; display: grid; place-items: center; padding: 0; border: 0; border-radius: 5px; background: transparent; color: #85949b; cursor: pointer; }
	.head-actions button:hover { background: #283137; color: #e5ecef; }
	.result-body { min-height: 0; padding: 0 18px 22px; overflow: auto; overscroll-behavior: contain; }
	.result-purpose, .result-summary { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 12px; padding: 16px 0; }
	.result-purpose { border-bottom: 1px solid #293238; }
	.result-purpose strong, .result-summary strong, .result-section h3, .result-notes h3 { color: #8e9ba1; font-size: 10px; font-weight: 650; }
	.result-purpose p, .result-summary p { margin: 0; color: #d6dfe2; font-size: 11.5px; line-height: 1.65; }
	.result-purpose p { color: #aebbc0; }
	.result-coverage { display: grid; gap: 6px; margin-top: 14px; padding: 10px 12px; border-left: 2px solid #4f8c71; background: rgba(66, 122, 92, 0.09); }
	.result-coverage.focused { border-left-color: #8e774c; background: rgba(130, 99, 52, 0.09); }
	.result-coverage div { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
	.result-coverage strong { color: #a9c9b8; font-size: 9.5px; }
	.result-coverage.focused strong { color: #c8b486; }
	.result-coverage span, .result-coverage p { margin: 0; color: #75858c; font-size: 9px; line-height: 1.5; }
	.result-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; border-top: 1px solid #303a40; border-bottom: 1px solid #303a40; }
	.result-metrics div { min-width: 0; padding: 12px 10px 12px 0; }
	.result-metrics div:not(:nth-child(3n + 1)) { padding-left: 12px; border-left: 1px solid #2b3439; }
	.result-metrics dt { margin-bottom: 4px; color: #6f7f86; font-size: 9px; }
	.result-metrics dd { margin: 0; overflow-wrap: anywhere; color: #cfd9dd; font-size: 11.5px; font-weight: 600; line-height: 1.4; }
	.result-section, .result-notes { padding-top: 18px; }
	.result-section summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; color: #728189; cursor: pointer; list-style: none; }
	.result-section summary::-webkit-details-marker { display: none; }
	.result-section summary::before { content: '›'; width: 12px; flex: 0 0 auto; color: #66757c; transform: rotate(0deg); transition: transform 120ms ease; }
	.result-section[open] summary::before { transform: rotate(90deg); }
	.result-section h3, .result-notes h3 { margin: 0; }
	.result-section summary h3 { flex: 1 1 auto; }
	.result-section summary span { font-size: 8.5px; }
	.result-items { border-top: 1px solid #2d373c; }
	.result-item { padding: 11px 0 12px; border-bottom: 1px solid #293238; }
	.item-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
	.item-heading strong { min-width: 0; overflow: hidden; color: #c8d2d6; font-size: 10.5px; font-weight: 630; text-overflow: ellipsis; white-space: nowrap; }
	.item-heading span { flex: 0 0 auto; color: #68777e; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 9px; }
	.item-text, .comparison p, .result-notes p { margin: 0; color: #aebbc0; font-size: 10.5px; line-height: 1.6; overflow-wrap: anywhere; }
	.comparison { display: grid; gap: 5px; }
	.comparison p { display: grid; grid-template-columns: 62px minmax(0, 1fr); gap: 8px; }
	.comparison b { color: #73848b; font-size: 9px; font-weight: 600; }
	.comparison p:last-child span { color: #cad8dc; }
	.comparison mark { padding: 0; background: transparent; color: inherit; }
	.comparison mark.changed { padding: 1px 2px; border-radius: 2px; background: rgba(95, 176, 199, 0.16); color: #a9d9e6; }
	.item-detail { margin: 7px 0 0; padding-left: 70px; color: #87979e; font-size: 9.5px; line-height: 1.55; overflow-wrap: anywhere; }
	.item-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; margin: 9px 0 0; overflow: hidden; border: 1px solid #2d373c; border-radius: 4px; background: #2d373c; }
	.item-facts div { min-width: 0; padding: 7px 8px; background: #191f23; }
	.item-facts dt { margin-bottom: 3px; color: #6f7f86; font-size: 8.5px; }
	.item-facts dd { margin: 0; overflow-wrap: anywhere; color: #b5c1c6; font-size: 9.5px; line-height: 1.45; }
	.item-visual { display: grid; grid-template-columns: 132px minmax(0, 1fr); align-items: center; gap: 10px; margin-top: 9px; color: #73838a; font-size: 8.5px; }
	.item-visual > div { height: 4px; overflow: hidden; border-radius: 2px; background: #293238; }
	.item-visual i { display: block; height: 100%; border-radius: inherit; background: #65aabd; }
	.item-links { display: grid; gap: 4px; margin-top: 9px; }
	.result-item .item-links a { width: 100%; display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 8px; margin: 0; padding: 7px 8px; border: 1px solid #2c373c; border-radius: 4px; background: #181e22; text-decoration: none; }
	.item-links a > span { min-width: 0; display: grid; gap: 2px; }
	.item-links strong { overflow: hidden; color: #9fc4cf; font-size: 9.5px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
	.item-links small { overflow: hidden; color: #708087; font-size: 8.5px; line-height: 1.4; text-overflow: ellipsis; white-space: nowrap; }
	.item-links em { color: #66767d; font-size: 8px; font-style: normal; white-space: nowrap; }
	.result-item.item-positive { border-left: 2px solid #4c8d71; padding-left: 10px; }
	.result-item.item-warning { border-left: 2px solid #9b7945; padding-left: 10px; }
	.result-item.item-muted { opacity: 0.78; }
	.result-item a { width: fit-content; display: inline-flex; align-items: center; gap: 4px; margin-top: 7px; color: #72b8cc; font-size: 9.5px; text-decoration: none; }
	.result-item a:hover { color: #a4d8e6; text-decoration: underline; }
	.result-notes { color: #d7af73; }
	.result-notes p { padding: 9px 10px; border-left: 2px solid #9e753c; background: rgba(130, 91, 39, 0.1); color: #c6ab82; }
	.result-notes p + p { margin-top: 6px; }
	.spinning { animation: result-spin 900ms linear infinite; }
	@keyframes backdrop-in { from { opacity: 0; } }
	@keyframes dialog-in { from { opacity: 0; transform: translateY(-6px) scale(0.992); } }
	@keyframes result-spin { to { transform: rotate(360deg); } }
	@media (max-width: 620px) {
		.result-backdrop { padding: 16px 8px; place-items: center; }
		.result-dialog { width: calc(100vw - 16px); max-height: calc(100dvh - 32px); }
		.result-head { align-items: flex-start; gap: 10px; padding-left: 12px; }
		.head-actions { gap: 5px; }
		.result-duration { display: none; }
		.result-body { padding-inline: 14px; }
		.result-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.result-metrics div:not(:nth-child(3n + 1)) { padding-left: 0; border-left: 0; }
		.result-metrics div:nth-child(even) { padding-left: 10px; border-left: 1px solid #2b3439; }
		.result-purpose, .result-summary { grid-template-columns: 1fr; gap: 5px; }
		.result-coverage div { align-items: flex-start; flex-direction: column; gap: 3px; }
		.item-facts { grid-template-columns: 1fr; }
		.item-visual { grid-template-columns: 1fr; gap: 5px; }
		.result-item .item-links a { grid-template-columns: minmax(0, 1fr) auto; }
		.item-links em { display: none; }
	}
	@media (prefers-reduced-motion: reduce) {
		.result-backdrop, .result-dialog, .spinning { animation: none; }
	}
</style>
