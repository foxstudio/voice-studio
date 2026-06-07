<script lang="ts">
	import { Api } from '$lib/api';
	import { Hash, Scissors, Sparkles } from 'lucide-svelte';

	let text = $state('');
	let segments = $state<string[]>([]);
	async function clean() { text = (await Api.cleanText(text)).text; }
	async function split() { segments = (await Api.splitText(text)).segments; }
	async function numbers() { text = (await Api.normalizeNumbers(text)).text; }
</script>

<svelte:head><title>文本工具 - 声音工作台</title></svelte:head>
<main class="page">
	<div class="page-head"><div><h1>文本工具</h1><p class="muted">分句、清洗、数字规范化和标签辅助</p></div></div>
	<div class="workbench">
		<section class="panel stack">
			<textarea bind:value={text} placeholder="粘贴脚本文本"></textarea>
			<div class="toolbar"><button class="btn" onclick={clean}><Sparkles size={15} /> 清洗</button><button class="btn" onclick={split}><Scissors size={15} /> 分句</button><button class="btn" onclick={numbers}><Hash size={15} /> 数字</button><button class="btn" onclick={() => (text += ' [laughter]')}>笑声</button><button class="btn" onclick={() => (text += ' pinyin()')}>拼音</button></div>
		</section>
		<aside class="panel stack">
			<h2>分句结果</h2>
			{#each segments as seg, i}<div class="card"><span class="muted">{i + 1}</span> {seg}</div>{/each}
		</aside>
	</div>
</main>
