export const API_RECOVERED_EVENT = 'voice-studio:api-recovered';

export function shouldNotifyApiRecovery(previous: string, current: string) {
	return previous !== 'checking'
		&& previous !== 'ok'
		&& current !== 'offline'
		&& current !== 'checking';
}

export function shouldRetryInitialLoadOnApiRecovery(options: {
	retryPending: boolean;
	loading: boolean;
	hasLoadedResource: boolean;
}) {
	return options.retryPending && !options.loading && !options.hasLoadedResource;
}
