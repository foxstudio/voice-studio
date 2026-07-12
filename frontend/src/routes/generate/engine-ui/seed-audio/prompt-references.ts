import type { SeedAudioReferenceSlot } from './state';

const AUDIO_REFERENCE_PATTERN = /@音频([1-9]\d*)/g;

export interface ParsedAudioReference {
	slot: number;
	start: number;
	end: number;
	raw: string;
}

export interface PromptReferenceIssue {
	code: 'reference_out_of_range' | 'reference_slot_empty' | 'reference_unused';
	message: string;
	slot: number;
}

export interface PromptReferenceValidation {
	references: ParsedAudioReference[];
	errors: PromptReferenceIssue[];
	warnings: PromptReferenceIssue[];
}

export function parseAudioPromptReferences(prompt: string): ParsedAudioReference[] {
	return Array.from(prompt.matchAll(AUDIO_REFERENCE_PATTERN), (match) => ({
		slot: Number(match[1]),
		start: match.index ?? 0,
		end: (match.index ?? 0) + match[0].length,
		raw: match[0]
	}));
}

export function validateAudioPromptReferences(
	prompt: string,
	slots: readonly SeedAudioReferenceSlot[]
): PromptReferenceValidation {
	const references = parseAudioPromptReferences(prompt);
	const referencedSlots = new Set<number>();
	const errors: PromptReferenceIssue[] = [];
	for (const reference of references) {
		if (reference.slot < 1 || reference.slot > 3) {
			errors.push({
				code: 'reference_out_of_range',
				slot: reference.slot,
				message: `${reference.raw} 不存在，只能引用 @音频1～3`
			});
			continue;
		}
		referencedSlots.add(reference.slot);
		if (!slots.find((slot) => slot.slot === reference.slot)?.asset) {
			errors.push({
				code: 'reference_slot_empty',
				slot: reference.slot,
				message: `${reference.raw} 还没有添加参考声音`
			});
		}
	}
	const warnings = slots
		.filter((slot) => slot.asset && !referencedSlots.has(slot.slot))
		.map((slot) => ({
			code: 'reference_unused' as const,
			slot: slot.slot,
			message: `@音频${slot.slot} 已添加但未在描述中引用`
		}));
	return { references, errors, warnings };
}

export interface CompiledPromptReferences {
	prompt: string;
	bindings: Array<{ slot: number; requestIndex: number; assetId: string }>;
}

/** Compacts non-empty slots and rewrites @音频N in one pass to avoid collisions. */
export function compileAudioPromptReferences(
	prompt: string,
	slots: readonly SeedAudioReferenceSlot[]
): CompiledPromptReferences {
	const bindings = slots
		.filter((slot): slot is SeedAudioReferenceSlot & { asset: NonNullable<SeedAudioReferenceSlot['asset']> } => Boolean(slot.asset))
		.map((slot, index) => ({ slot: slot.slot, requestIndex: index + 1, assetId: slot.asset.assetId }));
	const indexBySlot = new Map(bindings.map((binding) => [binding.slot, binding.requestIndex]));
	return {
		prompt: prompt.replace(AUDIO_REFERENCE_PATTERN, (raw, slotText: string) => {
			const requestIndex = indexBySlot.get(Number(slotText) as 1 | 2 | 3);
			return requestIndex ? `@音频${requestIndex}` : raw;
		}),
		bindings
	};
}

export function insertAudioPromptReference(prompt: string, slot: 1 | 2 | 3, cursor = prompt.length): string {
	const safeCursor = Math.max(0, Math.min(prompt.length, cursor));
	const before = prompt.slice(0, safeCursor);
	const after = prompt.slice(safeCursor);
	const leadingSpace = before && !/\s$/.test(before) ? ' ' : '';
	const trailingSpace = after && !/^\s/.test(after) ? ' ' : '';
	return `${before}${leadingSpace}@音频${slot}${trailingSpace}${after}`;
}
