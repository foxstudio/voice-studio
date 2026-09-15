import type { ComponentProps } from 'svelte';
import { describe, expectTypeOf, it } from 'vitest';
import CuttingInspector from './CuttingInspector.svelte';
import PreviewPanel from './PreviewPanel.svelte';
import VideoCuttingTimeline from './VideoCuttingTimeline.svelte';
import type {
	SubtitleDisplayFrame,
	SubtitleDisplayModel
} from './subtitle-display';

type PreviewProps = ComponentProps<typeof PreviewPanel>;
type TimelineProps = ComponentProps<typeof VideoCuttingTimeline>;
type InspectorProps = ComponentProps<typeof CuttingInspector>;

type HasProp<Props, Key extends PropertyKey> = Key extends keyof Props ? true : false;

describe('subtitle display consumer contracts', () => {
	it('gives the player only the lightweight shared playback frame', () => {
		expectTypeOf<PreviewProps['subtitleFrame']>().toEqualTypeOf<SubtitleDisplayFrame>();
		expectTypeOf<HasProp<PreviewProps, 'asrText'>>().toEqualTypeOf<false>();
		expectTypeOf<HasProp<PreviewProps, 'localizedSubtitle'>>().toEqualTypeOf<false>();
		expectTypeOf<HasProp<PreviewProps, 'subtitlePreviewLines'>>().toEqualTypeOf<false>();
	});

	it('gives the timeline and inspector the same display model contract', () => {
		expectTypeOf<TimelineProps['subtitleDisplay']>().toEqualTypeOf<SubtitleDisplayModel>();
		expectTypeOf<InspectorProps['subtitleDisplay']>().toEqualTypeOf<SubtitleDisplayModel>();
		expectTypeOf<HasProp<TimelineProps, 'asrPreview'>>().toEqualTypeOf<false>();
		expectTypeOf<HasProp<TimelineProps, 'localizationPreview'>>().toEqualTypeOf<false>();
		expectTypeOf<HasProp<InspectorProps, 'asrPreview'>>().toEqualTypeOf<false>();
		expectTypeOf<HasProp<InspectorProps, 'localizationPreview'>>().toEqualTypeOf<false>();
		expectTypeOf<InspectorProps['onUpdateDubSubtitle']>().toEqualTypeOf<
			((subtitleId: string, patch: { text?: string; start_ms?: number; end_ms?: number }) => void | Promise<void>) | undefined
		>();
	});

	it('does not expose callbacks from the retired inline dubbing generator', () => {
		expectTypeOf<HasProp<InspectorProps, 'onQuickGenerateVoice'>>().toEqualTypeOf<false>();
		expectTypeOf<HasProp<InspectorProps, 'onApplyGeneratedCandidate'>>().toEqualTypeOf<false>();
		expectTypeOf<HasProp<InspectorProps, 'onCreateVoiceRecipe'>>().toEqualTypeOf<false>();
	});
});
