import type {
	VideoLocalizationCue,
	VideoLocalizationDraft,
	VideoLocalizationTimelineEditPatchRequest,
	VideoLocalizationTimelineEditPatchResponse,
	VideoLocalizationSubtitleCue,
	VideoLocalizationTimelineClip
} from '$lib/api/types';
import type { VideoLocalizationTrackState } from './studio-state';
import { isTimelineRuntimeClip } from './timeline-runtime-projection';

const TIMING_FIELDS = [
	'start_ms',
	'end_ms',
	'source_start_ms',
	'source_end_ms',
	'media_source_clip_id'
] as const;
const CLIP_FIELDS = [...TIMING_FIELDS, 'dub_lane'] as const;
const LANE_STATE_FIELDS = ['muted', 'solo', 'volume', 'label', 'locked'] as const;
const UI_STATE_FIELDS = ['disabled_media_tracks', 'discarded_tts_task_ids'] as const;
const SUBTITLE_RUNTIME_FIELDS = [
	'tts_result_id',
	'tts_audio_path',
	'tts_batch_task_id',
	'tts_batch_status',
	'tts_batch_error',
	'tts_attempted_at',
	'generated_duration_ms'
] as const;
const TIMELINE_CLIP_SNAPSHOT_FIELDS = [
	'track_id',
	'cue_id',
	'subtitle_id',
	'source_cue_ids',
	'candidate_id',
	...CLIP_FIELDS
] as const;

export function timelineClipGenerationIdentity(clip: VideoLocalizationTimelineClip) {
	return String(clip.generation_identity ?? clip.clip_id);
}

export function timelineClipExpectedEditableFields(clip: VideoLocalizationTimelineClip) {
	return {
		start_ms: clip.start_ms == null ? null : Math.round(clip.start_ms),
		end_ms: clip.end_ms == null ? null : Math.round(clip.end_ms),
		source_start_ms: clip.source_start_ms == null ? null : Math.round(clip.source_start_ms),
		source_end_ms: clip.source_end_ms == null ? null : Math.round(clip.source_end_ms),
		media_source_clip_id: clip.media_source_clip_id == null ? null : String(clip.media_source_clip_id),
		dub_lane: clip.dub_lane == null ? null : Math.max(0, Math.round(Number(clip.dub_lane)))
	};
}

export function timelineClipTimingChanged(
	left: VideoLocalizationTimelineClip,
	right: VideoLocalizationTimelineClip
) {
	return TIMING_FIELDS.some((field) => !storedValuesEqual(
		readStoredValue(left, field),
		readStoredValue(right, field)
	));
}

export type TimelineClipEditableField = (typeof CLIP_FIELDS)[number];
export type TimelineClipEditablePatch = Partial<{
	start_ms: number | null;
	end_ms: number | null;
	source_start_ms: number | null;
	source_end_ms: number | null;
	media_source_clip_id: string | null;
	dub_lane: number;
}>;

export type TimelineClipPatchCommand = {
	clipId: string;
	patch: TimelineClipEditablePatch;
	unset?: TimelineClipEditableField[];
};

export type TimelineDubLaneStatePatchCommand = {
	lane: string | number;
	patch?: Partial<VideoLocalizationTrackState>;
	unset?: Array<keyof VideoLocalizationTrackState>;
	remove?: boolean;
};

export type TimelineEditTransaction = {
	type: 'transaction';
	label?: string;
	replaceCues?: VideoLocalizationCue[];
	replaceLocalizedSubtitles?: VideoLocalizationSubtitleCue[];
	replaceTimelineClips?: VideoLocalizationTimelineClip[];
	clipPatches?: TimelineClipPatchCommand[];
	addClips?: Array<{ clip: VideoLocalizationTimelineClip; index?: number }>;
	deleteClipIds?: string[];
	dubLaneStatePatches?: TimelineDubLaneStatePatchCommand[];
	uiStatePatch?: Partial<Record<(typeof UI_STATE_FIELDS)[number], string[]>>;
};

export type TimelineEditCommandResult = {
	status: 'applied' | 'noop';
	revision: number;
};

export type TimelineDirtyField = 'timing' | 'lane';
export type TimelineDirtyFieldsByClipId = ReadonlyMap<string, ReadonlySet<TimelineDirtyField>>;

export type TimelineEditSavePacket = {
	packetId: string;
	revision: number;
	draft: VideoLocalizationDraft;
	addedClipIds: ReadonlySet<string>;
	deletedClipIds: ReadonlySet<string>;
	deletedClipIdentities: ReadonlyMap<string, string>;
	dirtyFieldsByClipId: TimelineDirtyFieldsByClipId;
	dirtyDubLaneStateKeys: ReadonlySet<string>;
	hasChanges: boolean;
};

export type TimelineCompactSavePacket = {
	packetId: string;
	revision: number;
	request: VideoLocalizationTimelineEditPatchRequest;
};

type StoredValue = {
	exists: boolean;
	value?: unknown;
};

type ClipFieldChange = {
	clipId: string;
	fields: Partial<Record<TimelineClipEditableField, StoredValue>>;
};

type LaneField = (typeof LANE_STATE_FIELDS)[number];

type DirtyLaneState = {
	record: boolean;
	fields: ReadonlySet<LaneField>;
};

type LaneFieldChange = {
	lane: string;
	fields: Partial<Record<LaneField, StoredValue>>;
};

type LaneRecordChange = {
	lane: string;
	state: StoredValue;
};

type UiStateField = (typeof UI_STATE_FIELDS)[number];

type StoredClip = {
	clip: VideoLocalizationTimelineClip;
	index: number;
};

type StoredEntity<T> = {
	id: string;
	index: number;
	previousId: string | null;
	nextId: string | null;
	value: T;
	ownedFields: string[];
};

type StoredCollection<T> = {
	upserts: StoredEntity<T>[];
	removeIds: string[];
	order: string[] | null;
};

type TimelinePatch = {
	cues: StoredCollection<VideoLocalizationCue> | null;
	localizedSubtitles: StoredCollection<VideoLocalizationSubtitleCue> | null;
	timelineClips: StoredCollection<VideoLocalizationTimelineClip> | null;
	clipFields: ClipFieldChange[];
	removeClipIds: string[];
	restoreClips: StoredClip[];
	laneFields: LaneFieldChange[];
	laneRecords: LaneRecordChange[];
	uiStateFields: Partial<Record<UiStateField, StoredValue>>;
	addDeletedClipIds: string[];
	removeDeletedClipIds: string[];
};

type HistoryEntry = {
	label: string;
	forward: TimelinePatch;
	inverse: TimelinePatch;
};

type PendingPatch = {
	sequence: number;
	patch: TimelinePatch;
};

type SaveCheckpoint = {
	maxSequence: number;
};

const EMPTY_PATCH = (): TimelinePatch => ({
	cues: null,
	localizedSubtitles: null,
	timelineClips: null,
	clipFields: [],
	removeClipIds: [],
	restoreClips: [],
	laneFields: [],
	laneRecords: [],
	uiStateFields: {},
	addDeletedClipIds: [],
	removeDeletedClipIds: []
});

export class TimelineEditController {
	private baseDraft: VideoLocalizationDraft;
	private currentDraft: VideoLocalizationDraft;
	private past: HistoryEntry[] = [];
	private future: HistoryEntry[] = [];
	private pending: PendingPatch[] = [];
	private deletedClipIds = new Set<string>();
	private revision = 0;
	private sequence = 0;
	private packetSequence = 0;
	private readonly checkpoints = new Map<string, SaveCheckpoint>();
	private uncertainCompactSave: TimelineCompactSavePacket | null = null;
	private readonly maxHistory: number;

	constructor(draft: VideoLocalizationDraft, options: { maxHistory?: number } = {}) {
		this.baseDraft = cloneDraft(draft);
		this.currentDraft = cloneDraft(draft);
		this.maxHistory = Math.max(1, Math.floor(options.maxHistory ?? 30));
	}

	get draft() {
		return cloneDraft(this.currentDraft);
	}

	get undoCount() {
		return this.past.length;
	}

	get redoCount() {
		return this.future.length;
	}

	get hasPendingChanges() {
		return this.pending.length > 0 || this.uncertainCompactSave !== null;
	}

	get deletedTimelineClipIds(): ReadonlySet<string> {
		return new Set(this.deletedClipIds);
	}

	get addedTimelineClipIds(): ReadonlySet<string> {
		const baseIds = new Set(this.baseDraft.timeline_clips.map((clip) => clip.clip_id));
		return new Set(
			this.currentDraft.timeline_clips
				.map((clip) => clip.clip_id)
				.filter((clipId) => !baseIds.has(clipId))
		);
	}

	dispatch(command: TimelineEditTransaction): TimelineEditCommandResult {
		const entry = buildHistoryEntry(this.currentDraft, command);
		if (!entry) return { status: 'noop', revision: this.revision };
		this.applyAndQueue(entry.forward);
		this.past = [...this.past, entry].slice(-this.maxHistory);
		this.future = [];
		return { status: 'applied', revision: this.revision };
	}

	adoptPersisted(
		command: TimelineEditTransaction,
		persistedDraft: VideoLocalizationDraft
	): TimelineEditCommandResult {
		const entry = buildHistoryEntry(this.currentDraft, command);
		const replacedClipIds = this.invalidateReplacedGenerations(persistedDraft);
		this.baseDraft = cloneDraft(persistedDraft);
		this.replayPending();
		if (
			!entry
			|| patchTouchesClipIds(entry.forward, replacedClipIds)
			|| patchTouchesClipIds(entry.inverse, replacedClipIds)
		) return { status: 'noop', revision: this.revision };
		this.past = [...this.past, entry].slice(-this.maxHistory);
		this.future = [];
		this.revision += 1;
		return { status: 'applied', revision: this.revision };
	}

	undo(): TimelineEditCommandResult {
		const entry = this.past.at(-1);
		if (!entry) return { status: 'noop', revision: this.revision };
		this.past = this.past.slice(0, -1);
		this.applyAndQueue(entry.inverse);
		this.future = [...this.future, entry].slice(-this.maxHistory);
		return { status: 'applied', revision: this.revision };
	}

	redo(): TimelineEditCommandResult {
		const entry = this.future.at(-1);
		if (!entry) return { status: 'noop', revision: this.revision };
		this.future = this.future.slice(0, -1);
		this.applyAndQueue(entry.forward);
		this.past = [...this.past, entry].slice(-this.maxHistory);
		return { status: 'applied', revision: this.revision };
	}

	mergeRefresh(serverDraft: VideoLocalizationDraft) {
		const replacedClipIds = this.invalidateReplacedGenerations(serverDraft);
		this.baseDraft = cloneDraft(serverDraft);
		this.replayPending();
		return replacedClipIds;
	}

	discardConflictingClipEdits(clipIds: ReadonlySet<string>) {
		const discardedClipIds = new Set<string>();
		if (!clipIds.size) return discardedClipIds;
		const remaining = this.pending.filter(({ patch }) => {
			if (!patchTouchesClipIds(patch, clipIds)) return true;
			for (const clipId of clipIdsTouchedByPatch(patch)) discardedClipIds.add(clipId);
			return false;
		});
		if (!discardedClipIds.size) return discardedClipIds;
		this.pending = remaining;
		// A stale command must not remain reachable through undo/redo after the
		// server has kept a newer edit for the same clip.
		this.past = [];
		this.future = [];
		this.replayPending();
		return discardedClipIds;
	}

	private invalidateReplacedGenerations(serverDraft: VideoLocalizationDraft) {
		const previousById = new Map(this.baseDraft.timeline_clips.map((clip) => [clip.clip_id, clip]));
		const replacedClipIds = new Set(
			serverDraft.timeline_clips
				.filter((clip) => {
					const previous = previousById.get(clip.clip_id);
					return Boolean(
						previous
						&& timelineClipGenerationIdentity(previous) !== timelineClipGenerationIdentity(clip)
					);
				})
				.map((clip) => clip.clip_id)
		);
		if (replacedClipIds.size) {
			this.pending = this.pending.filter(({ patch }) => !patchTouchesClipIds(patch, replacedClipIds));
			// Undoing an edit against a replaced audio generation could resurrect
			// the old take. External replacement starts a new local edit history.
			this.past = [];
			this.future = [];
		}
		return replacedClipIds;
	}

	synchronizeExternalDraft(externalDraft: VideoLocalizationDraft) {
		if (this.invalidateReplacedGenerations(externalDraft).size) this.replayPending();
		if (!this.pending.length) {
			this.baseDraft = cloneDraft(externalDraft);
			this.currentDraft = cloneDraft(externalDraft);
			this.deletedClipIds = new Set();
			return;
		}
		const cueRebase = collectionChanges(
			this.baseDraft.cues,
			this.currentDraft.cues,
			(item) => item.cue_id,
			SUBTITLE_RUNTIME_FIELDS
		);
		const localizedSubtitleRebase = collectionChanges(
			this.baseDraft.localized_subtitles,
			this.currentDraft.localized_subtitles,
			(item) => item.subtitle_id,
			SUBTITLE_RUNTIME_FIELDS
		);
		const dirtyFields = dirtyClipFields(this.baseDraft, this.currentDraft);
		const nextBase = cloneDraft(externalDraft);
		if (cueRebase) {
			nextBase.cues = applyStoredCollection(
				nextBase.cues,
				cueRebase.inverse,
				(item) => item.cue_id
			);
		}
		if (localizedSubtitleRebase) {
			nextBase.localized_subtitles = applyStoredCollection(
				nextBase.localized_subtitles,
				localizedSubtitleRebase.inverse,
				(item) => item.subtitle_id
			);
		}
		const locallyAddedClipIds = new Set(
			this.pending.flatMap((item) => item.patch.restoreClips.map((stored) => stored.clip.clip_id))
				.filter((clipId) => !this.baseDraft.timeline_clips.some((clip) => clip.clip_id === clipId))
		);
		nextBase.timeline_clips = nextBase.timeline_clips.filter(
			(clip) => !locallyAddedClipIds.has(clip.clip_id)
		);
		const nextById = new Map(nextBase.timeline_clips.map((clip) => [clip.clip_id, clip]));
		const baseById = new Map(this.baseDraft.timeline_clips.map((clip) => [clip.clip_id, clip]));
		for (const clipId of this.deletedClipIds) {
			if (nextById.has(clipId)) continue;
			const baseClip = baseById.get(clipId);
			if (baseClip) nextBase.timeline_clips.push(cloneClip(baseClip));
		}
		for (const [clipId, fields] of dirtyFields) {
			const baseClip = baseById.get(clipId);
			const nextClip = nextBase.timeline_clips.find((clip) => clip.clip_id === clipId);
			if (!baseClip || !nextClip) continue;
			const target = nextClip as Record<string, unknown>;
			if (fields.has('timing')) {
				for (const field of TIMING_FIELDS) {
					applyStoredFields(target, { [field]: readStoredValue(baseClip, field) });
				}
			}
			if (fields.has('lane')) {
				applyStoredFields(target, { dub_lane: readStoredValue(baseClip, 'dub_lane') });
			}
		}
		const baseLaneStates = readLaneStates(this.baseDraft);
		const nextLaneStates = readLaneStates(nextBase);
		for (const [lane, dirty] of dirtyLaneStates(this.baseDraft, this.currentDraft)) {
			if (dirty.record) {
				if (lane in baseLaneStates) nextLaneStates[lane] = cloneLaneState(baseLaneStates[lane]);
				else delete nextLaneStates[lane];
				continue;
			}
			const nextState = { ...(nextLaneStates[lane] ?? {}) } as Record<string, unknown>;
			const baseState = baseLaneStates[lane] ?? {};
			for (const field of dirty.fields) {
				applyStoredFields(nextState, { [field]: readStoredValue(baseState, field) });
			}
			nextLaneStates[lane] = nextState as VideoLocalizationTrackState;
		}
		nextBase.ui_state = {
			...nextBase.ui_state,
			dub_lane_states: nextLaneStates
		};
		for (const field of dirtyUiStateKeys(this.baseDraft, this.currentDraft)) {
			const baseValue = readStoredValue(this.baseDraft.ui_state ?? {}, field);
			const nextUiState = nextBase.ui_state as Record<string, unknown>;
			applyStoredFields(nextUiState, { [field]: baseValue });
		}
		this.baseDraft = nextBase;
		this.replayPending();
	}

	prepareSave(): TimelineEditSavePacket {
		const packetId = `timeline-save-${++this.packetSequence}`;
		const maxSequence = this.pending.at(-1)?.sequence ?? 0;
		this.checkpoints.set(packetId, { maxSequence });
		return {
			packetId,
			revision: this.revision,
			draft: cloneDraft(this.currentDraft),
			addedClipIds: new Set(
				this.currentDraft.timeline_clips
					.map((clip) => clip.clip_id)
					.filter((clipId) => !this.baseDraft.timeline_clips.some((clip) => clip.clip_id === clipId))
			),
			deletedClipIds: new Set(this.deletedClipIds),
			deletedClipIdentities: new Map(
				this.baseDraft.timeline_clips
					.filter((clip) => this.deletedClipIds.has(clip.clip_id))
					.map((clip) => [clip.clip_id, timelineClipGenerationIdentity(clip)])
			),
			dirtyFieldsByClipId: dirtyClipFields(this.baseDraft, this.currentDraft),
			dirtyDubLaneStateKeys: dirtyLaneStateKeys(this.baseDraft, this.currentDraft),
			hasChanges: this.pending.length > 0
		};
	}

	prepareCompactSave(): TimelineCompactSavePacket | null {
		// A timeout is not proof that the server rejected the write. Resolve that
		// exact transaction first; newer edits remain behind its checkpoint.
		if (this.uncertainCompactSave) return cloneValue(this.uncertainCompactSave);
		if (!this.pending.length) return null;
		const touchedLanes = new Set(this.pending.flatMap(({ patch }) =>
			[...patch.laneFields, ...patch.laneRecords].map(({ lane }) => lane)
		));
		const baseClips = new Map(this.baseDraft.timeline_clips.map((clip) => [clip.clip_id, clip]));
		const currentClips = new Map(this.currentDraft.timeline_clips.map((clip) => [clip.clip_id, clip]));
		const addedClipIds = (
			[...currentClips.keys()].filter((clipId) => !baseClips.has(clipId))
		);
		const deletedClipIds = [...baseClips.keys()].filter((clipId) => !currentClips.has(clipId));
		const addedClips: NonNullable<VideoLocalizationTimelineEditPatchRequest['added_clips']> = [...addedClipIds].map((clipId) => {
			const clip = currentClips.get(clipId)!;
			const mediaSourceClipId = String(clip.media_source_clip_id ?? '');
			if (!mediaSourceClipId) throw new Error(`Missing timeline media source: ${clipId}`);
			return {
				clip_id: clipId,
				media_source_clip_id: mediaSourceClipId,
				start_ms: Math.max(0, Math.round(clip.start_ms ?? 0)),
				end_ms: Math.max(1, Math.round(clip.end_ms ?? 1)),
				source_start_ms: Math.max(0, Math.round(clip.source_start_ms ?? 0)),
				source_end_ms: Math.max(1, Math.round(clip.source_end_ms ?? 1)),
				dub_lane: Math.max(0, Math.round(Number(clip.dub_lane ?? 0)))
			};
		});
		const clipPatches: VideoLocalizationTimelineEditPatchRequest['clip_patches'] = [...baseClips]
			.flatMap(([clipId, baseClip]) => {
				const clip = currentClips.get(clipId)!;
				if (!clip) return [];
				const fields = CLIP_FIELDS.filter((field) => !storedValuesEqual(
					readStoredValue(baseClip, field), readStoredValue(clip, field)
				));
				return fields.length ? [{
					clip_id: clipId,
					expected_generation_identity: timelineClipGenerationIdentity(baseClip),
					expected_editable_fields: timelineClipExpectedEditableFields(baseClip),
					...Object.fromEntries([...fields].map((field) => [
						field,
						Object.prototype.hasOwnProperty.call(clip, field)
							? (clip as Record<string, unknown>)[field]
							: null
					]))
				}] : [];
		});
		const baseLaneStates = readLaneStates(this.baseDraft);
		const laneStates = readLaneStates(this.currentDraft);
		const lanePatches = [...touchedLanes]
			.filter((lane) => !laneStateRecordsEqual(baseLaneStates, laneStates, lane))
			.map((lane) => {
				const numericLane = Number(lane);
				const state = laneStates[lane];
				if (!state) return { lane: numericLane, remove: true };
				return Object.fromEntries([
					['lane', numericLane],
					...LANE_STATE_FIELDS
						.filter((field) => Object.prototype.hasOwnProperty.call(state, field))
						.map((field) => [field, state[field]])
				]);
			});
		const dirtyUiStateFields = dirtyUiStateKeys(this.baseDraft, this.currentDraft);
		const cueCollectionChange = collectionsEqual(this.baseDraft.cues, this.currentDraft.cues)
			? undefined
			: {
					expected: this.baseDraft.cues.map(cloneValue),
					desired: this.currentDraft.cues.map(cloneValue)
				};
		const localizedSubtitleCollectionChange = collectionsEqual(
			this.baseDraft.localized_subtitles,
			this.currentDraft.localized_subtitles
		)
			? undefined
			: {
					expected: this.baseDraft.localized_subtitles.map(cloneValue),
					desired: this.currentDraft.localized_subtitles.map(cloneValue)
			};
		// Clip deletion and its task-discard decision are one transaction. Keep
		// this field explicit even when both snapshots already contain [].
		if (deletedClipIds.length) dirtyUiStateFields.add('discarded_tts_task_ids');
		const uiStatePatch = Object.fromEntries(
			[...dirtyUiStateFields].map((field) => [
				field,
				[...((this.currentDraft.ui_state?.[field] as string[] | undefined) ?? [])]
			])
		);
		if (
			!clipPatches.length
			&& !addedClips.length
			&& !deletedClipIds.length
			&& !lanePatches.length
			&& !cueCollectionChange
			&& !localizedSubtitleCollectionChange
			&& !Object.keys(uiStatePatch).length
		) {
			return null;
		}
		const packetId = `timeline-save-${++this.packetSequence}`;
		const maxSequence = this.pending.at(-1)?.sequence ?? 0;
		this.checkpoints.set(packetId, { maxSequence });
		return {
			packetId,
			revision: this.revision,
			request: {
				schema_version: 'timeline-edit-patch-v2',
				request_id: crypto.randomUUID(),
				clip_patches: clipPatches,
				added_clips: addedClips,
				...(cueCollectionChange ? { cue_collection_change: cueCollectionChange } : {}),
				...(localizedSubtitleCollectionChange
					? { localized_subtitle_collection_change: localizedSubtitleCollectionChange }
					: {}),
				dub_lane_state_patches: lanePatches as VideoLocalizationTimelineEditPatchRequest['dub_lane_state_patches'],
				ui_state_patch: uiStatePatch,
				deleted_clips: deletedClipIds.map((clipId) => {
					const baseClip = baseClips.get(clipId)!;
					return {
						clip_id: clipId,
						expected_generation_identity: timelineClipGenerationIdentity(baseClip),
						expected_editable_fields: timelineClipExpectedEditableFields(baseClip)
					};
				})
			}
		};
	}

	/**
	 * Drop queued operations only when their complete controller-owned state is
	 * already identical to the latest persisted baseline. An outstanding save
	 * checkpoint makes that comparison unsafe: its acknowledgement may advance
	 * the baseline and turn a later undo into an inverse write.
	 */
	settlePendingNoop() {
		if (this.uncertainCompactSave) return false;
		if (!this.pending.length) return true;
		if (this.checkpoints.size || !timelineOwnedStateEqual(this.baseDraft, this.currentDraft)) {
			return false;
		}
		this.pending = [];
		this.deletedClipIds = new Set();
		return true;
	}

	acknowledgeSave(packetId: string, savedDraft: VideoLocalizationDraft) {
		const checkpoint = this.checkpoints.get(packetId);
		if (!checkpoint) throw new Error(`Unknown timeline save packet: ${packetId}`);
		this.checkpoints.delete(packetId);
		this.invalidateReplacedGenerations(savedDraft);
		this.pending = this.pending.filter((item) => item.sequence > checkpoint.maxSequence);
		this.baseDraft = cloneDraft(savedDraft);
		this.replayPending();
	}

	acknowledgeCompactSave(
		packetId: string,
		receipt: VideoLocalizationTimelineEditPatchResponse,
		options: { applyReceipt?: boolean } = {}
	) {
		const checkpoint = this.checkpoints.get(packetId);
		if (!checkpoint) throw new Error(`Unknown timeline save packet: ${packetId}`);
		this.checkpoints.delete(packetId);
		if (this.uncertainCompactSave?.packetId === packetId) this.uncertainCompactSave = null;
		const applyReceipt = options.applyReceipt !== false;
		let nextBase = this.baseDraft;
		let deletedClipIds = new Set<string>();
		const remaining: PendingPatch[] = [];
		for (const item of this.pending) {
			if (item.sequence > checkpoint.maxSequence) {
				remaining.push(item);
				continue;
			}
			if (applyReceipt) {
				const applied = applyPatch(nextBase, deletedClipIds, item.patch);
				nextBase = applied.draft;
				deletedClipIds = applied.deletedClipIds;
			}
		}
		this.pending = remaining;
		// A replay receipt can legitimately describe an older commit than a full
		// workspace already consumed by this browser. The receipt still confirms
		// this checkpoint, but the refreshed baseline is authoritative.
		if (!applyReceipt) {
			this.baseDraft = nextBase;
			this.replayPending();
			return;
		}
		const receiptClipsById = new Map(receipt.timeline_clips.map((clip) => [clip.clip_id, clip]));
		const authoritativeTimelineClips = nextBase.timeline_clips.map((clip) =>
			receiptClipsById.has(clip.clip_id)
				? cloneClip(receiptClipsById.get(clip.clip_id)!)
				: cloneClip(clip)
		);
		const knownClipIds = new Set(authoritativeTimelineClips.map((clip) => clip.clip_id));
		for (const clip of receipt.timeline_clips) {
			if (!knownClipIds.has(clip.clip_id)) authoritativeTimelineClips.push(cloneClip(clip));
		}
		this.baseDraft = {
			...nextBase,
			updated_at: receipt.updated_at,
			timeline_clips: authoritativeTimelineClips,
			cues: receipt.cues == null ? nextBase.cues : receipt.cues.map(cloneValue),
			localized_subtitles: receipt.localized_subtitles == null
				? nextBase.localized_subtitles
				: receipt.localized_subtitles.map(cloneValue),
			ui_state: {
				...nextBase.ui_state,
				dub_lane_states: {
					...readLaneStates(nextBase),
					...cloneValue(receipt.dub_lane_states)
				}
			}
		};
		this.replayPending();
	}

	retainCompactSaveForRetry(packet: TimelineCompactSavePacket) {
		if (!this.checkpoints.has(packet.packetId)) throw new Error(`Unknown timeline save packet: ${packet.packetId}`);
		this.uncertainCompactSave = cloneValue(packet);
	}

	rejectSave(packetId: string) {
		this.checkpoints.delete(packetId);
		if (this.uncertainCompactSave?.packetId === packetId) this.uncertainCompactSave = null;
	}

	private applyAndQueue(patch: TimelinePatch) {
		const applied = applyPatch(this.currentDraft, this.deletedClipIds, patch);
		this.currentDraft = applied.draft;
		this.deletedClipIds = applied.deletedClipIds;
		this.revision += 1;
		this.pending.push({ sequence: ++this.sequence, patch });
	}

	private replayPending() {
		let draft = cloneDraft(this.baseDraft);
		let deletedClipIds = new Set<string>();
		for (const item of this.pending) {
			const applied = applyPatch(draft, deletedClipIds, item.patch);
			draft = applied.draft;
			deletedClipIds = applied.deletedClipIds;
		}
		this.currentDraft = draft;
		this.deletedClipIds = deletedClipIds;
	}
}

function buildHistoryEntry(
	draft: VideoLocalizationDraft,
	command: TimelineEditTransaction
): HistoryEntry | null {
	if (command.type !== 'transaction') throw new Error(`Unsupported timeline command: ${String(command.type)}`);
	const forward = EMPTY_PATCH();
	const inverse = EMPTY_PATCH();
	if (command.replaceCues) {
		const replacement = collectionChanges(
			draft.cues,
			command.replaceCues,
			(item) => item.cue_id,
			SUBTITLE_RUNTIME_FIELDS
		);
		if (replacement) {
			forward.cues = replacement.forward;
			inverse.cues = replacement.inverse;
		}
	}
	if (command.replaceLocalizedSubtitles) {
		const replacement = collectionChanges(
			draft.localized_subtitles,
			command.replaceLocalizedSubtitles,
			(item) => item.subtitle_id,
			SUBTITLE_RUNTIME_FIELDS
		);
		if (replacement) {
			forward.localizedSubtitles = replacement.forward;
			inverse.localizedSubtitles = replacement.inverse;
		}
	}
	if (command.replaceTimelineClips) {
		if (
			command.clipPatches?.length
			|| command.addClips?.length
			|| command.deleteClipIds?.length
		) {
			throw new Error('A timeline transaction cannot replace and granularly modify timeline clips');
		}
		const replacementClips = command.replaceTimelineClips.filter(
			(clip) => !isTimelineRuntimeClip(clip)
		);
		const replacement = collectionChanges(
			draft.timeline_clips,
			replacementClips,
			(item) => item.clip_id,
			[],
			TIMELINE_CLIP_SNAPSHOT_FIELDS
		);
		if (replacement) {
			forward.timelineClips = replacement.forward;
			inverse.timelineClips = replacement.inverse;
			const beforeIds = new Set(draft.timeline_clips.map((clip) => clip.clip_id));
			const afterIds = new Set(replacementClips.map((clip) => clip.clip_id));
			forward.addDeletedClipIds.push(...[...beforeIds].filter((clipId) => !afterIds.has(clipId)));
			forward.removeDeletedClipIds.push(...[...afterIds].filter((clipId) => !beforeIds.has(clipId)));
			inverse.addDeletedClipIds.push(...[...afterIds].filter((clipId) => !beforeIds.has(clipId)));
			inverse.removeDeletedClipIds.push(...[...beforeIds].filter((clipId) => !afterIds.has(clipId)));
		}
	}
	const patchedIds = new Set<string>();
	const deletedIds = new Set((command.deleteClipIds ?? []).filter(Boolean));
	const addedIds = new Set<string>();

	for (const request of command.clipPatches ?? []) {
		if (!request.clipId) throw new Error('Timeline clip patch requires clipId');
		if (patchedIds.has(request.clipId)) throw new Error(`Duplicate timeline clip patch: ${request.clipId}`);
		if (deletedIds.has(request.clipId)) throw new Error(`A timeline transaction cannot patch and delete ${request.clipId}`);
		patchedIds.add(request.clipId);
		const clip = draft.timeline_clips.find((item) => item.clip_id === request.clipId);
		if (!clip) continue;
		const changes = clipFieldChanges(clip, request);
		if (!changes) continue;
		forward.clipFields.push({ clipId: request.clipId, fields: changes.after });
		inverse.clipFields.push({ clipId: request.clipId, fields: changes.before });
	}

	for (const clipId of deletedIds) {
		const index = draft.timeline_clips.findIndex((clip) => clip.clip_id === clipId);
		if (index < 0) continue;
		const clip = cloneClip(draft.timeline_clips[index]);
		forward.removeClipIds.push(clipId);
		forward.addDeletedClipIds.push(clipId);
		inverse.restoreClips.push({ clip, index });
		inverse.removeDeletedClipIds.push(clipId);
	}

	for (const request of command.addClips ?? []) {
		if (isTimelineRuntimeClip(request.clip)) continue;
		const clipId = request.clip.clip_id;
		if (!clipId) throw new Error('Timeline added clip requires clip_id');
		if (addedIds.has(clipId)) throw new Error(`Duplicate added timeline clip: ${clipId}`);
		if (patchedIds.has(clipId) || deletedIds.has(clipId)) {
			throw new Error(`A timeline transaction cannot add and modify ${clipId}`);
		}
		addedIds.add(clipId);
		if (draft.timeline_clips.some((clip) => clip.clip_id === clipId)) continue;
		forward.restoreClips.push({
			clip: cloneClip(request.clip),
			index: Math.max(0, Math.min(request.index ?? draft.timeline_clips.length, draft.timeline_clips.length))
		});
		inverse.removeClipIds.push(clipId);
	}

	const lanePatches = new Set<string>();
	const laneStates = readLaneStates(draft);
	for (const request of command.dubLaneStatePatches ?? []) {
		const lane = normalizeLaneKey(request.lane);
		if (lanePatches.has(lane)) throw new Error(`Duplicate dub lane state patch: ${lane}`);
		lanePatches.add(lane);
		if (request.remove) {
			if (!(lane in laneStates)) continue;
			forward.laneRecords.push({ lane, state: { exists: false } });
			inverse.laneRecords.push({ lane, state: { exists: true, value: cloneLaneState(laneStates[lane]) } });
			continue;
		}
		const changes = laneFieldChanges(laneStates[lane] ?? {}, request);
		if (!changes) continue;
		forward.laneFields.push({ lane, fields: changes.after });
		inverse.laneFields.push({ lane, fields: changes.before });
	}
	for (const [field, value] of Object.entries(command.uiStatePatch ?? {})) {
		if (!UI_STATE_FIELDS.includes(field as UiStateField)) {
			throw new Error(`Unsupported timeline UI state field: ${field}`);
		}
		const typedField = field as UiStateField;
		const previous = readStoredValue(draft.ui_state ?? {}, typedField);
		const next = { exists: true, value: [...(value ?? [])] };
		if (storedValuesEqual(previous, next)) continue;
		forward.uiStateFields[typedField] = next;
		inverse.uiStateFields[typedField] = previous;
	}

	if (patchIsEmpty(forward)) return null;
	return {
		label: command.label?.trim() || '时间线编辑',
		forward,
		inverse
	};
}

function collectionChanges<T extends object>(
	before: T[],
	after: T[],
	identity: (item: T) => string,
	runtimeFields: readonly string[],
	fixedOwnedFields?: readonly string[]
) {
	assertUniqueCollectionIds(before, identity);
	assertUniqueCollectionIds(after, identity);
	if (collectionsEqual(before, after)) return null;
	const beforeById = new Map(before.map((item) => [identity(item), item]));
	const afterById = new Map(after.map((item) => [identity(item), item]));
	return {
		forward: storedCollection(
			before,
			after,
			beforeById,
			identity,
			runtimeFields,
			fixedOwnedFields,
			true
		),
		inverse: storedCollection(
			after,
			before,
			afterById,
			identity,
			runtimeFields,
			fixedOwnedFields,
			false
		)
	};
}

function storedCollection<T extends object>(
	source: T[],
	target: T[],
	otherById: ReadonlyMap<string, T>,
	identity: (item: T) => string,
	runtimeFields: readonly string[],
	fixedOwnedFields: readonly string[] | undefined,
	ownChangedRuntimeFields: boolean
): StoredCollection<T> {
	const targetIds = new Set(target.map(identity));
	const sourceIds = source.map(identity);
	const targetIdList = target.map(identity);
	const commonSourceOrder = sourceIds.filter((id) => targetIds.has(id));
	const commonTargetOrder = targetIdList.filter((id) => otherById.has(id));
	return {
		removeIds: sourceIds.filter((id) => !targetIds.has(id)),
		order: collectionsEqual(commonSourceOrder, commonTargetOrder)
			? null
			: targetIdList,
		upserts: target.flatMap((item, index) => {
			const other = otherById.get(identity(item));
			if (other && JSON.stringify(other) === JSON.stringify(item)) return [];
			const itemRecord = item as Record<string, unknown>;
			const otherRecord = other as Record<string, unknown> | undefined;
			const candidateFields = fixedOwnedFields
				? [...fixedOwnedFields]
				: [...new Set([
						...Object.keys(itemRecord),
						...Object.keys(otherRecord ?? {})
					].filter((field) => !runtimeFields.includes(field)))];
			const ownedFields = otherRecord
				? candidateFields.filter((field) => !storedValuesEqual(
						readStoredValue(item, field),
						readStoredValue(otherRecord, field)
					))
				: candidateFields;
			if (ownChangedRuntimeFields && otherRecord) {
				for (const field of runtimeFields) {
					if (!storedValuesEqual(readStoredValue(item, field), readStoredValue(otherRecord, field))) {
						ownedFields.push(field);
					}
				}
			}
			return [{
				id: identity(item),
				index,
				previousId: index > 0 ? identity(target[index - 1]) : null,
				nextId: index + 1 < target.length ? identity(target[index + 1]) : null,
				value: cloneValue(item),
				ownedFields: [...new Set(ownedFields)]
			}];
		})
	};
}

function assertUniqueCollectionIds<T>(items: T[], identity: (item: T) => string) {
	const ids = new Set<string>();
	for (const item of items) {
		const id = identity(item);
		if (!id) throw new Error('Timeline collection item requires a stable id');
		if (ids.has(id)) throw new Error(`Duplicate timeline collection item: ${id}`);
		ids.add(id);
	}
}

function collectionsEqual<T>(left: T[], right: T[]) {
	return JSON.stringify(left) === JSON.stringify(right);
}

function laneStateRecordsEqual(
	left: Record<string, VideoLocalizationTrackState>,
	right: Record<string, VideoLocalizationTrackState>,
	lane: string
) {
	if ((lane in left) !== (lane in right)) return false;
	if (!(lane in left)) return true;
	return LANE_STATE_FIELDS.every((field) => storedValuesEqual(
		readStoredValue(left[lane], field),
		readStoredValue(right[lane], field)
	));
}

function timelineOwnedStateEqual(left: VideoLocalizationDraft, right: VideoLocalizationDraft) {
	if (!collectionsEqual(left.cues, right.cues)) return false;
	if (!collectionsEqual(left.localized_subtitles, right.localized_subtitles)) return false;
	if (collectionChanges(
		left.timeline_clips,
		right.timeline_clips,
		(item) => item.clip_id,
		[],
		TIMELINE_CLIP_SNAPSHOT_FIELDS
	)) return false;
	const leftLaneStates = readLaneStates(left);
	const rightLaneStates = readLaneStates(right);
	const lanes = new Set([...Object.keys(leftLaneStates), ...Object.keys(rightLaneStates)]);
	if ([...lanes].some((lane) => !laneStateRecordsEqual(leftLaneStates, rightLaneStates, lane))) {
		return false;
	}
	return UI_STATE_FIELDS.every((field) => storedValuesEqual(
		readStoredValue(left.ui_state ?? {}, field),
		readStoredValue(right.ui_state ?? {}, field)
	));
}

function clipFieldChanges(clip: VideoLocalizationTimelineClip, request: TimelineClipPatchCommand) {
	const patch = request.patch as Record<string, unknown>;
	const requestedFields = new Set<TimelineClipEditableField>();
	for (const field of Object.keys(patch)) {
		if (!CLIP_FIELDS.includes(field as TimelineClipEditableField)) {
			throw new Error(`Unsupported timeline clip field: ${field}`);
		}
		requestedFields.add(field as TimelineClipEditableField);
	}
	for (const field of request.unset ?? []) requestedFields.add(field);
	const before: Partial<Record<TimelineClipEditableField, StoredValue>> = {};
	const after: Partial<Record<TimelineClipEditableField, StoredValue>> = {};
	for (const field of requestedFields) {
		const previous = readStoredValue(clip, field);
		const next = request.unset?.includes(field)
			? { exists: false }
			: { exists: true, value: patch[field] };
		if (storedValuesEqual(previous, next)) continue;
		before[field] = previous;
		after[field] = next;
	}
	return Object.keys(after).length ? { before, after } : null;
}

function laneFieldChanges(
	state: Partial<VideoLocalizationTrackState>,
	request: TimelineDubLaneStatePatchCommand
) {
	const patch = (request.patch ?? {}) as Record<string, unknown>;
	const requestedFields = new Set<LaneField>();
	for (const field of Object.keys(patch)) {
		if (!LANE_STATE_FIELDS.includes(field as LaneField)) throw new Error(`Unsupported dub lane state field: ${field}`);
		requestedFields.add(field as LaneField);
	}
	for (const field of request.unset ?? []) {
		if (!LANE_STATE_FIELDS.includes(field as LaneField)) throw new Error(`Unsupported dub lane state field: ${String(field)}`);
		requestedFields.add(field as LaneField);
	}
	const before: Partial<Record<LaneField, StoredValue>> = {};
	const after: Partial<Record<LaneField, StoredValue>> = {};
	for (const field of requestedFields) {
		const previous = readStoredValue(state, field);
		const next = request.unset?.includes(field)
			? { exists: false }
			: { exists: true, value: patch[field] };
		if (storedValuesEqual(previous, next)) continue;
		before[field] = previous;
		after[field] = next;
	}
	return Object.keys(after).length ? { before, after } : null;
}

function applyPatch(
	source: VideoLocalizationDraft,
	currentDeletedClipIds: ReadonlySet<string>,
	patch: TimelinePatch
) {
	let clips = patch.timelineClips
		? applyStoredCollection(
				source.timeline_clips,
				patch.timelineClips,
				(item) => item.clip_id
			).map(cloneClip)
		: source.timeline_clips.map(cloneClip);
	if (patch.removeClipIds.length) {
		const removed = new Set(patch.removeClipIds);
		clips = clips.filter((clip) => !removed.has(clip.clip_id));
	}
	for (const restored of [...patch.restoreClips].sort((left, right) => left.index - right.index)) {
		if (clips.some((clip) => clip.clip_id === restored.clip.clip_id)) continue;
		clips.splice(Math.max(0, Math.min(restored.index, clips.length)), 0, cloneClip(restored.clip));
	}
	for (const change of patch.clipFields) {
		const index = clips.findIndex((clip) => clip.clip_id === change.clipId);
		if (index < 0) continue;
		const next = { ...clips[index] } as Record<string, unknown>;
		applyStoredFields(next, change.fields);
		clips[index] = next as VideoLocalizationTimelineClip;
	}

	const laneStates = readLaneStates(source);
	for (const change of patch.laneRecords) {
		if (!change.state.exists) delete laneStates[change.lane];
		else laneStates[change.lane] = cloneLaneState(change.state.value as Partial<VideoLocalizationTrackState>);
	}
	for (const change of patch.laneFields) {
		const next = { ...(laneStates[change.lane] ?? {}) } as Record<string, unknown>;
		applyStoredFields(next, change.fields);
		laneStates[change.lane] = next as VideoLocalizationTrackState;
	}
	const uiState = { ...source.ui_state } as Record<string, unknown>;
	applyStoredFields(uiState, patch.uiStateFields);

	const deletedClipIds = new Set(currentDeletedClipIds);
	for (const clipId of patch.addDeletedClipIds) deletedClipIds.add(clipId);
	for (const clipId of patch.removeDeletedClipIds) deletedClipIds.delete(clipId);
	return {
		draft: {
			...source,
			timeline_clips: clips,
			cues: patch.cues
				? applyStoredCollection(source.cues, patch.cues, (item) => item.cue_id)
				: source.cues.map(cloneValue),
			localized_subtitles: patch.localizedSubtitles
				? applyStoredCollection(
						source.localized_subtitles,
						patch.localizedSubtitles,
						(item) => item.subtitle_id
					)
				: source.localized_subtitles.map(cloneValue),
			ui_state: {
				...uiState,
				dub_lane_states: laneStates
			}
		},
		deletedClipIds
	};
}

function applyStoredCollection<T extends object>(
	current: T[],
	stored: StoredCollection<T>,
	identity: (item: T) => string
) {
	const removedIds = new Set(stored.removeIds);
	let result = current.filter((item) => !removedIds.has(identity(item))).map(cloneValue);
	for (const entry of [...stored.upserts].sort((left, right) => left.index - right.index)) {
		const currentIndex = result.findIndex((item) => identity(item) === entry.id);
		if (currentIndex < 0) {
			const previousIndex = entry.previousId
				? result.findIndex((item) => identity(item) === entry.previousId)
				: -1;
			const nextIndex = entry.nextId
				? result.findIndex((item) => identity(item) === entry.nextId)
				: -1;
			const insertionIndex = previousIndex >= 0
				? previousIndex + 1
				: nextIndex >= 0
					? nextIndex
					: Math.max(0, Math.min(entry.index, result.length));
			result.splice(
				insertionIndex,
				0,
				cloneValue(entry.value)
			);
			continue;
		}
		const next = cloneValue(result[currentIndex]) as Record<string, unknown>;
		const storedValue = entry.value as Record<string, unknown>;
		for (const field of entry.ownedFields) {
			if (Object.prototype.hasOwnProperty.call(storedValue, field)) {
				next[field] = cloneValue(storedValue[field]);
			} else {
				delete next[field];
			}
		}
		result[currentIndex] = next as T;
	}
	if (stored.order) {
		const rank = new Map(stored.order.map((id, index) => [id, index]));
		result = result
			.map((item, index) => ({ item, index }))
			.sort((left, right) => {
				const leftRank = rank.get(identity(left.item));
				const rightRank = rank.get(identity(right.item));
				if (leftRank === undefined && rightRank === undefined) return left.index - right.index;
				if (leftRank === undefined) return 1;
				if (rightRank === undefined) return -1;
				return leftRank - rightRank;
			})
			.map(({ item }) => item);
	}
	return result;
}

function dirtyClipFields(
	base: VideoLocalizationDraft,
	current: VideoLocalizationDraft
): Map<string, Set<TimelineDirtyField>> {
	const baseById = new Map(base.timeline_clips.map((clip) => [clip.clip_id, clip]));
	const currentById = new Map(current.timeline_clips.map((clip) => [clip.clip_id, clip]));
	const result = new Map<string, Set<TimelineDirtyField>>();
	for (const clipId of new Set([...baseById.keys(), ...currentById.keys()])) {
		const before = baseById.get(clipId);
		const after = currentById.get(clipId);
		const fields = new Set<TimelineDirtyField>();
		if (!before || !after || TIMING_FIELDS.some((field) => !storedValuesEqual(
			readStoredValue(before, field),
			readStoredValue(after, field)
		))) fields.add('timing');
		if (!before || !after || !storedValuesEqual(
			readStoredValue(before, 'dub_lane'),
			readStoredValue(after, 'dub_lane')
		)) fields.add('lane');
		if (fields.size) result.set(clipId, fields);
	}
	return result;
}

function dirtyLaneStateKeys(base: VideoLocalizationDraft, current: VideoLocalizationDraft) {
	return new Set(dirtyLaneStates(base, current).keys());
}

function dirtyLaneStates(base: VideoLocalizationDraft, current: VideoLocalizationDraft) {
	const before = readLaneStates(base);
	const after = readLaneStates(current);
	const dirty = new Map<string, DirtyLaneState>();
	for (const lane of new Set([...Object.keys(before), ...Object.keys(after)])) {
		if (!before[lane] || !after[lane]) {
			if (before[lane] !== after[lane]) dirty.set(lane, { record: true, fields: new Set() });
			continue;
		}
		const fields = new Set<LaneField>();
		for (const field of LANE_STATE_FIELDS) {
			if (!storedValuesEqual(readStoredValue(before[lane], field), readStoredValue(after[lane], field))) {
				fields.add(field);
			}
		}
		if (fields.size) dirty.set(lane, { record: false, fields });
	}
	return dirty;
}

function dirtyUiStateKeys(base: VideoLocalizationDraft, current: VideoLocalizationDraft) {
	const result = new Set<UiStateField>();
	for (const field of UI_STATE_FIELDS) {
		if (!storedValuesEqual(
			readStoredValue(base.ui_state ?? {}, field),
			readStoredValue(current.ui_state ?? {}, field)
		)) result.add(field);
	}
	return result;
}

function patchTouchesClipIds(patch: TimelinePatch, clipIds: ReadonlySet<string>) {
	return clipIdsTouchedByPatch(patch).some((clipId) => clipIds.has(clipId));
}

function clipIdsTouchedByPatch(patch: TimelinePatch) {
	return [
		...patch.clipFields.map((item) => item.clipId),
		...patch.removeClipIds,
		...patch.restoreClips.map((item) => item.clip.clip_id),
		...patch.addDeletedClipIds,
		...patch.removeDeletedClipIds,
		...(patch.timelineClips?.upserts.map((item) => item.id) ?? []),
		...(patch.timelineClips?.removeIds ?? [])
	];
}

function patchIsEmpty(patch: TimelinePatch) {
	return !(
		patch.cues
		|| patch.localizedSubtitles
		|| patch.timelineClips
		|| patch.clipFields.length
		|| patch.removeClipIds.length
		|| patch.restoreClips.length
		|| patch.laneFields.length
		|| patch.laneRecords.length
		|| Object.keys(patch.uiStateFields).length
		|| patch.addDeletedClipIds.length
		|| patch.removeDeletedClipIds.length
	);
}

function applyStoredFields(
	target: Record<string, unknown>,
	fields: Partial<Record<string, StoredValue>>
) {
	for (const [field, stored] of Object.entries(fields)) {
		if (!stored) continue;
		if (stored.exists) target[field] = stored.value;
		else delete target[field];
	}
}

function readStoredValue(record: object, field: string): StoredValue {
	const value = record as Record<string, unknown>;
	return Object.prototype.hasOwnProperty.call(value, field)
		? { exists: true, value: value[field] }
		: { exists: false };
}

function storedValuesEqual(left: StoredValue, right: StoredValue) {
	if (left.exists !== right.exists) return false;
	if (!left.exists) return true;
	if (Array.isArray(left.value) && Array.isArray(right.value)) {
		const leftValues = left.value;
		const rightValues = right.value;
		return leftValues.length === rightValues.length
			&& leftValues.every((value, index) => Object.is(value, rightValues[index]));
	}
	return Object.is(left.value, right.value);
}

function normalizeLaneKey(value: string | number) {
	const lane = Number(value);
	if (!Number.isInteger(lane) || lane < 0) throw new Error(`Invalid dub lane: ${String(value)}`);
	return String(lane);
}

function readLaneStates(draft: VideoLocalizationDraft) {
	const raw = draft.ui_state?.dub_lane_states;
	if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {} as Record<string, VideoLocalizationTrackState>;
	return Object.fromEntries(
		Object.entries(raw as Record<string, Partial<VideoLocalizationTrackState>>)
			.map(([lane, state]) => [lane, cloneLaneState(state)])
	) as Record<string, VideoLocalizationTrackState>;
}

function cloneDraft(draft: VideoLocalizationDraft): VideoLocalizationDraft {
	return {
		...draft,
		cues: draft.cues.map(cloneValue),
		localized_subtitles: draft.localized_subtitles.map(cloneValue),
		timeline_clips: draft.timeline_clips
			.filter((clip) => !isTimelineRuntimeClip(clip))
			.map(cloneClip),
		ui_state: {
			...draft.ui_state,
			dub_lane_states: readLaneStates(draft),
			...(Array.isArray(draft.ui_state?.disabled_media_tracks)
				? { disabled_media_tracks: [...draft.ui_state.disabled_media_tracks] }
				: {}),
			...(Array.isArray(draft.ui_state?.discarded_tts_task_ids)
				? { discarded_tts_task_ids: [...draft.ui_state.discarded_tts_task_ids] }
				: {})
		}
	};
}

function cloneClip(clip: VideoLocalizationTimelineClip): VideoLocalizationTimelineClip {
	return {
		...clip,
		...(Array.isArray(clip.source_cue_ids) ? { source_cue_ids: [...clip.source_cue_ids] } : {})
	};
}

function cloneLaneState(state: Partial<VideoLocalizationTrackState>) {
	return { ...state } as VideoLocalizationTrackState;
}

function cloneValue<T>(value: T): T {
	if (value === undefined || value === null) return value;
	return JSON.parse(JSON.stringify(value)) as T;
}
