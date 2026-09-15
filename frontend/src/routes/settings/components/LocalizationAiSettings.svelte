<script lang="ts">
	import { Api } from '$lib/api';
	import type {
		AppSettings,
		LlmProviderProfile
	} from '$lib/api/types';
	import { Languages, SlidersHorizontal } from 'lucide-svelte';
	import { onMount } from 'svelte';

	let {
		settings,
		onChange
	}: {
		settings: AppSettings;
		onChange: () => void;
	} = $props();

	let profiles = $state<LlmProviderProfile[]>([]);
	let loadError = $state('');
	const enabledProfiles = $derived(
		profiles.filter((item) => item.enabled)
	);

	onMount(async () => {
		try {
			const response = await Api.llmProfiles();
			profiles = response.profiles;
		} catch (error) {
			loadError =
				error instanceof Error
					? error.message
					: '无法读取语言模型配置';
		}
	});

	function profileLabel(profile: LlmProviderProfile) {
		const source =
			profile.protocol === 'codex_cli'
				? 'ChatGPT 订阅'
				: 'API';
		return `${profile.name} · ${profile.model_id || '默认模型'} · ${source}`;
	}
</script>

<section class="localization-ai" aria-labelledby="localization-ai-title">
	<header>
		<div class="title">
			<Languages size={17} />
			<div>
				<h2 id="localization-ai-title">视频本土化 AI 策略</h2>
				<p>默认自动分配；只有质量门不通过时，才提升思考或使用单独配置。</p>
			</div>
		</div>
		{#if loadError}
			<span class="error" role="alert">{loadError}</span>
		{/if}
	</header>

	<div class="primary-grid">
		<label>
			<span>运行策略</span>
			<select
				id="localization-ai-strategy"
				bind:value={settings.video_localization_ai_strategy}
				onchange={onChange}
			>
				<option value="auto">自动平衡（推荐）</option>
				<option value="quality">质量优先</option>
				<option value="cost">成本优先</option>
			</select>
			<small>
				{settings.video_localization_ai_strategy === 'quality'
					? '所有关键模型步骤使用更深思考；时间更长。'
					: settings.video_localization_ai_strategy === 'cost'
						? '固定使用低成本配置；质量门失败时不自动升级。'
						: '理解和质检从低成本开始，只升级明确失败的创作或时间歧义步骤。'}
			</small>
		</label>
		<div class="summary">
			<strong>不是一套模型包办全部</strong>
			<span>全文理解、中文创作、双重质检和时间歧义可以分别选择模型；本地语义映射不消耗 LLM 流量。</span>
		</div>
	</div>

	<details>
		<summary><SlidersHorizontal size={15} />高级分工</summary>
		<div class="advanced-grid">
			<label>
				<span>提示词策略</span>
				<select
					bind:value={settings.video_localization_prompt_strategy}
					onchange={onChange}
				>
					<option value="adaptive">按内容动态补充规则（推荐）</option>
					<option value="fixed">固定基础规则</option>
				</select>
			</label>
			<label>
				<span>全文理解</span>
				<select
					bind:value={settings.video_localization_understanding_profile_id}
					onchange={onChange}
				>
					<option value="">跟随默认语言模型</option>
					{#each enabledProfiles as profile}
						<option value={profile.profile_id}>{profileLabel(profile)}</option>
					{/each}
				</select>
			</label>
			<label>
				<span>中文创作</span>
				<select
					bind:value={settings.video_localization_creation_profile_id}
					onchange={onChange}
				>
					<option value="">跟随默认语言模型</option>
					{#each enabledProfiles as profile}
						<option value={profile.profile_id}>{profileLabel(profile)}</option>
					{/each}
				</select>
			</label>
			<label>
				<span>原意与中文质检</span>
				<select
					bind:value={settings.video_localization_review_profile_id}
					onchange={onChange}
				>
					<option value="">跟随默认语言模型</option>
					{#each enabledProfiles as profile}
						<option value={profile.profile_id}>{profileLabel(profile)}</option>
					{/each}
				</select>
			</label>
			<label>
				<span>时间歧义复核</span>
				<select
					bind:value={settings.video_localization_alignment_profile_id}
					onchange={onChange}
				>
					<option value="">跟随默认语言模型</option>
					{#each enabledProfiles as profile}
						<option value={profile.profile_id}>{profileLabel(profile)}</option>
					{/each}
				</select>
			</label>
		</div>
		<p class="note">
			“自动输出”会让事实与质检步骤使用 JSON，中文创作使用更稳定的分章节 Markdown，再由程序校验完整性。
		</p>
	</details>
</section>

<style>
	.localization-ai {
		overflow: hidden;
		border: 1px solid rgba(148, 163, 184, .16);
		border-radius: 11px;
		background: rgba(17, 22, 30, .82);
		color: #dfe5ec;
	}
	header,
	.title,
	summary {
		display: flex;
		align-items: center;
	}
	header {
		justify-content: space-between;
		gap: 14px;
		min-height: 62px;
		padding: 10px 14px;
		border-bottom: 1px solid rgba(148, 163, 184, .12);
	}
	.title { gap: 10px; }
	.title > :global(svg) { color: #69a7e8; }
	h2,
	p { margin: 0; }
	h2 { color: #eef2f6; font-size: 14px; font-weight: 680; }
	header p,
	.note,
	label small,
	.summary span { color: #7f8b99; font-size: 10px; line-height: 1.5; }
	.error { color: #ff9297; font-size: 10px; }
	.primary-grid {
		display: grid;
		grid-template-columns: minmax(260px, .75fr) minmax(280px, 1.25fr);
		gap: 18px;
		padding: 14px;
	}
	label,
	.summary { display: grid; gap: 6px; }
	label > span,
	.summary strong { color: #cfd6de; font-size: 11px; font-weight: 630; }
	select {
		width: 100%;
		min-height: 34px;
		padding: 0 30px 0 10px;
		border: 1px solid #2c3541;
		border-radius: 7px;
		background: #0d1218;
		color: #e4e9ee;
		font-size: 11px;
	}
	.summary {
		align-content: center;
		padding: 10px 12px;
		border: 1px solid rgba(148, 163, 184, .11);
		border-radius: 8px;
		background: rgba(11, 15, 20, .48);
	}
	details { border-top: 1px solid rgba(148, 163, 184, .12); }
	summary {
		gap: 7px;
		min-height: 40px;
		padding: 0 14px;
		color: #9eabb9;
		cursor: pointer;
		font-size: 11px;
		list-style: none;
	}
	summary::-webkit-details-marker { display: none; }
	.advanced-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px 16px;
		padding: 2px 14px 14px;
	}
	.note { padding: 0 14px 14px; }
	@media (max-width: 760px) {
		.primary-grid,
		.advanced-grid { grid-template-columns: 1fr; }
	}
</style>
