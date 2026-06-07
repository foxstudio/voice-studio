<script lang="ts">
	import '../app.css';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import { Api } from '$lib/api';
	import { engineStatusLabel } from '$lib/labels';
	import { onMount } from 'svelte';

	let { children } = $props();
	let status = $state('checking');
	let engines = $state<Record<string, string>>({});
	let sidebarCollapsed = $state(false);

	$effect(() => {
		Api.health()
			.then((h) => {
				status = h.status;
				engines = h.engines;
			})
			.catch(() => {
				status = 'offline';
			});
	});

	onMount(() => {
		sidebarCollapsed = localStorage.getItem('voice-studio-sidebar') === 'collapsed';
		const onPlay = (event: Event) => {
			const current = event.target;
			if (!(current instanceof HTMLAudioElement)) return;
			document.querySelectorAll('audio').forEach((audio) => {
				if (audio !== current) audio.pause();
			});
		};
		document.addEventListener('play', onPlay, true);
		return () => document.removeEventListener('play', onPlay, true);
	});

	function toggleSidebar() {
		sidebarCollapsed = !sidebarCollapsed;
		localStorage.setItem('voice-studio-sidebar', sidebarCollapsed ? 'collapsed' : 'expanded');
	}
</script>

<div class="app-shell" class:sidebar-collapsed={sidebarCollapsed}>
	<Sidebar collapsed={sidebarCollapsed} onToggle={toggleSidebar} />
	<div class="main">
		<header class="topbar">
			<div class="row">
				<span class="badge" class:ok={status === 'ok'} class:fail={status === 'offline'}>接口 {status === 'ok' ? '正常' : status === 'offline' ? '离线' : '检查中'}</span>
				{#each Object.entries(engines) as [id, state]}
					<span class="badge" class:ok={state === 'loaded'}>{id}: {engineStatusLabel(state)}</span>
				{/each}
			</div>
			<span class="muted">本地语音工作台</span>
		</header>
		{@render children()}
	</div>
</div>
