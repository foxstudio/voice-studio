<script lang="ts">
	import { Api } from '$lib/api';
	import type { HistoryItem } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { FileAudio, Layers } from 'lucide-svelte';

	let history = $state<HistoryItem[]>([]);
	let selected = $state<string[]>([]);
	let format = $state('wav');
	let normalize = $state(false);
	let exportPath = $state('');
	$effect(() => { Api.history().then((h) => (history = h)); });
	async function merge() {
		const rec = await Api.createExport({ result_ids: selected, format, silence_ms: 300, normalize });
		exportPath = rec.path;
	}

	const help = [
		{ title: '音频工具做什么', body: '这里处理已经生成好的音频，不负责重新生成。常见用法是从历史记录里勾选多个片段，按顺序合并导出成一个 WAV/MP3/FLAC 文件。' },
		{ title: '什么时候用', body: '视频项目如果已经生成了分段音频，可以先在这里检查每段是否可听；需要做整段试听或交给剪辑软件时，再合并导出。' },
		{ title: '音量标准化', body: '勾选后会尽量统一音量，适合多段来源不一致时使用。正式成片前仍建议人工听一遍，确认没有突兀停顿或音量跳变。' }
	];
</script>

<svelte:head><title>音频工具 - 声音工作台</title></svelte:head>
<main class="page">
	<div class="page-head">
		<div><h1>音频工具</h1><p class="muted">从历史结果中选择片段，合并导出、转换格式和统一音量</p></div>
		<div class="row"><HelpDrawer title="音频工具" sections={help} /><button class="btn primary" onclick={merge} disabled={selected.length === 0}><Layers size={15} /> 合并导出</button></div>
	</div>
	<div class="workbench">
		<section class="panel audio-panel">
			<table class="table audio-table">
				<thead><tr><th></th><th>文本</th><th>引擎</th><th>音频</th></tr></thead>
				<tbody>{#each history as item}<tr><td><input type="checkbox" checked={selected.includes(item.result_id)} onchange={(e) => { const checked = (e.currentTarget as HTMLInputElement).checked; selected = checked ? [...selected, item.result_id] : selected.filter((x) => x !== item.result_id); }} /></td><td>{item.input_text.slice(0, 70)}</td><td>{item.engine_id}</td><td><audio class="audio" controls src={`/api/history/${item.result_id}/audio`}></audio></td></tr>{/each}</tbody>
			</table>
		</section>
		<aside class="panel stack">
			<h2><FileAudio size={16} /> 导出参数</h2>
			<div class="field"><label for="fmt">格式</label><select id="fmt" bind:value={format}><option value="wav">WAV</option><option value="mp3">MP3</option><option value="flac">FLAC</option></select></div>
			<label for="norm"><input id="norm" type="checkbox" bind:checked={normalize} /> 音量标准化</label>
			{#if exportPath}<p class="badge ok">{exportPath}</p>{/if}
		</aside>
	</div>
</main>

<style>
	.audio-panel {
		overflow-x: auto;
	}

	.audio-table th:nth-child(4),
	.audio-table td:nth-child(4) {
		min-width: 340px;
		width: 42%;
	}

	@media (max-width: 760px) {
		.audio-table,
		.audio-table thead,
		.audio-table tbody,
		.audio-table tr,
		.audio-table th,
		.audio-table td {
			display: block;
			width: 100%;
		}

		.audio-table thead {
			display: none;
		}

		.audio-table tr {
			border: 1px solid var(--line);
			border-radius: 7px;
			padding: 10px;
			margin-bottom: 10px;
			background: #101215;
		}

		.audio-table td {
			border-bottom: 0;
			padding: 6px 0;
		}
	}
</style>
