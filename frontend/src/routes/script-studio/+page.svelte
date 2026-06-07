<script lang="ts">
	import { Api } from '$lib/api';
	import type { EngineDetail, Project, Role, ScriptSegment, VoiceAsset } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { segmentStatusLabel } from '$lib/labels';
	import { Mic, Plus, Rows3, Send, Trash2 } from 'lucide-svelte';

	let projects = $state<Project[]>([]);
	let current = $state<Project | null>(null);
	let voices = $state<VoiceAsset[]>([]);
	let engines = $state<EngineDetail[]>([]);
	let newProjectName = $state('新脚本项目');
	let roleName = $state('旁白');
	let bulkText = $state('');

	async function refresh() {
		[projects, voices, engines] = await Promise.all([Api.projects(), Api.voices(), Api.engines()]);
		if (!current && projects[0]) current = projects[0];
		if (current) current = projects.find((p) => p.project_id === current?.project_id) ?? current;
	}
	$effect(() => { refresh(); });

	async function createProject() {
		current = await Api.createProject(newProjectName);
		await refresh();
	}

	async function deleteCurrentProject() {
		if (!current) return;
		await Api.deleteProject(current.project_id);
		current = null;
		await refresh();
	}

	async function addRole() {
		if (!current) return;
		const role: Role = { role_id: crypto.randomUUID().slice(0, 12), name: roleName, color: '#4f9cf9', default_voice_id: voices[0]?.voice_id ?? null, default_engine_id: 'indextts-v2', default_language: 'zh', default_emotion: 'calm', default_speed: 1 };
		current = await Api.addRole(current.project_id, role);
	}

	async function importSegments() {
		if (!current) return;
		const lines = bulkText.split(/\n+/).map((x) => x.trim()).filter(Boolean);
		const base = current.segments.length;
		const role = current.roles[0];
		const segs: ScriptSegment[] = [...current.segments, ...lines.map((line, i) => ({
			segment_id: crypto.randomUUID().slice(0, 12),
			index: base + i,
			text: line,
			role_id: role?.role_id ?? null,
			voice_id: role?.default_voice_id ?? null,
			engine_id: role?.default_engine_id ?? 'indextts-v2',
			language: 'zh',
			emotion: role?.default_emotion ?? 'calm',
			speed: 1,
			status: 'ready' as const,
			result_audio_id: null,
			result_id: null,
			error_message: null,
			locked: false
		}))];
		current = await Api.putSegments(current.project_id, segs);
		bulkText = '';
	}

	async function saveSegments() {
		if (current) current = await Api.putSegments(current.project_id, current.segments);
	}
	async function generateProject() {
		if (!current) return;
		await saveSegments();
		await Api.generateProject(current.project_id);
		await refresh();
	}

	const help = [
		{ title: '脚本工作台做什么', body: '这里适合把长稿拆成多段，给不同角色绑定不同声音，然后批量生成。它偏 WebUI 内部项目管理；真正给其他 agent 调用时，优先使用 /api/batches/generate 或 scripts/voice_studio_batch.py。' },
		{ title: '如何导入段落', body: '把逐字稿按行粘贴到“导入段落”，每行会变成一个可单独生成的片段。之后可以在表格里修改声音、引擎、情绪和状态。' },
		{ title: 'Agent 批处理入口', body: 'web-video-presentation 可先运行 npm run extract-narrations 得到 audio-segments.json，再调用 scripts/voice_studio_batch.py audio-segments.json --voice <voice_id> --output-dir presentation/public/audio --wait。' }
	];
</script>

<svelte:head><title>脚本工作台 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head">
		<div><h1>脚本工作台</h1><p class="muted">多段落、多角色、批量配音和 agent 批处理入口</p></div>
		<div class="row"><HelpDrawer title="脚本工作台" sections={help} /><button class="btn primary" onclick={generateProject} disabled={!current}><Send size={15} /> 批量生成</button></div>
	</div>
	<div class="workbench">
		<section class="panel stack">
			<div class="toolbar">
				<select bind:value={current}>{#each projects as p}<option value={p}>{p.name}</option>{/each}</select>
				<input style="max-width:220px" bind:value={newProjectName} />
				<button class="btn" onclick={createProject}><Plus size={15} /> 新建</button>
				<button class="btn danger" onclick={deleteCurrentProject} disabled={!current}><Trash2 size={15} /> 删除项目</button>
			</div>
			<div class="batch-note">
				<strong>给其他 agent 的批处理方式</strong>
				<p class="muted">POST /api/batches/generate 或运行 scripts/voice_studio_batch.py。兼容 web-video-presentation 的 audio-segments.json。</p>
			</div>
			{#if current}
				<h2>{current.name}</h2>
				<table class="table">
					<thead><tr><th>#</th><th>文本</th><th>角色</th><th>声音</th><th>引擎</th><th>状态</th></tr></thead>
					<tbody>
						{#each current.segments as seg}
							<tr>
								<td>{seg.index + 1}</td>
								<td><textarea bind:value={seg.text} style="min-height:70px"></textarea></td>
								<td><select bind:value={seg.role_id}><option value={null}>无</option>{#each current.roles as r}<option value={r.role_id}>{r.name}</option>{/each}</select></td>
								<td><select bind:value={seg.voice_id}><option value={null}>无</option>{#each voices as v}<option value={v.voice_id}>{v.name}</option>{/each}</select></td>
								<td><select bind:value={seg.engine_id}>{#each engines as e}<option value={e.manifest.engine_id}>{e.manifest.display_name}</option>{/each}</select></td>
								<td><span class="badge" class:ok={seg.status === 'completed'} class:fail={seg.status === 'failed'}>{segmentStatusLabel(seg.status)}</span></td>
							</tr>
						{/each}
					</tbody>
				</table>
				<button class="btn" onclick={saveSegments}>保存段落</button>
			{:else}
				<div class="empty">先创建一个项目</div>
			{/if}
		</section>
		<aside class="panel stack">
			<h2><Mic size={16} /> 角色</h2>
			<div class="row"><input bind:value={roleName} /><button class="btn" onclick={addRole} disabled={!current}><Plus size={15} /> 添加</button></div>
			{#if current}{#each current.roles as role}<div class="card"><strong>{role.name}</strong><p class="muted">{role.default_engine_id} · {role.default_voice_id ?? '未绑定声音'}</p></div>{/each}{/if}
			<h2><Rows3 size={16} /> 导入段落</h2>
			<textarea bind:value={bulkText} placeholder="每行一个段落"></textarea>
			<button class="btn" onclick={importSegments} disabled={!current || !bulkText.trim()}>导入</button>
		</aside>
	</div>
</main>

<style>
	.batch-note {
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
		padding: 10px;
		display: grid;
		gap: 4px;
		margin-bottom: 12px;
	}

	.batch-note p {
		margin: 0;
	}
</style>
