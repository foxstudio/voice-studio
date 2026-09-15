export type SelectMenuPlacement = 'start' | 'end';

type TriggerBounds = {
	left: number;
	right: number;
	width: number;
};

export function selectMenuPlacement(
	trigger: TriggerBounds,
	viewportWidth: number,
	preferredWidth: number,
	edgePadding = 12
): SelectMenuPlacement {
	const availableWidth = Math.max(0, viewportWidth - edgePadding * 2);
	const menuWidth = Math.min(Math.max(trigger.width, preferredWidth), availableWidth);
	const fitsToRight = viewportWidth - trigger.left - edgePadding >= menuWidth;
	const fitsToLeft = trigger.right - edgePadding >= menuWidth;
	return !fitsToRight && fitsToLeft ? 'end' : 'start';
}
