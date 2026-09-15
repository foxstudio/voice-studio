<script lang="ts">
	import {
		AudioLines,
		Captions,
		Check,
		Download,
		Film,
		FolderOpen,
		Layers3,
		X
	} from 'lucide-svelte';
	import type {
		MediaExportAudioTrackId,
		MediaExportAvailability,
		MediaExportRequest,
		MediaExportSubtitleTrackId,
		StandaloneSubtitleExportSelection
	} from './export-options';
	import { buildSubtitleMediaExportRequest } from './export-options';
	import CommandSpinner from './CommandSpinner.svelte';

	let {
		request = $bindable(),
		availability,
		exporting = false,
		selectingDestination = false,
		destinationPath = '',
		progress = 0,
		stage = '',
		exportedFilename = '',
		outputFilename = '',
		errorMessage = '',
		onClose,
		onChooseDestination,
		onRequestChange,
		onOutputFilenameChange,
		onExport
	}: {
		request: MediaExportRequest;
		availability: MediaExportAvailability;
		exporting?: boolean;
		selectingDestination?: boolean;
		destinationPath?: string;
		progress?: number;
		stage?: string;
		exportedFilename?: string;
		outputFilename?: string;
		errorMessage?: string;
		onClose: () => void;
		onChooseDestination: () => void;
		onRequestChange: (request: MediaExportRequest) => void;
		onOutputFilenameChange: (filename: string) => void;
		onExport: () => void;
	} = $props();

	let mode = $state<'video' | 'audio' | 'subtitle'>(request.kind);
	let rememberedVideoSubtitles = $state<MediaExportSubtitleTrackId[]>([
		...request.subtitle_tracks
	]);
	let standaloneSubtitleSelection = $state<StandaloneSubtitleExportSelection>(
		request.subtitle_tracks.includes('asr')
			&& request.subtitle_tracks.includes('localized')
			? 'bilingual'
			: request.subtitle_tracks.includes('localized')
				? request.localized_subtitle_variant === 'dub'
					? 'dub'
					: 'localized'
				: 'asr'
	);

	const audioOptions: Array<{
		id: MediaExportAudioTrackId;
		label: string;
		detail: string;
	}> = [
		{ id: 'original', label: '原音轨', detail: '源视频里的完整声音' },
		{ id: 'vocals', label: '人声轨', detail: '分离后的人声' },
		{ id: 'background', label: '背景音乐', detail: '分离后的音乐和环境声' },
		{ id: 'dub', label: '合成配音', detail: '时间线上的配音片段' }
	];

	const audioSelectionValid = $derived(request.audio_tracks.length > 0);
	const expectedExtension = $derived(request.kind === 'video'
		? '.mp4'
		: request.kind === 'subtitle'
			? '.srt'
			: `.${request.audio_format}`);
	const subtitleSelectionAvailable = $derived(
		standaloneSubtitleSelection === 'asr'
			? availability.subtitles.asr
			: standaloneSubtitleSelection === 'localized'
				? availability.subtitles.localized
				: standaloneSubtitleSelection === 'dub'
					? availability.subtitles.dub
					: availability.subtitles.asr && availability.subtitles.localized
	);
	const filenameValid = $derived(
		Boolean(outputFilename.trim())
		&& !/[\\/\u0000-\u001f]/.test(outputFilename)
		&& outputFilename.endsWith(expectedExtension)
	);
	const canExport = $derived(
		Boolean(destinationPath) && filenameValid && (mode === 'video'
			? availability.sourceVideo
			: mode === 'audio'
				? audioSelectionValid
				: subtitleSelectionAvailable)
	);
	const progressPercent = $derived(
		Math.max(0, Math.min(100, Math.round(progress * 100)))
	);
	const sourceSizeLabel = $derived.by(() => {
		const { width, height, frameRate } = availability.sourceProfile;
		if (!width || !height) return '保持原视频尺寸';
		const quality = height >= 2160
			? '4K'
			: height >= 1440
				? '2K'
				: height >= 1080
					? '1080p'
					: `${height}p`;
		const frameRateLabel = frameRate ? `，${Number(frameRate.toFixed(2))} fps` : '';
		return `${width} × ${height}（${quality}${frameRateLabel}，原视频）`;
	});
	const sourceAudioLabel = $derived.by(() => {
		const { audioSampleRate, audioChannels } = availability.sourceProfile;
		const parts: string[] = [];
		if (audioSampleRate) parts.push(`${audioSampleRate / 1000} kHz`);
		if (audioChannels) {
			parts.push(audioChannels === 1 ? '单声道' : audioChannels === 2 ? '立体声' : `${audioChannels} 声道`);
		}
		return parts.length ? `${parts.join(' · ')}（原视频）` : '跟随原视频音频参数';
	});

	function audioBitrateFromSelect(value: string): MediaExportRequest['audio_bitrate_kbps'] {
		return value === 'source'
			? 'source'
			: Number(value) as MediaExportRequest['audio_bitrate_kbps'];
	}

	function setMode(nextMode: typeof mode) {
		mode = nextMode;
		if (nextMode === 'subtitle') {
			updateRequest(buildSubtitleMediaExportRequest(
				standaloneSubtitleSelection
			));
			return;
		}
		updateRequest({
			...request,
			kind: nextMode,
			subtitle_tracks: nextMode === 'video'
				? [...rememberedVideoSubtitles]
				: []
		});
	}

	function selectStandaloneSubtitle(
		selection: StandaloneSubtitleExportSelection
	) {
		standaloneSubtitleSelection = selection;
		updateRequest(buildSubtitleMediaExportRequest(selection));
	}

	function updateRequest(nextRequest: MediaExportRequest) {
		request = nextRequest;
		onRequestChange(nextRequest);
	}

	function audioAvailable(id: MediaExportAudioTrackId) {
		if (id === 'dub') return availability.dubLaneMedia.some(Boolean);
		return availability.audioTracks[id];
	}

	function toggleAudioTrack(id: MediaExportAudioTrackId) {
		if (!audioAvailable(id)) return;
		const selected = request.audio_tracks.includes(id);
		const audioTracks = selected
			? request.audio_tracks.filter((value) => value !== id)
			: [...request.audio_tracks, id];
		let dubLanes = request.dub_lanes;
		if (id === 'dub' && selected) dubLanes = [];
		if (id === 'dub' && !selected && !dubLanes.length) {
			dubLanes = availability.dubLaneMedia
				.map((available, lane) => available ? lane : -1)
				.filter((lane) => lane >= 0);
		}
		updateRequest({ ...request, audio_tracks: audioTracks, dub_lanes: dubLanes });
	}

	function toggleDubLane(lane: number) {
		if (!availability.dubLaneMedia[lane]) return;
		const selected = request.dub_lanes.includes(lane);
		const dubLanes = selected
			? request.dub_lanes.filter((value) => value !== lane)
			: [...request.dub_lanes, lane].sort((left, right) => left - right);
		updateRequest({
			...request,
			audio_tracks: dubLanes.length
				? request.audio_tracks.includes('dub')
					? request.audio_tracks
					: [...request.audio_tracks, 'dub']
				: request.audio_tracks.filter((value) => value !== 'dub'),
			dub_lanes: dubLanes
		});
	}

	function toggleSubtitleTrack(id: MediaExportSubtitleTrackId) {
		if (!availability.subtitles[id]) return;
		const selected = rememberedVideoSubtitles.includes(id);
		rememberedVideoSubtitles = selected
			? rememberedVideoSubtitles.filter((value) => value !== id)
			: [...rememberedVideoSubtitles, id];
		updateRequest({ ...request, subtitle_tracks: [...rememberedVideoSubtitles] });
	}

	function toggleLocalizedSubtitleVariant(
		variant: MediaExportRequest['localized_subtitle_variant']
	) {
		if (!availability.subtitles[variant]) return;
		const selected = rememberedVideoSubtitles.includes('localized')
			&& request.localized_subtitle_variant === variant;
		rememberedVideoSubtitles = selected
			? rememberedVideoSubtitles.filter((value) => value !== 'localized')
			: rememberedVideoSubtitles.includes('localized')
				? rememberedVideoSubtitles
				: [...rememberedVideoSubtitles, 'localized'];
		updateRequest({
			...request,
			localized_subtitle_variant: variant,
			subtitle_tracks: [...rememberedVideoSubtitles]
		});
	}

	function closeFromBackdrop(event: MouseEvent) {
		if (event.target === event.currentTarget && !exporting) onClose();
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && !exporting) onClose();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="export-backdrop" role="presentation" onclick={closeFromBackdrop}>
	<div
		class="export-dialog"
		role="dialog"
		aria-modal="true"
		aria-labelledby="export-dialog-title"
	>
		<header class="export-header">
			<div>
				<span class="export-kicker">最终交付</span>
				<h2 id="export-dialog-title">导出成品</h2>
				<p>勾选的轨道会合成一个文件，不会分轨导出。</p>
			</div>
			<button
				class="icon-close"
				type="button"
				aria-label="关闭导出面板"
				disabled={exporting}
				onclick={onClose}
			>
				<X size={17} />
			</button>
		</header>

		<nav class="export-tabs" aria-label="导出类型">
			<button class:active={mode === 'video'} type="button" onclick={() => setMode('video')}>
				<Film size={15} />视频
			</button>
			<button class:active={mode === 'audio'} type="button" onclick={() => setMode('audio')}>
				<AudioLines size={15} />音频
			</button>
			<button class:active={mode === 'subtitle'} type="button" onclick={() => setMode('subtitle')}>
				<Captions size={15} />字幕文件
			</button>
		</nav>

		{#if errorMessage}
			<div class="export-error" role="alert">{errorMessage}</div>
		{/if}

		<div class="export-body">
			<div class="export-columns">
				<section class="track-section">
					{#if mode === 'subtitle'}
						<div class="section-heading">
							<div>
								<h3>导出内容</h3>
								<p>选择一个字幕文件，不参与视频压制。</p>
							</div>
							<Captions size={17} />
						</div>
						<button
							class="track-row"
							class:selected={standaloneSubtitleSelection === 'asr'}
							type="button"
							aria-pressed={standaloneSubtitleSelection === 'asr'}
							disabled={!availability.subtitles.asr}
							onclick={() => selectStandaloneSubtitle('asr')}
						>
							<span class="check-box">{#if standaloneSubtitleSelection === 'asr'}<Check size={13} />{/if}</span>
							<span><strong>ASR 字幕</strong><small>原文字幕 · SRT</small></span>
						</button>
						<button
							class="track-row"
							class:selected={standaloneSubtitleSelection === 'localized'}
							type="button"
							aria-pressed={standaloneSubtitleSelection === 'localized'}
							disabled={!availability.subtitles.localized}
							onclick={() => selectStandaloneSubtitle('localized')}
						>
							<span class="check-box">{#if standaloneSubtitleSelection === 'localized'}<Check size={13} />{/if}</span>
							<span><strong>本土化字幕</strong><small>上屏字幕 · SRT</small></span>
						</button>
						<button
							class="track-row"
							class:selected={standaloneSubtitleSelection === 'dub'}
							type="button"
							aria-pressed={standaloneSubtitleSelection === 'dub'}
							disabled={!availability.subtitles.dub}
							onclick={() => selectStandaloneSubtitle('dub')}
						>
							<span class="check-box">{#if standaloneSubtitleSelection === 'dub'}<Check size={13} />{/if}</span>
							<span><strong>配音字幕</strong><small>语音合成结果字幕 · SRT</small></span>
						</button>
						<button
							class="track-row"
							class:selected={standaloneSubtitleSelection === 'bilingual'}
							type="button"
							aria-pressed={standaloneSubtitleSelection === 'bilingual'}
							disabled={!availability.subtitles.asr || !availability.subtitles.localized}
							onclick={() => selectStandaloneSubtitle('bilingual')}
						>
							<span class="check-box">{#if standaloneSubtitleSelection === 'bilingual'}<Check size={13} />{/if}</span>
							<span><strong>双语字幕</strong><small>原文 + 本土化字幕 · SRT</small></span>
						</button>
					{:else}
						<div class="section-heading">
							<div>
								<h3>导出内容</h3>
								<p>默认跟随当前时间线的显示和静音状态，可手动改选。</p>
							</div>
							<Layers3 size={17} />
						</div>

						{#if mode === 'video'}
							<div class="track-row fixed" class:unavailable={!availability.sourceVideo}>
								<span class="check-box"><Check size={13} /></span>
								<span><strong>视频画面</strong><small>视频导出的基础画面，固定包含</small></span>
							</div>
							<div class="track-group-label">压制字幕 · 两种中文字幕二选一</div>
							<button
								class="track-row"
								class:selected={rememberedVideoSubtitles.includes('localized') && request.localized_subtitle_variant === 'localized'}
								type="button"
								aria-pressed={rememberedVideoSubtitles.includes('localized') && request.localized_subtitle_variant === 'localized'}
								disabled={!availability.subtitles.localized}
								onclick={() => toggleLocalizedSubtitleVariant('localized')}
							>
								<span class="check-box">{#if rememberedVideoSubtitles.includes('localized') && request.localized_subtitle_variant === 'localized'}<Check size={13} />{/if}</span>
								<span><strong>本土化字幕</strong><small>本土化上屏文本，烧录到视频画面</small></span>
							</button>
							<button
								class="track-row"
								class:selected={rememberedVideoSubtitles.includes('localized') && request.localized_subtitle_variant === 'dub'}
								type="button"
								aria-pressed={rememberedVideoSubtitles.includes('localized') && request.localized_subtitle_variant === 'dub'}
								disabled={!availability.subtitles.dub}
								onclick={() => toggleLocalizedSubtitleVariant('dub')}
							>
								<span class="check-box">{#if rememberedVideoSubtitles.includes('localized') && request.localized_subtitle_variant === 'dub'}<Check size={13} />{/if}</span>
								<span><strong>配音字幕</strong><small>语音合成结果字幕，烧录到视频画面</small></span>
							</button>
							<button
								class="track-row"
								class:selected={rememberedVideoSubtitles.includes('asr')}
								type="button"
								aria-pressed={rememberedVideoSubtitles.includes('asr')}
								disabled={!availability.subtitles.asr}
								onclick={() => toggleSubtitleTrack('asr')}
							>
								<span class="check-box">{#if rememberedVideoSubtitles.includes('asr')}<Check size={13} />{/if}</span>
								<span><strong>ASR 字幕</strong><small>烧录原文字幕</small></span>
							</button>
						{/if}

						<div class="track-group-label">混合音频</div>
						{#each audioOptions as option}
							<button
								class="track-row"
								class:selected={request.audio_tracks.includes(option.id)}
								type="button"
								aria-pressed={request.audio_tracks.includes(option.id)}
								disabled={!audioAvailable(option.id)}
								onclick={() => toggleAudioTrack(option.id)}
							>
								<span class="check-box">{#if request.audio_tracks.includes(option.id)}<Check size={13} />{/if}</span>
								<span><strong>{option.label}</strong><small>{audioAvailable(option.id) ? option.detail : '当前项目没有可用内容'}</small></span>
							</button>
							{#if option.id === 'dub' && request.audio_tracks.includes('dub') && availability.dubLaneMedia.length > 1}
								<div class="dub-lanes">
									{#each availability.dubLaneMedia as available, lane}
										<button
											type="button"
											class:selected={request.dub_lanes.includes(lane)}
											aria-pressed={request.dub_lanes.includes(lane)}
											disabled={!available}
											onclick={() => toggleDubLane(lane)}
										>
											<span class="check-box">{#if request.dub_lanes.includes(lane)}<Check size={11} />{/if}</span>
											配音轨 {lane + 1}
										</button>
									{/each}
								</div>
							{/if}
						{/each}
					{/if}
				</section>

				<section class="settings-section">
						<div class="section-heading">
							<div>
								<h3>基础设置</h3>
								<p>
									{mode === 'video'
										? '输出一个 MP4 文件。'
										: mode === 'audio'
											? '输出一条完整混音。'
											: '输出一个 SRT 字幕文件。'}
								</p>
							</div>
						</div>
						{#if mode === 'video'}
							<label>
								<span>画面尺寸</span>
								<select
									value={request.video_size}
									onchange={(event) => updateRequest({
										...request,
										video_size: event.currentTarget.value as MediaExportRequest['video_size']
									})}
								>
									<option value="source">{sourceSizeLabel}</option>
									<option value="1080p">1080p</option>
									<option value="720p">720p</option>
								</select>
							</label>
							<label>
								<span>视频质量</span>
								<select
									value={request.video_quality}
									onchange={(event) => updateRequest({
										...request,
										video_quality: event.currentTarget.value as MediaExportRequest['video_quality']
									})}
								>
									<option value="source">跟随原视频（默认，重新编码）</option>
									<option value="high">高质量</option>
									<option value="balanced">均衡</option>
									<option value="compact">较小文件</option>
								</select>
							</label>
						{:else if mode === 'audio'}
							<label>
								<span>音频格式</span>
								<select
									value={request.audio_format}
									onchange={(event) => updateRequest({
										...request,
										audio_format: event.currentTarget.value as MediaExportRequest['audio_format']
									})}
								>
									<option value="wav">WAV · 无压缩</option>
									<option value="mp3">MP3 · 通用</option>
								</select>
								{#if request.audio_format === 'wav'}
									<small class="source-parameter">{sourceAudioLabel}</small>
								{/if}
							</label>
						{/if}
						{#if mode !== 'subtitle' && (mode === 'video' || request.audio_format === 'mp3')}
							<label>
								<span>音频码率</span>
								<select
									value={request.audio_bitrate_kbps}
								onchange={(event) => updateRequest({
									...request,
									audio_bitrate_kbps: audioBitrateFromSelect(event.currentTarget.value)
								})}
							>
								<option value="source">跟随原视频（默认）</option>
								<option value="128">128 kbps</option>
								<option value="192">192 kbps</option>
								<option value="256">256 kbps</option>
							</select>
							<small class="source-parameter">{sourceAudioLabel}</small>
						</label>
						{/if}
						<div class="destination-setting">
							<span>保存位置</span>
							<div class="destination-row">
								<div class="destination-path" title={destinationPath}>
									<FolderOpen size={15} />
									<span>{destinationPath || '请选择保存文件夹'}</span>
								</div>
								<button
									type="button"
									disabled={exporting || selectingDestination}
									onclick={onChooseDestination}
								>
									{#if selectingDestination}<CommandSpinner size={14} />{/if}
									{selectingDestination ? '正在选择' : '选择'}
								</button>
							</div>
							<small class="source-parameter">渲染完成后直接写入这个文件夹。</small>
						</div>
						<label>
							<span>文件名称</span>
							<input
								class="filename-input"
								value={outputFilename}
								disabled={exporting}
								maxlength="255"
								autocomplete="off"
								spellcheck="false"
								aria-invalid={!filenameValid}
								oninput={(event) => onOutputFilenameChange(event.currentTarget.value)}
							/>
							<small class:validation-hint={!filenameValid} class="source-parameter">
								最终将按这个名称保存，不会再次重命名。
								{#if outputFilename && !filenameValid}文件名不能含路径符号，且扩展名应为 {expectedExtension}。{/if}
							</small>
						</label>
						<div class="export-summary">
							<strong>
								将生成一个{mode === 'video'
									? '视频'
									: mode === 'audio'
										? '音频'
										: '字幕'}文件
							</strong>
							{#if mode === 'subtitle'}
								<span>1 条字幕轨 · SRT</span>
							{:else}
								<span>
									{request.audio_tracks.length} 类音频轨道
									{#if mode === 'video'} · {rememberedVideoSubtitles.length} 条字幕轨{/if}
								</span>
							{/if}
						</div>
						{#if exporting}
							<div class="render-progress" aria-live="polite">
								<div class="render-progress-heading">
									<strong>{stage || '正在渲染'}</strong>
									<span>{progressPercent}%</span>
								</div>
								<div
									class="render-progress-track"
									role="progressbar"
									aria-label="导出渲染进度"
									aria-valuemin="0"
									aria-valuemax="100"
									aria-valuenow={progressPercent}
								>
									<span style={`width: ${progressPercent}%`}></span>
								</div>
							</div>
						{:else if exportedFilename}
							<div class="export-success" role="status">
								<Check size={15} />
								<span>
									<strong>导出完成</strong>
									<small title={`${destinationPath}/${exportedFilename}`}>{exportedFilename}</small>
								</span>
							</div>
						{/if}
				</section>
			</div>
		</div>

		<footer class="export-footer">
				<div>
					{#if mode === 'video' && !availability.sourceVideo}
						<span class="validation-error">当前项目没有可用视频画面。</span>
					{:else if mode === 'audio' && !audioSelectionValid}
						<span class="validation-error">至少选择一个有声音的轨道。</span>
					{:else if mode === 'subtitle' && !subtitleSelectionAvailable}
						<span class="validation-error">选择的字幕轨当前没有可导出的内容。</span>
					{:else if !destinationPath}
						<span class="validation-error">请选择保存文件夹。</span>
					{:else if !filenameValid}
						<span class="validation-error">请输入有效的最终文件名称。</span>
					{:else if mode === 'video' && !request.audio_tracks.length}
						<span>未选音频，将导出静音视频。</span>
					{:else if mode === 'subtitle'}
						<span>字幕会直接保存为 SRT 文件。</span>
					{:else}
						<span>轨道会按当前音量混合。</span>
					{/if}
				</div>
				<button class="secondary" type="button" disabled={exporting} onclick={onClose}>取消</button>
				<button class="primary" type="button" disabled={!canExport || exporting} onclick={onExport}>
					{#if exporting}<CommandSpinner size={15} />{:else}<Download size={15} />{/if}
					{exporting
						? `正在渲染 ${progressPercent}%`
						: exportedFilename
							? '再次导出'
							: `导出${mode === 'video' ? '视频' : mode === 'audio' ? '音频' : '字幕'}`}
				</button>
		</footer>
	</div>
</div>

<style>
	.export-backdrop {
		position: fixed;
		inset: 0;
		z-index: 450;
		display: grid;
		place-items: center;
		padding: 24px;
		background: rgb(2 6 9 / 72%);
		backdrop-filter: blur(7px);
	}

	.export-dialog {
		width: min(800px, 100%);
		max-height: min(760px, calc(100vh - 40px));
		overflow: hidden;
		border: 1px solid #39454e;
		border-radius: 12px;
		background: #11171b;
		box-shadow: 0 28px 80px rgb(0 0 0 / 55%);
		color: var(--text);
	}

	.export-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 16px;
		padding: 15px 18px 12px;
		border-bottom: 1px solid var(--line);
		background:
			radial-gradient(circle at 12% -40%, rgb(29 200 169 / 16%), transparent 42%),
			#141a1f;
	}

	.export-kicker {
		color: #69d7c2;
		font-size: 10px;
		font-weight: 800;
		letter-spacing: .12em;
	}

	h2, h3, p { margin: 0; }
	h2 { margin-top: 2px; font-size: 17px; }
	h3 { font-size: 13px; }
	.export-header p, .section-heading p {
		margin-top: 4px;
		color: var(--muted);
		font-size: 11px;
	}

	.icon-close {
		display: grid;
		place-items: center;
		width: 31px;
		height: 31px;
		border: 1px solid #3b464e;
		border-radius: 6px;
		background: #1a2228;
		color: #b9c4ca;
		cursor: pointer;
	}

	.export-tabs {
		display: flex;
		gap: 3px;
		padding: 6px 18px 0;
		background: #11171b;
	}

	.export-tabs button {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 7px 11px;
		border: 0;
		border-bottom: 2px solid transparent;
		background: transparent;
		color: var(--muted);
		font-size: 12px;
		cursor: pointer;
	}

	.export-tabs button.active {
		border-color: #42cbb3;
		color: #eaf7f4;
	}

	.export-error {
		margin: 9px 18px 0;
		padding: 7px 9px;
		border: 1px solid rgb(214 105 105 / 48%);
		border-radius: 6px;
		background: rgb(124 39 39 / 18%);
		color: #f1b4b4;
		font-size: 11px;
		line-height: 1.45;
	}

	.export-body {
		max-height: calc(100vh - 270px);
		overflow: auto;
		padding: 13px 18px 16px;
	}

	.export-columns {
		display: grid;
		grid-template-columns: minmax(0, 1.3fr) minmax(250px, .8fr);
		gap: 12px;
	}

	.track-section, .settings-section {
		padding: 11px;
		border: 1px solid #303940;
		border-radius: 8px;
		background: #151c21;
	}

	.section-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin-bottom: 9px;
	}

	.track-group-label {
		margin: 10px 2px 5px;
		color: #829098;
		font-size: 10px;
		font-weight: 700;
		letter-spacing: .06em;
	}

	.track-row {
		display: grid;
		grid-template-columns: 20px minmax(0, 1fr);
		align-items: center;
		gap: 8px;
		width: 100%;
		min-height: 38px;
		padding: 5px 7px;
		border: 1px solid #303a42;
		border-radius: 6px;
		background: #11171b;
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}

	.track-row + .track-row { margin-top: 5px; }
	.track-row.selected, .track-row.fixed {
		border-color: #2d756b;
		background: #142824;
	}
	.track-row:disabled, .track-row.unavailable { opacity: .42; cursor: not-allowed; }
	.track-row span:nth-child(2) { display: grid; gap: 2px; }
	.track-row strong { font-size: 11px; }
	.track-row small { color: var(--muted); font-size: 10px; }

	.check-box {
		display: grid;
		place-items: center;
		width: 17px;
		height: 17px;
		border: 1px solid #4b5a63;
		border-radius: 4px;
		color: #8fe3d3;
	}

	.dub-lanes {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 4px;
		margin: 5px 0 6px 26px;
	}

	.dub-lanes button {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 5px 6px;
		border: 1px solid #344047;
		border-radius: 5px;
		background: #12191d;
		color: #aebbc1;
		font-size: 10px;
		cursor: pointer;
	}

	.dub-lanes button.selected { border-color: #2d756b; color: #dff7f2; }
	.dub-lanes button:disabled { opacity: .35; cursor: not-allowed; }
	.dub-lanes .check-box { width: 14px; height: 14px; }

	.settings-section {
		align-self: start;
		display: grid;
		gap: 9px;
	}

	label { display: grid; gap: 6px; }
	label > span { color: #b6c1c7; font-size: 11px; font-weight: 650; }
	.source-parameter { color: #73838b; font-size: 10px; line-height: 1.35; }
	select, .filename-input {
		width: 100%;
		height: 32px;
		padding: 0 9px;
		border: 1px solid #3a454d;
		border-radius: 6px;
		background: #0f1519;
		color: var(--text);
		font: inherit;
		font-size: 11px;
	}
	.filename-input:focus-visible, select:focus-visible {
		outline: 2px solid #42cbb3;
		outline-offset: 1px;
	}
	.filename-input[aria-invalid="true"] { border-color: #8f5050; }
	.validation-hint { color: #e19a9a; }

	.destination-setting {
		display: grid;
		gap: 6px;
	}
	.destination-setting > span {
		color: #b6c1c7;
		font-size: 11px;
		font-weight: 650;
	}
	.destination-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 6px;
	}
	.destination-path {
		display: flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
		height: 32px;
		padding: 0 9px;
		border: 1px solid #3a454d;
		border-radius: 6px;
		background: #0f1519;
		color: #c4cfd4;
		font-size: 11px;
	}
	.destination-path > span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.destination-row > button {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 5px;
		min-width: 64px;
		border: 1px solid #3a6059;
		border-radius: 6px;
		background: #172824;
		color: #bce9e0;
		font-size: 11px;
		font-weight: 700;
		cursor: pointer;
	}
	.destination-row > button:disabled { opacity: .5; cursor: not-allowed; }

	.export-summary {
		display: grid;
		gap: 3px;
		margin-top: 4px;
		padding: 8px;
		border-left: 2px solid #3bbda6;
		background: #10201d;
	}
	.export-summary strong { font-size: 11px; }
	.export-summary span { color: var(--muted); font-size: 10px; }

	.render-progress {
		display: grid;
		gap: 7px;
		padding: 8px;
		border: 1px solid #2e655c;
		border-radius: 6px;
		background: #10201d;
	}
	.render-progress-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		font-size: 10px;
	}
	.render-progress-heading strong {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.render-progress-heading span { color: #8fe3d3; font-variant-numeric: tabular-nums; }
	.render-progress-track {
		height: 5px;
		overflow: hidden;
		border-radius: 999px;
		background: #26343a;
	}
	.render-progress-track > span {
		display: block;
		height: 100%;
		border-radius: inherit;
		background: #42cbb3;
		transition: width 180ms ease;
	}
	.export-success {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 9px 10px;
		border: 1px solid #2d756b;
		border-radius: 6px;
		background: #10201d;
		color: #8fe3d3;
	}
	.export-success > span { display: grid; min-width: 0; gap: 2px; }
	.export-success strong { color: #dff7f2; font-size: 11px; }
	.export-success small {
		overflow: hidden;
		color: #91aaa4;
		font-size: 10px;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.export-footer {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto auto;
		align-items: center;
		gap: 8px;
		padding: 9px 18px;
		border-top: 1px solid var(--line);
		background: #141a1f;
	}
	.export-footer span { color: var(--muted); font-size: 10px; }
	.export-footer .validation-error { color: #f3a2a2; }
	.export-footer button {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		min-width: 82px;
		height: 32px;
		border-radius: 6px;
		font-size: 11px;
		font-weight: 700;
		cursor: pointer;
	}
	.export-footer button:disabled { opacity: .5; cursor: not-allowed; }
	.export-footer .secondary { border: 1px solid #3a454d; background: #1b2329; color: var(--text); }
	.export-footer .primary { border: 1px solid #3b8f80; background: #17695d; color: white; }

	@media (max-width: 760px) {
		.export-backdrop { padding: 8px; }
		.export-dialog { max-height: calc(100vh - 16px); }
		.export-columns { grid-template-columns: 1fr; }
		.export-body { max-height: calc(100vh - 250px); padding: 14px; }
		.export-header, .export-footer { padding-left: 14px; padding-right: 14px; }
		.export-footer { grid-template-columns: 1fr 1fr; }
		.export-footer > div { grid-column: 1 / -1; }
	}

	@media (prefers-reduced-motion: reduce) {
		.export-backdrop { backdrop-filter: none; }
		.render-progress-track > span { transition: none; }
	}
</style>
