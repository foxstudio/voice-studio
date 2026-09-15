import type {
	VideoLocalizationTtsTask,
	VideoLocalizationTtsTaskFeed
} from '$lib/api';

/** Keeps the page's TTS task view stable while an incremental feed changes it. */
export class TtsWorkflowFeedCache {
	private revisionValue: string | null = null;
	private tasks = new Map<string, VideoLocalizationTtsTask>();
	private order: string[] = [];

	get revision() {
		return this.revisionValue;
	}

	reset(tasks: readonly VideoLocalizationTtsTask[] = []) {
		this.revisionValue = null;
		this.tasks = new Map(tasks.map((task) => [task.workflow_id, task]));
		this.order = tasks.map((task) => task.workflow_id);
	}

	apply(feed: VideoLocalizationTtsTaskFeed) {
		if (feed.changed) {
			const retainedIds = new Set(feed.workflow_ids);
			for (const workflowId of this.tasks.keys()) {
				if (!retainedIds.has(workflowId)) this.tasks.delete(workflowId);
			}
			for (const task of feed.tasks) this.tasks.set(task.workflow_id, task);
			this.order = feed.workflow_ids;
		}
		this.revisionValue = feed.revision;
		return this.order
			.slice()
			.reverse()
			.map((workflowId) => this.tasks.get(workflowId))
			.filter((task): task is VideoLocalizationTtsTask => Boolean(task));
	}
}
