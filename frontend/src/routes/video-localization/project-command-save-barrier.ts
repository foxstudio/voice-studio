export type ProjectCommandSaveBarrierOptions<Result> = {
	flushPendingSave: () => Promise<boolean>;
	command: () => Promise<Result>;
	saveFailureMessage: string;
};

export async function runProjectCommandAfterSave<Result>({
	flushPendingSave,
	command,
	saveFailureMessage
}: ProjectCommandSaveBarrierOptions<Result>): Promise<Result> {
	if (!(await flushPendingSave())) throw new Error(saveFailureMessage);
	return command();
}
