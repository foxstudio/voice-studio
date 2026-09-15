import { describe, expect, it } from 'vitest';
import {
	BACKEND_OFFLINE_FAILURE_THRESHOLD,
	connectedBackendHealth,
	disconnectedBackendHealth,
	failedBackendHealth,
	initialBackendHealth
} from './backend-health';

describe('backend health presentation', () => {
	it('starts with an explicit connection check', () => {
		expect(initialBackendHealth).toMatchObject({
			kind: 'checking',
			label: '正在连接本地服务',
			detail: '正在确认语音与视频处理是否可用',
			responseStatus: 'checking'
		});
	});

	it('reports a ready backend as online', () => {
		expect(connectedBackendHealth({
			status: 'ok',
			version: '1.2.0',
			runtime_ready: true,
			optional_runtime_ready: true
		}, 42)).toEqual({
			kind: 'online',
			label: '本地服务可用',
			shortLabel: '服务可用',
			detail: '语音与视频处理可正常使用 · 版本 1.2.0',
			responseStatus: 'ok',
			checkedAt: 42
		});
	});

	it('keeps an older healthy response compatible when runtime readiness is absent', () => {
		expect(connectedBackendHealth({ status: 'ok' }).kind).toBe('online');
	});

	it('names missing runtime capabilities instead of calling the backend normal', () => {
		expect(connectedBackendHealth({
			status: 'ok',
			runtime_ready: true,
			optional_runtime_ready: false,
			runtime_capabilities: { mlx_audio: true, audio_separator: false }
		})).toMatchObject({
			kind: 'degraded',
			label: '部分功能还没准备好',
			detail: '暂时不能使用：音轨分离能力'
		});
	});

	it('keeps the older combined readiness contract compatible', () => {
		expect(connectedBackendHealth({
			status: 'ok',
			runtime_ready: false,
			runtime_capabilities: { mlx_audio: false }
		})).toMatchObject({
			kind: 'degraded',
			detail: '暂时不能使用：本地语音能力'
		});
	});

	it('uses readable labels for missing media tools', () => {
		expect(connectedBackendHealth({
			status: 'ok',
			runtime_ready: true,
			optional_runtime_ready: false,
			runtime_capabilities: { ffmpeg: false, ffprobe: false }
		})).toMatchObject({
			kind: 'degraded',
			detail: '暂时不能使用：音视频转换能力、媒体信息读取能力'
		});
	});

	it('preserves a non-ok server lifecycle state', () => {
		expect(connectedBackendHealth({ status: 'restarting' })).toMatchObject({
			kind: 'degraded',
			label: '本地服务正在重新启动',
			detail: '已经连上服务，等它恢复后就能继续使用',
			responseStatus: 'restarting'
		});
	});

	it('turns a failed health request into a reconnecting offline state', () => {
		expect(disconnectedBackendHealth(84)).toEqual({
			kind: 'offline',
			label: '暂时连不上本地服务',
			shortLabel: '正在重连',
			detail: '正在自动重连；如果一直没有恢复，请确认本地服务已经启动',
			responseStatus: 'offline',
			checkedAt: 84
		});
	});

	it('treats isolated health-check failures as a slow response instead of an outage', () => {
		expect(failedBackendHealth(1, 84)).toEqual({
			kind: 'degraded',
			label: '本地服务响应较慢',
			shortLabel: '响应较慢',
			detail: '健康检查暂时没有响应，正在自动重试',
			responseStatus: 'slow',
			checkedAt: 84
		});
		expect(failedBackendHealth(BACKEND_OFFLINE_FAILURE_THRESHOLD - 1).kind).toBe('degraded');
	});

	it('reports offline only after consecutive health-check failures', () => {
		expect(failedBackendHealth(BACKEND_OFFLINE_FAILURE_THRESHOLD, 126)).toEqual(
			disconnectedBackendHealth(126)
		);
	});
});
