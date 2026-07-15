<script lang="ts">
	import { AudioLines, BookOpenText, ChartNoAxesColumn, ChevronsLeft, ChevronsRight, FileAudio, Film, Library, Settings, SlidersHorizontal } from 'lucide-svelte';
	import { page } from '$app/state';

	let { collapsed = false, onToggle = () => {}, onNavClick = () => {} }: { collapsed?: boolean; onToggle?: () => void; onNavClick?: () => void } = $props();

	const items = [
		{ href: '/engine-hub', label: '引擎管理', icon: SlidersHorizontal },
		{ href: '/voice-library', label: '音色管理', icon: Library },
		{ href: '/generate', label: '语音合成', icon: AudioLines },
		{ href: '/video-localization', label: '视频本土化', icon: Film },
		{ href: '/script-studio', label: '脚本与批量', icon: BookOpenText },
		{ href: '/eval-reference', label: '参数参考', icon: ChartNoAxesColumn },
		{ href: '/audio-tools', label: '语音转写', icon: FileAudio },
		{ href: '/settings', label: '设置', icon: Settings }
	];
</script>

<aside class="sidebar">
	<div class="brand">
		<a class="brand-link" href="/generate" aria-label="进入语音合成" onclick={onNavClick}>
			<div class="brand-mark"><img src="/voice-studio-mark.png" alt="" /></div>
			<span>声音工作台</span>
		</a>
		<button class="icon-btn collapse-btn" type="button" aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'} data-tooltip={collapsed ? '展开侧边栏' : '收起侧边栏'} onclick={onToggle}>
			{#if collapsed}<ChevronsRight size={16} />{:else}<ChevronsLeft size={16} />{/if}
		</button>
	</div>
	<nav class="nav">
		{#each items as item}
			<a href={item.href} class:active={page.url.pathname === item.href} aria-label={item.label} data-tooltip={item.label} onclick={onNavClick}>
				<item.icon size={17} />
				<span>{item.label}</span>
			</a>
		{/each}
	</nav>
</aside>
