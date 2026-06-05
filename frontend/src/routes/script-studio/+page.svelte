<script lang="ts">
  import { Plus, Play, Lock, Unlock, Trash2 } from 'lucide-svelte';
</script>

<svelte:head><title>脚本工作台 - Voice Studio</title></svelte:head>

<section>
  <h1>脚本工作台</h1>
  <p class="desc">多段落、多角色、批量配音</p>

  <div class="empty">
    <p>创建一个脚本项目开始</p>
    <button class="btn primary"><Plus size={16} /> 新建项目</button>
  </div>
</section>

<style>
  h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 0.25rem; }
  .desc { color: var(--color-text-dim); font-size: 0.9rem; margin: 0 0 2rem; }
  .empty { text-align: center; padding: 4rem; }
  .empty p { color: var(--color-text-dim); margin-bottom: 1.5rem; }
  .btn { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.5rem 1.25rem;
    border-radius: 8px; border: none; font-size: 0.9rem; font-weight: 600; cursor: pointer; }
  .btn.primary { background: var(--color-accent); color: white; }
</style>
