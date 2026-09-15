#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';

import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const baseUrl = process.env.VIDEO_LOCALIZATION_E2E_URL || 'http://127.0.0.1:5173';
const projectId = process.env.VIDEO_LOCALIZATION_E2E_PROJECT_ID || '';
const taskLabel = process.env.VIDEO_LOCALIZATION_E2E_TASK_LABEL || '从人声轨生成 ASR 字幕';
const simulateInitialApiOutage = process.env.VIDEO_LOCALIZATION_E2E_INITIAL_API_OUTAGE === '1';

async function loadChromium() {
	try {
		const mod = await import('playwright');
		return mod.chromium ?? mod.default?.chromium;
	} catch {
		const localPlaywright = resolve('frontend/node_modules/playwright/index.js');
		if (!existsSync(localPlaywright)) {
			throw new Error('未找到 frontend/node_modules/playwright，请先安装前端依赖。');
		}
		const mod = await import(pathToFileURL(localPlaywright).href);
		return mod.chromium ?? mod.default?.chromium;
	}
}

async function openLatestAsrTask(
	page,
	{ operationDetailRequests = [], verifyLazyDetail = false } = {},
) {
	const taskCenter = page.locator('section[aria-label="后台任务进度"]');
	await taskCenter.waitFor({ state: 'visible' });
	await taskCenter.locator('article.history-row').first().waitFor({ state: 'visible' });
	let task = taskCenter.locator('article.history-row').filter({ hasText: taskLabel }).first();
	for (let pageIndex = 0; pageIndex < 20; pageIndex += 1) {
		if ((await task.count()) > 0 && (await task.isVisible())) break;
		const showEarlier = taskCenter.getByRole('button', {
			name: /查看(?:已加载的|更早的)/,
		});
		if ((await showEarlier.count()) === 0) break;
		await showEarlier.click();
		task = taskCenter.locator('article.history-row').filter({ hasText: taskLabel }).first();
	}
	await task.waitFor({ state: 'visible' });
	const summary = task.locator('button.history-summary');
	if ((await summary.getAttribute('aria-expanded')) !== 'true') {
		const requestsBeforeExpansion = operationDetailRequests.length;
		if (verifyLazyDetail && requestsBeforeExpansion > 0) {
			throw new Error('任务历史初始展示时提前读取了完整 operation。');
		}
		await summary.click();
		await task.getByText('处理流程', { exact: true }).waitFor();
		if (
			verifyLazyDetail
			&& operationDetailRequests.length <= requestsBeforeExpansion
		) {
			throw new Error('首次展开历史任务时没有按需读取完整 operation。');
		}
	}
	await task.getByText('处理流程', { exact: true }).waitFor();
	return task;
}

async function verifyCompactTaskFacts(task) {
	const layout = await task.evaluate((taskElement) => {
		let facts = taskElement.querySelector('.task-summary-facts');
		let injected = false;
		if (!(facts instanceof HTMLElement)) {
			const scopedElement = [taskElement, ...taskElement.querySelectorAll('[class]')]
				.find((element) => [...element.classList].some((name) => name.startsWith('svelte-')));
			const scopeClass = scopedElement
				? [...scopedElement.classList].find((name) => name.startsWith('svelte-'))
				: '';
			facts = document.createElement('dl');
			facts.classList.add('task-summary-facts');
			if (scopeClass) facts.classList.add(scopeClass);
			const fact = document.createElement('div');
			if (scopeClass) fact.classList.add(scopeClass);
			facts.append(fact);
			taskElement.append(facts);
			injected = true;
		}
		const style = getComputedStyle(facts);
		const firstFact = facts.firstElementChild;
		const firstFactStyle = firstFact ? getComputedStyle(firstFact) : null;
		const result = {
			display: style.display,
			flexWrap: style.flexWrap,
			firstFactMaxWidth: firstFactStyle?.maxWidth ?? '',
		};
		if (injected) facts.remove();
		return result;
	});
	if (
		layout.display !== 'flex'
		|| layout.flexWrap !== 'wrap'
		|| layout.firstFactMaxWidth !== '60px'
	) {
		throw new Error(
			`任务摘要布局异常：${JSON.stringify(layout)}`,
		);
	}
}

async function verifyResultDialogs(page, task, operationDetailRequests) {
	const requestsBeforeDialog = operationDetailRequests.length;
	await task.getByRole('button', { name: /最终结果/ }).click();
	const finalDialog = page.getByRole('dialog');
	await finalDialog.getByLabel('结果概览').waitFor();
	await finalDialog.getByLabel('结果详情').waitFor();
	await finalDialog.getByText('结果明细', { exact: true }).waitFor();
	await finalDialog.getByText(/生成.*条.*字幕|共汇总.*步骤/).first().waitFor();
	if (operationDetailRequests.length !== requestsBeforeDialog) {
		throw new Error('打开结果对话框时没有复用展开任务已加载的详情缓存。');
	}
	const requestsAfterFirstDialog = operationDetailRequests.length;
	await finalDialog.getByRole('button', { name: '关闭步骤结果' }).click();

	await task.getByRole('button', { name: /查看“对齐逐词时间”的结果/ }).click();
	const alignmentDialog = page.getByRole('dialog');
	await alignmentDialog.getByRole('heading', { name: '对齐逐词时间' }).waitFor();
	await alignmentDialog.getByText('调试信息', { exact: true }).waitFor();
	const dialogText = await alignmentDialog.innerText();
	if (/\b\d{4,}\s*毫秒\b/.test(dialogText)) {
		throw new Error('逐词对齐详情仍在向用户显示大整数毫秒。');
	}
	if (operationDetailRequests.length !== requestsAfterFirstDialog) {
		throw new Error('同一 operation 的第二个结果没有复用已加载的详情缓存。');
	}
	await alignmentDialog.getByRole('button', { name: '关闭步骤结果' }).click();
}

async function verifySubtitleDisplayConsistency(page) {
	await page.setViewportSize({ width: 1440, height: 1000 });
	let cue = page.locator('button.cue-chip.cue-asr.active:not(.preview-cue)').first();
	if ((await cue.count()) === 0) {
		cue = page.locator('button.cue-chip.cue-asr:not(.preview-cue)').first();
		await cue.waitFor({ state: 'visible' });
		await cue.scrollIntoViewIfNeeded();
		await cue.click();
		cue = page.locator('button.cue-chip.cue-asr.active:not(.preview-cue)').first();
	}
	await cue.waitFor({ state: 'visible' });
	await cue.scrollIntoViewIfNeeded();
	const targetCueId = await cue.getAttribute('data-subtitle-item-id');
	const timelineText = (await cue.locator('.cue-text span').innerText()).trim();
	if (!timelineText) throw new Error('ASR 时间线字幕没有可比较的显示文本。');

	const selectedCueIds = await page.locator('button.cue-chip.cue-asr.selected')
		.evaluateAll((items) => items.map((item) => item.getAttribute('data-subtitle-item-id')));
	const activeCueIds = await page.locator('button.cue-chip.cue-asr.active')
		.evaluateAll((items) => items.map((item) => item.getAttribute('data-subtitle-item-id')));
	const inspector = page.locator('aside.inspector');
	await inspector
		.locator('.inspector-mode-tabs')
		.getByRole('button', { name: '字幕', exact: true })
		.click();
	const inspectorText = (
		await inspector.getByLabel('原文/ASR', { exact: true }).inputValue()
	).trim();
	if (inspectorText !== timelineText) {
		throw new Error(
			`字幕状态源不一致：${JSON.stringify({
				targetCueId,
				timelineText,
				inspectorText,
				selectedCueIds,
				activeCueIds,
			})}`,
		);
	}

	const showAsrButton = page.getByRole('button', { name: '显示 ASR 字幕', exact: true });
	const restoreAsrHidden = (await showAsrButton.count()) > 0;
	if (restoreAsrHidden) {
		await showAsrButton.click();
		await page.getByRole('button', { name: '隐藏 ASR 字幕', exact: true }).waitFor();
	}
	const cueStartMs = Number(
		await cue.locator('.cue-handle').first().getAttribute('aria-valuenow'),
	);
	if (!Number.isFinite(cueStartMs)) {
		throw new Error(`ASR 字幕 ${targetCueId ?? ''} 缺少可用的入点。`);
	}
	const previewVideo = page.locator('.video-preview video').first();
	await previewVideo.evaluate((video, startMs) => {
		video.currentTime = (Number(startMs) + 50) / 1000;
		video.dispatchEvent(new Event('timeupdate'));
	}, cueStartMs);
	await page.waitForFunction((expected) => (
		[...document.querySelectorAll('.subtitle-overlay p')]
			.some((item) => item.textContent?.trim() === expected)
	), timelineText);
	const previewLines = await page.locator('.subtitle-overlay p').allInnerTexts();
	if (!previewLines.map((line) => line.trim()).includes(timelineText)) {
		throw new Error(
			`播放器上屏字幕没有复用时间线文本：${JSON.stringify(previewLines)}`,
		);
	}
	if (restoreAsrHidden) {
		await page.getByRole('button', { name: '隐藏 ASR 字幕', exact: true }).click();
		await page.getByRole('button', { name: '显示 ASR 字幕', exact: true }).waitFor();
	}
	await page.locator('video, audio').evaluateAll((elements) => {
		for (const element of elements) {
			if (element instanceof HTMLMediaElement) element.pause();
		}
	});
}

async function verifyLowZoomSubtitleHitTesting(page) {
	await page.setViewportSize({ width: 1440, height: 1000 });
	const zoomOut = page.getByRole('button', { name: '缩小时间线', exact: true });
	for (let attempt = 0; attempt < 30 && await zoomOut.isEnabled(); attempt += 1) {
		await zoomOut.click();
	}
	const zoomLabel = (await page.locator('.zoom-stepper span').innerText()).trim();
	if (!/^1(?:\.0)?x$/.test(zoomLabel)) throw new Error(`时间线没有回到 1x：${zoomLabel}`);

	const cues = page.locator('button.cue-chip.cue-asr:not(.preview-cue)');
	if ((await cues.count()) < 3) throw new Error('低缩放命中回归至少需要 3 条 ASR 字幕。');

	const cueData = async (index) => {
		const cue = cues.nth(index);
		await cue.waitFor({ state: 'visible' });
		await cue.scrollIntoViewIfNeeded();
		return cue.evaluate((element) => {
			const handles = element.querySelectorAll('.cue-handle');
			const startMs = Number(handles[0]?.getAttribute('aria-valuenow'));
			const endMs = Number(handles[1]?.getAttribute('aria-valuenow'));
			const durationMs = Number(handles[0]?.getAttribute('aria-valuemax'));
			const content = element.closest('.timeline-content');
			if (!(content instanceof HTMLElement)) throw new Error('字幕不在 timeline-content 内。');
			const contentRect = content.getBoundingClientRect();
			const cueRect = element.getBoundingClientRect();
			const midpointMs = (startMs + endMs) / 2;
			return {
				itemId: element.getAttribute('data-subtitle-item-id') ?? '',
				startMs,
				endMs,
				durationMs,
				width: cueRect.width,
				x: contentRect.left + (midpointMs / durationMs) * contentRect.width,
				y: cueRect.top + cueRect.height / 2,
				pointerTrimDisabled: element.classList.contains('pointer-trim-disabled')
			};
		});
	};
	const waitForActiveCue = async (itemId, timeout = 30_000) => {
		await page.waitForFunction((expected) => (
			[...document.querySelectorAll('button.cue-chip.cue-asr.active:not(.preview-cue)')]
				.some((cue) => cue.getAttribute('data-subtitle-item-id') === expected)
		), itemId, { timeout });
	};
	const selectedCueIds = () => page.locator('button.cue-chip.cue-asr.selected:not(.preview-cue)')
		.evaluateAll((items) => items.map((item) => item.getAttribute('data-subtitle-item-id')));

	const first = await cueData(0);
	const second = await cueData(1);
	const third = await cueData(2);
	if (first.width >= 8 || second.width >= 8) {
		throw new Error(`验收项目没有覆盖低缩放亚像素字幕：${JSON.stringify({ first, second })}`);
	}
	if (!first.pointerTrimDisabled || !second.pointerTrimDisabled) {
		throw new Error('低缩放字幕仍暴露会覆盖整个片段的拖边手柄。');
	}

	await page.mouse.click(first.x, first.y);
	await waitForActiveCue(first.itemId);

	await page.keyboard.down('Control');
	await page.mouse.click(second.x, second.y);
	await page.keyboard.up('Control');
	const additiveSelection = await selectedCueIds();
	if (!additiveSelection.includes(first.itemId) || !additiveSelection.includes(second.itemId)) {
		throw new Error(`低缩放多选丢失字幕：${JSON.stringify(additiveSelection)}`);
	}

	await cues.nth(2).focus();
	await page.keyboard.press('Enter');
	await waitForActiveCue(third.itemId);

	await page.mouse.dblclick(first.x, first.y);
	await waitForActiveCue(first.itemId);
	await page.locator('button.range-handle.in').waitFor({ state: 'visible' });
	await page.locator('button.range-handle.out').waitFor({ state: 'visible' });

	await page.mouse.move(first.x, first.y);
	await page.keyboard.down('Control');
	for (let attempt = 0; attempt < 10; attempt += 1) await page.mouse.wheel(0, -100);
	await page.keyboard.up('Control');
	let zoomedFirst = await cueData(0);
	if (zoomedFirst.width < 18 || zoomedFirst.pointerTrimDisabled) {
		throw new Error(`放大后字幕拖边仍不可用：${JSON.stringify(zoomedFirst)}`);
	}
	await page.mouse.click(zoomedFirst.x, zoomedFirst.y);
	await waitForActiveCue(zoomedFirst.itemId);

	for (let attempt = 0; attempt < 30 && await zoomOut.isEnabled(); attempt += 1) {
		await zoomOut.click();
	}
	const touchSecond = await cueData(1);
	const cdp = await page.context().newCDPSession(page);
	await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 });
	try {
		await page.evaluate(() => {
			window.__videoLocalizationTouchAudit = [];
			for (const type of ['touchstart', 'touchend', 'pointerdown', 'pointerup', 'click']) {
				document.addEventListener(type, (event) => {
					const pointer = event instanceof PointerEvent ? event : null;
					const touch = event instanceof TouchEvent ? event.changedTouches[0] : null;
					const target = event.target instanceof HTMLElement ? event.target : null;
					window.__videoLocalizationTouchAudit.push({
						type,
						x: pointer?.clientX ?? touch?.clientX ?? null,
						y: pointer?.clientY ?? touch?.clientY ?? null,
						target: target?.closest('[data-subtitle-item-id]')?.getAttribute('data-subtitle-item-id')
							?? target?.className
							?? target?.tagName
							?? ''
					});
				}, { capture: true, once: true });
			}
		});
		await cdp.send('Input.dispatchTouchEvent', {
			type: 'touchStart',
			touchPoints: [{ x: touchSecond.x, y: touchSecond.y, id: 1, radiusX: 1, radiusY: 1 }]
		});
		await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
		try {
			await waitForActiveCue(touchSecond.itemId, 5_000);
		} catch {
			const diagnostics = await page.evaluate(({ x, y }) => ({
				events: window.__videoLocalizationTouchAudit ?? [],
				hit: document.elementFromPoint(x, y)?.closest('[data-subtitle-item-id]')?.getAttribute('data-subtitle-item-id')
					?? document.elementFromPoint(x, y)?.className
					?? '',
				active: [...document.querySelectorAll('button.cue-chip.cue-asr.active:not(.preview-cue)')]
					.map((cue) => cue.getAttribute('data-subtitle-item-id')),
				selected: [...document.querySelectorAll('button.cue-chip.cue-asr.selected:not(.preview-cue)')]
					.map((cue) => cue.getAttribute('data-subtitle-item-id'))
			}), touchSecond);
			throw new Error(`触控低缩放命中失败：${JSON.stringify({ touchSecond, diagnostics })}`);
		}
	} finally {
		await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: false, maxTouchPoints: 1 });
		await cdp.detach();
	}
}

async function verifyRetiredProjectVoiceLibraryIsAbsent(page) {
	await page.setViewportSize({ width: 1440, height: 1000 });
	await page.getByRole('button', { name: '配音', exact: true }).click();
	const retiredLabels = ['项目音色库', '保存当前选区', '保存选区为音色', '保存到项目音色库'];
	for (const label of retiredLabels) {
		if (await page.getByText(label, { exact: true }).count()) {
			throw new Error(`已退役入口仍然可见：${label}`);
		}
	}
	await page.getByRole('button', { name: '任务', exact: true }).click();
}

async function main() {
	if (!projectId) {
		throw new Error(
			'请设置 VIDEO_LOCALIZATION_E2E_PROJECT_ID；该脚本只读取已有项目，不创建或重跑计费任务。',
		);
	}
	const chromium = await loadChromium();
	if (!chromium) throw new Error('Playwright chromium 不可用。');

	const browser = await chromium.launch(isolatedBrowserOptions());
	const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
	const pageErrors = [];
	const operationDetailRequests = [];
	page.on('pageerror', (error) => pageErrors.push(error.message));
	page.on('console', (message) => {
		if (message.type() === 'error') pageErrors.push(message.text());
	});
	page.on('request', (request) => {
		const pathname = new URL(request.url()).pathname;
		if (
			request.method() === 'GET'
			&& /\/video-localization\/operations\/(?!summaries$|feed(?:-v2)?$)[^/]+$/.test(pathname)
		) {
			operationDetailRequests.push(pathname);
		}
	});
	const url = `${baseUrl}/video-localization?project_id=${encodeURIComponent(projectId)}`;
	let initialApiOffline = simulateInitialApiOutage;
	if (simulateInitialApiOutage) {
		await page.route('**/api/**', async (route) => {
			const pathname = new URL(route.request().url()).pathname;
			if (initialApiOffline && pathname.startsWith('/api/projects')) {
				await route.abort('connectionrefused');
				return;
			}
			await route.continue();
		});
	}

	try {
		await page.goto(url, { waitUntil: 'domcontentloaded' });
		if (simulateInitialApiOutage) {
			await page.waitForFunction(() => {
				const projectSwitcher = document.querySelector(
					'button[aria-label="切换历史项目"]'
				);
				return projectSwitcher instanceof HTMLButtonElement
					&& !projectSwitcher.disabled;
			});
			initialApiOffline = false;
			pageErrors.length = 0;
			await page.evaluate(() => {
				window.dispatchEvent(new Event('voice-studio:api-recovered'));
			});
		}
		let task = await openLatestAsrTask(page, {
			operationDetailRequests,
			verifyLazyDetail: true,
		});
		await verifyCompactTaskFacts(task);
			await verifyResultDialogs(page, task, operationDetailRequests);
			await verifySubtitleDisplayConsistency(page);
			await verifyLowZoomSubtitleHitTesting(page);
			await verifyRetiredProjectVoiceLibraryIsAbsent(page);

			await page.reload({ waitUntil: 'domcontentloaded' });
			task = await openLatestAsrTask(page);
		await task.getByRole('button', { name: /查看“生成原始听写”的结果/ }).waitFor();

		await page.setViewportSize({ width: 760, height: 900 });
		await page.reload({ waitUntil: 'domcontentloaded' });
		task = await openLatestAsrTask(page);
		await task.getByText('处理流程', { exact: true }).waitFor();

		const relevantErrors = pageErrors.filter(
			(message) =>
				!message.includes('favicon')
				&& !message.includes('AbortError')
				&& !message.includes('The play() request was interrupted')
				&& !(simulateInitialApiOutage && message.includes('net::ERR_CONNECTION_REFUSED')),
		);
		if (relevantErrors.length) {
			throw new Error(`页面控制台出现错误：${relevantErrors.join(' | ')}`);
		}
		console.log(
				`PASS: 视频本土化浏览器回归通过（项目 ${projectId}，任务 ${taskLabel}`
					+ `${simulateInitialApiOutage ? '，覆盖初始接口离线恢复' : ''}`
					+ '，旧项目音色库入口已退役）。',
			);
	} finally {
		await browser.close();
	}
}

main().catch((error) => {
	console.error(error);
	process.exit(1);
});
