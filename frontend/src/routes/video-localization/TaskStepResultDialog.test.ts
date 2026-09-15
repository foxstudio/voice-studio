import { render } from 'svelte/server';
import { describe, expect, it, vi } from 'vitest';
import type { ActivityTaskStepResult } from './activity-notice';
import TaskStepResultDialog, {
	debugDetailFacts,
	detailFacts,
	detailNotes,
	detailSections,
	longDetailPreview,
	overviewContentLayout,
	resultFactUsesFullRow,
	resultItemContentLayout,
	resultSectionOpenByDefault,
	reviewTargetDetail,
	reviewTargetExcerpt,
	reviewTargetRange,
	stepPurpose
} from './TaskStepResultDialog.svelte';

const warningResult: ActivityTaskStepResult = {
	status: 'warning' as const,
	summary: '已修改 3 处，另有 1 处需要人工确认。',
	metrics: [],
	sections: [{
		title: '检查结果',
		items: [
			{ title: 'Seedance 2', text: '请确认产品名称是否正确。', tone: 'warning' as const, facts: [], links: [] },
			{ title: '已完成修改', text: '把 C-Ends 改成了 Seedance。', tone: 'positive' as const, facts: [], links: [] }
		]
	}],
	notes: ['请确认产品名称是否正确。', '请确认产品名称是否正确。', '已修改 3 处，另有 1 处需要人工确认。']
};

function renderDialog(
	result: ActivityTaskStepResult,
	overrides: Partial<{
		taskLabel: string;
		stepId: string;
		stepLabel: string;
		stepPositionLabel: string;
		durationLabel: string;
	}> = {}
) {
	return render(TaskStepResultDialog, {
		props: {
			taskLabel: '生成 ASR 字幕',
			stepId: 'review',
			stepLabel: '复核字幕',
			stepPositionLabel: '第 2 步',
			durationLabel: '2 秒',
			result,
			onClose: vi.fn(),
			...overrides
		}
	});
}

describe('task step result dialog content', () => {
	it('labels the non-blocking quality gate as completed with suggestions', () => {
		const { body } = renderDialog(warningResult, {
			stepId: 'transcript_quality_gate',
			stepLabel: '进入校时前检查'
		});

		expect(body).toContain('已完成，有建议');
		expect(body).toContain('aria-label="建议复听"');
		expect(body).toMatch(/<strong class="[^"]*">建议复听<\/strong>/);
		expect(body).toContain('流程已经完成，这些内容不阻塞交付');
		expect(body).not.toContain('aria-label="建议确认"');
	});

	it('shows a plain pending state before a step starts', () => {
		const { body } = renderDialog({
			status: 'todo',
			summary: '等待前序步骤。',
			metrics: [],
			sections: [],
			notes: []
		});

		expect(body).toContain('尚未开始');
		expect(body).toContain('等待前序步骤。');
		expect(body).not.toContain('处理中');
	});

	it('labels a non-item operation failure as handling guidance', () => {
		const { body } = renderDialog({
			status: 'failed',
			purpose: '定位导致任务中止的原因，并提供可执行的处理方向。',
			summary: 'Import a source video before extracting audio',
			metrics: [],
			sections: [],
			notes: ['先导入源视频，再重新提交抽取原音轨任务。']
		}, {
			stepId: '',
			stepLabel: '错误详情'
		});

		expect(body).toContain('处理建议');
		expect(body).toContain('按下方建议修正输入或服务状态，然后重新提交任务。');
		expect(body).not.toContain('需要复核什么');
		expect(body).not.toContain('对照原音和前后文');
	});

	it('labels structured failure targets as the cause instead of a review request', () => {
		const { body } = renderDialog({
			status: 'failed',
			summary: '声音已经生成，但台词覆盖校对未通过，所以没有放入配音轨。',
			metrics: [],
			sections: [],
			reviewTargets: [{
				title: '对齐字幕并放入轨道',
				detail: '生成音频与中文台词不一致（覆盖率 81%），未放入配音轨。'
			}],
			notes: ['试听生成结果后，再决定是否调整台词。']
		}, {
			stepId: '',
			stepLabel: '错误详情'
		});

		expect(body).toContain('aria-label="失败原因"');
		expect(body).toContain('这里说明任务为什么停止');
		expect(body).not.toContain('aria-label="建议确认"');
		expect(body).not.toContain('对照原音和前后文');
	});

	it('labels a saved failure section as the cause even when the old result has no explicit targets', () => {
		const { body } = renderDialog({
			status: 'failed',
			summary: '声音已经生成，但没有放入配音轨。',
			metrics: [],
			sections: [{
				title: '失败记录',
				items: [{
					title: '对齐字幕并放入轨道',
					text: '生成音频与中文台词不一致。',
					tone: 'warning',
					facts: [],
					links: []
				}]
			}],
			notes: []
		}, {
			stepId: '',
			stepLabel: '错误详情'
		});

		expect(body).toContain('aria-label="失败原因"');
		expect(body).not.toContain('aria-label="建议确认"');
	});

	it('shows a distinct running state while a step is executing', () => {
		const { body } = renderDialog({
			status: 'running',
			summary: '正在查询全文理解留下的问题。',
			metrics: [],
			sections: [],
			notes: []
		});

		expect(body).toContain('处理中');
		expect(body).toContain('正在查询全文理解留下的问题');
		expect(body).not.toContain('结果有效');
		expect(body).not.toContain('无需执行');
	});

	it('distinguishes not-needed work from pending, running, and completed work', () => {
		const { body } = renderDialog({
			status: 'not_needed',
			summary: '全文没有需要联网确认的问题，本步骤无需执行。',
			metrics: [
				{ label: '查询问题', value: '0' },
				{ label: '找到资料', value: '0' }
			],
			sections: [],
			notes: ['未调用搜索服务。']
		}, {
			stepLabel: '查询必要资料'
		});

		expect(body).toContain('无需执行');
		expect(body).toContain('全文没有需要联网确认的问题');
		expect(body).toContain('未调用搜索服务');
		expect(body).toContain('aria-label="执行说明"');
		expect(body).toContain('跳过原因');
		expect(body).not.toContain('质量提醒');
		expect(body).not.toContain('结果有效');
		expect(body).not.toContain('尚未开始');
		expect(body).not.toContain('处理中');
	});

	it('always explains a step in plain language', () => {
		expect(stepPurpose('生成原始听写稿')).toBe('把音轨里的讲话转成文字。');
		expect(stepPurpose('汇合听写与说话人')).toBe('把听写片段和匿名说话人时间段对齐，整理成谁在什么时候说了什么。');
		expect(stepPurpose('第 1 轮全文复核')).toBe('结合原音和前后文，检查字幕是否准确、通顺。');
		expect(stepPurpose('画面取证')).toBe('只查看必要画面，读取字幕条、图表等直接可见信息。');
		expect(stepPurpose('自定义步骤', '读取上下文并检查结果。')).toBe('读取上下文并检查结果。');
	});

	it('shows review information once and keeps unrelated details', () => {
		const reviewTargets = [{ title: 'Seedance 2', detail: '请确认产品名称是否正确。' }];
		expect(detailSections(warningResult, reviewTargets)).toEqual([{
			title: '检查结果',
			items: [warningResult.sections[0].items[1]]
		}]);
		expect(detailNotes(warningResult, reviewTargets)).toEqual([]);
	});

	it('keeps warning steps in a complete workflow ledger while also showing review targets', () => {
		const workflowResult = {
			...warningResult,
			detailMode: 'workflow_summary' as const
		};
		const reviewTargets = [{ title: 'Seedance 2', detail: '请确认产品名称是否正确。' }];

		expect(detailSections(workflowResult, reviewTargets)).toEqual(workflowResult.sections);
	});

	it('keeps a separate decision when two issues share one segment title', () => {
		const result = {
			...warningResult,
			sections: [{
				title: '逐条判断',
				items: [
					{
						title: 'asr_0004 · 15秒28帧 – 22秒27帧',
						text: '第一处问题需要人工确认。',
						tone: 'warning' as const,
						facts: [],
						links: []
					},
					{
						title: 'asr_0004 · 15秒28帧 – 22秒27帧',
						text: '第二处建议把握度不足，保留原文。',
						tone: 'neutral' as const,
						facts: [],
						links: []
					}
				]
			}]
		};
		const reviewTargets = [{
			title: 'asr_0004 · 15秒28帧 – 22秒27帧',
			detail: '第一处问题需要人工确认。'
		}];

		expect(detailSections(result, reviewTargets)[0].items).toEqual([
			result.sections[0].items[1]
		]);
	});

	it('removes facts that repeat the visible item text', () => {
		const item = {
			text: '已改成 Seedance 2。',
			facts: [
				{ label: '结果', value: '已改成 Seedance 2' },
				{ label: '位置', value: '00:12.000' },
				{ label: '位置', value: '00:12.000' }
			],
			links: []
		};
		expect(detailFacts(item)).toEqual([{ label: '位置', value: '00:12.000' }]);
	});

	it('keeps internal ids and similarity out of the reader view but available for debugging', () => {
		const item = {
			text: '这一处会由下一步自动复核。',
			facts: [
				{ label: '英文位置', value: 'cue_0085–cue_0086' },
				{ label: '相似度', value: '0.294' },
				{ label: '后续处理', value: '交给下一步复核' }
			],
			links: []
		};

		expect(detailFacts(item)).toEqual([
			{ label: '后续处理', value: '交给下一步复核' }
		]);
		expect(debugDetailFacts(item, [])).toEqual(item.facts);
	});

	it('does not repeat a review sentence as both title and detail', () => {
		expect(reviewTargetDetail({
			title: '确认 full wide 是否指全广角',
			detail: '确认 full wide 是否指全广角。'
		})).toBe('');
		expect(reviewTargetDetail({
			title: '语义段 0037',
			detail: '核对系统更正后的内容是否符合原音和上下文。'
		}, '本地映射语义时间')).toBe('这一处已交给下一步自动复核，不会阻塞后续流程。');
	});

	it('shows the current subtitle and exposes an exact replay range', () => {
		const result: ActivityTaskStepResult = {
			status: 'warning',
			summary: '建议复听一处。',
			metrics: [],
			sections: [{
				title: '建议复听',
				items: [{
					title: '确认专有名称',
					text: '请结合原音确认。',
					facts: [{ label: '当前听写原文', value: 'C dance 2.0' }],
					links: []
				}]
			}],
			reviewTargets: [{
				title: '确认专有名称',
				detail: '请结合原音确认。',
				startMs: 1200,
				endMs: 2400
			}],
			notes: []
		};

		expect(reviewTargetExcerpt(result, result.reviewTargets![0])).toBe('C dance 2.0');
		expect(reviewTargetRange(result.reviewTargets![0])).toEqual({ startMs: 1200, endMs: 2400 });
		const { body } = render(TaskStepResultDialog, {
			props: {
				taskLabel: '生成 ASR 字幕',
				stepId: 'transcript_quality_gate',
				stepLabel: '进入校时前检查',
				result,
				onClose: vi.fn(),
				onPlayRange: vi.fn(),
				onJumpToTime: vi.fn()
			}
		});
		expect(body).toContain('当前字幕');
		expect(body).toContain('C dance 2.0');
		expect(body).toContain('播放这一段');
		expect(body).toContain('跳到时间轴');
	});

	it('does not guess a subtitle when multiple saved items match the same review target', () => {
		const result: ActivityTaskStepResult = {
			status: 'warning',
			summary: '建议复听两处。',
			metrics: [],
			sections: [{
				title: '建议复听',
				items: [
					{
						title: '定点问题仍未关闭',
						text: '跨片段建议已回滚。',
						facts: [{ label: '当前字幕', value: '第一处字幕' }],
						links: []
					},
					{
						title: '定点问题仍未关闭',
						text: '跨片段建议已回滚。',
						facts: [{ label: '当前字幕', value: '第二处字幕' }],
						links: []
					}
				]
			}],
			notes: []
		};
		const target = {
			title: '定点问题仍未关闭',
			detail: '跨片段建议已回滚。'
		};

		expect(reviewTargetExcerpt(result, target)).toBe('');
	});

	it('folds long result details and keeps short details open', () => {
		const shortSection = {
			title: '准备内容',
			items: [{ title: '内容背景', text: '一段简短背景。', facts: [], links: [] }]
		};
		const longSection = {
			title: '全文翻译结果',
			items: [{ title: '第一版中文稿摘录', text: '中文'.repeat(500), facts: [], links: [] }]
		};
		expect(resultSectionOpenByDefault(shortSection)).toBe(true);
		expect(resultSectionOpenByDefault(longSection)).toBe(false);
		expect(longDetailPreview('一二三四五六', 4)).toBe('一二三四…');
	});

	it('uses rows for long overview copy and columns for short overview copy', () => {
		expect(overviewContentLayout('已完成处理。', '检查处理结果。')).toBe('columns');
		expect(overviewContentLayout(
			'已采用项目默认配置，交付内容为中文字幕与配音台词。',
			'把本次要交付什么、中文要怎么表达、时间如何与画面语义对应，以及哪些原文信息只能作为参考固定下来，供后续所有步骤共同使用。'
		)).toBe('rows');
		expect(overviewContentLayout('第一段。\n第二段。', '检查结果。')).toBe('rows');
	});

	it('keeps short ledger entries in columns and gives long prose full-width rows', () => {
		const shortItem = {
			title: '第 3 条',
			text: '检查该字幕。',
			facts: [{ label: '位置', value: '00:12.000' }],
			links: []
		};
		const longItem = {
			title: '时间对应方式',
			text: '中文可以重新分段，不要求与英文字幕条一一对齐；每段中文要落在原视频表达相同意思的时间范围内，并在下一段意思开始前结束。',
			facts: [],
			links: []
		};
		expect(resultItemContentLayout(shortItem)).toBe('columns');
		expect(resultItemContentLayout(longItem)).toBe('rows');
		expect(resultFactUsesFullRow({ label: '说明', value: '这是需要独占整行显示的较长字段内容，因为并排后会形成很窄、很难阅读的正文列。' })).toBe(true);
		expect(resultFactUsesFullRow({ label: '位置', value: '00:12.000' })).toBe(false);
	});

	it('honors an explicit open state for layered ASR samples', () => {
		const section = {
			title: '展开详情',
			items: [{ title: 'asr_0004', text: 'Sample 4.', facts: [], links: [] }]
		};
		expect(resultSectionOpenByDefault({ ...section, openByDefault: true })).toBe(true);
		expect(resultSectionOpenByDefault({ ...section, openByDefault: false })).toBe(false);
	});

	it('keeps sampling context in quality notes instead of a separate coverage banner', () => {
		const { body } = renderDialog({
			status: 'success',
			summary: '已完成抽样复核。',
			metrics: [],
			sections: [],
			notes: ['仅抽查了高风险片段。'],
			coverage: {
				mode: 'focused',
				shownCount: 3,
				totalCount: 20,
				unit: '片段',
				reason: '优先检查低置信度内容'
			}
		});

		expect(body).toContain('仅抽查了高风险片段。');
		expect(body).not.toContain('详情展示范围');
		expect(body).not.toContain('result-coverage');
	});

	it('keeps debugging details folded and explains them in plain language', () => {
		const { body } = renderDialog({
			status: 'success',
			summary: '处理完成。',
			metrics: [],
			sections: [],
			notes: [],
			debug: {
				metrics: [
					{ label: '调用模型', value: 'gpt-5.4-mini' },
					{ label: '调用方式', value: '本地 Codex CLI（local-codex-cli）' }
				],
				sections: [],
				notes: ['调试记录']
			}
		});

		expect(body).toMatch(/<details class="result-debug [^"]*">/);
		expect(body).not.toMatch(/<details class="result-debug [^"]*" open>/);
		expect(body).toMatch(/<strong class="[^"]*">调试信息<\/strong>/);
		expect(body).toContain('用于核对输入来源、模型配置、调用记录和结果依据；实际保存了什么就显示什么。');
		expect(body).toContain('gpt-5.4-mini');
		expect(body).toContain('本地 Codex CLI（local-codex-cli）');
		expect(body).not.toContain('<strong>高级信息</strong>');
	});

	it('does not repeat aggregate model identity inside each call detail', () => {
		const item = {
			title: '本土化台词终审',
			text: '正常结束',
			facts: [
				{ label: '实际模型', value: 'gpt-5.6-sol' },
				{ label: '调用方式', value: '本地 Codex CLI' },
				{ label: '模型配置', value: 'profile-1' },
				{ label: '输入规模', value: '2,312 字符' }
			],
			links: []
		};

		expect(debugDetailFacts(item, [
			{ label: '调用模型', value: 'gpt-5.6-sol' },
			{ label: '调用方式', value: '本地 Codex CLI' },
			{ label: '模型配置', value: 'profile-1' }
		])).toEqual([
			{ label: '输入规模', value: '2,312 字符' }
		]);
	});

	it('uses the compact transcript ledger layout for simple text-and-time result items', () => {
		const { body } = renderDialog({
			status: 'success',
			summary: '已整理三条字幕。',
			metrics: [{ label: '字幕', value: '3' }],
			sections: [{
				title: '字幕结果',
				items: [
					{ title: '第一条', text: '没有额外事实。', facts: [], links: [] },
					{
						title: '第二条',
						text: '包含一个时间事实。',
						facts: [{ label: '位置', value: '00:12.000' }],
						links: []
					},
					{
						title: '第三条',
						text: '包含两个事实。',
						facts: [
							{ label: '位置', value: '00:16.000' },
							{ label: '置信度', value: '92%' }
						],
						links: []
					}
				]
			}],
			notes: ['请核对专名。']
		});

		expect(body).toContain('aria-label="结果概览"');
		expect(body).toContain('aria-label="结果详情"');
		expect(body).toMatch(/<strong class="[^"]*">结果明细<\/strong>/);
		expect(body).toMatch(/class="result-item [^"]*item-compact item-simple/);
		expect(body).toMatch(/class="result-item [^"]*item-compact item-paired/);
		expect(body).toMatch(/class="result-item [^"]*item-compact item-detailed/);
		expect(body.match(/class="item-facts /g)).toHaveLength(2);
		expect(body).toContain('<div class="result-note-list');
	});

	it('renders long overview and detail prose as rows while preserving compact short fields', () => {
		const { body } = renderDialog({
			status: 'success',
			summary: '已采用项目默认配置，交付内容为中文字幕与配音台词。',
			purpose: '把本次要交付什么、中文要怎么表达、时间如何与画面语义对应，以及哪些原文信息只能作为参考固定下来，供后续所有步骤共同使用。',
			metrics: [],
			sections: [{
				title: '本土化要求',
				items: [
					{
						title: '时间对应方式',
						text: '中文可以重新分段，不要求与英文字幕条一一对齐；每段中文要落在原视频表达相同意思的时间范围内，并在下一段意思开始前结束。',
						facts: [],
						links: []
					},
					{
						title: '交付内容',
						text: '中文字幕与配音台词',
						facts: [{ label: '音频', value: '不生成' }],
						links: []
					}
				]
			}],
			notes: []
		});

		expect(body).toMatch(/class="overview-copy [^"]*layout-rows/);
		expect(body).toMatch(/class="result-item [^"]*layout-rows/);
		expect(body).toMatch(/class="result-item [^"]*item-compact item-paired/);
	});

	it('renders advanced facts once instead of duplicating the same rows', () => {
		const { body } = renderDialog({
			status: 'success',
			summary: '处理完成。',
			metrics: [],
			sections: [],
			notes: [],
			debug: {
				metrics: [],
				sections: [{
					title: '调用记录',
					items: [{
						title: '模型返回',
						facts: [{ label: '请求 ID', value: 'request-unique-42' }],
						links: []
					}]
				}],
				notes: []
			}
		});

		expect(body.match(/request-unique-42/g)).toHaveLength(1);
		expect(body.match(/请求 ID/g)).toHaveLength(1);
	});

	it('renders a long markdown result as a bounded read-only document', () => {
		const result = {
			status: 'success',
			summary: '已生成全文本土化初稿。',
			metrics: [],
			sections: [],
			notes: [],
			document: {
				title: '全文本土化初稿',
				format: 'markdown',
				content: '# 测试标题\n\n## 第一章\n\n这是第一段。\n\n这是第二段。'
			}
		} as ActivityTaskStepResult & {
			document: {
				title: string;
				format: 'markdown';
				content: string;
			};
		};

		const { body } = renderDialog(result, {
			stepLabel: '生成全文本土化初稿'
		});

		expect(body).toContain('aria-label="全文结果"');
		expect(body).toContain('全文本土化初稿');
		expect(body).toContain('只读');
		expect(body).toContain('测试标题');
		expect(body).toContain('第一章');
		expect(body).toContain('这是第一段。');
		expect(body).toContain('readonly-document');
		expect(body).not.toContain('textarea');
		expect(body).not.toContain('contenteditable');
	});

	it('shows bounded visual evidence frames inside the existing result dialog', () => {
		const frameUrl = '/api/projects/project-1/video-localization/operations/operation-1/development-visual-evidence-frames/frame_0123456789ab';
		const { body } = renderDialog({
			status: 'success',
			summary: '画面取证完成。',
			metrics: [],
			sections: [{
				title: '关键画面',
				items: [{
					title: '片头字幕',
					facts: [],
					links: [
						{ title: '第 1 帧', url: frameUrl },
						{ title: '本地文件', url: 'file:///tmp/private.png' }
					]
				}]
			}],
			notes: []
		});

		expect(body).toContain(`href="${frameUrl}"`);
		expect(body).toContain(`src="${frameUrl}?preview=true"`);
		expect(body).toContain('loading="lazy"');
		expect(body).toContain('decoding="async"');
		expect(body).toContain('item-frame-link');
		expect(body).not.toContain('file:///tmp/private.png');
	});

});
