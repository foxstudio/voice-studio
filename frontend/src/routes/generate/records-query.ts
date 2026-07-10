import type { TaskDateFilter } from './helpers';

const STATUS_ALIASES: Record<string, string> = {
	成功: 'success',
	异常: 'failed',
	失败: 'failed',
	取消: 'cancelled',
	队列: 'queued',
	等待: 'queued',
	生成中: 'running'
};

export function taskServerQuery(value: string) {
	const query = value.trim();
	return STATUS_ALIASES[query] ?? query;
}

export function taskDateStartIso(value: TaskDateFilter, now = new Date()) {
	if (value === 'all') return undefined;
	if (value === 'today') return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
	const days = value === '7d' ? 7 : 30;
	return new Date(now.getTime() - days * 24 * 60 * 60 * 1000).toISOString();
}
