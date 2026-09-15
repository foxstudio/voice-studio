<script lang="ts">
	import { Api } from '$lib/api';
	import type { VideoLocalizationDraft, DubbingGroupPreflightResult, DubbingRecoveryDecision } from '$lib/api/types';
	let { draft, projectId }: { draft: VideoLocalizationDraft; projectId: string } = $props();
	let groupId = $state('');
	let busy = $state(false);
	let message = $state('');
	let result = $state<DubbingGroupPreflightResult | null>(null);
	let phrases = $state('');
	let reason = $state('');
	let decision = $state<DubbingRecoveryDecision | null>(null);
	const plan = $derived(draft.dubbing_production?.active_plan);
	const group = $derived(plan?.groups.find((item) => item.group_id === groupId));
	function reset() {
		result = null; message = ''; decision = null;
		for (const task of [...(draft.tts_tasks ?? [])].reverse()) {
			for (const stage of task.stages) {
				const saved = stage.parameters.video_localization_recovery as DubbingRecoveryDecision | undefined;
				if (saved?.group_id === groupId && saved.plan_revision === plan?.plan_revision && saved.source_revision === plan?.source_revision && task.status !== 'cancelled') { decision = saved; break; }
			}
			if (decision) break;
		}
		phrases = decision?.phrases.join('\n') ?? group?.spoken_text ?? '';
		reason = decision?.reason ?? '';
	}
	async function check() {
		busy = true;
		try { result = await Api.videoLocalizationDubbingPreflight(projectId, groupId); message = result.message; }
		catch (error) { message = error instanceof Error ? error.message : String(error); }
		finally { busy = false; }
	}
	async function recover() {
		if (!plan || !group || !plan.plan_revision) return;
		busy = true;
		try {
			// Reuse the durable decision on repeated clicks. Editing explicitly starts a new decision.
			decision ??= { schema_version: 'dubbing-recovery-decision-v1', recovery_id: crypto.randomUUID().replaceAll('-', '').slice(0, 12), source_revision: plan.source_revision, plan_revision: plan.plan_revision, group_id: groupId, stage: 'semantic_phrases', phrases: phrases.split('\n').map((text) => text.trim()).filter(Boolean), reference_cue_ids: [], reason: reason.trim() };
			const response = await Api.recoverVideoLocalizationDubbing(projectId, decision);
			message = response.message;
		} catch (error) { message = error instanceof Error ? error.message : String(error); }
		finally { busy = false; }
	}
</script>

{#if plan}
	<details class="recovery-panel">
		<summary>生成检查与分段恢复</summary>
		<label>配音组
			<select aria-label="配音组" bind:value={groupId} onchange={reset} disabled={busy}>
				<option value="">选择配音组</option>
				{#each plan.groups as item, index}<option value={item.group_id}>{index + 1}. {item.spoken_text.slice(0, 32)}</option>{/each}
			</select>
		</label>
		<button onclick={check} disabled={!group || busy}>检查可用时长</button>
		{#if result}<p>可用 {(result.usable_duration_ms / 1000).toFixed(2)} 秒 · 预计 {result.estimated_speech_duration_ms == null ? '暂无可靠参照' : `${(result.estimated_speech_duration_ms / 1000).toFixed(2)} 秒`}</p>{/if}
		{#if group}
			<details>
				<summary>整组重试仍失败：按语义分段</summary>
				<p>每行一个完整短语，保留全部原文和标点。沿用原音色及速度；成功短语会保留。已完成组不会被覆盖。</p>
				<label>分段文本<textarea bind:value={phrases} oninput={() => decision = null} disabled={busy} rows="4"></textarea></label>
				<label>分段理由<input bind:value={reason} oninput={() => decision = null} disabled={busy} /></label>
				<button onclick={recover} disabled={busy || !reason.trim() || phrases.split('\n').filter((text) => text.trim()).length < 2}>{busy ? '处理中…' : '提交或接回分段恢复'}</button>
			</details>
		{/if}
		{#if message}<p role="status">{message}</p>{/if}
	</details>
{/if}

<style>
	.recovery-panel { padding: .6rem; border: 1px solid var(--border-color, #39434b); border-radius: 6px; font-size: .75rem; }
	summary { cursor: pointer; }
	label { display: grid; gap: .25rem; margin: .5rem 0; }
	select, textarea, input { width: 100%; min-width: 0; box-sizing: border-box; }
	p { line-height: 1.5; overflow-wrap: anywhere; }
	button { margin: .25rem 0; }
</style>
