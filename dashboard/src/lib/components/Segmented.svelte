<script lang="ts" generics="T extends string | number">
	let {
		value = $bindable(),
		options,
		label,
		onchange
	}: {
		value: T;
		options: { value: T; label: string }[];
		label: string;
		onchange?: (v: T) => void;
	} = $props();
</script>

<div class="seg" role="group" aria-label={label}>
	{#each options as o (o.value)}
		<button aria-pressed={value === o.value} onclick={() => o.value !== value && ((value = o.value), onchange?.(o.value))}>{o.label}</button>
	{/each}
</div>
