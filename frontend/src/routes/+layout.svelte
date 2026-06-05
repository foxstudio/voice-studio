<script lang="ts">
  import '../app.css';
  import Sidebar from '$lib/components/Sidebar.svelte';
  import PlayerBar from '$lib/components/PlayerBar.svelte';
  import InspectorPanel from '$lib/components/InspectorPanel.svelte';
  import { page } from '$app/stores';

  let { children } = $props();
  let showInspector = $state(false);
  let currentAudioUrl = $state<string | null>(null);
</script>

<div class="app-shell">
  <Sidebar />
  <main class="main-area">
    {@render children()}
  </main>
  {#if showInspector}
    <InspectorPanel onclose={() => showInspector = false} />
  {/if}
  <PlayerBar
    audioUrl={currentAudioUrl}
    onplay={(url: string) => currentAudioUrl = url}
  />
</div>

<style>
  .app-shell {
    display: grid;
    grid-template-columns: var(--sidebar-w) 1fr;
    grid-template-rows: 1fr var(--player-h);
    height: 100vh;
    overflow: hidden;
  }
  .main-area {
    grid-column: 2;
    grid-row: 1;
    overflow-y: auto;
    padding: 2rem;
  }
</style>
