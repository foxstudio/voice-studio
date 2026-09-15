import type { VideoLocalizationDraft } from '$lib/api/types';

export interface SemanticTtsGroupView {
	group_id: string;
	subtitle_ids: string[];
	text_preview: string;
	char_count: number;
}

export function activeDubbingPlanGroups(
	draft: VideoLocalizationDraft | null
): SemanticTtsGroupView[] | null {
	const plan = draft?.dubbing_production?.active_plan;
	if (!plan) return null;
	return plan.groups.map((group) => ({
		group_id: group.group_id,
		subtitle_ids: group.subtitle_ids,
		text_preview: group.spoken_text.slice(0, 120),
		char_count: Array.from(group.spoken_text).length
	}));
}
