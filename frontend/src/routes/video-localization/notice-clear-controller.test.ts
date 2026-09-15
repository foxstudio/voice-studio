import { afterEach, describe, expect, it, vi } from 'vitest';
import { NoticeClearController } from './notice-clear-controller';

afterEach(() => {
	vi.useRealTimers();
});

describe('NoticeClearController', () => {
	it('lets only the latest scheduled message clear itself', () => {
		vi.useFakeTimers();
		const controller = new NoticeClearController();
		let message = '第一条消息';

		controller.schedule({
			message,
			delayMs: 1_000,
			getMessage: () => message,
			clear: () => (message = '')
		});
		vi.advanceTimersByTime(500);
		message = '第二条消息';
		controller.schedule({
			message,
			delayMs: 1_000,
			getMessage: () => message,
			clear: () => (message = '')
		});

		vi.advanceTimersByTime(500);
		expect(message).toBe('第二条消息');
		vi.advanceTimersByTime(500);
		expect(message).toBe('');
	});

	it('does not clear a newer unscheduled message', () => {
		vi.useFakeTimers();
		const controller = new NoticeClearController();
		let message = '短提示';
		controller.schedule({
			message,
			delayMs: 1_000,
			getMessage: () => message,
			clear: () => (message = '')
		});

		message = '正在执行长期任务';
		vi.advanceTimersByTime(1_000);

		expect(message).toBe('正在执行长期任务');
	});

	it('cancels pending work when the page is disposed', () => {
		vi.useFakeTimers();
		const controller = new NoticeClearController();
		let message = '页面即将卸载';
		controller.schedule({
			message,
			delayMs: 1_000,
			getMessage: () => message,
			clear: () => (message = '')
		});

		controller.dispose();
		vi.runAllTimers();

		expect(message).toBe('页面即将卸载');
	});
});
