import { describe, expect, it } from 'vitest';
import {
	API_RECOVERED_EVENT,
	shouldRetryInitialLoadOnApiRecovery,
	shouldNotifyApiRecovery
} from './api-health-recovery';

describe('API health recovery signal', () => {
	it('notifies when the API recovers from a failed or offline check', () => {
		expect(shouldNotifyApiRecovery('checking', 'ok')).toBe(false);
		expect(shouldNotifyApiRecovery('ok', 'ok')).toBe(false);
		expect(shouldNotifyApiRecovery('ok', 'offline')).toBe(false);
		expect(shouldNotifyApiRecovery('offline', 'offline')).toBe(false);
		expect(shouldNotifyApiRecovery('slow', 'ok')).toBe(true);
		expect(shouldNotifyApiRecovery('offline', 'ok')).toBe(true);
	});

	it('uses one stable event name for media-session recovery', () => {
		expect(API_RECOVERED_EVENT).toBe('voice-studio:api-recovered');
	});

	it('retries only a failed initial load after recovery', () => {
		expect(shouldRetryInitialLoadOnApiRecovery({
			retryPending: true,
			loading: false,
			hasLoadedResource: false
		})).toBe(true);
		expect(shouldRetryInitialLoadOnApiRecovery({
			retryPending: false,
			loading: false,
			hasLoadedResource: false
		})).toBe(false);
		expect(shouldRetryInitialLoadOnApiRecovery({
			retryPending: true,
			loading: true,
			hasLoadedResource: false
		})).toBe(false);
		expect(shouldRetryInitialLoadOnApiRecovery({
			retryPending: true,
			loading: false,
			hasLoadedResource: true
		})).toBe(false);
	});
});
