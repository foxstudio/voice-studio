import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const component = readFileSync(new URL('./LlmSettings.svelte', import.meta.url), 'utf8');
const api = readFileSync(new URL('../../../lib/api/index.ts', import.meta.url), 'utf8');
const types = readFileSync(new URL('../../../lib/api/types.ts', import.meta.url), 'utf8');

describe('LLM provider settings wiring', () => {
	it('keeps API and Codex CLI behind the same profile protocol', () => {
		expect(types).toContain("'openai_compatible' | 'codex_cli'");
		expect(component).toContain('本机 Codex（ChatGPT 订阅）');
		expect(component).toContain("draft.protocol === 'openai_compatible'");
		expect(component).toContain("draft.protocol === 'codex_cli'");
	});

	it('uses the dedicated Codex auth and status endpoints', () => {
		expect(api).toContain("'/settings/codex-cli/status'");
		expect(api).toContain("'/settings/codex-cli/login'");
		expect(api).toContain("'/settings/codex-cli/logout'");
		expect(api).toContain("'/settings/codex-cli/relogin'");
		expect(component).toContain('subscription_usable');
		expect(component).toContain('supports_image_input');
		expect(component).toContain('startCodexPolling');
		expect(types).toContain("status: 'started' | 'logged_out'");
	});

	it('does not expose private account or token fields in the status card', () => {
		expect(component).not.toContain('codexStatus?.email');
		expect(component).not.toContain('codexStatus?.token');
		expect(component).toContain('codexStatus?.executable_path');
		expect(component).not.toContain("runCodexAction('relogin')");
		expect(component).not.toContain('重新登录其他账号');
		expect(component).toContain('使用 ChatGPT 登录');
		expect(component).toContain("runCodexAction('logout')");
		expect(component).toMatch(
			/{#if codexStatus\?\.logged_in}[\s\S]*runCodexAction\('logout'\)[\s\S]*{:else}[\s\S]*runCodexAction\('login'\)/
		);
		expect(component).toContain('会消耗少量 ChatGPT 订阅额度');
		expect(component).toContain('测试模型回复');
	});

	it('lets Codex CLI profiles select a model and reasoning depth', () => {
		expect(types).toContain("export type LlmReasoningEffort = 'default' | 'low' | 'high' | 'max'");
		expect(component).toContain('模型 ID');
		expect(component).toContain('跟随 Codex 默认模型');
		expect(component).toContain('思考深度');
		expect(component).toContain('<option value="low">低');
		expect(component).toContain('reasoning_effort: draft.reasoning_effort');
		expect(component).not.toContain("draft.model_id = ''");
		expect(component).toContain('model-settings-row');
		expect(component).toContain('获取当前账号可用模型');
		expect(component).toContain("chooseFetchedModel('')");
	});
});
