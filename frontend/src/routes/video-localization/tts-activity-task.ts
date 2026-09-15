import type { ActivityTask } from './activity-notice';

export type TtsGenerationActivityUpdate = {
	taskId: string;
	status: ActivityTask['status'];
	stage: string;
	progress: number;
};

/**
 * Keeps one short-lived generation row until the persisted workflow becomes
 * visible. The backend workflow is the durable task and replaces this row.
 */
export function upsertTtsGenerationActivity(
	tasks: readonly ActivityTask[],
	update: TtsGenerationActivityUpdate
): ActivityTask[] {
	const taskId = `tts:${update.taskId}`;
	const existing = tasks.find((task) => task.id === taskId);

	return [
		...tasks.filter((task) => task.id !== taskId),
		{
			id: taskId,
			label: existing?.label ?? '生成合成配音',
			stage: update.stage,
			progress: Math.max(0, Math.min(1, update.progress)),
			status: update.status,
			scope: existing?.scope ?? {
				trackIds: ['dub'],
				itemIds: [],
				area: 'generate',
				exclusive: false
			},
			createdAt: existing?.createdAt ?? new Date().toISOString()
		}
	];
}
