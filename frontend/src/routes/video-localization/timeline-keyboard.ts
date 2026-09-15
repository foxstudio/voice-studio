export function timelineDeleteShortcutAllowed(
	key: string,
	timelineOwnsKeyboard: boolean,
	hasDeletableSelection: boolean
) {
	if (!timelineOwnsKeyboard) return false;
	if (key === 'Delete' || key === 'Backspace') return true;
	return key.toLowerCase() === 'e' && hasDeletableSelection;
}
