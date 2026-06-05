<script lang="ts">
  import { api } from '$lib/api';
  import type { HistoryItem } from '$lib/api';
  import { Play, Download, Star, Trash2 } from 'lucide-svelte';

  let items = $state<HistoryItem[]>([]);

  $effect(() => {
    api.get<HistoryItem[]>('/history').then(d => items = d).catch(() => {});
  });
</script>

<svelte:head><title>历史记录 - Voice Studio</title></svelte:head>

<section>
  <h1>历史记录</h1>
  <p class="desc">查看所有生成记录与参数快照</p>

  {#if items.length === 0}
    <div class="empty">
      <p>还没有生成记录</p>
      <p class="dim">生成第一条语音后将显示在这里</p>
    </div>
  {:else}
    <div class="history-list">
      {#each items as item}
        <div class="history-item">
          <div class="item-main">
            <div class="item-text">{item.input_text}</div>
            <div class="item-meta">
              <span>{item.engine_id}</span>
              <span>{item.duration_ms ? `${(item.duration_ms / 1000).toFixed(1)}s` : ''}</span>
              <span>{item.generation_time_ms ? `${item.generation_time_ms}ms` : ''}</span>
            </div>
          </div>
          <div class="item-actions">
            {#if item.output_audio_id}
              <audio controls src="/api/history/{item.result_id}/audio" class="audio-mini"></audio>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 0.25rem; }
  .desc { color: var(--color-text-dim); font-size: 0.9rem; margin: 0 0 1.5rem; }
  .empty { text-align: center; padding: 4rem; color: var(--color-text-dim); }
  .dim { font-size: 0.85rem; margin-top: 0.5rem; }
  .history-list { display: flex; flex-direction: column; gap: 0.75rem; }
  .history-item { display: flex; justify-content: space-between; align-items: center;
    padding: 1rem 1.25rem; background: var(--color-surface);
    border: 1px solid var(--color-border); border-radius: 10px; }
  .item-text { font-size: 0.9rem; margin-bottom: 0.35rem; max-width: 500px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .item-meta { display: flex; gap: 0.75rem; font-size: 0.75rem; color: var(--color-text-dim); }
  .item-actions { display: flex; align-items: center; gap: 0.5rem; }
  .audio-mini { height: 32px; }
</style>
