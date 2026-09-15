import { describe, expect, it, vi } from 'vitest';
import type { Project, ProjectSummary } from '$lib/api/types';
import {
	ProjectCatalogController,
	VideoLocalizationProjectCatalogClient
} from './project-catalog-controller';

function summary(
	id: string,
	name = id,
	updatedAt = '2026-07-30T00:00:00Z'
): ProjectSummary {
	return {
		project_id: id,
		name,
		description: '',
		kind: 'video_localization',
		has_source_media: false,
		source_media_configured: false,
		source_media_status: 'unconfigured',
		has_local_package: true,
		package_status: 'available',
		created_at: '2026-07-29T00:00:00Z',
		updated_at: updatedAt
	};
}

function project(id: string, name = id): Project {
	return {
		project_id: id,
		name,
		description: '',
		default_engine_id: null,
		parameters: {},
		roles: [],
		segments: [],
		created_at: '2026-07-29T00:00:00Z',
		updated_at: '2026-07-30T00:00:00Z'
	};
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (error: unknown) => void;
	const promise = new Promise<T>((nextResolve, nextReject) => {
		resolve = nextResolve;
		reject = nextReject;
	});
	return { promise, resolve, reject };
}

function setup() {
	const transport = {
		projectSummaries: vi.fn(async () => [] as ProjectSummary[]),
		syncVideoLocalizationProjectSummaries: vi.fn(async () => [] as ProjectSummary[]),
		createProject: vi.fn(async (name: string) => project('created', name)),
		updateProject: vi.fn(async (projectId: string, patch: { name?: string | null }) =>
			project(projectId, patch.name ?? projectId)),
		autoNameVideoLocalizationProject: vi.fn(async (projectId: string) =>
			project(projectId, '自动命名')),
		deleteProject: vi.fn(async () => ({ status: 'deleted' }))
	};
	const updates: ProjectSummary[][] = [];
	const controller = new ProjectCatalogController(
		new VideoLocalizationProjectCatalogClient(transport),
		{ onProjects: (projects) => updates.push(projects) }
	);
	return { controller, transport, updates };
}

describe('project catalog controller', () => {
	it('loads summaries in stable last-updated order', async () => {
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([
			summary('older', '旧项目', '2026-07-30T00:00:00Z'),
			summary('newer', '新项目', '2026-07-30T01:00:00Z')
		]);

		await controller.load();

		expect(controller.projects.map((item) => item.project_id)).toEqual(['newer', 'older']);
	});

	it('does not let a late reconcile overwrite a completed rename', async () => {
		const staleReconcile = deferred<ProjectSummary[]>();
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([summary('project-a', '旧名称')]);
		transport.syncVideoLocalizationProjectSummaries.mockImplementationOnce(
			() => staleReconcile.promise
		);
		await controller.load();

		const reconcile = controller.reconcile();
		await controller.rename('project-a', '新名称');
		staleReconcile.resolve([summary('project-a', '旧名称')]);
		await reconcile;

		expect(controller.projects[0]?.name).toBe('新名称');
	});

	it('invalidates a catalog read that starts while a rename is still running', async () => {
		const pendingRename = deferred<Project>();
		const staleSync = deferred<ProjectSummary[]>();
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([summary('project-a', '旧名称')]);
		transport.updateProject.mockImplementationOnce(() => pendingRename.promise);
		transport.syncVideoLocalizationProjectSummaries.mockImplementationOnce(
			() => staleSync.promise
		);
		await controller.load();

		const rename = controller.rename('project-a', '新名称');
		const sync = controller.sync();
		pendingRename.resolve(project('project-a', '新名称'));
		await rename;
		staleSync.resolve([summary('project-a', '旧名称')]);
		await sync;

		expect(controller.projects[0]?.name).toBe('新名称');
	});

	it('does not let a late reconcile remove a newly created project', async () => {
		const staleReconcile = deferred<ProjectSummary[]>();
		const { controller, transport } = setup();
		transport.syncVideoLocalizationProjectSummaries.mockImplementationOnce(
			() => staleReconcile.promise
		);

		const reconcile = controller.reconcile();
		await controller.create('新项目', '说明');
		staleReconcile.resolve([]);
		await reconcile;

		expect(controller.projects.map((item) => item.project_id)).toEqual(['created']);
	});

	it('does not let a late reconcile resurrect a deleted project', async () => {
		const staleReconcile = deferred<ProjectSummary[]>();
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([summary('project-a')]);
		transport.syncVideoLocalizationProjectSummaries.mockImplementationOnce(
			() => staleReconcile.promise
		);
		await controller.load();

		const reconcile = controller.reconcile();
		await controller.delete('project-a');
		staleReconcile.resolve([summary('project-a')]);
		await reconcile;

		expect(controller.projects).toEqual([]);
	});

	it('deletes only the project id requested by the clicked item', async () => {
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([
			summary('project-a'),
			summary('project-b')
		]);
		await controller.load();

		await controller.delete('project-b');

		expect(transport.deleteProject).toHaveBeenCalledTimes(1);
		expect(transport.deleteProject).toHaveBeenCalledWith('project-b');
		expect(controller.projects.map((item) => item.project_id)).toEqual([
			'project-a'
		]);
	});

	it('lets only the newest catalog read publish', async () => {
		const older = deferred<ProjectSummary[]>();
		const newer = deferred<ProjectSummary[]>();
		const { controller, transport } = setup();
		transport.syncVideoLocalizationProjectSummaries
			.mockImplementationOnce(() => older.promise)
			.mockImplementationOnce(() => newer.promise);

		const first = controller.sync();
		const second = controller.sync();
		newer.resolve([summary('newer')]);
		await second;
		older.resolve([summary('older')]);
		await first;

		expect(controller.projects.map((item) => item.project_id)).toEqual(['newer']);
	});

	it('retains the active project as unavailable when a directory sync omits it', async () => {
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([summary('project-a', '当前项目')]);
		transport.syncVideoLocalizationProjectSummaries.mockResolvedValueOnce([]);
		await controller.load();

		const projects = await controller.sync('project-a');

		expect(projects).toEqual([
			expect.objectContaining({
				project_id: 'project-a',
				has_source_media: false,
				has_local_package: false,
				package_status: 'missing',
				source_media_status: 'unconfigured'
			})
		]);
		expect(controller.projects).toEqual(projects);
	});

	it('does not retain an omitted inactive project during directory sync', async () => {
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([
			summary('project-a'),
			summary('project-b')
		]);
		transport.syncVideoLocalizationProjectSummaries.mockResolvedValueOnce([
			summary('project-b')
		]);
		await controller.load();

		await controller.sync('project-b');

		expect(controller.projects.map((item) => item.project_id)).toEqual(['project-b']);
	});

	it('treats an ambiguous delete response as success when reconciliation proves absence', async () => {
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([summary('project-a')]);
		transport.deleteProject.mockRejectedValueOnce(new Error('connection closed'));
		transport.syncVideoLocalizationProjectSummaries.mockResolvedValueOnce([]);
		await controller.load();

		await expect(controller.delete('project-a')).resolves.toBe(true);
		expect(controller.projects).toEqual([]);
	});

	it('preserves the original delete error when reconciliation still finds the project', async () => {
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([summary('project-a')]);
		const deleteError = new Error('delete failed');
		transport.deleteProject.mockRejectedValueOnce(deleteError);
		transport.syncVideoLocalizationProjectSummaries.mockResolvedValueOnce([
			summary('project-a')
		]);
		await controller.load();

		await expect(controller.delete('project-a')).rejects.toBe(deleteError);
		expect(controller.projects.map((item) => item.project_id)).toEqual(['project-a']);
	});

	it('rejects an older rename response for the same project', async () => {
		const olderRename = deferred<Project>();
		const { controller, transport } = setup();
		transport.projectSummaries.mockResolvedValueOnce([summary('project-a', '原名称')]);
		transport.updateProject
			.mockImplementationOnce(() => olderRename.promise)
			.mockResolvedValueOnce(project('project-a', '第二次改名'));
		await controller.load();

		const first = controller.rename('project-a', '第一次改名');
		await controller.rename('project-a', '第二次改名');
		olderRename.resolve(project('project-a', '第一次改名'));
		await first;

		expect(controller.projects[0]?.name).toBe('第二次改名');
	});
});
