import { describe, expect, it } from 'vitest';
import { ProjectDraftSessionController } from './project-draft-session-controller';

describe('project draft session controller', () => {
	function setup() {
		let activeProjectId = 'project-a';
		const controller = new ProjectDraftSessionController({
			getActiveProjectId: () => activeProjectId
		});
		return {
			controller,
			setActiveProjectId: (projectId: string) => (activeProjectId = projectId)
		};
	}

	it('accepts the latest project load for the active project', async () => {
		const { controller } = setup();

		const result = await controller.load('project-a', async () => ({ revision: 1 }));

		expect(result).toEqual({ revision: 1 });
	});

	it('rejects an older load after a newer load starts', async () => {
		const { controller, setActiveProjectId } = setup();
		let releaseFirst!: (value: { revision: number }) => void;
		const first = controller.load(
			'project-a',
			() => new Promise<{ revision: number }>((resolve) => (releaseFirst = resolve))
		);

		setActiveProjectId('project-b');
		const second = controller.load('project-b', async () => ({ revision: 2 }));
		releaseFirst({ revision: 1 });

		expect(await first).toBeNull();
		expect(await second).toEqual({ revision: 2 });
	});

	it('rejects a refresh after an authoritative mutation', async () => {
		const { controller } = setup();
		let releaseRefresh!: (value: { revision: number }) => void;
		const refresh = controller.refresh(
			'project-a',
			() => new Promise<{ revision: number }>((resolve) => (releaseRefresh = resolve))
		);

		const mutation = controller.mutate('project-a', async () => ({ revision: 3 }));
		releaseRefresh({ revision: 2 });

		expect(await refresh).toBeNull();
		expect(await mutation).toEqual({ revision: 3 });
	});

	it('rejects a refresh when the active project changes', async () => {
		const { controller, setActiveProjectId } = setup();
		let releaseRefresh!: (value: { revision: number }) => void;
		const refresh = controller.refresh(
			'project-a',
			() => new Promise<{ revision: number }>((resolve) => (releaseRefresh = resolve))
		);

		setActiveProjectId('project-b');
		releaseRefresh({ revision: 2 });

		expect(await refresh).toBeNull();
	});

	it('allows concurrent refresh callers to share the same freshness boundary', async () => {
		const { controller } = setup();

		const [draft, tasks] = await Promise.all([
			controller.refresh('project-a', async () => ({ kind: 'draft' })),
			controller.refresh('project-a', async () => ({ kind: 'tasks' }))
		]);

		expect(draft).toEqual({ kind: 'draft' });
		expect(tasks).toEqual({ kind: 'tasks' });
	});

	it('keeps only the newest independently refreshed projection', async () => {
		const { controller } = setup();
		let releaseFirst!: (value: { revision: number }) => void;
		const first = controller.refreshLatest(
			'project-a',
			() => new Promise<{ revision: number }>((resolve) => (releaseFirst = resolve))
		);
		const second = controller.refreshLatest('project-a', async () => ({ revision: 2 }));
		releaseFirst({ revision: 1 });

		expect(await first).toBeNull();
		expect(await second).toEqual({ revision: 2 });
	});
});
