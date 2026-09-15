import { describe, expect, it } from 'vitest';
import type { VideoLocalizationCue, VideoLocalizationDubSubtitleCue, VideoLocalizationOperation, VideoLocalizationSubtitleCue } from '$lib/api/types';
import {
	SubtitleDisplayModel,
	defaultSubtitleDisplaySettings,
	resolveSubtitleDisplaySettings
} from './subtitle-display';

function asrCue(
	id: string,
	startMs: number,
	endMs: number,
	text: string
): VideoLocalizationCue {
	return {
		cue_id: id,
		speaker_id: null,
		start_ms: startMs,
		end_ms: endMs,
		audio_route: 'manual_review',
		en_subtitle_text: text,
		zh_localized_subtitle_text: null,
		tts_recommended_text: null,
		reference_clip_id: null,
		tts_result_id: null,
		tts_audio_path: null,
		tts_batch_task_id: null,
		tts_batch_status: null,
		tts_batch_error: null,
		tts_attempted_at: null,
		source_duration_ms: null,
		generated_duration_ms: null,
		source_word_ids: [],
		source_text_raw: text,
		timing_confidence: 'high',
		transcription_revision_id: null,
		review_status: 'ready',
		quality_flags: [],
		notes: null
	};
}

function localizedCue(
	id: string,
	startMs: number,
	endMs: number,
	text: string
): VideoLocalizationSubtitleCue {
	return {
		subtitle_id: id,
		start_ms: startMs,
		end_ms: endMs,
		text,
		quality_flags: []
	};
}

function operation(
	status: VideoLocalizationOperation['status'],
	previewCues: unknown[],
	kind: VideoLocalizationOperation['kind'] = 'localization_draft',
	overrides: Partial<VideoLocalizationOperation> = {}
): VideoLocalizationOperation {
	return {
		operation_id: 'localization-op',
		project_id: 'project',
		kind,
		status,
		label: null,
		progress: 0.5,
		error_code: null,
		error_message: null,
		cancel_requested: false,
		result_summary: { preview_cues: previewCues },
		parameters: {},
		created_at: '2026-07-24T00:00:00Z',
		started_at: null,
		completed_at: null,
		...overrides
	};
}

describe('SubtitleDisplayModel', () => {
	it('uses one set of cues for the timeline track and current player line', () => {
		const model = new SubtitleDisplayModel({
			settings: defaultSubtitleDisplaySettings(),
			asrCues: [asrCue('formal', 0, 1000, '旧稿')],
			asrPreview: {
				operationId: 'op',
				phase: 'asr_draft',
				phaseLabel: '原始听写稿',
				stage: '原始听写稿',
				progress: 1,
				isActive: false,
				cues: [{ cue_id: 'preview', start_ms: 0, end_ms: 1000, text: '阶段结果' }]
			}
		});

		expect(model.tracks.asr.cues.map((cue) => cue.id)).toEqual(['preview']);
		expect(model.tracks.asr.provisional).toBe(true);
			expect(model.frameAt(500).lines.filter((line) => !line.placeholder).map((line) => line.text))
				.toEqual(['阶段结果']);
	});

	it('falls back to committed ASR when no stage result exists', () => {
		const model = new SubtitleDisplayModel({
			settings: defaultSubtitleDisplaySettings(),
			asrCues: [asrCue('formal', 0, 1000, '正式稿')]
		});

		expect(model.tracks.asr.provisional).toBe(false);
		expect(model.frameAt(500).currentCues.asr?.text).toBe('正式稿');
		expect(model.activeSource).toBe('asr');
	});

	it('uses the localization stage result before committed localized subtitles', () => {
		const model = new SubtitleDisplayModel({
			settings: defaultSubtitleDisplaySettings(),
			localizedCues: [localizedCue('formal', 0, 1000, '正式中文')],
			localizedPreview: [localizedCue('preview', 0, 1000, '阶段中文')]
		});

		expect(model.tracks.localized.provisional).toBe(true);
		expect(model.frameAt(500).lines[0]?.text).toBe('阶段中文');
	});

		it('renders no stale line while the playhead is in a subtitle gap', () => {
			const model = new SubtitleDisplayModel({
				settings: defaultSubtitleDisplaySettings(),
				asrCues: [asrCue('asr', 0, 1000, '原文')],
				localizedCues: [localizedCue('localized', 2000, 3000, '中文')]
			});
			const frame = model.frameAt(1500);

		expect(frame.lines).toEqual([]);
		expect(frame.currentCues.asr).toBeNull();
			expect(frame.currentCues.localized).toBeNull();
		});

		it('keeps one stable lane for each enabled subtitle track while either track has text', () => {
			const model = new SubtitleDisplayModel({
				settings: defaultSubtitleDisplaySettings(),
				asrCues: [asrCue('asr', 0, 3000, '原文')],
				localizedCues: [localizedCue('localized', 1000, 2000, '中文')]
			});

			expect(model.frameAt(500).lines.map((line) => ({
				source: line.source,
				placeholder: line.placeholder
			}))).toEqual([
				{ source: 'localized', placeholder: true },
				{ source: 'asr', placeholder: false }
			]);
			expect(model.frameAt(1500).lines.map((line) => line.placeholder))
				.toEqual([false, false]);
			expect(model.frameAt(2500).lines.map((line) => ({
				source: line.source,
				placeholder: line.placeholder
			}))).toEqual([
				{ source: 'localized', placeholder: true },
				{ source: 'asr', placeholder: false }
			]);
		});

	it('keeps empty committed cues selectable on the timeline without rendering an empty player line', () => {
		const model = new SubtitleDisplayModel({
			settings: defaultSubtitleDisplaySettings(),
			asrCues: [asrCue('empty-asr', 0, 1000, '')],
			localizedCues: [localizedCue('empty-localized', 1000, 2000, '')]
		});

		expect(model.tracks.asr.cues.map((cue) => cue.id)).toEqual(['empty-asr']);
		expect(model.tracks.localized.cues.map((cue) => cue.id)).toEqual(['empty-localized']);
		expect(model.frameAt(500).lines).toEqual([]);
		expect(model.frameAt(1500).lines).toEqual([]);
	});

	it('uses explicit track visibility for both the timeline controls and player lines', () => {
		const settings = resolveSubtitleDisplaySettings({
			sources: { asr: false, localized: true }
		});
		const model = new SubtitleDisplayModel({
			settings,
			asrCues: [asrCue('asr', 0, 1000, '原文')],
			localizedCues: [localizedCue('localized', 0, 1000, '中文')]
		});

		expect(model.tracks.asr.visible).toBe(false);
		expect(model.tracks.localized.visible).toBe(true);
		expect(model.frameAt(500).lines.map((line) => line.source)).toEqual(['localized']);
	});

	it('uses the selected cue source for the inspector and updates only its style', () => {
		const model = new SubtitleDisplayModel({
			settings: defaultSubtitleDisplaySettings(),
			asrCues: [asrCue('asr', 0, 1000, '原文')],
			localizedCues: [localizedCue('localized', 0, 1000, '中文')],
			selection: { source: 'asr', id: 'asr' }
		});
		const next = model.updateStyle(model.activeSource, { fontSize: 25 });

		expect(model.activeSource).toBe('asr');
		expect(next.trackStyles.asr.fontSize).toBe(25);
		expect(next.trackStyles.localized.fontSize).toBe(18);
	});

	it('keeps a selected stage cue read-only while exposing the same cue to the inspector', () => {
		const model = new SubtitleDisplayModel({
			settings: defaultSubtitleDisplaySettings(),
			asrPreview: {
				operationId: 'op',
				phase: 'asr_draft',
				phaseLabel: '原始听写稿',
				stage: '原始听写稿',
				progress: 1,
				isActive: false,
				cues: [{ cue_id: 'stage', start_ms: 0, end_ms: 1000, text: '阶段结果' }]
			},
			selection: { source: 'asr', id: 'stage' }
		});

		expect(model.selectedCue).toBe(model.tracks.asr.cues[0]);
		expect(model.selectedCue).toMatchObject({
			key: 'asr:preview:stage',
			provisional: true,
			editable: false
		});
	});

	it('toggles only the requested source in the canonical settings', () => {
		const model = new SubtitleDisplayModel({
			settings: defaultSubtitleDisplaySettings()
		});

		expect(model.toggleSource('asr').sources).toEqual({
			asr: false,
			localized: true
		});
	});

	it('normalizes active localization operation previews inside the module', () => {
		const preview = SubtitleDisplayModel.resolveLocalizedPreview([
			operation('running', [{
				subtitle_id: 'localized_preview',
				start_ms: 120.4,
				end_ms: 940.6,
				text: ' 阶段中文 ',
				quality_flags: ['preview']
			}])
		]);

		expect(preview).toEqual([expect.objectContaining({
			subtitle_id: 'localized_preview',
			start_ms: 120,
			end_ms: 941,
			text: '阶段中文',
			quality_flags: ['preview']
		})]);
		expect(SubtitleDisplayModel.resolveLocalizedPreview([
			operation('success', [{ start_ms: 0, end_ms: 1000, text: '不应继续显示' }])
		])).toEqual([]);
	});

	it('normalizes an active synthesized-dub subtitle preview for the localized track', () => {
		const preview = SubtitleDisplayModel.resolveDubSubtitlePreview([
			operation('running', [{
				subtitle_id: 'dub_preview_1',
				start_ms: 13_540,
				end_ms: 15_000,
				text: '第一句配音',
				quality_flags: ['timing:forced-aligner']
			}], 'dub_subtitle_generation', {
				result_summary: {
					preview_phase: 'timing_segmentation',
					preview_cues: [{
						subtitle_id: 'dub_preview_1',
						start_ms: 13_540,
						end_ms: 15_000,
						text: '第一句配音',
						quality_flags: ['timing:forced-aligner']
					}]
				}
			})
		]);

		expect(preview).toEqual([expect.objectContaining({
			subtitle_id: 'dub_preview_1',
			start_ms: 13_540,
			end_ms: 15_000,
			text: '第一句配音',
			quality_flags: ['timing:forced-aligner']
		})]);
		expect(SubtitleDisplayModel.resolveDubSubtitlePreview([
			operation('success', [{
				start_ms: 0,
				end_ms: 1000,
				text: '完成后不再显示预览'
			}], 'dub_subtitle_generation')
		])).toEqual([]);
		expect(SubtitleDisplayModel.resolveDubSubtitlePreview([
			operation('success', [{
				start_ms: 13_540,
				end_ms: 15_000,
				text: '听写计算块不能显示为字幕'
			}], 'dub_subtitle_generation', {
				parameters: {
					execution_mode: 'development_target',
					development_target_step_id: 'transcribe_track'
				},
				result_summary: {
					stage_id: 'transcribe_track',
					preview_cues: [{
						start_ms: 13_540,
						end_ms: 15_000,
						text: '听写计算块不能显示为字幕'
					}]
				}
			})
		])).toEqual([]);
	});

	it('shows persisted dubbing subtitles as a reviewable localized-track alternative', () => {
		const dubSubtitles: VideoLocalizationDubSubtitleCue[] = [{
			subtitle_id: 'dub_1',
			start_ms: 200,
			end_ms: 900,
			text: '实际说出的台词',
			speaker_id: 'speaker_1',
			source_clip_ids: ['clip_1'],
			dub_lanes: [0, 1],
			source_audio_sha256: 'sha256',
			needs_review: false,
			quality_flags: []
		}];
		const model = new SubtitleDisplayModel({
			settings: defaultSubtitleDisplaySettings(),
			localizedCues: [localizedCue('localized_1', 200, 900, '本土化上屏台词')],
			localizedAlternative: {
				cues: dubSubtitles,
				label: '合成配音字幕'
			}
		});

		expect(model.tracks.localized).toMatchObject({
			provisional: false,
			phaseLabel: '合成配音字幕',
			isActive: false
		});
			expect(model.tracks.localized.cues[0]).toMatchObject({
			id: 'dub_1',
			text: '实际说出的台词',
			editable: false,
			reviewable: true,
			phaseLabel: '合成配音字幕'
		});
	});

	it('shows every overlapping audible dub lane as a stable player line', () => {
		const dubSubtitles: VideoLocalizationDubSubtitleCue[] = [
			{
				subtitle_id: 'dub_lane_1',
				start_ms: 0,
				end_ms: 1000,
				text: '第二轨同时说话',
				speaker_id: 'speaker_2',
				source_clip_ids: ['clip_2'],
				dub_lanes: [1],
				source_audio_sha256: 'b'.repeat(64),
				needs_review: false,
				quality_flags: []
			},
			{
				subtitle_id: 'dub_lane_0',
				start_ms: 0,
				end_ms: 1000,
				text: '第一轨同时说话',
				speaker_id: 'speaker_1',
				source_clip_ids: ['clip_1'],
				dub_lanes: [0],
				source_audio_sha256: 'a'.repeat(64),
				needs_review: false,
				quality_flags: []
			}
		];
		const model = new SubtitleDisplayModel({
			settings: {
				...defaultSubtitleDisplaySettings(),
				sources: { asr: false, localized: true }
			},
			localizedAlternative: {
				cues: dubSubtitles,
				label: '合成配音字幕'
			}
		});

		expect(
			model.frameAt(500).lines.map((line) => line.text)
		).toEqual(['第一轨同时说话', '第二轨同时说话']);
	});
});

describe('subtitle display settings migration', () => {
	it('uses an explicit runtime visibility shape without legacy source fields', () => {
		const settings = resolveSubtitleDisplaySettings({
			enabled: true,
			source: 'localized',
			sources: null
		}, { asr: true, localized: true });

		expect(settings.sources).toEqual({ asr: false, localized: true });
		expect(settings).not.toHaveProperty('enabled');
		expect(settings).not.toHaveProperty('source');
	});

	it('preserves the legacy selected track and falls back only when that whole track is unavailable', () => {
		expect(resolveSubtitleDisplaySettings(
			{ source: 'asr', sources: null },
			{ asr: true, localized: true }
		).sources).toEqual({ asr: true, localized: false });
		expect(resolveSubtitleDisplaySettings(
			{ source: 'localized', sources: null },
			{ asr: true, localized: false }
		).sources).toEqual({ asr: true, localized: false });
	});

	it('migrates the legacy shared style into both tracks and clamps values', () => {
		const settings = resolveSubtitleDisplaySettings({
			stylePreset: 'boxed',
			fontSize: 99,
			offsetX: -999
		});

		expect(settings.trackStyles.asr).toEqual(settings.trackStyles.localized);
		expect(settings.trackStyles.asr).toMatchObject({
			stylePreset: 'boxed',
			fontSize: 32,
			offsetX: -240
		});
	});

	it('maps the legacy global disabled state to both track switches', () => {
		expect(resolveSubtitleDisplaySettings({ enabled: false }).sources)
			.toEqual({ asr: false, localized: false });
	});
});
