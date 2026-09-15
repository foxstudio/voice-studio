import { render } from 'svelte/server';
import { describe, expect, it, vi } from 'vitest';
import type { HistoryItem } from '$lib/api/types';
import DubbingInspectorPanel from './DubbingInspectorPanel.svelte';
import { nextWaveformActivation } from './SubtitleAudioWaveform.svelte';
import SubtitleTtsHistory, {
	historyMetadataParts,
	historyActionIsDestructive,
	historyParameterLabel,
	historyParameterRows,
	historyParameterValue,
	unusedHistoryCount
} from './SubtitleTtsHistory.svelte';

function historyItem(overrides: Partial<HistoryItem> = {}): HistoryItem {
	return {
		result_id: 'result-1',
		task_id: 'task-1',
		generation_id: 'generation-1',
		engine_id: 'omnivoice',
		voice_id: 'voice-1',
		voice_name: '测试音色',
		project_id: 'project-1',
		segment_id: 'segment-1',
		localized_subtitle_id: 'segment-1',
		cue_id: null,
		bind_to_video_localization: true,
		longform_task_id: null,
		longform_segment_index: null,
		longform_segment_count: null,
		longform_export_id: null,
		input_text: '这是一条测试台词。',
		output_audio_id: null,
		output_path: null,
		duration_ms: 2450,
		generation_time_ms: 800,
		verification: null,
		verification_error: null,
		parameter_snapshot: { speed: 1.15 },
		favorite: false,
		created_at: 'saved-time',
		...overrides
	};
}

function renderHistory(props: {
	items?: HistoryItem[];
	selectedResultId?: string;
	usedResultIds?: string[];
	busy?: boolean;
	canGenerate?: boolean;
	scope?: 'current' | 'all';
	sourceSelectionAdvisory?: string | null;
	onCleanupUnused?: (scope: 'current' | 'all') => void | Promise<void>;
} = {}) {
	return render(SubtitleTtsHistory, {
		props: {
			items: props.items ?? [historyItem()],
			selectedSegmentId: 'segment-1',
			canGenerate: props.canGenerate ?? true,
			scope: props.scope,
			sourceSelectionAdvisory: props.sourceSelectionAdvisory,
			busy: props.busy ?? false,
			selectedResultId: props.selectedResultId,
			usedResultIds: props.usedResultIds,
			onOpenGenerate: vi.fn(),
			onReuse: vi.fn(),
			onCleanupUnused: props.onCleanupUnused
		}
	});
}

describe('subtitle TTS history presentation', () => {
	it('defers waveform requests until a history row approaches the visible area', () => {
		expect(nextWaveformActivation(false, [{ isIntersecting: false }])).toBe(false);
		expect(nextWaveformActivation(false, [{ isIntersecting: false }, { isIntersecting: true }])).toBe(true);
		expect(nextWaveformActivation(true, [{ isIntersecting: false }])).toBe(true);
	});

	it('does not mount one audio element per history row before the user starts playback', () => {
		const { body } = renderHistory({
			scope: 'all',
			items: [
				historyItem({ output_path: '/tmp/result-1.wav' }),
				historyItem({
					result_id: 'result-2',
					task_id: 'task-2',
					generation_id: 'generation-2',
					output_path: '/tmp/result-2.wav'
				})
			]
		});

		expect(body).not.toContain('<audio');
	});

	it('does not let a generation action globally lock cleanup controls', () => {
		expect(historyActionIsDestructive('reuse:segment-1:result-1')).toBe(false);
		expect(historyActionIsDestructive('cleanup-unused')).toBe(true);
		expect(historyActionIsDestructive('delete:result-1')).toBe(true);
	});

	it('builds one compact metadata line with time, duration, engine, and speed', () => {
		const parts = historyMetadataParts({
			created_at: 'saved-time',
			duration_ms: 2450,
			engine_id: 'omnivoice',
			parameter_snapshot: { speed: 1.15 }
		});

		expect(parts).toEqual(['saved-time', '2.5 秒', 'omnivoice', '语速 1.15x']);
	});

	it('uses bilingual detail keys and preserves original values', () => {
		const rows = historyParameterRows({
			speed: 0.95,
			reference_audio_path: '/tmp/参考音频.wav',
			engine_parameters: { denoise: false, strength: 0.4 },
			custom_option: 'raw-value'
		});

		expect(rows).toEqual([
			{ key: 'speed', label: '语速 / speed', value: '0.95' },
			{ key: 'reference_audio_path', label: '参考音频 / reference_audio_path', value: '/tmp/参考音频.wav' },
			{
				key: 'engine_parameters',
				label: '引擎参数 / engine_parameters',
				value: '{\n  "denoise": false,\n  "strength": 0.4\n}'
			},
			{ key: 'custom_option', label: '参数 / custom_option', value: 'raw-value' }
		]);
		expect(historyParameterLabel('emotion_mode')).toBe('情感模式 / emotion_mode');
		expect(historyParameterValue(null)).toBe('null');
	});

	it('renders optional selection and timeline usage as explicit status badges', () => {
		const defaultRender = renderHistory();
		expect(defaultRender.body).not.toContain('selected-badge');
		expect(defaultRender.body).not.toContain('used-badge');
		expect(defaultRender.body).toContain('>segment-1 · 音频 1</span>');

		const selectedRender = renderHistory({
			selectedResultId: 'result-1',
			usedResultIds: ['generation-1']
		});
		expect(selectedRender.body).toMatch(/class="status-badge selected-badge [^"]*">当前选中<\/span>/);
		expect(selectedRender.body).toMatch(/class="status-badge used-badge [^"]*">时间线在用<\/span>/);
		expect(selectedRender.body).toContain('aria-label="日期时间、持续时长、模型引擎和语速"');
		expect(selectedRender.body).not.toContain('生成记录');
	});

	it('does not add content-review badges to saved generation records', () => {
		const unreviewed = renderHistory({
			items: [
				historyItem({
					verification: {
						status: 'passed',
						coverage: 0.95,
						similarity: 0.95,
						expected_text: '这是一条测试台词。',
						transcript_text: '这是一条测试台词。',
						normalized_expected: '这是一条测试台词',
						normalized_transcript: '这是一条测试台词',
						missing_segments: [],
						segment_results: [],
						warnings: [],
						suggestions: [],
						result_id: 'result-1',
						transcription_id: 'transcription-1',
						asr_engine_id: 'test-asr'
					}
				})
			]
		});
		expect(unreviewed.body).not.toContain('覆盖 95%');
		expect(unreviewed.body).not.toContain('CQC');
	});

	it('renders the heading and its actions as separate rows with accessible scope tabs', () => {
		const { body } = renderHistory();

		expect(body).toMatch(
			/<div class="history-head [^"]*">[\s\S]*?<div class="history-heading [^"]*">[\s\S]*?<\/div>[\s\S]*?<div class="history-head-actions [^"]*">/
		);
		expect(body).toContain('role="tablist"');
		expect(body).toContain('aria-label="配音记录范围"');
		expect(body).toContain('role="tab" aria-selected="true"');
		expect(body).toContain('当前片段 <span');
		expect(body).toContain('全部片段 <span');
	});

	it('renders all project records when the parent opens the all-clips scope', () => {
		const { body } = renderHistory({
			scope: 'all',
			items: [
				historyItem(),
				historyItem({
					result_id: 'result-2',
					task_id: 'task-2',
					generation_id: 'generation-2',
					segment_id: 'segment-2',
					localized_subtitle_id: 'segment-2'
				})
			]
		});

		expect(body).toMatch(/全部片段 <span[^>]*>2<\/span><\/button>/);
		expect(body).toContain('data-result-id="result-2"');
		expect(body).toMatch(/aria-selected="true"[^>]*>全部片段/);
	});

	it('shows the segment id in the record header without repeating the script text', () => {
		const { body } = render(SubtitleTtsHistory, {
			props: {
				items: [historyItem({ segment_id: 'cue_0197', localized_subtitle_id: 'cue_0197' })],
				selectedSegmentId: 'cue_0197',
				segmentLabels: { cue_0197: '#cue_0197 · 第二是节奏 每个动作段都拖得太久' },
				canGenerate: true,
				scope: 'all',
				onOpenGenerate: vi.fn(),
				onReuse: vi.fn()
			}
		});

		expect(body).toContain('>cue_0197 · 音频 1</span>');
		expect(body).not.toContain('>#cue_0197 · 第二是节奏 每个动作段都拖得太久</span>');
		expect(body).toContain('这是一条测试台词。');
	});

	it('shows the source cue names and unique audio identity for grouped history', () => {
		const { body } = renderHistory({
			scope: 'all',
			items: [historyItem({
				cue_id: 'cue_0059',
				parameter_snapshot: {
					video_localization_source_cue_ids: ['cue_0059', 'cue_0060']
				}
			})]
		});

		expect(body).toContain('>cue_0059 + cue_0060 · 音频 1</span>');
	});

	it('counts unused records only within the visible current-segment scope', () => {
		const { body } = renderHistory({
			items: [
				historyItem({ result_id: 'used', generation_id: 'used-generation' }),
				historyItem({
					result_id: 'current-unused',
					task_id: 'current-unused-task',
					generation_id: 'current-unused-generation'
				}),
				historyItem({
					result_id: 'other-unused',
					task_id: 'other-unused-task',
					generation_id: 'other-unused-generation',
					segment_id: 'segment-2',
					localized_subtitle_id: 'segment-2'
				})
			],
			usedResultIds: ['used-generation'],
			onCleanupUnused: vi.fn()
		});

		expect(body).toContain('aria-label="清理当前片段未使用的配音素材"');
		expect(body).toContain('清理未用 1');
	});

	it('counts current and all cleanup scopes independently', () => {
		const items = [
			historyItem({ result_id: 'used', generation_id: 'used-generation' }),
			historyItem({ result_id: 'current-unused', generation_id: 'current-unused-generation' }),
			historyItem({
				result_id: 'other-unused',
				generation_id: 'other-unused-generation',
				segment_id: 'segment-2',
				localized_subtitle_id: 'segment-2'
			})
		];

		expect(unusedHistoryCount(items, 'current', 'segment-1', '', ['used-generation'])).toBe(1);
		expect(unusedHistoryCount(items, 'all', 'segment-1', '', ['used-generation'])).toBe(2);
	});

	it('includes a grouped generation under every localized subtitle it contains', () => {
		const grouped = historyItem({
			result_id: 'group-result',
			segment_id: 'group-segment-1-segment-3',
			localized_subtitle_id: 'segment-1',
			parameter_snapshot: {
				speed: 1.15,
				video_localization_target_subtitle_ids: ['segment-1', 'segment-2', 'segment-3']
			}
		});

		expect(unusedHistoryCount([grouped], 'current', 'segment-2')).toBe(1);
	});

	it('keeps parameter reuse available while another subtitle is being submitted', () => {
		const { body } = renderHistory({ busy: true });

		expect(body).toMatch(/class="reuse-button [^"]*"[^>]*aria-label="沿用这次参数生成"/);
		expect(body).not.toMatch(/class="reuse-button [^"]*"[^>]*disabled/);
		expect(body).toMatch(/class="open-generate [^"]*"[^>]*disabled/);
	});

	it('keeps both generation routes available while explaining a multi-speaker selection', () => {
		const reason = 'ASR 参考范围包含多个说话人，可进入语音合成页后重新调整参考选区';
		const { body } = renderHistory({
			canGenerate: true,
			sourceSelectionAdvisory: reason
		});

		expect(body).not.toMatch(/class="open-generate [^"]*"[^>]*disabled/);
		expect(body).not.toMatch(/class="reuse-button [^"]*"[^>]*disabled/);
		expect(body).toContain(`data-tooltip="${reason}；点击后会按正式生成流程校验。"`);
	});

	it('shows the adjustable selection note beside the selected dubbing target', () => {
		const reason = 'ASR 参考范围包含多个说话人，可进入语音合成页后重新调整参考选区';
		const { body } = render(DubbingInspectorPanel, {
			props: {
				selectedSegmentId: 'group-1',
				script: '第一句。第二句。',
				targetLabel: '2 条连续字幕',
				canGenerate: true,
				sourceSelectionAdvisory: reason,
				selectionCount: 2,
				selectionContiguous: true,
				onOpenGenerate: vi.fn(),
				onReuse: vi.fn()
			}
		});

		expect(body).toContain(`role="status">${reason}</p>`);
		expect(body).not.toMatch(/class="open-generate [^"]*"[^>]*disabled/);
	});
});
