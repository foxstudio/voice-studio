<script lang="ts">
	type Props = {
		checked?: boolean;
		label?: string;
		onChange?: (checked: boolean) => void;
	};

	let { checked = $bindable(false), label = '', onChange = () => {} }: Props = $props();

	function toggle() {
		checked = !checked;
		onChange(checked);
	}
</script>

<button class="toggle" class:checked type="button" role="switch" aria-checked={checked} onclick={toggle}>
	<span class="track"><span class="thumb"></span></span>
	{#if label}<span class="label">{label}</span>{/if}
</button>

<style>
	.toggle {
		width: fit-content;
		border: 0;
		background: transparent;
		color: var(--text);
		padding: 0;
		display: inline-flex;
		align-items: center;
		gap: 8px;
	}

	.track {
		width: 38px;
		height: 22px;
		border: 1px solid var(--line);
		border-radius: 999px;
		background: #101215;
		padding: 2px;
		transition: background 160ms ease, border-color 160ms ease;
	}

	.thumb {
		display: block;
		width: 16px;
		height: 16px;
		border-radius: 50%;
		background: var(--muted);
		transition: transform 160ms ease, background 160ms ease;
	}

	.toggle.checked .track {
		border-color: var(--accent);
		background: rgba(79, 156, 249, 0.28);
	}

	.toggle.checked .thumb {
		background: var(--accent);
		transform: translateX(16px);
	}

	.label {
		font-size: 13px;
		color: var(--text);
	}
</style>
