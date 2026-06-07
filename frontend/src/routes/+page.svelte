<script lang="ts">
	import { Api } from '$lib/api';
	import type { EngineDetail, HistoryItem, Project, VoiceAsset } from '$lib/api/types';
	import { engineStatusLabel } from '$lib/labels';
	import { ArrowRight, Mic2, Plus } from 'lucide-svelte';

	let engines = $state<EngineDetail[]>([]);
	let voices = $state<VoiceAsset[]>([]);
	let history = $state<HistoryItem[]>([]);
	let projects = $state<Project[]>([]);

	$effect(() => {
		Promise.all([Api.engines(), Api.voices(), Api.history(), Api.projects()]).then(([e, v, h, p]) => {
			engines = e;
			voices = v;
			history = h;
			projects = p;
		});
	});
</script>

<svelte:head><title>总览 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head">
		<div>
			<h1>总览</h1>
			<p class="muted">模型状态、声音资产、最近生成和脚本项目总览</p>
		</div>
		<a class="btn primary" href="/generate"><Mic2 size={16} /> 新生成</a>
	</div>

	<section class="grid">
		<div class="card"><h2>引擎</h2><strong>{engines.filter((e) => e.state.status === 'loaded').length}/{engines.length}</strong><p class="muted">已加载 / 总数</p></div>
		<div class="card"><h2>声音</h2><strong>{voices.length}</strong><p class="muted">可复用参考声音</p></div>
		<div class="card"><h2>历史</h2><strong>{history.length}</strong><p class="muted">生成结果</p></div>
		<div class="card"><h2>项目</h2><strong>{projects.length}</strong><p class="muted">脚本工作台项目</p></div>
	</section>

	<div class="split" style="margin-top:16px">
		<section class="panel">
			<h2>引擎状态</h2>
			<table class="table">
				<tbody>
					{#each engines as engine}
						<tr>
							<td>{engine.manifest.display_name}</td>
							<td><span class="badge" class:ok={engine.state.status === 'loaded'}>{engineStatusLabel(engine.state.status)}</span></td>
							<td class="muted">{engine.manifest.default_use_case}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</section>
		<section class="panel">
			<div class="row" style="justify-content:space-between"><h2>快速入口</h2><a class="btn" href="/voice-library"><Plus size={15} /> 声音</a></div>
			<div class="stack">
				<a class="card row" href="/engine-hub">配置本地引擎 <ArrowRight size={16} /></a>
				<a class="card row" href="/script-studio">创建脚本项目 <ArrowRight size={16} /></a>
				<a class="card row" href="/eval-reference">查看评测参考 <ArrowRight size={16} /></a>
				<a class="card row" href="/history">打开生成历史 <ArrowRight size={16} /></a>
			</div>
		</section>
	</div>
</main>
