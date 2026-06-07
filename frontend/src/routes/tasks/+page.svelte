<script lang="ts">
	import { Api } from '$lib/api';
	import type { GenerationTask } from '$lib/api/types';
	import { taskStatusLabel, taskTypeLabel } from '$lib/labels';
	import { RotateCcw, X } from 'lucide-svelte';

	let tasks = $state<GenerationTask[]>([]);
	async function refresh() { tasks = await Api.tasks(); }
	$effect(() => { refresh(); const id = setInterval(refresh, 2500); return () => clearInterval(id); });
</script>

<svelte:head><title>任务队列 - 声音工作台</title></svelte:head>
<main class="page">
	<div class="page-head"><div><h1>任务队列</h1><p class="muted">查看、取消、重试生成任务</p></div><button class="btn" onclick={refresh}><RotateCcw size={15} /> 刷新</button></div>
	<section class="panel">
		<table class="table">
			<thead><tr><th>任务</th><th>类型</th><th>引擎</th><th>文本</th><th>状态</th><th>耗时</th><th>操作</th></tr></thead>
			<tbody>{#each tasks as task}<tr><td>{task.task_id}</td><td>{taskTypeLabel(task.task_type)}</td><td>{task.engine_id}</td><td>{task.input_text.slice(0, 60)}</td><td><span class="badge" class:ok={task.status === 'success'} class:fail={task.status === 'failed'}>{taskStatusLabel(task.status)}</span></td><td>{task.generation_time_ms ? (task.generation_time_ms / 1000).toFixed(1) + 's' : '-'}</td><td class="row"><button class="icon-btn" title="取消" onclick={async () => { await Api.cancelTask(task.task_id); await refresh(); }}><X size={14} /></button><button class="icon-btn" title="重试" onclick={async () => { await Api.retryTask(task.task_id); await refresh(); }}><RotateCcw size={14} /></button></td></tr>{/each}</tbody>
		</table>
	</section>
</main>
