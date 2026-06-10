<script lang="ts">
	import type { VoiceAsset } from '$lib/api/types';
	import { voiceAuthTags } from '$lib/labels';
	import Select from '$lib/components/shared/Select.svelte';

	type Props = {
		voices: VoiceAsset[];
		value?: string;
		onChange?: (value: string) => void;
	};

	let {
		voices,
		value = $bindable(''),
		onChange = () => {}
	}: Props = $props();

	function voiceOptionLabel(voice: VoiceAsset) {
		const tags = voiceAuthTags(voice.tags);
		return tags.length ? `${voice.name}（${tags.join('、')}）` : voice.name;
	}

	const options = $derived([
		{ label: voices.length === 0 ? '无音色' : '未选择', value: '' },
		...voices.map((voice) => ({
			label: voiceOptionLabel(voice),
			value: voice.voice_id
		}))
	]);
</script>

<Select
	{options}
	bind:value
	{onChange}
	searchable={true}
	scrollBlock="center"
	placeholder="搜索或选择音色"
/>
