import type { VideoLocalizationTtsTask } from '$lib/api/types';

function targetSubtitleIds(task: VideoLocalizationTtsTask) {
	const generation = task.stages.find((stage) => stage.kind === 'generation');
	const parameters = generation?.parameters;
	if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) return [];
	const direct = parameters.video_localization_target_subtitle_ids;
	if (Array.isArray(direct)) return direct.map(String).filter(Boolean);
	const pack = parameters.video_localization_parameter_pack;
	if (!pack || typeof pack !== 'object' || Array.isArray(pack)) return [];
	const target = (pack as Record<string, unknown>).target;
	if (!target || typeof target !== 'object' || Array.isArray(target)) return [];
	const values = (target as Record<string, unknown>).subtitle_ids;
	return Array.isArray(values) ? values.map(String).filter(Boolean) : [];
}

export function workflowSegmentIdForSubtitleSelection(
	tasks: readonly VideoLocalizationTtsTask[],
	selectedSubtitleIds: readonly string[]
) {
	const selected = new Set(selectedSubtitleIds.filter(Boolean));
	if (!selected.size) return '';
	return [...tasks].reverse().find((task) => {
		const targetIds = targetSubtitleIds(task);
		return targetIds.length === selected.size && targetIds.every((id) => selected.has(id));
	})?.segment_id ?? '';
}
