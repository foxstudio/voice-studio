import { describe, expect, it } from 'vitest';
import { resolveAsrOperationPreview } from './asr-operation-preview';

describe('ASR operation preview', () => {
	it('normalizes active intermediate cues', () => {
		const preview = resolveAsrOperationPreview([{
			operation_id: 'asr-1', project_id: 'project', kind: 'english_asr', status: 'running',
			label: '听写字幕', progress: 0.38, error_code: null, error_message: null,
			cancel_requested: false, parameters: {}, created_at: '', started_at: '', completed_at: null,
			result_summary: {
				stage: '正在校对识别文本', preview_phase: 'text_review',
				preview_cues: [{ cue_id: 'preview_1', start_ms: 100, end_ms: 900, text: 'Hello world' }]
			}
		}]);
		expect(preview).toMatchObject({ phase: 'text_review', phaseLabel: '文本校对', progress: 0.38 });
		expect(preview?.cues).toEqual([{ cue_id: 'preview_1', start_ms: 100, end_ms: 900, text: 'Hello world' }]);
	});

	it('hides previews after cancellation or completion', () => {
		const operation = {
			operation_id: 'asr-1', project_id: 'project', kind: 'english_asr' as const, status: 'running' as const,
			label: '听写字幕', progress: 0.38, error_code: null, error_message: null,
			cancel_requested: true, parameters: {}, created_at: '', started_at: '', completed_at: null,
			result_summary: { preview_phase: 'asr_draft', preview_cues: [] }
		};
		expect(resolveAsrOperationPreview([operation])).toBeNull();
		expect(resolveAsrOperationPreview([{ ...operation, cancel_requested: false, status: 'success' }])).toBeNull();
	});

	it('uses the newest active ASR run instead of merging run histories', () => {
		const operation = {
			project_id: 'project', kind: 'english_asr' as const, status: 'running' as const,
			label: '听写字幕', progress: 0.2, error_code: null, error_message: null,
			cancel_requested: false, parameters: {}, completed_at: null
		};
		const preview = resolveAsrOperationPreview([
			{
				...operation, operation_id: 'older', created_at: '2026-07-15T08:00:00Z', started_at: '2026-07-15T08:00:01Z',
				result_summary: { preview_phase: 'asr_draft', stage: '旧运行', preview_cues: [] }
			},
			{
				...operation, operation_id: 'newer', created_at: '2026-07-15T09:00:00Z', started_at: '2026-07-15T09:00:01Z',
				result_summary: { preview_phase: 'text_review', stage: '新运行', preview_cues: [] }
			}
		]);
		expect(preview).toMatchObject({ operationId: 'newer', stage: '新运行', phase: 'text_review' });
	});
});
