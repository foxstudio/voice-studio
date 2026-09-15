export type NoticeClearRequest = {
	message: string;
	delayMs: number;
	getMessage: () => string;
	clear: () => void;
};

export class NoticeClearController {
	private generation = 0;
	private timer: ReturnType<typeof setTimeout> | null = null;

	schedule(request: NoticeClearRequest) {
		this.cancel();
		const generation = ++this.generation;
		this.timer = setTimeout(() => {
			if (generation !== this.generation) return;
			this.timer = null;
			if (request.getMessage() === request.message) request.clear();
		}, request.delayMs);
	}

	cancel() {
		this.generation += 1;
		if (this.timer) clearTimeout(this.timer);
		this.timer = null;
	}

	dispose() {
		this.cancel();
	}
}
