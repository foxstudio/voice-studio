import { repositoryRevisionIsOlder } from './workspace-timeline-projection';

type ReadScope = 'workspace' | 'timeline';

/** A mutation receipt observes a version; only a complete read consumes it. */
export class WorkspaceRevisionController {
	private projectId = '';
	private observed = '';
	private consumed = { workspace: '', timeline: '' };

	reset(projectId = '') {
		this.projectId = projectId;
		this.observed = '';
		this.consumed = { workspace: '', timeline: '' };
	}

	activate(projectId: string, revision: string) {
		this.reset(projectId);
		this.consume(projectId, revision, 'workspace');
	}

	observe(projectId: string, revision: string) {
		if (projectId !== this.projectId || repositoryRevisionIsOlder(revision, this.observed)) return;
		this.observed = revision;
	}

	canApply(projectId: string, revision: string) {
		return !!projectId && projectId === this.projectId && !repositoryRevisionIsOlder(revision, this.observed);
	}

	/** Apply a bounded mutation receipt without consuming a complete project read. */
	applyReceipt<Result>(projectId: string, revision: string, apply: () => Result): Result | null {
		if (!this.canApply(projectId, revision)) return null;
		const result = apply();
		this.observe(projectId, revision);
		return result;
	}

	consume(projectId: string, revision: string, scope: ReadScope) {
		if (!this.canApply(projectId, revision)) return;
		this.observe(projectId, revision);
		this.consumed[scope] = revision;
		if (scope === 'workspace') this.consumed.timeline = revision;
	}

	needsRefresh(projectId: string, revision: string, scope: ReadScope) {
		return projectId !== this.projectId
			|| !this.consumed[scope]
			|| this.consumed[scope] !== revision
			|| this.consumed[scope] !== this.observed;
	}
}
