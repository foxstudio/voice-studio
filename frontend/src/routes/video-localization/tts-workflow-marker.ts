const INITIALIZATION_PREFIX = 'init:';

export function ttsInitializationClientId(workflowMarker: string | null | undefined) {
	const value = String(workflowMarker ?? '');
	return value.startsWith(INITIALIZATION_PREFIX) ? value.slice(INITIALIZATION_PREFIX.length) : null;
}

export function persistedTtsWorkflowId(workflowMarker: string | null | undefined) {
	const value = String(workflowMarker ?? '');
	return value && !value.startsWith(INITIALIZATION_PREFIX) ? value : null;
}
