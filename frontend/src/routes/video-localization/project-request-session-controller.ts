type ProjectRequestSessionControllerOptions = {
	getActiveProjectId: () => string;
};

type ProjectRequestToken = {
	projectId: string;
	epoch: number;
};

/**
 * Prevents an older project-scoped response from overwriting newer state.
 *
 * `load` and `mutate` establish a new authoritative epoch. `refresh` joins the
 * current epoch, so an in-flight refresh is discarded as soon as a newer load
 * or mutation starts.
 */
export class ProjectRequestSessionController {
	private epoch = 0;
	private readonly options: ProjectRequestSessionControllerOptions;

	constructor(options: ProjectRequestSessionControllerOptions) {
		this.options = options;
	}

	invalidate() {
		this.epoch += 1;
	}

	async load<T>(projectId: string, request: () => Promise<T>): Promise<T | null> {
		this.invalidate();
		return this.run({ projectId, epoch: this.epoch }, request);
	}

	async refresh<T>(projectId: string, request: () => Promise<T>): Promise<T | null> {
		return this.run({ projectId, epoch: this.epoch }, request);
	}

	/**
	 * Start a read whose result supersedes every earlier read in this session.
	 * This is intended for a single independently refreshed projection where an
	 * older response must never be allowed to land after a newer one.
	 */
	async refreshLatest<T>(projectId: string, request: () => Promise<T>): Promise<T | null> {
		this.invalidate();
		return this.run({ projectId, epoch: this.epoch }, request);
	}

	async mutate<T>(projectId: string, request: () => Promise<T>): Promise<T | null> {
		this.invalidate();
		return this.run({ projectId, epoch: this.epoch }, request);
	}

	private async run<T>(token: ProjectRequestToken, request: () => Promise<T>) {
		const result = await request();
		return this.isCurrent(token) ? result : null;
	}

	private isCurrent(token: ProjectRequestToken) {
		return (
			token.projectId === this.options.getActiveProjectId()
			&& token.epoch === this.epoch
		);
	}
}
