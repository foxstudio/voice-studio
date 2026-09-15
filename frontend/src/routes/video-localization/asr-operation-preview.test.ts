import { describe, expect, it } from 'vitest';
import {
	buildDevelopmentAsrPreview,
	buildDevelopmentInitialAnalysisPreview,
	buildDevelopmentEntityNormalizationPreview,
	buildDevelopmentReviewDecisionsPreview,
	buildDevelopmentWholeRecheckPreview,
	canDisplayDevelopmentAsrPreview,
	developmentAsrPreviewMatchesDraft,
	developmentInitialAnalysisResultMatchesDraft,
	developmentEntityNormalizationResultMatchesDraft,
	developmentReviewDecisionsResultMatchesDraft,
	developmentWholeRecheckResultMatchesDraft,
	developmentAsrResultMatchesDraft,
	resolveAsrOperationPreview,
	resolveAsrPreviewTextAtTime,
	resolveDevelopmentAsrPreviewOperation
} from './asr-operation-preview';

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
		expect(preview).toMatchObject({ phase: 'text_review', phaseLabel: '文本校对', progress: 0.38, isActive: true });
		expect(preview?.cues).toEqual([{ cue_id: 'preview_1', start_ms: 100, end_ms: 900, text: 'Hello world' }]);
	});

	it('hides active previews after cancellation or completion', () => {
		const operation = {
			operation_id: 'asr-1', project_id: 'project', kind: 'english_asr' as const, status: 'running' as const,
			label: '听写字幕', progress: 0.38, error_code: null, error_message: null,
			cancel_requested: true, parameters: {}, created_at: '', started_at: '', completed_at: null,
			result_summary: { preview_phase: 'asr_draft', preview_cues: [] }
		};
		expect(resolveAsrOperationPreview([operation])).toBeNull();
		expect(resolveAsrOperationPreview([{ ...operation, cancel_requested: false, status: 'success' }])).toBeNull();
	});

	it('selects and builds a completed development transcript for a one-time result request', () => {
		const operation = {
			operation_id: 'partial-asr', project_id: 'project', kind: 'english_asr', status: 'success',
			label: '原始听写（开发单步）', progress: 1, error_code: null, error_message: null,
			cancel_requested: false,
			parameters: { execution_mode: 'stop_after', stop_after_step: 'asr' },
			created_at: '2026-07-24T10:00:00Z', started_at: '2026-07-24T10:00:01Z',
			completed_at: '2026-07-24T10:00:30Z',
			result_summary: { artifact_available: true }
		} as const;
		const selected = resolveDevelopmentAsrPreviewOperation([operation]);
		const preview = buildDevelopmentAsrPreview(operation, {
			contract_version: 'asr-raw-v2',
			input: {
				contract_version: 'asr-raw-v2', audio_path: '/tmp/vocals.wav', audio_sha256: 'sha',
				engine_id: 'asr', source_track_id: 'vocals', requested_language: 'auto',
				duration_ms: 900, context_terms: []
			},
			raw_text: 'Raw transcript.', language: 'en',
			segments: [{
				segment_id: 'asr_0001', start_ms: 0, end_ms: 900, raw_text: 'Raw transcript.',
				corrected_text: null, review_candidate_text: null, review_rejection_reason: null,
				review_confidence: null, review_flags: [], review_operations: []
			}],
			incomplete_chunk_ranges: [], usage_seconds: null, provider_response_id: null, stage_timing: {}
		});

		expect(selected?.operation_id).toBe('partial-asr');
		expect(preview).toMatchObject({
			operationId: 'partial-asr',
			phase: 'asr_draft',
			phaseLabel: '原始听写稿',
			isActive: false
		});
		expect(preview.cues).toEqual([
			{ cue_id: 'asr_0001', start_ms: 0, end_ms: 900, text: 'Raw transcript.' }
		]);
	});

	it('selects the joined transcript from the parallel initial-analysis breakpoint', () => {
		const operation = {
			operation_id: 'initial-analysis', project_id: 'project', kind: 'english_asr', status: 'success',
			label: '原始听写 + 说话人区分（并行开发）', progress: 1,
			error_code: null, error_message: null, cancel_requested: false,
			parameters: { execution_mode: 'stop_after', stop_after_step: 'initial_analysis' },
			created_at: '2026-07-25T10:00:00Z', started_at: '2026-07-25T10:00:01Z',
			completed_at: '2026-07-25T10:00:39Z',
			result_summary: { artifact_available: true }
		} as const;
		const result = {
			contract_version: 'asr-initial-analysis-snapshot-v1',
			analysis: {
				contract_version: 'asr-initial-analysis-v1',
				raw_asr: {
					contract_version: 'asr-raw-v2',
					input: {
						contract_version: 'asr-raw-v2', audio_path: '/tmp/vocals.wav',
						audio_sha256: 'sha', engine_id: 'asr', source_track_id: 'vocals',
						requested_language: 'auto', duration_ms: 900, context_terms: []
					},
					raw_text: 'Raw transcript.', language: 'en', segments: [],
					incomplete_chunk_ranges: [], usage_seconds: null,
					provider_response_id: null, stage_timing: {}
				},
				diarization: null,
				diarization_error: null
			},
			joined_transcript: {
				contract_version: 'asr-joined-transcript-v1',
				raw_asr: {} as never,
				diarization: null,
				segments: [{
					segment_id: 'asr_0001', start_ms: 0, end_ms: 900,
					raw_text: 'Raw transcript.', speaker_cluster_id: 'cluster_01',
					corrected_text: null, review_candidate_text: null,
					review_rejection_reason: null, review_confidence: null,
					review_flags: [], review_operations: []
				}],
				warnings: []
			}
		} as const;

		expect(resolveDevelopmentAsrPreviewOperation([operation])?.operation_id).toBe('initial-analysis');
		expect(buildDevelopmentInitialAnalysisPreview(operation, result as never)).toMatchObject({
			operationId: 'initial-analysis',
			phase: 'asr_draft',
			cues: [{ cue_id: 'asr_0001', start_ms: 0, end_ms: 900, text: 'Raw transcript.' }]
		});
		expect(developmentInitialAnalysisResultMatchesDraft(
			result as never,
			{ source_media: {}, stems: { vocals_clean_sha256: 'sha' } } as never
		)).toBe(true);
	});

	it('shows a completed name-normalization snapshot as the latest text-review track', () => {
		const operation = {
			operation_id: 'normalize', project_id: 'project', kind: 'english_asr', status: 'success',
			label: '统一名称与术语（开发单步）', progress: 1,
			error_code: null, error_message: null, cancel_requested: false,
			parameters: { execution_mode: 'stop_after', stop_after_step: 'normalize_entities' },
			created_at: '2026-07-25T11:00:00Z', started_at: null,
			completed_at: '2026-07-25T11:00:20Z',
			result_summary: { artifact_available: true, stage: '统一名称与术语已完成（开发单步）' }
		} as const;
		const result = {
			contract_version: 'asr-entity-normalization-v1',
			input: {
				contract_version: 'asr-entity-normalization-input-v1',
				source_track_id: 'vocals',
				source_audio_sha256: 'sha'
			},
			status: 'completed',
			updated_segments: [{
				segment_id: 'asr_0001', start_ms: 0, end_ms: 900,
				raw_text: 'Duan Feeny.', corrected_text: 'JoAnne Feeney.',
				review_candidate_text: null, review_rejection_reason: null,
				review_confidence: 0.96, review_flags: [], review_operations: []
			}],
			resolutions: [], changes: [], warnings: [], duration_ms: 20
		} as const;

		expect(resolveDevelopmentAsrPreviewOperation([operation])?.operation_id).toBe('normalize');
		expect(buildDevelopmentEntityNormalizationPreview(operation, result as never)).toMatchObject({
			phase: 'text_review',
			phaseLabel: '文本校对',
			cues: [{ cue_id: 'asr_0001', text: 'JoAnne Feeney.' }]
		});
		expect(developmentEntityNormalizationResultMatchesDraft(
			result as never,
			{ source_media: {}, stems: { vocals_clean_sha256: 'sha' } } as never
		)).toBe(true);
	});

	it('restores the complete accepted-review snapshot as the shared text-review track', () => {
		const operation = {
			operation_id: 'review-decisions', project_id: 'project', kind: 'english_asr', status: 'success',
			label: '汇总第 1 轮修改（开发单步）', progress: 1,
			error_code: null, error_message: null, cancel_requested: false,
			parameters: { execution_mode: 'stop_after', stop_after_step: 'review_decisions_r1' },
			created_at: '2026-07-25T12:00:00Z', started_at: null,
			completed_at: '2026-07-25T12:00:20Z',
			result_summary: { artifact_available: true, applied_change_count: 1 }
		} as const;
		const result = {
			contract_version: 'asr-review-decisions-v4',
			input: {
				contract_version: 'asr-review-decisions-input-v4',
				source_track_id: 'vocals',
				source_audio_sha256: 'sha'
			},
			status: 'completed',
			updated_segments: [
				{
					segment_id: 'asr_0001', start_ms: 0, end_ms: 900,
					raw_text: 'The old wording.', corrected_text: 'The accepted wording.',
					review_candidate_text: null, review_rejection_reason: null,
					review_confidence: 0.94, review_flags: [], review_operations: []
				},
				{
					segment_id: 'asr_0002', start_ms: 1_000, end_ms: 1_800,
					raw_text: 'Unchanged.', corrected_text: null,
					review_candidate_text: null, review_rejection_reason: null,
					review_confidence: null, review_flags: [], review_operations: []
				}
			],
			decisions: [], changes: [], warnings: [], duration_ms: 20
		} as const;

		expect(resolveDevelopmentAsrPreviewOperation([operation])?.operation_id).toBe('review-decisions');
		expect(buildDevelopmentReviewDecisionsPreview(operation, result as never)).toMatchObject({
			phase: 'text_review',
			cues: [
				{ cue_id: 'asr_0001', start_ms: 0, end_ms: 900, text: 'The accepted wording.' },
				{ cue_id: 'asr_0002', start_ms: 1_000, end_ms: 1_800, text: 'Unchanged.' }
			]
		});
		expect(developmentReviewDecisionsResultMatchesDraft(
			result as never,
			{ source_media: {}, stems: { vocals_clean_sha256: 'sha' } } as never
		)).toBe(true);
	});

	it('keeps the same complete subtitle snapshot after a read-only whole recheck', () => {
		const operation = {
			operation_id: 'whole-recheck', project_id: 'project', kind: 'english_asr', status: 'success',
			label: '第 1 轮全文复核（开发单步）', progress: 1,
			error_code: null, error_message: null, cancel_requested: false,
			parameters: { execution_mode: 'stop_after', stop_after_step: 'whole_recheck_r1' },
			created_at: '2026-07-25T12:01:00Z', started_at: null,
			completed_at: '2026-07-25T12:01:20Z',
			result_summary: { artifact_available: true, next_section_count: 1 }
		} as const;
		const result = {
			contract_version: 'asr-whole-recheck-v3',
			input: {
				contract_version: 'asr-whole-recheck-input-v3',
				source_track_id: 'vocals',
				source_audio_sha256: 'sha',
				segments: [{
					segment_id: 'asr_0001', start_ms: 0, end_ms: 900,
					raw_text: 'The old wording.', corrected_text: 'The accepted wording.',
					review_candidate_text: null, review_rejection_reason: null,
					review_confidence: 0.94, review_flags: [], review_operations: []
				}]
			},
			status: 'partial', passed: false, next_action: 'review_next_round',
			next_sections: [], unresolved_items: [], warnings: [], duration_ms: 20
		} as const;

		expect(resolveDevelopmentAsrPreviewOperation([operation])?.operation_id).toBe('whole-recheck');
		expect(buildDevelopmentWholeRecheckPreview(operation, result as never)).toMatchObject({
			phase: 'text_review',
			cues: [{ cue_id: 'asr_0001', text: 'The accepted wording.' }]
		});
		expect(developmentWholeRecheckResultMatchesDraft(
			result as never,
			{ source_media: {}, stems: { vocals_clean_sha256: 'sha' } } as never
		)).toBe(true);
	});

	it('keeps the whole recheck subtitle snapshot when a newer quality gate finishes', () => {
		const base = {
			project_id: 'project',
			kind: 'english_asr' as const,
			status: 'success' as const,
			progress: 1,
			error_code: null,
			error_message: null,
			cancel_requested: false,
			started_at: null
		};
		const wholeRecheck = {
			...base,
			operation_id: 'whole-recheck',
			label: '第 1 轮全文复核（开发单步）',
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'whole_recheck_r1'
			},
			created_at: '2026-07-25T12:01:00Z',
			completed_at: '2026-07-25T12:01:20Z',
			result_summary: { artifact_available: true }
		};
		const qualityGate = {
			...base,
			operation_id: 'quality-gate',
			label: '进入校时前检查（开发单步）',
			parameters: {
				execution_mode: 'stop_after',
				stop_after_step: 'transcript_quality_gate',
				input_whole_recheck_operation_id: 'whole-recheck'
			},
			created_at: '2026-07-25T12:02:00Z',
			completed_at: '2026-07-25T12:02:01Z',
			result_summary: {
				artifact_available: true,
				decision: 'ready_for_alignment',
				can_start_alignment: true,
				review_target_count: 1
			}
		};

		expect(resolveDevelopmentAsrPreviewOperation([
			wholeRecheck,
			qualityGate
		])?.operation_id).toBe('whole-recheck');
	});

	it('lets a newer completed formal ASR replace an older development preview', () => {
		const base = {
			project_id: 'project', kind: 'english_asr' as const, status: 'success' as const,
			label: '听写字幕', progress: 1, error_code: null, error_message: null,
			cancel_requested: false, started_at: null
		};
		expect(resolveDevelopmentAsrPreviewOperation([
			{
				...base,
				operation_id: 'partial-asr',
				parameters: { execution_mode: 'stop_after', stop_after_step: 'asr' },
				created_at: '2026-07-24T10:00:00Z',
				completed_at: '2026-07-24T10:00:30Z',
				result_summary: { artifact_available: true }
			},
			{
				...base,
				operation_id: 'formal-asr',
				parameters: { execution_mode: 'full' },
				created_at: '2026-07-24T11:00:00Z',
				completed_at: '2026-07-24T11:05:00Z',
				result_summary: { stage: 'ASR 字幕已完成' }
			}
		])).toBeNull();
	});

	it('keeps the latest subtitle-capable development preview after newer non-editing development steps', () => {
		const base = {
			project_id: 'project', kind: 'english_asr' as const, status: 'success' as const,
			label: 'ASR 开发任务', progress: 1, error_code: null, error_message: null,
			cancel_requested: false, started_at: null
		};
		expect(resolveDevelopmentAsrPreviewOperation([
			{
				...base,
				operation_id: 'initial-analysis',
				parameters: { execution_mode: 'stop_after', stop_after_step: 'initial_analysis' },
				created_at: '2026-07-25T10:00:00Z',
				completed_at: '2026-07-25T10:00:30Z',
				result_summary: { artifact_available: true }
			},
			{
				...base,
				operation_id: 'entity-normalization',
				parameters: { execution_mode: 'stop_after', stop_after_step: 'normalize_entities' },
				created_at: '2026-07-25T11:00:00Z',
				completed_at: '2026-07-25T11:00:30Z',
				result_summary: { artifact_available: true }
			},
			{
				...base,
				operation_id: 'section-review',
				parameters: { execution_mode: 'stop_after', stop_after_step: 'section_review_r1' },
				created_at: '2026-07-25T12:00:00Z',
				completed_at: '2026-07-25T12:00:30Z',
				result_summary: { artifact_available: true }
			}
		])?.operation_id).toBe('entity-normalization');
	});

	it('hides a development preview as soon as a new ASR run starts', () => {
		const base = {
			project_id: 'project', kind: 'english_asr' as const,
			label: '听写字幕', progress: 1, error_code: null, error_message: null,
			cancel_requested: false, started_at: null, completed_at: null
		};
		expect(resolveDevelopmentAsrPreviewOperation([
			{
				...base, operation_id: 'partial-asr', status: 'success',
				parameters: { execution_mode: 'stop_after', stop_after_step: 'asr' },
				created_at: '2026-07-24T10:00:00Z', completed_at: '2026-07-24T10:01:00Z',
				result_summary: { artifact_available: true }
			},
			{
				...base, operation_id: 'formal-asr', status: 'running',
				parameters: { execution_mode: 'full' },
				created_at: '2026-07-24T11:00:00Z', progress: 0.1, result_summary: {}
			}
		])).toBeNull();
	});

	it('uses the running review baseline instead of leaving the subtitle track empty', () => {
		const preview = resolveAsrOperationPreview([{
			operation_id: 'review-running', project_id: 'project', kind: 'english_asr', status: 'running',
			label: '汇总第 1 轮修改（开发单步）', progress: 0.4,
			error_code: null, error_message: null, cancel_requested: false,
			parameters: { execution_mode: 'stop_after', stop_after_step: 'review_decisions_r1' },
			created_at: '2026-07-25T12:00:00Z', started_at: '2026-07-25T12:00:01Z',
			completed_at: null,
			result_summary: {
				preview_phase: 'text_review',
				preview_cues: [
					{ cue_id: 'asr_0001', start_ms: 0, end_ms: 900, text: 'Current full-track baseline.' }
				]
			}
		}]);

		expect(preview).toMatchObject({
			operationId: 'review-running',
			phase: 'text_review',
			isActive: true,
			cues: [{ cue_id: 'asr_0001', text: 'Current full-track baseline.' }]
		});
	});

	it('keeps the previous compatible snapshot visible while the next snapshot is loading', () => {
		const preview = {
			operationId: 'normalize',
			sourceTrackId: 'vocals',
			sourceAudioSha256: 'sha',
			cues: [{ cue_id: 'asr_0001', start_ms: 0, end_ms: 900, text: 'Current text.' }]
		} as never;
		const draft = { source_media: {}, stems: { vocals_clean_sha256: 'sha' } } as never;

		expect(canDisplayDevelopmentAsrPreview(
			preview,
			draft,
			'review-decisions',
			'review-decisions'
		)).toBe(true);
		expect(canDisplayDevelopmentAsrPreview(
			preview,
			draft,
			'review-decisions',
			''
		)).toBe(false);
		expect(canDisplayDevelopmentAsrPreview(
			preview,
			{ source_media: {}, stems: { vocals_clean_sha256: 'new-sha' } } as never,
			'review-decisions',
			'review-decisions'
		)).toBe(false);
	});

	it('rejects a development result after the source track fingerprint changes', () => {
		const result = { input: { source_track_id: 'vocals', audio_sha256: 'old-sha' } };
		const draft = { source_media: {}, stems: { vocals_clean_sha256: 'new-sha' } };
		expect(developmentAsrResultMatchesDraft(result as never, draft as never)).toBe(false);
		expect(developmentAsrResultMatchesDraft(
			{ input: { source_track_id: 'vocals', audio_sha256: 'new-sha' } } as never,
			draft as never
		)).toBe(true);
		expect(developmentAsrPreviewMatchesDraft(
			{ sourceTrackId: 'vocals', sourceAudioSha256: 'old-sha' } as never,
			draft as never
		)).toBe(false);
		expect(developmentAsrPreviewMatchesDraft(null, draft as never)).toBe(false);
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

	it('resolves the same staged ASR cue text for the player at the current timeline time', () => {
		const preview = {
			cues: [
				{ cue_id: 'asr_0001', start_ms: 0, end_ms: 900, text: 'First staged cue.' },
				{ cue_id: 'asr_0002', start_ms: 1_000, end_ms: 1_800, text: 'Second staged cue.' }
			]
		};
		expect(resolveAsrPreviewTextAtTime(preview as never, 0)).toBe('First staged cue.');
		expect(resolveAsrPreviewTextAtTime(preview as never, 1_200)).toBe('Second staged cue.');
		expect(resolveAsrPreviewTextAtTime(preview as never, 950)).toBeNull();
	});
});
