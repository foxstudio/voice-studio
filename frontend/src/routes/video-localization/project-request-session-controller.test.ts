import { describe, expect, it } from 'vitest';
import { ProjectRequestSessionController } from './project-request-session-controller';

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((next) => (resolve = next));
	return { promise, resolve };
}

describe('ProjectRequestSessionController', () => {
	it('returns a response while its project and epoch remain current', async () => {
		let activeProjectId = 'project-a';
		const controller = new ProjectRequestSessionController({
			getActiveProjectId: () => activeProjectId
		});

		await expect(controller.refresh('project-a', async () => 'detail-a'))
			.resolves.toBe('detail-a');
	});

	it('rejects a late response after switching projects', async () => {
		let activeProjectId = 'project-a';
		const controller = new ProjectRequestSessionController({
			getActiveProjectId: () => activeProjectId
		});
		const request = deferred<string>();
		const result = controller.refresh('project-a', () => request.promise);

		activeProjectId = 'project-b';
		request.resolve('stale-detail');

		await expect(result).resolves.toBeNull();
	});

	it('rejects an ABA response after leaving and returning to the same project', async () => {
		let activeProjectId = 'project-a';
		const controller = new ProjectRequestSessionController({
			getActiveProjectId: () => activeProjectId
		});
		const request = deferred<string>();
		const result = controller.refresh('project-a', () => request.promise);

		activeProjectId = 'project-b';
		controller.invalidate();
		activeProjectId = 'project-a';
		controller.invalidate();
		request.resolve('old-project-a-detail');

		await expect(result).resolves.toBeNull();
	});
});
