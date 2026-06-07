<script lang="ts">
	import { Api } from '$lib/api';
	import type {
		EngineDetail,
		HistoryItem,
		Project,
		ScriptSegment,
		TranscriptionRecord,
		TranscriptionSegment,
		TranscriptionTask
	} from '$lib/api/types';
	import HelpDrawer from '$lib/components/HelpDrawer.svelte';
	import {
		ChevronLeft,
		ChevronRight,
		CheckSquare2,
		Copy,
		Download,
		FileAudio,
		Import,
		Languages,
		Layers,
		LoaderCircle,
		RefreshCw,
		Search,
		TextQuote,
		Trash2,
		UploadCloud
	} from 'lucide-svelte';
	import { onMount } from 'svelte';

	type EngineHealth = {
		healthy?: boolean;
		status?: string;
		detail?: string;
		[key: string]: unknown;
	};

	type UploadPreview = {
		id: string;
		name: string;
		sizeKb: number;
		url: string;
	};

	type TimestampStrategy = 'auto' | 'forced_aligner' | 'qwen3-asr-mlx';
	type AsrTaskTab = 'all' | 'active' | 'success' | 'failed';

	let history = $state<HistoryItem[]>([]);
	let transcriptions = $state<TranscriptionRecord[]>([]);
	let transcriptionTasks = $state<TranscriptionTask[]>([]);
	let projects = $state<Project[]>([]);
	let engines = $state<EngineDetail[]>([]);
	let asrHealth = $state<Record<string, EngineHealth>>({});

	let selected = $state<string[]>([]);
	let format = $state('wav');
	let normalize = $state(false);
	let exportPath = $state('');

	let asrFiles = $state<File[]>([]);
	let filePreviews = $state<UploadPreview[]>([]);
	let dragActive = $state(false);
	let asrEngineId = $state('mimo-v2.5-asr');
	let asrLanguage = $state<'auto' | 'zh' | 'en'>('auto');
	let asrMode = $state<'sync' | 'async'>('sync');
	let transcribing = $state(false);
	let submittingTask = $state(false);
	let supplementingTimestamps = $state(false);
	let transcript = $state<TranscriptionRecord | null>(null);
	let asrError = $state('');
	let asrInfo = $state('');
	let activeTaskId = $state<string | null>(null);
	let selectedTranscriptionId = $state<string | null>(null);
	let importProjectId = $state<string>('');
	let importMessage = $state('');
	let copyMessage = $state('');
	let timestampStrategy = $state<TimestampStrategy>('auto');

	let transcriptionQuery = $state('');
	let transcriptionEngineFilter = $state('all');
	let transcriptionTimestampFilter = $state<'all' | 'with_timestamps' | 'text_only'>('all');
	let selectedTranscriptionIds = $state<string[]>([]);
	let transcriptionPage = $state(1);
	let transcriptionPageSize = $state(8);
	let taskStatusTab = $state<AsrTaskTab>('all');
	let taskPage = $state(1);
	let taskPageSize = $state(6);

	const asrEngines = $derived(engines.filter((engine) => engine.manifest.capabilities.includes('speech_recognition')));
	const selectedAsrEngine = $derived(
		asrEngines.find((engine) => engine.manifest.engine_id === asrEngineId) ?? asrEngines[0] ?? null
	);
	const selectedAsrHealth = $derived(
		(selectedAsrEngine && asrHealth[selectedAsrEngine.manifest.engine_id]) || null
	);
	const activeTask = $derived(
		(activeTaskId && transcriptionTasks.find((task) => task.task_id === activeTaskId)) || null
	);
	const hasRunningTasks = $derived(
		transcriptionTasks.some((task) =>
			['pending', 'queued', 'running', 'retrying', 'postprocessing'].includes(task.status)
		)
	);
	const recommendAsync = $derived(asrFiles.reduce((total, file) => total + file.size, 0) >= 5 * 1024 * 1024 || asrFiles.length > 1);
	const taskCounts = $derived.by(() => ({
		all: transcriptionTasks.length,
		active: transcriptionTasks.filter((task) =>
			['pending', 'queued', 'running', 'retrying', 'postprocessing'].includes(task.status)
		).length,
		success: transcriptionTasks.filter((task) => task.status === 'success').length,
		failed: transcriptionTasks.filter((task) =>
			['failed', 'cancelled'].includes(task.status)
		).length
	}));
	const visibleTasks = $derived.by(() => {
		return transcriptionTasks.filter((task) => {
			if (taskStatusTab === 'active') {
				return ['pending', 'queued', 'running', 'retrying', 'postprocessing'].includes(task.status);
			}
			if (taskStatusTab === 'success') return task.status === 'success';
			if (taskStatusTab === 'failed') return ['failed', 'cancelled'].includes(task.status);
			return true;
		});
	});
	const taskPageCount = $derived(Math.max(1, Math.ceil(visibleTasks.length / taskPageSize)));
	const pagedTasks = $derived.by(() => {
		const start = (taskPage - 1) * taskPageSize;
		return visibleTasks.slice(start, start + taskPageSize);
	});
	const selectedImportProject = $derived(projects.find((project) => project.project_id === importProjectId) ?? null);
	const selectedTranscriptions = $derived(
		transcriptions.filter((item) => selectedTranscriptionIds.includes(item.transcription_id))
	);
	const visibleTranscriptions = $derived.by(() => {
		const query = transcriptionQuery.trim().toLowerCase();
		return transcriptions.filter((item) => {
			if (transcriptionEngineFilter !== 'all' && item.engine_id !== transcriptionEngineFilter) return false;
			if (transcriptionTimestampFilter === 'with_timestamps' && !item.segments.length) return false;
			if (transcriptionTimestampFilter === 'text_only' && item.segments.length) return false;
			if (!query) return true;
			return (
				item.filename.toLowerCase().includes(query) ||
				item.text.toLowerCase().includes(query) ||
				item.engine_id.toLowerCase().includes(query)
			);
		});
	});
	const transcriptionPageCount = $derived(
		Math.max(1, Math.ceil(visibleTranscriptions.length / transcriptionPageSize))
	);
	const pagedTranscriptions = $derived.by(() => {
		const start = (transcriptionPage - 1) * transcriptionPageSize;
		return visibleTranscriptions.slice(start, start + transcriptionPageSize);
	});
	const visibleTranscriptionIds = $derived(visibleTranscriptions.map((item) => item.transcription_id));
	const allVisibleSelected = $derived(
		visibleTranscriptionIds.length > 0 &&
			visibleTranscriptionIds.every((id) => selectedTranscriptionIds.includes(id))
	);
	const engineMap = $derived(new Map(engines.map((engine) => [engine.manifest.engine_id, engine])));

	async function refresh() {
		const [nextHistory, nextTranscriptions, nextEngines, nextTasks, nextProjects] = await Promise.all([
			Api.history(),
			Api.transcriptionHistory(),
			Api.engines(),
			Api.transcriptionTasks(),
			Api.projects()
		]);
		history = nextHistory;
		transcriptions = nextTranscriptions;
		transcriptionTasks = nextTasks;
		projects = nextProjects;
		engines = nextEngines;
		const nextAsrEngines = nextEngines.filter((engine) =>
			engine.manifest.capabilities.includes('speech_recognition')
		);
		if (nextAsrEngines.length && !nextAsrEngines.some((engine) => engine.manifest.engine_id === asrEngineId)) {
			asrEngineId = nextAsrEngines[0].manifest.engine_id;
		}
		if (nextProjects.length && !nextProjects.some((project) => project.project_id === importProjectId)) {
			importProjectId = nextProjects[0].project_id;
		}
		asrHealth = Object.fromEntries(
			await Promise.all(
				nextAsrEngines.map(async (engine) => [
					engine.manifest.engine_id,
					await Api.healthEngine(engine.manifest.engine_id)
				])
			)
		);

		if (selectedTranscriptionId) {
			const existing = nextTranscriptions.find(
				(item) => item.transcription_id === selectedTranscriptionId
			);
			if (existing) transcript = existing;
		}
	}

	onMount(() => {
		refresh();
		const id = setInterval(() => {
			if (hasRunningTasks) refresh();
		}, 2000);
		return () => {
			clearInterval(id);
			revokeFilePreviews();
		};
	});

	$effect(() => {
		if (taskPage > taskPageCount) taskPage = taskPageCount;
		if (transcriptionPage > transcriptionPageCount) transcriptionPage = transcriptionPageCount;
	});

	async function merge() {
		const rec = await Api.createExport({
			result_ids: selected,
			format,
			silence_ms: 300,
			normalize
		});
		exportPath = rec.path;
	}

	function setFiles(files: FileList | File[] | null) {
		if (!files) return;
		asrFiles = Array.from(files).filter((file) =>
			/\.(wav|mp3)$/i.test(file.name) || ['audio/wav', 'audio/mpeg', 'audio/mp3'].includes(file.type)
		);
		rebuildFilePreviews(asrFiles);
		asrInfo = asrFiles.length
			? `已选择 ${asrFiles.length} 个文件，共 ${Math.max(
					1,
					Math.round(asrFiles.reduce((sum, file) => sum + file.size, 0) / 1024)
				)} KB`
			: '';
		if (asrFiles.length > 1) asrMode = 'async';
	}

	function engineKind(engineId: string) {
		return engineMap.get(engineId)?.manifest.engine_type ?? (engineId.startsWith('mimo-') ? 'cloud' : 'local');
	}

	function engineTypeLabel(engineId: string) {
		return engineKind(engineId) === 'cloud' ? '云端' : '本地';
	}

	function clearFiles() {
		revokeFilePreviews();
		asrFiles = [];
		asrInfo = '';
	}

	function rebuildFilePreviews(files: File[]) {
		revokeFilePreviews();
		filePreviews = files.map((file, index) => ({
			id: `${file.name}-${index}`,
			name: file.name,
			sizeKb: Math.max(1, Math.round(file.size / 1024)),
			url: URL.createObjectURL(file)
		}));
	}

	function revokeFilePreviews() {
		for (const preview of filePreviews) URL.revokeObjectURL(preview.url);
		filePreviews = [];
	}

	async function transcribe() {
		if (!asrFiles.length || !selectedAsrEngine) return;
		if (asrMode === 'async' || asrFiles.length > 1) {
			submittingTask = true;
			asrError = '';
			try {
				const tasks = [];
				for (const file of asrFiles) {
					tasks.push(
						await Api.createTranscriptionTask(file, asrLanguage, selectedAsrEngine.manifest.engine_id)
					);
				}
				activeTaskId = tasks[0]?.task_id ?? null;
				transcript = null;
				selectedTranscriptionId = null;
				copyMessage = '';
				importMessage = '';
				asrInfo = `已提交 ${tasks.length} 个转写任务`;
				clearFiles();
				await refresh();
			} catch (err) {
				asrError = err instanceof Error ? err.message : '提交异步转写失败';
			} finally {
				submittingTask = false;
			}
			return;
		}
		transcribing = true;
		asrError = '';
		try {
			transcript = await Api.transcribeAudio(
				asrFiles[0],
				asrLanguage,
				selectedAsrEngine.manifest.engine_id
			);
			selectedTranscriptionId = transcript.transcription_id;
			activeTaskId = null;
			importMessage = '';
			copyMessage = '';
			clearFiles();
			await refresh();
			scrollToResult();
		} catch (err) {
			asrError = err instanceof Error ? err.message : '转写失败';
		} finally {
			transcribing = false;
		}
	}

	async function openTranscription(transcriptionId: string) {
		transcript = await Api.transcription(transcriptionId);
		selectedTranscriptionId = transcriptionId;
		activeTaskId = transcriptionTasks.find((task) => task.transcription_id === transcriptionId)?.task_id ?? null;
		importMessage = '';
		copyMessage = '';
		scrollToResult();
	}

	async function supplementTimestamps(record: TranscriptionRecord) {
		supplementingTimestamps = true;
		asrError = '';
		copyMessage = '';
		importMessage = '';
		try {
			const updated = await Api.supplementTranscriptionTimestamps(record.transcription_id, {
				strategy: timestampStrategy
			});
			transcript = updated;
			selectedTranscriptionId = updated.transcription_id;
			asrInfo =
				updated.timestamp_source_engine_id === 'qwen3-forced-aligner-0.6B'
					? '已完成精准强制对齐，现在可以导出 SRT。'
					: updated.timestamp_source_engine_id === 'qwen3-asr-mlx'
						? '已用本地 Qwen 快速补时间戳，现在可以导出 SRT。'
						: '已补充时间戳，现在可以导出 SRT。';
			await refresh();
			scrollToResult();
		} catch (err) {
			asrError = err instanceof Error ? err.message : '补时间戳失败';
		} finally {
			supplementingTimestamps = false;
		}
	}

	async function importTranscriptToProject() {
		if (!transcript || !selectedImportProject) return;
		const role = selectedImportProject.roles[0];
		const base = selectedImportProject.segments.length;
		const language = transcript.language === 'auto' ? 'zh' : transcript.language;
		const pieces = transcript.segments.length
			? transcript.segments.map((segment) => ({
					text: segment.text,
					source_start_ms: segment.start_ms,
					source_end_ms: segment.end_ms
				}))
			: splitTranscriptText(transcript.text).map((text) => ({
					text,
					source_start_ms: null,
					source_end_ms: null
				}));
		const imported: ScriptSegment[] = [
			...selectedImportProject.segments,
			...pieces.map((piece, index) => ({
				segment_id: crypto.randomUUID().slice(0, 12),
				index: base + index,
				text: piece.text,
				source_start_ms: piece.source_start_ms,
				source_end_ms: piece.source_end_ms,
				role_id: role?.role_id ?? null,
				voice_id: role?.default_voice_id ?? null,
				engine_id:
					role?.default_engine_id ??
					selectedImportProject.default_engine_id ??
					'indextts-v2',
				language,
				emotion: role?.default_emotion ?? 'calm',
				speed: role?.default_speed ?? 1,
				status: 'ready' as const,
				result_audio_id: null,
				result_id: null,
				error_message: null,
				locked: false
			}))
		];
		await Api.putSegments(selectedImportProject.project_id, imported);
		importMessage = `已导入 ${pieces.length} 段到 ${selectedImportProject.name}`;
		await refresh();
	}

	async function copyTranscript(text: string) {
		await navigator.clipboard.writeText(text);
		copyMessage = '已复制文字稿';
	}

	async function copySegments(segments: TranscriptionSegment[]) {
		const body = segments
			.map((segment) => `${segmentLabel(segment)}\n${segment.text}`)
			.join('\n\n');
		await navigator.clipboard.writeText(body);
		copyMessage = '已复制分段结果';
	}

	function asrExportHref(transcriptionId: string, format: 'txt' | 'srt') {
		return `/api/asr/${transcriptionId}/export?format=${format}`;
	}

	function canSupplement(record: TranscriptionRecord | null) {
		return !!record && !record.segments.length && record.has_source_audio;
	}

	function timestampBadge(record: TranscriptionRecord) {
		if (!record.segments.length) return '无时间戳';
		if (record.timestamp_source_engine_id === 'qwen3-forced-aligner-0.6B') return '精准强制对齐';
		if (record.timestamp_mode === 'supplemented') return '本地补时间戳';
		return '原生时间戳';
	}

	function timestampStrategyLabel(strategy: TimestampStrategy) {
		return {
			auto: '自动',
			forced_aligner: '精准 forced align',
			'qwen3-asr-mlx': '本地快速补齐'
		}[strategy];
	}

	function timestampStrategyHint() {
		return {
			auto: '优先尝试精准 forced align，不可用时自动回退到本地快速补齐。',
			forced_aligner: '只走精准 forced align；若模型不可用、音频过长或对齐失败，会直接报错。',
			'qwen3-asr-mlx': '直接用本地 Qwen 重新跑时间戳，速度更稳，但句级边界会更粗。'
		}[timestampStrategy];
	}

	function toggleTranscriptionSelection(transcriptionId: string, checked: boolean) {
		selectedTranscriptionIds = checked
			? [...selectedTranscriptionIds, transcriptionId]
			: selectedTranscriptionIds.filter((item) => item !== transcriptionId);
	}

	function clearSelectedTranscriptions() {
		selectedTranscriptionIds = [];
	}

	function toggleVisibleTranscriptions() {
		if (allVisibleSelected) {
			selectedTranscriptionIds = selectedTranscriptionIds.filter(
				(id) => !visibleTranscriptionIds.includes(id)
			);
			return;
		}
		selectedTranscriptionIds = Array.from(
			new Set([...selectedTranscriptionIds, ...visibleTranscriptionIds])
		);
	}

	async function deleteTranscriptionRecord(transcriptionId: string) {
		await Api.deleteTranscription(transcriptionId);
		if (selectedTranscriptionId === transcriptionId) {
			transcript = null;
			selectedTranscriptionId = null;
		}
		selectedTranscriptionIds = selectedTranscriptionIds.filter((item) => item !== transcriptionId);
		await refresh();
	}

	async function batchDeleteSelectedTranscriptions() {
		if (!selectedTranscriptionIds.length) return;
		await Api.batchDeleteTranscriptions(selectedTranscriptionIds);
		if (transcript && selectedTranscriptionIds.includes(transcript.transcription_id)) {
			transcript = null;
			selectedTranscriptionId = null;
		}
		asrInfo = `已删除 ${selectedTranscriptionIds.length} 条转写记录`;
		clearSelectedTranscriptions();
		await refresh();
	}

	async function batchSupplementSelectedTranscriptions() {
		if (!selectedTranscriptionIds.length) return;
		supplementingTimestamps = true;
		asrError = '';
		try {
			const updated = await Api.batchSupplementTranscriptionTimestamps(selectedTranscriptionIds, {
				strategy: timestampStrategy
			});
			const preciseCount = updated.filter(
				(item) => item.timestamp_source_engine_id === 'qwen3-forced-aligner-0.6B'
			).length;
			const coarseCount = updated.filter(
				(item) => item.timestamp_source_engine_id === 'qwen3-asr-mlx'
			).length;
			asrInfo = `已更新 ${updated.length} 条转写记录：精准对齐 ${preciseCount} 条，粗补 ${coarseCount} 条`;
			if (transcript) {
				const found = updated.find((item) => item.transcription_id === transcript?.transcription_id);
				if (found) transcript = found;
			}
			clearSelectedTranscriptions();
			await refresh();
		} catch (err) {
			asrError = err instanceof Error ? err.message : '批量补时间戳失败';
		} finally {
			supplementingTimestamps = false;
		}
	}

	function segmentLabel(segment: TranscriptionSegment) {
		return `${formatMs(segment.start_ms)} - ${formatMs(segment.end_ms)}`;
	}

	function formatMs(value: number) {
		const total = Math.max(0, Math.floor(value / 1000));
		const minutes = Math.floor(total / 60);
		const seconds = total % 60;
		return `${minutes}:${seconds.toString().padStart(2, '0')}`;
	}

	function splitTranscriptText(text: string) {
		return text
			.split(/\n+|(?<=[。！？!?])/)
			.map((item) => item.trim())
			.filter(Boolean);
	}

	function asrTaskStatusLabel(status: string) {
		return {
			pending: '待处理',
			queued: '排队中',
			running: '转写中',
			postprocessing: '整理中',
			success: '成功',
			failed: '失败',
			cancelled: '已取消',
			retrying: '重试中'
		}[status] ?? status;
	}

	function canDeleteTask(task: TranscriptionTask) {
		return !['pending', 'queued', 'running', 'retrying', 'postprocessing'].includes(task.status);
	}

	async function deleteTranscriptionTask(taskId: string) {
		if (!window.confirm('删除这条转写任务记录？已生成的转写结果不会被一并删除。')) return;
		await Api.deleteTranscriptionTask(taskId);
		if (activeTaskId === taskId) activeTaskId = null;
		await refresh();
	}

	function taskPageJump(delta: number) {
		const next = taskPage + delta;
		taskPage = Math.min(taskPageCount, Math.max(1, next));
	}

	function transcriptionPageJump(delta: number) {
		const next = transcriptionPage + delta;
		transcriptionPage = Math.min(transcriptionPageCount, Math.max(1, next));
	}

	function selectedTaskForRecord(record: TranscriptionRecord) {
		return transcriptionTasks.find((task) => task.transcription_id === record.transcription_id);
	}

	function scrollToResult() {
		requestAnimationFrame(() => {
			document.getElementById('transcript-result')?.scrollIntoView({
				behavior: 'smooth',
				block: 'start'
			});
		});
	}

	const help = [
		{
			title: '先看哪里',
			body: '现在一进来先是转写区。短音频可以同步直接返回，多个文件或更大的音频建议走异步任务。'
		},
		{
			title: 'MiMo 和 Qwen 的区别',
			body: 'MiMo 当前按官方公开接口提供文本稿；Qwen 本地链路支持分段时间戳。对 MiMo 记录，如果保留了源音频，现在会优先走本地 forced align 精准补时间戳。'
		},
		{
			title: '批量转写',
			body: '把多个 wav / mp3 一起拖进来时，会自动切到异步任务模式，逐个排队提交。'
		},
		{
			title: '后续使用',
			body: '转写完成后可以下载 TXT / SRT，也可以直接导入脚本工作台，继续走配音和批处理流程。'
		}
	];
</script>

<svelte:head><title>语音转写 - 声音工作台</title></svelte:head>

<main class="page">
	<div class="page-head">
		<div>
			<h1>语音转写</h1>
			<p class="muted">先转写，再导字幕、导脚本；历史音频合并放在下面，避免一进来就被旧记录淹没。</p>
		</div>
		<div class="row">
			<HelpDrawer title="语音转写" sections={help} />
			<button class="btn" onclick={refresh}><RefreshCw size={15} /> 刷新</button>
		</div>
	</div>

	<section class="hero-grid">
		<section class="panel stack asr-panel">
			<div class="row" style="justify-content:space-between">
				<h2><Languages size={16} /> ASR 转写</h2>
				<span class="muted">wav / mp3</span>
			</div>

			<div class="field">
				<label for="asr-engine">转写引擎</label>
				<select id="asr-engine" bind:value={asrEngineId}>
					{#each asrEngines as engine}
						<option value={engine.manifest.engine_id}>{engine.manifest.display_name}</option>
					{/each}
				</select>
			</div>

			{#if selectedAsrEngine}
				<div class={`engine-summary engine-surface ${selectedAsrEngine.manifest.engine_type === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
					<div class="row" style="justify-content:space-between">
						<strong>{selectedAsrEngine.manifest.display_name}</strong>
						<span class="badge badge-kind">{selectedAsrEngine.manifest.engine_type === 'cloud' ? '云端' : '本地'}</span>
					</div>
					<p class="muted">{selectedAsrEngine.manifest.description}</p>
					{#if selectedAsrHealth}
						<p class:muted={selectedAsrHealth.healthy !== false} class:health-fail={selectedAsrHealth.healthy === false}>
							状态：{String(selectedAsrHealth.status ?? 'unknown')}
							{#if selectedAsrHealth.detail} · {String(selectedAsrHealth.detail)}{/if}
						</p>
					{/if}
					<p class="muted">
						{#if selectedAsrEngine.manifest.engine_id === 'qwen3-asr-mlx'}
							音频留在本机处理，支持分段时间戳与 SRT。
						{:else}
							只上传你手动选择的文件。根据官方文档，当前公开接口返回文字稿，不直接提供稳定时间戳字段。
						{/if}
					</p>
				</div>
			{/if}

			<div
				class:drag-active={dragActive}
				class="dropzone"
				role="button"
				tabindex="0"
				onclick={() => document.getElementById('asr-file')?.click()}
				onkeydown={(event) => {
					if (event.key === 'Enter' || event.key === ' ') {
						event.preventDefault();
						document.getElementById('asr-file')?.click();
					}
				}}
				ondragover={(event) => {
					event.preventDefault();
					dragActive = true;
				}}
				ondragleave={() => (dragActive = false)}
				ondrop={(event) => {
					event.preventDefault();
					dragActive = false;
					setFiles(event.dataTransfer?.files ?? null);
				}}
			>
				<UploadCloud size={18} />
				<div>
					<strong>拖入音频文件</strong>
					<p class="muted">支持一次选择多个文件。多个文件会自动建议走异步任务。</p>
				</div>
				<input
					id="asr-file"
					type="file"
					multiple
					accept=".wav,.mp3,audio/wav,audio/mpeg,audio/mp3"
					onchange={(e) => setFiles((e.currentTarget as HTMLInputElement).files)}
				/>
			</div>

			{#if asrFiles.length}
				<div class="stack file-list">
					<div class="row" style="justify-content:space-between">
						<strong>待转写文件</strong>
						<button class="btn" onclick={clearFiles}>清空</button>
					</div>
					{#each filePreviews as preview}
						<div class="preview-card">
							<div class="row" style="justify-content:space-between">
								<strong>{preview.name}</strong>
								<span class="muted">{preview.sizeKb} KB</span>
							</div>
							<audio class="audio" controls src={preview.url}></audio>
						</div>
					{/each}
				</div>
			{/if}

			<div class="control-grid">
				<div class="field">
					<label for="asr-language">语种</label>
					<select id="asr-language" bind:value={asrLanguage}>
						<option value="auto">自动检测</option>
						<option value="zh">中文</option>
						<option value="en">英文</option>
					</select>
				</div>
				<div class="field">
					<label for="asr-mode">执行方式</label>
					<div id="asr-mode" class="mode-switch" role="tablist" aria-label="转写模式">
						<button class:active={asrMode === 'sync'} type="button" class="btn" onclick={() => (asrMode = 'sync')} disabled={asrFiles.length > 1}>同步返回</button>
						<button class:active={asrMode === 'async'} type="button" class="btn" onclick={() => (asrMode = 'async')}>异步任务</button>
					</div>
				</div>
			</div>

			<div class="field">
				<label for="timestamp-strategy">补时间戳策略</label>
				<select id="timestamp-strategy" bind:value={timestampStrategy}>
					<option value="auto">自动：优先精准，失败时回退</option>
					<option value="forced_aligner">精准 forced align</option>
					<option value="qwen3-asr-mlx">本地快速补齐</option>
				</select>
				<small>{timestampStrategyHint()}</small>
			</div>

			{#if recommendAsync}
				<p class="badge">当前文件数或体积较大，建议走异步任务。</p>
			{/if}
			{#if asrInfo}<p class="badge ok">{asrInfo}</p>{/if}
			{#if asrError}<p class="badge fail">{asrError}</p>{/if}

			<button
				class="btn primary"
				onclick={transcribe}
				disabled={!asrFiles.length || transcribing || submittingTask || selectedAsrHealth?.healthy === false}
			>
				{#if transcribing || submittingTask}
					<span class="spin"><LoaderCircle size={15} /></span>
				{:else}
					<Languages size={15} />
				{/if}
				{transcribing ? '转写中' : submittingTask ? '提交中' : asrMode === 'async' || asrFiles.length > 1 ? '提交转写任务' : '开始转写'}
			</button>
		</section>

		<section class="panel stack result-panel-large" id="transcript-result">
			<div class="row" style="justify-content:space-between">
				<h2>结果预览</h2>
				{#if transcript}<span class="badge">{transcript.engine_id}</span>{/if}
			</div>

			{#if activeTask && !transcript}
				<div class={`stack record active engine-surface ${engineKind(activeTask.engine_id) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
					<div class="row" style="justify-content:space-between">
						<strong>{activeTask.filename}</strong>
						<span class="badge" class:ok={activeTask.status === 'success'} class:fail={activeTask.status === 'failed'}>{asrTaskStatusLabel(activeTask.status)}</span>
					</div>
					<p class="muted">{activeTask.engine_id} · {activeTask.language} · {Math.max(1, Math.round(activeTask.size_bytes / 1024))} KB</p>
					{#if activeTask.status === 'success' && activeTask.transcription_id}
						<div class="row wrap">
							<button class="btn" onclick={() => openTranscription(activeTask.transcription_id!)}>查看结果</button>
							<a class="btn" href={asrExportHref(activeTask.transcription_id, 'txt')}><Download size={15} /> TXT</a>
							{#if activeTask.segments.length}
								<a class="btn" href={asrExportHref(activeTask.transcription_id, 'srt')}><TextQuote size={15} /> SRT</a>
							{/if}
						</div>
					{:else if activeTask.status === 'failed'}
						<p class="badge fail">{activeTask.error_message ?? '转写任务失败'}</p>
					{:else}
						<p class="muted">任务正在后台进行，页面会自动刷新状态。</p>
					{/if}
				</div>
			{/if}

			{#if transcript}
				<div class="stack">
					<div class="row" style="justify-content:space-between">
						<strong>{transcript.filename}</strong>
						<div class="row wrap">
							<span class="badge ok">{transcript.language}</span>
							<span class="badge">{timestampBadge(transcript)}</span>
						</div>
					</div>
					{#if transcript.timestamp_source_engine_id}
						<p class="muted">
							当前时间戳来源：{transcript.timestamp_source_engine_id === 'qwen3-forced-aligner-0.6B'
								? 'Qwen forced align 精准对齐'
								: transcript.timestamp_source_engine_id === 'qwen3-asr-mlx'
									? '本地 Qwen 快速补齐'
									: transcript.timestamp_source_engine_id}
						</p>
					{/if}
					<div class="row wrap">
						<a class="btn" href={asrExportHref(transcript.transcription_id, 'txt')}><Download size={15} /> TXT</a>
						{#if transcript.segments.length}
							<a class="btn" href={asrExportHref(transcript.transcription_id, 'srt')}><TextQuote size={15} /> SRT</a>
							<button class="btn" onclick={() => copySegments(transcript!.segments)}><Copy size={15} /> 复制分段</button>
						{:else}
							<span class="badge">无时间戳</span>
							{#if canSupplement(transcript)}
								<button class="btn" onclick={() => supplementTimestamps(transcript!)} disabled={supplementingTimestamps}>
									{#if supplementingTimestamps}
										<span class="spin"><LoaderCircle size={15} /></span>
									{:else}
										<RefreshCw size={15} />
									{/if}
									按“{timestampStrategyLabel(timestampStrategy)}”补时间戳
								</button>
							{/if}
						{/if}
						<button class="btn" onclick={() => copyTranscript(transcript!.text)}><Copy size={15} /> 复制文字稿</button>
					</div>
					{#if !transcript.segments.length}
						<p class="muted">
							{#if canSupplement(transcript)}
								这条记录保留了源音频。当前策略是“{timestampStrategyLabel(timestampStrategy)}”，{timestampStrategyHint()}
							{:else}
								当前只有文字稿，暂时还不能补时间戳。
							{/if}
						</p>
					{/if}
					{#if copyMessage}<p class="badge ok">{copyMessage}</p>{/if}
					<div class="field">
						<label for="import-project">导入脚本项目</label>
						<div class="row wrap">
							<select id="import-project" bind:value={importProjectId} disabled={!projects.length}>
								{#if projects.length}
									{#each projects as project}
										<option value={project.project_id}>{project.name}</option>
									{/each}
								{:else}
									<option value="">先在脚本工作台创建项目</option>
								{/if}
							</select>
							<button class="btn" onclick={importTranscriptToProject} disabled={!selectedImportProject}>
								<Import size={15} /> 导入脚本工作台
							</button>
							<a class="btn" href="/script-studio">打开脚本工作台</a>
						</div>
						{#if importMessage}<p class="badge ok">{importMessage}</p>{/if}
						<p class="muted">{transcript.segments.length ? '会按 ASR 分段导入，并保留来源时间。' : '没有时间戳时，会按句号/换行粗略拆段导入。'}</p>
					</div>
					<p class="transcript">{transcript.text}</p>
					{#if transcript.segments.length}
						<div class="stack transcript-segments">
							{#each transcript.segments as segment}
								<div class="segment-row">
									<div class="row wrap">
										<span class="badge">{segmentLabel(segment)}</span>
										{#if segment.language}<span class="muted">{segment.language}</span>{/if}
									</div>
									<p>{segment.text}</p>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{:else if !activeTask}
				<div class="empty large">选择文件并转写后，这里会显示文字稿、SRT 与导入动作。</div>
			{/if}
		</section>
	</section>

	<section class="split secondary">
		<div class="stack">
			<section class="panel stack">
				<div class="row" style="justify-content:space-between">
					<h2>转写任务</h2>
					<div class="row">
						<span class="muted">{visibleTasks.length} 条</span>
						<button class="btn" onclick={refresh}><RefreshCw size={15} /> 刷新</button>
					</div>
				</div>
				<div class="mode-switch" role="tablist" aria-label="转写任务筛选">
					<button class:active={taskStatusTab === 'all'} type="button" class="btn" onclick={() => { taskStatusTab = 'all'; taskPage = 1; }}>
						全部 {taskCounts.all}
					</button>
					<button class:active={taskStatusTab === 'active'} type="button" class="btn" onclick={() => { taskStatusTab = 'active'; taskPage = 1; }}>
						进行中 {taskCounts.active}
					</button>
					<button class:active={taskStatusTab === 'success'} type="button" class="btn" onclick={() => { taskStatusTab = 'success'; taskPage = 1; }}>
						成功 {taskCounts.success}
					</button>
					<button class:active={taskStatusTab === 'failed'} type="button" class="btn" onclick={() => { taskStatusTab = 'failed'; taskPage = 1; }}>
						异常 {taskCounts.failed}
					</button>
				</div>
				{#if pagedTasks.length}
					<div class="stack">
						{#each pagedTasks as task}
							<article class:active={task.task_id === activeTaskId} class={`record engine-surface ${engineKind(task.engine_id) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
								<div class="row" style="justify-content:space-between">
									<strong>{task.filename}</strong>
									<span class="badge" class:ok={task.status === 'success'} class:fail={task.status === 'failed'}>{asrTaskStatusLabel(task.status)}</span>
								</div>
								<p class="muted">{engineTypeLabel(task.engine_id)} · {task.engine_id} · {task.language} · {Math.max(1, Math.round(task.size_bytes / 1024))} KB</p>
								<div class="row wrap">
									{#if task.status === 'success' && task.transcription_id}
										<button class="btn" onclick={() => openTranscription(task.transcription_id!)}>查看结果</button>
										<a class="btn" href={asrExportHref(task.transcription_id, 'txt')}><Download size={15} /> TXT</a>
										{#if task.segments.length}
											<a class="btn" href={asrExportHref(task.transcription_id, 'srt')}><TextQuote size={15} /> SRT</a>
										{/if}
									{:else if task.status === 'failed'}
										<p class="badge fail">{task.error_message ?? '转写任务失败'}</p>
									{:else}
										<p class="muted">等待完成后可查看结果。</p>
									{/if}
									{#if canDeleteTask(task)}
										<button class="btn danger" onclick={() => deleteTranscriptionTask(task.task_id)}>
											<Trash2 size={15} /> 删除任务
										</button>
									{/if}
								</div>
							</article>
						{/each}
					</div>
					{#if taskPageCount > 1}
						<div class="pagination-row">
							<button class="btn" onclick={() => taskPageJump(-1)} disabled={taskPage <= 1}>
								<ChevronLeft size={15} /> 上一页
							</button>
							<span class="muted">第 {taskPage} / {taskPageCount} 页</span>
							<button class="btn" onclick={() => taskPageJump(1)} disabled={taskPage >= taskPageCount}>
								下一页 <ChevronRight size={15} />
							</button>
						</div>
					{/if}
				{:else}
					<div class="empty">当前筛选下没有转写任务</div>
				{/if}
			</section>

			<section class="panel stack">
				<div class="row" style="justify-content:space-between">
					<h2>最近转写</h2>
					<div class="row">
						<span class="muted">{visibleTranscriptions.length} 条</span>
						{#if selectedTranscriptionIds.length}
							<span class="badge ok">已选 {selectedTranscriptionIds.length}</span>
						{/if}
					</div>
				</div>
				<div class="filter-grid">
					<label class="field">
						<span>搜索</span>
						<div class="search-field">
							<Search size={15} />
							<input bind:value={transcriptionQuery} placeholder="文件名、文本、引擎" oninput={() => (transcriptionPage = 1)} />
						</div>
					</label>
					<label class="field">
						<span>引擎</span>
						<select bind:value={transcriptionEngineFilter} onchange={() => (transcriptionPage = 1)}>
							<option value="all">全部</option>
							{#each asrEngines as engine}
								<option value={engine.manifest.engine_id}>{engine.manifest.display_name}</option>
							{/each}
						</select>
					</label>
					<label class="field">
						<span>时间戳</span>
						<select bind:value={transcriptionTimestampFilter} onchange={() => (transcriptionPage = 1)}>
							<option value="all">全部</option>
							<option value="with_timestamps">有时间戳</option>
							<option value="text_only">仅文字稿</option>
						</select>
					</label>
				</div>
				<div class="row wrap toolbar-strip">
					<button class="btn" onclick={toggleVisibleTranscriptions} disabled={!visibleTranscriptionIds.length}>
						<CheckSquare2 size={15} /> {allVisibleSelected ? '取消全选当前筛选' : '全选当前筛选'}
					</button>
					<button class="btn" onclick={batchSupplementSelectedTranscriptions} disabled={!selectedTranscriptionIds.length || supplementingTimestamps}>
						<RefreshCw size={15} /> 按“{timestampStrategyLabel(timestampStrategy)}”批量补时间戳
					</button>
					<button class="btn danger" onclick={batchDeleteSelectedTranscriptions} disabled={!selectedTranscriptionIds.length}>
						<Trash2 size={15} /> 批量删除
					</button>
					<button class="btn" onclick={clearSelectedTranscriptions} disabled={!selectedTranscriptionIds.length}>
						<CheckSquare2 size={15} /> 清空选择
					</button>
				</div>
				{#if pagedTranscriptions.length}
					<div class="stack">
						{#each pagedTranscriptions as item}
							<article class:active={item.transcription_id === selectedTranscriptionId} class={`record engine-surface ${engineKind(item.engine_id) === 'cloud' ? 'engine-cloud' : 'engine-local'}`}>
								<div class="row" style="justify-content:space-between">
									<div class="row">
										<input
											type="checkbox"
											checked={selectedTranscriptionIds.includes(item.transcription_id)}
											onchange={(event) => toggleTranscriptionSelection(item.transcription_id, (event.currentTarget as HTMLInputElement).checked)}
										/>
										<strong>{item.filename}</strong>
									</div>
									<span class="badge badge-kind">{engineTypeLabel(item.engine_id)}</span>
								</div>
								<div class="row wrap">
									<span class="muted">
										{engineMap.get(item.engine_id)?.manifest.display_name ?? item.engine_id}
										· {item.language}
										· {timestampBadge(item)}
										{#if item.segments.length} · {item.segments.length} 段{/if}
										{#if selectedTaskForRecord(item)?.status} · {asrTaskStatusLabel(selectedTaskForRecord(item)!.status)}{/if}
									</span>
									<button class="btn" onclick={() => openTranscription(item.transcription_id)}>查看结果</button>
									<a class="btn" href={asrExportHref(item.transcription_id, 'txt')}><Download size={15} /> TXT</a>
									{#if item.segments.length}
										<a class="btn" href={asrExportHref(item.transcription_id, 'srt')}><TextQuote size={15} /> SRT</a>
									{:else if canSupplement(item)}
										<button class="btn" onclick={() => supplementTimestamps(item)} disabled={supplementingTimestamps}>
											{#if supplementingTimestamps}
												<span class="spin"><LoaderCircle size={15} /></span>
											{:else}
												<RefreshCw size={15} />
											{/if}
											补时间戳
										</button>
									{/if}
									<button class="btn danger" onclick={() => deleteTranscriptionRecord(item.transcription_id)}>
										<Trash2 size={15} /> 删除
									</button>
								</div>
								<p class="muted">{item.text}</p>
							</article>
						{/each}
					</div>
					{#if transcriptionPageCount > 1}
						<div class="pagination-row">
							<button class="btn" onclick={() => transcriptionPageJump(-1)} disabled={transcriptionPage <= 1}>
								<ChevronLeft size={15} /> 上一页
							</button>
							<span class="muted">第 {transcriptionPage} / {transcriptionPageCount} 页</span>
							<button class="btn" onclick={() => transcriptionPageJump(1)} disabled={transcriptionPage >= transcriptionPageCount}>
								下一页 <ChevronRight size={15} />
							</button>
						</div>
					{/if}
				{:else}
					<div class="empty">当前筛选下没有转写记录</div>
				{/if}
			</section>
		</div>

		<div class="stack">
			<section class="panel audio-panel stack">
				<div class="row" style="justify-content:space-between">
					<h2><FileAudio size={16} /> 历史音频记录</h2>
					<span class="muted">用于合并导出</span>
				</div>
				<table class="table audio-table">
					<thead>
						<tr><th></th><th>文本</th><th>引擎</th><th>音频</th></tr>
					</thead>
					<tbody>
						{#each history as item}
							<tr>
								<td>
									<input
										type="checkbox"
										checked={selected.includes(item.result_id)}
										onchange={(e) => {
											const checked = (e.currentTarget as HTMLInputElement).checked;
											selected = checked
												? [...selected, item.result_id]
												: selected.filter((x) => x !== item.result_id);
										}}
									/>
								</td>
								<td>{item.input_text.slice(0, 70)}</td>
								<td>{item.engine_id}</td>
								<td><audio class="audio" controls src={`/api/history/${item.result_id}/audio`}></audio></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</section>

			<aside class="panel stack">
				<h2><Layers size={16} /> 音频导出</h2>
				<div class="field">
					<label for="fmt">格式</label>
					<select id="fmt" bind:value={format}>
						<option value="wav">WAV</option>
						<option value="mp3">MP3</option>
						<option value="flac">FLAC</option>
					</select>
				</div>
				<label for="norm"><input id="norm" type="checkbox" bind:checked={normalize} /> 音量标准化</label>
				<button class="btn primary" onclick={merge} disabled={selected.length === 0}>
					<Layers size={15} /> 合并导出 {selected.length ? `(${selected.length})` : ''}
				</button>
				{#if exportPath}<p class="badge ok">{exportPath}</p>{/if}
				<p class="muted">这里保留原来的历史音频合并工作流，但不再占据首屏主位置。</p>
			</aside>
		</div>
	</section>
</main>

<style>
	.hero-grid {
		display: grid;
		grid-template-columns: minmax(420px, 520px) minmax(0, 1fr);
		gap: 16px;
		align-items: start;
	}

	.secondary {
		margin-top: 16px;
		align-items: start;
	}

	.secondary > .stack {
		min-width: 0;
	}

	.asr-panel,
	.result-panel-large {
		min-height: 100%;
	}

	.engine-summary,
	.dropzone,
	.file-list,
	.record {
		border: 1px solid var(--line);
		border-radius: 7px;
		background: #101215;
	}

	.engine-summary,
	.file-list,
	.record {
		padding: 12px;
	}

	.dropzone {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 12px;
		align-items: start;
		padding: 14px;
		cursor: pointer;
		transition: border-color 160ms ease, background 160ms ease;
	}

	.dropzone:hover,
	.dropzone.drag-active {
		border-color: var(--accent);
		background: #121722;
	}

	.dropzone input {
		display: none;
	}

	.control-grid,
	.filter-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
	}

	.filter-grid {
		grid-template-columns: minmax(0, 1.4fr) repeat(2, minmax(140px, 1fr));
	}

	.preview-card {
		display: grid;
		gap: 8px;
		padding: 10px;
		border-radius: 6px;
		background: #171a1f;
	}

	.result-panel-large {
		padding: 16px;
	}

	.result-panel-large .transcript {
		margin: 0;
		line-height: 1.7;
		white-space: pre-wrap;
	}

	.transcript-segments {
		border-top: 1px solid var(--line);
		padding-top: 12px;
	}

	.segment-row {
		display: grid;
		gap: 6px;
		padding: 10px 0;
		border-bottom: 1px solid rgba(255, 255, 255, 0.04);
	}

	.segment-row:last-child {
		border-bottom: 0;
	}

	.segment-row p,
	.engine-summary p,
	.record p {
		margin: 0;
	}

	.search-field {
		display: flex;
		align-items: center;
		gap: 8px;
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 0 10px;
		background: #0f1216;
	}

	.search-field input {
		border: 0;
		background: transparent;
		width: 100%;
		min-height: 38px;
		color: inherit;
		outline: none;
	}

	.record.active {
		border-color: var(--accent);
		box-shadow: inset 0 0 0 1px rgba(79, 156, 249, 0.2);
	}

	.mode-switch {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}

	.toolbar-strip {
		padding-top: 4px;
		border-top: 1px solid rgba(255, 255, 255, 0.04);
	}

	.pagination-row {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 12px;
		padding-top: 4px;
	}

	.mode-switch .btn.active {
		background: var(--accent);
		border-color: var(--accent);
		color: #fff;
	}

	.wrap {
		flex-wrap: wrap;
	}

	.audio-panel {
		overflow-x: auto;
	}

	.audio-table th:nth-child(4),
	.audio-table td:nth-child(4) {
		min-width: 280px;
		width: 40%;
	}

	.health-fail {
		color: #f87171;
	}

	.large {
		min-height: 320px;
		display: grid;
		place-items: center;
		text-align: center;
	}

	.spin {
		animation: spin 1s linear infinite;
	}

	@media (max-width: 1100px) {
		.hero-grid,
		.control-grid,
		.filter-grid,
		.secondary {
			grid-template-columns: 1fr;
		}
	}

	@media (max-width: 760px) {
		.pagination-row {
			flex-direction: column;
			align-items: flex-start;
		}

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

	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}
</style>
