<script lang="ts">
	import { AudioLines, BookOpenText, ChartNoAxesColumn, ChevronsLeft, ChevronsRight, Clock3, FileAudio, Gauge, History, Library, Settings, SlidersHorizontal } from 'lucide-svelte';
	import { page } from '$app/state';

	let { collapsed = false, onToggle = () => {}, onNavClick = () => {} }: { collapsed?: boolean; onToggle?: () => void; onNavClick?: () => void } = $props();

	const items = [
		{ href: '/', label: '总览', icon: Gauge },
		{ href: '/engine-hub', label: '引擎管理', icon: SlidersHorizontal },
		{ href: '/voice-library', label: '音色管理', icon: Library },
		{ href: '/generate', label: '语音合成', icon: AudioLines },
		{ href: '/history', label: '历史记录', icon: History },
		{ href: '/script-studio', label: '脚本与批量', icon: BookOpenText },
		{ href: '/eval-reference', label: '参数参考', icon: ChartNoAxesColumn },
		{ href: '/audio-tools', label: '语音转写', icon: FileAudio },
		{ href: '/settings', label: '设置', icon: Settings }
	];
</script>

<aside class="sidebar">
	<div class="brand">
		<div class="brand-mark"><Clock3 size={17} /></div>
		<span>声音工作台</span>
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
