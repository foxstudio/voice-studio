import type {
	VideoLocalizationDraft,
	VideoLocalizationTimelineClip,
	VideoLocalizationTtsTask
} from '$lib/api/types';
import { resolveDubClipLane } from './dub-track-lanes';
import { ttsInitializationClientId } from './tts-workflow-marker';

function workflowProgress(task: VideoLocalizationTtsTask) {
	const generation = task.stages.find((stage) => stage.kind === 'generation');
	const placement = task.stages.find((stage) => stage.kind === 'placement');
	if (placement?.status === 'success') return 1;
	if (generation?.status === 'success') {
		return 0.85 + Math.max(0, Math.min(1, placement?.progress ?? 0)) * 0.15;
	}
	return Math.max(0, Math.min(1, generation?.progress ?? 0)) * 0.85;
}

function workflowStatusLabel(task: VideoLocalizationTtsTask) {
	if (task.status === 'prepared') return '等待提交';
	if (task.status === 'queued') return '排队中';
	const generation = task.stages.find((stage) => stage.kind === 'generation');
	const placement = task.stages.find((stage) => stage.kind === 'placement');
	if (generation?.status === 'success') {
		return placement?.status === 'running' ? '正在放入轨道' : '等待放入轨道';
	}
	return '生成中';
}

function workflowIsActive(task: VideoLocalizationTtsTask) {
	return task.status === 'prepared' || task.status === 'queued' || task.status === 'running';
}

function clipLooksActive(clip: VideoLocalizationTimelineClip) {
	return ['pending', 'queued', 'running', 'processing', 'postprocessing', 'retrying', 'applying']
		.includes(String(clip.status ?? ''));
}

function clipContainsWorkflowResult(
	clip: VideoLocalizationTimelineClip,
	task: VideoLocalizationTtsTask
) {
	if (!clip.audio_path) return false;
	return Boolean(
		(task.generation_task_id && (
			clip.task_id === task.generation_task_id
			|| clip.generation_id === task.generation_task_id
		))
		|| (task.result_id && clip.result_id === task.result_id)
	);
}

/**
 * Reconciles local initialization/workflow placeholders with authoritative
 * workflow tasks without creating a blank frame between lifecycle phases.
 */
export function withTtsWorkflowPlaceholders(value: VideoLocalizationDraft): VideoLocalizationDraft {
	const optimisticClips = value.timeline_clips.filter((clip) => clip.optimistic_tts_workflow_id);
	const stableClips = value.timeline_clips.filter((clip) => !clip.optimistic_tts_workflow_id);
	const tasksByWorkflowId = new Map(
		(value.tts_tasks ?? []).map((task) => [task.workflow_id, task])
	);
	const placeholders: VideoLocalizationTimelineClip[] = [];
	const retainedWorkflowIds = new Set<string>();
	const seenPlaceholderMarkers = new Set<string>();
	for (const originalClip of optimisticClips) {
		const originalMarker = String(originalClip.optimistic_tts_workflow_id ?? '');
		const initializationClientId = ttsInitializationClientId(originalMarker);
		const handedOffTask = initializationClientId
			? tasksByWorkflowId.get(initializationClientId)
			: undefined;
		const marker = handedOffTask?.workflow_id ?? originalMarker;
		if (seenPlaceholderMarkers.has(marker)) continue;
		seenPlaceholderMarkers.add(marker);
		const clip = marker === originalMarker
			? originalClip
			: { ...originalClip, optimistic_tts_workflow_id: marker };
		if (ttsInitializationClientId(marker)) {
			placeholders.push(clip);
			continue;
		}
		const task = tasksByWorkflowId.get(marker);
		if (!task) {
			if (clipLooksActive(clip)) placeholders.push(clip);
			continue;
		}
		if (task.status === 'success') {
			// Workflow success means the server has already completed placement.
			// The authoritative stable timeline either contains that result or the
			// user removed/replaced it later; a runtime placeholder is never media
			// and must not survive either case.
			continue;
		}
		if (!workflowIsActive(task)) {
			// Terminal failures belong in task history, not on the editable audio
			// timeline. Keeping them here makes old failures reappear after refresh.
			continue;
		}
		placeholders.push({
			...clip,
			subtitle_id: task.segment_id,
			cue_id: task.source_cue_ids[0] ?? clip.cue_id ?? null,
			source_cue_ids: task.source_cue_ids,
			task_id: task.generation_task_id ?? clip.task_id,
			generation_id: task.generation_task_id ?? clip.generation_id,
			status: task.status === 'prepared' ? 'queued' : task.status,
			status_label: workflowStatusLabel(task),
			generation_progress: workflowProgress(task)
		});
		retainedWorkflowIds.add(task.workflow_id);
	}

	for (const task of value.tts_tasks ?? []) {
		if (!workflowIsActive(task) || retainedWorkflowIds.has(task.workflow_id)) continue;
		if (stableClips.some((clip) => clipContainsWorkflowResult(clip, task))) continue;
		const generation = task.stages.find((stage) => stage.kind === 'generation');
		const requestedClipId = typeof generation?.parameters?.timeline_clip_id === 'string'
			? generation.parameters.timeline_clip_id
			: '';
		const replacingClip = requestedClipId
			? stableClips.find((clip) => clip.track_id === 'dub' && clip.clip_id === requestedClipId)
			: undefined;
		const startMs = Math.max(0, replacingClip?.start_ms ?? task.start_ms);
		const endMs = Math.max(startMs + 300, replacingClip?.end_ms ?? task.end_ms);
		const preferredLane = Number(replacingClip?.dub_lane ?? 0);
		const dubLane = replacingClip
			? preferredLane
			: resolveDubClipLane([...stableClips, ...placeholders], startMs, endMs, preferredLane);
		placeholders.push({
			clip_id: `pending_tts_${task.workflow_id}`,
			track_id: 'dub',
			subtitle_id: task.segment_id,
			cue_id: task.source_cue_ids[0] ?? null,
			source_cue_ids: task.source_cue_ids,
			start_ms: startMs,
			end_ms: endMs,
			source_start_ms: 0,
			source_end_ms: endMs - startMs,
			audio_path: null,
			task_id: task.generation_task_id,
			generation_id: task.generation_task_id,
			dub_lane: dubLane,
			status: task.status === 'prepared' ? 'queued' : task.status,
			status_label: workflowStatusLabel(task),
			generation_progress: workflowProgress(task),
			optimistic_tts_workflow_id: task.workflow_id
		});
	}
	if (!placeholders.length && stableClips.length === value.timeline_clips.length) return value;
	return { ...value, timeline_clips: [...stableClips, ...placeholders] };
}
