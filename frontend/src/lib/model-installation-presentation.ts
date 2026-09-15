import type { EngineInstallation } from './api/types';

const SOURCE_SECURITY_BLOCKERS = new Set([
	'official_source_not_https',
	'download_source_missing',
	'download_source_not_https'
]);
const INTEGRITY_BLOCKERS = new Set([
	'model_revision_not_pinned',
	'model_checksum_missing'
]);

export function modelInstallationStatus(installation: EngineInstallation): string {
	if (installation.installation_status === 'installing') return '正在安装';
	if (installation.installation_status === 'failed') return '安装失败';
	if (installation.runtime_ready === true) return '模型与环境已就绪';
	if (installation.installed) return '模型已在本机';
	return '模型未安装';
}

export function modelInstallationGuidance(installation: EngineInstallation): string | null {
	if (installation.installed && installation.runtime_ready === false) {
		const detail = Array.isArray(installation.runtime_detail)
			? installation.runtime_detail.join('、')
			: installation.runtime_detail;
		if (installation.runtime_status === 'reference_preprocessing_missing') {
			return detail || '主模型已经转换，但上传参考音频还缺少配套模型；请按来源说明补齐。';
		}
		return detail
			? `模型文件已经存在，但运行环境未就绪：${detail}`
			: '模型文件已经存在，但运行环境未就绪；请运行环境检查查看缺少的依赖。';
	}

	if (installation.installed || installation.automatic_download_supported) return null;
	const blockers = new Set(installation.automatic_download_blockers ?? []);
	if (blockers.has('model_license_unverified') || blockers.has('model_license_missing')) {
		return '自动下载未开放：模型权重许可尚未完整核验。请查看官方来源并按其许可手动安装。';
	}
	if ([...blockers].some((blocker) => SOURCE_SECURITY_BLOCKERS.has(blocker))) {
		return '自动下载未开放：下载来源尚未通过安全校验。请使用页面列出的官方来源。';
	}
	if ([...blockers].some((blocker) => INTEGRITY_BLOCKERS.has(blocker))) {
		return '自动下载未开放：固定版本或完整性校验信息不完整。请按官方说明手动安装。';
	}
	if (blockers.has('license_acceptance_id_missing')) {
		return '自动下载未开放：许可确认配置不完整。请查看官方许可后手动安装。';
	}
	if (blockers.has('managed_install_not_supported')) {
		return '此模型需要按官方说明手动安装，当前暂不支持一键下载。';
	}
	return '当前暂不支持一键下载，请查看官方来源和安装说明。';
}
