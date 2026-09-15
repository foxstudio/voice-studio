import { Api } from '$lib/api';
import type { Project, ProjectSummary, ProjectUpdate } from '$lib/api/types';

type ProjectCatalogTransport = {
	projectSummaries: (kind: 'video_localization') => Promise<ProjectSummary[]>;
	syncVideoLocalizationProjectSummaries: () => Promise<ProjectSummary[]>;
	createProject: (
		name: string,
		description: string,
		defaultEngineId?: string | null
	) => Promise<Project>;
	updateProject: (projectId: string, patch: ProjectUpdate) => Promise<Project>;
	autoNameVideoLocalizationProject: (projectId: string) => Promise<Project>;
	deleteProject: (projectId: string) => Promise<{ status: string }>;
};

type ProjectCatalogControllerOptions = {
	onProjects: (projects: ProjectSummary[]) => void;
};

export function sortProjectSummariesByUpdatedAt<T extends {
	project_id: string;
	created_at: string;
	updated_at: string;
}>(projects: T[]): T[] {
	const timestamp = (value: string) => {
		const parsed = Date.parse(value);
		return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
	};
	return [...projects].sort((left, right) => {
		const updatedDifference = timestamp(right.updated_at) - timestamp(left.updated_at);
		if (updatedDifference) return updatedDifference;
		const createdDifference = timestamp(right.created_at) - timestamp(left.created_at);
		if (createdDifference) return createdDifference;
		return right.project_id.localeCompare(left.project_id);
	});
}

export function videoProjectSummary(project: Project): ProjectSummary {
	const localization = project.parameters?.video_localization as
		| { source_media?: { filename?: unknown; video_path?: unknown } }
		| undefined;
	return {
		project_id: project.project_id,
		name: project.name,
		description: project.description,
		kind: 'video_localization',
		has_source_media: false,
		source_media_configured: Boolean(
			localization?.source_media?.video_path || localization?.source_media?.filename
		),
		source_media_status: 'unknown',
		has_local_package: true,
		package_status: 'unknown',
		created_at: project.created_at,
		updated_at: project.updated_at
	};
}

export function unavailableProjectSummary(project: ProjectSummary): ProjectSummary {
	return {
		...project,
		has_source_media: false,
		source_media_status: project.source_media_configured ? 'missing' : 'unconfigured',
		has_local_package: false,
		package_status: 'missing'
	};
}

export class VideoLocalizationProjectCatalogClient {
	constructor(private readonly transport: ProjectCatalogTransport = Api) {}

	list() {
		return this.transport.projectSummaries('video_localization');
	}

	sync() {
		return this.transport.syncVideoLocalizationProjectSummaries();
	}

	create(name: string, description: string) {
		return this.transport.createProject(name, description);
	}

	rename(projectId: string, name: string) {
		return this.transport.updateProject(projectId, { name });
	}

	autoName(projectId: string) {
		return this.transport.autoNameVideoLocalizationProject(projectId);
	}

	delete(projectId: string) {
		return this.transport.deleteProject(projectId);
	}
}

export class ProjectCatalogController {
	private currentProjects: ProjectSummary[] = [];
	private readEpoch = 0;
	private mutationEpoch = 0;
	private entityEpochs = new Map<string, number>();

	constructor(
		private readonly client: VideoLocalizationProjectCatalogClient,
		private readonly options: ProjectCatalogControllerOptions
	) {}

	get projects() {
		return [...this.currentProjects];
	}

	load() {
		return this.read(() => this.client.list());
	}

	reconcile(activeProjectId?: string) {
		return this.read(() => this.client.sync(), activeProjectId);
	}

	sync(activeProjectId?: string) {
		return this.read(() => this.client.sync(), activeProjectId);
	}

	async create(name: string, description: string) {
		this.beginMutation();
		const project = await this.client.create(name, description);
		this.completeMutation();
		this.upsert(videoProjectSummary(project));
		return project;
	}

	async rename(projectId: string, name: string) {
		const entityEpoch = this.beginEntityMutation(projectId);
		const project = await this.client.rename(projectId, name);
		if (!this.entityIsCurrent(projectId, entityEpoch)) return null;
		this.completeMutation();
		this.upsert(videoProjectSummary(project));
		return project;
	}

	async autoName(projectId: string) {
		const entityEpoch = this.beginEntityMutation(projectId);
		const project = await this.client.autoName(projectId);
		if (!this.entityIsCurrent(projectId, entityEpoch)) return null;
		this.completeMutation();
		this.upsert(videoProjectSummary(project));
		return project;
	}

	async delete(projectId: string) {
		const entityEpoch = this.beginEntityMutation(projectId);
		try {
			await this.client.delete(projectId);
		} catch (deleteError) {
			const reconciled = await this.client.sync().catch(() => null);
			if (!this.entityIsCurrent(projectId, entityEpoch)) return false;
			if (!reconciled || reconciled.some((project) => project.project_id === projectId)) {
				throw deleteError;
			}
		}
		if (!this.entityIsCurrent(projectId, entityEpoch)) return false;
		this.completeMutation();
		this.remove(projectId);
		return true;
	}

	private async read(
		request: () => Promise<ProjectSummary[]>,
		retainProjectId?: string
	) {
		const readEpoch = ++this.readEpoch;
		const mutationEpoch = this.mutationEpoch;
		const retainedProject = retainProjectId
			? this.currentProjects.find((project) => project.project_id === retainProjectId)
			: undefined;
		const receivedProjects = await request();
		if (readEpoch !== this.readEpoch || mutationEpoch !== this.mutationEpoch) return null;
		const projects = sortProjectSummariesByUpdatedAt(
			retainedProject
				&& !receivedProjects.some((project) => project.project_id === retainedProject.project_id)
				? [...receivedProjects, unavailableProjectSummary(retainedProject)]
				: receivedProjects
		);
		this.replace(projects);
		return projects;
	}

	private beginMutation() {
		this.mutationEpoch += 1;
		this.readEpoch += 1;
	}

	private completeMutation() {
		this.mutationEpoch += 1;
		this.readEpoch += 1;
	}

	private beginEntityMutation(projectId: string) {
		this.beginMutation();
		const entityEpoch = (this.entityEpochs.get(projectId) ?? 0) + 1;
		this.entityEpochs.set(projectId, entityEpoch);
		return entityEpoch;
	}

	private entityIsCurrent(projectId: string, entityEpoch: number) {
		return this.entityEpochs.get(projectId) === entityEpoch;
	}

	private upsert(project: ProjectSummary) {
		this.replace(sortProjectSummariesByUpdatedAt([
			...this.currentProjects.filter((item) => item.project_id !== project.project_id),
			project
		]));
	}

	private remove(projectId: string) {
		this.replace(this.currentProjects.filter((project) => project.project_id !== projectId));
	}

	private replace(projects: ProjectSummary[]) {
		this.currentProjects = projects;
		this.options.onProjects(this.projects);
	}
}
