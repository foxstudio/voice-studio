<script lang="ts">
	import { Api } from '$lib/api';
	import type { EvaluationAudioSample, EvaluationReport } from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import { Download, FileJson, FileText, Music, RefreshCw, TableProperties } from 'lucide-svelte';

	let report = $state<EvaluationReport | null>(null);
	let loading = $state(true);
	let error = $state('');
	let selectedEngine = $state('all');

	const quickRules = [
		{ label: '默认旁白', params: 'IndexTTS v2 / calm / emo_alpha 0.6 / speed 1.0', use: '课程、口播、批量生成的首选基线' },
		{ label: '轻情绪', params: 'happy 或 sad / emo_alpha 0.35', use: '商业解说、轻微情绪变化，不容易过戏' },
		{ label: '强情绪', params: 'happy 或 sad / emo_alpha 0.85', use: '片头、转折、高潮、短句强调' },
		{ label: '教程慢讲', params: 'speed 0.82-0.95', use: '重点说明、复杂概念、屏幕录制配音' },
		{ label: '快节奏口播', params: 'speed 1.1-1.22', use: '信息密集短视频，需人工确认咬字' },
		{ label: '长文本剪辑', params: 'segment 45 / silence 400-650ms', use: '按段落卡点、切画面、做字幕节奏' }
	];

	$effect(() => {
		loadReport();
	});

	async function loadReport() {
		loading = true;
		error = '';
		try {
			report = await Api.latestEvaluation();
		} catch (err) {
			error = err instanceof Error ? err.message : '无法读取评测报告';
		} finally {
			loading = false;
		}
	}

	function formatNumber(value: unknown, digits = 2) {
		return typeof value === 'number' ? value.toFixed(digits) : '-';
	}

	function formatPercent(value: unknown) {
		return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '-';
	}

	function engineOptions(samples: EvaluationAudioSample[]) {
		return ['all', ...Array.from(new Set(samples.map((sample) => sample.engine_id)))];
	}

	function filteredSamples(samples: EvaluationAudioSample[]) {
		return selectedEngine === 'all' ? samples : samples.filter((sample) => sample.engine_id === selectedEngine);
	}

	function compactParams(sample: EvaluationAudioSample) {
		const params = sample.params;
		const keys = ['emotion', 'emo_alpha', 'emotion_text', 'speed', 'temperature', 'max_text_tokens_per_segment', 'interval_silence'];
		return keys
			.filter((key) => params[key] !== null && params[key] !== undefined && params[key] !== '')
			.map((key) => `${key}: ${params[key]}`)
			.join(' / ');
	}

	const help = [
		{ title: '这个页面解决什么问题', body: '它不是生成入口，而是参数样本库。你可以听不同参数生成出来的声音，查看时长、RMS、静音比例，再决定正式批量生成时用哪套参数。' },
		{ title: '怎么用', body: '先听“样本试听”，再看每条样本的参数和预期效果。适合把“成功的参数组合”沉淀为批处理默认值或项目模板。' },
		{ title: '报告文件', body: 'DOCX / Markdown 适合人工复盘，CSV / Manifest 适合给 agent 或脚本读取。后续批处理结果也可以按这个思路沉淀成新的评测样本。' }
	];
</script>

<svelte:head><title>参数参考 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head">
		<div>
			<h1>参数参考</h1>
			<p class="muted">试听成功样本，理解哪些参数适合旁白、快讲、长文本和情绪表达</p>
		</div>
		<div class="row"><HelpDrawer title="参数参考" sections={help} /><button class="btn" onclick={loadReport} disabled={loading}><RefreshCw size={16} /> 刷新</button></div>
	</div>

	{#if loading}
		<div class="empty">正在读取最新评测包...</div>
	{:else if error}
		<div class="empty">{error}</div>
	{:else if report}
		<section class="grid eval-summary">
			<div class="card">
				<h2>成功率</h2>
				<strong>{report.success_count}/{report.total_count}</strong>
				<p class="muted">本轮深度样本生成</p>
			</div>
			<div class="card">
				<h2>运行批次</h2>
				<strong>{report.run_id}</strong>
				<p class="muted">自动读取最新评测目录</p>
			</div>
			<div class="card">
				<h2>音频样本</h2>
				<strong>{report.audio_samples.length}</strong>
				<p class="muted">WAV，可直接导入剪辑软件</p>
			</div>
			<div class="card">
				<h2>主要结论</h2>
				<strong>IndexTTS v2</strong>
				<p class="muted">当前主力中文口播与剪辑母版</p>
			</div>
		</section>

		<section class="panel reference-actions">
			<div>
				<h2>报告文件</h2>
				<p class="muted">{report.report_dir}</p>
			</div>
			<div class="row">
				<a class="btn primary" href={report.files.docx}><Download size={16} /> DOCX</a>
				<a class="btn" href={report.files.markdown}><FileText size={16} /> Markdown</a>
				<a class="btn" href={report.files.metrics}><TableProperties size={16} /> CSV</a>
				<a class="btn" href={report.files.manifest}><FileJson size={16} /> Manifest</a>
			</div>
		</section>

		<section class="panel">
			<h2>参数速查</h2>
			<div class="rule-grid">
				{#each quickRules as rule}
					<div class="rule">
						<strong>{rule.label}</strong>
						<span>{rule.params}</span>
						<p>{rule.use}</p>
					</div>
				{/each}
			</div>
		</section>

		<section class="panel">
			<div class="section-head">
				<div>
					<h2>样本试听</h2>
					<p class="muted">每条样本保留生成参数、预期效果和客观音频指标</p>
				</div>
				<div class="field compact-filter">
					<label for="engine-filter">引擎</label>
					<select id="engine-filter" bind:value={selectedEngine}>
						{#each engineOptions(report.audio_samples) as engine}
							<option value={engine}>{engine === 'all' ? '全部引擎' : engine}</option>
						{/each}
					</select>
				</div>
			</div>

			<div class="sample-list">
				{#each filteredSamples(report.audio_samples) as sample, index}
					<article class="sample">
						<div class="sample-main">
							<div class="row">
								<span class="badge ok">#{index + 1}</span>
								<span class="badge">{sample.engine_id}</span>
							</div>
							<h3>{sample.title}</h3>
							<p>{sample.text}</p>
							<p class="muted">{sample.expectation}</p>
							<div class="params">{compactParams(sample)}</div>
							<audio class="audio" controls src={sample.audio_url} preload="none">
								<track kind="captions" />
							</audio>
						</div>
						<div class="metrics">
							<div><span>时长</span><strong>{formatNumber(sample.metrics.duration_sec, 2)}s</strong></div>
							<div><span>峰值</span><strong>{formatNumber(sample.metrics.peak, 3)}</strong></div>
							<div><span>RMS</span><strong>{formatNumber(sample.metrics.rms, 3)}</strong></div>
							<div><span>静音</span><strong>{formatPercent(sample.metrics.silence_ratio)}</strong></div>
							<a class="icon-btn" href={sample.audio_url} title="下载 WAV"><Music size={17} /></a>
						</div>
					</article>
				{/each}
			</div>
		</section>
	{/if}
</main>

<style>
	.eval-summary strong {
		display: block;
		font-size: 24px;
		margin-bottom: 4px;
	}

	.reference-actions {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		margin-top: 16px;
	}

	.rule-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		gap: 10px;
	}

	.rule {
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 12px;
		background: #121519;
		display: grid;
		gap: 5px;
	}

	.rule span {
		color: #b9c7d8;
		font-size: 13px;
	}

	.rule p {
		color: var(--muted);
		font-size: 13px;
		margin: 0;
		line-height: 1.45;
	}

	.section-head {
		display: flex;
		align-items: end;
		justify-content: space-between;
		gap: 14px;
		margin-bottom: 12px;
	}

	.compact-filter {
		width: 180px;
	}

	.sample-list {
		display: grid;
		gap: 12px;
	}

	.sample {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 14px;
		display: grid;
		grid-template-columns: minmax(0, 1fr) 240px;
		gap: 16px;
		background: #14171b;
	}

	.sample-main {
		display: grid;
		gap: 8px;
	}

	.sample-main h3,
	.sample-main p {
		margin: 0;
	}

	.sample-main p {
		line-height: 1.55;
	}

	.params {
		color: #c8d6e6;
		background: #101215;
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 8px 10px;
		font-size: 12px;
		line-height: 1.45;
	}

	.metrics {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 8px;
		align-content: start;
	}

	.metrics div {
		border: 1px solid var(--line);
		border-radius: 7px;
		padding: 9px;
		background: #101215;
		display: grid;
		gap: 2px;
	}

	.metrics span {
		color: var(--muted);
		font-size: 12px;
	}

	.metrics strong {
		font-size: 15px;
	}

	.metrics .icon-btn {
		grid-column: span 2;
		width: 100%;
	}

	@media (max-width: 900px) {
		.reference-actions,
		.section-head {
			align-items: stretch;
			flex-direction: column;
		}

		.compact-filter {
			width: 100%;
		}

		.sample {
			grid-template-columns: 1fr;
		}
	}
</style>
