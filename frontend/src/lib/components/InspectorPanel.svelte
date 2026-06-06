<script lang="ts">
  import { X } from 'lucide-svelte';
  let { onclose }: { onclose: () => void } = $props();
</script>

<aside
  class="w-80 flex-shrink-0 bg-[var(--color-surface)] border-l border-[var(--color-border)] overflow-y-auto"
  style="height: 100%"
>
  <div class="flex justify-between items-center px-5 py-4 border-b border-[var(--color-border)]">
    <h3 class="text-sm font-semibold m-0">参数面板</h3>
    <button
      class="bg-none border-none text-[var(--color-text-dim)] cursor-pointer p-1 rounded hover:bg-[var(--color-surface-2)]"
      onclick={onclose}
>
      <X size={16} />
    </button>
  </div>
  <div class="p-5">
    <p class="text-xs text-[var(--color-text-dim)]">选择引擎和声音后显示参数</p>
  </div>
</aside>
