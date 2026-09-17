<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		title,
		subtitle = '',
		meta,
		actions
	}: {
		title: string;
		subtitle?: string;
		/** 标题行内、紧跟在标题后面的内容（统计胶囊、帮助入口等）。 */
		meta?: Snippet;
		/** 右侧操作区。 */
		actions?: Snippet;
	} = $props();
</script>

<header class="page-head">
	<div class="page-head-copy">
		<div class="page-title-row">
			<h1>{title}</h1>
			{@render meta?.()}
		</div>
		{#if subtitle}<p class="page-subtitle">{subtitle}</p>{/if}
	</div>
	{#if actions}<div class="page-title-actions">{@render actions()}</div>{/if}
</header>

<style>
	/* 页面标题区的唯一实现：左侧标题 + 说明，右侧操作。
	   各页面只传内容，不再各自定义字号和间距。 */
	.page-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 16px;
		margin-bottom: 18px;
	}

	.page-head-copy {
		display: grid;
		gap: 6px;
		min-width: 0;
	}

	.page-title-row {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 14px;
		min-width: 0;
	}

	h1 {
		margin: 0;
		font-size: 22px;
		line-height: 1.2;
	}

	.page-subtitle {
		margin: 0;
		color: var(--muted);
		font-size: 13px;
	}

	.page-title-actions {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
	}

	@media (max-width: 760px) {
		.page-head {
			flex-direction: column;
			align-items: stretch;
		}
	}
</style>
