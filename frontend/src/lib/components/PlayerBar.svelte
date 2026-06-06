<script lang="ts">
  import { Play, Pause, SkipBack, Volume2, Download } from 'lucide-svelte';
  import { onMount } from 'svelte';

  let { audioUrl = $bindable(null) }: { audioUrl: string | null } = $props();
  let playing = $state(false);
  let currentTime = $state('0:00');
  let duration = $state('0:00');
  let waveContainer: HTMLDivElement | undefined = $state();
  let wavesurfer: any = $state();

  onMount(async () => {
    const ws = await import('wavesurfer.js');
  });

  async function loadAndPlay(url: string) {
    if (!waveContainer) return;
    const WaveSurfer = (await import('wavesurfer.js')).default;
    if (wavesurfer) wavesurfer.destroy();
    wavesurfer = WaveSurfer.create({
      container: waveContainer,
      waveColor: 'rgba(255,255,255,0.15)',
      progressColor: 'var(--color-accent)',
      height: 40,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      url,
    });
    wavesurfer.on('ready', () => {
      duration = formatTime(wavesurfer.getDuration());
      wavesurfer.play();
      playing = true;
    });
    wavesurfer.on('audioprocess', () => {
      currentTime = formatTime(wavesurfer.getCurrentTime());
    });
    wavesurfer.on('finish', () => { playing = false; });
  }

  function togglePlay() {
    if (!wavesurfer) return;
    wavesurfer.playPause();
    playing = !playing;
  }

  function formatTime(s: number): string {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  $effect(() => {
    if (audioUrl) loadAndPlay(audioUrl);
  });
</script>

<div class="player-bar">
  <div class="player-controls">
    <button class="btn-icon" onclick={togglePlay} disabled={!wavesurfer}>
      {#if playing}<Pause size={18} />{:else}<Play size={18} />{/if}
    </button>
    <span class="time">{currentTime}</span>
  </div>

  <div class="waveform" bind:this={waveContainer}></div>

  <div class="player-info">
    <span class="time">{duration}</span>
  </div>
</div>

<style>
  .player-bar {
    grid-column: 1 / -1;
    grid-row: 2;
    background: var(--color-surface);
    border-top: 1px solid var(--color-border);
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0 1.5rem;
  }
  .player-controls {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .btn-icon {
    background: none;
    border: none;
    color: var(--color-text);
    cursor: pointer;
    padding: 0.5rem;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s;
  }
  .btn-icon:hover { background: var(--color-surface-2); }
  .btn-icon:disabled { opacity: 0.3; cursor: default; }
  .waveform { flex: 1; min-width: 0; }
  .player-info { display: flex; align-items: center; gap: 0.75rem; }
  .time {
    font-size: 0.75rem;
    color: var(--color-text-dim);
    font-variant-numeric: tabular-nums;
    min-width: 3rem;
  }
</style>
