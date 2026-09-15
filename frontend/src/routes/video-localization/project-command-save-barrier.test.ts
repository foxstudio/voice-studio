import { describe, expect, it, vi } from 'vitest';
import { runProjectCommandAfterSave } from './project-command-save-barrier';

describe('project command save barrier', () => {
	it('waits for pending saves before running a draft-dependent command', async () => {
		let releaseSave!: (saved: boolean) => void;
		const flushPendingSave = vi.fn(
			() => new Promise<boolean>((resolve) => (releaseSave = resolve))
		);
		const command = vi.fn(async () => 'exported');

		const result = runProjectCommandAfterSave({
			flushPendingSave,
			command,
			saveFailureMessage: '项目修改尚未保存，请重试'
		});

		await Promise.resolve();
		expect(flushPendingSave).toHaveBeenCalledTimes(1);
		expect(command).not.toHaveBeenCalled();

		releaseSave(true);

		await expect(result).resolves.toBe('exported');
		expect(command).toHaveBeenCalledTimes(1);
	});

	it('does not run the command when the pending save reports failure', async () => {
		const command = vi.fn(async () => 'exported');

		await expect(
			runProjectCommandAfterSave({
				flushPendingSave: async () => false,
				command,
				saveFailureMessage: '字幕修改尚未保存，请重试'
			})
		).rejects.toThrow('字幕修改尚未保存，请重试');

		expect(command).not.toHaveBeenCalled();
	});

	it('propagates a save exception without running the command', async () => {
		const command = vi.fn(async () => 'exported');

		await expect(
			runProjectCommandAfterSave({
				flushPendingSave: async () => {
					throw new Error('保存服务离线');
				},
				command,
				saveFailureMessage: '项目修改尚未保存，请重试'
			})
		).rejects.toThrow('保存服务离线');

		expect(command).not.toHaveBeenCalled();
	});
});
