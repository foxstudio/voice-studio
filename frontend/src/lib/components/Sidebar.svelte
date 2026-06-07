<script lang="ts">
	import { AudioLines, BookOpenText, ChartNoAxesColumn, ChevronsLeft, ChevronsRight, Clock3, FileAudio, Gauge, History, Library, ListChecks, Settings, SlidersHorizontal, Wand2 } from 'lucide-svelte';
	import { page } from '$app/state';

	let { collapsed = false, onToggle = () => {} }: { collapsed?: boolean; onToggle?: () => void } = $props();

	const items = [
		{ href: '/', label: '总览', icon: Gauge },
		{ href: '/engine-hub', label: '引擎中心', icon: SlidersHorizontal },
		{ href: '/voice-library', label: '音色库', icon: Library },
		{ href: '/generate', label: '单条生成', icon: AudioLines },
		{ href: '/script-studio', label: '脚本工作台', icon: BookOpenText },
		{ href: '/eval-reference', label: '评测参考', icon: ChartNoAxesColumn },
		{ href: '/text-tools', label: '文本工具', icon: Wand2 },
		{ href: '/audio-tools', label: '音频与转写', icon: FileAudio },
		{ href: '/tasks', label: '任务队列', icon: ListChecks },
		{ href: '/history', label: '历史记录', icon: History },
		{ href: '/settings', label: '设置', icon: Settings }
	];
</script>

<aside class="sidebar">
	<div class="brand">
		<div class="brand-mark"><Clock3 size={17} /></div>
		<span>声音工作台</span>
		<button class="icon-btn collapse-btn" type="button" title={collapsed ? '展开侧边栏' : '收起侧边栏'} onclick={onToggle}>
			{#if collapsed}<ChevronsRight size={16} />{:else}<ChevronsLeft size={16} />{/if}
		</button>
	</div>
	<nav class="nav">
		{#each items as item}
			<a href={item.href} class:active={page.url.pathname === item.href} title={item.label}>
				<item.icon size={17} />
				<span>{item.label}</span>
			</a>
		{/each}
	</nav>
</aside>
