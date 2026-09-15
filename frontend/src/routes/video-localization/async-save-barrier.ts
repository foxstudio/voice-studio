export class AsyncSaveBarrier {
	private pending = new Set<Promise<boolean>>();

	track(work: Promise<boolean>): Promise<void> {
		const guarded = work.catch(() => false);
		this.pending.add(guarded);
		void guarded.finally(() => this.pending.delete(guarded));
		return guarded.then(() => undefined);
	}

	async flush() {
		let succeeded = true;
		while (this.pending.size) {
			const results = await Promise.all([...this.pending]);
			succeeded = results.every(Boolean) && succeeded;
		}
		return succeeded;
	}

	get size() {
		return this.pending.size;
	}
}
