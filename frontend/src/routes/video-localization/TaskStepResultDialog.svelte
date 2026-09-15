<script module lang="ts">
	import type {
		ActivityTaskReviewTarget,
		ActivityTaskStepResult as DialogActivityTaskStepResult,
		ActivityTaskStepResultFact,
		ActivityTaskStepResultItem,
		ActivityTaskStepResultSection
	} from './activity-notice';

	function comparableText(value = '') {
		return value.replace(/[\s，。！？、；：,.!?;:()（）【】\[\]"“”'‘’]/g, '').toLocaleLowerCase();
	}

	function isSameMessage(left = '', right = '') {
		const normalizedLeft = comparableText(left);
		const normalizedRight = comparableText(right);
		return Boolean(normalizedLeft && normalizedRight && normalizedLeft === normalizedRight);
	}

	function matchesReviewTarget(item: ActivityTaskStepResultItem, target: ActivityTaskReviewTarget) {
		const titleMatches = Boolean(
			item.title && target.title && isSameMessage(item.title, target.title)
		);
		const detailMatches = Boolean(
			item.text && target.detail && isSameMessage(item.text, target.detail)
		);
		if (target.title && target.detail) return titleMatches && detailMatches;
		return titleMatches || detailMatches;
	}

	export function stepPurpose(stepLabel: string, suppliedPurpose?: string) {
		if (suppliedPurpose?.trim()) return suppliedPurpose.trim();
		if (/汇合|合并听写/.test(stepLabel)) return '把听写片段和匿名说话人时间段对齐，整理成谁在什么时候说了什么。';
		if (/听写|转写|识别|ASR/i.test(stepLabel)) return '把音轨里的讲话转成文字。';
		if (/说话人|人物归属/.test(stepLabel)) return '判断每段声音是谁说的。';
		if (/理解|规划/.test(stepLabel)) return '通读全文，整理主题和接下来要重点检查的内容。';
		if (/画面|截图|视觉/.test(stepLabel)) return '只查看必要画面，读取字幕条、图表等直接可见信息。';
		if (/查证|专名|名称|术语|背景/.test(stepLabel)) return '核对可能写错的名称、术语和背景信息。';
		if (/复查|复核|校对|终审|复读/.test(stepLabel)) return '结合原音和前后文，检查字幕是否准确、通顺。';
		if (/时间|对齐/.test(stepLabel)) return '把字幕文字和原音时间对齐。';
		if (/停顿|断句|边界|分段/.test(stepLabel)) return '结合语义和声音停顿调整字幕断句。';
		if (/写入|保存|字幕轨/.test(stepLabel)) return '把处理好的字幕保存到时间轴。';
		return '完成当前步骤，并记录处理结果。';
	}

	export function detailSections(
		result: DialogActivityTaskStepResult,
		reviewTargets: ActivityTaskReviewTarget[]
	): ActivityTaskStepResultSection[] {
		if (result.detailMode === 'workflow_summary') return result.sections;
		if (result.status !== 'warning' && result.status !== 'failed') return result.sections;
		return result.sections.flatMap((section) => {
			const items = section.items.filter((item) => !reviewTargets.some((target) => matchesReviewTarget(item, target)));
			return items.length ? [{ ...section, items }] : [];
		});
	}

	export function detailNotes(result: DialogActivityTaskStepResult, reviewTargets: ActivityTaskReviewTarget[]) {
		return result.notes.filter((note, index, notes) => {
			if (isSameMessage(note, result.summary)) return false;
			if (reviewTargets.some((target) => isSameMessage(note, target.detail))) return false;
			return notes.findIndex((candidate) => isSameMessage(candidate, note)) === index;
		});
	}

	function deduplicatedDetailFacts(item: ActivityTaskStepResultItem): ActivityTaskStepResultFact[] {
		const visibleText = [item.text, item.before, item.after].filter((value): value is string => Boolean(value));
		return item.facts.filter((fact, index, facts) => {
			if (visibleText.some((text) => isSameMessage(fact.value, text))) return false;
			return facts.findIndex((candidate) => isSameMessage(candidate.label, fact.label) && isSameMessage(candidate.value, fact.value)) === index;
		});
	}

	export function detailFacts(item: ActivityTaskStepResultItem): ActivityTaskStepResultFact[] {
		return deduplicatedDetailFacts(item).filter((fact) => {
			if (/相似度/.test(fact.label)) return false;
			if (/英文位置/.test(fact.label) && /\bcue[_-]?\d+/i.test(fact.value)) return false;
			return !/(?:内部.*(?:ID|编号)|指纹)/i.test(fact.label);
		});
	}

	export function debugDetailFacts(
		item: ActivityTaskStepResultItem,
		metrics: ActivityTaskStepResultFact[]
	): ActivityTaskStepResultFact[] {
		const aggregateLabels = new Set(metrics.map((metric) => metric.label));
		const aggregateLabelByDetail = new Map([
			['实际模型', '调用模型'],
			['调用方式', '调用方式'],
			['模型配置', '模型配置']
		]);
		return deduplicatedDetailFacts(item).filter((fact) => {
			const aggregateLabel = aggregateLabelByDetail.get(fact.label);
			return !aggregateLabel || !aggregateLabels.has(aggregateLabel);
		});
	}

	export function reviewTargetDetail(
		target: ActivityTaskReviewTarget,
		stepLabel = ''
	) {
		if (
			/本地映射语义时间/.test(stepLabel)
			&& target.detail === '核对系统更正后的内容是否符合原音和上下文。'
		) {
			return '这一处已交给下一步自动复核，不会阻塞后续流程。';
		}
		return isSameMessage(target.title, target.detail) ? '' : target.detail;
	}

	export function reviewTargetExcerpt(
		result: DialogActivityTaskStepResult,
		target: ActivityTaskReviewTarget
	) {
		if (target.excerpt?.trim()) return target.excerpt.trim();
		const matchingItems = result.sections
			.flatMap((section) => section.items)
			.filter((candidate) => matchesReviewTarget(candidate, target));
		if (matchingItems.length !== 1) return '';
		return matchingItems[0].facts.find((fact) =>
			/(?:当前|现有).*(?:字幕|听写|原文)|当前听写原文/.test(fact.label)
		)?.value.trim() ?? '';
	}

	export function reviewTargetRange(target: ActivityTaskReviewTarget) {
		if (!Number.isFinite(target.startMs) || !Number.isFinite(target.endMs)) return null;
		const startMs = Math.max(0, Math.round(target.startMs ?? 0));
		const endMs = Math.max(startMs + 1, Math.round(target.endMs ?? startMs + 1));
		return { startMs, endMs };
	}

	export function resultSectionOpenByDefault(section: ActivityTaskStepResultSection) {
		if (section.openByDefault !== undefined) return section.openByDefault;
		const detailLength = section.items.reduce((total, item) => total + [
			item.title,
			item.text,
			item.before,
			item.after,
			...item.facts.flatMap((fact) => [fact.label, fact.value])
		].filter(Boolean).join('').length, 0);
		return section.items.length <= 6 && detailLength <= 900;
	}

	export function longDetailPreview(value = '', limit = 180) {
		const text = value.trim();
		return text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text;
	}

	function containsParagraphBreak(value = '') {
		return /[\r\n]/.test(value.trim());
	}

	export function overviewContentLayout(summary = '', purpose = ''): 'columns' | 'rows' {
		const summaryText = summary.trim();
		const purposeText = purpose.trim();
		if (containsParagraphBreak(summaryText) || containsParagraphBreak(purposeText)) return 'rows';
		if (summaryText.length > 60 || purposeText.length > 44) return 'rows';
		return summaryText.length + purposeText.length > 96 ? 'rows' : 'columns';
	}

	export function resultItemContentLayout(
		item: ActivityTaskStepResultItem,
		facts: ActivityTaskStepResultFact[] = detailFacts(item)
	): 'columns' | 'rows' {
		if (
			!item.text
			|| item.before
			|| item.after
			|| item.meta
			|| facts.length > 2
			|| item.visual
			|| item.links.length
			|| item.url
		) return 'rows';

		const title = item.title?.trim() ?? '';
		const text = item.text.trim();
		const factText = facts.map((fact) => `${fact.label}${fact.value}`);
		if ([title, text, ...factText].some(containsParagraphBreak)) return 'rows';
		if (title.length > 28 || text.length > 48) return 'rows';
		if (factText.some((value) => value.length > 32)) return 'rows';
		return title.length + text.length + factText.join('').length <= 108 ? 'columns' : 'rows';
	}

	export function resultFactUsesFullRow(fact: ActivityTaskStepResultFact) {
		const label = fact.label.trim();
		const value = fact.value.trim();
		return containsParagraphBreak(value) || label.length + value.length > 32;
	}

	export type MarkdownBlock = {
		kind: 'heading-1' | 'heading-2' | 'heading-3' | 'paragraph' | 'quote' | 'bullet' | 'numbered' | 'code';
		text: string;
	};

	export function markdownBlocks(content = ''): MarkdownBlock[] {
		const lines = content.replace(/\r\n?/g, '\n').split('\n');
		const blocks: MarkdownBlock[] = [];
		let paragraph: string[] = [];
		let code: string[] | null = null;
		const flushParagraph = () => {
			const text = paragraph.join(' ').trim();
			if (text) blocks.push({ kind: 'paragraph', text });
			paragraph = [];
		};
		for (const line of lines) {
			if (/^\s*```/.test(line)) {
				flushParagraph();
				if (code === null) code = [];
				else {
					blocks.push({ kind: 'code', text: code.join('\n') });
					code = null;
				}
				continue;
			}
			if (code !== null) {
				code.push(line);
				continue;
			}
			const trimmed = line.trim();
			if (!trimmed) {
				flushParagraph();
				continue;
			}
			const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed);
			const quote = /^>\s?(.+)$/.exec(trimmed);
			const bullet = /^[-*+]\s+(.+)$/.exec(trimmed);
			const numbered = /^\d+[.)]\s+(.+)$/.exec(trimmed);
			if (heading || quote || bullet || numbered) flushParagraph();
			if (heading) {
				blocks.push({
					kind: `heading-${heading[1].length}` as MarkdownBlock['kind'],
					text: heading[2].trim()
				});
			} else if (quote) blocks.push({ kind: 'quote', text: quote[1].trim() });
			else if (bullet) blocks.push({ kind: 'bullet', text: bullet[1].trim() });
			else if (numbered) blocks.push({ kind: 'numbered', text: numbered[1].trim() });
			else paragraph.push(trimmed);
		}
		if (code !== null) blocks.push({ kind: 'code', text: code.join('\n') });
		flushParagraph();
		return blocks;
	}
</script>

<script lang="ts">
	import { AlertTriangle, Check, Circle, CircleOff, ExternalLink, Info, LocateFixed, Play, X } from 'lucide-svelte';
	import { onMount } from 'svelte';
	import {
		activityTaskReviewAction,
		activityTaskReviewTargets,
		activityTaskStepAttentionKind,
		type ActivityTaskStepResult
	} from './activity-notice';
	import CommandSpinner from './CommandSpinner.svelte';

	let {
		taskLabel = '任务流程',
		stepId = '',
		stepLabel,
		stepPositionLabel = '',
		result,
		durationLabel = '',
		onJumpToTime = undefined,
		onPlayRange = undefined,
		onClose
	}: {
		taskLabel?: string;
		stepId?: string;
		stepLabel: string;
		stepPositionLabel?: string;
		result: ActivityTaskStepResult;
		durationLabel?: string;
		onJumpToTime?: (timeMs: number) => void;
		onPlayRange?: (range: { startMs: number; endMs: number }) => void;
		onClose: () => void;
	} = $props();

	let dialogElement: HTMLElement;

	const reviewTargets = $derived(activityTaskReviewTargets(result));
	const attentionKind = $derived(
		activityTaskStepAttentionKind(result, stepId)
	);
	const isNonBlockingReplayAdvisory = $derived(
		stepId === 'transcript_quality_gate'
		&& attentionKind === 'advisory'
	);
	const isAdvisory = $derived(attentionKind === 'advisory');
	const isHandlingGuidance = $derived(
		result.status === 'failed'
		&& result.sections.length === 0
	);
	const reviewAction = $derived(activityTaskReviewAction(stepLabel));
	const hasStructuredFailureCause = $derived(
		result.status === 'failed'
		&& (
			Boolean(result.reviewTargets?.length)
			|| result.sections.length > 0
		)
	);
	const reviewPanelLabel = $derived(
		hasStructuredFailureCause
			? '失败原因'
			: isNonBlockingReplayAdvisory
				? '建议复听'
				: isAdvisory
					? '可选建议'
					: isHandlingGuidance
						? '处理建议'
						: '建议确认'
	);
	const reviewPanelDescription = $derived(
		hasStructuredFailureCause
			? '这里说明任务为什么停止；技术代码和原始记录放在下方调试信息。'
			: isAdvisory
				? '流程已经完成，这些内容不阻塞交付；有需要时再检查或调整。'
				: isHandlingGuidance
					? '按下方建议修正输入或服务状态，然后重新提交任务。'
					: reviewAction
	);
	const statusLabel = $derived({
		todo: '尚未开始',
		running: '处理中',
		success: '已完成',
		warning: '已完成，有建议',
		failed: '处理失败',
		skipped: '已跳过',
		not_needed: '无需执行'
	}[result.status]);
	const purpose = $derived(stepPurpose(stepLabel, result.purpose));
	const overviewLayout = $derived(overviewContentLayout(result.summary, purpose));
	const sections = $derived(detailSections(result, reviewTargets));
	const notes = $derived(detailNotes(result, reviewTargets));
	const detailItemCount = $derived(sections.reduce((total, section) => total + section.items.length, 0));
	const totalReviewTargets = $derived(reviewTargets.length);
	function portal(node: HTMLElement) {
		document.body.appendChild(node);
		return { destroy: () => node.remove() };
	}

	function initializeDetailsOpen(node: HTMLDetailsElement, open: boolean) {
		node.open = open;
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

	function safeResultUrl(value?: string) {
		if (!value) return '';
		if (/^\/api\/projects\/[^/]+\/video-localization\/operations\/[^/]+\/(?:development-)?visual-evidence-frames\/frame_[0-9a-f]{12}$/.test(value)) return value;
		return safeExternalUrl(value);
	}

	function isVisualEvidenceFrame(value?: string) {
		return Boolean(value && /\/(?:development-)?visual-evidence-frames\/frame_[0-9a-f]{12}$/.test(value));
	}

	function visualEvidencePreviewUrl(value?: string) {
		const original = safeResultUrl(value);
		return isVisualEvidenceFrame(original) ? `${original}?preview=true` : '';
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

	function jumpToTarget(target: ActivityTaskReviewTarget) {
		const range = reviewTargetRange(target);
		if (!range || !onJumpToTime) return;
		onClose();
		queueMicrotask(() => onJumpToTime?.(range.startMs));
	}

	function playTarget(target: ActivityTaskReviewTarget) {
		const range = reviewTargetRange(target);
		if (!range || !onPlayRange) return;
		onClose();
		queueMicrotask(() => onPlayRange?.(range));
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
				<span class="heading-icon" aria-hidden="true"><Info size={18} /></span>
				<div>
					<div class="result-kicker">
						<span>{taskLabel}</span>
						{#if stepPositionLabel}<span class="step-position">{stepPositionLabel}</span>{/if}
					</div>
					<h2 id="task-step-result-title">{stepLabel}</h2>
				</div>
			</div>
			<div class="head-actions">
				<span class="result-status">
					<span aria-hidden="true">
						{#if result.status === 'todo'}<Circle size={11} />
						{:else if result.status === 'running'}<CommandSpinner size={12} />
						{:else if result.status === 'success'}<Check size={12} />
						{:else if result.status === 'skipped' || result.status === 'not_needed'}<CircleOff size={12} />
						{:else}<AlertTriangle size={12} />{/if}
					</span>
					{statusLabel}
				</span>
				{#if durationLabel}<span class="result-duration">{durationLabel}</span>{/if}
				<button type="button" aria-label="关闭步骤结果" onclick={onClose}><X size={16} /></button>
			</div>
		</header>

		<div class="result-body">
			<section class="result-overview" aria-label="结果概览">
				<div class="overview-copy" class:layout-rows={overviewLayout === 'rows'}>
					<article class="overview-primary">
						<span>结果综述</span>
						<p>{result.summary}</p>
					</article>
					<article class="overview-purpose">
						<span>步骤目标</span>
						<p>{purpose}</p>
					</article>
				</div>
				{#if result.metrics.length}
					<dl class="result-metrics">
						{#each result.metrics as metric}
							<div><dt>{metric.label}</dt><dd>{metric.value}</dd></div>
						{/each}
					</dl>
				{/if}
			</section>

			{#if result.document}
				<section class="result-document" aria-label="全文结果">
					<header>
						<div>
							<span>本步骤全文输出</span>
							<strong>{result.document.title}</strong>
						</div>
						<small>只读 · {result.document.format === 'markdown' ? 'Markdown' : '纯文本'}</small>
					</header>
					<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
					<div
						class="readonly-document"
						role="region"
						aria-label={`${result.document.title}只读内容`}
						tabindex="0"
					>
						{#if result.document.format === 'markdown'}
							{#each markdownBlocks(result.document.content) as block}
								{#if block.kind === 'heading-1'}<h1>{block.text}</h1>
								{:else if block.kind === 'heading-2'}<h2>{block.text}</h2>
								{:else if block.kind === 'heading-3'}<h3>{block.text}</h3>
								{:else if block.kind === 'quote'}<blockquote>{block.text}</blockquote>
								{:else if block.kind === 'bullet'}<p class="document-list">• {block.text}</p>
								{:else if block.kind === 'numbered'}<p class="document-list">— {block.text}</p>
								{:else if block.kind === 'code'}<pre>{block.text}</pre>
								{:else}<p>{block.text}</p>{/if}
							{/each}
						{:else}
							<p class="document-plain">{result.document.content}</p>
						{/if}
					</div>
				</section>
			{/if}

			{#if result.status === 'warning' || result.status === 'failed'}
				<section class="result-review" aria-label={reviewPanelLabel}>
					<header>
						<span aria-hidden="true"><AlertTriangle size={13} /></span>
						<div>
							<strong>{reviewPanelLabel}</strong>
							<p>{reviewPanelDescription}</p>
						</div>
					</header>
					{#if reviewTargets.length}
						<ul>
							{#each reviewTargets as target}
								{@const targetRange = reviewTargetRange(target)}
								{@const excerpt = targetRange ? reviewTargetExcerpt(result, target) : ''}
								<li>
									<div><b>{target.title}</b>{#if target.location}<span>{target.location}</span>{/if}</div>
									{#if excerpt}
										<div class="review-excerpt"><span>当前字幕</span><p>{excerpt}</p></div>
									{/if}
									{#if reviewTargetDetail(target, stepLabel)}<p>{reviewTargetDetail(target, stepLabel)}</p>{/if}
									{#if targetRange && (onPlayRange || onJumpToTime)}
										<div class="review-actions">
											{#if onPlayRange}
												<button type="button" onclick={() => playTarget(target)}><Play size={11} />播放这一段</button>
											{/if}
											{#if onJumpToTime}
												<button type="button" onclick={() => jumpToTarget(target)}><LocateFixed size={11} />跳到时间轴</button>
											{/if}
										</div>
									{/if}
								</li>
							{/each}
						</ul>
						{#if totalReviewTargets > reviewTargets.length}
							<small>这里先列出 {reviewTargets.length} 项，完整内容请在下方对应明细中查看。</small>
						{/if}
					{:else}
						<p class="review-fallback">{result.summary}</p>
					{/if}
				</section>
			{/if}

			{#if sections.length}
			<section class="result-details" aria-label="结果详情">
				<header class="result-details-head">
					<div><span>本步骤输出</span><strong>结果明细</strong></div>
					<small>{sections.length} 组 · {detailItemCount} 项</small>
				</header>
				{#each sections as section}
					<details class="result-section" use:initializeDetailsOpen={resultSectionOpenByDefault(section)}>
						<summary><h3>{section.title}</h3><span>{section.items.length} 项</span></summary>
						<div class="result-items">
						{#each section.items as item, index}
						{@const facts = detailFacts(item)}
						{@const itemLayout = resultItemContentLayout(item, facts)}
						{@const compactItem = itemLayout === 'columns'}
							<article
								class="result-item"
								class:item-positive={item.tone === 'positive'}
								class:item-warning={item.tone === 'warning'}
								class:item-muted={item.tone === 'muted'}
								class:item-compact={compactItem}
								class:layout-rows={itemLayout === 'rows'}
								class:item-simple={compactItem && facts.length === 0}
								class:item-paired={compactItem && facts.length === 1}
								class:item-detailed={compactItem && facts.length === 2}
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
									{#if item.text.length > 420}
										<details class="item-long-text">
											<summary><span>{longDetailPreview(item.text)}</span><b>展开内容</b></summary>
											<p>{item.text}</p>
										</details>
									{:else}
										<p class="item-text">{item.text}</p>
									{/if}
								{/if}
								{#if facts.length}
									<dl class="item-facts">
										{#each facts as fact}
											<div class:fact-wide={resultFactUsesFullRow(fact)}>
												<dt>{fact.label}</dt><dd>{fact.value}</dd>
											</div>
										{/each}
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
											{#if safeResultUrl(link.url)}
												<a
													href={safeResultUrl(link.url)}
													target="_blank"
													rel="noreferrer"
													class:item-frame-link={isVisualEvidenceFrame(link.url)}
												>
													{#if isVisualEvidenceFrame(link.url)}
														<img
															src={visualEvidencePreviewUrl(link.url)}
															alt={link.title}
															loading="lazy"
															decoding="async"
														/>
													{/if}
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
			</section>
			{/if}

			{#if notes.length}
				<section class="result-notes" aria-label={result.status === 'failed' ? '处理建议' : result.status === 'not_needed' ? '执行说明' : '质量提醒'}>
					<header>
						<span aria-hidden="true">{#if result.status === 'failed'}<AlertTriangle size={14} />{:else}<Info size={14} />{/if}</span>
						<div>
							<small>{result.status === 'failed' ? '下一步建议' : result.status === 'not_needed' ? '跳过原因' : '结果边界'}</small>
							<h3>{result.status === 'failed' ? '处理建议' : result.status === 'not_needed' ? '执行说明' : '质量提醒'}</h3>
						</div>
					</header>
					<div class="result-note-list">{#each notes as note}<p>{note}</p>{/each}</div>
				</section>
			{/if}

			{#if result.debug}
				<details class="result-debug" use:initializeDetailsOpen={false}>
					<summary>
						<span aria-hidden="true"><Info size={13} /></span>
						<div>
							<strong>调试信息</strong>
							<small>用于核对输入来源、模型配置、调用记录和结果依据；实际保存了什么就显示什么。</small>
						</div>
						<b>展开</b>
					</summary>
					<div class="debug-body">
						{#if result.debug.metrics.length}
							<dl class="debug-metrics">
								{#each result.debug.metrics as metric}<div><dt>{metric.label}</dt><dd>{metric.value}</dd></div>{/each}
							</dl>
						{/if}
						{#each result.debug.sections as section}
							<section class="debug-section">
								<header><strong>{section.title}</strong><span>{section.items.length} 项</span></header>
								{#each section.items as item}
									{@const facts = debugDetailFacts(item, result.debug.metrics)}
									<article>
										<div><b>{item.title || '记录'}</b>{#if item.meta}<span>{item.meta}</span>{/if}</div>
										{#if item.text}<p>{item.text}</p>{/if}
										{#if facts.length}
											<dl>{#each facts as fact}<div><dt>{fact.label}</dt><dd>{fact.value}</dd></div>{/each}</dl>
										{/if}
									</article>
								{/each}
							</section>
						{/each}
						{#if result.debug.notes.length}
							<div class="debug-notes">{#each result.debug.notes as note}<p>{note}</p>{/each}</div>
						{/if}
					</div>
				</details>
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
		padding: clamp(48px, 7vh, 82px) 18px 24px;
		background: rgba(3, 7, 10, 0.8);
		backdrop-filter: blur(4px);
		animation: backdrop-in 140ms ease-out;
	}
	.result-dialog {
		width: min(920px, calc(100vw - 36px));
		max-height: min(820px, calc(100dvh - 56px));
		display: grid;
		grid-template-rows: auto minmax(0, 1fr);
		border: 1px solid #34454e;
		border-radius: 14px;
		background: #11181d;
		box-shadow: 0 30px 90px rgba(0, 0, 0, 0.58), 0 1px 0 rgba(255, 255, 255, 0.045) inset;
		color: #d8e1e5;
		font-family: Inter, "SF Pro Text", "PingFang SC", "Microsoft YaHei", sans-serif;
		outline: none;
		overflow: hidden;
		animation: dialog-in 170ms ease-out;
	}
	.result-head {
		min-height: 82px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 24px;
		padding: 15px 18px 15px 20px;
		border-bottom: 1px solid #293840;
		background:
			linear-gradient(105deg, rgba(74, 137, 153, 0.12), transparent 40%),
			#151e24;
	}
	.result-heading, .head-actions, .result-status { display: flex; align-items: center; }
	.result-heading { min-width: 0; gap: 13px; }
	.heading-icon {
		width: 40px;
		height: 40px;
		display: grid;
		place-items: center;
		flex: 0 0 auto;
		border: 1px solid #416574;
		border-radius: 10px;
		background: linear-gradient(145deg, #1c333c, #16272e);
		color: #8ed0df;
		box-shadow: 0 0 0 3px rgba(95, 174, 195, 0.05);
	}
	.result-heading div { min-width: 0; }
	.result-kicker { display: flex; align-items: center; gap: 7px; margin-bottom: 3px; color: #7e929b; font-size: 10px; line-height: 1.3; }
	.step-position { padding: 2px 6px; border: 1px solid #35464e; border-radius: 999px; background: #182229; color: #91a4ac; }
	.result-heading h2 { margin: 0; overflow: hidden; color: #f0f5f7; font-size: 17px; font-weight: 680; letter-spacing: -0.01em; text-overflow: ellipsis; white-space: nowrap; }
	.head-actions { flex: 0 0 auto; gap: 10px; }
	.result-status {
		gap: 5px;
		padding: 5px 9px;
		border: 1px solid rgba(87, 164, 121, 0.28);
		border-radius: 999px;
		background: rgba(67, 135, 96, 0.1);
		color: #83d1a5;
		font-size: 10px;
		font-weight: 620;
		white-space: nowrap;
	}
	.status-warning .result-status { color: #d8b36f; }
	.status-failed .result-status { color: #df8d8e; }
	.status-running .result-status { color: #78bed2; }
	.status-warning .result-status { border-color: rgba(184, 139, 70, 0.28); background: rgba(149, 106, 48, 0.1); }
	.status-failed .result-status { border-color: rgba(190, 88, 89, 0.28); background: rgba(144, 55, 57, 0.1); }
	.status-running .result-status { border-color: rgba(80, 155, 176, 0.28); background: rgba(60, 125, 144, 0.1); }
	.result-duration { color: #7f9199; font-size: 10px; white-space: nowrap; }
	.head-actions button {
		width: 34px;
		height: 34px;
		display: grid;
		place-items: center;
		padding: 0;
		border: 1px solid transparent;
		border-radius: 8px;
		background: transparent;
		color: #8799a1;
		cursor: pointer;
	}
	.head-actions button:hover { border-color: #33434b; background: #202b31; color: #eef4f6; }
	.head-actions button:focus-visible, .result-section > summary:focus-visible, .item-long-text > summary:focus-visible {
		outline: 2px solid #6bb6c8;
		outline-offset: 2px;
	}
	.result-body { min-height: 0; padding: 20px 22px 22px; overflow: auto; overscroll-behavior: contain; }
	.result-overview {
		overflow: hidden;
		border: 1px solid #2b3a42;
		border-radius: 10px;
		background: #151e24;
		box-shadow: 0 1px 0 rgba(255, 255, 255, 0.025) inset;
	}
	.overview-copy {
		display: grid;
		grid-template-columns: minmax(0, 1.35fr) minmax(240px, 0.65fr);
		gap: 1px;
		background: #2b3a42;
	}
	.overview-copy.layout-rows { grid-template-columns: minmax(0, 1fr); }
	.overview-primary, .overview-purpose { display: grid; align-content: start; gap: 6px; padding: 16px 17px; background: #151e24; }
	.overview-primary { position: relative; }
	.overview-primary::before { content: ""; position: absolute; top: 16px; bottom: 16px; left: 0; width: 3px; border-radius: 0 3px 3px 0; background: #6bb6c8; }
	.overview-primary span, .overview-purpose span { color: #7f939c; font-size: 9.5px; font-weight: 680; letter-spacing: 0.04em; }
	.overview-primary p, .overview-purpose p { margin: 0; overflow-wrap: anywhere; }
	.overview-primary p { color: #e1eaed; font-size: 13px; font-weight: 560; line-height: 1.65; }
	.overview-purpose p { color: #a7b6bc; font-size: 11px; line-height: 1.6; }
	.result-metrics {
		display: flex;
		flex-wrap: wrap;
		align-items: stretch;
		gap: 6px;
		margin: 0;
		padding: 8px 10px 9px;
		border-top: 1px solid #2b3a42;
		background: #131b20;
	}
	.result-metrics div {
		min-width: 0;
		min-height: 44px;
		flex: 0 1 142px;
		max-width: 142px;
		padding: 7px 9px 8px;
		border: 1px solid #2b3a42;
		border-radius: 6px;
		background: linear-gradient(145deg, #182329, #151f24);
		box-shadow: 0 1px 0 rgba(255, 255, 255, 0.025) inset;
	}
	.result-metrics dt { margin-bottom: 2px; color: #73868f; font-size: 8px; }
	.result-metrics dd { margin: 0; overflow-wrap: anywhere; color: #e0e9ec; font-size: 13px; font-weight: 680; line-height: 1.2; }
	.result-review {
		display: grid;
		gap: 12px;
		margin-top: 16px;
		padding: 14px;
		border: 1px solid rgba(174, 129, 66, 0.3);
		border-radius: 9px;
		background: rgba(130, 91, 39, 0.08);
	}
	.result-document {
		margin-top: 18px;
		overflow: hidden;
		border: 1px solid #2b3a42;
		border-radius: 10px;
		background: #131b20;
	}
	.result-document > header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 14px;
		padding: 13px 15px;
		border-bottom: 1px solid #2b3a42;
		background: #172127;
	}
	.result-document > header > div { display: grid; gap: 1px; }
	.result-document > header span { color: #6f838c; font-size: 8.5px; letter-spacing: 0.04em; }
	.result-document > header strong { color: #dce5e8; font-size: 12px; font-weight: 670; }
	.result-document > header small {
		padding: 4px 7px;
		border: 1px solid #304149;
		border-radius: 999px;
		background: #131b20;
		color: #83959d;
		font-size: 8.5px;
		white-space: nowrap;
	}
	.readonly-document {
		max-height: min(52dvh, 540px);
		padding: 22px clamp(18px, 4vw, 42px) 28px;
		overflow: auto;
		overscroll-behavior: contain;
		background:
			linear-gradient(180deg, rgba(89, 145, 160, 0.035), transparent 140px),
			#11191e;
		color: #c8d3d7;
		scrollbar-gutter: stable;
	}
	.readonly-document:focus-visible { outline: 2px solid #6bb6c8; outline-offset: -3px; }
	.readonly-document h1,
	.readonly-document h2,
	.readonly-document h3 { color: #edf3f5; overflow-wrap: anywhere; }
	.readonly-document h1 { margin: 0 0 24px; font-size: 22px; line-height: 1.35; letter-spacing: -0.02em; }
	.readonly-document h2 { margin: 30px 0 12px; padding-top: 4px; color: #dce9ec; font-size: 16px; line-height: 1.45; }
	.readonly-document h2:first-child { margin-top: 0; }
	.readonly-document h3 { margin: 22px 0 9px; font-size: 13px; line-height: 1.5; }
	.readonly-document p,
	.readonly-document blockquote {
		margin: 0 0 13px;
		color: #c0ccd0;
		font-size: 12px;
		line-height: 1.9;
		overflow-wrap: anywhere;
		white-space: pre-wrap;
	}
	.readonly-document blockquote { padding-left: 13px; border-left: 2px solid #568c9b; color: #9fb2b9; }
	.readonly-document .document-list { padding-left: 12px; }
	.readonly-document pre {
		margin: 0 0 14px;
		padding: 11px 12px;
		overflow: auto;
		border: 1px solid #293940;
		border-radius: 6px;
		background: #0d1418;
		color: #b7c7cc;
		font: 10px/1.65 ui-monospace, SFMono-Regular, Menlo, monospace;
		white-space: pre-wrap;
	}
	.readonly-document .document-plain { margin: 0; white-space: pre-wrap; }
	.result-review > header { display: grid; grid-template-columns: 22px minmax(0, 1fr); gap: 9px; align-items: start; }
	.result-review > header > span { display: grid; place-items: center; padding-top: 1px; color: #d3a25f; }
	.result-review strong { color: #e2bd83; font-size: 11px; }
	.result-review header p, .result-review li p, .review-fallback { margin: 4px 0 0; color: #baa889; font-size: 10.5px; line-height: 1.55; overflow-wrap: anywhere; }
	.result-review ul { display: grid; gap: 1px; margin: 0; padding: 0; overflow: hidden; border: 1px solid rgba(142, 112, 69, 0.24); border-radius: 7px; background: rgba(142, 112, 69, 0.16); list-style: none; }
	.result-review li { min-width: 0; padding: 9px 10px; background: #171e22; }
	.result-review li > div { min-width: 0; display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
	.result-review li b { min-width: 0; overflow: hidden; color: #ccd6da; font-size: 10px; font-weight: 630; text-overflow: ellipsis; white-space: nowrap; }
	.result-review li span { flex: 0 0 auto; color: #8d806c; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 9px; }
	.result-review li p { color: #9eaaa9; }
	.result-review small { color: #8e816d; font-size: 9px; line-height: 1.45; }
	.result-review li .review-excerpt {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		align-items: start;
		justify-content: start;
		gap: 8px;
		margin-top: 7px;
		padding: 7px 8px;
		border: 1px solid rgba(104, 148, 160, 0.18);
		border-radius: 5px;
		background: rgba(74, 116, 128, 0.08);
	}
	.result-review li .review-excerpt span {
		padding-top: 1px;
		color: #78909a;
		font-family: inherit;
		font-size: 8.5px;
		font-weight: 650;
	}
	.result-review li .review-excerpt p { margin: 0; color: #c4d0d4; }
	.review-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
	.review-actions button {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		min-height: 28px;
		padding: 5px 8px;
		border: 1px solid #354a53;
		border-radius: 6px;
		background: #1a272d;
		color: #a9c3cb;
		font: inherit;
		font-size: 9px;
		cursor: pointer;
	}
	.review-actions button:hover { border-color: #4f7783; background: #20323a; color: #d9e7eb; }
	.review-actions button:focus-visible { outline: 2px solid #6bb6c8; outline-offset: 2px; }
	.result-details {
		margin-top: 18px;
		overflow: hidden;
		border: 1px solid #2b3a42;
		border-radius: 10px;
		background: #141d22;
	}
	.result-details-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 14px;
		padding: 13px 15px;
		border-bottom: 1px solid #2b3a42;
		background: #172127;
	}
	.result-details-head > div { display: grid; gap: 1px; }
	.result-details-head span { color: #6f838c; font-size: 8.5px; letter-spacing: 0.04em; }
	.result-details-head strong { color: #dce5e8; font-size: 12px; font-weight: 670; }
	.result-details-head small {
		padding: 4px 7px;
		border: 1px solid #304149;
		border-radius: 999px;
		background: #131b20;
		color: #83959d;
		font-size: 8.5px;
		white-space: nowrap;
	}
	.result-section + .result-section { border-top: 1px solid #29373e; }
	.result-section > summary {
		display: grid;
		grid-template-columns: 16px minmax(0, 1fr) auto;
		align-items: center;
		gap: 9px;
		min-height: 42px;
		padding: 0 14px;
		color: #8799a1;
		cursor: pointer;
		list-style: none;
		background: #151f24;
	}
	.result-section > summary::-webkit-details-marker { display: none; }
	.result-section > summary::before { content: '›'; color: #6f858f; font-size: 16px; transform: rotate(0deg); transition: transform 120ms ease; }
	.result-section[open] > summary::before { transform: rotate(90deg); }
	.result-section h3, .result-notes h3 { margin: 0; }
	.result-section > summary h3 { color: #b9c7cc; font-size: 10.5px; font-weight: 650; }
	.result-section > summary span { padding: 3px 6px; border-radius: 999px; background: #202c32; color: #82959d; font-size: 8.5px; }
	.result-section[open] > summary { background: #182329; }
	.result-items { border-top: 1px solid #29373e; }
	.result-item { padding: 11px 14px 12px; border-bottom: 1px solid #25333a; background: #131b20; }
	.result-item:last-child { border-bottom: 0; }
	.result-item:hover { background: #151f24; }
	.item-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 7px; }
	.item-heading strong { min-width: 0; overflow: hidden; color: #cfdbdf; font-size: 10.5px; font-weight: 660; text-overflow: ellipsis; white-space: nowrap; }
	.item-heading span { flex: 0 0 auto; color: #71838b; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 9px; }
	.item-text, .comparison p, .result-notes p { margin: 0; color: #b5c2c7; font-size: 11px; line-height: 1.65; overflow-wrap: anywhere; white-space: pre-wrap; }
	.result-item.item-compact {
		display: grid;
		align-items: start;
	}
	.result-item.item-compact.item-simple {
		grid-template-columns: minmax(116px, 142px) minmax(0, 1fr);
		gap: 18px;
	}
	.result-item.item-compact.item-detailed {
		grid-template-columns: minmax(116px, 142px) minmax(0, 1fr) minmax(220px, 280px);
		gap: 16px;
	}
	.item-compact .item-heading { min-width: 0; display: block; margin: 0; padding-top: 2px; }
	.item-compact .item-heading strong {
		display: block;
		overflow-wrap: anywhere;
		color: #9fc4cf;
		font-size: 10px;
		font-weight: 680;
		letter-spacing: 0.01em;
		line-height: 1.4;
		text-overflow: clip;
		white-space: normal;
	}
	.item-compact .item-text { min-width: 0; color: #c2cdd1; line-height: 1.55; }
	.item-compact .item-facts {
		min-width: 0;
		display: flex;
		gap: 12px;
		align-self: start;
		margin: 0;
		overflow: visible;
		border: 0;
		background: transparent;
	}
	.item-compact .item-facts div { min-width: 0; flex: 1 1 0; padding: 0; background: transparent; text-align: right; }
	.item-compact .item-facts dt { margin-bottom: 2px; color: #667b84; }
	.result-item.item-compact.item-paired {
		position: relative;
		grid-template-areas: "heading facts" "text facts";
		grid-template-columns: minmax(0, 1.3fr) minmax(220px, 0.7fr);
		column-gap: 0;
		row-gap: 0;
		padding: 0;
		background:
			linear-gradient(90deg, rgba(85, 153, 172, 0.04), transparent 64%),
			#131b20;
	}
	.item-paired .item-heading {
		grid-area: heading;
		padding: 12px 16px 0;
	}
	.item-paired .item-text {
		grid-area: text;
		padding: 5px 16px 14px;
		color: #c9d4d8;
		line-height: 1.62;
	}
	.item-paired .item-facts {
		grid-area: facts;
		align-self: stretch;
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		border-left: 1px solid #2a3940;
		background: rgba(24, 34, 40, 0.72);
	}
	.item-paired .item-facts div {
		display: grid;
		align-content: start;
		gap: 4px;
		padding: 12px 15px 14px;
		text-align: left;
	}
	.item-paired .item-facts dt { margin: 0; color: #728992; font-size: 9px; font-weight: 650; }
	.item-paired .item-facts dd { color: #b9c8cd; font-size: 10.5px; line-height: 1.6; }
	.item-long-text { margin: 0; }
	.item-long-text > summary { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 12px; margin: 0; color: #aebbc0; cursor: pointer; list-style: none; }
	.item-long-text > summary::-webkit-details-marker { display: none; }
	.item-long-text > summary span { font-size: 11px; line-height: 1.65; white-space: pre-wrap; }
	.item-long-text > summary b { color: #82c4d4; font-size: 9px; font-weight: 620; white-space: nowrap; }
	.item-long-text[open] > summary { display: none; }
	.item-long-text > p { margin: 0; color: #b7c4c9; font-size: 11px; line-height: 1.72; overflow-wrap: anywhere; white-space: pre-wrap; }
	.comparison { display: grid; gap: 7px; }
	.comparison p { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 10px; }
	.comparison b { color: #738991; font-size: 9.5px; font-weight: 620; }
	.comparison p:last-child span { color: #cad8dc; }
	.comparison mark { padding: 0; background: transparent; color: inherit; }
	.comparison mark.changed { padding: 1px 3px; border-radius: 3px; background: rgba(95, 176, 199, 0.16); color: #b1dfeb; }
	.item-detail { margin: 8px 0 0; padding-left: 82px; color: #879aa2; font-size: 10px; line-height: 1.6; overflow-wrap: anywhere; }
	.item-facts { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 1px; margin: 9px 0 0; overflow: hidden; border: 1px solid #2d373c; border-radius: 4px; background: #2d373c; }
	.item-facts > div { grid-column: span 2; }
	.item-facts > div.fact-wide { grid-column: 1 / -1; }
	.item-facts div { min-width: 0; padding: 8px 9px; background: #172127; }
	.item-facts dt { margin-bottom: 3px; color: #71858e; font-size: 8.5px; }
	.item-facts dd { margin: 0; overflow-wrap: anywhere; color: #bbc7cb; font-size: 9.5px; line-height: 1.45; }
	.item-visual { display: grid; grid-template-columns: 132px minmax(0, 1fr); align-items: center; gap: 10px; margin-top: 9px; color: #73838a; font-size: 8.5px; }
	.item-visual > div { height: 4px; overflow: hidden; border-radius: 2px; background: #293238; }
	.item-visual i { display: block; height: 100%; border-radius: inherit; background: #65aabd; }
	.item-links { display: grid; gap: 4px; margin-top: 9px; }
	.result-item .item-links a { width: 100%; display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 8px; margin: 0; padding: 7px 8px; border: 1px solid #2c373c; border-radius: 4px; background: #181e22; text-decoration: none; }
	.result-item .item-links a.item-frame-link { grid-template-columns: 96px minmax(0, 1fr) auto auto; }
	.item-frame-link img { width: 96px; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 3px; background: #0f1519; }
	.item-links a > span { min-width: 0; display: grid; gap: 2px; }
	.item-links strong { overflow: hidden; color: #9fc4cf; font-size: 9.5px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
	.item-links small { overflow: hidden; color: #708087; font-size: 8.5px; line-height: 1.4; text-overflow: ellipsis; white-space: nowrap; }
	.item-links em { color: #66767d; font-size: 8px; font-style: normal; white-space: nowrap; }
	.result-item.item-positive { border-left: 2px solid #4c8d71; padding-left: 10px; }
	.result-item.item-warning { border-left: 2px solid #9b7945; padding-left: 10px; }
	.result-item.item-muted { opacity: 0.78; }
	.result-item a { width: fit-content; display: inline-flex; align-items: center; gap: 4px; margin-top: 7px; color: #72b8cc; font-size: 9.5px; text-decoration: none; }
	.result-item a:hover { color: #a4d8e6; text-decoration: underline; }
	.result-notes {
		display: grid;
		grid-template-columns: 150px minmax(0, 1fr);
		gap: 16px;
		margin-top: 18px;
		padding: 14px 15px;
		border: 1px solid rgba(170, 127, 63, 0.28);
		border-radius: 10px;
		background: linear-gradient(100deg, rgba(134, 92, 34, 0.12), rgba(106, 80, 42, 0.04));
	}
	.result-notes > header { display: grid; grid-template-columns: 25px minmax(0, 1fr); gap: 8px; align-items: start; }
	.result-notes > header > span {
		width: 25px;
		height: 25px;
		display: grid;
		place-items: center;
		border: 1px solid rgba(197, 147, 74, 0.3);
		border-radius: 7px;
		background: rgba(150, 101, 38, 0.12);
		color: #d3a25f;
	}
	.result-notes header div { display: grid; gap: 2px; }
	.result-notes small { color: #8d7b62; font-size: 8.5px; letter-spacing: 0.04em; }
	.result-notes h3 { color: #d8b47d; font-size: 11px; font-weight: 680; }
	.result-note-list { display: grid; gap: 6px; align-content: start; }
	.result-notes p { position: relative; padding-left: 12px; color: #bea987; }
	.result-notes p::before { content: ""; position: absolute; top: 0.72em; left: 0; width: 4px; height: 4px; border-radius: 50%; background: #a87b43; }
	.result-debug {
		margin-top: 14px;
		overflow: hidden;
		border: 1px solid #2b3a42;
		border-radius: 9px;
		background: #131b20;
	}
	.result-debug > summary {
		display: grid;
		grid-template-columns: 22px minmax(0, 1fr) auto;
		align-items: center;
		gap: 9px;
		min-height: 46px;
		padding: 0 13px;
		color: #738890;
		cursor: pointer;
		list-style: none;
	}
	.result-debug > summary::-webkit-details-marker { display: none; }
	.result-debug > summary > span { display: grid; place-items: center; }
	.result-debug > summary > div { min-width: 0; display: grid; gap: 2px; }
	.result-debug > summary strong { color: #aebdc2; font-size: 10px; }
	.result-debug > summary small {
		overflow: hidden;
		color: #718188;
		font-size: 8.5px;
		line-height: 1.35;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.result-debug > summary > b { color: #78939c; font-size: 8.5px; font-weight: 600; }
	.result-debug[open] > summary { border-bottom: 1px solid #29373e; background: #162027; }
	.result-debug[open] > summary > b { color: #8db9c5; }
	.debug-body { display: grid; gap: 10px; padding: 10px 12px 12px; }
	.debug-metrics {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
		gap: 5px;
		margin: 0;
	}
	.debug-metrics div,
	.debug-section article {
		min-width: 0;
		padding: 7px 8px;
		border: 1px solid #29363c;
		border-radius: 5px;
		background: #161f24;
	}
	.debug-metrics dt,
	.debug-section dt { color: #6f838b; font-size: 8px; }
	.debug-metrics dd,
	.debug-section dd {
		margin: 2px 0 0;
		overflow-wrap: anywhere;
		color: #b8c5ca;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 9px;
	}
	.debug-section { display: grid; gap: 5px; }
	.debug-section > header,
	.debug-section article > div { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
	.debug-section > header strong { color: #91a4ab; font-size: 9px; }
	.debug-section > header span,
	.debug-section article span { color: #687a82; font-size: 8px; }
	.debug-section article b { color: #aab9be; font-size: 9px; }
	.debug-section article p,
	.debug-notes p { margin: 4px 0 0; color: #85979e; font-size: 9px; line-height: 1.5; overflow-wrap: anywhere; }
	.debug-section article dl { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0 0; }
	.debug-section article dl div { min-width: 100px; }
	.debug-notes { padding-top: 2px; border-top: 1px solid #253239; }
	@keyframes backdrop-in { from { opacity: 0; } }
	@keyframes dialog-in { from { opacity: 0; transform: translateY(-8px) scale(0.99); } }
	@media (max-width: 720px) {
		.result-backdrop { padding: 10px 8px; }
		.result-dialog { width: calc(100vw - 16px); max-height: calc(100dvh - 32px); }
		.result-head { min-height: 70px; align-items: flex-start; gap: 10px; padding: 12px; }
		.heading-icon { width: 34px; height: 34px; border-radius: 8px; }
		.result-heading h2 { font-size: 15px; }
		.head-actions { gap: 5px; }
		.result-duration { display: none; }
		.result-status { padding: 4px 7px; }
		.result-body { padding: 14px; }
		.overview-copy { grid-template-columns: 1fr; }
		.result-metrics { gap: 5px; padding: 7px; }
		.result-metrics div { min-height: 42px; flex-basis: 112px; max-width: 132px; padding: 7px 8px; }
		.item-facts { grid-template-columns: 1fr; }
		.item-facts > div,
		.item-facts > div.fact-wide { grid-column: 1; }
		.result-item.item-compact { grid-template-columns: 1fr; gap: 4px; }
		.result-item.item-compact.item-simple,
		.result-item.item-compact.item-detailed { grid-template-columns: 1fr; gap: 4px; }
		.result-item.item-compact.item-paired { grid-template-areas: "heading" "text" "facts"; grid-template-columns: 1fr; }
		.item-compact .item-facts { justify-content: flex-start; }
		.item-compact .item-facts div { text-align: left; }
		.item-paired .item-facts { border-top: 1px solid #2a3940; border-left: 0; }
		.result-notes { grid-template-columns: 1fr; gap: 10px; }
		.item-visual { grid-template-columns: 1fr; gap: 5px; }
		.result-item .item-links a { grid-template-columns: minmax(0, 1fr) auto; }
		.item-links em { display: none; }
	}
	@media (prefers-reduced-motion: reduce) {
		.result-backdrop, .result-dialog { animation: none; }
	}
</style>
