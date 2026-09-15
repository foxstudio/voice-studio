import { writable } from 'svelte/store';

export type BackendHealthResponse = {
	status: string;
	version?: string;
	uptime_seconds?: number;
	runtime_ready?: boolean;
	optional_runtime_ready?: boolean;
	runtime_capabilities?: Record<string, boolean>;
	platform_capabilities?: {
		schema_version: number;
		operating_system: string;
		architecture: string;
		python_version: string;
		available_devices: string[];
		preferred_device: string;
		frameworks: Record<string, boolean>;
		optional_capabilities: Record<string, boolean>;
	};
};

export type BackendHealthState = {
	kind: 'checking' | 'online' | 'degraded' | 'offline';
	label: string;
	shortLabel: string;
	detail: string;
	responseStatus: string;
	checkedAt: number | null;
};

export const initialBackendHealth: BackendHealthState = {
	kind: 'checking',
	label: '正在连接本地服务',
	shortLabel: '正在连接',
	detail: '正在确认语音与视频处理是否可用',
	responseStatus: 'checking',
	checkedAt: null
};

export const backendHealth = writable<BackendHealthState>(initialBackendHealth);
export const BACKEND_OFFLINE_FAILURE_THRESHOLD = 3;

function unavailableCapabilityLabel(capability: string) {
	const labels: Record<string, string> = {
		mlx_audio: '本地语音能力',
		audio_separator: '音轨分离能力',
		ffmpeg: '音视频转换能力',
		ffprobe: '媒体信息读取能力'
	};
	return labels[capability] ?? capability;
}

function nonOkStatusLabel(status: string) {
	const normalized = status.trim().toLowerCase();
	if (normalized === 'starting') return '本地服务正在启动';
	if (normalized === 'restarting') return '本地服务正在重新启动';
	if (normalized === 'maintenance') return '本地服务正在维护';
	if (normalized === 'degraded') return '部分功能暂时不可用';
	return `服务状态异常（${status || '未知'}）`;
}

export function connectedBackendHealth(
	response: BackendHealthResponse,
	checkedAt = Date.now()
): BackendHealthState {
	const responseStatus = String(response.status || 'unknown');
	if (responseStatus.toLowerCase() !== 'ok') {
		const label = nonOkStatusLabel(responseStatus);
		return {
			kind: 'degraded',
			label,
			shortLabel: label.replace(/^本地服务/, ''),
			detail: '已经连上服务，等它恢复后就能继续使用',
			responseStatus,
			checkedAt
		};
	}

	const optionalRuntimeReady = response.optional_runtime_ready ?? response.runtime_ready;
	if (optionalRuntimeReady === false) {
		const unavailable = Object.entries(response.runtime_capabilities ?? {})
			.filter(([, ready]) => !ready)
			.map(([capability]) => unavailableCapabilityLabel(capability));
		return {
			kind: 'degraded',
			label: '部分功能还没准备好',
			shortLabel: '功能准备中',
			detail: unavailable.length
				? `暂时不能使用：${unavailable.join('、')}`
				: '有些功能仍在准备，请稍后再试',
			responseStatus,
			checkedAt
		};
	}

	return {
		kind: 'online',
		label: '本地服务可用',
		shortLabel: '服务可用',
		detail: response.version
			? `语音与视频处理可正常使用 · 版本 ${response.version}`
			: '语音与视频处理可以正常使用',
		responseStatus,
		checkedAt
	};
}

export function disconnectedBackendHealth(checkedAt = Date.now()): BackendHealthState {
	return {
		kind: 'offline',
		label: '暂时连不上本地服务',
		shortLabel: '正在重连',
		detail: '正在自动重连；如果一直没有恢复，请确认本地服务已经启动',
		responseStatus: 'offline',
		checkedAt
	};
}

export function failedBackendHealth(
	consecutiveFailures: number,
	checkedAt = Date.now()
): BackendHealthState {
	if (consecutiveFailures >= BACKEND_OFFLINE_FAILURE_THRESHOLD) {
		return disconnectedBackendHealth(checkedAt);
	}
	return {
		kind: 'degraded',
		label: '本地服务响应较慢',
		shortLabel: '响应较慢',
		detail: '健康检查暂时没有响应，正在自动重试',
		responseStatus: 'slow',
		checkedAt
	};
}
