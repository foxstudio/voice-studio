import { Captions, ChevronsLeft, ChevronsRight, CircleOff, Languages, MoveHorizontal, Trash2 } from 'lucide-svelte';
import type { ContextMenuItem } from '$lib/components/shared/context-menu';
import type { VideoLocalizationTrackId } from './studio-state';
import { asrSubtitleActionLabel, localizationSubtitleActionLabel } from './activity-notice';

export type SubtitleTrackKind = 'asr' | 'localized';

export type TimelineContextMenuTarget =
	| { kind: 'track'; hit: 'empty'; trackId: VideoLocalizationTrackId; subtitleTrack?: SubtitleTrackKind; timeMs: number }
	| { kind: 'subtitle-clip'; trackId: 'subtitles' | 'localizedSubtitles'; subtitleTrack: SubtitleTrackKind; itemId: string; timeMs: number }
	| { kind: 'audio-clip'; trackId: VideoLocalizationTrackId; itemId: string; timeMs: number }
	| { kind: 'video-clip'; itemId: string; timeMs: number }
	| { kind: 'canvas'; timeMs: number };

export type TimelineContextMenuContext = {
	itemCount: number;
	locked: boolean;
	canGenerateAsr: boolean;
	asrBusy: boolean;
	canGenerateLocalization?: boolean;
	localizationBusy?: boolean;
	trackBusy: boolean;
	asrUnavailableReason: string;
	localizationUnavailableReason?: string;
	hasSelectionPoints: boolean;
	onGenerateAsr: () => void | Promise<void>;
	onGenerateLocalization?: () => void | Promise<void>;
	onClearSubtitleTrack: (track: SubtitleTrackKind) => void | Promise<void>;
	onDeleteSubtitleItem: (track: SubtitleTrackKind, itemId: string) => void | Promise<void>;
	onDeleteAudioClip: (itemId: string) => void | Promise<void>;
	onFillSubtitleGaps: (track: SubtitleTrackKind) => void | Promise<void>;
	onSetSelectionStart: (timeMs: number) => void;
	onSetSelectionEnd: (timeMs: number) => void;
	onClearSelection: () => void;
};

export function buildSubtitleTrackCommands(
	track: SubtitleTrackKind,
	context: TimelineContextMenuContext
): ContextMenuItem[] {
	const commands: ContextMenuItem[] = [];
	if (track === 'asr') {
		const generateDisabled = context.locked || !context.canGenerateAsr || context.asrBusy || context.trackBusy;
		commands.push({
			id: 'generate-asr-subtitles',
			label: asrSubtitleActionLabel(context.itemCount > 0),
			description: context.locked
				? '请先解锁 ASR 字幕轨'
				: context.asrBusy
					? 'ASR 字幕生成任务正在运行'
					: context.trackBusy
						? '当前字幕轨正在处理，请稍候'
						: context.canGenerateAsr
							? context.itemCount
								? '重新识别人声并生成字幕，保留已保护的人工编辑'
								: '识别分离后的人声，生成带时间码的 ASR 字幕'
							: context.asrUnavailableReason,
			icon: Captions,
			disabled: generateDisabled,
			onSelect: context.onGenerateAsr
		});
	}
	if (track === 'localized') {
		const canGenerate = context.canGenerateLocalization === true;
		const generateDisabled = context.locked || !canGenerate || context.localizationBusy === true || context.trackBusy;
		commands.push({
			id: 'generate-localized-subtitles',
			label: localizationSubtitleActionLabel(context.itemCount > 0),
			description: context.locked
				? '请先解锁本土化字幕轨'
				: context.localizationBusy
					? '本土化字幕初稿生成任务正在运行'
					: context.trackBusy
						? '当前字幕轨正在处理，请稍候'
						: canGenerate
							? context.itemCount
								? '根据最新 ASR 字幕重新生成本土化字幕初稿'
								: '理解 ASR 字幕内容并生成带初步时间的本土化字幕'
							: context.localizationUnavailableReason || 'ASR 字幕轨有内容后，才能生成本土化字幕初稿',
			icon: Languages,
			disabled: generateDisabled,
			onSelect: context.onGenerateLocalization ?? (() => {})
		});
	}

	const operationBusy = track === 'asr' ? context.asrBusy : context.localizationBusy === true;

	commands.push({
		id: `fill-${track}-subtitle-gaps`,
		label: '延续短停顿',
		description: context.locked
			? '请先解锁当前字幕轨'
			: context.trackBusy
				? '当前字幕轨正在处理，请稍候'
				: context.itemCount > 1
					? '把连续说话中的短空隙延伸到下一条入点，不重叠且不修改最后一条'
					: '至少需要两条字幕才能判断短停顿',
		icon: MoveHorizontal,
		disabled: context.locked || context.trackBusy || context.itemCount < 2 || operationBusy,
		separatorBefore: true,
		onSelect: () => context.onFillSubtitleGaps(track)
	});

	commands.push({
		id: `clear-${track}-subtitle-track`,
		label: '删除当前轨全部字幕',
		description: context.locked
			? '请先解锁当前字幕轨'
			: context.trackBusy
				? '当前字幕轨正在处理，请稍候'
				: operationBusy
					? track === 'asr'
						? '字幕听写运行期间不能清空 ASR 字幕轨'
						: '本土化字幕生成期间不能清空本土化字幕轨'
					: context.itemCount
						? `将删除 ${context.itemCount} 个片段，其他轨道不受影响`
						: '当前字幕轨已经为空',
		icon: Trash2,
		disabled: context.locked || context.trackBusy || context.itemCount === 0 || operationBusy,
		tone: 'danger',
		onSelect: () => context.onClearSubtitleTrack(track)
	});
	return commands;
}

export function buildSelectionCommands(
	target: TimelineContextMenuTarget,
	context: TimelineContextMenuContext,
	separatorBefore = false
): ContextMenuItem[] {
	return [
		{
			id: 'set-selection-start',
			label: '在此处设置入点',
			description: '把右键位置设为当前选区的开始时间',
			icon: ChevronsLeft,
			separatorBefore,
			onSelect: () => context.onSetSelectionStart(target.timeMs)
		},
		{
			id: 'set-selection-end',
			label: '在此处设置出点',
			description: '把右键位置设为当前选区的结束时间',
			icon: ChevronsRight,
			onSelect: () => context.onSetSelectionEnd(target.timeMs)
		},
		{
			id: 'clear-selection',
			label: '清除出入点选区',
			description: context.hasSelectionPoints ? '移除当前入点、出点和范围选区' : '当前没有设置出入点',
			icon: CircleOff,
			disabled: !context.hasSelectionPoints,
			onSelect: context.onClearSelection
		}
	];
}

export function buildTimelineContextMenuItems(
	target: TimelineContextMenuTarget,
	context: TimelineContextMenuContext
): ContextMenuItem[] {
	const subtitleTrack = target.kind === 'track' || target.kind === 'subtitle-clip'
		? target.subtitleTrack
		: undefined;
	const itemCommands: ContextMenuItem[] = [];
	if (target.kind === 'subtitle-clip') {
		itemCommands.push({
			id: `delete-${target.subtitleTrack}-subtitle-${target.itemId}`,
			label: '删除当前字幕片段',
			description: context.locked || context.trackBusy
				? '当前字幕片段暂时不可编辑'
				: '只删除当前选中的字幕片段，其他字幕不受影响',
			icon: Trash2,
			disabled: context.locked || context.trackBusy
				|| (target.subtitleTrack === 'asr' ? context.asrBusy : context.localizationBusy === true),
			tone: 'danger',
			onSelect: () => context.onDeleteSubtitleItem(target.subtitleTrack, target.itemId)
		});
	}
	if (target.kind === 'audio-clip') {
		itemCommands.push({
			id: `delete-audio-clip-${target.itemId}`,
			label: '删除当前音频片段',
			description: context.locked || context.trackBusy
				? '当前音频片段暂时不可编辑'
				: '从当前轨道移除选中的音频片段，可使用撤销恢复',
			icon: Trash2,
			disabled: context.locked || context.trackBusy,
			tone: 'danger',
			onSelect: () => context.onDeleteAudioClip(target.itemId)
		});
	}
	const trackCommands = subtitleTrack ? buildSubtitleTrackCommands(subtitleTrack, context) : [];
	const priorCount = itemCommands.length + trackCommands.length;
	return [...itemCommands, ...trackCommands, ...buildSelectionCommands(target, context, priorCount > 0)];
}
