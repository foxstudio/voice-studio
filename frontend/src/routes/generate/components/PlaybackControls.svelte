<script lang="ts">
	import type { GenerationTask } from '$lib/api/types';
	import { Play, Square } from 'lucide-svelte';

	type Props = {
		task: GenerationTask;
		isPlaying: boolean;
		onPlay: (task: GenerationTask) => void;
		onStop: (task: GenerationTask) => void;
	};

	let { task, isPlaying, onPlay, onStop }: Props = $props();

	function handleClick() {
		if (isPlaying) {
			onStop(task);
		} else {
			onPlay(task);
		}
	}
</script>

<button class="gen-icon-btn result-play-btn" class:playing={isPlaying} type="button" onclick={handleClick}>
	{#if isPlaying}
		<Square size={15} />
	{:else}
		<Play size={15} />
	{/if}
</button>
