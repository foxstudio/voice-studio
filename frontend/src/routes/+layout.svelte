<script lang="ts">
  import '../app.css';
  import Sidebar from '$lib/components/Sidebar.svelte';
  import PlayerBar from '$lib/components/PlayerBar.svelte';
  import InspectorPanel from '$lib/components/InspectorPanel.svelte';

  let { children } = $props();
  let showInspector = $state(true);
  let currentAudioUrl = $state<string | null>(null);
</script>

<!-- 3-column layout: Sidebar (fixed) | Main (flex) | InspectorPanel (fixed, collapsible) -->
<div class="flex flex-col h-screen overflow-hidden">
  <div class="flex flex-1 overflow-hidden">
    <div class="w-60 flex-shrink-0">
      <Sidebar />
    </div>
    <main class="flex-1 overflow-y-auto p-8 min-w-0">
      {@render children()}
    </main>
    {#if showInspector}
      <InspectorPanel onclose={() => showInspector = false} />
    {/if}
  </div>
  <PlayerBar
    audioUrl={currentAudioUrl}
    onplay={(url: string) => currentAudioUrl = url}
  />
</div>
