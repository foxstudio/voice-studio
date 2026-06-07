<script lang="ts">
	import { Api } from '$lib/api';
	import type { AppSettings, EngineDetail, VoiceAsset } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { Save } from 'lucide-svelte';

	let settings = $state<AppSettings | null>(null);
	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let saved = $state('');
	let mimoApiKey = $state('');
	let clearMimoKey = $state(false);
	const ttsEngines = $derived(engines.filter((engine) => !engine.manifest.capabilities.includes('speech_recognition')));
	$effect(() => {
		Promise.all([Api.settings(), Api.engines(), Api.voices()]).then(([s, e, v]) => {
			settings = s;
			engines = e;
			voices = v;
		});
	});
	async function save() {
		if (!settings) return;
		settings = await Api.saveSettings(settings);
		if (mimoApiKey.trim() || clearMimoKey) {
			settings = await Api.saveMimoSecret({ api_key: mimoApiKey.trim() || null, clear: clearMimoKey });
			mimoApiKey = '';
			clearMimoKey = false;
		}
		saved = '已保存';
		setTimeout(() => (saved = ''), 1600);
	}

		const help = [
			{ title: 'MiMo Token Plan 怎么用', body: '先开启云端引擎，再填写 Token Plan 专属 base URL 和 API Key。保存后，引擎中心会显示 MiMo 的预置音色、音色设计、音色复刻和 ASR；没有 key 时会提示不可用，不影响本地 IndexTTS 和 OmniVoice。' },
			{ title: '密钥安全', body: 'API Key 只保存在本地后端设置表里，前端只显示是否已配置，不会把 key 回填到输入框。需要换 key 时直接填新的；需要删除时勾选清除。' },
			{ title: '默认音色', body: '默认音色用于单条和批量生成的初始选择。可以设为狐狸，也可以以后换成别的本地参考音色。' },
			{ title: '目录设置', body: '声音目录保存参考音频，输出目录保存生成结果，批处理默认会在输出目录下创建 batches 子目录。视频项目批量合成时可以单独指定 presentation/public/audio。' }
		];
	</script>

<svelte:head><title>设置 - 声音工作台</title></svelte:head>
<main class="page">
	<div class="page-head">
		<div><h1>设置</h1><p class="muted">模型、输出、缓存、本地/云端策略和默认参数</p></div>
		<div class="row"><HelpDrawer title="设置" sections={help} /><button class="btn primary" onclick={save} disabled={!settings}><Save size={15} /> 保存</button></div>
	</div>
	{#if settings}
		<div class="split">
				<section class="panel stack">
					<h2>默认行为</h2>
					<div class="field">
						<label for="default-engine">默认引擎</label>
						<select id="default-engine" bind:value={settings.default_engine_id}>
							{#each ttsEngines as engine}<option value={engine.manifest.engine_id}>{engine.manifest.display_name}</option>{/each}
						</select>
					</div>
					<div class="field">
						<label for="default-voice">默认音色</label>
						<select id="default-voice" bind:value={settings.default_voice_id}>
							<option value={null}>不指定</option>
							{#each voices as voice}<option value={voice.voice_id}>{voice.name}</option>{/each}
						</select>
					</div>
					<div class="field"><label for="default-lang">默认语言</label><select id="default-lang" bind:value={settings.default_language}><option value="zh">中文</option><option value="en">英文</option><option value="auto">自动</option></select></div>
					<div class="field"><label for="default-format">默认格式</label><select id="default-format" bind:value={settings.default_output_format}><option value="wav">WAV</option><option value="mp3">MP3</option><option value="flac">FLAC</option></select></div>
					<div class="field"><label for="device">设备</label><select id="device" bind:value={settings.device}><option value="auto">自动</option><option value="mps">Apple 芯片 MPS</option><option value="cpu">CPU</option></select></div>
				<div class="field"><label for="cloud"><input id="cloud" type="checkbox" bind:checked={settings.cloud_enabled} /> 启用云端引擎</label></div>
			</section>
			<section class="panel stack">
				<h2>MiMo Token Plan</h2>
					<div class="field"><label for="mimo-base">专属 Base URL</label><input id="mimo-base" bind:value={settings.mimo_base_url} /></div>
					<div class="field"><label for="mimo-voice">默认 MiMo 音色</label><input id="mimo-voice" bind:value={settings.mimo_default_voice} placeholder="例如 mimo_default 或官方音色名" /></div>
					<div class="field"><label for="mimo-upload-confirm"><input id="mimo-upload-confirm" type="checkbox" bind:checked={settings.mimo_voiceclone_confirm_upload} /> MiMo 音色复刻每次生成前提醒云端上传</label></div>
					<div class="field">
					<label for="mimo-key">API Key（不会回显）</label>
					<input id="mimo-key" type="password" bind:value={mimoApiKey} placeholder={settings.mimo_api_key_configured ? '已配置；填写新 key 可覆盖' : '未配置'} />
				</div>
				<label for="mimo-clear"><input id="mimo-clear" type="checkbox" bind:checked={clearMimoKey} /> 清除已保存的 MiMo API Key</label>
				<span class="badge" class:ok={settings.mimo_api_key_configured} class:warn={!settings.mimo_api_key_configured}>{settings.mimo_api_key_configured ? 'MiMo Key 已配置' : 'MiMo Key 未配置'}</span>
				<p class="muted">Token Plan 专属入口默认使用 https://token-plan-cn.xiaomimimo.com/v1。</p>
			</section>
			<section class="panel stack">
				<h2>目录</h2>
				<div class="field"><label for="data-dir">数据目录</label><input id="data-dir" bind:value={settings.data_dir} /></div>
				<div class="field"><label for="model-dir">模型目录</label><input id="model-dir" bind:value={settings.model_dir} /></div>
				<div class="field"><label for="voice-dir">声音目录</label><input id="voice-dir" bind:value={settings.voice_dir} /></div>
				<div class="field"><label for="output-dir">输出目录</label><input id="output-dir" bind:value={settings.output_dir} /></div>
				<div class="field"><label for="export-dir">导出目录</label><input id="export-dir" bind:value={settings.export_dir} /></div>
				<div class="field"><label for="project-dir">项目目录</label><input id="project-dir" bind:value={settings.project_dir} /></div>
			</section>
		</div>
		{#if saved}<p class="badge ok">{saved}</p>{/if}
	{:else}
		<div class="empty">加载设置中</div>
	{/if}
</main>
