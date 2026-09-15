import { Api } from '$lib/api';
import type {
	HistoryItem,
	HistoryPage,
	VideoLocalizationTtsHistoryDeleteRequest,
	VideoLocalizationTtsHistoryDeleteResponse
} from '$lib/api/types';

type TtsHistoryTransport = {
	historyPage: (params: {
		limit?: number;
		offset?: number;
		project_id?: string;
		segment_id?: string;
		source?: string;
	}) => Promise<HistoryPage>;
	history: (params: {
		limit?: number;
		project_id?: string;
		source?: string;
	}) => Promise<HistoryItem[]>;
	deleteVideoLocalizationTtsHistory: (
		projectId: string,
		request: VideoLocalizationTtsHistoryDeleteRequest
	) => Promise<VideoLocalizationTtsHistoryDeleteResponse>;
};

type TtsHistoryControllerOptions = {
	onItems: (items: HistoryItem[]) => void;
	onPageState?: (state: {
		total: number;
		loaded: number;
		hasMore: boolean;
		loading: boolean;
	}) => void;
};

const PROJECT_PAGE_SIZE = 40;
const SEGMENT_PAGE_SIZE = 8;

export class VideoLocalizationTtsHistoryClient {
	constructor(private readonly transport: TtsHistoryTransport = Api) {}

	listPage(projectId: string, offset = 0) {
		return this.transport.historyPage({
			limit: PROJECT_PAGE_SIZE,
			offset,
			project_id: projectId,
			source: 'video_localization'
		});
	}

	listSegment(projectId: string, segmentId: string) {
		return this.transport.historyPage({
			limit: SEGMENT_PAGE_SIZE,
			offset: 0,
			project_id: projectId,
			segment_id: segmentId,
			source: 'video_localization'
		});
	}

	listAll(projectId: string) {
		return this.transport.history({
			limit: -1,
			project_id: projectId,
			source: 'video_localization'
		});
	}

	deleteScope(
		projectId: string,
		request: VideoLocalizationTtsHistoryDeleteRequest
	) {
		return this.transport.deleteVideoLocalizationTtsHistory(projectId, request);
	}
}

export class TtsHistoryController {
	private activeProjectId = '';
	private currentItems: HistoryItem[] = [];
	private projectItems: HistoryItem[] = [];
	private segmentItems = new Map<string, HistoryItem>();
	private totalCount = 0;
	private loadedProjectCount = 0;
	private loading = false;
	private generation = 0;
	private readEpoch = 0;
	private mutationEpoch = 0;

	constructor(
		private readonly client: VideoLocalizationTtsHistoryClient,
		private readonly options: TtsHistoryControllerOptions
	) {}

	get projectId() {
		return this.activeProjectId;
	}

	get items() {
		return [...this.currentItems];
	}

	get total() {
		return this.totalCount;
	}

	get hasMore() {
		return this.loadedProjectCount < this.totalCount;
	}

	deactivate() {
		if (!this.activeProjectId && !this.currentItems.length) return;
		this.generation += 1;
		this.readEpoch += 1;
		this.mutationEpoch += 1;
		this.activeProjectId = '';
		this.projectItems = [];
		this.segmentItems.clear();
		this.totalCount = 0;
		this.loadedProjectCount = 0;
		this.loading = false;
		this.publish();
	}

	async loadProject(projectId: string) {
		if (!projectId) {
			this.deactivate();
			return [];
		}
		if (this.activeProjectId !== projectId) this.activate(projectId);
		return this.readProjectPage(projectId, false);
	}

	async refresh(projectId = this.activeProjectId) {
		if (!projectId || projectId !== this.activeProjectId) return null;
		return this.readProjectPage(projectId, false);
	}

	async loadMore(projectId = this.activeProjectId) {
		if (!projectId || projectId !== this.activeProjectId || this.loading || !this.hasMore) return null;
		return this.readProjectPage(projectId, true);
	}

	async ensureSegment(segmentId: string, projectId = this.activeProjectId) {
		if (!projectId || projectId !== this.activeProjectId || !segmentId) return null;
		const generation = this.generation;
		const mutationEpoch = this.mutationEpoch;
		const page = await this.client.listSegment(projectId, segmentId);
		if (
			generation !== this.generation
			|| mutationEpoch !== this.mutationEpoch
			|| projectId !== this.activeProjectId
		) return null;
		for (const item of page.items) this.segmentItems.set(item.result_id, item);
		this.publish();
		return page.items;
	}

	async deleteMany(projectId: string, resultIds: string[]) {
		const uniqueResultIds = [...new Set(resultIds.filter(Boolean))];
		if (!uniqueResultIds.length) return emptyDeleteResponse();
		const selected = new Set(uniqueResultIds);
		return this.deleteScope(
			projectId,
			{ scope: 'result_ids', result_ids: uniqueResultIds },
			(item) => selected.has(item.result_id)
		);
	}

	async deleteSegment(projectId: string, segmentId: string) {
		if (!segmentId) return emptyDeleteResponse();
		return this.deleteScope(
			projectId,
			{ scope: 'segment', segment_id: segmentId },
			(item) => historyBelongsToSegment(item, segmentId)
		);
	}

	async deleteAll(projectId: string) {
		return this.deleteScope(
			projectId,
			{ scope: 'project' },
			() => true
		);
	}

	private activate(projectId: string) {
		this.generation += 1;
		this.readEpoch += 1;
		this.mutationEpoch += 1;
		this.activeProjectId = projectId;
		this.projectItems = [];
		this.segmentItems.clear();
		this.totalCount = 0;
		this.loadedProjectCount = 0;
		this.loading = false;
		this.publish();
	}

	private async readProjectPage(projectId: string, append: boolean) {
		const generation = this.generation;
		const readEpoch = ++this.readEpoch;
		const mutationEpoch = this.mutationEpoch;
		this.loading = true;
		this.publishPageState();
		let page: HistoryPage;
		try {
			page = await this.client.listPage(
				projectId,
				append ? this.loadedProjectCount : 0
			);
		} finally {
			if (generation === this.generation && projectId === this.activeProjectId) {
				this.loading = false;
				this.publishPageState();
			}
		}
		if (
			generation !== this.generation
			|| projectId !== this.activeProjectId
			|| readEpoch !== this.readEpoch
			|| mutationEpoch !== this.mutationEpoch
		) return null;
		this.projectItems = append
			? mergeHistoryItems(this.projectItems, page.items)
			: page.items;
		if (!append) this.segmentItems.clear();
		this.loadedProjectCount = page.offset + page.items.length;
		this.totalCount = page.total;
		this.publish();
		return page.items;
	}

	private beginMutation() {
		this.mutationEpoch += 1;
		this.readEpoch += 1;
	}

	private completeMutation() {
		this.mutationEpoch += 1;
		this.readEpoch += 1;
		return this.mutationEpoch;
	}

	private async deleteScope(
		projectId: string,
		request: VideoLocalizationTtsHistoryDeleteRequest,
		matches: (item: HistoryItem) => boolean
	) {
		if (!projectId || projectId !== this.activeProjectId) return null;
		const generation = this.generation;
		const expectedVisibleCount = this.currentItems.filter(matches).length;
		this.beginMutation();
		let response: VideoLocalizationTtsHistoryDeleteResponse;
		try {
			response = await this.client.deleteScope(projectId, request);
		} catch (error) {
			if (
				generation !== this.generation
				|| projectId !== this.activeProjectId
			) return null;
			this.completeMutation();
			let reconciled: HistoryItem[] | null;
			try {
				reconciled = await this.client.listAll(projectId);
			} catch {
				throw error;
			}
			if (reconciled === null) return null;
			if (!reconciled.some(matches)) {
				this.projectItems = this.projectItems.filter((item) => !matches(item));
				for (const [resultId, item] of this.segmentItems) {
					if (matches(item)) this.segmentItems.delete(resultId);
				}
				this.totalCount = Math.max(0, this.totalCount - expectedVisibleCount);
				this.loadedProjectCount = this.projectItems.length;
				this.publish();
				return {
					...emptyDeleteResponse(),
					removed_records: expectedVisibleCount
				};
			}
			throw error;
		}
		if (
			generation !== this.generation
			|| projectId !== this.activeProjectId
		) return null;
		const completedMutationEpoch = this.completeMutation();
		try {
			await this.readProjectPage(projectId, false);
		} catch {
			if (
				generation === this.generation
				&& projectId === this.activeProjectId
				&& completedMutationEpoch === this.mutationEpoch
			) {
				this.projectItems = this.projectItems.filter((item) => !matches(item));
				for (const [resultId, item] of this.segmentItems) {
					if (matches(item)) this.segmentItems.delete(resultId);
				}
				this.totalCount = Math.max(0, this.totalCount - expectedVisibleCount);
				this.loadedProjectCount = this.projectItems.length;
				this.publish();
			}
		}
		if (
			generation !== this.generation
			|| projectId !== this.activeProjectId
		) return null;
		return response;
	}

	private publish() {
		this.currentItems = mergeHistoryItems(
			this.projectItems,
			[...this.segmentItems.values()]
		);
		this.options.onItems(this.items);
		this.publishPageState();
	}

	private publishPageState() {
		this.options.onPageState?.({
			total: this.totalCount,
			loaded: this.loadedProjectCount,
			hasMore: this.hasMore,
			loading: this.loading
		});
	}
}

function mergeHistoryItems(primary: HistoryItem[], additional: HistoryItem[]) {
	const byId = new Map(primary.map((item) => [item.result_id, item]));
	for (const item of additional) byId.set(item.result_id, item);
	return [...byId.values()].sort((left, right) =>
		String(right.created_at).localeCompare(String(left.created_at))
	);
}

export function historyBelongsToSegment(
	item: HistoryItem,
	segmentId: string
) {
	const parameters = item.parameter_snapshot ?? {};
	const parameterIds = [
		parameters.video_localization_target_subtitle_ids,
		parameters.video_localization_source_cue_ids
	].flatMap((value) => Array.isArray(value) ? value.map(String) : []);
	return [
		item.segment_id,
		item.localized_subtitle_id,
		item.cue_id,
		...parameterIds
	].some((value) => value === segmentId);
}

function emptyDeleteResponse(): VideoLocalizationTtsHistoryDeleteResponse {
	return {
		removed_records: 0,
		cleanup_failures: 0
	};
}
