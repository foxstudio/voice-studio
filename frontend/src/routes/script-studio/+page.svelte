<script lang="ts">
	import { Api } from '$lib/api';
	import type { EngineDetail, Project, Role, ScriptSegment, VoiceAsset } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { segmentStatusLabel } from '$lib/labels';
	import { Mic, Plus, Rows3, Send, SlidersHorizontal, Trash2 } from 'lucide-svelte';

	let projects = $state<Project[]>([]);
	let current = $state<Project | null>(null);
	let voices = $state<VoiceAsset[]>([]);
	let engines = $state<EngineDetail[]>([]);
	let newProjectName = $state('新脚本项目');
	let roleName = $state('旁白');
	let bulkText = $state('');
	let selectedSegmentId = $state<string | null>(null);
	const ttsEngines = $derived(engines.filter((engine) => !engine.manifest.capabilities.includes('speech_recognition')));
	const selectedSegment = $derived(current?.segments.find((seg) => seg.segment_id === selectedSegmentId) ?? current?.segments[0] ?? null);

	async function refresh() {
		[projects, voices, engines] = await Promise.all([Api.projects(), Api.voices(), Api.engines()]);
		if (!current && projects[0]) current = projects[0];
		if (current) current = projects.find((p) => p.project_id === current?.project_id) ?? current;
	}
	$effect(() => { refresh(); });
	$effect(() => {
		if (current?.segments.length && !current.segments.some((seg) => seg.segment_id === selectedSegmentId)) {
			selectedSegmentId = current.segments[0].segment_id;
		}
	});

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
		const role: Role = { role_id: crypto.randomUUID().slice(0, 12), name: roleName, color: '#4f9cf9', default_voice_id: voices[0]?.voice_id ?? null, default_engine_id: 'indextts-v2', default_language: 'zh', default_emotion: 'calm', default_speed: 1, default_parameters: {} };
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
			source_start_ms: null,
			source_end_ms: null,
			role_id: role?.role_id ?? null,
			voice_id: role?.default_voice_id ?? null,
			engine_id: role?.default_engine_id ?? 'indextts-v2',
			language: 'zh',
			emotion: role?.default_emotion ?? 'calm',
			speed: null,
			status: 'ready' as const,
			result_audio_id: null,
			result_id: null,
			error_message: null,
			locked: false,
			parameters: {}
		}))];
		current = await Api.putSegments(current.project_id, segs);
		selectedSegmentId = segs.at(-1)?.segment_id ?? selectedSegmentId;
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

	function projectParam(key: string, fallback: string | number = '') {
		return current?.parameters?.[key] ?? fallback;
	}

	function setProjectParam(key: string, value: string | number | null) {
		if (!current) return;
		current.parameters = { ...(current.parameters ?? {}), [key]: value };
	}

	function segmentParam(seg: ScriptSegment | null, key: string, fallback: string | number = '') {
		return seg?.parameters?.[key] ?? fallback;
	}

	function setSegmentParam(seg: ScriptSegment | null, key: string, value: string | number | null) {
		if (!seg) return;
		seg.parameters = { ...(seg.parameters ?? {}), [key]: value };
	}

	function numericValue(value: unknown, fallback: number) {
		const next = Number(value);
		return Number.isFinite(next) ? next : fallback;
	}

	const help = [
		{ title: '脚本工作台做什么', body: '这里适合把长稿拆成多段，给不同角色绑定不同声音，然后批量生成。它偏 WebUI 内部项目管理；真正给其他 agent 调用时，优先使用 /api/batches/generate 或 scripts/voice_studio_batch.py。' },
		{ title: '如何导入段落', body: '把逐字稿按行粘贴到“导入段落”，每行会变成一个可单独生成的片段。之后可以在表格里修改声音、引擎、情绪和状态。' },
		{ title: 'Agent 批处理入口', body: 'web-video-presentation 可先运行 npm run extract-narrations 得到 audio-segments.json，再调用 scripts/voice_studio_batch.py audio-segments.json --voice <voice_id> --output-dir presentation/public/audio --wait。' }
	];
</script>

<svelte:head><title>脚本与批量 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head">
		<div><h1>脚本与批量</h1><p class="muted">多段落、多角色、批量配音和 agent 批处理入口</p></div>
		<div class="row"><HelpDrawer title="脚本与批量" sections={help} /><button class="btn primary" onclick={generateProject} disabled={!current}><Send size={15} /> 批量生成</button></div>
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
				<table class="table segment-table">
					<thead><tr><th>#</th><th>文本</th><th>来源时间</th><th>角色</th><th>声音</th><th>引擎</th><th>状态</th><th>参数</th></tr></thead>
					<tbody>
						{#each current.segments as seg}
							<tr class:selected={seg.segment_id === selectedSegmentId}>
								<td>{seg.index + 1}</td>
								<td><textarea bind:value={seg.text} style="min-height:70px"></textarea></td>
								<td>{#if seg.source_start_ms !== null && seg.source_end_ms !== null}<span class="badge">{Math.floor(seg.source_start_ms / 1000 / 60)}:{Math.floor(seg.source_start_ms / 1000 % 60).toString().padStart(2, '0')} - {Math.floor(seg.source_end_ms / 1000 / 60)}:{Math.floor(seg.source_end_ms / 1000 % 60).toString().padStart(2, '0')}</span>{:else}<span class="muted">-</span>{/if}</td>
								<td><select bind:value={seg.role_id}><option value={null}>无</option>{#each current.roles as r}<option value={r.role_id}>{r.name}</option>{/each}</select></td>
								<td><select bind:value={seg.voice_id}><option value={null}>无</option>{#each voices as v}<option value={v.voice_id}>{v.name}</option>{/each}</select></td>
									<td><select bind:value={seg.engine_id}>{#each ttsEngines as e}<option value={e.manifest.engine_id}>{e.manifest.display_name}</option>{/each}</select></td>
								<td><span class="badge" class:ok={seg.status === 'completed'} class:fail={seg.status === 'failed'}>{segmentStatusLabel(seg.status)}</span></td>
								<td>
									<button class="btn icon-text" onclick={() => (selectedSegmentId = seg.segment_id)}>
										<SlidersHorizontal size={15} /> 编辑
									</button>
								</td>
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

			<h2><SlidersHorizontal size={16} /> 项目默认参数</h2>
			<div class="param-grid">
				<label class="field">
					<span>输出格式</span>
					<select value={String(projectParam('output_format', 'wav'))} onchange={(e) => setProjectParam('output_format', (e.currentTarget as HTMLSelectElement).value)}>
						<option value="wav">WAV</option>
						<option value="mp3">MP3</option>
						<option value="flac">FLAC</option>
					</select>
				</label>
				<label class="field">
					<span>Temperature</span>
					<input type="number" min="0.1" max="2" step="0.05" value={projectParam('temperature', 0.8)} oninput={(e) => setProjectParam('temperature', numericValue((e.currentTarget as HTMLInputElement).value, 0.8))} />
				</label>
				<label class="field">
					<span>Top-P</span>
					<input type="number" min="0" max="1" step="0.05" value={projectParam('top_p', 0.8)} oninput={(e) => setProjectParam('top_p', numericValue((e.currentTarget as HTMLInputElement).value, 0.8))} />
				</label>
				<label class="field">
					<span>分段 Token</span>
					<input type="number" min="10" max="500" step="10" value={projectParam('max_text_tokens_per_segment', 120)} oninput={(e) => setProjectParam('max_text_tokens_per_segment', numericValue((e.currentTarget as HTMLInputElement).value, 120))} />
				</label>
			</div>

			<h2><SlidersHorizontal size={16} /> 当前段落参数</h2>
			{#if selectedSegment}
				<div class="selected-segment">
					<strong>{selectedSegment.text.slice(0, 36) || '未命名段落'}</strong>
					<p class="muted">这些参数只覆盖当前段落；空值会使用项目或角色默认值。</p>
				</div>
				<div class="field">
					<label for="seg-style">风格指令</label>
					<input id="seg-style" value={String(segmentParam(selectedSegment, 'style_instruction', ''))} oninput={(e) => setSegmentParam(selectedSegment, 'style_instruction', (e.currentTarget as HTMLInputElement).value || null)} />
				</div>
				<div class="field">
					<label for="seg-design">MiMo 声音设计</label>
					<textarea id="seg-design" rows="3" value={String(segmentParam(selectedSegment, 'voice_design_prompt', ''))} oninput={(e) => setSegmentParam(selectedSegment, 'voice_design_prompt', (e.currentTarget as HTMLTextAreaElement).value || null)}></textarea>
				</div>
				<div class="param-grid">
					<label class="field">
						<span>MiMo 音色</span>
						<input value={String(segmentParam(selectedSegment, 'mimo_voice', ''))} placeholder="冰糖 / Mia / ..." oninput={(e) => setSegmentParam(selectedSegment, 'mimo_voice', (e.currentTarget as HTMLInputElement).value || null)} />
					</label>
					<label class="field">
						<span>语速</span>
						<input type="number" min="0.5" max="3" step="0.05" value={selectedSegment.speed ?? ''} placeholder="默认" oninput={(e) => (selectedSegment.speed = (e.currentTarget as HTMLInputElement).value ? numericValue((e.currentTarget as HTMLInputElement).value, 1) : null)} />
					</label>
					<label class="field">
						<span>情绪强度</span>
						<input type="number" min="0" max="1" step="0.05" value={segmentParam(selectedSegment, 'emo_alpha', '')} placeholder="默认" oninput={(e) => setSegmentParam(selectedSegment, 'emo_alpha', (e.currentTarget as HTMLInputElement).value ? numericValue((e.currentTarget as HTMLInputElement).value, 0.6) : null)} />
					</label>
					<label class="field">
						<span>扩散步数</span>
						<input type="number" min="1" max="100" step="1" value={segmentParam(selectedSegment, 'diffusion_steps', '')} placeholder="默认" oninput={(e) => setSegmentParam(selectedSegment, 'diffusion_steps', (e.currentTarget as HTMLInputElement).value ? numericValue((e.currentTarget as HTMLInputElement).value, 25) : null)} />
					</label>
				</div>
			{:else}
				<div class="empty">选择一个段落后编辑参数</div>
			{/if}

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

	.segment-table tr.selected {
		background: rgba(79, 156, 249, 0.08);
	}

	.segment-table textarea {
		min-width: 260px;
	}

	.param-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 10px;
	}

	.param-grid .field {
		gap: 5px;
	}

	.param-grid input,
	.param-grid select {
		min-height: 34px;
	}

	.selected-segment {
		display: grid;
		gap: 4px;
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #10151b;
		padding: 10px;
	}

	.selected-segment p {
		margin: 0;
	}

	@media (max-width: 720px) {
		.param-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
