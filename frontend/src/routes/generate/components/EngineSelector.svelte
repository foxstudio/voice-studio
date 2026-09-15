<script lang="ts">
	import type { EngineDetail } from '$lib/api/types';
	import { engineStatusLabel } from '$lib/labels';
	import Select from '$lib/components/shared/Select.svelte';

	type Props = {
		engines: EngineDetail[];
		value?: string;
		onChange?: (value: string) => void;
	};

	let {
		engines,
		value = $bindable(''),
		onChange = () => {}
	}: Props = $props();

	const options = $derived(
		engines.map((engine) => ({
			label: `${engine.manifest.display_name}  ·  ${engineStatusLabel(engine.state.status)}  ·  ${engine.manifest.engine_type === 'cloud' ? '云端' : '本地'}`,
			value: engine.manifest.engine_id
		}))
	);
</script>

<Select {options} bind:value {onChange} placeholder="选择引擎" />
