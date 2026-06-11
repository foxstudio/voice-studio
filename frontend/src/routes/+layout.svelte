<script lang="ts">
	import '../app.css';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import { Api } from '$lib/api';
	import { engineStatusLabel } from '$lib/labels';
	import { onMount } from 'svelte';
	import { initTooltips } from '$lib/tooltip';
	import { Menu } from 'lucide-svelte';

	let { children } = $props();
	let status = $state('checking');
	let engines = $state<Record<string, string>>({});
	let sidebarCollapsed = $state(false);
	let sidebarMobileOpen = $state(false);

	$effect(() => {
		Api.health()
			.then((h) => {
				status = h.status;
				engines = Object.fromEntries(
					Object.entries(h.engines).filter(([, state]) => state === 'loaded')
				);
			})
			.catch(() => {
				status = 'offline';
			});
	});

	onMount(() => {
		initTooltips();
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

	function toggleMobileSidebar() {
		sidebarMobileOpen = !sidebarMobileOpen;
	}

	function closeMobileSidebar() {
		sidebarMobileOpen = false;
	}
</script>

<div class="app-shell" class:sidebar-collapsed={sidebarCollapsed} class:sidebar-mobile-open={sidebarMobileOpen}>
	{#if sidebarMobileOpen}
		<div
			class="sidebar-overlay"
			role="button"
			tabindex="0"
			aria-label="关闭导航"
			onclick={closeMobileSidebar}
			onkeydown={(event) => {
				if (event.key === 'Enter' || event.key === ' ' || event.key === 'Escape') {
					event.preventDefault();
					closeMobileSidebar();
				}
			}}
		></div>
	{/if}
	<Sidebar collapsed={sidebarCollapsed} onToggle={toggleSidebar} onNavClick={closeMobileSidebar} />
	<div class="main">
		<header class="topbar">
			<div class="row">
				<button class="icon-btn hamburger-btn" type="button" onclick={toggleMobileSidebar} aria-label="打开导航">
					<Menu size={18} />
				</button>
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
